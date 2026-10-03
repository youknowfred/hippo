"""RWY-2: the one frontmatter accessor — its read modes, the flat-read counter, and the grep
pin that keeps the hand-rolled flat-or-nested idiom from coming back."""

from __future__ import annotations

import os
import re

from memory import fm_access as F

_MEMORY_PKG = os.path.dirname(os.path.abspath(F.__file__))


def test_default_reads_flat_then_falls_back_on_none():
    assert F.fm_get({"cited_paths": ["a"], "metadata": {"cited_paths": ["b"]}}, "cited_paths") == ["a"]
    assert F.fm_get({"cited_paths": None, "metadata": {"cited_paths": ["b"]}}, "cited_paths") == ["b"]
    assert F.fm_get({"metadata": {"cited_paths": ["b"]}}, "cited_paths") == ["b"]
    # an empty-but-present flat value wins in the default mode (the `is None` fallback)
    assert F.fm_get({"source_commit": "", "metadata": {"source_commit": "abc"}}, "source_commit") == ""


def test_falsy_mode_matches_or_semantics():
    fm = {"steer": "", "metadata": {"steer": "pin"}}
    assert F.fm_get(fm, "steer", falsy=True) == "pin"
    # `a or b` returns b even when b is itself falsy
    assert F.fm_get({"pack_version": 0, "metadata": {"pack_version": ""}}, "pack_version", falsy=True) == ""
    assert F.fm_get({"pack_version": 0}, "pack_version", falsy=True) is None


def test_nested_first_mode():
    fm = {"type": "user", "metadata": {"type": "project"}}
    assert F.fm_get(fm, "type", falsy=True, nested_first=True) == "project"
    assert F.fm_get({"type": "user", "metadata": {"type": ""}}, "type", falsy=True, nested_first=True) == "user"


def test_default_and_junk_inputs():
    assert F.fm_get(None, "type", "x") == "x"
    assert F.fm_get({"metadata": "not-a-dict"}, "type", "x") == "x"
    assert F.fm_get({}, "type") is None
    assert F.fm_metadata({"metadata": ["list"]}) == {}


def test_flat_read_counter_counts_only_flat_served_nested_keys():
    F.reset_flat_reads()
    F.fm_get({"type": "user"}, "type")
    F.fm_get({"metadata": {"type": "user"}}, "type")
    F.fm_get({"description": "d"}, "description")  # a top-level key by design: not counted
    F.fm_get({"type": "", "metadata": {"type": "x"}}, "type", falsy=True)  # served nested
    assert F.flat_reads() == {"type": 1}
    F.reset_flat_reads()
    assert F.flat_reads() == {}


_IDIOM_RE = re.compile(r"""\.get\(\s*["']metadata["']\s*\)\s+if\s+isinstance\(""")


def test_the_flat_or_nested_idiom_lives_only_in_the_accessor():
    offenders = []
    for fname in sorted(os.listdir(_MEMORY_PKG)):
        if not fname.endswith(".py") or fname == "fm_access.py":
            continue
        with open(os.path.join(_MEMORY_PKG, fname), encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if _IDIOM_RE.search(line):
                    offenders.append(f"{fname}:{i}")
    assert not offenders, "hand-rolled flat-or-nested frontmatter read; use fm_access.fm_get:\n  " + "\n  ".join(
        offenders
    )


def test_export_receipt_reads_a_flat_shape_confidence(tmp_path):
    """Repro (RWY-2): recall ranks on a flat-shape ``confidence:`` (build_index reads both
    shapes), but the export receipt read only ``metadata.confidence`` and showed the same
    memory as unset."""
    from memory.export_receipts import _graduation

    text = "---\nname: m\ndescription: d\ntype: project\nconfidence: draft\n---\nbody\n"
    assert _graduation(text)["confidence"] == "draft"
