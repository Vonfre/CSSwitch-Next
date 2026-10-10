"""Translate incremental Chat / Responses SSE events to Anthropic SSE events."""
import json
import uuid


def events(response):
    data = []
    size = 0
    while True:
        raw = response.readline(1024 * 1024 + 1)
        if not raw:
            if data:
                yield json.loads("\n".join(data))
            return
        if len(raw) > 1024 * 1024:
            raise ValueError("Upstream SSE line exceeds 1 MB")
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                payload = "\n".join(data)
                data, size = [], 0
                if payload == "[DONE]":
                    return
                yield json.loads(payload)
        elif line.startswith("data:"):
            part = line[5:].lstrip(" ")
            size += len(part)
            if size > 2 * 1024 * 1024:
                raise ValueError("Upstream SSE event exceeds 2 MB")
            data.append(part)


class Bridge:
    def __init__(self, handler, model):
        self.handler = handler
        self.blocks = {}
        self.arguments = {}
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.stop = "end_turn"
        self.complete = False
        self.seen_content = False
        self.emit("message_start", {"message": {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                  "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": self.usage.copy()}})

    def emit(self, kind, value):
        self.handler._sse(kind, {"type": kind, **value})

    def block(self, key, block):
        if key not in self.blocks:
            index = len(self.blocks)
            self.blocks[key] = index
            self.emit("content_block_start", {"index": index, "content_block": block})
        return self.blocks[key]

    def text(self, text, key="text"):
        if not text:
            return
        self.seen_content = True
        idx = self.block(key, {"type": "text", "text": ""})
        self.emit("content_block_delta", {"index": idx, "delta": {"type": "text_delta", "text": text}})

    def tool(self, key, call_id=None, name=None, arguments=None):
        state = self.arguments.setdefault(key, {"id": None, "name": None, "buffer": "", "sent": 0})
        state["id"] = call_id or state["id"]
        state["name"] = name or state["name"]
        state["buffer"] += arguments or ""
        if state["id"] and state["name"]:
            self.seen_content = True
            idx = self.block(key, {"type": "tool_use", "id": state["id"], "name": state["name"], "input": {}})
            delta = state["buffer"][state["sent"]:]
            if delta:
                self.emit("content_block_delta", {"index": idx, "delta": {"type": "input_json_delta", "partial_json": delta}})
                state["sent"] = len(state["buffer"])
            self.stop = "tool_use"

    def chat(self, event):
        if event.get("error"):
            raise ValueError("Upstream stream returned an error")
        usage = event.get("usage") or {}
        if usage:
            self.usage = {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0),
                          "cache_read_input_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)}
        for c in event.get("choices", []):
            if c.get("index", 0) != 0:
                continue
            d = c.get("delta") or {}
            self.text(d.get("content"))
            for tc in d.get("tool_calls", []):
                f = tc.get("function") or {}
                self.tool(("tool", tc.get("index", 0)), tc.get("id"), f.get("name"), f.get("arguments"))
            if c.get("finish_reason"):
                self.complete = True
                self.stop = {"length": "max_tokens", "tool_calls": "tool_use", "stop": "end_turn"}.get(c["finish_reason"], "end_turn")

    def responses(self, event):
        t = event.get("type")
        key = ("tool", event.get("output_index", 0))
        if t in ("error", "response.failed"):
            raise ValueError("Upstream Responses stream failed")
        if t in ("response.output_text.delta", "response.refusal.delta"):
            self.text(event.get("delta"), ("text", event.get("output_index", 0), event.get("content_index", 0)))
        elif t == "response.output_item.added" and event.get("item", {}).get("type") == "function_call":
            item = event["item"]
            self.tool(key, item.get("call_id"), item.get("name"), item.get("arguments"))
        elif t == "response.function_call_arguments.delta":
            self.tool(key, arguments=event.get("delta"))
        elif t in ("response.completed", "response.incomplete"):
            resp = event.get("response", {})
            u = resp.get("usage") or {}
            self.usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                          "cache_read_input_tokens": u.get("input_tokens_details", {}).get("cached_tokens", 0)}
            self.complete = True
            if t == "response.incomplete":
                self.stop = "max_tokens"

    def finish(self):
        if not self.complete or not self.seen_content:
            raise ValueError("Upstream stream ended before a complete response")
        for state in self.arguments.values():
            if not state["id"] or not state["name"]:
                raise ValueError("Incomplete upstream tool call")
            json.loads(state["buffer"] or "{}")
        for idx in self.blocks.values():
            self.emit("content_block_stop", {"index": idx})
        self.emit("message_delta", {"delta": {"stop_reason": self.stop, "stop_sequence": None}, "usage": self.usage})
        self.emit("message_stop", {})
        self.handler.wfile.write(b"0\r\n\r\n")
