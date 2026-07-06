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
from .staleness import count_unresolvable_baselines, find_stale, find_unparseable

# Harness caps hook output at 10,000 chars; stay comfortably under it.
_MAX_CONTEXT_CHARS = 9000
_MAX_ITEMS_PER_PRODUCER = 20


def stale_venv_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """One-line re-bootstrap nudge when plugin deps changed after the last bootstrap.

    The venv-in-PLUGIN_DATA model is update-safe for CODE but not DEPS: a plugin update
    that bumps requirements.txt leaves hooks running the old venv indefinitely, with new
    imports failing into silent excepts (COR-11). Compare sha256 of the CURRENT
    requirements.txt against the hash the bootstrap sentinel recorded; nudge on mismatch.
    Runs once per session by construction (SessionStart). Silent when not bootstrapped
    (ONB-1's pre-Python nudge owns that state) or when anything is unreadable.
    """
    try:
        import hashlib

        data_dir = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        if not data_dir:
            return None
        sentinel_path = os.path.join(data_dir, ".bootstrap-sentinel")
        req_path = os.path.join(plugin_root, "requirements.txt")
        if not os.path.isfile(sentinel_path) or not os.path.isfile(req_path):
            return None
        with open(sentinel_path, "r", encoding="utf-8") as fh:
            recorded = (json.load(fh) or {}).get("requirements_hash") or ""
        with open(req_path, "rb") as fh:
            current = hashlib.sha256(fh.read()).hexdigest()
        if not recorded or recorded == current:
            return None
        return (
            "⚠ hippo deps changed with the last plugin update — the venv still runs the "
            "old dependency set (new imports degrade silently). Run /hippo:bootstrap to "
            "re-provision."
        )
    except Exception:
        return None


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


def unresolvable_baseline_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """LOUD count for memories whose staleness baseline sha isn't in this repo's history.

    A squash-merge default (or a shallow/partial CI clone) rewrites/truncates history so a
    branch-authored memory's ``source_commit`` is never reachable from mainline — SHP-3 falls
    back to the memory's own stored ``source_commit_time`` instead of silently exempting it
    from drift detection forever, but that fallback IS a degradation and must be legible.
    """
    n = count_unresolvable_baselines(memory_dir, repo_root)
    if not n:
        return None
    return (
        f"⚠ {n} memories have unresolvable staleness baselines (source_commit sha not in "
        "history — likely squash-merge or a shallow clone); falling back to time-based comparison."
    )


# (label, fn). Each tier appends a producer here — never a parallel hook entry.
PRODUCERS: List[Tuple[str, Callable[[str, str], Optional[str]]]] = [
    ("stale_venv", stale_venv_producer),  # environment-level — a stale venv taints everything below
    ("integrity", integrity_producer),  # a malformed memory must not hide
    ("staleness", staleness_producer),
    ("reconsolidation", reconsolidation_producer),  # recall-filtered subset of staleness; silent unless a recently-recalled memory is stale
    ("unresolvable_baseline", unresolvable_baseline_producer),  # legibility for find_stale's sha-fallback path
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
        # Heal residual EMPTY staleness baselines (source_commit: "") to HEAD once
        # resolvable — an empty baseline leaves a memory invisible to staleness forever
        # (COR-1). Runs BEFORE the index refresh so the healed frontmatter is what gets
        # hashed. Frontmatter-only, per-line, never touches a real baseline, never raises.
        try:
            from .provenance import heal_empty_baselines

            heal_empty_baselines(memory_dir, repo_root)
        except Exception:
            pass
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
