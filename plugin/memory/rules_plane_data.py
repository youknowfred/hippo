"""RUL-2's data-file leg: does a dotted ref about to flag live in a YAML/JSON/TOML file?

Decomposed out of ``rules_plane.py``, which keeps ``rules_rot`` and the parse cap
``_DATA_DOC_MAX_BYTES`` (passed in on every call, so a patch of the façade's constant
still governs); this sibling never imports its façade. ``meta.mechanisms`` living under
``meta:`` in ``docs/audience-matrix.yaml`` is a live citation, not a rotten Python symbol
(the 2026-09-01 false positive), so before a symbol ref flags it is tried as a nested
MAPPING path from the root of every tracked data document.

Parsing is the whole cost of that, and the leg runs on every SessionStart: on 2026-10-03
em-growth-labs' 1,126 tracked data files (38MB, 11MB of it YAML that PyYAML's
pure-Python loader reads at ~3MB/s) took 3.6s of a 3.8s ``rules_rot``, for two refs that
resolve in no data file at all. Two moves make it cheap without moving a verdict:

  1. ``may_hold`` reads a file's raw bytes before parsing it, and skips the parse only
     when no key of the path can be in it (why that is exact: its docstring).
  2. ``vouched`` spends the cheapest evidence first: the small data files, then the
     symbol leg's tree sweep (``rules_plane_symbols.alive_elsewhere``), then the big
     data files, only for refs nothing has vouched for yet. The data leg and the sweep
     each only ever SILENCE a ref, each ref on its own evidence, so the order changes
     the cost and never the findings. Either fixed order regressed one of the three
     measured repos: em-growth-labs' refs are quoted strings (a schema.table, a Railway
     reference) the sweep vouches at once, while ic-memobot's resolve in small plan
     YAMLs, where data-first stops at the first file and sweep-first spends 0.4s on
     the whole 11k-file tree.

Read-only; never raises out of ``rules_rot`` (unreadable, unparseable and oversized files
contribute nothing, which can only keep a finding, never invent one).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Set, Tuple

DATA_DOC_EXTS = (".yaml", ".yml", ".json", ".toml")

# A data file at or under this size is parsed BEFORE the tree sweep, a bigger one only
# after it. ~10ms of pure-Python YAML: below it a parse costs less than the sweep it can
# spare (0.4-1s over a large tree); above it the sweep is the cheaper way to vouch.
# Measured 2026-10-03 over 0/32/64/128/256KB: 32KB was the knee on all three repos.
_EAGER_PARSE_MAX_BYTES = 32_000

# Bytes regexes for ``may_hold``. A word character of an identifier, raw: ASCII word
# characters, or any byte of a multi-byte UTF-8 character (a superset, so a guess here can
# only cost a parse). A line break YAML knows: CRLF, CR, LF, NEL, LS, PS.
_W = rb"[A-Za-z0-9_\x80-\xff]"
_LB = rb"(?:\r\n|[\r\n]|\xc2\x85|\xe2\x80[\xa8\xa9])"
# An escape that can write a word character without spelling it: \xHH, \uHHHH,
# \UHHHHHHHH, or a YAML escaped line break (joins its neighbours, eats the indentation).
_ESCAPE = rb"\\(?:[xuU]|" + _LB + rb"[ \t]*)"
_ANY_ESCAPE_RE = re.compile(_ESCAPE)
# A double-quoted string that is nothing but word characters and such escapes, at least
# one escape: the only shape whose decoded value can be an identifier it does not spell.
_ESCAPED_IDENTIFIER_RE = re.compile(rb'"' + _W + rb"*(?:" + _ESCAPE + _W + rb"*)+" + rb'"')


def may_hold(full_path: str, parts: List[str], max_bytes: int) -> bool:
    """False only when a parse of ``full_path`` provably cannot resolve ``parts``: some
    part is absent from the raw bytes, and no double-quoted string could have written it.

    Why that is exact. A part is an identifier (word characters only), and a key equal
    to one is spelled out literally in all three formats: a plain, single-quoted or block
    YAML scalar keeps its characters and folds a line break into a space or a newline
    (which no identifier holds); an alias or ``<<`` merge reuses a node written out in
    the same file; a JSON or TOML key never folds at all. Escapes are the one way to
    WRITE a word character without spelling it, and only ``\\x``/``\\u``/``\\U`` and
    YAML's escaped line break (``"com\\`` + newline + ``ms"`` reads ``comms``) can do it —
    every other escape yields a control character, a space, a quote or a slash, and a
    key holding one is no identifier. So a key that equals a part either appears in the
    bytes or is a double-quoted string of word characters and those escapes alone.
    UTF-8 bytes stand in for the decoded text: a part's encoding occurs in the bytes
    exactly when the part occurs in the text, and newline translation only touches
    ``\\r``. A file that does not decode fails its parse too, and contributes nothing.

    True whenever that is not certain: an unreadable file, or one above the parse cap
    ``max_bytes`` (left unread here; ``data_docs`` skips it on its own).
    """
    try:
        if os.path.getsize(full_path) > max_bytes:
            return True
        with open(full_path, "rb") as fh:
            return raw_may_hold(fh.read(), parts)
    except Exception:
        return True


def raw_may_hold(raw: bytes, parts: List[str]) -> bool:
    """``may_hold`` on bytes already read: every part spelled out, or an escaped one
    possible."""
    if all(part.encode("utf-8") in raw for part in parts):
        return True
    return bool(_ANY_ESCAPE_RE.search(raw) and _ESCAPED_IDENTIFIER_RE.search(raw))


def data_docs(repo_root: str, rel_path: str, cache: Dict[str, list], max_bytes: int) -> list:
    """Parsed document roots for one tracked data file, cached per ``rules_rot`` call.

    YAML may be multi-document (``safe_load_all``); JSON and TOML yield one root each.
    Unparseable/oversized/unreadable files contribute ``[]`` — silence, never a finding.
    ``tomllib`` is stdlib only since 3.11 while 3.9/3.10 sit inside the supported
    ``_PY_WINDOW``: there the import lands in this catch, TOML files contribute ``[]``,
    and a TOML-backed dotted ref degrades to the pre-fix verdict (flag) — pinned by
    ``test_dotted_ref_toml_resolution_needs_tomllib``.
    """
    if rel_path in cache:
        return cache[rel_path]
    docs: list = []
    full = os.path.join(repo_root, rel_path)
    try:
        if os.path.getsize(full) <= max_bytes:
            if rel_path.endswith((".yaml", ".yml")):
                import yaml

                with open(full, "r", encoding="utf-8") as fh:
                    docs = [d for d in yaml.safe_load_all(fh) if d is not None]
            elif rel_path.endswith(".json"):
                import json as _json

                with open(full, "r", encoding="utf-8") as fh:
                    docs = [_json.load(fh)]
            elif rel_path.endswith(".toml"):
                import tomllib

                with open(full, "rb") as fh:
                    docs = [tomllib.load(fh)]
    except Exception:
        docs = []
    cache[rel_path] = docs
    return docs


def resolves_in_data(
    parts: List[str], repo_root: str, data_files: List[str], cache: Dict[str, list], max_bytes: int
) -> bool:
    """True when ``a.b.c`` resolves as a nested MAPPING path from the root of a document
    in any of ``data_files`` (tracked YAML/JSON/TOML paths) — ``meta.mechanisms`` living
    under ``meta:`` in ``docs/audience-matrix.yaml`` is a live citation, not a rotten
    Python symbol (the 2026-09-01 false positive: the repo also had exactly one unrelated
    ``meta.py``, so the module leg matched it and flagged the missing "symbol"). A key
    whose VALUE is null still counts as present (sentinel lookup, not truthiness).
    Consulted only for a ref the module leg is ABOUT to flag, so the happy path never
    parses a data file at all; a file not yet parsed in this call is parsed only when
    ``may_hold`` says its bytes could hold the path."""
    _MISSING = object()
    for rel_path in data_files:
        if rel_path not in cache and not may_hold(
            os.path.join(repo_root, rel_path), parts, max_bytes
        ):
            continue
        for doc in data_docs(repo_root, rel_path, cache, max_bytes):
            node = doc
            for part in parts:
                if not isinstance(node, dict):
                    node = _MISSING
                    break
                node = node.get(part, _MISSING)
                if node is _MISSING:
                    break
            if node is not _MISSING:
                return True
    return False


def _split_by_cost(repo_root: str, repo_files: Set[str]) -> Tuple[List[str], List[str]]:
    """The tracked data files, sorted, split at ``_EAGER_PARSE_MAX_BYTES``. A file that
    will not stat goes with the small ones: its parse fails at once and costs nothing."""
    small: List[str] = []
    big: List[str] = []
    for rel_path in sorted(repo_files):
        if not rel_path.endswith(DATA_DOC_EXTS):
            continue
        try:
            size = os.path.getsize(os.path.join(repo_root, rel_path))
        except Exception:
            size = 0
        (small if size <= _EAGER_PARSE_MAX_BYTES else big).append(rel_path)
    return small, big


def vouched(
    repo_root: str, repo_files: Set[str], refs: Dict[str, Tuple[List[str], str]], max_bytes: int
) -> Set[str]:
    """The subset of ``refs`` (span -> (parts, resolved module path)) that is alive after
    all: a nested mapping path in some data file, or vouched for by the tree sweep. The
    same set any order of the three passes would return; this order is the cheap one."""
    if not refs:
        return set()
    from . import rules_plane_symbols as RS

    cache: Dict[str, list] = {}
    small, big = _split_by_cost(repo_root, repo_files)
    alive = {
        span for span, (parts, _m) in refs.items()
        if resolves_in_data(parts, repo_root, small, cache, max_bytes)
    }
    rest = {span: ref for span, ref in refs.items() if span not in alive}
    if rest:
        alive |= RS.alive_elsewhere(repo_root, repo_files, rest)
    return alive | {
        span for span, (parts, _m) in rest.items()
        if span not in alive and resolves_in_data(parts, repo_root, big, cache, max_bytes)
    }
