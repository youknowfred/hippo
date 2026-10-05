"""CLM-6: one remediation spelling — every hint is a plain intent phrase or `hippo <verb>`.

Hints used to spell the same fix four ways: `python -m memory.<mod> --flag`, a
`python -c "from memory.trust import ..."` one-liner, a v1 skill name, or a v1 MCP tool
name. Since v1.42 the door is `hippo <verb>` (on the Bash tool's PATH), the skills are
nine verbs and the MCP tools ten, so anything hippo prints for a person or the model must
use those. Scope: runtime string constants in plugin/memory (docstrings are history), the
hook scripts, and SKILL.md prose outside code blocks. Existence of each named verb, tool and
flag is held by tests/test_surface_registry.py; this lint holds the SPELLING.
"""

from __future__ import annotations

import ast
import glob
import os
import re

from memory import surfaces as S
from memory.mcp_schemas_v2 import DEPRECATED

_PLUGIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_MEMORY = os.path.join(_PLUGIN, "memory")

_RAW_ENGINE = re.compile(r"(?:\bpython3?|\"\$PY\"|\$PY)\s+-(?:m\s+memory\.|c\s+[\"']?\s*(?:from|import)\s+memory)")

# Strings that are not hints: machine matchers and generated command lines, and code run by
# hippo itself in a subprocess. Each is (module, a substring of the string).
_NOT_HINTS = {
    ("machine_census", "-m memory.sleep"),  # recognizes an existing scheduled job's command line
    ("sleep", " -m memory.sleep"),  # the generated schedule recipe's command line
}
_CODE_PAYLOAD = re.compile(r"^\s*(?:import |from memory\.)")  # `python -c` bodies hippo runs itself

# Modules whose strings DESCRIBE the deprecated names on purpose: the v1 tool schemas (still
# listed through the window), the v2 schemas' deprecation map, and the build-time registry.
_DEPRECATION_HOMES = {"mcp_schemas", "mcp_schemas_packs", "mcp_schemas_v2", "surfaces"}

_RETIRED = sorted(S.route_verbs(), key=len, reverse=True)
_RETIRED_RE = re.compile(r"/hippo:(" + "|".join(map(re.escape, _RETIRED)) + r")\b(?!-)")
_TOOL_REFS = (
    re.compile(r"`([a-z][a-z_]*)`\s+(?:MCP\s+)?tools?\b"),
    re.compile(r"\b([a-z][a-z]*_[a-z_]+)\s+(?:MCP\s+)?tools?\b"),
    re.compile(r"\bthe ([a-z_]+) (?:MCP )?tools?\b"),
    re.compile(r"\bmcp__plugin_hippo_hippo__([a-z_]+)\b"),
)


def _runtime_strings():
    """(module, lineno, text) for every non-docstring str constant in plugin/memory."""
    out = []
    for path in sorted(glob.glob(os.path.join(_MEMORY, "*.py"))):
        stem = os.path.basename(path)[:-3]
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
                out.append((stem, node.lineno, node.value))
    return out


def _prose(text: str) -> str:
    """SKILL.md text outside fenced code blocks."""
    return re.sub(r"^[ \t]*```.*?^[ \t]*```[ \t]*$", "", text, flags=re.M | re.S)


def test_no_hint_spells_a_raw_engine_call():
    bad = []
    for stem, line, text in _runtime_strings():
        if not _RAW_ENGINE.search(text):
            continue
        if _CODE_PAYLOAD.match(text) or any(stem == m and s in text for m, s in _NOT_HINTS):
            continue
        bad.append(f"memory/{stem}.py:{line}: {text.strip()[:100]!r}")
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "hooks", "*.sh"))):
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                if not raw.lstrip().startswith("#") and _RAW_ENGINE.search(raw):
                    bad.append(f"hooks/{os.path.basename(path)}:{n}")
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "skills", "*", "*.md"))):
        with open(path, encoding="utf-8") as fh:
            if _RAW_ENGINE.search(_prose(fh.read())):
                bad.append(os.path.relpath(path, _PLUGIN))
    assert not bad, (
        "hints that spell a raw engine call — use `hippo <verb>` or a plain intent phrase:\n  "
        + "\n  ".join(bad)
    )


def test_no_hint_names_a_retired_verb():
    bad = [
        f"memory/{stem}.py:{line}: /hippo:{m.group(1)}"
        for stem, line, text in _runtime_strings()
        if stem not in _DEPRECATION_HOMES
        for m in _RETIRED_RE.finditer(text)
    ]
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "hooks", "*.sh"))):
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                if not raw.lstrip().startswith("#"):
                    bad += [f"hooks/{os.path.basename(path)}:{n}: /hippo:{m.group(1)}"
                            for m in _RETIRED_RE.finditer(raw)]
    assert not bad, "hints that name a retired verb (name its v2 verb):\n  " + "\n  ".join(bad)


def test_no_hint_names_a_deprecated_tool():
    bad = []
    for stem, line, text in _runtime_strings():
        if stem in _DEPRECATION_HOMES:
            continue
        for pattern in _TOOL_REFS:
            for m in pattern.finditer(text):
                if m.group(1) in DEPRECATED:
                    bad.append(f"memory/{stem}.py:{line}: {m.group(1)!r}")
    assert not bad, (
        "hints that name a deprecated MCP tool (name its v2 route):\n  " + "\n  ".join(sorted(set(bad)))
    )
