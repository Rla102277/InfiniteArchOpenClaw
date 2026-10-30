import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq import llm


class LlmTests(unittest.TestCase):
    def test_stub_scripts_and_records(self):
        stub = llm.StubModel(["hello", [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]])
        first = stub.complete("sys", [{"role": "user", "content": "hi"}], None, web=True)
        self.assertEqual(first["content"][0]["text"], "hello")
        self.assertEqual(stub.calls[0]["web"], True)
        second = stub.complete("sys", [], None)
        self.assertEqual(second["stop_reason"], "tool_use")
        self.assertEqual(stub.complete("sys", [], None)["content"][0]["text"], "Stub has nothing more to say.")
        self.assertTrue(stub.supports_images and stub.supports_web)

    def test_model_ids_default_and_from_os_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "system").mkdir()
            (root / "system" / "os.json").write_text(json.dumps({"schema_version": 1}))
            self.assertEqual(llm.model_ids(root), llm.DEFAULT_MODELS)
            (root / "system" / "os.json").write_text(json.dumps({"schema_version": 1, "models": {"deep": "claude-x"}}))
            ids = llm.model_ids(root)
            self.assertEqual(ids["deep"], "claude-x")
            self.assertEqual(ids["default"], llm.DEFAULT_MODELS["default"])

    def test_flatten_marks_images_and_tool_results(self):
        text = llm.flatten([{"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": "grade this"}]},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}])
        self.assertIn("cannot see images", text)
        self.assertIn("TOOL RESULT: ok", text)

    def test_anthropic_requires_key_and_adds_web_tool(self):
        with patch.object(llm, "api_key", return_value=""), self.assertRaises(ValueError):
            llm.AnthropicModel("m", key="")
        model = llm.AnthropicModel("m", key="k")
        self.assertTrue(model.supports_images and model.supports_web)
        captured = {}

        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(request, timeout=0):
            captured["body"] = json.loads(request.data)
            return Resp(json.dumps({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}).encode())

        original = llm.urllib.request.urlopen
        llm.urllib.request.urlopen = fake_open
        try:
            out = model.complete("s", [{"role": "user", "content": "hi"}], [{"name": "t", "input_schema": {"type": "object"}}], web=True)
        finally:
            llm.urllib.request.urlopen = original
        self.assertEqual(out["content"][0]["text"], "ok")
        names = [t.get("name") for t in captured["body"]["tools"]]
        self.assertEqual(names, ["t", "web_search"])
        self.assertEqual(captured["body"]["tools"][1]["type"], "web_search_20250305")

    def test_backend_status_shape(self):
        status = llm.backend_status("hermes")
        self.assertIn(status["chosen"], {"hermes", "missing"})
        self.assertIn("default", status)
        self.assertIn("order", status)


if __name__ == "__main__":
    unittest.main()
