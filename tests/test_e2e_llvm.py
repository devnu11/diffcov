"""End to end: real clang coverage of a tiny project, two test binaries."""
import json
import os
import shutil
import subprocess

import pytest
from project import commit_project

from diffcov.cli import main
from diffcov.coverage import LoadError
from diffcov.coverage.llvm import find_tool

CLANGXX = os.environ.get("CLANGXX", "clang++")


def _have_llvm():
    try:
        subprocess.run([*find_tool("llvm-cov"), "--version"], check=True, capture_output=True)
        return shutil.which(CLANGXX) is not None
    except (LoadError, OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _have_llvm(), reason="clang++ / llvm-cov not available")


@pytest.fixture
def built(repo, tmp_path):
    feature = commit_project(repo)
    build = tmp_path / "build"
    build.mkdir()
    flags = ["-O0", "-fprofile-instr-generate", "-fcoverage-mapping", f"-I{repo.root / 'inc'}"]
    profraws = []
    for t in ("t1", "t2"):
        exe = build / t
        subprocess.run(
            [CLANGXX, *flags, str(repo.root / "tests" / f"{t}.cpp"), str(repo.root / "src" / "lib.cpp"), "-o", str(exe)],
            check=True,
        )
        raw = build / f"{t}.profraw"
        subprocess.run([str(exe)], env={**os.environ, "LLVM_PROFILE_FILE": str(raw)})
        profraws.append(str(raw))
    return repo, feature, build, profraws


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def test_llvm_end_to_end(built, capsys):
    repo, feature, build, profraws = built
    code, out = run(
        capsys, "--repo", str(repo.root), "--commits", feature,
        "--profraw", *profraws, "--object", str(build / "t1"), "--object", str(build / "t2"),
        "--metric", "region", "--format", "json",
    )
    doc = json.loads(out.out)
    files = {f["path"]: f for f in doc["files"]}
    assert set(files) == {"inc/util.h", "src/lib.cpp", "src/unbuilt.cpp"}

    util = files["inc/util.h"]
    assert 3 in util["partial"], util
    assert util["uncovered"] == [4]
    lib = files["src/lib.cpp"]
    assert set(lib["uncovered"]) >= {5, 7, 8}
    assert lib["branches"]["total"] > 0 and lib["regions"]["total"] > 0

    assert files["src/unbuilt.cpp"]["status"] == "unknown-source"
    assert code == 1 and not doc["passed"]
    assert "README" not in files and "tests/t1.cpp" not in files


def test_llvm_unknown_warn_and_html(built, capsys, tmp_path):
    repo, feature, build, profraws = built
    report = tmp_path / "report.html"
    code, out = run(
        capsys, "--repo", str(repo.root), "--commits", feature,
        "--profraw", *profraws, "--object", str(build / "t1"), "--object", str(build / "t2"),
        "--unknown", "warn", "--metric", "branch", "--format", "html", "-o", str(report),
    )
    assert code == 0
    page = report.read_text()
    assert 'class="l partial"' in page and 'class="l uncovered"' in page and "src/unbuilt.cpp" in page
