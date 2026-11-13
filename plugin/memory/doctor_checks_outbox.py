"""OUT-1's doctor line: where saves land, and what is waiting in the outbox.

One line, read-only. ``warn`` when ``hippo.json``'s ``write_path`` is a value hippo does not
know, when the corpus asks for the outbox and this machine has not accepted that yet, when
git tracks anything under the outbox, or when recall withholds an outbox file. The counts are
reported whatever ``hippo.json`` says now: turning the switch off never hides the machine's
own unshipped memories. ``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

from typing import Dict, List

from .doctor_checks_env import DoctorContext


def check_outbox(ctx: DoctorContext) -> Dict[str, str]:
    try:
        from . import outbox as OB

        md, rr = ctx.memory_dir, ctx.repo_root
        parts: List[str] = []
        warn = False
        unknown = OB.unknown_write_path(md)
        held = OB.held_back_line(md, rr)
        if unknown is not None:
            warn = True
            parts.append(
                f"hippo.json sets write_path to {unknown!r}, which hippo does not know (it takes "
                '"outbox" or "direct"), so saves land in the corpus dir'
            )
        elif held:
            warn = True
            parts.append(held)
        elif OB.write_target(md, rr) == "outbox":
            parts.append(f"saves land in the outbox ({OB.outbox_dir(md)}), not the corpus dir")
        st = OB.status(md, rr)
        if st["exists"]:
            n = st["new"] + st["edited"]
            pieces = [f"{st['new']} new", f"{st['edited']} edited copies"]
            if st["floor_pending"]:
                pieces.append("floor change pending")
            parts.append(
                f"{n} memor{'y' if n == 1 else 'ies'} waiting in the outbox ({st['dir']}): "
                f"{', '.join(pieces)}; recall sees them; they are not in git until they are moved "
                "into the corpus dir (copy each file into it, commit, delete the outbox copy, then "
                "consent the new files with `hippo trust review`)"
            )
            if st["withheld"]:
                warn = True
                parts.append(
                    f"{st['withheld']} outbox file(s) withheld from recall: hippo did not write "
                    "their current bytes on this machine (pulled, or edited outside hippo); read "
                    "them with `hippo trust review`, then grant them there or delete them"
                )
            if st["tracked_by_git"]:
                warn = True
                parts.append(
                    f"{st['tracked_by_git']} path(s) under the outbox are tracked by git, and it "
                    f"must never be committed: `git rm -r --cached {st['dir']}` stops tracking them"
                )
        if not parts:
            parts.append("off — saves land in the corpus dir")
        return {"status": "warn" if warn else "ok", "message": "outbox: " + "; ".join(parts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"outbox check failed: {exc}."}
