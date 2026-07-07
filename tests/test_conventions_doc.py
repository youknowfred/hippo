"""Packaging gate for the DOC-6 CONVENTIONS.md asset.

plugin/assets/CONVENTIONS.md is the single-page corpus-convention reference
/hippo:init seeds into .claude/memory/ (idempotent — see plugin/skills/init/SKILL.md step
2c). tests/test_docs_links.py deliberately EXCLUDES plugin/assets/ from its repo-wide
relative-link sweep (asset templates' links resolve in the DESTINATION corpus, not this
repo) — this file independently holds CONVENTIONS.md to that SAME dead-link convention,
since it ships as a standalone file with no destination-relative targets of its own.
"""

from __future__ import annotations

import os

from memory.provenance import _is_memory_filename

from .test_docs_links import _DOC_FILES, _relative_targets

_ASSETS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin", "assets"))
_CONVENTIONS = os.path.join(_ASSETS, "CONVENTIONS.md")


def test_conventions_md_ships():
    assert os.path.isfile(_CONVENTIONS)


def test_conventions_md_has_no_dead_relative_links():
    """Same rule test_docs_links.test_every_relative_link_resolves applies repo-wide,
    applied here to the one asset that rule's own path filter skips."""
    with open(_CONVENTIONS, "r", encoding="utf-8") as fh:
        text = fh.read()
    broken = [
        target
        for target in _relative_targets(text)
        if not os.path.exists(os.path.normpath(os.path.join(_ASSETS, target)))
    ]
    assert not broken, f"CONVENTIONS.md has dead relative link target(s): {broken}"


def test_conventions_md_is_excluded_from_the_repo_wide_doc_link_sweep():
    """plugin/assets/ is deliberately excluded from _DOC_FILES (seed templates, not shipped
    docs) — pin that CONVENTIONS.md lands there too, so this file stays the actual gate."""
    assert _CONVENTIONS not in _DOC_FILES


def test_conventions_md_filename_is_excluded_from_corpus_membership():
    """The destination filename (once copied to .claude/memory/CONVENTIONS.md by init) must
    be excluded from indexing/recall/floor-scan the same canonical way MEMORY.md is — see
    tests/test_provenance.py's dedicated exclusion tests for the full corpus-iterator check."""
    assert _is_memory_filename(os.path.basename(_CONVENTIONS)) is False
