"""Model calls with pacing, retries, a daily attempt cap, and a record of each call.

``ModelCaller.call`` makes one logical model call: up to ``RetrySettings.max_tries`` attempts, retrying
only the statuses listed there (429, 503, 504) with exponential backoff. Before each attempt it takes
a slot from the ``CallLedger`` (raising ``DailyCapReached`` when the day's cap is used up) and waits for
the ``Pacer``. It returns the response and an ``LLMCallRecord``, or raises ``APIFailure`` carrying the
record of the failed attempts. Any known secret is scrubbed from every string it records.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ci_triage.llm.clients import API_KEY_ENV, APICallError, LLMClient, LLMResponse, Message
from ci_triage.llm.config import RetrySettings

MAX_RECORDED_OUTPUT = 20_000  # characters of raw model output kept per call
REDACTED = "<redacted>"


class LLMCallRecord(BaseModel):
    """One logical model call (its retries included), as written to cases.jsonl."""

    purpose: Literal["diagnose", "repair"]
    backend: str
    requested_model: str
    model_version: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    thoughts_tokens: int | None = None
    latency_s: float | None = None  # of the attempt that answered
    total_s: float = 0.0  # all attempts, backoff and pacing included
    attempts: int = 0
    retries: int = 0
    attempt_errors: list[str] = Field(default_factory=list)
    finish_reason: str | None = None
    output: str | None = None  # the raw answer, clipped to MAX_RECORDED_OUTPUT
    context_overflow: bool = False  # tokens_in + max_output_tokens exceeded the context window


class APIFailure(Exception):
    """A model call that failed after its retries, or with a status that is not retried."""

    def __init__(self, record: LLMCallRecord) -> None:
        super().__init__(record.attempt_errors[-1] if record.attempt_errors else "API failure")
        self.record = record


class DailyCapReached(Exception):
    pass


# -- pacing -----------------------------------------------------------------------------------------


class Pacer:
    """At most ``per_minute`` attempt starts per minute: consecutive starts are 60/per_minute s apart."""

    def __init__(self, per_minute: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval = 60.0 / per_minute
        self.clock, self.sleep = clock, sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self.interval - self.clock()
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()


# -- the daily cap ------------------------------------------------------------------------------------


def pacific_date(now: datetime | None = None) -> str:
    """Today's date in US Pacific time, when Gemini's daily quotas reset."""
    now = now or datetime.now(UTC)
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001 - no tz database: standard time, at worst an hour early
        zone = timezone(timedelta(hours=-8))
    return now.astimezone(zone).date().isoformat()


class CallLedger:
    """Attempts made per model and Pacific-time day, persisted in a JSON file so the cap holds across runs."""

    def __init__(self, path: Path, model: str, cap: int, today: Callable[[], str] = pacific_date) -> None:
        self.path, self.model, self.cap, self.today = Path(path), model, cap, today

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def used(self) -> int:
        return self._load().get(self.model, {}).get(self.today(), 0)

    def take(self) -> None:
        """Count one attempt, or raise ``DailyCapReached`` if the cap is used up."""
        data = self._load()
        day = self.today()
        per_day = {day: data.get(self.model, {}).get(day, 0)}  # older days are dropped
        if per_day[day] >= self.cap:
            raise DailyCapReached(f"daily cap of {self.cap} calls to {self.model} reached for {day} (Pacific time)")
        per_day[day] += 1
        data[self.model] = per_day
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


# -- secrets ----------------------------------------------------------------------------------------


def known_secrets() -> list[str]:
    key = os.environ.get(API_KEY_ENV)
    return [key] if key and len(key) >= 8 else []


def scrub(text: str | None, secrets: Sequence[str]) -> str | None:
    if text is None:
        return None
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


# -- the caller -------------------------------------------------------------------------------------


class ModelCaller:
    def __init__(self, client: LLMClient, *, temperature: float, max_output_tokens: int, retry: RetrySettings,
                 context_tokens: int | None = None, pacer: Pacer | None = None, ledger: CallLedger | None = None,
                 sleep: Callable[[float], None] = time.sleep, secrets: Sequence[str] | None = None) -> None:
        self.client = client
        self.temperature, self.max_output_tokens = temperature, max_output_tokens
        self.retry, self.context_tokens = retry, context_tokens
        self.pacer, self.ledger, self.sleep = pacer, ledger, sleep
        self.secrets = list(secrets) if secrets is not None else known_secrets()

    def call(self, purpose: Literal["diagnose", "repair"], system: str, messages: Sequence[Message],
             schema: dict[str, Any]) -> tuple[LLMResponse, LLMCallRecord]:
        record = LLMCallRecord(purpose=purpose, backend=self.client.backend,
                               requested_model=self.client.requested_model)
        start = time.perf_counter()
        while True:
            if self.ledger is not None:
                self.ledger.take()
            if self.pacer is not None:
                self.pacer.wait()
            record.attempts += 1
            try:
                response = self.client.generate(system, messages, schema, temperature=self.temperature,
                                                max_output_tokens=self.max_output_tokens)
            except APICallError as exc:
                record.attempt_errors.append(scrub(str(exc), self.secrets)[:500])
                retryable = exc.status in self.retry.statuses and record.attempts < self.retry.max_tries
                if not retryable:
                    record.retries = record.attempts - 1
                    record.total_s = time.perf_counter() - start
                    raise APIFailure(record) from None
                self.sleep(self.retry.delay(record.attempts))
                continue
            break
        record.retries = record.attempts - 1
        record.total_s = time.perf_counter() - start
        record.model_version = scrub(response.model_version, self.secrets)
        record.tokens_in, record.tokens_out = response.tokens_in, response.tokens_out
        record.thoughts_tokens = response.thoughts_tokens
        record.latency_s = response.latency_s
        record.finish_reason = response.finish_reason
        record.output = scrub(response.text[:MAX_RECORDED_OUTPUT], self.secrets)
        if self.context_tokens and response.tokens_in is not None:
            record.context_overflow = response.tokens_in + self.max_output_tokens > self.context_tokens
        return response, record
