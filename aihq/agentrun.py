"""The tool loop every OS agent runs on.

run() feeds the model, executes tool_use blocks through a toolbelt, passes server-side tool
blocks (web search) straight back, and stops at end_turn or the step cap. It returns the whole
transcript so a chat can store it and continue from it.

A toolbelt is anything with specs() -> list[dict] (Anthropic tool schema) and call(name, args)
-> str. Composite joins several; the first belt that knows a name handles it.
"""
from __future__ import annotations

import json
import time

SERVER_BLOCKS = {"server_tool_use", "web_search_tool_result"}
ANSWER_LIMIT = 64000
TOOL_RESULT_LIMIT = 40000


class Composite:
    """Several toolbelts as one. Later belts cannot shadow an earlier belt's tool."""

    def __init__(self, belts: list):
        self.belts = [belt for belt in belts if belt is not None]

    def specs(self) -> list[dict]:
        out, seen = [], set()
        for belt in self.belts:
            for spec in belt.specs():
                if spec["name"] not in seen:
                    seen.add(spec["name"])
                    out.append(spec)
        return out

    def call(self, name: str, args: dict) -> str:
        for belt in self.belts:
            if any(spec["name"] == name for spec in belt.specs()):
                return belt.call(name, args)
        return f"Unknown tool: {name}"


class _NoTools:
    def specs(self):
        return []

    def call(self, name, args):
        return f"Unknown tool: {name}"


def _text_of(blocks: list[dict]) -> str:
    return "\n".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text").strip()


def _usage_add(total: dict, usage: dict | None) -> None:
    usage = usage or {}
    total["input"] += int(usage.get("input_tokens", 0) or 0)
    total["output"] += int(usage.get("output_tokens", 0) or 0)


def _is_config_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(word in text for word in ("api key", "authenticate", "authentication", "not on path", "no model backend", "401", "403"))


def run(model, system: str, messages: list[dict], toolbelt=None, max_steps: int = 16, on_step=None,
        web: bool = False, max_tokens: int = 4096) -> dict:
    """Run the loop. `messages` is the conversation so far (Anthropic format); it is not mutated.

    Returns {"answer", "messages", "tool_calls", "usage": {"input", "output"}, "stop", "seconds", "steps"}.
    Config and auth problems raise ValueError with a plain message; anything else ends the run
    with the error in the answer.
    """
    belt = toolbelt or _NoTools()
    specs = belt.specs() if getattr(model, "supports_tools", True) else []
    transcript = [dict(message) for message in messages]
    tool_calls: list[dict] = []
    usage = {"input": 0, "output": 0}
    started = time.time()
    stop = "max_steps"
    answer = ""
    continued = False
    use_web = bool(web and getattr(model, "supports_web", False))
    for step in range(1, max_steps + 1):
        try:
            response = model.complete(system, list(transcript), specs or None, max_tokens=max_tokens, web=use_web)
        except ValueError as exc:
            if _is_config_error(exc) or step == 1:
                raise ValueError(str(exc)) from exc
            answer = (answer + "\n\n" if answer else "") + f"The run stopped: {exc}"
            stop = "error"
            break
        except Exception as exc:  # the loop never takes the dashboard down
            answer = (answer + "\n\n" if answer else "") + f"The run stopped: {exc}"
            stop = "error"
            break
        content = response.get("content") or []
        _usage_add(usage, response.get("usage"))
        reason = response.get("stop_reason") or "end_turn"
        transcript.append({"role": "assistant", "content": content})
        text = _text_of(content)
        if text:
            answer = (answer + "\n\n" + text) if answer else text
        uses = [block for block in content if block.get("type") == "tool_use"]
        if on_step:
            try:
                on_step({"step": step, "text": text, "tool_uses": [u.get("name") for u in uses], "stop_reason": reason})
            except Exception:
                pass
        if uses:
            results = []
            for use in uses:
                name, args = str(use.get("name", "")), use.get("input") or {}
                if not isinstance(args, dict):
                    args = {"value": args}
                try:
                    if specs and name not in {spec["name"] for spec in specs}:
                        result = f"Unknown tool: {name}"
                    else:
                        result = str(belt.call(name, args))
                except Exception as exc:
                    result = f"Tool {name} failed: {exc}"
                result = result[:TOOL_RESULT_LIMIT]
                tool_calls.append({"name": name, "args": args, "result": result[:4000]})
                results.append({"type": "tool_result", "tool_use_id": use.get("id", f"call-{step}"), "content": result})
            transcript.append({"role": "user", "content": results})
            continue
        if reason == "max_tokens" and not continued:
            continued = True
            transcript.append({"role": "user", "content": [{"type": "text", "text": "Continue exactly where you stopped."}]})
            continue
        stop = reason
        break
    return {"answer": answer[:ANSWER_LIMIT] or "No answer.", "messages": transcript, "tool_calls": tool_calls,
            "usage": usage, "stop": stop, "seconds": round(time.time() - started, 2), "steps": len(tool_calls),
            "model": getattr(model, "name", "model"), "backend": getattr(model, "backend", "none"), "web": use_web}


def transcript_text(result: dict) -> str:
    """The run as readable Markdown, for logs and chat storage."""
    lines = [result.get("answer", "")]
    if result.get("tool_calls"):
        lines.append("\n<details><summary>Tool calls</summary>\n")
        for call in result["tool_calls"]:
            lines.append(f"- `{call['name']}` {json.dumps(call['args'], ensure_ascii=False)[:300]}")
        lines.append("\n</details>")
    return "\n".join(lines)
