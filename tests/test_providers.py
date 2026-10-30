"""Any model, like T3 Chat: the OpenAI-compatible adapter, refs, the catalog, wire-safe tool names."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from aihq import llm


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake(responses, captured):
    def urlopen(request, timeout=0):
        captured.append({"url": request.full_url, "headers": dict(request.header_items()),
                         "body": json.loads(request.data.decode("utf-8")) if request.data else None})
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Reply(json.dumps(item).encode("utf-8"))
    return urlopen


TOOLS = [{"name": "calendar.create_event", "description": "make an event",
          "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}}}]


class OpenAICompatTests(unittest.TestCase):
    def test_request_shape_and_tool_round_trip(self):
        captured = []
        replies = [{"model": "gpt-5", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                       {"id": "call_1", "type": "function", "function": {"name": "calendar__create_event", "arguments": "{\"title\": \"Deadline: TPS\"}"}}]}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 7}}]
        model = llm.OpenAICompatModel("gpt-5", "openai", key="sk-test")
        messages = [{"role": "user", "content": [{"type": "text", "text": "grade this"},
                                                 {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]}]
        with mock.patch.object(llm.urllib.request, "urlopen", _fake(replies, captured)):
            out = model.complete("be brief", messages, TOOLS, max_tokens=500)
        sent = captured[0]
        self.assertEqual(sent["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(sent["headers"].get("Authorization"), "Bearer sk-test")
        body = sent["body"]
        self.assertEqual(body["messages"][0], {"role": "system", "content": "be brief"})
        self.assertEqual(body["messages"][1]["content"][1]["image_url"]["url"], "data:image/png;base64,AAAA")
        self.assertEqual(body["tools"][0]["function"]["name"], "calendar__create_event", "dots are not allowed on the wire")
        self.assertEqual(out["stop_reason"], "tool_use")
        self.assertEqual(out["content"][0], {"type": "tool_use", "id": "call_1", "name": "calendar.create_event",
                                             "input": {"title": "Deadline: TPS"}})
        self.assertEqual(out["usage"], {"input_tokens": 50, "output_tokens": 7})

        # the loop's next turn: assistant tool_use + user tool_result become tool_calls + a tool message
        captured.clear()
        replies = [{"choices": [{"finish_reason": "stop", "message": {"content": "Done. On your calendar."}}], "usage": {}}]
        follow = messages + [{"role": "assistant", "content": out["content"]},
                             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "created"}]}]
        with mock.patch.object(llm.urllib.request, "urlopen", _fake(replies, captured)):
            final = model.complete("be brief", follow, TOOLS)
        sent = captured[0]["body"]["messages"]
        self.assertEqual(sent[2]["role"], "assistant")
        self.assertEqual(sent[2]["tool_calls"][0]["function"]["name"], "calendar__create_event")
        self.assertEqual(json.loads(sent[2]["tool_calls"][0]["function"]["arguments"]), {"title": "Deadline: TPS"})
        self.assertEqual(sent[3], {"role": "tool", "tool_call_id": "call_1", "content": "created"})
        self.assertEqual(final["content"][0]["text"], "Done. On your calendar.")
        self.assertEqual(final["stop_reason"], "end_turn")

    def test_retries_then_raises_on_a_bad_request(self):
        def err(code):
            return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{\"error\": \"nope\"}"))
        captured = []
        replies = [err(429), {"choices": [{"message": {"content": "ok"}}]}]
        model = llm.OpenAICompatModel("m", "openrouter", key="k")
        with mock.patch.object(llm.urllib.request, "urlopen", _fake(replies, captured)), mock.patch.object(llm.time, "sleep", lambda s: None):
            self.assertEqual(model.complete("", [{"role": "user", "content": "hi"}])["content"][0]["text"], "ok")
        self.assertEqual(captured[0]["url"], "https://openrouter.ai/api/v1/chat/completions")
        with mock.patch.object(llm.urllib.request, "urlopen", _fake([err(400)], [])):
            with self.assertRaises(ValueError):
                model.complete("", [{"role": "user", "content": "hi"}])

    def test_server_tool_blocks_are_dropped_with_a_note(self):
        messages = [{"role": "assistant", "content": [{"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}},
                                                      {"type": "web_search_tool_result", "tool_use_id": "s", "content": []},
                                                      {"type": "text", "text": "found two"}]}]
        out = llm.to_openai("", messages)
        self.assertEqual(out[0]["role"], "assistant")
        self.assertIn("found two", out[0]["content"])
        self.assertIn("omitted", out[0]["content"])

    def test_capabilities(self):
        self.assertTrue(llm.OpenAICompatModel("gpt-5", "openai", key="k").supports_images)
        self.assertFalse(llm.OpenAICompatModel("gpt-5", "openai", key="k").supports_web)
        self.assertFalse(llm.OpenAICompatModel("qwen3:8b", "ollama").supports_images)
        self.assertTrue(llm.OpenAICompatModel("qwen2.5vl", "ollama").supports_images)
        with mock.patch.object(llm, "provider_key", lambda p: None):
            with self.assertRaises(ValueError):
                llm.OpenAICompatModel("gpt-5", "openai")


class AnthropicWireNameTests(unittest.TestCase):
    def test_dotted_tool_names_are_translated_both_ways(self):
        captured = []
        replies = [{"content": [{"type": "tool_use", "id": "t1", "name": "telegram__send", "input": {"text": "hi"}}],
                    "stop_reason": "tool_use", "usage": {}}]
        model = llm.AnthropicModel("claude-sonnet-5-5", key="k")
        history = [{"role": "user", "content": "go"},
                   {"role": "assistant", "content": [{"type": "tool_use", "id": "t0", "name": "calendar.create_event", "input": {}}]},
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t0", "content": "ok"}]}]
        tools = TOOLS + [{"name": "telegram.send", "input_schema": {"type": "object"}}, dict(llm.WEB_SEARCH_TOOL)]
        with mock.patch.object(llm.urllib.request, "urlopen", _fake(replies, captured)):
            out = model.complete("s", history, tools, web=False)
        body = captured[0]["body"]
        self.assertEqual([t.get("name") for t in body["tools"]], ["calendar__create_event", "telegram__send", "web_search"])
        self.assertEqual(body["messages"][1]["content"][0]["name"], "calendar__create_event")
        self.assertEqual(out["content"][0]["name"], "telegram.send")


class ResolveAndCatalogTests(unittest.TestCase):
    def test_resolve(self):
        with mock.patch.object(llm, "api_key", lambda: "k"):
            self.assertEqual(llm.resolve("anthropic:claude-opus-5-5").name, "claude-opus-5-5")
        model = llm.resolve("ollama:qwen3:8b")
        self.assertIsInstance(model, llm.OpenAICompatModel)
        self.assertEqual((model.provider, model.name), ("ollama", "qwen3:8b"))
        with mock.patch.object(llm, "provider_key", lambda p: "k"):
            self.assertEqual(llm.resolve("gemini:gemini-2.5-pro").base_url, "https://generativelanguage.googleapis.com/v1beta/openai")
        with self.assertRaises(ValueError):
            llm.resolve("nobody:model")

    def test_catalog_and_saved_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = Path(tmp)
            with mock.patch.object(llm, "provider_key", lambda p: "k" if p == "openai" else None), \
                 mock.patch.object(llm, "api_key", lambda: None):
                llm.save_model("openrouter:meta-llama/llama-4-maverick", domain)
                with self.assertRaises(ValueError):
                    llm.save_model("not-a-ref", domain)
                items = llm.catalog(domain, probe_ollama=False)
            refs = {i["ref"]: i for i in items}
            self.assertTrue(refs["openai:gpt-5"]["configured"])
            self.assertFalse(refs["gemini:gemini-2.5-pro"]["configured"])
            self.assertFalse(refs["anthropic:claude-sonnet-5-5"]["configured"])
            self.assertIn("openrouter:meta-llama/llama-4-maverick", refs)
            self.assertTrue(refs["openrouter:"].get("placeholder"))


if __name__ == "__main__":
    unittest.main()
