"""Turn paths as written in coverage data into repo-relative paths."""
from __future__ import annotations

import os


def parse_path_map(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise ValueError(f"--path-map wants FROM=TO, got {spec!r}")
    frm, to = spec.split("=", 1)
    return frm, to


class PathNormalizer:
    def __init__(
        self,
        repo_root: str,
        source_root: str | None = None,
        path_maps: list[tuple[str, str]] = (),
    ):
        self.repo = os.path.normpath(os.path.abspath(repo_root))
        self.repo_real = os.path.realpath(self.repo)
        self.source_root = os.path.abspath(source_root) if source_root else self.repo
        # A relative TO means relative to the repo.
        self.maps = [(frm, to if os.path.isabs(to) else os.path.join(self.repo, to)) for frm, to in path_maps]

    def _mapped(self, path: str) -> str:
        for frm, to in self.maps:
            frm = frm.rstrip("/")
            if path == frm or path.startswith(frm + "/"):
                return to.rstrip("/") + path[len(frm) :]
        return path

    def __call__(self, path: str, base: str | None = None) -> str | None:
        """Repo-relative path with '/' separators, or None if outside the repo."""
        p = self._mapped(path)
        if not os.path.isabs(p):
            p = os.path.join(base or self.source_root, p)
        p = os.path.normpath(p)
        for root, cand in ((self.repo, p), (self.repo_real, os.path.realpath(p))):
            rel = os.path.relpath(cand, root)
            if rel != ".." and not rel.startswith(".." + os.sep):
                return rel.replace(os.sep, "/")
        return None
