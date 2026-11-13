"""RUL-9: promote-to-rule as a tend proposal, from where a feedback memory's touches land.

RUL-6's ``promote_rule`` drafts a path-scoped ``.claude/rules/<name>.md`` for a memory when
someone asks. This module finds the memories worth asking about: a feedback memory whose
recorded touches concentrate on one path glob is a lesson about that corner of the tree,
and a rule scoped to the glob loads exactly when that corner is edited — the harness's own
lazy load, instead of a hook reminder.

The evidence is the touch-grain provenance the JIT lane already records (JIT-2): every
PostToolUse file touch whose path a memory cites lands in ``outcome_events.jsonl`` with
``cited_by`` naming those memories (``tree_path`` when the touch was in a worktree). The
per-session JIT state docs list which files and which names fired, but not which name fired
on which file, so they cannot carry this join. A memory's RECORDED TOUCHES are its distinct
(session, path) pairs there — the first-touch grain the reminder itself uses, so one long
session editing one file thirty times is one touch.

Who qualifies, each rule load-bearing:
  - a ``feedback`` memory not linked from the floor (the memories the first-touch reminder
    serves; a floor memory already loads every session);
  - at least ``MIN_TOUCHES`` recorded touches, so one lucky touch proposes nothing;
  - one glob covering at least ``MIN_COVERAGE`` of them, derived deterministically: the
    single file when it alone covers enough, else the deepest directory (never the repo
    root) with an extension pattern, narrowed to one directory level when every covered
    touch sits directly in it. The covered share rides in the evidence;
  - no rule for it yet: neither ``.claude/rules/<name>.md`` nor a ``.claude/rules`` file
    that cites it by name;
  - ``promote_rule`` drafts it (a memory it refuses — retired, hand-authored file in the
    way — proposes nothing), and the glob matches a file in the tree when the tree is known.

Read-only: the tend source lists; a ``write`` verdict on one item writes that one rule file
through ``promote_rule.write_rule`` and nothing else. Never raises.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

# Set from the field corpora's ledgers: of the feedback memories with any recorded touch,
# most had one or two (the largest corpus: 7 with one, 3 with two, then a long tail). At
# three, an 80% share needs EVERY touch under the glob, so two coinciding touches never
# propose; on that corpus the count of proposals goes 10, 4, 1 for minimums 1, 2, 3 and
# stays at 1 through 10.
MIN_TOUCHES = 3
MIN_COVERAGE = 0.8
_WORKTREE_PREFIX = ".claude/worktrees/"


def _rel_path(row: dict) -> Optional[str]:
    """The repo-relative path one outcome row touched, or None (absolute, outside, worktree
    noise without its in-tree normalization)."""
    p = str(row.get("tree_path") or row.get("path") or "").strip()
    if not p or os.path.isabs(p) or p.startswith("../") or p.startswith(_WORKTREE_PREFIX):
        return None
    return p[2:] if p.startswith("./") else p


def memory_touches(telemetry_dir: str) -> Dict[str, Counter]:
    """``{memory name: Counter(path -> distinct sessions that touched it)}`` from the
    ``cited_by`` rows of the outcome ledger. Never raises."""
    seen: Set[Tuple[str, str, str]] = set()
    out: Dict[str, Counter] = {}
    try:
        from .telemetry import read_outcomes

        for row in read_outcomes(telemetry_dir):
            names = row.get("cited_by")
            if not isinstance(names, list) or not names:
                continue
            path = _rel_path(row)
            if path is None:
                continue
            sid = str(row.get("session_id") or "")
            for name in names:
                key = (str(name), sid, path)
                if not name or key in seen:
                    continue
                seen.add(key)
                out.setdefault(str(name), Counter())[path] += 1
    except Exception:
        return out
    return out


def _covered(glob: str, touches: Counter) -> int:
    from .rules_plane import _glob_to_re

    rx = _glob_to_re(glob)
    return sum(n for p, n in touches.items() if rx.match(p))


def derive_glob(touches: Counter, min_coverage: float = MIN_COVERAGE) -> Optional[Tuple[str, int]]:
    """``(glob, covered touches)`` for the most specific glob covering ``min_coverage`` of
    ``touches``, or None. Deterministic; never the repo root; never raises."""
    try:
        total = sum(touches.values())
        if not total:
            return None
        need = min_coverage * total
        top_path, top_n = sorted(touches.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if top_n >= need:
            return top_path, top_n
        dirs: Set[str] = set()
        exts: Set[str] = {""}
        for p in touches:
            d = os.path.dirname(p)
            while d:
                dirs.add(d)
                d = os.path.dirname(d)
            exts.add(os.path.splitext(p)[1])
        # Deepest directory first, then an extension pattern before none, then by name.
        for d in sorted(dirs, key=lambda x: (-x.count("/"), x)):
            for ext in sorted(exts, key=lambda e: (e == "", e)):
                for glob in (f"{d}/*{ext}", f"{d}/**/*{ext}"):
                    n = _covered(glob, touches)
                    if n >= need:
                        return glob, n
        return None
    except Exception:
        return None


def _has_rule(repo_root: str, name: str) -> bool:
    """A ``.claude/rules`` file for ``name`` already: its own file, or one citing it."""
    try:
        rules = os.path.join(repo_root, ".claude", "rules")
        if os.path.isfile(os.path.join(rules, f"{name}.md")):
            return True
        from .rules_plane import _BACKTICK_TOKEN_RE

        for fn in sorted(os.listdir(rules)) if os.path.isdir(rules) else []:
            if not fn.endswith(".md"):
                continue
            try:
                with open(os.path.join(rules, fn), "r", encoding="utf-8") as fh:
                    toks = _BACKTICK_TOKEN_RE.findall(fh.read())
            except Exception:
                continue
            if name in toks or f"{name}.md" in toks:
                return True
    except Exception:
        return False
    return False


def _feedback_names(memory_dir: str) -> Set[str]:
    """Names of the corpus's feedback memories that the floor does not already carry."""
    from .jit import _memory_type
    from .lint_floor import floor_memory_names
    from .provenance import _iter_memory_files, parse_frontmatter

    floor = floor_memory_names(memory_dir)
    out: Set[str] = set()
    for path in _iter_memory_files(memory_dir):
        name = os.path.splitext(os.path.basename(path))[0]
        if name in floor:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                fm = parse_frontmatter(fh.read())
        except Exception:
            continue
        if _memory_type(fm) == "feedback":
            out.add(name)
    return out


def _universe(repo_root: str) -> Set[str]:
    try:
        from .provenance import build_repo_file_index
        from .rules_plane import _repo_paths_for_globs

        files, _bn = build_repo_file_index(repo_root)
        return _repo_paths_for_globs(repo_root, files) if files else set()
    except Exception:
        return set()


def proposals(
    memory_dir: str,
    repo_root: str,
    *,
    telemetry_dir: Optional[str] = None,
    universe: Optional[Set[str]] = None,
    min_touches: int = MIN_TOUCHES,
) -> List[dict]:
    """``[{name, glob, touches, covered, coverage, path, proposed}]`` — one per qualifying
    memory, by name. ``universe`` (the tree's paths) defaults to git's view of
    ``repo_root``; an empty one skips the glob-matches-a-file check. Never raises."""
    out: List[dict] = []
    try:
        if telemetry_dir is None:
            from .telemetry import default_telemetry_dir

            telemetry_dir = default_telemetry_dir(memory_dir)
        touches = memory_touches(telemetry_dir)
        if not touches:
            return out
        eligible = _feedback_names(memory_dir)
        from .promote_rule import promote_to_rule
        from .rules_plane import _glob_to_re

        for name in sorted(eligible):
            t = touches.get(name)
            total = sum(t.values()) if t else 0
            if total < min_touches:
                continue
            found = derive_glob(t)
            if found is None or _has_rule(repo_root, name):
                continue
            glob, covered = found
            if universe is None:
                universe = _universe(repo_root)
            if universe and not any(_glob_to_re(glob).match(u) for u in universe):
                continue  # a rule born dead: its glob matches nothing in the tree
            res = promote_to_rule(memory_dir, name, repo_root, globs=[glob])
            if not res.get("proposed"):
                continue
            out.append({"name": name, "glob": glob, "touches": total, "covered": covered,
                        "coverage": round(covered / total, 4), "path": res["path"],
                        "proposed": res["proposed"]})
    except Exception:
        return out
    return out


def tend_entries(memory_dir: str, repo_root: str) -> List[dict]:
    """One tend ``rule`` item per proposal (the glob rides on the entry). Never raises."""
    try:
        from .tend_queue import _entry

        out = []
        for p in proposals(memory_dir, repo_root):
            e = _entry(
                "rule", p["name"],
                f"{p['covered']} of its {p['touches']} recorded touches "
                f"({p['coverage']:.0%}) fall under {p['glob']}: a rule scoped there loads "
                "exactly when that code is edited",
                f"write {p['path']} (shown below), or skip",
            )
            e["glob"] = p["glob"]
            out.append(e)
        return out
    except Exception:
        return []


def detail_lines(entry: dict, memory_dir: str, repo_root: str) -> List[str]:
    """The file a ``write`` would create, shown in full."""
    from .promote_rule import promote_to_rule

    res = promote_to_rule(memory_dir, entry["target"], repo_root, globs=[entry.get("glob") or ""])
    if not res.get("proposed"):
        return [f"(no draft: {res.get('reason')})"]
    return [f"{res['path']} would be written as:", ""] + res["proposed"].rstrip("\n").split("\n")


def apply_write(entry: dict, memory_dir: str, repo_root: str) -> dict:
    """The ``write`` verdict: draft again (the tree may have moved), then write that one
    rule file through ``promote_rule``'s writer. ``{"ok", "message"}``; never raises."""
    try:
        from .promote_rule import promote_to_rule, write_rule

        res = promote_to_rule(memory_dir, entry["target"], repo_root, globs=[entry["glob"]])
        if not res.get("proposed"):
            return {"ok": False, "message": f"{entry['target']}: {res.get('reason') or 'no draft'}"}
        write_rule(repo_root, res)
        return {"ok": True, "message": f"wrote {res['path']} (paths: {entry['glob']}). "
                "It is a governance file: commit it yourself."}
    except Exception as exc:
        return {"ok": False, "message": f"{entry.get('target')}: {type(exc).__name__}: {exc}"}
