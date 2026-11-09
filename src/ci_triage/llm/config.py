"""The pre-registered settings of an LLM baseline (``configs/<name>.toml``) and its prompt file."""

from __future__ import annotations

import hashlib
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ci_triage.labpaths import REPO_ROOT

DEFAULT_CONFIG = REPO_ROOT / "configs" / "one_call_v2.toml"
REPAIR_MARKER = "<!-- REPAIR MESSAGE"
FINAL_MARKER = "<!-- FINAL TURN MESSAGE"  # agent prompts only: the forced last turn


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Generation(_Strict):
    temperature: float
    max_output_tokens: int
    repair_calls: int


class Budget(_Strict):
    context_tokens: int
    reserve_tokens: int
    chars_per_token: float
    jobs_chars: int
    failure_block_chars: int
    workflow_chars: int
    diff_chars: int

    @property
    def prompt_tokens(self) -> int:
        """Tokens the whole prompt (system and user turns) may take: the context minus the answer and slack."""
        return self.context_tokens - self.reserve_tokens

    def prompt_chars(self, max_output_tokens: int) -> int:
        return int((self.prompt_tokens - max_output_tokens) * self.chars_per_token)

    def estimate_tokens(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)


class GeminiSettings(_Strict):
    model: str
    thinking_level: str | None = None
    seed: int | None = None
    max_requests_per_minute: float
    daily_call_cap: int
    timeout_s: float


class OllamaSettings(_Strict):
    model: str
    think: bool
    num_ctx: int
    seed: int
    host: str | None = None
    timeout_s: float


class RetrySettings(_Strict):
    max_tries: int
    statuses: tuple[int, ...]
    backoff_initial_s: float
    backoff_factor: float
    backoff_max_s: float

    def delay(self, failed_attempts: int) -> float:
        """Seconds to wait after the ``failed_attempts``-th failed attempt (1-based)."""
        return min(self.backoff_max_s, self.backoff_initial_s * self.backoff_factor ** (failed_attempts - 1))


class LLMConfig(_Strict):
    name: str
    prompt: str
    generation: Generation
    budget: Budget
    gemini: GeminiSettings
    ollama: OllamaSettings
    retry: RetrySettings


@dataclass(frozen=True)
class Prompt:
    system: str
    repair: str  # contains "{errors}"
    sha256: str
    final_turn: str | None = None  # agent prompts: the user turn that asks for the answer now

    def repair_message(self, errors: str) -> str:
        return self.repair.replace("{errors}", errors)


@dataclass(frozen=True)
class LoadedConfig:
    config: LLMConfig
    text: str  # the file verbatim, for reports
    path: Path
    sha256: str
    prompt: Prompt


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prompt(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    system, marker, rest = text.partition(REPAIR_MARKER)
    if not marker:
        raise ValueError(f"{path}: no '{REPAIR_MARKER}' section")
    repair, final_marker, final_rest = rest.split("-->", 1)[1].partition(FINAL_MARKER)
    repair = repair.strip()
    if "{errors}" not in repair:
        raise ValueError(f"{path}: the repair message has no {{errors}} placeholder")
    final = final_rest.split("-->", 1)[1].strip() if final_marker else None
    return Prompt(system=system.strip(), repair=repair, sha256=_sha(text), final_turn=final)


def load_config(path: Path | str = DEFAULT_CONFIG) -> LoadedConfig:
    path = Path(path)
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    config = LLMConfig.model_validate(tomllib.loads(text))
    prompt_path = Path(config.prompt)
    if not prompt_path.is_absolute():
        prompt_path = REPO_ROOT / prompt_path
    return LoadedConfig(config=config, text=text, path=path, sha256=_sha(text), prompt=load_prompt(prompt_path))
