"""Which lines at the built revision belong to a set of commits.

Rule: a line counts when any commit in the set added or modified it and the
line still exists at ``rev`` -- even if a later commit outside the set edited
it again. (Patch A changes lines 1-4, patch B then changes line 3: measuring A
looks at lines 1-4.)

Two passes, unioned:

* forward mapping -- each commit's changed lines are carried through the hunks
  of ``git diff <commit> <rev>``. A line inside a hunk that a later commit
  rewrote maps to all of that hunk's new lines (deliberately generous); a line
  inside a pure deletion is gone.
* blame -- lines at ``rev`` whose origin commit is in the set. This also finds
  code a set commit wrote that was later moved or copied into another file,
  which forward mapping (a delete plus an add) loses.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable


class GitError(RuntimeError):
    pass


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotePath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


# Pin everything a user's git config could change about diff output.
DIFF_OPTS = (
    "-U0",
    "-w",
    "-M",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-relative",
    "--ignore-submodules",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass
class FileDiff:
    old_path: str | None = None
    new_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}


def _unquote(s: str) -> str:
    """Undo git's C-style quoting of unusual paths."""
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    raw = s[1:-1].encode("utf-8", "surrogateescape")
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == 0x5C and i + 1 < len(raw):
            nxt = chr(raw[i + 1])
            if nxt in "01234567":
                out.append(int(raw[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, raw[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return out.decode("utf-8", "surrogateescape")


def _side_path(s: str, prefix: str) -> str | None:
    s = _unquote(s)
    if s == "/dev/null":
        return None
    return s[len(prefix) :] if s.startswith(prefix) else s


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    in_header = False
    git_line = ""
    new = deleted = False

    def finish() -> None:
        if cur is None:
            return
        # Files with no ---/+++ lines (pure renames, empty or binary files).
        if cur.old_path is None and cur.new_path is None:
            rest = git_line[len("diff --git ") :]
            n = (len(rest) - 5) // 2
            if n > 0 and rest.startswith("a/") and rest[2 + n : 5 + n] == " b/":
                cur.old_path = cur.new_path = rest[2 : 2 + n]
        if new:
            cur.old_path = None
        if deleted:
            cur.new_path = None

    for line in text.split("\n"):
        if line.startswith("diff --git "):
            finish()
            cur = FileDiff()
            files.append(cur)
            in_header, git_line, new, deleted = True, line, False, False
            continue
        if cur is None:
            continue
        if in_header:
            if line.startswith("--- "):
                cur.old_path = _side_path(line[4:], "a/")
                new = cur.old_path is None
                continue
            if line.startswith("+++ "):
                cur.new_path = _side_path(line[4:], "b/")
                deleted = cur.new_path is None
                continue
            if line.startswith("rename from "):
                cur.old_path = _unquote(line[len("rename from ") :])
                continue
            if line.startswith("rename to "):
                cur.new_path = _unquote(line[len("rename to ") :])
                continue
            if line.startswith("new file mode"):
                new = True
                continue
            if line.startswith("deleted file mode"):
                deleted = True
                continue
        m = _HUNK.match(line)
        if m:
            in_header = False
            cur.hunks.append(
                Hunk(
                    int(m[1]),
                    int(m[2]) if m[2] is not None else 1,
                    int(m[3]),
                    int(m[4]) if m[4] is not None else 1,
                )
            )
    finish()
    return files


def map_line(hunks: list[Hunk], line: int) -> list[int]:
    """Map a line number on the old side of ``hunks`` to the new side.

    Hunks are -U0 hunks sorted by position. Returns [] if the line was deleted,
    and every replacement line if it sits inside a rewritten block.
    """
    offset = 0
    for h in hunks:
        if h.old_count == 0:
            # Pure insertion after old line ``old_start``.
            if line > h.old_start:
                offset += h.new_count
                continue
            break
        if line < h.old_start:
            break
        if line < h.old_start + h.old_count:
            return list(range(h.new_start, h.new_start + h.new_count))
        offset += h.new_count - h.old_count
    return [line + offset]


@dataclass
class ChangedLines:
    rev: str
    commits: list[str]
    lines: dict[str, set[int]]
    # Merge commits stand for the whole branch they merged: their diff
    # against the first parent, plus the branch's commits for blame.
    merges: list[str] = field(default_factory=list)
    # Commits that aren't ancestors of rev; ignored.
    not_ancestors: list[str] = field(default_factory=list)


def rev_parse(repo: str, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options", rev + "^{commit}").strip()


def resolve_commits(repo: str, specs: Iterable[str]) -> list[str]:
    """Expand ``A..B`` ranges and resolve single revisions; keeps order, dedupes."""
    seen: dict[str, None] = {}
    for spec in specs:
        if ".." in spec:
            shas = git(repo, "rev-list", "--reverse", spec, "--").split()
            if not shas:
                raise GitError(f"range {spec!r} contains no commits")
        else:
            shas = [rev_parse(repo, spec)]
        for sha in shas:
            seen[sha] = None
    return list(seen)


def _empty_tree(repo: str) -> str:
    return git(repo, "hash-object", "-t", "tree", "/dev/null").strip()


def _is_ancestor(repo: str, sha: str, rev: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor", sha, rev],
        capture_output=True,
    )
    return proc.returncode == 0


def _literal(paths: Iterable[str]) -> list[str]:
    return [f":(literal){p}" for p in paths]


def commit_lines(repo: str, parent: str, sha: str) -> dict[str, set[int]]:
    """Lines ``sha`` added or modified, in ``sha``'s own line numbers."""
    out: dict[str, set[int]] = {}
    for fd in parse_diff(git(repo, "diff", *DIFF_OPTS, parent, sha)):
        if fd.new_path is None:
            continue
        lines = {n for h in fd.hunks for n in range(h.new_start, h.new_start + h.new_count)}
        if lines:
            out.setdefault(fd.new_path, set()).update(lines)
    return out


def _name_status(repo: str, old: str, new: str) -> tuple[dict[str, str], set[str], set[str], set[str]]:
    """Returns (renames old->new, deleted, modified-in-place, changed paths at ``new``)."""
    toks = git(
        repo, "diff", "--name-status", "-z", "-M", "--no-relative", "--ignore-submodules", old, new
    ).split("\0")
    renames: dict[str, str] = {}
    deleted: set[str] = set()
    modified: set[str] = set()
    changed: set[str] = set()
    i = 0
    while i < len(toks) and toks[i]:
        status = toks[i]
        if status[0] in "RC":
            src, dst = toks[i + 1], toks[i + 2]
            if status[0] == "R":
                renames[src] = dst
            changed.add(dst)
            i += 3
            continue
        path = toks[i + 1]
        if status[0] == "D":
            deleted.add(path)
        else:
            modified.add(path)
            changed.add(path)
        i += 2
    return renames, deleted, modified, changed


def forward(repo: str, sha: str, lines: dict[str, set[int]], rev: str) -> tuple[dict[str, set[int]], set[str]]:
    """Carry ``lines`` (in ``sha``'s line numbers) to ``rev``.

    Also returns the paths at ``rev`` that changed since ``sha``, the
    candidates for code moved out of the commit's files.
    """
    if sha == rev:
        return {p: set(ls) for p, ls in lines.items()}, set()
    renames, deleted, modified, changed = _name_status(repo, sha, rev)
    need = [p for p in lines if p not in deleted and (p in renames or p in modified)]
    hunks: dict[str, list[Hunk]] = {}
    if need:
        spec = _literal(need + [renames[p] for p in need if p in renames])
        for fd in parse_diff(git(repo, "diff", *DIFF_OPTS, sha, rev, "--", *spec)):
            if fd.old_path is not None:
                hunks[fd.old_path] = fd.hunks
    out: dict[str, set[int]] = {}
    for path, ls in lines.items():
        if path in deleted:
            continue
        dst = out.setdefault(renames.get(path, path), set())
        for n in ls:
            dst.update(map_line(hunks.get(path, []), n))
    return out, changed


_BLAME_HDR = re.compile(r"^([0-9a-f]{40,64}) (\d+) (\d+)(?: \d+)?$")


def blame_lines(repo: str, rev: str, path: str, shas: set[str]) -> set[int]:
    """Lines of ``path`` at ``rev`` whose origin commit is in ``shas``."""
    try:
        out = git(repo, "blame", "--porcelain", "-w", "-M", "-C", rev, "--", path)
    except GitError:
        return set()
    found = set()
    for line in out.split("\n"):
        m = _BLAME_HDR.match(line)
        if m and m[1] in shas:
            found.add(int(m[3]))
    return found


def changed_lines(
    repo: str,
    specs: Iterable[str],
    rev: str = "HEAD",
    *,
    blame: bool = True,
    blame_filter: Callable[[str], bool] | None = None,
) -> ChangedLines:
    rev_sha = rev_parse(repo, rev)
    commits = resolve_commits(repo, specs)
    shas = set(commits)
    result = ChangedLines(rev=rev_sha, commits=commits, lines={})
    candidates: set[str] = set()
    empty = None
    for sha in commits:
        if not _is_ancestor(repo, sha, rev_sha):
            result.not_ancestors.append(sha)
            shas.discard(sha)
            continue
        parents = git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        if len(parents) > 1:
            result.merges.append(sha)
            shas.update(git(repo, "rev-list", f"{parents[0]}..{sha}").split())
        if parents:
            parent = parents[0]
        else:
            empty = empty or _empty_tree(repo)
            parent = empty
        mapped, since = forward(repo, sha, commit_lines(repo, parent, sha), rev_sha)
        for path, ls in mapped.items():
            result.lines.setdefault(path, set()).update(ls)
        candidates |= mapped.keys() | since
    if blame and shas:
        for path in sorted(candidates):
            if blame_filter is not None and not blame_filter(path):
                continue
            found = blame_lines(repo, rev_sha, path, shas)
            if found:
                result.lines.setdefault(path, set()).update(found)
    result.lines = {p: ls for p, ls in result.lines.items() if ls}
    return result


def dirty_files(repo: str, rev: str, paths: Iterable[str]) -> list[str]:
    """Paths whose working-tree content differs from ``rev``."""
    differing = set(git(repo, "diff", "--name-only", "-z", "--no-relative", rev).split("\0"))
    return sorted(p for p in paths if p in differing)


def file_at(repo: str, rev: str, path: str) -> list[str]:
    try:
        text = git(repo, "cat-file", "blob", f"{rev}:{path}")
    except GitError:
        return []
    return text.splitlines()


def toplevel(path: str) -> str:
    return git(path, "rev-parse", "--show-toplevel").strip()
