"""Query hygiene + intent routing for recall: harness-envelope stripping, RET-4
fence/traceback identifier mining, ``clean_query``, the RCL-3 rescue knobs, and
RCL-1's per-query dense/lexical routing. Decomposed out of ``recall.py`` as pure
code motion; every symbol stays importable at ``memory.recall.<name>`` via the
façade's explicit re-exports."""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from .build_index import tokenize

# --------------------------------------------------------------------------- #
# Query hygiene
# --------------------------------------------------------------------------- #
# The UserPromptSubmit hook feeds the prompt VERBATIM. In practice a large fraction of prompts
# are harness envelopes (<task-notification> tool-use blobs) or near-empty continuations
# ("?", "continue") that carry no retrieval intent — embedding them wastes a ~400ms cold model
# load to inject pure semantic noise. clean_query() strips the envelopes and returns "" to SKIP
# recall entirely when nothing of substance remains (the hook then injects no context).
_ENVELOPE_BLOCK_RE = re.compile(
    r"<(task-notification|system-reminder|local-command-stdout)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)

# RET-4: tag stripping is scoped to KNOWN harness tag names only. Previously _TAG_RE matched
# ANY angle-bracketed span, which silently ate `<lambda>`, `Vec<String>`, `<module>` — exactly
# the symbol-shaped tokens debugging prompts are made of. Restricting the deletion to the
# harness's own envelope/wrapper tags means an unknown tag (a Python repr, a generic type, a
# stray HTML-ish fragment a user pasted) is LEFT IN PLACE; angle brackets tokenize harmlessly
# (they're not word chars) so the identifier text inside/around them still reaches BM25/dense.
_KNOWN_HARNESS_TAGS = (
    "task-notification",
    "system-reminder",
    "local-command-stdout",
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
)
_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_KNOWN_HARNESS_TAGS) + r")\b[^>]*/?>",
    re.IGNORECASE,
)
_MIN_CONTENT_TOKENS = 2

# RCL-3: main()-only (not clean_query -- see the rescue block there) -- a HIGHER bar than
# _MIN_CONTENT_TOKENS above. clean_query already lets a query like "and the other one?"
# through (it clears _MIN_CONTENT_TOKENS=2), but a query this short/pronoun-heavy routinely
# shares no vocabulary with any memory and abstains downstream anyway -- _RESCUE_MIN_TOKENS
# is the bar for "substantive enough to stand alone," gated well above the bare hygiene
# floor so a genuinely substantive prompt is never touched by the rescue blend.
_RESCUE_MIN_TOKENS = 4  # override: HIPPO_RESCUE_MIN_TOKENS
_RESCUE_TURNS = 3  # override: HIPPO_RESCUE_TURNS -- how many prior same-session query
# previews to blend in, most-recent-last (oldest of the window first).


def _rescue_min_tokens() -> int:
    """``HIPPO_RESCUE_MIN_TOKENS`` override; malformed/absent -> the module default."""
    raw = os.environ.get("HIPPO_RESCUE_MIN_TOKENS")
    if raw is None or not raw.strip():
        return _RESCUE_MIN_TOKENS
    try:
        return int(raw)
    except ValueError:
        return _RESCUE_MIN_TOKENS


def _rescue_turns() -> int:
    """``HIPPO_RESCUE_TURNS`` override; malformed/absent -> the module default."""
    raw = os.environ.get("HIPPO_RESCUE_TURNS")
    if raw is None or not raw.strip():
        return _RESCUE_TURNS
    try:
        return int(raw)
    except ValueError:
        return _RESCUE_TURNS

# Terse continuation/filler prompts that carry no retrieval intent (matched normalized+lowered).
_CONTINUATION_PHRASES = frozenset(
    {
        "continue", "pls continue", "please continue", "go on", "keep going", "next",
        "proceed", "lets proceed", "let's proceed", "go ahead", "yes pls", "yes please",
        "option 1", "option 2", "option 3", "drop it", "ok", "okay", "yes", "no", "y", "n",
        "thanks", "ty", "stop",
    }
)

# --------------------------------------------------------------------------- #
# RET-4: fence/traceback mining
# --------------------------------------------------------------------------- #
# clean_query used to DELETE fenced code blocks outright (_FENCE_BLOCK_RE.sub(" ", ...)) —
# throwing away the error class, symbol names, and file paths that are the strongest lexical
# signal in exactly the debugging prompts where recall matters most ("why does
# `_BG_TASKS.discard` never fire" is far more retrievable than the sentence around it once the
# identifier survives). Instead of deleting fence contents, MINE them: pull identifier-like
# tokens out, rank by a cheap rarity proxy, and APPEND the top few to the cleaned text so they
# still reach tokenize()/BM25/dense. clean_query has no index access (it must stay pure/total,
# no I/O), so true corpus-frequency rarity is unavailable — "longer / multi-part (underscored,
# dotted, camelCase) / UPPER_CASE" is the cheap proxy: those shapes are overwhelmingly
# project-specific identifiers, while short bare words are usually keywords/builtins that BM25
# already handles fine from the surrounding prose.
_IDENTIFIER_RE = re.compile(
    r"""
    [A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+   # dotted path: module.attr, a.b.c
    | [A-Za-z0-9_]+/[A-Za-z0-9_./-]*[A-Za-z0-9_]           # path-like: dir/file.py, a/b/c
    | [A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+               # snake_case (>=1 underscore)
    | [a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*                   # camelCase / PascalCase-ish
    | [A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*                   # ErrorClass-style (CapWords, e.g. IOError)
    | [A-Z][A-Z0-9_]{2,}                                    # UPPER_CASE constants (>=3 chars)
    """,
    re.VERBOSE,
)
# Traceback lines carry identifier signal even OUTSIDE a fence (a pasted stack trace often
# isn't triple-backtick'd). Both are cheap, total (no backtracking blowup — bounded charsets,
# anchored per-line) regexes: `File "path", line N` and a trailing `SomeError: message`.
_TRACEBACK_FILE_RE = re.compile(r'File\s+"([^"]+)",\s*line\s+(\d+)')
_TRACEBACK_ERROR_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\b\s*:")
_MAX_MINED_TOKENS = 8  # module constant per the roadmap spec — cap on mined tokens appended


def _mine_identifiers(text: str) -> List[str]:
    """Extract+rank identifier-like tokens from ``text`` (fence contents or raw traceback lines).

    Rarity proxy (clean_query is pure — no index/corpus access to compute real IDF): rank by
    (is multi-part [underscore/dot/slash] first, then length desc) — longer, structured tokens
    are overwhelmingly project-specific identifiers (error classes, symbol names, file paths),
    while short undecorated words are usually common tokens BM25 already scores fine from the
    surrounding prose. Dedupes while preserving first-seen order among equal-ranked tokens
    (stable sort). Never raises (caller wraps); pure text in, list out.
    """
    found = _IDENTIFIER_RE.findall(text)
    # File "path", line N -> the path is exactly as strong a signal as a fenced identifier.
    for path, _line in _TRACEBACK_FILE_RE.findall(text):
        found.append(path)
    found.extend(_TRACEBACK_ERROR_RE.findall(text))

    seen: dict = {}  # token -> first-seen index (for stable ordering among equal rank)
    for i, tok in enumerate(found):
        if tok not in seen:
            seen[tok] = i

    def _is_multipart(tok: str) -> bool:
        return ("_" in tok) or ("." in tok) or ("/" in tok)

    ranked = sorted(
        seen.keys(),
        key=lambda tok: (not _is_multipart(tok), -len(tok), seen[tok]),
    )
    return ranked[:_MAX_MINED_TOKENS]


def clean_query(raw: str) -> str:
    """Normalize a raw prompt into a recall query, or "" to SKIP recall (no model load).

    Strips harness envelopes (``<task-notification>`` / ``<system-reminder>`` tool-use blobs)
    and KNOWN harness wrapper tags, MINES identifier-like tokens out of fenced code blocks and
    traceback lines (rather than deleting them — RET-4), and returns "" when what remains
    carries no retrieval intent (a terse continuation like "?"/"continue", or fewer than
    ``_MIN_CONTENT_TOKENS`` content tokens). Pure; never raises — any failure degrades to the
    raw prompt (recall on the un-cleaned text rather than skip).

    RET-3: the min-content gate below calls the SAME ``tokenize()`` used for BM25/dense, which
    is now Unicode-aware (word tokens for Latin/Cyrillic/etc., character bigrams for CJK runs
    lacking whitespace segmentation — see ``build_index.tokenize``). That makes this gate
    effectively grapheme-aware for free: a substantive Japanese/Russian prompt tokenizes to
    >=2 real content tokens (never "0 tokens" the way the old ASCII-only tokenizer produced for
    non-Latin text) and clears the gate exactly like an equivalent English prompt would. The
    one deliberately-unchanged edge case: a 1-2 character CJK prompt yields only 0-1 bigram
    tokens and DOES trip the skip — the same treatment a single English word gets today (not a
    regression, just applying the existing "too terse to carry retrieval intent" rule uniformly
    across scripts; a 3+ character CJK prompt already yields >=2 bigrams and passes).
    """
    try:
        if not raw or not raw.strip():
            return ""
        text = _ENVELOPE_BLOCK_RE.sub(" ", raw)

        # Mine identifiers from fenced blocks AND raw traceback lines BEFORE the fences are
        # removed from the running text — mining reads the ORIGINAL raw (fences + surrounding
        # prose both scanned) so a traceback pasted without a fence is treated identically.
        mined = _mine_identifiers(raw)

        text = _FENCE_BLOCK_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        text = " ".join(text.split()).strip()
        if mined:
            text = (text + " " + " ".join(mined)).strip() if text else " ".join(mined)
        if not text:
            return ""
        if text.lower().strip(" ?!.,") in _CONTINUATION_PHRASES:
            return ""
        if len(tokenize(text)) < _MIN_CONTENT_TOKENS:
            return ""
        return text
    except Exception:
        return (raw or "").strip()

# The dense wall-clock bound + timeout machinery live in build_index (shared with the
# offline SessionStart refresh); recall uses the short per-query bound.


# --------------------------------------------------------------------------- #
# RCL-1: per-query dense/lexical intent routing
# --------------------------------------------------------------------------- #
# _mine_identifiers already extracts identifier-shaped tokens (dotted paths, snake_case,
# camelCase, UPPER_CASE, traceback file/error lines) from every query for free -- the RATIO
# of mined identifiers to total query tokens is a cheap, hot-path-safe proxy for "is this
# query symbol/error-heavy (BM25 exactness matters more) or prose (semantic paraphrase
# matters more)". Computed fresh INSIDE recall() (not clean_query, which is pure/
# single-prompt and discards the count) so every surface that calls recall() directly --
# the hook, /hippo:recall, the MCP tool, eval_recall -- gets the same routing regardless of
# whether clean_query ran first. A dead-band around the boundary (and any query too short to
# make the ratio meaningful) returns EXACTLY (1.0, 1.0) -- byte-identical to the pre-RCL-1
# balanced default -- so a weak/ambiguous shape signal can never mis-route: there is no
# baseline-diff eval gate, only the absolute thresholds, and a bad route could erode them
# silently.
_INTENT_MIN_TOKENS = 8  # override: HIPPO_INTENT_MIN_TOKENS -- below this a ratio is noise --
# short queries (a handful of plain technical words, e.g. "oauth token refresh flow policy
# gateway") must stay balanced: at low token counts the density ratio swings wildly on a
# single incidental match, and the corpus's ACTUAL rank order (not just the primary_relevance
# the knee compares) is sensitive enough to a weight shift that a short, ambiguously-shaped
# query reordering the fused list can shift WHICH entry's relevance the knee compares against
# next -- a real regression an earlier draft of this item hit on a 6-token fixture.
_INTENT_DENSE_DENSITY = 0.10  # override: HIPPO_INTENT_DENSE_DENSITY -- at/below -> lean dense
_INTENT_LEXICAL_DENSITY = 0.35  # override: HIPPO_INTENT_LEXICAL_DENSITY -- at/above -> lean lexical
_INTENT_LEAN_WEIGHT = 1.3  # override: HIPPO_INTENT_LEAN_WEIGHT -- favored side's weight (the
# other side gets 2.0 - lean, so the pair always sums to 2.0 -- the same total weight mass as
# the balanced 1.0 + 1.0 default, just redistributed toward the favored signal).


def _intent_weights(query: str, q_tokens: List[str]) -> Tuple[float, float]:
    """``(dense_weight, lexical_weight)`` for the RRF fusion's primary (description) slots.

    Malformed env overrides degrade to the module default (never raise); any internal
    failure returns the balanced ``(1.0, 1.0)`` default -- this must never break recall.
    """

    def _env_float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    try:
        total = len(q_tokens)
        if total < int(_env_float("HIPPO_INTENT_MIN_TOKENS", _INTENT_MIN_TOKENS)):
            return (1.0, 1.0)
        density = len(_mine_identifiers(query)) / total
        lexical_bound = _env_float("HIPPO_INTENT_LEXICAL_DENSITY", _INTENT_LEXICAL_DENSITY)
        dense_bound = _env_float("HIPPO_INTENT_DENSE_DENSITY", _INTENT_DENSE_DENSITY)
        if density >= lexical_bound:
            lean = _env_float("HIPPO_INTENT_LEAN_WEIGHT", _INTENT_LEAN_WEIGHT)
            return (2.0 - lean, lean)
        if density <= dense_bound:
            lean = _env_float("HIPPO_INTENT_LEAN_WEIGHT", _INTENT_LEAN_WEIGHT)
            return (lean, 2.0 - lean)
        return (1.0, 1.0)
    except Exception:
        return (1.0, 1.0)
