"""Bounded local usage journal; stores counts, never prompts or credentials."""
import json
import os
import threading
import time
from collections import deque

LOCK = threading.Lock()


def record(path, provider, model, started, usage=None, ok=True):
    if not path:
        return
    usage = usage or {}
    entry = {"time": int(time.time()), "provider": provider, "model": model,
             "latency_ms": round((time.monotonic() - started) * 1000), "ok": ok,
             "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
             "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0)}
    with LOCK:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if os.path.getsize(path) > 4 * 1024 * 1024:
            with open(path, encoding="utf-8") as f:
                tail = deque(f, maxlen=2000)
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(tail)
            os.replace(tmp, path)


class AnthropicMeter:
    def __init__(self):
        self.buffer = b""
        self.usage = {}
        self.complete = False

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > 2 * 1024 * 1024:
            raise ValueError("Upstream SSE event exceeds 2 MB")
        while b"\n\n" in self.buffer or b"\r\n\r\n" in self.buffer:
            separator = b"\r\n\r\n" if b"\r\n\r\n" in self.buffer else b"\n\n"
            event, self.buffer = self.buffer.split(separator, 1)
            data = b"\n".join(line[5:].strip() for line in event.splitlines() if line.startswith(b"data:"))
            if not data:
                continue
            obj = json.loads(data)
            if obj.get("type") == "message_start":
                self.usage.update(obj.get("message", {}).get("usage", {}))
            elif obj.get("type") == "message_delta":
                self.usage.update(obj.get("usage", {}))
            elif obj.get("type") == "message_stop":
                self.complete = True
            elif obj.get("type") == "error":
                raise ValueError("Upstream Anthropic stream returned an error")
