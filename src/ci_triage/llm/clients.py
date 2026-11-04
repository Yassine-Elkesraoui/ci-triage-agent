"""LLM backends behind one small protocol: Gemini (hosted), Ollama (local) and a scripted fake for tests.

``google-genai`` and ``ollama`` are imported lazily, inside the clients that need them, so the rest
of the package (and CI) runs without them; install them with ``pip install -e ".[llm]"``.

A client makes exactly one request per ``generate`` call: no retries, no pacing (``calling.ModelCaller``
owns those). It raises ``APICallError`` for any failed request; ``status`` is the HTTP status when
there is one, 504 for a client-side timeout, None when the server could not be reached.

The Gemini key is read only from the ``GEMINI_API_KEY`` environment variable, handed to the SDK and
never stored on the client, logged or printed.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

API_KEY_ENV = "GEMINI_API_KEY"
CLIENT_TIMEOUT_STATUS = 504  # a request that timed out on our side is treated like a gateway timeout


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_version: str | None  # what the backend says it served
    tokens_in: int | None
    tokens_out: int | None
    latency_s: float
    finish_reason: str | None = None
    thoughts_tokens: int | None = None


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


# -- Fake -----------------------------------------------------------------------------------------


@dataclass
class FakeCall:
    system: str
    messages: list[Message]
    schema: dict[str, Any]
    temperature: float
    max_output_tokens: int


@dataclass
class FakeClient:
    """Plays back ``script``: each item is the text to answer, or an exception to raise. For tests."""

    script: list[str | Exception]
    requested_model: str = "fake-model"
    model_version: str = "fake-model-001"
    backend: str = "fake"
    calls: list[FakeCall] = field(default_factory=list)

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
