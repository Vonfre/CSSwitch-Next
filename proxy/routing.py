"""Immutable upstream plans loaded at request boundaries, including legacy presets."""
import copy
import json
import os
import stat
from science_models import configure, family, canonical
from urllib.parse import urlsplit, urlunsplit
from private_files import windows_private


SUFFIXES = {"anthropic": "/messages", "openai": "/chat/completions", "responses": "/responses"}


def endpoint_for(base, protocol):
    if protocol not in SUFFIXES:
        raise ValueError("Unsupported API protocol")
    u = urlsplit(base)
    if not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("API URL must not contain credentials, query parameters or fragments")
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")):
        raise ValueError("Use HTTPS, or HTTP on loopback")
    path = u.path.rstrip("/")
    suffix = SUFFIXES[protocol]
    for known in SUFFIXES.values():
        if path.endswith(known):
            if known != suffix:
                raise ValueError("API endpoint does not match the selected protocol")
            return urlunsplit((u.scheme, u.netloc, path, "", ""))
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((u.scheme, u.netloc, path + suffix, "", ""))


def model_endpoint(base, default_protocol, protocol):
    """Change only the inference suffix; keep the configured host and API prefix."""
    original = endpoint_for(base, default_protocol)
    return endpoint_for(original[:-len(SUFFIXES[default_protocol])], protocol)


def select_model_route(plan, name, target):
    """Pin one model/protocol pair in this request's snapshot, never global state."""
    exact = plan['prov'].get('native_routes', {})
    selected = exact.get(name) or exact.get(canonical(name or ''))
    routes = plan['prov'].get('model_routes')
    if selected:
        return {**plan, 'prov': {**plan['prov'], 'mode': selected['mode'],
                                'url': selected['url'], 'resolved_model': target}}
    if not routes:
        return plan
    kind = family(name) if name in plan['prov']['model_map'] else None
    if kind is None:
        # Exact upstream IDs keep their configured route, including Claude IDs.
        selected = next((r for r in exact.values() if r['model'] == name), None)
        if selected:
            return {**plan, 'prov': {**plan['prov'], 'mode': selected['mode'],
                                    'url': selected['url'], 'resolved_model': target}}
        kind = next((k for k in ('sonnet', 'opus', 'haiku', 'fable') if routes[k]['model'] == name), None)
    kind = kind or family(name) or 'sonnet'
    selected = routes[kind]
    return {**plan, 'prov': {**plan['prov'], 'mode': selected['mode'],
                            'url': selected['url'], 'resolved_model': target}}


def load_profile(path, presets, selected=None):
    if any(os.path.islink(p) or getattr(os.path, 'isjunction', lambda _: False)(p)
           for p in (path, os.path.dirname(path))):
        raise ValueError("Refusing a symlinked configuration directory")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        st = os.fstat(f.fileno())
        private = windows_private(f.fileno()) if os.name == 'nt' else st.st_uid == os.getuid() and not st.st_mode & 0o077
        if not stat.S_ISREG(st.st_mode) or not private:
            raise ValueError("Configuration must be owned by this user and have permission 0600")
        cfg = json.load(f)
    return profile_from_config(cfg, presets, selected)


def profile_from_config(cfg, presets, selected=None):
    selected = selected or cfg.get("provider", "deepseek")
    p = cfg.get("providers", {}).get(selected, {})
    if not p.get("base_url"):
        if selected not in presets:
            raise ValueError("Connection not found")
        prov = copy.deepcopy(presets[selected])
    else:
        protocol = p.get("protocol")
        model = p.get("model", "").strip()
        if not model:
            raise ValueError("Default model is required")
        quality = p.get("quality_model", "").strip() or model
        cap = p.get("max_tokens", 8192)
        if not isinstance(cap, int) or not 1 <= cap <= 1_000_000:
            raise ValueError("Invalid output token limit")
        prov = {"mode": protocol, "url": endpoint_for(p["base_url"], protocol),
                "default_model": model, "default_cap": cap, "model_caps": {},
                "family_map": {"opus": quality, "sonnet": model, "haiku": model},
                "models": [],
                "model_map": {"claude-opus-5": quality, "claude-opus-4-8": quality, "claude-sonnet-5": model,
                              "claude-sonnet-4-6": model, "claude-haiku-4-5": model}}
        routes = {}
        for kind, field, target in [('sonnet', 'model', model), ('opus', 'quality_model', quality)]:
            override = p.get(field.replace('_model', '') + '_protocol', '') if field != 'model' else ''
            if override and override not in SUFFIXES:
                raise ValueError('Unsupported model API protocol')
            # A blank model inherits both the default model and its protocol.
            mode = (override if p.get(field, '').strip() else '') or protocol
            routes[kind] = {'model': target, 'mode': mode, 'url': model_endpoint(p['base_url'], protocol, mode)}
        # Retired Haiku settings never override the default model or protocol.
        routes['haiku'] = dict(routes['sonnet'])
        routes['fable'] = dict(routes['opus'])
        families = p.get('family_mappings', {})
        if not isinstance(families, dict) or any(k not in routes for k in families):
            raise ValueError('Invalid model families')
        prov['four_slots'] = all(kind in families for kind in ('fable', 'opus', 'sonnet', 'haiku'))
        # Sonnet supplies the model for any empty slot; protocol choices stay independent.
        for kind in ('sonnet', 'opus', 'fable', 'haiku'):
            if kind not in families:
                continue
            mapping = families[kind]
            if not isinstance(mapping, dict) or not isinstance(mapping.get('model', ''), str):
                raise ValueError('Invalid family mapping')
            mode = mapping.get('protocol') or protocol
            if mode not in SUFFIXES:
                raise ValueError('Invalid family API protocol')
            target = mapping.get('model', '').strip() or routes['sonnet']['model']
            routes[kind] = {'model': target, 'mode': mode, 'url': model_endpoint(p['base_url'], protocol, mode)}
        prov['family_map'] = {kind: route['model'] for kind, route in routes.items()}
        prov['default_model'] = routes['sonnet']['model']
        prov['model_map'] = {mid: prov['family_map'][family(mid)] for mid in prov['model_map']}
        prov['model_routes'] = routes
        mappings = p.get('model_mappings', {})
        if not isinstance(mappings, dict) or len(mappings) > 256:
            raise ValueError('Invalid model mappings')
        prov['native_routes'] = {}
        for native, mapping in mappings.items():
            if not family(native) or not isinstance(mapping, dict):
                raise ValueError('Invalid native model mapping')
            if family(native) in families:
                continue  # A saved four-slot configuration controls all versions of that family.
            target, override = mapping.get('model', ''), mapping.get('protocol', '')
            if not isinstance(target, str) or override not in ('', *SUFFIXES):
                raise ValueError('Invalid model mapping or API protocol')
            target = target.strip()
            if not target:
                continue
            mode = override or protocol
            prov['native_routes'][native] = {'model': target, 'mode': mode,
                'url': model_endpoint(p['base_url'], protocol, mode)}
            prov['model_map'][native] = target
    configure(prov)
    key = p.get("key", "").strip()
    if not key:
        raise ValueError("Save an API key for this connection first")
    if len(key) > 16384 or "\r" in key or "\n" in key:
        raise ValueError("Invalid API key")
    return {"provider": selected, "prov": prov, "key": key}
