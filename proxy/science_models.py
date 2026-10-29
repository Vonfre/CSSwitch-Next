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
FALLBACK = {'opus': 'claude-opus-5', 'sonnet': 'claude-sonnet-5'}
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
    if records:
        # The public selector catalog is authoritative, including older entries
        # under More models. Internal default literals are not extra menu items.
        unique = {}
        for mid, name, overflow in records:
            unique.setdefault(mid, {'id': mid, 'name': name, 'family': family(mid), 'overflow': overflow})
        return list(unique.values())
    models = []
    for kind in FALLBACK:
        ids = [mid for mid in literals if family(mid) == kind]
        if ids:
            mid = max(ids, key=rank)
            models.append({'id': mid, 'name': display_name(mid), 'family': kind, 'overflow': False})
    return models


@lru_cache(maxsize=4)
def _discover(path, inode, size, modified):
    try:
        models = read_catalog(path)
    except (OSError, ValueError):
        models = []
    if models:
        return {'models': models, 'source': 'installed'}
    return {'models': [{'id': mid, 'name': display_name(mid), 'family': kind, 'overflow': False}
                       for kind, mid in FALLBACK.items()], 'source': 'fallback'}



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


def mapped_name(name, model, prov):
    target = prov['family_map'].get(family(model))
    return name.split(' → ', 1)[0] + (' → ' + target if target else '')


def configure(prov):
    models = discover()['models']
    prov['native_models'] = models
    prov['models'] = [(m['id'], mapped_name(m['name'], m['id'], prov)) for m in models]
    prov['model_map'].update({m['id']: prov['family_map'][m['family']] for m in models})
    return prov


def adapt_model_response(payload, prov):
    """Show exactly the native catalog, with each model's current mapped target."""
    if not isinstance(payload, dict) or payload.get('first_party_catalog') or payload.get('model_list_source') == 'selector':
        return payload
    if payload.get('auth_error') or payload.get('fetch_error'):
        return payload
    groups = payload.get('models')
    if not isinstance(groups, dict) or not isinstance(groups.get('anthropic'), list):
        return payload
    from conversation_models import is_binding, native_id
    original = [m for m in groups['anthropic'] if isinstance(m, dict)]
    generated = not original or any(is_binding(m.get('id')) or m.get('csswitch_binding') or
        ' → ' in str(m.get('name', '')) or str(m.get('description', '')).startswith(('CSSwitch ', '由 CSSwitch'))
        for m in original)
    originals = {m.get('id'): m for m in original if not is_binding(m.get('id'))}
    native = prov['native_models'] if generated else original
    models = []
    for model in native:
        if is_binding(model.get('id')):
            continue
        item = {**originals.get(model['id'], {}), **model}
        item.pop('csswitch_binding', None)
        item.pop('family', None)
        if str(item.get('description', '')).startswith(('CSSwitch ', '由 CSSwitch')):
            item['description'] = ''
        item['name'] = mapped_name(model['name'], model['id'], prov)
        models.append(item)
    result = {**payload, 'models': {**groups, 'anthropic': models}}
    if 'default_model_id' in result:
        result['default_model_id'] = native_id(result['default_model_id'], native)
    return result
