"""GAT-1: the PreToolUse write gate — judge a file-tool write into the corpus before it lands.

``memory_pre_tool.sh`` hands this module the PreToolUse payload only when the write's path
names a memory file (its bash fast path costs no interpreter start on every other write).
Here the write's effect is simulated (Write: the content; Edit: ``old_string`` ->
``new_string``, ``replace_all`` honoured; MultiEdit: its edits in order), the before/after
pair is judged by ``gate.judge_file``, and the result is at most one ``hookSpecificOutput``:

- **warn** (the default): ``additionalContext`` naming each budget, its before and after,
  and the way out. The write goes ahead; the model reads the line beside the tool result.
- **deny**: ``permissionDecision: "deny"`` with the same text as the reason, which the
  harness shows the model. Only when ALL of: the corpus's ``hippo.json`` declares
  ``"gate": {"write": "deny"}`` and this machine accepted that value
  (``policy_consent.honoured``: declared, the corpus trusted here, ``hippo trust policy
  --accept`` run on the current value); the COMMITTED ``hippo.json`` at HEAD says the same
  (an uncommitted local edit cannot turn denial on; the one git call runs only when a
  denial is otherwise about to happen); and the checker found a worsening.
- ``"gate": {"write": "off"}`` silences the check for that corpus. Turning the gate down is
  the safe direction, so "off" needs no acceptance. Any other value behaves as "warn"
  (doctor names it).

Which writes are corpus writes: the target's REAL path (symlinks resolved, so the
harness's native-memory path ``~/.claude/projects/<key>/memory/…`` counts) must sit
directly in the resolved corpus dir, or in a ``.claude/memory`` inside the tree this
session works in (a linked worktree's own copy, judged under its own policy file). Any
error or ambiguity (an ``old_string`` the tool will not find, an unreadable file, a
malformed payload) says nothing. The process always exits 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import List, Optional

WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
_MAX_REASON = 1500
_CORPUS_TAIL = os.sep + os.path.join(".claude", "memory")


def simulate(tool: str, tool_input: dict, current: Optional[str]) -> Optional[str]:
    """The text the tool would leave, or None when it cannot be known (or the tool itself
    will refuse: an ``old_string`` that is not in the file)."""
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    if not isinstance(edits, list) or not edits:
        return None
    text = current
    for e in edits:
        if not isinstance(e, dict):
            return None
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if text is None:
            if old == "":  # an Edit that creates the file
                text = new
                continue
            return None
        if old == "" or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") is True else text.replace(old, new, 1)
    return text


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def resolve_target(file_path: str, memory_dir: str, tree_root: str) -> Optional[tuple]:
    """``(corpus_dir, filename)`` when ``file_path`` is a top-level file of a corpus this
    session owns, else None."""
    if not isinstance(file_path, str) or not os.path.isabs(file_path):
        return None
    real = os.path.realpath(file_path)
    cdir, name = os.path.split(real)
    if not name.endswith(".md"):
        return None
    if cdir == os.path.realpath(memory_dir):
        return memory_dir, name
    if cdir.endswith(_CORPUS_TAIL) and _is_under(cdir, os.path.realpath(tree_root)):
        return cdir, name
    return None


def gate_mode(corpus_dir: str) -> str:
    """``off`` / ``warn`` / ``deny`` as DECLARED (not yet as honoured); unknown -> warn."""
    from .policy_consent import declared

    g = declared(corpus_dir, "gate")
    w = g.get("write") if isinstance(g, dict) else None
    return w if w in ("off", "warn", "deny") else "warn"


def committed_write_mode(corpus_dir: str) -> Optional[str]:
    """``gate.write`` in the hippo.json committed at HEAD, or None. One git call."""
    try:
        p = subprocess.run(
            ["git", "-C", corpus_dir, "show", "HEAD:./hippo.json"],
            capture_output=True, text=True, timeout=5,
        )
        if p.returncode != 0:
            return None
        g = json.loads(p.stdout).get("gate")
        return g.get("write") if isinstance(g, dict) else None
    except Exception:
        return None


def may_deny(corpus_dir: str, repo_root: Optional[str]) -> bool:
    from .policy_consent import honoured

    g = honoured(corpus_dir, "gate", repo_root)
    if not (isinstance(g, dict) and g.get("write") == "deny"):
        return False
    return committed_write_mode(corpus_dir) == "deny"


def render(findings: List[dict], *, deny: bool) -> str:
    from .gate import describe

    body = " ".join(describe(f) for f in findings)
    if deny:
        text = (
            "hippo write gate: refused, because this corpus's committed hippo.json asks hippo "
            "to refuse a write that leaves a budget over and makes it worse. "
            f"{body} Rewrite the change so none of these grows (standing debt is fine: an "
            "edit that holds or shrinks a measure always passes), then retry."
        )
    else:
        text = (
            "hippo write gate: this write leaves a budget over and makes it worse (it still "
            f"goes ahead; this corpus's policy only warns). {body}"
        )
    return text if len(text) <= _MAX_REASON else text[: _MAX_REASON - 1].rstrip() + "…"


def evaluate(payload, *, memory_dir: Optional[str] = None, repo_root: Optional[str] = None) -> Optional[dict]:
    """The hook's one output object, or None for silence. Never raises."""
    try:
        if not isinstance(payload, dict) or payload.get("tool_name") not in WRITE_TOOLS:
            return None
        ti = payload.get("tool_input")
        if not isinstance(ti, dict):
            return None
        from .provenance import launch_root, resolve_dirs

        tree = launch_root()
        if memory_dir is None or repo_root is None:
            md, rr = resolve_dirs()
            memory_dir, repo_root = memory_dir or md, repo_root or rr
        target = resolve_target(ti.get("file_path"), memory_dir, tree)
        if target is None:
            return None
        corpus_dir, name = target
        if corpus_dir != memory_dir:
            repo_root = tree
        if gate_mode(corpus_dir) == "off":
            return None
        real = os.path.join(os.path.realpath(corpus_dir), name)
        before = None
        if os.path.exists(real):
            with open(real, "r", encoding="utf-8") as fh:
                before = fh.read()
        after = simulate(payload["tool_name"], ti, before)
        if after is None:
            return None
        from .gate import judge_file
        from .provenance import read_floor_lint

        findings = judge_file(name, before, after, read_floor_lint(corpus_dir))
        if not findings:
            return None
        if gate_mode(corpus_dir) == "deny" and may_deny(corpus_dir, repo_root):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": render(findings, deny=True),
                }
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": render(findings, deny=False),
            }
        }
    except Exception:
        return None


def main(argv: Optional[list] = None) -> int:
    try:
        args = list(argv if argv is not None else sys.argv[1:])
        if "--from-hook" not in args:
            print("pre-tool: the write gate's hook entry; it reads a PreToolUse payload on stdin (--from-hook).")
            return 0
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = None
        from .session_consent import note_before_write  # OUT-5: opt-in; writes nothing otherwise

        note_before_write(payload)
        out = evaluate(payload)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
