"""Toolkit: runs tools by name over one GitHubSource, never raises, and records every call."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from ci_triage.github_source import GitHubSource
from ci_triage.tools.github_tools import TOOLS
from ci_triage.tools.models import NotAvailableResult, ToolCall
from ci_triage.tools.text import MAX_OUTPUT_CHARS, render

_not_available = NotAvailableResult.of


def _validation_summary(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in error.errors())


class Toolkit:
    """The tools of ``TOOLS`` bound to ``source``, with a per-call output cap of ``max_chars``."""

    def __init__(self, source: GitHubSource, max_chars: int = MAX_OUTPUT_CHARS) -> None:
        self.source = source
        self.max_chars = max_chars
        self.calls: list[ToolCall] = []

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> BaseModel:
        """Run tool ``name``: its output model, or a ``NotAvailableResult``; never raises."""
        recorded = dict(arguments) if isinstance(arguments, Mapping) else {"_invalid": repr(arguments)[:200]}
        error = None
        try:
            result = self._run(name, arguments)
        except Exception as exc:  # noqa: BLE001 - a tool must never take the agent down
            error = f"{type(exc).__name__}: {exc}"
            result = _not_available(f"{name} call", "internal error while running the tool")
        size = len(render(result))
        if size > self.max_chars:
            result = _not_available(f"{name} call", f"output exceeded the {self.max_chars}-character cap")
            error = error or f"output of {size} chars exceeded the cap"
            size = len(render(result))
        self.calls.append(ToolCall(tool=str(name), arguments=recorded, output_chars=size,
                                   available=result.available, error=error))
        return result

    def call_json(self, name: str, arguments: Mapping[str, Any] | None = None) -> str:
        """``call``, rendered as the JSON string a model reads."""
        return render(self.call(name, arguments))

    def _run(self, name: str, arguments: Mapping[str, Any] | None) -> BaseModel:
        tool = TOOLS.get(name) if isinstance(name, str) else None
        if tool is None:
            return _not_available(f"tool {name!r}", f"unknown tool; tools are: {', '.join(TOOLS)}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return _not_available(f"{name} call", "invalid arguments: expected a JSON object")
        try:
            args = tool.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            return _not_available(f"{name} call", f"invalid arguments: {_validation_summary(exc)}")
        return tool.run(self.source, args, self.max_chars)

    def call_counts(self) -> Counter[str]:
        return Counter(call.tool for call in self.calls)
