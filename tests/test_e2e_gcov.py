"""End to end: real gcc/gcov coverage of the same project (Linux CI)."""
import json
import os
import shutil
import subprocess

import pytest
from project import commit_project

from diffcov.cli import main

GXX = os.environ.get("GXX", "g++")
GCOV = os.environ.get("GCOV", "gcov")


def _have_gcc():
    # On macOS g++ is clang in disguise; insist on the real thing.
    if not (shutil.which(GXX) and shutil.which(GCOV)):
        return False
    out = subprocess.run([GXX, "--version"], capture_output=True, text=True).stdout
    return "Free Software Foundation" in out


pytestmark = pytest.mark.skipif(not _have_gcc(), reason="gcc / gcov not available")


@pytest.fixture
def built(repo, tmp_path):
    feature = commit_project(repo)
    build = tmp_path / "build"
    for t in ("t1", "t2"):
        # One object dir per test binary, so the header shows up in two units.
        out = build / t
        out.mkdir(parents=True)
        objs = []
        for src in (repo.root / "tests" / f"{t}.cpp", repo.root / "src" / "lib.cpp"):
            obj = out / (src.stem + ".o")
            subprocess.run(
                [GXX, "-O0", "--coverage", f"-I{repo.root / 'inc'}", "-c", str(src), "-o", str(obj)],
                check=True,
            )
            objs.append(str(obj))
        exe = out / t
        subprocess.run([GXX, "--coverage", *objs, "-o", str(exe)], check=True)
        subprocess.run([str(exe)])
        gcdas = sorted(str(p.name) for p in out.glob("*.gcda"))
        subprocess.run([GCOV, "--json-format", "--branch-probabilities", *gcdas], cwd=out, check=True, capture_output=True)
    return repo, feature, build


def test_gcov_end_to_end(built, capsys):
    repo, feature, build = built
    code = main([
        "--repo", str(repo.root), "--commits", feature,
        "--gcov-json", str(build), "--metric", "branch", "--format", "json",
    ])
    doc = json.loads(capsys.readouterr().out)
    files = {f["path"]: f for f in doc["files"]}
    assert set(files) == {"inc/util.h", "src/lib.cpp", "src/unbuilt.cpp"}

    util = files["inc/util.h"]
    assert 3 in util["partial"], util
    assert util["uncovered"] == [4]
    lib = files["src/lib.cpp"]
    assert set(lib["uncovered"]) >= {5, 8}
    assert lib["branches"]["total"] > 0

    assert files["src/unbuilt.cpp"]["status"] == "unknown-source"
    assert code == 1
