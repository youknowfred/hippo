"""Tests for memory/reconsolidate_brief.py — EVD-1's per-entry reverify brief.

Pins the acceptance criteria: NEW sibling module (the pinned-size files stay untouched),
diffstat + bounded hunk headers from the entry's OWN source_commit baseline, raw hunk
bodies only under the capture lane's secret discipline (otherwise stat/header-only),
composition of what already rides the entry (changed_paths / recency / linked /
evidence-drift fences / invalid_after), zero persisted state, the cold-path source/AST
pin (the resolve_evidence precedent), the untouched verdict vocabulary, and the honest
value claim (no minutes-per-verdict anywhere).

Hermetic: throwaway git repo + tmp telemetry, the test_reconsolidate.py fixture family.
"""

from __future__ import annotations

import ast
import inspect
import json
import os

import memory.reconsolidate as R
import memory.reconsolidate_brief as RB
from memory.build_index import default_index_dir
from memory.staleness import write_stale_cache

from .conftest import git_commit, write_file

# find_stale()'s default `since` window is WALL-CLOCK-relative; pinned-epoch fixtures need
# the wide fixed window (test_reconsolidate.py's `_ALL` pattern).
_ALL = "2000-01-01"


def _mem(name, cited, source_commit, extra=""):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return (
        f"---\nname: {name}\ndescription: \"{name} description\"\ncited_paths: {cp}\n"
        f"source_commit: {sc}\n{extra}---\nbody for {name}\n"
    )


def _seed_events(td, session_names):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for sid, names in session_names:
            fh.write(json.dumps({"session_id": sid, "names": names, "backend": "bm25"}) + "\n")


def _drifted_repo(repo, memory_dir, body_v1="def handler():\n    return 1\n",
                  body_v2="def handler():\n    return 2\n"):
    """One memory citing src/foo.py at baseline c1; the file then drifts. Returns c1."""
    write_file(repo, "src/foo.py", body_v1)
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    git_commit(repo, "add memory", 1_700_000_050)
    write_file(repo, "src/foo.py", body_v2)
    git_commit(repo, "drift", 1_700_000_100)
    return c1


# --------------------------------------------------------------------------- #
# The evidence itself — diffstat + headers from the entry's OWN baseline
# --------------------------------------------------------------------------- #
def test_brief_diffstat_and_headers_from_own_baseline(repo, memory_dir):
    """The diff range is the ENTRY's source_commit → HEAD — cumulative across every drift
    commit since the baseline, not just the newest one."""
    write_file(repo, "src/foo.py", "def handler():\n    x = 1\n    return x\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    git_commit(repo, "add memory", 1_700_000_050)
    write_file(repo, "src/foo.py", "def handler():\n    x = 2\n    return x\n")
    git_commit(repo, "drift 1", 1_700_000_100)
    write_file(repo, "src/foo.py", "def handler():\n    x = 3\n    return x\n")
    git_commit(repo, "drift 2", 1_700_000_200)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    brief = RB.brief_for_name("m_alpha", memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert brief is not None and brief["on_worklist"] is True
    assert brief["baseline_resolvable"] is True
    assert brief["source_commit"] == c1
    assert any("src/foo.py" in ln for ln in brief["diffstat"])
    # git's own function-context tail rides the @@ header lines
    assert any(ln.lstrip().startswith("@@") for ln in brief["hunk_headers"])
    assert any("def handler" in ln for ln in brief["hunk_headers"])
    # cumulative from c1 (−x=1 … +x=3), NOT newest-commit-only (which would show −x=2)
    assert "-    x = 1" in brief["bodies"] and "+    x = 3" in brief["bodies"]
    assert "-    x = 2" not in brief["bodies"]
    text = "\n".join(RB.render_brief(brief))
    assert f"baseline {c1[:7]}" in text and "diffstat:" in text


def test_brief_accepts_md_suffix_and_misses_are_none(repo, memory_dir):
    _drifted_repo(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    assert RB.brief_for_name("m_alpha.md", memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert RB.brief_for_name("no_such", memory_dir, repo, telemetry_dir=td, since=_ALL) is None


def test_stale_but_not_recalled_falls_back_with_an_honest_note(repo, memory_dir):
    """A name off the worklist (never recalled) still gets evidence — find_stale fallback,
    labeled as such (a human may brief a snoozed/terminal/unrecalled stale memory)."""
    _drifted_repo(repo, memory_dir)
    td = os.path.join(repo, "tele")  # empty ledger — nothing recalled
    brief = RB.brief_for_name("m_alpha", memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert brief is not None and brief["on_worklist"] is False
    text = "\n".join(RB.render_brief(brief))
    assert "not on the current worklist" in text and "evidence rendered anyway" in text


def test_unresolvable_baseline_degrades_honestly(repo, memory_dir):
    """SHP-3's class (squash-merge / shallow clone): no diff is fabricated; the brief says
    why and still lists the drifted paths."""
    entry = {"name": "m_gone", "changed_paths": ["src/foo.py"], "recency": 1_700_000_100,
             "source_commit": "f" * 40}
    brief = RB.entry_brief(entry, memory_dir, repo)
    assert brief["baseline_resolvable"] is False
    assert brief["diffstat"] == [] and brief["hunk_headers"] == [] and brief["bodies"] is None
    text = "\n".join(RB.render_brief(brief))
    assert "not in this repo's history" in text and "src/foo.py" in text


# --------------------------------------------------------------------------- #
# The capture lane's secret discipline — bodies gated, never a new exfil path
# --------------------------------------------------------------------------- #
def test_bodies_withheld_on_secret_hit_stat_and_headers_survive(repo, memory_dir):
    c1_body = "config = {}\n"
    drift_body = 'config = {"key": "AKIAIOSFODNN7EXAMPLE"}\n'
    _drifted_repo(repo, memory_dir, body_v1=c1_body, body_v2=drift_body)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    brief = RB.brief_for_name("m_alpha", memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert brief["bodies"] is None and "secret-scan hit" in brief["bodies_withheld"]
    assert brief["diffstat"]  # path names + counts only — always safe, always rendered
    text = "\n".join(RB.render_brief(brief))
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "withheld" in text and "diffstat/header-only" in text


def test_bodies_capped_at_the_capture_lane_cap(repo, memory_dir):
    big = "".join(f"line_{i} = {i}\n" for i in range(400))
    _drifted_repo(repo, memory_dir, body_v1="x = 0\n", body_v2=big)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    brief = RB.brief_for_name("m_alpha", memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert brief["bodies_truncated"] is True
    assert len(brief["bodies"]) <= RB._MAX_PROMPT_HUNK_CHARS
    assert "truncated at the capture lane's cap" in "\n".join(RB.render_brief(brief))


def test_clean_small_bodies_render(repo, memory_dir):
    _drifted_repo(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    brief = RB.brief_for_name("m_alpha", memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert brief["bodies_withheld"] is None and brief["bodies_truncated"] is False
    assert "+    return 2" in brief["bodies"]
    assert "secret-linted" in "\n".join(RB.render_brief(brief))


# --------------------------------------------------------------------------- #
# Composition — what already rides the entry / the caches
# --------------------------------------------------------------------------- #
def test_composes_linked_drift_fences_and_invalid_after(repo, memory_dir):
    c1 = _drifted_repo(repo, memory_dir)
    # invalid_after on the file (terminal state — find_stale is invalid_after-blind)
    write_file(
        memory_dir, "m_alpha.md",
        _mem("m_alpha", ["src/foo.py"], c1, extra="invalid_after: \"2026-01-01\"\n"),
    )
    idx = default_index_dir(memory_dir)
    assert write_stale_cache(
        idx,
        [{"name": "m_alpha", "changed_paths": ["src/foo.py"], "source_commit": c1}],
        evidence_drift={"m_alpha": {"fences": 2, "missing": 1, "whitespace": 1}},
    )
    entry = {"name": "m_alpha", "changed_paths": ["src/foo.py"], "recency": 1_700_000_100,
             "source_commit": c1, "linked": ["m_keep", "m_other"], "watermark": True}
    brief = RB.entry_brief(entry, memory_dir, repo, index_dir=idx)
    assert brief["evidence_drift"] == {"fences": 2, "missing": 1, "whitespace": 1}
    assert brief["invalid_after"] == "2026-01-01"
    assert brief["linked"] == ["m_keep", "m_other"]
    text = "\n".join(RB.render_brief(brief))
    assert "[since-watermark]" in text
    assert "evidence drift (CLB-3): 2 fenced snippet(s) — 1 missing at HEAD" in text
    assert "invalid_after: 2026-01-01" in text and "pre-cut penalty" in text
    assert "linked (review-adjacent): m_keep, m_other" in text


def test_zero_persisted_state_and_zero_corpus_writes(repo, memory_dir):
    """inv1: git history is the record — a brief run leaves the tree byte-identical
    (outside .git) and creates no new files anywhere in the repo."""
    _drifted_repo(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])

    def snapshot():
        out = {}
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                p = os.path.join(root, f)
                with open(p, "rb") as fh:
                    out[p] = fh.read()
        return out

    before = snapshot()
    brief = RB.brief_for_name("m_alpha", memory_dir, repo, telemetry_dir=td, since=_ALL)
    RB.render_brief(brief)
    assert snapshot() == before


# --------------------------------------------------------------------------- #
# Cold path + read-only — the resolve_evidence precedent, pinned
# --------------------------------------------------------------------------- #
def test_cold_path_source_pin_no_hook_surface_reaches_the_brief():
    """SessionStart producers, the UserPromptSubmit hot path, and the PostToolUse/
    SessionEnd lanes must hold ZERO references to the brief — it is reachable only from
    the reconsolidate MCP tool, its own CLI, and the consolidate skill."""
    import memory.capture as capture
    import memory.jit as jit
    import memory.outcome as outcome
    import memory.recall as recall
    import memory.session_start as session_start
    import memory.session_start_health as session_start_health
    import memory.session_start_signals as session_start_signals
    import memory.telemetry as telemetry

    for mod in (
        session_start,
        session_start_health,
        session_start_signals,
        recall,
        R,
        outcome,
        jit,
        capture,
        telemetry,
    ):
        assert "reconsolidate_brief" not in inspect.getsource(mod), (
            f"{mod.__name__} references reconsolidate_brief — the brief is cold-path only "
            "(EVD-1's binding pin, the resolve_evidence precedent)"
        )


def test_producer_renders_no_brief_evidence(repo, memory_dir):
    """The behavior half of the pin: a stale+recalled corpus still renders the worklist
    as names+paths only — no diffstat, no hunks, no git-diff mining at SessionStart."""
    _drifted_repo(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    ctx = R.RunContext(worklist=worklist)
    out = R.reconsolidation_producer(memory_dir, repo, ctx)
    assert out and "m_alpha" in out
    assert "diffstat" not in out and "hunk" not in out and "baseline" not in out


def test_brief_module_calls_no_write_primitive():
    """AST pin: the whole module is read-only — no corpus/ledger write primitive is ever
    called, and open() is only ever a read (the test_resolve_view.py pattern)."""
    _WRITERS = {
        "write_memory", "semantic_reverify", "add_typed_relation", "remove_typed_relation",
        "set_invalid_after", "restore_file_bytes", "write_text_atomic", "write_json_atomic",
        "write_stale_cache", "record_reconsolidation_outcome", "replace", "dump", "makedirs",
    }
    tree = ast.parse(inspect.getsource(RB))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else ""
            )
            if called in _WRITERS:
                offenders.append(called)
            if called == "open":
                modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                modes += [
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
                ]
                if any("w" in str(m) or "a" in str(m) or "+" in str(m) for m in modes):
                    offenders.append("open(write-mode)")
    assert not offenders, f"reconsolidate_brief must stay read-only; found: {offenders}"


def test_pinned_size_files_are_untouched_by_the_brief():
    """The 900/900 and 2072/2072 files receive nothing at all for EVD-1 — the brief's
    wiring lives in mcp_tools_consolidate.py and the skill text only."""
    import memory.provenance as P

    assert "reconsolidate_brief" not in inspect.getsource(R)
    assert "reconsolidate_brief" not in inspect.getsource(P)


# --------------------------------------------------------------------------- #
# Verdict vocabulary + the honest value claim
# --------------------------------------------------------------------------- #
def test_verdict_vocabulary_untouched_and_all_four_paths_render():
    from memory.mcp_schemas import _TOOLS

    assert R._VALID_OUTCOMES == frozenset({"graduate", "fix", "demote"})
    recon = next(t for t in _TOOLS if t["name"] == "reconsolidate")
    assert recon["inputSchema"]["properties"]["outcome"]["enum"] == [
        "graduate", "fix", "demote", "snooze",
    ]
    entry = {"name": "m", "changed_paths": ["a.py"], "source_commit": "e" * 40}
    text = "\n".join(RB.render_brief(RB.entry_brief(entry, "/nonexistent", None)))
    for path in ("graduate", "fix", "demote", "snooze"):
        assert path in text  # evidence for ALL four human paths (LIF-1)
    assert "suggested" not in text  # the brief renders evidence, never a prefill


def test_schema_retires_the_hand_diff_and_names_the_brief():
    from memory.mcp_schemas import _TOOLS

    recon = next(t for t in _TOOLS if t["name"] == "reconsolidate")
    assert "diff its cited paths" not in recon["description"]  # EKPI4R-4's target
    assert "brief" in recon["description"]
    assert recon["inputSchema"]["properties"]["action"]["enum"] == [
        "worklist", "brief", "reverify",
    ]


def test_no_minutes_per_verdict_claim_ships():
    """The honest value bound carried from vetting: no unmeasured time-savings claim in
    the module, the tool description, or the skill text."""
    from memory.mcp_schemas import _TOOLS

    skill = os.path.join(
        os.path.dirname(os.path.dirname(inspect.getsourcefile(RB))), "skills",
        "consolidate", "SKILL.md",
    )
    with open(skill, "r", encoding="utf-8") as fh:
        skill_text = fh.read()
    recon = next(t for t in _TOOLS if t["name"] == "reconsolidate")
    for text in (inspect.getsource(RB), recon["description"], skill_text):
        assert "minutes per verdict" not in text and "minutes-per-verdict" not in text


def test_skill_step2_names_the_brief_cli():
    skill = os.path.join(
        os.path.dirname(os.path.dirname(inspect.getsourcefile(RB))), "skills",
        "consolidate", "SKILL.md",
    )
    with open(skill, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "memory.reconsolidate_brief" in text and "action='brief'" in text


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #
def test_cli_renders_brief(repo, memory_dir, capsys):
    _drifted_repo(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    rc = RB.main([
        "m_alpha", "--memory-dir", memory_dir, "--repo-root", repo,
        "--telemetry-dir", td, "--since", _ALL,
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m_alpha — baseline" in out and "diffstat:" in out and "verdict (yours" in out


def test_cli_nothing_to_brief_is_legible(repo, memory_dir, capsys):
    rc = RB.main([
        "no_such", "--memory-dir", memory_dir, "--repo-root", repo, "--since", _ALL,
    ])
    assert rc == 1
    assert "nothing to brief" in capsys.readouterr().out
