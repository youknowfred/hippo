"""RUL-8: the text leg of the rule↔memory conflict radar.

``rules_plane.conflict_radar`` compares edges and recall strength, never text: a memory
that tells the agent to ask before an act a CLAUDE.md section now tells it to run (or the
reverse) is a live conflict that radar reports as none. This module adds a deterministic
text leg (no model, no index, no network):

  - a CANDIDATE is a (behavior memory, governance section) pair holding one sentence each
    that share most of their content words (``build_index.tokenize`` + ``bm25_terms``, the
    corpus's own stemmer; the directive cue words themselves are not content), and
  - whose DIRECTIVES differ on one axis of ``DIRECTIVES`` (the table below, data so the
    list can grow): ask first / just run, always / never, the order of two shared steps,
    and whose act it is (the agent's / the user's).

Polarity is read per clause, and only from clauses that carry a shared content word, so a
directive about something else in the same sentence does not count. Negated forms are
listed as cues of the OTHER pole and matched first ("never ask" and "without asking" are
the run pole, not the ask pole); a clause holding both poles of an axis is ambiguous and
casts no vote. A sentence pair conflicts only when each side votes one pole and the poles
differ.

Only behavior-lane memories (``type: user`` / ``feedback``) are read: ``project`` and
``reference`` memories describe state, not directives. Each candidate becomes ONE tend
``contradiction`` item (``<memory>|rule:<file>#<section>``) naming both sides with the two
sentences that disagree; it takes that kind's verdicts (``apply_verdict``), and nothing
here picks a winner. The opt-in LLM contradiction check may CONFIRM a candidate
(``confirm_candidates``, run by the dream pass beside its memory-pair check); its verdict is
recorded in the same derived ledger and only ever adds "confirmed" to the evidence. A
"no conflict" answer is recorded and changes nothing.

Runs where the rules radar runs (doctor, sleep through doctor, the tend queue's source);
never on UserPromptSubmit. Read-only over every file it reads. Never raises.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set, Tuple

# --------------------------------------------------------------------------- #
# The directive table: (axis, pole, pattern), matched in order over a lowercased clause.
# Each match is masked before later patterns run, so a negated form listed first claims
# its words ("never ask" never also counts as "ask" or as "never").
# --------------------------------------------------------------------------- #
DIRECTIVES: Tuple[Tuple[str, str, str], ...] = (
    # ask first / just run — negated asks first
    ("ask_run", "run", r"\bwithout (?:first )?(?:asking|waiting|confirming|checking)\b"),
    ("ask_run", "run", r"\b(?:never|don'?t|do not|no need to|need not) (?:ask|wait for|confirm|check with)\b"),
    ("ask_run", "run", r"\b(?:just|simply) (?:do|run) it\b"),
    ("ask_run", "run", r"\b(?:proceed|autonomously|automatically|unprompted)\b"),
    ("ask_run", "run", r"(?:^|[,:]\s*|\byou\s+|\bthen\s+)(?:run|execute)\b"),
    ("ask_run", "ask", r"\bask(?:s|ed|ing)?\b"),
    ("ask_run", "ask", r"\bconfirm(?:s|ed|ation)?\b"),
    ("ask_run", "ask", r"\b(?:wait for|check with)\b"),
    ("ask_run", "ask", r"\b(?:approval|permission|sign-?off|go-ahead)\b"),
    ("ask_run", "ask", r"\b\w+'s (?:word|yes|ok|okay|approval)\b"),
    ("ask_run", "ask", r"-gated\b"),
    # whose act: the agent's / the user's — the agent's possessives first, so the generic
    # "<someone>'s call" below never claims "the session's call"
    ("whose", "agent", r"\bnot (?:the |an? )?(?:user|owner|operator|human)'s\b"),
    ("whose", "user", r"\bnot (?:the |an? )?(?:session|agent|claude|assistant)'s\b"),
    ("whose", "agent", r"\b(?:session|agent|claude|assistant)'s (?:to \w+|call|job|act)\b"),
    ("whose", "agent", r"\b(?:yourself|on your own)\b"),
    ("whose", "user", r"\b(?:user|owner|operator|human)'s (?:to \w+|call|job|act|alone)\b"),
    ("whose", "user", r"\bfor (?:the )?(?:user|owner|operator|human) to\b"),
    ("whose", "user", r"\bhand (?:it |them |this )?(?:to )?(?:the )?(?:user|owner|operator)\b"),
    ("whose", "user", r"\b\w+'s (?:alone|call|act|to run|to take|to make|decision)\b"),
    # always / never
    ("always_never", "never", r"\b(?:never|must not|mustn'?t|do not|don'?t|should not|shouldn'?t|no longer|avoid\w*)\b"),
    ("always_never", "always", r"\b(?:always|must|every time)\b"),
)

AXIS_LABELS: Dict[str, str] = {
    "ask_run": "one says ask first, the other says just do it",
    "whose": "one says the act is the agent's, the other says it is the user's",
    "always_never": "one says always, the other says never",
    "order": "they order the same two steps the other way round",
}

_COMPILED = tuple((axis, pole, re.compile(p)) for axis, pole, p in DIRECTIVES)
# Order cues: "A before B", "A after B" / "after A, B", "A, then B".
_ORDER_RE = re.compile(r"\b(before|after|then)\b")
_ORDER_WINDOW = 6

# A sentence pair must share at least this many content stems, and they must be most
# (more than MIN_SHARE) of the shorter sentence's content stems: "shares most of its
# content words". Measured on the estate (counts in the RUL-8 PR): 0.5 lists the one live
# conflict and nothing else on its own plane; 0.4 and 0.35 add mostly false candidates.
MIN_SHARED = 4
MIN_SHARE = 0.5
# The two clauses that flip must share this many content stems themselves. always/never
# words are everywhere in rule prose, so that axis needs a two-word shared predicate.
CLAUSE_OVERLAP: Dict[str, int] = {"ask_run": 1, "whose": 1, "always_never": 2}
# A "sentence" past this many content stems is a list or a table run on, not one directive.
MAX_SENTENCE_STEMS = 40
# Directive cue words are not content: two sentences do not "share" the word "ask".
_CUE_WORDS = frozenset(
    "ask asks asked asking run runs execute confirm confirmed confirmation wait check "
    "always never must avoid before after then first required mandatory proceed "
    "approval permission word yes ok okay gated just simply yourself call alone".split()
)
_BEHAVIOR_TYPES = ("user", "feedback")
_QUOTE_CHARS = 150
_MAX_CANDIDATES = 200

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BULLET_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'*(\[_])|\s+[·•☠]\s*|^[☠•]\s*")
_CLAUSE_SPLIT_RE = re.compile(r"\s+[—–]\s+|\s+--\s+|;\s*|:\s+|,?\s+but\s+|,?\s+then,?\s+|[()]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# Text → sentences → clauses → features
# --------------------------------------------------------------------------- #
def _sentences(text: str) -> List[str]:
    """Prose sentences of one body: fenced code, tables, headings and comments dropped;
    wrapped lines joined back into their bullet or paragraph first."""
    text = _COMMENT_RE.sub(" ", text or "")
    blocks: List[str] = []
    cur: List[str] = []
    in_fence = False

    def flush() -> None:
        if cur:
            blocks.append(" ".join(cur))
            cur.clear()

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith(("```", "~~~")):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        if not s or s.startswith(("#", "|", "Related:", "---")):
            flush()
            continue
        if _BULLET_RE.match(s):
            flush()
            s = _BULLET_RE.sub("", s, count=1)
        cur.append(s)
    flush()
    out: List[str] = []
    for b in blocks:
        out.extend(x.strip() for x in _SENTENCE_SPLIT_RE.split(b) if x.strip())
    return out


def _stems(text: str) -> Set[str]:
    from .build_index import bm25_terms, tokenize

    toks = [t for t in tokenize(text) if t not in _CUE_WORDS]
    return set(bm25_terms(toks))


def _clause_poles(clause: str) -> Dict[str, Set[str]]:
    """``{axis: {poles}}`` the clause votes, after negated forms claim their words."""
    s = clause.lower().replace("’", "'").strip()
    out: Dict[str, Set[str]] = {}
    for axis, pole, rx in _COMPILED:
        hit = False

        def _mask(m: "re.Match") -> str:
            nonlocal hit
            hit = True
            return " " * len(m.group(0))

        s = rx.sub(_mask, s)
        if hit:
            out.setdefault(axis, set()).add(pole)
    return out


def _orders(sentence: str) -> List[Tuple[Set[str], Set[str]]]:
    """``[(first, second)]`` stem sets for each order cue in the sentence."""
    low = sentence.lower()
    out: List[Tuple[Set[str], Set[str]]] = []
    for m in _ORDER_RE.finditer(low):
        left_text = re.split(r"[.;:—–]", low[: m.start()])[-1]
        right_text = re.split(r"[.;:—–]", low[m.end():])[0]
        cue = m.group(1)
        if cue == "after" and not left_text.strip(" ,"):
            # "after A, B": A first
            a, _, b = right_text.partition(",")
            first, second = _stems(a), _stems(b)
        else:
            right_words = right_text.split()[: _ORDER_WINDOW * 2]
            right = _stems(" ".join(right_words))
            left_words = left_text.split()[-_ORDER_WINDOW * 2:]
            left = _stems(" ".join(left_words))
            first, second = (right, left) if cue == "after" else (left, right)
        if first and second:
            out.append((first, second))
    return out


class _Sent:
    """One sentence: its content stems now, its clauses only when a pair is judged (most
    sentences never share enough words with any rule sentence to get that far)."""

    __slots__ = ("text", "stems", "_clauses")

    def __init__(self, text: str) -> None:
        self.text = text
        self.stems = _stems(text)
        self._clauses: Optional[list] = None

    @property
    def clauses(self) -> list:
        if self._clauses is None:
            self._clauses = [
                (_stems(c), _clause_poles(c))
                for c in _CLAUSE_SPLIT_RE.split(self.text) if c and c.strip()
            ]
        return self._clauses


def _votes(sent: _Sent, shared: Set[str]) -> Dict[str, Optional[str]]:
    """One pole per axis from the clauses that carry a shared word; ``None`` = ambiguous."""
    seen: Dict[str, Set[str]] = {}
    for stems, poles in sent.clauses:
        if not (stems & shared):
            continue
        for axis, ps in poles.items():
            seen.setdefault(axis, set()).update(ps)
    return {axis: (next(iter(ps)) if len(ps) == 1 else None) for axis, ps in seen.items()}


def _clauses_oppose(sa: _Sent, sb: _Sent, axis: str) -> bool:
    """True when one clause of each sentence votes opposite poles on ``axis`` AND the two
    clauses share the predicate (``CLAUSE_OVERLAP[axis]`` content stems): the directive
    flip has to sit on the shared words, not elsewhere in a long sentence."""
    need = CLAUSE_OVERLAP.get(axis, 1)
    for a_stems, a_poles in sa.clauses:
        pa = a_poles.get(axis)
        if not pa or len(pa) != 1:
            continue
        for b_stems, b_poles in sb.clauses:
            pb = b_poles.get(axis)
            if pb and len(pb) == 1 and pa != pb and len(a_stems & b_stems) >= need:
                return True
    return False


def directive_conflict(a: str, b: str) -> Optional[str]:
    """The axis on which sentence ``a`` and sentence ``b`` disagree, or ``None``.

    Both gates in one call: the two must share most of their content words, and differ in
    their directive on that shared predicate. Pure; exposed for tests and the gate."""
    sa, sb = _Sent(a), _Sent(b)
    return _pair_axis(sa, sb)


def _pair_axis(sa: _Sent, sb: _Sent) -> Optional[str]:
    shared = sa.stems & sb.stems
    if len(shared) < MIN_SHARED:
        return None
    if len(shared) / max(1, min(len(sa.stems), len(sb.stems))) <= MIN_SHARE:
        return None
    va, vb = _votes(sa, shared), _votes(sb, shared)
    for axis in ("ask_run", "whose", "always_never"):
        pa, pb = va.get(axis), vb.get(axis)
        if pa and pb and pa != pb and _clauses_oppose(sa, sb, axis):
            return axis
    for fa, sa_ in _orders(sa.text):
        for fb, sb_ in _orders(sb.text):
            x = fa & sb_ & shared  # first in a, second in b
            y = sa_ & fb & shared  # second in a, first in b
            if x - y and y - x:  # two DISTINCT steps swap places, not one token on both sides
                return "order"
    return None


# --------------------------------------------------------------------------- #
# Corpus × governance
# --------------------------------------------------------------------------- #
def _slug(heading: Optional[str]) -> str:
    return _SLUG_RE.sub("-", (heading or "").lower()).strip("-")


def rule_sections(repo_root: str) -> List[dict]:
    """``[{"label", "file", "heading", "sentences": [_Sent]}]`` over the governance plane.
    ``label`` is ``rule:<file>`` or ``rule:<file>#<heading-slug>`` (suffixed ``-2``… when
    one file repeats a heading). Unreadable files are skipped."""
    from .rules_plane import _rel, _split_sections, gov_files

    out: List[dict] = []
    for path in gov_files(repo_root):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        rel = _rel(repo_root, path)
        used: Dict[str, int] = {}
        for heading, sec in _split_sections(text):
            slug = _slug(heading)
            n = used.get(slug, 0) + 1
            used[slug] = n
            if n > 1:
                slug = f"{slug}-{n}"
            body = sec.split("\n", 1)[1] if heading and "\n" in sec else ("" if heading else sec)
            sents = [_Sent(s) for s in _sentences(body)]
            sents = [s for s in sents if MIN_SHARED <= len(s.stems) <= MAX_SENTENCE_STEMS]
            if sents:
                out.append({
                    "label": f"rule:{rel}#{slug}" if slug else f"rule:{rel}",
                    "file": rel,
                    "heading": heading or "",
                    "sentences": sents,
                })
    return out


def behavior_memories(
    memory_dir: str, types: Tuple[str, ...] = _BEHAVIOR_TYPES
) -> List[Tuple[str, List[_Sent]]]:
    """``[(name, sentences)]`` for every memory of ``types`` (the behavior lane by default)
    in the effective view, description first, then body. ``MEMORY.md`` and untyped files
    are skipped."""
    from .fm_access import fm_get
    from .outbox_edit import iter_files
    from .provenance import parse_frontmatter, split_frontmatter

    out: List[Tuple[str, List[_Sent]]] = []
    for path in iter_files(memory_dir):
        name = os.path.basename(path)[:-3]
        if name == "MEMORY":
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        fm = parse_frontmatter(text) or {}
        if str(fm_get(fm, "type") or "").strip().lower() not in types:
            continue
        _fm, body = split_frontmatter(text)
        desc = str(fm_get(fm, "description") or "").strip()
        sents = [_Sent(s) for s in ([desc] if desc else []) + _sentences(body)]
        sents = [s for s in sents if MIN_SHARED <= len(s.stems) <= MAX_SENTENCE_STEMS]
        if sents:
            out.append((name, sents))
    return out


def _quote(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= _QUOTE_CHARS else s[: _QUOTE_CHARS - 1].rstrip() + "…"


def text_conflicts(
    memory_dir: str, repo_root: str, *, types: Tuple[str, ...] = _BEHAVIOR_TYPES
) -> List[dict]:
    """Every (behavior memory, rule section) pair whose sentences disagree, one row each::

        {"memory", "rule", "file", "heading", "axis", "memory_says", "rule_says", "shared"}

    The row keeps the best-sharing disagreeing sentence pair of that (memory, section).
    Sorted by memory then rule label; capped. One doctor run or tend apply asks more than
    once, so a result is reused in-process while no read file's mtime or size moved.
    Never raises; ``[]`` on any failure."""
    try:
        if not repo_root or not os.path.isdir(repo_root):
            return []  # no project root, no governance plane (never glob the cwd)
        key = (os.path.abspath(memory_dir), os.path.abspath(repo_root), tuple(types),
               MIN_SHARED, MIN_SHARE, _signature(memory_dir, repo_root))
        if key not in _CACHE:
            _CACHE.clear()  # one live entry: the corpus this process is looking at
            _CACHE[key] = _text_conflicts(memory_dir, repo_root, types)
        return [dict(r) for r in _CACHE[key]]
    except Exception:
        return []


_CACHE: Dict[tuple, List[dict]] = {}


def _signature(memory_dir: str, repo_root: str) -> tuple:
    from .outbox_edit import iter_files
    from .rules_plane import gov_files

    sig = []
    for p in list(gov_files(repo_root)) + list(iter_files(memory_dir)):
        try:
            st = os.stat(p)
            sig.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((p, 0, 0))
    return tuple(sig)


def _text_conflicts(memory_dir: str, repo_root: str, types: Tuple[str, ...]) -> List[dict]:
    try:
        sections = rule_sections(repo_root)
        if not sections:
            return []
        # Inverted index: stem -> [(section idx, sentence idx)], so each memory sentence
        # only meets the rule sentences it shares at least one word with.
        postings: Dict[str, List[Tuple[int, int]]] = {}
        for si, sec in enumerate(sections):
            for ji, sent in enumerate(sec["sentences"]):
                for st in sent.stems:
                    postings.setdefault(st, []).append((si, ji))
        rows: List[dict] = []
        for name, msents in behavior_memories(memory_dir, types):
            best: Dict[int, Tuple[int, dict]] = {}
            for ms in msents:
                counts: Dict[Tuple[int, int], int] = {}
                for st in ms.stems:
                    for key in postings.get(st, ()):
                        counts[key] = counts.get(key, 0) + 1
                for (si, ji), n in counts.items():
                    if n < MIN_SHARED or (si in best and best[si][0] >= n):
                        continue
                    rs = sections[si]["sentences"][ji]
                    axis = _pair_axis(ms, rs)
                    if axis is None:
                        continue
                    sec = sections[si]
                    best[si] = (n, {
                        "memory": name, "rule": sec["label"], "file": sec["file"],
                        "heading": sec["heading"], "axis": axis,
                        "memory_says": _quote(ms.text), "rule_says": _quote(rs.text),
                        "shared": n,
                    })
            rows.extend(r for _n, r in best.values())
        rows.sort(key=lambda r: (r["memory"], r["rule"]))
        return rows[:_MAX_CANDIDATES]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# The tend queue: entries, the opt-in LLM confirmation, details and verdicts
# --------------------------------------------------------------------------- #
_PROPOSED = "read both; fix the memory or the rule, scope both, or mark them not conflicting"
_CONFIRMED_NOTE = " Confirmed by the contradiction check."


def target_of(row: dict) -> str:
    """The tend target: ``<memory>|rule:<file>[#<section>]`` (memory names hold no ``|``)."""
    return f"{row['memory']}|{row['rule']}"


def is_rule_target(target: str) -> bool:
    return "|rule:" in (target or "")


def where(row: dict) -> str:
    return f"{row['file']} § {row['heading']}" if row.get("heading") else row["file"]


def _pair_key(memory: str, rule: str) -> Tuple[str, str]:
    """The DRM-C ledger's order-free key (``dream_contra._contra_key``)."""
    return (memory, rule) if memory <= rule else (rule, memory)


def _ledger(telemetry_dir: Optional[str]) -> Dict[Tuple[str, str], dict]:
    try:
        if not telemetry_dir:
            return {}
        from .dream_contra import read_contradiction_verdicts

        return read_contradiction_verdicts(telemetry_dir)
    except Exception:
        return {}


def entries_for(rows: List[dict], telemetry_dir: Optional[str]) -> List[dict]:
    """``rows`` as tend ``contradiction`` entries. The evidence names both sides and quotes
    the two sentences, so editing either text changes the evidence hash and lapses a skip;
    an LLM "conflict" verdict on the pair adds the confirmed note, and nothing else."""
    from .tend_queue import _entry

    ledger = _ledger(telemetry_dir)
    out = []
    for row in rows:
        rec = ledger.get(_pair_key(row["memory"], row["rule"])) or {}
        note = _CONFIRMED_NOTE if rec.get("conflict") is True else ""
        out.append(_entry(
            "contradiction", target_of(row),
            f"{row['memory']} disagrees with the rule in {where(row)} "
            f"({AXIS_LABELS[row['axis']]}): the memory says “{row['memory_says']}”; "
            f"the rule says “{row['rule_says']}”.{note}",
            _PROPOSED,
        ))
    return out


def queue_entries(memory_dir: str, repo_root: str) -> List[dict]:
    """The tend queue's text-leg source (``tend_queue._src_contradiction`` appends these)."""
    from .telemetry import default_telemetry_dir

    return entries_for(text_conflicts(memory_dir, repo_root), default_telemetry_dir(memory_dir))


def pending(memory_dir: str, repo_root: str) -> List[dict]:
    """The text leg's rows minus this clone's tend skips, snoozes and holds: what doctor
    counts, so a dismissed candidate stops counting. Never raises; ``[]`` on failure."""
    try:
        from .telemetry import default_telemetry_dir
        from .tend_queue import hidden_check

        rows = text_conflicts(memory_dir, repo_root)
        if not rows:
            return []
        hidden = hidden_check(memory_dir)
        entries = entries_for(rows, default_telemetry_dir(memory_dir))
        return [row for row, e in zip(rows, entries) if hidden(e) is None]
    except Exception:
        return []


def _row_for(target: str, memory_dir: str, repo_root: str) -> Optional[dict]:
    for row in text_conflicts(memory_dir, repo_root):
        if target_of(row) == target:
            return row
    return None


def detail_lines(entry: dict, memory_dir: str, repo_root: str) -> List[str]:
    """``hippo tend next`` / ``show`` lines for one rule item: both sides, the two
    sentences, and what each verdict does here."""
    from .resolve_view import _description_of

    memory, rule = entry["target"].split("|", 1)
    row = _row_for(entry["target"], memory_dir, repo_root)
    lines = [f"{memory}: {_description_of(memory_dir, memory)}"]
    if row is None:
        return lines + [f"{rule}: no longer disagrees (the text moved since the queue was built)."]
    return lines + [
        f"rule: {where(row)}",
        f"  the memory says: {row['memory_says']}",
        f"  the rule says:   {row['rule_says']}",
        "hippo never edits a rule file. keep_one or merge with the rule as winner demotes the "
        f"memory (--winner {rule} --loser {memory}); with the memory as winner, edit "
        f"{row['file']} yourself and the item leaves the queue.",
    ]


def apply_verdict(entry: dict, verdict: str, memory_dir: str, repo_root: str,
                  winner: Optional[str], loser: Optional[str]) -> dict:
    """One verdict on one rule item, routed like the memory-pair kind but never writing a
    rule file:

      - ``not_conflicting`` / ``scope_both``: recorded as a tend skip on this evidence (the
        existing skip/evidence-hash state, no new store), so the item stays down until
        either text changes;
      - ``keep_one`` / ``merge`` with the RULE as winner: the memory is demoted (the
        reverify ``demote`` engine call, the one ``keep_one`` uses for a memory loser);
      - ``keep_one`` / ``merge`` with the MEMORY as winner: refused, because hippo never
        edits governance files; edit the rule and the item leaves the queue on its own.

    ``--winner rule`` / ``--loser rule`` name the rule side without its full label.
    """
    from . import tend as T

    memory, rule = entry["target"].split("|", 1)
    if verdict in ("not_conflicting", "scope_both"):
        T._mark_done(memory_dir, entry)
        what = "not conflicting" if verdict == "not_conflicting" else "scoped"
        return T._ok(f"{memory} ⇄ {rule}: recorded as {what}; it stays down until either text changes.")
    w = rule if winner == "rule" else winner
    lo = rule if loser == "rule" else loser
    if {w, lo} != {memory, rule}:
        return T._refused(f"{verdict} needs --winner and --loser naming {memory} and {rule}")
    if w == memory:
        file_part = rule[len("rule:"):].split("#", 1)[0]
        return T._refused(
            f"hippo never edits a rule file: change {file_part} so it no longer disagrees "
            f"with {memory} (the item then leaves the queue), or mark them not conflicting."
        )
    r = T._apply_reverify(memory, "demote", memory_dir, repo_root, None)
    if r.get("ok"):
        r["message"] = f"{verdict} {rule} over {memory}: {r['message']}"
    return r


def repo_root_of(memory_dir: str) -> str:
    """The project root a corpus's governance plane hangs off: git's toplevel for the
    corpus dir, else the dir holding a ``.claude/memory``; ``""`` (no plane) otherwise."""
    try:
        from .provenance_env import git_root

        top = git_root(memory_dir)
        if top:
            return top
    except Exception:
        pass
    md = os.path.abspath(memory_dir)
    parent = os.path.dirname(md)
    if os.path.basename(md) == "memory" and os.path.basename(parent) == ".claude":
        return os.path.dirname(parent)
    return ""


def _prompt(row: dict) -> str:
    return "\n".join([
        "A saved memory note and a project rule follow, with the one sentence of each that a",
        "text check found disagreeing. Decide whether they ACTUALLY CONFLICT (following one",
        "would break the other) or only overlap or apply to different cases.",
        "Respond with ONLY a JSON object, no prose:",
        '{"conflict": true|false, "reason": "<one line, <=200 chars>"}',
        "",
        f"MEMORY ({row['memory']}): {row['memory_says']}",
        f"RULE ({where(row)}): {row['rule_says']}",
    ])


def confirm_candidates(memory_dir: str, repo_root: str, telemetry_dir: str, *,
                       pass_id: str, max_pairs: Optional[int] = None,
                       timeout_s: Optional[float] = None) -> dict:
    """The opt-in LLM contradiction check over the text leg's candidates (the dream pass
    runs it beside its memory-pair check, under the same opt-in). One strict-JSON call per
    candidate not judged before; the verdict lands in the same derived ledger
    (``contradictions.jsonl``) keyed ``(memory, rule:…)``, where the memory-pair inbox
    never lists it (a ``rule:`` side is no memory file). ``conflict: true`` adds the
    confirmed note to the item's evidence; ``false`` is recorded and changes nothing: the
    check may confirm, never dismiss or decide. Never raises."""
    stats = {"attempts": 0, "confirmed": 0, "not_confirmed": 0, "llm_failures": 0}
    try:
        from . import llm_client
        from .dream_config import contra_llm_timeout, contra_max_pairs
        from .dream_contra import _append_contradiction_rows
        from .secrets import scan_text

        cap = contra_max_pairs() if max_pairs is None else max_pairs
        tmo = contra_llm_timeout() if timeout_s is None else timeout_s
        prior = _ledger(telemetry_dir)
        rows = []
        for row in text_conflicts(memory_dir, repo_root):
            if stats["attempts"] >= cap:
                break
            key = _pair_key(row["memory"], row["rule"])
            if key in prior:
                continue
            stats["attempts"] += 1
            raw = llm_client.complete(_prompt(row), timeout_s=tmo, max_tokens=256)
            verdict = llm_client.extract_json(raw) if raw else None
            if not isinstance(verdict, dict) or not isinstance(verdict.get("conflict"), bool):
                stats["llm_failures"] += 1
                continue
            reason = " ".join(str(verdict.get("reason") or "").split())[:240]
            if reason and scan_text(reason):
                reason = "(reason withheld: secret lint)"
            stats["confirmed" if verdict["conflict"] else "not_confirmed"] += 1
            rows.append({
                "pass": pass_id, "a": key[0], "b": key[1], "conflict": verdict["conflict"],
                "reason": reason, "model": llm_client.model_name(),
                "state": "rule-confirmed" if verdict["conflict"] else "rule-not-confirmed",
            })
        _append_contradiction_rows(telemetry_dir, rows)
    except Exception:
        pass
    return stats
