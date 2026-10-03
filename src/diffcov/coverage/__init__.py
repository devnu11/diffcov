"""Coverage loaders. Each returns a list of ``Record``s with paths as written
in the coverage data; ``paths.PathNormalizer`` turns them into repo paths."""
from __future__ import annotations

from dataclasses import dataclass

from ..model import FileCov


@dataclass
class Record:
    path: str
    # Directory a relative ``path`` is relative to, if the format says.
    base: str | None
    cov: FileCov


class LoadError(RuntimeError):
    pass
