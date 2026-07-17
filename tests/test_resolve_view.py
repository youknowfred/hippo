"""GOV-1: contradiction inbox + /hippo:resolve — a standing, drainable conflict queue.

Every unresolved `contradicts` pair appears corpus-wide (not only when both sides
co-surface in one recall); /hippo:resolve renders per-item verdicts. Corpus-mutating
verdicts are ordinary git edits (exercised here as plain file edits); ONLY the
mark-not-conflicting verdict lands in the per-clone gitignored ledger. Nothing auto-picks
a winner — the module structurally has no corpus-write path (AST-pinned below).
"""

from __future__ import annotations

import ast
import inspect
import json
import os

import memory.resolve_view as RV
import memory.session_start as S

from .conftest import git_commit


def _write(md, name, description, contradicts=None):
    os.makedirs(md, exist_ok=True)
    lines = ["---", f"name: {name}", f'description: "{description}"']
    if contradicts:
        lines.append(f"contradicts: [{', '.join(contradicts)}]")
    lines += ["---", "", f"body of {name}", ""]
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _conflicted_corpus(md):
    """new-api declares it contradicts old-api; bystander carries no typed edges."""
    _write(md, "old-api", "we call the v1 endpoint directly")
    _write(md, "new-api", "we stopped calling v1 — everything goes through the gateway", ["old-api"])
    _write(md, "bystander", "an unrelated fact")


# ---- the inbox (unresolved_contradictions) ------------------------------------------------ #
def test_inbox_lists_every_pair_without_co_surfacing(memory_dir, repo):
    """The acceptance bar: a standing queue — no recall, no co-surfacing, the pair is there."""
    _conflicted_corpus(memory_dir)
    inbox = RV.unresolved_contradictions(memory_dir, repo_root=repo)
    assert inbox == [{"pair": ["new-api", "old-api"], "declared_by": ["new-api"]}]


def test_mutual_declarations_collapse_to_one_pair(memory_dir, repo):
    _write(memory_dir, "a", "claim A", ["b"])
    _write(memory_dir, "b", "claim B", ["a"])
    inbox = RV.unresolved_contradictions(memory_dir, repo_root=repo)
    assert inbox == [{"pair": ["a", "b"], "declared_by": ["a", "b"]}]


def test_corpus_edit_is_the_resolving_verdict(memory_dir, repo):
    """A corpus-mutating verdict (here: dropping the contradicts declaration, the edit every
    supersede/scope/merge recipe ends with) drains the pair with NO ledger involved."""
    _conflicted_corpus(memory_dir)
    assert len(RV.unresolved_contradictions(memory_dir, repo_root=repo)) == 1
    _write(memory_dir, "new-api", "we stopped calling v1 — everything goes through the gateway")
    assert RV.unresolved_contradictions(memory_dir, repo_root=repo) == []


# ---- the ledger (mark-not-conflicting, the ONE corpus-preserving verdict) ------------------ #
def test_dismiss_drains_pair_and_persists_per_clone(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    res = RV.mark_not_conflicting("new-api", "old-api", repo)
    assert res["recorded"] is True and res["error"] is None
    assert res["pair"] == ["new-api", "old-api"]  # canonical order-free identity
    assert RV.unresolved_contradictions(memory_dir, repo_root=repo) == []
    # the ledger is a plain per-clone JSON under CLAUDE_PLUGIN_DATA, keyed by repo
    with open(res["ledger"], "r", encoding="utf-8") as fh:
        assert json.load(fh) == {"resolved": [["new-api", "old-api"]]}
    # the corpus itself is untouched — the edge is still declared in the file
    with open(os.path.join(memory_dir, "new-api.md"), "r", encoding="utf-8") as fh:
        assert "contradicts: [old-api]" in fh.read()


def test_dismiss_is_order_free_and_idempotent(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    RV.mark_not_conflicting("old-api", "new-api", repo)  # reversed order
    RV.mark_not_conflicting("new-api", "old-api", repo)  # repeat
    assert RV.read_resolved(repo) == {("new-api", "old-api")}
    assert RV.unresolved_contradictions(memory_dir, repo_root=repo) == []


def test_dismiss_scopes_to_its_own_pair(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    _write(memory_dir, "left", "left claim", ["right"])
    _write(memory_dir, "right", "right claim")
    RV.mark_not_conflicting("new-api", "old-api", repo)
    inbox = RV.unresolved_contradictions(memory_dir, repo_root=repo)
    assert inbox == [{"pair": ["left", "right"], "declared_by": ["left"]}]


def test_dismiss_without_plugin_data_is_a_loud_no(memory_dir, repo):
    """No durable per-clone home -> the verdict is REFUSED legibly (inv3), never silently
    forgotten; the pair stays in the inbox."""
    _conflicted_corpus(memory_dir)
    res = RV.mark_not_conflicting("new-api", "old-api", repo)
    assert res["recorded"] is False
    assert "CLAUDE_PLUGIN_DATA" in res["error"]
    assert len(RV.unresolved_contradictions(memory_dir, repo_root=repo)) == 1


def test_ledger_is_keyed_per_clone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    clone_a = str(tmp_path / "clone-a")
    clone_b = str(tmp_path / "clone-b")
    os.makedirs(clone_a), os.makedirs(clone_b)
    assert RV.ledger_path(clone_a) != RV.ledger_path(clone_b)
    RV.mark_not_conflicting("x", "y", clone_a)
    assert RV.read_resolved(clone_a) == {("x", "y")}
    assert RV.read_resolved(clone_b) == set()  # a dismissal never leaks across clones


def test_dismiss_rejects_self_and_empty():
    assert RV.mark_not_conflicting("a", "a", ".")["error"]
    assert RV.mark_not_conflicting("", "b", ".")["error"]


# ---- nothing auto-resolves: the module has no corpus-write path (structural pin) ----------- #
def test_corpus_writers_are_confined_to_the_verdict_engine():
    """inv4, re-pinned for INV-4 (scope ratified 2026-07-16): the LISTING half of this
    module stays read-only — corpus-mutating primitives may be called ONLY from the
    per-item verdict engine (``apply_resolve_verdict`` + its ``_drop_declarations``
    chain helper), and only the SHARED ones (semantic_reverify, remove_typed_relation,
    restore_file_bytes) — never a raw write, never a bulk shape, never from the inbox/
    describe/proposal readers."""
    _WRITERS = {
        "write_memory", "semantic_reverify", "add_typed_relation",
        "remove_typed_relation", "set_invalid_after", "restore_file_bytes",
        "write_text_atomic", "write_json_atomic", "open",
    }
    _ENGINE = {"apply_resolve_verdict", "_drop_declarations"}
    # mark_not_conflicting / _log_verdict write the per-clone LEDGER (not the corpus) —
    # each open("w") is allowlisted in the INV-2 write-discipline lint. The TMB-1
    # evidence-card readers (_read_ledger_doc + pair_evidence's legs) open() READ-ONLY.
    _LEDGER = {
        "mark_not_conflicting", "read_resolved", "_description_of", "pair_edge_state",
        "_read_ledger_doc", "read_verdict_log", "_log_verdict",
    }

    tree = ast.parse(inspect.getsource(RV))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                if isinstance(fn, ast.Attribute):
                    called.add(fn.attr)
                elif isinstance(fn, ast.Name):
                    called.add(fn.id)
        hits = called & _WRITERS
        if hits and node.name not in _ENGINE:
            # readers may open() corpus files READ-ONLY; flag only real writers there
            if hits == {"open"} and node.name in _LEDGER:
                continue
            offenders.append((node.name, sorted(hits)))
    assert not offenders, (
        f"corpus-writing primitives escaped the verdict engine: {offenders} — the "
        "inbox/listing half of resolve_view must stay read-only (INV-4)"
    )
    # And the engine itself uses only the SHARED primitives — never a raw write.
    engine_src = "".join(
        inspect.getsource(getattr(RV, name)) for name in sorted(_ENGINE)
    )
    assert "write_text_atomic" not in engine_src and 'open(' not in engine_src.replace(
        'open(os.path.join(memory_dir, f"{side}.md"), "r"', ""
    ).replace('open(path, "r"', "")


# ---- the SessionStart producer ------------------------------------------------------------- #
def test_producer_lists_pairs_and_routes_to_resolve(memory_dir, repo):
    _conflicted_corpus(memory_dir)
    out = S.contradiction_inbox_producer(memory_dir, repo)
    assert out is not None
    assert out.startswith("⚖ Contradiction inbox — 1 unresolved")
    assert "/hippo:resolve" in out and "nothing auto-picks a winner" in out
    assert "new-api ⇄ old-api" in out


def test_producer_silent_when_inbox_empty(memory_dir, repo):
    _write(memory_dir, "solo", "no conflicts here")
    assert S.contradiction_inbox_producer(memory_dir, repo) is None


def test_producer_silent_after_dismissal(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    RV.mark_not_conflicting("new-api", "old-api", repo)
    assert S.contradiction_inbox_producer(memory_dir, repo) is None


def test_producer_counts_radar_pairs_but_does_not_reprint_them(memory_dir, repo, monkeypatch):
    """T2 boundary: a pair the rules_conflict producer already surfaced this session is
    counted in the header but not re-printed (no double nag)."""
    import memory.rules_plane as RP

    _conflicted_corpus(memory_dir)
    _write(memory_dir, "left", "left claim", ["right"])
    _write(memory_dir, "right", "right claim")
    monkeypatch.setattr(
        RP,
        "conflict_radar",
        lambda md, rr, **kw: {
            "authority_gaps": [],
            "edge_conflicts": [
                {"name": "old-api", "relation": "contradicts", "by": "new-api", "cited_by": ["CLAUDE.md"]}
            ],
            "gate_met": True,
            "distinct_sessions": 5,
        },
    )
    out = S.contradiction_inbox_producer(memory_dir, repo)
    assert out is not None
    assert "2 unresolved contradiction pair(s)" in out  # counted...
    assert "new-api ⇄ old-api" not in out  # ...but not re-printed
    assert "left ⇄ right" in out
    assert "1 pair(s) already shown by the rule↔memory conflict radar above" in out


def test_producer_wired_into_producers():
    assert any(label == "contradiction_inbox" for label, _fn in S.PRODUCERS)


# ---- the CLI (/hippo:resolve's surface) ---------------------------------------------------- #
def test_cli_list_names_pairs_and_descriptions(memory_dir, repo, capsys):
    _conflicted_corpus(memory_dir)
    rc = RV.main(["--list", "--memory-dir", memory_dir, "--repo-root", repo])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 unresolved contradiction pair(s)" in out
    assert "new-api ⇄ old-api" in out and "(declared by: new-api)" in out
    assert "we call the v1 endpoint directly" in out  # both sides' descriptions shown
    assert "everything goes through the gateway" in out


def test_cli_dismiss_then_list_is_empty(memory_dir, repo, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    rc = RV.main(["--dismiss", "new-api", "old-api", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert "recorded : new-api ⇄ old-api" in capsys.readouterr().out
    RV.main(["--list", "--memory-dir", memory_dir, "--repo-root", repo])
    assert "inbox is empty" in capsys.readouterr().out


def test_cli_dismiss_without_plugin_data_prints_error(memory_dir, repo, capsys):
    _conflicted_corpus(memory_dir)
    rc = RV.main(["--dismiss", "new-api", "old-api", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert "error    : CLAUDE_PLUGIN_DATA is unset" in capsys.readouterr().out


def test_bogus_dirs_never_raise(tmp_path):
    bogus = str(tmp_path / "nope")
    assert RV.unresolved_contradictions(bogus, repo_root=bogus) == []
    assert S.contradiction_inbox_producer(bogus, bogus) is None
    assert RV.main(["--list", "--memory-dir", bogus, "--repo-root", bogus]) == 0

# ---- TMB-1: the evidence card (age + git-freshness + drift + usage + prefill) -------------- #
def test_evidence_card_age_and_git_newer_side(memory_dir, repo):
    """Age = commits since the declaration's introduction (git-mined, zero persisted
    state); the git-newer side is whichever .md history touched last."""
    _write(memory_dir, "old-api", "we call the v1 endpoint directly")
    git_commit(repo, "old-api born", 1_700_000_000)
    _write(memory_dir, "new-api", "we stopped calling v1", ["old-api"])
    git_commit(repo, "new-api declares the conflict", 1_700_100_000)
    with open(os.path.join(repo, "filler.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    git_commit(repo, "unrelated work since", 1_700_200_000)

    card = RV.pair_evidence("new-api", "old-api", memory_dir, repo)
    assert card["age_commits"] == 1  # one commit landed since the declaring commit
    assert card["newer"] == "new-api"  # committed at 1_700_100_000 vs old-api's 1_700_000_000

    listing = RV.describe(memory_dir, repo_root=repo)
    assert "age: born 1 commit(s) ago" in listing
    assert "git-newer: new-api" in listing


def test_evidence_card_age_unknown_when_uncommitted(memory_dir, repo):
    """Legible fallback (the AC's shallow/rewritten-history arm): no history -> unknown,
    never a guess."""
    _conflicted_corpus(memory_dir)  # written, never committed
    card = RV.pair_evidence("new-api", "old-api", memory_dir, repo)
    assert card["age_commits"] is None
    assert card["newer"] is None
    assert "age: unknown" in RV.describe(memory_dir, repo_root=repo)


def test_evidence_card_drift_reads_stale_cache_with_zero_git(memory_dir, repo, monkeypatch):
    """The drift leg reads stale.json (the LIF-6 cache) — never a fresh git scan."""
    import memory.build_index as B
    import memory.provenance as P
    from memory.staleness import write_stale_cache

    _conflicted_corpus(memory_dir)
    idx = B.default_index_dir(memory_dir)
    write_stale_cache(idx, [
        {"name": "old-api", "changed_paths": ["app.py", "lib.py"], "recency": 5, "source_commit": "d" * 40},
    ])
    monkeypatch.setattr(P, "run_git", lambda *a, **k: (_ for _ in ()).throw(AssertionError("git ran")))
    card = RV.pair_evidence("new-api", "old-api", memory_dir, None)  # no repo_root: git legs skipped
    assert card["drift"] == {"old-api": 2}


def test_evidence_card_usage_floor_boundary(memory_dir, tmp_path):
    """Usage asymmetry is withheld below the stated session floor and shown at it —
    unit-tested exactly at the boundary (4 vs 5 recorded sessions)."""
    td = str(tmp_path / "telemetry")
    os.makedirs(td)

    def _write_aggregates(session_count):
        with open(os.path.join(td, "usage_aggregates.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "version": 1,
                "sessions": {"count": session_count, "first_ts": 1.0, "last_session_id": "s"},
                "memories": {
                    "new-api": {"first_ts": 1.0, "last_ts": 9.0, "sessions": 4, "last_session_id": "s"},
                    "old-api": {"first_ts": 1.0, "last_ts": 2.0, "sessions": 1, "last_session_id": "s"},
                },
            }, fh)

    _conflicted_corpus(memory_dir)
    _write_aggregates(RV._EVIDENCE_USAGE_MIN_SESSIONS - 1)
    counts, confident = RV._usage_evidence("new-api", "old-api", td)
    assert confident is False and counts == {"new-api": 4, "old-api": 1}
    assert "usage: withheld" in "\n".join(
        RV.render_pair_evidence("new-api", "old-api",
                                RV.pair_evidence("new-api", "old-api", memory_dir, None, telemetry_dir=td))
    )
    _write_aggregates(RV._EVIDENCE_USAGE_MIN_SESSIONS)  # exactly at the floor -> shown
    counts, confident = RV._usage_evidence("new-api", "old-api", td)
    assert confident is True
    rendered = "\n".join(
        RV.render_pair_evidence("new-api", "old-api",
                                RV.pair_evidence("new-api", "old-api", memory_dir, None, telemetry_dir=td))
    )
    assert "new-api 4 session(s)" in rendered and "old-api 1 session(s)" in rendered


def test_suggestion_rule_is_drift_asymmetry_gated_and_taxonomy_pure(memory_dir):
    """keep_one only when exactly one side cites drifted code and freshness agrees;
    everything else abstains. Suggestions use ONLY the four skill verdict names +
    abstain — no second taxonomy exists to grep."""
    # drift on one side, freshness unknown -> keep_one, winner = the clean side
    s, w, _r = RV._suggest_verdict("a", "b", {"a": 2}, None)
    assert (s, w) == ("keep_one", "b")
    # freshness agrees (the clean side is newer) -> same suggestion
    s, w, _r = RV._suggest_verdict("a", "b", {"a": 2}, "b")
    assert (s, w) == ("keep_one", "b")
    # signals disagree (the drifted side is the fresher edit) -> abstain
    s, w, _r = RV._suggest_verdict("a", "b", {"a": 2}, "a")
    assert (s, w) == (RV._ABSTAIN, None)
    # both stale / neither stale -> abstain (content judgments stay human)
    assert RV._suggest_verdict("a", "b", {"a": 1, "b": 1}, "a")[0] == RV._ABSTAIN
    assert RV._suggest_verdict("a", "b", {}, "a")[0] == RV._ABSTAIN
    # the ONE taxonomy: the module's names are exactly the resolve skill's four
    assert RV._VERDICT_NAMES == ("keep_one", "scope_both", "merge", "not_conflicting")
    assert RV._ABSTAIN == "abstain"


def test_prefill_recorded_on_each_of_the_four_verdict_paths(memory_dir, repo, tmp_path, monkeypatch):
    """AC: each verdict path gains ONE additive field capturing prefill-vs-choice — on the
    existing per-clone ledger (no sibling ledger), with _RECONSOLIDATION_OUTCOMES untouched."""
    import memory.telemetry as T

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "telemetry"))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # the demote chain refreshes the index
    outcomes_before = set(T._RECONSOLIDATION_OUTCOMES)

    # keep_one
    _write(memory_dir, "a1", "claim A", ["b1"])
    _write(memory_dir, "b1", "claim B")
    r = RV.apply_resolve_verdict(memory_dir, repo, "keep_one", winner="a1", loser="b1", prefill="keep_one")
    assert r["applied"] and r["prefill"] == "keep_one"
    # merge
    _write(memory_dir, "a2", "claim A2", ["b2"])
    _write(memory_dir, "b2", "claim B2")
    r = RV.apply_resolve_verdict(memory_dir, repo, "merge", winner="a2", loser="b2", prefill="abstain")
    assert r["applied"] and r["prefill"] == "abstain"
    # scope_both
    _write(memory_dir, "a3", "claim A3", ["b3"])
    _write(memory_dir, "b3", "claim B3")
    r = RV.apply_resolve_verdict(memory_dir, repo, "scope_both", a="a3", b="b3", prefill="keep_one")
    assert r["applied"] and r["prefill"] == "keep_one"
    # not_conflicting
    _write(memory_dir, "a4", "claim A4", ["b4"])
    _write(memory_dir, "b4", "claim B4")
    r = RV.apply_resolve_verdict(memory_dir, repo, "not_conflicting", a="a4", b="b4")  # no card seen
    assert r["applied"] and r["prefill"] is None

    log = RV.read_verdict_log(repo)
    assert [(row["verdict"], row["prefill"]) for row in log] == [
        ("keep_one", "keep_one"), ("merge", "abstain"),
        ("scope_both", "keep_one"), ("not_conflicting", None),
    ]
    # dismissals AND verdicts coexist in the one ledger document (no sibling file)
    assert RV.read_resolved(repo) == {("a4", "b4")}
    # a junk prefill records as None (honest over guessed), never as a fifth name
    _write(memory_dir, "a5", "claim A5", ["b5"])
    _write(memory_dir, "b5", "claim B5")
    r = RV.apply_resolve_verdict(memory_dir, repo, "scope_both", a="a5", b="b5", prefill="banana")
    assert r["applied"] and r["prefill"] is None
    # the reconsolidation outcome vocabulary did not move (TMB-1's binding constraint)
    assert set(T._RECONSOLIDATION_OUTCOMES) == outcomes_before


def test_cli_dismiss_records_prefill(memory_dir, repo, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    rc = RV.main([
        "--dismiss", "new-api", "old-api", "--prefill", "abstain",
        "--memory-dir", memory_dir, "--repo-root", repo,
    ])
    assert rc == 0 and "recorded :" in capsys.readouterr().out
    assert RV.read_verdict_log(repo) == [
        {"pair": ["new-api", "old-api"], "verdict": "not_conflicting", "prefill": "abstain"}
    ]


def test_producer_runs_zero_git_and_renders_no_card(memory_dir, repo, monkeypatch):
    """inv6, AC-pinned: the SessionStart contradiction producer never reaches TMB-1's
    git mining — zero git subprocesses during producer execution, and the card lines
    stay confined to describe()/--list."""
    import memory.provenance as P

    _conflicted_corpus(memory_dir)
    calls = []
    monkeypatch.setattr(P, "run_git", lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(
        P, "git_last_commit_with_time", lambda *a, **k: calls.append(a) or (None, None)
    )
    out = S.contradiction_inbox_producer(memory_dir, repo)
    assert out and "new-api ⇄ old-api" in out
    assert calls == []  # the producer mined nothing
    assert "evidence:" not in out and "suggested:" not in out  # card is cold-path only


def test_describe_card_present_on_proposed_and_declared_pairs(memory_dir, repo):
    """The card renders under every inbox item; a proposal-only pair gets the honest
    unknown-age arm (nothing is declared to mine)."""
    _conflicted_corpus(memory_dir)
    listing = RV.describe(memory_dir, repo_root=repo)
    assert listing.count("suggested:") == 1
    assert "never auto-applied" in listing
