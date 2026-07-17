"""Write-side threat lint (SEN-2) — deterministic detection of memory-poisoning payloads.

``secrets.py``'s SIBLING, not an extension of it: ``secrets._PATTERNS``/``scan_text`` stay
byte-identical (the SEC-8 CI gate and every credential surface keep their exact semantics),
while this module owns the THREAT classes — content shapes whose purpose is to smuggle
instructions into agent context or exfiltrate it, rather than to leak a credential. A
memory corpus is a durable injection channel (committed once, re-injected on every recall),
and rules files are the documented attack target (the Pillar invisible-Unicode campaign),
so detection runs at the WRITE-SIDE seams — capture, the write ticket, import, doctor —
never the recall hot path (inv6). The one hot-path-adjacent capability (render-time
codepoint stripping) is deliberately NOT built.

TIERED BY PRECISION (ED-1 applied one level deeper — inv3: an over-broad flag surface
degrades the exact human-review channel it feeds):

Tier-A — deterministic, near-zero-FP classes, SURFACED (flags at every seam; the
invisible-unicode / mixed-script / exfil classes also HOLD an import):
  - ``invisible-unicode``: zero-width characters, bidi CONTROLS, Unicode TAG characters
    (the invisible-instruction smuggling alphabet), and private-use-area codepoints.
    Carve-outs keep real text clean: ZWJ inside an emoji join sequence never flags;
    isolated variation selectors (emoji presentation, CJK ideographic variation) never
    flag — only a RUN of them (a data-smuggling shape) does. RTL POSTURE, stated: bidi
    control CHARACTERS are always flagged (the Trojan-Source shape); RTL SCRIPTS
    (Hebrew/Arabic letters) are NEVER flagged — a genuinely RTL corpus stays clean
    because prose needs no explicit direction-override controls in markdown.
  - ``mixed-script``: a single word-token mixing ASCII Latin with Cyrillic/Greek
    HOMOGLYPHS (the lookalike alphabet, curated below) — the spoofed-identifier shape.
    Whole-word single-script text (a Russian or Greek memory) never flags; only the
    Latin×lookalike mix inside ONE token does.
  - ``exfil-link``: remote image embeds (``![...](https://...)`` — the auto-fetch beacon
    shape) and URLs whose query string carries a long opaque data value. NEVER a bare
    URL: an ordinary docs link, however unfamiliar, is not a finding (scope is the
    load-bearing precision decision).
  - ``html-comment``: LINT-ONLY — flagged (a hidden-from-render channel is worth naming
    at review time) but it never joins the import HOLD set and is never neutralized:
    markdown authors legitimately write ``<!-- -->`` comments, and any stripping decision
    is ED-3-gated on an in-harness spike of whether the harness already strips them from
    hooks' additionalContext (finding recorded on the SEN-2 roadmap item; until a dated
    owner decision, lint-only is the whole ambition).

Tier-B — imperative injection grammar (ignore-previous-instructions, role reassignment,
tool/role mimicry, concealment directives): HELD DARK. Detected but LEDGER-ONLY — findings
append to a gitignored telemetry ledger and surface as ONE aggregate doctor line; no
per-write flag, no capture-seed stamp, and they MUST NOT enter import's HOLD set — until a
dated owner decision graduates a pattern on a ledger-measured near-zero FP rate
(not_pursuing: tier-b-imperative-injection-flags). English prose ABOUT injection (this
docstring, a security memory) matches these patterns — that is the expected FP mode and
exactly why the tier is dark: the ledger measures it instead of nagging about it.

CI: the SEC-8 CI gate has ONE vehicle — CLB-1's ``--ci`` review packet (T12, unbuilt).
``scan_files`` below is the FEEDER that vehicle calls when it lands; this module ships no
second CI surface and no repo-walk gate of its own (``memory.secrets --repo`` keeps the
credential gate; forking a second one was explicitly rejected).

Shared doctrine with secrets.py (same reasons, stated there): WARN NEVER BLOCK at hippo's
own write surfaces (import's foreign-content HOLD is the one deliberate exception, and it
mirrors the shipped secret-lint HOLD exactly); HIGH PRECISION OVER RECALL; NEVER ECHO the
payload — findings name codepoints (``U+200B×2``) and pattern ids, never the matched text,
because a finding line lands in logs/transcripts and re-emitting a smuggled payload there
would hand it the channel it wanted. Zero LLM, zero network, pure ``re``/``ord``.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Tier-A class 1: invisible Unicode.
# --------------------------------------------------------------------------- #

# Zero-width characters flagged UNCONDITIONALLY (no legitimate markdown-body use):
# ZWSP, ZWNJ, WORD JOINER, ZWNBSP/BOM-in-body. ZWJ (U+200D) is handled separately —
# it is load-bearing inside emoji join sequences and only flags OUTSIDE one.
_ZERO_WIDTH_ALWAYS = {0x200B, 0x200C, 0x2060, 0xFEFF}
_ZWJ = 0x200D

# Bidi CONTROL characters (Trojan-Source alphabet): embeddings/overrides + isolates +
# the implicit direction MARKS. Controls, never scripts — see the module docstring's
# stated RTL posture.
_BIDI_CONTROLS = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x200E, 0x200F}

# Variation selectors: basic (FE00-FE0F) + supplement (E0100-E01EF). Isolated use is
# legitimate presentation; a RUN of this many is the encode-data-in-selectors shape.
_VS_RUN_FLOOR = 4

# Unicode TAG characters (U+E0000-U+E007F): a full invisible ASCII twin — the classic
# instruction-smuggling alphabet. No carve-out; there is no legitimate memory-body use.
def _is_tag(cp: int) -> bool:
    return 0xE0000 <= cp <= 0xE007F


def _is_pua(cp: int) -> bool:
    """Private-use-area codepoints (BMP PUA + planes 15/16) — render as nothing/tofu."""
    return 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD


def _is_variation_selector(cp: int) -> bool:
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def _is_emojiish(ch: str) -> bool:
    """Loose emoji-side test for the ZWJ carve-out: is this char plausibly an emoji part?

    Deliberately GENEROUS (symbols, pictographs, VS-16, regional indicators, skin-tone
    modifiers): a generous carve-out only risks missing a ZWJ smuggled BETWEEN two emoji —
    a channel of a few bits — while a stingy one flags real family/flag/profession emoji
    in ordinary prose, which is the precision failure this module refuses first.
    """
    if not ch:
        return False
    o = ord(ch)
    return (
        o >= 0x1F000
        or 0x2190 <= o <= 0x2BFF  # arrows, misc symbols, dingbats
        or o == 0xFE0F  # VS-16 emoji presentation
        or o == 0x20E3  # combining enclosing keycap
    )


def _invisible_findings(text: str) -> List[Tuple[str, int]]:
    """``[(codepoint-label, count)]`` for every invisible-Unicode hit in ``text``.

    Sorted by codepoint for order-stable output. Labels are ``U+XXXX`` — the finding
    names the ALPHABET, never re-emits the smuggled content.
    """
    counts: Dict[int, int] = {}
    vs_run = 0
    for i, ch in enumerate(text):
        cp = ord(ch)
        if _is_variation_selector(cp):
            vs_run += 1
            if vs_run == _VS_RUN_FLOOR:
                counts[cp] = counts.get(cp, 0) + 1  # one finding per run, keyed on the run's edge char
            continue
        vs_run = 0
        if cp in _ZERO_WIDTH_ALWAYS or cp in _BIDI_CONTROLS or _is_tag(cp) or _is_pua(cp):
            counts[cp] = counts.get(cp, 0) + 1
        elif cp == _ZWJ:
            prev_ch = text[i - 1] if i > 0 else ""
            next_ch = text[i + 1] if i + 1 < len(text) else ""
            if not (_is_emojiish(prev_ch) and _is_emojiish(next_ch)):
                counts[cp] = counts.get(cp, 0) + 1
    return [(f"U+{cp:04X}", n) for cp, n in sorted(counts.items())]


# --------------------------------------------------------------------------- #
# Tier-A class 2: mixed-script confusables.
# --------------------------------------------------------------------------- #

# The curated LOOKALIKE alphabets — codepoints that render (near-)identically to an
# ASCII Latin letter. Deliberately not "any Cyrillic/Greek": a token like "kΩ" (Latin k +
# OMEGA, no lookalike) or wholly-Cyrillic prose must never flag; "pаypal" (Cyrillic а)
# must. Small and curated beats complete and noisy — same doctrine as secrets._PATTERNS.
_CYRILLIC_LOOKALIKES = set("аеорсухіјѕќґЁ") | set("АВЕЗКМНОРСТУХІЈЅ")
_GREEK_LOOKALIKES = set("οικνρτυχ") | set("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ")
_LOOKALIKES = _CYRILLIC_LOOKALIKES | _GREEK_LOOKALIKES

_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _mixed_script_findings(text: str) -> int:
    """Count of word-tokens mixing ASCII Latin letters with confusable lookalikes."""
    hits = 0
    for tok in _WORD_TOKEN_RE.findall(text):
        has_latin = any("a" <= c.lower() <= "z" for c in tok if ord(c) < 128)
        if has_latin and any(c in _LOOKALIKES for c in tok):
            hits += 1
    return hits


# --------------------------------------------------------------------------- #
# Tier-A class 3: exfil-link shapes.  Class 4: HTML comments (lint-only).
# --------------------------------------------------------------------------- #

# Remote image embed: markdown auto-render turns it into a zero-click fetch whose URL an
# attacker controls — the beacon shape. Local/relative image paths never flag.
_IMAGE_EMBED_RE = re.compile(r"!\[[^\]]*\]\(\s*https?://", re.IGNORECASE)

# Data-bearing query string: one query-param VALUE long and opaque enough to be a payload
# (≥24 chars of token-ish material, no dots — dots reintroduce filename/tracking-slug FPs).
# A bare URL, a short ?tab=readme, a ?v=<11-char id> all stay clean by construction.
_DATA_QUERY_RE = re.compile(r"https?://[^\s)\"'<>]*[?&][A-Za-z0-9_.~-]+=[A-Za-z0-9+/=_%-]{24,}")

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# --------------------------------------------------------------------------- #
# Tier-A assembly: the one class table (mirrors secrets' one-list architecture).
# --------------------------------------------------------------------------- #

# Classes that HOLD a foreign import (import_mdc) — html-comment is deliberately absent
# (lint-only pending ED-3; markdown comments are legitimate authoring practice).
HOLD_CLASSES = ("invisible-unicode", "mixed-script", "exfil-link")
LINT_ONLY_CLASSES = ("html-comment",)
TIER_A_CLASSES = HOLD_CLASSES + LINT_ONLY_CLASSES

_MAX_CODEPOINTS_NAMED = 6


def scan_details(text: str) -> List[dict]:
    """Structured Tier-A findings: ``[{"class", "message", "count"}]``. [] when clean.

    ``message`` is the human line every warn surface prints — KIND + counts + (for
    invisible Unicode) the codepoint labels, NEVER the matched text. Deterministic and
    order-stable (class-table order). Never raises: a scan failure returns [] rather
    than blocking any write (the module-header doctrine).
    """
    try:
        out: List[dict] = []
        inv = _invisible_findings(text)
        if inv:
            shown = ", ".join(f"{label}×{n}" for label, n in inv[:_MAX_CODEPOINTS_NAMED])
            more = f" (+{len(inv) - _MAX_CODEPOINTS_NAMED} more)" if len(inv) > _MAX_CODEPOINTS_NAMED else ""
            total = sum(n for _, n in inv)
            out.append(
                {
                    "class": "invisible-unicode",
                    "message": f"invisible Unicode detected: {total} zero-width/bidi/tag/PUA "
                    f"codepoint(s) ({shown}{more}) — an invisible-instruction smuggling shape",
                    "count": total,
                }
            )
        mixed = _mixed_script_findings(text)
        if mixed:
            out.append(
                {
                    "class": "mixed-script",
                    "message": f"mixed-script confusable detected: {mixed} token(s) mix Latin "
                    "with Cyrillic/Greek lookalike letters — a homoglyph-spoof shape",
                    "count": mixed,
                }
            )
        embeds = len(_IMAGE_EMBED_RE.findall(text))
        data_qs = len(_DATA_QUERY_RE.findall(text))
        if embeds or data_qs:
            parts = []
            if embeds:
                parts.append(f"{embeds} remote image embed(s) (auto-fetch beacon shape)")
            if data_qs:
                parts.append(f"{data_qs} URL(s) with a long opaque query value (data-bearing exfil shape)")
            out.append(
                {
                    "class": "exfil-link",
                    "message": "exfil-link shape detected: " + "; ".join(parts) + " — bare URLs never flag",
                    "count": embeds + data_qs,
                }
            )
        comments = len(_HTML_COMMENT_RE.findall(text))
        if comments:
            out.append(
                {
                    "class": "html-comment",
                    "message": f"HTML comment(s) present: {comments} — a hidden-from-render channel "
                    "(lint-only pending the ED-3 harness-stripping spike; never a HOLD)",
                    "count": comments,
                }
            )
        return out
    except Exception:
        return []


def scan_text(text: str) -> List[str]:
    """Tier-A threat warnings for ``text`` — message lines only. [] when clean.

    The sibling of ``secrets.scan_text``: one detector, every surface (capture flag,
    write ticket, import, doctor sweep, and — when CLB-1's ``--ci`` lands — the CI
    feeder) calls THIS, so the class set never forks. Never raises.
    """
    return [d["message"] for d in scan_details(text)]


def hold_findings(text: str) -> List[str]:
    """The import-HOLD subset of Tier-A findings (never ``html-comment``, never Tier-B).

    ``import_mdc`` refuses to write foreign content while this is non-empty — exactly
    the shipped secret-lint HOLD posture. Lint-only classes are excluded by CONSTANT
    (``HOLD_CLASSES``), not by call-site judgment, so the boundary cannot drift.
    """
    return [d["message"] for d in scan_details(text) if d["class"] in HOLD_CLASSES]


def scan_corpus(memory_dir: str) -> List[dict]:
    """Corpus-wide Tier-A sweep: ``[{"file", "warnings"}]`` — secrets.scan_corpus's twin.

    Backs the doctor threat check. Clean files omitted; unreadable files skipped; never
    raises. Same walk order (``_iter_memory_files``) so output is deterministic.
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
            warnings = scan_text(text)
            if warnings:
                findings.append({"file": os.path.basename(path), "warnings": warnings})
    except Exception:
        return findings
    return findings


def scan_files(paths) -> List[dict]:
    """Tier-A scan over arbitrary files: ``[{"file", "warnings"}]`` per hit.

    THE CI FEEDER: when CLB-1's ``--ci`` review packet (T12) lands, it calls this beside
    ``secrets.scan_files`` — one vehicle, two detectors, no second CI surface here.
    Also covers the rules-plane sweep (doctor passes the GOV files — the Pillar target).
    Binary/unreadable files skip; never raises.
    """
    findings: List[dict] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        warnings = scan_text(text)
        if warnings:
            findings.append({"file": path, "warnings": warnings})
    return findings


# --------------------------------------------------------------------------- #
# Tier-B: imperative injection grammar — HELD DARK (ledger-only).
# --------------------------------------------------------------------------- #

# Pattern ids, not messages: Tier-B findings are never rendered as warnings anywhere.
# The id is what the ledger (and its one aggregate doctor line) carries.
_TIER_B_PATTERNS = [
    (
        re.compile(
            r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b"
            r"[^.\n]{0,40}\b(?:instruction|prompt|rule|direction)",
            re.IGNORECASE,
        ),
        "imperative-override",
    ),
    (re.compile(r"\byou are now\b|\bnew instructions?\s*:", re.IGNORECASE), "role-reassignment"),
    (
        re.compile(
            r"\b(?:do not|don't|never)\s+(?:tell|reveal|mention|inform)\b[^.\n]{0,40}\b(?:user|human|owner)\b",
            re.IGNORECASE,
        ),
        "concealment-directive",
    ),
    (re.compile(r"^\s*(?:system|assistant)\s*:", re.IGNORECASE | re.MULTILINE), "role-mimicry"),
    (re.compile(r"<\s*(?:antml:)?(?:function_calls|invoke|system|assistant)\b", re.IGNORECASE), "tool-mimicry"),
]


def scan_tier_b(text: str) -> List[str]:
    """Tier-B pattern IDS matched by ``text`` (deduplicated, table order). [] when clean.

    Ids only — never the matched span. This function's output goes to the ledger and
    nowhere else: no warning line, no HOLD, no seed stamp, until a dated owner decision
    graduates a pattern on the ledger's measured FP rate. Never raises.
    """
    try:
        found: List[str] = []
        for pattern, pid in _TIER_B_PATTERNS:
            if pid not in found and pattern.search(text):
                found.append(pid)
        return found
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# The Tier-B findings ledger (gitignored, append-only, telemetry-dir sibling).
# --------------------------------------------------------------------------- #

_TIER_B_LEDGER_NAME = "threat_findings.jsonl"


def tier_b_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _TIER_B_LEDGER_NAME)


def append_tier_b_findings(
    patterns: List[str], *, seam: str, name: str, memory_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> bool:
    """Append one Tier-B finding event to the dark ledger. Best-effort; never raises.

    A row is ``{"ts", "seam", "name", "patterns"}`` — the pattern IDS and the seam that
    saw them (``write`` / ``import``), never the matched text. Event-shaped on purpose:
    the graduation decision needs an FP RATE, and the honest denominator is the flow of
    real writes/imports, not repeated corpus re-sweeps double-counting the same file.
    No-ops on an empty pattern list (a clean write leaves zero trace).
    """
    if not patterns:
        return False
    try:
        from .telemetry import default_telemetry_dir

        if telemetry_dir is None:
            if memory_dir is None:
                return False
            telemetry_dir = default_telemetry_dir(memory_dir)
        os.makedirs(telemetry_dir, exist_ok=True)
        row = {"ts": time.time(), "seam": seam, "name": name, "patterns": list(patterns)}
        with open(tier_b_ledger_path(telemetry_dir), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return True
    except Exception:
        return False


def read_tier_b_summary(telemetry_dir: str) -> dict:
    """Aggregate the dark ledger for doctor's ONE line: ``{"events", "names", "patterns"}``