"""MEA-3: co-recall null model — lift-vs-independence annotation + confound collapse.

The GRW-2 proposal surface proposed raw distinct-session counts with no null model; on
the flagship corpus that produced 20/20 chance-level pairs (lift 1.02–1.21 at build).
The pins:

  AC1  each returned pair carries observed_sessions / expected_under_independence /
       lift (+ member_sessions & session_universe for the render) as ADDITIVE fields —
       pair/sessions values, ordering, min_sessions, and the 20-pair cap are unchanged;
       marginals come from the SAME union pass (no second ledger walk — source pin).
  AC2  the consolidate proposal render shows lift + member frequencies per pair and
       collapses sub-floor pairs into ONE count line — suppressed pairs are countable,
       never invisible (inv3).
  AC3  the deparasite decision, recorded at build: BOTH reads (protection :protected_map,
       weak-link) keep RAW counts — the permissive-protection default; neither applies
       the lift floor (source pin), and a chance-level pair still protects.
  AC4  the lift floor is a module constant (no env knob); cap and sort order untouched.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

from memory import telemetry as T
from memory import telemetry_mining as TM
from memory.telemetry import co_recall_pairs, default_telemetry_dir


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


def _sessions(td, mapping):
    for sid, names in mapping.items():
        T.log_episode(list(names), query="q", telemetry_dir=td, session_id=sid)


def test_lift_fields_are_exact_and_additive(memory_dir):
    td = default_telemetry_dir(memory_dir)
    # A in 5 sessions, B in 4, co-recalled in 3, universe = 6 (s6 keeps names, no pair)
    _sessions(td, {
        "s1": ["mem-a", "mem-b"],
        "s2": ["mem-a", "mem-b"],
        "s3": ["mem-a", "mem-b"],
        "s4": ["mem-a"],
        "s5": ["mem-a"],
        "s6": ["mem-b", "mem-c"],
    })
    pairs = co_recall_pairs(td)
    rec = next(p for p in pairs if p["pair"] == ["mem-a", "mem-b"])
    # the pre-MEA-3 surface is byte-identical for consumers reading only pair/sessions
    assert rec["sessions"] == 3
    # additive null-model fields
    assert rec["observed_sessions"] == 3
    assert rec["session_universe"] == 6
    assert rec["member_sessions"] == {"mem-a": 5, "mem-b": 4}
    assert rec["expected_under_independence"] == round(5 * 4 / 6, 2)
    assert rec["lift"] == round(3 / (5 * 4 / 6), 2)


def test_ordering_cap_and_threshold_unchanged(memory_dir):
    td = default_telemetry_dir(memory_dir)
    # 7 names co-recalled in 3 shared sessions -> C(7,2) = 21 pairs, capped at 20
    names = [f"m{i}" for i in range(7)]
    _sessions(td, {f"s{j}": names for j in range(3)})
    pairs = co_recall_pairs(td)
    assert len(pairs) == 20  # _CORECALL_MAX_PAIRS untouched
    assert pairs == sorted(pairs, key=lambda p: (-p["sessions"], p["pair"]))
    # below min_sessions the sparse map STAYS empty
    td2_root = os.path.join(os.path.dirname(td), "alt-telemetry")
    _sessions(td2_root, {"s1": ["x", "y"], "s2": ["x", "y"]})
    assert co_recall_pairs(td2_root) == []


def test_marginals_ride_the_same_union_pass():
    """No second ledger walk: co_recall_pairs reads episodes exactly once."""
    src = inspect.getsource(TM.co_recall_pairs)
    assert src.count("read_episodes(") == 1


def test_lift_floor_is_a_module_constant_not_an_env_knob():
    assert isinstance(TM._CORECALL_LIFT_FLOOR, float)
    assert "HIPPO" not in inspect.getsource(TM.co_recall_pairs)


def _mem(md, stem, description):
    with open(os.path.join(md, f"{stem}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {stem}\ndescription: {json.dumps(description)}\n"
            f"metadata:\n  type: project\n---\nbody about {stem}\n"
        )


def test_proposal_render_shows_lift_and_collapses_confounds(memory_dir, monkeypatch):
    from memory import mcp_tools_consolidate as MC
    from memory import provenance as P

    repo = os.path.dirname(os.path.dirname(memory_dir))
    for stem in ("rare-a", "rare-b", "staple-c", "staple-d", "filler-e"):
        _mem(memory_dir, stem, f"notes about {stem}")
    td = default_telemetry_dir(memory_dir)
    # strong pair: rare-a/rare-b co-recalled in all 3 of their sessions, universe 10
    mapping = {f"r{i}": ["rare-a", "rare-b"] for i in range(3)}
    # confounded staples: in 9/10 sessions each, co in 8 -> lift ~0.99
    for i in range(8):
        mapping[f"c{i}"] = ["staple-c", "staple-d"]
    mapping["c8"] = ["staple-c", "filler-e"]
    mapping["c9"] = ["staple-d", "filler-e"]
    _sessions(td, mapping)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    out = MC._tool_co_recall_proposals({})
    assert "rare-a <-> rare-b" in out
    assert "lift" in out
    # member frequencies rendered for the proposed pair
    assert "3/13" in out  # rare members appear in 3 of 13 sessions
    # the chance-level pair is COLLAPSED into a count line, never listed
    assert "staple-c <-> staple-d" not in out
    assert "chance-level pair(s) suppressed" in out
    assert str(TM._CORECALL_LIFT_FLOOR) in out


def test_deparasite_keeps_raw_counts_on_both_reads(memory_dir):
    """The recorded MEA-3 build decision (permissive-protection default): a chance-level
    pair still protects, and neither deparasite read references the lift floor."""
    from memory import deparasite as DP
    from memory.links import build_graph

    _mem(memory_dir, "staple-c", "notes about staple c")
    _mem(memory_dir, "staple-d", "notes about staple d")
    _mem(memory_dir, "filler-e", "notes about filler e")
    td = default_telemetry_dir(memory_dir)
    mapping = {f"c{i}": ["staple-c", "staple-d"] for i in range(8)}
    mapping["c8"] = ["staple-c", "filler-e"]
    mapping["c9"] = ["staple-d", "filler-e"]
    _sessions(td, mapping)  # staple pair lift ~0.99 — chance-level

    graph = build_graph(memory_dir)
    protected = DP.protected_map(memory_dir, graph, td)
    assert "co-recalled" in protected.get("staple-c", [])
    assert "co-recalled" in protected.get("staple-d", [])
    # negative-capability: the lift floor appears NOWHERE in deparasite
    src = inspect.getsource(DP)
    assert "_CORECALL_LIFT_FLOOR" not in src
