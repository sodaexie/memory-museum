"""Private, per-installation model settings and cloud model adapters."""

import json
import re
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from journal import DemoModel, InvalidInput, JournalModel, ModelUnavailable, NoRedirect, Ollama, atomic_write, read_json

DEFAULT_OPENAI_MODEL = "gpt-6-luna"
OPENAI_BASE = "https://api.openai.com/v1"
PROVIDERS = {"ollama", "openai", "compatible"}
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


def validated_model(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", value.strip()):
        raise InvalidInput("请输入服务商提供的模型名称。")
    return value.strip()


def validated_url(value):
    invalid = "请输入有效的 API 基础地址，例如 https://example.com/v1。"
    if not isinstance(value, str) or len(value) > 500 or re.search(r"[\s\\\x00-\x1f\x7f]", value.strip()):
        raise InvalidInput(invalid)
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise InvalidInput(invalid) from exc
    if not host or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise InvalidInput(invalid)
    if parts.scheme != "https" and not (
        parts.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}
    ):
        raise InvalidInput("远程 API 地址需要使用 HTTPS。本机回环地址可以使用 HTTP。")
    path = parts.path.rstrip("/")
    if path.endswith(("/chat/completions", "/responses")):
        raise InvalidInput("请填写基础地址，例如 https://example.com/v1，结尾保留到 /v1。")
    port_suffix = f":{port}" if port is not None and (parts.scheme, port) not in {("https", 443), ("http", 80)} else ""
    authority = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme, authority + port_suffix, path, "", ""))


def validated_key(value):
    if not isinstance(value, str):
        raise InvalidInput("请输入有效的 API Key。")
    key = value.strip()
    if len(key) > 4096 or (key and not re.fullmatch(r"[!-~]+", key)):
        raise InvalidInput("API Key 含有无效字符。请从服务商复制原始 Key。")
    return key


def strict_schema(schema):
    result = dict(schema)
    if result.get("type") == "object":
        properties = {name: strict_schema(child) for name, child in schema["properties"].items()}
        result.update({"properties": properties, "required": list(properties), "additionalProperties": False})
    elif result.get("type") == "array":
        result["items"] = strict_schema(schema["items"])
    return result


class ApiModel(JournalModel):
    def __init__(self, provider, model, base_url, api_key):
        self.provider, self.model, self.base_url, self.api_key = provider, model, base_url, api_key
        if urlsplit(base_url).hostname in {"127.0.0.1", "::1", "localhost"}:
            self.opener = build_opener(ProxyHandler({}), NoRedirect())
        else:
            self.opener = build_opener(NoRedirect())

    def status(self):
        return {"mode": "api_configured" if self.api_key else "api_missing", "model": self.model}

    def request(self, path, payload, timeout):
        if not self.api_key:
            raise ModelUnavailable("请在「模型设置」填写你的 API Key。")
        request = Request(
            self.base_url + path, data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
        except HTTPError as exc:
            errors = {
                400: "API 不接受这次请求。请确认模型支持所选接口的 JSON 输出模式。",
                401: "API Key 无法通过验证，请检查后重新填写。",
                403: "这个 API Key 暂无访问所选模型的权限。",
                404: "没有找到接口或模型。请检查 API 地址和模型名称。",
                429: "API 暂时达到额度或请求频率限制，请稍后再试。",
            }
            raise ModelUnavailable(errors.get(exc.code, f"模型服务暂时不可用（HTTP {exc.code}）。")) from exc
        except (OSError, URLError, ValueError) as exc:
            raise ModelUnavailable("暂时连不上模型服务，或服务返回了无法读取的数据。请检查网络和 API 地址。") from exc

    def chat(self, instruction, content, schema, timeout=90):
        user_input = json.dumps(content, ensure_ascii=False)
        if self.provider == "openai":
            response = self.request("/responses", {
                "model": self.model, "store": False, "max_output_tokens": 4096,
                "instructions": instruction, "input": user_input,
                "text": {"format": {"type": "json_schema", "name": "xinji_result",
                                    "schema": strict_schema(schema), "strict": True}},
            }, timeout)
        else:
            response = self.request("/chat/completions", {
                "model": self.model, "stream": False,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instruction + "\n只返回一个 JSON 对象，字段结构："
                     + json.dumps(schema, ensure_ascii=False)},
                    {"role": "user", "content": user_input},
                ],
            }, timeout)

        try:
            if self.provider == "openai":
                if response["status"] != "completed":
                    raise ValueError("Incomplete response")
                parts = [
                    part["text"] for item in response["output"] if item["type"] == "message"
                    for part in item["content"] if part["type"] == "output_text"
                ]
                raw = "".join(parts)
            else:
                choice = response["choices"][0]
                if choice.get("finish_reason") in {"length", "content_filter"}:
                    raise ValueError("Incomplete response")
                raw = choice["message"]["content"]
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Expected object")
            return value
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelUnavailable("模型这次没有返回完整的 JSON 整理结果，可以重新尝试。") from exc


class ModelSettings:
    def __init__(self, directory, default_model, demo=False):
        self.path = Path(directory) / ".model-settings.json"
        self.demo = demo
        self.default = {"provider": "ollama", "model": validated_model(default_model), "base_url": "",
                        "api_key": "", "auto_analyze": True}
        Ollama(self.default["model"])  # Validate the existing local CLI default before starting.
        self.lock = threading.RLock()

    def current(self):
        saved = read_json(self.path, {})
        provider = saved.get("provider")
        if not isinstance(provider, str) or provider not in PROVIDERS:
            return dict(self.default)
        try:
            model = validated_model(saved.get("model"))
            if provider == "ollama":
                Ollama(model)
                return {**self.default, "model": model}
            base_url = OPENAI_BASE if provider == "openai" else validated_url(saved.get("base_url"))
            return {
                "provider": provider, "model": model, "base_url": base_url,
                "api_key": validated_key(saved.get("api_key", "")),
                "auto_analyze": saved.get("auto_analyze") is True,
            }
        except InvalidInput:
            return dict(self.default)

    def public(self):
        config = self.current()
        return {
            **{name: config[name] for name in ("provider", "model", "base_url", "auto_analyze")},
            "has_key": bool(config["api_key"]), "demo": self.demo,
            "default_openai_model": DEFAULT_OPENAI_MODEL,
        }

    def candidate(self, payload):
        provider = payload.get("provider")
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise InvalidInput("请先选择一种模型连接方式。")
        model = validated_model(payload.get("model"))
        if provider == "ollama":
            Ollama(model)
            return {"provider": provider, "model": model, "base_url": "", "api_key": "", "auto_analyze": True}
        base_url = OPENAI_BASE if provider == "openai" else validated_url(payload.get("base_url"))
        old = self.current()
        key = validated_key(payload.get("api_key", ""))
        if not key and old["provider"] == provider and old["base_url"] == base_url:
            key = old["api_key"]
        if not key:
            raise InvalidInput("请填写此 API 地址对应的 API Key。")
        return {"provider": provider, "model": model, "base_url": base_url, "api_key": key,
                "auto_analyze": payload.get("auto_analyze") is True}

    def save(self, payload):
        self.require_personal_mode()
        with self.lock:
            config = self.candidate(payload)
            atomic_write(self.path, json.dumps(config, ensure_ascii=False, indent=2))
            return self.public()

    def clear_key(self):
        self.require_personal_mode()
        with self.lock:
            config = self.current()
            config.update({"api_key": "", "auto_analyze": config["provider"] == "ollama"})
            atomic_write(self.path, json.dumps(config, ensure_ascii=False, indent=2))
            return self.public()

    def require_personal_mode(self):
        if self.demo:
            raise InvalidInput("请在个人模式下连接模型。")

    def to_model(self, config):
        if self.demo:
            return DemoModel()
        if config["provider"] == "ollama":
            return Ollama(config["model"])
        return ApiModel(config["provider"], config["model"], config["base_url"], config["api_key"])

    def test(self, payload):
        self.require_personal_mode()
        candidate = self.candidate(payload)
        model = self.to_model(candidate)
        arguments = ("你是连接测试助手。只返回一个 JSON 对象，字段 ok 的值为 true。",
                     {"message": "心迹的固定连接测试"}, PROBE_SCHEMA)
        if isinstance(model, ApiModel):
            result = model.chat(*arguments, timeout=25)
        else:
            if model.status()["mode"] != "ready":
                raise ModelUnavailable("请先打开 Ollama，并下载所填的本地模型。")
            result = model.chat(*arguments)
        if result.get("ok") is not True:
            raise ModelUnavailable("服务已响应，但模型没有按测试要求返回 JSON。")
        return {"ok": True}
