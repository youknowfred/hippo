"""Secret-pattern lint for memory text (SEC-2 — warn at write time + corpus scan in doctor).

Memories are committed to git and recalled forever; a credential pasted into a body — an
AWS key from a stack trace, a GitHub token from a curl example, a private key block — is
committed to shared history and re-injected into context on every recall. Nothing scanned
for that at creation, and no corpus-wide check existed. This module is the ONE detector both
surfaces share: ``new_memory`` runs it over the rendered text at write time (warn, never
block — see below), and ``/hippo:doctor`` runs it over every memory file for a corpus-wide
sweep. One pattern list, one code path — never two drifting copies of the regex set.

Design constraints (all load-bearing):
  - WARN, NEVER BLOCK. A write-time secret match is agent-gated, not a refusal: the write
    ALWAYS proceeds if otherwise valid, and the match is surfaced as a ``warnings`` entry so
    the calling skill/agent decides what to do (report-then-act). Silently refusing a write
    would be a legibility failure (the invariant: no silent no-ops), and blocking on a
    false positive would be worse than missing an exotic secret — see next point.
  - HIGH PRECISION over recall. False positives on ordinary prose (a memory ABOUT secrets,
    a hex sha, a base64 snippet) are worse than missing an unusual secret format, because a
    noisy warning trains the agent to ignore it. The pattern list is deliberately SMALL and
    anchored to high-signal shapes: known token PREFIXES (AKIA…, ghp_/gho_/…) and PEM
    ``BEGIN … PRIVATE KEY`` blocks. The optional entropy catch-all is conservative (long,
    mixed-class, no whitespace) so normal words/hashes don't trip it.
  - NEVER ECHO THE SECRET. A warning names the KIND of match ("possible AWS access key
    detected"), never the matched substring — the warning text itself lands in logs / the
    agent transcript, and re-emitting the credential there would defeat the purpose.
  - No new dependency. Pure ``re`` + a tiny hand-rolled entropy heuristic.

Remediation, appended once to any non-empty finding set: if it's a real secret, remove it,
rotate the credential, and scrub it from git history before committing — pointing at SEC-4's
full purge procedure (``plugin/memory/README.md``, "Purging a memory") for the exact recipe.
"""

from __future__ import annotations

import math
import os
import re
from typing import List

# Remediation pointer appended to any non-empty warning set. SEC-4: names the purge doc so the
# same pointer reaches BOTH surfaces that interpolate this constant — write-time new_memory
# warnings (scan_with_remediation) and doctor's check_secrets — with one source of truth.
REMEDIATION = (
    "if this is a real secret, remove it, rotate the credential, and scrub it from git "
    "history before committing — see plugin/memory/README.md ('Purging a memory') for the "
    "full purge procedure"
)

# High-signal, anchored patterns → the human-readable KIND reported (never the match itself).
# SMALL by design: each shape is one a real credential has and ordinary prose does not.
_PATTERNS = [
    # AWS access key IDs: the AKIA prefix + exactly 20 uppercase-alnum chars.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "possible AWS access key detected"),
    # GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ + >=36 token chars.
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "possible GitHub token detected"),
    # PEM private-key blocks (RSA/EC/OPENSSH/PGP/plain).
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), "possible private key block detected"),
]

# Entropy catch-all thresholds. Conservative so normal prose / hex shas / short base64 pass:
# a token must be long AND draw from a mixed character class AND clear a Shannon-entropy bar.
_ENTROPY_MIN_LEN = 32
_ENTROPY_MIN_BITS = 4.0
_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{%d,}" % _ENTROPY_MIN_LEN)
_ENTROPY_MSG = "possible high-entropy secret detected"


def _shannon_entropy(s: str) -> float:
    """Shannon entropy (bits/char) of ``s``. 0.0 for empty. Never raises."""
    if not s:
        return 0.0
    counts: dict = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _has_mixed_classes(s: str) -> bool:
    """True when ``s`` mixes at least two of {lower, upper, digit} — a real-token shape.

    A long all-lowercase word (or an all-hex sha, which is single-class-ish) should NOT trip
    the entropy catch-all; requiring class mixing keeps the heuristic off ordinary text.
    """
    classes = 0
    if any(c.islower() for c in s):
        classes += 1
    if any(c.isupper() for c in s):
        classes += 1
    if any(c.isdigit() for c in s):
        classes += 1
    return classes >= 2


def scan_text(text: str) -> List[str]:
    """Return human-readable warnings for secret-looking patterns in ``text``. [] when clean.

    Each entry names the KIND of match, never the matched substring (the warning text is
    logged / shown to the agent — echoing the credential there would leak it). Deduplicated
    and order-stable. Never raises: a scan failure returns [] rather than blocking a write.
    This is the SINGLE detector — ``new_memory`` (write-time warn) and doctor (corpus sweep)
    both call it, so the pattern set never forks into two drifting copies.
    """
    try:
        found: List[str] = []
        for pattern, message in _PATTERNS:
            if pattern.search(text) and message not in found:
                found.append(message)
        # Entropy catch-all: only flag a long, mixed-class, high-entropy token — and only if
        # a prefix pattern above didn't already flag this text (avoid a redundant second line).
        if _ENTROPY_MSG not in found:
            for token in _ENTROPY_TOKEN_RE.findall(text):
                if _has_mixed_classes(token) and _shannon_entropy(token) >= _ENTROPY_MIN_BITS:
                    found.append(_ENTROPY_MSG)
                    break
        return found
    except Exception:
        return []


def scan_with_remediation(text: str) -> List[str]:
    """``scan_text`` plus a trailing generic remediation line when anything matched. [] if clean.

    The remediation is appended ONCE (not per match) so the caller can hand the whole list to
    the agent as the ``warnings`` field. Empty in, empty out — a clean scan adds no noise.
    """
    warnings = scan_text(text)
    if warnings:
        warnings.append(REMEDIATION)
    return warnings


def scan_corpus(memory_dir: str) -> List[dict]:
    """Corpus-wide sweep: one ``{"file", "warnings"}`` entry per memory file with a match.

    Applies the SAME detector to every memory file's full text (frontmatter + body). Backs
    doctor's corpus-scan check. Files that match get a ``warnings`` list (the KIND lines only,
    no remediation — doctor prints the pointer once for the whole run). Clean files are
    omitted, so ``[]`` means the corpus is clean. Never raises; an unreadable file is skipped.
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
