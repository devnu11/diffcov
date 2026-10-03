"""gcov JSON intermediate format (``gcov --json-format``, gcc 9+)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from ..model import FileCov
from . import LoadError, Record


def _documents(text: str) -> list[dict]:
    # One document per file, or JSON lines from ``gcov --stdout``.
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def parse_gcov_json(doc: dict) -> list[Record]:
    base = doc.get("current_working_directory")
    records = []
    for f in doc.get("files", []):
        cov = FileCov()
        for ln in f.get("lines", []):
            n = ln["line_number"]
            cov.add_line(n, ln.get("count", 0))
            for i, br in enumerate(ln.get("branches", [])):
                cov.add_branch(n, i, br.get("count", 0))
        records.append(Record(f["file"], base, cov))
    return records


def _files(paths: list[str]) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(p.rglob("*.gcov.json.gz")) + sorted(p.rglob("*.gcov.json"))
        else:
            out.append(p)
    return out


def load_gcov_json(paths: list[str]) -> list[Record]:
    files = _files(paths)
    if not files:
        raise LoadError(f"no *.gcov.json(.gz) files found in {', '.join(paths)}")
    records = []
    for f in files:
        try:
            raw = f.read_bytes()
            if f.suffix == ".gz":
                raw = gzip.decompress(raw)
            for doc in _documents(raw.decode("utf-8", "surrogateescape")):
                records += parse_gcov_json(doc)
        except (OSError, ValueError, KeyError) as e:
            raise LoadError(f"cannot parse gcov JSON {f}: {e}") from e
    return records
