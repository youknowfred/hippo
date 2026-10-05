"""FMT-1: `hippo migrate --check` inventories what format 6 will touch and writes nothing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

from .conftest import write_file
from memory import migrate as MG

_PLUGIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))

NESTED = """---
name: nested
description: "nested shape"
metadata:
  type: project
  cited_paths: ["a.py"]
  source_commit: "abc"
---
body
"""
FLAT = """---
name: flat
description: "flat shape"
type: feedback
cited_paths: ["b.py"]
---
body
"""
LEGACY = """---
name: legacy
description: "legacy relation spelling"
metadata:
  type: project
  derives-from: [nested]
---
body
"""


def _hash_tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[p] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


def _corpus(memory_dir, marker=None):
    write_file(memory_dir, "nested.md", NESTED)
    write_file(memory_dir, "flat.md", FLAT)
    write_file(memory_dir, "legacy.md", LEGACY)
    write_file(memory_dir, "loose.md", "no frontmatter at all\n")
    write_file(memory_dir, "MEMORY.md", "# Memory\n")
    if marker is not None:
        write_file(memory_dir, ".format", json.dumps(marker))


def test_the_inventory_names_every_class(memory_dir):
    _corpus(memory_dir, {"corpus_format": 5, "cite_derivation": 4, "volatile_paths": ["x"]})
    r = MG.check(memory_dir)
    assert r["files"] == 4
    assert r["marker"]["state"] == "ok" and r["marker"]["declared"] == 5
    assert r["cite_derivation"] == 4
    assert r["flat"] == ["flat"]
    assert r["legacy_keys"] == {"derives-from": ["legacy"]}
    assert r["derived_fields"] == ["flat", "nested"]
    assert r["policy_keys"] == ["volatile_paths"]
    assert r["no_frontmatter"] == ["loose"]
    text = MG.render(r)
    assert "format 6 is the next migration" in text and "nothing was written" in text
    assert "derived by extractor 4; this plugin derives" in text


def test_a_missing_marker_reads_as_format_1(memory_dir):
    _corpus(memory_dir)
    r = MG.check(memory_dir)
    assert r["marker"]["state"] == "absent"
    assert "no format marker (read as format 1)" in MG.render(r)


def test_the_check_writes_nothing_in_process(repo, memory_dir):
    _corpus(memory_dir, {"corpus_format": 5})
    before = _hash_tree(repo)
    MG.check(memory_dir)
    assert MG.main(["--check", "--memory-dir", memory_dir]) == 0
    assert _hash_tree(repo) == before


def test_the_verb_writes_nothing_through_the_door(repo, memory_dir, tmp_path):
    _corpus(memory_dir, {"corpus_format": 5, "floor_lint": {}})
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {repo: {"memory_dir": memory_dir}}}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": _PLUGIN, "HIPPO_PROJECTS_FILE": str(registry)}
    before = _hash_tree(repo)
    r = subprocess.run([sys.executable, "-m", "memory.cli", "migrate", "--check", "--all-projects"],
                       capture_output=True, text=True, timeout=60, env=env, cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "policy keys in .format that move to hippo.json: floor_lint" in r.stdout
    assert _hash_tree(repo) == before


def test_without_check_it_refuses(capsys):
    assert MG.main([]) == 2
    assert "only --check" in capsys.readouterr().out


def test_doctor_runs_it(memory_dir, repo):
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_lifecycle import check_format_migration

    _corpus(memory_dir, {"corpus_format": 5, "cite_derivation": 4})
    r = check_format_migration(DoctorContext(memory_dir=memory_dir, repo_root=repo))
    assert r["status"] == "ok"
    assert "1 flat" in r["message"] and "1 legacy key(s)" in r["message"] and "hippo migrate --check" in r["message"]
