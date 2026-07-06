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

_SKILLS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "plugin", "skills")
)
_ALL_SKILLS = sorted(glob.glob(os.path.join(_SKILLS_DIR, "*", "SKILL.md")))

# The shared ONB-7 preflight guard: unset/empty CLAUDE_PLUGIN_DATA must stop a skill
# BEFORE any code block expands it (`uv venv "/venv"` provisions a root-owned path).
_GUARD = '[ -n "${CLAUDE_PLUGIN_DATA:-}" ] ||'


def test_five_skills_ship():
    names = sorted(os.path.basename(os.path.dirname(p)) for p in _ALL_SKILLS)
    assert names == ["audit", "bootstrap", "doctor", "init", "new"]


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
