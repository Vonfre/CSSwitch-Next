#!/usr/bin/env python3
"""CSSwitch's 24 local connector profiles.

Use the user's installed Science tool implementations without copying or
redistributing that application's code. Preserve its schemas, serving gates,
rate limits and MCP App resources. No Claude-hosted MCP endpoint is used.
"""
import json
import os
import re
import shutil
from pathlib import Path

NAMES = {
    'biomart': 'BioMart', 'cancer-models': 'Cancer Models', 'cellguide': 'CellGuide',
    'chemistry': 'Chemistry', 'clinical-genomics': 'Clinical Genomics',
    'drug-regulatory': 'Drug Regulatory', 'expression': 'Expression',
    'genes-ontologies': 'Genes & Ontologies', 'genomes': 'Genomes',
    'human-genetics': 'Human Genetics', 'ketcher-chemistry': 'Ketcher Chemistry',
    'literature': 'Literature Graph', 'omics-archives': 'Omics Archives',
    'protein-annotation': 'Protein Annotation', 'regulation': 'Regulation',
    'research-resources': 'Research Resources', 'rna': 'RNA',
    'structures-interactions': 'Structures & Interactions', 'variants': 'Variants',
    'zinc': 'ZINC', 'biorxiv': 'bioRxiv', 'chembl': 'ChEMBL',
    'clinical-trials': 'Clinical Trials', 'pubmed': 'PubMed',
}
HOSTED = ('pubmed', 'biorxiv', 'chembl', 'clinical-trials')
PREFIX = 'csswitch-local-'
MARKER = 'CSSwitch local Science connector: '


def runtime(home):
    candidates = []
    for path in (Path(home) / '.claude-science/runtime').glob('*-release'):
        match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)-release', path.name)
        if match and (path / '.ok').is_file():
            candidates.append((tuple(map(int, match.groups())), path))
    if not candidates:
        raise ValueError('缺少完整的 Science 本机运行资源，请先安装或更新 Claude Science。')
    return max(candidates)[1]


def command(home, profile, node=None):
    if profile not in NAMES:
        raise ValueError('未知的 Science 连接器。')
    root = runtime(home)
    if profile == 'ketcher-chemistry':
        script = root / 'mcp-servers/ketcher-chemistry/server.js'
        widget = script.parent / 'widget/index.html'
        executable = node or shutil.which('node')
        if not executable or not Path(executable).is_file() or not widget.is_file():
            raise ValueError('Ketcher Chemistry 需要 Node.js 和完整的 Science 编辑器资源。')
        args = [str(script)]
    else:
        environment = Path(home) / '.claude-science/conda/envs/claude-science-mcp'
        executable = str(environment / ('python.exe' if os.name == 'nt' else 'bin/python'))
        script = root / 'mcp-servers/bio-tools/run_server.py'
        package = 'mcp_' + profile.replace('-', '_')
        if not Path(executable).is_file() or not (script.parent / 'lib' / package / 'server.py').is_file():
            raise ValueError(f'{NAMES[profile]} 的本机工具或 Python 环境缺失，请更新 Science。')
        args = [str(script), package]
    if not script.is_file():
        raise ValueError(f'{NAMES[profile]} 的本机服务入口缺失。')
    return [str(executable), *args]


def proxy_env(port):
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('网关端口无效。')
    env = {k: f'http://127.0.0.1:{port}' for k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')}
    env.update(NO_PROXY='127.0.0.1,localhost,::1', no_proxy='127.0.0.1,localhost,::1')
    return env


def spec(home, profile, launcher, port, node=None, env=None):
    target = command(home, profile, node)  # Preflight before modifying any connector.
    extra = env or {}
    if not isinstance(extra, dict) or not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) and isinstance(v, str) for k, v in extra.items()):
        raise ValueError('环境变量需为 JSON 字符串对象。')
    # Only user-supplied values; never read native credentials or invent a contact.
    values = dict(extra)
    values.update(proxy_env(port))
    # Resolve paths in the manager. The MCP child is sandboxed and may read
    # its explicit entry point but cannot enumerate the parent runtime directory.
    # Pin the complete runtime here; the next install/launch repairs update paths.
    return dict(name=PREFIX + profile, description=MARKER + NAMES[profile],
                command=target[0], args=target[1:], env=values, attach=True)


def catalog(connectors, home=None, node=None):
    entries = []
    for key, name in NAMES.items():
        local = next((c for c in connectors if c.get('source') == 'local-stdio' and c.get('name') == PREFIX + key and c.get('description') == MARKER + name), None)
        native = next((c for c in connectors if c.get('source') == 'bundled' and c.get('name') == key), None)
        selected = local if key in HOSTED else native
        problem = None
        if home:
            try:
                command(home, key, node)
            except ValueError as e:
                problem = str(e)
        bound = selected and 'OPERON' in selected.get('attachedAgents', [])
        ready = bool(selected and bound and selected.get('enabled', True) and selected.get('connectionStatus') == 'connected' and not selected.get('admin_blocked'))
        entries.append(dict(profile=key, displayName=name, kind='local' if key in HOSTED else 'native',
                            serverId=selected.get('id') if selected else None,
                            ready=ready, status=selected.get('connectionStatus') if selected else 'missing',
                            resourceError=problem))
    return entries
