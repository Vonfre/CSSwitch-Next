"""Read-only model discovery. Credentials arrive on stdin, never argv or logs."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from routing import endpoint_for, SUFFIXES


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def models_endpoint(base, protocol):
    inference = urllib.parse.urlsplit(endpoint_for(base, protocol))
    path = inference.path[:-len(SUFFIXES[protocol])] + "/models"
    # DeepSeek's Anthropic adapter has no catalog of its own.
    if inference.hostname == "api.deepseek.com" and path == "/anthropic/v1/models":
        path = "/models"
    return urllib.parse.urlunsplit((inference.scheme, inference.netloc, path, "", ""))


def fetch_models(base, protocol, key, opener=None):
    endpoint = models_endpoint(base, protocol)
    if not key or any(c in key for c in "\r\n"):
        raise ValueError("请填写 API Key。")
    opener = opener or urllib.request.build_opener(NoRedirect)
    headers = {"Accept": "application/json"}
    if protocol == "anthropic" and urllib.parse.urlsplit(endpoint).hostname != "api.deepseek.com":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = "Bearer " + key
    models = {}
    after = None
    started = time.monotonic()
    for _ in range(20):
        remaining = 30 - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("模型列表获取超时，请重试。")
        url = endpoint
        if protocol == "anthropic":
            url += "?" + urllib.parse.urlencode({"limit": 100, **({"after_id": after} if after else {})})
        request = urllib.request.Request(url, headers=headers)
        with opener.open(request, timeout=min(10, remaining)) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("模型列表响应过大。")
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError("该 API 未返回标准模型列表，请手动输入模型 ID。")
        for model in data["data"]:
            if not isinstance(model, dict):
                continue
            identifier = model.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 512 or any(ord(c) < 32 for c in identifier):
                continue
            name = model.get("display_name") or model.get("name") or identifier
            models[identifier] = {"id": identifier, "name": str(name)[:512]}
            if len(models) > 20000:
                raise ValueError("模型列表过大，请使用更具体的 API 地址。")
        if not data.get("has_more"):
            break
        next_id = data.get("last_id")
        if protocol != "anthropic" or not isinstance(next_id, str) or not next_id or next_id == after:
            raise ValueError("模型列表分页无效，请手动输入模型 ID。")
        after = next_id
    else:
        raise ValueError("模型列表分页过多，请手动输入模型 ID。")
    if not models:
        raise ValueError("该 API 没有返回可用模型，请手动输入模型 ID。")
    return sorted(models.values(), key=lambda model: model["id"].lower())


def main():
    try:
        payload = json.loads(sys.stdin.read(65536))
        models = fetch_models(payload["base_url"], payload["protocol"], payload["key"])
        result = {"ok": True, "models": models}
    except urllib.error.HTTPError as error:
        hints = {401: "API Key 无效或未授权。", 403: "没有读取模型列表的权限。", 404: "该 API 不提供模型列表，请手动输入。", 429: "模型列表请求过于频繁，请稍后重试。"}
        result = {"ok": False, "hint": hints.get(error.code, f"模型列表接口返回 HTTP {error.code}。")}
    except ValueError as error:
        # Never surface arbitrary upstream bodies or decoder excerpts containing credentials.
        hint = str(error) if type(error) is ValueError else "模型列表响应无法解析。"
        result = {"ok": False, "hint": hint}
    except Exception:
        result = {"ok": False, "hint": "无法获取模型列表，请检查 API 地址与网络。"}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
