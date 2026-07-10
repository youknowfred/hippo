"""Tests for memory/trust.py — the SEC-1 foreign-corpus trust gate.

Hermetic: the trust registry is pointed at a tmp file via HIPPO_TRUST_FILE so the real
~/.claude/hippo-trust.json is NEVER touched. Corpora that must be gated live inside a real
git repo (the conftest `repo`/`memory_dir` fixtures) so `git_root` resolves a repo_root for
the gate to key on. SEC-12: a non-git directory that carries an actual corpus is ALSO gated
(the "Download ZIP" twin of the clone attack); only an EMPTY non-git dir stays a no-op.
"""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import recall as R
from memory import session_start as S
from memory import trust as T


def _mem(name: str, description: str, body: str = "body") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


_CORPUS = {
    "reranker_voyage.md": "voyage rerank cross encoder is the primary reranker bm25 hybrid fallback",
    "budget_envelope.md": "phase envelope budget authority guards the synthesis tail reservation",
    "excel_header.md": "excel parser llm header rescue for non canonical column layouts",
}


def _write_corpus(memory_dir: str, items: dict = _CORPUS) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for fname, desc in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))


def _point_registry(monkeypatch, tmp_path):
    reg = str(tmp_path / "hippo-trust.json")
    monkeypatch.setenv("HIPPO_TRUST_FILE", reg)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    return reg


# --------------------------------------------------------------------------- #
# Acceptance criterion 1: freshly-cloned foreign corpus injects nothing until trusted
# --------------------------------------------------------------------------- #
def test_untrusted_git_corpus_recall_returns_empty(repo, memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)
    idx = str(tmp_path / "idx")
    _write_corpus(memory_dir)
    B.build_index(memory_dir, idx)

    # The index has real matching content, but the corpus's repo_root is NOT in the registry.
    assert R.recall("which reranker do we use", k=5, memory_dir=memory_dir, index_dir=idx) == []


# --------------------------------------------------------------------------- #
# SEC-1 (opportunistic, COR-8 release): main() must leave ZERO telemetry trace for an
# untrusted corpus. The trust gate INSIDE recall() already returns [] for it; before this
# fix, main() still appended a backend="none" ledger line on top of the empty result --
# even a "found nothing" line is itself a trace that a foreign, unreviewed corpus was
# queried. Gate main()'s telemetry/episode block on the same trust condition (reusing the
# already-resolved repo_root -- no extra git call).
# --------------------------------------------------------------------------- #
def test_untrusted_git_corpus_recall_main_writes_no_telemetry(repo, memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)
    idx = str(tmp_path / "idx")
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    _write_corpus(memory_dir)
    B.build_index(memory_dir, idx)

    # repo_root is NOT in the registry -> untrusted; recall.main() is the hook/CLI entry.
    rc = R.main(["which", "reranker", "do", "we", "use", "--memory-dir", memory_dir,
                 "--index-dir", idx, "--repo-root", repo])
    assert rc == 0
    import memory.telemetry as telemetry

    assert not os.path.exists(telemetry._ledger_path(td))  # zero recall-ledger trace
    assert not os.path.exists(telemetry._episode_ledger_path(td))  # zero episode-buffer trace


# --------------------------------------------------------------------------- #
# Acceptance criterion 2: after trusting the repo_root, the SAME corpus recalls for real
# --------------------------------------------------------------------------- #
def test_trusted_git_corpus_recall_returns_results(repo, memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)
    idx = str(tmp_path / "idx")
    _write_corpus(memory_dir)
    B.build_index(memory_dir, idx)

    assert T.mark_trusted(repo) is True
    res = R.recall("which reranker do we use", k=5, memory_dir=memory_dir, index_dir=idx)
    names = [r["name"] for r in res]
    assert "reranker_voyage" in names


# --------------------------------------------------------------------------- #
# Acceptance criterion 3: HIPPO_TRUST_ALL bypasses the gate regardless of registry state
# --------------------------------------------------------------------------- #
def test_trust_all_env_bypasses_gate(repo, memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)  # registry empty -> would deny
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    idx = str(tmp_path / "idx")
    _write_corpus(memory_dir)
    B.build_index(memory_dir, idx)

    res = R.recall("which reranker do we use", k=5, memory_dir=memory_dir, index_dir=idx)
    assert any(r["name"] == "reranker_voyage" for r in res)


# --------------------------------------------------------------------------- #
# Acceptance criterion 4: init's corpus-creation path (mark_trusted) writes the marker
# --------------------------------------------------------------------------- #
def test_mark_trusted_writes_registry_marker(repo, tmp_path, monkeypatch):
    reg = _point_registry(monkeypatch, tmp_path)
    assert T.is_trusted(repo) is False
    assert T.mark_trusted(repo) is True
    assert T.is_trusted(repo) is True

    with open(reg, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    assert os.path.realpath(repo) in doc["trusted"]
    assert "trusted_at" in doc["trusted"][os.path.realpath(repo)]


def test_mark_trusted_is_idempotent_and_preserves_siblings(repo, tmp_path, monkeypatch):
    reg = _point_registry(monkeypatch, tmp_path)
    other = str(tmp_path / "other-repo")
    assert T.mark_trusted(other) is True
    assert T.mark_trusted(repo) is True
    assert T.mark_trusted(repo) is True  # idempotent second write

    with open(reg, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    # The first (sibling) key survived the later writes.
    assert os.path.realpath(other) in doc["trusted"]
    assert os.path.realpath(repo) in doc["trusted"]


# --------------------------------------------------------------------------- #
# gate_repo_root + SEC-12: a NON-git corpus WITH content is gated (untrusted by default);
# an empty non-git dir stays inapplicable; env/init/consent are the overrides.
# --------------------------------------------------------------------------- #
def test_non_git_corpus_with_content_is_untrusted_by_default(tmp_path, monkeypatch):
    """SEC-12: an extracted ('Download ZIP') non-git corpus injects nothing until trusted."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)  # deletes HIPPO_TRUST_ALL
    md = str(tmp_path / "memory")  # a bare tmp dir — NOT inside any git repo
    idx = str(tmp_path / "idx")
    _write_corpus(md)
    B.build_index(md, idx)

    # A real non-git corpus now resolves a gate key (its own real root) instead of None...
    assert T.gate_repo_root(md) == os.path.realpath(md)
    # ...and recall injects NOTHING because that key isn't trusted.
    assert R.recall("which reranker do we use", k=5, memory_dir=md, index_dir=idx) == []


def test_non_git_corpus_injects_after_consent(tmp_path, monkeypatch):
    """The 'init override': marking the non-git corpus trusted (what /hippo:init does) restores it."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / "idx")
    _write_corpus(md)
    B.build_index(md, idx)

    assert T.mark_trusted(md) is True  # init/doctor consent keys on the same real root
    res = R.recall("which reranker do we use", k=5, memory_dir=md, index_dir=idx)
    assert any(r["name"] == "reranker_voyage" for r in res)


def test_non_git_corpus_env_override_restores_old_behavior(tmp_path, monkeypatch):
    """The 'env override': HIPPO_TRUST_NONGIT makes a non-git corpus inapplicable again."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("HIPPO_TRUST_NONGIT", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / "idx")
    _write_corpus(md)
    B.build_index(md, idx)

    assert T.gate_repo_root(md) is None  # opt-out → inapplicable
    res = R.recall("which reranker do we use", k=5, memory_dir=md, index_dir=idx)
    assert any(r["name"] == "reranker_voyage" for r in res)


def test_empty_non_git_dir_stays_inapplicable(tmp_path, monkeypatch):
    """An empty non-git dir (resolve_dirs' fallback / a hermetic path) has nothing to gate."""
    _point_registry(monkeypatch, tmp_path)
    empty = str(tmp_path / "empty-project")
    os.makedirs(empty)
    assert T.gate_repo_root(empty) is None
    assert T.gate_repo_root(empty, empty) is None


def test_non_git_untrusted_corpus_nudge_is_not_silent(tmp_path, monkeypatch):
    """inv3 legibility: the untrusted-corpus nudge fires for a gated non-git corpus."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _point_registry(monkeypatch, tmp_path)
    md = str(tmp_path / "memory")
    _write_corpus(md)
    # The nudge's low-frequency modulo fires on the first unseen root (count 0 -> due).
    msg = S.untrusted_corpus_nudge(md, md)
    assert msg is not None and "UNTRUSTED" in msg


def test_gate_resolves_git_toplevel_not_passed_path_blind(repo, memory_dir, monkeypatch, tmp_path):
    from memory.provenance import git_root

    _point_registry(monkeypatch, tmp_path)
    top = git_root(repo)
    # Always resolved through git_root — never the passed path taken blind.
    assert T.gate_repo_root(memory_dir, None) == top
    assert T.gate_repo_root(memory_dir, repo) == top
    assert T.gate_repo_root(None, repo) == top
    # A non-git fallback path (what resolve_dirs hands back for a non-git project) -> None.
    non_git = str(tmp_path / "not-a-git-repo")
    os.makedirs(non_git)
    assert T.gate_repo_root(non_git, non_git) is None


# --------------------------------------------------------------------------- #
# is_trusted fail-closed posture
# --------------------------------------------------------------------------- #
def test_is_trusted_fails_closed_on_missing_registry(repo, tmp_path, monkeypatch):
    _point_registry(monkeypatch, tmp_path)  # file does not exist yet
    assert T.is_trusted(repo) is False


def test_is_trusted_fails_closed_on_corrupt_registry(repo, tmp_path, monkeypatch):
    reg = _point_registry(monkeypatch, tmp_path)
    with open(reg, "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json ]")
    assert T.is_trusted(repo) is False


def test_is_trusted_falsy_repo_root_is_untrusted(tmp_path, monkeypatch):
    _point_registry(monkeypatch, tmp_path)
    assert T.is_trusted(None) is False
    assert T.is_trusted("") is False


# --------------------------------------------------------------------------- #
# corpus_count / corpus_sample: what the consent prompt shows (names, not bodies)
# --------------------------------------------------------------------------- #
def test_corpus_count_and_sample(memory_dir):
    _write_corpus(memory_dir)
    assert T.corpus_count(memory_dir) == len(_CORPUS)
    sample = T.corpus_sample(memory_dir, limit=2)
    assert len(sample) == 2
    assert all(name in {fname[:-3] for fname in _CORPUS} for name in sample)
    # MEMORY.md floor is excluded from both.
    with open(os.path.join(memory_dir, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# floor\n")
    assert T.corpus_count(memory_dir) == len(_CORPUS)
    assert "MEMORY" not in T.corpus_sample(memory_dir, limit=99)


# --------------------------------------------------------------------------- #
# SessionStart: untrusted corpus -> only the nudge; producers suppressed. Trusted -> normal.
# --------------------------------------------------------------------------- #
def test_build_context_untrusted_emits_only_nudge(repo, memory_dir, tmp_path, monkeypatch):
    _point_registry(monkeypatch, tmp_path)
    _write_corpus(memory_dir)
    ctx = S.build_context(memory_dir, repo)
    assert "UNTRUSTED" in ctx
    assert "/hippo:doctor" in ctx
    assert str(len(_CORPUS)) in ctx  # the memory count is shown


def test_build_context_trusted_runs_producers_normally(repo, memory_dir, tmp_path, monkeypatch):
    _point_registry(monkeypatch, tmp_path)
    _write_corpus(memory_dir)
    T.mark_trusted(repo)
    ctx = S.build_context(memory_dir, repo)
    # Trusted -> the nudge is gone; normal producers run (may be empty, but never the nudge).
    assert "UNTRUSTED" not in ctx


def test_untrusted_nudge_is_low_frequency(repo, memory_dir, tmp_path, monkeypatch):
    _point_registry(monkeypatch, tmp_path)
    data_dir = str(tmp_path / "plugin-data")
    os.makedirs(data_dir)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data_dir)
    _write_corpus(memory_dir)

    # First eligible session fires; the next few are suppressed by the modulo (NUDGE_EVERY=5).
    fired = [bool(S.untrusted_corpus_nudge(memory_dir, repo)) for _ in range(6)]
    assert fired[0] is True
    assert fired[1:5] == [False, False, False, False]
    assert fired[5] is True


def test_untrusted_nudge_silent_when_trusted(repo, memory_dir, tmp_path, monkeypatch):
    _point_registry(monkeypatch, tmp_path)
    _write_corpus(memory_dir)
    T.mark_trusted(repo)
    assert S.untrusted_corpus_nudge(memory_dir, repo) is None
