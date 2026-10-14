#!/usr/bin/env python3
"""Run one of all 24 Science profiles locally, keeping upstream code unmodified."""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

module = importlib.util.spec_from_file_location('science_connectors', Path(__file__).with_name('science-connectors.py'))
profiles = importlib.util.module_from_spec(module)
module.loader.exec_module(profiles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--profile', choices=profiles.NAMES, required=True)
    parser.add_argument('--node')
    args = parser.parse_args()
    try:
        cmd = profiles.command(args.home, args.profile, args.node)
        # Exec preserves full stdio MCP (including Ketcher resources) and signals.
        # Do not remove native serving/license gates or credential requirements.
        os.execv(cmd[0], cmd)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
