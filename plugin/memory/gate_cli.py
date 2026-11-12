"""GAT-2: ``hippo gate`` — the write gate's checks at commit time, and declared budgets.

  hippo gate --staged             judge what the next commit records: HEAD -> the index
  hippo gate --commit <rev>       judge one commit: its first parent -> it (read-only replay, CI)
  hippo gate --install [--yes]    preview, then write, a git pre-commit hook that runs --staged

The floor checks are GAT-1's (``gate.judge_floor``) with the same delta posture: a finding
needs the AFTER side over its budget AND worse than the BEFORE side, so standing debt never
fails a commit. The banned pattern over memory BODIES is deliberately not run here: it stays
a write-time warning (the PreToolUse hook), never a failed commit. Replayed over 30 days of
two production corpora it would have failed about half of all corpus commits (187 of 359 and
11 of 22), each for a status word a session typed into a body, while on the floor and the
caps this verb agreed with the larger corpus's own hand-built checker on every commit. A
gate that fails half the commits gets skipped, and the floor is what rides every session.
So ``judge_sides`` never calls ``judge_body`` and never reads a memory body. Added here:

- ``hippo.json: "budgets"`` — ``files`` (memory files directly in the corpus dir),
  ``floor_bytes``, ``floor_lines`` and ``journals`` (files matching ``fold_digests`` or a
  ``journals`` glob list). Each optional; a value that is not a positive integer is
  undeclared. Budgets only ever feed this verb's verdict, so unlike ``gate`` they need no
  acceptance on the machine.
- At commit time the floor's size is held to the harness READ LIMIT (25,000 bytes / 200
  lines, or a lower declared ``floor_lint`` cap), not the advisory warn line the write-time
  hook warns on: a commit gate is a hard edge, and the warn line is advice. ``floor_bytes``
  / ``floor_lines`` are the corpus's own hard numbers, judged beside it.

Trees are read with git plumbing (``ls-tree`` / ``ls-files -s`` / ``cat-file --batch``):
nothing is checked out, and untracked files are never counted. The policy is the AFTER
side's ``hippo.json`` (and legacy ``.format``), so a change that raises a budget
deliberately is judged by the budget it sets. A corpus that declares no ``budgets`` and no
``floor_lint`` is not checked (exit 0, one line).

Exit codes: 0 nothing got worse; 1 a budget got worse (or ``--install`` refused); 2 usage.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

BUDGET_KEYS = ("files", "floor_bytes", "floor_lines", "journals")
HOOK_MARKER = "# hippo-gate: pre-commit hook written by `hippo gate --install`"
_EMPTY = "<empty>"


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #
def _git(cwd: str, *args: str, data: Optional[bytes] = None) -> Tuple[int, bytes]:
    try:
        p = subprocess.run(["git", "-C", cwd, *args], input=data, capture_output=True, timeout=120)
        return p.returncode, p.stdout
    except Exception:
        return 1, b""


def _toplevel(path: str) -> Optional[str]:
    rc, out = _git(path, "rev-parse", "--show-toplevel")
    return out.decode().strip() if rc == 0 and out.strip() else None


def _list(top: str, rel: str, side: str) -> Dict[str, str]:
    """``{filename: blob sha}`` for the files directly in ``rel`` on one side: a commit-ish,
    ``:index`` (stage 0 of the index), or the empty tree."""
    if side == _EMPTY:
        return {}
    prefix = rel.rstrip("/") + "/"
    out: Dict[str, str] = {}
    if side == ":index":
        rc, raw = _git(top, "ls-files", "-z", "-s", "--", prefix)
        if rc != 0:
            return out
        for rec in raw.decode("utf-8", "replace").split("\0"):
            if "\t" not in rec:
                continue
            meta, path = rec.split("\t", 1)
            parts = meta.split()
            if len(parts) == 3 and parts[2] == "0":
                name = path[len(prefix):]
                if path.startswith(prefix) and name and "/" not in name:
                    out[name] = parts[1]
        return out
    rc, raw = _git(top, "ls-tree", "-z", side, "--", prefix)
    if rc != 0:
        return out
    for rec in raw.decode("utf-8", "replace").split("\0"):
        if "\t" not in rec:
            continue
        meta, path = rec.split("\t", 1)
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            name = path[len(prefix):]
            if path.startswith(prefix) and name and "/" not in name:
                out[name] = parts[2]
    return out


def _blobs(top: str, shas: List[str]) -> Dict[str, str]:
    """``{sha: text}`` via one ``cat-file --batch``."""
    want = sorted(set(s for s in shas if s))
    if not want:
        return {}
    rc, raw = _git(top, "cat-file", "--batch", data=("\n".join(want) + "\n").encode())
    out: Dict[str, str] = {}
    if rc != 0:
        return out
    i = 0
    while i < len(raw):
        nl = raw.index(b"\n", i)
        head = raw[i:nl].decode().split()
        i = nl + 1
        if len(head) == 2 and head[1] == "missing":
            continue
        size = int(head[2])
        out[head[0]] = raw[i : i + size].decode("utf-8", "replace")
        i += size + 1
    return out


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #
def read_budgets(raw) -> Dict[str, int]:
    """The declared ``budgets``: each positive integer kept, anything else undeclared."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v for k, v in raw.items()
        if k in BUDGET_KEYS and isinstance(v, int) and not isinstance(v, bool) and v > 0
    }


def _journal_globs(policy_dir: str) -> List[str]:
    from .provenance_format import read_fold_digests, read_policy_key

    globs = list(read_fold_digests(policy_dir))
    raw = read_policy_key(policy_dir, "journals")
    for g in raw if isinstance(raw, list) else []:
        if isinstance(g, str) and g.strip() and "/" not in g and g.strip() not in globs:
            globs.append(g.strip())
    return globs


def policy_of(files: Dict[str, str]) -> dict:
    """The corpus policy from one side's ``hippo.json`` / ``.format`` texts, read by the
    same readers the engine uses (materialized into a scratch dir, never the corpus)."""
    from .provenance_format import read_floor_lint, read_policy_key

    tmp = tempfile.mkdtemp(prefix="hippo-gate-")
    try:
        for name in ("hippo.json", ".format"):
            if name in files:
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    fh.write(files[name])
        return {
            "floor_lint": read_floor_lint(tmp),
            "floor_lint_declared": isinstance(read_policy_key(tmp, "floor_lint"), dict),
            "budgets": read_budgets(read_policy_key(tmp, "budgets")),
            "journals": _journal_globs(tmp),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# the judgment
# --------------------------------------------------------------------------- #
def _counts(names, globs) -> Tuple[int, int]:
    from .provenance import _is_memory_filename

    files = [n for n in names if _is_memory_filename(n)]
    journals = [n for n in files if any(fnmatch.fnmatch(n, g) for g in globs)]
    return len(files), len(journals)


def judge_sides(top: str, rel: str, before: str, after: str, budgets_override: Optional[dict] = None) -> dict:
    """``{"declared", "findings", "changed"}`` for BEFORE -> AFTER of the corpus at ``rel``.
    ``budgets_override`` replaces the declared budgets (a replay measuring other caps)."""
    from .gate import FLOOR_NAME, judge_floor

    b, a = _list(top, rel, before), _list(top, rel, after)
    changed = sorted(n for n in set(b) | set(a) if b.get(n) != a.get(n))
    # The policy and the floor, both sides. Memory bodies are never read at commit time:
    # their banned-pattern check is the write-time hook's warning, not a commit failure.
    want = [a.get(n) for n in ("hippo.json", ".format")] + [b.get(FLOOR_NAME), a.get(FLOOR_NAME)]
    text = _blobs(top, [w for w in want if w])
    pol = policy_of({n: text[a[n]] for n in ("hippo.json", ".format") if a.get(n) in text})
    budgets = read_budgets(budgets_override) if budgets_override is not None else pol["budgets"]
    out = {"declared": bool(budgets or pol["floor_lint_declared"]), "findings": [], "changed": len(changed)}
    if not out["declared"]:
        return out
    fl = dict(pol["floor_lint"])
    # The hard edge at commit time is the read limit, not the advisory warn line.
    fl["warn_bytes"], fl["warn_lines"] = fl["cap_bytes"], fl["cap_lines"]
    if not pol["floor_lint_declared"]:
        fl.update(banned_re=None, max_line=None, section_budgets={})
    get = lambda side, n: text.get(side.get(n)) if side.get(n) else None  # noqa: E731
    f: List[dict] = []
    if a.get(FLOOR_NAME):
        f += judge_floor(get(b, FLOOR_NAME), get(a, FLOOR_NAME), fl, budgets)
    (bf, bj), (af, aj) = _counts(b, pol["journals"]), _counts(a, pol["journals"])
    for key, what, bv, av in (("files", "memory files", bf, af), ("journals", "journal files", bj, aj)):
        cap = budgets.get(key)
        if cap and av > cap and av > bv:
            f.append({"kind": key, "what": what, "before": bv, "after": av, "budget": cap, "file": rel})
    out["findings"] = f
    return out


_REMEDY = {
    "files": "fold or archive memories in the same change, or raise budgets.files in hippo.json deliberately",
    "journals": "fold settled journals into their digests first, or raise budgets.journals deliberately",
}


def render(res: dict, label: str) -> str:
    from .gate import describe

    if not res["declared"]:
        return "hippo gate: this corpus declares no budgets and no floor_lint policy, so there is nothing to check."
    if not res["findings"]:
        return f"hippo gate: {label} makes no corpus budget worse ({res['changed']} corpus file(s) changed)."
    lines = [f"hippo gate: {label} leaves {len(res['findings'])} corpus budget(s) over and makes them worse:"]
    for f in res["findings"]:
        line = describe(f)
        if f["kind"] in _REMEDY:
            line = line.rstrip(".") + f"; {_REMEDY[f['kind']]}."
        lines.append(f"  - {line}")
    lines.append("Standing debt is fine: a change that holds or shrinks a measure always passes.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the pre-commit hook
# --------------------------------------------------------------------------- #
def hook_text(hippo_bin: str, rel: str) -> str:
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}. Safe to delete.\n"
        "# Fails the commit only when it leaves a declared corpus budget over AND makes it worse\n"
        "# (`hippo gate --staged` exits 1). Anything else, hippo missing included, lets it through.\n"
        f"git diff --cached --quiet -- '{rel}' 2>/dev/null && exit 0\n"
        "HIPPO=$(command -v hippo 2>/dev/null)\n"
        f"[ -n \"$HIPPO\" ] || HIPPO='{hippo_bin}'\n"
        "[ -x \"$HIPPO\" ] || exit 0\n"
        "\"$HIPPO\" gate --staged\n"
        "[ $? -eq 1 ] && exit 1\n"
        "exit 0\n"
    )


def install(top: str, rel: str, yes: bool) -> int:
    rc, raw = _git(top, "rev-parse", "--git-path", "hooks")
    if rc != 0 or not raw.strip():
        print("hippo gate --install: not inside a git repository; nothing written.")
        return 1
    hooks_dir = raw.decode().strip()
    hooks_dir = hooks_dir if os.path.isabs(hooks_dir) else os.path.join(top, hooks_dir)
    target = os.path.join(hooks_dir, "pre-commit")
    hippo_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "hippo")
    if "'" in hippo_bin or "'" in rel:
        print("hippo gate --install: a path contains a single quote, which the hook cannot quote; nothing written.")
        return 1
    text = hook_text(hippo_bin, rel)
    current = None
    if os.path.lexists(target):
        try:
            with open(target, encoding="utf-8") as fh:
                current = fh.read()
        except Exception:
            current = ""
        if HOOK_MARKER not in current:
            print(
                f"hippo gate --install: {target} already exists and was not written by hippo, so it "
                "is left alone. To chain the gate, add these lines to it by hand:\n"
                "  hippo gate --staged\n"
                "  [ $? -eq 1 ] && exit 1"
            )
            return 1
        if current == text:
            print(f"hippo gate --install: the hook at {target} is already current; nothing to do.")
            return 0
    verb = "replace hippo's hook at" if current is not None else "write"
    if not yes:
        print(f"hippo gate --install would {verb} {target}:\n\n{text}\nNothing was written. Re-run with --yes to write it.")
        return 0
    try:
        from .atomic import write_text_atomic

        os.makedirs(hooks_dir, exist_ok=True)
        write_text_atomic(target, text)
        os.chmod(target, 0o755)
    except Exception as exc:
        print(f"hippo gate --install: could not write {target} ({exc}); nothing was changed.", file=sys.stderr)
        return 1
    print(f"hippo gate --install: wrote {target}. `git commit --no-verify` skips it for one commit.")
    return 0


# --------------------------------------------------------------------------- #
def _corpus(memory_dir: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if memory_dir is None:
        from .provenance import resolve_dirs

        memory_dir, _ = resolve_dirs()
    top = _toplevel(memory_dir) if os.path.isdir(memory_dir) else None
    if not top:
        return None, None
    rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(top))
    if rel.startswith(".."):
        return None, None
    return top, rel.replace(os.sep, "/")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="hippo gate",
        description="Check that a change makes no declared corpus budget worse (exit 1 when one does).",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="judge the index against HEAD (what the next commit records)")
    mode.add_argument("--commit", metavar="REV", help="judge one commit against its first parent (read-only)")
    mode.add_argument("--install", action="store_true", help="preview a git pre-commit hook that runs --staged")
    ap.add_argument("--yes", action="store_true", help="with --install: write the hook")
    ap.add_argument("--memory-dir", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.yes and not args.install:
        ap.error("--yes only goes with --install")
    top, rel = _corpus(args.memory_dir)
    if top is None:
        print("hippo gate: no corpus inside a git repository here, so there is nothing to check.")
        return 0
    if args.install:
        return install(top, rel, args.yes)
    if args.staged:
        rc, _ = _git(top, "rev-parse", "--verify", "-q", "HEAD")
        before, after, label = ("HEAD" if rc == 0 else _EMPTY), ":index", "the staged change"
    else:
        rc, raw = _git(top, "rev-parse", "--verify", "-q", f"{args.commit}^{{commit}}")
        if rc != 0:
            print(f"hippo gate: {args.commit!r} is not a commit here.", file=sys.stderr)
            return 2
        sha = raw.decode().strip()
        prc, praw = _git(top, "rev-parse", "--verify", "-q", f"{sha}^1")
        before, after, label = (praw.decode().strip() if prc == 0 else _EMPTY), sha, f"commit {sha[:9]}"
    res = judge_sides(top, rel, before, after)
    print(render(res, label))
    return 1 if res["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
