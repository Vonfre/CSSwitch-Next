# CC Switch source attribution

CSSwitch Next 0.8.27 adapts the countdownStr, barPercent and formatRelativeTime functions from
[CC Switch quotaRules.ts](https://github.com/farion1231/cc-switch/blob/cdca0984231021c4eb7e8978af8670cd29c6e415/src/components/quota/quotaRules.ts).
The compact account quota columns follow AccountQuota.tsx in the same revision.
They are translated from React/TypeScript into this application's existing vanilla JavaScript and CSS.

Upstream: https://github.com/farion1231/cc-switch
Revision: cdca0984231021c4eb7e8978af8670cd29c6e415
Copyright (c) 2025 Jason Young. MIT License.
Full license: desktop/src/assets/cc-switch-LICENSE.txt (included in packaged frontend assets).

The Science routing adapter is specific to Science's native organization/workspace model.
Claude Code's CLAUDE_CONFIG_DIR account isolation does not provide Science native identity hot switching.

## Bundled macOS Python runtime (0.8.28)

CPython 3.13.18 from Astral python-build-standalone release 20261026, with separate
aarch64 and x86_64 archives, pinned by SHA-256 in scripts/prepare-macos-runtime.py.
Upstream: https://github.com/astral-sh/python-build-standalone
Runtime licenses remain in its distribution. Dependencies and their licenses are
shipped in site-packages (cryptography 48.0.1 universal2, cffi, pycparser, certifi).
Exact wheel hashes are in scripts/macos-runtime-requirements.txt.
