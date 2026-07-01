"""SessionStart dispatcher for agent memory.

ONE process, ONE corpus load, ONE merged ``additionalContext`` for all dynamic memory
context. Producers (each ADDED here, never as a parallel hook entry — so there is a single
SessionStart producer for the memory concerns):
  - staleness   (Tier 1) — memories whose cited code drifted since they were written.
  - git-recent  (Tier 2) — memories captured within the recent window (newest first).
  - link-health (Tier 3) — dangling/orphan wikilink count across the corpus.
  - floor                — SILENT unless project/reference links re-bloat the MEMORY.md floor
                           (memory pointers belong only under User + Working-Style).

Contract (mirrors ``.claude/hooks/agent_staleness.sh``):
  - Self-suppresses (prints nothing) when no producer has anything to say.
  - Bounds the merged output below the harness's 10,000-char cap.
  - ALWAYS exits 0; a failing producer is skipped, never crashes the dispatcher.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, List, Optional, Tuple

from .lint_floor import floor_producer
from .lint_links import lint_links_producer
from .provenance import resolve_dirs
from .recall import git_recent_producer
from .reconsolidate import reconsolidation_producer
from .staleness import find_stale, find_unparseable

# Harness caps hook output at 10,000 chars; stay comfortably under it.
_MAX_CONTEXT_CHARS = 9000
_MAX_ITEMS_PER_PRODUCER = 20


def integrity_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """LOUD warning for memory files whose frontmatter does not parse.

    These are otherwise a silent hole — skipped by the staleness signal AND re-baselined
    by ``provenance --refresh``. Surfaced FIRST so a malformed memory can't hide.
    """
    broken = find_unparseable(memory_dir)
    if not broken:
        return None
    lines = [
        f"⚠ Memory integrity — {len(broken)} memory file(s) have UNPARSEABLE frontmatter "
        "(yaml.safe_load fails → INVISIBLE to staleness AND silently re-baselined by "
        "`provenance --refresh`). Fix the frontmatter — usually an unquoted value containing "
        "a ': ' (wrap it in quotes):"
    ]
    for name in broken[:_MAX_ITEMS_PER_PRODUCER]:
        lines.append(f"  • {name}")
    if len(broken) > _MAX_ITEMS_PER_PRODUCER:
        lines.append(f"  …and {len(broken) - _MAX_ITEMS_PER_PRODUCER} more.")
    return "\n".join(lines)


def staleness_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    # find_stale already orders most-recently-drifted first.
    stale = find_stale(memory_dir, repo_root)
    if not stale:
        return None
    lines = [
        f"⚠ Memory staleness — {len(stale)} memories cite code that changed since they were "
        "written (most-recently-drifted first); verify against current code before relying on them:"
    ]
    for item in stale[:_MAX_ITEMS_PER_PRODUCER]:
        paths = ", ".join(item["changed_paths"][:4])
        more = "" if len(item["changed_paths"]) <= 4 else f" (+{len(item['changed_paths']) - 4} more)"
        lines.append(f"  • {item['name']}: {paths}{more}")
    if len(stale) > _MAX_ITEMS_PER_PRODUCER:
        lines.append(f"  …and {len(stale) - _MAX_ITEMS_PER_PRODUCER} more.")
    return "\n".join(lines)


# (label, fn). Each tier appends a producer here — never a parallel hook entry.
PRODUCERS: List[Tuple[str, Callable[[str, str], Optional[str]]]] = [
    ("integrity", integrity_producer),  # FIRST — a malformed memory must not hide
    ("staleness", staleness_producer),
    ("reconsolidation", reconsolidation_producer),  # recall-filtered subset of staleness; silent unless a recently-recalled memory is stale
    ("git_recent", git_recent_producer),
    ("link_health", lint_links_producer),
    ("floor", floor_producer),  # silent unless project/reference links re-bloat the MEMORY.md floor
]


def build_context(memory_dir: str, repo_root: str, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    """Run every producer, merge their non-empty blocks, bound the total. Never raises."""
    blocks: List[str] = []
    for _label, fn in PRODUCERS:
        try:
            out = fn(memory_dir, repo_root)
        except Exception:
            out = None
        if out:
            blocks.append(out.rstrip())
    if not blocks:
        return ""
    ctx = "\n\n".join(blocks)
    if len(ctx) > max_chars:
        ctx = ctx[: max_chars - 16].rstrip() + "\n…(truncated)"
    return ctx


def main(argv: Optional[List[str]] = None) -> int:
    try:
        memory_dir, repo_root = resolve_dirs()
        # Bring the recall index up to date so a memory written during the LAST session is
        # indexed (recallable) this one. Incremental, OFFLINE, bounded, never-downgrade,
        # never-raises — a fast no-op when nothing changed. (Side effect, not a producer.)
        try:
            from .build_index import refresh_index

            refresh_index(memory_dir)
        except Exception:
            pass
        # Open a NEW telemetry session so the recall ledger can count distinct sessions
        # (the curation-soak signal). Side effect, not a producer; never raises. Guarded on a
        # real corpus dir so a bogus/nonexistent memory_dir never creates a stray ledger dir
        # (mirrors refresh_index, which no-ops on a missing corpus).
        try:
            from .telemetry import default_telemetry_dir, mark_session

            if os.path.isdir(memory_dir):
                mark_session(default_telemetry_dir(memory_dir))
        except Exception:
            pass
        ctx = build_context(memory_dir, repo_root)
        if ctx:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": ctx,
                        }
                    }
                )
            )
    except Exception:
        pass  # SessionStart must never fail loudly
    return 0


if __name__ == "__main__":
    sys.exit(main())
