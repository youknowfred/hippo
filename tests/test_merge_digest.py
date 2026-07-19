"""CLB-4: the incoming-merge duplicate digest — merge-time gets write-time's dedup.

Covers: the merge-signal + watermark-range gates (a purely-local session stays
silent; a merge that touches no memory stays silent), the GRW-3 detector reuse
(pairs surface with calibrated scores; cap ≤5), human routing (contradicts →
/hippo:resolve, everything else → /hippo:consolidate), the explicit degradation
line on an unreachable watermark (squash/rewrite — never silent nothing), the
SEC-6 trust-drift fallback stem source, read-only-ness (no autonomous edge
writes — corpus bytes identical), producer registration/adjacency, and the
doctor line that re-derives the SAME pairs (one detector, two surfaces).
"""

from __future__ import annotations

import hashlib
import json
import os

from .conftest import git_commit, write_file

from memory import merge_digest as MD
from memory.merge_digest import incoming_duplicate_pairs, merge_digest_producer


def _mem(name: str, desc: str, extra_fm: str = "") -> str:
    fm_extra = (extra_fm.rstrip("\n") + "\n") if extra_fm else ""
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{desc}"\n'
        f"{fm_extra}"
        "metadata:\n"
        "  type: project\n"
        "---\n"
        "body\n"
    )


_DESC = "zebra migration patterns in the savanna during seasonal drought cycles"


def _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch):
    """Seed a small realistic corpus + an episode (the watermark); return telemetry dir.

    The filler memories matter: normalized-BM25 dup scoring is honestly ``None`` on a
    degenerate 1-2 doc corpus (``_bm25_dup_scores``' documented posture), so the dup
    detector needs a corpus with real idf mass to score against.
    """
    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # BM25 dup scoring — hermetic
    write_file(repo, ".claude/memory/m-local.md", _mem("m-local", _DESC))
    for i in range(5):
        write_file(
            repo,
            f".claude/memory/filler-{i}.md",
            _mem(f"filler-{i}", f"distinct topic {i} about build tooling caches and release step {i}"),
        )
    git_commit(repo, "seed corpus", 1_700_000_000)
    from memory.telemetry import log_episode

    assert log_episode(["m-local"], query="zebra", repo_root=repo, telemetry_dir=td)
    return td


def _merge_in(repo, rel_path, text, when=1_700_000_100, msg="merge teammate memories (#7)"):
    """Land an 'incoming' commit whose subject trips the shared merge-signal probe."""
    write_file(repo, rel_path, text)
    return git_commit(repo, msg, when)


def _build(memory_dir):
    from memory.build_index import build_index, default_index_dir

    build_index(memory_dir, default_index_dir(memory_dir))


# --------------------------------------------------------------------------- #
# Gates: silent-normal cases
# --------------------------------------------------------------------------- #
def test_silent_without_merge_signals(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(repo, ".claude/memory/m-in.md", _mem("m-in", _DESC), msg="plain local commit")
    _build(memory_dir)
    assert merge_digest_producer(memory_dir, repo) is None


def test_silent_when_range_touches_no_memory(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(repo, "src/code.py", "x = 1\n")
    _build(memory_dir)
    assert merge_digest_producer(memory_dir, repo) is None


def test_silent_when_incoming_memory_is_no_duplicate(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(
        repo, ".claude/memory/m-novel.md",
        _mem("m-novel", "an entirely unrelated postgres connection pooling lesson"),
    )
    _build(memory_dir)
    assert merge_digest_producer(memory_dir, repo) is None


# --------------------------------------------------------------------------- #
# The firing path
# --------------------------------------------------------------------------- #
def test_incoming_duplicate_surfaces_one_routed_pair(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(repo, ".claude/memory/m-in.md", _mem("m-in", _DESC))
    _build(memory_dir)

    out = merge_digest_producer(memory_dir, repo)
    assert out is not None
    assert "🔀 Incoming-merge duplicate digest" in out
    assert "m-in ⇄ m-local" in out
    assert "/hippo:consolidate" in out and "GRW-3" in out
    assert "nothing merges or writes edges automatically" in out


def test_contradicts_pair_routes_to_resolve(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(
        repo, ".claude/memory/m-in.md", _mem("m-in", _DESC, extra_fm="contradicts: m-local")
    )
    _build(memory_dir)
    out = merge_digest_producer(memory_dir, repo)
    assert out is not None
    assert "m-in ⇄ m-local" in out and "/hippo:resolve" in out


def test_pairs_capped_at_five(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    for i in range(6):
        write_file(repo, f".claude/memory/m-in-{i}.md", _mem(f"m-in-{i}", _DESC))
    git_commit(repo, "merge teammate memories (#8)", 1_700_000_100)
    _build(memory_dir)
    pairs, _deg, _incoming = incoming_duplicate_pairs(
        memory_dir, repo, os.environ["HIPPO_TELEMETRY_DIR"]
    )
    assert len(pairs) == 5  # the spec's cap — bounded, never a full sweep


def test_incoming_archive_arrivals_are_skipped(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(repo, ".claude/memory/archive/m-old.md", _mem("m-old", _DESC))
    _build(memory_dir)
    assert merge_digest_producer(memory_dir, repo) is None


# --------------------------------------------------------------------------- #
# Degradation + fallback
# --------------------------------------------------------------------------- #
def test_unreachable_watermark_emits_explicit_degradation(repo, memory_dir, tmp_path, monkeypatch):
    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(repo, ".claude/memory/m-local.md", _mem("m-local", _DESC))
    git_commit(repo, "merge teammate memories (#9)", 1_700_000_000)
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": 1.0, "session_id": "s1", "query_preview": "q",
            "recalled_names": [], "head_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        }) + "\n")
    out = merge_digest_producer(memory_dir, repo)
    assert out is not None
    assert "unreachable" in out and "squash" in out
    assert "NOT dup-checked" in out  # legible: the signal was lost, not clean


def test_trust_drift_fallback_supplies_stems(repo, memory_dir, tmp_path, monkeypatch):
    """No watermark at all (fresh telemetry) + a TRUSTED corpus whose consent baseline
    drifted -> the SEC-6 delta is the stem source (the spec's fallback leg)."""
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "empty-telemetry"))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)  # exercise the real gate
    write_file(repo, ".claude/memory/m-local.md", _mem("m-local", _DESC))
    git_commit(repo, "seed corpus", 1_700_000_000)
    from memory import trust

    trust.mark_trusted(repo, memory_dir)
    # drift: a new memory lands AFTER consent (e.g. pulled in) — no episode watermark
    write_file(repo, ".claude/memory/m-in.md", _mem("m-in", _DESC))
    git_commit(repo, "merge teammate memories (#10)", 1_700_000_100)
    _build(memory_dir)
    stems, degradation = MD._incoming_memory_stems(
        memory_dir, repo, str(tmp_path / "empty-telemetry")
    )
    assert degradation is None
    assert "m-in" in stems


# --------------------------------------------------------------------------- #
# Read-only + registration
# --------------------------------------------------------------------------- #
def _corpus_digest(memory_dir):
    out = {}
    for root, _dirs, files in os.walk(memory_dir):
        for f in sorted(files):
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


def test_producer_never_writes_corpus_or_edges(repo, memory_dir, tmp_path, monkeypatch):
    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(repo, ".claude/memory/m-in.md", _mem("m-in", _DESC))
    _build(memory_dir)
    before = _corpus_digest(memory_dir)
    assert merge_digest_producer(memory_dir, repo) is not None
    assert _corpus_digest(memory_dir) == before  # inv4: report, never act


def test_producer_registered_once_after_floor_change():
    from memory import session_start as S

    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("merge_digest") == 1
    assert labels.index("merge_digest") == labels.index("floor_change") + 1
    fns = [fn for label, fn in S.PRODUCERS if label == "merge_digest"]
    assert fns == [merge_digest_producer]


# --------------------------------------------------------------------------- #
# Doctor — same derivation, one line
# --------------------------------------------------------------------------- #
def test_doctor_merge_digest_ok_when_clean(repo, memory_dir, tmp_path, monkeypatch):
    from memory.doctor import CHECKS
    from memory.doctor_checks_lifecycle import check_merge_digest
    from memory.doctor_checks_env import DoctorContext

    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    r = check_merge_digest(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "no duplicate pairs" in r["message"]
    assert "\n" not in r["message"]
    labels = [label for label, _fn in CHECKS]
    assert labels.count("merge_digest") == 1
    assert labels[-1] == "stale_memobot_env"


def test_doctor_merge_digest_warns_with_routed_pairs(repo, memory_dir, tmp_path, monkeypatch):
    from memory.doctor_checks_lifecycle import check_merge_digest
    from memory.doctor_checks_env import DoctorContext

    _seed_with_watermark(repo, memory_dir, tmp_path, monkeypatch)
    _merge_in(repo, ".claude/memory/m-in.md", _mem("m-in", _DESC))
    _build(memory_dir)
    r = check_merge_digest(DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"
    assert "m-in ⇄ m-local" in r["message"]
    assert "/hippo:consolidate" in r["message"]
    assert "\n" not in r["message"]
    assert check_merge_digest(DoctorContext(memory_dir, repo)) == r  # deterministic
