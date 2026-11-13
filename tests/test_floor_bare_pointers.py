"""A floor pointer may be a bare slug.

hippo writes a floor pointer as ``- [title](name.md) — hook``, and every floor reader used to
find pointers by that markdown link only. A corpus may write its floor as bare bullets,
``- name — text`` (the link form spells the name twice, and floor bytes are scarce): on such a
floor every link-only reader saw no pointer, so the prompt hook re-injected floor memories as
ordinary rows, the lint and the trim report weighed nothing, and the floor diet could neither
see nor remove a line.

One definition now (``lint_floor.pointer_targets``): a markdown link to ``<name>.md`` as
before, or a top-level list item whose first token is a bare name (optionally in backticks,
optionally ``name.md``) followed by an em dash, a spaced hyphen or a colon, WHERE A MEMORY FILE
OF THAT NAME EXISTS. The existence test is what keeps a prose bullet prose.
"""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import floor_diet as FD
from memory import recall as R
from memory import tend as TD
from memory import tend_queue as Q
from memory.lint_floor import floor_memory_names, floor_violations, pointer_targets, trim_safety_report
from memory.new_memory_floor import _remove_floor_pointer, _with_pointer

from .conftest import write_file


def _mem(name, desc, mtype="feedback"):
    return f'---\nname: {name}\ndescription: "{desc}"\ntype: {mtype}\n---\n{desc}, in the body too.\n'


_MEMS = {
    "no_backcompat": ("no backward compat one path refactor single pr", "feedback"),
    "owner_role": ("the owner runs the platform team and prefers short answers", "user"),
    "deploy_steps": ("deploy rollback steps for the web app release", "feedback"),
    "voyage_reranker": ("voyage rerank cross encoder primary reranker bm25 hybrid fallback", "reference"),
    "inflight_lane": ("the lane in flight this week on the billing export", "project"),
}

# Bare bullets in every accepted shape, a prose bullet whose first word is not a memory, an
# indented sub-bullet, and a bare pointer under a section that is not a floor section.
_BARE_FLOOR = (
    "# Memory index\n\n"
    "## In flight\n"
    "- inflight_lane — the billing export lane\n\n"
    "## User\n"
    "- `owner_role` — who the owner is\n\n"
    "## Working Style & Process Feedback\n"
    "- no_backcompat — one path, no shims; see deploy_steps too\n"
    "- deploy_steps.md: the rollback order\n"
    "- remember — prose, not a memory of that name\n"
    "  - voyage_reranker — an indented sub-bullet is detail, not an index entry\n"
)


def _corpus(tmp_path, monkeypatch, floor=_BARE_FLOOR, policy=None):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    for n, (desc, mtype) in _MEMS.items():
        write_file(md, f"{n}.md", _mem(n, desc, mtype))
    write_file(md, "MEMORY.md", floor)
    if policy:
        write_file(md, "hippo.json", json.dumps(policy))
    B.build_index(md, B.default_index_dir(md))
    return md, repo


def _rows(tmp_path):
    with open(str(tmp_path / "tel" / "recall_events.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------------- #
# The repro: the hook and floor_memory_names on a bare floor
# --------------------------------------------------------------------------- #
def test_a_bare_floor_names_its_pointers(tmp_path, monkeypatch):
    md, _repo = _corpus(tmp_path, monkeypatch)
    # Backticked and ``.md`` forms count; a prose first word does not; neither does the
    # sub-bullet, a name mentioned later in a line, or a pointer outside the floor sections.
    assert floor_memory_names(md) == {"owner_role", "no_backcompat", "deploy_steps"}


def test_the_prompt_hook_collapses_a_bare_floor_memory_instead_of_serving_it(tmp_path, monkeypatch, capsys):
    md, _repo = _corpus(tmp_path, monkeypatch)
    idx = B.default_index_dir(md)
    assert R.main(["no backward compat refactor voyage reranker", "--memory-dir", md, "--index-dir", idx]) == 0
    out = capsys.readouterr().out
    floor_line = next(ln for ln in out.splitlines() if "no_backcompat" in ln)
    assert "already in floor (MEMORY.md):" in floor_line
    row = _rows(tmp_path)[-1]
    assert "no_backcompat" not in row["names"] and "no_backcompat" in row["collapsed"]
    assert "voyage_reranker" in row["names"]


# --------------------------------------------------------------------------- #
# The one definition
# --------------------------------------------------------------------------- #
def test_pointer_targets_reads_both_forms_and_only_existing_bare_names():
    known = {"alpha", "beta.two", "gamma-3"}.__contains__
    assert pointer_targets("- [Alpha](alpha.md) — hook", known) == ["alpha.md"]
    assert pointer_targets("- [A](missing.md) — a link may be a missing target", known) == ["missing.md"]
    assert pointer_targets("- alpha — text", known) == ["alpha.md"]
    assert pointer_targets("- `beta.two` — text", known) == ["beta.two.md"]
    assert pointer_targets("- gamma-3.md: text", known) == ["gamma-3.md"]
    assert pointer_targets("- `gamma-3.md` - text", known) == ["gamma-3.md"]
    assert pointer_targets("* alpha: text", known) == ["alpha.md"]
    assert pointer_targets("- alpha —", known) == ["alpha.md"]
    # Prose: no such memory, no separator, not a list item, indented, or allow-listed.
    assert pointer_targets("- delta — no memory of that name", known) == []
    assert pointer_targets("- alpha is a word here", known) == []
    assert pointer_targets("- alpha-beta — the token is the whole slug", known) == []
    assert pointer_targets("alpha — not a list item", known) == []
    assert pointer_targets("  - alpha — a sub-bullet", known) == []
    assert pointer_targets("- MEMORY.full.md — snapshot", lambda n: True) == []
    # Several names on one line: the leading one is the pointer, the rest is its text.
    assert pointer_targets("- alpha — see beta.two and gamma-3", known) == ["alpha.md"]
    assert pointer_targets("- alpha, beta.two — a list is not a pointer", known) == []
    # Without a way to test existence, only the link form reads (a pure caller).
    assert pointer_targets("- alpha — text") == []
    # A line with a link stays link-form, exactly as before.
    assert pointer_targets("- alpha — see [B](beta.two.md)", known) == ["beta.two.md"]


def test_lint_flags_a_bare_pointer_outside_the_floor_sections_and_never_a_missing_bare(tmp_path, monkeypatch):
    md, _repo = _corpus(tmp_path, monkeypatch)
    v = floor_violations(md)
    assert v["rebloat"] == [{"file": "inflight_lane.md", "section": "In flight"}]
    assert v["missing_targets"] == []  # "remember" is prose, not link rot


def test_trim_report_weighs_bare_pointers(tmp_path, monkeypatch):
    md, _repo = _corpus(tmp_path, monkeypatch)
    rows = trim_safety_report(md)["rows"]
    assert {r["name"] for r in rows} == {"owner_role", "no_backcompat", "deploy_steps"}


# --------------------------------------------------------------------------- #
# Removal: the diet's demote, promote's removal, and the writer's own checks
# --------------------------------------------------------------------------- #
def test_the_floor_diet_sees_and_demotes_a_bare_pointer(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, policy={"budgets": {"floor_bytes": 120}})
    p = FD.plan(md)
    assert p["pointers"] == 4  # every section is weighed: the in-flight line too
    ids = [e["id"] for e in Q.build_queue(md, repo, kinds=("floor",), write_cache=False)["pending"]
           if e["target"].startswith(FD.TARGET_PREFIX)]
    assert ids
    name = ids[0].split(":", 2)[2]
    before = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read().split("\n")
    assert TD.apply(ids[0], "demote", memory_dir=md, repo_root=repo)["ok"]
    after = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read().split("\n")
    gone = [ln for ln in before if ln not in after]
    assert len(gone) == 1 and len(after) == len(before) - 1 and name in gone[0]
    assert os.path.isfile(os.path.join(md, f"{name}.md"))


def test_removal_of_a_bare_pointer_works_after_its_file_is_gone(tmp_path, monkeypatch):
    # Promote moves the file out first, then removes the pointer: the name is given, so the
    # line is matched by name and needs no file.
    md, _repo = _corpus(tmp_path, monkeypatch)
    os.remove(os.path.join(md, "deploy_steps.md"))
    assert _remove_floor_pointer(md, "deploy_steps")["status"] == "removed"
    text = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    assert "deploy_steps.md:" not in text
    assert "see deploy_steps too" in text  # a mention in another line's text is not touched
    assert _remove_floor_pointer(md, "remember")["status"] == "removed"  # named: it goes
    assert _remove_floor_pointer(md, "nothing_here")["status"] == "skipped"


def test_the_writer_does_not_pin_a_name_a_bare_pointer_already_pins():
    text = "## User\n- `owner_role` — who the owner is\n"
    new, out = _with_pointer(text, "## User", "owner_role", "Owner", "hook")
    assert new is None and out["reason"] == "pointer already present"
    new, out = _with_pointer(text, "## User", "zed_note", "Zed", "hook")
    assert out["status"] == "appended" and new.endswith("- [Zed](zed_note.md) — hook\n")


def test_ship_replays_a_demoted_bare_pointer(tmp_path):
    from memory import ship as S

    base = "# M\n\n## User\n- alpha — a\n- beta — b\n"
    copy = "# M\n\n## User\n- beta — b\n"
    known = {"alpha", "beta"}.__contains__
    assert S.floor_changes(base, copy) is None  # without existence, a bare line is a hand edit
    adds, removes = S.floor_changes(base, copy, known)
    assert adds == [] and removes == ["alpha"]
    text, n_add, n_rm = S.replay_floor(base + "- gamma — c\n", adds, removes)
    assert (n_add, n_rm) == (0, 1) and text == "# M\n\n## User\n- beta — b\n- gamma — c\n"


# --------------------------------------------------------------------------- #
# A link-form floor reads exactly as before
# --------------------------------------------------------------------------- #
def test_a_link_floor_with_prose_bullets_is_unchanged(tmp_path, monkeypatch):
    floor = (
        "# Agent Memory\n\n## User\n- [Owner](owner_role.md) — who\n- note: a prose bullet\n\n"
        "## Working Style & Process Feedback\n- [No compat](no_backcompat.md) — one path\n\n"
        "## Recalled on demand\n- [Voyage](voyage_reranker.md) — reranker\n"
    )
    md, _repo = _corpus(tmp_path, monkeypatch, floor=floor)
    assert floor_memory_names(md) == {"owner_role", "no_backcompat"}
    assert floor_violations(md) == {"rebloat": [{"file": "voyage_reranker.md", "section": "Recalled on demand"}],
                                    "missing_targets": []}
