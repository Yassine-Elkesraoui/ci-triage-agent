"""Read-only access to a git repository through the git CLI."""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_HEX = re.compile(r"[0-9a-f]{4,40}")
_STATUS = {"A": "added", "M": "modified", "D": "removed", "T": "changed"}


class GitError(RuntimeError):
    """A git command failed unexpectedly."""


class Git:
    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def _run(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "-c", "core.quotepath=off", *args],
            capture_output=True,
        )
        if proc.returncode not in ok_codes:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise GitError(f"git {' '.join(args)} exited {proc.returncode}: {stderr}")
        return proc

    def text(self, *args: str) -> str:
        return self._run(*args).stdout.decode("utf-8", errors="replace")

    def is_shallow(self) -> bool:
        return self.text("rev-parse", "--is-shallow-repository").strip() == "true"

    @lru_cache(maxsize=None)
    def resolve_commit(self, ref: str) -> str | None:
        """Full sha of the commit named by a hex sha or unique prefix, else None."""
        ref = ref.lower()
        if not _HEX.fullmatch(ref):
            return None
        proc = self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok_codes=(0, 1, 128))
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    @lru_cache(maxsize=None)
    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True if ``ancestor`` is ``descendant`` or one of its ancestors."""
        return self._run("merge-base", "--is-ancestor", ancestor, descendant, ok_codes=(0, 1)).returncode == 0

    @lru_cache(maxsize=None)
    def rev_list(self, spec: str) -> tuple[str, ...]:
        """Full shas of the commits ``git rev-list <spec>`` names, newest first."""
        return tuple(self.text("rev-list", spec).split())

    @lru_cache(maxsize=None)
    def hidden_count(self, base: str, sha: str, hidden: tuple[str, ...]) -> int:
        """How many files under ``hidden`` changed from ``base`` to ``sha``."""
        if not hidden:
            return 0
        return len(self.text("diff-tree", "-r", "--no-renames", "--name-only", base, sha, "--", *hidden).splitlines())

    @lru_cache(maxsize=None)
    def commit_header(self, sha: str) -> tuple[str, tuple[str, ...], str]:
        """(tree sha, parent shas, author date in ISO 8601)."""
        tree, parents, author_date = self.text("show", "-s", "--format=%T%n%P%n%aI", sha).splitlines()[:3]
        return tree, tuple(parents.split()), author_date

    @lru_cache(maxsize=None)
    def diff_files(self, base: str, sha: str, excluded: tuple[str, ...] = ()) -> tuple[dict[str, Any], ...]:
        """Per-file changes from ``base`` to ``sha``, GitHub-style, without paths under ``excluded``.

        Renames are reported as a removal plus an addition.
        """
        args = ["diff-tree", "-r", "--no-renames", base, sha]
        spec = ["--", ".", *(f":(exclude){path}" for path in excluded)]
        statuses = [line.split("\t", 1) for line in self.text(*args, "--name-status", *spec).splitlines()]
        numstats = [line.split("\t", 2) for line in self.text(*args, "--numstat", *spec).splitlines()]
        chunks = [c for c in re.split(r"(?m)^(?=diff --git )", self.text(*args, "-p", *spec)) if c]
        if not len(statuses) == len(numstats) == len(chunks):
            raise GitError(f"diff {base}..{sha}: name-status, numstat and patch disagree")
        files = []
        for (status, name), (added, deleted, _), chunk in zip(statuses, numstats, chunks):
            binary = added == "-"
            hunks = chunk.find("\n@@")
            files.append({
                "filename": name,
                "status": _STATUS.get(status[0], status),
                "additions": 0 if binary else int(added),
                "deletions": 0 if binary else int(deleted),
                "patch": None if binary else (chunk[hunks + 1:] if hunks >= 0 else ""),
            })
        return tuple(files)

    def ls_tree(self, sha: str, path: str = "") -> list[tuple[str, str, str, str]]:
        """(mode, type, object id, path) entries; ``path`` ending in "/" lists a directory."""
        args = ["--literal-pathspecs", "ls-tree", "-z", sha]
        if path:
            args += ["--", path]
        out = self._run(*args).stdout.decode("utf-8", errors="replace")
        entries = []
        for record in filter(None, out.split("\0")):
            meta, name = record.split("\t", 1)
            mode, kind, oid = meta.split()
            entries.append((mode, kind, oid, name))
        return entries

    def blob(self, oid: str) -> bytes:
        return self._run("cat-file", "blob", oid).stdout
