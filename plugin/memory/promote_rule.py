"""RUL-6: propose a glob-scoped ``.claude/rules/<name>.md`` for a reinforced procedural memory.

LIF-7 promotes a reinforced procedural memory into the always-load plane. Done as an unscoped
``CLAUDE.md`` line, that is another line every prompt pays for. Done RIGHT (RUL-6), the memory's
concrete ``cited_paths`` derive a ``.claude/rules/<name>.md`` whose ``paths:`` globs make the
rule PATTERN-SCOPED — the harness lazy-loads it only when an edited path matches — the
memory→rule direction done right.

PROPOSE, never write (inv4 — writes into the rules plane are reviewable per-item diffs). This
renders the complete proposed rule file + a unified diff and STOPS; the skill applies it only on
an explicit human yes. The ``paths:`` derivation is ``rules_plane.derive_paths_globs`` — the SAME
over-scoping-capped derivation RUL-7's AGENTS.md export uses (a single citation stays a literal;
a same-dir/same-ext group may collapse to ``<dir>/*<ext>`` only within ``DERIVE_OVERSCOPE_FACTOR``;
``**`` is never emitted; a collapse never crosses a directory) — so a promoted rule can never
silently become a near-unscoped always-load, the exact failure a memory→rule promotion must avoid.
"""

from __future__ import annotations

import difflib
import json
import os
from typing import List, Optional

from .links import _WIKILINK_RE
from .provenance import build_repo_file_index
from .rules_plane import _repo_paths_for_globs, derive_paths_globs
from .staleness import read_invalid_after, read_provenance

# Our frontmatter marker: a re-run recognizes a hippo-authored rule and regenerates it; a rule
# file WITHOUT this marker is hand-authored and we REFUSE to clobber it.
_RULE_MARKER = "# hippo:rule-promote"
_FM_FENCE = "---"


def _split_raw_frontmatter(text: str) -> tuple:
    """(raw_frontmatter_or_None, body). RAW — never normalizes (mirrors export_agents): the body
    must survive byte-verbatim, and reassembling parsed lines would break that."""
    if text.startswith(_FM_FENCE + "\n") or text.startswith(_FM_FENCE + "\r\n"):
        end = text.find("\n" + _FM_FENCE, len(_FM_FENCE))
        if end != -1:
            nl = text.find("\n", end + 1)
            body = text[(nl + 1) if nl != -1 else len(text):]
            fm = text[: (nl if nl != -1 else len(text))]
            return fm, body
    return None, text


def _render_body(text: str) -> str:
    """The memory body with ``[[wikilinks]]`` rewritten to backtick stems — the citation shape
    the governance scanners track (mirrors export_agents._render_body)."""
    _fm, body = _split_raw_frontmatter(text)

    def _sub(m) -> str:
        target = m.group(1).split("|")[0].split("#")[0].strip()
        return f"`{target}`" if target else ""

    return _WIKILINK_RE.sub(_sub, body).strip()


def _render_rule(globs: List[str], body: str) -> str:
    """The proposed rule file: JSON-quoted ``paths:`` globs (an unquoted ``- *.py`` is a YAML
    alias — the RCH-2 lesson) under our marker, then the procedural body as the rule text."""
    lines = [_FM_FENCE, _RULE_MARKER, "paths:"]
    lines.extend(f"  - {json.dumps(g)}" for g in globs)
    lines.append(_FM_FENCE)
    head = "\n".join(lines) + "\n"
    return head + (("\n" + body + "\n") if body else "")


def _diff(existing: Optional[str], proposed: str, basename: str) -> str:
    a = existing.splitlines(keepends=True) if existing is not None else []
    return "".join(
        difflib.unified_diff(
            a,
            proposed.splitlines(keepends=True),
            fromfile=basename if existing is not None else "/dev/null",
            tofile=f"{basename} (proposed)",
        )
    )


def promote_to_rule(memory_dir: str, name: str, repo_root: str) -> dict:
    """Propose ``.claude/rules/<name>.md`` from the memory ``<name>.md``. NEVER writes.

    Returns ``{proposed, diff, changed, exists, path, globs, flags, cited_paths, reason}``.
    ``proposed`` is None + ``reason`` set when refused: unreadable/retired memory, no cited_paths,
    the derivation yields no scopable glob (can't make a SCOPED rule — the whole point), or an
    existing rule file is hand-authored (no marker; we never clobber a foreign rule). Never raises.
    """
    rel_path = os.path.join(".claude", "rules", f"{name}.md")
    out = {
        "proposed": None, "diff": "", "changed": False, "exists": False,
        "path": rel_path, "globs": [], "flags": [], "cited_paths": [], "reason": None,
    }
    try:
        try:
            with open(os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            out["reason"] = f"no readable memory named {name!r}"
            return out

        boundary = read_invalid_after(text)
        if boundary is not None:
            out["reason"] = f"memory {name!r} is retired (invalid_after {boundary}) — not promotable"
            return out

        cited, _source_commit = read_provenance(text)
        out["cited_paths"] = cited
        if not cited:
            out["reason"] = f"memory {name!r} cites no paths — a scoped rule needs cited_paths"
            return out

        repo_files, _bn = build_repo_file_index(repo_root)
        universe = _repo_paths_for_globs(repo_root, repo_files) if repo_files else set()
        globs, flags = derive_paths_globs(cited, universe)
        out["globs"], out["flags"] = globs, flags
        if not globs:
            miss = [f.get("path") for f in flags if f.get("kind") == "missing"]
            out["reason"] = (
                "no scopable path — every cited path is missing from the tree"
                + (f" ({', '.join(m for m in miss if m)})" if any(miss) else "")
            )
            return out

        proposed = _render_rule(globs, _render_body(text))

        existing: Optional[str] = None
        abs_rule = os.path.join(repo_root, rel_path)
        if os.path.isfile(abs_rule):
            out["exists"] = True
            with open(abs_rule, "r", encoding="utf-8") as fh:
                existing = fh.read()
            if _RULE_MARKER not in existing:
                out["reason"] = (
                    f"{rel_path} already exists and is hand-authored (no hippo marker) — "
                    "refusing to overwrite; remove or rename it first"
                )
                return out

        out["proposed"] = proposed
        out["diff"] = _diff(existing, proposed, rel_path)
        out["changed"] = bool(out["diff"])
        return out
    except Exception as exc:
        out["reason"] = f"promotion failed: {exc}"
        return out


def _flag_lines(flags: List[dict]) -> List[str]:
    """Human-readable one-liners for the derivation flags (over-scope / missing / no-oracle)."""
    lines: List[str] = []
    for f in flags:
        kind = f.get("kind")
        if kind == "over_scope":
            lines.append(
                f"  ⚠ over-scope: '{f.get('glob')}' would match {f.get('matched')} files for "
                f"{f.get('cited')} cited — kept as literals instead (never a near-unscoped rule)."
            )
        elif kind == "missing":
            lines.append(f"  ⚠ cited path not in the tree (excluded): {f.get('path')}")
        elif kind == "no_oracle":
            lines.append("  ⚠ no git oracle — globs are the cited paths verbatim, unvalidated.")
    return lines


def main(argv=None) -> int:
    """CLI (RUL-6): ``python -m memory.promote_rule --name <memory>`` proposes a glob-scoped
    ``.claude/rules/<name>.md`` from that memory's cited_paths and prints the diff — READ-ONLY.
    ``--apply`` writes the proposed file AFTER a human has reviewed the diff (the skill's
    explicitly-approved step); it is the ONLY write this tool makes.
    """
    import argparse

    from .provenance import resolve_dirs

    ap = argparse.ArgumentParser(
        prog="memory.promote_rule",
        description="Propose a glob-scoped .claude/rules/<name>.md for a procedural memory (RUL-6).",
    )
    ap.add_argument("--name", required=True, help="memory stem to promote (e.g. lint_before_commit)")
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="write the proposed rule file (review the diff first — propose-only otherwise)")
    args = ap.parse_args(argv)

    memory_dir, repo_root = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    repo_root = args.repo_root or repo_root

    res = promote_to_rule(memory_dir, args.name, repo_root)
    if res["reason"]:
        print(f"promote-rule REFUSED: {res['reason']}")
        return 1

    print(f"proposed: {res['path']}  (paths: {', '.join(res['globs'])})")
    for line in _flag_lines(res["flags"]):
        print(line)
    if not args.apply:
        if not res["changed"]:
            print("no change — the proposed rule matches the existing file.")
            return 0
        print("--- proposed diff (nothing written; re-run with --apply to write) ---")
        print(res["diff"], end="")
        return 0

    # Approved apply step: this is the one and only write. INV-2: the rule file is
    # committed, always-loaded governance — a torn write would ship half a rule.
    from .atomic import write_text_atomic

    abs_rule = os.path.join(repo_root, res["path"])
    os.makedirs(os.path.dirname(abs_rule), exist_ok=True)
    write_text_atomic(abs_rule, res["proposed"])
    print(f"wrote {res['path']} — commit it as a reviewable per-item rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
