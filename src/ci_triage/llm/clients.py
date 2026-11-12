"""LLM backends behind one small protocol: Gemini (hosted), Ollama (local) and a scripted fake for tests.

``google-genai`` and ``ollama`` are imported lazily, inside the clients that need them, so the rest
of the package (and CI) runs without them; install them with ``pip install -e ".[llm]"``.

A client makes exactly one request per ``generate`` or ``chat`` call: no retries, no pacing
(``calling.ModelCaller`` owns those). ``generate`` is the one-call baseline's request (JSON constrained by a
schema); ``chat`` is the agent's (D6b): native tool calling when tools are offered, and/or JSON constrained
by a schema. Tool turns travel as ``Message``s: an assistant message carries the ``ToolCallRequest``s it
made, a ``tool`` message carries one result. It raises ``APICallError`` for any failed request; ``status`` is the HTTP status when
there is one, 504 for a client-side timeout, None when the server could not be reached.

The Gemini key is read only from the ``GEMINI_API_KEY`` environment variable, handed to the SDK and
never stored on the client, logged or printed.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

API_KEY_ENV = "GEMINI_API_KEY"
CLIENT_TIMEOUT_STATUS = 504  # a request that timed out on our side is treated like a gateway timeout


@dataclass(frozen=True)
class ToolCallRequest:
    """A tool call the model asked for."""

    name: str
    arguments: Any  # a dict when the model sent a JSON object; anything else is passed on and refused
    id: str | None = None  # the backend's call id, sent back with the result (Gemini)
    signature: bytes | None = field(default=None, repr=False)  # Gemini's thought signature, sent back verbatim


@dataclass(frozen=True)
class ToolSpec:
    """A tool as offered to the model: name, what it returns, its parameters' JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str  # for a tool message: the result as JSON text, exactly as the model reads it
    tool_calls: tuple[ToolCallRequest, ...] = ()  # assistant messages: the calls the model made
    tool_name: str | None = None  # tool messages: which tool answered
    tool_call_id: str | None = None  # tool messages: the id of the call answered


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_version: str | None  # what the backend says it served
    tokens_in: int | None
    tokens_out: int | None
    latency_s: float
    finish_reason: str | None = None
    thoughts_tokens: int | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()


class APICallError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"{status}: {message}" if status is not None else message)
        self.status = status
        self.message = message


class MissingAPIKey(RuntimeError):
    pass


@runtime_checkable
class LLMClient(Protocol):
    backend: str  # "gemini", "ollama", "fake"
    requested_model: str

    def generate(self, system: str, messages: Sequence[Message], schema: dict[str, Any], *,
                 temperature: float, max_output_tokens: int) -> LLMResponse:
        """One request; the answer is constrained to ``schema`` where the backend supports it."""

    def chat(self, system: str, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None,
             schema: dict[str, Any] | None, temperature: float, max_output_tokens: int) -> LLMResponse:
        """One request of a tool-using conversation: ``tools`` offered for native tool calling (None: none),
        and/or the answer constrained to ``schema`` (None: free text)."""


# -- Gemini ---------------------------------------------------------------------------------------


class GeminiClient:
    """``generate_content`` with JSON output constrained by ``response_json_schema``."""

    backend = "gemini"

    def __init__(self, model: str, *, thinking_level: str | None = None, seed: int | None = None,
                 timeout_s: float = 120.0) -> None:
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise MissingAPIKey(f"set the {API_KEY_ENV} environment variable")
        from google import genai
        from google.genai import types

        self._types = types
        # attempts=1: the SDK must not retry on its own, every attempt is paced and counted by ModelCaller.
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
        self.requested_model = model
        self.thinking_level = thinking_level
        self.seed = seed

    def __repr__(self) -> str:
        return f"GeminiClient(model={self.requested_model!r})"

    def generate(self, system: str, messages: Sequence[Message], schema: dict[str, Any], *,
                 temperature: float, max_output_tokens: int) -> LLMResponse:
        types = self._types
        from google.genai import errors

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            seed=self.seed,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level) if self.thinking_level else None,
        )
        contents = [types.Content(role="user" if m.role == "user" else "model", parts=[types.Part(text=m.content)])
                    for m in messages]
        start = time.perf_counter()
        try:
            response = self._client.models.generate_content(model=self.requested_model, contents=contents,
                                                            config=config)
        except errors.APIError as exc:
            raise APICallError(exc.code, f"{exc.status or ''} {exc.message or ''}".strip()) from None
        except Exception as exc:  # noqa: BLE001 - transport errors come from several libraries
            raise _transport_error(exc) from None
        latency = time.perf_counter() - start
        usage = response.usage_metadata
        candidates = response.candidates or []
        finish = candidates[0].finish_reason if candidates else None
        try:
            text = response.text or ""
        except Exception:  # noqa: BLE001 - .text raises on some blocked responses
            text = ""
        return LLMResponse(
            text=text,
            model_version=response.model_version,
            tokens_in=usage.prompt_token_count if usage else None,
            tokens_out=usage.candidates_token_count if usage else None,
            thoughts_tokens=usage.thoughts_token_count if usage else None,
            latency_s=latency,
            finish_reason=str(getattr(finish, "value", finish)) if finish is not None else None,
        )


    def chat(self, system: str, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None,
             schema: dict[str, Any] | None, temperature: float, max_output_tokens: int) -> LLMResponse:
        types = self._types
        from google.genai import errors

        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools])]
            extra["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if schema is not None:
            extra.update(response_mime_type="application/json", response_json_schema=schema)
        config = types.GenerateContentConfig(
            system_instruction=system, temperature=temperature, max_output_tokens=max_output_tokens, seed=self.seed,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level) if self.thinking_level else None,
            **extra)
        start = time.perf_counter()
        try:
            response = self._client.models.generate_content(model=self.requested_model,
                                                            contents=gemini_contents(types, messages), config=config)
        except errors.APIError as exc:
            raise APICallError(exc.code, f"{exc.status or ''} {exc.message or ''}".strip()) from None
        except Exception as exc:  # noqa: BLE001
            raise _transport_error(exc) from None
        latency = time.perf_counter() - start
        usage = response.usage_metadata
        candidates = response.candidates or []
        finish = candidates[0].finish_reason if candidates else None
        parts = (candidates[0].content.parts or []) if candidates and candidates[0].content else []
        calls = tuple(ToolCallRequest(name=p.function_call.name or "", arguments=dict(p.function_call.args or {}),
                                      id=p.function_call.id, signature=p.thought_signature)
                      for p in parts if p.function_call)
        text = "".join(p.text for p in parts if p.text and not p.thought)
        return LLMResponse(
            text=text, model_version=response.model_version,
            tokens_in=usage.prompt_token_count if usage else None,
            tokens_out=usage.candidates_token_count if usage else None,
            thoughts_tokens=usage.thoughts_token_count if usage else None,
            latency_s=latency, finish_reason=str(getattr(finish, "value", finish)) if finish is not None else None,
            tool_calls=calls)


_REPEATED = re.compile(r"(Repeated call: same result as turn \d+\.)\n(.*)", re.DOTALL)


def gemini_tool_response(content: str) -> dict[str, Any]:
    """A tool message's content as a function_response object. A repeated call's result (the agent's
    ``Repeated call: same result as turn N.`` line, then the earlier output) stays structured:
    ``{"note": <that line>, "result": <the earlier result, as its own function_response carried it>}``."""
    repeated = _REPEATED.fullmatch(content)
    if repeated:
        return {"note": repeated.group(1), "result": _as_object(repeated.group(2))}
    return _as_object(content)


def gemini_contents(types: Any, messages: Sequence[Message]) -> list[Any]:
    """``messages`` as Gemini contents: tool calls as function_call parts (with their thought signatures),
    consecutive tool results as one user turn of function_response parts."""
    contents: list[Any] = []
    for m in messages:
        if m.role == "tool":
            part = types.Part(function_response=types.FunctionResponse(
                id=m.tool_call_id, name=m.tool_name, response=gemini_tool_response(m.content)))
            previous = contents[-1] if contents else None
            if previous is not None and previous.role == "user" and all(
                    getattr(p, "function_response", None) for p in previous.parts):
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
        elif m.role == "assistant":
            parts = [types.Part(text=m.content)] if m.content else []
            parts += [types.Part(function_call=types.FunctionCall(id=c.id, name=c.name, args=_as_object(c.arguments)),
                                 thought_signature=c.signature) for c in m.tool_calls]
            contents.append(types.Content(role="model", parts=parts or [types.Part(text="")]))
        else:
            contents.append(types.Content(role="user", parts=[types.Part(text=m.content)]))
    return contents


def _as_object(value: Any) -> dict[str, Any]:
    """A JSON object for the backend: dicts as they are, JSON text parsed, anything else wrapped."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
        if isinstance(parsed, dict):
            return parsed
        value = parsed
    return {"value": value}


def _transport_error(exc: Exception) -> APICallError:
    name = type(exc).__name__
    if "Timeout" in name or isinstance(exc, TimeoutError):
        return APICallError(CLIENT_TIMEOUT_STATUS, f"client-side timeout ({name})")
    return APICallError(None, f"{name}: {exc}")


# -- Ollama ---------------------------------------------------------------------------------------


class OllamaClient:
    """``/api/chat`` with ``format`` set to the JSON schema (grammar-constrained decoding)."""

    backend = "ollama"

    def __init__(self, model: str, *, think: bool = False, num_ctx: int = 8192, seed: int = 42,
                 host: str | None = None, timeout_s: float = 600.0) -> None:
        import ollama

        self._ollama = ollama
        self._client = ollama.Client(host=host, timeout=timeout_s)
        self.requested_model = model
        self.think = think
        self.num_ctx = num_ctx
        self.seed = seed
        self._version: str | None = None

    def __repr__(self) -> str:
        return f"OllamaClient(model={self.requested_model!r})"

    def model_version(self) -> str | None:
        """The model's name and digest as the server lists it (the digest pins the exact weights)."""
        if self._version is None:
            try:
                for listed in self._client.list().models:
                    if listed.model == self.requested_model:
                        self._version = f"{listed.model}@{(listed.digest or '')[:12]}"
            except Exception:  # noqa: BLE001 - the version is informative, never fatal
                return None
        return self._version

    def options(self, temperature: float, max_output_tokens: int) -> dict[str, Any]:
        return {"temperature": temperature, "seed": self.seed, "num_ctx": self.num_ctx,
                "num_predict": max_output_tokens}

    def generate(self, system: str, messages: Sequence[Message], schema: dict[str, Any], *,
                 temperature: float, max_output_tokens: int) -> LLMResponse:
        chat = [{"role": "system", "content": system}, *({"role": m.role, "content": m.content} for m in messages)]
        start = time.perf_counter()
        try:
            response = self._client.chat(model=self.requested_model, messages=chat, format=schema, think=self.think,
                                         options=self.options(temperature, max_output_tokens), stream=False)
        except self._ollama.ResponseError as exc:
            raise APICallError(exc.status_code if exc.status_code > 0 else None, str(exc.error)) from None
        except Exception as exc:  # noqa: BLE001
            raise _transport_error(exc) from None
        latency = time.perf_counter() - start
        served = response.model or self.requested_model
        version = self.model_version() if served == self.requested_model else served
        return LLMResponse(text=response.message.content or "", model_version=version or served,
                           tokens_in=response.prompt_eval_count, tokens_out=response.eval_count, latency_s=latency,
                           finish_reason=response.done_reason)

    def chat(self, system: str, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None,
             schema: dict[str, Any] | None, temperature: float, max_output_tokens: int) -> LLMResponse:
        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = ollama_tools(tools)
        if schema is not None:
            extra["format"] = schema
        start = time.perf_counter()
        try:
            response = self._client.chat(model=self.requested_model, messages=ollama_messages(system, messages),
                                         think=self.think, options=self.options(temperature, max_output_tokens),
                                         stream=False, **extra)
        except self._ollama.ResponseError as exc:
            raise APICallError(exc.status_code if exc.status_code > 0 else None, str(exc.error)) from None
        except Exception as exc:  # noqa: BLE001
            raise _transport_error(exc) from None
        latency = time.perf_counter() - start
        served = response.model or self.requested_model
        version = self.model_version() if served == self.requested_model else served
        calls = tuple(ToolCallRequest(name=c.function.name, arguments=_plain(c.function.arguments))
                      for c in (response.message.tool_calls or []))
        # prompt_eval_count counts only the prompt tokens not already in the server's cache, so in a
        # multi-turn conversation it is less than the prompt's length
        return LLMResponse(text=response.message.content or "", model_version=version or served,
                           tokens_in=response.prompt_eval_count, tokens_out=response.eval_count, latency_s=latency,
                           finish_reason=response.done_reason, tool_calls=calls)

    def server_info(self) -> dict[str, Any]:
        """The server's version, this machine's basic hardware, and how much of the model is in GPU memory."""
        info: dict[str, Any] = {"ollama_server_version": None, **hardware_info()}
        try:
            info["ollama_server_version"] = self._client._client.get("/api/version").json().get("version")
        except Exception:  # noqa: BLE001 - informative only
            pass
        try:
            loaded = [m for m in self._client.ps().models if m.model == self.requested_model]
            if loaded:
                info["model_size_bytes"], info["model_vram_bytes"] = loaded[0].size, loaded[0].size_vram
        except Exception:  # noqa: BLE001
            pass
        return info


def _plain(value: Any) -> Any:
    try:
        return dict(value)
    except (TypeError, ValueError):
        return value


def ollama_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}} for t in tools]


def ollama_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    """``messages`` as Ollama chat messages, the system prompt first."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "content": m.content, "tool_name": m.tool_name})
        elif m.role == "assistant" and m.tool_calls:
            out.append({"role": "assistant", "content": m.content,
                        "tool_calls": [{"function": {"name": c.name, "arguments": _as_object(c.arguments)}}
                                       for c in m.tool_calls]})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def hardware_info() -> dict[str, Any]:
    """Basic facts about this machine: OS, CPU, cores, memory, and the GPU if nvidia-smi names one."""
    info: dict[str, Any] = {"os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
                            "cpu": platform.processor() or None, "cpu_count": os.cpu_count(),
                            "memory_bytes": _memory_bytes(), "gpu": None}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        info["gpu"] = out or None
    except Exception:  # noqa: BLE001 - no NVIDIA GPU, or no driver tools
        pass
    return info


def _memory_bytes() -> int | None:
    try:
        if platform.system() == "Windows":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_avail", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_avail", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

            status = _Status(length=ctypes.sizeof(_Status))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return int(status.total)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:  # noqa: BLE001
        return None


# -- Fake -----------------------------------------------------------------------------------------


@dataclass
class FakeCall:
    system: str
    messages: list[Message]
    schema: dict[str, Any]
    temperature: float
    max_output_tokens: int


@dataclass(frozen=True)
class FakeTurn:
    """A scripted ``chat`` answer: text and/or tool calls."""

    text: str = ""
    tool_calls: tuple[ToolCallRequest, ...] = ()

    @classmethod
    def calling(cls, *calls: tuple[str, Any], text: str = "") -> FakeTurn:
        """A turn calling each (tool name, arguments) in order."""
        return cls(text, tuple(ToolCallRequest(name, args, id=f"call-{i}") for i, (name, args) in enumerate(calls)))


@dataclass
class FakeChatCall:
    system: str
    messages: list[Message]
    tools: list[ToolSpec] | None
    schema: dict[str, Any] | None
    temperature: float
    max_output_tokens: int


@dataclass
class FakeClient:
    """Plays back ``script``: each item is the text to answer (``chat``: or a ``FakeTurn``), or an exception
    to raise. For tests."""

    script: list[str | FakeTurn | Exception]
    requested_model: str = "fake-model"
    model_version: str = "fake-model-001"
    backend: str = "fake"
    calls: list[FakeCall] = field(default_factory=list)
    chats: list[FakeChatCall] = field(default_factory=list)

    @classmethod
    def of(cls, items: Iterable[str | Exception], **kwargs: Any) -> FakeClient:
        return cls(script=list(items), **kwargs)

    def generate(self, system: str, messages: Sequence[Message], schema: dict[str, Any], *,
                 temperature: float, max_output_tokens: int) -> LLMResponse:
        self.calls.append(FakeCall(system, list(messages), schema, temperature, max_output_tokens))
        if not self.script:
            raise AssertionError("FakeClient script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model_version=self.model_version, tokens_in=len(system) // 4,
                           tokens_out=len(item) // 4, latency_s=0.0, finish_reason="STOP")

    def chat(self, system: str, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None,
             schema: dict[str, Any] | None, temperature: float, max_output_tokens: int) -> LLMResponse:
        self.chats.append(FakeChatCall(system, list(messages), None if tools is None else list(tools), schema,
                                       temperature, max_output_tokens))
        if not self.script:
            raise AssertionError("FakeClient script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        turn = item if isinstance(item, FakeTurn) else FakeTurn(text=item)
        prompt = len(system) + sum(len(m.content) for m in messages)
        return LLMResponse(text=turn.text, model_version=self.model_version, tokens_in=prompt // 4,
                           tokens_out=len(turn.text) // 4 + 10 * len(turn.tool_calls), latency_s=0.0,
                           finish_reason="STOP", tool_calls=turn.tool_calls)
