"""Tests for TYPE-1 — the type-based staleness-ARMING exemption (VOL-1's sibling).

The 2026-08-17 em-growth-labs memory×Linear audit's option A, owner-picked: ``type:
project`` memories no longer ARM the reconsolidation worklist, the SessionStart staleness
note, or the [since-watermark] flag — 70/79 worklist items were project-typed, 83% of past
reverify verdicts were "graduate", and 60/66 graduated items re-armed, so the queue was
metering repo velocity, not truth-risk. DETECTION stays type-blind (find_stale,
stale.json, RET-5/RET-6, JIT, --for-diff, derivation), suppression is never silent
(``DIAG_TYPE_KEY`` + note/worklist tails), an item with NO type ARMS (fail-open), and
``HIPPO_ARMING_EXEMPT_TYPES`` (comma-list; EMPTY ⇒ arm everything) is the reversible
override. Partition order is VOL then TYPE — each suppressed name lands in ONE bucket.

Hermetic: throwaway git repo + memory corpus per test (the ``repo``/``memory_dir``
fixtures), synthesized ledgers in tmp telemetry dirs, pinned commit epochs.
"""

from __future__ import annotations

import json
import os

import memory.reconsolidate as R
import memory.session_start as S
import memory.session_start_signals as SG
from memory import staleness_policy as SP
from memory.staleness import find_stale, read_memory_type

from .conftest import git_commit, write_file

# Wide fixed window (mirrors test_volatile_paths.py's _ALL) so pinned-epoch fixtures are
# always inside find_stale's wall-clock-relative default.
_ALL = "2000-01-01"

_ROADMAP = "GROWTH-LOOP-ROADMAP.yaml"


def _mem(name, cited, source_commit, mtype=None, nested=False):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    if mtype is None:
        head = f"---\nname: {name}\ncited_paths: {cp}\nsource_commit: {sc}\n---\n"
    elif nested:
        head = (
            f"---\nname: {name}\nmetadata:\n  type: {mtype}\ncited_paths: {cp}\n"
            f"source_commit: {sc}\n---\n"
        )
    else:
        head = f"---\nname: {name}\ntype: {mtype}\ncited_paths: {cp}\nsource_commit: {sc}\n---\n"
    return head + f"body for {name}\n"


def _declare_volatile(memory_dir, paths):
    marker = os.path.join(memory_dir, ".format")
    data = {}
    if os.path.isfile(marker):
        with open(marker, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    data["volatile_paths"] = paths
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


def _typed_corpus(repo, memory_dir, t0=1_700_000_000):
    """Three memories over one drifted durable file: project-typed / user-typed / untyped."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", t0)
    write_file(memory_dir, "m_proj.md", _mem("m_proj", ["src/foo.py"], c1, mtype="project"))
    write_file(memory_dir, "m_user.md", _mem("m_user", ["src/foo.py"], c1, mtype="user"))
    write_file(memory_dir, "m_untyped.md", _mem("m_untyped", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", t0 + 100)
    return c1


# --------------------------------------------------------------------------- #
# The frontmatter read — staleness.read_memory_type
# --------------------------------------------------------------------------- #
def test_read_memory_type_top_level_and_nested_and_lowercased():
    assert read_memory_type("---\nname: a\ntype: Project\n---\nb\n") == "project"
    assert read_memory_type("---\nname: a\nmetadata:\n  type: user\n---\nb\n") == "user"


def test_read_memory_type_absent_blank_or_nonstring_is_none():
    assert read_memory_type("---\nname: a\n---\nb\n") is None
    assert read_memory_type("---\nname: a\ntype: \"  \"\n---\nb\n") is None
    assert read_memory_type("---\nname: a\ntype: 7\n---\nb\n") is None
    assert read_memory_type("no frontmatter at all") is None


# --------------------------------------------------------------------------- #
# The exempt set — staleness_policy.arming_exempt_types
# --------------------------------------------------------------------------- #
def test_arming_exempt_types_default_is_project(monkeypatch):
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    assert SP.arming_exempt_types() == frozenset({"project"})


def test_arming_exempt_types_env_list_normalizes(monkeypatch):
    monkeypatch.setenv(SP._EXEMPT_TYPES_ENV, " Project , user ,")
    assert SP.arming_exempt_types() == frozenset({"project", "user"})


def test_arming_exempt_types_empty_env_arms_everything(monkeypatch):
    monkeypatch.setenv(SP._EXEMPT_TYPES_ENV, "")
    assert SP.arming_exempt_types() == frozenset()


# --------------------------------------------------------------------------- #
# The policy partition — staleness_policy.split_type_exempt
# --------------------------------------------------------------------------- #
def test_split_type_exempt_partitions_and_preserves_order():
    items = [
        {"name": "a", "type": "project", "changed_paths": ["x"]},
        {"name": "b", "type": "user", "changed_paths": ["x"]},
        {"name": "c", "type": "project", "changed_paths": ["x", "y"]},
    ]
    armed, suppressed = SP.split_type_exempt(items, frozenset({"project"}))
    assert [i["name"] for i in armed] == ["b"]
    assert [i["name"] for i in suppressed] == ["a", "c"]
    assert suppressed[1]["changed_paths"] == ["x", "y"]  # items pass through untouched


def test_split_type_exempt_missing_type_arms():
    items = [{"name": "a", "changed_paths": ["x"]}, {"name": "b", "type": None, "changed_paths": ["x"]}]
    armed, suppressed = SP.split_type_exempt(items, frozenset({"project"}))
    assert armed == items and suppressed == []


def test_split_type_exempt_empty_set_is_identity():
    items = [{"name": "a", "type": "project", "changed_paths": ["x"]}]
    armed, suppressed = SP.split_type_exempt(items, frozenset())
    assert armed == items and suppressed == []


# --------------------------------------------------------------------------- #
# Detection stays type-blind, and carries the field the split reads
# --------------------------------------------------------------------------- #
def test_find_stale_stays_type_blind_and_carries_type(repo, memory_dir):
    _typed_corpus(repo, memory_dir)
    stale = find_stale(memory_dir, repo, since=_ALL)
    by_name = {s["name"]: s for s in stale}
    assert set(by_name) == {"m_proj", "m_user", "m_untyped"}  # detection sees everything
    assert by_name["m_proj"]["type"] == "project"
    assert by_name["m_user"]["type"] == "user"
    assert by_name["m_untyped"]["type"] is None


# --------------------------------------------------------------------------- #
# The worklist's stale lane
# --------------------------------------------------------------------------- #
def test_worklist_exempts_project_typed_and_reports_them(repo, memory_dir, monkeypatch):
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    _typed_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_proj", "m_user", "m_untyped"])])

    diagnostics = {}
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    names = {w["name"] for w in worklist}
    assert names == {"m_user", "m_untyped"}  # fail-open: only the explicit project type exempts
    assert diagnostics[SP.DIAG_TYPE_KEY] == ["m_proj"]
    assert SP.DIAG_KEY not in diagnostics or diagnostics[SP.DIAG_KEY] == []


def test_worklist_override_empty_env_arms_project_again(repo, memory_dir, monkeypatch):
    monkeypatch.setenv(SP._EXEMPT_TYPES_ENV, "")
    _typed_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_proj", "m_user", "m_untyped"])])

    names = {
        w["name"]
        for w in R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    }
    assert names == {"m_proj", "m_user", "m_untyped"}  # byte-identical pre-TYPE-1 arming


def test_vol_then_type_order_buckets_each_name_once(repo, memory_dir, monkeypatch):
    """A project-typed memory whose ONLY drift is volatile lands in the VOL bucket."""
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    write_file(repo, _ROADMAP, "phase: 1\n")
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_projvol.md", _mem("m_projvol", [_ROADMAP], c1, mtype="project"))
    write_file(memory_dir, "m_proj.md", _mem("m_proj", ["src/foo.py"], c1, mtype="project", nested=True))
    write_file(repo, _ROADMAP, "phase: 2\n")
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)
    _declare_volatile(memory_dir, [_ROADMAP])
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_projvol", "m_proj"])])

    diagnostics = {}
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    assert worklist == []
    assert diagnostics[SP.DIAG_KEY] == ["m_projvol"]  # VOL first…
    assert diagnostics[SP.DIAG_TYPE_KEY] == ["m_proj"]  # …TYPE takes the remainder (nested schema too)


# --------------------------------------------------------------------------- #
# The [since-watermark] lane
# --------------------------------------------------------------------------- #
def test_watermark_exempts_project_typed_and_reports_them(repo, memory_dir, monkeypatch):
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    c1 = _typed_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _episode_line(td, "last-sess", 100.0, c1)

    diagnostics = {}
    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td, diagnostics=diagnostics)
    names = {c["name"] for c in cands}
    assert names == {"m_user", "m_untyped"}
    assert diagnostics[SP.DIAG_TYPE_KEY] == ["m_proj"]


def test_evidence_drift_still_arms_an_exempted_project_memory(repo, memory_dir, monkeypatch):
    """CLB-3 composition holds for TYPE-1 exactly as for VOL-1: span-level quoted-evidence
    drift re-arms a policy-exempted memory — the fold runs AFTER the producer's filters."""
    from memory.staleness_evidence import fold_drift_candidates

    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    c1 = _typed_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _episode_line(td, "last-sess", 100.0, c1)

    wm = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    assert all(c["name"] != "m_proj" for c in wm)  # policy exempted the whole-file hit
    folded = fold_drift_candidates(
        wm, {"m_proj": {"fences": 1, "missing": 1, "whitespace": 0, "paths": ["src/foo.py"]}}
    )
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, since=_ALL, watermark_stale=folded
    )
    assert any(w["name"] == "m_proj" and w.get("evidence") for w in worklist)


# --------------------------------------------------------------------------- #
# The SessionStart staleness note
# --------------------------------------------------------------------------- #
def _stub_stale(monkeypatch, items):
    monkeypatch.setattr(SG, "find_stale", lambda md, repo, diagnostics=None: list(items))


def test_staleness_note_counts_type_exempt_tail(memory_dir, monkeypatch):
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    _stub_stale(
        monkeypatch,
        [
            {"name": "m_user", "type": "user", "changed_paths": ["src/foo.py"]},
            {"name": "m_proj", "type": "project", "changed_paths": ["src/foo.py"]},
            {"name": "m_proj2", "type": "project", "changed_paths": ["src/bar.py"]},
        ],
    )
    out = S.staleness_producer(memory_dir, "repo")
    assert "1 memories cite code" in out  # header counts ARMED entries only
    assert "m_user" in out
    assert not any(ln.strip().startswith("• m_proj") for ln in out.splitlines())
    assert "(+2" in out and "type-exempt" in out
    assert SP._EXEMPT_TYPES_ENV in out  # auditable override pointer


def test_staleness_note_all_type_exempt_renders_one_calm_line(memory_dir, monkeypatch):
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    _stub_stale(
        monkeypatch,
        [
            {"name": "m_proj", "type": "project", "changed_paths": ["src/foo.py"]},
            {"name": "m_proj2", "type": "project", "changed_paths": ["src/bar.py"]},
        ],
    )
    out = S.staleness_producer(memory_dir, "repo")
    assert out is not None  # suppression must never look like "nothing stale"
    assert "2" in out and "type" in out and "ℹ" in out
    assert "m_proj" not in out  # no per-item treadmill lines


def test_staleness_note_untyped_items_unchanged(memory_dir, monkeypatch):
    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    _stub_stale(monkeypatch, [{"name": "m_untyped", "changed_paths": ["src/foo.py"]}])
    out = S.staleness_producer(memory_dir, "repo")
    assert "m_untyped" in out and "type-exempt" not in out


# --------------------------------------------------------------------------- #
# The CLI and MCP worklist listings
# --------------------------------------------------------------------------- #
def test_reconsolidate_cli_prints_type_exempt_count(repo, memory_dir, capsys, monkeypatch):
    import time

    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    _typed_corpus(repo, memory_dir, t0=int(time.time()) - 7200)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_proj", "m_user", "m_untyped"])])

    rc = R.main(["--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m_user" in out
    assert not any(ln.strip().startswith("• m_proj") for ln in out.splitlines())
    assert "arming-exempt by type" in out


def test_consolidate_mcp_worklist_prints_type_exempt_count(repo, memory_dir, monkeypatch):
    import time

    from memory import mcp_tools_consolidate as MC

    monkeypatch.delenv(SP._EXEMPT_TYPES_ENV, raising=False)
    _typed_corpus(repo, memory_dir, t0=int(time.time()) - 7200)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_proj", "m_user", "m_untyped"])])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)

    out = MC._tool_reconsolidate({"action": "worklist"})
    assert "m_user" in out
    assert "arming-exempt by type" in out
