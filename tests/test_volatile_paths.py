"""Tests for VOL-1 — the volatile-paths staleness-ARMING policy.

The field report's split, held mechanically: a corpus-level ``volatile_paths`` key in the
``.claude/memory/.format`` marker names high-churn coordination files (a living roadmap, a
migration runner) whose drift alone must not ARM the reconsolidation worklist, the
SessionStart staleness note, or the [since-watermark] flag — while DERIVATION (the
extractor still cites them) and every RECALL surface (JIT touch map, ``recall --for-diff``,
the RET-6 stale.json banner, RET-5's penalty) stay byte-identical. Suppression is never
silent: the note/worklist/CLI surfaces print what policy suppressed, and doctor carries
one display line. Absent/empty registry ⇒ behavior identical to today (ED-4).

Hermetic: throwaway git repo + memory corpus per test (the ``repo``/``memory_dir``
fixtures), synthesized ledgers in tmp telemetry dirs, pinned commit epochs.
"""

from __future__ import annotations

import json
import os

import memory.reconsolidate as R
import memory.session_start as S
from memory import staleness_policy as SP
from memory.provenance_format import (
    read_volatile_paths,
    write_cite_derivation,
    write_corpus_format,
)
from memory.staleness import RunContext, find_stale, read_stale_cache, write_stale_cache

from .conftest import git_commit, write_file

# Wide fixed window (mirrors test_staleness.py's _ALL) so pinned-epoch fixtures are
# always inside find_stale's wall-clock-relative default.
_ALL = "2000-01-01"

# The field report's tier-1 seed registrations — used here as fixture vocabulary.
_ROADMAP = "GROWTH-LOOP-ROADMAP.yaml"
_MATRIX = "docs/audience-matrix.yaml"


def _mem(name, cited, source_commit):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return (
        f"---\nname: {name}\ndescription: \"{name} description\"\ncited_paths: {cp}\n"
        f"source_commit: {sc}\n---\nbody for {name}\n"
    )


def _declare(memory_dir, paths, **extra):
    """Merge ``volatile_paths`` into the corpus marker the way an operator would commit it."""
    marker = os.path.join(memory_dir, ".format")
    data = {}
    if os.path.isfile(marker):
        with open(marker, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    data.update({"volatile_paths": paths}, **extra)
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _seed_events(td, session_names):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for sid, names in session_names:
            fh.write(json.dumps({"session_id": sid, "names": names, "backend": "bm25"}) + "\n")


def _episode_line(td, sid, ts, head_commit):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": ts,
                    "session_id": sid,
                    "query_preview": "q",
                    "recalled_names": [],
                    "head_commit": head_commit,
                }
            )
            + "\n"
        )


def _drifted_corpus(repo, memory_dir, t0=1_700_000_000):
    """One volatile-only-drifted memory + one mixed-drift memory.

    Baseline commit c1 at ``t0``; then BOTH the roadmap (volatile) and src/foo.py
    (durable) drift. ``m_vol`` cites only the roadmap; ``m_mix`` cites both. Pass a
    now-relative ``t0`` for surfaces that cannot override find_stale's wall-clock
    ``since`` window (the CLI / MCP tool), pinned epochs everywhere else.
    """
    write_file(repo, _ROADMAP, "phase: 1\n")
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", t0)
    write_file(memory_dir, "m_vol.md", _mem("m_vol", [_ROADMAP], c1))
    write_file(memory_dir, "m_mix.md", _mem("m_mix", [_ROADMAP, "src/foo.py"], c1))
    write_file(repo, _ROADMAP, "phase: 2\n")
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", t0 + 100)
    return c1


# --------------------------------------------------------------------------- #
# The registry read — provenance_format.read_volatile_paths
# --------------------------------------------------------------------------- #
def test_read_volatile_paths_absent_marker_is_empty(tmp_path):
    assert read_volatile_paths(str(tmp_path)) == []


def test_read_volatile_paths_normalizes_and_dedupes(tmp_path):
    _declare(str(tmp_path), ["./" + _ROADMAP, _ROADMAP, "  docs/audience-matrix.yaml  ", "", 7, True, None])
    assert read_volatile_paths(str(tmp_path)) == [_ROADMAP, _MATRIX]


def test_read_volatile_paths_wrong_shapes_degrade_to_empty(tmp_path):
    _declare(str(tmp_path), "not-a-list")
    assert read_volatile_paths(str(tmp_path)) == []
    with open(os.path.join(str(tmp_path), ".format"), "w", encoding="utf-8") as fh:
        fh.write("{corrupt json")
    assert read_volatile_paths(str(tmp_path)) == []


def test_format_and_derivation_stamps_preserve_volatile_paths(tmp_path):
    """The marker's merge-not-clobber writer must carry the registry through version stamps."""
    md = str(tmp_path)
    _declare(md, [_ROADMAP])
    assert write_corpus_format(md)
    assert write_cite_derivation(md)
    assert read_volatile_paths(md) == [_ROADMAP]


def test_read_volatile_paths_reexported_via_provenance_facade():
    from memory import provenance as P

    assert P.read_volatile_paths is read_volatile_paths


# --------------------------------------------------------------------------- #
# The policy partition — staleness_policy.split_volatile_only
# --------------------------------------------------------------------------- #
def test_split_volatile_only_partitions_and_preserves_items():
    items = [
        {"name": "a", "changed_paths": [_ROADMAP]},
        {"name": "b", "changed_paths": [_ROADMAP, "src/foo.py"]},
        {"name": "c", "changed_paths": ["src/bar.py"]},
    ]
    armed, suppressed = SP.split_volatile_only(items, {_ROADMAP})
    assert [i["name"] for i in armed] == ["b", "c"]
    assert [i["name"] for i in suppressed] == ["a"]
    # Armed items keep their FULL changed_paths — the listing stays honest (AC5).
    assert armed[0]["changed_paths"] == [_ROADMAP, "src/foo.py"]


def test_split_volatile_only_empty_registry_is_identity():
    items = [{"name": "a", "changed_paths": [_ROADMAP]}]
    armed, suppressed = SP.split_volatile_only(items, set())
    assert armed == items and suppressed == []


def test_split_volatile_only_never_suppresses_empty_changed_paths():
    """An item with NO changed paths (whatever produced it) is not 'volatile-only'."""
    items = [{"name": "a", "changed_paths": []}]
    armed, suppressed = SP.split_volatile_only(items, {_ROADMAP})
    assert armed == items and suppressed == []


# --------------------------------------------------------------------------- #
# AC1 + AC5 — the worklist's stale lane
# --------------------------------------------------------------------------- #
def test_worklist_drops_volatile_only_items_and_reports_them(repo, memory_dir):
    _drifted_corpus(repo, memory_dir)
    _declare(memory_dir, [_ROADMAP])
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_vol", "m_mix"])])

    diagnostics = {}
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    names = [w["name"] for w in worklist]
    assert names == ["m_mix"]  # AC1: volatile-only drift never enters the worklist
    # AC5: the mixed item is flagged exactly as today — full changed_paths, both files.
    assert set(worklist[0]["changed_paths"]) == {_ROADMAP, "src/foo.py"}
    # AC3 plumbing: the suppression is recorded, never silent.
    assert diagnostics[SP.DIAG_KEY] == ["m_vol"]


def test_worklist_without_registry_is_unchanged(repo, memory_dir):
    """AC4: absent registry ⇒ byte-identical behavior — m_vol arms exactly as today."""
    _drifted_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_vol", "m_mix"])])

    names = {w["name"] for w in R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)}
    assert names == {"m_vol", "m_mix"}


# --------------------------------------------------------------------------- #
# AC1 — the [since-watermark] lane
# --------------------------------------------------------------------------- #
def test_watermark_drops_volatile_only_hits_and_reports_them(repo, memory_dir):
    c1 = _drifted_corpus(repo, memory_dir)
    _declare(memory_dir, [_ROADMAP])
    td = os.path.join(repo, "tele")
    _episode_line(td, "last-sess", 100.0, c1)  # last session started at the baseline

    diagnostics = {}
    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td, diagnostics=diagnostics)
    names = [c["name"] for c in cands]
    assert names == ["m_mix"]  # m_vol's only watermark hit is the roadmap — no flag (AC1)
    assert set(cands[0]["changed_paths"]) == {_ROADMAP, "src/foo.py"}  # AC5: full listing
    assert diagnostics[SP.DIAG_KEY] == ["m_vol"]


def test_watermark_without_registry_is_unchanged(repo, memory_dir):
    c1 = _drifted_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _episode_line(td, "last-sess", 100.0, c1)

    names = {c["name"] for c in R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)}
    assert names == {"m_vol", "m_mix"}


def test_evidence_drift_still_arms_a_volatile_only_memory(repo, memory_dir):
    """CLB-3 composition: quoted-evidence drift is span-level truth, NOT whole-file churn —
    it arms even when the memory's only drifted citation is a volatile path. The fold runs
    AFTER the watermark producer's policy filter, so the evidence lane must survive it."""
    from memory.staleness_evidence import fold_drift_candidates

    c1 = _drifted_corpus(repo, memory_dir)
    _declare(memory_dir, [_ROADMAP])
    td = os.path.join(repo, "tele")
    _episode_line(td, "last-sess", 100.0, c1)

    wm = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    assert all(c["name"] != "m_vol" for c in wm)  # policy suppressed the whole-file hit
    folded = fold_drift_candidates(wm, {"m_vol": {"fences": 1, "missing": 1, "whitespace": 0, "paths": [_ROADMAP]}})
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, since=_ALL, watermark_stale=folded
    )
    assert any(w["name"] == "m_vol" and w.get("evidence") for w in worklist)


# --------------------------------------------------------------------------- #
# AC3 — the SessionStart staleness note reports what policy suppressed
# --------------------------------------------------------------------------- #
def _stub_stale(monkeypatch, items):
    monkeypatch.setattr(S, "find_stale", lambda md, repo, diagnostics=None: list(items))


def test_staleness_note_counts_policy_suppressed_tail(memory_dir, monkeypatch):
    _declare(memory_dir, [_ROADMAP])
    _stub_stale(
        monkeypatch,
        [
            {"name": "m_mix", "changed_paths": ["src/foo.py", _ROADMAP]},
            {"name": "m_vol", "changed_paths": [_ROADMAP]},
            {"name": "m_vol2", "changed_paths": [_ROADMAP]},
        ],
    )
    out = S.staleness_producer(memory_dir, "repo")
    assert "1 memories cite code" in out  # header counts ARMED entries only
    assert "m_mix" in out
    assert "m_vol" not in out.replace("m_vol2", "")  # no per-item line for suppressed items
    assert "(+2" in out and "volatile" in out and "policy-suppressed" in out
    assert ".format volatile_paths" in out  # auditable pointer, per the report


def test_staleness_note_all_suppressed_renders_one_calm_line(memory_dir, monkeypatch):
    _declare(memory_dir, [_ROADMAP, _MATRIX])
    _stub_stale(
        monkeypatch,
        [
            {"name": "m_vol", "changed_paths": [_ROADMAP]},
            {"name": "m_vol2", "changed_paths": [_MATRIX, _ROADMAP]},
        ],
    )
    out = S.staleness_producer(memory_dir, "repo")
    assert out is not None  # suppression must never look like "nothing stale"
    assert "2" in out and "volatile" in out and "policy-suppressed" in out
    assert "m_vol" not in out  # no per-item treadmill lines
    assert "already demoted" not in out  # and not the WRONG all-demoted text


def test_staleness_note_without_registry_is_unchanged(memory_dir, monkeypatch):
    _stub_stale(monkeypatch, [{"name": "m_vol", "changed_paths": [_ROADMAP]}])
    out = S.staleness_producer(memory_dir, "repo")
    assert "m_vol" in out and "policy-suppressed" not in out


def test_staleness_note_excludes_worklist_names_from_suppressed_count(memory_dir, monkeypatch):
    """A volatile-only memory armed via the EVIDENCE lane is on the worklist below — it
    renders there, so the suppressed count must not double-report it."""
    _declare(memory_dir, [_ROADMAP])
    _stub_stale(
        monkeypatch,
        [
            {"name": "m_vol", "changed_paths": [_ROADMAP]},
            {"name": "m_vol2", "changed_paths": [_ROADMAP]},
        ],
    )
    ctx = RunContext(
        stale=[
            {"name": "m_vol", "changed_paths": [_ROADMAP]},
            {"name": "m_vol2", "changed_paths": [_ROADMAP]},
        ],
        worklist=[{"name": "m_vol", "changed_paths": [_ROADMAP], "watermark": True, "evidence": True}],
    )
    out = S.staleness_producer(memory_dir, "repo", ctx)
    assert "(+1" in out or "1 " in out  # one suppressed (m_vol2), not two
    assert "(+2" not in out


# --------------------------------------------------------------------------- #
# AC3 — the CLI and MCP worklist listings
# --------------------------------------------------------------------------- #
def test_reconsolidate_cli_prints_suppressed_count(repo, memory_dir, capsys):
    import time

    _drifted_corpus(repo, memory_dir, t0=int(time.time()) - 7200)
    _declare(memory_dir, [_ROADMAP])
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_vol", "m_mix"])])

    rc = R.main(["--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m_mix" in out
    assert not any(ln.strip().startswith("• m_vol") for ln in out.splitlines())
    assert "policy-suppressed" in out


def test_consolidate_mcp_worklist_prints_suppressed_count(repo, memory_dir, monkeypatch):
    import time

    from memory import mcp_tools_consolidate as MC

    _drifted_corpus(repo, memory_dir, t0=int(time.time()) - 7200)
    _declare(memory_dir, [_ROADMAP])
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_vol", "m_mix"])])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)

    out = MC._tool_reconsolidate({"action": "worklist"})
    assert "m_mix" in out
    assert "policy-suppressed" in out


# --------------------------------------------------------------------------- #
# AC2 — recall surfaces keep consuming the citation (derivation + recall unchanged)
# --------------------------------------------------------------------------- #
def test_find_stale_and_stale_cache_still_carry_volatile_only_memories(repo, memory_dir, tmp_path):
    """Detection (and thus RET-5's penalty + RET-6's banner source) stays drift-complete —
    the policy nuances ARMING, never the stale.json recall surfaces."""
    _drifted_corpus(repo, memory_dir)
    _declare(memory_dir, [_ROADMAP])

    stale = find_stale(memory_dir, repo, since=_ALL)
    assert {s["name"] for s in stale} == {"m_vol", "m_mix"}
    idx = str(tmp_path / "index")
    assert write_stale_cache(idx, stale)
    cache = read_stale_cache(idx)
    assert "m_vol" in cache and cache["m_vol"]["changed"] == 1


def test_for_diff_join_still_includes_volatile_cited_memory(repo, memory_dir):
    import time

    from memory.recall_diff import memories_for_paths

    # Now-relative commits: the join's stale annotation runs find_stale's default window.
    _drifted_corpus(repo, memory_dir, t0=int(time.time()) - 7200)
    _declare(memory_dir, [_ROADMAP])

    rows = memories_for_paths([_ROADMAP], memory_dir, repo_root=repo)
    by_name = {r["name"]: r for r in rows}
    assert "m_vol" in by_name and _ROADMAP in by_name["m_vol"]["paths"]
    assert by_name["m_vol"]["stale"] is not None  # the verify-at-use annotation still rides


def test_jit_touch_map_still_maps_volatile_files(repo, memory_dir):
    from memory.jit import build_touch_cache

    _drifted_corpus(repo, memory_dir)
    _declare(memory_dir, [_ROADMAP])

    cache = build_touch_cache(memory_dir)
    assert "m_vol" in (cache.get("cited") or {}).get(_ROADMAP, [])


# --------------------------------------------------------------------------- #
# AC6 — derivation is registry-blind: rederive output identical with and without it
# --------------------------------------------------------------------------- #
def test_backfill_derivation_identical_with_and_without_registry(repo, memory_dir):
    from memory import provenance as P

    write_file(repo, _ROADMAP, "phase: 1\n")
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    body = (
        "---\nname: m_del\ndescription: \"delegates to the roadmap\"\n---\n"
        f"Live status lives in {_ROADMAP} (canonical); helper is src/foo.py.\n"
    )
    path = write_file(memory_dir, "m_del.md", body)

    P.backfill_corpus(memory_dir, repo)
    with open(path, "r", encoding="utf-8") as fh:
        without_registry = fh.read()
    assert _ROADMAP in without_registry  # derivation still CITES the volatile path

    write_file(memory_dir, "m_del.md", body)  # reset to the underived form
    _declare(memory_dir, [_ROADMAP])
    P.backfill_corpus(memory_dir, repo)
    with open(path, "r", encoding="utf-8") as fh:
        with_registry = fh.read()
    assert with_registry == without_registry  # AC6: byte-identical derivation


# --------------------------------------------------------------------------- #
# Doctor — one display line, never a nag
# --------------------------------------------------------------------------- #
def test_doctor_volatile_paths_line_absent_registry(repo, memory_dir):
    import memory.doctor as D

    r = D.check_volatile_paths(D.DoctorContext(memory_dir, repo, plugin_data="", plugin_root=""))
    assert r["status"] == "ok"
    assert "volatile" in r["message"] and "none declared" in r["message"]


def test_doctor_volatile_paths_line_counts_registered_and_suppressed(repo, memory_dir):
    import time

    import memory.doctor as D

    # Now-relative commits: the check cannot override find_stale's wall-clock window.
    _drifted_corpus(repo, memory_dir, t0=int(time.time()) - 7200)
    _declare(memory_dir, [_ROADMAP, _MATRIX])
    r = D.check_volatile_paths(D.DoctorContext(memory_dir, repo, plugin_data="", plugin_root=""))
    assert r["status"] == "ok"  # policy state is information, never a warn (no new nag)
    assert "2" in r["message"]  # registered-path count
    assert "1" in r["message"]  # m_vol is currently suppressed (volatile-only drift)
