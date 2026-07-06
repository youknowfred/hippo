"""Tests for memory/doctor.py — the DOC-4 deterministic doctor engine.

Each check is exercised DIRECTLY against a hermetic ``DoctorContext`` (repo/memory_dir
fixtures + write_file/git_commit helpers), asserting stable status/message for a known input
state. The engine's contract is DETERMINISM: the same context maps to byte-identical output, so
several tests assert ``render()`` twice against identical state and compare the two runs.

Trust checks delete the autouse ``MEMOBOT_TRUST_ALL`` (set open by conftest) to drive the real
deny/allow gate, mirroring tests/test_trust.py.
"""

from __future__ import annotations

import json
import os

import numpy as np

from memory import build_index as B
from memory import doctor as D

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
    assert labels[0] == "bootstrap" and labels[-1] == "secrets"


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
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", str(os.path.join(repo, ".memory-index")))
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    r = D.check_index_count(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no index built yet" in r["message"]


def test_index_count_matches_after_build(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", idx)
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    write_file(memory_dir, "b.md", _mem("b", "beta"))
    B.build_index(memory_dir, idx)
    r = D.check_index_count(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "matches the corpus (2)" in r["message"]


def test_index_count_warns_on_drift(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", idx)
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    # Add a memory AFTER the build -> manifest count (1) < corpus count (2).
    write_file(memory_dir, "b.md", _mem("b", "beta"))
    r = D.check_index_count(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "does not match" in r["message"]


def test_index_corruption_surfaces_truncated_manifest(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", idx)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    with open(os.path.join(idx, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")
    r = D.check_index_corruption(_ctx(memory_dir, repo))
    assert r["status"] == "fail" and "corrupt" in r["message"]


def test_format_version_ok_when_current(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", idx)
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "alpha"))
    B.build_index(memory_dir, idx)
    r = D.check_format_version(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and f"v{B.SCHEMA_VERSION}" in r["message"]


def test_format_version_warns_on_old_schema(repo, memory_dir, tmp_path, monkeypatch):
    idx = str(tmp_path / ".memory-index")
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", idx)
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.delenv("MEMOBOT_TRUST_ALL", raising=False)
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
    monkeypatch.delenv("MEMOBOT_TRUST_ALL", raising=False)
    _seed(memory_dir)
    git_commit(repo, "seed", 1_700_000_000)
    from memory.trust import mark_trusted

    assert mark_trusted(repo) is True
    r = D.check_trust(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "trusted" in r["message"]


def test_trust_check_bypassed_with_trust_all(repo, memory_dir):
    # conftest sets MEMOBOT_TRUST_ALL=1 by default.
    _seed(memory_dir)
    git_commit(repo, "seed", 1_700_000_000)
    r = D.check_trust(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "MEMOBOT_TRUST_ALL" in r["message"]


def test_trust_check_na_outside_git(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMOBOT_TRUST_ALL", raising=False)
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
