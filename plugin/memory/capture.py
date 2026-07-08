"""CAP-2: Stop/SessionEnd draft-capture pass over the episode buffer.

The read path is fully automated; the write path was 100% manual — durable facts died with
the session unless someone remembered ``/hippo:new``. The episode buffer (query previews +
recalled names + a repo HEAD watermark) has been soaking since a prior release specifically so
a capture pass would have something to replay. THIS is that consumer.

At SessionEnd this module snapshots the ending session's EPHEMERAL episode-buffer entries (the
buffer rotates under a byte cap; the snapshot is durable) together with ``git diff`` since the
session's HEAD watermark, into ONE ``session-capture`` seed in a GITIGNORED pending queue
(``.claude/.memory-pending/``). The agent reviews that seed NEXT session and — per item,
explicitly — approves any durable fact into the corpus via ``/hippo:new`` (or the
``/hippo:consolidate`` drain skill). Sleep-time-compute: the only work here is cheap I/O + one
``git diff``; the LLM work of drafting an actual memory from a seed happens off the hot path,
at the agent's deliberate drain, never in this hook and never per-prompt.

THE APPROVAL GATE IS STRUCTURAL, NOT A MATTER OF DISCIPLINE. This module has NO code path that
writes to ``.claude/memory/``: it imports no corpus writer (no ``new_memory``, no
``write_memory``), it resolves only the pending dir, and every write it makes targets that
gitignored dir. A negative-capability test pins that a full capture pass alone lands NOTHING in
the corpus. That is the non-goal made unbreakable — "no autonomous bulk writes, ever": capture
is automated up to the approval gate, never one byte past it.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

from .provenance import ensure_self_ignoring_dir, resolve_dirs, run_git
from .telemetry import default_telemetry_dir, read_episodes

# The gitignored pending queue — a sibling of ``.claude/memory`` and of the index/telemetry
# dirs, following the same self-ignoring-cache convention (SEC-3). It is NOT the corpus and is
# NOT git-tracked: a draft here is a proposal awaiting explicit per-item approval, never memory.
_PENDING_DIRNAME = ".memory-pending"
_SEED_SCHEMA = 1
# Bounds so a chatty session can't write a multi-megabyte seed (the previews/names are already
# privacy-truncated in the episode buffer; these cap the COUNT).
_MAX_QUERY_PREVIEWS = 40
_MAX_RECALLED_NAMES = 60
_MAX_CHANGED_PATHS = 200


def default_pending_dir(memory_dir: str) -> str:
    """``.claude/.memory-pending`` — a sibling of ``.claude/memory`` (its own gitignored dir).

    Mirrors ``build_index.default_index_dir`` / ``telemetry.default_telemetry_dir`` so the
    queue lands beside the index and ledgers. ``HIPPO_PENDING_DIR`` overrides (hermetic tests).
    """
    override = os.environ.get("HIPPO_PENDING_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), _PENDING_DIRNAME)


def _resolve_pending_dir(pending_dir: Optional[str], memory_dir: Optional[str]) -> str:
    if pending_dir:
        return pending_dir
    if memory_dir:
        return default_pending_dir(memory_dir)
    md, _ = resolve_dirs()
    return default_pending_dir(md)


def _git_changed_paths(head_commit: Optional[str], repo_root: Optional[str]) -> List[str]:
    """Files changed OR newly created since the ``head_commit`` watermark.

    A superset of the roadmap's ``<head_commit>..HEAD``, chosen because it is more useful for
    capture: it unions (a) ``git diff --name-only <head_commit>`` — the working-tree diff
    against the watermark, so committed AND uncommitted-modified tracked files both show — with
    (b) ``git ls-files --others --exclude-standard`` — currently-untracked, non-ignored files,
    which are exactly the NEW files a session created (a plain ``git diff`` misses them, and
    they are the most capture-worthy signal). Returns ``[]`` on any failure (not a git repo, an
    unreachable watermark after a squash-merge, …) — never raises.
    """
    if not repo_root:
        return []
    paths = set()
    if head_commit:
        for ln in run_git(["diff", "--name-only", head_commit], repo_root).splitlines():
            if ln.strip():
                paths.add(ln.strip())
    for ln in run_git(["ls-files", "--others", "--exclude-standard"], repo_root).splitlines():
        if ln.strip():
            paths.add(ln.strip())
    return sorted(paths)[:_MAX_CHANGED_PATHS]


def gather_session_context(
    session_id: Optional[str],
    *,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> Optional[Dict]:
    """Replay the episode buffer for ``session_id`` into a capture-seed dict, or ``None``.

    Reads ``episode_buffer.jsonl`` (via ``telemetry.read_episodes``), keeps only this session's
    episodes, derives the HEAD watermark (the earliest recorded ``head_commit``), unions the
    recalled names, collects the (already-truncated) query previews, and diffs the repo since
    the watermark. Returns ``None`` when the session left no episodes to replay — there is
    nothing to capture, so no empty seed is written. Never raises: any failure yields ``None``.
    """
    try:
        if telemetry_dir is None and memory_dir is not None:
            telemetry_dir = default_telemetry_dir(memory_dir)
        episodes = list(read_episodes(telemetry_dir))
        # Isolate ONE session's episodes: a real harness id matches that session; a None id
        # (older harness / bare CLI) matches the episodes that were logged with no id. Either
        # way we never fold the whole multi-session buffer into one seed.
        episodes = [e for e in episodes if e.get("session_id") == session_id]
        if not episodes:
            return None

        watermark = None
        for e in episodes:
            if e.get("head_commit"):
                watermark = e["head_commit"]
                break

        names: List[str] = []
        seen_names = set()
        previews: List[str] = []
        seen_previews = set()
        for e in episodes:
            for n in e.get("recalled_names") or []:
                if n and n not in seen_names:
                    seen_names.add(n)
                    names.append(n)
            q = (e.get("query_preview") or "").strip()
            if q and q not in seen_previews:
                seen_previews.add(q)
                previews.append(q)

        head_now = run_git(["rev-parse", "HEAD"], repo_root).strip() or None if repo_root else None

        return {
            "schema": _SEED_SCHEMA,
            "kind": "session-capture",
            "session_id": session_id,
            "head_commit": watermark,  # the session's starting watermark (from the buffer)
            "head": head_now,          # HEAD at capture time — the two bound the diff range
            "changed_paths": _git_changed_paths(watermark, repo_root),
            "recalled_names": names[:_MAX_RECALLED_NAMES],
            "query_previews": previews[:_MAX_QUERY_PREVIEWS],
            "episode_count": len(episodes),
            "earliest_ts": episodes[0].get("ts"),
        }
    except Exception:
        return None


def _seed_filename(seed: Dict) -> str:
    """A per-session filename so a re-fired SessionEnd overwrites rather than duplicates."""
    sid = seed.get("session_id") or ""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(sid)).strip("-")[:48]
    if not slug:
        ts = seed.get("earliest_ts") or 0
        try:
            slug = f"anon-{int(float(ts))}"
        except (TypeError, ValueError):
            slug = "anon"
    return f"capture-{slug}.json"


def write_session_capture(
    session_id: Optional[str],
    *,
    reason: Optional[str] = None,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    pending_dir: Optional[str] = None,
) -> Optional[str]:
    """Write ONE session-capture seed into the gitignored pending queue. Returns its path.

    Returns ``None`` (writes nothing) when the session left no episodes to replay. NEVER writes
    to the corpus — the only directory it touches is the pending queue, created self-ignoring
    (SEC-3). Fire-and-forget; never raises.
    """
    try:
        if memory_dir is None or repo_root is None:
            md, rr = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or rr
        seed = gather_session_context(
            session_id, repo_root=repo_root, telemetry_dir=telemetry_dir, memory_dir=memory_dir
        )
        if seed is None:
            return None
        seed["reason"] = reason
        seed["captured_at"] = round(time.time(), 3)
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        ensure_self_ignoring_dir(pd)  # gitignored queue: mkdir + self-ignoring .gitignore (SEC-3)
        path = os.path.join(pd, _seed_filename(seed))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # atomic: a reader never sees a half-written seed
        return path
    except Exception:
        return None


def read_pending(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> List[Dict]:
    """Every pending capture seed, sorted by filename. Skips corrupt files. Never raises."""
    out: List[Dict] = []
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return []
        for name in sorted(os.listdir(pd)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(pd, name), "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict):
                    obj["_path"] = os.path.join(pd, name)
                    out.append(obj)
            except Exception:
                continue
    except Exception:
        return out
    return out


def pending_count(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> int:
    """Number of pending capture seeds (cheap listdir). Never raises."""
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        return sum(1 for n in os.listdir(pd) if n.endswith(".json"))
    except Exception:
        return 0


def discard_pending(path: str) -> bool:
    """Remove one drained/approved seed from the queue. Returns True on success. Never raises."""
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def _format_listing(seeds: List[Dict]) -> str:
    if not seeds:
        return "No pending captures — the queue is empty."
    out = [f"{len(seeds)} pending capture(s) awaiting review (nothing is in the corpus yet):", ""]
    for s in seeds:
        sid = s.get("session_id") or "(no session id)"
        wm = (s.get("head_commit") or "?")[:12]
        head = (s.get("head") or "?")[:12]
        out.append(f"  • {os.path.basename(s.get('_path', ''))}  session={sid}")
        out.append(f"      commits: {wm}..{head}   episodes: {s.get('episode_count', 0)}")
        cp = s.get("changed_paths") or []
        if cp:
            shown = ", ".join(cp[:8]) + (f", +{len(cp) - 8} more" if len(cp) > 8 else "")
            out.append(f"      changed: {shown}")
        rn = s.get("recalled_names") or []
        if rn:
            out.append(f"      recalled: {', '.join(rn[:10])}")
        qp = s.get("query_previews") or []
        if qp:
            out.append(f"      queries: {'; '.join(qp[:5])}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Draft-capture pass (CAP-2): snapshot the session's episode buffer + diff "
        "into the GITIGNORED pending queue for later per-item approval. Never writes the corpus."
    )
    parser.add_argument("--session-id", default=None, help="the harness session id to capture")
    parser.add_argument("--reason", default=None, help="SessionEnd reason (clear/logout/…)")
    parser.add_argument(
        "--from-hook",
        action="store_true",
        help="read the SessionEnd JSON payload (session_id, reason) from stdin",
    )
    parser.add_argument("--list", action="store_true", help="list pending captures and exit")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        if args.list:
            print(_format_listing(read_pending(memory_dir=args.memory_dir)))
            return 0
        session_id, reason = args.session_id, args.reason
        if args.from_hook:
            import sys

            try:
                payload = json.load(sys.stdin)
                if isinstance(payload, dict):
                    session_id = session_id or (payload.get("session_id") or None)
                    reason = reason or (payload.get("reason") or None)
            except Exception:
                pass
        path = write_session_capture(
            session_id, reason=reason, memory_dir=args.memory_dir, repo_root=args.repo_root
        )
        # Silent on the hook path: SessionEnd has no context consumer. A written seed surfaces
        # NEXT session via the SessionStart pending-capture producer.
        if path and not args.from_hook:
            print(f"captured → {path}")
        return 0
    except Exception:  # never raise out of the SessionEnd hook path
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
