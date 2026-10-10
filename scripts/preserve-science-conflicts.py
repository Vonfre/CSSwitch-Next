"""Preserve divergent transcript snapshots as new frames, then use native import."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

from importlib.machinery import SourceFileLoader


def texts(message):
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return []


def text_inventory(db):
    result = {}
    for frame, body in db.execute("SELECT frame_id, msg_json FROM frame_messages"):
        for text in texts(json.loads(body)):
            result.setdefault(frame, set()).add(hashlib.sha256(text.encode()).hexdigest())
    return result


def remap(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): remap(item, mapping) for key, item in value.items()}
    return value


def rewrite(value, mapping):
    if not isinstance(value, str):
        return value
    if value in mapping:
        return mapping[value]
    if value.startswith(("{", "[")):
        try:
            return json.dumps(remap(json.loads(value), mapping), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    backup = args.backup.resolve()
    report = json.loads((backup / "verification.json").read_text())
    stage = Path(report["stage"])
    official = Path(report["official"])
    target = next(stage.glob("orgs/*/operon-cli.db"))
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as dst:
        current = text_inventory(dst)
    helper = SourceFileLoader("migrate_history", str(Path(__file__).with_name("migrate-science-history.py"))).load_module()
    directory = backup / "conflicts-v2"
    directory.mkdir(mode=0o700, exist_ok=True)
    retained = set()
    variants = []
    for index, root in enumerate(map(Path, report["sources"])):
        for dbindex, path in enumerate(helper.databases(root)):
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as src:
                src.row_factory = sqlite3.Row
                original_text = text_inventory(src)
                missing = {frame for frame, content in original_text.items() if not content <= current.get(frame, set())}
                selected = set()
                for frame in missing:
                    content = original_text[frame]
                    signature = (frame, tuple(sorted(content)))
                    if signature in retained:
                        continue
                    retained.add(signature)
                    selected.add(frame)
                if not selected:
                    continue
                # Include descendant frames and their owning projects, but no unrelated history.
                rows = list(src.execute("SELECT * FROM frames"))
                while True:
                    extended = selected | {r["id"] for r in rows if r["parent_frame_id"] in selected or r["root_frame_id"] in selected}
                    if extended == selected:
                        break
                    selected = extended
                frame_rows = [r for r in rows if r["id"] in selected]
                project_ids = {r["project_id"] for r in frame_rows if r["project_id"]}
                mapping = {identifier: str(uuid.uuid4()) for identifier in selected | project_ids}
                export = directory / f"source-{index}-{dbindex}.db"
                assert not export.exists(), "Conflict exports must not be overwritten"
                with sqlite3.connect(export) as out:
                    tables = {r[0]: r[1] for r in src.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
                    copy = {"projects": [r for r in src.execute("SELECT * FROM projects") if r["id"] in project_ids], "frames": frame_rows}
                    for table in ("frame_messages", "frame_blobs", "frame_system_prompts", "frame_branch_archives", "compaction_archives", "transcript_annotations", "frame_read_cursors"):
                        if table in tables:
                            copy[table] = [r for r in src.execute(f'SELECT * FROM "{table}"') if any(r[key] in selected for key in ("frame_id", "root_frame_id") if key in r.keys())]
                    # Per-frame auxiliary records with their own IDs must not collide either.
                    for table, records in copy.items():
                        if table in ("projects", "frames"):
                            continue
                        for row in records:
                            if "id" in row.keys() and isinstance(row["id"], str):
                                mapping[row["id"]] = str(uuid.uuid4())
                    for table, records in copy.items():
                        out.execute(tables[table])
                        for row in records:
                            data = {key: rewrite(row[key], mapping) for key in row.keys()}
                            if table == "projects":
                                data["name"] = (data.get("name") or "Project") + f" [Recovered {index + 1}]"
                                if "uploads_frame_id" in data and row["uploads_frame_id"] not in selected:
                                    data["uploads_frame_id"] = None
                            if table == "frames":
                                data["name"] = (data.get("name") or "Conversation") + f" [Recovered {index + 1}]"
                            columns = ",".join(f'"{key}"' for key in data)
                            out.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in data)})', tuple(data.values()))
                with (directory / f"source-{index}-{dbindex}.log").open("wb") as log:
                    env = dict(os.environ, PATH=f"{backup / 'tools'}:/usr/bin:/bin:/usr/sbin:/sbin")
                    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "BUN_OPTIONS"):
                        env.pop(key, None)
                    result = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network-outbound)", str(official / "bin/claude-science"), "import", str(export), "--data-dir", str(stage), "--config", str(backup / "import.toml")], env=env, stdout=log, stderr=log, timeout=300)
                assert result.returncode == 0, "Conflict import failed; originals unchanged"
                with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as dst:
                    imported = text_inventory(dst)
                for frame in selected:
                    assert original_text.get(frame, set()) <= imported.get(mapping[frame], set()), "Conflict transcript verification failed"
                variants.append({"source": str(path), "frame_mapping": {frame: mapping[frame] for frame in selected}, "projects": len(project_ids)})
                print(json.dumps({"recovered_source": index, "frames": len(selected), "projects": len(project_ids)}), flush=True)
    actual = helper.inventory([stage])
    assert not any(report["missing"].values())
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as dst:
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not dst.execute("PRAGMA foreign_key_check").fetchall(), "Foreign key integrity failed"
    final = {"actual": helper.counts(actual), "recovered_variants": variants, "all_original_ids_present": True, "all_divergent_text_preserved": True}
    (backup / "final-verification.json").write_text(json.dumps(final, indent=2))
    print(json.dumps({"actual": final["actual"], "recovered_frames": sum(len(v["frame_mapping"]) for v in variants), "verified": True}), flush=True)


if __name__ == "__main__":
    main()
