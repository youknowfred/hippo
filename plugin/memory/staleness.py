"""Code-tied staleness for agent-memory files.

Replaces the harness's calendar-age warning ("N days old") with a git-drift signal:
a memory is stale when any file it cites (``cited_paths``) changed AFTER the memory's
``source_commit``. This is correlated with the thing it warns about — code drift —
unlike calendar age.

Fast path (for the SessionStart hook): a BOUNDED number of git calls, proportional to the
cited-path set (not repo history):
  1. ``git log --since=<window> --name-only -- <cited paths, chunked>`` → newest change
     time per path. SHP-6: scoped to the union of cited_paths across the corpus (chunked
     to stay well under ARG_MAX) instead of scanning every path in the repo's history —
     on a large monorepo, an unscoped scan can emit hundreds of MB and blow the subprocess
     timeout, which used to silently degrade to "no stale memories" with no notice.
  2. ``git show -s`` over the distinct source_commits → their commit times.
Then it's pure in-memory comparison. Never raises.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .provenance import (
    _iter_memory_files,
    build_repo_file_index,
    parse_frontmatter,
    run_git,
    split_frontmatter,
)

# How far back to scan history for path changes. A perf bound, not correctness-critical:
# a memory citing code last changed beyond this window simply won't be flagged.
_DEFAULT_WINDOW = "2 years ago"
_CHANGE_MARKER = "__C__"

# SHP-6: chunk the cited-path pathspec so a single git invocation never approaches the
# OS's real ARG_MAX (~1MB on macOS/Linux). This is a defensive, conservative constant —
# not tuned to any OS's exact limit — chosen so even a monorepo with thousands of cited
# paths issues a handful of git calls instead of one call that risks blowing the timeout.
_MAX_PATHSPEC_BYTES = 8_000
_GIT_LOG_TIMEOUT = 20


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


def read_source_commit_time(text: str) -> Optional[int]:
    """Return the memory's stored ``source_commit_time`` (committer epoch), if any.

    SHP-3's fallback baseline: recorded alongside ``source_commit`` at backfill/reverify
    time so a memory can still be judged for drift when its baseline SHA is unresolvable
    (squash-merge rewrote history; a shallow/partial clone never fetched it).
    """
    fm = parse_frontmatter(text)
    if not fm:
        return None
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    sct = fm.get("source_commit_time")
    if sct is None:
        sct = meta.get("source_commit_time")
    if isinstance(sct, bool):
        return None
    if isinstance(sct, int):
        return sct
    if isinstance(sct, str) and sct.strip():
        try:
            return int(sct.strip())
        except ValueError:
            return None
    return None


def read_last_verified(text: str) -> Optional[str]:
    """Return the memory's stored ``last_verified`` (top-level or under ``metadata:``), if any.

    RET-6's reinforcement stamp — ``provenance.reverify_file`` writes this ISO-8601 timestamp
    ONCE, the FIRST time a memory is ever re-verified (a ``graduate``/``fix`` verdict via
    ``reconsolidate.semantic_reverify``). Distinct from ``source_commit_time`` (WHICH commit
    the CITED CODE was at): this is WHEN a human confirmed the memory, an audit fact, not the
    signal that clears the drift banner (that's ``source_commit`` itself, re-baselined on
    every reverify regardless of this stamp). Never raises; ``None`` when absent/malformed or
    the memory has never been reverified.
    """
    fm = parse_frontmatter(text)
    if not fm:
        return None
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    lv = fm.get("last_verified")
    if lv is None:
        lv = meta.get("last_verified")
    if isinstance(lv, str) and lv.strip():
        return lv
    return None


def _chunk_paths(paths: List[str], max_bytes: int = _MAX_PATHSPEC_BYTES) -> List[List[str]]:
    """Split ``paths`` into batches whose total byte length stays under ``max_bytes``.

    Keeps each ``git log -- <paths>`` invocation's argument list well clear of the OS's
    real ARG_MAX, regardless of how many distinct files the corpus's memories cite.
    """
    chunks: List[List[str]] = []
    cur: List[str] = []
    cur_bytes = 0
    for p in paths:
        p_bytes = len(p.encode("utf-8")) + 1
        if cur and cur_bytes + p_bytes > max_bytes:
            chunks.append(cur)
            cur = []
            cur_bytes = 0
        cur.append(p)
        cur_bytes += p_bytes
    if cur:
        chunks.append(cur)
    return chunks


def _run_git_log_scoped(repo_root: str, since: str, paths: List[str]) -> Tuple[str, bool]:
    """Run ONE ``git log --since=<since> --name-only -- <paths>`` call; return ``(stdout, timed_out)``.

    Distinct from ``provenance.run_git`` on purpose (SHP-6): that helper's broad
    ``except Exception`` swallows ``subprocess.TimeoutExpired`` indistinguishably from any
    other failure, which is exactly the silent-degradation bug this fix closes. Scoped to
    this module's own git-log invocation — ``run_git``'s general contract for OTHER callers
    (archive.py, provenance.py) is untouched.

    ``cited_paths`` are always TOPLEVEL-relative (SHP-1), but a bare pathspec after ``--``
    is interpreted relative to ``-C repo_root`` — so for a monorepo-subdir-rooted corpus
    (``repo_root`` below the git toplevel), an unanchored pathspec would silently match
    nothing. The ``:/`` magic prefix anchors each pathspec to the repo top regardless of
    ``-C``, matching the toplevel-relative convention every other caller in this module
    already relies on.
    """
    pathspecs = [f":/{p}" for p in paths]
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "log", f"--since={since}", "--name-only",
             f"--format={_CHANGE_MARKER}%ct", "--", *pathspecs],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_LOG_TIMEOUT,
        )
        return out.stdout or "", False
    except subprocess.TimeoutExpired:
        return "", True
    except Exception:
        return "", False


def _path_change_times(
    repo_root: str, since: str, paths: List[str]
) -> Tuple[Dict[str, int], bool]:
    """Map repo-relative path -> newest commit unix-time within the window.

    SHP-6: scoped to ``paths`` (the union of cited_paths across the corpus being checked)
    instead of the whole repo history, and chunked so no single git invocation risks
    blowing ``_GIT_LOG_TIMEOUT`` on a large monorepo. Returns ``(times, timed_out)`` —
    ``timed_out`` is True if ANY chunk hit the timeout, so the caller can surface a visible
    diagnostic instead of silently treating a partial result as "nothing is stale".
    """
    out: Dict[str, int] = {}
    if not paths:
        return out, False
    timed_out = False
    for chunk in _chunk_paths(paths):
        log, chunk_timed_out = _run_git_log_scoped(repo_root, since, chunk)
        if chunk_timed_out:
            timed_out = True
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
    return out, timed_out


def _commit_times(shas: List[str], repo_root: str) -> Dict[str, int]:
    """Map commit sha -> commit unix-time, in one ``git show`` call.

    ``--ignore-missing`` (SHP-3): without it, a SINGLE unresolvable sha in the batch
    (squash-merge / shallow clone — exactly the mixed batch this signal must survive)
    makes ``git show`` exit nonzero and print NOTHING, silently poisoning the lookup for
    every OTHER, perfectly resolvable sha in the same call too.
    """
    shas = [s for s in dict.fromkeys(shas) if s]
    if not shas:
        return {}
    out: Dict[str, int] = {}
    res = run_git(["show", "-s", "--format=%H %ct", "--ignore-missing", *shas], repo_root)
    for line in res.split("\n"):
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def find_stale(
    memory_dir: str,
    repo_root: str,
    since: str = _DEFAULT_WINDOW,
    diagnostics: Optional[dict] = None,
) -> List[dict]:
    """Return ``[{"name", "changed_paths"}]`` for memories whose cited code drifted.

    SHP-3: when a memory's ``source_commit`` sha is NOT in this repo's history (squash-merge
    rewrote it away, or a shallow/partial clone never fetched it), fall back to the memory's
    OWN stored ``source_commit_time`` as the baseline instead of skipping the memory outright
    — an unresolvable sha must not make a memory permanently exempt from drift detection.
    Memories with neither a resolvable sha NOR a stored time are still skipped (unjudgeable).

    SHP-6: the git-log path-change scan is scoped to the union of ``cited_paths`` across
    every memory being checked (chunked to stay under a conservative pathspec-byte bound),
    not the whole repo history — bounding scan cost to the cited-path set instead of repo
    size. If ``diagnostics`` (a caller-owned dict) is passed, this sets
    ``diagnostics["timed_out"] = True`` when any chunk of that scan hit the git timeout, so
    a caller (e.g. ``session_start.staleness_producer``) can emit a visible
    "scan timed out" note instead of treating a partial/empty result as "nothing stale".

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
                memories.append((name, cited, sc, text))
        if not memories:
            return []

        cited_union = sorted({p for m in memories for p in m[1]})
        path_times, timed_out = _path_change_times(repo_root, since, cited_union)
        if diagnostics is not None:
            diagnostics["timed_out"] = timed_out
        commit_times = _commit_times([m[2] for m in memories], repo_root)

        stale: List[dict] = []
        for name, cited, sc, text in memories:
            base = commit_times.get(sc)
            if base is None:
                # sha unresolvable — squash-merge/shallow clone. Fall back to the memory's
                # OWN recorded commit time (SHP-3) rather than silently skipping it forever.
                base = read_source_commit_time(text)
                if base is None:
                    continue  # no fallback baseline available either — cannot judge
            changed = [p for p in cited if path_times.get(p, 0) > base]
            if changed:
                # recency = newest drift among the cited files; ranks the most-urgently-stale first
                recency = max(path_times.get(p, 0) for p in changed)
                # LIF-6: carry the resolved baseline sha along -- write_stale_cache's "sha"
                # field (a short-form anchor for RET-6's future banner) reads this rather
                # than re-deriving it; every existing consumer only reads "name"/
                # "changed_paths" so this extra key is purely additive.
                stale.append(
                    {"name": name, "changed_paths": changed, "recency": recency, "source_commit": sc}
                )
        # Most-recently-drifted first (then name) so the SessionStart note surfaces what matters.
        stale.sort(key=lambda d: (-d["recency"], d["name"]))
        return stale
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# LIF-6: ONE per-run staleness context, computed once by the SessionStart dispatcher and
# threaded through every producer — see session_start.py's PRODUCERS loop.
# --------------------------------------------------------------------------- #
@dataclass
class RunContext:
    """Per-SessionStart-run state, computed ONCE by ``session_start.build_context`` and
    passed POSITIONALLY to every registered producer — not just the two that read it, so
    the PRODUCERS loop keeps ONE uniform call shape instead of special-casing which
    producer gets which arity. ``stale`` mirrors ``find_stale``'s own return contract;
    ``worklist`` mirrors ``reconsolidate.recalled_stale_worklist``'s. A producer that has
    nothing to do with staleness (most of them) just declares the trailing parameter and
    never reads it.

    The all-defaults constructor (``RunContext()``) reproduces the exact EMPTY state a
    clean corpus produces, so ``staleness_producer``/``reconsolidation_producer`` stay
    independently callable with no ``ctx`` at all (tests, the ``reconsolidate`` CLI) —
    they fall back to deriving their own single-producer view instead of needing one
    fabricated for them.

    SIG-1: ``changed_paths`` carries the session's uncommitted working-tree diff (modified-
    tracked + untracked files since HEAD, via ``capture._git_changed_paths``), computed ONCE
    here so the ``relevant_to_work`` positive producer can intersect it against the corpus's
    ``cited_paths`` without a second ``git diff``. Empty on a clean tree / non-git corpus.
    """

    stale: List[dict] = field(default_factory=list)
    stale_diagnostics: dict = field(default_factory=dict)
    worklist: List[dict] = field(default_factory=list)
    changed_paths: List[str] = field(default_factory=list)


STALE_CACHE_SCHEMA_VERSION = 1
_STALE_CACHE_NAME = "stale.json"
# "short" sha length, matching git's own default `--short` width.
_SHORT_SHA_LEN = 7


def stale_cache_path(index_dir: str) -> str:
    """``<index_dir>/stale.json`` — the one path the writer and ``read_stale_cache`` below
    (RET-5's ranking penalty; RET-6's future drift banner) must agree on."""
    return os.path.join(index_dir, _STALE_CACHE_NAME)


def write_stale_cache(index_dir: str, stale: List[dict]) -> bool:
    """Persist ``find_stale``'s result to ``<index_dir>/stale.json`` (RET-5/RET-6 setup).

    Derived, rebuildable, gitignored — same standing as ``links.json``/``manifest.json``,
    and written the same way (tmp + ``os.replace``, ``links.write_links_cache``'s pattern)
    so a reader never sees a torn file. The shape is the MINIMUM a later bounded ranking
    penalty and a one-line "anchored to <sha>; verify before relying" banner both need,
    per stale name::

        {"schema_version": 1, "generated_at": "<iso8601>",
         "stale": {"<name>": {"changed": <len(changed_paths)>, "sha": "<short source_commit>"}}}

    Written on EVERY call, including an empty ``stale`` list — an honest
    ``{"stale": {}}`` means "checked this session, found nothing", never a skipped write,
    so a reader can trust the file's mere presence rather than guess whether staleness
    ever ran. ``read_stale_cache`` (below) is the reader half — recall.py's RET-5 salience
    blend is its first consumer, treating an absent/corrupt file as advisory — a no-op,
    never a hard error. RET-6's drift banner is a future second consumer. Never raises;
    returns True on a successful write, False on any failure (a bad ``index_dir``, a
    permissions error) — callers must not let a cache-write failure cost them the
    SessionStart run itself.
    """
    try:
        os.makedirs(index_dir, exist_ok=True)
        payload = {
            "schema_version": STALE_CACHE_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stale": {
                item["name"]: {
                    "changed": len(item.get("changed_paths") or []),
                    "sha": str(item.get("source_commit") or "")[:_SHORT_SHA_LEN],
                }
                for item in stale
            },
        }
        path = stale_cache_path(index_dir)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        return True
    except Exception:
        return False


def read_stale_cache(index_dir: str) -> Optional[Dict[str, dict]]:
    """The RET-5 reader half of ``write_stale_cache`` — ``{"<name>": {"changed", "sha"}}``,
    or ``None`` when the cache is absent, corrupt, or schema-mismatched.

    Advisory, same posture as ``links.load_edges``: this is a single small-JSON read of a
    file SessionStart already refreshed once per run (never a git call — the staleness scan
    that produced it already paid that cost, and belongs to ``_build_run_context``, not the
    hot path). A missing file (index never built, or predates LIF-6) or a schema mismatch
    both degrade to ``None`` -- recall.py's salience blend (RET-5) treats that identically to
    "nothing stale", never a hard error. Never raises.
    """
    try:
        with open(stale_cache_path(index_dir), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != STALE_CACHE_SCHEMA_VERSION:
            return None
        stale = payload.get("stale")
        if not isinstance(stale, dict):
            return None
        return {name: rec for name, rec in stale.items() if isinstance(name, str) and isinstance(rec, dict)}
    except Exception:
        return None


def unresolvable_baseline_names(memory_dir: str, repo_root: str) -> List[str]:
    """Names of memories whose ``source_commit`` sha is NOT in this repo's history.

    The per-item form of ``count_unresolvable_baselines`` — GRW-6's healing offer needs the
    NAMES so the agent can route each through ``reverify_file`` (confirm the memory still
    holds post-merge, then re-baseline), not just know how many broke. Sorted for a
    deterministic render. Never raises; ``[]`` on any failure.
    """
    try:
        by_name: Dict[str, str] = {}
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            _, sc = read_provenance(text)
            if sc:
                by_name[os.path.splitext(os.path.basename(path))[0]] = sc
        if not by_name:
            return []
        commit_times = _commit_times(list(by_name.values()), repo_root)
        return sorted(name for name, sc in by_name.items() if sc not in commit_times)
    except Exception:
        return []


def count_unresolvable_baselines(memory_dir: str, repo_root: str) -> int:
    """Count memories whose ``source_commit`` sha is NOT in this repo's history.

    These are the squash-merge / shallow-clone casualties (SHP-3): their staleness baseline
    falls back to their own stored ``source_commit_time`` inside ``find_stale`` rather than
    being silently skipped, but that fallback is a WEAKER signal (a git-cross-checked sha
    beats a self-reported timestamp) — worth its own visible count at SessionStart and in
    doctor so the degradation is never silent. Never raises; ``0`` on any failure.

    NOTE: deliberately COUNT-shaped (kept for its pinned callers) while GRW-6's
    ``unresolvable_baseline_names`` above is the per-item form; a duplicate-named memory
    file cannot occur (names are filenames), so ``len(names)`` and this count can only
    differ when two memories share one unresolvable sha — both are honest, each for its
    surface.
    """
    try:
        shas: List[str] = []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            _, sc = read_provenance(text)
            if sc:
                shas.append(sc)
        if not shas:
            return 0
        commit_times = _commit_times(shas, repo_root)
        return sum(1 for sc in shas if sc not in commit_times)
    except Exception:
        return 0


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


def find_citation_rot(memory_dir: str, repo_root: str) -> List[dict]:
    """Memories whose frontmatter cites paths that no longer exist in the repo file index.

    ``find_unparseable``'s citation-rot sibling (LIF-3), judging CURRENT state: a cited
    file that was renamed/deleted leaves a dangling ``cited_paths`` entry that the next
    re-derivation (``provenance --refresh`` / ``--reverify``) would silently drop —
    possibly emptying the list, after which the memory is permanently exempt from the
    staleness signal. This catches the rot WITHOUT needing to have observed that drop
    (the drop itself is reported by ``dropped_citations`` on the write path).

    Returns ``[{"name", "missing_paths", "cited_count"}]`` sorted by name; an entry with
    ``len(missing_paths) == cited_count`` is TOTAL rot (a refresh would zero its
    citations). Returns ``[]`` when the repo file index is unavailable (non-git dir /
    git failure) — with no index, EVERY citation would look missing; under-flag beats
    cry-wolf, same direction as ``resolve_citations``. Never raises.
    """
    out: List[dict] = []
    try:
        repo_files, _ = build_repo_file_index(repo_root)
        if not repo_files:
            return []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            cited, _sc = read_provenance(text)
            missing = [p for p in cited if p not in repo_files]
            if missing:
                out.append(
                    {
                        "name": os.path.splitext(os.path.basename(path))[0],
                        "missing_paths": missing,
                        "cited_count": len(cited),
                    }
                )
    except Exception:
        return []
    return sorted(out, key=lambda d: d["name"])


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


def set_invalid_after(path: str, ts: Optional[str] = None, *, dry_run: bool = False) -> dict:
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
    ``reverify_file``'s guard. ``dry_run`` reports (``changed``/``invalid_after``) without
    writing — same preview contract as ``reverify_file``, needed by ``semantic_reverify``'s
    LIF-1 demote chain so its ``--dry-run`` stays byte-exact. Never raises.
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
        if changed and not dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def read_invalid_after(text: str) -> Optional[str]:
    """The memory's ``invalid_after`` (top-level or under ``metadata:``), or ``None``.

    The text-level sibling of ``read_provenance``/``read_source_commit_time``, delegating
    the frontmatter-dict lookup to ``build_index._extract_invalid_after`` — the ONE
    extractor for this key (same ``metadata:`` fallback + YAML-date coercion the index
    itself applies), so what LIF-1's producers see can never drift from what recall's
    penalty actually acts on. Never raises; ``None`` on any failure (fails OPEN to
    "not invalidated", the same direction as ``recall._invalidation_state``).
    """
    try:
        from .build_index import _extract_invalid_after

        return _extract_invalid_after(parse_frontmatter(text))
    except Exception:
        return None


def invalid_after_map(names: List[str], memory_dir: str) -> Dict[str, str]:
    """``{name: invalid_after}`` for the subset of ``names`` whose file carries the key.

    LIF-1's read half of the soft-invalidation chain: an ``invalid_after``-carrying stale
    memory is in demote's TERMINAL state (recall's pre-cut penalty is already ranking it
    down), so the SessionStart staleness producer suppresses its per-item line and the
    reconsolidation worklist stops re-nagging it. Deliberately bounded to the caller's
    ``names`` (an already-small stale/worklist set) — never a corpus scan, and NOT folded
    into ``find_stale`` itself: drift detection stays invalid_after-blind by pinned
    contract (code-drift and content-validity are separate concerns). Read-only; never
    raises; a missing/unreadable file is simply absent from the map (fails toward
    re-nagging — the legible direction).
    """
    out: Dict[str, str] = {}
    try:
        for name in names:
            try:
                with open(os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8") as fh:
                    ia = read_invalid_after(fh.read())
            except Exception:
                continue
            if ia:
                out[name] = ia
    except Exception:
        return out
    return out


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
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md
    repo_root = args.repo_root or repo

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
    # LIF-3: find_unparseable's citation-rot sibling — count-first, then per-memory with the
    # vanished path(s) named; TOTAL rot (every citation gone) is called out distinctly because
    # a refresh over it would zero cited_paths and make the memory staleness-exempt.
    rot = find_citation_rot(memory_dir, repo_root)
    if rot:
        print(f"⚠ {len(rot)} memory file(s) cite paths that no longer exist in the repo (citation rot):")
        for item in rot:
            missing = ", ".join(item["missing_paths"][:6])
            more = f" (+{len(item['missing_paths']) - 6} more)" if len(item["missing_paths"]) > 6 else ""
            total = (
                " — ALL its citations (a refresh would EMPTY cited_paths → staleness-EXEMPT)"
                if len(item["missing_paths"]) == item["cited_count"]
                else ""
            )
            print(f"  ! {item['name']}: {missing}{more}{total}")
    diagnostics: dict = {}
    stale = find_stale(memory_dir, repo_root, diagnostics=diagnostics)
    if diagnostics.get("timed_out"):
        print("⚠ staleness scan timed out — signal may be incomplete")
    if not stale:
        print("No code-stale memories detected.")
        return 0
    print(f"{len(stale)} memories cite code that changed since they were written:")
    for item in stale:
        print(f"  • {item['name']}: {', '.join(item['changed_paths'][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
