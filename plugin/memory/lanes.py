"""HOT-7: recall lanes by kind — behavior, fact, journal.

Each memory gets a lane from data the corpus already declares:

  - ``journal`` — its filename matches a ``fold_digests`` glob or a ``journals`` glob in
    ``hippo.json`` (journal wins over type);
  - ``behavior`` — frontmatter ``type`` is ``user`` or ``feedback``;
  - ``fact`` — everything else (``project``, ``reference``, unknown or missing).

``build_index`` stores the lane on every manifest entry, for every corpus, and records at
the manifest top level what it was computed from (``lanes: {declared, globs}``). The lanes
ACT only in the prompt hook, and only for a corpus whose ``hippo.json`` declares
``journals`` as a list (``[]`` included):

  - a journal is admitted only when the prompt names its id, or (GP-S4) when it cites a
    tracker id the prompt names and the corpus declares a ledger (``LaneRequest``); a withheld
    journal takes no pool slot, seeds no expansion and cannot enter by one;
  - the admitted rows are then picked by lane, behavior first (``select``), and the
    prompt's char budget follows the same shares (``fit_lanes``).

Explicit recall (``hippo recall``, the MCP tool, the eval bench) never passes a
``LaneRequest`` and is unchanged on every corpus, declared or not. ``journals`` is read
directly (no ``hippo trust policy`` consent): it declares data and can only withhold or
reorder memories this machine already consented to, the same class as ``mute``.

Stdlib only at import time: the hook imports this module on every prompt.
"""

from __future__ import annotations

import fnmatch
import math
import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

BEHAVIOR = "behavior"
FACT = "fact"
JOURNAL = "journal"
RULE = "rule"  # a rules-plane pointer row (recall_tiers._RULES_SOURCE); never a memory lane
LANES = (BEHAVIOR, FACT, JOURNAL)
_BEHAVIOR_TYPES = frozenset({"user", "feedback"})

# The behavior lane's share of the hook's k slots and of the prompt's char budget.
# override: HIPPO_LANE_BEHAVIOR_SHARE (a fraction in [0, 1]). Measured on the three corpora
# (HOT-7's replay, see the commit body): 0.3 left emgl's behavior share under twice its
# corpus share; 0.4 and 0.5 cleared it but cost the hook-mode hard set 0.029; no value moved
# Skyline, whose behavior memories all sit in its floor. None met both, so 0.4, as ruled.
_BEHAVIOR_SHARE = 0.4
# Prompt-named journals admitted per prompt before the rest of the slots go to facts.
_JOURNAL_SLOTS = 2


def behavior_share() -> float:
    """``HIPPO_LANE_BEHAVIOR_SHARE`` override, clamped to [0, 1]; junk -> the default."""
    raw = os.environ.get("HIPPO_LANE_BEHAVIOR_SHARE")
    if raw is None or not raw.strip():
        return _BEHAVIOR_SHARE
    try:
        val = float(raw)
    except ValueError:
        return _BEHAVIOR_SHARE
    if val != val:  # NaN
        return _BEHAVIOR_SHARE
    return min(1.0, max(0.0, val))


def behavior_slots(k: int, share: float) -> int:
    return max(0, min(int(k), int(math.ceil(share * int(k) - 1e-9))))


# --------------------------------------------------------------------------- #
# The declaration (one reader, shared with the commit gate)
# --------------------------------------------------------------------------- #
def journal_globs(policy_dir: str) -> List[str]:
    """Every journal filename glob the corpus declares: ``fold_digests`` then the
    ``journals`` list, basenames only, order kept minus duplicates. The ONE reader — the
    commit gate's journal budget (``gate_cli``) and the lanes both call it."""
    from .provenance_format import read_fold_digests, read_policy_key

    globs = list(read_fold_digests(policy_dir))
    raw = read_policy_key(policy_dir, "journals")
    for g in raw if isinstance(raw, list) else []:
        if isinstance(g, str) and g.strip() and "/" not in g and g.strip() not in globs:
            globs.append(g.strip())
    return globs


def journals_declared(policy_dir: str) -> bool:
    """True when the corpus declares ``journals`` as a list (an empty list counts)."""
    from .provenance_format import read_policy_key

    try:
        return isinstance(read_policy_key(policy_dir, "journals"), list)
    except Exception:
        return False


def lane_state(memory_dir: str) -> dict:
    """The manifest's top-level ``lanes`` record: what the entries' lanes were computed
    from. Never raises (a failure reads as undeclared with no globs)."""
    try:
        return {"declared": journals_declared(memory_dir), "globs": journal_globs(memory_dir)}
    except Exception:
        return {"declared": False, "globs": []}


def lane_of(filename: str, kind, globs: Sequence[str]) -> str:
    """The lane of one memory file. Journal (a declared glob) wins over ``type``."""
    if filename and any(fnmatch.fnmatch(filename, g) for g in globs or ()):
        return JOURNAL
    if isinstance(kind, str) and kind.strip().lower() in _BEHAVIOR_TYPES:
        return BEHAVIOR
    return FACT


def relabel(entry: dict, globs: Sequence[str]) -> str:
    """The lane of an entry synthesized at fuse time (the outbox) under the PROJECT
    corpus's globs: its own build saw no declaration, so only the journal test can change."""
    if lane_of(entry.get("file") or "", None, globs) == JOURNAL:
        return JOURNAL
    lane = entry.get("lane")
    return lane if lane in LANES else FACT


def active(manifest: Optional[dict]) -> bool:
    """True when the loaded manifest says the corpus declared ``journals``."""
    rec = (manifest or {}).get("lanes")
    return isinstance(rec, dict) and rec.get("declared") is True


def row_lane(row: dict) -> str:
    """A result row's lane, defaulting the way the hook counts it."""
    if row.get("corpus") == RULE:
        return RULE
    lane = row.get("lane")
    return lane if lane in LANES else FACT


# --------------------------------------------------------------------------- #
# Journal admission in the hook
# --------------------------------------------------------------------------- #
_ID_TOKEN = re.compile(r"[\w.\-]+", re.UNICODE)


def named_ids(text: str) -> Set[str]:
    """The lowercased whole tokens of ``text`` that could be a memory id: one pass, no
    per-journal regex. ``[[stem]]`` (brackets separate), ``stem.md`` and a sentence-final
    ``stem.`` all yield ``stem``; a stem inside a longer slug yields only the longer slug."""
    out: Set[str] = set()
    for tok in _ID_TOKEN.findall((text or "").lower()):
        tok = tok.strip(".-")
        if tok.endswith(".md"):
            tok = tok[:-3]
        if tok:
            out.add(tok)
    return out


def issue_join_admissions(prompt: str, join=None) -> Dict[str, str]:
    """GP-S4: the journals an issue join admits for this prompt, as ``{lowercased journal
    name: the issue id that admitted it}`` — the journals that cite a tracker id the prompt
    names (the second of V2Q-14's three admissions; the prompt naming the journal and
    explicit recall are the other two).

    ``join`` is the loaded ledger cache (``ledger.load``), whose reverse map ``id ->
    journal names`` was built off the hot path with the cache; None (no ledger declared)
    admits nothing at no cost. Otherwise O(ids the prompt names): the declared id patterns
    scan the prompt's first chars and each id is one lookup. The hook never scans the
    corpus. Keyed on names, so a digest section slug can be admitted the same way."""
    if join is None or not prompt:
        return {}
    try:
        return join.journals_for(prompt)
    except Exception:
        return {}


class LaneRequest:
    """The hook's lane context for one prompt, passed to ``recall(lanes=...)``.

    ``recall`` calls ``start`` once with the loaded manifest (the extra over-fetch it
    returns makes the behavior share reachable) and ``withhold`` on its candidate lists;
    the hook then reads ``active`` to decide whether to select by lane. Nothing here reads
    a file: the declaration comes off the manifest recall already loaded."""

    def __init__(
        self, prompt: str, k: int, share: Optional[float] = None, floor: Iterable[str] = (),
        join=None,
    ) -> None:
        self.prompt = prompt or ""
        self.k = int(k)
        # A journal the MEMORY.md floor already names is in context whatever recall does:
        # it passes, and the hook collapses it to its name on the floor line (no slot).
        self.floor = frozenset(floor or ())
        self.share = behavior_share() if share is None else share
        self.active = False
        self.withheld: Set[str] = set()
        self.admitted: Set[str] = set()
        # GP-S4: why each admitted journal passed — "named" (the prompt names it) or
        # "issue" (it cites an issue id the prompt names). The hook copies it onto the row,
        # so the recall event and the rollup count each admission by cause.
        self.admitted_by: Dict[str, str] = {}
        self.join = join  # the ledger cache (ledger.load) or None
        self._named: Optional[Set[str]] = None
        self._issue: Optional[Dict[str, str]] = None

    def start(self, manifest: Optional[dict]) -> int:
        self.active = active(manifest)
        return behavior_slots(self.k, self.share) if self.active else 0

    def named(self) -> Set[str]:
        if self._named is None:
            self._named = named_ids(self.prompt)
        return self._named

    def issue(self) -> Dict[str, str]:
        if self._issue is None:
            self._issue = issue_join_admissions(self.prompt, self.join)
        return self._issue

    def _admit(self, name: str) -> Optional[str]:
        low = name.lower()
        if low in self.named():
            return "named"
        if low in self.issue():
            return "issue"
        return None

    def withhold(self, rows: List[tuple], entries: List[dict], record=None) -> List[tuple]:
        """``rows`` (tuples whose first item is an entry index) minus every journal the
        prompt did not name. Recorded once per memory as ``journal_lane``."""
        if not self.active:
            return rows
        kept: List[tuple] = []
        for row in rows:
            e = entries[row[0]]
            if e.get("lane") != JOURNAL:
                kept.append(row)
                continue
            name = e.get("name") or ""
            if name in self.floor:
                kept.append(row)
                continue
            why = self._admit(name)
            if why:
                self.admitted.add(name)
                self.admitted_by[name] = why
                kept.append(row)
                continue
            if name not in self.withheld:
                self.withheld.add(name)
                if record is not None:
                    record(name, "journal_lane", row[1])
        return kept


# --------------------------------------------------------------------------- #
# Lane shares: slots, then chars
# --------------------------------------------------------------------------- #
def select(rows: List[dict], k: int, share: float) -> Tuple[List[dict], List[dict]]:
    """Pick ``k`` of ``rows`` (non-collapsed, in recall's order) by lane: behavior up to
    ``behavior_slots(k, share)``, journals up to ``_JOURNAL_SLOTS``, facts the rest, and any
    slot a lane leaves unused goes to the next rows in recall order whatever their lane.
    Returns ``(picked, cut)``: picked is behavior rows first, then the rest, each in recall
    order; cut is every row not picked, in recall order. Pure."""
    k = max(0, int(k))
    caps = {BEHAVIOR: behavior_slots(k, share), JOURNAL: _JOURNAL_SLOTS}
    by: Dict[str, List[int]] = {BEHAVIOR: [], JOURNAL: [], FACT: []}
    for pos, r in enumerate(rows):
        by.setdefault(row_lane(r), []).append(pos)
    take: Set[int] = set()
    for lane in (BEHAVIOR, JOURNAL):
        take.update(by[lane][: caps[lane]])
    rest = [p for lane, ps in by.items() if lane not in caps for p in ps]
    take.update(sorted(rest)[: max(0, k - len(take))])
    if len(take) < k:  # unused slots flow on, in recall order
        spare = [p for p in range(len(rows)) if p not in take]
        take.update(spare[: k - len(take)])
    picked = [rows[p] for p in sorted(take) if row_lane(rows[p]) == BEHAVIOR]
    picked += [rows[p] for p in sorted(take) if row_lane(rows[p]) != BEHAVIOR]
    cut = [rows[p] for p in range(len(rows)) if p not in take]
    return picked, cut


def lane_refs(full: List[dict]) -> Dict[int, Tuple[dict, Optional[float]]]:
    """``{id(row): (first row of its lane, that lane's top score)}`` for the render knee,
    judged against the top row OF THE SAME LANE."""
    firsts: Dict[str, dict] = {}
    out: Dict[int, Tuple[dict, Optional[float]]] = {}
    for r in full:
        first = firsts.setdefault(row_lane(r), r)
        out[id(r)] = (first, first.get("score"))
    return out


def fit_lanes(
    head: List[str],
    blocks: Sequence[Tuple[str, List[str], bool]],
    behavior: Sequence[bool],
    tail: List[str],
    max_chars: int,
    share: float,
) -> Tuple[str, int]:
    """``recall_budget.fit`` with the behavior lane capped at ``share`` of ``max_chars``
    while other rows wait (unused chars flow on). Each group closes on its own first miss,
    so rank order is kept inside a lane; every row that does not fit is named on the one
    overflow line. Returns ``(text, rows_rendered)``."""
    from .recall_budget import overflow_line

    def _size(lines: List[str]) -> int:
        return len("\n".join(lines))

    kept: List[str] = list(head)
    overflow: List[str] = []
    rendered = 0
    closed = {True: False, False: False}
    others = any(not b for b in behavior)
    cap = int(share * max_chars) if others else max_chars
    used = 0
    names = [b[0] for b in blocks]
    for pos, (name, lines, force) in enumerate(blocks):
        g = bool(behavior[pos])
        if closed[g] or force:
            overflow.append(name)
            continue
        cost = _size(lines) + 1
        trial = kept + lines
        later = overflow + names[pos + 1 :]
        reserve = tail + ([overflow_line(later)] if later else [])
        if (not g or used + cost <= cap) and _size(trial + reserve) <= max_chars:
            kept = trial
            rendered += 1
            used += cost if g else 0
        else:
            overflow.append(name)
            closed[g] = True
    out_lines = kept + ([overflow_line(overflow)] if overflow else []) + tail
    out = "\n".join(out_lines)
    if len(out) > max_chars:
        out = out[: max_chars - 16].rstrip() + "\n…(truncated)"
    return out, rendered


# --------------------------------------------------------------------------- #
# Telemetry (K14's instrument)
# --------------------------------------------------------------------------- #
def count_by_lane(rows: Iterable[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        if r.get("name"):
            lane = row_lane(r)
            out[lane] = out.get(lane, 0) + 1
    return out


def rendered_names(block: str) -> Set[str]:
    """Names of the rows ``block`` rendered as full pointer lines (``  • <name>...``)."""
    out: Set[str] = set()
    for line in (block or "").splitlines():
        if line.startswith("  • "):
            name = line[4:].split(" ", 1)[0]
            if name:
                out.add(name)
    return out
