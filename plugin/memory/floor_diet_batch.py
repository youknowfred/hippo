"""NAT-3: the floor diet as one reviewed batch.

The diet (``floor_diet``) proposes one floor line per tend item, and a demotion is one
verdict on one item. A floor far over its budget needs dozens of them (72 on the corpus the
diet was first worked on), each a yes to a line that looks like the one before. This module
is the second way to give that yes, for the floor diet only:

- **review** (``hippo tend diet``): every demotion the batch would make, in the diet's order,
  each with its evidence and the literal floor line that goes; then what a batch never
  takes, each with its line and any other pointer on it; then where the floor would end
  against its target and budget, and a digest.
- **verdict** (``hippo tend apply floor:diet --verdict demote --digest <d>``): demotes the
  lines that review listed, less any names given with ``--except``.

The rule this keeps. hippo's corrective writes are agent-gated and per item: one verdict,
one write, never autonomous. A reviewed batch is one verdict over an enumerated set the user
was shown. Nothing is applied that was not listed, nothing is applied without the digest of
that list, and nothing here runs by itself: no hook, no schedule and no other verb calls it.
It is not an apply-all. It does not exist for any other tend kind.

What the batch is. The list is the sequence the single-item path would serve, less the lines
a batch never takes: take the diet's first item proposed as safe, remove its line, weigh the floor again, take the next,
until the floor is at its target or nothing safe is left. Weighing again matters because a
pointer's trim-safety is judged against the floor it sits on: a demoted memory stops
collapsing onto its floor line and competes for the hook's slots, which can change whether
a later pointer is still served without its own line. So each listed item was judged on the
floor as the items before it leave it, which is the floor it will be judged on again.

What the digest binds. The sha256 of the floor's bytes (the outbox's floor copy when there
is one) and the listed names, in order. The review records those three things, and nothing
else, in the gitignored tend state (``tend_state.json``, the file snoozes and holds live
in): the verdict needs the list, and the list cannot be recomputed later because the
judgments move. The verdict is refused when the digest is not the recorded one or when the
floor's bytes are not the reviewed ones, so a digest is spent by the first line it removes.
The review writes nothing in the corpus.

Judged again at apply, and skipped rather than forced. Between the review and the verdict
the replay's window moves: it holds the newest 200 prompts, and the reviewing session's own
prompts enter it. On the first corpus worked, three of 72 approved demotions had become
lines that lose a recall hit by the time they were applied, and one pointer went safe,
unsafe, safe again inside one session. There were two ways to treat that:

1. pin the batch to the rankings the review used, so what was approved is what happens;
2. judge each item again as its turn comes, on the prompts then on record, and skip one
   that is no longer proposed as safe.

This module does the second. A prompt on which the hook reached a memory only through its
floor line is evidence about that line whichever session typed it, and a pinned batch
would have hippo remove a line it can see, at that moment, losing a recall hit: the one
thing the diet never proposes. The costs are not alike either. A skip costs a second,
shorter review; a forced demotion costs a recall hit nobody is told about. And pinning the
rankings would not pin the judgments, because they also depend on the floor, which the
batch itself changes line by line; the skip has to exist either way. So an item is demoted
only if, at its turn, the floor is still over its target and the diet still judges the line
safe: it loses no recall hit, its replay is finished, and it is not too new. Anything else
is skipped and named with its reason, the result says where the floor ended against the
target and the budget, and names what the diet proposes next. The removal is exact as well:
a line that is no longer the reviewed text is skipped, not removed.

Never in the batch: an item the diet marks "wait" or "keep it", and a line that carries
more than one pointer. The diet weighs the first pointer on a line; the others have no use
count and no replay, so it cannot say they are safe to take off the floor, and a batch
that took the line would lose their recall hits unseen, which is the thing the skip above
exists to prevent. The review shows all of these with their lines, and demoting one stays a
single explicit verdict on that item. A name passed to ``--except``
that the review did not list refuses the whole verdict (a typo must not demote the line it
was meant to spare). An item the tend state hides (``hippo tend hold``, ``snooze``,
``skip``) is left on the floor and the review plans around it: that is how a line is kept
whatever its use count says. A hold placed after the review is honoured by the verdict too.

The review and the verdict finish the trim-safety replay before judging (``hippo tend``
ranks at most 50 prompts per call; these rank what is left, a cold 200-prompt replay being
about a minute on a thousand-memory corpus), so a review does not come back mostly "wait".

A corpus that declares no floor budget has no diet: the review says so and writes nothing.
"""

from __future__ import annotations

import hashlib
import time
from typing import List, Optional

from . import floor_diet as FD

BATCH_ID = "floor:diet"
REVIEW_COMMAND = "hippo tend diet"
DIGEST_CHARS = 12  # the length ``hippo trust review`` prints
_NEXT_SHOWN = 5


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def review_digest(floor_sha: str, names: List[str]) -> str:
    """The token a verdict must quote: a hash over the floor's hash and the listed names in
    order. A byte of the floor or a name of the list changes it."""
    raw = "\n".join(["floor-diet-review-1", floor_sha, *names])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def _settled(memory_dir: str, text: str, now: float, memo: dict) -> Optional[dict]:
    """The diet's plan for ``text`` with the replay finished: each pass ranks one batch of
    the prompts still missing, and the loop ends when none are (or ranking cannot run)."""
    p = None
    for _ in range(FD.REPLAY_PROMPTS // max(1, FD.REPLAY_BATCH) + 2):
        p = FD.plan(memory_dir, now=now, text=text, memo=memo)
        if not p or not p["unreplayed"]:
            break
        memo.pop("pools_key", None)
    return p


def _against(size: int, target: int, budget: int) -> str:
    """``N B: where that is against the target and the budget``."""
    if size <= target:
        return f"{size:,}B: {target - size:,}B under its {target:,}B target, within its {budget:,}B budget"
    if size <= budget:
        return f"{size:,}B: {size - target:,}B over its {target:,}B target, within its {budget:,}B budget"
    return (f"{size:,}B: {size - target:,}B over its {target:,}B target and {size - budget:,}B over "
            f"its {budget:,}B budget")


def _kept_by_state(memory_dir: str, rows, now: float) -> dict:
    """``{name: why}`` for the rows this clone's tend state hides (held, snoozed, skipped),
    judged on the evidence the queue shows for the floor as it is, which is the evidence a
    skip was taken over."""
    from . import tend_queue as Q

    state, hidden = Q.read_state(memory_dir), Q.hidden_check(memory_dir, now)
    kept = {}
    for r in rows:
        entry = Q._entry("floor", FD.TARGET_PREFIX + r["name"], r["evidence"], "")
        why = hidden(entry)
        if why == "held":
            why = f"held: {(Q._held_by(entry, state) or {}).get('reason') or 'no reason given'}"
        if why:
            kept[r["name"]] = why
    return kept


def _solo_line(r: dict) -> str:
    """The likely verdict for a line the diet proposes but a batch never takes."""
    return (f"single verdict only: its line also points at {', '.join(r['riders'])}, which the diet "
            "did not weigh (no use count, no replay); demote it by itself if you accept that "
            f"{'it leaves' if len(r['riders']) == 1 else 'they leave'} the floor too")


def _item(r: dict, lines: List[str], exists) -> dict:
    return {"id": f"{BATCH_ID}:{r['name']}", "name": r["name"], "evidence": r["evidence"],
            "lines": lines, "riders": FD.riders(lines, r["name"], exists)}


def build_review(memory_dir: str, repo_root: str, *, now: Optional[float] = None) -> Optional[dict]:
    """The review, or None when the corpus declares no floor budget (or has no floor).

    ``{"budget", "target", "bytes", "items", "rest", "held", "after", "floor_sha256",
    "digest"}``: ``items`` is the batch in order (``id, name, evidence, lines, riders,
    after``), ``rest`` what a batch never takes (``proposed`` added): the lines that carry
    other pointers, then the "wait" and "keep it" items the floor would still need, ``held`` the items the tend state keeps on the floor, ``after``
    the floor's bytes once the batch is applied, and ``digest`` None when the batch is
    empty. Writes nothing in the corpus; fills the gitignored replay cache."""
    from .lint_floor import memory_exists
    from .new_memory_floor import _without_pointer

    text0 = FD.floor_text(memory_dir)
    if text0 is None:
        return None
    now = time.time() if now is None else now
    memo: dict = {}
    q = _settled(memory_dir, text0, now, memo)
    if q is None:
        return None
    exists = memory_exists(memory_dir)
    kept = _kept_by_state(memory_dir, q["rows"], now)
    out = {"budget": q["budget"], "target": q["target"], "bytes": q["bytes"], "items": [], "rest": [],
           "held": [], "floor_sha256": _sha(text0), "digest": None}
    text, passed, rest = text0, [], {}
    while q["bytes"] > q["target"]:
        pick = None
        for r in q["rows"]:  # in the diet's order: what it proposes as safe comes first
            if not FD.demotable(r):
                break
            if r["name"] in kept:
                if r["name"] not in passed:
                    passed.append(r["name"])
                continue
            if r["riders"]:  # its other pointers were never weighed: a single verdict only
                rest[r["name"]] = r
                continue
            pick = r
            break
        if pick is None:
            break
        text, gone = _without_pointer(text, pick["name"])
        out["items"].append(dict(_item(pick, gone, exists), after=len(text.encode("utf-8"))))
        q = FD.plan(memory_dir, now=now, text=text, memo=memo)
    left = q["bytes"]
    for r in q["rows"]:  # then what the floor still needs and the diet does not propose as safe
        if left <= q["target"]:
            break
        if r["name"] in kept:
            continue
        left -= r["bytes"]
        rest.setdefault(r["name"], r)
    for name, r in rest.items():
        _t, gone = _without_pointer(text, name)
        proposed = _solo_line(r) if FD.demotable(r) else FD.proposal_line(q, r)
        out["rest"].append(dict(_item(r, gone, exists), proposed=proposed))
    out["held"] = [{"id": f"{BATCH_ID}:{n}", "name": n, "why": kept[n]} for n in passed]
    out["after"] = len(text.encode("utf-8"))
    if out["items"]:
        out["digest"] = review_digest(out["floor_sha256"], [it["name"] for it in out["items"]])
    return out


def _item_lines(it: dict, n: Optional[int] = None) -> List[str]:
    out = [f"{n:>3}. {it['id']}" if n else f"  {it['id']}", f"      why: {it['evidence']}"]
    if it.get("proposed"):
        out.append(f"      likely: {it['proposed']}")
    out += [f"      line: {ln}" for ln in it["lines"]]
    if it["riders"]:
        too = "it leaves" if len(it["riders"]) == 1 else "they leave"
        out.append(f"      also on this line ({too} the floor too): {', '.join(it['riders'])}")
    if "after" in it:
        out.append(f"      then: the floor would be {it['after']:,}B")
    return out


def render_review(rv: dict, surface: str = "cli") -> str:
    """The review as text. ``surface`` picks how the verdict is spelled: the ``hippo``
    command, or the ``tend`` tool's arguments (``mcp``)."""
    items, rest = rv["items"], rv["rest"]
    size = f"MEMORY.md is {rv['bytes']:,}B; its budget is {rv['budget']:,}B and the diet runs to {rv['target']:,}B."
    single = ("hippo tend apply <id> --verdict demote" if surface == "cli"
              else "tend action='apply', id=<id>, verdict='demote'")
    lines: List[str] = []
    if not items and not rest:
        return (f"Floor diet review: nothing to review. {size} "
                + ("The floor is within the diet's target." if rv["bytes"] <= rv["target"] else
                   "Every line the diet would propose is held, snoozed or skipped in your tend state: "
                   + "; ".join(f"{h['id']} ({h['why']})" for h in rv["held"]) + "."))
    if items:
        lines += [f"Floor diet review: {len(items)} demotion(s), in the order the diet serves them. "
                  "This review demotes nothing.", size, ""]
        for n, it in enumerate(items, start=1):
            lines += _item_lines(it, n)
    else:
        lines += ["Floor diet review: no line can go in a batch.", size]
    if rest:
        lines += ["", f"Not in this batch ({len(rest)}): a batch never takes these. Each stays a single "
                  f"verdict ({single})."]
        for it in rest:
            lines += _item_lines(it)
    if rv["held"]:
        lines += ["", "Left on the floor by your tend state, and planned around: "
                  + "; ".join(f"{h['id']} ({h['why']})" for h in rv["held"]) + "."]
    if not items:
        return "\n".join(lines)
    lines += ["", f"After this batch the floor would be {_against(rv['after'], rv['target'], rv['budget'])}.",
              "Each item is judged again as its turn comes, on the floor as the earlier lines leave it and "
              "the prompts then on record. One that is no longer safe is skipped and named, never forced."]
    if not rv["digest"]:
        return "\n".join(lines + ["This review could not be recorded in the tend state, so it has no digest "
                                  f"and cannot be applied as a batch; each item still takes a single verdict ({single})."])
    if surface == "cli":
        lines += [f"digest: {rv['digest']}",
                  f"Apply exactly this list: hippo tend apply {BATCH_ID} --verdict demote --digest {rv['digest']}",
                  "Leave some out: add --except <name>,<name>"]
    else:
        lines += [f"digest: {rv['digest']}",
                  f"Apply exactly this list: tend action='apply', id='{BATCH_ID}', verdict='demote', "
                  f"digest='{rv['digest']}'",
                  "Leave some out: add except=[<name>, …]"]
    lines.append("Nothing is demoted until that runs.")
    return "\n".join(lines)


def _record(memory_dir: str, rv: dict) -> bool:
    """Keep what the review showed (digest, floor hash, names) in the tend state."""
    from . import tend as T
    from . import tend_queue as Q

    state = Q.read_state(memory_dir)
    state["review"] = {BATCH_ID: {"digest": rv["digest"], "floor_sha256": rv["floor_sha256"],
                                  "names": [it["name"] for it in rv["items"]], "at": int(time.time())}}
    return bool(T._write_state(memory_dir, state))


def _no_diet(memory_dir: str) -> str:
    if not FD.floor_budget(memory_dir):
        return ("this corpus declares no floor budget, so it has no floor diet. Declare one in hippo.json "
                f"(\"budgets\": {{\"floor_bytes\": {FD.SUGGESTED_BUDGET}}} suits a floor imported into "
                "every session) and the diet proposes which floor lines to move out.")
    return "MEMORY.md could not be read, so there is no floor to review."


def review(memory_dir: str, repo_root: str, surface: str = "cli") -> dict:
    """``{"ok", "message", "review"}``: build the review, record what it shows, render it."""
    try:
        rv = build_review(memory_dir, repo_root)
        if rv is None:
            return {"ok": False, "message": _no_diet(memory_dir), "review": None}
        if rv["items"] and not _record(memory_dir, rv):
            rv = dict(rv, digest=None)
        return {"ok": True, "message": render_review(rv, surface), "review": rv}
    except Exception as exc:
        return {"ok": False, "message": f"floor diet review: {type(exc).__name__}: {exc}", "review": None}


def _names(leave_out) -> List[str]:
    if isinstance(leave_out, str):
        leave_out = leave_out.split(",")
    out = []
    for raw in leave_out or ():
        n = str(raw).strip()
        n = n[len(BATCH_ID) + 1:] if n.startswith(BATCH_ID + ":") else n
        n = n[:-3] if n.endswith(".md") else n
        if n and n not in out:
            out.append(n)
    return out


def _why_not(q: dict, row: Optional[dict]) -> Optional[str]:
    """Why this item is not demoted at its turn, or None when the diet still proposes it."""
    if q["bytes"] <= q["target"]:
        return f"the floor is already at its target ({q['bytes']:,}B of {q['target']:,}B)"
    if row is None:
        return "its floor line is gone"
    if row["lost"]:
        return f"it now loses {row['lost']} recall hit{'' if row['lost'] == 1 else 's'} without its floor line"
    if row["waiting"]:
        return "its trim-safety replay is not finished"
    if row["young"]:
        return "it now reads as too new to judge on use"
    return None


def _refused(msg: str) -> dict:
    return {"ok": False, "message": msg, "applied": [], "skipped": [], "left_out": []}


def apply_batch(memory_dir: str, repo_root: str, digest: Optional[str], leave_out=None) -> dict:
    """One verdict over the reviewed list. ``{"ok", "message", "applied": [name],
    "skipped": [(name, why)], "left_out": [name]}``; ``ok`` is False only when the verdict
    itself is refused, and then nothing was demoted."""
    from . import outbox_edit as OE
    from . import tend_queue as Q
    from .new_memory_floor import _without_pointer

    digest = (digest or "").strip()
    again = f"Run `{REVIEW_COMMAND}`, read the list, and quote the digest it prints."
    if not digest:
        return _refused(f"the reviewed floor diet needs --digest: the batch applies one list you were shown. {again}")
    rec = (Q.read_state(memory_dir).get("review") or {}).get(BATCH_ID) or {}
    names = [n for n in rec.get("names") or [] if isinstance(n, str)]
    sha = rec.get("floor_sha256")
    if not names or not isinstance(sha, str) or rec.get("digest") != digest or review_digest(sha, names) != digest:
        return _refused(f"no floor diet review on record has the digest {digest}. Nothing was demoted. {again}")
    text0 = FD.floor_text(memory_dir)
    if text0 is None or _sha(text0) != sha:
        return _refused("the floor is not the one that review showed: MEMORY.md changed since. Nothing was "
                        f"demoted. {again}")
    out_names = _names(leave_out)
    unknown = [n for n in out_names if n not in names]
    if unknown:
        return _refused(f"{', '.join(unknown)}: not in the reviewed list, so the list to leave out is not "
                        f"understood. Nothing was demoted. The review listed: {', '.join(names)}.")
    now, memo = time.time(), {}
    q = _settled(memory_dir, text0, now, memo)
    if q is None:
        return _refused(f"{_no_diet(memory_dir)} Nothing was demoted.")
    budget, target = q["budget"], q["target"]
    shown = {n: _without_pointer(text0, n)[1] for n in names}  # the reviewed bytes: the lines shown
    kept = _kept_by_state(memory_dir, q["rows"], now)  # a hold placed since the review counts
    applied, skipped = [], []
    for name in names:
        if name in out_names:
            continue
        try:
            text = FD.floor_text(memory_dir) or ""
            q = FD.plan(memory_dir, now=now, text=text, memo=memo) or q
            why = _why_not(q, next((r for r in q["rows"] if r["name"] == name), None))
            if why is None and name in kept:
                why = f"your tend state keeps it on the floor ({kept[name]})"
            if why is None:
                r = FD.demote(name, memory_dir, repo_root, expect=shown[name])
                why = None if r["ok"] else (r.get("reason") or r["message"])
        except Exception as exc:  # lines before this one are gone: report it, never "refused"
            why = f"it could not be judged or removed ({type(exc).__name__}: {exc})"
        if why is None:
            applied.append(name)
        else:
            skipped.append((name, why))
    size = len((FD.floor_text(memory_dir) or "").encode("utf-8"))
    lines = [f"Floor diet batch (digest {digest}): {len(applied)} demoted, {len(skipped)} skipped, "
             f"{len(out_names)} left out."]
    lines += [f"  skipped {BATCH_ID}:{n}: {why}" for n, why in skipped]
    if out_names:
        lines.append("  left out at your word: " + ", ".join(f"{BATCH_ID}:{n}" for n in out_names))
    where = ""
    if applied:
        where = (" The lines are gone from the outbox's floor copy (it ships with `hippo ship`)."
                 if OE.floor_dir_for_edit(memory_dir, repo_root) else
                 " The lines are gone from MEMORY.md, a working-tree change to commit.")
        where += " The memory files stay, and recall still reaches them."
    lines.append(f"The floor is now {_against(size, target, budget)}.{where}")
    if size <= target:
        lines.append("At its target: the diet is finished.")
    else:
        nxt = build_review(memory_dir, repo_root) or {"items": [], "rest": []}  # the next list, unrecorded
        rows = ([(it["id"], "demote") for it in nxt["items"]]
                + [(it["id"], it["proposed"].split(":", 1)[0]) for it in nxt["rest"]])
        named = ", ".join(f"{i} ({what})" for i, what in rows[:_NEXT_SHOWN])
        more = len(rows) - _NEXT_SHOWN
        if named:
            lines.append(f"The diet proposes next: {named}{f', and {more} more' if more > 0 else ''}. "
                         f"`{REVIEW_COMMAND}` shows them as the next list.")
        if out_names:
            lines.append(f"To keep a line on the floor whatever its use: hippo tend hold {BATCH_ID}:{out_names[0]} "
                         "--reason \"<why>\"; the next review plans around a held line.")
    return {"ok": True, "message": "\n".join(lines), "applied": applied, "skipped": skipped, "left_out": out_names}
