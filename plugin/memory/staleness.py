"""Code-tied staleness for agent-memory files.

Replaces the harness's calendar-age warning ("N days old") with a git-drift signal:
a memory is stale when any file it cites (``cited_paths``) changed AFTER the memory's
``source_commit``. This is correlated with the thing it warns about — code drift —
unlike calendar age.

Fast path (for the SessionStart hook): TWO git calls total, regardless of corpus size.
  1. ``git log --since=<window> --name-only`` → newest change time per path.
  2. ``git show -s`` over the distinct source_commits → their commit times.
Then it's pure in-memory comparison. Never raises.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .provenance import (
    _iter_memory_files,
    parse_frontmatter,
    run_git,
    split_frontmatter,
)

# How far back to scan history for path changes. A perf bound, not correctness-critical:
# a memory citing code last changed beyond this window simply won't be flagged.
_DEFAULT_WINDOW = "2 years ago"
_CHANGE_MARKER = "__C__"


def read_provenance(text: str) -> tuple[List[str], Optional[str]]:
    """Return ``(cited_paths, source_commit)`` from a memory's frontmatter.

    Looks both top-level and under a ``metadata:`` block (the corpus uses both schemas).
    """
    fm = parse_frontmatter(text)
    if not fm:
        return [], None
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    cited = fm.get("cited_paths")
    if cited is None:
        cited = meta.get("cited_paths")
    sc = fm.get("source_commit")
    if sc is None:
        sc = meta.get("source_commit")
    if not isinstance(cited, list):
        cited = []
    cited = [c for c in cited if isinstance(c, str)]
    if not isinstance(sc, str) or not sc:
        sc = None
    return cited, sc


def _path_change_times(repo_root: str, since: str) -> Dict[str, int]:
    """Map repo-relative path -> newest commit unix-time within the window."""
    out: Dict[str, int] = {}
    log = run_git(
        ["log", f"--since={since}", "--name-only", f"--format={_CHANGE_MARKER}%ct"],
        repo_root,
    )
    cur: Optional[int] = None
    for line in log.split("\n"):
        if line.startswith(_CHANGE_MARKER):
            try:
                cur = int(line[len(_CHANGE_MARKER):] or 0)
            except ValueError:
                cur = None
        elif line.strip() and cur is not None:
            # git log is newest-first, so the first time we see a path is its newest change.
            if line not in out:
                out[line] = cur
    return out


def _commit_times(shas: List[str], repo_root: str) -> Dict[str, int]:
    """Map commit sha -> commit unix-time, in one ``git show`` call."""
    shas = [s for s in dict.fromkeys(shas) if s]
    if not shas:
        return {}
    out: Dict[str, int] = {}
    res = run_git(["show", "-s", "--format=%H %ct", *shas], repo_root)
    for line in res.split("\n"):
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def find_stale(memory_dir: str, repo_root: str, since: str = _DEFAULT_WINDOW) -> List[dict]:
    """Return ``[{"name", "changed_paths"}]`` for memories whose cited code drifted.

    Never raises; returns ``[]`` on any failure.
    """
    try:
        memories = []  # (name, cited_paths, source_commit)
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            cited, sc = read_provenance(text)
            if cited and sc:
                name = os.path.splitext(os.path.basename(path))[0]
                memories.append((name, cited, sc))
        if not memories:
            return []

        path_times = _path_change_times(repo_root, since)
        commit_times = _commit_times([m[2] for m in memories], repo_root)

        stale: List[dict] = []
        for name, cited, sc in memories:
            base = commit_times.get(sc)
            if base is None:
                continue  # baseline commit not in history (rebased/squashed) — cannot judge
            changed = [p for p in cited if path_times.get(p, 0) > base]
            if changed:
                # recency = newest drift among the cited files; ranks the most-urgently-stale first
                recency = max(path_times.get(p, 0) for p in changed)
                stale.append({"name": name, "changed_paths": changed, "recency": recency})
        # Most-recently-drifted first (then name) so the SessionStart note surfaces what matters.
        stale.sort(key=lambda d: (-d["recency"], d["name"]))
        return stale
    except Exception:
        return []


def find_unparseable(memory_dir: str) -> List[str]:
    """Memory files whose frontmatter block EXISTS but does NOT yaml-parse to a dict.

    These are a SILENT hole in the signal: ``read_provenance`` cannot read their
    cited_paths/source_commit, so ``find_stale`` skips them entirely (their cited code can
    drift forever un-flagged), AND ``provenance --refresh`` re-baselines their source_commit
    to ``git_last_commit`` (the parse failure falls through). The usual cause is an unquoted
    frontmatter value containing a ``': '`` (e.g. a ``description:`` with a colon mid-text).
    A malformed memory must be LOUD, not silently untracked.

    Returns sorted memory names (no extension). Never raises.
    """
    out: List[str] = []
    try:
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            fm_lines, _ = split_frontmatter(text)
            if not fm_lines or not any(ln.strip() for ln in fm_lines):
                continue  # no frontmatter block to parse — not "malformed"
            if not parse_frontmatter(text):  # {} → yaml raised or produced a non-dict mapping
                out.append(os.path.splitext(os.path.basename(path))[0])
    except Exception:
        return []
    return sorted(out)


# --------------------------------------------------------------------------- #
# Soft-invalidation primitive (graceful decay — demotion, never deletion)
# --------------------------------------------------------------------------- #
_INVALID_AFTER_RE = re.compile(r"\s*invalid_after\s*:")
_FENCE = "---"


def _strip_invalid_after(text: str) -> str:
    """Remove any existing ``invalid_after`` line from the frontmatter (body verbatim)."""
    if not text.startswith(_FENCE):
        return text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return text
    fm = [ln for ln in lines[1:close] if not _INVALID_AFTER_RE.match(ln)]
    return "\n".join([lines[0]] + fm + lines[close:])


def set_invalid_after(path: str, ts: Optional[str] = None) -> dict:
    """Set/refresh the ``invalid_after`` ADDITIVE frontmatter key on ONE memory file.

    Soft-invalidation: the validity window CLOSES at ``ts`` (an ISO-8601 timestamp; defaults
    to now in UTC). Mirrors ``provenance.backfill_text``'s additive-insertion pattern — same
    ``metadata:``-nesting awareness as ``cited_paths``/``source_commit``, so a later read
    (``build_index.compute_corpus``) finds it regardless of which frontmatter schema the file
    uses. The BODY is left byte-identical.

    Idempotent: calling with the SAME ``ts`` twice is a no-op the second time (``changed``
    is False); calling with a DIFFERENT ``ts`` refreshes (re-closes) the window — this is a
    deliberate per-item re-mark, not a blind bulk pass (there is no batch parameter here, and
    no autonomous caller in this tier — the memory-master agent invokes this one memory at a
    time after judging it). Refuses (no write) on unparseable frontmatter, mirroring
    ``reverify_file``'s guard. Never raises.
    """
    result = {"path": path, "changed": False, "invalid_after": None, "error": None}
    try:
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm_lines, _ = split_frontmatter(text)
        if fm_lines is None:
            result["error"] = "no frontmatter -- cannot write invalid_after"
            return result
        if not parse_frontmatter(text):
            result["error"] = "unparseable frontmatter -- refusing to write (fix the YAML)"
            return result

        stripped = _strip_invalid_after(text)
        lines = stripped.split("\n")
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
        fm = lines[1:close]
        ia_val = json.dumps(ts)

        meta_idx = next((i for i, ln in enumerate(fm) if re.match(r"^metadata\s*:\s*$", ln)), None)
        if meta_idx is not None:
            indent = "  "
            last = meta_idx
            j = meta_idx + 1
            while j < len(fm):
                ln = fm[j]
                if ln.strip() == "" or not ln.startswith((" ", "\t")):
                    break
                m = re.match(r"^(\s+)\S", ln)
                if m:
                    indent = m.group(1)
                last = j
                j += 1
            fm2 = fm[: last + 1] + [f"{indent}invalid_after: {ia_val}"] + fm[last + 1:]
        else:
            fm2 = fm + [f"invalid_after: {ia_val}"]

        new_text = "\n".join([lines[0]] + fm2 + lines[close:])
        changed = new_text != text
        result.update({"changed": changed, "invalid_after": ts})
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Code-tied staleness + soft-invalidation.")
    parser.add_argument(
        "--invalidate",
        metavar="NAME",
        default=None,
        help="set/refresh invalid_after on ONE memory after judging it questionable "
        "(closes the validity window; reverify_file clears it). Per-memory by design — "
        "there is no bulk invalidate. NAME is the slug, with or without .md",
    )
    parser.add_argument("--memory-dir", default=None)
    args = parser.parse_args(argv)

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md

    if args.invalidate:
        name = args.invalidate if args.invalidate.endswith(".md") else f"{args.invalidate}.md"
        target = os.path.join(memory_dir, name)
        r = set_invalid_after(target)
        base = os.path.basename(target)
        if r["error"]:
            print(f"invalidate {base}: refused — {r['error']}")
        elif r["changed"]:
            print(f"invalidate {base}: validity window closed at {r['invalid_after']}")
        else:
            print(f"invalidate {base}: already current (no change)")
        return 0

    broken = find_unparseable(memory_dir)
    if broken:
        print(f"⚠ {len(broken)} memory file(s) have UNPARSEABLE frontmatter (fix the YAML):")
        for name in broken:
            print(f"  ! {name}")
    stale = find_stale(memory_dir, repo)
    if not stale:
        print("No code-stale memories detected.")
        return 0
    print(f"{len(stale)} memories cite code that changed since they were written:")
    for item in stale:
        print(f"  • {item['name']}: {', '.join(item['changed_paths'][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
