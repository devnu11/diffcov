import gzip
import json
import os

from diffcov.coverage.gcov import load_gcov_json
from diffcov.coverage.lcov import parse_lcov
from diffcov.coverage.llvm import parse_llvm_regions
from diffcov.model import CoverageData
from diffcov.paths import PathNormalizer

LCOV = """\
TN:
SF:/src/repo/a.cpp
FN:3,_Z1fv
DA:3,4
DA:4,0
DA:5,4
BRDA:5,0,0,3
BRDA:5,0,1,-
end_of_record
SF:/src/repo/inc/h.h
DA:2,1
end_of_record
SF:/src/repo/inc/h.h
DA:2,2
DA:3,0
end_of_record
"""


def test_lcov_parse_and_merge():
    data = CoverageData()
    for r in parse_lcov(LCOV):
        data.add(r.path, r.cov)
    a = data.files["/src/repo/a.cpp"]
    assert a.lines == {3: 4, 4: 0, 5: 4}
    assert a.branches == {5: {("0", "0"): 3, ("0", "1"): 0}}
    # Header seen in two compilation units: counts summed.
    assert data.files["/src/repo/inc/h.h"].lines == {2: 3, 3: 0}


def test_gcov_json(tmp_path):
    doc = {
        "current_working_directory": "/build",
        "files": [{
            "file": "../src/a.c",
            "lines": [
                {"line_number": 2, "count": 1, "branches": [{"count": 1}, {"count": 0}]},
                {"line_number": 3, "count": 0, "branches": []},
            ],
        }],
    }
    (tmp_path / "a.gcda.gcov.json.gz").write_bytes(gzip.compress(json.dumps(doc).encode()))
    [rec] = load_gcov_json([str(tmp_path)])
    assert rec.path == "../src/a.c" and rec.base == "/build"
    assert rec.cov.lines == {2: 1, 3: 0}
    assert rec.cov.branches == {2: {0: 1, 1: 0}}


def test_llvm_regions_dedupe_and_skip_non_code():
    fn = lambda count: {
        "filenames": ["/r/a.cpp", "/r/m.h"],
        "regions": [
            [1, 1, 5, 2, count, 0, 0, 0],   # code region
            [2, 3, 2, 9, 0, 0, 0, 3],       # gap region: skipped
            [3, 1, 3, 4, 7, 0, 1, 1],       # expansion: skipped
            [8, 1, 8, 20, 0, 1, 0, 0],      # code region in macro file
        ],
    }
    out = parse_llvm_regions({"data": [{"functions": [fn(2), fn(3)]}]})
    assert out["/r/a.cpp"].regions == {(1, 1, 5, 2): 5}
    assert out["/r/m.h"].regions == {(8, 1, 8, 20): 0}


def test_path_normalizer(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    norm = PathNormalizer(str(repo), path_maps=[("/ci/checkout", ".")])
    assert norm(str(repo / "src" / "a.c")) == "src/a.c"
    assert norm("/ci/checkout/src/a.c") == "src/a.c"
    assert norm("src/a.c") == "src/a.c"
    assert norm("../src/a.c", base=str(repo / "build")) == "src/a.c"
    assert norm("/usr/include/stdio.h") is None
    # Repo reached through a symlink.
    link = tmp_path / "link"
    os.symlink(repo, link)
    assert norm(str(link / "src" / "a.c")) == "src/a.c"
