"""Immutable upstream plans loaded at request boundaries, including legacy presets."""
import copy
import json
import os
import stat
from science_models import configure
from urllib.parse import urlsplit, urlunsplit


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


def load_profile(path, presets, selected=None):
    if os.path.islink(os.path.dirname(path)):
        raise ValueError("Refusing a symlinked configuration directory")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
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
        fast = p.get("fast_model", "").strip() or model
        cap = p.get("max_tokens", 8192)
        if not isinstance(cap, int) or not 1 <= cap <= 1_000_000:
            raise ValueError("Invalid output token limit")
        prov = {"mode": protocol, "url": endpoint_for(p["base_url"], protocol),
                "default_model": model, "default_cap": cap, "model_caps": {},
                "family_map": {"opus": quality, "sonnet": model, "haiku": fast},
                "models": [],
                "model_map": {"claude-opus-5": quality, "claude-opus-4-8": quality, "claude-sonnet-5": model,
                              "claude-sonnet-4-6": model, "claude-haiku-4-5": fast}}
    configure(prov)
    key = p.get("key", "").strip()
    if not key:
        raise ValueError("Save an API key for this connection first")
    if len(key) > 16384 or "\r" in key or "\n" in key:
        raise ValueError("Invalid API key")
    return {"provider": selected, "prov": prov, "key": key}
