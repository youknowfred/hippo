"""HOT-3: per-prompt and per-session context budgets for the recall hook.

The hook used to render up to k full pointer lines under a 9,000-char ceiling and cut the
text mid-line when it overflowed. On em-growth-labs that came to ~4,000 chars per prompt
and ~27,500 per session, most of it never read. Now:

  - each prompt gets ``_PROMPT_BUDGET_CHARS``. Rows are added whole, in rank order, while
    they fit; the rest collapse into one line that still names them (collapse, never drop),
    and a trailing row scoring under ``_RENDER_KNEE`` of the top score joins that line too;
  - each session gets ``_SESSION_BUDGET_CHARS``. Once this session's earlier prompts have
    injected that much, a prompt gets ``_SPENT_PROMPT_CHARS``, and every memory already
    shown this session collapses (not just the last few turns' — RCL-2's window), so a
    long session stops paying for the same pointers again.

The session total is read from the episode buffer this hook already scans (each episode
row records the chars its prompt injected). Rendering stays in ``recall.format_results``;
this module only holds the numbers and the row fitter.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

_PROMPT_BUDGET_CHARS = 2500
_SESSION_BUDGET_CHARS = 12000
# After the session budget is spent: room for the header and about two compact rows.
_SPENT_PROMPT_CHARS = 800
# A row whose fused score is under this fraction of the top row's renders as a name only.
_RENDER_KNEE = 0.4
# Names listed on the overflow line before "+N more".
_OVERFLOW_NAMES = 5


def session_spent(session_episodes: Iterable[dict]) -> int:
    """Chars this session's earlier prompts injected (episode rows' ``injected_chars``)."""
    total = 0
    for ep in session_episodes or ():
        try:
            total += int(ep.get("injected_chars") or 0)
        except (TypeError, ValueError):
            continue
    return total


def over_session_budget(spent: int) -> bool:
    return spent >= _SESSION_BUDGET_CHARS


def prompt_budget(spent: int) -> int:
    """This prompt's char budget given what the session already spent."""
    return _SPENT_PROMPT_CHARS if over_session_budget(spent) else _PROMPT_BUDGET_CHARS


def below_knee(score: Optional[float], top: Optional[float]) -> bool:
    """True for a row scoring under the render knee relative to the top row."""
    if not isinstance(score, (int, float)) or not isinstance(top, (int, float)) or top <= 0:
        return False
    return score < _RENDER_KNEE * top


def overflow_line(names: Sequence[str]) -> str:
    shown = ", ".join(names[:_OVERFLOW_NAMES])
    more = len(names) - _OVERFLOW_NAMES
    tail = f" (+{more} more)" if more > 0 else ""
    return f"  ⤷ {len(names)} more past this prompt's context budget: {shown}{tail}"


def fit(
    head: List[str],
    blocks: Sequence[Tuple[str, List[str], bool]],
    tail: List[str],
    max_chars: int,
) -> Tuple[str, int]:
    """Assemble ``head`` + as many whole ``blocks`` as fit + an overflow line + ``tail``.

    ``blocks`` are ``(name, lines, force_overflow)`` in rank order; a block that does not
    fit (or is flagged ``force_overflow``, the render knee) goes to the overflow line by
    name, and so does every block after the first one that does not fit, so rank order is
    kept. Returns ``(text, rows_rendered)``. The text never exceeds ``max_chars`` unless the
    head and tail alone do; then it is cut, as the old renderer did.
    """
    kept: List[str] = list(head)
    overflow: List[str] = []
    rendered = 0
    closed = False

    def _size(lines: List[str]) -> int:
        return len("\n".join(lines))

    names = [b[0] for b in blocks]
    for pos, (name, lines, force) in enumerate(blocks):
        if closed or force:
            overflow.append(name)
            continue
        trial = kept + lines
        # Room for the overflow line every later row could still end up on.
        later = overflow + names[pos + 1 :]
        reserve = tail + ([overflow_line(later)] if later else [])
        if _size(trial + reserve) <= max_chars:
            kept = trial
            rendered += 1
        else:
            overflow.append(name)
            closed = True
    out_lines = kept + ([overflow_line(overflow)] if overflow else []) + tail
    out = "\n".join(out_lines)
    if len(out) > max_chars:
        out = out[: max_chars - 16].rstrip() + "\n…(truncated)"
    return out, rendered
