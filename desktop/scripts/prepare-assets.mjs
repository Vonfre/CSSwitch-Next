import { copyFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const root = new URL('../', import.meta.url);
const assets = new URL('src/assets/', root);
mkdirSync(assets, { recursive: true });
for (const [source, target] of [
  ['node_modules/lucide/dist/umd/lucide.min.js', 'lucide.min.js'],
  ['node_modules/lucide/LICENSE', 'lucide-LICENSE.txt'],
  ['node_modules/marked/lib/marked.umd.js', 'marked.min.js'],
  ['node_modules/marked/LICENSE.md', 'marked-LICENSE.txt'],
  ['node_modules/dompurify/dist/purify.min.js', 'purify.min.js'],
  ['node_modules/dompurify/LICENSE', 'dompurify-LICENSE.txt'],
  ['src-tauri/icons/128x128.png', 'app-icon.png'],
]) copyFileSync(fileURLToPath(new URL(source, root)), fileURLToPath(new URL(target, assets)));

await import('./build-mcp.mjs');
