"""Corpus-integrity lint: every SHIPPED operator-pack memory must be machine-readable.

QUA-10: this used to assert a property of the REAL ``.claude/memory/`` corpus — the
developer's own, un-committed working corpus — which meant it pytest.skip'd everywhere
except a machine that happened to have one seeded (never true in CI, so it was a permanent
skip there and a no-op merge gate). The property worth gating in CI is the same one, just
pointed at something that actually SHIPS and IS in git: ``plugin/assets/packs/**/*.md``
(the operator-pack starter memories every ``/hippo:init`` seeds from). A pack memory with
broken frontmatter would be silently invisible to the staleness signal AND silently
re-baselined by ``provenance --refresh`` the moment a consumer adopted it — exactly the
unquoted-``description:`` YAML break this test originally existed to catch (see git log),
just caught before it ships instead of after. The suite should end at 0 skipped.
"""

from __future__ import annotations

import glob
import os

from memory.new_memory import VALID_TYPES
from memory.provenance import parse_frontmatter

_ASSETS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin", "assets"))
_PACKS_DIR = os.path.join(_ASSETS, "packs")
_PACK_MEMORY_FILES = sorted(
    p
    for p in glob.glob(os.path.join(_PACKS_DIR, "**", "*.md"), recursive=True)
    # README.md is the packs index, not a memory file — no frontmatter to parse.
    if os.path.basename(p) != "README.md"
)


def test_shipped_packs_are_discovered():
    """A guard against the glob itself silently finding nothing (e.g. a moved packs dir) —
    without this, every assertion below would vacuously pass over zero files."""
    assert len(_PACK_MEMORY_FILES) >= 20, _PACK_MEMORY_FILES


def test_every_shipped_pack_memory_frontmatter_parses_with_required_fields():
    """Every shipped pack memory must parse AND carry the fields the creation convention
    requires (see ``new_memory._render_frontmatter`` / ``write_memory``): ``name`` matching
    its own filename stem, a non-empty ``description``, and ``metadata.type`` in
    ``VALID_TYPES``. Broken (frontmatter, name) fails LOUD and per-file — no skip, no
    partial credit."""
    broken = []
    for path in _PACK_MEMORY_FILES:
        rel = os.path.relpath(path, _PACKS_DIR)
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm = parse_frontmatter(text)
        if not fm:
            broken.append(f"{rel}: frontmatter did not parse")
            continue
        if fm.get("name") != stem:
            broken.append(f"{rel}: name {fm.get('name')!r} != filename stem {stem!r}")
        if not fm.get("description"):
            broken.append(f"{rel}: missing/empty description")
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        mtype = meta.get("type")
        if mtype not in VALID_TYPES:
            broken.append(f"{rel}: metadata.type {mtype!r} not in {VALID_TYPES}")
    assert broken == [], (
        "Shipped operator-pack memory/memories failed the packaging gate (frontmatter or "
        "required fields) — every consumer's /hippo:init seeds directly from these files:\n"
        + "\n".join(broken)
    )
