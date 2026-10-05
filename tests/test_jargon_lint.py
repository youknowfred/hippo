"""CLM-5: no roadmap ids in anything a person or the model reads.

Ids like the ones the roadmaps assign (two to five capitals, a dash, a number) mean nothing
outside this repo's history, yet they had spread into every SKILL.md, into hook-injected
text, doctor output and MCP descriptions. This lint fails on one anywhere that text ships:

  - every skill file (SKILL.md and its supporting flow files), frontmatter included
  - runtime string constants in plugin/memory: doctor and SessionStart lines, MCP
    descriptions and handler output, CLI help (docstrings are history, so they are allowed)
  - the strings hook scripts print (shell comments are allowed)
  - the live MCP listings (tools and resources) and a real doctor run

Allowlisted on purpose: comments and docstrings, the CHANGELOG and the roadmaps, and
`surfaces.py` (a build-time registry whose notes no user sees). A few tokens share the
shape and are not ids (UTF-8, ISO-8601, SHA-256).
"""

from __future__ import annotations

import ast
import glob
import json
import os
import re

_PLUGIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_ID = re.compile(r"\b[A-Z]{2,5}-\d+\b")
_NOT_IDS = {"UTF-8", "ISO-8601", "SHA-256"}
_BUILD_TIME_ONLY = {"surfaces"}


def _ids(text: str):
    return sorted({m.group(0) for m in _ID.finditer(text)} - _NOT_IDS)


def test_skills_carry_no_roadmap_ids():
    bad = []
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "skills", "*", "*.md"))):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                found = _ids(line)
                if found:
                    bad.append(f"{os.path.relpath(path, _PLUGIN)}:{n}: {', '.join(found)}")
    assert not bad, "roadmap ids in skill text:\n  " + "\n  ".join(bad)


def test_runtime_strings_carry_no_roadmap_ids():
    bad = []
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "memory", "*.py"))):
        stem = os.path.basename(path)[:-3]
        if stem in _BUILD_TIME_ONLY:
            continue
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docs.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                found = _ids(node.value)
                if found:
                    bad.append(f"memory/{stem}.py:{node.lineno}: {', '.join(found)}")
    assert not bad, "roadmap ids in runtime strings:\n  " + "\n  ".join(bad)


def _strip_shell_comment(line: str) -> str:
    """The line up to an unquoted `#` that starts a comment."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def test_hook_output_carries_no_roadmap_ids():
    bad = []
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "hooks", "*.sh"))):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                found = _ids(_strip_shell_comment(line))
                if found:
                    bad.append(f"hooks/{os.path.basename(path)}:{n}: {', '.join(found)}")
    assert not bad, "roadmap ids in hook text:\n  " + "\n  ".join(bad)


def test_mcp_listings_carry_no_roadmap_ids(monkeypatch):
    from memory import mcp_server as M

    monkeypatch.setattr(M, "_NEGOTIATED", "2025-06-18")
    tools = M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    resources = M.handle_request({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})["result"]
    found = _ids(json.dumps(tools) + json.dumps(resources))
    assert not found, f"roadmap ids in the MCP listings: {found}"


def test_a_doctor_run_carries_no_roadmap_ids(repo, memory_dir):
    from memory.doctor import DoctorContext, render

    with open(os.path.join(memory_dir, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: alpha\ndescription: "a fact"\nmetadata:\n  type: project\n---\nbody\n')
    found = _ids(render(DoctorContext(memory_dir, repo)))
    assert not found, f"roadmap ids in doctor output: {found}"
