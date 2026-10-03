"""LLVM source-based coverage via ``llvm-cov export``.

Lines and branches come from the lcov export; code regions from the JSON
export's per-function region lists.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from ..model import FileCov
from . import LoadError, Record
from .lcov import parse_lcov

# Region kinds in llvm-cov's JSON export.
CODE_REGION = 0


def find_tool(name: str) -> list[str]:
    env = os.environ.get(name.upper().replace("-", "_"))
    if env:
        return [env]
    found = shutil.which(name)
    if found:
        return [found]
    if sys.platform == "darwin" and shutil.which("xcrun"):
        return ["xcrun", name]
    raise LoadError(f"{name} not found; put it on PATH or set {name.upper().replace('-', '_')}")


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="surrogateescape")
    if proc.returncode != 0:
        raise LoadError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout


def merge_profraw(profraws: list[str], out: str) -> str:
    _run([*find_tool("llvm-profdata"), "merge", "-sparse", *profraws, "-o", out])
    return out


def parse_llvm_regions(doc: dict) -> dict[str, FileCov]:
    """Code regions from ``llvm-cov export -format=text``, deduped by
    position (templates and inline functions appear once per instance)."""
    out: dict[str, FileCov] = {}
    for data in doc.get("data", []):
        for fn in data.get("functions", []):
            names = fn.get("filenames", [])
            for r in fn.get("regions", []):
                l1, c1, l2, c2, count, file_id, _expanded, kind = r[:8]
                if kind != CODE_REGION or file_id >= len(names):
                    continue
                out.setdefault(names[file_id], FileCov()).add_region((l1, c1, l2, c2), count)
    return out


def load_llvm(profdata: str, objects: list[str], *, regions: bool = True) -> list[Record]:
    if not objects:
        raise LoadError("--llvm-profdata needs at least one --object (test binary)")
    cmd = [
        *find_tool("llvm-cov"),
        "export",
        f"-instr-profile={profdata}",
        objects[0],
        *(f"-object={o}" for o in objects[1:]),
    ]
    records = parse_lcov(_run([*cmd, "-format=lcov"]))
    if regions:
        try:
            doc = json.loads(_run([*cmd, "-format=text", "-skip-expansions"]))
        except json.JSONDecodeError as e:
            raise LoadError(f"llvm-cov JSON export unreadable: {e}") from e
        for path, cov in parse_llvm_regions(doc).items():
            records.append(Record(path, None, cov))
    return records
