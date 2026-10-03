"""Classify each changed line against the coverage data."""
from __future__ import annotations

import bisect
import posixpath
from dataclasses import dataclass, field

from .model import CoverageData, FileCov, RegionKey

SOURCE_EXTS = {
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm", ".swift", ".rs",
}
HEADER_EXTS = {
    ".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tcc", ".tpp",
}

# Per-line statuses.
COVERED = "covered"
PARTIAL = "partial"  # hit, but a branch outcome or region on it never ran
UNCOVERED = "uncovered"
NONEXEC = "nonexec"  # file is instrumented, this line isn't
UNKNOWN = "unknown"  # file isn't in the coverage data at all

# Per-file statuses.
MEASURED = "measured"
UNKNOWN_SOURCE = "unknown-source"  # never compiled into an instrumented binary
UNKNOWN_HEADER = "unknown-header"  # may just be declarations; warn only


def is_code(path: str) -> bool:
    ext = posixpath.splitext(path)[1].lower()
    return ext in SOURCE_EXTS or ext in HEADER_EXTS


@dataclass
class Tally:
    hit: int = 0
    total: int = 0

    @property
    def pct(self) -> float | None:
        return 100.0 * self.hit / self.total if self.total else None

    def __iadd__(self, other: Tally) -> Tally:
        self.hit += other.hit
        self.total += other.total
        return self


@dataclass
class FileResult:
    path: str
    status: str
    line_status: dict[int, str] = field(default_factory=dict)
    lines: Tally = field(default_factory=Tally)
    branches: Tally = field(default_factory=Tally)
    regions: Tally = field(default_factory=Tally)
    uncovered_regions: list[RegionKey] = field(default_factory=list)

    def with_status(self, *statuses: str) -> list[int]:
        return sorted(n for n, s in self.line_status.items() if s in statuses)


@dataclass
class Result:
    files: list[FileResult]
    has_branches: bool
    has_regions: bool
    lines: Tally = field(default_factory=Tally)
    branches: Tally = field(default_factory=Tally)
    regions: Tally = field(default_factory=Tally)

    def by_status(self, status: str) -> list[FileResult]:
        return [f for f in self.files if f.status == status]


def _measure(path: str, changed: list[int], cov: FileCov) -> FileResult:
    res = FileResult(path, MEASURED)
    zero_edges: set[int] = set()  # lines where a never-run region starts or ends
    for (l1, c1, l2, c2), count in cov.regions.items():
        lo = bisect.bisect_left(changed, l1)
        if lo < len(changed) and changed[lo] <= l2:
            res.regions.total += 1
            if count:
                res.regions.hit += 1
            else:
                res.uncovered_regions.append((l1, c1, l2, c2))
        if not count:
            zero_edges.update((l1, l2))
    res.uncovered_regions.sort()
    for n in changed:
        outcomes = cov.branches.get(n, {})
        res.branches.total += len(outcomes)
        res.branches.hit += sum(1 for c in outcomes.values() if c)
        if n not in cov.lines:
            res.line_status[n] = NONEXEC
            continue
        res.lines.total += 1
        if not cov.lines[n]:
            res.line_status[n] = UNCOVERED
            continue
        res.lines.hit += 1
        partial = any(not c for c in outcomes.values()) or n in zero_edges
        res.line_status[n] = PARTIAL if partial else COVERED
    return res


def classify(changed: dict[str, set[int]], cov: CoverageData) -> Result:
    result = Result([], cov.has_branches, cov.has_regions)
    for path in sorted(changed):
        lines = sorted(changed[path])
        fc = cov.files.get(path)
        if fc is not None:
            fr = _measure(path, lines, fc)
        elif is_code(path):
            ext = posixpath.splitext(path)[1].lower()
            fr = FileResult(path, UNKNOWN_HEADER if ext in HEADER_EXTS else UNKNOWN_SOURCE)
            fr.line_status = {n: UNKNOWN for n in lines}
        else:
            continue  # not code we track (docs, build files, ...)
        result.files.append(fr)
        result.lines += fr.lines
        result.branches += fr.branches
        result.regions += fr.regions
    return result


@dataclass
class Verdict:
    passed: bool
    failures: list[str]
    warnings: list[str]


def evaluate(result: Result, metric: str, fail_under: float | None, unknown: str) -> Verdict:
    failures: list[str] = []
    warnings: list[str] = []
    tally: Tally = getattr(result, {"line": "lines", "branch": "branches", "region": "regions"}[metric])
    if fail_under is not None and tally.pct is not None and tally.pct < fail_under:
        failures.append(f"{metric} coverage {tally.pct:.1f}% is below {fail_under:g}%")
    sources = result.by_status(UNKNOWN_SOURCE)
    if sources and unknown != "ignore":
        msg = (
            f"{len(sources)} changed source file(s) not in the coverage data "
            f"(not compiled into any instrumented binary?): {', '.join(f.path for f in sources)}"
        )
        (failures if unknown == "fail" else warnings).append(msg)
    headers = result.by_status(UNKNOWN_HEADER)
    if headers and unknown != "ignore":
        warnings.append(
            f"{len(headers)} changed header(s) not in the coverage data "
            f"(fine if declarations only): {', '.join(f.path for f in headers)}"
        )
    return Verdict(not failures, failures, warnings)
