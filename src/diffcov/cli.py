"""diffcov: coverage of only the lines a set of commits changed."""
from __future__ import annotations

import argparse
import fnmatch
import functools
import os
import sys
import tempfile

from . import gitlines
from .classify import classify, evaluate, is_code
from .coverage import LoadError, Record
from .coverage.gcov import load_gcov_json
from .coverage.lcov import load_lcov
from .coverage.llvm import load_llvm, merge_profraw
from .model import CoverageData
from .paths import PathNormalizer, parse_path_map
from .report import RENDERERS, Report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="diffcov",
        description="Coverage of only the lines a set of commits changed.",
    )
    p.add_argument("--commits", nargs="+", required=True, metavar="SHA|A..B",
                   help="commits to measure: SHAs, refs, or A..B ranges (any mix)")
    p.add_argument("--rev", default="HEAD",
                   help="revision that was built and tested; all line numbers refer to it (default: HEAD)")
    p.add_argument("--repo", default=".", help="path inside the git repo (default: .)")

    src = p.add_argument_group("coverage data (any combination; merged)")
    src.add_argument("--llvm-profdata", metavar="FILE", help="merged .profdata")
    src.add_argument("--profraw", nargs="+", metavar="FILE", help=".profraw files to merge with llvm-profdata")
    src.add_argument("--object", action="append", default=[], metavar="BIN",
                     help="instrumented binary for llvm-cov (repeat for every test binary)")
    src.add_argument("--gcov-json", action="append", default=[], metavar="PATH",
                     help="directory or file of gcov --json-format output (*.gcov.json.gz)")
    src.add_argument("--lcov", action="append", default=[], metavar="FILE", help="lcov tracefile")

    paths = p.add_argument_group("paths")
    paths.add_argument("--source-root", help="directory relative paths in coverage data are relative to (default: repo root)")
    paths.add_argument("--path-map", action="append", default=[], metavar="FROM=TO",
                       help="rewrite a path prefix in the coverage data, e.g. /ci/checkout=. (repeatable)")
    paths.add_argument("--include", action="append", default=[], metavar="GLOB", help="only these repo paths")
    paths.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip these repo paths")

    out = p.add_argument_group("result")
    out.add_argument("--metric", choices=["line", "branch", "region"], default="line",
                     help="metric for --fail-under (default: line)")
    out.add_argument("--fail-under", type=float, metavar="PCT", help="fail if the metric is below PCT")
    out.add_argument("--unknown", choices=["fail", "warn", "ignore"], default="fail",
                     help="changed source files missing from the coverage data (default: fail)")
    out.add_argument("--no-blame", action="store_true",
                     help="skip the blame pass (faster; misses code later moved to another file)")
    out.add_argument("--format", choices=sorted(RENDERERS), default="text")
    out.add_argument("-o", "--output", metavar="FILE", help="write the report here instead of stdout")
    return p


def load_coverage(args: argparse.Namespace, normalize: PathNormalizer, tmpdir: str) -> CoverageData:
    records: list[Record] = []
    profdata = args.llvm_profdata
    if args.profraw:
        if profdata:
            raise LoadError("use --llvm-profdata or --profraw, not both")
        profdata = merge_profraw(args.profraw, os.path.join(tmpdir, "merged.profdata"))
    if profdata:
        records += load_llvm(profdata, args.object, regions=True)
    elif args.object:
        raise LoadError("--object needs --llvm-profdata or --profraw")
    if args.gcov_json:
        records += load_gcov_json(args.gcov_json)
    for f in args.lcov:
        records += load_lcov(f)
    if not records:
        raise LoadError("no coverage data: give --llvm-profdata/--profraw, --gcov-json or --lcov")
    data = CoverageData()
    for r in records:
        path = normalize(r.path, r.base)
        if path is not None:
            data.add(path, r.cov)
    return data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def warn(msg: str) -> None:
        print(f"diffcov: warning: {msg}", file=sys.stderr)

    try:
        repo = gitlines.toplevel(args.repo)
        normalize = PathNormalizer(repo, args.source_root, [parse_path_map(m) for m in args.path_map])
        with tempfile.TemporaryDirectory(prefix="diffcov-") as tmp:
            cov = load_coverage(args, normalize, tmp)
        if args.metric == "region" and not cov.has_regions:
            raise LoadError("--metric region needs LLVM coverage (--llvm-profdata/--profraw)")
        if args.metric == "branch" and not cov.has_branches:
            raise LoadError("--metric branch: the coverage data has no branch information")

        def wanted(path: str) -> bool:
            if args.include and not any(fnmatch.fnmatch(path, g) for g in args.include):
                return False
            return not any(fnmatch.fnmatch(path, g) for g in args.exclude)

        changed = gitlines.changed_lines(
            repo, args.commits, args.rev,
            blame=not args.no_blame,
            blame_filter=lambda p: wanted(p) and (p in cov.files or is_code(p)),
        )
        changed.lines = {p: ls for p, ls in changed.lines.items() if wanted(p)}
    except (gitlines.GitError, LoadError, ValueError) as e:
        print(f"diffcov: error: {e}", file=sys.stderr)
        return 2

    for sha in changed.not_ancestors:
        warn(f"{sha[:12]} is not an ancestor of {args.rev}; ignored")
    dirty = gitlines.dirty_files(repo, changed.rev, changed.lines)
    if dirty:
        warn(
            f"working tree differs from {args.rev} in {', '.join(dirty)}; "
            "if coverage was built from the working tree, line numbers may not match"
        )

    result = classify(changed.lines, cov)
    verdict = evaluate(result, args.metric, args.fail_under, args.unknown)
    source = functools.lru_cache(maxsize=None)(lambda path: gitlines.file_at(repo, changed.rev, path))
    text = RENDERERS[args.format](Report(result, verdict, changed, args.metric, source))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
