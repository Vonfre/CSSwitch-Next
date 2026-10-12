#!/usr/bin/env python3
"""Verify both architecture signatures and generate the Tauri static updater manifest."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile


def release_notes(root, version):
    text = (root / "CHANGELOG.md").read_text()
    match = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match:
        raise ValueError(f"Missing changelog for {version}")
    return match.group(1).strip()


def generate(root, assets, version, repo, verify=True):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid GitHub repository")
    config = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text())
    pubkey = base64.b64decode(config["plugins"]["updater"]["pubkey"], validate=True)
    platforms = {}
    with tempfile.TemporaryDirectory() as temp:
        public = Path(temp) / "public.key"
        public.write_bytes(pubkey)
        for arch, target in (("arm64", "darwin-aarch64"), ("x64", "darwin-x86_64")):
            name = f"CSSwitch-Next-{version}-{arch}.app.tar.gz"
            archive = assets / name
            if not archive.is_file() or not archive.stat().st_size:
                raise ValueError(f"Missing updater archive: {name}")
            for suffix in ("dmg", "app.zip"):
                file = assets / f"CSSwitch-Next-{version}-{arch}.{suffix}"
                if not file.is_file() or not file.stat().st_size:
                    raise ValueError(f"Missing installer: {file.name}")
            signature = (assets / (name + ".sig")).read_text().strip()
            decoded = base64.b64decode(signature, validate=True)
            if not decoded.startswith(b"untrusted comment:"):
                raise ValueError(f"Invalid signature: {name}")
            if not any(line.startswith("trusted comment:") and f"\tversion:{version}" in line for line in decoded.decode().splitlines()):
                raise ValueError(f"Signature is not bound to release version: {name}")
            if verify:
                sigfile = Path(temp) / "signature"
                sigfile.write_bytes(decoded)
                subprocess.run(["minisign", "-V", "-m", str(archive), "-p", str(public), "-x", str(sigfile)], check=True)
            platforms[target] = {"signature": signature, "url": f"https://github.com/{repo}/releases/download/v{version}/{name}"}
    return {"version": version, "notes": release_notes(root, version),
            "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "platforms": platforms}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = generate(root, args.assets, args.version, args.repo)
    (args.assets / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (args.assets / "RELEASE-NOTES.md").write_text(manifest["notes"] + "\n")
