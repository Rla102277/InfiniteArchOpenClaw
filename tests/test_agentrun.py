import unittest

from aihq import agentrun, llm


class Echo:
    def __init__(self):
        self.called = []

    def specs(self):
        return [{"name": "echo", "description": "echo", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}]

    def call(self, name, args):
        self.called.append((name, args))
        return "echoed: " + str(args.get("text"))


class AgentRunTests(unittest.TestCase):
    def test_tool_loop_round_trip(self):
        stub = llm.StubModel([[{"type": "text", "text": "Let me check."}, {"type": "tool_use", "id": "t1", "name": "echo", "input": {"text": "hi"}}],
                              "Verdict: hi."])
        belt = Echo()
        out = agentrun.run(stub, "system", [{"role": "user", "content": "say hi"}], belt, max_steps=4)
        self.assertEqual(belt.called, [("echo", {"text": "hi"})])
        self.assertIn("Verdict: hi.", out["answer"])
        self.assertEqual(out["stop"], "end_turn")
        self.assertEqual(out["tool_calls"][0]["result"], "echoed: hi")
        # the tool result went back to the model as a tool_result block
        second_call = stub.calls[1]["messages"]
        self.assertEqual(second_call[-1]["content"][0]["type"], "tool_result")
        self.assertEqual(second_call[-1]["content"][0]["tool_use_id"], "t1")
        self.assertEqual(out["usage"], {"input": 20, "output": 10})
        self.assertEqual(len(out["messages"]), 4)

    def test_server_tool_blocks_pass_through(self):
        server = [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "art shows"}},
                  {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [{"type": "web_search_result", "url": "https://x", "title": "X"}]},
                  {"type": "text", "text": "Found one."}]
        stub = llm.StubModel([{"content": server, "stop_reason": "end_turn"}])
        out = agentrun.run(stub, "s", [{"role": "user", "content": "find"}], Echo(), web=True)
        self.assertEqual(out["answer"], "Found one.")
        self.assertTrue(out["web"])
        self.assertEqual(out["messages"][-1]["content"], server, "server blocks stay in the transcript untouched")
        self.assertTrue(stub.calls[0]["web"])

    def test_web_off_for_models_that_cannot(self):
        stub = llm.StubModel(["ok"], web=False)
        out = agentrun.run(stub, "s", [{"role": "user", "content": "x"}], None, web=True)
        self.assertFalse(out["web"])
        self.assertFalse(stub.calls[0]["web"])

    def test_step_cap_and_unknown_tool(self):
        responses = [[{"type": "tool_use", "id": f"t{i}", "name": "nope", "input": {}}] for i in range(5)]
        stub = llm.StubModel(responses)
        out = agentrun.run(stub, "s", [{"role": "user", "content": "x"}], Echo(), max_steps=3)
        self.assertEqual(out["stop"], "max_steps")
        self.assertEqual(len(out["tool_calls"]), 3)
        self.assertTrue(out["tool_calls"][0]["result"].startswith("Unknown tool"))

    def test_config_error_raises_plainly(self):
        class Broken(llm.Model):
            def complete(self, *a, **k):
                raise ValueError("No Anthropic API key. Set ANTHROPIC_API_KEY")
        with self.assertRaises(ValueError):
            agentrun.run(Broken(), "s", [{"role": "user", "content": "x"}])

    def test_later_error_lands_in_answer(self):
        class Flaky(llm.StubModel):
            def complete(self, *a, **k):
                if len(self.calls) == 1:
                    self.calls.append({})
                    raise ValueError("Anthropic API 529: overloaded")
                return super().complete(*a, **k)
        stub = Flaky([[{"type": "tool_use", "id": "t1", "name": "echo", "input": {"text": "a"}}]])
        out = agentrun.run(stub, "s", [{"role": "user", "content": "x"}], Echo())
        self.assertEqual(out["stop"], "error")
        self.assertIn("overloaded", out["answer"])

    def test_composite_first_belt_wins(self):
        a, b = Echo(), Echo()
        belt = agentrun.Composite([a, b])
        self.assertEqual(len(belt.specs()), 1)
        belt.call("echo", {"text": "x"})
        self.assertEqual((len(a.called), len(b.called)), (1, 0))
        self.assertTrue(belt.call("zzz", {}).startswith("Unknown tool"))

    def test_max_tokens_continues_once(self):
        stub = llm.StubModel([{"content": [{"type": "text", "text": "part one"}], "stop_reason": "max_tokens"},
                              {"content": [{"type": "text", "text": "part two"}], "stop_reason": "max_tokens"}])
        out = agentrun.run(stub, "s", [{"role": "user", "content": "x"}])
        self.assertEqual(out["answer"], "part one\n\npart two")
        self.assertEqual(out["stop"], "max_tokens")
        self.assertEqual(len(stub.calls), 2)


if __name__ == "__main__":
    unittest.main()
