import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from journal import EXTRACT_SCHEMA, InvalidInput, ModelUnavailable
from model_settings import DEFAULT_OPENAI_MODEL, OPENAI_BASE, ApiModel, ModelSettings, validated_url


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.settings = ModelSettings(self.root, "qwen3:4b")
        self.payload = {"provider": "openai", "model": DEFAULT_OPENAI_MODEL, "api_key": "test-key"}

    def test_persists_private_key_without_returning_it(self):
        self.assertEqual(self.settings.public()["provider"], "ollama")
        saved = self.settings.save(self.payload)
        self.assertTrue(saved["has_key"])
        self.assertNotIn("api_key", saved)
        self.assertFalse(saved["auto_analyze"])
        self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)
        fresh = ModelSettings(self.root, "qwen3:4b")
        self.assertEqual(fresh.to_model(fresh.current()).api_key, "test-key")
        self.assertEqual(fresh.save({**self.payload, "api_key": "", "auto_analyze": True})["auto_analyze"], True)
        self.assertFalse(fresh.clear_key()["has_key"])
        self.assertEqual(fresh.to_model(fresh.current()).status()["mode"], "api_missing")

    def test_retained_key_is_bound_to_provider_and_base_url(self):
        first = {**self.payload, "provider": "compatible", "base_url": "https://one.example/v1"}
        self.settings.save(first)
        with self.assertRaises(InvalidInput):
            self.settings.save({**first, "api_key": "", "base_url": "https://two.example/v1"})
        with self.assertRaises(InvalidInput):
            self.settings.save({**self.payload, "api_key": ""})
        self.assertEqual(self.settings.save({**first, "api_key": "", "base_url": "https://ONE.EXAMPLE/v1/"})["base_url"],
                         "https://one.example/v1")
        self.settings.save({"provider": "ollama", "model": "qwen3:1.7b"})
        self.assertFalse(self.settings.public()["has_key"])
        self.assertEqual(self.settings.public()["model"], "qwen3:1.7b")

    def test_test_does_not_persist_config_and_uses_constant_diagnostic(self):
        with patch.object(ApiModel, "chat", return_value={"ok": True}) as call:
            self.assertEqual(self.settings.test(self.payload), {"ok": True})
        self.assertIn("固定连接测试", call.call_args.args[1]["message"])
        self.assertFalse(self.settings.path.exists())

    def test_demo_refuses_real_credentials(self):
        demo = ModelSettings(self.root, "qwen3:4b", demo=True)
        with self.assertRaises(InvalidInput):
            demo.save(self.payload)
        with self.assertRaises(InvalidInput):
            demo.test(self.payload)
        self.assertFalse(demo.path.exists())

    def test_custom_base_requires_safe_address_format(self):
        self.assertEqual(validated_url("http://localhost:9999/v1/"), "http://localhost:9999/v1")
        for address in ("http://remote.example/v1", "https://user:pass@remote.example",
                        "https://remote.example/v1/chat/completions", "https://remote.example/v1?token=x",
                        "http://localhost@remote.example/v1"):
            with self.subTest(address=address), self.assertRaises(InvalidInput):
                validated_url(address)

    def test_openai_cannot_use_a_custom_base_url(self):
        saved = self.settings.save({**self.payload, "base_url": "https://unrelated.example/v1"})
        self.assertEqual(saved["base_url"], OPENAI_BASE)
        data = json.loads(self.settings.path.read_text())
        self.settings.path.write_text(json.dumps({**data, "base_url": "https://unrelated.example/v1"}))
        self.assertEqual(self.settings.current()["base_url"], OPENAI_BASE)


class ApiModelTests(unittest.TestCase):
    def simulated_request(self, provider, response):
        base = OPENAI_BASE if provider == "openai" else "https://example.com/v1"
        model = ApiModel(provider, DEFAULT_OPENAI_MODEL, base, "test-key")
        with patch.object(model.opener, "open", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            parsed = model.chat("提取信息", {"diary": "测试日记"}, EXTRACT_SCHEMA)
        return parsed, send.call_args.args[0]

    def test_openai_sends_strict_schema_and_reads_output_text(self):
        result, request = self.simulated_request("openai", {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": '{"summary":"摘要","events":[]}'}]},
            ],
        })
        self.assertEqual(result["summary"], "摘要")
        self.assertEqual(request.full_url, OPENAI_BASE + "/responses")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        schema = body["text"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["events"]["items"]["additionalProperties"])

    def test_compatible_uses_chat_completions_json_mode(self):
        result, request = self.simulated_request("compatible", {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"summary":"摘要","events":[]}'}}],
        })
        self.assertEqual(result["summary"], "摘要")
        self.assertEqual(request.full_url, "https://example.com/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["response_format"], {"type": "json_object"})

    def test_api_errors_do_not_expose_response_body_or_key(self):
        model = ApiModel("openai", DEFAULT_OPENAI_MODEL, OPENAI_BASE, "test-key")
        error = HTTPError(OPENAI_BASE, 401, "test-key / confidential", {}, None)
        with patch.object(model.opener, "open", side_effect=error):
            with self.assertRaisesRegex(ModelUnavailable, "^API Key 无法通过验证，请检查后重新填写。$"):
                model.chat("测试", {"diary": "测试日记"}, EXTRACT_SCHEMA)
