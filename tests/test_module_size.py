"""The module-size ratchet — decomposed files stay decomposed.

The 2026-07-16 decomposition split the five largest engine modules (recall, mcp_server,
eval_recall, dream, doctor) into prefix-named siblings behind re-exporting façades
(see CONTRIBUTING.md "Code layout"). This ratchet keeps that shape from silently
regressing, the same two-directional way WRITE_OPEN_ALLOWLIST guards write sites:

- a NEW module must fit the cap (900 lines in plugin/memory/, 1200 in tests/) — when a
  concern outgrows its module, split along a section banner into a prefix-named sibling
  instead of growing the file;
- files that PRE-DATE the ratchet are grandfathered at the size recorded below plus a
  small fix-headroom (+60), so they can take small fixes but can only meaningfully
  shrink — raising a pinned size is a deliberate, reviewed decision, not a drive-by;
- a grandfathered entry whose file dropped back under the cap (or vanished) is a DEAD
  entry and fails loudly, so the ledger below always reads as the live list of
  remaining decomposition debt.

Physical lines (wc -l), not logical lines: the point is reviewability, and comments
are part of what a reader scrolls through.
"""

from __future__ import annotations

import os

PLUGIN_CAP = 900
TEST_CAP = 1200
# Headroom on grandfathered pins: room for small in-place fixes without re-pinning,
# small enough that any real feature growth trips the ratchet and forces a split.
GRANDFATHER_SLACK = 60

# Size when ratcheted (2026-07-17, post-decomposition, rebased over v1.20.0 Sentinel).
# Shrink freely; to grow past pin+slack, split the file instead — or re-pin here with a
# PR that says why.
GRANDFATHERED_PLUGIN = {
    "provenance.py": 2012,
    "eval_recall.py": 1803,  # re-pinned at the ED5R-3 split (SEN-4 probe → eval_adversarial); SIG-6/GRF-3 writers stay AST-pinned to this file (crash contract)
    "new_memory.py": 1757,
    "build_index.py": 1735,
    "recall.py": 1637,  # recall() orchestrator + hook entry; rankers/salience/tiers already split out
    "links.py": 1363,
    "dream_generate.py": 1314,
    "telemetry.py": 978,  # re-pinned at the ED5R-3 split (substrate → telemetry_store, SIG-3/GRW-2 mining → telemetry_mining)
    "packs.py": 988,
    "mcp_schemas.py": 914,  # one unsplittable _TOOLS data literal; grows only with new tools
}

GRANDFATHERED_TESTS = {
    "test_recall.py": 4221,
    "test_eval_recall.py": 2351,
    "test_provenance.py": 2210,
    "test_build_index.py": 1752,
    "test_creation_convention.py": 1461,
    "test_doctor.py": 1448,
    "test_reconsolidate.py": 1271,
    "test_session_start.py": 1207,
}

_HERE = os.path.dirname(__file__)
_PLUGIN_DIR = os.path.abspath(os.path.join(_HERE, "..", "plugin", "memory"))
_TESTS_DIR = os.path.abspath(_HERE)


def _line_count(path: str) -> int:
    with open(path, "r", encoding="utf-8") as fh:
        return len(fh.read().splitlines())


def _py_files(dirpath: str):
    """Top-level .py files only — subdirs (e.g. plugin/memory/_vendor/, vendored
    third-party code) are exempt from the ratchet."""
    for name in sorted(os.listdir(dirpath)):
        if name.endswith(".py") and os.path.isfile(os.path.join(dirpath, name)):
            yield name


def _check_dir(dirpath: str, cap: int, grandfathered: dict) -> list:
    failures = []
    for name in _py_files(dirpath):
        lines = _line_count(os.path.join(dirpath, name))
        pinned = grandfathered.get(name)
        if pinned is not None:
            if lines > pinned + GRANDFATHER_SLACK:
                failures.append(
                    f"{name}: {lines} lines exceeds its grandfathered pin "
                    f"{pinned}+{GRANDFATHER_SLACK} — split along a section banner "
                    f"into a prefix-named sibling (see CONTRIBUTING.md 'Code layout') "
                    f"instead of growing it"
                )
        elif lines > cap:
            failures.append(
                f"{name}: {lines} lines exceeds the {cap}-line cap for new modules — "
                f"split along a section banner into a prefix-named sibling "
                f"(see CONTRIBUTING.md 'Code layout')"
            )
    return failures


def test_plugin_modules_fit_the_ratchet():
    failures = _check_dir(_PLUGIN_DIR, PLUGIN_CAP, GRANDFATHERED_PLUGIN)
    assert not failures, "module-size ratchet:\n  " + "\n  ".join(failures)


def test_test_files_fit_the_ratchet():
    failures = _check_dir(_TESTS_DIR, TEST_CAP, GRANDFATHERED_TESTS)
    assert not failures, "module-size ratchet:\n  " + "\n  ".join(failures)


def test_grandfather_ledger_carries_no_dead_entries():
    """A pinned file that shrank under the cap (or was removed/renamed) must leave the
    ledger — the tables above stay an honest live list of remaining decomposition debt,
    and a re-grown file can't hide behind a stale generous pin."""
    dead = []
    for dirpath, cap, table in (
        (_PLUGIN_DIR, PLUGIN_CAP, GRANDFATHERED_PLUGIN),
        (_TESTS_DIR, TEST_CAP, GRANDFATHERED_TESTS),
    ):
        for name, pinned in table.items():
            path = os.path.join(dirpath, name)
            if not os.path.isfile(path):
                dead.append(f"{name}: pinned at {pinned} but the file no longer exists")
            elif _line_count(path) <= cap:
                dead.append(
                    f"{name}: now {_line_count(path)} lines (under the {cap} cap) — "
                    f"drop its grandfather entry; the general cap governs it"
                )
    assert not dead, "dead grandfather entries:\n  " + "\n  ".join(dead)
