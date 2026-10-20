"""Read public model identifiers from the installed Science executable, never credentials.

Science embeds its public fallback catalog in the executable. Read just the ID/name
records and public default assignments, not implementation code. Cache by file identity
so replacing the executable refreshes discovery at the next request boundary.
"""
from functools import lru_cache
import mmap
from pathlib import Path
import re

FAMILIES = ('opus', 'sonnet', 'haiku')
ROLES = {'opus': '质量', 'sonnet': '默认', 'haiku': '快速'}
FALLBACK = {'opus': 'claude-opus-5', 'sonnet': 'claude-sonnet-5', 'haiku': 'claude-haiku-4-5'}
PUBLIC_RECORD = re.compile(rb'\{id:"(claude-(?:opus|sonnet|haiku)-\d{1,2}(?:-\d{1,2})*)",name:"(Claude (?:Opus|Sonnet|Haiku) [0-9.]+)"([^}]{0,80})\}')
PUBLIC_LITERAL = re.compile(rb'=\s*["\'](claude-(?:opus|sonnet|haiku)-\d{1,2}(?:-\d{1,2})*(?:-20\d{6})?)["\']')
FAMILY_ID = re.compile(r'^claude-(?:(opus|sonnet|haiku)-\d{1,2}(?:-\d{1,2})*|\d{1,2}(?:-\d{1,2})*-(opus|sonnet|haiku))(?:-20\d{6}|-latest)?$', re.I)


def family(model):
    match = FAMILY_ID.fullmatch(model or '')
    return (match.group(1) or match.group(2)).lower() if match else None


def canonical(model):
    return re.sub(r'-(?:20\d{6}|latest)$', '', model)


def rank(model):
    return tuple(int(n) for n in canonical(model).split('-')[2:])


def display_name(model):
    return 'Claude ' + model.split('-')[1].title() + ' ' + '.'.join(canonical(model).split('-')[2:])


def read_catalog(binary):
    records, literals = [], set()
    with open(binary, 'rb') as source:
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            for match in PUBLIC_RECORD.finditer(data):
                records.append((match[1].decode(), match[2].decode(), b'overflow:!0' in match[3] or b'overflow:true' in match[3]))
            literals.update(canonical(m[1].decode()) for m in PUBLIC_LITERAL.finditer(data)
                            if m.start() == 0 or data[m.start() - 1] not in b'=!<>')
    models = []
    for kind in FAMILIES:
        candidates = [r for r in records if family(r[0]) == kind]
        primary = [r for r in candidates if not r[2]] or candidates
        if primary:
            mid, name, _ = max(primary, key=lambda r: rank(r[0]))
        else:
            ids = [mid for mid in literals if family(mid) == kind]
            if not ids:
                continue
            mid = max(ids, key=rank); name = display_name(mid)
        models.append({'id': mid, 'name': name, 'family': kind})
    return models


@lru_cache(maxsize=4)
def _discover(path, inode, size, modified):
    try:
        models = read_catalog(path)
    except (OSError, ValueError):
        models = []
    found = {m['family'] for m in models}
    for kind in FAMILIES:
        if kind not in found:
            mid = FALLBACK[kind]
            models.append({'id': mid, 'name': display_name(mid), 'family': kind})
    return {'models': models, 'source': 'installed' if len(found) == 3 else 'partial' if found else 'fallback'}


def discover(binary=None):
    if binary is None:
        updated = Path.home() / '.claude-science/bin/claude-science'
        binary = updated if updated.is_file() else Path('/Applications/Claude Science.app/Contents/Resources/bin/claude-science')
    try:
        st = Path(binary).stat()
        result = _discover(str(binary), st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        result = _discover('', 0, 0, 0)
    # Callers must not mutate the cached discovery result.
    return {'source': result['source'], 'models': [dict(m) for m in result['models']]}


def configure(prov):
    targets = prov['family_map']
    models = discover()['models']
    prov['models'] = [(m['id'], f"{m['name']} → {targets[m['family']]} · {ROLES[m['family']]}") for m in models]
    prov['model_map'].update({m['id']: targets[m['family']] for m in models})
    return prov


def adapt_model_response(payload, prov):
    """Refresh only the local legacy catalog; leave native policy/error feeds intact."""
    if not isinstance(payload, dict) or payload.get('first_party_catalog') or payload.get('model_list_source') == 'selector':
        return payload
    if payload.get('auth_error') or payload.get('fetch_error'):
        return payload
    groups = payload.get('models')
    if not isinstance(groups, dict) or not isinstance(groups.get('anthropic'), list):
        return payload
    original = {m.get('id'): m for m in groups['anthropic'] if isinstance(m, dict)}
    models = []
    for mid, name in prov['models']:
        old = original.get(mid, {})
        # Preserve availability and other metadata already supplied by Science.
        models.append({**old, 'id': mid, 'name': name, 'overflow': False,
                       'description': 'CSSwitch ' + ROLES[family(mid)] + '模型'})
    # Old sessions / explicit native selections keep their IDs and stay routable.
    known = {m['id'] for m in models}
    selected = payload.get('default_model_id')
    if selected not in known and family(selected):
        kind = family(selected)
        models.append({**original.get(selected, {}), 'id': selected,
                       'name': f"{selected} → {prov['family_map'][kind]} · {ROLES[kind]}", 'overflow': True})
    return {**payload, 'models': {**groups, 'anthropic': models}}
