"""lcov tracefiles (from lcov, gcovr --lcov, llvm-cov export -format=lcov)."""
from __future__ import annotations

from pathlib import Path

from ..model import FileCov
from . import LoadError, Record


def _int(s: str) -> int:
    # Some producers write counts as floats or '-' for "not taken".
    s = s.strip()
    if s in ("", "-"):
        return 0
    return int(float(s))


def parse_lcov(text: str, base: str | None = None) -> list[Record]:
    records: list[Record] = []
    cur: FileCov | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            cur = FileCov()
            records.append(Record(line[3:], base, cur))
        elif cur is None:
            continue
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            cur.add_line(int(parts[0]), _int(parts[1]))
        elif line.startswith("BRDA:"):
            parts = line[5:].split(",")
            if len(parts) >= 4:
                cur.add_branch(int(parts[0]), (parts[1], parts[2]), _int(parts[3]))
        elif line == "end_of_record":
            cur = None
    return records


def load_lcov(path: str) -> list[Record]:
    try:
        text = Path(path).read_text(errors="surrogateescape")
    except OSError as e:
        raise LoadError(f"cannot read lcov file {path}: {e}") from e
    return parse_lcov(text)
