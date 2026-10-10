"""Offline, backed-up migration using Science's own importer, not a schema merge."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess


def databases(root):
    return sorted(root.glob("orgs/*/operon-cli.db")) + list(root.glob("operon-cli.db"))


def inventory(roots):
    result = {"projects": set(), "frames": set(), "roots": set(), "messages": {}, "versions": set()}
    for root in roots:
        for path in databases(root):
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                result["projects"].update(r[0] for r in db.execute("SELECT id FROM projects"))
                result["frames"].update(r[0] for r in db.execute("SELECT id FROM frames"))
                result["roots"].update(r[0] for r in db.execute("SELECT id FROM frames WHERE parent_frame_id IS NULL"))
                result["versions"].update(r[0] for r in db.execute("SELECT id FROM artifact_versions"))
                for frame, idx, body in db.execute("SELECT frame_id, idx, msg_json FROM frame_messages"):
                    key = (frame, idx)
                    digest = hashlib.sha256(body.encode()).hexdigest()
                    result["messages"].setdefault(key, set()).add(digest)
    return result


def counts(data):
    return {key: len(value) for key, value in data.items()}


def clone(source, destination):
    # APFS clones preserve independent files without copying multi-GB runtimes.
    subprocess.run(["/bin/cp", "-cRp", str(source), str(destination)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    official = args.official.resolve()
    backup = args.backup.resolve()
    assert not backup.exists(), "Use a new backup directory"
    sources = [s.resolve() for s in args.source]
    assert len(set(sources)) == len(sources) and official not in sources
    for root in [official, *sources]:
        assert root.is_dir() and databases(root), "Missing history source"
        # All Science processes must have been stopped before invoking this script.
        check = subprocess.run([str(official / "bin/claude-science"), "status", "--data-dir", str(root)], capture_output=True, check=True)
        assert not json.loads(check.stdout)["running"], "Science is still running"
    expected = inventory([official, *sources])
    backup.mkdir(mode=0o700, parents=True)
    clone(official, backup / "official-before")
    stage = backup / "merged"
    clone(official, stage)
    work = backup / "source-work"
    work.mkdir(mode=0o700)
    config = backup / "import.toml"
    config.write_text("data_dir = " + json.dumps(str(stage)) + "\n[paths]\nauth_dir = " + json.dumps(str(stage)) + "\n[update]\nauto_update = false\n")
    tools = backup / "tools"
    tools.mkdir(mode=0o700)
    shim = tools / "security"
    shim.write_text("#!/bin/sh\nexit 1\n")
    shim.chmod(0o500)
    env = dict(os.environ, PATH=f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin")
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "BUN_OPTIONS"):
        env.pop(key, None)
    for index, root in enumerate(sources):
        copy = work / str(index)
        clone(root, copy)
        with (backup / f"import-{index}.log").open("wb") as log:
            cmd = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network-outbound)", str(official / "bin/claude-science"), "import", str(copy), "--data-dir", str(stage), "--config", str(config)]
            result = subprocess.run(cmd, env=env, stdout=log, stderr=log, timeout=900)
        assert result.returncode == 0, f"Importer failed for source {index}; originals unchanged. Inspect private log."
        print(json.dumps({"imported_source": index, "stage": counts(inventory([stage]))}), flush=True)
    actual = inventory([stage])
    missing = {key: len(set(value) - set(actual[key])) for key, value in expected.items()}
    lost_message_versions = sum(not digests <= actual["messages"].get(key, set()) for key, digests in expected["messages"].items())
    report = {"expected": counts(expected), "actual": counts(actual), "missing": missing, "message_conflicts": sum(len(v) > 1 for v in expected["messages"].values()), "lost_message_versions": lost_message_versions, "official": str(official), "stage": str(stage), "sources": [str(s) for s in sources]}
    (backup / "verification.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    assert not any(missing.values()) and not lost_message_versions, "Verification failed; originals unchanged"
    print("VERIFIED: staged migration only; originals have not been moved or deleted", flush=True)


if __name__ == "__main__":
    main()
