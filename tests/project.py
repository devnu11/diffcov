"""Tiny C++ project shared by the end-to-end tests."""
from conftest import numbered

UTIL_H = [
    "#pragma once",
    "inline int both(bool a, bool b) {",   # 2
    "    if (a && b)",                     # 3  partial: b never true
    "        return 1;",                   # 4  uncovered
    "    return 0;",                       # 5
    "}",
]
LIB = [
    '#include "util.h"',
    "int lib_used(int x) {",               # 2
    "    if (x > 0)",                      # 3
    "        return both(true, x > 5);",   # 4
    "    return -1;",                      # 5  uncovered
    "}",
    "int lib_unused(int x) {",             # 7  uncovered
    "    return x * 2;",                   # 8  uncovered
    "}",
]
T1 = ["int lib_used(int);", "int main() { return lib_used(1) == 0 ? 0 : 1; }"]
T2 = ['#include "util.h"', "int lib_used(int);", "int main() { return both(false, false); }"]


def commit_project(repo):
    """Base commit, the feature commit under test, then an unrelated commit."""
    repo.commit("base", {"README": numbered(2), "tests/t1.cpp": T1, "tests/t2.cpp": T2})
    feature = repo.commit("feature", {
        "inc/util.h": UTIL_H,
        "src/lib.cpp": LIB,
        "src/unbuilt.cpp": ["int never_compiled() { return 42; }"],
    })
    repo.commit("unrelated", {"README": numbered(3)})
    return feature
