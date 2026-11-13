"""TND-3: derived evidence anchors (report-only).

An anchor is an identifier the memory's own text names that also occurs in the cited file
at the memory's baseline. A drifted cited path is intact when it has an anchor, no added or
removed line touches one, and every anchor still occurs at HEAD; a memory is quiet when
every drifted path is intact. The replay over real verdicts missed the gate, so quiet
changes NO arming surface: doctor counts it and ``hippo staleness --anchors`` lists it.

Hermetic: a throwaway git repo per test with pinned commit times, fixtures written here.
"""

from __future__ import annotations

import os
import time

import pytest

from memory import doctor as D
from memory import staleness_anchors as A
from memory import staleness_policy as SP
from memory.staleness import find_stale
from memory.staleness import main as staleness_main

from .conftest import git_commit, write_file

# Recent, pinned per run: find_stale's default window is wall-clock relative.
T0 = int(time.time()) - 100_000

_SRC_V1 = (
    "def find_stale(memory_dir):\n"
    "    return scan_paths(memory_dir)\n"
    "\n"
    "def scan_paths(memory_dir):\n"
    "    return []\n"
    "\n"
    "def unrelated_helper():\n"
    "    return 1\n"
)


def _mem(name, cited, sc, body, mtype=None):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    t = f"type: {mtype}\n" if mtype else ""
    return (
        f'---\nname: {name}\ndescription: "{name} note"\n{t}cited_paths: {cp}\n'
        f'source_commit: "{sc}"\n---\n{body}\n'
    )


def _base(repo):
    write_file(repo, "src/engine.py", _SRC_V1)
    write_file(repo, "README.md", "readme\n")
    return git_commit(repo, "base", T0)


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def test_candidates_are_the_tokens_the_memory_writes_as_code():
    text = _mem(
        "m", ["src/engine.py"], "abc",
        "`find_stale` walks `staleness.scan_paths(memory_dir)`; RunContext_in_prose carries "
        "it. Short `id`, a sha `0489fe3` and words outside backticks do not count.",
    )
    c = A.candidate_identifiers(text)
    assert c == ["find_stale", "memory_dir", "scan_paths", "staleness"]


def test_frontmatter_binding_is_not_a_claim():
    # cited_paths/source_commit keys are binding, not what the memory says
    text = _mem("m", ["src/engine.py"], "abc", "nothing specific here")
    assert A.candidate_identifiers(text) == []


def test_the_derivation_carries_a_version():
    assert isinstance(A.ANCHOR_DERIVATION_VERSION, int) and A.ANCHOR_DERIVATION_VERSION >= 1
    v = A.judge_memory(_mem("m", ["a"], "s", "`find_stale`"), {"a": (_SRC_V1, _SRC_V1 + "\n")})
    assert v["version"] == A.ANCHOR_DERIVATION_VERSION


# --------------------------------------------------------------------------- #
# Judgement over contents (pure)
# --------------------------------------------------------------------------- #
CANDS = ["find_stale", "scan_paths"]


def test_change_elsewhere_in_the_file_is_intact():
    new = _SRC_V1.replace("return 1", "return 2")
    assert A.judge_path(CANDS, _SRC_V1, new)["status"] == "intact"


def test_a_change_line_naming_an_anchor_is_touched_with_evidence():
    new = _SRC_V1.replace("return scan_paths(memory_dir)", "return scan_paths(memory_dir, deep=True)")
    r = A.judge_path(CANDS, _SRC_V1, new)
    assert r["status"] == "anchor_touched"
    assert any(e.startswith("+") and "deep=True" in e for e in r["evidence"])
    assert any(e.startswith("-") for e in r["evidence"])


def test_an_anchor_gone_at_head_is_touched():
    new = _SRC_V1.replace("find_stale", "find_drift")
    r = A.judge_path(CANDS, _SRC_V1, new)
    assert r["status"] == "anchor_touched"


def test_a_moved_line_is_not_a_change():
    lines = _SRC_V1.split("\n")
    moved = "\n".join(lines[6:8] + [""] + lines[0:6]) + "\n"  # helper block moved to the top
    assert A.judge_path(CANDS, _SRC_V1, moved)["status"] == "intact"


@pytest.mark.parametrize(
    "old,new,status",
    [
        (_SRC_V1, None, "deleted"),
        (None, _SRC_V1, "absent_at_baseline"),
        ("bin\0ary", "bin\0ary2", "binary"),
        ("no identifiers here\n", "still none\n", "no_anchor"),
    ],
)
def test_never_intact_without_evidence(old, new, status):
    assert A.judge_path(CANDS, old, new)["status"] == status


def test_net_unchanged_content_is_intact():
    r = A.judge_path(CANDS, _SRC_V1, _SRC_V1)
    assert r["status"] == "intact" and r.get("unchanged")


def test_quiet_needs_every_drifted_path_intact():
    text = _mem("m", ["a", "b"], "s", "`find_stale` and `scan_paths`")
    ok = (_SRC_V1, _SRC_V1.replace("return 1", "return 2"))
    bad = (_SRC_V1, None)
    assert A.judge_memory(text, {"a": ok})["quiet"] is True
    assert A.judge_memory(text, {"a": ok, "b": bad})["quiet"] is False
    assert A.judge_memory(text, {})["quiet"] is False


# --------------------------------------------------------------------------- #
# Against a real (synthetic) git history
# --------------------------------------------------------------------------- #
def _corpus(repo, memory_dir):
    s = _base(repo)
    write_file(memory_dir, "quiet-one.md", _mem("quiet-one", ["src/engine.py"], s, "`find_stale` calls `scan_paths`."))
    write_file(memory_dir, "touched-one.md", _mem("touched-one", ["src/engine.py"], s, "`unrelated_helper` returns one."))
    write_file(memory_dir, "vague-one.md", _mem("vague-one", ["src/engine.py"], s, "The engine does things."))
    git_commit(repo, "memories", T0 + 10)
    write_file(repo, "src/engine.py", _SRC_V1.replace("def unrelated_helper():", "def unrelated_helper(flag):"))
    git_commit(repo, "edit the helper", T0 + 100)
    return s


def test_batch_verdicts_over_a_real_history(repo, memory_dir):
    _corpus(repo, memory_dir)
    stale = find_stale(memory_dir, repo, since="2000-01-01")
    assert {i["name"] for i in stale} == {"quiet-one", "touched-one", "vague-one"}
    by = {v["name"]: v for v in A.anchor_verdicts(memory_dir, stale, repo_root=repo)}
    assert by["quiet-one"]["quiet"] is True and by["quiet-one"]["reason"] is None
    assert by["quiet-one"]["paths"]["src/engine.py"]["anchors"] == ["find_stale", "scan_paths"]
    assert by["touched-one"]["paths"]["src/engine.py"]["anchors"] == ["unrelated_helper"]
    assert by["touched-one"]["quiet"] is False and by["touched-one"]["reason"] == "anchor_touched"
    assert by["vague-one"]["quiet"] is False and by["vague-one"]["reason"] == "no_anchor"


def test_one_memory_verdict_needs_no_staleness_producer(repo, memory_dir):
    _corpus(repo, memory_dir)
    v = A.memory_anchor_verdict(memory_dir, "quiet-one")
    assert v["quiet"] is True and list(v["paths"]) == ["src/engine.py"]
    assert A.memory_anchor_verdict(memory_dir, "missing-memory")["reason"] == "unreadable"


def test_deleted_or_renamed_cited_path_is_never_quiet(repo, memory_dir):
    _corpus(repo, memory_dir)
    os.rename(os.path.join(repo, "src", "engine.py"), os.path.join(repo, "src", "engine2.py"))
    git_commit(repo, "rename", T0 + 200)
    v = A.memory_anchor_verdict(memory_dir, "quiet-one")
    assert v["quiet"] is False and v["paths"]["src/engine.py"]["status"] == "deleted"


def test_an_unresolvable_baseline_is_never_quiet(repo, memory_dir):
    _base(repo)
    write_file(memory_dir, "m.md", _mem("m", ["src/engine.py"], "0" * 40, "`find_stale`"))
    git_commit(repo, "m", T0 + 10)
    v = A.anchor_verdicts(memory_dir, [{"name": "m", "changed_paths": ["src/engine.py"]}], repo_root=repo)[0]
    assert v["quiet"] is False and v["paths"]["src/engine.py"]["status"] == "absent_at_baseline"


def test_synthetic_verdict_replay(repo, memory_dir):
    """The replay harness's shape, on a history written here: each verdict is judged at the
    commit it was made at (``head``), from the memory text the human judged."""
    s = _corpus(repo, memory_dir)
    h1 = git_commit(repo, "verdict point one", T0 + 150)
    write_file(repo, "src/engine.py", _SRC_V1.replace("find_stale", "find_drift"))
    h2 = git_commit(repo, "rename the function", T0 + 300)
    ledger = [  # (memory, verdict, HEAD at the verdict)
        ("quiet-one", "graduate", h1),  # helper edit only: anchors intact
        ("quiet-one", "fix", h2),  # the named function was renamed: anchors moved
    ]
    got = [
        (oc, A.memory_anchor_verdict(memory_dir, name, head=head)["quiet"]) for name, oc, head in ledger
    ]
    assert got == [("graduate", True), ("fix", False)]
    graduates_quiet = sum(q for oc, q in got if oc == "graduate")
    fixes_suppressed = sum(q for oc, q in got if oc != "graduate")
    assert (graduates_quiet, fixes_suppressed) == (1, 0)
    assert s != h1


# --------------------------------------------------------------------------- #
# Report-only: arming is untouched; the report surfaces count and list
# --------------------------------------------------------------------------- #
def _snapshot(root):
    out = {}
    for d, _subs, files in os.walk(root):
        if ".git" in d.split(os.sep):
            continue
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[p] = fh.read()
    return out


def test_report_only_leaves_every_arming_partition_unchanged(repo, memory_dir):
    _corpus(repo, memory_dir)
    assert A.QUIET_ARMING_GATE_MET is False and A.quiet_arming_active() is False
    stale = find_stale(memory_dir, repo, since="2000-01-01")
    armed, vol, typ = SP.arming_partition(memory_dir, stale)
    assert {i["name"] for i in armed} == {"quiet-one", "touched-one", "vague-one"}
    assert SP.armed_stale_names(memory_dir, stale) == ["quiet-one", "touched-one", "vague-one"]


def test_doctor_line_counts_would_be_quiet_and_writes_nothing(repo, memory_dir):
    _corpus(repo, memory_dir)
    before = _snapshot(repo)
    r = A.doctor_line(memory_dir, repo)
    assert r["status"] == "ok"
    assert "1 of 3 armed stale memories would be quiet" in r["message"]
    assert "hippo staleness --anchors" in r["message"] and "report-only" in r["message"]
    assert _snapshot(repo) == before  # zero corpus writes, zero derived writes


def test_doctor_registers_the_line_once():
    assert [label for label, _ in D.CHECKS].count("evidence_anchors") == 1


def test_cli_lists_quiet_first_with_anchor_evidence(repo, memory_dir, capsys):
    _corpus(repo, memory_dir)
    assert staleness_main(["--anchors", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert "1 of 3 armed stale memories would be quiet" in out
    lines = out.splitlines()
    first = next(i for i, l in enumerate(lines) if l.strip().startswith(("quiet", "armed")))
    assert lines[first].split() == ["quiet", "quiet-one"]
    assert "anchors: find_stale, scan_paths" in out
    assert "+def unrelated_helper(flag):" in out  # touched-one's evidence line


def test_override_zero_turns_anchors_off(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir)
    monkeypatch.setenv("HIPPO_QUIET_ARMING", "0")
    assert "off (HIPPO_QUIET_ARMING=0)" in A.doctor_line(memory_dir, repo)["message"]
    staleness_main(["--anchors", "--memory-dir", memory_dir, "--repo-root", repo])
    assert "off" in capsys.readouterr().out


def test_type_exempt_memories_are_outside_the_armed_set(repo, memory_dir):
    s = _base(repo)
    write_file(memory_dir, "p.md", _mem("p", ["src/engine.py"], s, "`find_stale`", mtype="project"))
    git_commit(repo, "m", T0 + 10)
    write_file(repo, "src/engine.py", _SRC_V1 + "\n# tail\n")
    git_commit(repo, "edit", T0 + 100)
    verdicts, detected = A.armed_verdicts(memory_dir, repo)
    assert detected == 1 and verdicts == []
