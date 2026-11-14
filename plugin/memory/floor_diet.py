"""NAT-3: a floor diet before a floor channel.

The floor (``MEMORY.md``) loads into every session. A corpus that declares a hard floor budget
in ``hippo.json`` (GAT-2's ``"budgets": {"floor_bytes": N}``, the one key for that number) gets
demotion proposals in tend's existing ``floor`` kind, one floor pointer per item, until the
floor would be under the budget by the diet's headroom (below). A demotion removes the
pointer line and nothing else: the memory file stays, and recall reaches it like any other
memory.

Headroom. The gate (``gate.judge_floor``) objects to a change that leaves the floor over its
budget and bigger, and ``hippo ship --yes`` refuses on a finding. A diet that stops at the
budget stops within one line of it (40 bytes under, on emgl's 8,192), so the next save that
adds a floor pointer (100 to 230 bytes there) is that change. The diet therefore runs to a
target under the budget: ``budget - headroom``, where the headroom is a sixteenth of the
budget (512 of 8,192: two to five pointer adds) unless the corpus declares
``"budgets": {"floor_headroom_bytes": N}`` (``0`` stops at the budget, as before). The
budget stays the gate's number and the one doctor and each item report against; a floor
between the target and the budget is within its budget, and the diet proposes the lines
that restore the room.

The order, decided for NAT-3:

1. pointers whose memory is not behavior-lane (``lanes.lane_of``: anything but ``type: user``
   or ``feedback``, journals included), least used first;
2. then behavior-lane pointers, least used first;
3. then, and not proposed as safe, any pointer whose memory is too new for its use count to
   mean anything (see "Age" below);
4. last, and never proposed as safe, any pointer whose removal loses a recall hit on CLM-7's
   trim-safety replay (``lint_floor.trim_safety_report``).

Within a class, ties go to the bigger line (fewer demotions reach the budget), then the name.

Age. A use count compares memories only when each had the same time to be used. A memory
first committed inside the span the ledgers hold had less, so it would sort to the front as
"least used" for being new (on emgl's floor, with 4.4 days of ledgers, 11 of the first 41
proposals were memories first committed inside those 4.4 days). Such a pointer is listed
after every pointer that was judged on a full span, its item says "wait" and its evidence
gives the age. The bar is the span the ledgers hold, and never less than ``MIN_AGE_DAYS``:
with short or empty ledgers a memory saved this week is still not "unused", it has not been
needed yet. Ledgers that hold the whole window make the bar the window: there a memory waits
``WINDOW_DAYS`` days. A memory's birth is the commit that first added its file, read for the whole
corpus by one ``git log --diff-filter=A`` (``archive._first_seen_times``; about a quarter
of a second on emgl's 5,700 commits), run only when the floor is over its target. A file git does not date
(a corpus outside git, a gitignored corpus, an unshipped outbox copy, a save not committed
yet) is dated by its mtime, which is the latest it can have been born: old enough by mtime
is old enough, and a file changed inside the bar waits.

"Use" is read from the two raw ledgers hippo already keeps, over the last 30 days:

- injected: prompts on which the prompt hook named the memory, whether it served the row or
  collapsed it onto the floor line (the episode buffer's ``recalled_names``). For a floor
  pointer the collapse is the normal case: the hook matched the memory, and the floor line
  is what delivered it. A memory the hook never matched was delivered only because the floor
  loads everything.
- opened: Read-tool opens of the memory file (OBS-10's ``row_opened`` over the outcome ledger).

A line with more than one pointer. The diet weighs one pointer per floor line, the first.
A demotion removes the whole line, so any other pointer joined onto it leaves the floor
too, and it has no line of its own to be weighed on (no use count, no replay). The item
says so: its evidence names the other memories, ``hippo tend show`` prints the literal
line, and the demotion's answer names what left with it.

The usage aggregates are not used: they fold served names only, so a floor memory, collapsed
on most prompts, reads there as never used. Both ledgers are byte-capped and rotate to their
newest half, so on a busy corpus the window is shorter than 30 days; the evidence names the
span the ledgers actually hold.

The trim-safety replay is the only expensive part: one recall per replayed prompt. It ranks
the newest ``REPLAY_PROMPTS`` distinct prompts once each, and keeps each ranking in the
gitignored ``floor_diet_replay.json`` under the telemetry dir, keyed per prompt and valid while
the index manifest is unchanged. The rankings do not depend on the floor (recall never reads
it), so a demotion reuses them; a new prompt costs one ranking. Doctor never ranks: under
``warm_only`` it reads what is cached and otherwise says the replay runs on ``hippo tend``.

An undeclared corpus is untouched: no proposals, no doctor line unless its floor is over
hippo's warn line, where doctor suggests the 8,192-byte budget the roadmap names for a floor
delivered by a CLAUDE.md import. Nothing here applies that number to anyone.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from typing import Dict, List, Optional, Tuple

WINDOW_DAYS = 30
# The least age at which a use count is read as low, however short the ledgers are. Seven
# days: work on a topic comes in bursts, and under a week "not used" is "not needed yet".
MIN_AGE_DAYS = 7
SUGGESTED_BUDGET = 8192
# The room a finished diet leaves under the budget, so the next floor pointer still passes
# the gate: the declared budgets.floor_headroom_bytes, else budget // HEADROOM_SHARE.
HEADROOM_KEY = "floor_headroom_bytes"
HEADROOM_SHARE = 16
TARGET_PREFIX = "diet:"
START_COMMAND = "hippo tend next --kind floor"

# The trim-safety replay: how many of the newest distinct human prompts it judges (CLM-7's
# 200), how many one call may rank, and how deep. Ranking a prompt is the whole cost (one
# recall each, about 0.25 s dense on emgl's 1,073 memories). A smaller sample loosened the
# judgment there (40 to 80 prompts flagged 0 of the 2 pointers 200 flag), so the sample
# stays 200 and a call ranks at most REPLAY_BATCH of the missing ones, newest first, about
# 12 s on emgl; the rest wait for the next call, and until a pointer's prompts are all
# judged it is not proposed as safe. See _replay_pools for the cache.
REPLAY_PROMPTS = 200
REPLAY_BATCH = 50
REPLAY_K = 10
REPLAY_DEPTH = REPLAY_K + 64  # the hook over-fetches by the floor's size; 64 covers it
_CACHE_FILE = "floor_diet_replay.json"
_CACHE_VERSION = 1
_WARM_ONLY = {"on": False}


@contextlib.contextmanager
def warm_only():
    """Inside it, the diet reads cached replay rankings but never ranks a prompt itself
    (doctor's maintenance-queue row: a health check must not stall on a replay)."""
    was = _WARM_ONLY["on"]
    _WARM_ONLY["on"] = True
    try:
        yield
    finally:
        _WARM_ONLY["on"] = was


def floor_budget(memory_dir: str) -> Optional[int]:
    """The declared ``budgets.floor_bytes``, or None. One small policy read; never raises."""
    try:
        from .gate_cli import read_budgets
        from .provenance_format import read_policy_key

        return read_budgets(read_policy_key(memory_dir, "budgets")).get("floor_bytes")
    except Exception:
        return None


def floor_headroom(memory_dir: str, budget: int) -> int:
    """The bytes the diet keeps free under ``budget``: the declared
    ``budgets.floor_headroom_bytes`` when it is a whole number of bytes from 0 to under the
    budget, else a sixteenth of the budget. The gate never reads it. Never raises."""
    try:
        from .provenance_format import read_policy_key

        raw = read_policy_key(memory_dir, "budgets")
        v = raw.get(HEADROOM_KEY) if isinstance(raw, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < budget:
            return v
    except Exception:
        pass
    return budget // HEADROOM_SHARE


def floor_text(memory_dir: str) -> Optional[str]:
    """The floor as a floor edit sees it (the outbox's copy when there is one)."""
    try:
        from .outbox import effective_path

        with open(effective_path(memory_dir, "MEMORY"), "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def pointers(memory_dir: str, text: str) -> List[dict]:
    """``[{name, line, bytes, type, lane, behavior}]`` for each memory pointer line in the
    floor whose memory exists, in any section (a flat floor keeps its pointers outside the
    floor sections, and every line loads all the same). A pointer to a missing memory is
    the ``missing_targets`` item's business."""
    from .lanes import BEHAVIOR, journal_globs, lane_of
    from .lint_floor import memory_exists
    from .new_memory_floor import _pointer_name
    from .outbox_edit import resolve
    from .staleness import read_memory_type

    globs = journal_globs(memory_dir)
    exists = memory_exists(memory_dir)  # FLR-1: a bare pointer line is weighed too
    out, seen = [], set()
    for i, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith("#"):
            continue
        name = _pointer_name(line, exists)
        if not name or name in seen:
            continue
        path = resolve(os.path.join(memory_dir, f"{name}.md"))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                kind = read_memory_type(fh.read())
        except Exception:
            continue
        seen.add(name)
        lane = lane_of(f"{name}.md", kind, globs)
        out.append({"name": name, "line": i, "bytes": len(line.encode("utf-8")) + 1,
                    "type": kind or "untyped", "lane": lane, "behavior": lane == BEHAVIOR,
                    "text": line, "riders": riders([line], name, exists)})
    return out


def riders(lines, name: str, is_memory=None) -> List[str]:
    """The other memories ``name``'s floor line(s) point at. A demotion removes the whole
    line, so they leave the floor with it; they have no line of their own, so the diet
    weighs none of them (no use count, no replay)."""
    from .lint_floor import pointer_targets

    out: List[str] = []
    for line in lines:
        for t in pointer_targets(line, is_memory):
            n = t.rsplit("/", 1)[-1][:-3]
            if n != name and n not in out:
                out.append(n)
    return out


def _riders_line(names: List[str]) -> str:
    """The evidence clause for a line that carries other pointers; ``""`` for a line of one."""
    if not names:
        return ""
    what = "1 other memory, which leaves" if len(names) == 1 else f"{len(names)} other memories, which leave"
    return f"; its line also points at {what} the floor with it: {', '.join(names)}"


def usage(memory_dir: str, names, now: Optional[float] = None) -> Tuple[Dict[str, dict], float]:
    """``({name: {"injected", "opened"}}, days the ledgers hold inside the window)``."""
    from .outcome_opened import memory_rel, row_opened
    from .telemetry import default_telemetry_dir, read_episodes, read_outcomes

    now = time.time() if now is None else now
    since = now - WINDOW_DAYS * 86400
    names = set(names)
    out = {n: {"injected": 0, "opened": 0} for n in names}
    td = default_telemetry_dir(memory_dir)
    oldest = now
    for e in read_episodes(td):
        ts = e.get("ts")
        if not isinstance(ts, (int, float)) or ts < since:
            continue
        oldest = min(oldest, ts)
        for n in set(e.get("recalled_names") or []) & names:
            out[n]["injected"] += 1
    rel = memory_rel(memory_dir)
    for o in read_outcomes(td):
        ts = o.get("ts")
        if not isinstance(ts, (int, float)) or ts < since:
            continue
        oldest = min(oldest, ts)
        stem = row_opened(o, rel)
        if stem in names:
            out[stem]["opened"] += 1
    return out, round((now - oldest) / 86400.0, 1)


def births(memory_dir: str, names) -> Dict[str, Tuple[float, str]]:
    """``{name: (unix time, "git" | "file")}``: when each memory came to exist. The commit
    that first added its file (one ``git log`` for the whole corpus), else the file's mtime
    (the latest it can have been born). A name is absent only when neither can be read."""
    from .outbox_edit import resolve

    try:
        from .archive import _first_seen_times

        seen = _first_seen_times(memory_dir, memory_dir)
    except Exception:
        seen = {}
    out: Dict[str, Tuple[float, str]] = {}
    for n in names:
        if n in seen:
            out[n] = (float(seen[n]), "git")
            continue
        try:
            out[n] = (os.path.getmtime(resolve(os.path.join(memory_dir, f"{n}.md"))), "file")
        except Exception:
            continue
    return out


def _age_line(r: dict, span: float) -> str:
    """The evidence clause for a pointer too new to judge on use; ``""`` for any other."""
    if not r.get("young"):
        return ""
    if r.get("age_days") is None:
        when = "its age could not be read"
    elif r.get("dated_by") == "git":
        when = f"first committed {r['age_days']:g} day(s) ago"
    else:
        when = f"not in git history, file last changed {r['age_days']:g} day(s) ago"
    bar = f"the {span:g} days the ledgers hold" if span > MIN_AGE_DAYS else f"the {MIN_AGE_DAYS}-day minimum"
    return f"; age: {when}, younger than {bar}, so its use count is not read as low yet"


# --------------------------------------------------------------------------- #
# The replay cache: one ranking per prompt, on disk, so tend and doctor read it back
# --------------------------------------------------------------------------- #
def _cache_path(memory_dir: str) -> str:
    from .telemetry import default_telemetry_dir

    return os.path.join(default_telemetry_dir(memory_dir), _CACHE_FILE)


def _index_fingerprint(index_dir: str) -> Optional[list]:
    """What every cached ranking depends on: the index manifest's size and mtime (any
    rebuild or refresh rewrites it), and the ranking's own depth and k."""
    try:
        st = os.stat(os.path.join(index_dir, "manifest.json"))
        return [_CACHE_VERSION, st.st_size, st.st_mtime_ns, REPLAY_K, REPLAY_DEPTH]
    except OSError:
        return None


def _qhash(q: str) -> str:
    return hashlib.sha256(q.encode("utf-8")).hexdigest()[:24]


def _read_cache(memory_dir: str, fingerprint) -> dict:
    """``{"share", "pools": {prompt hash: [[name, lane, admitted]]}}`` when the cache on
    disk was built against this index; anything torn, foreign or stale reads as empty."""
    empty = {"share": None, "pools": {}}
    try:
        with open(_cache_path(memory_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("fingerprint") != fingerprint or fingerprint is None:
            return empty
        pools = {}
        for h, rows in (doc.get("pools") or {}).items():
            if isinstance(rows, list) and all(isinstance(r, list) and len(r) == 3 for r in rows):
                pools[h] = [(str(n), str(lane), bool(ok)) for n, lane, ok in rows]
        share = doc.get("share")
        return {"share": share if isinstance(share, (int, float)) else None, "pools": pools}
    except Exception:
        return empty


def _write_replay_cache(memory_dir: str, doc: dict) -> bool:
    """Best effort: a failed write costs the next call a replay, nothing else. Never
    creates the telemetry dir (a corpus without one keeps none)."""
    try:
        from .atomic import write_json_atomic

        path = _cache_path(memory_dir)
        if not os.path.isdir(os.path.dirname(path)):
            return False
        write_json_atomic(path, doc)
        return True
    except Exception:
        return False


def _replay_pools(memory_dir: str):
    """``pools_for`` for ``trim_safety_report``: each prompt's ranking from the cache, and
    the missing ones ranked now (never under ``warm_only``), then the cache rewritten with
    exactly the prompts asked for. A new prompt costs one ranking, not a whole replay; a
    cold cache fills ``REPLAY_BATCH`` prompts per call, newest first."""

    def run(queries):
        from .build_index import default_index_dir

        idir = default_index_dir(memory_dir)
        fp = _index_fingerprint(idir)
        cache = _read_cache(memory_dir, fp)
        cached, share = cache["pools"], cache["share"]
        pools = {q: cached[_qhash(q)] for q in queries if _qhash(q) in cached}
        missing = [q for q in queries if q not in pools]
        if missing and not _WARM_ONLY["on"] and fp is not None:
            ranked, new_share = _rank(memory_dir, idir, missing[::-1][:REPLAY_BATCH])
            if ranked is not None:
                pools.update(ranked)
                share = new_share
                _write_replay_cache(memory_dir, {
                    "fingerprint": fp, "share": share,
                    "pools": {_qhash(q): [list(r) for r in pools[q]] for q in queries if q in pools},
                })
        if not pools and (missing or fp is None):
            return None, None
        return pools, share

    return run


def _rank(memory_dir: str, idir: str, queries) -> Tuple[Optional[dict], Optional[float]]:
    """One hook-faithful ranking per prompt: ``REPLAY_DEPTH`` deep, with the hook's lane
    request; every journal is ranked and marked admitted only if the prompt names it."""
    from .build_index import load_index
    from .lanes import JOURNAL, LaneRequest, row_lane
    from .recall import recall

    idx = load_index(idir)
    if idx is None:
        return None, None
    everyone = frozenset(e.get("name") for e in (idx.entries or []) if e.get("name"))
    out, share = {}, None
    for q in queries:
        req = LaneRequest(q, REPLAY_K, floor=everyone)
        got = recall(q, k=REPLAY_DEPTH, index=idx, index_dir=idir, memory_dir=memory_dir, lanes=req)
        out[q] = [(h.get("name") or "", row_lane(h),
                   not req.active or row_lane(h) != JOURNAL or (h.get("name") or "").lower() in req.named())
                  for h in got]
        if req.active:
            share = req.share
    return out, share


def replay_cached(memory_dir: str) -> bool:
    """True when every prompt the replay would rank is already cached for this index."""
    try:
        from .build_index import default_index_dir
        from .lint_floor import recent_prompts

        queries, _named = recent_prompts(memory_dir, None, REPLAY_PROMPTS)
        fp = _index_fingerprint(default_index_dir(memory_dir))
        pools = _read_cache(memory_dir, fp)["pools"]
        return fp is not None and all(_qhash(q) in pools for q in queries)
    except Exception:
        return False


def _use_line(u: dict, span: float) -> str:
    held = "" if span >= WINDOW_DAYS - 0.5 else f" (the ledgers hold {span:g} of them)"
    if not (u["injected"] or u["opened"]):
        return f"neither injected nor opened in the last {WINDOW_DAYS} days{held}"
    return (f"injected on {u['injected']} prompt(s), opened {u['opened']} time(s) in the last "
            f"{WINDOW_DAYS} days{held}")


def _safety_line(row: dict, report: dict) -> str:
    n = report.get("queries", 0)
    if not n:
        return "trim-safety: no recent prompts to replay"
    if not report.get("replayed"):
        return "trim-safety: not replayed yet (it runs on `hippo tend`, against the index)"
    if row.get("lost"):
        return (f"trim-safety: LOSES {row['lost']} recall hit(s) — on {row['lost']} of the "
                f"{row['named']} replayed prompt(s) it matched, the hook would not serve it "
                "without its floor line")
    if row.get("pending"):
        return (f"trim-safety: not judged yet — {report.get('unreplayed', 0)} of the recent prompts "
                "wait to be replayed; the next `hippo tend` replays more of them")
    if row.get("named"):
        return (f"trim-safety: 0 lost hits — on all {row['named']} replayed prompt(s) it matched, "
                "the hook would serve it without its floor line")
    return f"trim-safety: 0 lost hits — it matched none of {n} replayed prompt(s)"


def _unsafe(row: dict, report: dict) -> bool:
    """A pointer is never proposed as safe when the replay shows a lost hit, when a prompt
    the hook named it on is not replayed yet, or when there are prompts to replay but no
    ranking of them at all."""
    return (bool(row.get("lost")) or bool(row.get("pending"))
            or bool(report.get("queries") and not report.get("replayed")))


def plan(memory_dir: str, *, now: Optional[float] = None) -> Optional[dict]:
    """The diet for a corpus with a declared floor budget, or None when none is declared.

    ``{"budget", "headroom", "target", "bytes", "pointers", "non_behavior", "proposals":
    [row...]}``, where ``target`` is the budget less the headroom; each proposal
    row carries ``name, bytes, type, lane, behavior, use, injected, opened, unsafe, young,
    age_days, evidence, after`` (the floor bytes once this and every earlier proposal is
    taken).
    Read-only. The replay runs only when the floor is over its target."""
    budget = floor_budget(memory_dir)
    if not budget:
        return None
    text = floor_text(memory_dir)
    if text is None:
        return None
    total = len(text.encode("utf-8"))
    rows = pointers(memory_dir, text)
    headroom = floor_headroom(memory_dir, budget)
    target = budget - headroom
    out = {"budget": budget, "headroom": headroom, "target": target, "bytes": total, "pointers": len(rows),
           "non_behavior": sum(1 for r in rows if not r["behavior"]), "proposals": []}
    if total <= target or not rows:
        return out
    from .lint_floor import trim_safety_report

    report = trim_safety_report(memory_dir, floor_text=text, names={r["name"] for r in rows},
                                k=REPLAY_K, max_queries=REPLAY_PROMPTS, pools_for=_replay_pools(memory_dir))
    safety = {r["name"]: r for r in report.get("rows") or []}
    now = time.time() if now is None else now
    uses, span = usage(memory_dir, [r["name"] for r in rows], now)
    born = births(memory_dir, [r["name"] for r in rows])
    need = max(span, float(MIN_AGE_DAYS))
    for r in rows:
        u = uses.get(r["name"]) or {"injected": 0, "opened": 0}
        s = safety.get(r["name"]) or {}
        when, dated_by = born.get(r["name"]) or (None, None)
        age = None if when is None else round(max(0.0, now - when) / 86400.0, 1)
        r.update(injected=u["injected"], opened=u["opened"], use=u["injected"] + u["opened"],
                 unsafe=_unsafe(s, report),
                 waiting=not s.get("lost") and _unsafe(s, report),
                 young=age is None or age < need, age_days=age, dated_by=dated_by, age_bar=need)
        lane = "behavior-lane" if r["behavior"] else "not behavior-lane"
        r["evidence"] = (f"floor diet: a {r['bytes']}B floor line, {lane} (type: {r['type']})"
                         f"{_riders_line(r['riders'])}; "
                         f"{_use_line(u, span)}{_age_line(r, span)}; {_safety_line(s, report)}")
    rows.sort(key=lambda r: (2 if r["unsafe"] else int(r["young"]), r["behavior"], r["use"],
                             -r["bytes"], r["name"]))
    left = total
    for r in rows:
        if left <= target:
            break
        left -= r["bytes"]
        out["proposals"].append(dict(r, after=left))
    return out


def queue_entries(memory_dir: str, make) -> List[dict]:
    """Tend's floor-kind demotion items, in proposal order. ``make`` is tend's entry
    builder ``(kind, target, evidence, proposed)``. ``[]`` for an undeclared corpus."""
    p = plan(memory_dir)
    if not p:
        return []
    out = []
    room = f" (the diet runs to {p['target']:,}B, so a new floor line still fits)" if p["headroom"] else ""
    for r in p["proposals"]:
        if r["waiting"]:
            proposed = ("wait: its trim-safety replay is not finished; run `hippo tend` again "
                        "before demoting it")
        elif r["unsafe"]:
            proposed = ("keep it: removing its line loses a recall hit (listed last); demote only "
                        "if you accept that")
        elif r["young"]:
            proposed = (f"wait: too new to judge on use; it is weighed with the rest once it is as "
                        f"old as the span its use is counted over ({r['age_bar']:g} days today); demote "
                        "it now only if you know it is not needed")
        else:
            proposed = (f"demote: remove its line from MEMORY.md (the memory stays, reachable by "
                        f"recall); the floor would be {r['after']:,}B of its {p['budget']:,}B budget{room}")
        out.append(make("floor", TARGET_PREFIX + r["name"], r["evidence"], proposed))
    return out


def detail_lines(name: str, memory_dir: str) -> List[str]:
    """What ``hippo tend show`` adds under a diet item: the literal floor line(s) a demotion
    removes, and the other pointers on them. ``[]`` when the floor has no such line."""
    from .lint_floor import memory_exists
    from .new_memory_floor import _without_pointer

    _text, gone = _without_pointer(floor_text(memory_dir) or "", name)
    out = [f"floor line: {ln}" for ln in gone]
    also = riders(gone, name, memory_exists(memory_dir))
    if also:
        out.append(f"also on this line: {', '.join(also)} (no line of their own; a demotion "
                   "takes them off the floor too)")
    return out


def demote(name: str, memory_dir: str, repo_root: str) -> dict:
    """``{"ok", "message"}``: remove ``name``'s floor pointer line, through the floor kind's
    write path (the outbox's floor copy when outbox mode is honoured, else the corpus dir's
    ``MEMORY.md``). Touches no memory file. The line goes whole, so the message names any
    other pointer that was on it."""
    from . import outbox_edit as OE
    from .lint_floor import memory_exists
    from .new_memory_floor import _remove_floor_pointer, _without_pointer

    floor_dir = OE.floor_dir_for_edit(memory_dir, repo_root)
    _text, gone = _without_pointer(floor_text(memory_dir) or "", name)
    also = riders(gone, name, memory_exists(memory_dir))
    r = _remove_floor_pointer(memory_dir, name, floor_dir=floor_dir)
    if r.get("status") != "removed":
        return {"ok": False, "message": f"{name}: the floor line was not removed ({r.get('reason')})."}
    budget = floor_budget(memory_dir)
    text = floor_text(memory_dir) or ""
    size = len(text.encode("utf-8"))
    where = ("in the outbox's floor copy (it ships with `hippo ship`)" if floor_dir
             else "in MEMORY.md, a working-tree change to commit")
    against = f" of its {budget:,}B budget" if budget else ""
    rode = "" if not also else (
        f" Its line also pointed at {', '.join(also)}, which left the floor with it "
        "(those memory files stay too).")
    return {"ok": True, "message": (
        f"demoted {name}: its floor line is gone {where}; the memory file stays and recall "
        f"still reaches it.{rode} The floor is now {size:,}B{against}.")}


def doctor_line(memory_dir: str) -> Optional[Dict[str, str]]:
    """K17's instrument: the floor against its declared budget, how many floor pointers are
    not behavior-lane, and the command that starts the diet (also shown when the floor is
    within its budget but inside the diet's headroom). Without a budget: a suggestion
    only when the floor is over hippo's warn line, else None (no line)."""
    budget = floor_budget(memory_dir)
    text = floor_text(memory_dir)
    if text is None:
        return None
    size = len(text.encode("utf-8"))
    if not budget:
        from .lint_floor import floor_governance

        warn = floor_governance(memory_dir).get("warn_bytes") or 0
        if not warn or size <= warn:
            return None
        return {"status": "warn", "message": (
            f"floor budget: none declared, and MEMORY.md is {size:,}B, over the {warn:,}B warn "
            f"line. Declare one in hippo.json (\"budgets\": {{\"floor_bytes\": {SUGGESTED_BUDGET}}} "
            "suits a floor imported into every session) and `hippo tend` proposes which floor "
            "lines to move out, one at a time.")}
    rows = pointers(memory_dir, text)
    nb = sum(1 for r in rows if not r["behavior"])
    lanes = f"{nb} of {len(rows)} floor pointer(s) are not behavior-lane"
    headroom = floor_headroom(memory_dir, budget)
    target = budget - headroom
    if size <= target:
        return {"status": "ok", "message": (
            f"floor budget: MEMORY.md {size:,}B, within its declared {budget:,}B "
            f"(budgets.floor_bytes); {lanes}.")}
    if size <= budget:
        return {"status": "ok", "message": (
            f"floor budget: MEMORY.md {size:,}B, within its declared {budget:,}B "
            f"(budgets.floor_bytes) with {budget - size:,}B to spare; {lanes}. The diet keeps "
            f"{headroom:,}B free so a new floor line still passes the gate: `{START_COMMAND}` "
            "proposes the next line to move out.")}
    runs_to = f" (it runs to {target:,}B, leaving {headroom:,}B for new floor lines)" if headroom else ""
    later = "" if replay_cached(memory_dir) else (
        " Which lines are safe to move is judged by a replay of recent prompts, which runs on "
        "`hippo tend`, not here.")
    return {"status": "warn", "message": (
        f"floor budget: MEMORY.md {size:,}B, over its declared {budget:,}B "
        f"(budgets.floor_bytes) by {size - budget:,}B; {lanes}. Start the diet: "
        f"`{START_COMMAND}`{runs_to}.{later}")}


def check_floor_budget(ctx) -> Optional[Dict[str, str]]:
    """Doctor's row (``doctor.CHECKS``). None prints no line."""
    try:
        return doctor_line(ctx.memory_dir)
    except Exception as exc:
        return {"status": "ok", "message": f"floor budget: check skipped ({exc})."}
