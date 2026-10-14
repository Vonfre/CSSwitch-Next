#!/usr/bin/env python3
"""Check all 24 local profiles over stdio MCP; optional real public API calls."""
import argparse
import asyncio
import importlib.util
import json
import os
import shutil
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('profiles', repo / 'scripts/science-connectors.py')
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--home', type=Path, default=Path.home())
parser.add_argument('--live', action='store_true')
parser.add_argument('--output', type=Path, default=repo / 'artifacts/science-local-suite.json')
args = parser.parse_args()
SAMPLES = {
    'pubmed': ('search_articles', {'query': 'BRCA1 breast cancer', 'max_results': 2}),
    'chembl': ('compound_search', {'name': 'aspirin', 'chembl_id': 'CHEMBL25', 'limit': 1}),
    'biorxiv': ('search_preprints', {'date_from': '2024-01-01', 'date_to': '2024-01-02', 'limit': 1}),
    'clinical-trials': ('get_trial_details', {'nct_id': 'NCT04280705'}),
    'genes-ontologies': ('get_uniprot_entries', {'accessions': ['P04637'], 'format': 'fasta'}),
}


async def check(profile):
    executable = args.home / '.claude-science/conda/envs/claude-science-mcp/bin/python'
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'NO_PROXY', 'no_proxy', 'NCBI_EMAIL')}
    command = [str(executable), str(repo / 'scripts/science-connector-run.py'), '--home', str(args.home), '--profile', profile]
    if profile == 'ketcher-chemistry':
        command += ['--node', shutil.which('node')]
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, limit=32 * 1024 * 1024)
    async def ask(identifier, method, params=None):
        process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params or {}}) + '\n').encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), 110 if method == 'tools/call' else 30)
            if not line:
                raise RuntimeError(profile + ': MCP process exited')
            message = json.loads(line)
            if message.get('id') == identifier:
                if 'error' in message:
                    raise RuntimeError(profile + ': MCP protocol error')
                return message['result']
    try:
        initial = await ask(1, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'CSSwitch-local-probe', 'version': '1'}})
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        tools = (await ask(2, 'tools/list'))['tools']
        assert tools and len({t['name'] for t in tools}) == len(tools)
        assert all(isinstance(t.get('inputSchema'), dict) for t in tools)
        record = {'profile': profile, 'displayName': profiles.NAMES[profile], 'serverInfo': initial['serverInfo'], 'tools': tools}
        if profile == 'ketcher-chemistry':
            resources = await ask(3, 'resources/list')
            uri = resources['resources'][0]['uri']
            widget = await ask(4, 'resources/read', {'uri': uri})
            assert any('text' in item and len(item['text']) > 1000 for item in widget['contents'])
            record['widget'] = {'uri': uri, 'read': True}
        if args.live and profile in SAMPLES:
            name, params = SAMPLES[profile]
            assert name in {t['name'] for t in tools}
            result = await ask(5, 'tools/call', {'name': name, 'arguments': params})
            if result.get('isError'):
                raise RuntimeError(profile + ': ' + str(result.get('content'))[:450])
            value = json.loads(''.join(c.get('text', '') for c in result.get('content', []) if c.get('type') == 'text'))
            valid = {'pubmed': bool(value.get('pmids')), 'chembl': bool(value.get('compounds')), 'biorxiv': bool(value.get('results')), 'clinical-trials': value.get('found') is True, 'genes-ontologies': value.get('n_found') == 1}.get(profile)
            assert valid, profile + ': expected public data missing'
            # Only public records are saved; never save env or server stderr.
            record['sample'] = {'tool': name, 'result': result}
        print(f'PASS {profiles.NAMES[profile]}: {len(tools)} tools' + (' + public API' if 'sample' in record else ''), flush=True)
        return record
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


async def main():
    records = []
    for start in range(0, len(profiles.NAMES), 4):
        group = list(profiles.NAMES)[start:start + 4]
        records.extend(await asyncio.gather(*(check(key) for key in group)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'profiles': records, 'count': len(records), 'tools': sum(len(r['tools']) for r in records)}, ensure_ascii=False, indent=2))
    print('Validated', len(records), 'profiles;', sum(len(r['tools']) for r in records), 'tools', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
