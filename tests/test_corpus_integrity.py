"""Corpus-integrity lint: the REAL agent-memory corpus must be machine-readable.

Non-hermetic BY DESIGN — unlike the other memory_tools tests (which build throwaway
corpora under tmp_path), this asserts a property of the actual ``.claude/memory/`` files.
It is the guard that would have caught the unquoted-``description:`` YAML break in
``formula_graph_watchdog_heartbeat_invariant.md`` — which made that memory silently
invisible to the staleness signal AND silently re-baselined on ``provenance --refresh``.
"""

from __future__ import annotations

import os

import pytest

from memory.provenance import resolve_dirs
from memory.staleness import find_unparseable


def test_real_corpus_frontmatter_all_parses():
    memory_dir, _ = resolve_dirs()
    if not os.path.isdir(memory_dir):
        pytest.skip(f"no memory corpus at {memory_dir}")
    broken = find_unparseable(memory_dir)
    assert broken == [], (
        "Memory file(s) with UNPARSEABLE frontmatter — silently untracked by the staleness "
        "signal AND re-baselined by `provenance --refresh`. Usually an unquoted value "
        "containing a ': ' (wrap it in quotes): " + ", ".join(broken)
    )
