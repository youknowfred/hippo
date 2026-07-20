"""OPS-1 (doctor half): the running-vs-source skew line — DOC-7's sibling.

The twice-bitten live-hook-lag class (5 releases of lag until 2026-07-17; 4 fresh-
baselined memories re-flagged by pre-GRW-5 code on 2026-07-19) finally gets a name
where it bites. The pins:

  AC1  the line FIRES (warn) only on the this-repo-is-the-source shape with differing
       versions: name-matched manifests + realpath difference + version difference;
       empty-norm (ok, no skew claim) on non-hippo-source projects, on equality, on
       unreadable manifests, and when the hooks run the tree's source directly.
  AC2  insertion respects the pinned doctor tail order (and sits beside DOC-7 —
       the sibling placement is deliberate).
  AC3  ED4R-3: visibility, never coordination — the check names facts and the update
       command; its source contains no subprocess/os.system (negative-capability pin).
"""

from __future__ import annotations

import inspect
import json
import os

from memory import doctor as D
from memory.doctor_checks_env import check_plugin_source_skew


def _manifest(dirpath: str, name="hippo", version="1.27.0", garbage=False):
    mdir = os.path.join(dirpath, ".claude-plugin")
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, "plugin.json"), "w", encoding="utf-8") as fh:
        fh.write("{broken" if garbage else json.dumps({"name": name, "version": version}))
    return dirpath


def _ctx(repo_root: str, plugin_root: str) -> D.DoctorContext:
    md = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    return D.DoctorContext(md, repo_root, plugin_root=plugin_root, plugin_data="")


def _source_repo(tmp_path, name="hippo", version="1.28.0", garbage=False) -> str:
    repo = str(tmp_path / "src-repo")
    _manifest(os.path.join(repo, "plugin"), name=name, version=version, garbage=garbage)
    return repo


def _running_root(tmp_path, name="hippo", version="1.27.0") -> str:
    return _manifest(str(tmp_path / "plugin-cache" / version), name=name, version=version)


# --------------------------------------------------------------------------- #
# AC1: fires only on the dogfood shape with a version difference
# --------------------------------------------------------------------------- #
def test_fires_on_the_dogfood_shape_with_differing_versions(tmp_path):
    repo = _source_repo(tmp_path, version="1.28.0")
    running = _running_root(tmp_path, version="1.27.0")
    r = check_plugin_source_skew(_ctx(repo, running))
    assert r["status"] == "warn"
    assert "live hooks run v1.27.0 (pinned at session launch)" in r["message"]
    assert "this tree ships v1.28.0" in r["message"]
    # the line names the remediation COMMAND for the human; hippo itself never acts
    assert "claude plugin update + restart" in r["message"]


def test_empty_norm_on_a_non_hippo_source_project(tmp_path):
    repo = str(tmp_path / "plain-repo")
    os.makedirs(repo, exist_ok=True)
    running = _running_root(tmp_path)
    r = check_plugin_source_skew(_ctx(repo, running))
    assert r["status"] == "ok"
    assert "live hooks" not in r["message"]  # no skew claim of any kind


def test_empty_norm_on_equal_versions(tmp_path):
    repo = _source_repo(tmp_path, version="1.27.0")
    running = _running_root(tmp_path, version="1.27.0")
    r = check_plugin_source_skew(_ctx(repo, running))
    assert r["status"] == "ok"
    assert "in sync" in r["message"]


def test_empty_norm_when_hooks_run_the_tree_source_directly(tmp_path):
    """Realpath equality (a from-source dev session): no launch-pin is possible."""
    repo = _source_repo(tmp_path, version="1.28.0")
    r = check_plugin_source_skew(_ctx(repo, os.path.join(repo, "plugin")))
    assert r["status"] == "ok"
    assert "this tree's plugin source directly" in r["message"]


def test_realpath_difference_is_resolved_not_textual(tmp_path):
    """A symlinked plugin root that RESOLVES to the tree's source is still 'directly' —
    the shape test is realpath difference, not string difference."""
    repo = _source_repo(tmp_path, version="1.28.0")
    alias = str(tmp_path / "alias-root")
    os.symlink(os.path.join(repo, "plugin"), alias)
    r = check_plugin_source_skew(_ctx(repo, alias))
    assert r["status"] == "ok"
    assert "this tree's plugin source directly" in r["message"]


def test_empty_norm_on_unreadable_manifests(tmp_path):
    # source manifest is garbage bytes
    repo_bad = _source_repo(tmp_path, garbage=True)
    running = _running_root(tmp_path)
    r = check_plugin_source_skew(_ctx(repo_bad, running))
    assert r["status"] == "ok" and "unreadable" in r["message"]
    # running manifest missing entirely
    repo = _source_repo(tmp_path, version="1.28.0")
    r2 = check_plugin_source_skew(_ctx(repo, str(tmp_path / "no-such-root")))
    assert r2["status"] == "ok" and "unreadable" in r2["message"]


def test_empty_norm_on_name_mismatch(tmp_path):
    """A repo shipping SOME plugin's source that is not the running plugin."""
    repo = _source_repo(tmp_path, name="otherplug", version="9.9.9")
    running = _running_root(tmp_path, version="1.27.0")
    r = check_plugin_source_skew(_ctx(repo, running))
    assert r["status"] == "ok"
    assert "not the running plugin" in r["message"]


def test_empty_norm_when_a_manifest_carries_no_version(tmp_path):
    repo = _source_repo(tmp_path)
    running = str(tmp_path / "plugin-cache" / "nover")
    mdir = os.path.join(running, ".claude-plugin")
    os.makedirs(mdir)
    with open(os.path.join(mdir, "plugin.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "hippo"}, fh)
    r = check_plugin_source_skew(_ctx(repo, running))
    assert r["status"] == "ok"
    assert "no version" in r["message"]


def test_never_raises_on_a_hostile_context(tmp_path):
    """inv2 posture: garbage roots degrade to a line, never a raise."""
    r = check_plugin_source_skew(D.DoctorContext("", "", plugin_root="", plugin_data=""))
    assert r["status"] in ("ok", "warn")


# --------------------------------------------------------------------------- #
# AC2: registry placement — beside DOC-7, tail pins intact
# --------------------------------------------------------------------------- #
def test_registers_beside_doc7_before_the_pinned_tail():
    labels = [label for label, _ in D.CHECKS]
    assert labels.index("plugin_source_skew") == labels.index("plugin_version") + 1
    assert labels[-3:] == ["machine_state", "subset_boundary", "stale_memobot_env"]


# --------------------------------------------------------------------------- #
# AC3: ED4R-3 negative-capability pin — visibility, never coordination
# --------------------------------------------------------------------------- #
def test_check_source_never_executes_anything():
    src = inspect.getsource(check_plugin_source_skew)
    for forbidden in ("subprocess", "os.system", "os.exec", "Popen"):
        assert forbidden not in src, f"ED4R-3: the skew check must not touch {forbidden}"
