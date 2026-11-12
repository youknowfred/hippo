"""GAT-1: the write gate's checker, and the behaviours of a hand-built floor gate reproduced.

Every fixture is synthetic. The cases come from enumerating what a corpus's own vendored
write-time gate and floor checker did (a delta judgment on the floor at write time and at
commit time); each test names the behaviour it pins and whether hippo keeps it or departs
from it on purpose.
"""

from __future__ import annotations

import json

from memory import gate as G
from memory.lint_floor import banned_lines, floor_governance, measure_floor
from memory.provenance import read_floor_lint

from .conftest import write_file

BANNED = r"DONE-NOW|#\d{3,}|\b[0-9a-f]{8}\b"
POLICY_DECL = {"banned_re": BANNED, "max_line": 80, "section_budgets": {"## Active": 120}}


def _policy(memory_dir, floor_lint=POLICY_DECL, where="hippo.json"):
    if floor_lint is not None:
        write_file(memory_dir, where, json.dumps({"floor_lint": floor_lint}))
    return read_floor_lint(memory_dir)


FLOOR = (
    "# Memory\n\n"
    "## Active\n- [Tile cache](tile-cache.md) — warm before a deploy\n\n"
    "## User\n- [Prefers short replies](prefers-short.md) — keep answers tight\n"
)
ROW = "- [More](more.md) — another active lane here\n"


def active(n):
    """FLOOR with ``n`` more rows in the budgeted section."""
    return FLOOR.replace("\n\n## User", "\n" + ROW * n + "\n## User")


def kinds(findings):
    return sorted(f["kind"] for f in findings)


# --- the measurement is lint_floor's own ------------------------------------------------
def test_measure_floor_is_floor_governance_on_the_same_bytes(memory_dir):
    pol = _policy(memory_dir)
    text = FLOOR + "- see #4521 now\n" + "x" * 90 + "\n"
    write_file(memory_dir, "MEMORY.md", text)
    assert measure_floor(text.encode("utf-8"), pol) == floor_governance(memory_dir)
    assert [r["line"] for r in banned_lines(text, BANNED)] == [8]


# --- estate behaviour: a NEW status token on the floor objects ---------------------------
def test_new_banned_token_line_objects(memory_dir):
    pol = _policy(memory_dir)
    f = G.judge_floor(FLOOR, FLOOR + "- [Billing](billing.md) — DONE-NOW\n", pol)
    assert kinds(f) == ["floor_banned"]
    assert f[0]["before"] == 0 and f[0]["after"] == 1 and f[0]["lines"] == [8]


def test_short_hash_and_issue_number_count_as_banned(memory_dir):
    pol = _policy(memory_dir)
    assert kinds(G.judge_floor(FLOOR, FLOOR + "- fixed in 1a2b3c4d\n", pol)) == ["floor_banned"]
    assert kinds(G.judge_floor(FLOOR, FLOOR + "- see #812\n", pol)) == ["floor_banned"]


# --- estate behaviour: a NEW over-long line objects --------------------------------------
def test_new_long_line_objects(memory_dir):
    pol = _policy(memory_dir)
    f = G.judge_floor(FLOOR, FLOOR + "- [Long](long.md) — " + "detail " * 20 + "\n", pol)
    assert kinds(f) == ["floor_longlines"] and f[0]["after"] == 1


# --- estate behaviour: standing debt carried verbatim passes -----------------------------
def test_standing_debt_carried_verbatim_passes(memory_dir):
    pol = _policy(memory_dir)
    debt = FLOOR + "- old row DONE-NOW\n- " + "y" * 100 + "\n"
    assert G.judge_floor(debt, debt + "- [New](new.md) — a clean hook\n", pol) == []


def test_removing_debt_passes(memory_dir):
    pol = _policy(memory_dir)
    debt = FLOOR + "- old row DONE-NOW\n"
    assert G.judge_floor(debt, FLOOR, pol) == []


# --- deliberate difference: counts, not line identity ------------------------------------
def test_rewording_a_debt_line_holds_the_count_and_passes(memory_dir):
    """The hand-built gate keyed a finding on the line's exact text, so a typo fix on a
    standing-debt line read as a NEW violation and was refused. hippo counts: the same
    number of flagged lines is not worse."""
    pol = _policy(memory_dir)
    before = FLOOR + "- old row DONE-NOW, recieved\n"
    after = FLOOR + "- old row DONE-NOW, received\n"
    assert G.judge_floor(before, after, pol) == []


def test_adding_a_second_flagged_line_objects_even_with_debt(memory_dir):
    pol = _policy(memory_dir)
    before = FLOOR + "- old row DONE-NOW\n"
    f = G.judge_floor(before, before + "- another #9001\n", pol)
    assert kinds(f) == ["floor_banned"] and (f[0]["before"], f[0]["after"]) == (1, 2)
    assert f[0]["lines"] == [9]


# --- estate behaviour: a budgeted section objects only over AND growing -------------------
def test_section_over_budget_and_growing_objects(memory_dir):
    pol = _policy(memory_dir)
    f = G.judge_floor(FLOOR, active(3), pol)
    assert kinds(f) == ["section_budget"] and f[0]["budget"] == 120


def test_section_over_budget_but_shrinking_passes(memory_dir):
    pol = _policy(memory_dir)
    assert G.judge_floor(active(4), active(2), pol) == []


def test_section_under_budget_growing_passes(memory_dir):
    pol = _policy(memory_dir)
    assert G.judge_floor(FLOOR, FLOOR.replace("deploy\n", "deploy\n- x\n"), pol) == []


# --- estate behaviour: the read limit; hippo keeps the delta posture there too ----------
def test_growing_past_the_read_limit_objects_and_says_so(memory_dir):
    pol = _policy(memory_dir, None)
    big = FLOOR + ("- [P](p.md) — pointer\n" * 1300)
    f = G.judge_floor(big, big + "- [Q](q.md) — one more\n", pol)
    assert "floor_bytes_warn" in kinds(f)
    row = [x for x in f if x["kind"] == "floor_bytes_warn"][0]
    assert row["past_read_limit"] == 25000 and "read limit" in G.describe(row)


def test_shrinking_but_still_past_the_read_limit_passes(memory_dir):
    """Deliberate difference: the hand-built gate refused ANY floor left past the read
    limit, so a fold in progress that trimmed it from 30 KB to 27 KB was refused. hippo
    lets every shrinking edit through (the ratified delta posture)."""
    pol = _policy(memory_dir, None)
    big = FLOOR + ("- [P](p.md) — pointer\n" * 1400)
    assert G.judge_floor(big, big[: len(big) - 2000], pol) == []


def test_lines_warn_line(memory_dir):
    pol = _policy(memory_dir, None)
    many = FLOOR + "- x\n" * 150
    assert kinds(G.judge_floor(many, many + "- y\n", pol)) == ["floor_lines_warn"]


# --- estate behaviour: no floor yet reads as an empty base ------------------------------
def test_a_new_floor_is_judged_against_nothing(memory_dir):
    pol = _policy(memory_dir)
    assert kinds(G.judge_floor(None, FLOOR + "- DONE-NOW\n", pol)) == ["floor_banned"]
    assert G.judge_floor(None, FLOOR, pol) == []


def test_deleting_the_floor_objects_to_nothing(memory_dir):
    assert G.judge_floor(FLOOR, None, _policy(memory_dir)) == []


# --- deliberate difference: an undeclared corpus is still measured for size --------------
def test_undeclared_corpus_checks_size_only(memory_dir):
    """The hand-built gate only knew a floor whose policy declared ``floor_lint``. The
    harness read window applies to every corpus, so hippo measures size always and the
    pattern/line/section checks only when declared."""
    pol = _policy(memory_dir, None)
    assert G.judge_floor(FLOOR, FLOOR + "- DONE-NOW " + "z" * 200 + "\n", pol) == []


def test_legacy_format_marker_policy_is_read(memory_dir):
    pol = _policy(memory_dir, POLICY_DECL, where=".format")
    assert kinds(G.judge_floor(FLOOR, FLOOR + "- DONE-NOW\n", pol)) == ["floor_banned"]


# --- new in hippo: the same pattern over memory bodies ----------------------------------
NOTE = '---\nname: tile-cache\ndescription: "warm the tile cache"\ntype: project\n---\nWarm it first.\n'


def test_body_gaining_a_banned_line_objects(memory_dir):
    pol = _policy(memory_dir)
    f = G.judge_file("tile-cache.md", NOTE, NOTE + "Status: DONE-NOW\n", pol)
    assert kinds(f) == ["body_banned"] and f[0]["lines"] == [7]


def test_body_debt_does_not_object(memory_dir):
    pol = _policy(memory_dir)
    debt = NOTE + "Status: DONE-NOW\n"
    assert G.judge_file("tile-cache.md", debt, debt + "More plain text.\n", pol) == []
    assert G.judge_file("tile-cache.md", debt, NOTE, pol) == []


def test_frontmatter_is_not_body(memory_dir):
    pol = _policy(memory_dir)
    fm = NOTE.replace('type: project', 'type: project\nissue: "#4521"')
    assert G.judge_file("tile-cache.md", NOTE, fm, pol) == []


def test_new_memory_file_with_a_banned_body_objects(memory_dir):
    pol = _policy(memory_dir)
    assert kinds(G.judge_file("new.md", None, NOTE + "see #1234\n", pol)) == ["body_banned"]


def test_no_pattern_no_body_check(memory_dir):
    pol = _policy(memory_dir, {"max_line": 80})
    assert G.judge_file("tile-cache.md", NOTE, NOTE + "DONE-NOW #1234\n", pol) == []


def test_non_memory_files_are_not_judged(memory_dir):
    pol = _policy(memory_dir)
    assert G.judge_file("CONVENTIONS.md", "", "DONE-NOW\n", pol) == []
    assert G.judge_file("hippo.json", "", "{}", pol) == []


# --- caller budgets ---------------------------------------------------------------------
def test_extra_hard_budgets_use_the_same_posture(memory_dir):
    pol = _policy(memory_dir, None)
    extra = {"floor_bytes": 150, "floor_lines": 7}
    f = G.judge_floor(FLOOR, FLOOR + "- [B](b.md) — another\n", pol, extra)
    assert kinds(f) == ["floor_bytes", "floor_lines"]
    assert G.judge_floor(FLOOR + "- [B](b.md) — another\n", FLOOR, pol, extra) == []
    assert G.judge_floor(FLOOR, FLOOR + "- y\n", pol, {"floor_bytes": True, "floor_lines": -1}) == []


def test_describe_names_numbers_budget_and_way_out(memory_dir):
    pol = _policy(memory_dir)
    f = G.judge_floor(FLOOR, active(3), pol)
    line = G.describe(f[0])
    assert "MEMORY.md" in line and "->" in line and "budget of 120" in line and "fold" in line


def test_checker_never_raises_on_garbage():
    assert G.judge_floor("x", "y", {}) == []
    assert G.judge_body("x", "y", {"banned_re": "("}, "a.md") == []
