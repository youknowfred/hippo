"""Ungrounded-prescription lint (SEN-3) — agent-voiced attribution-of-intent, warn-only.

Capture-from-evidence is hippo's spine: a memory should transcribe what the git diff /
session decisions actually show, not SYNTHESIZE a claim about what the user "always wants."
That synthesized-prescription shape is exactly what amplifies sycophancy (the landscape
finding: memory amplifies sycophancy 25x) — a fabricated standing preference, asserted as
fact, recalled forever and reinforced every session. Nothing flagged it. This module does,
deterministically (a ``secrets.py``-style lint), and WARN-ONLY forever in this item's scope:
it never blocks a write, never routes, never ranks.

WHAT IT MATCHES (AC1 — restricted to agent-voiced attribution-of-intent, NOT bare
must/always/never): ``the {user|owner|maintainer|developer|author|team|reviewer}
[always|never|…] {wants|prefers|likes|needs|expects|insists|values|…}``. Tuned for high
precision — it excludes conditional/interrogative uses ("if the owner wants", "confirm the
user wants", "whether the team prefers") and contrastive design-rationale glosses ("the
reviewer wants the top lessons, not the corpus"), because those ASK about or EXPLAIN intent
rather than fabricate a standing preference. Verified ZERO false positives against hippo's
own plugin/memory docstrings + skill prose before defaulting on (a test pins it, and this
docstring is deliberately written so its own example — the ``the-<subject>-always-wants-X``
template — does not self-match). The only repo strings that match are the ROADMAP /
EXPLORATIONS catalog quoting the raw example as THIS item's spec text, not a memory body.

WHAT MAKES A MATCH "UNGROUNDED" (AC2): a matched prose span is grounded when either a GOV-3
``--rationale`` was supplied (or a ``Rationale:`` line already fences the WHY into the body)
OR the claim's content overlaps a fenced GRW-1 hunk in the body (the verbatim evidence).
Only a span with NEITHER is flagged — a preference asserted with no captured support and no
stated why. Prescriptions INSIDE a fenced block are never matched (a verbatim quote is
transcription, not synthesis).

SURFACES: ``write_memory`` (warn on the result — never the route/neighbors; SEN-3 stays out
of ``check_candidate`` entirely, so the confidence-never-ranking AST pin holds), the audit
skill's corpus sweep (classifies grounded / ungrounded-prescription / observation and
proposes per-item fixes, inv4), and a doctor "ungrounded-prescription fraction" line (no
persisted per-item field). The refuse-write half is DROPPED, not deferred: secrets.py's own
warn-never-block doctrine for a higher-severity/lower-FP class argues against a blocking
ambition for a fuzzier one.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# Attribution-of-intent: a person/role SUBJECT + a preference/intent VERB (optionally
# generalized by always/never/…). Preference verbs only — never the bare action verbs
# hippo's factual prose uses ("the user consents/reviews/creates/said/decides"), so an
# OBSERVATION of what the user DID never trips this.
_SUBJECT = r"(?:the\s+(?:user|owner|maintainer|developer|author|team|reviewer))"
_INTENT = (
    r"(?:always|never|really|generally|usually|typically)\s+"
    r"(?:wants?|prefers?|likes?|dislikes?|hates?|needs?|expects?|insists?|cares?\s+about|values?)"
    r"|(?:wants?|prefers?|insists?\s+on|expects?|requires?|is\s+annoyed\s+by|doesn't\s+want)"
)
_PRESCRIPTION_RE = re.compile(rf"\b{_SUBJECT}\s+(?:{_INTENT})\b", re.I)

# A preference under a conditional/interrogative lead-in is a QUESTION about intent, not a
# fabricated ASSERTION of it — the dominant false-positive shape in real prose.
_GUARD_RE = re.compile(
    r"(?:\bif\b|\bwhether\b|\bconfirm\b|\bunless\b|\bwhen\b|\bask\b|\bcheck\b|\bdoes\b"
    r"|\bdo\b|\bwould\b|\bshould\b|\bwhat\b|\bwhich\b)\s+\S*\s*$",
    re.I,
)
# A contrastive design-rationale gloss ("wants X, not Y") explains a tradeoff, not a standing
# user preference.
_CONTRAST_RE = re.compile(r"\s+\S+.{0,40}?,\s*not\b")

_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are be it this that each such any all "
    "them they he she we you i".split()
)


def _fenced_re():
    """COR-20's fenced-block parser — the ONE notion of 'fenced' the link lint already uses."""
    from .links import _FENCED_CODE_RE

    return _FENCED_CODE_RE


def _strip_fenced(text: str) -> str:
    """The PROSE half of ``text`` — fenced blocks blanked. A prescription inside a fenced
    block is a verbatim quote (transcription), never synthesis, so it is not prose to flag."""
    return _fenced_re().sub("\n", text or "")


def _fenced_text(text: str) -> str:
    """The concatenated CONTENT of every fenced block — the evidence a claim can be grounded in."""
    parts: List[str] = []
    for m in _fenced_re().finditer(text or ""):
        lines = m.group(0).split("\n")
        parts.append("\n".join(lines[1:-1]))
    return "\n".join(parts)


def prescriptive_spans(text: str) -> List[str]:
    """Agent-voiced attribution-of-intent spans in ``text`` PROSE (fenced blocks excluded).

    Order-stable, deduplicated. Conditional/interrogative and contrastive-gloss matches are
    excluded (high precision — see the module header). Never raises.
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        prose = _strip_fenced(text)
        out: List[str] = []
        seen: set = set()
        for m in _PRESCRIPTION_RE.finditer(prose):
            pre = prose[max(0, m.start() - 40): m.start()]
            if _GUARD_RE.search(pre):
                continue
            if _CONTRAST_RE.match(prose[m.end(): m.end() + 60]):
                continue
            key = m.group(0).lower()
            if key not in seen:
                seen.add(key)
                out.append(m.group(0))
        return out
    except Exception:
        return []


def _sentence_around(text: str, span: str) -> str:
    """The sentence containing the first occurrence of ``span`` (for content-overlap grounding)."""
    idx = text.lower().find(span.lower())
    if idx < 0:
        return span
    start = max(text.rfind(".", 0, idx), text.rfind("\n", 0, idx)) + 1
    end_dot = text.find(".", idx)
    end_nl = text.find("\n", idx)
    ends = [e for e in (end_dot, end_nl) if e != -1]
    end = min(ends) if ends else len(text)
    return text[start:end]


def _grounded_by_evidence(span: str, prose: str, fenced: str) -> bool:
    """Does the claim's content overlap the fenced evidence? (the write-time-hunk grounding)."""
    if not fenced.strip():
        return False
    sentence = _sentence_around(prose, span)
    fenced_low = fenced.lower()
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", sentence):
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        # skip the attribution scaffolding itself — overlap must be on the CLAIM's content
        if low in ("user", "owner", "maintainer", "developer", "author", "team", "reviewer",
                   "wants", "want", "prefers", "prefer", "always", "never", "needs", "need",
                   "expects", "expect", "likes", "like", "values", "value", "insists", "insist"):
            continue
        if low in fenced_low:
            return True
    return False


def _has_rationale_line(body: str) -> bool:
    """A GOV-3 ``Rationale:`` fence in a committed body grounds the WHY (the audit path)."""
    return bool(re.search(r"(?mi)^\s*Rationale:\s*\S", body or ""))


def find_ungrounded(body: str, *, rationale: Optional[str] = None) -> List[str]:
    """Prescriptive spans in ``body`` that are grounded in NEITHER a rationale NOR a hunk.

    AC2: flagged only when the span has no fenced-evidence overlap AND no ``--rationale``
    (and no committed ``Rationale:`` line). Warn-only; the caller reports, never rejects.
    Never raises.
    """
    try:
        spans = prescriptive_spans(body)
        if not spans:
            return []
        if rationale and str(rationale).strip():
            return []
        if _has_rationale_line(body):
            return []
        prose = _strip_fenced(body)
        fenced = _fenced_text(body)
        return [s for s in spans if not _grounded_by_evidence(s, prose, fenced)]
    except Exception:
        return []


def classify(body: str, *, rationale: Optional[str] = None) -> str:
    """``"observation"`` | ``"grounded"`` | ``"ungrounded-prescription"`` for one memory body.

    - ``observation``: no attribution-of-intent span at all (the transcription norm).
    - ``grounded``: has a span, all grounded (rationale / hunk).
    - ``ungrounded-prescription``: has at least one span grounded in nothing.
    """
    spans = prescriptive_spans(body)
    if not spans:
        return "observation"
    return "ungrounded-prescription" if find_ungrounded(body, rationale=rationale) else "grounded"


def scan_corpus(memory_dir: str) -> dict:
    """Corpus classification for the audit sweep (AC3) — counts + the ungrounded per-item list.

    ``{"total", "observation", "grounded", "ungrounded", "ungrounded_items": [{name, spans}]}``.
    Proposes nothing and writes nothing — the audit skill walks ``ungrounded_items`` per item
    (inv4). Grounding for a committed memory is read from the body itself (a ``Rationale:``
    line or a fenced hunk overlapping the claim). Never raises.
    """
    out = {"total": 0, "observation": 0, "grounded": 0, "ungrounded": 0, "ungrounded_items": []}
    try:
        from .provenance import _iter_memory_files, split_frontmatter

        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            _, body = split_frontmatter(text)
            out["total"] += 1
            kind = classify(body)
            if kind == "observation":
                out["observation"] += 1
            elif kind == "grounded":
                out["grounded"] += 1
            else:
                out["ungrounded"] += 1
                import os

                stem = os.path.splitext(os.path.basename(path))[0]
                out["ungrounded_items"].append({"name": stem, "spans": find_ungrounded(body)})
    except Exception:
        return out
    return out
