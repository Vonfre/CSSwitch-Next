"""OpenAI Responses request/response conversion on top of the Chat adapter."""
import json


def from_chat(chat):
    items = []
    for m in chat["messages"]:
        role = m["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
            continue
        content = m.get("content")
        if content:
            if isinstance(content, str):
                content = [{"type": "output_text" if role == "assistant" else "input_text", "text": content}]
            else:
                content = [{"type": "input_image", "image_url": b["image_url"]["url"]} if b["type"] == "image_url"
                           else {"type": "output_text" if role == "assistant" else "input_text", "text": b["text"]}
                           for b in content]
            items.append({"role": role, "content": content})
        for tc in m.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": tc["id"], "name": tc["function"]["name"],
                          "arguments": tc["function"]["arguments"]})
    out = {"model": chat["model"], "input": items, "store": False, "stream": chat.get("stream", False)}
    for source, target in (("max_tokens", "max_output_tokens"), ("temperature", "temperature"), ("top_p", "top_p")):
        if source in chat:
            out[target] = chat[source]
    if chat.get("tools"):
        out["tools"] = [{"type": "function", **t["function"]} for t in chat["tools"]]
    tc = chat.get("tool_choice")
    if tc is not None:
        out["tool_choice"] = {"type": "function", "name": tc["function"]["name"]} if isinstance(tc, dict) else tc
    if chat.get("stop"):
        raise ValueError("OpenAI Responses does not support stop_sequences")
    return out


def to_anthropic(resp, model):
    if resp.get("error") or resp.get("status") == "failed":
        raise ValueError("Upstream Responses request failed")
    blocks = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for b in item.get("content", []):
                if b.get("type") == "output_text":
                    blocks.append({"type": "text", "text": b.get("text", "")})
                elif b.get("type") == "refusal":
                    blocks.append({"type": "text", "text": b.get("refusal", "")})
        elif item.get("type") == "function_call":
            blocks.append({"type": "tool_use", "id": item["call_id"], "name": item["name"],
                           "input": json.loads(item.get("arguments") or "{}")})
    if not blocks:
        raise ValueError("Upstream returned no text or tool calls")
    usage = resp.get("usage", {})
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    if resp.get("status") == "incomplete":
        stop = "max_tokens"
    return {"id": resp.get("id", "msg_proxy"), "type": "message", "role": "assistant", "model": model,
            "content": blocks, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                      "cache_read_input_tokens": usage.get("input_tokens_details", {}).get("cached_tokens", 0)}}
