"""FMT-3: readers refuse a newer corpus format loudly; a garbled marker is never silent;
doctor compares the installed plugin version with the running one."""

from __future__ import annotations

import json
import os

import pytest

from memory import build_index as B
from memory import session_start as SS
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_platform import check_installed_version
from memory.jit import _corpus_trusted
from memory.provenance import CORPUS_FORMAT_VERSION, injection_refusal, marker_state
from memory.recall import recall
from memory.session_start_health import corpus_format_producer


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = tmp_path / "proj" / ".claude" / "memory"
    md.mkdir(parents=True)
    (md / "deploy.md").write_text(
        '---\nname: deploy\ndescription: "deploy rollback steps"\ntype: project\n---\nbody\n',
        encoding="utf-8",
    )
    (md / "MEMORY.md").write_text("# Memory\n\n## User\n", encoding="utf-8")
    B.build_index(str(md))
    return str(md)


def _marker(md, text):
    with open(os.path.join(md, ".format"), "w", encoding="utf-8") as fh:
        fh.write(text)


def test_a_newer_format_injects_nothing_and_says_why(corpus):
    assert recall("deploy rollback steps", memory_dir=corpus)  # baseline: it recalls
    _marker(corpus, json.dumps({"corpus_format": CORPUS_FORMAT_VERSION + 1}))
    log: dict = {}
    assert recall("deploy rollback steps", memory_dir=corpus, drop_log=log) == []
    assert "reads up to" in log["refused"]
    assert injection_refusal(corpus) and not _corpus_trusted(corpus, None)
    ctx = SS.build_context(corpus, os.path.dirname(os.path.dirname(corpus)))
    assert ctx.startswith("⚠ Corpus format —") and "Update the hippo plugin" in ctx
    assert "\n" not in ctx.strip()  # the integrity line is ALL that shows


def test_the_current_format_is_unaffected(corpus):
    _marker(corpus, json.dumps({"corpus_format": CORPUS_FORMAT_VERSION}))
    assert marker_state(corpus)["state"] == "ok" and injection_refusal(corpus) is None
    assert recall("deploy rollback steps", memory_dir=corpus)


def test_a_garbled_marker_is_named_not_silent(corpus):
    _marker(corpus, "{not json")
    assert marker_state(corpus)["state"] == "unreadable"
    assert injection_refusal(corpus) is None  # still read as format 1, never refused
    assert recall("deploy rollback steps", memory_dir=corpus)
    line = corpus_format_producer(corpus, os.path.dirname(corpus))
    assert line and "unreadable" in line and "format 1" in line


def test_doctor_names_the_garbled_marker(corpus):
    from memory.doctor_checks_corpus import check_format_version

    _marker(corpus, json.dumps({"corpus_format": "five"}))
    r = check_format_version(DoctorContext(memory_dir=corpus, repo_root=None))
    assert r["status"] == "warn" and "unreadable" in r["message"]


def _registry(tmp_path, monkeypatch, versions):
    cfg = tmp_path / "cfg"
    (cfg / "plugins").mkdir(parents=True)
    doc = {"version": 2, "plugins": {"hippo@hippo": [{"version": v} for v in versions]}}
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))


def _plugin_root(tmp_path, version):
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
    return str(root)


def test_installed_vs_running(tmp_path, monkeypatch):
    ctx = lambda v: DoctorContext(memory_dir=str(tmp_path), repo_root=None,  # noqa: E731
                                  plugin_root=_plugin_root(tmp_path, v))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "none"))
    assert "nothing to compare" in check_installed_version(ctx("1.41.0"))["message"]
    _registry(tmp_path, monkeypatch, ["1.41.0"])
    assert check_installed_version(ctx("1.41.0"))["status"] == "ok"
    r = check_installed_version(ctx("1.39.1"))
    assert r["status"] == "warn" and "restart Claude Code sessions" in r["message"]
    assert "ahead of the install" in check_installed_version(ctx("1.42.0"))["message"]
