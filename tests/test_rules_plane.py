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
