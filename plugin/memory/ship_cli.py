"""OUT-2: ``hippo ship`` — preview by default; ``--yes`` acts. See ``ship.py`` for the rules.

  hippo ship                    preview: what would ship, what waits, what is stale or held
  hippo ship --yes              one commit on one new branch off the default-branch ref
  hippo ship --drop <name>      remove a stale outbox copy (its row and hash record too)
  hippo ship --mine <name>      keep a stale copy: its base becomes the committed version
  --base <ref>  --branch <name>  --json

Exit codes: 0 for a preview, a ship, or nothing to ship; 1 when ``--yes``, ``--drop`` or
``--mine`` was refused. No network, ever.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import ship as S


def _names(items: List[dict]) -> str:
    return ", ".join(i["name"] for i in items)


def render(plan: dict, *, gate: Optional[dict] = None, done: Optional[dict] = None) -> str:
    if plan.get("error"):
        return f"hippo ship: {plan['error']}."
    b = plan["base"]
    lines = [f"hippo ship: base {b['ref']} at {b['short']} ({b['date']}); {S.FRESHNESS_LINE}."]
    if not plan["trusted"]:
        lines.append("this corpus is not trusted on this machine, so nothing in its outbox ships.")
    lines.append(S.counts_line(plan) + ".")
    if plan["new"]:
        lines.append("to ship: " + ", ".join(f"{i['name']} ({'edit' if i['edit'] else 'new'})" for i in plan["new"]))
    for w in plan["waiting"]:
        lines.append(f"waiting: {w['name']} is in flight on {w['branch']}")
    for it in plan["landed"]:
        lines.append(f"landed: {it['name']} is on {b['ref']} and in this checkout"
                     + (" (cleared from the outbox)" if done else "; `hippo ship --yes` clears its outbox copy"))
    for it in plan["landed_unpulled"]:
        lines.append(f"landed: {it['name']} is on {b['ref']}; kept until this checkout pulls it")
    for it in plan["stale"]:
        lines.append(f"stale: {it['name']}: {it['why']}; it is not shipped and never overwrites the committed copy.")
        lines.append(it["diff"].rstrip("\n"))
        lines.append(f"  keep the outbox copy as an edit of the current version: hippo ship --mine {it['stem']}")
        lines.append(f"  or drop the outbox copy: hippo ship --drop {it['stem']}")
    for it in plan["held"]:
        lines.append(f"held: {it['name']}: {'; '.join(it['classes'])}. Not shipped; fix or remove it.")
    for it in plan["ignored"]:
        lines.append(f"ignored: {it['name']} ({it['why']}); only memory files directly in the outbox ship.")
    fl = plan.get("floor")
    if fl:
        if fl["state"] == "new":
            lines.append(f"floor: MEMORY.md is replayed onto {b['ref']}'s floor: {fl['adds']} pointer(s) added, "
                         f"{fl['removes']} removed (never committed whole).")
        elif fl["state"] == "stale":
            lines.append(f"stale: MEMORY.md: {fl['why']}; it is not replayed.")
            lines.append(fl["diff"].rstrip("\n"))
            lines.append("  drop the outbox floor copy: hippo ship --drop MEMORY")
        elif fl["state"] == "waiting":
            lines.append(f"floor: its pointer changes are in flight on {fl['branch']}")
        else:
            lines.append(f"floor: every pointer change is on {b['ref']}"
                         + ("" if fl["state"] == "landed" else "; kept until this checkout pulls it"))
    if gate and gate.get("findings"):
        from .gate_cli import render as render_gate

        lines.append(render_gate(gate, "this ship").replace("hippo gate: ", "gate: "))
        lines.append("`hippo ship --yes` refuses while that stands (the corpus's own committed policy).")
    if done:
        if done.get("branch"):
            lines.append(f"shipped: commit {done['commit'][:9]} on new branch {done['branch']} "
                         f"(off {b['ref']}). Nothing was pushed: push it and open the pull request yourself.")
            lines.append(S.HOOKS_LINE + ".")
        elif done.get("error"):
            lines.append(f"refused: {done['error']}. Nothing was created.")
        else:
            lines.append("nothing to ship; no branch was created.")
        for p in done.get("problems") or []:
            lines.append(f"warning: {p}")
    elif S.counts_line(plan).startswith("0 to ship,"):
        lines.append("nothing to ship.")
    else:
        lines.append("preview only: nothing was written. `hippo ship --yes` creates the branch; " + S.HOOKS_LINE + ".")
    return "\n".join(lines)


def _json(plan: dict, gate=None, done=None) -> str:
    strip = lambda items: [{k: v for k, v in i.items() if k != "path"} for i in items]  # noqa: E731
    out = {k: (strip(plan[k]) if isinstance(plan.get(k), list) else plan.get(k))
           for k in ("error", "base", "rel", "trusted", "new", "waiting", "stale", "held", "landed",
                     "landed_unpulled", "ignored")}
    fl = plan.get("floor")
    out["floor"] = {k: v for k, v in fl.items() if k not in ("replayed", "path")} if fl else None
    out["counts"] = S.counts_line(plan) if not plan.get("error") else None
    out["gate_findings"] = (gate or {}).get("findings") or []
    out["result"] = done
    return json.dumps(out, indent=2, sort_keys=True)


def _stale_item(plan: dict, name: str) -> Optional[dict]:
    stem = name[:-3] if name.endswith(".md") else name
    if stem == "MEMORY":
        fl = plan.get("floor")
        return {"name": "MEMORY.md", "stem": "MEMORY", "diff": fl["diff"]} if fl and fl["state"] == "stale" else None
    return next((i for i in plan["stale"] if i["stem"] == stem), None)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="hippo ship", description="Batch the outbox into one commit on one new branch (preview first).")
    ap.add_argument("--yes", action="store_true", help="create the commit and the branch")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--base", default=None, help="the ref to branch from (default: the local default-branch ref)")
    ap.add_argument("--branch", default=None, help="the new branch's name (default: hippo/ship-<UTC time>)")
    act = ap.add_mutually_exclusive_group()
    act.add_argument("--drop", metavar="NAME", help="remove one stale outbox copy")
    act.add_argument("--mine", metavar="NAME", help="keep one stale copy as an edit of the committed version")
    ap.add_argument("--memory-dir", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    from .provenance import resolve_dirs

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md
    if args.memory_dir:
        repo = None  # the trust gate resolves the corpus's own git root
    plan = S.build_plan(memory_dir, repo, args.base)
    emit = (lambda p, g=None, d=None: print(_json(p, g, d))) if args.json else (lambda p, g=None, d=None: print(render(p, gate=g, done=d)))
    if plan.get("error"):
        emit(plan)
        return 1 if (args.yes or args.drop or args.mine) else 0
    if args.drop or args.mine:
        name = args.drop or args.mine
        it = _stale_item(plan, name)
        if it is None:
            print(f"hippo ship: {name} is not stale now; --drop and --mine only take an item a preview showed as stale.")
            return 1
        print(it["diff"].rstrip("\n"))
        if args.drop:
            why = S.drop_item(memory_dir, it["name"], repo)
            print(f"hippo ship --drop: {why}." if why else f"hippo ship --drop: removed the outbox copy of {it['name']}; the corpus dir is untouched.")
            return 1 if why else 0
        if it["stem"] == "MEMORY":
            print("hippo ship --mine: the floor is a replay, not a file; drop its copy instead.")
            return 1
        ok = S.set_base(memory_dir, it["name"], it["committed_sha256"])
        print(f"hippo ship --mine: {it['name']} now ships as an edit of the committed version."
              if ok else "hippo ship --mine: the outbox manifest could not be written; nothing changed.")
        return 0 if ok else 1
    n_ship = len(plan["new"]) + (1 if plan.get("floor") and plan["floor"]["state"] == "new" else 0)
    built = S.build_commit(plan, write=False) if n_ship and plan["trusted"] else None
    gate = (built or {}).get("gate")
    if not args.yes:
        emit(plan, gate)
        return 0
    if not plan["trusted"]:
        emit(plan, gate, {"error": "this corpus is not trusted on this machine, so nothing in its outbox ships",
                          "branch": None, "commit": None, "dropped": [], "problems": []})
        return 1
    dropped, problems = S.land(memory_dir, plan, repo)
    done = {"dropped": dropped, "problems": problems, "branch": None, "commit": None, "error": None}
    if not n_ship:
        emit(plan, gate, done)
        return 0
    if not plan["trusted"]:
        done["error"] = "this corpus is not trusted on this machine, so nothing in its outbox ships"
    elif built and built.get("error"):
        done["error"] = built["error"]
    elif gate and gate.get("findings"):
        done["error"] = "the corpus's committed gate policy has a finding against this ship"
    if done["error"]:
        emit(plan, gate, done)
        return 1
    real = S.build_commit(plan, write=True)
    if real.get("error") or not real.get("commit"):
        done["error"] = real.get("error") or "the commit could not be created"
        emit(plan, gate, done)
        return 1
    branch = args.branch or S.default_branch_name()
    why = S.create_branch(plan["top"], branch, real["commit"])
    if why:
        done["error"] = why
        emit(plan, gate, done)
        return 1
    fl = plan.get("floor")
    if not S.stamp_shipped(memory_dir, plan["new"], branch, real["commit"],
                           fl["sha256"] if fl and fl["state"] == "new" else None):
        problems.append("the outbox manifest could not record the ship; the next run still reads "
                        f"these files as in flight on {branch}")
    done.update(branch=branch, commit=real["commit"])
    emit(plan, gate, done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
