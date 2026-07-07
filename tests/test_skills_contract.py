"""Contract tests over the shipped SKILL.md files.

The skills ARE the lifecycle — their embedded code blocks run verbatim in consumers'
shells. This file pins the cross-skill contracts that prose reviews miss (the full
skills-contract suite is a later roadmap item, QUA-8; this seeds it with the shipped
guarantees).
"""

from __future__ import annotations

import glob
import os
import re

_PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_SKILLS_DIR = os.path.join(_PLUGIN_DIR, "skills")
_ALL_SKILLS = sorted(glob.glob(os.path.join(_SKILLS_DIR, "*", "SKILL.md")))
_RESOLVE_PY_SH = os.path.join(_PLUGIN_DIR, "hooks", "_resolve_py.sh")

# The shared ONB-7 preflight guard: unset/empty CLAUDE_PLUGIN_DATA must stop a skill
# BEFORE any code block expands it (`uv venv "/venv"` provisions a root-owned path).
_GUARD = '[ -n "${CLAUDE_PLUGIN_DATA:-}" ] ||'


def test_six_skills_ship():
    names = sorted(os.path.basename(os.path.dirname(p)) for p in _ALL_SKILLS)
    assert names == ["audit", "bootstrap", "doctor", "init", "new", "remove"]


def test_every_skill_carries_the_plugin_data_guard():
    for path in _ALL_SKILLS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert _GUARD in text, (
            f"{os.path.relpath(path)} lacks the shared CLAUDE_PLUGIN_DATA preflight "
            "guard (ONB-7) — an unset var makes its code blocks expand to root paths"
        )


def test_guard_appears_before_any_plugin_data_expansion():
    """The guard must come BEFORE the first code line that expands the variable."""
    for path in _ALL_SKILLS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        guard_pos = text.find(_GUARD)
        # First expansion that is NOT the guard itself / prose backticks: look for the
        # brace-expansion form used in runnable blocks.
        for m in re.finditer(r"\$\{CLAUDE_PLUGIN_DATA\}", text):
            assert guard_pos != -1 and guard_pos < m.start(), (
                f"{os.path.relpath(path)}: ${{CLAUDE_PLUGIN_DATA}} expanded at "
                f"offset {m.start()} before the ONB-7 guard at {guard_pos}"
            )
            break  # only the first expansion matters


# --------------------------------------------------------------------------- #
# DOC-3: frontmatter description budget + shape
# --------------------------------------------------------------------------- #
_DESCRIPTION_BUDGET = 500  # chars — well under the 1024 truncation/validation limit


def _description_of(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^description: (.*)$", text, re.M)
    assert m, f"{os.path.relpath(path)}: no frontmatter description"
    return m.group(1)


def test_skill_descriptions_fit_the_budget():
    for path in _ALL_SKILLS:
        desc = _description_of(path)
        assert len(desc) <= _DESCRIPTION_BUDGET, (
            f"{os.path.relpath(path)}: description is {len(desc)} chars "
            f"(budget {_DESCRIPTION_BUDGET}) — trigger phrases get buried past the "
            "truncation point; move flags/caveats to the body"
        )


def test_skill_descriptions_carry_their_trigger_phrase():
    """Consistency: every description names its own /hippo:<name> invocation."""
    for path in _ALL_SKILLS:
        name = os.path.basename(os.path.dirname(path))
        assert f"/hippo:{name}" in _description_of(path), os.path.relpath(path)


# --------------------------------------------------------------------------- #
# OSP-6: one canonical PY-resolution snippet (hooks/_resolve_py.sh), reused
# everywhere instead of eight hand-rolled copies (two hooks, bin/hippo, five
# skills). This pins the shape, not just presence, so a future surface can't
# silently reintroduce its own inline PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"
# dance.
# --------------------------------------------------------------------------- #
_HOOKS_DIR = os.path.join(_PLUGIN_DIR, "hooks")
_BIN_HIPPO = os.path.join(_PLUGIN_DIR, "bin", "hippo")
_BOTH_HOOKS = [
    os.path.join(_HOOKS_DIR, "memory_session_start.sh"),
    os.path.join(_HOOKS_DIR, "memory_user_prompt.sh"),
]
_ALL_PY_RESOLVING_SURFACES = _BOTH_HOOKS + [_BIN_HIPPO] + _ALL_SKILLS

# A hand-rolled duplicate of the resolver's own fallback dance — the exact
# drift-prone pattern OSP-6 eliminates. Any surface OTHER than _resolve_py.sh
# itself containing this is a regression back to eight independent copies.
_INLINE_DUPLICATE_RE = re.compile(
    r'PY="\$\{CLAUDE_PLUGIN_DATA:-\}/venv/bin/python"'
)
# How a surface "routes through" the canonical resolver: sourcing the shared
# file (a plain `source`/`.` line naming _resolve_py.sh).
_SOURCE_LINE_RE = re.compile(r'(?:^|\s)\.\s+["\'$][^\n]*_resolve_py\.sh')


def test_resolve_py_snippet_exists_and_defines_the_canonical_function():
    assert os.path.isfile(_RESOLVE_PY_SH), "plugin/hooks/_resolve_py.sh is missing"
    with open(_RESOLVE_PY_SH, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "hippo_resolve_py()" in text
    assert 'PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"' in text
    assert 'PY="python3"' in text
    assert "export PYTHONPATH=" in text


def test_no_surface_hand_rolls_its_own_py_resolution():
    """No hook/bin/skill outside _resolve_py.sh itself duplicates the fallback dance."""
    for path in _ALL_PY_RESOLVING_SURFACES:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert not _INLINE_DUPLICATE_RE.search(text), (
            f"{os.path.relpath(path, _PLUGIN_DIR)} hand-rolls its own PY-resolution "
            "dance instead of sourcing the canonical hooks/_resolve_py.sh (OSP-6)"
        )


def test_both_hooks_source_the_canonical_resolver():
    for path in _BOTH_HOOKS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert _SOURCE_LINE_RE.search(text), (
            f"{os.path.relpath(path, _PLUGIN_DIR)} does not source _resolve_py.sh"
        )
        assert "hippo_resolve_py" in text


def test_bin_hippo_sources_the_canonical_resolver():
    with open(_BIN_HIPPO, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert _SOURCE_LINE_RE.search(text), "bin/hippo does not source _resolve_py.sh"
    assert "hippo_resolve_py" in text


def test_every_skill_routes_through_the_canonical_resolver():
    """Every SKILL.md either sources _resolve_py.sh directly, or (like bootstrap,
    which builds the venv itself rather than choosing between venv/bare-python3)
    never performs a PY-selection fallback at all."""
    for path in _ALL_SKILLS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        name = os.path.basename(os.path.dirname(path))
        if name == "bootstrap":
            # Bootstrap constructs the venv; it never chooses between venv/bare
            # python3, so it has nothing to route through the resolver.
            continue
        assert _SOURCE_LINE_RE.search(text) or "hippo_resolve_py" in text, (
            f"{os.path.relpath(path, _PLUGIN_DIR)} does not route PY resolution "
            "through hooks/_resolve_py.sh (OSP-6)"
        )


# --------------------------------------------------------------------------- #
# COR-7: init's corpus-format seeding snippet stays in lockstep with the constant.
# Steps 1-2 of init run BEFORE $PY is resolved, so the SKILL.md snippet writes a
# LITERAL number instead of calling memory.provenance.write_corpus_format — this
# parity pin (the same cross-language pattern as test_fastembed_cache_path.py)
# is what makes a CORPUS_FORMAT_VERSION bump a one-constant change: bump the
# constant and this test names the exact init surface that must follow.
# --------------------------------------------------------------------------- #
_INIT_SKILL = os.path.join(_SKILLS_DIR, "init", "SKILL.md")


def test_init_skill_seeds_the_canonical_corpus_format():
    from memory.provenance import CORPUS_FORMAT_VERSION

    with open(_INIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert ".claude/memory/.format" in text, (
        "init SKILL.md no longer seeds the corpus format marker (COR-7 step 2b)"
    )
    literals = re.findall(r'\{"corpus_format":\s*(\d+)\}', text)
    assert literals, "init SKILL.md carries no {\"corpus_format\": N} literal to pin"
    assert all(int(v) == CORPUS_FORMAT_VERSION for v in literals), (
        f"init SKILL.md seeds corpus_format {sorted(set(literals))}, but "
        f"memory.provenance.CORPUS_FORMAT_VERSION is {CORPUS_FORMAT_VERSION} — "
        "update the SKILL.md snippet in the same change that bumps the constant"
    )
