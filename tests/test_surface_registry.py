"""INV-1: the verb-surface parity lint — the registry against reality, both directions.

The registry (``memory/surfaces.py``) declares every /hippo:* verb's surface story;
this lint fails when reality disagrees:

  - the skills dir and the registry must list the SAME verbs;
  - the MCP server's ``_DISPATCH`` and the registry's claimed tools must be EQUAL —
    adding a tool without a registry row fails here, naming the registry;
  - each SKILL.md's Desktop routing must match its row (the honest terminal-only
    marker on terminal-only verbs, the named tools on routed verbs — INT-19's class);
  - every routed skill keeps its 'Surface routing' section, the only thing that sends
    it to its MCP tools on Desktop, and nothing shipped says typed /hippo:* commands
    are terminal-only (CLM-8 retired the SessionStart surface note);
  - every nudge/advice string in ``plugin/memory`` (and every SKILL.md) that names a
    runnable command must name one that EXISTS on the surface that renders it: a
    ``/hippo:<verb>`` must be a registered verb, ``the <name> tool`` must be a served
    tool, ``<module> --flag`` must be a real flag of that module, and ``hippo <sub>``
    must be a dispatching bin/hippo subcommand (INT-18's class);
  - nothing on the hot path imports the registry (it is a build-time artifact).

Lint on named-command EXISTENCE, never on prose — copy edits must stay cheap.
"""

from __future__ import annotations

import ast
import os
import re

from memory import mcp_server as M
from memory import surfaces as S

_MEMORY_PKG = os.path.dirname(os.path.abspath(S.__file__))
_PLUGIN_ROOT = os.path.dirname(_MEMORY_PKG)
_SKILLS_DIR = os.path.join(_PLUGIN_ROOT, "skills")
_HOOKS_DIR = os.path.join(_PLUGIN_ROOT, "hooks")
_ASSETS_DIR = os.path.join(_PLUGIN_ROOT, "assets")
_BIN_HIPPO = os.path.join(_PLUGIN_ROOT, "bin", "hippo")

_REGISTRY_HINT = (
    "declare it in plugin/memory/surfaces.py (a verb row's mcp_tools, or VERBLESS_TOOLS) — "
    "every user-facing tool needs a surface story (INV-1)"
)


def _memory_modules() -> dict:
    """``stem -> source text`` for every first-party module in plugin/memory/."""
    out = {}
    for fname in sorted(os.listdir(_MEMORY_PKG)):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(_MEMORY_PKG, fname), encoding="utf-8") as fh:
            out[fname[:-3]] = fh.read()
    return out


def _skill_texts() -> dict:
    """``verb -> SKILL.md text`` for every skill directory."""
    out = {}
    for verb in sorted(os.listdir(_SKILLS_DIR)):
        path = os.path.join(_SKILLS_DIR, verb, "SKILL.md")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                out[verb] = fh.read()
    return out


# --------------------------------------------------------------------------- #
# Registry <-> skills dir <-> _DISPATCH
# --------------------------------------------------------------------------- #
def test_registry_rows_match_skills_dir_exactly():
    skills = set(_skill_texts())
    rows = {v.verb for v in S.VERBS}
    assert rows == skills, (
        f"registry verbs != plugin/skills dirs — missing rows: {sorted(skills - rows)}; "
        f"rows without a skill: {sorted(rows - skills)}. Every /hippo:* verb gets ONE "
        "surface story in plugin/memory/surfaces.py."
    )


def test_every_dispatch_tool_is_claimed_by_the_registry():
    served = set(M._DISPATCH)
    claimed = set(S.claimed_tools())
    unclaimed = sorted(served - claimed)
    assert not unclaimed, f"MCP tool(s) {unclaimed} are served but have no surface story — {_REGISTRY_HINT}"
    phantom = sorted(claimed - served)
    assert not phantom, (
        f"registry claims tool(s) {phantom} that _DISPATCH does not serve — "
        "either add the tool to mcp_server or drop the stale claim in surfaces.py"
    )


def test_registry_shape_is_coherent():
    for v in S.VERBS:
        assert v.desktop in ("tool", "skill_tools", "terminal_only", "route"), v.verb
        if v.desktop in ("terminal_only", "route"):
            assert not v.mcp_tools, f"{v.verb}: {v.desktop} rows must claim no tools"
        else:
            assert v.mcp_tools, f"{v.verb}: a routed verb must name the tool(s) that serve it"
        if v.desktop == "route":
            assert v.routes_to in S.V2_VERBS, f"{v.verb}: routes to {v.routes_to!r}, not a v2 verb"
        else:
            assert v.verb in S.V2_VERBS and not v.routes_to, v.verb
    assert {v.verb for v in S.VERBS if v.desktop != "route"} == set(S.V2_VERBS)
    overlap = set(S.VERBLESS_TOOLS) & {t for v in S.VERBS for t in v.mcp_tools}
    assert not overlap, f"tool(s) {sorted(overlap)} are both verb-bound and verbless — pick one"


# --------------------------------------------------------------------------- #
# SKILL.md Desktop routing parity (the INT-19 class)
# --------------------------------------------------------------------------- #
def test_terminal_only_skills_carry_the_honest_marker():
    texts = _skill_texts()
    for v in S.VERBS:
        text = texts[v.verb]
        if v.desktop == "route":
            assert S.TERMINAL_ONLY_MARKER not in text, v.verb
            continue
        if v.desktop == "terminal_only":
            assert S.TERMINAL_ONLY_MARKER in text, (
                f"skills/{v.verb}/SKILL.md: registry says terminal_only but the preflight "
                f"does not carry the honest marker ({S.TERMINAL_ONLY_MARKER!r}) — a Desktop "
                "user must be told plainly, not left to a dead end (INT-19)"
            )
        else:
            assert S.TERMINAL_ONLY_MARKER not in text, (
                f"skills/{v.verb}/SKILL.md: still claims {S.TERMINAL_ONLY_MARKER!r} but the "
                "registry routes it on Desktop — update the preflight to name the tool route"
            )


def test_routed_skills_name_every_tool_they_drive():
    texts = _skill_texts()
    for v in S.VERBS:
        if v.desktop in ("terminal_only", "route"):
            continue
        for tool in v.mcp_tools:
            assert re.search(rf"\b{re.escape(tool)}\b", texts[v.verb]), (
                f"skills/{v.verb}/SKILL.md never mentions its own MCP tool {tool!r} — "
                "the Desktop route exists only if the skill names it"
            )


def test_routed_skills_keep_their_surface_routing_section():
    """CLM-8: typed /hippo:* runs on Desktop, and each routed skill's own 'Surface routing'
    section is what sends it to its MCP tools there (PLATFORM.md §1). Since INT-20 the bash
    preflight passes on Desktop too, but no verb's bash flow has a Desktop receipt yet, so the
    section stays until one does. It must not lean on the retired SessionStart note."""
    texts = _skill_texts()
    for v in S.VERBS:
        text = texts[v.verb]
        assert "⌨ Surface note" not in text, (
            f"skills/{v.verb}/SKILL.md still cites the retired SessionStart surface note"
        )
        if v.desktop not in ("terminal_only", "route"):
            assert "## Surface routing" in text, (
                f"skills/{v.verb}/SKILL.md lost its 'Surface routing' section — without it "
                "a Desktop session runs its bash flow, which has no Desktop receipt yet"
            )


def test_route_rows_open_their_v2_verb_and_count_the_old_name():
    """SRF-3: a retired name is a one-line route — its SKILL.md names the v2 verb it now
    opens, and its preflight counts the OLD name, the usage the v2.0 removal is decided on."""
    texts = _skill_texts()
    for old, new in S.route_verbs().items():
        text = texts[old]
        assert f"`/hippo:{new}`" in text and f"hippo:{new}" in text, old
        assert re.search(rf"^hippo_note_usage skill {re.escape(old)}\b", text, re.M), old
        assert len(text.splitlines()) < 30, f"{old}: a route stub, not a second copy of the flow"


def test_v2_skills_teach_v2_names_only():
    """SRF-3: the nine verbs name v2 tools and v2 verbs; the v1 names are routes, never advice."""
    texts = _skill_texts()
    deprecated = set(S.DEPRECATED_TOOLS)
    retired = set(S.route_verbs())
    extra = os.path.join(_SKILLS_DIR, "doctor", "audit.md")
    with open(extra, encoding="utf-8") as fh:
        texts = dict(texts, **{"doctor/audit.md": fh.read()})
    bad = []
    for verb, text in texts.items():
        if verb in retired:
            continue
        for pattern in _TOOL_REF_RES:
            for m in pattern.finditer(text):
                name = m.group(1)[len(_WIRE_PREFIX):] if m.group(1).startswith(_WIRE_PREFIX) else m.group(1)
                if name in deprecated:
                    bad.append(f"{verb}: names v1 tool {name!r}")
        for m in re.finditer(r"/hippo:([a-z][a-z0-9-]*)", text):
            if m.group(1) in retired:
                bad.append(f"{verb}: names retired verb /hippo:{m.group(1)}")
    assert not bad, "\n".join(sorted(set(bad)))


# --------------------------------------------------------------------------- #
# CLM-8: nothing shipped may claim typed /hippo:* commands are terminal-only.
# They ran in the Desktop app's Code tab on 2026-10-03 (PLATFORM.md §1). The
# honest per-verb story stays where it lives: TERMINAL_ONLY_MARKER above.
# --------------------------------------------------------------------------- #
_TERMINAL_ONLY_CLAIM = re.compile(
    r"typed\s+`?/hippo:\*`?\s+commands\s+(?:are\s+terminal-only|exist\s+only|work\s+only)",
    re.I,
)
_READMES = (
    os.path.join(os.path.dirname(_PLUGIN_ROOT), "README.md"),
    os.path.join(_PLUGIN_ROOT, "README.md"),
)


def test_no_shipped_text_claims_typed_commands_are_terminal_only():
    texts = list(_all_advice_texts())
    for path in _READMES:
        with open(path, encoding="utf-8") as fh:
            texts.append((os.path.relpath(path, os.path.dirname(_PLUGIN_ROOT)), False, fh.read()))
    bad = [
        f"{origin}: {m.group(0)!r}"
        for origin, _is_doc, text in texts
        for m in _TERMINAL_ONLY_CLAIM.finditer(text)
    ]
    assert not bad, (
        "these strings still say typed /hippo:* commands are terminal-only, which stopped "
        f"being true on the Desktop app (PLATFORM.md §1): {bad}"
    )


# --------------------------------------------------------------------------- #
# bin/hippo subcommand parity
# --------------------------------------------------------------------------- #
def test_bin_hippo_hands_every_verb_but_mcp_to_the_door():
    """SRF-1: the script has exactly two exec arms — the long-lived MCP server, and the
    door (memory.cli) for everything else. A verb added as its own case arm would bypass
    the table the registry and the usage text read."""
    with open(_BIN_HIPPO, encoding="utf-8") as fh:
        script = fh.read()
    execs = re.findall(r"^\s*exec\s+\"\$PY\"\s+-m\s+(memory\.[a-z_]+)", script, re.M)
    assert execs == ["memory.mcp_server", "memory.cli"], execs


def test_bin_hippo_subcommands_match_registry():
    from memory.cli_verbs import CLI_VERBS

    assert S.BIN_HIPPO_SUBCOMMANDS == tuple(v.verb for v in CLI_VERBS)
    assert len(set(S.BIN_HIPPO_SUBCOMMANDS)) == len(S.BIN_HIPPO_SUBCOMMANDS)


# --------------------------------------------------------------------------- #
# Named-command existence (the INT-18 class): every command a string names must
# exist on the surface that renders it. Existence only — never prose.
# --------------------------------------------------------------------------- #

# Tool-NAME references only — prose ("the right tool") must stay invisible to the
# lint (the roadmap's own risk note: lint on named-command existence, never prose).
# A reference counts when it is identifier-shaped: backticked before "tool", an
# underscore-bearing name before "tool", or the full mcp__plugin_hippo_hippo__<name>
# wire form the skills use. Plain single words before "tool" ("the doctor tool") are
# deliberately NOT matched — the registry-driven note/SKILL parity tests above cover
# those from the declaration side, and matching them here would lint prose.
_WIRE_PREFIX = "mcp__plugin_hippo_hippo__"
_TOOL_REF_RES = (
    re.compile(r"`([a-z][a-z_]*)`\s+(?:MCP\s+)?tools?\b"),
    re.compile(r"\b((?:mcp__)?[a-z][a-z]*_[a-z_]+)\s+(?:MCP\s+)?tools?\b"),
    re.compile(r"\b" + _WIRE_PREFIX + r"([a-z_]+)\b"),
)


def _py_string_constants():
    """``(module_stem, is_docstring, text)`` for every str constant in plugin/memory."""
    out = []
    for stem, src in _memory_modules().items():
        tree = ast.parse(src)
        doc_positions = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ) and isinstance(body[0].value.value, str):
                    doc_positions.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append((stem, id(node) in doc_positions, node.value))
    return out


def _all_advice_texts():
    """Every (origin, text) the lint scans: py strings + SKILL.md + hooks + assets."""
    texts = [
        (f"memory/{stem}.py" + (" (docstring)" if is_doc else ""), is_doc, s)
        for stem, is_doc, s in _py_string_constants()
    ]
    for verb, text in _skill_texts().items():
        texts.append((f"skills/{verb}/SKILL.md", False, text))
    for dirpath, base in ((_HOOKS_DIR, "hooks"), (_ASSETS_DIR, "assets")):
        if not os.path.isdir(dirpath):
            continue
        for root, _dirs, files in os.walk(dirpath):
            for fname in sorted(files):
                if fname.endswith((".sh", ".md")):
                    with open(os.path.join(root, fname), encoding="utf-8") as fh:
                        rel = os.path.relpath(os.path.join(root, fname), _PLUGIN_ROOT)
                        texts.append((rel, False, fh.read()))
    return texts


def test_every_named_hippo_verb_exists():
    verbs = {v.verb for v in S.VERBS}
    bad = []
    for origin, _is_doc, text in _all_advice_texts():
        for m in re.finditer(r"/hippo:([a-z][a-z0-9-]*)", text):
            if m.group(1) not in verbs:
                bad.append(f"{origin}: /hippo:{m.group(1)}")
    assert not bad, (
        "advice names /hippo:* verbs that do not exist (INT-18's class):\n  "
        + "\n  ".join(sorted(set(bad)))
    )


def test_every_named_mcp_tool_exists():
    served = set(M._DISPATCH)
    bad = []
    for origin, _is_doc, text in _all_advice_texts():
        for pattern in _TOOL_REF_RES:
            for m in pattern.finditer(text):
                name = m.group(1)
                if name.startswith(_WIRE_PREFIX):
                    name = name[len(_WIRE_PREFIX):]
                if name not in served:
                    bad.append(f"{origin}: names tool {name!r}")
    assert not bad, (
        "advice names MCP tools the server does not serve (INT-18's class):\n  "
        + "\n  ".join(sorted(set(bad)))
    )


def test_every_named_python_module_exists():
    modules = set(_memory_modules())
    bad = []
    for origin, _is_doc, text in _all_advice_texts():
        # `-m memory.<mod>` only — bare "memory.local"/"memory.pre-…" are DIRECTORY
        # names (.claude/memory.local, the snapshot dirs), not module references.
        for m in re.finditer(r"-m\s+memory\.([a-z_][a-z0-9_]*)", text):
            if m.group(1) not in modules:
                bad.append(f"{origin}: python -m memory.{m.group(1)}")
    assert not bad, "advice names memory.* modules that do not exist:\n  " + "\n  ".join(
        sorted(set(bad))
    )


_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]+")


def _flags_missing_from(module_src: str, flags) -> list:
    return [f for f in flags if f not in module_src]


# RWY-1: a façade whose CLI moved into a sibling module still answers ``-m memory.<façade>``
# (its ``main`` delegates), so the flags it advertises are bound to the façade AND the
# sibling that now defines its argparse.
_CLI_SIBLINGS = {
    "recall": "recall_hook",
    "dream": "dream_cli",
    "reconsolidate": "reconsolidate_cli",
}


def _cli_source(modules: dict, name: str) -> str:
    return modules[name] + modules.get(_CLI_SIBLINGS.get(name, ""), "")


def test_every_named_cli_flag_exists_on_its_module():
    """INT-18 itself: 'provenance --reverify' shipped in a nudge; the flag lives in
    reconsolidate. Bind each named flag to the module named alongside it and require
    the flag literal in that module's source."""
    modules = _memory_modules()
    bad = []
    for origin, is_doc, text in _all_advice_texts():
        if is_doc:
            continue  # runtime advice only — docstrings narrate history
        # Form 1: `<module> --flag [--flag ...]` inside one backtick span.
        for span in re.findall(r"`([^`\n]+)`", text):
            m = re.match(r"\s*(?:python\s+-m\s+memory\.|memory\.)?([a-z_][a-z0-9_]*)\s+--", span)
            if m and m.group(1) in modules:
                missing = _flags_missing_from(_cli_source(modules, m.group(1)), _FLAG_RE.findall(span))
                bad += [f"{origin}: `{span}` names {f} but memory/{m.group(1)}.py has no such flag" for f in missing]
        # Form 2: flags scattered through ONE Python advice string that names exactly
        # one `-m memory.<module>` (the cite_derivation nudge's shape). Python string
        # constants only — a SKILL.md/hook is a whole file of unrelated commands, and
        # binding a `git rev-parse --show-toplevel` to whichever module the file
        # mentions is exactly the false positive this form must not produce.
        if not origin.endswith(".py"):
            continue
        named = set(re.findall(r"-m\s+memory\.([a-z_][a-z0-9_]*)", text)) & set(modules)
        if len(named) == 1:
            mod = named.pop()
            missing = _flags_missing_from(_cli_source(modules, mod), _FLAG_RE.findall(text))
            bad += [f"{origin}: names {f} alongside memory.{mod}, which has no such flag" for f in missing]
    assert not bad, "advice names CLI flags their module does not define (INT-18):\n  " + "\n  ".join(
        sorted(set(bad))
    )


def test_every_named_bin_hippo_subcommand_dispatches():
    subs = set(S.BIN_HIPPO_SUBCOMMANDS)
    bad = []
    for origin, is_doc, text in _all_advice_texts():
        if is_doc:
            continue
        for m in re.finditer(r"(?<![/\w-])hippo\s+([a-z][a-z-]+)\s+--", text):
            if m.group(1) not in subs:
                bad.append(f"{origin}: 'hippo {m.group(1)} --…' — bin/hippo has no {m.group(1)!r} subcommand")
        for m in re.finditer(r"`hippo\s+([a-z][a-z-]+)", text):
            if m.group(1) not in subs:
                bad.append(f"{origin}: '`hippo {m.group(1)}…`' — bin/hippo has no {m.group(1)!r} subcommand")
    assert not bad, (
        "advice names bin/hippo subcommands the dispatcher refuses (INT-18's class):\n  "
        + "\n  ".join(sorted(set(bad)))
    )


# --------------------------------------------------------------------------- #
# Zero runtime behavior change: the registry never rides the hot path
# --------------------------------------------------------------------------- #
# The registry's designed OFFLINE consumers (SLP-1's morning report renders each
# section's drain verb per surface from it). The hot path — hooks, the MCP server,
# recall — must still never read it; only deliberate, off-session runners may.
_REGISTRY_CONSUMERS = {"sleep"}


def test_no_runtime_module_imports_the_registry():
    offenders = []
    for stem, src in _memory_modules().items():
        if stem == "surfaces" or stem in _REGISTRY_CONSUMERS:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "surfaces" in node.module:
                offenders.append(stem)
            if isinstance(node, ast.Import) and any("surfaces" in a.name for a in node.names):
                offenders.append(stem)
    assert not offenders, (
        f"plugin/memory module(s) {sorted(set(offenders))} import the surface registry — "
        "it is a build-time artifact; hooks and the server must never read it (INV-1)"
    )
