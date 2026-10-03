import json

from diffcov.classify import (
    COVERED, NONEXEC, PARTIAL, UNCOVERED, UNKNOWN_HEADER, UNKNOWN_SOURCE, classify, evaluate,
)
from diffcov.gitlines import ChangedLines
from diffcov.model import CoverageData, FileCov
from diffcov.report import RENDERERS, Report, ranges


def make_cov():
    fc = FileCov()
    for n, c in {1: 1, 2: 1, 3: 0, 5: 4, 6: 4}.items():
        fc.add_line(n, c)
    fc.add_branch(2, 0, 1)
    fc.add_branch(2, 1, 0)
    fc.add_region((1, 1, 6, 2), 1)
    fc.add_region((3, 1, 3, 10), 0)
    fc.add_region((6, 5, 6, 12), 0)
    data = CoverageData()
    data.add("src/a.cpp", fc)
    return data


CHANGED = {"src/a.cpp": {1, 2, 3, 4, 6}, "src/new.cpp": {1, 2}, "inc/x.h": {5}, "README.md": {1}}


def test_classify_statuses_and_tallies():
    res = classify(CHANGED, make_cov())
    a, x, new = res.files  # sorted: inc/x.h, src/a.cpp, src/new.cpp
    a, x = x, a
    assert a.path == "src/a.cpp"
    assert a.line_status == {1: COVERED, 2: PARTIAL, 3: UNCOVERED, 4: NONEXEC, 6: PARTIAL}
    assert (a.lines.hit, a.lines.total) == (3, 4)
    assert (a.branches.hit, a.branches.total) == (1, 2)
    assert (a.regions.hit, a.regions.total) == (1, 3)
    assert x.status == UNKNOWN_HEADER and new.status == UNKNOWN_SOURCE
    assert [f.path for f in res.files] == ["inc/x.h", "src/a.cpp", "src/new.cpp"]  # README dropped


def test_evaluate_policies():
    res = classify(CHANGED, make_cov())
    v = evaluate(res, "line", 80, "fail")
    assert not v.passed and len(v.failures) == 2 and len(v.warnings) == 1
    v = evaluate(res, "line", 70, "warn")
    assert v.passed and len(v.warnings) == 2
    v = evaluate(res, "region", 50, "ignore")
    assert not v.passed and "region coverage 33.3%" in v.failures[0]


def test_ranges():
    assert ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"
    assert ranges([]) == ""


def test_renderers_smoke():
    res = classify(CHANGED, make_cov())
    rep = Report(
        res, evaluate(res, "line", 80, "fail"),
        ChangedLines(rev="a" * 40, commits=["b" * 40], lines=CHANGED), "line",
        lambda p: ["int <a> = 1;"] * 8,
    )
    text = RENDERERS["text"](rep)
    assert "src/a.cpp" in text and "uncovered  3" in text and "FAIL" in text
    doc = json.loads(RENDERERS["json"](rep))
    assert doc["files"][1]["uncovered"] == [3] and doc["passed"] is False
    md = RENDERERS["markdown"](rep)
    assert "| `src/a.cpp` |" in md and "❌" in md
    page = RENDERERS["html"](rep)
    assert "int &lt;a&gt; = 1;" in page and 'class="l uncovered"' in page
