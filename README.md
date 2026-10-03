# diffcov

Coverage of **only the lines a set of commits changed**, from gcov or LLVM
source-based coverage. Use it locally or as a CI gate.

```sh
pip install -e .            # stdlib only; add [test] for pytest

# LLVM: pass every test binary so headers/inline code merge across them
diffcov --commits main..HEAD \
        --profraw build/*.profraw --object build/t1 --object build/t2 \
        --metric region --fail-under 80

# gcov (gcc 9+): run `gcov --json-format` first
diffcov --commits abc123 def456 --gcov-json build/ --format markdown

# anything that emits lcov (gcovr --lcov, lcov --capture, ...)
diffcov --commits feature-start..feature-end --lcov coverage.info --format html -o report.html
```

## Which lines count

`--commits` takes SHAs, refs and `A..B` ranges in any mix; the commits don't
have to be contiguous. `--rev` (default `HEAD`) is the revision that was built
and tested; every line number refers to it.

A line counts if any listed commit added or modified it **and it still exists
at `--rev`, even if a later commit outside the list edited it again**. If
patch A changes lines 1–4 and patch B later changes line 3, measuring A looks
at lines 1–4.

- Forward mapping carries each commit's lines through `git diff <commit> <rev>`.
  If a later commit rewrites a block containing the line, the whole new block
  counts. If it deletes the line, the line is gone.
- Blame adds lines whose origin commit is listed. This finds code that was
  later moved into another file. `--no-blame` skips it, which is faster.
- Whitespace-only changes are ignored (`-w`), and renames are followed.
- A merge commit stands for the whole branch it merged: its diff against
  its first parent (the branch plus any conflict resolution), with the
  branch's commits credited by blame. Commits that aren't ancestors of
  `--rev` are ignored, with a warning.

## Line statuses

| status | meaning |
|---|---|
| covered | instrumented and executed |
| partial | executed, but a branch outcome or LLVM region on the line never ran |
| uncovered | instrumented, never executed |
| not executable | file is instrumented, line isn't (comments, braces, continuation lines); not counted |
| not in coverage data | a changed **source** file with no coverage at all: never compiled into an instrumented binary. Fails by default (`--unknown fail\|warn\|ignore`), so it can't pass as 100%. Headers missing from the data only warn, because a header with only declarations legitimately has no coverage. |

Metrics: `line` (any loader), `branch` (gcov, lcov `BRDA`, clang ≥ 12),
`region` (LLVM only). `--fail-under` applies to the `--metric` you choose.
Every available metric is reported.

Output formats: `text`, `json`, `markdown` (for PR comments) and `html`
(one self-contained page). Exit codes: 0 pass, 1 fail, 2 usage or input error.

## Paths

Coverage paths are mapped to repo-relative paths. Relative paths resolve
against gcov's recorded working directory, or `--source-root` (default: the
repo root). Use `--path-map /ci/checkout=.` when coverage was produced
somewhere else. Files outside the repo (system headers etc.) are dropped.
`--include` / `--exclude` take globs on repo paths.

## Known limitations

- Code removed by an `#ifdef` inside a file that *is* compiled shows up as
  not executable. gcov and LLVM don't record it, so diffcov can't tell.
- The coverage build must match `--rev`. diffcov warns when the working tree
  differs from `--rev` in a changed file, but can't check the binaries.
- Build coverage at `-O0`. Optimisation merges and drops line records.

## Tests

```sh
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest            # end-to-end LLVM tests run when clang++ and llvm-cov are present
```
