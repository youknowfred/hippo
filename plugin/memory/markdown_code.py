"""Code-span / fenced-block masking — the one masker every text lint shares.

A markdown lint that reads memory bodies has to answer the same question twice: is this
occurrence the THING, or documentation ABOUT the thing? Backticks are how a memory says
"about" — and a bare regex cannot hear it. That gap has now produced the same defect in
two different lints, which is why the masker lives here instead of inside either one.
Six call sites across the package share these two patterns (the link lint, the threat
lint, the write ticket, the evidence-fence extractor, the prescription lint, doctor's
fence check) — "what counts as code" must not mean two things.

  · COR-20 (links.py) — ``parse_wikilinks`` minted phantom edges from prose. FOUR of six
    dangling targets in this repo's own corpus were sentences ABOUT the convention:
    ``[[child]]``, ``[[wikilink]]``, ``[[wikilinks]]`` — reported as broken references
    forever, with nothing to fix.
  · COR-21 (threat_lint.py) — the Tier-A HTML-comment class flagged
    ``(`<!-- hippo:agents-export:begin/end -->`)``, a memory documenting hippo's OWN
    AGENTS.md block markers, and turned the memory-review CI lane red on PR #104. That
    class exists because a comment is HIDDEN from a human reading rendered markdown while
    still reaching the model verbatim (SEN-2's ED-3 finding). Inside a code span the
    asymmetry is gone: the renderer prints the comment as literal visible text, so the
    human sees exactly what the model sees, and nothing is hidden.

The trap both defects share, worth stating once where the fix lives: BACKTICKING DOES NOT
HELP A BARE REGEX. The author does the obvious thing, the diff reads as fixed, and the
lint reports exactly what it did before — so the masking has to happen before the pattern
ever sees the text.

WHY MASKING IS NOT A BYPASS, and the one rule that keeps it that way: masking is safe
only because it MIRRORS THE RENDERER — to hide a payload from a masked lint you must make
it visibly rendered, which defeats hiding it. Wherever the mask is LOOSER than CommonMark
that guarantee inverts and the masker becomes the attack surface. Found live probing
COR-21 before it shipped: the original span pattern was ``re.S`` with no blank-line
guard, so a lone backtick either side of a blank-line-separated ``<!-- … -->`` ate the
comment — while CommonMark, which cannot form a span across a paragraph break, rendered
two literal backticks and kept the comment hidden. Hence the ``\\n[ \\t]*\\n`` guard below.
Every remaining divergence is deliberately in the CONSERVATIVE direction (an unclosed
fence, an indented fence: not masked, so the lint still sees them).

Fences are stripped BEFORE spans so a ``` block containing a stray backtick cannot desync
the span pass.
"""

from __future__ import annotations

import re

# A closed fenced block at column 0. An UNCLOSED fence is deliberately not matched (a
# lint keeps seeing the tail of the document), as is an indented fence — CommonMark
# allows up to three leading spaces, we allow none. Both err toward masking less.
FENCED_CODE_RE = re.compile(r"^(?P<fence>```+|~~~+)[^\n]*\n.*?^(?P=fence)[^\n]*$", re.M | re.S)
# An inline span: a run of N backticks closed by another run of N. It may cross a single
# newline (CommonMark folds it to a space, and memories do wrap long backticked markers
# mid-sentence) but NEVER a blank line — see the bypass in the module docstring.
INLINE_CODE_RE = re.compile(r"(?P<ticks>`+)(?:(?!(?P=ticks))(?!\n[ \t]*\n).)+?(?P=ticks)", re.S)


def strip_code(text: str) -> str:
    """Blank out fenced blocks + inline code spans. Never raises.

    Blanks rather than deletes (a fence becomes ``\\n``, a span a space) so surrounding
    prose keeps its line and word boundaries — a lint that counts or locates hits reads
    the same text it would have, minus the code.
    """
    try:
        return INLINE_CODE_RE.sub(" ", FENCED_CODE_RE.sub("\n", text or ""))
    except Exception:
        return text or ""
