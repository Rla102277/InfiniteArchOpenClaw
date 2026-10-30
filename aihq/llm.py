"""The OS model layer. One interface, four backends, no third-party packages.

- AnthropicModel: the Messages API over HTTPS with urllib. Full tool use, images, and the
  server-side web_search tool when asked for.
- ClaudeCLIModel: `claude -p` (Claude Code signed in with the subscription). No tool loop on our
  side; Claude Code reads the repo itself and our tools are described in the prompt.
- HermesCLIModel: `hermes chat --oneshot`, whatever model Hermes is configured with.
- StubModel: scripted responses for tests. Never touches the network.

Every complete() returns an Anthropic-shaped response dict:
{"content": [blocks], "stop_reason": str, "usage": {"input_tokens", "output_tokens"}}

Model ids: `system/os.json` may carry {"models": {"default": ..., "deep": ...}}; the brain only
checks schema_version and default_theme there, so the key is optional and safe. Without it the
constants below apply.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
def KEYCHAIN_SERVICE() -> str:
    from . import core
    return str(core.local_config().get("secrets_service") or "agentic-os")
KEYCHAIN_ACCOUNT = "anthropic"
RETRY_STATUSES = {429, 500, 502, 503, 529}
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}
DEFAULT_MODELS = {"default": "claude-sonnet-5-5", "deep": "claude-opus-5-5"}
OS_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ORDER = "anthropic (full tool loop, images, web) > claude-cli > hermes"


def api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if platform.system() == "Darwin" and shutil.which("security"):
        try:
            result = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE(), "-a", KEYCHAIN_ACCOUNT, "-w"],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
    return None


def model_ids(os_root: Path | None = None) -> dict:
    """{"default": id, "deep": id}, from system/os.json when it names them."""
    ids = dict(DEFAULT_MODELS)
    path = Path(os_root or OS_ROOT) / "system" / "os.json"
    try:
        configured = json.loads(path.read_text(encoding="utf-8")).get("models") or {}
    except (OSError, ValueError, AttributeError):
        configured = {}
    for kind in ("default", "deep"):
        value = configured.get(kind) if isinstance(configured, dict) else None
        if isinstance(value, str) and value.strip():
            ids[kind] = value.strip()
    return ids


# ---------- tool names on the wire ----------
# The OS names connector tools `gmail.search`; Anthropic and OpenAI only accept
# ^[a-zA-Z0-9_-]{1,64}$. Dots become `__` going out and come back on the way in.

def _wire(name: str) -> str:
    return str(name).replace(".", "__")[:64]


def wire_tools(tools: list[dict] | None, messages: list[dict]) -> tuple[list[dict] | None, list[dict], dict]:
    """(tools, messages, wire->os name map) with every tool name made API-safe."""
    names: dict[str, str] = {}
    out_tools = None
    if tools:
        out_tools = []
        for tool in tools:
            if isinstance(tool, dict) and "name" in tool and "type" not in tool:
                wired = _wire(tool["name"])
                names[wired] = tool["name"]
                out_tools.append({**tool, "name": wired})
            else:
                out_tools.append(tool)
    out_messages = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_use" and "." in str(b.get("name", "")) for b in content):
            content = [{**b, "name": _wire(b["name"])} if isinstance(b, dict) and b.get("type") == "tool_use" else b for b in content]
            out_messages.append({**message, "content": content})
        else:
            out_messages.append(message)
    return out_tools, out_messages, names


def unwire(response: dict, names: dict) -> dict:
    if not names or not isinstance(response, dict):
        return response
    content = response.get("content")
    if isinstance(content, list):
        response = {**response, "content": [{**b, "name": names.get(b.get("name"), b.get("name"))}
                                             if isinstance(b, dict) and b.get("type") == "tool_use" else b for b in content]}
    return response


class Model:
    name = "model"
    backend = "none"

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 4096, web: bool = False) -> dict:
        raise NotImplementedError

    @property
    def supports_tools(self) -> bool:
        return True

    @property
    def supports_images(self) -> bool:
        return False

    @property
    def supports_web(self) -> bool:
        return False


class AnthropicModel(Model):
    backend = "anthropic"

    def __init__(self, name: str, key: str | None = None, timeout: int = 180):
        self.name = name
        self.key = key or api_key()
        self.timeout = timeout
        if not self.key:
            raise ValueError("No Anthropic API key. Set ANTHROPIC_API_KEY or store one in the Keychain: "
                             f"security add-generic-password -s {KEYCHAIN_SERVICE()} -a {KEYCHAIN_ACCOUNT} -w <key>")

    @property
    def supports_images(self) -> bool:
        return True

    @property
    def supports_web(self) -> bool:
        return True

    def complete(self, system, messages, tools=None, max_tokens=4096, web=False):
        tools, messages, names = wire_tools(tools, messages)
        return unwire(self._complete(system, messages, tools, max_tokens, web), names)

    def _complete(self, system, messages, tools=None, max_tokens=4096, web=False):
        body = {"model": self.name, "max_tokens": max_tokens, "system": system, "messages": messages}
        all_tools = list(tools or [])
        if web:
            all_tools.append(dict(WEB_SEARCH_TOOL))
        if all_tools:
            body["tools"] = all_tools
        data = json.dumps(body).encode("utf-8")
        last_error = None
        for attempt in range(3):
            request = urllib.request.Request(API_URL, data=data, method="POST", headers={
                "content-type": "application/json", "x-api-key": self.key, "anthropic-version": API_VERSION})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:2000]
                last_error = ValueError(f"Anthropic API {error.code}: {detail}")
                if error.code not in RETRY_STATUSES:
                    raise last_error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = ValueError(f"Anthropic API unreachable: {error}")
            time.sleep(2 ** attempt * 1.5)
        raise last_error or ValueError("Anthropic API failed.")


def flatten(messages: list[dict]) -> str:
    """Messages as plain text, for backends that take one prompt."""
    parts = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = message.get("content")
        if isinstance(content, str):
            parts.append(f"{role}: {content}")
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                parts.append(f"{role}: {block.get('text', '')}")
            elif kind == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, list):
                    inner = " ".join(str(b.get("text", "")) for b in inner if isinstance(b, dict))
                parts.append(f"TOOL RESULT: {inner}")
            elif kind == "image":
                parts.append(f"{role}: [photo attached; this backend cannot see images]")
    return "\n\n".join(parts)


class ClaudeCLIModel(Model):
    """Claude Code in print mode. Read-only on the repo; it cannot call our tools, so the prompt
    tells it what they would have been and asks it to say what it would need."""
    backend = "claude-cli"

    def __init__(self, domain, executable: str | None = None, timeout: int = 600, name: str = "claude-cli"):
        self.domain = str(domain)
        self.executable = executable or shutil.which("claude")
        self.timeout = timeout
        self.name = name
        if not self.executable:
            raise ValueError("Claude Code (`claude`) is not on PATH and no API key is set.")

    @property
    def supports_tools(self) -> bool:
        return False

    def complete(self, system, messages, tools=None, max_tokens=4096, web=False):
        prompt = system
        if tools:
            names = ", ".join(f"{tool['name']} ({tool.get('description', '')[:80]})" for tool in tools)
            prompt += ("\n\nIn this mode you cannot call tools. Read the repo yourself with Read, Glob and Grep "
                       f"(repo root: {self.domain}). If an action is needed that only these tools can do, say so "
                       f"plainly at the end under a heading 'Needs:' with the tool and arguments. Tools: {names}")
        if web:
            prompt += "\n\nThis backend has no web search. Say so if the answer needs it."
        prompt += "\n\n" + flatten(messages)
        args = [self.executable, "-p", "--output-format", "json", "--permission-mode", "plan",
                "--allowedTools", "Read,Glob,Grep", "--add-dir", self.domain, "--", prompt]
        result = subprocess.run(args, cwd=self.domain, capture_output=True, text=True, timeout=self.timeout,
                                stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            try:
                payload = json.loads(result.stdout)
                detail = str(payload.get("result") or detail)
            except (json.JSONDecodeError, AttributeError):
                pass
            if "Authentication" in detail or "authenticate" in detail:
                detail += ". Sign in to Claude Code on this Mac (run `claude`, then /login), or set an API key."
            raise ValueError(f"Claude Code could not answer: {detail[:600]}")
        text, usage = result.stdout.strip(), {"input_tokens": 0, "output_tokens": 0}
        try:
            payload = json.loads(result.stdout)
            if isinstance(payload, dict):
                text = str(payload.get("result") or payload.get("content") or text)
                raw = payload.get("usage") or {}
                usage = {"input_tokens": int(raw.get("input_tokens", 0) or 0), "output_tokens": int(raw.get("output_tokens", 0) or 0)}
        except json.JSONDecodeError:
            pass
        return {"content": [{"type": "text", "text": text or "Claude Code returned nothing."}],
                "stop_reason": "end_turn", "usage": usage, "model": self.name}


class HermesCLIModel(Model):
    """Hermes in one-shot chat mode. Uses whatever model Hermes is configured with."""
    backend = "hermes"

    def __init__(self, domain, executable: str | None = None, timeout: int = 600, name: str = "hermes"):
        self.domain = str(domain)
        self.executable = executable or shutil.which("hermes")
        self.timeout = timeout
        self.name = name
        if not self.executable:
            raise ValueError("Hermes (`hermes`) is not on PATH.")

    @property
    def supports_tools(self) -> bool:
        return False

    def complete(self, system, messages, tools=None, max_tokens=4096, web=False):
        prompt = system + "\n\n" + flatten(messages)
        if tools:
            prompt += ("\n\nYou cannot call the OS tools in this mode. If an action is needed, "
                       "say so under a heading 'Needs:' with the tool and arguments.")
        args = [self.executable, "chat", "--oneshot", "--max-turns", "8", "-q", prompt]
        result = subprocess.run(args, cwd=self.domain, capture_output=True, text=True, timeout=self.timeout,
                                stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise ValueError(f"Hermes could not answer: {(result.stderr or result.stdout).strip()[:600]}")
        text = result.stdout.strip() or "Hermes returned nothing."
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}, "model": self.name}


class StubModel(Model):
    """Scripted responses. Each entry is a full response dict, a string (one text block), or a
    list of content blocks. Every call is recorded in .calls."""
    backend = "stub"

    def __init__(self, responses: list | None = None, name: str = "stub", images: bool = True, web: bool = True):
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.name = name
        self._images = images
        self._web = web

    @property
    def supports_images(self) -> bool:
        return self._images

    @property
    def supports_web(self) -> bool:
        return self._web

    def complete(self, system, messages, tools=None, max_tokens=4096, web=False):
        self.calls.append({"system": system, "messages": messages, "tools": tools, "web": web, "max_tokens": max_tokens})
        if not self.responses:
            response: dict = {"content": [{"type": "text", "text": "Stub has nothing more to say."}], "stop_reason": "end_turn"}
        else:
            item = self.responses.pop(0)
            if isinstance(item, str):
                response = {"content": [{"type": "text", "text": item}], "stop_reason": "end_turn"}
            elif isinstance(item, list):
                stop = "tool_use" if any(block.get("type") == "tool_use" for block in item) else "end_turn"
                response = {"content": item, "stop_reason": stop}
            else:
                response = dict(item)
        response.setdefault("usage", {"input_tokens": 10, "output_tokens": 5})
        response.setdefault("stop_reason", "end_turn")
        return response


def backend_status(backend: str = "auto", os_root: Path | None = None) -> dict:
    """What pick() would choose, for the dashboard and `./hq doctor`."""
    key = api_key()
    cli = shutil.which("claude")
    hermes = shutil.which("hermes")
    wanted = str(backend or "auto")
    if wanted == "anthropic" or (wanted == "auto" and key):
        chosen = "anthropic" if key else "missing"
    elif wanted in {"claude-cli", "auto"} and cli:
        chosen = "claude-cli"
    elif wanted in {"hermes", "auto"} and hermes:
        chosen = "hermes"
    else:
        chosen = "missing"
    ids = model_ids(os_root)
    return {"configured": wanted, "chosen": chosen, "api_key": bool(key), "claude_cli": cli or None,
            "hermes": hermes or None, "default": ids["default"], "deep": ids["deep"], "order": BACKEND_ORDER}


def pick(kind: str = "default", backend: str = "auto", domain: Path | None = None, os_root: Path | None = None) -> Model:
    """A model for `kind` ("default" or "deep"). `domain` is the repo root the CLI backends read."""
    status = backend_status(backend, os_root)
    root = Path(os_root or OS_ROOT)
    domain = Path(domain) if domain else root.parent
    if status["chosen"] == "anthropic":
        return AnthropicModel(status["deep" if kind == "deep" else "default"])
    if status["chosen"] == "claude-cli":
        return ClaudeCLIModel(domain, status["claude_cli"])
    if status["chosen"] == "hermes":
        return HermesCLIModel(domain, status["hermes"])
    raise ValueError("No model backend. Set ANTHROPIC_API_KEY (or the curator-os Keychain item), sign in to Claude Code, or install Hermes.")


# ---------- any model, like T3 Chat ----------

PROVIDERS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "secret": "openai", "env": "OPENAI_API_KEY"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "secret": "gemini", "env": "GEMINI_API_KEY"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "secret": "openrouter", "env": "OPENROUTER_API_KEY"},
    "ollama": {"label": "Ollama (this Mac)", "base_url": "http://127.0.0.1:11434/v1", "secret": None, "env": None},
}
BUILTIN_MODELS = [
    ("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("anthropic", "claude-opus-5-5", "Claude Opus 5.5"),
    ("openai", "gpt-5", "GPT-5"),
    ("openai", "gpt-5-mini", "GPT-5 mini"),
    ("gemini", "gemini-2.5-pro", "Gemini 2.5 Pro"),
    ("gemini", "gemini-2.5-flash", "Gemini 2.5 Flash"),
]


def provider_key(provider: str) -> str | None:
    """The key for a provider: the OS secret store when present, else the provider's env var."""
    if provider == "anthropic":
        return api_key()
    spec = PROVIDERS.get(provider) or {}
    if not spec.get("secret"):
        return None
    try:
        from . import secrets as os_secrets
        value = os_secrets.get(spec["secret"])
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(spec.get("env") or "", "").strip() or None


def _json_args(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (TypeError, ValueError):
        return {"raw": str(value)}


def to_openai(system: str, messages: list[dict], images: bool = True) -> list[dict]:
    """Anthropic-shaped messages to OpenAI chat messages."""
    out = [{"role": "system", "content": system}] if system else []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        texts, parts, calls, results, dropped = [], [], [], [], 0
        for block in content or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text", ""))
                parts.append({"type": "text", "text": block.get("text", "")})
            elif kind == "image":
                src = block.get("source") or {}
                if images and src.get("type") == "base64":
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"}})
                else:
                    texts.append("[photo attached; this model cannot see images]")
                    parts.append({"type": "text", "text": "[photo attached; this model cannot see images]"})
            elif kind == "tool_use":
                calls.append({"id": block.get("id", ""), "type": "function",
                              "function": {"name": block.get("name", ""), "arguments": json.dumps(block.get("input") or {})}})
            elif kind == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, list):
                    inner = "\n".join(str(b.get("text", "")) for b in inner if isinstance(b, dict))
                results.append({"role": "tool", "tool_call_id": block.get("tool_use_id", ""), "content": str(inner)})
            else:  # server tool blocks (web search) mean nothing to another provider
                dropped += 1
        if dropped:
            texts.append(f"[{dropped} web search step(s) from an earlier model omitted]")
        if role == "assistant":
            entry = {"role": "assistant", "content": "\n".join(t for t in texts if t) or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        else:
            out.extend(results)
            if parts and (texts or len(parts) > len(texts)):
                has_image = any(p["type"] == "image_url" for p in parts)
                out.append({"role": "user", "content": parts if has_image else "\n".join(texts)})
    return out


def tools_to_openai(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools if isinstance(t, dict) and "name" in t and "type" not in t]


def from_openai(data: dict) -> dict:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"] if isinstance(message["content"], str) else json.dumps(message["content"])})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        content.append({"type": "tool_use", "id": call.get("id") or f"call_{len(content)}", "name": fn.get("name", ""),
                        "input": _json_args(fn.get("arguments"))})
    finish = choice.get("finish_reason")
    stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else ("max_tokens" if finish == "length" else "end_turn")
    usage = data.get("usage") or {}
    return {"content": content or [{"type": "text", "text": ""}], "stop_reason": stop, "model": data.get("model"),
            "usage": {"input_tokens": int(usage.get("prompt_tokens", 0) or 0), "output_tokens": int(usage.get("completion_tokens", 0) or 0)}}


class OpenAICompatModel(Model):
    """Any /chat/completions endpoint with tools: OpenAI, Gemini, OpenRouter, Ollama."""

    def __init__(self, name: str, provider: str = "openai", base_url: str | None = None, key_name: str | None = None,
                 key: str | None = None, timeout: int = 180):
        spec = PROVIDERS.get(provider, {})
        self.provider = provider
        self.backend = provider
        self.name = name
        self.base_url = (base_url or spec.get("base_url") or "").rstrip("/")
        self.timeout = timeout
        self.key = key if key is not None else (provider_key(provider) if spec.get("secret") or key_name else None)
        if spec.get("secret") and not self.key:
            raise ValueError(f"No {spec.get('label', provider)} key. Save one in Connectors > Models, "
                             f"or `./hq secret set {spec['secret']}`.")

    @property
    def supports_images(self) -> bool:
        if self.provider == "ollama":
            return any(tag in self.name.lower() for tag in ("vl", "llava"))
        return True

    @property
    def supports_web(self) -> bool:
        return False

    def complete(self, system, messages, tools=None, max_tokens=4096, web=False):
        tools, messages, names = wire_tools(tools, messages)
        body = {"model": self.name, "messages": to_openai(system, messages, self.supports_images), "max_tokens": max_tokens}
        spec_tools = tools_to_openai(tools)
        if spec_tools:
            body["tools"] = spec_tools
        data = json.dumps(body).encode("utf-8")
        headers = {"content-type": "application/json"}
        if self.key:
            headers["authorization"] = f"Bearer {self.key}"
        if self.provider == "openrouter":
            headers["x-title"] = "InfiniteArch Agentic OS"
        last_error = None
        for attempt in range(3):
            request = urllib.request.Request(f"{self.base_url}/chat/completions", data=data, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return unwire(from_openai(json.loads(response.read().decode("utf-8"))), names)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:2000]
                last_error = ValueError(f"{self.provider} API {error.code}: {detail}")
                if error.code not in RETRY_STATUSES:
                    raise last_error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = ValueError(f"{self.provider} unreachable: {error}")
            time.sleep(2 ** attempt * 1.5)
        raise last_error or ValueError(f"{self.provider} failed.")


def resolve(ref: str | None, kind: str = "default", domain: Path | None = None, os_root: Path | None = None) -> Model:
    """`provider:model` to a Model. Empty, "default" or "deep" keep pick()'s behavior; a bare
    name is an Anthropic model id."""
    ref = str(ref or "").strip()
    if not ref or ref in ("default", "deep"):
        return pick(ref or kind, domain=domain, os_root=os_root)
    provider, _, model = ref.partition(":")
    if not model:
        return AnthropicModel(ref)
    if provider == "anthropic":
        return AnthropicModel(model)
    if provider in PROVIDERS:
        return OpenAICompatModel(model, provider)
    raise ValueError(f"Unknown model provider {provider!r}. Use one of: anthropic, " + ", ".join(PROVIDERS))


def _ollama_models(timeout: float = 1.0) -> list[str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def saved_models(domain: Path | None = None) -> list[str]:
    path = Path(domain or OS_ROOT.parent) / "hq" / "os" / "models.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        refs = data.get("refs", data) if isinstance(data, dict) else data
        return [str(r) for r in refs if isinstance(r, str) and ":" in r]
    except (OSError, ValueError):
        return []


def save_model(ref: str, domain: Path | None = None) -> list[str]:
    ref = str(ref).strip()
    if ":" not in ref or ref.split(":", 1)[0] not in ({"anthropic"} | set(PROVIDERS)):
        raise ValueError("A model ref looks like provider:model, for example openrouter:meta-llama/llama-4-maverick.")
    refs = saved_models(domain)
    if ref not in refs:
        refs.append(ref)
    path = Path(domain or OS_ROOT.parent) / "hq" / "os" / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"refs": refs}, indent=2) + "\n", encoding="utf-8")
    return refs


def catalog(domain: Path | None = None, probe_ollama: bool = True) -> list[dict]:
    """Every model the pickers offer, each with whether its key is set."""
    configured = {"anthropic": bool(api_key())}
    for provider, spec in PROVIDERS.items():
        configured[provider] = True if not spec.get("secret") else bool(provider_key(provider))
    out, seen = [], set()

    def add(provider, model, label):
        ref = f"{provider}:{model}"
        if ref in seen:
            return
        seen.add(ref)
        out.append({"ref": ref, "provider": provider, "label": label, "configured": configured.get(provider, False)})

    ids = model_ids()
    add("anthropic", ids["default"], f"Claude ({ids['default']})")
    add("anthropic", ids["deep"], f"Claude ({ids['deep']})")
    for provider, model, label in BUILTIN_MODELS:
        add(provider, model, label)
    for model in (_ollama_models() if probe_ollama else []):
        add("ollama", model, f"{model} (Ollama)")
    for ref in saved_models(domain):
        provider, _, model = ref.partition(":")
        add(provider, model, model)
    out.append({"ref": "openrouter:", "provider": "openrouter", "label": "OpenRouter: type a model id",
                "configured": configured["openrouter"], "placeholder": True})
    return out
