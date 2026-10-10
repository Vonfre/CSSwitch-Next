#!/usr/bin/env python3
"""Validate the locked desktop version before building a release (Python 3.11+)."""

import argparse
import json
from pathlib import Path
import re
import tomllib


def check_version(root, tag=""):
    desktop = Path(root) / "desktop"
    package = json.loads((desktop / "package.json").read_text())
    lock = json.loads((desktop / "package-lock.json").read_text())
    tauri = json.loads((desktop / "src-tauri/tauri.conf.json").read_text())
    cargo = tomllib.loads((desktop / "src-tauri/Cargo.toml").read_text())
    cargo_lock = tomllib.loads((desktop / "src-tauri/Cargo.lock").read_text())
    versions = {
        "package.json": package["version"],
        "package-lock.json": lock["version"],
        "package-lock.json packages root": lock["packages"][""]["version"],
        "tauri.conf.json": tauri["version"],
        "Cargo.toml": cargo["package"]["version"],
        "Cargo.lock": next(p["version"] for p in cargo_lock["package"] if p["name"] == cargo["package"]["name"]),
    }
    version = package["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", version):
        raise ValueError("Invalid release version")
    if any(value != version for value in versions.values()):
        raise ValueError(f"Desktop versions do not match: {versions}")
    if tag and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match v{version}")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    try:
        version = check_version(Path(__file__).resolve().parents[1], args.tag)
    except (ValueError, KeyError, StopIteration) as exc:
        parser.error(str(exc))
    print(f"version={version}")


if __name__ == "__main__":
    main()
