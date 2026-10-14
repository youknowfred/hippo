"""TND-2: the ``tend`` verb — work the one maintenance queue, one item at a time.

``hippo tend`` lists the derived queue (``tend_queue``); ``next`` shows the top item with
its evidence and the verdicts it accepts; ``apply`` executes ONE verdict through the
engine call that already owns that kind of write; ``snooze``, ``skip``, ``hold`` and
``release`` manage the operator's tend state. The same engine serves the ``tend`` MCP tool
and the ``/hippo:tend`` skill.

Every per-item gate is preserved, because ``apply`` only routes:
  - capture        discard or done (a memory drafted from it goes through ``hippo new``,
                   which runs its own duplicate check, before ``done`` drains the seed)
  - reverify       graduate | fix | demote | snooze | archive (``reconsolidate`` and
                   ``archive``, unchanged)
  - contradiction  keep_one | merge | scope_both | not_conflicting (``resolve_view``)
  - merge          supersede (demote the loser under the winner) | updated | distinct
  - baseline       rebaseline (the graduate re-baseline: confirm, then move the baseline)
  - link, floor    done (re-checked: refused while the problem is still there, fixed once
                   the source no longer reports it)
  - derivation     apply (re-derive ONE memory) | stamp (earned: refused while any
                   memory still derives differently)
  - trust          routed to the consent review (re-consent is its own gate)

There is no apply-all. ``hold`` is an owner decision with a reason (for example, keeping
an old citation derivation on purpose); held items count as resolved.
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Dict, List, Optional, Tuple

from . import tend_queue as Q

VERDICTS: Dict[str, Tuple[str, ...]] = {
    "trust": ("grant",),
    "baseline": ("rebaseline",),
    "contradiction": ("keep_one", "merge", "scope_both", "not_conflicting"),
    "merge": ("supersede", "updated", "distinct"),
    "capture": ("discard", "done"),
    "reverify": ("graduate", "fix", "demote", "snooze", "archive"),
    "link": ("done",),
    "floor": ("done",),
    "derivation": ("apply", "stamp"),
}

_DEFAULT_SNOOZE_DAYS = 7


def _dirs(memory_dir: Optional[str], repo_root: Optional[str]) -> Tuple[str, str]:
    if memory_dir and repo_root:
        return memory_dir, repo_root
    from .provenance import resolve_dirs

    md, rr = resolve_dirs()
    return memory_dir or md, repo_root or rr


def find_entry(memory_dir: str, repo_root: str, entry_id: str) -> Tuple[Optional[dict], Optional[str]]:
    """``(entry, state)`` where state is ``pending``/``held``/``snoozed``/``skipped``, or
    ``(None, None)`` when the id is not in the queue now."""
    kind = entry_id.split(":", 1)[0]
    if kind not in Q.KINDS:
        return None, None
    r = Q.build_queue(memory_dir, repo_root, kinds=(kind,), write_cache=False)
    for bucket in ("pending", "held", "snoozed", "skipped"):
        for e in r[bucket]:
            if e["id"] == entry_id:
                return e, bucket
    return None, None


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_list(result: dict, limit: int = 15) -> str:
    total = Q.total_pending(result)
    counts = result["counts"]
    held = sorted({e["kind"] for e in result["held"]})
    if total == 0:
        lines = ["Maintenance queue: empty — nothing needs a decision."]
    else:
        parts = ", ".join(f"{counts[k]} {k}" for k in Q.KINDS if counts.get(k))
        lines = [f"Maintenance queue: {total} item(s) need a decision ({parts}).", ""]
        for e in result["pending"][:limit]:
            lines.append(f"  {e['id']}")
            lines.append(f"      {e['evidence']}")
            lines.append(f"      likely: {e['proposed']}")
        if total > limit:
            lines.append(f"  …and {total - limit} more.")
        lines += ["", "Next: `hippo tend next` shows the top item with its evidence and verdicts."]
    if held:
        reasons = []
        for kind in held:
            h = next(e["hold"] for e in result["held"] if e["kind"] == kind)
            reasons.append(f"{kind} ({h.get('reason') or 'no reason given'})")
        lines.append(f"Held by the owner (counted as resolved): {'; '.join(reasons)}.")
    hidden = len(result["snoozed"]) + len(result["skipped"])
    if hidden:
        lines.append(f"{hidden} item(s) snoozed or skipped.")
    for kind, err in sorted(result["errors"].items()):
        lines.append(f"⚠ the {kind} source failed and is not listed: {err}")
    return "\n".join(lines)


def _detail_lines(entry: dict, memory_dir: str, repo_root: str) -> List[str]:
    kind, target = entry["kind"], entry["target"]
    try:
        if kind == "capture":
            from .capture_queue import _format_listing, read_pending

            seeds = [s for s in read_pending(memory_dir=memory_dir)
                     if os.path.basename(s.get("_path", "")) == target]
            return _format_listing(seeds).splitlines() if seeds else []
        if kind == "reverify":
            from .reconsolidate_brief import brief_for_name, render_brief

            brief = brief_for_name(target, memory_dir, repo_root)
            return render_brief(brief) if brief else []
        if kind == "contradiction":
            from .resolve_evidence import render_pair_evidence
            from .resolve_view import _description_of, pair_evidence

            a, b = target.split("|", 1)
            lines = [f"{side}: {_description_of(memory_dir, side)}" for side in (a, b)]
            return lines + render_pair_evidence(a, b, pair_evidence(a, b, memory_dir, repo_root))
        if kind == "merge":
            from .resolve_view import _description_of

            a, b = target.split("|", 1)
            return [f"{side}: {_description_of(memory_dir, side)}" for side in (a, b)]
        if kind == "derivation" and target != "corpus":
            from .provenance import build_repo_file_index, rederive_one_lines, rederive_preview

            repo_files, index = build_repo_file_index(repo_root)
            preview = rederive_preview(os.path.join(memory_dir, f"{target}.md"), repo_root, repo_files, index)
            return rederive_one_lines(f"{target}.md", preview, dry_run=True)
    except Exception as exc:
        return [f"(evidence unavailable: {exc})"]
    return []


def _verdict_help(kind: str, entry_id: str) -> List[str]:
    v = VERDICTS[kind]
    if kind == "trust":
        return ["Re-consent is its own gate: `hippo trust review` shows each change against the "
                "consented version, then `hippo trust grant` with the digest it prints (the trust "
                "MCP tool on Desktop)."]
    lines = [f"Verdicts: {' | '.join(v)}", f"  hippo tend apply {entry_id} --verdict <verdict>"]
    if kind == "contradiction":
        lines.append("  keep_one and merge also take --winner <name> --loser <name>.")
    if kind == "merge":
        lines.append("  supersede takes --winner <name> (the other side is demoted under it).")
    if kind == "reverify":
        lines.append("  demote may take --superseded-by <name>; fix means you already edited the body.")
    if kind == "capture":
        lines.append("  done means you already wrote what was durable with `hippo new` (check first).")
    if kind in ("link", "floor"):
        lines.append("  done means you already edited the file; tend re-checks before clearing it.")
    lines.append(f"Or: hippo tend snooze {entry_id} | hippo tend skip {entry_id}")
    return lines


def render_next(entry: Optional[dict], memory_dir: str, repo_root: str, remaining: int) -> str:
    if entry is None:
        return "Maintenance queue: empty — nothing needs a decision."
    lines = [
        f"Next ({remaining} pending): {entry['id']}",
        f"  why: {entry['evidence']}",
        f"  likely verdict: {entry['proposed']}",
        f"  gate: {entry['gate']}",
    ]
    detail = _detail_lines(entry, memory_dir, repo_root)
    if detail:
        lines += ["", *[f"  {d}" for d in detail]]
    lines += ["", *_verdict_help(entry["kind"], entry["id"])]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Apply — one verdict, routed to the engine call that owns the write
# --------------------------------------------------------------------------- #
def _ok(msg: str) -> dict:
    return {"ok": True, "message": msg}


def _refused(msg: str) -> dict:
    return {"ok": False, "message": msg}


def _apply_reverify(name, verdict, memory_dir, repo_root, superseded_by):
    if verdict == "snooze":
        from .reconsolidate import snooze

        r = snooze(name, memory_dir)
        return _refused(r.get("error")) if r.get("error") else _ok(f"snoozed {name}: off the worklist for a while.")
    if verdict == "archive":
        from .archive import archive_memory

        r = archive_memory(name, memory_dir, repo_root)
        if not r.get("moved"):
            return _refused(r.get("error") or "not archived")
        return _ok(f"archived {name} (restore with `hippo archive --restore {name}`).")
    from .reconsolidate import semantic_reverify

    r = semantic_reverify(name, verdict, memory_dir, repo_root, superseded_by=superseded_by)
    if r.get("error"):
        return _refused(f"{name}: {r['error']}")
    return _ok(f"{verdict} {name}: recorded.")


def _apply_capture(target, verdict, memory_dir):
    from .capture_queue import default_pending_dir, discard_pending

    path = os.path.join(default_pending_dir(memory_dir), target)
    if not os.path.isfile(path):
        return _refused(f"no pending capture named {target}")
    if not discard_pending(path, drafted=verdict == "done", memory_dir=memory_dir):
        return _refused(f"could not remove {target} from the pending queue")
    what = "drained after drafting" if verdict == "done" else "discarded"
    return _ok(f"{target}: {what}.")


def _apply_contradiction(target, verdict, memory_dir, repo_root, winner, loser):
    from .resolve_view import apply_resolve_verdict

    a, b = target.split("|", 1)
    if verdict in ("keep_one", "merge"):
        if {winner, loser} != {a, b}:
            return _refused(f"{verdict} needs --winner and --loser naming {a} and {b}")
        r = apply_resolve_verdict(memory_dir, repo_root, verdict, winner=winner, loser=loser)
    else:
        r = apply_resolve_verdict(memory_dir, repo_root, verdict, a=a, b=b)
    if r.get("error") or not r.get("applied"):
        return _refused(r.get("error") or "the verdict was not applied")
    return _ok(f"{verdict} {a} ⇄ {b}: applied.")


def _apply_merge(entry, verdict, memory_dir, repo_root, winner):
    a, b = entry["target"].split("|", 1)
    if verdict == "supersede":
        if winner not in (a, b):
            return _refused(f"supersede needs --winner naming {a} or {b}")
        loser = b if winner == a else a
        return _apply_reverify(loser, "demote", memory_dir, repo_root, winner)
    _mark_done(memory_dir, entry)
    return _ok(f"{a} ⇄ {b}: recorded as {verdict}.")


def _apply_recheck(entry, memory_dir, repo_root):
    """link/floor ``done``: clear only what the source no longer reports."""
    still = [e for e in Q.SOURCES[entry["kind"]](memory_dir, repo_root) if e["id"] == entry["id"]]
    if still:
        return _refused(f"{entry['id']} is still reported: {still[0]['evidence']}. Edit the file first.")
    return _ok(f"{entry['id']}: fixed.")


def _apply_derivation(target, verdict, memory_dir, repo_root):
    from .provenance import build_repo_file_index, rederive_file, rederive_worklist
    from .provenance_format import CITATION_DERIVATION_VERSION, read_cite_derivation, write_cite_derivation

    if verdict == "stamp":
        work = rederive_worklist(memory_dir, repo_root)
        if work:
            return _refused(f"stamp refused: {len(work)} memory(ies) still re-derive differently.")
        was = read_cite_derivation(memory_dir)
        if was >= CITATION_DERIVATION_VERSION:
            return _ok(f"already stamped at derivation {was}.")
        if not write_cite_derivation(memory_dir):
            return _refused("could not write the corpus marker.")
        return _ok(f"stamped citation derivation {was} → {CITATION_DERIVATION_VERSION}.")
    if target == "corpus":
        return _refused("the corpus entry takes --verdict stamp.")
    repo_files, index = build_repo_file_index(repo_root)
    r = rederive_file(os.path.join(memory_dir, f"{target}.md"), repo_root, repo_files, index)
    if r.get("error"):
        return _refused(f"{target}: {r['error']}")
    return _ok(f"re-derived {target}'s citations.")


def apply(
    entry_id: str,
    verdict: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    winner: Optional[str] = None,
    loser: Optional[str] = None,
    superseded_by: Optional[str] = None,
) -> dict:
    """Execute ONE verdict on ONE queue item. ``{"ok", "message"}``. Never raises."""
    memory_dir, repo_root = _dirs(memory_dir, repo_root)
    try:
        entry, state = find_entry(memory_dir, repo_root, entry_id)
        kind = entry_id.split(":", 1)[0]
        if entry is None and kind in ("link", "floor"):
            # A hand edit drops the item from the queue: re-check it, and answer fixed if gone.
            entry = {"kind": kind, "id": entry_id, "target": entry_id.split(":", 1)[-1]}
        if entry is None:
            return _refused(f"{entry_id} is not in the maintenance queue now (already resolved?).")
        kind, target = entry["kind"], entry["target"]
        if verdict not in VERDICTS[kind]:
            return _refused(f"{kind} items take: {', '.join(VERDICTS[kind])}")
        if kind == "trust":
            return _refused("re-consent is its own gate: `hippo trust review`, then "
                            "`hippo trust grant` with the digest it prints.")
        if kind == "reverify":
            r = _apply_reverify(target, verdict, memory_dir, repo_root, superseded_by)
        elif kind == "baseline":
            r = _apply_reverify(target, "graduate", memory_dir, repo_root, None)
        elif kind == "capture":
            r = _apply_capture(target, verdict, memory_dir)
        elif kind == "contradiction":
            r = _apply_contradiction(target, verdict, memory_dir, repo_root, winner, loser)
        elif kind == "merge":
            r = _apply_merge(entry, verdict, memory_dir, repo_root, winner)
        elif kind in ("link", "floor"):
            r = _apply_recheck(entry, memory_dir, repo_root)
        else:
            r = _apply_derivation(target, verdict, memory_dir, repo_root)
        if r["ok"]:
            _count(memory_dir, kind, verdict)
        return r
    except Exception as exc:
        return _refused(f"{entry_id}: {type(exc).__name__}: {exc}")


def _count(memory_dir: str, kind: str, verdict: str) -> None:
    """OBS-2: one drained item per apply, keyed by kind and verdict. Never raises."""
    try:
        from .telemetry_rollup import record_usage

        td = Q._telemetry_dir(memory_dir)
        if os.path.isdir(td):
            record_usage(td, surface="tend", verb=kind, action=verdict,
                         client=(os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").strip() or None)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Tend state writes (gitignored)
# --------------------------------------------------------------------------- #
def _write_state(memory_dir: str, state: dict) -> bool:
    from .atomic import write_json_atomic

    td = Q._telemetry_dir(memory_dir)
    try:
        os.makedirs(td, exist_ok=True)
        write_json_atomic(Q.state_path(memory_dir), state)
        return True
    except Exception:
        return False


def _mark_done(memory_dir: str, entry: dict) -> None:
    """A resolved-by-hand item (merge updated/distinct): skip it until its evidence moves."""
    state = Q.read_state(memory_dir)
    state["skip"][entry["id"]] = Q.evidence_hash(entry)
    _write_state(memory_dir, state)


def snooze(entry_id: str, days: float = _DEFAULT_SNOOZE_DAYS, *, memory_dir=None, repo_root=None) -> dict:
    memory_dir, repo_root = _dirs(memory_dir, repo_root)
    entry, _ = find_entry(memory_dir, repo_root, entry_id)
    if entry is None:
        return _refused(f"{entry_id} is not in the maintenance queue now.")
    state = Q.read_state(memory_dir)
    state["snooze"][entry_id] = time.time() + days * 86400
    if not _write_state(memory_dir, state):
        return _refused("could not write the tend state.")
    return _ok(f"snoozed {entry_id} for {days:g} day(s).")


def skip(entry_id: str, *, memory_dir=None, repo_root=None) -> dict:
    memory_dir, repo_root = _dirs(memory_dir, repo_root)
    entry, _ = find_entry(memory_dir, repo_root, entry_id)
    if entry is None:
        return _refused(f"{entry_id} is not in the maintenance queue now.")
    _mark_done(memory_dir, entry)
    return _ok(f"skipped {entry_id} until its evidence changes.")


def hold(key: str, reason: str, *, memory_dir=None, repo_root=None) -> dict:
    """An owner hold on a whole kind (``derivation``) or one item id. Needs a reason."""
    memory_dir, repo_root = _dirs(memory_dir, repo_root)
    if key not in Q.KINDS and key.split(":", 1)[0] not in Q.KINDS:
        return _refused(f"hold takes a kind ({', '.join(Q.KINDS)}) or an item id.")
    if not (reason or "").strip():
        return _refused("a hold needs --reason: it is an owner decision, recorded with its why.")
    state = Q.read_state(memory_dir)
    state["hold"][key] = {"reason": reason.strip(), "at": time.strftime("%Y-%m-%d")}
    if not _write_state(memory_dir, state):
        return _refused("could not write the tend state.")
    return _ok(f"held {key}: {reason.strip()}")


def release(key: str, *, memory_dir=None, repo_root=None) -> dict:
    memory_dir, repo_root = _dirs(memory_dir, repo_root)
    state = Q.read_state(memory_dir)
    if state["hold"].pop(key, None) is None:
        return _refused(f"nothing held under {key}.")
    if not _write_state(memory_dir, state):
        return _refused("could not write the tend state.")
    return _ok(f"released {key}: its items are back in the queue.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="hippo tend", description=__doc__.split("\n\n")[0])
    p.add_argument("action", nargs="?", default="list",
                   choices=("list", "next", "show", "apply", "snooze", "skip", "hold", "release"))
    p.add_argument("target", nargs="?", default=None, help="an item id, or a kind for hold/release")
    p.add_argument("--verdict", default=None)
    p.add_argument("--winner", default=None)
    p.add_argument("--loser", default=None)
    p.add_argument("--superseded-by", default=None)
    p.add_argument("--days", type=float, default=_DEFAULT_SNOOZE_DAYS)
    p.add_argument("--reason", default=None)
    p.add_argument("--kind", default=None, help="limit list/next to one kind")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--memory-dir", default=None)
    p.add_argument("--repo-root", default=None)
    args = p.parse_args(argv)
    memory_dir, repo_root = _dirs(args.memory_dir, args.repo_root)
    kinds = (args.kind,) if args.kind in Q.KINDS else None

    if args.action in ("list", "next"):
        result = Q.build_queue(memory_dir, repo_root, kinds=kinds)
        if args.action == "list":
            print(render_list(result, args.limit))
        else:
            top = result["pending"][0] if result["pending"] else None
            print(render_next(top, memory_dir, repo_root, Q.total_pending(result)))
        return 0
    if not args.target:
        print(f"hippo tend {args.action}: needs an item id (see `hippo tend`).")
        return 2
    if args.action == "show":
        entry, state = find_entry(memory_dir, repo_root, args.target)
        if entry is None:
            print(f"{args.target} is not in the maintenance queue now.")
            return 1
        print(render_next(entry, memory_dir, repo_root, 1) + ("" if state == "pending" else f"\n  ({state})"))
        return 0
    if args.action == "apply":
        if not args.verdict:
            print("hippo tend apply: needs --verdict (see `hippo tend show <id>`).")
            return 2
        r = apply(args.target, args.verdict, memory_dir=memory_dir, repo_root=repo_root,
                  winner=args.winner, loser=args.loser, superseded_by=args.superseded_by)
    elif args.action == "snooze":
        r = snooze(args.target, args.days, memory_dir=memory_dir, repo_root=repo_root)
    elif args.action == "skip":
        r = skip(args.target, memory_dir=memory_dir, repo_root=repo_root)
    elif args.action == "hold":
        r = hold(args.target, args.reason or "", memory_dir=memory_dir, repo_root=repo_root)
    else:
        r = release(args.target, memory_dir=memory_dir, repo_root=repo_root)
    print(r["message"] if r["ok"] else f"refused — {r['message']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
