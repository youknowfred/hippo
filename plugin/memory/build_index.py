"""Offline index builder for agent-memory recall (Tier 2 of the activation roadmap).

Builds a HYBRID retrieval index over the memory corpus:
  - DENSE: ``bge-small-en-v1.5`` embeddings via ``fastembed`` (ONNX, no PyTorch). The
    ~130 MB model cache is warmed HERE, offline — NEVER from a hook.
  - SPARSE: a ``rank-bm25`` index over the same tokenized text (already a repo dep).

What gets indexed per memory = its ``name`` + ``description:`` (the recall hook the files
already carry). Body-summary embedding is deferred (see the roadmap) until the
description-only index is measured.

Persistence: a gitignored, rebuildable cache at ``.claude/.memory-index/``
  - ``manifest.json`` — schema version, model, per-entry {name, file, hash, tokens, doc_text},
    and a "bm25" block (PRF-1: precomputed postings/doc_len/avgdl/idf/k1/b — see
    ``compute_bm25_stats`` — so query time never reconstructs BM25Okapi over the whole corpus)
  - ``dense.npy``    — float32 [N, dim] L2-normalized embeddings (row i ↔ entries[i])

Markdown-in-git stays the single source of authority; this cache is derived and
deleting it loses nothing (``build_index`` regenerates it). The build is INCREMENTAL:
unchanged memories (same content hash) reuse their cached embedding row, so only
new/edited files are re-embedded.

Degrades cleanly: with ``fastembed`` absent (or ``HIPPO_DISABLE_DENSE=1``) it builds a
BM25-only index without error (``dense_ready=false``); recall still works on BM25 alone.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from .provenance import (
    _iter_memory_files,
    ensure_self_ignoring_dir,
    parse_frontmatter,
    resolve_dirs,
    split_frontmatter,
)
from .staleness import read_source_commit_time

# --------------------------------------------------------------------------- #
# Config (all overridable via env so the hook/tests never hard-depend on one model)
# --------------------------------------------------------------------------- #
_INDEX_DIRNAME = ".memory-index"
# v2 (Tier 3, memory-organism-instrument-immunize): entries gained "invalid_after".
# v3 (RET-5, salience fusion): entries gained "source_commit_time" (the frontmatter-persisted
# committer epoch — see ``staleness.read_source_commit_time`` — copied into the manifest at
# build time) so recall's optional recency prior is pure arithmetic on already-loaded index
# state, never a git call on the hot path.
# v4 (RCL-6, evidence snippet): body_chunks entries regained "text"; manifest gained
# "head_commit".
# v5 (GOV-2, steer:pin): entries gained "steer" (the author's bounded always-on recall
# lift, read from frontmatter by ``_extract_steer``) so the hot path reads steering off
# the already-loaded manifest and never re-reads files per prompt.
# v6 (GOV-7, confidence tier): entries gained "confidence" (the author's trust dial —
# draft|verified|authoritative, read by ``_extract_confidence``) so the inject-time render
# never re-reads a file per hit. Shipped display-only; DRM-6 later wired the SAME entry
# key into ranking (draft down-weight + the draft-only abstention guard in recall) — an
# entry-shape no-op, so no manifest bump was needed for it.
# COR-7 made this constant LOAD-BEARING: ``_load_manifest`` (the one gate every manifest
# consumer goes through — build_index's incremental reuse, refresh_index's hash fast-path,
# load_index and therefore recall) treats a manifest whose ``schema_version`` differs from
# this value as ABSENT, so bumping it forces exactly ONE full rebuild at the next
# build/refresh (the index is derived — a rebuild loses nothing) instead of silently
# serving a stale shape to code expecting the new one. The CORPUS format is versioned
# separately (``provenance.CORPUS_FORMAT_VERSION`` + the ``.claude/memory/.format``
# marker) — the corpus is authoritative and is never auto-migrated; see doctor's
# ``check_format_version`` and plugin/memory/README.md.
# v7 (RET-12, BM25 stemming): entries' persisted "tokens" (both description rows and body
# chunks) are now ``bm25_terms(tokenize(...))`` instead of raw ``tokenize(...)`` -- a
# manifest built under v6 has un-stemmed postings that would silently under-match a stemmed
# query token, so this MUST bump to force one full reindex rather than degrade quietly.
SCHEMA_VERSION = 7
# Public (not underscore-prefixed): doctor's non-English-corpus check (RET-3) compares a
# manifest's recorded model against this constant to decide whether the CURRENTLY configured
# model is the English default vs. an already-switched multilingual/other model.
ENGLISH_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_MODEL_PRESET_FILENAME = "model.json"


def resolve_embed_model() -> str:
    """The embedding model id to use, in precedence order: env > persisted preset > default.

    RET-3 / OQ-4: the release keeps an ENGLISH model as the hardcoded default (bootstrap's
    model-warm step and the doctor English-corpus check both need a known constant to compare
    against) while offering ``--multilingual`` as an OPT-IN, PERSISTED switch — not a second
    env var users have to remember to set every session. Precedence:
      1. ``HIPPO_EMBED_MODEL`` env override — wins unconditionally (existing behavior/tests
         that set this env var to point at a fake/alternate model must keep working verbatim).
      2. ``${CLAUDE_PLUGIN_DATA}/model.json`` — a small persisted preset file
         (``{"embed_model": "<id>"}``) that bootstrap's ``--multilingual`` flag writes. This is
         what makes the choice STICK across sessions without re-exporting an env var each time.
      3. ``ENGLISH_DEFAULT_MODEL`` (``bge-small-en-v1.5``) — the release's chosen default.
    Cheap (one ``os.stat`` + a small JSON read, no fastembed import) and NEVER raises — any
    read/parse problem (missing file, missing ``CLAUDE_PLUGIN_DATA``, corrupt JSON, non-dict
    JSON, non-string field) silently falls through to the next precedence level rather than
    blocking module import or a recall call. Module-level ``DEFAULT_MODEL`` calls this ONCE at
    import time (unchanged usage everywhere else in the module — ``_get_model``/``_MODEL_CACHE``/
    ``_HF_SOURCE_REPO_BY_MODEL`` keep reading the same module-level constant as before); a
    process that needs to pick up a preset written by another process (e.g. a test simulating
    bootstrap-then-recall in one run) re-resolves explicitly rather than relying on the module
    being re-imported.
    """
    env_override = os.environ.get("HIPPO_EMBED_MODEL")
    if env_override:
        return env_override
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if plugin_data:
        preset_path = os.path.join(plugin_data, _MODEL_PRESET_FILENAME)
        try:
            if os.path.isfile(preset_path):
                with open(preset_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    model = data.get("embed_model")
                    if isinstance(model, str) and model.strip():
                        return model.strip()
        except Exception:
            pass  # corrupt/unreadable preset -> fall through to the default, never raise
    return ENGLISH_DEFAULT_MODEL


DEFAULT_MODEL = resolve_embed_model()
_MANIFEST_NAME = "manifest.json"
_DENSE_NAME = "dense.npy"


def default_index_dir(memory_dir: str) -> str:
    """``.claude/.memory-index`` — a sibling of ``.claude/memory`` (the gitignored cache)."""
    override = os.environ.get("HIPPO_INDEX_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), _INDEX_DIRNAME)


def dense_disabled() -> bool:
    """True when the dense path is explicitly suppressed (tests / forced BM25-only)."""
    return os.environ.get("HIPPO_DISABLE_DENSE", "").strip() not in ("", "0", "false", "False")


# --------------------------------------------------------------------------- #
# Wall-clock bound for the dense model (shared by recall's query path + the offline
# SessionStart refresh). A WARM model load from the cache is ~1-2s; a COLD/wiped cache
# makes fastembed attempt a fetch and — even with HF forced offline — sleep ~27s on retry
# before failing. That would blow a hook's timeout, so the dense attempt is bounded and
# aborts to BM25 instead of blocking. recall.py imports these.
# --------------------------------------------------------------------------- #
class DenseTimeout(Exception):
    pass


def _parse_timeout_env(name: str, default: float) -> float:
    """Parse a float timeout env var; a malformed value must NEVER crash module import."""
    try:
        return float(os.environ.get(name) or str(default))
    except (TypeError, ValueError):
        return default


# query path (per-prompt recall) — short; refresh path (SessionStart embed batch) — longer.
DENSE_QUERY_TIMEOUT_SECS = _parse_timeout_env("HIPPO_DENSE_TIMEOUT", 5.0)
DENSE_REFRESH_TIMEOUT_SECS = _parse_timeout_env("HIPPO_REFRESH_TIMEOUT", 15.0)


def _parse_int_env(name: str, default: int) -> int:
    """Parse an int env var; a malformed value must NEVER crash module import."""
    try:
        return int(os.environ.get(name) or str(default))
    except (TypeError, ValueError):
        return default


# COR-3: the offline batch embed is sliced into bounded-size chunks so a large corpus
# persists PARTIAL progress within one DENSE_REFRESH_TIMEOUT_SECS budget instead of an
# all-or-nothing attempt that discards everything on a single slow batch. Each slice's
# already-embedded hashes are cache-reused on the next call (see build_index's
# old_row_by_hash), so a 500-doc corpus converges to dense over N sessions.
DENSE_EMBED_CHUNK_SIZE = _parse_int_env("HIPPO_EMBED_CHUNK_SIZE", 64)


def run_bounded(fn, seconds: float):
    """Run ``fn`` with a wall-clock bound that holds regardless of which thread calls this.

    OSP-4: SIGALRM only works in the main thread of a Unix process — a future MCP server (or
    any embedded use) calling recall from a worker thread would silently get NO bound at all.
    Instead, ``fn`` is submitted to a single-worker ``ThreadPoolExecutor`` and awaited with
    ``future.result(timeout=seconds)`` — this works identically from any calling thread. The
    tradeoff (accepted per the roadmap): this can make the CALLER stop waiting, but cannot
    forcibly kill ``fn`` if it doesn't cooperate — a still-running fastembed/onnx call keeps
    running in the background thread after ``DenseTimeout`` is raised here. That thread is not
    joined; it's left to finish (or the executor is garbage-collected once unreferenced).
    """
    if seconds <= 0:
        return fn()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        raise DenseTimeout()
    finally:
        executor.shutdown(wait=False)


# --------------------------------------------------------------------------- #
# Tokenization (shared by BM25 build, recall, and self-recall query derivation)
# --------------------------------------------------------------------------- #
# Compact English stopword set. Domain terms (pdf, irr, dscr, llm, ...) are deliberately
# NOT stopped — they are the most discriminating tokens in this corpus.
_STOPWORDS = frozenset(
    """
    a an the of to in on for and or but is are was were be been being it its this that these those
    with without within into onto from by as at via per vs not no nor so than then thus too very
    can could should would may might must will shall do does did done has have had having
    if else when while where which who whom whose what why how all any each few more most other some
    such only own same about above below over under again further once here there both
    we you they i he she them our your their he's also new now use used using up out off down
    """.split()
)

# RET-3: the old ``_TOKEN_RE`` (``[a-z0-9][a-z0-9_]*``) only matched ASCII — every non-English
# prompt/memory tokenized to EMPTY (Japanese/Russian text has no ASCII word chars at all), and
# accented Latin lost its accents before matching so degraded mid-word ("café" -> the regex
# only sees "caf", the trailing "é" isn't in the class at all and is silently dropped). That
# silently broke BOTH bm25 ranking for non-English memories AND the min-content skip gate in
# recall.clean_query (a substantive non-English prompt looked "empty" and recall never ran).
#
# The fix is UNCONDITIONAL (OQ-4: tokenization correctness isn't gated behind an opt-in) and
# stdlib-only (no `regex` third-party module):
#   - Latin/Cyrillic/etc. (anything with whitespace-separated "words"): ``\w`` under
#     ``re.UNICODE`` (the default for a ``str`` pattern in Python 3) already matches any
#     Unicode letter/digit/underscore -- ``str.lower()`` case-folds Cyrillic and accented Latin
#     correctly (Python's lower() is Unicode-aware), so "café".lower() -> "café" and the \w+
#     match captures the WHOLE word (accents are \w, not stripped) instead of truncating it.
#   - CJK (Han/Hiragana/Katakana/Hangul): these scripts don't segment words with whitespace, so
#     a plain \w+ run would swallow an entire CJK sentence as ONE giant "token" -- useless for
#     BM25 overlap matching. Instead, each maximal run of CJK codepoints is split into
#     overlapping character BIGRAMS (a length-1 run emits the single char, since a real bigram
#     needs 2 chars) -- a cheap, standard, dependency-free proxy for CJK "word" boundaries that
#     lets BM25 token-overlap actually fire on shared substrings between a query and a memory.
# Codepoint ranges (explicit, not a `regex` \p{Script} property -- stdlib `re` doesn't expose
# those): CJK Unified Ideographs (Han, incl. common Ext-A) 0x4E00-0x9FFF, Hiragana
# 0x3040-0x309F, Katakana 0x30A0-0x30FF, Hangul Syllables 0xAC00-0xD7A3. This is a pragmatic
# subset (not exhaustive of every CJK extension plane) but covers the overwhelming majority of
# real-world Japanese/Chinese/Korean text.
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs (Han)
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0xAC00, 0xD7A3),  # Hangul Syllables
)


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


# Unicode-aware word tokens: \w matches any Unicode letter/digit/underscore (Python 3 `str`
# patterns default to UNICODE matching), so this covers Latin/Cyrillic/Greek/etc. uniformly.
# CJK codepoints are individually \w too, but are handled separately by the bigram pass below
# (a run of CJK chars would otherwise match here as one unsegmented blob).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _cjk_bigrams(run: str) -> List[str]:
    """Character bigrams over one maximal run of CJK chars; a length-1 run yields the char itself."""
    if len(run) < 2:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def _split_cjk_and_word_runs(tok: str) -> List[str]:
    """Split one \\w+ match into alternating CJK-bigram and plain-word sub-tokens.

    A \\w+ match almost never actually mixes scripts in practice, but this stays correct (and
    still cheap) if it ever does — e.g. a token like "东京2024" (CJK + digits glued with no
    separator) yields CJK bigrams for the Han run and a plain word token for the digit run,
    rather than either silently dropping one script or bigramming digits. Each non-CJK
    sub-run gets the SAME length/stopword filter as a normal word token (a lone leftover char
    is dropped by the ``len(sub) < 2`` check at the call site, same as today).
    """
    out: List[str] = []
    cjk_run: List[str] = []
    word_run: List[str] = []

    def _flush_cjk() -> None:
        if cjk_run:
            out.extend(_cjk_bigrams("".join(cjk_run)))
            cjk_run.clear()

    def _flush_word() -> None:
        if word_run:
            sub = "".join(word_run)
            if len(sub) >= 2 and sub not in _STOPWORDS:
                out.append(sub)
            word_run.clear()

    for ch in tok:
        if _is_cjk_char(ch):
            _flush_word()
            cjk_run.append(ch)
        else:
            _flush_cjk()
            word_run.append(ch)
    _flush_cjk()
    _flush_word()
    return out


def tokenize(text: str) -> List[str]:
    """Unicode-aware tokens: \\w-based words (case-folded) for Latin/Cyrillic/etc., character
    bigrams for whitespace-less CJK runs. Stopwords + <2-length tokens dropped (the stopword
    set is English-only by design — non-English tokens are simply never stopped; the CJK
    bigram path skips the length/stopword filter entirely since 1-2 char CJK tokens ARE the
    unit of signal there, not noise to be filtered like a stray ASCII letter).

    Deliberately NOT stemmed — this is the shared, substring-safe primitive: ``clean_query``
    is fuzz-tested (Hypothesis) on the invariant that every token it emits is a literal
    substring of the raw input, and the min-content gate (``recall.clean_query``) only counts
    tokens here, both of which stemming would be free to violate or wouldn't need. Stemming
    for BM25 matching lives in ``bm25_terms`` below, applied as a SEPARATE pass over this
    function's output only at the specific call sites that build/query BM25 postings."""
    if not text:
        return []
    out: List[str] = []
    lowered = text.lower()
    for m in _TOKEN_RE.finditer(lowered):
        tok = m.group(0)
        if any(_is_cjk_char(ch) for ch in tok):
            out.extend(_split_cjk_and_word_runs(tok))
            continue
        if len(tok) < 2 or tok in _STOPWORDS:
            continue
        out.append(tok)
    return out


# --------------------------------------------------------------------------- #
# Light stemming for BM25 matching (RET-12: BM25's real weakness is surface-form
# mismatch -- "embed"/"embeds"/"embedding" are three distinct terms without this).
# --------------------------------------------------------------------------- #
# Deliberately NOT the full Porter algorithm. Porter's steps 2-4 rewrite derivational
# suffixes (-ational, -iveness, -aliti, ...) and are a well-documented source of
# over-stemming false-positive collisions -- an acceptable trade on a web-scale corpus,
# a much riskier one on hippo's small (tens-to-low-hundreds of memories) curated
# corpora, where one bad collision measurably hurts precision. This implements only
# the low-risk, high-value steps: plural -s/-es/-ies and verbal -ing/-ed, each with the
# same doubled-consonant/vowel guards Porter uses to avoid mangling short or irregular
# words. Applied ONLY to plain word tokens that already survived ``tokenize``'s
# length/stopword filter -- never called on CJK bigrams (which contain no ASCII
# suffix this can match) and never folded into ``tokenize`` itself (see its docstring).
_VOWELS = frozenset("aeiou")

# False positives for the -ies -> -y rule: a word whose SINGULAR already ends in "ie"
# (movie, cookie, tie) pluralizes by just adding "s", not by the consonant+y -> consonant+
# ies pattern "queries" collapses correctly on -- stripping "ies" and adding "y" back
# mangles these into a non-word ("movies" -> "movy") instead of missing the merge safely.
# No purely orthographic rule distinguishes "quer(y)+ies" from "mov(ie)+s" (both surface as
# consonant+"ies"), so this is a plain, explicit exception list, not a smarter rule --
# covers the common word/linguistics-literature cases; an uncovered "-ie" word still just
# MISSES its merge (unchanged token), never gets actively mangled, since it wouldn't be in
# here to mangle in the first place... except it would, by the general rule, if not listed.
# Keep this list open to growing with any newly-observed corpus vocabulary.
_STEM_IES_EXCEPTIONS = frozenset(
    {
        "series", "species",  # irregular: don't pluralize by adding -s at all
        "movies", "cookies", "selfies", "veggies", "zombies", "genies", "rookies",
        "calories", "ties", "pies", "prairies",
    }
)


def _has_vowel(s: str) -> bool:
    return any(ch in _VOWELS for ch in s)


def _strip_double_consonant(s: str) -> str:
    """Undouble a trailing doubled consonant from an -ing/-ed strip (hopp -> hop) UNLESS
    it's l/s/z (call, pass, buzz keep both -- Porter's *L/*S/*Z exception: those double
    consonants are part of the base word, not an inflectional doubling)."""
    if len(s) >= 2 and s[-1] == s[-2] and s[-1] not in _VOWELS and s[-1] not in "lsz":
        return s[:-1]
    return s


def stem(token: str) -> str:
    """Light, dependency-free suffix stripping so morphological variants collapse to one
    BM25 term. See the module comment above for scope and rationale."""
    w = token
    if w in _STEM_IES_EXCEPTIONS:
        return w

    if w.endswith("sses"):  # caresses -> caress, classes -> class
        w = w[:-2]
    elif w.endswith(("ches", "shes", "xes")) and len(w) > 4:  # boxes -> box, watches -> watch
        w = w[:-2]
    # NOT "zes": unlike ch/sh/x, "z" is at least as often the last letter of a base word
    # that ALREADY ends in silent "e" (size, prize, maze, gaze, doze, blaze, glaze, freeze)
    # as it is a true consonant-final base needing an inserted "e" before the plural "-s"
    # (buzz -> buzzes) -- and this corpus's own vocabulary is full of the former via the
    # -ize/-yze verb family's 3rd-person-singular form (tokenizes, serializes, optimizes,
    # organizes, recognizes, analyzes, ...). Stripping "es" unconditionally here would
    # mangle "tokenizes" -> "tokeniz" instead of "tokenize"; falling through to the plain
    # single-"s" strip below gets the -ize family right (tokenizes -> tokenize) at the cost
    # of the rarer buzz-style double-z merge (buzzes -> "buzze", not "buzz" -- unmerged, the
    # safe direction, matching classic Porter's own accepted imprecision here).
    elif w.endswith("ies") and len(w) > 4:  # queries -> query
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith(("ss", "us", "is", "as")) and len(w) > 3:
        w = w[:-1]  # embeds -> embed; NOT corpus/analysis/basis/bias/atlas (false plurals)

    # The resulting stem must be >=4 chars (len(w) > 6 for -ing, > 5 for -ed): a plain
    # "has a vowel" guard alone still mangles a base word that merely ends in "ed"/"ing"
    # without being inflected at all -- "embed" -> stem "emb" (3 chars, vowel present,
    # textbook Porter would strip it too) is exactly the kind of false positive this
    # exists to avoid on a corpus where "embed" is core vocabulary, not a rare miss. Some
    # genuine short inflections (moved/moving) fall below the floor and stay unmerged --
    # the safe failure mode (a missed collapse) over the dangerous one (a mangled word).
    if w.endswith("ing") and len(w) > 6 and _has_vowel(w[:-3]):
        w = _strip_double_consonant(w[:-3])  # embedding -> embed, hopping -> hop
    elif w.endswith("ed") and len(w) > 5 and _has_vowel(w[:-2]):
        w = _strip_double_consonant(w[:-2])  # tanned -> tan, tested -> test
    return w


def bm25_terms(tokens: List[str]) -> List[str]:
    """Stem a ``tokenize()``-produced token list for BM25 indexing/matching. A distinct pass
    over ``tokenize``'s output (never folded into ``tokenize`` itself) so callers that need
    its substring/count guarantees are unaffected -- see ``tokenize``'s docstring."""
    return [stem(t) for t in tokens]


# --------------------------------------------------------------------------- #
# Per-memory document text
# --------------------------------------------------------------------------- #
def _first_meaningful_body_line(body: str) -> str:
    for raw in (body or "").split("\n"):
        ln = raw.strip().lstrip("#").strip()
        if len(ln) >= 8 and not ln.startswith(("---", "```", "**Why", "**How")):
            return ln
    return ""


def extract_description(text: str) -> str:
    """The memory's ``description:`` (top-level or under ``metadata:``), or a body fallback.

    3 of the corpus carry no description; for them the first meaningful body line is used.
    """
    fm = parse_frontmatter(text)
    desc = ""
    if isinstance(fm, dict):
        d = fm.get("description")
        if not d:
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            d = meta.get("description")
        if isinstance(d, str):
            desc = d.strip()
    if not desc:
        _, body = split_frontmatter(text)
        desc = _first_meaningful_body_line(body)
    return desc


def _name_words(name: str) -> str:
    return name.replace("_", " ").replace("-", " ")


def memory_doc_text(name: str, text: str) -> str:
    """The text indexed for one memory: ``name`` (slug words) + its ``description``.

    The name is included because the kebab/snake slug is itself a dense recall signal
    (e.g. ``density-adaptive-floor``).
    """
    return f"{_name_words(name)}. {extract_description(text)}".strip()


def entry_description(entry: dict) -> str:
    """The raw description for display. Prefers the stored field; falls back to splitting
    ``doc_text`` on the first ``. `` (the name/description boundary) for legacy indexes."""
    d = entry.get("description")
    if isinstance(d, str):
        return d
    doc = entry.get("doc_text", "")
    return doc.split(". ", 1)[1] if ". " in doc else doc


def _hash(doc_text: str) -> str:
    return hashlib.sha1(doc_text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# RET-2: body chunks — a memory's crucial fact (error signature, config value,
# rationale) often lives in the BODY, behind a generic description. Description-only
# indexing makes that fact invisible to both backends no matter how good the query is.
# These chunks are a BACKSTOP, not a replacement: description rows stay primary (see
# recall._BODY_RRF_WEIGHT), and the bounds below are deliberately tight so a large corpus'
# index doesn't balloon just because bodies are verbose.
# --------------------------------------------------------------------------- #
_BODY_CHUNK_CHAR_BUDGET = 1500  # only the first ~1500 chars of body are ever considered
_BODY_CHUNK_MAX = 3  # hard cap: at most 3 body chunks per memory
_BODY_CHUNK_MIN_CHARS = 80  # a chunk shorter than this is trivia (a lone heading, a stray
# line) -- skip it rather than index noise that would never usefully rank above a real hit.
_HEADING_RE = re.compile(r"^#{2,6}\s+\S", re.MULTILINE)  # "## " / "### " etc, not the H1 title


def _split_heading_guided(body: str) -> List[str]:
    """Split ``body`` on ``##``+ headings, each section = heading line + its following text.

    The heading line itself is KEPT in the chunk (it is often the most information-dense line
    -- "Why:"/"How to apply:"-style headers, or a descriptive section title -- dropping it
    would throw away a cheap, strong signal). Returns [] when there is no ``##``+ heading
    anywhere in ``body`` (the caller falls back to paragraph-guided splitting in that case).
    """
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return []
    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append(body[start:end].strip())
    return [s for s in sections if s]


def _split_paragraph_guided(body: str) -> List[str]:
    """Split ``body`` on blank-line-separated paragraphs (the fallback when no heading
    structure exists -- most of this corpus's memories are a few bolded-label paragraphs,
    per ``feedback_root_cause_not_symptom_handling.md``'s shape: no ``##`` headings at all,
    just ``**Why:**``/``**How to apply:**`` paragraphs and bullet lists)."""
    paras = re.split(r"\n\s*\n", body)
    return [p.strip() for p in paras if p.strip()]


def compute_body_chunks(name: str, text: str) -> List[dict]:
    """Bounded body chunks for one memory -> ``[{text, tokens, hash}, ...]`` (<=3, each
    >=~80 chars). ``text`` is the FULL raw file (frontmatter + body), matching every other
    reader in this module (``extract_description``, ``compute_corpus``).

    Bounds (deliberately tight -- a backstop, not a second index):
      - only the first ``_BODY_CHUNK_CHAR_BUDGET`` (~1500) chars of the body are considered
        at all -- a memory's most important body content is overwhelmingly near the top
        (the lede), and this caps embedding/BM25 cost from growing with body length;
      - heading-guided (``## ...`` sections) when the body has any ``##``+ heading, else
        paragraph-guided (blank-line-separated paragraphs) -- see the two helpers above;
      - at most ``_BODY_CHUNK_MAX`` (3) chunks survive -- the first 3 (in body order, which
        is authoring order -- the memory's own structure already puts the load-bearing
        content first for a human reader, and this indexer trusts that same ordering);
      - a chunk shorter than ``_BODY_CHUNK_MIN_CHARS`` (~80) is skipped as trivia (a bare
        heading with nothing under it, a one-line stub) rather than indexed as noise.

    ``hash`` is per-CHUNK (sha1 of the chunk's own text, not the whole doc) so build_index's
    incremental reuse is keyed exactly like entry rows: a chunk whose text is byte-identical
    to a prior build's reuses that prior build's embedding row; only genuinely NEW/changed
    chunk text gets re-embedded. Never raises -- any parse hiccup degrades to [] (a memory
    with unparseable body content simply gets no body chunks, never a crash).
    """
    try:
        _, body = split_frontmatter(text)
        # DRM-2: the machine-managed dream:links block is ADJACENCY data, not memory
        # content — strip it from chunking UNCONDITIONALLY (both HIPPO_DREAM arms). Its
        # stamp text (target slugs, firing queries) would otherwise enter the BM25/dense
        # body rows and let an applied edge perturb lexical RANKING — the A/B arm must
        # toggle edge admission ONLY, and a query matching a stamp's tokens must never
        # false-hit the stamped memory. The graph reader (links.LinkGraph) still sees the
        # raw text and parses the block's wikilinks when admitted.
        from .links import strip_dream_edges

        body, _stamped = strip_dream_edges(body or "")
        body = body.strip()
        if not body:
            return []
        budget = body[:_BODY_CHUNK_CHAR_BUDGET]
        sections = _split_heading_guided(budget) or _split_paragraph_guided(budget)
        chunks: List[dict] = []
        for section in sections:
            if len(chunks) >= _BODY_CHUNK_MAX:
                break
            if len(section) < _BODY_CHUNK_MIN_CHARS:
                continue
            chunks.append(
                {
                    "text": section,
                    "tokens": bm25_terms(tokenize(section)),
                    "hash": _hash(section),
                }
            )
        return chunks
    except Exception:
        return []


def _extract_invalid_after(fm: dict) -> Optional[str]:
    """The memory's ``invalid_after`` (top-level or under ``metadata:``), or ``None``.

    Mirrors ``extract_description``'s exact top-level-then-``metadata:`` fallback. This is
    load-bearing: every OTHER provenance-style key in this corpus (``cited_paths``,
    ``source_commit``) nests under ``metadata:`` when present, and
    ``staleness.set_invalid_after`` follows that same convention — a top-level-only read
    here would make Tier 3's soft-invalidation PERMANENTLY inert the moment a memory's
    frontmatter uses the nested schema, not just a no-op on first ship.

    Also coerces a YAML-auto-typed ``date``/``datetime`` value (``yaml.safe_load`` parses an
    UNQUOTED ``invalid_after: 2026-06-01`` — the most natural hand-authored form — into a
    native ``datetime.date``, not a ``str``) to its ISO string, rather than silently
    discarding it. Without this, the value would also reach ``json.dump`` un-serializable
    and crash ``build_index()`` the first time anyone writes the field the natural way.
    """
    if not isinstance(fm, dict):
        return None
    ia = fm.get("invalid_after")
    if not ia:
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        ia = meta.get("invalid_after")
    if isinstance(ia, str):
        return ia
    if isinstance(ia, (date, datetime)):
        return ia.isoformat()
    return None


# GOV-2: the closed set of steer modes. `pin` is the only shipped mode — MUTE stays
# deferred until the salience keystone (SIG-5/T7) decides the down-weight class, and when
# it lands it must be COUNTED in doctor, never a silent full-suppress (inv3).
_VALID_STEER = ("pin",)


def _extract_steer(fm: dict) -> Optional[str]:
    """The memory's ``steer`` mode (top-level or under ``metadata:``), or ``None``.

    Mirrors ``_extract_invalid_after``'s exact top-level-then-``metadata:`` fallback — the
    corpus uses both frontmatter schemas, and a top-level-only read would leave steering
    PERMANENTLY inert for the nested one. The value is a CLOSED enum (``_VALID_STEER``),
    never a user-supplied float: an unknown/junk value reads as ``None`` (unsteered),
    fail-open, so a typo can never become an accidental ranking knob and the boost's cap
    lives in code (recall's ``_pin_boost``), not in user data.
    """
    if not isinstance(fm, dict):
        return None
    val = fm.get("steer")
    if not val:
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        val = meta.get("steer")
    if isinstance(val, str) and val.strip().lower() in _VALID_STEER:
        return val.strip().lower()
    return None


# GOV-7 → DRM-6: the closed set of author confidence tiers. Shipped display-only; DRM-6
# made the tier LOAD-BEARING in recall ranking (draft ×0.5 quarantine weight,
# authoritative ×1.1 bounded promotion, draft-only result sets collapse to abstention).
# The dial is the AUTHOR'S — ranking reads the declared tier, never popularity (the
# popularity=correctness trap stays closed); an unknown value reads as unset.
_VALID_CONFIDENCE = ("draft", "verified", "authoritative")


def _extract_confidence(fm: dict) -> Optional[str]:
    """The memory's ``confidence`` tier (top-level or under ``metadata:``), or ``None``.

    Same both-schema read as ``_extract_invalid_after``/``_extract_steer``; same
    closed-enum fail-open posture as steer — junk reads as unset, never a passthrough.
    """
    if not isinstance(fm, dict):
        return None
    val = fm.get("confidence")
    if not val:
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        val = meta.get("confidence")
    if isinstance(val, str) and val.strip().lower() in _VALID_CONFIDENCE:
        return val.strip().lower()
    return None


def compute_corpus(
    memory_dir: str,
    *,
    texts_out: Optional[Dict[str, str]] = None,
    sigs_out: Optional[Dict[str, List[int]]] = None,
    body_chunks_out: Optional[Dict[int, List[dict]]] = None,
) -> List[dict]:
    """Scan the corpus -> ordered entries
    ``{name, file, doc_text, description, hash, tokens, invalid_after, source_commit_time,
    steer, confidence}``.

    Order is deterministic (sorted filenames, from ``_iter_memory_files``). Re-scanned FRESH
    on every call (every file re-read from disk) — only the dense embedding ROW is
    cache-reused, keyed by ``hash`` (= sha1 of ``doc_text``, which is name + description
    ONLY). Adding ``invalid_after``/``source_commit_time`` therefore can never disturb
    embedding-cache reuse, and a metadata-only change (e.g. a fresh ``invalid_after``, or a
    reverify that bumps ``source_commit_time``) is reflected on every rebuild — including a
    rebuild whose embedding rows are entirely cache-hit.

    RET-5: ``source_commit_time`` (the frontmatter-persisted committer epoch —
    ``staleness.read_source_commit_time``, already computed for the staleness baseline, SHP-3)
    is copied verbatim into the entry here so recall's optional recency prior can read it off
    the already-loaded manifest — pure arithmetic, no git call on the hot path. ``None`` when
    the memory has no baseline yet (a hand-authored file that predates provenance backfill) —
    the recency prior treats that as "no signal", never a penalty.

    GRA-6: ``texts_out``/``sigs_out`` (caller-supplied dicts, mutated in place) capture the
    FULL text and stat signature ``[st_mtime_ns, st_size]`` per stem as a side product of
    the read this function already performs — so the caller can build/persist the wikilink
    graph with ZERO extra file reads. The stat runs BEFORE the read: a write racing between
    the two makes the recorded sig look STALE on the next freshness check (a wasted rebuild,
    the safe direction), never fresh-but-wrong.

    RET-2: ``body_chunks_out`` (caller-supplied dict, mutated in place, keyed by the ENTRY
    INDEX in the returned list) is the same "side product of a read this function already
    performs" pattern as ``texts_out``/``sigs_out`` — the entries list itself stays EXACTLY
    the shape it was before this item (every existing consumer of ``compute_corpus`` /
    ``entries`` is untouched), and body chunks are threaded to the caller (``build_index``)
    out-of-band instead of growing the entry dict.
    """
    entries: List[dict] = []
    for path in _iter_memory_files(memory_dir):
        try:
            st = os.stat(path) if sigs_out is not None else None
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        if texts_out is not None:
            texts_out[name] = text
        if st is not None and sigs_out is not None:
            sigs_out[name] = [st.st_mtime_ns, st.st_size]
        desc = extract_description(text)
        doc_text = f"{_name_words(name)}. {desc}".strip()
        fm = parse_frontmatter(text)
        if body_chunks_out is not None:
            body_chunks_out[len(entries)] = compute_body_chunks(name, text)
        entries.append(
            {
                "name": name,
                "file": os.path.basename(path),
                "doc_text": doc_text,
                "description": desc,  # stored separately so display never re-parses doc_text
                "hash": _hash(doc_text),
                "tokens": bm25_terms(tokenize(doc_text)),
                "invalid_after": _extract_invalid_after(fm),
                "source_commit_time": read_source_commit_time(text),
                # GOV-2: carried in the manifest so recall's pin boost is pure arithmetic
                # on already-loaded index state — never a file re-read on the hot path.
                "steer": _extract_steer(fm),
                # GOV-7: carried so the inject-time marker never re-reads a file per hit.
                "confidence": _extract_confidence(fm),
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# PRF-1: precomputed BM25 statistics (postings/doc_len/avgdl/idf), persisted into the
# manifest so query time never reconstructs BM25Okapi over the WHOLE corpus per query.
# --------------------------------------------------------------------------- #
# This mirrors rank_bm25.BM25Okapi's math EXACTLY (grounded against the installed
# rank_bm25 source AND the vendored plugin/memory/_vendor/bm25.py, which tests/test_vendor.py
# pins as score-identical to it): Okapi idf = ln((N - df + 0.5)/(df + 0.5)), with negative
# idfs floored to epsilon * average_idf (epsilon 0.25, rank_bm25's default). k1=1.5, b=0.75
# are rank_bm25's defaults too — recall._bm25_rank never overrides them, so hardcoding here
# (rather than threading them as params) keeps this function's signature simple; a future
# change to override k1/b would need to thread them through both here and the query-time
# fast path in lockstep, same as today.
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_EPSILON = 0.25


def compute_bm25_stats(corpus_tokens: List[List[str]]) -> dict:
    """Precompute BM25 postings/doc_len/avgdl/idf over ``corpus_tokens`` (one list per entry,
    in entry-index order). Returned dict is JSON-safe (all keys/values are str/int/float/list)
    so it can be written straight into the manifest and round-tripped through ``json.dump``.

    ``postings``: token -> [[entry_index, tf], ...] — ONLY docs actually containing the
    token, each paired with its raw term frequency. This is the structure that lets query
    time score a candidate in O(matched postings) instead of O(corpus): for each query token,
    walk its postings list directly rather than scanning every document to check membership.

    The IDF table is precomputed here so query time never re-derives it (that recomputation —
    one pass over every token's document frequency — was exactly the O(N)-per-query cost this
    item removes). Doc frequency (len of a token's postings list) and negative-IDF flooring
    follow rank_bm25.BM25Okapi._calc_idf verbatim (see the vendored fallback's mirrored
    implementation + tests/test_vendor.py's parity pin).
    """
    corpus_size = len(corpus_tokens)
    doc_len = [len(toks) for toks in corpus_tokens]
    avgdl = (sum(doc_len) / corpus_size) if corpus_size else 0.0

    # One pass: per-doc term frequency dict, immediately folded into the postings lists.
    postings: Dict[str, List[List[int]]] = {}
    for i, toks in enumerate(corpus_tokens):
        freqs: Dict[str, int] = {}
        for tok in toks:
            freqs[tok] = freqs.get(tok, 0) + 1
        for tok, tf in freqs.items():
            postings.setdefault(tok, []).append([i, tf])

    # Okapi IDF, negative values floored to epsilon * average_idf — identical to
    # rank_bm25.BM25Okapi / _vendor/bm25.py (a df of len(postings[tok]) is exactly the doc
    # frequency rank_bm25 computes, since postings only ever contains docs where tf > 0).
    idf: Dict[str, float] = {}
    negative: List[str] = []
    idf_sum = 0.0
    for tok, plist in postings.items():
        df = len(plist)
        val = math.log(corpus_size - df + 0.5) - math.log(df + 0.5)
        idf[tok] = val
        idf_sum += val
        if val < 0:
            negative.append(tok)
    average_idf = idf_sum / len(idf) if idf else 0.0
    floor = _BM25_EPSILON * average_idf
    for tok in negative:
        idf[tok] = floor

    return {
        "postings": postings,
        "doc_len": doc_len,
        "avgdl": avgdl,
        "idf": idf,
        "k1": _BM25_K1,
        "b": _BM25_B,
    }


# --------------------------------------------------------------------------- #
# Durable fastembed model cache (closes a live silent-degradation bug)
# --------------------------------------------------------------------------- #
# fastembed resolves its ONNX model cache from FASTEMBED_CACHE_PATH, DEFAULTING to the
# EPHEMERAL ``$TMPDIR/fastembed_cache`` (fastembed/common/utils.py::define_cache_dir). On
# macOS that lives under ``/var/folders`` which the OS PURGES on a schedule — silently
# wiping the ~130 MB ``bge-small-en-v1.5`` model. Once wiped, the OFFLINE recall + SessionStart
# refresh paths (allow_download=False) cannot re-fetch it, so hybrid recall degrades to
# BM25-only with NO error. Pin the cache to a durable, machine-shared dir so the model warms
# ONCE and survives reboots / temp purges. Exporting the env var is sufficient — fastembed
# honors it with no code change on its side. The two memory hooks export the same default;
# this Python-side setdefault additionally covers a manual ``python -m memory.build_index``
# run that never passed through a hook (the warm path), so the manual re-warm and the hook
# read paths share ONE cache dir.
#
# Default precedence (below an explicit FASTEMBED_CACHE_PATH, which ``ensure_fastembed_cache_path``
# honors): ``$CLAUDE_PLUGIN_DATA/fastembed`` when CLAUDE_PLUGIN_DATA is set — the packaged
# plugin's UPDATE-surviving data dir — else a platform-conventional home cache (OSP-2). The hooks
# run BEFORE this resolver and their export WINS via setdefault, so they implement the SAME order
# (see the cross-language guard in tests/test_fastembed_cache_path.py).
def platform_cache_dir(*, subpath: str = "hippo-memory") -> str:
    """The OS-conventional user cache dir, joined with ``subpath`` — macOS vs Linux/XDG (OSP-2).

    darwin -> ``~/Library/Caches/<subpath>``; anything else (Linux, and any unrecognized
    ``sys.platform`` — Windows is out of scope per OQ-2, so no hard-fail on an odd platform
    string) -> ``$XDG_CACHE_HOME/<subpath>`` or ``~/.cache/<subpath>`` when XDG_CACHE_HOME is
    unset/empty. Absolute and ``~``-expanded. Must stay equivalent to the bash branch the memory
    hooks mirror (same ``uname``-based split; see tests/test_fastembed_cache_path.py).
    """
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", subpath)
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME", "")
    if xdg_cache_home:
        return os.path.join(xdg_cache_home, subpath)
    return os.path.join(os.path.expanduser("~"), ".cache", subpath)


def durable_fastembed_cache_dir() -> str:
    """A durable, machine-shared cache dir for the fastembed model — NEVER under ``$TMPDIR``.

    Prefers ``$CLAUDE_PLUGIN_DATA/fastembed`` when CLAUDE_PLUGIN_DATA is set+non-empty (the
    packaged plugin's update-surviving data dir), else ``platform_cache_dir()/fastembed`` (macOS:
    ``~/Library/Caches/hippo-memory/fastembed``; Linux: XDG-or-``~/.cache/hippo-memory/fastembed``).
    Absolute and ``~``-expanded; stable across reboots and macOS temp purges. Must stay equivalent
    to the ``FASTEMBED_CACHE_PATH`` default the memory hooks export (same precedence in bash;
    ``set -u``-safe ``:+`` / ``:-`` expansions).
    """
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if plugin_data:  # non-empty (matches bash ${CLAUDE_PLUGIN_DATA:+...}); harness sets a clean abs path
        return os.path.join(plugin_data, "fastembed")
    return os.path.join(platform_cache_dir(), "fastembed")


def ensure_fastembed_cache_path() -> str:
    """Pin ``FASTEMBED_CACHE_PATH`` to the durable dir unless the caller already set it.

    Idempotent ``setdefault`` — it RESPECTS an explicit override (e.g. the hooks' export, or a
    future packaged plugin pointing it at its own data dir). Call this BEFORE importing /
    instantiating ``fastembed.TextEmbedding`` so every load — build, offline recall, SessionStart
    refresh — warms/reads the SAME durable cache. Returns the effective cache path.
    """
    os.environ.setdefault("FASTEMBED_CACHE_PATH", durable_fastembed_cache_dir())
    return os.environ["FASTEMBED_CACHE_PATH"]


# --------------------------------------------------------------------------- #
# Dense embedding (lazy fastembed; warms the model cache at BUILD time only)
# --------------------------------------------------------------------------- #
_MODEL_CACHE: dict = {}

# OSP-4: the fastembed model's HF *cache* repo id -- NOT the fastembed ``model_name``
# (``BAAI/bge-small-en-v1.5``) passed to ``TextEmbedding``. fastembed maps that name to this
# ``sources.hf`` id internally (``fastembed/text/onnx_embedding.py``'s SUPPORTED_MODELS) and
# snapshots it under ``models--{id.replace('/', '--')}``. Grounded against the installed
# fastembed 0.7.4 in this repo's .venv (see ``ModelSource(hf="qdrant/bge-small-en-v1.5-onnx-q")``)
# and against this machine's real warmed cache
# (``~/Library/Caches/hippo-memory/fastembed/models--qdrant--bge-small-en-v1.5-onnx-q``).
# Hardcoded (not looked up via a fastembed import) because importing even one fastembed
# submodule pulls in onnxruntime transitively (~500ms+) -- exactly the cost this pre-check
# exists to avoid paying on a cold cache. If ``HIPPO_EMBED_MODEL`` is ever pointed at a
# different model, the id below won't match -- see ``_expected_model_snapshot_dir``'s fallback.
#
# RET-3: ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`` is the
# ``--multilingual`` bootstrap preset's model (see ``resolve_embed_model``). Grounded the SAME
# way as the English entry above -- against this repo's installed fastembed 0.7.4:
# ``TextEmbedding.list_supported_models()`` returns ``ModelSource(hf="qdrant/paraphrase-
# multilingual-MiniLM-L12-v2-onnx-Q")`` for this model name. ``intfloat/multilingual-e5-small``
# (the roadmap's first-preference id) is NOT in this fastembed version's supported-model list
# (only ``intfloat/multilingual-e5-large`` is, at 2.24 GB) -- confirmed by running
# ``TextEmbedding.list_supported_models()`` against the installed .venv, per the roadmap's
# instruction to ground the choice rather than guess. Of the listed multilingual options this
# is the SMALLEST (0.22 GB, same 384-dim as bge-small-en-v1.5, ~50 languages, no query/passage
# prefix required) -- closest in spirit to the "small" default this plugin ships.
_HF_SOURCE_REPO_BY_MODEL = {
    "BAAI/bge-small-en-v1.5": "qdrant/bge-small-en-v1.5-onnx-q",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
}

# The multilingual bootstrap preset's model id -- named separately from
# ``_HF_SOURCE_REPO_BY_MODEL`` (which is keyed by whatever ``DEFAULT_MODEL`` resolves to) so
# bootstrap/doctor can reference "the multilingual model" without depending on module import
# order or re-deriving the id from the dict.
MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _expected_model_snapshot_dir(cache_dir: str) -> Optional[str]:
    """The on-disk snapshot dir fastembed would use for ``DEFAULT_MODEL``, or ``None``.

    ``None`` when the model isn't in ``_HF_SOURCE_REPO_BY_MODEL`` (an unrecognized
    ``HIPPO_EMBED_MODEL`` override) -- the pre-check can't ground a path for it, so the
    caller should skip the stat-check and fall through to the existing (bounded) load attempt
    rather than wrongly declaring "not cached".
    """
    hf_source_repo = _HF_SOURCE_REPO_BY_MODEL.get(DEFAULT_MODEL)
    if not hf_source_repo:
        return None
    return os.path.join(cache_dir, f"models--{hf_source_repo.replace('/', '--')}")


def _fastembed_model_cached(cache_dir: str) -> bool:
    """Pure-stat check: is ``DEFAULT_MODEL`` already warmed on disk at ``cache_dir``?

    NO fastembed import, NO network -- just directory/file existence, so this costs
    microseconds even on a cold-cache machine. Walks the snapshot dir (fastembed materializes
    the model files there as symlinks into ``blobs/``) looking for at least one ``.onnx``
    file; an existing-but-empty or partially-downloaded snapshot dir must NOT read as cached
    (a half-written model would still fail to load, so this would just trade a fast "no" for a
    slow, confusing failure inside fastembed instead). An unrecognized model (``None`` from
    ``_expected_model_snapshot_dir``) returns True -- "assume cached, let the real load attempt
    decide" -- so an unusual ``HIPPO_EMBED_MODEL`` override degrades to the old (bounded)
    behavior rather than being wrongly short-circuited to "unavailable".
    """
    snapshot_dir = _expected_model_snapshot_dir(cache_dir)
    if snapshot_dir is None:
        return True
    if not os.path.isdir(snapshot_dir):
        return False
    for root, _dirs, files in os.walk(snapshot_dir):
        if any(f.endswith(".onnx") for f in files):
            return True
    return False


def _normalize_rows(mat):
    import numpy as np

    arr = np.asarray(mat, dtype="float32")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _get_model(allow_download: bool):
    """Return a cached ``fastembed.TextEmbedding`` or raise.

    With ``allow_download=False`` (the recall/hook path) HF Hub is forced OFFLINE so a
    cache miss raises immediately instead of triggering a synchronous ~130 MB download. The
    build path (``allow_download=True``) is the ONLY place a download may happen.

    OSP-4: for the offline path, a cheap filesystem pre-check
    (``_fastembed_model_cached``) runs BEFORE the fastembed import -- on a cold/wiped cache
    (the common case on a fresh machine) this raises immediately without importing fastembed
    at all, so the caller falls back to BM25 in microseconds instead of needing a wall-clock
    timeout to bound an import+load that was never going to succeed.
    """
    if dense_disabled():
        raise RuntimeError("dense disabled via HIPPO_DISABLE_DENSE")
    key = (DEFAULT_MODEL, bool(allow_download))
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if not allow_download:
        # Belt: any cache miss now errors fast (no network) -> caller falls back to BM25.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Pin the model cache to a durable dir BEFORE the fastembed import so the model warms /
    # loads from a path that survives macOS temp purges (closes the silent BM25-degradation
    # bug). Unconditional: the BUILD path (allow_download=True) is where the model is WARMED,
    # so it must warm into the durable dir too — not just the offline read paths.
    cache_dir = ensure_fastembed_cache_path()
    if not allow_download and not _fastembed_model_cached(cache_dir):
        raise RuntimeError(f"fastembed model not cached offline at {cache_dir}")
    from fastembed import TextEmbedding  # lazy: never imported at module load

    model = TextEmbedding(model_name=DEFAULT_MODEL)
    _MODEL_CACHE[key] = model
    return model


# --------------------------------------------------------------------------- #
# RCL-5: offline cross-encoder rerank — EXPLICIT SURFACES ONLY (/hippo:recall, the MCP
# recall tool). Never the UserPromptSubmit hot path, so there is no p95 budget to protect —
# but a cold/uncached model must still degrade FAST: fastembed's own model loader wraps a
# cache MISS in a retry-with-backoff sleep loop regardless of WHY the load failed (confirmed
# empirically -- HF_HUB_OFFLINE=1 correctly blocks the actual network reach, but fastembed
# still burns ~40s of sleep-and-retry around that local failure before raising). Mirrors
# _get_model's cheap filesystem pre-check for exactly that reason: a cold cache must raise in
# microseconds, not after a ~40s hang on an "explicit surface" a human is waiting on.
# --------------------------------------------------------------------------- #
# Smallest fastembed-supported cross-encoder (0.08 GB) -- this feature's whole point is a
# cheap joint query/description read, not a heavyweight reranker. Verified against the
# installed fastembed 0.7.4 (TextCrossEncoder.list_supported_models()): this model's HF
# source repo IS its fastembed model name (unlike DEFAULT_MODEL's embedding mapping, which
# goes through a qdrant/* re-export) -- grounded, not guessed.
_CROSS_ENCODER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
_CROSS_ENCODER_HF_SOURCE_REPO = "Xenova/ms-marco-MiniLM-L-6-v2"
_CROSS_ENCODER_CACHE: dict = {}


def _cross_encoder_cached(cache_dir: str) -> bool:
    """Pure-stat check: is the cross-encoder model already warmed on disk at ``cache_dir``?

    Mirrors ``_fastembed_model_cached`` exactly (same snapshot-dir-then-.onnx-walk shape, no
    fastembed import, no network) -- see the module comment above for why this pre-check is
    load-bearing here, not just an optimization.
    """
    snapshot_dir = os.path.join(
        cache_dir, f"models--{_CROSS_ENCODER_HF_SOURCE_REPO.replace('/', '--')}"
    )
    if not os.path.isdir(snapshot_dir):
        return False
    for root, _dirs, files in os.walk(snapshot_dir):
        if any(f.endswith(".onnx") for f in files):
            return True
    return False


def _get_cross_encoder(allow_download: bool):
    """Return a cached ``fastembed`` ``TextCrossEncoder`` or raise -- mirrors ``_get_model``'s
    contract exactly. ``allow_download=False`` (every recall-time caller -- this is never on
    the hot path, but it never downloads either) forces HF Hub offline and pre-checks the
    cache on disk BEFORE import/construction, so a cold cache raises in microseconds instead
    of fastembed's own ~40s retry-and-sleep loop. ``allow_download=True`` (build/bootstrap
    time only) may download and warm the cache -- callers should invoke this at
    build_index/bootstrap time to warm it, exactly like the embedding model.
    """
    key = (_CROSS_ENCODER_MODEL, bool(allow_download))
    if key in _CROSS_ENCODER_CACHE:
        return _CROSS_ENCODER_CACHE[key]
    if not allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    cache_dir = ensure_fastembed_cache_path()
    if not allow_download and not _cross_encoder_cached(cache_dir):
        raise RuntimeError(f"cross-encoder model not cached offline at {cache_dir}")
    from fastembed.rerank.cross_encoder import TextCrossEncoder  # lazy: never imported at module load

    model = TextCrossEncoder(model_name=_CROSS_ENCODER_MODEL, cache_dir=cache_dir)
    _CROSS_ENCODER_CACHE[key] = model
    return model


def embed_documents(texts: List[str], allow_download: bool = True):
    """L2-normalized passage embeddings as a float32 matrix [len(texts), dim]."""
    model = _get_model(allow_download=allow_download)
    vecs = list(model.embed(texts))
    return _normalize_rows(vecs)


def embed_query(text: str, allow_download: bool = False):
    """L2-normalized query embedding (1-D). Uses the model's asymmetric ``query_embed``."""
    model = _get_model(allow_download=allow_download)
    embedder = getattr(model, "query_embed", None) or model.embed
    vec = list(embedder([text]))[0]
    return _normalize_rows(vec)[0]


# --------------------------------------------------------------------------- #
# Manifest / dense matrix IO
# --------------------------------------------------------------------------- #
def _read_manifest_json(index_dir: str) -> Optional[dict]:
    """Raw manifest read — NO schema gate. Only for surfaces that must SEE an old-version
    manifest in order to name it (``check_index_integrity``, doctor's format check); every
    load-bearing consumer goes through ``_load_manifest`` below, which enforces
    ``SCHEMA_VERSION``. ``None`` on a missing file, unparseable JSON, or a non-dict payload."""
    p = os.path.join(index_dir, _MANIFEST_NAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_manifest(index_dir: str) -> Optional[dict]:
    """The versioned manifest load (COR-7) — the ONE gate every consumer passes through.

    A manifest whose ``schema_version`` != the running module's ``SCHEMA_VERSION`` is
    treated as ABSENT (returns ``None``), exactly like a missing/corrupt file: the index is
    derived, so the only correct response to a shape this code no longer (or does not yet)
    writes is one full rebuild, never serving the stale shape verbatim. Consequences per
    caller, all automatic: ``build_index`` sees no old manifest -> no hash-keyed row reuse
    -> full re-embed; ``refresh_index``'s corpus-unchanged fast-path sees no old manifest ->
    falls through to that one full rebuild (which stamps the CURRENT version, so the next
    refresh no-ops again); ``load_index``/recall see no index -> the hook's implicit
    BM25-only build replaces it. This state is user-visible at doctor
    (``check_format_version``) — it is a routine plugin-update artifact, not corruption."""
    manifest = _read_manifest_json(index_dir)
    if manifest is None:
        return None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return None
    return manifest


def _load_dense(index_dir: str):
    p = os.path.join(index_dir, _DENSE_NAME)
    if not os.path.exists(p):
        return None
    try:
        import numpy as np

        return np.load(p)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Build (incremental)
# --------------------------------------------------------------------------- #
def build_index(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    *,
    force: bool = False,
    allow_download: bool = True,
    preserve_on_dense_fail: bool = False,
) -> dict:
    """(Re)build the hybrid index. Returns the manifest dict. Never raises on dense failure.

    Incremental: an entry whose content ``hash`` matches the prior manifest reuses its
    cached embedding row; only new/changed memories are embedded. ``force=True`` re-embeds
    everything. With fastembed unavailable/disabled, builds BM25-only (``dense_ready``
    False) — the BM25 part is always rebuilt (cheap), so the index is never stale.

    ``allow_download=False`` (the offline SessionStart refresh) forbids a model download and
    bounds the embed so a cold cache can't hang. The batch is embedded in
    ``DENSE_EMBED_CHUNK_SIZE``-sized slices against the SAME overall
    ``DENSE_REFRESH_TIMEOUT_SECS`` wall-clock budget: once a slice completes, no NEW slice is
    started if the budget is already exhausted, but everything embedded so far is still
    persisted (row=None for anything left un-embedded). This lets a large corpus converge to
    dense across sessions (each pass reuses the previous pass's rows via ``old_row_by_hash``)
    instead of an all-or-nothing 15s attempt that discards partial progress.

    ``dense_ready`` is True only when EVERY entry AND every body chunk (RET-2) ends up with a
    row — a partially-embedded corpus stays ``dense_ready=False`` (BM25-only) rather than
    exposing a half-filled dense matrix to recall, which cosine-scores placeholder rows as if
    they were real embeddings. The rows themselves (and the manifest's per-entry/per-chunk
    ``row``) are still saved so the next build's ``old_row_by_hash`` skips re-embedding them —
    "never worse than BM25" is protected for CURRENT recall while embedding progress is not
    thrown away.

    ``preserve_on_dense_fail=True`` means: if the existing index was dense and this build
    could NOT produce (fully) dense (offline embed failed or ran out of budget), leave the
    existing index untouched rather than DOWNGRADE it to BM25-only — "never worse".

    RET-2: body chunks are a BACKSTOP over the SAME ``dense.npy``/BM25-postings machinery as
    description rows, not a second index. Rows 0..N-1 (N = entry count) are description rows,
    exactly as before this item; rows N.. are body-chunk rows, in ``(entry_index, chunk_index)``
    order. Chunk rows reuse the identical hash-keyed incremental-embed logic as entry rows (see
    ``old_row_by_hash`` below, now populated from BOTH populations) so an unchanged body chunk
    is never re-embedded. BM25 gets the same treatment: ``compute_bm25_stats`` runs over the
    UNIFIED doc list (entries first, chunks appended) so entry doc indices 0..N-1 are BYTE
    IDENTICAL to the pre-RET-2 stats (the golden-equivalence tests pin this), and chunk docs
    just extend the same postings table at indices N...
    """
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)
    ensure_self_ignoring_dir(index_dir)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)

    texts: Dict[str, str] = {}
    sigs: Dict[str, List[int]] = {}
    body_chunks_by_entry: Dict[int, List[dict]] = {}
    entries = compute_corpus(
        memory_dir, texts_out=texts, sigs_out=sigs, body_chunks_out=body_chunks_by_entry
    )

    # GRA-6: persist the resolved wikilink graph (links.json) from the texts this build
    # already read — zero extra file reads. Done BEFORE the dense work (and before the
    # never-worse early return below): edge freshness is orthogonal to dense outcome, so a
    # failed/preserved dense build must still leave a CURRENT edge cache behind. Keyed by
    # per-file stat sigs, NOT doc_text hashes — wikilinks live in bodies, and body edits
    # don't change doc_text, so a hash-keyed cache would go silently stale. Never raises.
    try:
        from .links import LinkGraph, write_links_cache

        write_links_cache(index_dir, LinkGraph(memory_dir, texts=texts), sigs)
    except Exception:
        pass

    old_manifest = None if force else _load_manifest(index_dir)
    old_dense = None if force else _load_dense(index_dir)

    # RET-2: flatten body_chunks_by_entry into one deterministic list, in (entry_index,
    # chunk_index) order — this is also the manifest's persisted "body_chunks" ORDER, and
    # (once description rows occupy 0..N-1) the dense-row order for chunk rows N...
    body_chunks: List[dict] = []
    for entry_idx in range(len(entries)):
        for chunk in body_chunks_by_entry.get(entry_idx, []):
            body_chunks.append({"entry": entry_idx, "hash": chunk["hash"], "tokens": chunk["tokens"], "text": chunk["text"]})

    want_dense = not dense_disabled()
    dense_rows = None
    dense_ready = False
    if want_dense:
        try:
            import numpy as np

            n_entries = len(entries)
            n_chunks = len(body_chunks)
            n_total = n_entries + n_chunks

            # NOTE: gated on the model matching + a dense matrix being present — NOT on
            # old_manifest["dense_ready"] — so a PARTIALLY-embedded manifest (COR-3 chunked
            # embed, dense_ready=False but some entries do have a row) still contributes its
            # already-embedded rows here, letting a large corpus converge across sessions
            # instead of re-embedding from scratch every time the prior attempt fell short.
            # RET-2: this hash->row map is now populated from BOTH the old entries AND the
            # old body_chunks blocks -- a chunk hash-hit is exactly as cache-reusable as an
            # entry hash-hit (same incremental-embed contract, same manifest, same dense.npy).
            old_row_by_hash: Dict[str, int] = {}
            if old_manifest and old_dense is not None and old_manifest.get("model") == DEFAULT_MODEL:
                for e in old_manifest.get("entries", []):
                    if "row" in e and e["row"] is not None and 0 <= e["row"] < len(old_dense):
                        old_row_by_hash[e["hash"]] = e["row"]
                for c in old_manifest.get("body_chunks", []) or []:
                    if c.get("row") is not None and 0 <= c["row"] < len(old_dense):
                        old_row_by_hash[c["hash"]] = c["row"]

            # Unified (doc_text, hash) work list: entries first (rows 0..N-1), chunks appended
            # (rows N..) -- this is the row order dense_rows is assembled in below.
            all_hashes = [e["hash"] for e in entries] + [c["hash"] for c in body_chunks]
            all_texts = [e["doc_text"] for e in entries] + [c["text"] for c in body_chunks]

            to_embed_idx = [i for i in range(n_total) if all_hashes[i] not in old_row_by_hash]
            new_vecs_by_idx: Dict[int, "np.ndarray"] = {}
            if to_embed_idx:
                if allow_download:
                    # Online build (bootstrap/manual): one unbounded batch, as before.
                    batch_texts = [all_texts[i] for i in to_embed_idx]
                    vecs = embed_documents(batch_texts, allow_download=True)
                    for pos, i in enumerate(to_embed_idx):
                        new_vecs_by_idx[i] = vecs[pos]
                else:
                    # Offline: slice into bounded chunks against ONE overall wall-clock budget
                    # so a large corpus persists whatever it manages instead of all-or-nothing.
                    deadline = time.monotonic() + DENSE_REFRESH_TIMEOUT_SECS
                    for start in range(0, len(to_embed_idx), DENSE_EMBED_CHUNK_SIZE):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break  # budget exhausted -> stop starting new slices
                        chunk_idx = to_embed_idx[start : start + DENSE_EMBED_CHUNK_SIZE]
                        batch_texts = [all_texts[i] for i in chunk_idx]
                        try:
                            chunk_vecs = run_bounded(
                                lambda t=batch_texts: embed_documents(t, allow_download=False),
                                remaining,
                            )
                        except DenseTimeout:
                            break  # this slice didn't finish -> keep what's already persisted
                        for pos, i in enumerate(chunk_idx):
                            new_vecs_by_idx[i] = chunk_vecs[pos]

            dim = None
            if new_vecs_by_idx:
                dim = next(iter(new_vecs_by_idx.values())).shape[0]
            elif old_dense is not None and len(old_dense):
                dim = old_dense.shape[1]
            if dim is None:
                raise RuntimeError("could not determine embedding dim")

            rows = np.zeros((n_total, dim), dtype="float32")
            all_embedded = True
            row_of: List[Optional[int]] = [None] * n_total
            for i in range(n_total):
                h = all_hashes[i]
                if h in old_row_by_hash:
                    rows[i] = old_dense[old_row_by_hash[h]]
                    row_of[i] = i
                elif i in new_vecs_by_idx:
                    rows[i] = new_vecs_by_idx[i]
                    row_of[i] = i
                else:
                    row_of[i] = None
                    all_embedded = False
            for i, e in enumerate(entries):
                e["row"] = row_of[i]
            for j, c in enumerate(body_chunks):
                c["row"] = row_of[n_entries + j]
            dense_rows = rows
            dense_ready = all_embedded
        except Exception:
            # Any dense failure (no fastembed, no cached model, offline miss, timeout) -> BM25.
            dense_rows = None
            dense_ready = False

    # Never-worse guard: don't overwrite a complete dense index with a BM25-only one just
    # because an OFFLINE embed couldn't run. Leave the last good index in place.
    if (
        preserve_on_dense_fail
        and not dense_ready
        and old_manifest is not None
        and old_manifest.get("dense_ready")
    ):
        return old_manifest

    if dense_rows is None:
        # Total dense failure (no partial progress to keep) -> every entry/chunk is row=None.
        for e in entries:
            e["row"] = None
        for c in body_chunks:
            c["row"] = None

    # PRF-1 (+ RET-2): precompute BM25 postings/doc_len/avgdl/idf ONCE at build time (cheap,
    # pure python — no numpy/fastembed needed) so query time never reconstructs BM25Okapi over
    # the whole corpus. UNIFIED doc space: entries first (doc indices 0..N-1, byte-identical to
    # the pre-RET-2 stats — the golden-equivalence tests pin this), body chunks appended (doc
    # indices N..). recall._bm25_rank's fast path indexes into this SAME order for both.
    bm25_stats = compute_bm25_stats(
        [e.get("tokens") or [] for e in entries] + [c.get("tokens") or [] for c in body_chunks]
    )

    # RCL-6 CORRECTION (was RET-2's original comment here): chunk TEXT is now persisted
    # after all -- RCL-6's evidence snippet needs the winning body chunk's verbatim text at
    # recall/emit time, and re-reading the source file per-hit would be a hot-path file read
    # this module's own contract forbids. Duplicating a bounded per-chunk string (the index
    # is gitignored/derived/rebuildable either way, per inv1) is the cheaper, hot-path-safe
    # trade -- SCHEMA_VERSION bumped 3->4 for this shape change.
    body_chunks_manifest = [
        {"entry": c["entry"], "hash": c["hash"], "tokens": c["tokens"], "row": c["row"], "text": c["text"]}
        for c in body_chunks
    ]

    # RCL-6: manifest-wide ``head_commit`` -- the "indexed @<sha>" evidence-snippet mark's
    # source. ``memory_dir`` is already resolved to a real path by this point; ``git -C``
    # resolves the toplevel repo from a subdirectory transparently, so no repo_root plumbing
    # is needed here. Mirrors telemetry.log_episode's identical rev-parse pattern. One git
    # call PER BUILD (never per-query -- the hot path only ever reads this cached value).
    head_commit = None
    try:
        from .provenance import run_git

        head_commit = run_git(["rev-parse", "HEAD"], memory_dir).strip() or None
    except Exception:
        head_commit = None

    manifest = {
        "schema_version": SCHEMA_VERSION,
        # A partially-embedded (dense_ready=False) manifest still names the model so the
        # next build's cache-reuse check (old_manifest.get("model") == DEFAULT_MODEL) fires.
        "model": DEFAULT_MODEL if dense_rows is not None else None,
        "dense_ready": dense_ready,
        "dim": int(dense_rows.shape[1]) if dense_rows is not None else None,
        "count": len(entries),
        "entries": entries,
        "body_chunks": body_chunks_manifest,
        "bm25": bm25_stats,
        "head_commit": head_commit,
    }

    # COR-12: dense.npy (or its removal) is durably in place BEFORE the manifest that
    # references it is made visible — a recall racing this rebuild must never observe a
    # manifest ahead of its data (e.g. dense_ready=true with a stale/missing dense.npy).
    dense_path = os.path.join(index_dir, _DENSE_NAME)
    if dense_rows is not None:
        # Persisted even when dense_ready=False (partial progress) so the next build's
        # old_row_by_hash can resume from here instead of re-embedding from scratch.
        import numpy as np

        tmp_dense_path = dense_path + ".tmp.npy"
        try:
            np.save(tmp_dense_path, dense_rows)
            os.replace(tmp_dense_path, dense_path)
        finally:
            if os.path.exists(tmp_dense_path):
                try:
                    os.remove(tmp_dense_path)
                except Exception:
                    pass
    elif os.path.exists(dense_path):
        # Stale dense file from a prior dense build — remove so recall doesn't misread it.
        try:
            os.remove(dense_path)
        except Exception:
            pass

    manifest_path = os.path.join(index_dir, _MANIFEST_NAME)
    tmp_manifest_path = manifest_path + ".tmp"
    try:
        with open(tmp_manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        os.replace(tmp_manifest_path, manifest_path)
    finally:
        if os.path.exists(tmp_manifest_path):
            try:
                os.remove(tmp_manifest_path)
            except Exception:
                pass
    return manifest


def refresh_index(memory_dir: Optional[str] = None, index_dir: Optional[str] = None) -> Optional[dict]:
    """Incrementally bring the index up to date with the corpus — OFFLINE, never-raises.

    For the SessionStart hook: so a memory written during one session is indexed (and thus
    recallable) by the next. Fast no-op when nothing changed AND the index isn't degraded (a
    hash check, NO model load); otherwise an offline, bounded, never-downgrade incremental
    build. Returns the manifest (or the unchanged one), or None on any failure.

    COR-3: the short-circuit must NOT fire on an unchanged-but-degraded (``dense_ready``
    False) index — that would serve BM25-only recall forever, healed only by a corpus write
    perturbing a hash. Falling through to ``build_index`` retries the offline dense embed
    (bounded, chunked, never-downgrade), so a warm model cache upgrades the index to dense on
    this SessionStart instead of waiting on the next write.

    RET-2: "corpus unchanged" now ALSO compares body-chunk hashes (not just entry/description
    hashes) — a body edit that leaves the description untouched changes NO entry hash at all,
    so without this the no-op short-circuit would never notice a body-only edit and body
    drift would never heal, even across restarts. This is exactly the SessionStart rebuild
    ``_drift_patch``'s docstring (recall.py) defers body drift to — mid-session, a body edit
    is NOT patched live (that would mean re-embedding on the hot path); it heals HERE, at the
    next SessionStart refresh, the same as a stale dense row already does.
    """
    try:
        if memory_dir is None:
            memory_dir, _ = resolve_dirs()
        if index_dir is None:
            index_dir = default_index_dir(memory_dir)
        texts: Dict[str, str] = {}
        sigs: Dict[str, List[int]] = {}
        body_chunks_now: Dict[int, List[dict]] = {}
        entries_now = compute_corpus(
            memory_dir, texts_out=texts, sigs_out=sigs, body_chunks_out=body_chunks_now
        )
        old = _load_manifest(index_dir)
        if old is not None:
            old_hashes = [e.get("hash") for e in old.get("entries", [])]
            now_hashes = [e["hash"] for e in entries_now]
            old_chunk_hashes = [c.get("hash") for c in old.get("body_chunks", []) or []]
            now_chunk_hashes = [
                chunk["hash"]
                for entry_idx in range(len(entries_now))
                for chunk in body_chunks_now.get(entry_idx, [])
            ]
            # LIF-1: invalid_after is METADATA — it never perturbs a doc_text/body hash, so
            # a hash-only compare would no-op away every soft-invalidation (set OR cleared:
            # reverify_file strips the key) forever on an otherwise-quiet corpus, starving
            # recall's pre-cut penalty of the one field it acts on. Compare it explicitly,
            # the same starvation-proofing GRA-6 gave the body-link edge cache below.
            old_invalid = [e.get("invalid_after") for e in old.get("entries", [])]
            now_invalid = [e.get("invalid_after") for e in entries_now]
            # RET-5: source_commit_time is the SAME kind of metadata-not-in-doc_text field
            # invalid_after already needed starvation-proofing for — a provenance
            # --reverify (or a fresh backfill) bumps it without touching name/description,
            # so a hash-only compare would leave recall's optional recency prior reading a
            # stale baseline forever on an otherwise-quiet corpus.
            old_sct = [e.get("source_commit_time") for e in old.get("entries", [])]
            now_sct = [e.get("source_commit_time") for e in entries_now]
            # GOV-2: steer is the THIRD metadata-not-in-doc_text field needing this exact
            # starvation-proofing — pinning/unpinning never touches name/description, so a
            # hash-only compare would leave the manifest's steer stale forever on an
            # otherwise-quiet corpus and the boost would never engage (or never release).
            old_steer = [e.get("steer") for e in old.get("entries", [])]
            now_steer = [e.get("steer") for e in entries_now]
            # GOV-7: confidence is the FOURTH — an author re-grading draft→verified on a
            # quiet corpus must reach the inject-time marker, both directions.
            old_conf = [e.get("confidence") for e in old.get("entries", [])]
            now_conf = [e.get("confidence") for e in entries_now]
            corpus_unchanged = (
                old_hashes == now_hashes
                and old_chunk_hashes == now_chunk_hashes
                and old_invalid == now_invalid
                and old_sct == now_sct
                and old_steer == now_steer
                and old_conf == now_conf
            )
            if corpus_unchanged and (old.get("dense_ready") or dense_disabled()):
                # GRA-6: "corpus unchanged" above compares doc_text hashes, which BODY
                # edits do not perturb — but wikilinks live in bodies. Before skipping
                # the rebuild, independently verify the edge cache against the stat sigs
                # and re-persist it from the texts already in hand when stale/missing
                # (covers both body-only link edits and a pre-GRA-6 index that has no
                # links.json yet — otherwise the no-op path would starve the cache
                # forever on a quiet corpus). Never raises.
                try:
                    from .links import LinkGraph, links_cache_fresh, write_links_cache

                    if not links_cache_fresh(index_dir, sigs):
                        write_links_cache(index_dir, LinkGraph(memory_dir, texts=texts), sigs)
                except Exception:
                    pass
                return old  # corpus unchanged + already as good as it can be -> no-op
        return build_index(
            memory_dir, index_dir, allow_download=False, preserve_on_dense_fail=True
        )
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Load (for recall / eval)
# --------------------------------------------------------------------------- #
class LoadedIndex:
    """In-memory view of the persisted index. ``dense`` is None for a BM25-only index.

    QUA-4: ``manifest.json`` and ``dense.npy`` are read as TWO separate files (see
    ``load_index``) -- COR-12 made each individual write atomic, but a rebuild racing
    BETWEEN those two reads can still swap ``dense.npy`` out from under a reader that
    already has the OLD manifest in hand (e.g. the next build removes it, going
    BM25-only, or replaces it with a different-shape/different-entry-count matrix).
    Rather than exposing that torn pair, ``dense_ready`` is verified HERE against the
    actual loaded matrix's shape and every entry's ``row`` index -- any mismatch
    degrades to BM25-only for this read, exactly like a fully-failed dense load would.

    RET-2: ``body_chunks`` (a flat list of ``{entry, hash, tokens, row}`` -- RCL-6 added
    ``text`` to this shape, SCHEMA_VERSION 3->4 -- may be empty for a corpus with no
    qualifying body chunks) is validated against the SAME widened dense matrix -- a torn
    read must degrade the WHOLE dense view (entries AND chunks) to BM25-only together, never
    a half-valid matrix where entry rows are trusted but chunk rows are garbage (or vice
    versa).
    """

    def __init__(self, manifest: dict, dense):
        self.manifest = manifest
        self.entries: List[dict] = manifest.get("entries", [])
        self.body_chunks: List[dict] = manifest.get("body_chunks", []) or []
        dense_ready = bool(manifest.get("dense_ready")) and dense is not None
        if dense_ready and not self._dense_matches_entries(dense, self.entries, self.body_chunks):
            dense_ready = False
            dense = None
        self.dense_ready: bool = dense_ready
        self.dense = dense if self.dense_ready else None
        self.model: Optional[str] = manifest.get("model")

    @staticmethod
    def _dense_matches_entries(dense, entries: List[dict], body_chunks: Optional[List[dict]] = None) -> bool:
        try:
            n_rows = dense.shape[0]
        except Exception:
            return False
        if n_rows != len(entries) + len(body_chunks or []):
            return False
        for e in entries:
            row = e.get("row")
            if row is None or not (0 <= row < n_rows):
                return False
        for c in body_chunks or []:
            row = c.get("row")
            if row is None or not (0 <= row < n_rows):
                return False
        return True

    def __len__(self) -> int:
        return len(self.entries)


def load_index(index_dir: str) -> Optional[LoadedIndex]:
    """The persisted index as a LoadedIndex, or None when there is no usable manifest.

    PRF-4: honours ``dense_disabled()``. This is a correctness fix wearing a perf hat —
    ``HIPPO_DISABLE_DENSE=1`` was enforced at exactly ONE boundary, ``_get_model`` raising,
    which stops every consumer that must EMBED a query. ``recall._mmr_rerank`` is the only
    consumer that reads the STORED matrix and needs no model, so it walked straight past the
    single enforcement point; its own guard is ``dense is None``, which this function decided
    without consulting the flag. Net effect: the flag suppressed dense SCORING and left dense
    diversity reranking live, measurably changing result order — against ``dense_disabled``'s
    own docstring ("forced BM25-only"), the README, CONTRIBUTING, and STABILITY.md's
    committed meaning of the flag.

    ``new_memory`` already gets this right (``if dense_disabled() or not index.dense_ready
    or index.dense is None``); this is the same question asked in the one place that skipped
    it. Bonus: with dense skipped, numpy is never imported on the BM25 lane at all
    (measured ~-20ms, -30%), and ``eval_recall``'s ``backend`` label becomes accurate for
    free, since ``LoadedIndex.__init__`` computes ``dense_ready = manifest and dense is not
    None``.
    """
    manifest = _load_manifest(index_dir)
    if not manifest:
        return None
    dense = (
        _load_dense(index_dir)
        if (manifest.get("dense_ready") and not dense_disabled())
        else None
    )
    return LoadedIndex(manifest, dense)


# --------------------------------------------------------------------------- #
# QUA-5: on-disk corruption diagnosis (distinct from LoadedIndex's in-memory,
# already-degrades-gracefully view — this names WHAT is wrong on disk, for a
# SessionStart producer / doctor to surface, without needing a full recall).
# --------------------------------------------------------------------------- #
def check_index_integrity(index_dir: str) -> Optional[str]:
    """One-line diagnosis of on-disk index corruption, or ``None`` if nothing's wrong.

    Never raises. Distinguishes the FOUR silent-degradation states this item (and COR-8)
    close:
      (a) ``manifest.json`` exists but isn't valid JSON (truncated/garbled) — recall already
          degrades to an empty/rebuilt index via ``_load_manifest``'s except->None, but
          nothing said so; the next ``refresh_index``/``build_index`` call rebuilds from
          scratch (``old_manifest`` is None -> full re-embed) so this self-heals, but the
          CURRENT session's recall was silently empty/BM25-only until then.
      (b) manifest claims ``dense_ready: true`` but ``dense.npy`` is missing — the LOADED view
          (``LoadedIndex``) already degrades this to BM25-only, but the ON-DISK manifest still
          wrongly claims dense_ready until the next rebuild overwrites it.
      (c) ``dense.npy`` exists but its shape doesn't match the manifest (row count != entry
          count, or column count != declared ``dim``) — ``LoadedIndex``/`_dense_rank`` already
          degrade this to BM25-only without raising, but silently.
      (d) COR-8: the manifest's recorded ``model`` is set but differs from the CURRENTLY
          configured ``DEFAULT_MODEL`` (e.g. ``HIPPO_EMBED_MODEL`` changed, or a plugin
          update bumped the default, since this index was last built) — ``recall._dense_rank``
          already refuses to cosine-score across two different embedding spaces and degrades
          to BM25, but silently; this names BOTH models and the remediation.
    A missing manifest (no index built yet) is NOT corruption — returns ``None``. Neither
    is a schema-version mismatch (COR-7): that manifest is already treated as absent by
    every load path and rebuilt on the next refresh; ``doctor.check_format_version`` owns
    naming it, so this detector returns ``None`` rather than calling an update "damage".
    """
    try:
        manifest_path = os.path.join(index_dir, _MANIFEST_NAME)
        if not os.path.exists(manifest_path):
            return None  # nothing built yet -> not a corruption state
        # RAW read (not _load_manifest): a schema-version mismatch must NOT be mislabeled
        # as corrupt JSON here — it is a routine plugin-update state, not damage.
        manifest = _read_manifest_json(index_dir)
        if manifest is None:
            return (
                "index manifest is corrupt (invalid JSON) — will rebuild on next refresh"
            )
        if manifest.get("schema_version") != SCHEMA_VERSION:
            # Not corruption (COR-7): every load path already treats this manifest as
            # absent and the next build/refresh performs one full rebuild. doctor's
            # check_format_version owns reporting it; diagnosing the dense/model state of
            # a manifest no reader will ever serve would only mislead.
            return None
        dense_path = os.path.join(index_dir, _DENSE_NAME)
        if manifest.get("dense_ready"):
            if not os.path.exists(dense_path):
                return (
                    "index manifest claims dense embeddings exist but the data file is "
                    "missing — recall will degrade to BM25 until the next rebuild"
                )
            dense = _load_dense(index_dir)
            entries = manifest.get("entries", [])
            body_chunks = manifest.get("body_chunks", []) or []
            dim = manifest.get("dim")
            shape_ok = dense is not None and getattr(dense, "ndim", 0) == 2
            # RET-2: the matrix is WIDENED (description rows + body-chunk rows) — its row
            # count must match entries+chunks together, not entries alone.
            if shape_ok and dense.shape[0] != len(entries) + len(body_chunks):
                shape_ok = False
            if shape_ok and dim is not None and dense.shape[1] != dim:
                shape_ok = False
            if not shape_ok:
                return (
                    "index dense.npy shape does not match the manifest (row/column count "
                    "mismatch) — recall will degrade to BM25 until the next rebuild"
                )
        manifest_model = manifest.get("model")
        if manifest_model and manifest_model != DEFAULT_MODEL:
            return (
                f"index was embedded with model '{manifest_model}' but the configured model "
                f"is now '{DEFAULT_MODEL}' — recall will degrade to BM25 until the index is "
                "rebuilt (run `/hippo:doctor` or re-run bootstrap to rebuild the index)"
            )
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the agent-memory recall index (offline).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--force", action="store_true", help="re-embed every memory")
    args = parser.parse_args(argv)

    memory_dir, _ = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    index_dir = args.index_dir or default_index_dir(memory_dir)

    manifest = build_index(memory_dir, index_dir, force=args.force)
    print(f"index dir     : {index_dir}")
    print(f"memories      : {manifest['count']}")
    print(f"dense backend : {'ready (' + str(manifest['model']) + ')' if manifest['dense_ready'] else 'BM25-only (fastembed unavailable/disabled)'}")
    if manifest["dense_ready"]:
        print(f"embedding dim : {manifest['dim']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
