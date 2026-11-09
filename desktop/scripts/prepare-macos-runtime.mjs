import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
if (process.platform === 'darwin') {
  const triple = process.env.TAURI_ENV_TARGET_TRIPLE || process.env.RUST_TARGET || '';
  const arch = triple ? (triple.startsWith('aarch64-') ? 'arm64' : triple.startsWith('x86_64-') ? 'x64' : null) : process.arch === 'arm64' ? 'arm64' : 'x64';
  if (!arch) throw new Error(`Unsupported macOS target: ${triple}`);
  const result = spawnSync('python3', [fileURLToPath(new URL('../../scripts/prepare-macos-runtime.py', import.meta.url)), '--arch', arch], {stdio:'inherit'});
  if (result.error || result.status !== 0) throw new Error('Unable to prepare the bundled macOS Python runtime. Build stopped.');
}
