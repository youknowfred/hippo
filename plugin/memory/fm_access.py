"""RWY-2: the one frontmatter accessor for the two shapes a memory's keys arrive in.

hippo writes ``name``/``description`` at the top level and nests ``type`` plus its
provenance keys under ``metadata:``; native auto memory nests too; older and hand-written
files carry the same keys flat. Every reader used to re-spell the read itself
(``meta = fm.get("metadata") if isinstance(...) else {}`` then a flat/nested fallback),
and the spellings disagreed on precedence. ``fm_get`` is now the only place that idiom
lives (``tests/test_fm_access.py`` greps for it), and each call site states its precedence
in keywords instead of re-implementing it.

The flat-read counter records every read served from the top level for a key hippo
nests, the signal ``migrate --check`` (FMT-1) reports on. Process-local, never persisted,
zero-dependency, so any module (hot path included) can import this one.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict

# Keys hippo itself writes at the top level; a flat read of these is the canonical shape,
# not a legacy one, so the counter ignores them.
_TOP_LEVEL_KEYS = frozenset({"name", "description"})

_flat_reads: Counter = Counter()


def fm_metadata(fm: Any) -> Dict[str, Any]:
    """The ``metadata:`` mapping of a parsed frontmatter, or ``{}``."""
    if not isinstance(fm, dict):
        return {}
    meta = fm.get("metadata")
    return meta if isinstance(meta, dict) else {}


def fm_get(fm: Any, key: str, default: Any = None, *, falsy: bool = False, nested_first: bool = False) -> Any:
    """Read ``key`` from either frontmatter shape.

    By default the top-level value wins unless it is ``None`` (absent or YAML null), and the
    ``metadata:`` value is the fallback. ``falsy=True`` falls through on any falsy value
    instead, with the semantics of ``a or b``: the second shape's value is returned whenever
    the first is falsy, even when that value is itself falsy. ``nested_first=True`` reads
    ``metadata:`` first. Returns ``default`` only where the chosen expression would have
    produced ``None``. Never raises.
    """
    if not isinstance(fm, dict):
        return default
    meta = fm_metadata(fm)
    flat_val = fm.get(key)
    nested_val = meta.get(key)
    if nested_first:
        first, second, second_is_flat = nested_val, flat_val, True
    else:
        first, second, second_is_flat = flat_val, nested_val, False
    use_first = bool(first) if falsy else first is not None
    if use_first:
        value, from_flat = first, not second_is_flat
    else:
        value, from_flat = second, second_is_flat
    if value is None:
        return default
    if from_flat and key not in _TOP_LEVEL_KEYS:
        _flat_reads[key] += 1
    return value


def flat_reads() -> Dict[str, int]:
    """This process's count of reads served from the flat shape, per key (a copy)."""
    return dict(_flat_reads)


def reset_flat_reads() -> None:
    """Zero the flat-read counter (tests)."""
    _flat_reads.clear()
