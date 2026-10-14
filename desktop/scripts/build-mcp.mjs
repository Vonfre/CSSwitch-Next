// Local connector code is provided by the installed Science runtime.
// CSSwitch bundles only its own launcher and management bridge.
import { existsSync, rmSync } from 'node:fs';
const old = new URL('../../scripts/openscience-mcp/', import.meta.url);
if (existsSync(old)) rmSync(old, { recursive: true });
for (const name of ['science-connectors.py', 'science-connector-run.py']) {
  if (!existsSync(new URL(`../../scripts/${name}`, import.meta.url))) throw new Error(`Missing ${name}`);
}
