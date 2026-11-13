"""CON-3: the STABILITY v2 draft is pinned to the tree, so it cannot rot before the cut.

``STABILITY.v2.md`` is the draft contract that takes effect at v2.0.0. Its legacy table
lists every deprecated spelling the tree still reads, and its frozen lists name every
skill, MCP tool, ``hippo.json`` key and ``hippo`` verb. Each test here reads the names
from the code (the tables of record, and an AST scan for ``hippo.json`` keys) and fails
when one is missing from the draft, naming the section to add it to.

ONE-DIRECTIONAL on purpose: every name the tree reads must appear in the draft; the
draft may name more (a surface another branch adds, or one already removed). The
blanket reverse lint over every ``HIPPO_*`` name is NOT built here, for the reason
``test_stability_doc.py`` gives: ranking knobs move freely. Only the twelve frozen
names are pinned doc-to-code.
"""

from __future__ import annotations

import ast
import glob
import importlib
import os
import re

from memory import cli_verbs, mcp_schemas_v2, settings, surfaces

_MEMORY = os.path.dirname(os.path.abspath(surfaces.__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_MEMORY))
_DRAFT = os.path.join(_REPO_ROOT, "STABILITY.v2.md")
_FIX = "Classify it in STABILITY.v2.md (the draft must list every surface the tree reads)."


def _doc() -> str:
    with open(_DRAFT, encoding="utf-8") as fh:
        return fh.read()


def _section(heading: str) -> str:
    """The text under ``heading`` (an exact heading line) up to the next heading of the
    same or a higher level."""
    lines = _doc().splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    assert heading in lines, f"STABILITY.v2.md lost its section {heading!r}"
    start = lines.index(heading) + 1
    out = []
    for line in lines[start:]:
        m = re.match(r"^(#+) ", line)
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    return "\n".join(out)


def _named(text: str) -> set:
    """Every backticked span in ``text``, plus each comma- or space-separated piece of it."""
    spans = set(re.findall(r"`([^`\n]+)`", text))
    return spans | {p for s in spans for p in re.split(r"[\s,=]+", s) if p}


def _missing(names, heading: str) -> list:
    have = _named(_section(heading))
    return sorted(n for n in names if n not in have)


def test_the_draft_is_headed_as_a_draft_for_v2_and_stability_points_to_it():
    head = _doc()[:600]
    assert "draft" in head.lower() and "v2.0.0" in head and "STABILITY.md" in head
    with open(os.path.join(_REPO_ROOT, "STABILITY.md"), encoding="utf-8") as fh:
        assert "(STABILITY.v2.md)" in fh.read(), "STABILITY.md must point at the v2 draft"


# --------------------------------------------------------------------------- #
# The legacy table: every deprecated spelling the tree reads
# --------------------------------------------------------------------------- #
def _legacy_groups():
    from memory import derived_dirs, migrate, provenance_snapshot

    groups = [
        ("### Old MCP tool names", set(mcp_schemas_v2.DEPRECATED)),
        ("### Old skill names", set(surfaces.route_verbs())),
        ("### Old environment names", set(settings.LEGACY_ENV)),
        ("### Policy keys still read from `.format`", set(settings.POLICY_KEYS)),
        ("### Keys of the old `hippo-llm.json` file", set(settings.LEGACY_LLM_FILE_KEYS) | {"HIPPO_LLM_CONFIG"}),
        ("### CLI names that redirect to a skill", set(cli_verbs.SKILL_REDIRECTS)),
        (
            "### Old locations",
            {f".claude/{d}" for d in derived_dirs.LEGACY_DIRNAMES.values()}
            | {provenance_snapshot.LEGACY_PREFIX + "*", provenance_snapshot.V1423_DIRNAME},
        ),
        ("### Frontmatter spellings format 6 renames", set(migrate.LEGACY_KEYS)),
    ]
    # Dream's deprecated tiers ship with the dream reduction; a tree without that module
    # has nothing to pin here.
    try:
        dd = importlib.import_module("memory.dream_deprecated")
    except ImportError:
        dd = None
    if dd is not None:
        groups.append(
            ("### Dream tiers deprecated in v1.45.0", set(dd.deprecated_tiers()) | {dd.REVERSE_REPLAY_ENV})
        )
    return groups


def test_every_legacy_name_in_the_tree_is_in_the_legacy_table():
    gaps = {}
    for heading, names in _legacy_groups():
        assert names, f"the code table behind {heading!r} parsed to nothing"
        miss = _missing(names, heading)
        if miss:
            gaps[heading] = miss
    assert not gaps, f"legacy names the tree reads but the draft does not list: {gaps}. {_FIX}"


def test_every_old_skill_dir_is_a_route_row_or_a_v2_skill():
    dirs = {os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(_MEMORY, "..", "skills", "*", "SKILL.md"))}
    assert dirs, "no skill dirs found"
    assert not _missing(set(surfaces.V2_VERBS), "### Skills"), _FIX
    unknown = dirs - set(surfaces.V2_VERBS) - set(surfaces.route_verbs())
    assert not unknown, f"skill dirs the registry does not classify: {sorted(unknown)}"


# --------------------------------------------------------------------------- #
# The frozen lists
# --------------------------------------------------------------------------- #
def test_every_mcp_tool_is_classified_and_frozen_ones_carry_their_required_inputs():
    from memory import mcp_server

    tools = {t["name"]: t for t in mcp_server._tools_list()}
    assert set(tools) == set(surfaces.claimed_tools())
    deprecated = set(mcp_schemas_v2.DEPRECATED)
    frozen = sorted(set(tools) - deprecated - set(surfaces.VERBLESS_TOOLS))
    table = _section("### MCP tools")
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| `([a-z_]+)` \|[^\n]*", table, re.M)}
    gaps = []
    for name in frozen:
        row = rows.get(name)
        if row is None:
            gaps.append(f"{name}: no row")
            continue
        req = tools[name]["inputSchema"].get("required", [])
        gaps += [f"{name}: required input {r!r} not on its row" for r in req if f"`{r}`" not in row]
    assert not gaps, f"{gaps}. {_FIX}"
    assert set(surfaces.VERBLESS_TOOLS) <= _named(_doc()), _FIX


def test_every_mcp_resource_uri_is_listed():
    from memory import mcp_resources

    uris = {r["uri"] for r in mcp_resources._RESOURCES}
    assert uris and not _missing(uris, "### MCP resources"), _FIX


def _hippo_json_keys() -> dict:
    """Every ``hippo.json`` key the tree reads: a string literal (or a module-level string
    constant) passed as the key to a policy reader, plus the tables of record."""
    from memory import lint_ack, policy_consent

    readers = {"read_policy_key", "_policy", "honoured", "declared"}
    keys: dict = {}
    for path in sorted(glob.glob(os.path.join(_MEMORY, "*.py"))):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        consts = {
            t.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for t in node.targets if isinstance(t, ast.Name)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and len(node.args) >= 2):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            if name not in readers:
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.setdefault(arg.value, os.path.basename(path))
            elif isinstance(arg, ast.Name) and arg.id in consts:
                keys.setdefault(consts[arg.id], os.path.basename(path))
    for k in (*settings.POLICY_KEYS, *policy_consent.SENSITIVE_KEYS, lint_ack.ACK_KEY):
        keys.setdefault(k, "a table of record")
    return keys


def test_every_hippo_json_key_the_tree_reads_is_in_the_schema():
    keys = _hippo_json_keys()
    assert {"write_path", "gate", "journals"} <= set(keys), "the key scan found too little"
    miss = _missing(keys, "### The `hippo.json` schema")
    assert not miss, f"hippo.json keys read in {[keys[k] for k in miss]} but not in the draft: {miss}. {_FIX}"


def test_the_format_marker_keys_are_listed():
    from memory import migrate

    assert not _missing(set(migrate.MARKER_KEYS), "### The corpus format and its marker"), _FIX


def test_every_hippo_verb_is_classified_and_v1_frozen_verbs_stay_frozen():
    table = _section("### `hippo` verbs")
    rows = {m.group(1): m.group(2).strip() for m in re.finditer(r"^\| `([a-z-]+)` \| ([a-z ]+) \|", table, re.M)}
    miss = sorted(v.verb for v in cli_verbs.CLI_VERBS if v.verb not in rows)
    assert not miss, f"hippo verbs without a row: {miss}. {_FIX}"
    thawed = sorted(v.verb for v in cli_verbs.CLI_VERBS if v.frozen and rows[v.verb] != "frozen")
    assert not thawed, f"v1-frozen verbs the draft does not keep frozen: {thawed}"
    internal = sorted(v.verb for v in cli_verbs.CLI_VERBS if v.internal and rows[v.verb] != "internal")
    assert not internal, f"hook entries not classed internal: {internal}"
    assert set(rows.values()) <= {"frozen", "not yet", "internal"}


def test_the_frozen_environment_set_is_at_most_twelve_real_names():
    text = _section("### Environment variables")
    names = sorted(set(re.findall(r"`(HIPPO_[A-Z0-9_]+)`", text)))
    assert 0 < len(names) <= 12, f"the frozen environment set lists {len(names)} names: {names}"
    source = []
    for sub in ("memory", "hooks"):
        for path in glob.glob(os.path.join(_MEMORY, "..", sub, "*")):
            if path.endswith((".py", ".sh")):
                with open(path, encoding="utf-8") as fh:
                    source.append(fh.read())
    hay = "\n".join(source)
    absent = [n for n in names if not re.search(rf"\b{n}\b", hay)]
    assert not absent, f"frozen names the shipped source never reads: {absent}"
    assert not _missing(set(settings.DISABLE_FEATURES), "### Environment variables"), _FIX


# --------------------------------------------------------------------------- #
# Hygiene: the draft is read by plugin users
# --------------------------------------------------------------------------- #
def test_relative_links_resolve():
    text = re.sub(r"```.*?```", "", _doc(), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    dead = [
        t for t in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text)
        if not t.startswith(("http://", "https://", "mailto:", "#"))
        and not os.path.exists(os.path.join(_REPO_ROOT, t.split("#", 1)[0]))
    ]
    assert not dead, f"dead relative links in STABILITY.v2.md: {dead}"


def test_no_roadmap_ids_in_the_draft():
    found = sorted(set(re.findall(r"\b[A-Z]{2,5}-\d+\b", _doc())) - {"UTF-8", "ISO-8601", "SHA-256"})
    assert not found, f"roadmap ids in STABILITY.v2.md (it is read by plugin users): {found}"
