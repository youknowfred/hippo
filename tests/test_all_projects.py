"""RCH-4 — /hippo:recall --all-projects: explicit, trust-gated cross-project recall.

Modeled on test_two_tier.py: every corpus is a tmp dir, BM25-only. The SEC-1 legs build
REAL git repos (``gate_repo_root`` resolves through ``git rev-parse`` — a non-git corpus
is deliberately gate-inapplicable) and delete the conftest ``HIPPO_TRUST_ALL`` bypass.
The registry rides ``HIPPO_PROJECTS_FILE`` (conftest points it at tmp, mirroring
``HIPPO_TRUST_FILE``). Pins both acceptance criteria — hits labeled by source repo, an
untrusted registered source contributes NOTHING — plus the hook-path-never-involved pin.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess

from memory import build_index as B
from memory import recall as R
from memory import recall_view as V
from memory import registry as REG
from memory import trust as T

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _mem(name: str, description: str) -> str:
    return (
        f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n'
        f"  type: project\n---\nbody of {name}\n"
    )


def _seed(memory_dir: str, items: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for stem, desc in items.items():
        with open(os.path.join(memory_dir, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(_mem(stem, desc))


def _project(tmp_path, name: str, items: dict, *, git: bool = False) -> tuple:
    """A project checkout: <tmp>/<name>/.claude/memory (+ optional real git repo)."""
    root = str(tmp_path / name)
    md = os.path.join(root, ".claude", "memory")
    _seed(md, items)
    if git:
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", root, "commit", "-q", "-m", "seed"], check=True, env=_GIT_ENV
        )
    return root, md


def _current(tmp_path, monkeypatch, items: dict, *, git: bool = False) -> tuple:
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    root, md = _project(tmp_path, "current", items, git=git)
    idx = os.path.join(root, ".claude", ".memory-index")
    B.build_index(md, idx)
    return root, md, idx


# --------------------------------------------------------------------------- #
# registry.py — the machine-local project list
# --------------------------------------------------------------------------- #
def test_registry_round_trip_and_env_override(tmp_path, monkeypatch):
    reg_file = str(tmp_path / "projects.json")
    monkeypatch.setenv("HIPPO_PROJECTS_FILE", reg_file)
    assert REG.projects_registry_path() == reg_file
    root, md = _project(tmp_path, "alpha", {"m": "d"})
    assert REG.register_project(root, md) is True
    got = REG.registered_projects()
    key = os.path.realpath(root)
    assert key in got and got[key]["memory_dir"] == md
    assert got[key]["registered_at"]
    assert REG.deregister_project(root) is True
    assert REG.registered_projects() == {}
    assert REG.deregister_project(root) is True  # idempotent


def test_registry_self_heals_vanished_memory_dirs(tmp_path, monkeypatch):
    root, md = _project(tmp_path, "gone", {"m": "d"})
    assert REG.register_project(root, md)
    import shutil

    shutil.rmtree(md)
    assert REG.registered_projects() == {}  # skipped at read time...
    with open(REG.projects_registry_path(), encoding="utf-8") as fh:
        assert os.path.realpath(root) in json.load(fh)["projects"]  # ...never auto-pruned


def test_registry_never_raises_and_preserves_sibling_keys(tmp_path, monkeypatch):
    path = REG.projects_registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert REG.registered_projects() == {}
    root, md = _project(tmp_path, "beta", {"m": "d"})
    assert REG.register_project(root, md)  # corrupt file degrades to a fresh document
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"projects": {}, "future_sibling": {"kept": True}}, fh)
    assert REG.register_project(root, md)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["future_sibling"] == {"kept": True}


# --------------------------------------------------------------------------- #
# recall_all_projects — fusion, labels, precedence
# --------------------------------------------------------------------------- #
def test_hits_labeled_by_source_repo_basename(tmp_path, monkeypatch):
    _, md, idx = _current(
        tmp_path, monkeypatch, {"local-fact": "how the current build pipeline caches"}
    )
    other_root, other_md = _project(
        tmp_path, "widgetlib", {"canvas-trick": "widget canvas rendering fallback trick"}
    )
    REG.register_project(other_root, other_md)

    res = R.recall_all_projects(
        "widget canvas rendering trick", k=5, memory_dir=md, index_dir=idx
    )
    byname = {h["name"]: h for h in res["hits"]}
    assert "canvas-trick" in byname, "a registered corpus's memory must be searchable"
    assert byname["canvas-trick"]["corpus"] == "widgetlib"
    assert byname["canvas-trick"]["root"] == other_md
    assert "widgetlib" in res["searched"] and "project" in res["searched"]
    assert res["skipped_untrusted"] == [] and res["skipped_unavailable"] == []

    local = R.recall_all_projects(
        "current build pipeline caches", k=5, memory_dir=md, index_dir=idx
    )
    top = next(h for h in local["hits"] if h["name"] == "local-fact")
    assert top["corpus"] == "project"  # the current project keeps its tier label


def test_current_project_wins_slug_collisions(tmp_path, monkeypatch):
    _, md, idx = _current(
        tmp_path, monkeypatch, {"deploy": "current project deploy pipeline steps"}
    )
    other_root, other_md = _project(
        tmp_path, "otherproj", {"deploy": "other project deploy pipeline steps"}
    )
    REG.register_project(other_root, other_md)
    res = R.recall_all_projects("deploy pipeline steps", k=5, memory_dir=md, index_dir=idx)
    hits = [h for h in res["hits"] if h["name"] == "deploy"]
    assert len(hits) == 1 and hits[0]["corpus"] == "project"  # first-wins by name


def test_user_tier_still_fuses(tmp_path, monkeypatch):
    _, md, idx = _current(tmp_path, monkeypatch, {"local-fact": "current project fact"})
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    _seed(user, {"tabs-pref": "always indent with tabs never spaces preference"})
    res = R.recall_all_projects("indent with tabs preference", k=5, memory_dir=md, index_dir=idx)
    hit = next(h for h in res["hits"] if h["name"] == "tabs-pref")
    assert hit["corpus"] == "user" and "user" in res["searched"]


def test_same_basename_registrations_stay_distinguishable(tmp_path, monkeypatch):
    _, md, idx = _current(tmp_path, monkeypatch, {"m": "current fact"})
    root_a, md_a = _project(tmp_path / "one", "hippo", {"a-fact": "alpha clone lesson about parsers"})
    root_b, md_b = _project(tmp_path / "two", "hippo", {"b-fact": "beta clone lesson about lexers"})
    REG.register_project(root_a, md_a)
    REG.register_project(root_b, md_b)
    res = R.recall_all_projects("clone lesson parsers lexers", k=5, memory_dir=md, index_dir=idx)
    labels = {h["corpus"] for h in res["hits"] if h["name"] in ("a-fact", "b-fact")}
    assert labels == {"hippo", "hippo~2"}
    assert set(res["searched"]) >= {"hippo", "hippo~2"}


def test_current_project_not_double_searched_when_self_registered(tmp_path, monkeypatch):
    # init registers EVERY project — including the one you are standing in.
    root, md, idx = _current(tmp_path, monkeypatch, {"m": "the current fact here"})
    REG.register_project(root, md)
    res = R.recall_all_projects("the current fact here", k=5, memory_dir=md, index_dir=idx)
    assert res["searched"].count("project") == 1
    assert "current" not in res["searched"]  # never re-listed under its basename
    assert [h["name"] for h in res["hits"]].count("m") == 1


def test_empty_registered_corpus_is_a_named_skip(tmp_path, monkeypatch):
    _, md, idx = _current(tmp_path, monkeypatch, {"m": "the current fact"})
    other_root, other_md = _project(tmp_path, "hollow", {})
    os.makedirs(other_md, exist_ok=True)
    REG.register_project(other_root, other_md)
    res = R.recall_all_projects("anything at all", k=5, memory_dir=md, index_dir=idx)
    assert res["skipped_unavailable"] == ["hollow"]


# --------------------------------------------------------------------------- #
# The SEC-1 legs — every source gated at query time
# --------------------------------------------------------------------------- #
def test_untrusted_registered_corpus_contributes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    # SEC-12 gates non-git corpora too; this test's focus is the GIT (cloned) registered
    # corpus, so opt the non-git current/local corpora out via the documented override.
    monkeypatch.setenv("HIPPO_TRUST_NONGIT", "1")
    _, md, idx = _current(tmp_path, monkeypatch, {"m": "current fact"})  # non-git: opted out
    evil_root, evil_md = _project(
        tmp_path, "evilclone", {"payload": "widget canvas rendering fallback trick"},
        git=True,
    )
    REG.register_project(evil_root, evil_md)

    res = R.recall_all_projects("widget canvas rendering trick", k=5, memory_dir=md, index_dir=idx)
    assert all(h["name"] != "payload" for h in res["hits"]), "untrusted must contribute NOTHING"
    assert res["skipped_untrusted"] == ["evilclone"]
    assert "evilclone" not in res["searched"]

    assert T.mark_trusted(evil_root)  # the user reviews + trusts -> it joins the search
    res2 = R.recall_all_projects("widget canvas rendering trick", k=5, memory_dir=md, index_dir=idx)
    assert any(h["name"] == "payload" for h in res2["hits"])
    assert res2["skipped_untrusted"] == []


def test_untrusted_current_project_is_skipped_but_trusted_sources_still_serve(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _, md, idx = _current(tmp_path, monkeypatch, {"m": "current fact"}, git=True)
    other_root, other_md = _project(
        tmp_path, "goodproj", {"lesson": "widget canvas rendering fallback trick"}, git=True
    )
    T.mark_trusted(other_root)
    REG.register_project(other_root, other_md)
    res = R.recall_all_projects("widget canvas rendering trick", k=5, memory_dir=md, index_dir=idx)
    assert "project" in res["skipped_untrusted"]
    assert all(h["name"] != "m" for h in res["hits"])
    assert any(h["name"] == "lesson" for h in res["hits"])


# --------------------------------------------------------------------------- #
# Surfaces: the describe() renderer + the hook-path negative pin
# --------------------------------------------------------------------------- #
def test_describe_all_projects_labels_and_sources_trailer(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    # SEC-12: widgetlib + the current project are non-git; this test checks provenance
    # LABELING (not the gate), so opt the non-git corpora in via the documented override.
    monkeypatch.setenv("HIPPO_TRUST_NONGIT", "1")
    _, md, idx = _current(tmp_path, monkeypatch, {"m": "current fact"})
    good_root, good_md = _project(
        tmp_path, "widgetlib", {"canvas-trick": "widget canvas rendering fallback trick"}
    )
    evil_root, evil_md = _project(tmp_path, "evilclone", {"payload": "anything"}, git=True)
    REG.register_project(good_root, good_md)
    REG.register_project(evil_root, evil_md)

    view = V.describe(
        "widget canvas rendering trick", 5, memory_dir=md, index_dir=idx, all_projects=True
    )
    assert "from widgetlib" in view  # cross-project provenance on the hit line
    assert "1 corpus skipped: evilclone — untrusted" in view
    assert "searched: " in view


def test_describe_all_projects_abstention_still_names_sources(tmp_path, monkeypatch):
    _, md, idx = _current(tmp_path, monkeypatch, {"m": "current fact"})
    view = V.describe(
        "watering indoor houseplants in winter", 5,
        memory_dir=md, index_dir=idx, all_projects=True,
    )
    assert "No memories cleared the relevance floor" in view
    assert "searched: project" in view


def test_format_results_falls_through_to_generic_label(tmp_path):
    hit = {"name": "x", "file": "x.md", "description": "d", "corpus": "widgetlib"}
    assert "(widgetlib)" in R.format_results([hit])
    plain = {"name": "x", "file": "x.md", "description": "d"}
    assert "(widgetlib)" not in R.format_results([plain])
    project = {"name": "x", "file": "x.md", "description": "d", "corpus": "project"}
    assert R.format_results([project]) == R.format_results([plain])  # byte-identical


def test_hook_path_never_involves_all_projects():
    # inv6 + the item's own AC: the stdin-json hook entry and the per-prompt recall()
    # never touch cross-project fusion — it is an explicit-surface feature only.
    assert "all_projects" not in inspect.getsource(R.main)
    assert "all_projects" not in inspect.getsource(R.recall)
