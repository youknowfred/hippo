"""Tests for memory/doctor.py — the DOC-4 deterministic doctor engine.

Each check is exercised DIRECTLY against a hermetic ``DoctorContext`` (repo/memory_dir
fixtures + write_file/git_commit helpers), asserting stable status/message for a known input
state. The engine's contract is DETERMINISM: the same context maps to byte-identical output, so
several tests assert ``render()`` twice against identical state and compare the two runs.

Trust checks delete the autouse ``HIPPO_TRUST_ALL`` (set open by conftest) to drive the real
deny/allow gate, mirroring tests/test_trust.py.
"""

from __future__ import annotations

import json
import os

import numpy as np

from memory import build_index as B
from memory import doctor as D
from memory import provenance as P

from .conftest import git_commit, write_file


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _mem(name: str, description: str, body: str = "body text") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def _seed(memory_dir: str, *, floor: bool = True) -> None:
    if floor:
        write_file(memory_dir, "MEMORY.md", "# floor\n")


def _ctx(memory_dir: str, repo_root: str, **kw) -> D.DoctorContext:
    # Default plugin_data/root to "" so env-dependent checks are hermetic and don't read the
    # developer's real install (the bootstrap/venv checks then report their unset-data path).
    kw.setdefault("plugin_data", "")
    kw.setdefault("plugin_root", "")
    return D.DoctorContext(memory_dir, repo_root, **kw)


def _fake_embedder(dim: int = 16):
    def _vec(text: str):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[hash(tok) % dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(texts, allow_download=True):
        return np.vstack([_vec(t) for t in texts]).astype("float32")

    return embed_documents


# --------------------------------------------------------------------------- #
# The literal acceptance criterion: identical state -> identical output
# --------------------------------------------------------------------------- #
def test_render_is_deterministic_across_runs(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    write_file(memory_dir, "b.md", _mem("b", "beta"))
    git_commit(repo, "seed", 1_700_000_000)
    ctx = _ctx(memory_dir, repo)
    first = D.render(ctx)
    second = D.render(ctx)
    assert first == second
    # One line per registered check, in the fixed CHECKS order.
    assert first.count("\n") + 1 == len(D.CHECKS)


def test_run_checks_order_is_fixed(repo, memory_dir):
    _seed(memory_dir)
    ctx = _ctx(memory_dir, repo)
    labels = [label for label, _ in D.run_checks(ctx)]
    assert labels == [label for label, _ in D.CHECKS]
    # Sanity: the order is a real ordered list, not derived from a set/dict view.
    # DOC-8 appended "stale_memobot_env" as the new last check (an environment-hygiene check,
    # deliberately last so it never shifts the corpus-content checks' relative order).
    assert labels[0] == "bootstrap" and labels[-1] == "stale_memobot_env"


def test_every_line_has_a_status_glyph(repo, memory_dir):
    _seed(memory_dir)
    git_commit(repo, "seed", 1_700_000_000)
    for line in D.render(_ctx(memory_dir, repo)).split("\n"):
        assert line[0] in ("✔", "⚠", "✘"), line


# --------------------------------------------------------------------------- #
# Bootstrap / venv (canonical sentinel-hash compare, shared with session_start)
# --------------------------------------------------------------------------- #
def _plugin_env(tmp_path, *, req_text: str, sentinel_hash):
    import hashlib

    data_dir = tmp_path / "plugin-data"
    plugin_root = tmp_path / "plugin-root"
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(plugin_root, exist_ok=True)
    (plugin_root / "requirements.txt").write_text(req_text, encoding="utf-8")
    if sentinel_hash == "current":
        sentinel_hash = hashlib.sha256(req_text.encode()).hexdigest()
    if sentinel_hash is not None:
        (data_dir / ".bootstrap-sentinel").write_text(
            json.dumps({"requirements_hash": sentinel_hash}), encoding="utf-8"
        )
    return str(data_dir), str(plugin_root)


def test_bootstrap_check_warns_when_data_dir_unset(repo, memory_dir):
    r = D.check_bootstrap(_ctx(memory_dir, repo, plugin_data="", plugin_root=""))
    assert r["status"] == "warn" and "CLAUDE_PLUGIN_DATA is unset" in r["message"]


def test_bootstrap_check_fails_when_not_bootstrapped(repo, memory_dir, tmp_path):
    data, root = _plugin_env(tmp_path, req_text="numpy>=2\n", sentinel_hash=None)
    r = D.check_bootstrap(_ctx(memory_dir, repo, plugin_data=data, plugin_root=root))
    assert r["status"] == "fail" and "not bootstrapped" in r["message"]


def test_bootstrap_check_fails_on_stale_deps(repo, memory_dir, tmp_path):
    data, root = _plugin_env(tmp_path, req_text="numpy>=2\n", sentinel_hash="0" * 64)
    r = D.check_bootstrap(_ctx(memory_dir, repo, plugin_data=data, plugin_root=root))
    assert r["status"] == "fail" and "STALE" in r["message"]


def test_bootstrap_check_ok_when_current(repo, memory_dir, tmp_path):
    data, root = _plugin_env(tmp_path, req_text="numpy>=2\n", sentinel_hash="current")
    r = D.check_bootstrap(_ctx(memory_dir, repo, plugin_data=data, plugin_root=root))
    assert r["status"] == "ok" and "deps current" in r["message"]


def test_bootstrap_check_matches_session_start_bootstrap_state(repo, memory_dir, tmp_path):
    """Doctor's bootstrap line and the SessionStart nudge read the SAME canonical state."""
    from memory.session_start import bootstrap_state

    data, root = _plugin_env(tmp_path, req_text="numpy>=2\n", sentinel_hash="0" * 64)
    assert bootstrap_state(data, root) == "stale"
    r = D.check_bootstrap(_ctx(memory_dir, repo, plugin_data=data, plugin_root=root))
    assert r["status"] == "fail"


def test_venv_check_skips_when_not_bootstrapped(repo, memory_dir, tmp_path):
    data, root = _plugin_env(tmp_path, req_text="numpy>=2\n", sentinel_hash=None)
    r = D.check_venv(_ctx(memory_dir, repo, plugin_data=data, plugin_root=root))
    assert r["status"] == "warn" and "not bootstrapped" in r["message"]


# --------------------------------------------------------------------------- #
# Corpus existence + resolution
# --------------------------------------------------------------------------- #
def test_corpus_check_ok_with_floor(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    r = D.check_corpus_exists(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "1 memories" in r["message"]


def test_corpus_check_fails_when_absent(repo, tmp_path):
    missing = str(tmp_path / "nope" / ".claude" / "memory")
    r = D.check_corpus_exists(_ctx(missing, repo))
    assert r["status"] == "fail" and "/hippo:init" in r["message"]


# --------------------------------------------------------------------------- #
# git degraded mode (SHP-4)
# --------------------------------------------------------------------------- #
def test_git_mode_ok_in_a_repo(repo, memory_dir):
    r = D.check_git_mode(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "git repo detected" in r["message"]


def test_git_mode_warns_outside_git(tmp_path):
    plain = str(tmp_path / "plain")
    md = os.path.join(plain, ".claude", "memory")
    os.makedirs(md)
    r = D.check_git_mode(_ctx(md, plain))
    assert r["status"] == "warn" and "DEGRADED" in r["message"]
    assert "staleness tracking INACTIVE" in r["message"]


# --------------------------------------------------------------------------- #
# Frontmatter integrity (find_unparseable) + unresolvable baselines
# --------------------------------------------------------------------------- #
def test_integrity_ok_on_parseable_corpus(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    r = D.check_integrity(_ctx(memory_dir, repo))
    assert r["status"] == "ok"


def test_integrity_fails_and_names_the_broken_file(repo, memory_dir):
    _seed(memory_dir)
    # An unquoted description containing ': ' breaks yaml.safe_load.
    write_file(memory_dir, "bad.md", "---\nname: bad\ndescription: key: value oops\n---\nbody\n")
    r = D.check_integrity(_ctx(memory_dir, repo))
    assert r["status"] == "fail" and "bad" in r["message"] and "UNPARSEABLE" in r["message"]


def test_unresolvable_baselines_ok_when_all_resolve(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    git_commit(repo, "seed", 1_700_000_000)
    r = D.check_unresolvable_baselines(_ctx(memory_dir, repo))
    assert r["status"] == "ok"


def test_unresolvable_baselines_warns_on_missing_sha(repo, memory_dir):
    _seed(memory_dir)
    # A source_commit sha that is NOT in this repo's history -> the SHP-3 fallback path.
    write_file(
        memory_dir,
        "a.md",
        '---\nname: a\ndescription: "alpha"\ncited_paths: []\nsource_commit: "'
        + "d" * 40
        + '"\n---\nbody\n',
    )
    git_commit(repo, "seed", 1_700_000_000)
    r = D.check_unresolvable_baselines(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "unresolvable staleness baselines" in r["message"]


# --------------------------------------------------------------------------- #
# Index corruption / count / format version (QUA-5 + DOC-4 count check)
# --------------------------------------------------------------------------- #
def test_index_count_ok_when_no_index(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_INDEX_DIR", str(os.path.join(repo, ".memory-index")))
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    r = D.check_index_count(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no index built yet" in r["message"]


def test_index_count_matches_after_build(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    write_file(memory_dir, "b.md", _mem("b", "beta"))
    B.build_index(memory_dir, idx)
    r = D.check_index_count(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "matches the corpus (2)" in r["message"]


def test_index_count_warns_on_drift(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    # Add a memory AFTER the build -> manifest count (1) < corpus count (2).
    write_file(memory_dir, "b.md", _mem("b", "beta"))
    r = D.check_index_count(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "does not match" in r["message"]


def test_index_corruption_surfaces_truncated_manifest(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    with open(os.path.join(idx, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")
    r = D.check_index_corruption(_ctx(memory_dir, repo))
    assert r["status"] == "fail" and "corrupt" in r["message"]


def test_abstention_cold_start_warns_on_bm25_only(repo, memory_dir, tmp_path, monkeypatch):
    # RET-11: a bm25-only index (no warmed dense model) → abstention is degraded; the check
    # must NAME the dense-gating and nudge /hippo:bootstrap.
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    r = D.check_abstention_cold_start(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "/hippo:bootstrap" in r["message"] and "dense-gated" in r["message"]


def test_abstention_cold_start_ok_when_dense_ready(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    r = D.check_abstention_cold_start(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "abstention floor active" in r["message"]


def test_abstention_cold_start_ok_when_no_index(repo, memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_INDEX_DIR", str(tmp_path / ".memory-index"))  # never built
    _seed(memory_dir)
    r = D.check_abstention_cold_start(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no index built yet" in r["message"]


def _seed_two_topic_corpus(memory_dir, idx):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("apple", "apple orchard harvest pruning"))
    write_file(memory_dir, "b.md", _mem("banana", "banana bread ripening recipe"))
    B.build_index(memory_dir, idx)


def test_abstention_floor_sanity_ok_when_no_fixture(repo, memory_dir, tmp_path, monkeypatch):
    # RET-9: no corpus-local off-topic fixture → skip cleanly (nothing to measure).
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_two_topic_corpus(memory_dir, idx)
    r = D.check_abstention_floor_sanity(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no corpus-local off-topic fixture" in r["message"]


def test_abstention_floor_sanity_warns_when_offtopic_leaks(repo, memory_dir, tmp_path, monkeypatch):
    # RET-9: an off-topic fixture whose queries SHARE tokens with the corpus leaks (they return
    # results instead of abstaining) → low abstention rate → warn with the per-corpus count.
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_two_topic_corpus(memory_dir, idx)
    write_file(
        memory_dir, ".audit-fixtures/recall_abstention_set.yaml",
        '- query: "apple"\n- query: "banana"\n- query: "orchard"\n- query: "recipe"\n',
    )
    r = D.check_abstention_floor_sanity(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "off-topic fixture queries" in r["message"] and "bm25-only" in r["message"]


def test_abstention_floor_sanity_ok_when_corpus_abstains(repo, memory_dir, tmp_path, monkeypatch):
    # RET-9: genuinely off-topic queries (no token overlap) abstain → healthy rate → ok.
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_two_topic_corpus(memory_dir, idx)
    write_file(
        memory_dir, ".audit-fixtures/recall_abstention_set.yaml",
        '- query: "xylophone zebra"\n- query: "quokka umbrella"\n- query: "tectonic glacier"\n',
    )
    r = D.check_abstention_floor_sanity(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "correctly abstained" in r["message"]


def test_format_version_ok_when_current(repo, memory_dir, tmp_path, monkeypatch):
    from memory.provenance import write_cite_derivation, write_corpus_format

    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    # "Current" means the corpus DECLARES the current format: since GRA-4 bumped
    # CORPUS_FORMAT_VERSION past the v1 baseline, an unstamped corpus correctly warns
    # (it lags the plugin — the doctor-driven migration surface), so stamp it here.
    assert write_corpus_format(memory_dir) is True
    assert write_cite_derivation(memory_dir) is True  # DRV-2: the other axis
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and f"v{B.SCHEMA_VERSION}" in r["message"]


def test_format_version_warns_on_old_schema(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    man_path = os.path.join(idx, "manifest.json")
    with open(man_path, "r", encoding="utf-8") as fh:
        man = json.load(fh)
    man["schema_version"] = B.SCHEMA_VERSION - 1
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(man, fh)
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "format version" in r["message"]
    # COR-7: the mismatch is now ENFORCED (the manifest reads as absent) — the message must
    # say the stale index is ignored and rebuilt, and the check must still SEE the old
    # version (it reads the raw manifest; the gated loader would hide exactly this state).
    assert f"v{B.SCHEMA_VERSION - 1}" in r["message"]
    assert "rebuild" in r["message"].lower()


# --------------------------------------------------------------------------- #
# COR-7: corpus format vs plugin expectation — BOTH directions (the roadmap AC)
# --------------------------------------------------------------------------- #
def test_format_version_reports_corpus_format_alongside_index(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    assert P.write_corpus_format(memory_dir) is True
    # DRV-2: a fully-current corpus is current on BOTH axes — shape AND derivation.
    assert P.write_cite_derivation(memory_dir) is True
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert f"corpus format current (v{P.CORPUS_FORMAT_VERSION})" in r["message"]
    assert f"citation derivation current (v{P.CITATION_DERIVATION_VERSION})" in r["message"]


def test_format_version_warns_when_citations_predate_the_extractor_fix(
    repo, memory_dir, tmp_path, monkeypatch
):
    """AC (DRV-2): format-current and derivation-stale are INDEPENDENT states, and the
    second had no name before this. A corpus can be perfectly shaped and still hold
    cited_paths produced by an extractor that read package.json as package.js."""
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    assert P.write_corpus_format(memory_dir) is True  # shape current...
    # ...derivation deliberately left undeclared == v1
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert f"corpus format current (v{P.CORPUS_FORMAT_VERSION})" in r["message"]
    assert "citation derivation is v1" in r["message"]
    assert "consent-gated" in r["message"]  # never presented as something hippo just does


def test_format_version_warns_when_corpus_newer_than_plugin(repo, memory_dir, monkeypatch):
    """Corpus NEWER than the plugin -> update the plugin. Reported even with NO index built
    (a fresh clone on a stale plugin is exactly this shape)."""
    monkeypatch.setenv("HIPPO_INDEX_DIR", str(os.path.join(repo, ".memory-index")))
    _seed(memory_dir)
    assert P.write_corpus_format(memory_dir, version=P.CORPUS_FORMAT_VERSION + 1) is True
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "update the hippo plugin" in r["message"]
    assert f"v{P.CORPUS_FORMAT_VERSION + 1}" in r["message"]


def test_format_version_warns_when_corpus_older_than_plugin(repo, memory_dir, monkeypatch):
    """Corpus OLDER than the plugin -> the doctor-driven migration path (never automatic).
    An UNDECLARED corpus (no marker == format 1) under a post-bump plugin is the flagship
    case, so no marker is written here."""
    monkeypatch.setenv("HIPPO_INDEX_DIR", str(os.path.join(repo, ".memory-index")))
    _seed(memory_dir)
    monkeypatch.setattr(P, "CORPUS_FORMAT_VERSION", P.CORPUS_FORMAT_VERSION + 1)
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "MIGRATION" in r["message"]
    assert "README" in r["message"]  # points at the documented doctor-driven path
    assert "never migrates automatically" in r["message"]


# --------------------------------------------------------------------------- #
# FILL-ME templates (ported from ONB-4 prose)
# --------------------------------------------------------------------------- #
def test_fill_me_ok_on_filled_corpus(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "user_role.md", _mem("user_role", "solo founder building X"))
    r = D.check_fill_me(_ctx(memory_dir, repo))
    assert r["status"] == "ok"


def test_fill_me_fails_and_names_the_unfilled_file(repo, memory_dir):
    _seed(memory_dir)
    write_file(
        memory_dir,
        "user_role.md",
        '---\nname: user_role\ndescription: "<FILL-ME: your name>"\ntype: user\n---\nbody\n',
    )
    r = D.check_fill_me(_ctx(memory_dir, repo))
    assert r["status"] == "fail" and "user_role.md" in r["message"]


def test_fill_me_scans_the_floor_too(repo, memory_dir):
    write_file(memory_dir, "MEMORY.md", "# <FILL-ME: project name>\n")
    r = D.check_fill_me(_ctx(memory_dir, repo))
    assert r["status"] == "fail" and "MEMORY.md" in r["message"]


# --------------------------------------------------------------------------- #
# Trust state (SEC-1) — drive the REAL gate (delete the conftest bypass)
# --------------------------------------------------------------------------- #
def test_trust_check_warns_on_untrusted_corpus(repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    write_file(memory_dir, "b.md", _mem("b", "beta"))
    git_commit(repo, "seed", 1_700_000_000)
    r = D.check_trust(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "UNTRUSTED" in r["message"] and "mark_trusted" in r["message"]
    # The command names THIS repo's real git root (the gate key).
    assert os.path.realpath(repo) in r["message"]


def test_trust_check_ok_when_trusted(repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _seed(memory_dir)
    git_commit(repo, "seed", 1_700_000_000)
    from memory.trust import mark_trusted

    assert mark_trusted(repo) is True
    r = D.check_trust(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "trusted" in r["message"]


def test_trust_check_bypassed_with_trust_all(repo, memory_dir):
    # conftest sets HIPPO_TRUST_ALL=1 by default.
    _seed(memory_dir)
    git_commit(repo, "seed", 1_700_000_000)
    r = D.check_trust(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "HIPPO_TRUST_ALL" in r["message"]


def test_trust_check_na_outside_git(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    plain = str(tmp_path / "plain")
    md = os.path.join(plain, ".claude", "memory")
    os.makedirs(md)
    r = D.check_trust(_ctx(md, plain))
    assert r["status"] == "ok" and "N/A" in r["message"]


# --------------------------------------------------------------------------- #
# Secret scan (SEC-2) — the factored-out detector, called not reimplemented
# --------------------------------------------------------------------------- #
def test_secrets_ok_on_clean_corpus(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "a note about the deploy pipeline"))
    r = D.check_secrets(_ctx(memory_dir, repo))
    assert r["status"] == "ok"


def test_secrets_warns_and_names_the_file(repo, memory_dir):
    _seed(memory_dir)
    fake_aws = "AKIAIOSFODNN7EXAMPLE"
    write_file(memory_dir, "leak.md", _mem("leak", "note", body=f"key is {fake_aws} here"))
    r = D.check_secrets(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "leak.md" in r["message"] and "AWS access key" in r["message"]
    # Never echoes the secret itself.
    assert fake_aws not in r["message"]


# --------------------------------------------------------------------------- #
# Link density (GRA-3) — one-time hint when a >=5-memory corpus has zero edges
# --------------------------------------------------------------------------- #
def test_link_density_na_below_corpus_floor(repo, memory_dir):
    _seed(memory_dir)
    for i in range(3):  # below the 5-file floor
        write_file(memory_dir, f"m{i}.md", _mem(f"m{i}", f"note {i}"))
    r = D.check_link_density(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "N/A" in r["message"]


def test_link_density_warns_when_zero_edges_at_or_above_floor(repo, memory_dir):
    _seed(memory_dir)
    for i in range(5):  # at the floor, no [[wikilinks]] anywhere
        write_file(memory_dir, f"m{i}.md", _mem(f"m{i}", f"note {i}"))
    r = D.check_link_density(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "[[name]]" in r["message"] and "/hippo:new" in r["message"]


def test_link_density_ok_once_an_edge_exists(repo, memory_dir):
    _seed(memory_dir)
    for i in range(4):
        write_file(memory_dir, f"m{i}.md", _mem(f"m{i}", f"note {i}"))
    write_file(memory_dir, "m4.md", _mem("m4", "note 4", body="see [[m0]]"))
    r = D.check_link_density(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "1 wikilink edge" in r["message"]


# --------------------------------------------------------------------------- #
# GRF-1: edge rot — one threshold-gated line over links.graph_audit
# --------------------------------------------------------------------------- #
def test_edge_rot_ok_on_clean_graph(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "note a", body="see [[b]]"))
    write_file(memory_dir, "b.md", _mem("b", "note b"))
    r = D.check_edge_rot(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "edge rot: 0" in r["message"]
    assert "\n" not in r["message"]  # ONE line — the doctor render/line-count determinism pins


def test_edge_rot_warns_and_names_the_audit_command(repo, memory_dir):
    _seed(memory_dir)
    os.makedirs(os.path.join(memory_dir, "archive"), exist_ok=True)
    write_file(memory_dir, "a.md", _mem("a", "note a", body="see [[gone]] and [[attic]]"))
    write_file(
        memory_dir, os.path.join("archive", "attic.md"), _mem("attic", "archived note")
    )
    r = D.check_edge_rot(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "archived=1" in r["message"] and "dangling=1" in r["message"]
    assert "python -m memory.links --audit" in r["message"]  # runnable remedy, named
    assert "\n" not in r["message"]  # ONE line — the doctor render/line-count determinism pins


# --------------------------------------------------------------------------- #
# RET-3: non-English corpus served by the English default model
# --------------------------------------------------------------------------- #
_JP_DESC = "東京都渋谷区の天気予報と観光案内について詳しく説明しています今日と明日の予定"


def test_non_english_corpus_na_with_no_index(repo, memory_dir):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", _JP_DESC))
    r = D.check_non_english_corpus(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "N/A" in r["message"]


def test_non_english_corpus_na_below_sample_floor(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "東京"))  # tiny sample, well below the floor
    B.build_index(memory_dir, idx)
    r = D.check_non_english_corpus(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "N/A" in r["message"] and "below the" in r["message"]


def test_non_english_corpus_ok_when_english(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(
        memory_dir,
        "a.md",
        _mem("a", "this is a perfectly ordinary english language memory description about deploys and testing procedures"),
    )
    B.build_index(memory_dir, idx)
    r = D.check_non_english_corpus(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "Latin-script" in r["message"]


def test_non_english_corpus_warns_when_visibly_non_latin_and_english_model(
    repo, memory_dir, tmp_path, monkeypatch
):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    for i in range(3):
        write_file(memory_dir, f"m{i}.md", _mem(f"m{i}", _JP_DESC))
    B.build_index(memory_dir, idx)
    # BM25-only build -> manifest["model"] is None, which is NOT "already switched away from
    # English" (None just means "no dense recorded yet") -- the check must still fire.
    r = D.check_non_english_corpus(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "--multilingual" in r["message"]


def test_non_english_corpus_ok_when_already_multilingual_model(
    repo, memory_dir, tmp_path, monkeypatch
):
    """Once the manifest records a DIFFERENT (already-switched) model, the hint has nothing
    left to suggest -- silent even though the corpus is still visibly non-Latin."""
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    monkeypatch.setattr(B, "DEFAULT_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    _seed(memory_dir)
    for i in range(3):
        write_file(memory_dir, f"m{i}.md", _mem(f"m{i}", _JP_DESC))
    B.build_index(memory_dir, idx)
    r = D.check_non_english_corpus(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "already" in r["message"]


def test_non_english_corpus_never_raises_on_unreadable_file(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", _JP_DESC))
    B.build_index(memory_dir, idx)
    # Corrupt the file after the build so the check's own read hits a real filesystem error path.
    os.remove(os.path.join(memory_dir, "a.md"))
    r = D.check_non_english_corpus(_ctx(memory_dir, repo))
    assert r["status"] in ("ok", "warn")  # degrades gracefully -- never raises


# --------------------------------------------------------------------------- #
# Stale MEMOBOT_* env (DOC-8) — the pre-v0.4.0 prefix is now silently inert; this check is the
# one legible signal. Fully controls its own env (never relies on conftest's autouse fixtures,
# which now only ever set HIPPO_* names) so it can assert on the OLD prefix without interference.
# --------------------------------------------------------------------------- #
def test_stale_memobot_env_ok_when_absent(repo, memory_dir, monkeypatch):
    for key in list(os.environ):
        if key.startswith("MEMOBOT_"):
            monkeypatch.delenv(key, raising=False)
    r = D.check_stale_memobot_env(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "no stale MEMOBOT_" in r["message"]


def test_stale_memobot_env_warns_and_names_the_replacement(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("MEMOBOT_TRUST_ALL", "1")
    r = D.check_stale_memobot_env(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "MEMOBOT_TRUST_ALL is ignored since v0.4.0 — use HIPPO_TRUST_ALL" in r["message"]


def test_stale_memobot_env_never_fails_and_reports_all_stale_vars(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("MEMOBOT_TRUST_ALL", "1")
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    r = D.check_stale_memobot_env(_ctx(memory_dir, repo))
    assert r["status"] == "warn"  # warn-only — never fails the doctor run
    assert "MEMOBOT_DISABLE_DENSE" in r["message"] and "MEMOBOT_TRUST_ALL" in r["message"]


# --------------------------------------------------------------------------- #
# Pack drift (uses existing pack/pack_version metadata; no new instrumentation)
# --------------------------------------------------------------------------- #
def _pack_root(tmp_path, *, pack: str, version: str) -> str:
    root = tmp_path / "plugin-root"
    pack_dir = root / "assets" / "packs" / pack
    os.makedirs(pack_dir, exist_ok=True)
    with open(pack_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"pack": pack, "version": version, "memories": []}, fh)
    return str(root)


def test_pack_drift_ok_when_versions_match(repo, memory_dir, tmp_path):
    root = _pack_root(tmp_path, pack="core", version="0.3.0")
    _seed(memory_dir)
    write_file(
        memory_dir,
        "m.md",
        '---\nname: m\ndescription: "x"\nmetadata:\n  pack: core\n  pack_version: "0.3.0"\n---\nbody\n',
    )
    r = D.check_pack_drift(_ctx(memory_dir, repo, plugin_root=root))
    assert r["status"] == "ok"


def test_pack_drift_warns_on_lagging_version(repo, memory_dir, tmp_path):
    root = _pack_root(tmp_path, pack="core", version="0.3.0")
    _seed(memory_dir)
    write_file(
        memory_dir,
        "m.md",
        '---\nname: m\ndescription: "x"\nmetadata:\n  pack: core\n  pack_version: "0.2.0"\n---\nbody\n',
    )
    r = D.check_pack_drift(_ctx(memory_dir, repo, plugin_root=root))
    assert r["status"] == "warn"
    assert "m " in r["message"] and "v0.2.0" in r["message"] and "v0.3.0" in r["message"]


def test_pack_drift_na_without_packs_dir(repo, memory_dir):
    r = D.check_pack_drift(_ctx(memory_dir, repo, plugin_root=""))
    assert r["status"] == "ok" and "N/A" in r["message"]


# --------------------------------------------------------------------------- #
# main() CLI
# --------------------------------------------------------------------------- #
def test_main_prints_and_returns_zero(repo, memory_dir, monkeypatch, capsys):
    _seed(memory_dir)
    git_commit(repo, "seed", 1_700_000_000)
    monkeypatch.setattr(D, "resolve_dirs", lambda: (memory_dir, repo))
    rc = D.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("\n") == len(D.CHECKS)  # one line per check + trailing newline from print


def test_main_output_is_deterministic(repo, memory_dir, monkeypatch, capsys):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    git_commit(repo, "seed", 1_700_000_000)
    monkeypatch.setattr(D, "resolve_dirs", lambda: (memory_dir, repo))
    D.main()
    first = capsys.readouterr().out
    D.main()
    second = capsys.readouterr().out
    assert first == second


# --------------------------------------------------------------------------- #
# INT-4 — native-memory coexistence contract (symlink drift + native-layout change)
# --------------------------------------------------------------------------- #
def _projects_memory_link(fake_home, repo):
    from memory.provenance import encode_project_dir

    return os.path.join(fake_home, ".claude", "projects", encode_project_dir(repo), "memory")


def _native_ctx(tmp_path, monkeypatch):
    fake_home = str(tmp_path / "home")
    os.makedirs(fake_home)
    monkeypatch.setattr(os.path, "expanduser", lambda p: fake_home if p == "~" else p)
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return fake_home, repo, md


def test_native_coexistence_intact_when_symlink_resolves_to_corpus(tmp_path, monkeypatch):
    fake_home, repo, md = _native_ctx(tmp_path, monkeypatch)
    link = _projects_memory_link(fake_home, repo)
    os.makedirs(os.path.dirname(link))
    os.symlink(md, link)
    r = D.check_native_coexistence(_ctx(md, repo))
    assert r["status"] == "ok" and "coexistence intact" in r["message"]


def test_native_coexistence_detects_symlink_target_drift(tmp_path, monkeypatch):
    fake_home, repo, md = _native_ctx(tmp_path, monkeypatch)
    elsewhere = str(tmp_path / "other-corpus")
    os.makedirs(elsewhere)
    link = _projects_memory_link(fake_home, repo)
    os.makedirs(os.path.dirname(link))
    os.symlink(elsewhere, link)  # symlink resolves to a DIFFERENT corpus
    r = D.check_native_coexistence(_ctx(md, repo))
    assert r["status"] == "warn" and "DRIFT" in r["message"]


def test_native_coexistence_detects_native_layout_change(tmp_path, monkeypatch):
    fake_home, repo, md = _native_ctx(tmp_path, monkeypatch)
    link = _projects_memory_link(fake_home, repo)
    os.makedirs(link)  # a REAL dir occupies the slot (native memory took it over) — not a symlink
    r = D.check_native_coexistence(_ctx(md, repo))
    assert r["status"] == "warn" and "native-layout change" in r["message"]


def test_native_coexistence_missing_link_is_ok(tmp_path, monkeypatch):
    fake_home, repo, md = _native_ctx(tmp_path, monkeypatch)  # no link created
    r = D.check_native_coexistence(_ctx(md, repo))
    assert r["status"] == "ok" and "no projects-dir memory link yet" in r["message"]


def test_native_coexistence_is_registered_in_checks():
    assert "native_coexistence" in [label for label, _ in D.CHECKS]


# --------------------------------------------------------------------------- #
# INT-5 — hot-path p95 latency check over the recall ledger
# --------------------------------------------------------------------------- #
def _write_recall_ledger(memory_dir, latencies):
    from memory.provenance import ensure_self_ignoring_dir
    from memory.telemetry import default_telemetry_dir

    td = default_telemetry_dir(memory_dir)
    ensure_self_ignoring_dir(td)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for lat in latencies:
            fh.write(json.dumps({
                "ts": 1.0, "latency_ms": lat, "names": [], "backend": "bm25",
                "k": 10, "query_preview": "q",
            }) + "\n")


def test_hot_path_latency_empty_ledger_is_ok(memory_dir, repo):
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no recall events" in r["message"]


def test_hot_path_latency_reports_p95_under_budget(memory_dir, repo):
    _write_recall_ledger(memory_dir, [10, 20, 30, 40, 50])
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "p95 = 50ms" in r["message"] and "5 recall" in r["message"]


def test_hot_path_latency_warns_over_budget(memory_dir, repo):
    _write_recall_ledger(memory_dir, [100.0, 200.0, 5000.0])  # p95 = 5000 > 1500
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "ABOVE" in r["message"]


def test_hot_path_latency_registered_in_checks():
    assert "hot_path_latency" in [label for label, _ in D.CHECKS]


# --------------------------------------------------------------------------- #
# DOC-7 — installed-vs-bootstrapped plugin version delta
# --------------------------------------------------------------------------- #
def _version_ctx(tmp_path, installed, sentinel_version, *, write_sentinel=True):
    proot = tmp_path / "plugin-root"
    os.makedirs(proot / ".claude-plugin")
    with open(proot / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as fh:
        json.dump({"name": "hippo", "version": installed}, fh)
    pdata = tmp_path / "plugin-data"
    os.makedirs(pdata)
    if write_sentinel:
        rec = {"requirements_hash": "h", "bootstrapped_at": "test"}
        if sentinel_version is not None:
            rec["plugin_version"] = sentinel_version
        with open(pdata / ".bootstrap-sentinel", "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
    return _ctx(str(tmp_path / "m"), str(tmp_path / "r"), plugin_data=str(pdata), plugin_root=str(proot))


def test_plugin_version_in_sync(tmp_path):
    r = D.check_plugin_version(_version_ctx(tmp_path, "0.6.0", "0.6.0"))
    assert r["status"] == "ok" and "in sync" in r["message"]


def test_plugin_version_delta_warns(tmp_path):
    r = D.check_plugin_version(_version_ctx(tmp_path, "0.6.0", "0.5.0"))
    assert r["status"] == "warn" and "version delta" in r["message"]
    assert "0.6.0" in r["message"] and "0.5.0" in r["message"]


def test_plugin_version_sentinel_predates_tracking(tmp_path):
    r = D.check_plugin_version(_version_ctx(tmp_path, "0.6.0", None))  # sentinel has no plugin_version
    assert r["status"] == "warn" and "predates version tracking" in r["message"]


def test_plugin_version_not_bootstrapped(tmp_path):
    r = D.check_plugin_version(_version_ctx(tmp_path, "0.6.0", None, write_sentinel=False))
    assert r["status"] == "ok" and "not bootstrapped" in r["message"]


def test_plugin_version_registered_in_checks():
    assert "plugin_version" in [label for label, _ in D.CHECKS]


def _bootstrapped_env(tmp_path, monkeypatch, *, installed, sentinel_version):
    """A COMPLETE, deps-current bootstrap (matching requirements_hash) whose sentinel
    records ``sentinel_version`` — the setup a version-only update produces."""
    import hashlib

    root = tmp_path / "plugin-root"
    os.makedirs(root / ".claude-plugin")
    (root / "requirements.txt").write_text("numpy>=2\n", encoding="utf-8")
    with open(root / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as fh:
        json.dump({"name": "hippo", "version": installed}, fh)
    data = tmp_path / "plugin-data"
    os.makedirs(data)
    req_hash = hashlib.sha256(b"numpy>=2\n").hexdigest()
    (data / ".bootstrap-sentinel").write_text(
        json.dumps(
            {
                "requirements_hash": req_hash,
                "bootstrapped_at": "2026-01-01T00:00:00+00:00",
                "plugin_version": sentinel_version,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    return data / ".bootstrap-sentinel", req_hash


def test_start_restamps_stale_version_on_fast_path(tmp_path, monkeypatch):
    """A version-only ('re-bootstrap: no') update leaves the sentinel's plugin_version
    stale; start()'s already-current fast path refreshes ONLY that label (offline), so the
    DOC-7 delta becomes clearable instead of nagging to run a bootstrap that no-ops."""
    from memory import bootstrap as B

    sentinel, req_hash = _bootstrapped_env(tmp_path, monkeypatch, installed="1.11.1", sentinel_version="1.10.0")
    ctx = _ctx(str(tmp_path / "m"), str(tmp_path / "r"),
               plugin_data=str(sentinel.parent), plugin_root=str(tmp_path / "plugin-root"))

    # before: doctor nags on the version delta
    assert D.check_plugin_version(ctx)["status"] == "warn"

    # start() fast-paths (deps current) AND re-stamps the label — never spawns a worker
    result = B.start()
    assert result["status"] == "already_bootstrapped" and result["restamped"] is True

    rec = json.loads(sentinel.read_text(encoding="utf-8"))
    assert rec["plugin_version"] == "1.11.1"                        # label refreshed
    assert rec["requirements_hash"] == req_hash                     # deps state preserved
    assert rec["bootstrapped_at"] == "2026-01-01T00:00:00+00:00"    # real bootstrap time preserved

    # after: doctor now reads in-sync — the delta was actually clearable
    assert D.check_plugin_version(ctx)["status"] == "ok"

    # idempotent: a second run finds nothing to refresh
    assert B.start()["restamped"] is False


def test_start_no_restamp_when_version_already_matches(tmp_path, monkeypatch):
    """When the sentinel already records the installed version, the fast path is a no-op."""
    from memory import bootstrap as B

    _bootstrapped_env(tmp_path, monkeypatch, installed="1.11.1", sentinel_version="1.11.1")
    assert B.start() == {"status": "already_bootstrapped", "restamped": False}


# --------------------------------------------------------------------------- #
# GOV-2: the steering count — "N memories pinned", the control axis made visible
# (pre-wires the mandatory MUTE count for when the down-weight ships).
# --------------------------------------------------------------------------- #
def _steered_corpus(tmp_path, monkeypatch, *, pin_names=()):
    import memory.build_index as B

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "smem")
    idx_default = B.default_index_dir(md)
    os.makedirs(md, exist_ok=True)
    for name in ("one", "two", "three"):
        steer = "steer: pin\n" if name in pin_names else ""
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "d {name}"\n{steer}---\nbody\n')
    B.build_index(md, idx_default)
    return md


def test_steering_counts_pinned_memories(tmp_path, monkeypatch):
    md = _steered_corpus(tmp_path, monkeypatch, pin_names=("two", "three"))
    r = D.check_steering(_ctx(md, str(tmp_path)))
    assert r["status"] == "ok"
    assert "steering: 2 memory(ies) pinned" in r["message"]
    assert "three, two" in r["message"]  # sorted, deterministic


def test_steering_ok_when_nothing_pinned(tmp_path, monkeypatch):
    md = _steered_corpus(tmp_path, monkeypatch)
    r = D.check_steering(_ctx(md, str(tmp_path)))
    assert r["status"] == "ok" and "no memories pinned" in r["message"]


def test_steering_ok_when_no_index_yet(tmp_path):
    r = D.check_steering(_ctx(str(tmp_path / "empty"), str(tmp_path)))
    assert r["status"] == "ok" and "no index built yet" in r["message"]


def test_steering_registered_in_checks():
    assert "steering" in [label for label, _ in D.CHECKS]


# --------------------------------------------------------------------------- #
# GOV-6: the trust scorecard — one deterministic rollup line, graceful absence
# --------------------------------------------------------------------------- #
def test_trust_scorecard_all_zero_on_empty_state(memory_dir, repo):
    r = D.check_trust_scorecard(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    m = r["message"]
    assert m.startswith("trust scorecard: ")
    assert "0 contested-unresolved (→ /hippo:resolve)" in m
    assert "0 rule↔memory conflict(s) (→ /hippo:consolidate)" in m
    assert "0 rules-plane rot (edit the named file)" in m
    assert "0 blind spot(s) (→ /hippo:consolidate)" in m
    assert "0 orphan(s) never recalled (→ /hippo:audit)" in m
    assert "0 pinned / 0 muted" in m
    assert "0 draft" in m
    assert "no watermark baseline yet" in m
    assert "\n" not in m  # ONE line — the doctor render/line-count determinism pins


def test_trust_scorecard_aggregates_real_counts(memory_dir, repo, tmp_path, monkeypatch):
    """The aggregation seam: GOV-1's inbox, GOV-2's pin, GOV-7's draft, and GOV-4's floor
    delta all roll up with their fix routes — real non-zero numbers, not just zeros."""
    import memory.build_index as B
    import memory.session_start as S

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    # a contradicts pair (GOV-1), a pinned memory (GOV-2), a draft memory (GOV-7)
    with open(os.path.join(memory_dir, "old-api.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: old-api\ndescription: "we call v1 directly"\n---\nbody\n')
    with open(os.path.join(memory_dir, "new-api.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: new-api\ndescription: "we stopped calling v1"\n'
            "contradicts: [old-api]\nsteer: pin\n---\nbody\n"
        )
    with open(os.path.join(memory_dir, "guess.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: guess\ndescription: "an unverified hunch"\n'
            "metadata:\n  confidence: draft\n---\nbody\n"
        )
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    # GOV-4 watermark: baseline, then add a corpus file so the delta is non-zero
    S.floor_change_producer(memory_dir, repo)
    with open(os.path.join(memory_dir, "pulled.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: pulled\ndescription: "arrived via pull"\n---\nbody\n')

    r = D.check_trust_scorecard(D.DoctorContext(memory_dir, repo))
    m = r["message"]
    assert r["status"] == "warn"  # a live contradiction is actionable
    assert "1 contested-unresolved (→ /hippo:resolve)" in m
    assert "1 pinned / 0 muted" in m
    assert "1 draft" in m
    assert "corpus +1/−0 since last session" in m
    # the peek did NOT consume GOV-4's surfaced-once semantics
    assert S.floor_change_producer(memory_dir, repo) is not None


def test_trust_scorecard_orphans_are_isolates_intersect_never_recalled(memory_dir, repo, monkeypatch):
    """Orphan = fully disconnected AND never recalled — a linked or recalled memory is not
    curation backlog."""
    import memory.build_index as B
    from memory import telemetry as T

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    with open(os.path.join(memory_dir, "island.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: island\ndescription: "disconnected and never recalled"\n---\nbody\n')
    with open(os.path.join(memory_dir, "linked_a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: linked_a\ndescription: "links out"\n---\nsee [[linked_b]]\n')
    with open(os.path.join(memory_dir, "linked_b.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: linked_b\ndescription: "is linked"\n---\nbody\n')
    with open(os.path.join(memory_dir, "recalled_isle.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: recalled_isle\ndescription: "disconnected but recalled"\n---\nbody\n')
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    T.log_recall_event(
        [{"name": "recalled_isle", "backend": "bm25", "score": 0.5, "rank": 1}],
        query="q", k=6, latency_ms=1.0,
        telemetry_dir=T.default_telemetry_dir(memory_dir), session_id="s",
    )
    m = D.check_trust_scorecard(D.DoctorContext(memory_dir, repo))["message"]
    assert "1 orphan(s) never recalled" in m  # island only


def test_trust_scorecard_registered_right_after_trust():
    labels = [label for label, _ in D.CHECKS]
    assert labels.index("trust_scorecard") == labels.index("trust") + 1


def test_trust_scorecard_bogus_dirs_never_error(tmp_path):
    r = D.check_trust_scorecard(D.DoctorContext(str(tmp_path / "nope"), str(tmp_path / "nah")))
    assert r["status"] == "ok"
    assert "0 contested-unresolved" in r["message"]  # absent producers read as zero


def test_trust_scorecard_reports_graph_components(tmp_path, memory_dir, repo, monkeypatch):
    import memory.build_index as B

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    with open(os.path.join(memory_dir, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "links to b"\n---\nsee [[b]]\n')
    with open(os.path.join(memory_dir, "b.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: b\ndescription: "linked"\n---\nbody\n')
    with open(os.path.join(memory_dir, "island.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: island\ndescription: "alone"\n---\nbody\n')
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    m = D.check_trust_scorecard(D.DoctorContext(memory_dir, repo))["message"]
    assert "2 graph component(s)" in m  # {a,b} + {island}


# --------------------------------------------------------------------------- #
# INT-8: the stdio MCP server launch-health check (bin/hippo mcp actually starts)
# --------------------------------------------------------------------------- #
def test_mcp_launch_reports_the_server_starts(memory_dir, repo):
    from memory import mcp_server as M

    r = D.check_mcp_launch(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "MCP server starts" in r["message"]
    # The message reflects the REAL tool/resource surface + the SEC-13 per-message bound.
    assert f"{len(M._TOOLS)} tool(s) / {len(M._RESOURCES)} resource(s)" in r["message"]
    assert f"{M._MAX_MESSAGE_CHARS} bytes" in r["message"]


def test_mcp_launch_does_not_leak_offline_env(memory_dir, repo, monkeypatch):
    # serve() sets HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE (setdefault) + FASTEMBED_CACHE_PATH (via
    # ensure_fastembed_cache_path); the check snapshots/restores ALL THREE. Pin each one — the
    # autouse recall-state fixture would otherwise silently absorb a leak of the latter two.
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "FASTEMBED_CACHE_PATH"):
        monkeypatch.delenv(key, raising=False)
    D.check_mcp_launch(D.DoctorContext(memory_dir, repo))
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "FASTEMBED_CACHE_PATH"):
        assert key not in os.environ, f"check_mcp_launch leaked {key}"


def test_mcp_launch_registered_before_the_trailing_env_check():
    labels = [label for label, _ in D.CHECKS]
    assert "mcp_launch" in labels
    assert labels[-1] == "stale_memobot_env"  # the env-hygiene check stays pinned last


# --------------------------------------------------------------------------- #
# MSR-4: the drop-autopsy aggregation line — one deterministic line, min-gated.
# --------------------------------------------------------------------------- #
def test_drop_autopsy_quiet_below_min_events(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "t"))
    r = D.check_drop_autopsy(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "not enough evidence" in r["message"]
    assert "\n" not in r["message"]


def test_drop_autopsy_aggregates_reasons_deterministically(memory_dir, repo, tmp_path, monkeypatch):
    from memory import telemetry as T

    td = str(tmp_path / "t")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    for i in range(5):
        T.log_recall_event(
            [],
            query=f"q{i}",
            k=5,
            latency_ms=1.0,
            telemetry_dir=td,
            drops=[
                {"name": f"m{i}", "reason": "knee_cliff", "score": 0.01},
                {"name": f"n{i}", "reason": "dense_floor", "score": 0.2, "threshold": 0.4},
            ],
            near_miss=[{"name": f"n{i}", "score": 0.2}],
            dense_floor=0.4,
        )
    ctx = D.DoctorContext(memory_dir, repo)
    r = D.check_drop_autopsy(ctx)
    assert r["status"] == "ok"
    m = r["message"]
    assert "\n" not in m  # ONE line — the doctor render/line-count determinism pins
    assert "over 5 recall event(s)" in m
    assert "dense_floor ×5" in m and "knee_cliff ×5" in m
    assert "median margin 0.2000 below the dense floor" in m
    # deterministic: identical state renders byte-identical
    assert D.check_drop_autopsy(ctx)["message"] == m


def test_drop_autopsy_registered_after_blind_spots():
    labels = [label for label, _fn in D.CHECKS]
    assert labels.index("drop_autopsy") == labels.index("recall_blind_spots") + 1


# --------------------------------------------------------------------------- #
# MSR-3: the hot-path p95 is channel-filtered; the channels line counts surfaces.
# --------------------------------------------------------------------------- #
def test_hot_path_p95_ignores_mcp_channel_events(memory_dir, repo, tmp_path, monkeypatch):
    from memory import telemetry as T

    td = str(tmp_path / "t")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    for _ in range(4):
        T.log_recall_event([], query="q", k=5, latency_ms=10.0, telemetry_dir=td)
    ctx = D.DoctorContext(memory_dir, repo)
    before = D.check_hot_path_latency(ctx)["message"]
    # an in-process MCP recall (different cost animal) must not corrupt KPI-3's p95
    T.log_recall_event([], query="q", k=5, latency_ms=99999.0, telemetry_dir=td, channel="mcp")
    after = D.check_hot_path_latency(ctx)["message"]
    assert after == before  # byte-identical — the MCP event is invisible to this gate


def test_recall_channels_line_counts_and_mcp_blind_spots(memory_dir, repo, tmp_path, monkeypatch):
    from memory import telemetry as T

    td = str(tmp_path / "t")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    ctx = D.DoctorContext(memory_dir, repo)
    assert "no recall events logged yet" in D.check_recall_channels(ctx)["message"]
    T.log_recall_event([], query="q", k=5, latency_ms=1.0, telemetry_dir=td)
    assert "all 1 event(s) via hook" in D.check_recall_channels(ctx)["message"]
    for _ in range(3):
        T.log_recall_event(
            [], query="what is the terraform registry", k=5, latency_ms=1.0,
            telemetry_dir=td, channel="mcp",
        )
    m = D.check_recall_channels(ctx)["message"]
    assert "1 hook / 3 mcp event(s)" in m
    assert "1 recurring MCP blind-spot cluster(s)" in m
    assert "\n" not in m
    assert D.check_recall_channels(ctx)["message"] == m  # deterministic


def test_recall_channels_registered_after_hot_path_latency():
    labels = [label for label, _fn in D.CHECKS]
    assert labels.index("recall_channels") == labels.index("hot_path_latency") + 1


# --------------------------------------------------------------------------- #
# MSR-6: the scorecard's folded-in cost-honesty part.
# --------------------------------------------------------------------------- #
def test_scorecard_cost_line_reads_measured_ledgers(memory_dir, repo, tmp_path, monkeypatch):
    from memory import telemetry as T

    td = str(tmp_path / "t")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    ctx = D.DoctorContext(memory_dir, repo)
    zero = D.check_trust_scorecard(ctx)["message"]
    assert "injected ~0 chars over 0 session(s); touched n/a" in zero
    assert "\n" not in zero  # still ONE line — folded in, not a new check
    T.log_recall_event(
        [], query="q", k=5, latency_ms=1.0, telemetry_dir=td,
        session_id="s1", injected_chars=1200,
    )
    T.log_injection_producers(
        {"floor": 300}, total=300, cap=9000, telemetry_dir=td, session_id="s2"
    )
    m = D.check_trust_scorecard(ctx)["message"]
    assert "injected ~1500 chars over 2 session(s)" in m
    assert D.check_trust_scorecard(ctx)["message"] == m  # deterministic


# --------------------------------------------------------------------------- #
# GRF-3: floor calibration — configured dense floor vs the persisted sweep
# --------------------------------------------------------------------------- #
def _write_sweep(memory_dir, monkeypatch, tmp_path, **over):
    import json as _json

    from memory.eval_recall import _FLOOR_SWEEP_SCHEMA, default_floor_sweep_path

    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    doc = {
        "ok": True,
        "schema": _FLOOR_SWEEP_SCHEMA,
        "model": "BAAI/bge-small-en-v1.5",
        "configured_floor": 0.60,
        "corpus_fingerprint": "fp-stale",
        "generated_at": "2026-07-16",
        "recommended": 0.61,
        "overlap": False,
        "on_n": 6,
        "off_n": 3,
        "on_min": 0.7,
        "off_max": 0.5,
        "safety_delta": 0.11,
        "leaked_off": 0,
        "cut_on": 0,
    }
    doc.update(over)
    path = default_floor_sweep_path(memory_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(doc, fh)
    return doc


def test_floor_calibration_no_sweep_names_the_command(repo, memory_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "telemetry"))
    _seed(memory_dir)
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "python -m memory.eval_recall --floor-sweep" in r["message"]
    assert "\n" not in r["message"]  # ONE line — the doctor render/line-count determinism pins


def test_floor_calibration_ok_within_tolerance(repo, memory_dir, monkeypatch, tmp_path):
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "note a"))
    _write_sweep(memory_dir, monkeypatch, tmp_path, recommended=0.61)
    # no index built -> the fingerprint staleness leg is skipped, numbers compare
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "0.6" in r["message"] and "0.61" in r["message"]


def test_floor_calibration_warns_on_gap_and_never_auto_writes(
    repo, memory_dir, monkeypatch, tmp_path
):
    _seed(memory_dir)
    _write_sweep(memory_dir, monkeypatch, tmp_path, recommended=0.72)
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "0.6" in r["message"] and "0.72" in r["message"]
    assert "nothing auto-writes" in r["message"]
    assert "\n" not in r["message"]  # ONE line — the doctor render/line-count determinism pins


def test_floor_calibration_stale_fingerprint_reports_stale(
    repo, memory_dir, monkeypatch, tmp_path
):
    from memory import build_index as B

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "note a"))
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    _write_sweep(memory_dir, monkeypatch, tmp_path, corpus_fingerprint="fp-stale")
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "STALE" in r["message"] and "--floor-sweep" in r["message"]


# --------------------------------------------------------------------------- #
# MSR-5: the salience-evidence lived-in nudge — ED-2's one automatic surface
# --------------------------------------------------------------------------- #
def _seed_usage_aggregates(monkeypatch, tmp_path, sessions):
    import json as _json

    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    os.makedirs(td, exist_ok=True)
    doc = {
        "version": 1,
        "sessions": {"count": sessions, "first_ts": 1.0, "last_session_id": "s"},
        "memories": {"m0": {"first_ts": 1.0, "last_ts": 2.0, "sessions": sessions, "last_session_id": "s"}},
    }
    with open(os.path.join(td, "usage_aggregates.json"), "w", encoding="utf-8") as fh:
        _json.dump(doc, fh)
    return td


def test_salience_evidence_quiet_below_lived_in_threshold(repo, memory_dir, monkeypatch, tmp_path):
    _seed_usage_aggregates(monkeypatch, tmp_path, sessions=3)
    _seed(memory_dir)
    r = D.check_salience_evidence(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "not yet lived-in" in r["message"]
    assert "\n" not in r["message"]  # ONE line — the doctor render/line-count determinism pins


def test_salience_evidence_nudges_once_lived_in(repo, memory_dir, monkeypatch, tmp_path):
    _seed_usage_aggregates(monkeypatch, tmp_path, sessions=12)
    _seed(memory_dir)
    r = D.check_salience_evidence(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "--ab HIPPO_SALIENCE" in r["message"]
    assert "owner-decided-OFF" in r["message"]  # the nudge restates ED-2, never a flip
    assert "\n" not in r["message"]


def test_salience_evidence_ok_once_recorded(repo, memory_dir, monkeypatch, tmp_path):
    import json as _json

    from memory.salience_eval import _SALIENCE_AB_SCHEMA, default_report_path

    td = _seed_usage_aggregates(monkeypatch, tmp_path, sessions=12)
    _seed(memory_dir)
    path = default_report_path(memory_dir, td)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(
            {"schema": _SALIENCE_AB_SCHEMA, "deltas": {"single-hop": {}}, "identical_arms": True},
            fh,
        )
    r = D.check_salience_evidence(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "A/B recorded" in r["message"] and "dated owner decision" in r["message"]
