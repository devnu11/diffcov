from conftest import numbered

from diffcov.gitlines import Hunk, changed_lines, map_line, parse_diff


def lines_for(repo, *specs, **kw):
    return changed_lines(str(repo.root), specs, **kw).lines


# --- map_line ---------------------------------------------------------------

def test_map_line_offsets_and_rewrites():
    hunks = [
        Hunk(2, 0, 3, 2),   # insert 2 lines after old line 2
        Hunk(5, 1, 8, 1),   # rewrite old line 5
        Hunk(7, 2, 9, 0),   # delete old lines 7-8
    ]
    assert map_line(hunks, 1) == [1]
    assert map_line(hunks, 2) == [2]
    assert map_line(hunks, 3) == [5]
    assert map_line(hunks, 5) == [8]
    assert map_line(hunks, 7) == []
    assert map_line(hunks, 9) == [9]


def test_map_line_rewritten_block_maps_to_all_new_lines():
    assert map_line([Hunk(3, 1, 3, 3)], 3) == [3, 4, 5]


def test_parse_diff_handles_new_deleted_renamed():
    text = "\n".join([
        "diff --git a/new.c b/new.c",
        "new file mode 100644",
        "--- /dev/null",
        "+++ b/new.c",
        "@@ -0,0 +1,2 @@",
        "+a",
        "+b",
        "diff --git a/gone.c b/gone.c",
        "deleted file mode 100644",
        "--- a/gone.c",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-x",
        "diff --git a/old name.c b/new name.c",
        "similarity index 100%",
        "rename from old name.c",
        "rename to new name.c",
        'diff --git "a/tab\\there.c" "b/tab\\there.c"',
        '--- "a/tab\\there.c"',
        '+++ "b/tab\\there.c"',
        "@@ -1 +1 @@",
        "-x",
        "+y",
    ])
    new, gone, renamed, quoted = parse_diff(text)
    assert (new.old_path, new.new_path, new.hunks) == (None, "new.c", [Hunk(0, 0, 1, 2)])
    assert (gone.old_path, gone.new_path) == ("gone.c", None)
    assert (renamed.old_path, renamed.new_path, renamed.hunks) == ("old name.c", "new name.c", [])
    assert quoted.new_path == "tab\there.c"


# --- changed_lines against real repos ----------------------------------------

def test_later_edit_by_other_commit_keeps_patch_lines(repo):
    """Patch A changes lines 1-4, patch B then changes line 3: A -> {1,2,3,4}."""
    base = numbered(10)
    repo.commit("base", {"f.c": base})
    a_lines = ["A " + l for l in base[:4]] + base[4:]
    a = repo.commit("A", {"f.c": a_lines})
    b_lines = list(a_lines)
    b_lines[2] = "B rewrote line three entirely"
    repo.commit("B", {"f.c": b_lines})
    assert lines_for(repo, a) == {"f.c": {1, 2, 3, 4}}


def test_other_commits_lines_excluded_and_shift_applied(repo):
    base = numbered(10)
    repo.commit("base", {"f.c": base})
    a_lines = base[:5] + ["A added this line in the middle"] + base[5:]
    a = repo.commit("A", {"f.c": a_lines})
    # B inserts two lines at the top and edits the last line.
    b_lines = ["B one at the top", "B two at the top"] + a_lines[:-1] + ["B changed the last line"]
    repo.commit("B", {"f.c": b_lines})
    assert lines_for(repo, a) == {"f.c": {8}}


def test_deleted_line_is_gone(repo):
    base = numbered(6)
    repo.commit("base", {"f.c": base})
    a_lines = ["A " + l for l in base[:3]] + base[3:]
    a = repo.commit("A", {"f.c": a_lines})
    repo.commit("B", {"f.c": [a_lines[0], a_lines[2]] + a_lines[3:]})  # delete A's line 2
    assert lines_for(repo, a) == {"f.c": {1, 2}}


def test_non_contiguous_list_and_rewrite_counted_once(repo):
    base = numbered(8)
    repo.commit("base", {"f.c": base})
    a_lines = ["A " + base[0]] + base[1:]
    a = repo.commit("A", {"f.c": a_lines})
    b_lines = a_lines[:4] + ["B " + a_lines[4]] + a_lines[5:]
    repo.commit("B", {"f.c": b_lines})
    c_lines = ["C rewrote A's line"] + b_lines[1:6] + ["C " + b_lines[6]] + b_lines[7:]
    c = repo.commit("C", {"f.c": c_lines})
    assert lines_for(repo, a, c) == {"f.c": {1, 7}}


def test_range_spec(repo):
    base = repo.commit("base", {"f.c": numbered(5)})
    repo.commit("A", {"f.c": numbered(5)[:4] + ["A five"]})
    repo.commit("B", {"g.c": ["B new file line"]})
    assert lines_for(repo, f"{base}..HEAD") == {"f.c": {5}, "g.c": {1}}


def test_root_commit(repo):
    root = repo.commit("root", {"f.c": numbered(3)})
    assert lines_for(repo, root) == {"f.c": {1, 2, 3}}


def test_rename_follows_to_new_path(repo):
    repo.commit("base", {"old.c": numbered(10)})
    a = repo.commit("A", {"old.c": numbered(9) + ["A line ten"]})
    repo.commit("B", mv=("old.c", "new.c"))
    assert lines_for(repo, a) == {"new.c": {10}}


def test_whitespace_only_changes(repo):
    base = numbered(5)
    repo.commit("base", {"f.c": base})
    a_lines = base[:2] + ["A real change here"] + base[3:]
    a = repo.commit("A", {"f.c": a_lines})
    # B reindents A's line: still A's line.
    repo.commit("B", {"f.c": a_lines[:2] + ["    A real change here"] + a_lines[3:]})
    # W only reindents: contributes nothing.
    w = repo.commit("W", {"f.c": ["\t" + l for l in base[:2]] + ["    A real change here"] + a_lines[3:]})
    assert lines_for(repo, a) == {"f.c": {3}}
    assert lines_for(repo, w) == {}


def test_code_moved_to_other_file_found_by_blame(repo):
    repo.commit("base", {"x.c": numbered(3, "x"), "y.c": numbered(3, "y")})
    func = [
        "int compute_the_answer(int input_value) {",
        "    int accumulated_result = input_value * 6;",
        "    accumulated_result = accumulated_result + 12;",
        "    return accumulated_result;",
        "}",
    ]
    a = repo.commit("A", {"x.c": numbered(3, "x") + func})
    repo.commit("B moves it", {"x.c": numbered(3, "x"), "y.c": numbered(3, "y") + func})
    assert lines_for(repo, a) == {"y.c": {4, 5, 6, 7, 8}}
    assert lines_for(repo, a, blame=False) == {}


def test_not_ancestor_ignored(repo):
    repo.commit("base", {"f.c": numbered(3)})
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit("side", {"g.c": ["side"]})
    repo.git("checkout", "-q", "main")
    res = changed_lines(str(repo.root), [side])
    assert res.lines == {} and res.not_ancestors == [side]


def test_merge_commit_stands_for_whole_branch(repo):
    base = numbered(10)
    repo.commit("base", {"f.c": base})
    repo.git("checkout", "-q", "-b", "feature")
    f1_lines = base[:2] + ["F1 added this line"] + base[2:]
    repo.commit("F1", {"f.c": f1_lines})
    f2 = repo.commit("F2", {"g.c": ["F2 new file", "second line of it"]})
    repo.git("checkout", "-q", "main")
    repo.commit("M1 unrelated", {"f.c": base[:-1] + ["M1 changed the last line"]})
    repo.git("merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    merge = repo.git("rev-parse", "HEAD").strip()
    # Later commit outside the set rewrites F1's line: still counted.
    lines = repo.root.joinpath("f.c").read_text().splitlines()
    lines[2] = "later rewrite of F1's line"
    repo.commit("later", {"f.c": lines})
    res = changed_lines(str(repo.root), [merge])
    assert res.lines == {"f.c": {3}, "g.c": {1, 2}}
    assert res.merges == [merge]
    # Branch commits are credited by blame too: code moved out of g.c is found.
    func = ["int helper_for_feature_two(int v) {", "    return v * 1234567 + 7654321;", "}"]
    repo.git("checkout", "-q", "-b", "feature2", f2)
    repo.commit("F3", {"g.c": ["F2 new file", "second line of it"] + func})
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge feature2", "feature2")
    merge2 = repo.git("rev-parse", "HEAD").strip()
    repo.commit("move", {"g.c": ["F2 new file", "second line of it"], "h.c": func})
    assert changed_lines(str(repo.root), [merge2]).lines == {"h.c": {1, 2, 3}}
