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
at the agent's deliberate drain, never in this hook and never per-prompt. ONE opt-in
exception, default OFF: ``HIPPO_CAPTURE_LLM=1`` adds a single bounded small-model TRIAGE call
at capture (``capture_triage.py`` — suggested type/description + a near-duplicate second
opinion, annotated onto the seed as ``llm_triage``). Suggestions only: the drain's per-item
human ratification is untouched, and any triage failure falls back to exactly this
heuristic-only seed.

THE APPROVAL GATE IS STRUCTURAL, NOT A MATTER OF DISCIPLINE. This module has NO code path that
writes to ``.claude/memory/``: it imports no corpus writer (no ``new_memory``, no
``write_memory`` — the opt-in triage seam lives in ``capture_triage``, which reuses only that
module's DRY-RUN checker), it resolves only the pending dir, and every write it makes targets
that gitignored dir. A negative-capability test pins that a full capture pass alone lands
NOTHING in the corpus — with the triage flag ON as well as off. That is the non-goal made
unbreakable — "no autonomous bulk writes, ever": capture is automated up to the approval gate,
never one byte past it.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

from .provenance import ensure_self_ignoring_dir, resolve_dirs, run_git
from .secrets import scan_text
from .telemetry import (
    abstention_backlog,
    default_telemetry_dir,
    log_threat_findings,
    read_decisions,
    read_episodes,
)
from .threat_lint import scan_threats, scan_tier_b

# The gitignored pending queue — a sibling of ``.claude/memory`` and of the index/telemetry
# dirs, following the same self-ignoring-cache convention (SEC-3). It is NOT the corpus and is
# NOT git-tracked: a draft here is a proposal awaiting explicit per-item approval, never memory.
_PENDING_DIRNAME = ".memory-pending"
# The QUEUE's own schema, not the corpus format: bumping it is not an ED-4 event because no
# committed artifact changes shape — the queue is gitignored ephemera a drain consumes whole.
# Schema 2 (GRW-1 + GRW-4, one coordinated bump): adds ``diff_hunks`` (verbatim evidence),
# ``hunks_secret_flagged``, ``salience`` (a value LABEL, never a gate), and ``decisions``.
# CAP-LLM rides schema 2 unbumped: ``llm_triage`` is OPTIONAL and purely advisory (present
# only when HIPPO_CAPTURE_LLM opted in AND the call succeeded) — a drain that ignores it
# loses nothing, so the shape a consumer must understand is unchanged.
_SEED_SCHEMA = 2
# Bounds so a chatty session can't write a multi-megabyte seed (the previews/names are already
# privacy-truncated in the episode buffer; these cap the COUNT).
_MAX_QUERY_PREVIEWS = 40
_MAX_RECALLED_NAMES = 60
_MAX_CHANGED_PATHS = 200
_MAX_DECISIONS = 20
# Byte cap on the verbatim diff-hunk evidence (GRW-1). ``run_git`` imposes NO output bound, so
# the slice happens here — always on a line boundary, with a legible truncation marker, so a
# monorepo-wide diff can never balloon a seed. Counts capture COUNTS; this one caps BYTES.
_MAX_HUNK_BYTES = 20_000
# CAP-6: hard bound on the pending queue. One seed lands per session that recalls anything, so
# an un-drained queue would grow WITHOUT LIMIT — the exact "soaks forever" footgun the LIF
# workstream goal ("nothing nags forever") already closed for reconsolidation. When a fresh
# capture pushes the queue past this cap, the LOWEST-value then OLDEST seeds are pruned so the
# queue keeps the sessions a drain would lead with (highest salience, most recent). Ephemera in
# a gitignored dir: pruning a stale trivial seed loses nothing a re-capture couldn't redraft.
_MAX_PENDING_SEEDS = 50
# CAP-6: how many NEW recall-ledger sessions an explicit queue --snooze holds the SessionStart
# nudge for before it re-nags. Parity with ``reconsolidate._SNOOZE_WINDOW_SESSIONS`` (same value,
# same session-aging rhythm): a snooze is a DEFERRAL, never a dismissal — it must expire so a
# growing backlog resurfaces. The nudge is the only thing snoozed; the seeds are untouched.
_SNOOZE_WINDOW_SESSIONS = 5
# The queue-snooze marker: a tiny sibling of the seeds inside the gitignored pending dir (queue
# state lives with the queue). Dotfile so ``read_pending``/``pending_count`` (``*.json`` only)
# never mistake it for a seed.
_SNOOZE_MARKER = ".capture-snooze.json"


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


def _git_untracked(repo_root: Optional[str]) -> List[str]:
    """Currently-untracked, non-ignored files — the NEW files a session created. Never raises."""
    if not repo_root:
        return []
    out = []
    for ln in run_git(["ls-files", "--others", "--exclude-standard"], repo_root).splitlines():
        if ln.strip():
            out.append(ln.strip())
    return out


def _git_changed_paths(
    head_commit: Optional[str], repo_root: Optional[str], untracked: Optional[List[str]] = None
) -> List[str]:
    """Files changed OR newly created since the ``head_commit`` watermark.

    A superset of the roadmap's ``<head_commit>..HEAD``, chosen because it is more useful for
    capture: it unions (a) ``git diff --name-only <head_commit>`` — the working-tree diff
    against the watermark, so committed AND uncommitted-modified tracked files both show — with
    (b) ``git ls-files --others --exclude-standard`` — currently-untracked, non-ignored files,
    which are exactly the NEW files a session created (a plain ``git diff`` misses them, and
    they are the most capture-worthy signal). ``untracked`` accepts a precomputed (b) so one
    capture pass runs ``ls-files`` once, not per consumer. Returns ``[]`` on any failure (not a
    git repo, an unreachable watermark after a squash-merge, …) — never raises.
    """
    if not repo_root:
        return []
    paths = set()
    if head_commit:
        for ln in run_git(["diff", "--name-only", head_commit], repo_root).splitlines():
            if ln.strip():
                paths.add(ln.strip())
    paths.update(_git_untracked(repo_root) if untracked is None else untracked)
    return sorted(paths)[:_MAX_CHANGED_PATHS]


def _truncate_on_line_boundary(text: str, max_bytes: int) -> str:
    """Cut ``text`` to ``max_bytes`` (utf-8) at a line boundary, with a legible marker."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[:max_bytes].decode("utf-8", errors="ignore")
    kept = cut.rsplit("\n", 1)[0] if "\n" in cut else ""
    return kept + f"\n… (diff truncated at {max_bytes} bytes)"


def _strip_binary_sections(diff_text: str) -> str:
    """Drop per-file sections of a git diff that describe binary content.

    A binary file's section carries no reviewable hunk lines ("Binary files … differ" or a
    "GIT binary patch" blob) — verbatim evidence is for TEXT. Sections are the
    ``diff --git …`` units; non-section preamble (shouldn't occur) passes through unchanged.
    """
    if "diff --git " not in diff_text:
        return diff_text
    parts = diff_text.split("\ndiff --git ")
    # Re-attach the split marker to every section after the first, then filter.
    sections = [parts[0]] + ["diff --git " + p for p in parts[1:]]
    kept = [
        s
        for s in sections
        if s.strip() and "\nBinary files " not in "\n" + s and "GIT binary patch" not in s
    ]
    return "\n".join(kept)


def _git_diff_hunks(
    head_commit: Optional[str],
    repo_root: Optional[str],
    untracked: Optional[List[str]] = None,
    *,
    max_bytes: int = _MAX_HUNK_BYTES,
) -> str:
    """Bounded VERBATIM diff hunks since the watermark — the seed's quotable evidence (GRW-1).

    Two sources, mirroring ``_git_changed_paths``'s superset semantics: (a) tracked changes via
    ``git diff --unified=3 -M <head_commit>`` (working tree vs the watermark, renames
    detected); (b) UNTRACKED files — which a plain ``git diff`` misses and which are the
    highest-value evidence — rendered per-path via ``git diff --no-index /dev/null <path>``
    (``run_git`` ignores the nonzero found-a-difference exit, so stdout survives). Binary
    sections are dropped; the concatenation is sliced to ``max_bytes`` ON A LINE BOUNDARY with
    a legible truncation marker (``run_git`` itself imposes no cap). ``""`` when there is
    nothing to quote or on any failure — never raises.
    """
    if not repo_root:
        return ""
    try:
        pieces: List[str] = []
        total = 0
        if head_commit:
            tracked = _strip_binary_sections(
                run_git(["diff", "--unified=3", "-M", head_commit], repo_root)
            ).strip("\n")
            if tracked:
                pieces.append(tracked)
                total += len(tracked.encode("utf-8"))
        for path in _git_untracked(repo_root) if untracked is None else untracked:
            if total > max_bytes:
                break  # already past the cap — the slice below owns the final boundary
            piece = _strip_binary_sections(
                run_git(["diff", "--no-index", "--", os.devnull, path], repo_root)
            ).strip("\n")
            if piece:
                pieces.append(piece)
                total += len(piece.encode("utf-8"))
        return _truncate_on_line_boundary("\n".join(pieces).strip("\n"), max_bytes)
    except Exception:
        return ""


def _session_decisions(session_id: Optional[str], telemetry_dir: Optional[str]) -> List[str]:
    """This session's user-confirmed decisions from the GRW-4 ledger, deduped and bounded.

    Matching mirrors the episode isolation above exactly (``d.get("session_id") ==
    session_id`` — the two ledgers share ``log_*``'s keying), so a decision recorded
    mid-session lands in the SAME seed as that session's episodes. Never raises.
    """
    out: List[str] = []
    try:
        seen = set()
        for d in read_decisions(telemetry_dir):
            if d.get("session_id") != session_id:
                continue
            t = (d.get("text") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= _MAX_DECISIONS:
                break
    except Exception:
        return out
    return out


def _seed_salience(seed: Dict, telemetry_dir: Optional[str], untracked: List[str]) -> Dict:
    """A cheap per-seed VALUE LABEL (GRW-1) — never a gate, never an auto-prune.

    Scores the signals a drain reviewer would weigh anyway: new files created (the strongest
    capture-worthy evidence), a commit landing during the session, query breadth, and queries
    that also show up in the abstention backlog (the user asked; hippo had nothing — exactly
    what a new memory would fix). The formula is deliberately simple and documented here; the
    score ORDERS the pending listing best-first and labels no-op sessions ``trivial`` — it
    never gates or prunes a seed. Never raises.
    """
    new_files = len(untracked)
    commit_landed = bool(
        seed.get("head") and seed.get("head_commit") and seed["head"] != seed["head_commit"]
    )
    distinct_queries = len(seed.get("query_previews") or [])
    abstained = 0
    try:
        previews = set(seed.get("query_previews") or [])
        if previews:
            for cluster in abstention_backlog(telemetry_dir):
                if previews.intersection(cluster.get("queries") or []):
                    abstained += 1
    except Exception:
        abstained = 0
    score = (
        2 * new_files
        + (3 if commit_landed else 0)
        + 2 * abstained
        + (1 if distinct_queries >= 3 else 0)
        + (1 if seed.get("changed_paths") else 0)
    )
    return {
        "score": score,
        "new_files": new_files,
        "commit_landed": commit_landed,
        "distinct_queries": distinct_queries,
        "abstained_queries": abstained,
        "trivial": score == 0,
    }


def gather_session_context(
    session_id: Optional[str],
    *,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
    include_hunks: bool = True,
) -> Optional[Dict]:
    """Replay the episode buffer for ``session_id`` into a capture-seed dict, or ``None``.

    Reads ``episode_buffer.jsonl`` (via ``telemetry.read_episodes``), keeps only this session's
    episodes, derives the HEAD watermark (the earliest recorded ``head_commit``), unions the
    recalled names, collects the (already-truncated) query previews, and diffs the repo since
    the watermark — including bounded VERBATIM diff hunks (GRW-1) so a drafted memory can quote
    its evidence instead of paraphrasing. ``include_hunks=False`` skips the hunk subprocesses
    for read-only consumers (the SessionStart resume card) that never persist the seed. Returns
    ``None`` when the session left no episodes to replay — there is nothing to capture, so no
    empty seed is written. Never raises: any failure yields ``None``.
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
        untracked = _git_untracked(repo_root)
        hunks = (
            _git_diff_hunks(watermark, repo_root, untracked) if include_hunks else ""
        )
        # MANDATORY lint (GRW-1 invariant): verbatim hunks widen the secret-exposure surface,
        # so every hunk-bearing seed is scanned AT CAPTURE. A hit only FLAGS the seed (the
        # queue is gitignored, same trust domain as the episode buffer) — the consolidate
        # drain refuses to fence flagged hunks into a corpus body, and write_memory's own
        # lint is the backstop behind that.
        # SEN-2: the poisoning-payload twin. hunks_threat_flagged rides beside
        # hunks_secret_flagged — a Tier-A hit (invisible Unicode / confusable / exfil shape /
        # HTML comment) in the captured evidence flags the seed identically (absent when
        # clean, ED-4). Tier-B imperative grammar is MEASURED to the dark ledger below, never
        # a seed field. Additive fields, no _SEED_SCHEMA bump (read defensively via .get()).
        threats = scan_threats(hunks) if hunks else {"tier_a": [], "tier_b": []}
        seed = {
            "schema": _SEED_SCHEMA,
            "kind": "session-capture",
            "session_id": session_id,
            "head_commit": watermark,  # the session's starting watermark (from the buffer)
            "head": head_now,          # HEAD at capture time — the two bound the diff range
            "changed_paths": _git_changed_paths(watermark, repo_root, untracked),
            "recalled_names": names[:_MAX_RECALLED_NAMES],
            "query_previews": previews[:_MAX_QUERY_PREVIEWS],
            "episode_count": len(episodes),
            "earliest_ts": episodes[0].get("ts"),
            "diff_hunks": hunks,
            "hunks_secret_flagged": bool(hunks and scan_text(hunks)),
            "hunks_threat_flagged": bool(threats["tier_a"]),
            # GRW-4: the session's user-confirmed WHY, replayed from the in-session decision
            # ledger (memory.capture --add-decision) with the SAME session matching as the
            # episodes above — agent-recorded transcription only, never synthesized here.
            "decisions": _session_decisions(session_id, telemetry_dir),
        }
        seed["salience"] = _seed_salience(seed, telemetry_dir, untracked)
        return seed
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
        # CAP-LLM (opt-in, default OFF): one bounded triage call annotates the seed with
        # SUGGESTED fields the drain reviewer still ratifies per item. The PRIOR seed at
        # this same per-session path rides along so an unchanged-evidence re-capture
        # (SubagentStop×N, then SessionEnd) carries the suggestions over instead of
        # re-billing. Lazy import behind the flag + its own catch: a triage failure of
        # ANY kind (no key, timeout, junk response) leaves this seed exactly as built
        # above — the fail-open contract.
        try:
            from .capture_triage import enrich_seed, triage_enabled

            if triage_enabled():
                prior = None
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        prior = json.load(fh)
                except Exception:
                    prior = None
                enrichment = enrich_seed(seed, memory_dir, repo_root=repo_root, prior=prior)
                if enrichment:
                    seed["llm_triage"] = enrichment
        except Exception:
            pass
        tmp = path + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # atomic: a reader never sees a half-written seed
        # SEN-2 Tier-B: measure imperative-grammar in the captured evidence to the dark
        # ledger — never a seed field, never surfaced (inv3). SessionEnd, off the hot path.
        try:
            hunks = seed.get("diff_hunks") or ""
            if hunks:
                log_threat_findings(
                    scan_tier_b(hunks), source="capture",
                    name=seed.get("session_id"), telemetry_dir=telemetry_dir,
                    session_id=session_id,
                )
        except Exception:
            pass
        # CAP-6: self-bound the queue so an un-drained backlog can't grow without limit. Prune
        # keeps the highest-value seeds (recency breaks ties), so a just-written seed survives
        # UNLESS the queue is already full of strictly higher-value captures — in which case a
        # trivial new seed yields to them. Value-first, not FIFO: the queue keeps what a drain
        # would lead with.
        prune_pending(pd, max_seeds=_MAX_PENDING_SEEDS)
        return path
    except Exception:
        return None


def _seed_score(seed: Dict) -> int:
    """The stored salience score of a seed; 0 for pre-GRW-1 (schema 1) seeds. Never raises."""
    try:
        return int((seed.get("salience") or {}).get("score", 0))
    except Exception:
        return 0


def read_pending(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> List[Dict]:
    """Every pending capture seed, HIGH-VALUE FIRST (GRW-1), then by filename for stability.

    The salience score only ORDERS the review queue so a deep backlog leads with the sessions
    most worth drafting — a low score never drops a seed (label, not gate). Skips corrupt
    files. Never raises.
    """
    out: List[Dict] = []
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return []
        for name in sorted(os.listdir(pd)):
            # Seeds are ``capture-*.json``; skip dotfiles (the ``.gitignore`` and the CAP-6
            # ``.capture-snooze.json`` marker are queue state, never seeds).
            if name.startswith(".") or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(pd, name), "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict):
                    obj["_path"] = os.path.join(pd, name)
                    out.append(obj)
            except Exception:
                continue
        out.sort(key=lambda s: (-_seed_score(s), os.path.basename(s.get("_path", ""))))
    except Exception:
        return out
    return out


def corrupt_pending(
    pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None
) -> List[str]:
    """Seed FILENAMES in the queue that ``read_pending`` cannot parse. Never raises.

    RCH-9: a corrupt seed silently vanished from the drain listing while the bare
    file count (``pending_count``, the SessionStart nudge) still included it — the
    queue said "2 pending", the listing showed one, and a captured session was lost
    without a trace. The listing names what it cannot read; deleting or inspecting
    the file is the human's call (the queue is gitignored ephemera).
    """
    out: List[str] = []
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return []
        for name in sorted(os.listdir(pd)):
            if name.startswith(".") or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(pd, name), "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if not isinstance(obj, dict):
                    out.append(name)
            except Exception:
                out.append(name)
    except Exception:
        return out
    return out


def pending_count(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> int:
    """Number of pending capture seeds (cheap listdir). Never raises."""
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        return sum(1 for n in os.listdir(pd) if n.endswith(".json") and not n.startswith("."))
    except Exception:
        return 0


def discard_pending(path: str) -> bool:
    """Remove one drained/approved/dismissed seed from the queue. True on success. Never raises."""
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def _seed_captured_at(seed: Dict) -> float:
    """A seed's capture timestamp for recency ordering; falls back to earliest_ts, then 0.0."""
    for key in ("captured_at", "earliest_ts"):
        val = seed.get(key)
        try:
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
        except Exception:
            pass
    return 0.0


def prune_pending(
    pending_dir: Optional[str] = None,
    *,
    memory_dir: Optional[str] = None,
    max_seeds: int = _MAX_PENDING_SEEDS,
) -> int:
    """Bound the queue at ``max_seeds`` — drop the LOWEST-value, then OLDEST, seeds past the cap.

    A pending seed is gitignored ephemera awaiting review; a queue that grows one-seed-per-session
    without limit is itself a soak the LIF goal forbids. Keeps the ``max_seeds`` a drain would
    lead with — ranked by ``(salience score desc, captured_at desc)`` — so a just-written seed
    (the newest ``captured_at``) always survives a same-score tie, giving a rolling window rather
    than a hard stop that would silently swallow new captures. Returns the number pruned. A
    label/order operation on the queue, NEVER on a seed's fate in the corpus. Never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        seeds = read_pending(pd)
        if len(seeds) <= max_seeds:
            return 0
        ranked = sorted(seeds, key=lambda s: (-_seed_score(s), -_seed_captured_at(s)))
        pruned = 0
        for seed in ranked[max_seeds:]:
            if discard_pending(seed.get("_path", "")):
                pruned += 1
        return pruned
    except Exception:
        return 0


def _snooze_marker_path(pending_dir: str) -> str:
    return os.path.join(pending_dir, _SNOOZE_MARKER)


def snooze_queue(
    pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None
) -> bool:
    """Defer the SessionStart pending-capture nudge for ``_SNOOZE_WINDOW_SESSIONS`` sessions.

    Writes a timestamp marker inside the gitignored pending dir. The seeds are UNTOUCHED — this
    quiets only the nudge, and only until it ages out (parity with the reconsolidation snooze:
    a deferral, never a dismissal). Returns True on success. Never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        ensure_self_ignoring_dir(pd)
        marker = _snooze_marker_path(pd)
        tmp = marker + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": round(time.time(), 3)}, fh)
        os.replace(tmp, marker)
        return True
    except Exception:
        return False


def queue_snoozed(
    pending_dir: Optional[str] = None,
    *,
    memory_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> bool:
    """True while an explicit queue snooze is younger than ``_SNOOZE_WINDOW_SESSIONS`` sessions.

    Ages by SESSIONS, not wall-clock, exactly like ``reconsolidate._snoozed_names``: each recall-
    ledger session whose first ts-carrying event lands after the ack counts once, and the snooze
    expires once ``_SNOOZE_WINDOW_SESSIONS`` such sessions have started. Degrades toward
    RE-NAGGING, never silence: a missing/corrupt marker, a ts-less ack, or an unreadable ledger
    all read as "not snoozed". Read-only; never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        marker = _snooze_marker_path(pd)
        if not os.path.isfile(marker):
            return False
        with open(marker, "r", encoding="utf-8") as fh:
            acked = float((json.load(fh) or {}).get("ts") or 0.0)
        if acked <= 0:
            return False
        if telemetry_dir is None and memory_dir is not None:
            telemetry_dir = default_telemetry_dir(memory_dir)
        from .telemetry import read_events

        first_ts: Dict[str, float] = {}
        for e in read_events(telemetry_dir):
            sid, ts = e.get("session_id"), e.get("ts")
            if (
                sid
                and sid not in first_ts
                and isinstance(ts, (int, float))
                and not isinstance(ts, bool)
            ):
                first_ts[sid] = float(ts)
        started_since = sum(1 for s in first_ts.values() if s > acked)
        return started_since < _SNOOZE_WINDOW_SESSIONS
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
        sal = s.get("salience") or {}
        if sal:
            out.append(
                f"      value: {sal.get('score', 0)}"
                + (" (trivial session)" if sal.get("trivial") else "")
            )
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
        hunks = s.get("diff_hunks") or ""
        if hunks:
            out.append(f"      evidence: {len(hunks.encode('utf-8'))} bytes of verbatim diff hunks")
            if s.get("hunks_secret_flagged"):
                out.append(
                    "      ⚠ secret lint flagged these hunks — do NOT fence them into a memory "
                    "body without scrubbing (run memory.secrets.scan_with_remediation first)"
                )
            if s.get("hunks_threat_flagged"):
                out.append(
                    "      ⚠ threat lint flagged these hunks (SEN-2 Tier-A: invisible Unicode / "
                    "confusable / exfil shape / HTML comment) — inspect before fencing into a "
                    "body (run memory.threat_lint.scan_tier_a on the exact lines)"
                )
        dec = s.get("decisions") or []
        if dec:
            out.append(f"      decisions: {'; '.join(str(d) for d in dec[:5])}")
        tri = s.get("llm_triage") or {}
        if tri:
            out.append(
                f"      triage (LLM suggestion — ratify or discard at drain): "
                f"type={tri.get('suggested_type') or '?'}  name={tri.get('suggested_name') or '?'}"
            )
            if tri.get("draft_description"):
                out.append(f"        draft description: {tri['draft_description']}")
            dups = tri.get("llm_duplicate_flags") or []
            if dups:
                out.append(f"        possible duplicates (LLM 2nd opinion): {', '.join(dups[:5])}")
            dc = tri.get("dup_check") or {}
            if dc.get("neighbors"):
                shown = ", ".join(
                    f"{n.get('name')} ({n.get('score')})" for n in dc["neighbors"][:3]
                )
                out.append(f"        index dup check: route={dc.get('route')} — {shown}")
            elif dc.get("route"):
                out.append(f"        index dup check: route={dc.get('route')}")
            if tri.get("secret_flagged"):
                out.append(
                    "        ⚠ secret lint flagged the triage text — scrub before any corpus use"
                )
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
    parser.add_argument(
        "--discard",
        "--dismiss",
        dest="discard",
        default=None,
        metavar="PATH",
        help="remove ONE seed by path — after it is approved/skipped in the drain, or to dismiss "
        "a capture you don't want kept (the two are the same op: the seed leaves the queue)",
    )
    parser.add_argument(
        "--snooze",
        action="store_true",
        help="CAP-6: defer the SessionStart pending-capture nudge for "
        f"{_SNOOZE_WINDOW_SESSIONS} sessions (the seeds stay; only the nudge quiets, then re-nags)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=f"CAP-6: bound the queue to the {_MAX_PENDING_SEEDS} highest-value/newest seeds now "
        "(runs automatically on every capture; this forces it)",
    )
    parser.add_argument(
        "--add-decision",
        default=None,
        metavar="TEXT",
        help="GRW-4: record ONE user-confirmed session decision (quote or faithfully "
        "paraphrase what the USER stated — never infer one from the diff); it lands in this "
        "session's SessionEnd capture seed as its durable WHY",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        if args.add_decision is not None:
            from .telemetry import log_decision

            td = default_telemetry_dir(args.memory_dir) if args.memory_dir else None
            ok = log_decision(args.add_decision, telemetry_dir=td, session_id=args.session_id)
            print(
                "decision recorded — it will ride this session's capture seed"
                if ok
                else "nothing recorded (empty text or unwritable ledger)"
            )
            return 0
        if args.list:
            print(_format_listing(read_pending(memory_dir=args.memory_dir)))
            return 0
        if args.snooze:
            ok = snooze_queue(memory_dir=args.memory_dir)
            print(
                f"pending-capture nudge snoozed for {_SNOOZE_WINDOW_SESSIONS} sessions "
                "(seeds kept; the nudge re-nags after it expires)"
                if ok
                else "could not record the snooze (unwritable pending dir)"
            )
            return 0
        if args.prune:
            n = prune_pending(memory_dir=args.memory_dir)
            print(
                f"pruned {n} low-value/old seed(s) — queue bounded to {_MAX_PENDING_SEEDS}"
                if n
                else f"nothing to prune (queue is within the {_MAX_PENDING_SEEDS}-seed bound)"
            )
            return 0
        if args.discard:
            ok = discard_pending(args.discard)
            print(f"discarded: {args.discard}" if ok else f"nothing to discard at {args.discard}")
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
            # T18 FLT-1: a genuinely ENDING session clears its own presence doc (a crash
            # ages out via TTL instead). SubagentStop rides this same entry point with
            # the PARENT's session_id (--reason subagent-stop) — the parent is still
            # live, so its doc must survive.
            if reason != "subagent-stop":
                try:
                    from .presence import clear_presence

                    clear_presence(args.memory_dir, session_id=session_id)
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
