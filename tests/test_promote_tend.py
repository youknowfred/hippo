"""OWN-2: promotion as a tend kind.

A feedback/user memory that another registered, trusted corpus on this machine also keeps
becomes a tend item in the corpus being tended: ``promote:<name>`` when the other text is
the same (a yes lifts it into the user tier through the Promote flow's writer), or
``promote:drift:<name>|<corpus>/<twin>`` when the same rule reads differently there (the
evidence is a diff; ``adopt`` takes the other text HERE only, ``distinct`` keeps both,
``promote`` lifts this one). Untrusted corpora contribute nothing and are never named; a
corpus with no other corpus has no item and an unchanged queue; a dismissed pair stays
dismissed until a text moves; the scan is cached under this corpus's telemetry dir.
"""

from __future__ import annotations

import json
import os
import subprocess

from memory import promote_scan as PS
from memory import promote_tend as PT
from memory import registry as REG
from memory import tend as TD
from memory import tend_queue as Q
from memory import trust as T
from memory.build_index import build_index, default_index_dir

from .conftest import write_file

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}

# BM25's idf mass is degenerate on a 1-2 document corpus, so every fixture corpus carries
# a few unrelated notes beside its lessons.
_FILLERS = (
    ("kiln-schedule-note", "the pottery kiln fires on alternate thursdays only"),
    ("harbor-tide-note", "the harbor ferry waits for the evening high tide"),
    ("orchard-graft-note", "apple scions are grafted onto dwarf rootstock in march"),
    ("choir-tuning-note", "the choir tunes to the organ's slightly flat middle c"),
    ("bakery-starter-note", "the sourdough starter is fed rye flour twice a day"),
)

_RULE = "always rehearse the rollback before the maintenance window opens"


def _mem(name: str, desc: str, body: str, mtype: str = "feedback", extra: str = "") -> str:
    return (f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n{extra}'
            f'---\n{body}\n')


def _corpus(tmp_path, slug: str, lessons: dict, *, register: bool = True) -> tuple:
    """A git repo with a corpus of filler notes plus ``lessons`` (name -> file text),
    committed, indexed, and (by default) registered. Returns ``(root, memory_dir)``."""
    root = str(tmp_path / slug)
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    subprocess.run(["git", "init", "-q", root], check=True, env=_GIT_ENV)
    for name, desc in _FILLERS:
        write_file(md, f"{name}.md", _mem(name, desc, "Body.", mtype="project"))
    for name, text in lessons.items():
        write_file(md, f"{name}.md", text)
    write_file(root, ".gitignore", ".hippo/\n")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True, env=_GIT_ENV)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    os.makedirs(os.path.join(root, ".hippo", "telemetry"), exist_ok=True)
    build_index(md, default_index_dir(md))
    if register:
        assert REG.register_project(root, md) is True
    return root, md


def _pending(md: str, root: str) -> list:
    return [e for e in Q.build_queue(md, root, kinds=("promote",), write_cache=False)["pending"]]


def _ids(md: str, root: str) -> list:
    return [e["id"] for e in _pending(md, root)]


def _env(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


# --------------------------------------------------------------------------- #
# The kind is registered everywhere the queue enumerates kinds
# --------------------------------------------------------------------------- #
def test_promote_is_a_registered_kind_with_gate_source_and_verdicts():
    assert "promote" in Q.KINDS and "promote" in Q.GATES and "promote" in Q.SOURCES
    assert TD.VERDICTS["promote"] == ("promote", "adopt", "distinct")


# --------------------------------------------------------------------------- #
# Gate: a corpus with no other registered corpus — no item, unchanged queue
# --------------------------------------------------------------------------- #
def test_lone_corpus_has_no_promote_item_and_an_unchanged_queue(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "alone", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    full = Q.build_queue(md, root, write_cache=False)
    assert full["counts"]["promote"] == 0
    others = tuple(k for k in Q.KINDS if k != "promote")
    without = Q.build_queue(md, root, kinds=others, write_cache=False)
    assert [e["id"] for e in full["pending"]] == [e["id"] for e in without["pending"]]
    assert not os.path.exists(PT._cache_path(md)), "nothing to compare, so nothing cached"


# --------------------------------------------------------------------------- #
# The promote shape
# --------------------------------------------------------------------------- #
def test_same_text_in_another_corpus_is_a_promote_item_naming_it(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    pending = _pending(md, root)
    assert [e["id"] for e in pending] == ["promote:rollback-first"]
    assert "sibling" in pending[0]["evidence"]
    text = TD.render_next(pending[0], md, root, 1)
    assert "sibling: rehearse-rollback.md, the same text" in text
    assert _RULE in text and "promote | adopt | distinct" in text


def test_a_same_named_twin_counts(tmp_path, monkeypatch):
    """The in-corpus duplicate check skips its own name; across corpora the same name is
    the likeliest twin, so the scan must not skip it."""
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    _corpus(tmp_path, "sibling", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    assert _ids(md, root) == ["promote:rollback-first"]


def test_promote_verdict_lifts_into_the_user_tier_and_removes_the_project_copy(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    sib_root, sib_md = _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    sib_before = open(os.path.join(sib_md, "rehearse-rollback.md"), "rb").read()
    r = TD.apply("promote:rollback-first", "promote", memory_dir=md, repo_root=root)
    assert r["ok"], r
    from memory.provenance import user_memory_dir

    lifted = os.path.join(user_memory_dir(), "rollback-first.md")
    assert os.path.isfile(lifted) and "origin" in open(lifted).read()
    assert not os.path.exists(os.path.join(md, "rollback-first.md"))
    assert open(os.path.join(sib_md, "rehearse-rollback.md"), "rb").read() == sib_before
    assert _ids(md, root) == []


def test_a_linked_lesson_promotes_with_its_referrers_named_and_resolving_cross_tier(tmp_path, monkeypatch):
    """The Promote flow refuses a memory others link to unless forced; the tend item names
    those referrers in its evidence, so the yes covers them, and afterwards the links read
    as cross-tier (the user tier), not dangling."""
    from memory.lint_links import lint

    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "Rehearse it."),
        "window-checklist": _mem("window-checklist", "what the maintenance checklist covers",
                                 "Steps.\n\nRelated: [[rollback-first]]", mtype="project"),
    })
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    entry = _pending(md, root)[0]
    assert "linked from 1 memory(ies) here: window-checklist" in TD.render_next(entry, md, root, 1)
    r = TD.apply("promote:rollback-first", "promote", memory_dir=md, repo_root=root)
    assert r["ok"] and "1 link(s) here now resolve to the user tier" in r["message"], r
    report = lint(md)
    assert not [d for d in report["dangling"] if d["target"] == "rollback-first"]
    assert [d for d in report["cross_tier"] if d["target"] == "rollback-first"]


def test_machine_local_lessons_are_never_proposed_for_the_user_tier(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "Rehearse it.", extra="  machine_local: true\n")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    assert _ids(md, root) == []


def test_a_retired_lesson_never_appears(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "Rehearse it.", extra="  invalid_after: 2026-01-01\n")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    assert _ids(md, root) == []


# --------------------------------------------------------------------------- #
# The drifted shape
# --------------------------------------------------------------------------- #
def _drifted(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "Rehearse it on staging.\n\nRelated: [[kiln-schedule-note]]")})
    sib_root, sib_md = _corpus(tmp_path, "sibling", {
        "rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it on a production clone, twice.")})
    return root, md, sib_root, sib_md


_DRIFT_ID = "promote:drift:rollback-first|sibling/rehearse-rollback"


def test_the_same_rule_reading_differently_is_a_drift_item_with_a_diff(tmp_path, monkeypatch):
    root, md, _sr, _sm = _drifted(tmp_path, monkeypatch)
    pending = _pending(md, root)
    assert [e["id"] for e in pending] == [_DRIFT_ID]
    text = TD.render_next(pending[0], md, root, 1)
    assert "-Rehearse it on staging." in text
    assert "+Rehearse it on a production clone, twice." in text
    assert "adopt" in text


def test_related_lines_alone_are_not_drift(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "Rehearse it.\n\nRelated: [[kiln-schedule-note]]")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    assert _ids(md, root) == ["promote:rollback-first"]


def test_adopt_rewrites_this_memory_only_and_keeps_its_own_keys(tmp_path, monkeypatch):
    root, md, _sr, sib_md = _drifted(tmp_path, monkeypatch)
    sib_before = open(os.path.join(sib_md, "rehearse-rollback.md"), "rb").read()
    r = TD.apply(_DRIFT_ID, "adopt", memory_dir=md, repo_root=root)
    assert r["ok"], r
    after = open(os.path.join(md, "rollback-first.md"), encoding="utf-8").read()
    assert "Rehearse it on a production clone, twice." in after
    assert "name: rollback-first" in after and "type: feedback" in after
    assert open(os.path.join(sib_md, "rehearse-rollback.md"), "rb").read() == sib_before
    # Now the texts agree: the drift item is gone and the lesson is a promote candidate.
    assert _ids(md, root) == ["promote:rollback-first"]


def test_adopt_is_refused_for_a_same_text_item(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    r = TD.apply("promote:rollback-first", "adopt", memory_dir=md, repo_root=root)
    assert not r["ok"] and "already the same" in r["message"]


def test_distinct_stays_dismissed_until_a_text_moves(tmp_path, monkeypatch):
    root, md, _sr, sib_md = _drifted(tmp_path, monkeypatch)
    assert TD.apply(_DRIFT_ID, "distinct", memory_dir=md, repo_root=root)["ok"]
    assert _ids(md, root) == []
    assert _ids(md, root) == [], "a dismissed pair must not return on the next pass"
    write_file(sib_md, "rehearse-rollback.md",
               _mem("rehearse-rollback", _RULE, "Rehearse it on a production clone, three times."))
    assert _ids(md, root) == [_DRIFT_ID], "a moved text brings the pair back"


def test_drift_promote_verdict_is_refused_for_a_machine_local_memory(tmp_path, monkeypatch):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "On staging.", extra="  machine_local: true\n")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "On a clone.")})
    assert _ids(md, root) == [_DRIFT_ID]
    r = TD.apply(_DRIFT_ID, "promote", memory_dir=md, repo_root=root)
    assert not r["ok"] and "this machine only" in r["message"]
    assert os.path.isfile(os.path.join(md, "rollback-first.md"))


def test_adopt_in_outbox_mode_writes_the_outbox_copy_and_the_item_leaves(tmp_path, monkeypatch):
    from memory import outbox as OB
    from memory import policy_consent as PC

    _env(monkeypatch)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)  # the real consent gates
    root, md = _corpus(tmp_path, "here", {
        "rollback-first": _mem("rollback-first", _RULE, "Rehearse it on staging.")})
    sib_root, sib_md = _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "On a clone.")})
    write_file(md, "hippo.json", json.dumps({"write_path": "outbox"}))
    write_file(root, ".gitignore", ".hippo/\n.claude/memory.outbox/\n")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True, env=_GIT_ENV)
    subprocess.run(["git", "-C", root, "commit", "-qm", "outbox"], check=True, env=_GIT_ENV)
    assert T.mark_trusted(T.gate_repo_root(md, root), memory_dir=md, origin="init")
    assert T.mark_trusted(T.gate_repo_root(sib_md, sib_root), memory_dir=sib_md, origin="init")
    assert PC.acknowledge(md, root)["ok"]
    committed = open(os.path.join(md, "rollback-first.md"), "rb").read()
    r = TD.apply(_DRIFT_ID, "adopt", memory_dir=md, repo_root=root)
    assert r["ok"], r
    assert open(os.path.join(md, "rollback-first.md"), "rb").read() == committed
    copy = os.path.join(OB.outbox_dir(md), "rollback-first.md")
    assert "On a clone." in open(copy, encoding="utf-8").read()
    assert _DRIFT_ID not in _ids(md, root), "the source reads the copy it wrote"


# --------------------------------------------------------------------------- #
# Only registered AND trusted corpora are read
# --------------------------------------------------------------------------- #
def test_an_untrusted_registered_corpus_contributes_nothing_and_is_not_named(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    root, md = _corpus(tmp_path, "here", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    _corpus(tmp_path, "quarantined", {"secret-twin-name": _mem("secret-twin-name", _RULE, "Rehearse it.")})
    assert T.mark_trusted(root) is True  # only this corpus is trusted
    result = Q.build_queue(md, root)
    assert result["counts"]["promote"] == 0
    rendered = TD.render_list(result)
    cache = open(PT._cache_path(md), encoding="utf-8").read() if os.path.exists(PT._cache_path(md)) else ""
    for leak in ("quarantined", "secret-twin-name"):
        assert leak not in rendered and leak not in cache


def test_adopt_is_refused_once_the_other_corpus_is_no_longer_trusted(tmp_path, monkeypatch):
    root, md, sib_root, _sm = _drifted(tmp_path, monkeypatch)
    entry, _state = TD.find_entry(md, root, _DRIFT_ID)
    assert entry is not None
    assert REG.deregister_project(sib_root)
    r = TD._apply_promote(entry, "adopt", md, root)
    assert not r["ok"]
    assert "Rehearse it on staging." in open(os.path.join(md, "rollback-first.md")).read()


# --------------------------------------------------------------------------- #
# Off the hot path, bounded and cached
# --------------------------------------------------------------------------- #
def test_the_scan_is_cached_and_a_moved_input_rescans(tmp_path, monkeypatch):
    root, md, _sr, sib_md = _drifted(tmp_path, monkeypatch)
    first = PT.scan_for(md)
    assert first["pairs"] and os.path.isfile(PT._cache_path(md))
    calls = []
    real = PS.twins
    monkeypatch.setattr(PS, "twins", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert PT.scan_for(md)["pairs"] == first["pairs"]
    assert calls == [], "an unchanged machine reads the cache"
    write_file(sib_md, "rehearse-rollback.md", _mem("rehearse-rollback", _RULE, "Changed."))
    os.utime(os.path.join(sib_md, "rehearse-rollback.md"), (1, 1))
    PT.scan_for(md)
    assert calls, "a changed corpus file rescans"


def test_the_smaller_side_is_the_query_side(tmp_path, monkeypatch):
    """The per-pair budget must cover the small corpus whole: with the cap at one lesson,
    the small corpus's only lesson still finds its twin among the large one's many."""
    _env(monkeypatch)
    monkeypatch.setattr(PS, "MAX_LESSONS_PER_PAIR", 1)
    many = {f"aaa-lesson-{i}": _mem(f"aaa-lesson-{i}", f"unrelated habit number {i} about {w}", "B.")
            for i, w in enumerate(("lanterns", "saddles", "violins", "glaciers"))}
    many["zz-rollback"] = _mem("zz-rollback", _RULE, "Rehearse it.")
    root, md = _corpus(tmp_path, "big", many)
    _corpus(tmp_path, "small", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    assert _ids(md, root) == ["promote:zz-rollback"]


def test_sleep_section_points_at_tend(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    root, md = _corpus(tmp_path, "here", {"rollback-first": _mem("rollback-first", _RULE, "Rehearse it.")})
    _corpus(tmp_path, "sibling", {"rehearse-rollback": _mem("rehearse-rollback", _RULE, "Rehearse it.")})
    from memory import sleep as SLEEP

    assert SLEEP.main(["--memory-dir", md, "--repo-root", root]) == 0
    out = capsys.readouterr().out
    assert "Cross-project promotion candidates" in out
    assert "tend" in out.split("Cross-project promotion candidates", 1)[1]
