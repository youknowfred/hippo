"""SRF-7 — dream reduced to the linker: the three tiers that measured no recall gain are off
by default and deprecated, still reachable by name, and say so in one plain line.

Pinned:
  - reverse replay no longer runs inside a discovery pass (so not inside scheduled sleep's
    report pass either) unless HIPPO_DREAM_REVERSE_REPLAY=1 asks for it; off, the pass is
    the no-outcome pass byte for byte (candidates, order, no boost ledger);
  - the generative tier, de-parasite and their sibling verbs still run when named, on the
    CLI and the MCP tool, and append exactly one deprecation line;
  - each tier's default is one switch in dream_deprecated; flipping it restores the old
    behaviour (reverse replay back on by default, no notice);
  - doctor names what is deprecated and warns when one is turned on;
  - the dream MCP schema keeps every input name and action while its text shrinks.
"""

from __future__ import annotations

import json
import os
import re

import pytest

import memory.dream as dream
from memory import dream_deprecated as DD

from .test_dream import _seed_sessions, _snapshot_md, _write_memory
from .test_dream_reward import _lineage_corpus, _seed_outcome_hit


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    for name in ("HIPPO_DREAM_REVERSE_REPLAY", "HIPPO_DREAM_GENERATIVE", "HIPPO_DREAM_APPLY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def dirs(tmp_path):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    return md, td, idx


def _outcome_corpus(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "charlie-outcome", "src/app.py", sid="s1")
    return md, td, idx


def _shape(result):
    return [(c["kind"], c["source"], c["target"], c["cofire"], c.get("boost")) for c in result["candidates"]]


_ROADMAP_ID = re.compile(r"\b[A-Z]{2,5}-\d+\b")


# --------------------------------------------------------------------------- #
# Reverse replay: off by default, one env var turns it back on
# --------------------------------------------------------------------------- #
def test_reverse_replay_is_off_by_default_even_with_a_recorded_outcome(dirs):
    md, td, idx = _outcome_corpus(dirs)
    assert DD.reverse_replay_enabled() is False

    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert result["reward"] == {"outcome_memories": {}, "memories": {}, "memory_sources": {}, "edges": []}
    assert result["stats"]["reward_boosted_edges"] == 0
    assert all("boost" not in c for c in result["candidates"])

    # The report pass (what scheduled sleep runs) writes no boost ledger and no reward line.
    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0 and "reward (reverse replay)" not in text
    assert not [f for f in os.listdir(dream.dream_dir(td)) if f.startswith("boosts-")]


def test_off_is_byte_identical_to_a_pass_with_no_outcome(dirs, tmp_path):
    md, td, idx = _outcome_corpus(dirs)
    with_outcome = dream.discover(md, idx, td)
    td2 = str(tmp_path / "tele-no-outcome")
    _seed_sessions(td2, 5)
    without = dream.discover(md, idx, td2)
    assert _shape(with_outcome) == _shape(without)
    assert with_outcome["stats"]["worklist_preview"] == without["stats"]["worklist_preview"]


def test_reverse_replay_runs_when_asked_for(dirs, monkeypatch):
    md, td, idx = _outcome_corpus(dirs)
    monkeypatch.setenv("HIPPO_DREAM_REVERSE_REPLAY", "1")
    assert DD.reverse_replay_enabled() is True
    result = dream.discover(md, idx, td)
    assert result["stats"]["reward_boosted_edges"] >= 1
    monkeypatch.setenv("HIPPO_DREAM_REVERSE_REPLAY", "0")
    assert DD.reverse_replay_enabled() is False


def test_the_switch_restores_the_old_default(dirs, monkeypatch):
    md, td, idx = _outcome_corpus(dirs)
    monkeypatch.setattr(DD, "REVERSE_REPLAY_DEPRECATED", False)
    assert DD.reverse_replay_enabled() is True
    assert DD.notice("reverse_replay") is None
    assert dream.discover(md, idx, td)["stats"]["reward_boosted_edges"] >= 1


# --------------------------------------------------------------------------- #
# The notice: one plain line, every deprecated tier, nothing when kept
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tier", ["reverse_replay", "generative", "deparasite"])
def test_notice_is_one_plain_line(tier, monkeypatch):
    line = DD.notice(tier)
    assert line and "\n" not in line and "v2.0.0" in line and "deprecated" in line
    assert not _ROADMAP_ID.search(line), line
    assert DD.with_notice("body", tier) == "body\n" + line
    attr = {"reverse_replay": "REVERSE_REPLAY_DEPRECATED", "generative": "GENERATIVE_DEPRECATED",
            "deparasite": "DEPARASITE_DEPRECATED"}[tier]
    monkeypatch.setattr(DD, attr, False)
    assert DD.notice(tier) is None and DD.with_notice("body", tier) == "body"
    assert tier not in DD.deprecated_tiers()


def test_generative_pass_still_runs_and_says_it_is_deprecated(dirs):
    md, td, idx = dirs
    for i, word in enumerate(("alpha", "bravo", "charlie")):
        _write_memory(md, f"note-{word}", f"{word} retry policy for the deploy lane {i}")
    _seed_sessions(td, 5)
    from memory.dream_generate import run_generative_pass

    code, text = run_generative_pass(md, idx, td)
    assert code == 0 and "dream generative pass" in text
    assert text.splitlines()[-1] == DD.notice("generative")
    # Below the soak bar the refusal still carries the line.
    code, text = run_generative_pass(md, idx, str(dirs[1]) + "-young")
    assert text.splitlines()[-1] == DD.notice("generative")


def test_deparasite_still_runs_and_says_it_is_deprecated(dirs):
    md, td, idx = dirs
    for word in ("alpha", "bravo", "charlie"):
        _write_memory(md, f"note-{word}", f"{word} retry policy")
    _seed_sessions(td, 5)
    before = _snapshot_md(md)
    from memory.deparasite import run_deparasite_pass

    code, text = run_deparasite_pass(md, idx, td)
    assert code == 0 and "de-parasite" in text
    assert text.splitlines()[-1] == DD.notice("deparasite")
    assert text.count(DD.notice("deparasite")) == 1
    assert _snapshot_md(md) == before


def test_cli_names_the_deprecation_once_per_deprecated_verb(dirs, capsys):
    md, td, idx = dirs
    _write_memory(md, "note-alpha", "alpha retry policy")
    base = ["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx]
    for argv, tier in (
        (["--dedup-merge", "note-alpha", "missing"], "deparasite"),
        (["--sweep-drafts"], "generative"),
        (["--archive-draft", "missing"], "generative"),
        (["--prospective"], "generative"),
        (["--deparasite"], "deparasite"),
        (["--generate"], "generative"),
    ):
        dream.main(base + argv)
        out = capsys.readouterr().out
        assert out.count(DD.notice(tier)) == 1, (argv, out)


def test_cli_bare_pass_has_no_notice_unless_reverse_replay_is_asked_for(dirs, capsys, monkeypatch):
    md, td, idx = _outcome_corpus(dirs)
    base = ["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx, "--dry-run"]
    assert dream.main(base) == 0
    out = capsys.readouterr().out
    assert "deprecated" not in out and "reward (reverse replay)" not in out
    monkeypatch.setenv("HIPPO_DREAM_REVERSE_REPLAY", "1")
    assert dream.main(base) == 0
    out = capsys.readouterr().out
    assert "reward (reverse replay)" in out and out.count(DD.notice("reverse_replay")) == 1
    # --json stays parseable: no notice line rides on the raw dump.
    assert dream.main(base[:-1] + ["--json"]) == 0
    json.loads(capsys.readouterr().out)


def test_mcp_tool_names_the_deprecation(dirs, monkeypatch):
    import memory.mcp_tools_setup as T

    md, td, idx = dirs
    _write_memory(md, "note-alpha", "alpha retry policy")
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda: (md, os.path.dirname(md)))
    for args, tier in (
        ({"action": "dedup_merge", "survivor": "note-alpha", "loser": "missing"}, "deparasite"),
        ({"action": "sweep_drafts"}, "generative"),
        ({"action": "archive_draft", "name": "missing"}, "generative"),
        ({"action": "prospective"}, "generative"),
        ({"action": "deparasite"}, "deparasite"),
        ({"action": "generate"}, "generative"),
    ):
        text = T._tool_dream(args)
        assert text.count(DD.notice(tier)) == 1, (args, text)
    assert "deprecated" not in T._tool_dream({"action": "log"})
    assert "deprecated" not in T._tool_dream({"action": "pass", "apply": False})
    monkeypatch.setenv("HIPPO_DREAM_REVERSE_REPLAY", "1")
    assert DD.notice("reverse_replay") in T._tool_dream({"action": "pass", "apply": False})


# --------------------------------------------------------------------------- #
# Doctor and the MCP schema
# --------------------------------------------------------------------------- #
def test_doctor_names_the_deprecated_tiers_and_warns_when_one_is_on(dirs, monkeypatch):
    from memory.doctor import CHECKS
    from memory.doctor_checks_env import DoctorContext

    md, _td, _idx = dirs
    assert ("dream_tiers", DD.check_dream_tiers) in CHECKS
    ctx = DoctorContext(md, os.path.dirname(md))
    r = DD.check_dream_tiers(ctx)
    assert r["status"] == "ok"
    for word in ("reverse replay", "generative", "de-parasite", "v2.0.0"):
        assert word in r["message"], r
    assert not _ROADMAP_ID.search(r["message"])
    monkeypatch.setenv("HIPPO_DREAM_REVERSE_REPLAY", "1")
    assert DD.check_dream_tiers(ctx)["status"] == "warn"
    monkeypatch.delenv("HIPPO_DREAM_REVERSE_REPLAY")
    monkeypatch.setenv("HIPPO_DREAM_GENERATIVE", "1")
    r = DD.check_dream_tiers(ctx)
    assert r["status"] == "warn" and "generative" in r["message"]
    for attr in ("REVERSE_REPLAY_DEPRECATED", "GENERATIVE_DEPRECATED", "DEPARASITE_DEPRECATED"):
        monkeypatch.setattr(DD, attr, False)
    assert DD.check_dream_tiers(ctx) == {"status": "ok", "message": "dream: every tier is current."}


_INPUTS = {"action", "apply", "contradictions", "edge_id", "reason", "undo_since", "retract",
           "survivor", "loser", "stage", "name"}
_ACTIONS = ["pass", "undo", "retire_ghost", "log", "deparasite", "dedup_merge", "generate",
            "sweep_drafts", "archive_draft", "prospective"]


def test_dream_schema_keeps_every_input_and_shrinks_to_the_linker():
    from memory.mcp_schemas import _TOOLS
    from memory.mcp_schemas_v2 import V2_TOOLS

    for tools in (_TOOLS, V2_TOOLS):
        tool = next(t for t in tools if t["name"] == "dream")
        props = tool["inputSchema"]["properties"]
        assert set(props) == _INPUTS
        assert props["action"]["enum"] == _ACTIONS
        assert "Deprecated, removed in v2.0.0" in tool["description"]
        assert not _ROADMAP_ID.search(tool["description"])
    v1 = next(t for t in _TOOLS if t["name"] == "dream")
    text = len(v1["description"]) + sum(len(p.get("description", "")) for p in v1["inputSchema"]["properties"].values())
    assert text <= 1200, text  # was 4,150 chars of schema text before the cut
