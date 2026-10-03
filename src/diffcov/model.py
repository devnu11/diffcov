"""Format-neutral coverage data, keyed by repo-relative path."""
from __future__ import annotations

from dataclasses import dataclass, field

# (line_start, col_start, line_end, col_end)
RegionKey = tuple[int, int, int, int]


@dataclass
class FileCov:
    # Execution counts for instrumented lines only.
    lines: dict[int, int] = field(default_factory=dict)
    # line -> {branch outcome id: count}
    branches: dict[int, dict[object, int]] = field(default_factory=dict)
    # LLVM code regions -> count
    regions: dict[RegionKey, int] = field(default_factory=dict)

    def add_line(self, line: int, count: int) -> None:
        self.lines[line] = self.lines.get(line, 0) + max(count, 0)

    def add_branch(self, line: int, key: object, count: int) -> None:
        outcomes = self.branches.setdefault(line, {})
        outcomes[key] = outcomes.get(key, 0) + max(count, 0)

    def add_region(self, key: RegionKey, count: int) -> None:
        self.regions[key] = self.regions.get(key, 0) + max(count, 0)

    def merge(self, other: FileCov) -> None:
        """Sum counts, so a header compiled into many units shows up once."""
        for n, c in other.lines.items():
            self.add_line(n, c)
        for n, outcomes in other.branches.items():
            for k, c in outcomes.items():
                self.add_branch(n, k, c)
        for k, c in other.regions.items():
            self.add_region(k, c)


@dataclass
class CoverageData:
    files: dict[str, FileCov] = field(default_factory=dict)

    def add(self, path: str, cov: FileCov) -> None:
        self.files.setdefault(path, FileCov()).merge(cov)

    @property
    def has_branches(self) -> bool:
        return any(f.branches for f in self.files.values())

    @property
    def has_regions(self) -> bool:
        return any(f.regions for f in self.files.values())
