"""The agent's pre-registered settings (``configs/agent_v1.toml``), its prompt, and the one-call config whose
bundle it starts from."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ci_triage.labpaths import REPO_ROOT
from ci_triage.llm.config import (GeminiSettings, Generation, LoadedConfig, OllamaSettings, Prompt, RetrySettings,
                                  load_config, load_prompt)

DEFAULT_AGENT_CONFIG = REPO_ROOT / "configs" / "agent_v1.toml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentSettings(_Strict):
    max_turns: int = Field(ge=1)  # model turns per case, the answer turn included; repair calls are extra
    loop: Literal["v1", "v2"] = "v1"  # agent_v1.toml has no such key: v1
    # v2 only: after this many repeated calls in a case, the next model turn is the answer turn (repeat_forced)
    repeated_calls_to_answer: int | None = Field(None, ge=1)


class ContextSettings(_Strict):
    context_tokens: int
    reserve_tokens: int
    # v1: one value for every backend. v2: one per backend tokenizer ({"gemini": .., "ollama": ..}); any other
    # backend (the tests' fake) gets the lowest, the most conservative.
    chars_per_token: float | dict[str, float]
    # v2: once a model call has returned the server's prompt count, the check uses it for the conversation so far
    # and the estimate for the new content only
    use_server_count: bool = False

    def room(self, max_output_tokens: int) -> int:
        """Prompt tokens a turn may take: the context minus the answer and slack."""
        return self.context_tokens - self.reserve_tokens - max_output_tokens

    def ratio(self, backend: str | None = None) -> float:
        if isinstance(self.chars_per_token, float | int):
            return float(self.chars_per_token)
        return self.chars_per_token.get(backend or "", min(self.chars_per_token.values()))

    def tokens_for(self, chars: int, backend: str | None = None) -> int:
        return math.ceil(chars / self.ratio(backend))

    def estimate_tokens(self, text: str, backend: str | None = None) -> int:
        return self.tokens_for(len(text), backend)


class PlannedRun(_Strict):
    backend: Literal["gemini", "ollama"]
    mode: Literal["log-only", "full"]


class AgentConfig(_Strict):
    name: str
    prompt: str
    bundle: str  # the one-call config whose bundle is the first user turn
    agent: AgentSettings
    generation: Generation
    context: ContextSettings
    gemini: GeminiSettings
    ollama: OllamaSettings
    retry: RetrySettings
    run: tuple[PlannedRun, ...]  # the registered run plan, in order

    @model_validator(mode="after")
    def _consistent(self) -> AgentConfig:
        if self.ollama.num_ctx != self.context.context_tokens:
            raise ValueError("ollama.num_ctx must equal context.context_tokens")
        if len(set(self.run)) != len(self.run):
            raise ValueError("the run plan lists a run twice")
        v2 = self.agent.loop == "v2"
        if v2 != (self.agent.repeated_calls_to_answer is not None):
            raise ValueError("agent.repeated_calls_to_answer is set for the v2 loop, and only for it")
        if not v2 and (self.context.use_server_count or not isinstance(self.context.chars_per_token, float | int)):
            raise ValueError("the v1 loop has one chars_per_token and no server count")
        if v2 and not isinstance(self.context.chars_per_token, dict):
            raise ValueError("the v2 loop has one chars_per_token per backend")
        return self


@dataclass(frozen=True)
class LoadedAgentConfig:
    config: AgentConfig
    text: str  # the file verbatim, for reports
    path: Path
    sha256: str
    prompt: Prompt
    bundle: LoadedConfig  # the one-call config the bundle is built with


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else REPO_ROOT / path


def load_agent_config(path: Path | str = DEFAULT_AGENT_CONFIG) -> LoadedAgentConfig:
    import hashlib

    path = Path(path)
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    config = AgentConfig.model_validate(tomllib.loads(text))
    prompt = load_prompt(_resolve(config.prompt))
    if prompt.final_turn is None:
        raise ValueError(f"{config.prompt}: an agent prompt needs a FINAL TURN MESSAGE section")
    if (config.agent.loop == "v2") != (prompt.tool_call_error is not None):
        raise ValueError(f"{config.prompt}: a v2 agent prompt, and only one, has a TOOL CALL ERROR MESSAGE section")
    return LoadedAgentConfig(config=config, text=text, path=path, sha256=hashlib.sha256(text.encode()).hexdigest(),
                             prompt=prompt, bundle=load_config(_resolve(config.bundle)))
