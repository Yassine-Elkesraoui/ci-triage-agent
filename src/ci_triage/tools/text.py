"""Text helpers shared by the tools: log cleaning, line numbering, clipping, and fitting into the size cap."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TypeVar

from pydantic import BaseModel

MAX_OUTPUT_CHARS = 4000  # per call, measured on the rendered JSON the model will read
MAX_LINE_CHARS = 1000  # longer log or file lines are clipped (pip prints 5,000-char version lists)

_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

M = TypeVar("M", bound=BaseModel)


def render(model: BaseModel) -> str:
    """The JSON a model reads for a tool result; the size cap applies to this string."""
    return model.model_dump_json(exclude_none=True)


def split_lines(text: str) -> list[str]:
    """Lines of ``text`` without terminators; unlike str.splitlines, only \\n (and \\r\\n) break lines."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def log_lines(text: str, timestamps: bool = False) -> list[str]:
    """A job log's lines without ANSI colour codes and, unless ``timestamps``, without GitHub's timestamp prefix."""
    lines = (_ANSI.sub("", line) for line in split_lines(text))
    return list(lines) if timestamps else [_TIMESTAMP.sub("", line, count=1) for line in lines]


def clip(line: str, limit: int) -> str:
    return line if len(line) <= limit else f"{line[:limit]}… [+{len(line) - limit} chars]"


def numbered(lines: Iterable[tuple[int, str]]) -> str:
    """``"<n>| <text>"`` per line, ``n`` being the 1-based line number."""
    return "\n".join(f"{n}| {text}" for n, text in lines)


def fit(make: Callable[[int], M], n_max: int, cap: int) -> M:
    """``make(n)`` for the largest ``n`` in ``[0, n_max]`` whose rendering fits ``cap``.

    Assumes the size grows with ``n``; returns ``make(0)`` if nothing fits (the
    toolkit's final guard then catches it).
    """
    best = make(n_max)
    if len(render(best)) <= cap:
        return best
    best = make(0)
    lo, hi = 1, n_max - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = make(mid)
        if len(render(candidate)) <= cap:
            best, lo = candidate, mid + 1
        else:
            hi = mid - 1
    return best


def page_note(first: int, shown: int, total: int, limited_by_cap: bool, unit: str = "lines") -> str | None:
    """The continuation note for a page of ``shown`` items starting at 0-based ``first``."""
    nxt = first + shown
    if nxt >= total:
        return None
    span = f"{unit} {first + 1}-{nxt} of {total} shown" if shown else f"no {unit} shown of {total}"
    why = "truncated at the size cap" if limited_by_cap else "more remain"
    return f"{why}, use offset={nxt} to continue ({span})"
