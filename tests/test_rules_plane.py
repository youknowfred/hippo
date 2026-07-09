"""RUL-1: the rule↔memory conflict radar over the governance plane (rules_plane).

The governance plane (CLAUDE.md / AGENTS.md / .claude/rules|agents|skills) is always-loaded
and unranked; the radar joins its backtick memory citations against the corpus: the
typed-edge leg (cited but superseded/contradicted — authored facts, fires always) and the
strength leg (cited but never recalled — telemetry, gated on the 5-session soak bar).
Read-only; findings route to a per-item decision, nothing auto-resolves.
"""

from __future__ import annotations

import os

import memory.doctor as D
import memory.rules_plane as RP
import memory.session_start as S
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

from .conftest import write_file


def _mem(memory_dir, name, description="a memory", extra_fm=""):
    """Write one corpus memory file (frontmatter convention; extra_fm is raw YAML lines)."""
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, f"{name}.md")
    extra = (extra_fm.rstrip("\n") + "\n") if extra_fm else ""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {name}\ndescription: {description}\n{extra}"
            "metadata:\n  type: project\n---\nbody\n"
        )
    return path


def _recall(td, names, sid):
    """Log ONE successful recall of ``names`` under session ``sid`` (strength numerator)."""
    T.log_recall_event(
        [
            {"name": n, "backend": "dense+bm25", "score": 0.9, "rank": i + 1}
            for i, n in enumerate(names)
        ],
        query="q",
        k=6,
        latency_ms=1.0,
        telemetry_dir=td,
        session_id=sid,
    )


def _mature(td, names=("workhorse",), sessions=5):
    """Meet the soak gate: ``sessions`` distinct sessions each recalling ``names``."""
    for i in range(sessions):
        _recall(td, list(names), sid=f"s{i}")


# ---- gov_files / gov_citations (the canonical governance enumeration) --------------------- #
def test_gov_files_covers_the_audit_glob_set(repo, memory_dir):
    write_file(repo, "CLAUDE.md", "root rules")
    write_file(repo, "AGENTS.md", "cross-tool rules")
    write_file(repo, ".claude/rules/20-style.md", "style rule")
    write_file(repo, ".claude/agents/reviewer.md", "an agent")
    write_file(repo, ".claude/skills/audit/SKILL.md", "a skill")
    write_file(repo, ".claude/rules/not-a-rule.txt", "ignored — not markdown")
    rels = [os.path.relpath(p, repo) for p in RP.gov_files(repo)]
    assert rels == [
        "CLAUDE.md",
        "AGENTS.md",
        ".claude/rules/20-style.md",
        ".claude/agents/reviewer.md",
        os.path.join(".claude", "skills", "audit", "SKILL.md"),
    ]


def test_gov_citations_only_resolve_real_corpus_stems(repo, memory_dir):
    _mem(memory_dir, "deploy_runbook")
    write_file(repo, "CLAUDE.md", "See `deploy_runbook` and `not_a_memory` and `README.md`.")
    cited = RP.gov_citations(repo, {"deploy_runbook"})
    assert cited == {"deploy_runbook": ["CLAUDE.md"]}  # precision: unresolvable tokens drop


def test_gov_citations_md_suffix_optional_and_deduped(repo, memory_dir):
    write_file(repo, "CLAUDE.md", "Both `note_a.md` and `note_a` in one file.")
    write_file(repo, ".claude/rules/10-x.md", "Also `note_a`.")
    cited = RP.gov_citations(repo, {"note_a"})
    assert cited == {"note_a": ["CLAUDE.md", os.path.join(".claude", "rules", "10-x.md")]}


# ---- the typed-edge leg (fires regardless of soak maturity) -------------------------------- #
def test_edge_conflict_names_rule_file_and_both_memories(repo, memory_dir):
    _mem(memory_dir, "old_way")
    _mem(memory_dir, "new_way", extra_fm="supersedes: old_way")
    write_file(repo, "CLAUDE.md", "Always follow `old_way`.")
    radar = RP.conflict_radar(memory_dir, repo)
    assert radar["edge_conflicts"] == [
        {
            "name": "old_way",
            "relation": "supersedes",
            "by": "new_way",
            "cited_by": ["CLAUDE.md"],
        }
    ]
    assert radar["authority_gaps"] == []  # gate not met — strength leg stays silent


def test_contradicts_edge_is_a_conflict_too(repo, memory_dir):
    _mem(memory_dir, "base")
    _mem(memory_dir, "rival", extra_fm="contradicts: base")
    write_file(repo, ".claude/rules/30-arch.md", "Per `base`, keep it monolithic.")
    radar = RP.conflict_radar(memory_dir, repo)
    assert [(c["name"], c["relation"], c["by"]) for c in radar["edge_conflicts"]] == [
        ("base", "contradicts", "rival")
    ]


def test_uncited_superseded_memory_is_not_a_conflict(repo, memory_dir):
    _mem(memory_dir, "old_way")
    _mem(memory_dir, "new_way", extra_fm="supersedes: old_way")
    write_file(repo, "CLAUDE.md", "No memory citations here.")
    radar = RP.conflict_radar(memory_dir, repo)
    assert radar["edge_conflicts"] == []  # the radar is about GOVERNANCE citations only


# ---- the strength leg (soak-gated) --------------------------------------------------------- #
def test_authority_gap_fires_once_soak_gate_met(repo, memory_dir):
    _mem(memory_dir, "cited_gap")
    _mem(memory_dir, "workhorse")
    write_file(repo, "CLAUDE.md", "Consult `cited_gap` before deploys.")
    _mature(default_telemetry_dir(memory_dir))  # 5 sessions, none recall cited_gap
    radar = RP.conflict_radar(memory_dir, repo)
    assert radar["gate_met"] is True
    assert radar["authority_gaps"] == [
        {"name": "cited_gap", "strength": 0.0, "cited_by": ["CLAUDE.md"]}
    ]


def test_strength_leg_silent_before_soak_gate(repo, memory_dir):
    _mem(memory_dir, "cited_gap")
    write_file(repo, "CLAUDE.md", "Consult `cited_gap`.")
    _recall(default_telemetry_dir(memory_dir), ["workhorse"], sid="s0")  # 1 session < gate
    radar = RP.conflict_radar(memory_dir, repo)
    assert radar["gate_met"] is False
    assert radar["authority_gaps"] == []  # fresh clones are never nagged about strength


def test_strongly_recalled_cited_memory_is_not_a_gap(repo, memory_dir):
    _mem(memory_dir, "workhorse")
    write_file(repo, "CLAUDE.md", "Consult `workhorse`.")
    _mature(default_telemetry_dir(memory_dir), names=("workhorse",))
    radar = RP.conflict_radar(memory_dir, repo)
    assert radar["gate_met"] is True
    assert radar["authority_gaps"] == []


# ---- the SessionStart producer -------------------------------------------------------------- #
def test_producer_reports_edge_conflict_loud_and_specific(repo, memory_dir):
    _mem(memory_dir, "old_way")
    _mem(memory_dir, "new_way", extra_fm="supersedes: old_way")
    write_file(repo, "CLAUDE.md", "Always follow `old_way`.")
    out = S.rules_conflict_producer(memory_dir, repo)
    assert out is not None
    assert out.startswith("⚖ Rule↔memory conflicts")
    assert "CLAUDE.md cites `old_way` but `new_way` supersedes it" in out
    assert "/hippo:consolidate" in out  # routes to a per-item decision


def test_producer_reports_authority_gap_with_strength(repo, memory_dir):
    _mem(memory_dir, "cited_gap")
    write_file(repo, "CLAUDE.md", "Consult `cited_gap`.")
    _mature(default_telemetry_dir(memory_dir))
    out = S.rules_conflict_producer(memory_dir, repo)
    assert out is not None
    assert "CLAUDE.md cites `cited_gap` but no session recalls it (strength 0.00)" in out


def test_producer_silent_when_planes_agree(repo, memory_dir):
    _mem(memory_dir, "workhorse")
    write_file(repo, "CLAUDE.md", "Consult `workhorse`.")
    _mature(default_telemetry_dir(memory_dir), names=("workhorse",))
    assert S.rules_conflict_producer(memory_dir, repo) is None


def test_producer_caps_lines_and_counts_overflow(repo, memory_dir):
    for i in range(6):
        _mem(memory_dir, f"old_{i}")
        _mem(memory_dir, f"new_{i}", extra_fm=f"supersedes: old_{i}")
    write_file(repo, "CLAUDE.md", " ".join(f"`old_{i}`" for i in range(6)))
    out = S.rules_conflict_producer(memory_dir, repo)
    assert out is not None
    bullets = [ln for ln in out.splitlines() if ln.startswith("  • ")]
    assert len(bullets) == S._MAX_RULES_CONFLICT_LINES
    assert "… and 2 more" in out


# ---- the doctor check (always-available surface) -------------------------------------------- #
def test_doctor_warns_and_names_the_top_conflict(repo, memory_dir):
    _mem(memory_dir, "old_way")
    _mem(memory_dir, "new_way", extra_fm="supersedes: old_way")
    write_file(repo, "CLAUDE.md", "Always follow `old_way`.")
    r = D.check_rules_conflicts(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"
    assert "1 typed-edge conflict(s)" in r["message"]
    assert "CLAUDE.md cites `old_way` but `new_way` supersedes it" in r["message"]


def test_doctor_ok_names_pending_soak_gate(repo, memory_dir):
    _mem(memory_dir, "cited_gap")
    write_file(repo, "CLAUDE.md", "Consult `cited_gap`.")
    r = D.check_rules_conflicts(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "soak gate" in r["message"]  # the silent strength leg is made legible, not hidden


def test_doctor_ok_when_planes_agree_and_gate_met(repo, memory_dir):
    _mem(memory_dir, "workhorse")
    write_file(repo, "CLAUDE.md", "Consult `workhorse`.")
    _mature(default_telemetry_dir(memory_dir), names=("workhorse",))
    r = D.check_rules_conflicts(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "agree" in r["message"]


# ---- nothing auto-resolves (inv4) ----------------------------------------------------------- #
def test_radar_and_surfaces_never_write(repo, memory_dir):
    _mem(memory_dir, "old_way")
    _mem(memory_dir, "new_way", extra_fm="supersedes: old_way")
    claude_md = write_file(repo, "CLAUDE.md", "Always follow `old_way`.")
    before = {
        p: open(p, encoding="utf-8").read()
        for p in [
            claude_md,
            os.path.join(memory_dir, "old_way.md"),
            os.path.join(memory_dir, "new_way.md"),
        ]
    }
    RP.conflict_radar(memory_dir, repo)
    S.rules_conflict_producer(memory_dir, repo)
    D.check_rules_conflicts(D.DoctorContext(memory_dir, repo))
    after = {p: open(p, encoding="utf-8").read() for p in before}
    assert after == before  # read-only: findings PROPOSE, a human decides per item


# ---- wiring + robustness -------------------------------------------------------------------- #
def test_wired_into_producers_and_checks():
    assert any(label == "rules_conflict" for label, _fn in S.PRODUCERS)
    assert "rules_conflicts" in [label for label, _ in D.CHECKS]


def test_bogus_dirs_never_raise(tmp_path):
    bogus = str(tmp_path / "nope")
    assert RP.gov_files(bogus) == []
    assert RP.gov_citations(bogus, {"x"}) == {}
    radar = RP.conflict_radar(bogus, bogus)
    assert radar["authority_gaps"] == [] and radar["edge_conflicts"] == []
    assert S.rules_conflict_producer(bogus, bogus) is None


# =============================================================================================
# RUL-2: rules_rot — citation rot + dead paths: globs over the rules plane itself
# =============================================================================================
from .conftest import git_commit  # noqa: E402


def test_code_ref_rot_flags_missing_path_after_rename(repo, memory_dir):
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add dep", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Keep `src/dep.py` and `src/dep.py:12` in sync.")
    import subprocess

    subprocess.run(["git", "mv", "src/dep.py", "src/dep_moved.py"], cwd=repo, check=True)
    git_commit(repo, "mv dep", 1_700_000_100)
    rot = RP.rules_rot(repo)
    assert rot["code_ref_rot"] == [
        {"file": "CLAUDE.md", "ref": "src/dep.py", "kind": "path"},
        {"file": "CLAUDE.md", "ref": "src/dep.py:12", "kind": "path"},
    ]


def test_code_ref_alive_path_and_bare_basename_are_silent(repo, memory_dir):
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add dep", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "See `src/dep.py` and `dep.py` (bare basename).")
    assert RP.rules_rot(repo)["code_ref_rot"] == []


def test_symbol_ref_rot_flags_vanished_symbol_in_resolved_module(repo, memory_dir):
    write_file(repo, "plugin/util.py", "def kept(a):\n    return a\n\nGONE_CONST = 1\n")
    git_commit(repo, "add util", 1_700_000_000)
    write_file(
        repo,
        ".claude/rules/10-api.md",
        "Call `util.kept` never `util.vanished`; `util.GONE_CONST` is fine.",
    )
    rot = RP.rules_rot(repo)
    assert rot["code_ref_rot"] == [
        {"file": os.path.join(".claude", "rules", "10-api.md"), "ref": "util.vanished", "kind": "symbol"}
    ]


def test_symbol_ref_unresolvable_or_ambiguous_module_is_silence(repo, memory_dir):
    write_file(repo, "a/dup.py", "def f():\n    pass\n")
    write_file(repo, "b/dup.py", "def g():\n    pass\n")
    git_commit(repo, "two dups", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "See `dup.missing` and `nosuchmodule.thing`.")
    assert RP.rules_rot(repo)["code_ref_rot"] == []  # under-flag beats cry-wolf


def test_dead_paths_glob_flagged_with_exact_glob(repo, memory_dir):
    write_file(repo, "src/real.py", "x = 1\n")
    git_commit(repo, "add real", 1_700_000_000)
    write_file(
        repo,
        ".claude/rules/20-scoped.md",
        '---\npaths:\n  - "src/legacy/**"\n---\nLegacy tree rules.\n',
    )
    rot = RP.rules_rot(repo)
    assert rot["dead_path_globs"] == [
        {"file": os.path.join(".claude", "rules", "20-scoped.md"), "glob": "src/legacy/**"}
    ]


def test_live_paths_glob_and_untracked_files_count_as_alive(repo, memory_dir):
    write_file(repo, "src/real.py", "x = 1\n")
    git_commit(repo, "add real", 1_700_000_000)
    write_file(repo, "docs/notes.md", "untracked but not ignored\n")  # NOT committed
    write_file(
        repo,
        ".claude/rules/30-scoped.md",
        '---\npaths:\n  - "src/**/*.py"\n  - "docs/**"\n---\nScoped rules.\n',
    )
    assert RP.rules_rot(repo)["dead_path_globs"] == []


def test_paths_glob_brace_expansion_matches(repo, memory_dir):
    write_file(repo, "web/app.tsx", "export {}\n")
    git_commit(repo, "add tsx", 1_700_000_000)
    write_file(
        repo,
        ".claude/rules/40-web.md",
        '---\npaths:\n  - "web/**/*.{ts,tsx}"\n---\nWeb rules.\n',
    )
    assert RP.rules_rot(repo)["dead_path_globs"] == []


def test_rule_without_paths_frontmatter_is_not_a_glob_finding(repo, memory_dir):
    write_file(repo, "src/real.py", "x = 1\n")
    git_commit(repo, "add real", 1_700_000_000)
    write_file(repo, ".claude/rules/50-unscoped.md", "Always-loaded rule, no frontmatter.\n")
    assert RP.rules_rot(repo)["dead_path_globs"] == []


# ---- RUL-2 surfaces -------------------------------------------------------------------------- #
def test_rot_producer_names_file_and_exact_reference(repo, memory_dir):
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add dep", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Keep `src/gone.py` in mind.")
    write_file(
        repo,
        ".claude/rules/20-scoped.md",
        '---\npaths:\n  - "src/legacy/**"\n---\nLegacy.\n',
    )
    out = S.rules_rot_producer(memory_dir, repo)
    assert out is not None
    assert out.startswith("🧭 Rules-plane rot")
    assert "CLAUDE.md references `src/gone.py` — path no longer in the repo" in out
    assert "scopes paths: 'src/legacy/**' — matches nothing" in out


def test_rot_producer_silent_on_healthy_plane(repo, memory_dir):
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add dep", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "See `src/dep.py`.")
    assert S.rules_rot_producer(memory_dir, repo) is None


def test_rot_doctor_warns_with_counts_and_top_finding(repo, memory_dir):
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add dep", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Keep `src/gone.py` in mind.")
    r = D.check_rules_plane_rot(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"
    assert "1 rotten code reference(s)" in r["message"]
    assert "CLAUDE.md references `src/gone.py`" in r["message"]


def test_rot_doctor_ok_when_clean(repo, memory_dir):
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add dep", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "See `src/dep.py`.")
    r = D.check_rules_plane_rot(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"


def test_rot_never_writes_and_never_raises(repo, memory_dir, tmp_path):
    claude_md = write_file(repo, "CLAUDE.md", "Keep `src/gone.py` in mind.")
    write_file(repo, "src/dep.py", "x = 1\n")
    git_commit(repo, "add", 1_700_000_000)
    before = open(claude_md, encoding="utf-8").read()
    RP.rules_rot(repo)
    S.rules_rot_producer(memory_dir, repo)
    assert open(claude_md, encoding="utf-8").read() == before  # names the fix, never makes it
    bogus = str(tmp_path / "nope")
    assert RP.rules_rot(bogus) == {"code_ref_rot": [], "dead_path_globs": []}
    assert S.rules_rot_producer(bogus, bogus) is None


def test_rot_wired_into_producers_and_checks():
    assert any(label == "rules_rot" for label, _fn in S.PRODUCERS)
    assert "rules_plane_rot" in [label for label, _ in D.CHECKS]


# =============================================================================================
# RUL-3: rule_dup_candidates — write-time dedup against the rules plane (preventive)
# =============================================================================================
_UV_RULE = (
    "# Python\n\n"
    "Always use uv for python dependency management, never pip install directly "
    "into the environment.\n\n"
    "Unrelated second block about commit messages and changelog hygiene.\n"
)


def test_restated_rule_flagged_with_file_score_preview(repo, memory_dir):
    write_file(repo, "CLAUDE.md", _UV_RULE)
    cands = RP.rule_dup_candidates(
        "use uv for python dependency management, never pip install", "", repo
    )
    assert len(cands) == 1
    assert cands[0]["file"] == "CLAUDE.md"
    assert cands[0]["score"] >= RP.RULE_DUP_CONTAINMENT
    assert "uv for python dependency management" in cands[0]["preview"]


def test_distinct_draft_not_flagged(repo, memory_dir):
    write_file(repo, "CLAUDE.md", _UV_RULE)
    assert (
        RP.rule_dup_candidates(
            "watering schedule for indoor houseplants during winter months", "", repo
        )
        == []
    )


def test_short_draft_is_never_judged(repo, memory_dir):
    write_file(repo, "CLAUDE.md", _UV_RULE)
    # 100%-contained but under the 5-content-token bar: silence, not a cheap match.
    assert RP.rule_dup_candidates("uv python", "", repo) == []


def test_candidates_best_first_and_capped(repo, memory_dir):
    for i in range(5):
        write_file(repo, f".claude/rules/{i}0-uv.md", _UV_RULE)
    cands = RP.rule_dup_candidates(
        "use uv for python dependency management, never pip install", "", repo
    )
    assert len(cands) == RP._RULE_DUP_MAX_CANDIDATES
    assert [c["score"] for c in cands] == sorted((c["score"] for c in cands), reverse=True)


def test_gov_blocks_strip_frontmatter():
    text = '---\npaths:\n  - "src/**"\n---\nBlock one text.\n\nBlock two text.\n'
    blocks = RP._gov_blocks(text)
    assert blocks == ["Block one text.", "Block two text."]  # paths: scoping is not content


def test_rule_dup_never_raises(tmp_path):
    assert RP.rule_dup_candidates("anything at all here now", "", str(tmp_path / "nope")) == []


# =============================================================================================
# RUL-4: rules as an on-demand recall SOURCE — labelled pointer, no import, no displacement
# =============================================================================================
import memory.build_index as B  # noqa: E402
import memory.recall as R  # noqa: E402
import memory.recall_view as RV  # noqa: E402

_GOV_MD = (
    "# Deploys\n\n"
    "Rollback procedure: run scripts/rollback.sh, verify the healthcheck endpoint, "
    "then re-enable the deploy pipeline traffic gate.\n\n"
    "# Style\n\n"
    "Prefer short functions and explicit names over clever abstractions everywhere.\n"
)


def _seed_recall_corpus(md, idx, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    os.makedirs(md, exist_ok=True)
    _mem(md, "billing_tiers", "billing plan tiers offered after onboarding completes")
    _mem(md, "signup_validation", "signup form validation rules for the onboarding flow")
    _mem(md, "rollback_incident", "the march incident where a rollback failed under load")
    B.build_index(md, idx)


def test_strong_query_surfaces_labelled_rule_pointer(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / "idx")
    _seed_recall_corpus(memory_dir, idx, monkeypatch)
    write_file(repo, "CLAUDE.md", _GOV_MD)
    RP.refresh_rules_cache(repo, idx)

    hits = R.recall(
        "rollback procedure healthcheck deploy pipeline", k=6,
        memory_dir=memory_dir, index_dir=idx, repo_root=repo,
    )
    rules = [h for h in hits if h["corpus"] == "rule"]
    assert rules, "expected the Deploys section to surface as a rule pointer"
    top = rules[0]
    assert top["name"] == "Deploys" and top["file"] == "CLAUDE.md" and top["via"] == "rules"
    assert "rollback" in top["description"]
    rendered = R.format_results(hits)
    assert "(rule)" in rendered  # the label, at the display layer


def test_rule_pointers_append_and_never_displace_organic_hits(repo, memory_dir, tmp_path, monkeypatch):
    """AC: recall only ADDS a pointer — the organic result set is byte-identical with and
    without the rules cache, and every rule hit sits AFTER every organic hit."""
    idx = str(tmp_path / "idx")
    _seed_recall_corpus(memory_dir, idx, monkeypatch)
    write_file(repo, "CLAUDE.md", _GOV_MD)
    query = "rollback procedure healthcheck deploy pipeline"

    before = R.recall(query, k=6, memory_dir=memory_dir, index_dir=idx, repo_root=repo)
    assert all(h["corpus"] != "rule" for h in before)  # no cache yet -> no rule hits

    RP.refresh_rules_cache(repo, idx)
    after = R.recall(query, k=6, memory_dir=memory_dir, index_dir=idx, repo_root=repo)
    organic = [h for h in after if h["corpus"] != "rule"]
    assert organic == before  # identical organic set: nothing demoted, nothing displaced
    kinds = ["rule" if h["corpus"] == "rule" else "mem" for h in after]
    assert kinds == sorted(kinds, key=lambda s: s == "rule")  # every rule after every mem


def test_rule_pointer_surfaces_even_on_corpus_abstention(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / "idx")
    _seed_recall_corpus(memory_dir, idx, monkeypatch)
    write_file(repo, "CLAUDE.md", _GOV_MD)
    RP.refresh_rules_cache(repo, idx)

    hits = R.recall(
        "clever abstractions explicit names short functions", k=6,
        memory_dir=memory_dir, index_dir=idx, repo_root=repo,
    )
    assert hits and all(h["corpus"] == "rule" for h in hits)
    assert hits[0]["name"] == "Style"
    assert all("steer" not in h for h in hits)  # GOV-2: rule pointers never carry steer


def test_irrelevant_query_clears_no_rule_floor(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / "idx")
    _seed_recall_corpus(memory_dir, idx, monkeypatch)
    write_file(repo, "CLAUDE.md", _GOV_MD)
    RP.refresh_rules_cache(repo, idx)
    hits = R.recall(
        "watering indoor houseplants during winter", k=6,
        memory_dir=memory_dir, index_dir=idx, repo_root=repo,
    )
    assert [h for h in hits if h["corpus"] == "rule"] == []


def test_no_import_no_duplication(repo, memory_dir, tmp_path, monkeypatch):
    """AC: the pointer copies nothing — the corpus gains no file, the rule file is untouched."""
    idx = str(tmp_path / "idx")
    _seed_recall_corpus(memory_dir, idx, monkeypatch)
    gov = write_file(repo, "CLAUDE.md", _GOV_MD)
    corpus_before = sorted(os.listdir(memory_dir))
    gov_before = open(gov, encoding="utf-8").read()
    RP.refresh_rules_cache(repo, idx)
    R.recall(
        "rollback procedure healthcheck deploy pipeline", k=6,
        memory_dir=memory_dir, index_dir=idx, repo_root=repo,
    )
    assert sorted(os.listdir(memory_dir)) == corpus_before
    assert open(gov, encoding="utf-8").read() == gov_before


def test_refresh_cache_signature_fast_path_and_rebuild(repo, memory_dir, tmp_path):
    idx = str(tmp_path / "idx")
    write_file(repo, "CLAUDE.md", _GOV_MD)
    first = RP.refresh_rules_cache(repo, idx)
    assert first["built"] is True and first["entries"] == 2
    second = RP.refresh_rules_cache(repo, idx)
    assert second["built"] is False and second["entries"] == 2  # unchanged sigs -> no-op
    write_file(repo, "CLAUDE.md", _GOV_MD + "\n# New\n\nA brand new governance section here.\n")
    third = RP.refresh_rules_cache(repo, idx)
    assert third["built"] is True and third["entries"] == 3


def test_cache_is_gitignored_derived_state(repo, memory_dir, tmp_path):
    idx = str(tmp_path / "idx")
    write_file(repo, "CLAUDE.md", _GOV_MD)
    RP.refresh_rules_cache(repo, idx)
    assert os.path.exists(os.path.join(idx, RP.RULES_CACHE_NAME))
    gi = os.path.join(idx, ".gitignore")
    assert os.path.exists(gi) and "*" in open(gi, encoding="utf-8").read()  # inv1


def test_no_governance_plane_removes_stale_cache(repo, memory_dir, tmp_path):
    idx = str(tmp_path / "idx")
    gov = write_file(repo, "CLAUDE.md", _GOV_MD)
    RP.refresh_rules_cache(repo, idx)
    os.remove(gov)
    out = RP.refresh_rules_cache(repo, idx)
    assert out == {"entries": 0, "built": False}
    assert RP.load_rules_cache(idx) is None  # no ghosts served after the plane vanished


def test_recall_view_tags_rule_pointer(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / "idx")
    _seed_recall_corpus(memory_dir, idx, monkeypatch)
    write_file(repo, "CLAUDE.md", _GOV_MD)
    RP.refresh_rules_cache(repo, idx)
    out = RV.describe(
        "rollback procedure healthcheck deploy pipeline", 6,
        memory_dir=memory_dir, index_dir=idx, repo_root=repo,
    )
    assert "rule — governance plane, not a memory" in out


def test_doctor_rules_source_reports_cache_state(repo, memory_dir, tmp_path, monkeypatch):
    r = D.check_rules_source(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok" and "no governance files" in r["message"]
    write_file(repo, "CLAUDE.md", _GOV_MD)
    r = D.check_rules_source(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok" and "not built yet" in r["message"]
    from memory.build_index import default_index_dir

    RP.refresh_rules_cache(repo, default_index_dir(memory_dir))
    r = D.check_rules_source(D.DoctorContext(memory_dir, repo))
    assert "2 governance section(s) indexed from 1 file(s)" in r["message"]
    assert "rules_source" in [label for label, _ in D.CHECKS]


def test_rules_source_never_raises(tmp_path):
    assert R._rules_source_hits([], None, None) == []
    assert R._rules_source_hits(["a"], str(tmp_path / "nope"), None) == []
    assert RP.load_rules_cache(None) is None
    assert RP.refresh_rules_cache(str(tmp_path / "nope"), str(tmp_path / "idx2")) == {
        "entries": 0,
        "built": False,
    }
