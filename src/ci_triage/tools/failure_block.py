"""Deterministic extraction of the evidence in a failed job's log (no LLM).

``find_evidence`` scans a cleaned log (see ``text.log_lines``) for anchor lines,
gives each a span of context, merges overlapping spans into blocks and orders
the blocks by importance. ``fit_blocks`` then renders as many blocks as the size
cap allows: the key lines of every block first, then as much context as fits.

Priorities (1 is most important):

1. pytest short test summary; a Python traceback's final exception (with its
   last frames and message lines); ``##[error]`` timeouts, cancellations and
   other runner errors (setup-python's "version not found", ...); ``ERROR:``
   lines from pip, pytest's config checks and the like; BuildKit's
   ``ERROR: failed to solve`` with the failed step and Dockerfile excerpt
   BuildKit prints above it.
2. pytest's ``E`` assertion lines; an exception that caused another one;
   BuildKit's ``#N ERROR:`` step lines; orphan processes the runner killed;
   pytest's final counts line.
3. pytest's per-test ``FAILED``/``ERROR`` progress lines; ``##[error]Process
   completed with exit code N``.
4. pip's "Ignored the following versions" noise.
5. Fallback when nothing else matched: the last lines before job cleanup.

Ties keep log order. Identical anchors (BuildKit prints a step's output twice)
are kept once.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ci_triage.tools.models import EvidenceBlock
from ci_triage.tools.text import clip, numbered

LINE_CHARS = 300  # evidence lines are clipped to this, then to TIGHT_LINE_CHARS as a last resort
TIGHT_LINE_CHARS = 160
_SHRINK_LEVELS = (None, 8, 5, 3, 2, 1, 0)  # max distance from a key line; None keeps the whole span

# BuildKit prefixes ("#11 5.996 ", "#11 ") and its replay of a failed step's output ("6.002 ").
_BUILDKIT_PREFIX = re.compile(r"^(?:#\d+ (?:\d+\.\d+ )?|\d+\.\d+ )")
_SUMMARY = re.compile(r"^=+ short test summary info =+$")
_PYTEST_COUNTS = re.compile(
    r"^=+ (?:.*\b\d+ (?:failed|passed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b.*|no tests ran.*) =+$"
)
_SUMMARY_ITEM = re.compile(r"^(?:FAILED|ERROR) \S")
_PROGRESS_FAIL = re.compile(r"^\S+::\S.*\s(?:FAILED|ERROR)(?:\s+\[\s*\d+%\])?$")
_E_LINE = re.compile(r"^E(?:\s{2,}|$)")
_PYTEST_LOCATION = re.compile(r"^\S+\.py:\d+: \w+")
_TRACEBACK = "Traceback (most recent call last):"
_EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*(?::.*)?$")
_CHAINED = ("The above exception was the direct cause", "During handling of the above exception")
_ACTIONS_ERROR = re.compile(r"^##\[error\]")
_TIMEOUT = re.compile(r"(?i)\btimed out\b|\btimeout\b")
_CANCELLED = re.compile(r"(?i)\bcancell?ed\b")
_EXIT_CODE = re.compile(r"^##\[error\]Process completed with exit code \d+")
_ERROR_LINE = re.compile(r"^ERROR:\s")
_BUILDKIT_SOLVE = re.compile(r"^ERROR: failed to (?:solve|build)\b")
_BUILDKIT_STEP_ERROR = re.compile(r"^#\d+ ERROR:")
_BUILDKIT_STEP_HEADER = re.compile(r"^ > \[")
_PIP_NOISE = re.compile(r"^ERROR: Ignored the following")
_ORPHAN = re.compile(r"^Terminate orphan process")
_STEP_START = re.compile(r"^##\[group\]Run (.+)")
_CLEANUP = "Post job cleanup."
# Context lines that are evidence in their own right, kept while the context around them is trimmed.
_ERRORISH = re.compile(
    r"(?i)\b(?:error|exception|failed|failure|not found|unable to|denied|timed out|cancell?ed|conflict)\b"
    r"|^E: |^\s*\d+ \| >>>|^ > \["
)


@dataclass
class Evidence:
    """A span of log lines (0-based, inclusive) around one or more anchor lines."""

    kind: str
    priority: int
    start: int
    end: int
    keys: set[int] = field(default_factory=set)  # lines kept however hard the block is trimmed
    anchor: int = -1

    def __post_init__(self) -> None:
        if self.anchor < 0:
            self.anchor = min(self.keys)


@dataclass
class Block:
    kinds: list[str]
    priority: int
    start: int
    end: int
    keys: dict[int, int]  # key line -> priority of the evidence it belongs to


def normalize(line: str) -> str:
    return _BUILDKIT_PREFIX.sub("", line, count=1)


def _pytest(norm: list[str]) -> list[Evidence]:
    found = []
    for i, line in enumerate(norm):
        if _SUMMARY.match(line):
            end = i
            for j in range(i + 1, min(len(norm), i + 80)):
                if _SUMMARY_ITEM.match(norm[j]) or _PYTEST_COUNTS.match(norm[j]):
                    end = j
                if _PYTEST_COUNTS.match(norm[j]):
                    break
            keys = {j for j in range(i + 1, end + 1) if _SUMMARY_ITEM.match(norm[j]) or _PYTEST_COUNTS.match(norm[j])}
            found.append(Evidence("pytest_summary", 1, i, end, keys or {i}))
        elif _PYTEST_COUNTS.match(line):
            found.append(Evidence("pytest_counts", 2, i, i, {i}))
        elif _PROGRESS_FAIL.match(line):
            found.append(Evidence("pytest_failed_test", 3, i, i, {i}))
        elif _E_LINE.match(line) and (i == 0 or not _E_LINE.match(norm[i - 1])):
            last = i
            while last + 1 < len(norm) and _E_LINE.match(norm[last + 1]):
                last += 1
            keys = set(range(i, last + 1))
            end = last
            for j in range(last + 1, min(len(norm), last + 4)):
                if _PYTEST_LOCATION.match(norm[j]):
                    keys.add(j)
                    end = j
                    break
            found.append(Evidence("pytest_assertion", 2, max(0, i - 3), end, keys))
    return found


def _tracebacks(norm: list[str]) -> list[Evidence]:
    found = []
    for i, line in enumerate(norm):
        if line.strip() != _TRACEBACK:
            continue
        j = i + 1
        while j < len(norm) and j < i + 300 and (norm[j][:1] in (" ", "\t") or norm[j].lstrip().startswith("^")):
            j += 1
        if j >= len(norm) or not _EXCEPTION.match(norm[j]):
            continue
        keys = {j}
        end = j
        while end + 1 < len(norm) and end < j + 6 and norm[end + 1].strip() and not norm[end + 1].startswith(
            ("##[", _TRACEBACK, *_CHAINED)
        ):
            end += 1
            if end <= j + 3:
                keys.add(end)
        after = next((norm[k] for k in range(end + 1, min(len(norm), end + 4)) if norm[k].strip()), "")
        priority = 2 if after.startswith(_CHAINED) else 1
        found.append(Evidence("python_exception", priority, max(i, j - 4), end, keys, anchor=j))
    return found


def _actions_errors(norm: list[str]) -> list[Evidence]:
    found = []
    for i, line in enumerate(norm):
        if not _ACTIONS_ERROR.match(line):
            continue
        if _EXIT_CODE.match(line):
            found.append(Evidence("exit_code", 3, i, i, {i}))
        elif _TIMEOUT.search(line):
            found.append(Evidence("timeout", 1, max(0, i - 8), i, {i}))
        elif _CANCELLED.search(line):
            found.append(Evidence("cancelled", 1, max(0, i - 8), i, {i}))
        else:
            found.append(Evidence("actions_error", 1, max(0, i - 3), i, {i}))
    orphans = [i for i, line in enumerate(norm) if _ORPHAN.match(line)]
    if orphans:
        found.append(Evidence("orphan_process", 2, orphans[0], orphans[-1], set(orphans)))
    return found


def _error_lines(raw: list[str], norm: list[str]) -> list[Evidence]:
    found = []
    for i, line in enumerate(norm):
        if _BUILDKIT_STEP_ERROR.match(raw[i]):
            found.append(Evidence("buildkit_step_error", 2, max(0, i - 2), i, {i}))
        elif _BUILDKIT_SOLVE.match(line):
            start = max(0, i - 3)
            for j in range(i - 1, max(-1, i - 40), -1):
                if _BUILDKIT_STEP_HEADER.match(norm[j]):
                    start = max(0, j - 1)
                    break
            found.append(Evidence("buildkit_failed_to_solve", 1, start, i, {i}))
        elif _PIP_NOISE.match(line):
            found.append(Evidence("error_line", 4, i, i, {i}))
        elif _ERROR_LINE.match(line):
            end = i
            while end + 1 < len(norm) and end < i + 8 and not norm[end + 1].startswith("##["):
                end += 1
            found.append(Evidence("error_line", 1, max(0, i - 1), end, {i}))
    return found


def _fallback(norm: list[str]) -> list[Evidence]:
    end = next((i for i, line in enumerate(norm) if line == _CLEANUP), len(norm)) - 1
    while end > 0 and not norm[end].strip():
        end -= 1
    if end < 0:
        return []
    start = max(0, end - 19)
    return [Evidence("log_tail", 5, start, end, set(range(start, end + 1)))]


def _dedupe(found: list[Evidence], norm: list[str]) -> list[Evidence]:
    seen: set[tuple[str, str]] = set()
    kept = []
    for evidence in sorted(found, key=lambda e: (e.priority, e.anchor)):
        key = (evidence.kind, norm[evidence.anchor].strip())
        if key not in seen:
            seen.add(key)
            kept.append(evidence)
    return kept


def _merge(found: list[Evidence]) -> list[Block]:
    blocks: list[tuple[Block, list[Evidence]]] = []
    for evidence in sorted(found, key=lambda e: (e.start, e.end)):
        if blocks and evidence.start <= blocks[-1][0].end + 1:
            block, members = blocks[-1]
            block.end = max(block.end, evidence.end)
            members.append(evidence)
        else:
            blocks.append((Block([], 0, evidence.start, evidence.end, {}), [evidence]))
    merged = []
    for block, members in blocks:
        block.priority = min(e.priority for e in members)
        ranked = sorted(members, key=lambda e: (e.priority, e.anchor))
        block.kinds = list(dict.fromkeys(e.kind for e in ranked))
        # pip's noise lines stay as context when they sit next to real evidence
        core = [e for e in ranked if e.priority <= 3] or ranked
        for evidence in core:
            for key in evidence.keys:
                block.keys.setdefault(key, evidence.priority)
        merged.append(block)
    return merged


def find_evidence(lines: list[str]) -> list[Block]:
    """Evidence blocks of a cleaned log, most important first."""
    norm = [normalize(line) for line in lines]
    found = _pytest(norm) + _tracebacks(norm) + _actions_errors(norm) + _error_lines(lines, norm)
    found = _dedupe(found, norm) or _fallback(norm)
    blocks = _merge(found)
    for block in blocks:
        for i in range(block.start, block.end + 1):
            if _ERRORISH.search(norm[i]) and not _PIP_NOISE.match(norm[i]):
                block.keys.setdefault(i, block.priority)
    return sorted(blocks, key=lambda b: (b.priority, b.start))


def step_names(lines: list[str]) -> list[str | None]:
    """For each line, the "Run ..." step it belongs to (None before the first step and during cleanup)."""
    current: str | None = None
    steps = []
    for line in lines:
        if match := _STEP_START.match(line):
            current = clip(match[1].strip(), 120)
        elif line == _CLEANUP:
            current = None
        steps.append(current)
    return steps


def render_block(block: Block, lines: list[str], step: str | None, level: int | None, line_chars: int,
                 max_keys: int | None = None) -> EvidenceBlock:
    """The block with the lines within ``level`` of a key line (all of them if ``level`` is None).

    ``max_keys`` keeps only that many key lines, most important first (with ``level`` 0), for blocks
    too big to show whole.
    """
    keys = sorted(block.keys)
    if max_keys is not None:
        shown = sorted(sorted(keys, key=lambda k: (block.keys[k], k))[:max_keys])
    else:
        shown = [
            i for i in range(block.start, block.end + 1)
            if level is None or min(abs(i - k) for k in keys) <= level
        ]
    parts, previous = [], None
    for i in shown:
        if previous is not None and i > previous + 1:
            parts.append("…")
        parts.append(numbered([(i + 1, clip(lines[i], line_chars))]))
        previous = i
    if shown[-1] < block.end:
        parts.append("…")
    return EvidenceBlock(kinds=block.kinds, step=step, start_line=shown[0] + 1, end_line=shown[-1] + 1, text="\n".join(parts))


def fit_blocks(
    blocks: list[Block],
    lines: list[str],
    fits: Callable[[list[EvidenceBlock]], bool],
) -> tuple[list[EvidenceBlock], list[Block], bool]:
    """Render as many blocks as ``fits`` accepts: key lines of every block first, then context.

    Pass 1 places each block, in order, with its key lines only (clipped tighter
    if need be, or only the first ones that fit) or leaves it out. Pass 2 gives
    each placed block, in order, the most context that still fits. Returns the
    rendered blocks, the blocks left out, and whether any placed block lost context.
    """
    steps = step_names(lines)

    def render(block: Block, level: int | None, chars: int, max_keys: int | None = None) -> EvidenceBlock:
        return render_block(block, lines, steps[block.start], level, chars, max_keys)

    placed: list[tuple[Block, EvidenceBlock]] = []
    omitted: list[Block] = []
    for block in blocks:
        others = [r for _, r in placed]
        candidate = next((c for c in (render(block, 0, LINE_CHARS), render(block, 0, TIGHT_LINE_CHARS))
                          if fits(others + [c])), None)
        if candidate is None and fits(others + [render(block, 0, TIGHT_LINE_CHARS, 1)]):
            lo, hi = 1, len(block.keys) - 1  # the most key lines that fit
            while lo < hi:
                mid = (lo + hi + 1) // 2
                lo, hi = (mid, hi) if fits(others + [render(block, 0, TIGHT_LINE_CHARS, mid)]) else (lo, mid - 1)
            candidate = render(block, 0, TIGHT_LINE_CHARS, lo)
        if candidate is None:
            omitted.append(block)
        else:
            placed.append((block, candidate))
    trimmed = False
    for n, (block, current) in enumerate(placed):
        for level in _SHRINK_LEVELS:
            candidate = render(block, level, LINE_CHARS)
            if candidate.text == current.text:
                break
            if fits([r for _, r in placed[:n]] + [candidate] + [r for _, r in placed[n + 1:]]):
                placed[n] = (block, candidate)
                break
        trimmed |= placed[n][1].text != render(block, None, LINE_CHARS).text
    return [r for _, r in placed], omitted, trimmed
