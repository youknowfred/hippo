"""Write-side threat lint for memory text (SEN-2) — the poisoning-payload sibling of secrets.py.

``secrets.py`` catches leaked CREDENTIALS. It is blind to memory-POISONING payloads: an
invisible zero-width/bidi/PUA codepoint smuggled into a body, a mixed-script homograph, an
HTML-comment instruction channel, an image-embed/data-query exfil shape, or
injection-imperative grammar. Those reach the corpus through capture/import/write with no
detection today, and a rules file is the exact Pillar-2025 attack target. This module is the
detector for them — a ``secrets.py`` SIBLING that reuses the same design doctrine (one
pattern set, warn-KIND-never-payload, never-raise) without touching or forking secrets.py's
credential table.

TIERED BY MEASURED PRECISION (ED-1's split, one level deeper — an over-broad flag surface
degrades the human-review channel, inv3):

  Tier-A — SURFACED + import HOLD. Deterministic codepoint/regex classes precise enough to
    show a human and to hold a foreign import:
      · invisible / dangerous Unicode — zero-width joins, bidi CONTROLS (Trojan Source),
        tag-block ASCII smuggling, Private Use Area. Carve-outs: emoji ZWJ sequences and
        variation selectors (legitimate emoji construction). RTL POSTURE (stated): the bidi
        CONTROL codepoints are flagged (they reorder rendered text — the attack); RTL SCRIPT
        LETTERS (Arabic/Hebrew) are never flagged (legitimate multilingual content uses
        letters, not control codes). A genuinely RTL corpus that embeds directional marks
        would see Tier-A noise — the multilingual mode is where that carve-out would live, a
        deferred follow-on, not this item.
      · mixed-script confusables — a SINGLE token drawing from Latin AND Cyrillic/Greek (the
        homograph attack, ``pаypal``). Whole-word multilingual prose (each token one script)
        is NOT flagged.
      · HTML comments — LINT-ONLY, ED-3-gated (see below). Flagged, never neutralized.
        Scoped to comments OUTSIDE code spans/fences (COR-21): the class is hiddenness, and
        a code span renders the comment as literal visible text. The ONLY masked class —
        see ``_html_comment_findings`` for why the other three must not mask.
      · exfil shapes — scoped STRICTLY to image-embeds (``![](url)`` / ``<img src>``) and
        data-bearing query strings (a long opaque ``?param=<blob>``). NEVER a bare URL: a
        plain reference link is not an exfil shape, and flagging it would be exactly the
        noisy-warning anti-pattern secrets.py's header warns against.

  Tier-B — LEDGER-ONLY. Injection-imperative grammar (ignore-previous-instructions,
    tool-mimicry). MEASURED to a persisted ledger + one aggregate doctor line, NEVER
    surfaced, NEVER a HOLD, until a dated owner decision graduates it on a measured near-zero
    FP rate (``not_pursuing: tier-b-imperative-injection-flags``). hippo's OWN corpus is
    about prompt injection and CARRIES these phrases as data, so Tier-B WILL false-positive
    on it — which is precisely why surfacing it would degrade the review channel, and why it
    stays dark until the ledger proves the FP rate.

ED-3 SPIKE (HTML-comment stripping) — run + dated 2026-07-16, recorded on the ROADMAP item
and the SEN capstone: hook ``additionalContext`` is delivered to the model as RAW TEXT (the
UserPromptSubmit recall block, emitted by ``recall --stdin-json`` as
``hookSpecificOutput.additionalContext``, reaches the model verbatim — observed directly
this session, punctuation and markup intact; there is no markdown render or comment-strip on
that channel). So an HTML comment in a memory body WOULD survive into a consuming agent's
context: a real hidden-instruction channel. Conclusion: HTML comments stay Tier-A LINT (the
flag gives the human the signal); NEUTRALIZATION (stripping comment bytes at render) is
justified by the finding but DEFERRED behind a dated owner decision — removing content from a
memory body is a mutation, the ED-4 clean-break discipline, not a warn-time side effect.

CI leg (the fifth seam): the roadmap's sanctioned CI vehicle is CLB-1 ``--ci`` (T12,
UNBUILT). Per "SEC-8's CI gate has ONE vehicle (CLB-1 --ci); SEN-2 feeds it, does not fork
it," this module ships ``scan_files`` ready for that vehicle to call, and does NOT stand up a
second CI job. (The shipped ``memory.secrets --repo`` secret-scan gate is deliberately not
extended here — extending it would be forking a second CI surface for threats.) The four
live seams — capture, new_memory write-ticket, import HOLD, doctor sweep — ship now.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Dict, List

# COR-21: the code-span masker COR-20 gave the link lint, shared — not a second copy.
# A leaf module (stdlib ``re`` only), so the write-side seams that import this one keep
# their import cost.
from .markdown_code import strip_code

# --------------------------------------------------------------------------- #
# Tier-A class 1: invisible / dangerous Unicode.
# --------------------------------------------------------------------------- #
# Zero-width joins/spaces that hide bytes in otherwise-normal text.
_ZERO_WIDTH = frozenset(
    {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x2061, 0x2062, 0x2063, 0x2064}
)
# Bidi CONTROLS — the Trojan-Source reordering set. RTL SCRIPT LETTERS are NOT here (the
# stated posture): only the format controls that visually reorder text are the attack.
_BIDI_CONTROLS = frozenset(
    {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x200E, 0x200F, 0x061C}
)


def _is_pua(cp: int) -> bool:
    """Private Use Area (BMP + planes 15/16) — never legitimate in shared prose."""
    return 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD


def _is_tag_char(cp: int) -> bool:
    """Unicode TAG block (U+E0000–U+E007F) — the ASCII-smuggling channel."""
    return 0xE0000 <= cp <= 0xE007F


def _is_variation_selector(cp: int) -> bool:
    """VS1–16 (U+FE00–U+FE0F) + supplement (U+E0100–U+E01EF) — legitimate emoji presentation."""
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def _is_emoji_ish(cp: int) -> bool:
    """True for a codepoint that legitimately participates in an emoji ZWJ sequence.

    Deliberately broad — the point is the CARVE-OUT: a ZWJ flanked by emoji/VS is a
    legitimate sequence (family/profession emoji), so we must NOT flag it. Covers the main
    emoji blocks, regional indicators, VS, and skin-tone modifiers.
    """
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF        # misc symbols + dingbats
        or 0x1F000 <= cp <= 0x1F2FF      # mahjong/domino/enclosed
        or 0x1F1E6 <= cp <= 0x1F1FF      # regional indicators
        or 0x2190 <= cp <= 0x21FF        # arrows (some are emoji base)
        or 0x2B00 <= cp <= 0x2BFF
        or cp in (0x2122, 0x2139, 0x24C2, 0x3030, 0x303D, 0x3297, 0x3299)
        or _is_variation_selector(cp)
    )


def _invisible_findings(text: str) -> List[str]:
    """KINDS for invisible/dangerous codepoints — counts only, NEVER the payload bytes.

    Emitting the matched substring would re-inject the exact invisible payload into the
    log/agent transcript this warning lands in (secrets.py's own never-echo rule, sharper
    here: the payload is by definition unreadable). ZWJ inside an emoji sequence and any
    variation selector are carved out.
    """
    zero = bidi = pua = tag = 0
    n = len(text)
    for i, ch in enumerate(text):
        cp = ord(ch)
        if cp in _ZERO_WIDTH:
            if cp == 0x200D:
                prev_cp = ord(text[i - 1]) if i > 0 else 0
                next_cp = ord(text[i + 1]) if i + 1 < n else 0
                # Legitimate ONLY when emoji-flanked on BOTH sides (a real ZWJ sequence).
                if _is_emoji_ish(prev_cp) and _is_emoji_ish(next_cp):
                    continue
            zero += 1
        elif cp in _BIDI_CONTROLS:
            bidi += 1
        elif _is_variation_selector(cp):
            continue
        elif _is_tag_char(cp):
            tag += 1
        elif _is_pua(cp):
            pua += 1
    out: List[str] = []
    if zero:
        out.append(f"invisible Unicode: {zero} zero-width character(s)")
    if bidi:
        out.append(f"invisible Unicode: {bidi} bidi-control character(s) (text-reordering)")
    if tag:
        out.append(f"invisible Unicode: {tag} tag-block character(s) (ASCII smuggling)")
    if pua:
        out.append(f"invisible Unicode: {pua} Private-Use-Area character(s)")
    return out


# --------------------------------------------------------------------------- #
# Tier-A class 2: mixed-script confusables (within-token homographs).
# --------------------------------------------------------------------------- #
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)  # runs of >=2 letters (no digits/underscore)


def _script(cp: int) -> str:
    """The confusable-relevant script of a codepoint: latin/cyrillic/greek/other."""
    if 0x41 <= cp <= 0x5A or 0x61 <= cp <= 0x7A:
        return "latin"
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
        return "cyrillic"
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return "greek"
    try:
        name = unicodedata.name(chr(cp), "")
    except ValueError:
        return "other"
    if name.startswith("LATIN"):
        return "latin"
    if name.startswith("CYRILLIC"):
        return "cyrillic"
    if name.startswith("GREEK"):
        return "greek"
    return "other"


# Only these WITHIN-TOKEN combinations are the homograph attack. Latin+Cyrillic and
# Latin+Greek are the confusable-heavy pairs; anything else (or a token that is wholly one
# script) is legitimate.
_CONFUSABLE_PAIRS = (frozenset({"latin", "cyrillic"}), frozenset({"latin", "greek"}))


def _confusable_findings(text: str) -> List[str]:
    """One KIND when any single token mixes Latin with Cyrillic/Greek. Never the token."""
    hits = 0
    for m in _WORD_RE.finditer(text or ""):
        scripts = {_script(ord(c)) for c in m.group(0)}
        scripts.discard("other")
        if any(pair <= scripts for pair in _CONFUSABLE_PAIRS):
            hits += 1
    if hits:
        return [f"mixed-script confusable: {hits} token(s) mixing Latin with Cyrillic/Greek (homograph)"]
    return []


# --------------------------------------------------------------------------- #
# Tier-A class 3: HTML comments (LINT-ONLY, ED-3-gated — see module header).
# --------------------------------------------------------------------------- #
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _html_comment_findings(text: str) -> List[str]:
    """One KIND per body carrying a comment OUTSIDE code (COR-21 masks code first).

    The class is about HIDDENNESS, not about the byte sequence: a comment is invisible to
    a human reading rendered markdown while reaching the model verbatim (ED-3), and that
    asymmetry is the channel. A comment inside a code span or fenced block renders as
    literal visible text — the human sees exactly what the model sees — so it is
    documentation ABOUT the marker, not a marker. hippo's own corpus proved it: a memory
    writing ``(`<!-- hippo:agents-export:begin/end -->`)`` about this repo's AGENTS.md
    block markers gated the memory-review CI lane on PR #104, a false red on correct
    content that would have re-fired on every PR touching that file.

    Deliberately the ONLY Tier-A class that masks. The other three do not turn on being
    hidden from a renderer: a zero-width codepoint inside backticks is still invisible, a
    Cyrillic ``а`` in a code span is still a homograph — masking those would be softening
    the class, not correcting its scope.
    """
    n = len(_HTML_COMMENT_RE.findall(strip_code(text or "")))
    if n:
        return [f"HTML comment: {n} comment(s) (hidden-instruction channel; lint-only, not neutralized)"]
    return []


# --------------------------------------------------------------------------- #
# Tier-A class 4: exfil shapes — image embeds + data-bearing query strings ONLY.
# --------------------------------------------------------------------------- #
# A markdown image embed pointing at an external http(s) host (auto-fetched on render).
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(https?://[^)\s]+)\)", re.I)
# An HTML <img> with an external http(s) src (same auto-fetch beacon shape).
_HTML_IMG_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"']?\s*https?://", re.I)
# A URL query parameter carrying a long opaque value — the data-bearing exfil query. The
# >=24-char opaque run is what distinguishes ``?data=<base64 blob>`` from ``?tab=2``.
_DATA_QUERY_RE = re.compile(r"https?://[^\s)]+\?[^\s)]*=[A-Za-z0-9+/_-]{24,}")


def _exfil_findings(text: str) -> List[str]:
    out: List[str] = []
    body = text or ""
    if _MD_IMAGE_RE.search(body) or _HTML_IMG_RE.search(body):
        out.append("exfil shape: external image embed (auto-fetched beacon)")
    if _DATA_QUERY_RE.search(body):
        out.append("exfil shape: URL with a data-bearing query string")
    return out


# --------------------------------------------------------------------------- #
# Tier-B: injection-imperative grammar — LEDGER-ONLY (never surfaced, never HOLD).
# --------------------------------------------------------------------------- #
_TIER_B_PATTERNS = [
    (re.compile(r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b"
                r"[^.\n]{0,20}\b(?:instruction|instructions|prompt|context|rules?)\b", re.I),
     "imperative: ignore/override prior instructions"),
    (re.compile(r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b|\bnew\s+(?:instructions|directive|rules)\s*:",
                re.I),
     "imperative: role/instruction reassignment"),
    (re.compile(r"(?m)^\s*(?:system|assistant|human|user|developer)\s*:\s*\S", re.I),
     "tool-mimicry: fake role/turn prefix"),
    (re.compile(r"<\s*/?\s*(?:system|tool_call|function_call|assistant)\b", re.I),
     "tool-mimicry: fake tool/role tag"),
]


def scan_tier_a(text) -> List[str]:
    """Tier-A threat KINDS for ``text`` — the SURFACED classes (also the import-HOLD set).

    Deterministic, zero LLM. Order-stable, deduplicated. Each entry names the KIND with a
    count, NEVER the matched payload (echoing an invisible/homograph payload re-injects it).
    Never raises: a scan failure returns [] rather than blocking a write (secrets.py's
    warn-never-block posture). Tier-B grammar is deliberately absent — it is measured by
    ``scan_tier_b``, never surfaced here (inv3).
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        out: List[str] = []
        out.extend(_invisible_findings(text))
        out.extend(_confusable_findings(text))
        out.extend(_html_comment_findings(text))
        out.extend(_exfil_findings(text))
        # dedupe order-stable
        seen: set = set()
        deduped: List[str] = []
        for k in out:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        return deduped
    except Exception:
        return []


def scan_tier_b(text) -> List[str]:
    """Tier-B injection-imperative KINDS — MEASURED ONLY, never surfaced, never a HOLD.

    These graduate to Tier-A only on a dated owner decision backed by a ledger-measured
    near-zero FP rate. Deterministic, zero LLM, never the payload, never raises.
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        found: List[str] = []
        for pattern, kind in _TIER_B_PATTERNS:
            if kind in found:
                continue
            if pattern.search(text):
                found.append(kind)
        return found
    except Exception:
        return []


def scan_threats(text) -> Dict[str, List[str]]:
    """``{"tier_a": [...], "tier_b": [...]}`` — the two tiers, strictly separated.

    The single entry point a seam calls when it wants both (e.g. capture, which flags on
    Tier-A and measures Tier-B). The tiers never bleed: nothing in ``tier_a`` is an
    imperative-grammar hit, and nothing in ``tier_b`` is ever surfaced by ``scan_tier_a``.
    """
    return {"tier_a": scan_tier_a(text), "tier_b": scan_tier_b(text)}


def scan_files(paths) -> List[dict]:
    """Tier-A-only scan over ``paths`` — one ``{"file", "warnings"}`` per hit. Never raises.

    The reusable file-list core the CLB-1 ``--ci`` vehicle (T12, unbuilt) will call to gate a
    shipped tree against poisoning payloads, mirroring ``secrets.scan_files``. Tier-B is
    deliberately excluded (it is ledger-measured, never a gate). A binary/unreadable file is
    skipped. This module ships the function; wiring it into a CI job is CLB-1's job — SEN-2
    does not fork a second CI surface.
    """
    findings: List[dict] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        warnings = scan_tier_a(text)
        if warnings:
            findings.append({"file": path, "warnings": warnings})
    return findings


def scan_corpus(memory_dir: str) -> List[dict]:
    """Corpus-wide Tier-A sweep — one ``{"file", "warnings"}`` per flagged memory file.

    Backs doctor's ``check_threat_lint``. Same shape/never-raise contract as
    ``secrets.scan_corpus``; clean files are omitted, so ``[]`` means the corpus carries no
    surfaced-tier threat payloads. Deterministic (sorted file walk via _iter_memory_files).
    """
    findings: List[dict] = []
    try:
        from .provenance import _iter_memory_files

        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            warnings = scan_tier_a(text)
            if warnings:
                findings.append({"file": os.path.basename(path), "warnings": warnings})
    except Exception:
        return findings
    return findings
