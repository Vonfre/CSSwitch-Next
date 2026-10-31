"""Expose owned local drafts in Science's native skill picker.

The native catalog excludes drafts even when its skill loader can read them.
Keep the native rows authoritative and supplement only authenticated, approved
drafts from the exact Science session owned by this CSSwitch installation.
"""
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location('catalog_local_skills', Path(__file__).with_name('science-local-skills.py'))
local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local)
CatalogError = local.LocalSkillError


def augment_catalog(catalog, config, upstream_port, request):
    if not isinstance(catalog, dict) or not isinstance(catalog.get('skills'), list):
        return catalog
    config = Path(config)
    if json.loads(config.read_text()).get('mode', 'proxy') != 'proxy':
        return catalog
    session_file = local.guarded(config.parent / 'shared-science/session.json')
    if not local.regular(session_file) or session_file.stat().st_size > 16384:
        return catalog
    session = json.loads(session_file.read_text())
    if session.get('port') != upstream_port:
        return catalog
    # All requests reuse the caller's credentials; never mint a privileged
    # session or substitute another user's identity for this page request.
    policy = request('/api/org-policy')
    if policy.get('caps', {}).get('operon_custom_skills', {}).get('allowed') is not True:
        return catalog
    drafts = request('/api/skills/drafts').get('drafts', [])
    me = request('/api/me')
    if not isinstance(drafts, list) or not isinstance(me.get('user_id'), str):
        return catalog
    agents = request('/api/agents')
    if isinstance(agents, dict):
        agents = agents.get('agents', [])
    if not isinstance(agents, list):
        return catalog
    store = local.LocalSkills(session['data_dir'], Path(session['config']).parent / 'auth')
    disabled = store.disabled(me['user_id'])
    rows = list(catalog['skills'])
    existing = {row.get('name') for row in rows if isinstance(row, dict)}
    for name in drafts:
        if not isinstance(name, str) or not local.NAME.fullmatch(name) or name in existing:
            continue
        try:
            info = store.catalog_info(name)
        except (OSError, ValueError, local.LocalSkillError):
            continue
        if info is None:
            continue
        rows.append({'name': name, 'displayName': name, 'description': info['description'],
                     'source': 'imported', 'enabled': name not in disabled,
                     'attachedAgents': [a['name'] for a in agents if isinstance(a, dict)
                                        and isinstance(a.get('name'), str)
                                        and name in (a.get('skillNames') or [])]})
        existing.add(name)
    return {**catalog, 'skills': rows}
