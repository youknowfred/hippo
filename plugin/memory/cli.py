"""SRF-1: ``hippo <verb>`` — the one engine entry.

``bin/hippo`` resolves the interpreter (the shared ``hooks/_resolve_py.sh``) and hands
every verb but ``mcp`` to this module. It looks the verb up in ``cli_verbs.CLI_VERBS``
and runs that module as ``__main__`` with the remaining arguments, which is exactly what
``python -m memory.<module>`` did: the same argv parsing, output and exit code. Skills,
hooks and printed hints therefore spell one thing, ``hippo <verb> ...``, and the module
layout behind it can move without touching them.

Usage text is generated from the table, so it cannot drift from what dispatches. An
unknown verb prints it to stderr and exits 2. ``init``, ``bootstrap`` and ``audit`` are
multi-step skills; they name the skill to run and exit 1.

OBS-2: a verb typed into a shell counts one ``cli`` use in the corpus's usage spool, the
same line ``hippo_note_usage`` appends from bash. Hooks call verbs with
``HIPPO_SURFACE=hook`` and count their own path, so they are not counted twice.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from typing import List, Optional

from .cli_verbs import CLI_VERBS, SKILL_REDIRECTS, verb_table


def usage(all_verbs: bool = False) -> str:
    rows = [v for v in CLI_VERBS if all_verbs or not v.internal]
    width = max(len(v.verb) for v in rows)
    lines = ["usage: hippo <verb> [args...]", ""]
    lines += [f"  {v.verb.ljust(width)}  {v.summary}" for v in rows]
    lines += [
        "",
        f"  {', '.join(SKILL_REDIRECTS)}: multi-step skills, run as /hippo:<name>",
        "  hippo <verb> --help shows a verb's own options.",
    ]
    return "\n".join(lines) + "\n"


def _telemetry_dir() -> Optional[str]:
    """Where bash's ``hippo_note_usage`` would write: the override, this tree's ledger
    dir, or a linked worktree's main tree. Only an existing dir counts."""
    override = os.environ.get("HIPPO_TELEMETRY_DIR")
    if override:
        return override if os.path.isdir(override) else None
    if os.path.isdir(os.path.join(".claude", "memory")):
        td = os.path.join(".claude", ".memory-telemetry")
        return td if os.path.isdir(td) else None
    if os.path.isfile(".git"):
        try:
            common = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except Exception:
            return None
        if common and os.path.basename(common) == ".git":
            main = os.path.dirname(common)
            if os.path.isdir(os.path.join(main, ".claude", "memory")):
                td = os.path.join(main, ".claude", ".memory-telemetry")
                return td if os.path.isdir(td) else None
    return None


def _note_cli_use(verb: str) -> None:
    """OBS-2: one spool line per verb typed into a shell. Never raises."""
    if os.environ.get("HIPPO_SURFACE") == "hook":
        return
    try:
        td = _telemetry_dir()
        if not td:
            return
        line = {
            "surface": "cli",
            "verb": verb,
            "action": "",
            "client": os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "unknown",
        }
        with open(os.path.join(td, "usage_spool.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(usage())
        return 2
    verb, rest = args[0], args[1:]
    if verb in ("help", "-h", "--help"):
        sys.stdout.write(usage(all_verbs="--all" in rest))
        return 0
    if verb in SKILL_REDIRECTS:
        sys.stderr.write(
            f"hippo: '{verb}' is a multi-step skill, not a bare CLI command — "
            f"use /hippo:{verb} instead.\n"
        )
        return 1
    row = verb_table().get(verb)
    if row is None:
        sys.stderr.write(f"hippo: unknown verb '{verb}'\n" + usage())
        return 2
    _note_cli_use(verb)
    sys.argv = [f"hippo {verb}"] + rest
    try:
        runpy.run_module(f"memory.{row.module}", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        sys.stderr.write(f"{code}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
