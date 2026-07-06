"""Tests for memory/_vendor — the bare-python3 pre-bootstrap fallbacks (ONB-2).

Two claims are pinned here:
  1. Parity — the vendored BM25 scores identically to the pinned rank_bm25, and
     miniyaml parses the frontmatter subset identically to PyYAML (including
     REJECTING what PyYAML rejects — degraded mode must not hide broken memories).
  2. The end-to-end promise — a stock python3 with NONE of the pinned deps serves
     real BM25 recall through the actual hook script, importing neither numpy nor
     fastembed on the way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv

import pytest

from memory._vendor import miniyaml
from memory._vendor.bm25 import BM25Okapi as VendoredBM25

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))


# --------------------------------------------------------------------------- #
# BM25 parity with the pinned rank_bm25
# --------------------------------------------------------------------------- #
_CORPUS = [
    "zebra deploy canary rollout pager escalation".split(),
    "postgres catalog bucket warehouse lakehouse files".split(),
    "excel header rescue inference column layout".split(),
    "deploy deploy deploy repeated token document".split(),
    "the common token appears in every document deploy".split(),
]


def test_vendored_bm25_scores_match_rank_bm25():
    rank_bm25 = pytest.importorskip("rank_bm25")
    queries = [
        ["deploy", "canary"],
        ["postgres", "warehouse"],
        ["deploy"],  # high-df token — exercises the negative-idf epsilon floor
        ["nonexistent"],
        ["excel", "deploy", "catalog"],
    ]
    theirs = rank_bm25.BM25Okapi(_CORPUS)
    ours = VendoredBM25(_CORPUS)
    assert ours.corpus_size == len(_CORPUS)
    for q in queries:
        expected = list(theirs.get_scores(q))
        got = ours.get_scores(q)
        assert got == pytest.approx(expected, abs=1e-9), q


def test_vendored_bm25_empty_corpus_and_query():
    assert VendoredBM25([]).get_scores(["x"]) == []
    assert VendoredBM25(_CORPUS).get_scores([]) == [0.0] * len(_CORPUS)


# --------------------------------------------------------------------------- #
# miniyaml parity with PyYAML on the frontmatter subset
# --------------------------------------------------------------------------- #
_FRONTMATTER_SAMPLES = [
    # new_memory._render_frontmatter shape
    'name: my_memory\ndescription: "a one-line hook: with a colon inside quotes"\n'
    "metadata:\n  type: feedback\n",
    # provenance.backfill_text shapes (flat + nested-metadata, flow lists)
    'name: m\ncited_paths: ["src/a.py", "src/b.py"]\nsource_commit: "abc123"\n',
    'name: m\nmetadata:\n  type: project\n  cited_paths: []\n  source_commit: ""\n',
    # scalars, comments, blanks, block lists
    "# full-line comment\nname: bare_scalar\ncount: 3\nratio: 0.5\nflag: true\n"
    "empty:\n\nitems:\n  - one\n  - two\n",
    # single quotes
    "name: 'single ''quoted'' value'\n",
]


def test_miniyaml_matches_pyyaml_on_the_subset():
    yaml = pytest.importorskip("yaml")
    for sample in _FRONTMATTER_SAMPLES:
        assert miniyaml.safe_load(sample) == yaml.safe_load(sample), sample


def test_miniyaml_rejects_what_pyyaml_rejects():
    yaml = pytest.importorskip("yaml")
    # THE corpus hazard: an unquoted value containing ': '. PyYAML raises; the
    # degraded path must too, or broken memories become invisible pre-bootstrap.
    bad = "name: m\ndescription: unquoted: colon value\n"
    with pytest.raises(Exception):
        yaml.safe_load(bad)
    with pytest.raises(miniyaml.MiniYamlError):
        miniyaml.safe_load(bad)


def test_miniyaml_rejects_block_scalars_and_tabs():
    with pytest.raises(miniyaml.MiniYamlError):
        miniyaml.safe_load("description: >-\n  folded text\n")
    with pytest.raises(miniyaml.MiniYamlError):
        miniyaml.safe_load("name: x\n\tindented: tab\n")


def test_miniyaml_empty_and_none():
    assert miniyaml.safe_load("") is None
    assert miniyaml.safe_load("# only a comment\n") is None


def test_parse_frontmatter_works_through_the_fallback(monkeypatch):
    """provenance.parse_frontmatter with PyYAML replaced by miniyaml — same output."""
    from memory import provenance as P

    text = '---\nname: m\ndescription: "d"\nmetadata:\n  type: user\n---\nbody\n'
    expected = P.parse_frontmatter(text)
    monkeypatch.setattr(P, "yaml", miniyaml)
    assert P.parse_frontmatter(text) == expected == {
        "name": "m", "description": "d", "metadata": {"type": "user"}
    }


# --------------------------------------------------------------------------- #
# The end-to-end promise: stock python3, no deps, real BM25 recall
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def bare_python(tmp_path_factory) -> str:
    """A venv with NO site-packages installs — a stand-in for stock python3."""
    root = tmp_path_factory.mktemp("bare-venv")
    venv.EnvBuilder(with_pip=False).create(str(root))
    py = os.path.join(str(root), "bin", "python3")
    if not os.path.exists(py):
        py = os.path.join(str(root), "bin", "python")
    # Sanity: the pinned deps must genuinely be absent in this interpreter.
    probe = subprocess.run(
        [py, "-c", "import yaml"], capture_output=True, text=True, timeout=30
    )
    assert probe.returncode != 0, "bare venv unexpectedly has PyYAML — fixture broken"
    return py


_MEMORY_MD = """---
name: zebra_deploy_runbook
description: "How the zebra service is deployed — rollout order, canary steps, and the pager escalation path."
metadata:
  type: project
---

Deploy zebra via the canary lane first; page the on-call if step two stalls.
"""


def _seed_project(tmp_path) -> str:
    project = tmp_path / "project"
    memdir = project / ".claude" / "memory"
    os.makedirs(memdir, exist_ok=True)
    (memdir / "zebra_deploy_runbook.md").write_text(_MEMORY_MD, encoding="utf-8")
    (memdir / "MEMORY.md").write_text("# Memory Index\n\n## User\n", encoding="utf-8")
    return str(project)


def test_bare_python3_serves_bm25_recall_through_the_hook(tmp_path, bare_python):
    """The ONB-2 acceptance test: NO venv, stock python3 → the UserPromptSubmit hook
    returns real BM25 results (the pre-bootstrap claim, finally true)."""
    project = _seed_project(tmp_path)
    bindir = tmp_path / "bin"
    os.makedirs(bindir)
    import shutil as _shutil

    for tool in ("cat", "printf"):
        real = _shutil.which(tool)
        if real:
            os.symlink(real, bindir / tool)
    os.symlink(bare_python, bindir / "python3")
    home = tmp_path / "home"
    os.makedirs(home)
    data_dir = tmp_path / "plugin-data"  # exists but has NO venv — pre-bootstrap
    os.makedirs(data_dir)

    hook = os.path.join(_PLUGIN_ROOT, "hooks", "memory_user_prompt.sh")
    proc = subprocess.run(
        ["/bin/bash", hook],
        input=json.dumps({"prompt": "how is the zebra service deployed with canary rollout"}),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": str(bindir),
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": project,
            "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(data_dir),
        },
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert out, "bare python3 must serve BM25 recall — the pre-bootstrap claim"
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "zebra_deploy_runbook" in ctx


def test_vendored_path_imports_no_numpy_or_fastembed(tmp_path, bare_python):
    """ONB-2 acceptance: the vendored path must not touch numpy/fastembed (they are
    not even installed here — this asserts recall neither needs nor imports them)."""
    project = _seed_project(tmp_path)
    md = os.path.join(project, ".claude", "memory")
    probe = (
        "import sys, json\n"
        "from memory.recall import recall\n"
        f"r = recall('zebra canary deploy', memory_dir={md!r})\n"
        "print(json.dumps({'names': [x['name'] for x in r],"
        " 'backend': r[0]['backend'] if r else None,"
        " 'numpy': 'numpy' in sys.modules, 'fastembed': 'fastembed' in sys.modules}))\n"
    )
    proc = subprocess.run(
        [bare_python, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": _PLUGIN_ROOT, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip())
    assert "zebra_deploy_runbook" in data["names"]
    assert data["backend"] == "bm25"
    assert data["numpy"] is False and data["fastembed"] is False
