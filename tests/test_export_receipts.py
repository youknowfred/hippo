"""IOP-3 — the curated export receipt: an evidence ledger for AGENTS.md floor items.

Hermetic: a real scratch git repo + corpus floor; BM25-only. Pins the acceptance
criteria — (1) the receipt is a SEPARATE report that changes zero bytes of the proposed
AGENTS.md, (2) evidence comes from the shipped functions called verbatim, (3) a
thin/fresh-clone corpus renders "insufficient evidence" (inv3), never
indistinguishable-from-clean, (4) evidence stays display-only: export_agents never
reads a strength/staleness value (AST pin) and the hot path never imports the receipt.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess

from memory.build_index import default_index_dir
from memory.export_agents import export_agents
from memory.export_receipts import curation_receipt, describe_receipt
from memory.staleness import write_stale_cache

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "src"))
    for rel in ("src/a.py", "src/b.py"):
        with open(os.path.join(repo, rel), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"], check=True, env=_GIT_ENV)
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return repo, md


def _write_memory(md, name, body, *, cited=None, extra_meta=""):
    cited_line = ""
    if cited is not None:
        quoted = ", ".join(f'"{c}"' for c in cited)
        cited_line = f"  cited_paths: [{quoted}]\n  source_commit: abc123\n"
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {name}\ndescription: {name} description\nmetadata:\n"
            f"  type: feedback\n{cited_line}{extra_meta}---\n\n{body}\n"
        )


def _write_floor(md, names):
    lines = ["# Memory", "", "## Working Style & Process Feedback"]
    lines += [f"- [{n}]({n}.md) — hook" for n in names]
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _seed_sessions(td, n, names):
    """n distinct sessions, each recalling every name in ``names``."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"session_id": f"s{i}", "names": list(names), "backend": "bm25"}) + "\n")


# --------------------------------------------------------------------------- #
# AC 1 — separate report, zero AGENTS.md bytes, only via the explicit invocation
# --------------------------------------------------------------------------- #
def test_receipt_changes_nothing_and_proposed_bytes_are_identical(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    before = export_agents(memory_dir=md, repo_root=repo)
    listing = sorted(os.listdir(repo))
    r = curation_receipt(memory_dir=md, repo_root=repo)
    assert r["reason"] is None
    after = export_agents(memory_dir=md, repo_root=repo)
    assert before["proposed"] == after["proposed"]  # byte-identical proposal
    assert sorted(os.listdir(repo)) == listing      # receipt wrote nothing
    # the receipt carries evidence, never the proposal
    assert "proposed" not in r and "diff" not in r
    text = describe_receipt(r)
    assert "lint-first" in text
    assert "hippo:agents-export" not in text  # zero bytes of the AGENTS.md content


def test_receipt_passes_through_export_refusal(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_floor(md, [])
    r = curation_receipt(memory_dir=md, repo_root=repo)
    assert r["items"] == [] and "floor pins no memories" in r["reason"]
    assert "no curation receipt" in describe_receipt(r)


# --------------------------------------------------------------------------- #
# AC 3 / inv3 — thin corpus reads "insufficient evidence", never clean
# --------------------------------------------------------------------------- #
def test_thin_corpus_withholds_strength_below_the_soak_gate(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    r = curation_receipt(memory_dir=md, repo_root=repo)  # no telemetry at all
    assert r["gate_met"] is False
    assert r["items"][0]["strength"] is None  # withheld, not 0.0
    text = describe_receipt(r)
    assert "insufficient evidence" in text
    assert "0/5 distinct sessions" in text
    assert "strength: 0.0" not in text


def test_absent_stale_cache_renders_unknown_never_fresh(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    r = curation_receipt(memory_dir=md, repo_root=repo)
    assert r["staleness_cache"] is False
    text = describe_receipt(r)
    assert "staleness: unknown" in text
    assert "fresh at last scan" not in text


# --------------------------------------------------------------------------- #
# AC 2 — evidence via the shipped functions, wired end-to-end
# --------------------------------------------------------------------------- #
def test_mature_corpus_shows_strength_staleness_and_conflicts(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_memory(md, "old-way", "Use the legacy build.")
    _write_memory(md, "new-way", "Use the new build.\n\nSupersedes: [[old-way]].",
                  extra_meta="  supersedes: [old-way]\n")
    _write_floor(md, ["lint-first", "old-way"])
    # governance cites both floor memories -> conflict radar has a join to make
    with open(os.path.join(repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
        fh.write("Follow `lint-first` and `old-way`.\n")
    td = os.path.join(str(tmp_path), "telemetry")
    _seed_sessions(td, 6, ["lint-first"])  # gate met; lint-first strong, old-way never recalled
    idx = default_index_dir(md)
    assert write_stale_cache(idx, [
        {"name": "lint-first", "changed_paths": ["src/a.py"], "source_commit": "abc123def"},
    ])
    r = curation_receipt(memory_dir=md, repo_root=repo, telemetry_dir=td, index_dir=idx)
    assert r["gate_met"] is True and r["distinct_sessions"] == 6
    by_name = {it["name"]: it for it in r["items"]}
    assert by_name["lint-first"]["strength"] == 1.0
    assert by_name["lint-first"]["stale"] == {"changed": 1, "sha": "abc123d"}
    assert by_name["old-way"]["strength"] is None  # never recalled -> absent from scores
    # conflict radar: old-way is governance-cited AND superseded (typed leg) + a 0.0-strength gap
    kinds = {c["kind"] for c in by_name["old-way"]["conflicts"]}
    assert "edge_conflict" in kinds and "authority_gap" in kinds
    text = describe_receipt(r)
    assert "strength: 1.0" in text
    assert "STALE — 1 cited file(s) changed since abc123d" in text
    assert "new-way supersedes this memory" in text
    assert "strength: 0.0 (never recalled in any logged session)" in text


def test_skipped_floor_lines_are_named_with_reasons(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "live-one", "Keep.", cited=["src/a.py"])
    _write_memory(md, "retired", "Old.", extra_meta="  invalid_after: \"2026-01-01T00:00:00+00:00\"\n")
    _write_floor(md, ["live-one", "retired", "ghost"])
    r = curation_receipt(memory_dir=md, repo_root=repo)
    reasons = {s["name"]: s["reason"] for s in r["skipped"]}
    assert "retired" in reasons and "invalid_after" in reasons["retired"]
    assert "ghost" in reasons  # floor pointer without a readable file
    text = describe_receipt(r)
    assert "excluded: retired" in text and "excluded: ghost" in text


def test_prior_agents_rot_is_scoped_to_agents_md(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    r0 = curation_receipt(memory_dir=md, repo_root=repo)
    assert r0["agents_exists"] is False
    assert "no prior-block rot to check" in describe_receipt(r0)
    # a prior AGENTS.md with a dead paths: glob and a rotten backtick code ref
    with open(os.path.join(repo, "AGENTS.md"), "w", encoding="utf-8") as fh:
        fh.write('---\npaths:\n  - "nothing/matches/*.zzz"\n---\nsee `src/gone.py` here\n')
    r1 = curation_receipt(memory_dir=md, repo_root=repo)
    assert r1["agents_exists"] is True
    assert [g["glob"] for g in r1["prior_rot"]["dead_path_globs"]] == ["nothing/matches/*.zzz"]
    assert [c["ref"] for c in r1["prior_rot"]["code_ref_rot"]] == ["src/gone.py"]
    assert "dead paths: glob" in describe_receipt(r1)


# --------------------------------------------------------------------------- #
# AC 4 — evidence is display-only: selection can not read it (structural pins)
# --------------------------------------------------------------------------- #
def _module_source(name: str) -> str:
    import memory

    path = os.path.join(os.path.dirname(memory.__file__), name)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_export_agents_never_reads_receipt_evidence():
    """The AST pin: export_agents (the SELECTION) contains zero references to the
    evidence surface — strength, stale cache, rot, radar, or the receipt module —
    so receipt values can never become a selection/filtering/ranking input."""
    src = _module_source("export_agents.py")
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    for banned in ("compute_strength_scores", "read_stale_cache", "rules_rot",
                   "conflict_radar", "curation_receipt"):
        assert banned not in names, f"export_agents.py must not reference {banned}"
    assert "export_receipts" not in src


def test_hot_path_never_imports_the_receipt():
    """The receipt fires only via the explicit skill invocation — session_start (the
    producers) and the recall hot path never touch it, and doctor gained no check."""
    for mod in ("session_start.py", "session_start_health.py", "session_start_signals.py",
                "recall.py", "recall_tiers.py", "doctor.py", "doctor_checks_corpus.py",
                "doctor_checks_env.py", "doctor_checks_recall.py", "doctor_checks_lifecycle.py"):
        assert "export_receipts" not in _module_source(mod), mod
