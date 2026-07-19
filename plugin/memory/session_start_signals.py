"""Staleness, governance & positive-block producers for the SessionStart dispatcher —
decomposed out of ``session_start.py`` (REL-1), which keeps the ``PRODUCERS`` registry,
the dispatcher (``build_context``/``main``), and explicit re-exports of every moved name.

The LIF-6 staleness note (VOL-1/LIF-1/TMB-2 aware), the CAP-2 pending-capture nudge, the
SIG-3 blind-spot backlog, the RUL-1/RUL-2 rules-plane radars, the GOV-1 contradiction
inbox, GOV-4 floor & corpus change governance, and the two positive blocks — SIG-1
relevant-to-work and SIG-2 resume card. Producers keep the uniform
``(memory_dir, repo_root, ctx)`` call shape (LIF-6) even where ``ctx`` is unused.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from .recall import _INVALIDATION_RECENT_DAYS, _invalidation_state
from .session_start_health import _MAX_ITEMS_PER_PRODUCER, _periodic_nudge_should_fire
from .staleness import (
    RunContext,
    find_stale,
    invalid_after_map,
    nondrift_old_invalidated,
)
from .staleness_policy import (
    split_volatile_only,
    stale_note_all_suppressed,
    stale_note_tail,
    volatile_set,
)


def staleness_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """LIF-6: ``ctx`` (the dispatcher's shared ``RunContext``) already carries a single
    ``find_stale`` call's result plus the reconsolidation worklist derived from it — this
    producer reads both instead of re-deriving them. When ``ctx`` is ``None`` (a standalone
    call, e.g. a test that stubs only ``find_stale``), it derives its own staleness view
    exactly as before LIF-6, with no worklist to exclude (there's nothing to de-duplicate
    against outside the dispatcher).

    VOL-1: registry-listed volatile paths never arm this note alone — every downstream
    count reads ARMED items only; the suppressed residue gets its own honest line (a
    volatile-only item that also carries invalid_after counts as suppressed, not demoted).
    """
    if ctx is not None:
        stale = ctx.stale
        diagnostics = ctx.stale_diagnostics
        worklist_names = {item["name"] for item in ctx.worklist}
    else:
        # find_stale already orders most-recently-drifted first.
        diagnostics = {}
        stale = find_stale(memory_dir, repo_root, diagnostics=diagnostics)
        worklist_names = set()
    # SHP-6: a scoped git-log scan can still time out on a pathologically large cited-path
    # set; surface that instead of silently reporting "nothing stale" (legible degradation).
    timeout_note = (
        "⚠ staleness scan timed out — signal may be incomplete"
        if diagnostics.get("timed_out")
        else None
    )
    # TMB-2: the CORPUS-WIDE terminal-state count — retirements the drift signal cannot
    # see (invalid_after past the old horizon, NO cited-code drift: the supersede/merge
    # case). Before this, such a memory signaled nowhere: not here (the invalid_after map
    # below is stale-scoped), not archive_candidates (stale-gated 4-way). Cheap-at-zero:
    # an empty result adds zero output and the producer stays byte-identical to pre-TMB-2.
    nondrift_old = nondrift_old_invalidated(
        memory_dir, [item["name"] for item in stale]
    )
    retired_line = None
    if nondrift_old:
        names = sorted(nondrift_old)
        shown = ", ".join(names[:4])
        more = f" (+{len(names) - 4} more)" if len(names) > 4 else ""
        retired_line = (
            f"♻ {len(names)} memor{'y' if len(names) == 1 else 'ies'} retired OUTSIDE the "
            f"drift signal — invalid_after past the {int(_INVALIDATION_RECENT_DAYS)}-day "
            f"horizon with no cited-code drift (supersede/merge retirements): {shown}{more} "
            "— recall already filters them; now archivable via the /hippo:audit archive "
            "flow (`python -m memory.archive`), reinstatable per item via reconsolidate "
            "outcome=graduate|fix."
        )
    # VOL-1: partitioned AFTER nondrift_old (suppressed items DO have drift — not retired);
    # anything the worklist armed anyway (the CLB-3 evidence lane) renders THERE, not here.
    stale, vol_sup = split_volatile_only(stale, volatile_set(memory_dir))
    vol_sup = [i for i in vol_sup if i["name"] not in worklist_names]
    vol_line = (stale_note_all_suppressed if not stale else stale_note_tail)(len(vol_sup)) if vol_sup else None
    if not stale:
        extra = [ln for ln in (vol_line, retired_line, timeout_note) if ln]
        return "\n".join(extra) if extra else None
    # LIF-1: a stale entry ALREADY carrying invalid_after is in demote's terminal state —
    # the verdict (or a manual --invalidate) closed its validity window and recall's
    # pre-cut penalty is already ranking it down, so a per-item "verify this" line here is
    # the staleness/demote double-nag this item closes. Suppress those LINES but keep the
    # COUNT visible (legible degradation applies to suppression too: nothing may silently
    # disappear), and — once an invalidation ages past recall's old horizon — point at the
    # audit skill's archive flow (report-only; the flow applies its own 4-way gate).
    invalidated = invalid_after_map([item["name"] for item in stale], memory_dir)
    # LIF-6: a name already on the reconsolidation worklist gets its per-item line THERE
    # (worklist first) — re-listing it here would be the exact double-nag this item closes,
    # wasting the shared 9000-char budget on the same drifted memory twice. Suppress the
    # LINE but keep it in the honest tail count below (mirrors LIF-1's invalidated tail —
    # nothing silently disappears; invalidated/worklist are disjoint by construction, since
    # recalled_stale_worklist already drops invalidated names itself).
    active = [
        item for item in stale if item["name"] not in invalidated and item["name"] not in worklist_names
    ]
    if active:
        lines = [
            f"⚠ Memory staleness — {len(active)} memories cite code that changed since they were "
            "written (most-recently-drifted first); verify against current code before relying on them:"
        ]
        for item in active[:_MAX_ITEMS_PER_PRODUCER]:
            paths = ", ".join(item["changed_paths"][:4])
            more = "" if len(item["changed_paths"]) <= 4 else f" (+{len(item['changed_paths']) - 4} more)"
            lines.append(f"  • {item['name']}: {paths}{more}")
        if len(active) > _MAX_ITEMS_PER_PRODUCER:
            lines.append(f"  …and {len(active) - _MAX_ITEMS_PER_PRODUCER} more.")
        if worklist_names:
            lines.append(
                f"  (+{len(worklist_names)} already on the reconsolidation worklist below — "
                "no separate re-verify needed here)"
            )
        if invalidated:
            lines.append(
                f"  (+{len(invalidated)} already demoted — invalid_after set; recall already "
                "ranks them down, no re-verify needed)"
            )
    elif worklist_names and not invalidated:
        lines = [
            f"⚠ Memory staleness — all {len(stale)} stale memories are already on the "
            "reconsolidation worklist below; nothing new to verify here."
        ]
    elif worklist_names:
        lines = [
            f"⚠ Memory staleness — all {len(stale)} stale memories are already accounted for "
            f"({len(invalidated)} demoted, {len(worklist_names)} on the reconsolidation worklist "
            "below); nothing new to verify here."
        ]
    else:
        lines = [
            f"⚠ Memory staleness — all {len(stale)} stale memories are already demoted "
            "(invalid_after set); recall already ranks them down, nothing new to verify."
        ]
    if vol_line:
        lines.append(vol_line)  # VOL-1: suppression is visible, never silent
    old = sorted(
        name
        for name, ia in invalidated.items()
        if _invalidation_state({"invalid_after": ia}) == "old"
    )
    if old:
        shown = ", ".join(old[:4])
        more = f" (+{len(old) - 4} more)" if len(old) > 4 else ""
        lines.append(
            f"  ({len(old)} demoted past the {int(_INVALIDATION_RECENT_DAYS)}-day "
            f"old-invalidation horizon — consider the /hippo:audit archive flow: {shown}{more})"
        )
    if retired_line:
        lines.append(retired_line)  # TMB-2: the non-drift retirements (never in `stale`)
    if timeout_note:
        lines.append(timeout_note)
    return "\n".join(lines)


def pending_capture_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """Surface the CAP-2 pending-capture queue so it never soaks silently.

    The SessionEnd draft-capture pass (``memory.capture``) snapshots a prior session's episode
    buffer + ``git diff`` into the gitignored ``.claude/.memory-pending/`` queue. This nudge
    makes that queue LEGIBLE (guiding invariant: every silent-fallback/soak path gains a
    user-visible signal) and routes to the deliberate, per-item drain — NOTHING in the queue is
    in the corpus until the agent explicitly approves each candidate. Self-clearing: it goes
    silent once the seeds are drained/discarded. Since GRW-1 the nudge also labels how many of
    the queued seeds look TRIVIAL (salience score 0 — no changes, no commit, no unanswered
    queries) so a deep queue reads as "what's worth drafting", not an undifferentiated chore.
    ``ctx`` (LIF-6) is unused.
    """
    try:
        from .capture import default_pending_dir, pending_count, queue_snoozed

        pd = default_pending_dir(memory_dir)
        n = pending_count(pd)
    except Exception:
        return None
    if not n:
        return None
    # CAP-6: an explicit ``--snooze`` quiets this nudge for a bounded number of sessions (parity
    # with the reconsolidation snooze). The queue is untouched and the nudge re-nags once the
    # snooze ages out — "nothing nags forever" now cuts BOTH ways (it also never nags-forever).
    try:
        if queue_snoozed(pd, memory_dir=memory_dir):
            return None
    except Exception:
        pass
    trivial = 0
    try:
        from .capture import read_pending

        trivial = sum(1 for s in read_pending(pd) if (s.get("salience") or {}).get("trivial"))
    except Exception:
        trivial = 0
    label = f" ({trivial} trivial)" if trivial else ""
    # INV-1: the deferral must name RUNNABLE forms — the old text said `hippo capture
    # --snooze`, a bin/hippo subcommand that does not exist (the INT-18 class; the
    # surface-registry lint now fails on any such reference). The capture tool serves
    # both surfaces; the terminal CLI spelling is `-m memory.capture --snooze`.
    return (
        f"📥 {n} pending capture(s){label} from a prior session await review — run "
        "/hippo:consolidate to draft them into memory (nothing is saved until you approve "
        "each one, per item), or defer this nudge with the capture tool (action='snooze'; "
        "in a terminal: `--snooze` on `python -m memory.capture`)."
    )


# SIG-3: how many recurring blind-spot lines the SessionStart nudge shows, and how rarely it
# fires. The doctor blind-spot check is the always-available surface; SessionStart is the RARE
# nudge (every _BLIND_SPOT_NUDGE_EVERY-th session that has a backlog).
_BLIND_SPOT_NUDGE_EVERY = 5
_MAX_BLIND_SPOT_LINES = 2


def blind_spot_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SIG-3: turn silent recall abstention into a low-frequency curation backlog.

    RET-1 correctly injects NOTHING when nothing clears the floor — but that abstention was
    invisible, so the corpus never learned what it keeps being ASKED and can't answer. This reads
    the gitignored recall ledger (``telemetry.abstention_backlog``), clusters recurring
    ``backend='none'`` queries, and surfaces the top blind spot(s): 'you asked "X" N× recently;
    no memory above the floor — capture one', routing to /hippo:consolidate. One-off/diverse
    abstentions never cluster, so they never nag; recurring ones surface only every
    ``_BLIND_SPOT_NUDGE_EVERY``-th session (the doctor blind-spot check is the always-available
    surface). Read-only aggregation of the gitignored ledger (inv1); loud at doctor/SessionStart
    while the hook stays silent (inv2/inv3); no writes (inv4). Clone-local: the ledger is
    per-clone (and abstentions are only logged for trusted, telemetry-opted-in corpora, so the
    backlog reflects THOSE). ``ctx`` (LIF-6) is unused.
    """
    try:
        from .telemetry import abstention_backlog, default_telemetry_dir

        backlog = abstention_backlog(default_telemetry_dir(memory_dir))
        if not backlog:
            return None
        # Gate AFTER confirming there IS a backlog, so the cadence counts only backlog-bearing
        # sessions (mirrors the untrusted-corpus nudge's gate-among-eligible-sessions pattern).
        if not _periodic_nudge_should_fire(
            repo_root, slug="blind-spot", every=_BLIND_SPOT_NUDGE_EVERY
        ):
            return None
        lines = [
            "🔎 Recall blind spots — recurring questions your corpus can't answer (asked, but "
            "nothing cleared the floor). Capture one via /hippo:consolidate:"
        ]
        for c in backlog[:_MAX_BLIND_SPOT_LINES]:
            q = c.get("sample_query") or ", ".join(c.get("terms") or [])
            lines.append(f'  • "{q}" — asked {c["count"]}× recently, no memory above the floor')
        return "\n".join(lines)
    except Exception:
        return None


# RUL-1: how many rule↔memory conflict lines the radar shows before folding into a count.
# First-class loud (fires every session findings exist, like citation_rot — a live conflict
# between the always-loaded rules plane and the corpus should not wait for a nudge cadence).
_MAX_RULES_CONFLICT_LINES = 4


def rules_conflict_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """RUL-1: the rule↔memory conflict radar — governance cites what the corpus disputes.

    Generalizes the audit skill's authority-gap join into a standing producer over
    ``rules_plane.conflict_radar``: the TYPED-EDGE leg (a rule cites a memory another memory
    ``supersedes``/``contradicts`` — both files named) and the STRENGTH leg (a rule cites a
    memory no session ever recalls, strength < 0.15 — gated on the 5-session soak bar so a
    fresh clone is never nagged; /hippo:audit keeps the ungated join). Findings route to a
    per-item human decision via /hippo:consolidate — nothing auto-resolves (inv4). Read-only
    over user-owned governance files (inv1); off the hot path (inv6); loud (inv3).
    ``ctx`` (LIF-6) is unused.
    """
    try:
        from .rules_plane import conflict_radar

        radar = conflict_radar(memory_dir, repo_root)
        conflicts = radar["edge_conflicts"]
        gaps = radar["authority_gaps"]
        if not conflicts and not gaps:
            return None
        lines = [
            "⚖ Rule↔memory conflicts — governance files cite memories the corpus disputes. "
            "Decide per item via /hippo:consolidate (nothing auto-resolves):"
        ]
        entries: List[str] = []
        for c in conflicts:
            entries.append(
                f"  • {c['cited_by'][0]} cites `{c['name']}` but `{c['by']}` "
                f"{c['relation']} it — reconcile the rule with the newer memory"
            )
        for g in gaps:
            entries.append(
                f"  • {g['cited_by'][0]} cites `{g['name']}` but no session recalls it "
                f"(strength {g['strength']:.2f}) — verify it still matters"
            )
        lines.extend(entries[:_MAX_RULES_CONFLICT_LINES])
        overflow = len(entries) - _MAX_RULES_CONFLICT_LINES
        if overflow > 0:
            lines.append(f"  … and {overflow} more — run /hippo:doctor for the full list.")
        return "\n".join(lines)
    except Exception:
        return None


# RUL-2: how many rules-plane rot lines show before folding into a count. Same loud family
# as citation_rot — the rules plane is ALWAYS-LOADED, so a rotten reference there misleads
# every session until fixed.
_MAX_RULES_ROT_LINES = 4


def rules_rot_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """RUL-2: staleness/citation-rot applied to the rules plane itself.

    The governance plane has ZERO staleness tracking of its own: a CLAUDE.md backtick
    reference to a moved file, a ``module.symbol`` that no longer exists, or a
    ``.claude/rules`` ``paths:`` glob scoping a deleted tree (the lazy-load feature RUL-0
    confirmed) silently wastes always-loaded context and misleads. This surfaces
    ``rules_plane.rules_rot`` findings with the exact file + reference so the fix is a
    per-item human edit — hippo never rewrites a governance file (inv1/inv4). Loud like
    citation_rot (inv3); off the hot path (inv6). ``ctx`` (LIF-6) is unused.
    """
    try:
        from .rules_plane import rules_rot

        rot = rules_rot(repo_root)
        code_rot = rot["code_ref_rot"]
        dead_globs = rot["dead_path_globs"]
        if not code_rot and not dead_globs:
            return None
        lines = [
            "🧭 Rules-plane rot — governance files reference code that left the tree. "
            "Fix per item (hippo names the reference, you edit the file):"
        ]
        entries: List[str] = []
        for r in code_rot:
            what = "path no longer in the repo" if r["kind"] == "path" else "symbol no longer defined"
            entries.append(f"  • {r['file']} references `{r['ref']}` — {what}")
        for d in dead_globs:
            entries.append(
                f"  • {d['file']} scopes paths: '{d['glob']}' — matches nothing, the rule can never load"
            )
        lines.extend(entries[:_MAX_RULES_ROT_LINES])
        overflow = len(entries) - _MAX_RULES_ROT_LINES
        if overflow > 0:
            lines.append(f"  … and {overflow} more — run /hippo:doctor for the full list.")
        return "\n".join(lines)
    except Exception:
        return None


# GOV-1: how many contradiction pairs the inbox lists before folding into a count. Same
# loud family as rules_conflict — a live contradiction means the model is being injected
# both sides of a dispute, which should not wait for a nudge cadence.
_MAX_CONTRADICTION_LINES = 4


def contradiction_inbox_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """GOV-1: the standing contradiction inbox — every unresolved ``contradicts`` pair.

    A ``contradicts`` edge deliberately demotes neither side (GRA-4), so before this its
    only surface was the hot-path annotation, visible IF both sides co-surfaced in one
    recall — a live conflict could sit unresolved forever. This enumerates ALL pairs
    corpus-wide (``LinkGraph.all_typed_edges`` via ``resolve_view``) minus the per-clone
    resolved ledger, and routes each to a per-item human verdict via /hippo:resolve —
    nothing auto-picks a winner (inv4). T2 boundary: ``rules_conflict_producer`` above
    already prints the GOVERNANCE-cited subset loudly, and producers share no state, so
    this one re-derives that subset from ``conflict_radar`` and skips re-PRINTING those
    pairs (no double nag) while still COUNTING them — the header is the whole inbox.
    Read-only (inv1); empty-is-fine; off the hot path (inv6). ``ctx`` (LIF-6) is unused.
    """
    try:
        from .resolve_view import unresolved_contradictions

        inbox = unresolved_contradictions(memory_dir, repo_root=repo_root)
        if not inbox:
            return None
        radar_pairs = set()
        try:
            from .rules_plane import conflict_radar

            radar = conflict_radar(memory_dir, repo_root)
            for c in radar["edge_conflicts"]:
                if c.get("relation") == "contradicts":
                    radar_pairs.add(tuple(sorted((c["by"], c["name"]))))
        except Exception:
            radar_pairs = set()
        fresh = [item for item in inbox if tuple(item["pair"]) not in radar_pairs]
        lines = [
            f"⚖ Contradiction inbox — {len(inbox)} unresolved contradiction pair(s) in the "
            "corpus. Decide per item via /hippo:resolve (nothing auto-picks a winner):"
        ]
        for item in fresh[:_MAX_CONTRADICTION_LINES]:
            lines.append(f"  • {item['pair'][0]} ⇄ {item['pair'][1]}")
        overflow = len(fresh) - _MAX_CONTRADICTION_LINES
        if overflow > 0:
            lines.append(f"  … and {overflow} more — run /hippo:resolve for the full list.")
        skipped = len(inbox) - len(fresh)
        if skipped > 0:
            lines.append(
                f"  ({skipped} pair(s) already shown by the rule↔memory conflict radar above)"
            )
        return "\n".join(lines)
    except Exception:
        return None


# GOV-4: floor & corpus change governance — how many names each delta clause lists.
_GOV4_WATERMARK_PREFIX = ".gov4-watermark-"
_MAX_FLOOR_DELTA_NAMES = 6


def _gov4_watermark_path(repo_root: str) -> Optional[str]:
    """This clone's floor/corpus watermark path, or ``None`` when CLAUDE_PLUGIN_DATA is unset.

    Same per-corpus key derivation as the nudge counters above. UNLIKE
    ``_periodic_nudge_should_fire``, an unset data dir means SILENT (``None``), not
    fire-every-session: without a durable baseline every session would scream
    "everything changed" — noise, not legibility.
    """
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
    if not data_dir:
        return None
    import hashlib

    key = hashlib.sha256(os.path.realpath(repo_root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(data_dir, f"{_GOV4_WATERMARK_PREFIX}{key}")


def _gov4_snapshot(memory_dir: str) -> dict:
    """The watermarked view of the corpus: floor pointer-set, corpus stems, floor hashes.

    Git-free by design (a teammate's pull changes files with no known base commit; a
    sorted-set diff needs no git log — this producer stays cheap). ``floor_hashes`` is a
    WHOLE-FILE sha1 per floor pointer resolvable in the PROJECT tier — the manifest's
    entry hash is name+description only, so it would miss exactly the body-edit case the
    floor (the highest-trust, always-loaded surface) most needs caught. User/private-tier
    floor pointers are membership-tracked only: they are this machine's own local files —
    a pull can't change them silently.
    """
    import hashlib

    from .provenance import _iter_memory_files
    from .recall import fused_floor_names

    floor = sorted(fused_floor_names(memory_dir))
    try:
        corpus = sorted(
            os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)
        )
    except Exception:
        corpus = []
    floor_hashes: Dict[str, str] = {}
    for name in floor:
        try:
            with open(os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8") as fh:
                floor_hashes[name] = hashlib.sha1(fh.read().encode("utf-8")).hexdigest()
        except Exception:
            continue
    return {"floor": floor, "corpus": corpus, "floor_hashes": floor_hashes}


def _gov4_delta(old: dict, now: dict) -> dict:
    """Pure sorted-set diff between two snapshots — the producer's and GOV-6's shared read."""
    old_floor, now_floor = set(old.get("floor") or []), set(now.get("floor") or [])
    old_hashes = old.get("floor_hashes") or {}
    now_hashes = now.get("floor_hashes") or {}
    return {
        "floor_added": sorted(now_floor - old_floor),
        "floor_removed": sorted(old_floor - now_floor),
        "floor_edited": sorted(
            n
            for n in now_floor & old_floor
            if n in old_hashes and n in now_hashes and old_hashes[n] != now_hashes[n]
        ),
        "corpus_added": len(set(now.get("corpus") or []) - set(old.get("corpus") or [])),
        "corpus_removed": len(set(old.get("corpus") or []) - set(now.get("corpus") or [])),
    }


def floor_change_peek(memory_dir: str, repo_root: str) -> Optional[dict]:
    """Read-only delta vs this clone's stored watermark; ``None`` when there is no watermark
    home (CLAUDE_PLUGIN_DATA unset) or no baseline yet. NEVER writes — the doctor scorecard
    (GOV-6) reads this without consuming the producer's surfaced-once semantics."""
    try:
        path = _gov4_watermark_path(repo_root)
        if path is None or not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        if not isinstance(old, dict) or not isinstance(old.get("floor"), list):
            return None
        return _gov4_delta(old, _gov4_snapshot(memory_dir))
    except Exception:
        return None


def floor_change_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """GOV-4: "changed since your last session here" — floor & corpus change governance.

    The always-loaded floor is the highest-trust, least-reviewed surface: it changes
    silently via git pull, and more generally a teammate's pull can add/remove memories
    with no legible signal. This diffs the current floor pointer-set (all recall tiers),
    the project corpus stem-set, and each project-tier floor pointer's WHOLE-FILE hash (a
    silent body edit to an always-loaded memory is exactly the risk) against a per-clone
    gitignored watermark, then advances the watermark AFTER surfacing — a seen change
    stays quiet (no re-nag), an unseen one waits. First run writes the baseline silently.
    Read-only over the corpus; the watermark is derived per-clone state (inv1); loud when
    something changed (inv3); ``ctx`` (LIF-6) is unused.
    """
    try:
        path = _gov4_watermark_path(repo_root)
        if path is None:
            return None
        now = _gov4_snapshot(memory_dir)

        def _persist() -> None:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(now, fh)
            except Exception:
                pass

        old: Optional[dict] = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("floor"), list):
                old = data
        except Exception:
            old = None
        if old is None:
            _persist()  # first run (or corrupt watermark): baseline silently, no block
            return None
        delta = _gov4_delta(old, now)
        if not any(delta.values()):
            return None  # nothing changed — and nothing to persist either
        _persist()  # advance AFTER surfacing: a seen change stays quiet from here on
        lines = ["📜 Corpus changed since this clone's last session — review before relying:"]
        if delta["floor_added"] or delta["floor_removed"]:
            bits = []
            if delta["floor_added"]:
                shown = ", ".join(delta["floor_added"][:_MAX_FLOOR_DELTA_NAMES])
                bits.append(f"+{len(delta['floor_added'])} ({shown})")
            if delta["floor_removed"]:
                shown = ", ".join(delta["floor_removed"][:_MAX_FLOOR_DELTA_NAMES])
                bits.append(f"−{len(delta['floor_removed'])} ({shown})")
            lines.append(
                f"  • always-loaded floor: {' / '.join(bits)} — the highest-trust surface; "
                "review with `git log -p -- .claude/memory/MEMORY.md`"
            )
        if delta["floor_edited"]:
            shown = ", ".join(delta["floor_edited"][:_MAX_FLOOR_DELTA_NAMES])
            lines.append(
                f"  • floor memory edited in place: {shown} — body changed without an "
                "add/remove; re-read before relying"
            )
        if delta["corpus_added"] or delta["corpus_removed"]:
            lines.append(
                f"  • corpus: added {delta['corpus_added']} / removed "
                f"{delta['corpus_removed']} memory file(s) — see "
                "`git log --stat -- .claude/memory/`"
            )
        return "\n".join(lines)
    except Exception:
        return None


# SIG-1: how many relevant-to-current-work memories the positive producer lists, and how far
# each description is trimmed. A positive block stays FOCUSED (a handful of top matches), unlike
# the warning producers whose count is the point — so this cap is tighter than _MAX_ITEMS_PER_PRODUCER.
_MAX_RELEVANT_ITEMS = 8
_RELEVANT_DESC_CHARS = 140


def _diff_query(changed_paths: List[str]) -> str:
    """A bounded recall query assembled from changed file paths — each file's stem + its parent
    dir name (e.g. ``plugin/memory/recall.py`` -> ``recall memory``). Pure string assembly, no
    LLM/network. Used ONLY to ORDER the cited-path matches by recall strength, never to select
    them (selection is the exact cited_paths intersection in the producer)."""
    tokens: List[str] = []
    seen = set()
    for p in changed_paths:
        stem = os.path.splitext(os.path.basename(p))[0]
        parent = os.path.basename(os.path.dirname(p))
        for tok in (stem, parent):
            t = tok.strip()
            if t and t not in seen:
                seen.add(t)
                tokens.append(t)
    return " ".join(tokens[:60])


def relevant_to_work_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SIG-1: the FIRST positive SessionStart block — memories about the code you're touching.

    Every other producer is a warning or the floor; none says "here are the memories about the
    files you're actually editing." This one does, BEFORE the first prompt. Selection is PRECISE:
    project memories whose ``cited_paths`` intersect the session's uncommitted working-tree diff
    (``ctx.changed_paths`` — modified-tracked + untracked since HEAD), so a clean tree emits
    nothing (no false block) and only memories genuinely about a touched file appear. Ordering is
    by recall STRENGTH: a diff-derived query is run through ``recall.recall`` (itself SEC-1-gated)
    purely to rank the matches; when recall abstains the ranking falls back to match count. The
    matched path is NAMED so the block is legible (inv3). Read-only over the git-native corpus
    (inv1); all work is at SessionStart, never the UserPromptSubmit hot path (inv6). When ``ctx``
    is absent (a standalone/test call) the diff is derived here instead.
    """
    try:
        if ctx is not None:
            changed = set(ctx.changed_paths)
        else:
            from .capture import _git_changed_paths

            changed = set(_git_changed_paths("HEAD", repo_root))
        if not changed:
            return None

        from .build_index import extract_description
        from .staleness import _iter_memory_files, read_provenance

        matches: List[Tuple[str, str, List[str]]] = []  # (name, description, matched_paths)
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            cited, _sc = read_provenance(text)
            if not cited:
                continue
            matched = [p for p in cited if p in changed]
            if not matched:
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            matches.append((name, extract_description(text), matched))
        if not matches:
            return None

        # Rank by recall STRENGTH (the diff-derived query is an ORDERING signal only — membership
        # is the exact cited_paths intersection above). recall returns best-first with a fused
        # score; abstention (empty) leaves score_by_name empty and the sort falls back to match count.
        score_by_name: dict = {}
        try:
            from .recall import recall as _recall

            for r in _recall(
                _diff_query(sorted(changed)),
                _MAX_ITEMS_PER_PRODUCER,
                memory_dir=memory_dir,
                repo_root=repo_root,
            ):
                score_by_name.setdefault(r.get("name"), r.get("score", 0.0))
        except Exception:
            score_by_name = {}
        matches.sort(key=lambda m: m[0])  # name asc — stable tiebreak under the next sort
        matches.sort(key=lambda m: (score_by_name.get(m[0], 0.0), len(m[2])), reverse=True)

        lines = [
            f"🎯 Relevant to your current work — {len(matches)} memory(ies) about files you're "
            "editing this session (uncommitted changes):"
        ]
        for name, desc, matched in matches[:_MAX_RELEVANT_ITEMS]:
            d = " ".join((desc or "").split())
            if len(d) > _RELEVANT_DESC_CHARS:
                d = d[: _RELEVANT_DESC_CHARS - 1].rstrip() + "…"
            shown = ", ".join(matched[:3])
            more = f", +{len(matched) - 3} more" if len(matched) > 3 else ""
            suffix = f" — {d}" if d else ""
            lines.append(f"  • {name}{suffix} [cites {shown}{more}]")
        if len(matches) > _MAX_RELEVANT_ITEMS:
            lines.append(f"  …and {len(matches) - _MAX_RELEVANT_ITEMS} more.")
        return "\n".join(lines)
    except Exception:
        return None


# SIG-2: the resume card's strict caps + the substantive-thread gate. A trivial/exploratory
# last session (one throwaway query, no recall) is BELOW the gate and produces nothing.
_MAX_RESUME_THEMES = 4
_MAX_RESUME_RELIED = 6
_MAX_RESUME_CHANGED = 6
_MIN_RESUME_THEMES = 2  # substantive iff it leaned on a memory OR asked >= this many distinct things


def _corpus_cited_union(memory_dir: str) -> set:
    """The union of every project memory's ``cited_paths`` (repo-relative). Never raises."""
    from .staleness import _iter_memory_files, read_provenance

    union: set = set()
    try:
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            cited, _sc = read_provenance(text)
            for c in cited:
                union.add(c)
    except Exception:
        return union
    return union


def resume_card_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SIG-2: a 'where was I' resume card replayed from the episode buffer.

    The episode buffer already soaks the exact ingredients for continuity — per-session query
    previews, recalled names, and a repo HEAD watermark — but only ``capture`` (the SessionEnd
    draft pass) ever read it; reopening a repo was a cold start. This replays the MOST RECENT
    session in THIS clone's (gitignored, clone-local) buffer into a legible orientation block:
    what you were working on, which memories you leaned on, and which cited files changed since.

    Precise + bounded: the "changed cited files" list is the session's since-watermark diff
    intersected with the corpus's own cited_paths (so it names only files a memory knows about,
    strictly capped). GATED to substantive threads — a trivial/exploratory last session (no
    recall, < _MIN_RESUME_THEMES distinct queries) produces nothing. Pure template assembly at
    SessionStart over gitignored telemetry (inv1); read-only, never acts on prior work (inv3);
    off the hot path (inv6). Labelled clone-local: the buffer is per-clone, so this can never
    resume a teammate's thread. ``ctx`` (LIF-6) is unused — see ``stale_venv_producer``.
    """
    try:
        from .capture import gather_session_context
        from .telemetry import default_telemetry_dir, read_episodes

        td = default_telemetry_dir(memory_dir)
        # The most-recent session in the buffer. At SessionStart the CURRENT session has logged
        # no episodes yet (recall logs on UserPromptSubmit, which fires later), so on a fresh
        # start this is genuinely the PRIOR session; on resume/compact it is this same thread's
        # earlier work — either way "where you left off" is the correct framing.
        latest_ts = None
        sid = None
        any_episode = False
        for e in read_episodes(td):
            any_episode = True
            ts = e.get("ts")
            if ts is None:
                continue
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
                sid = e.get("session_id")
        if not any_episode:
            return None  # cold start — no local history to resume yet

        # include_hunks=False: the resume card never persists (or renders) the seed's verbatim
        # diff evidence, so skip GRW-1's hunk subprocesses on this read-only SessionStart path.
        seed = gather_session_context(
            sid, repo_root=repo_root, telemetry_dir=td, memory_dir=memory_dir, include_hunks=False
        )
        if not seed:
            return None
        themes = seed.get("query_previews") or []
        relied = seed.get("recalled_names") or []
        if not relied and len(themes) < _MIN_RESUME_THEMES:
            return None  # trivial/exploratory last session — nothing worth resuming

        changed = set(seed.get("changed_paths") or [])
        changed_cited = sorted(changed & _corpus_cited_union(memory_dir)) if changed else []

        lines = [
            "🧭 Where you left off — recent work in this repo (from your local session "
            "history on this clone):"
        ]
        if themes:
            shown = "; ".join(themes[:_MAX_RESUME_THEMES])
            more = f" (+{len(themes) - _MAX_RESUME_THEMES} more)" if len(themes) > _MAX_RESUME_THEMES else ""
            lines.append(f"  • you were working on: {shown}{more}")
        if relied:
            shown = ", ".join(relied[:_MAX_RESUME_RELIED])
            more = f" (+{len(relied) - _MAX_RESUME_RELIED} more)" if len(relied) > _MAX_RESUME_RELIED else ""
            lines.append(f"  • you leaned on: {shown}{more}")
        if changed_cited:
            shown = ", ".join(changed_cited[:_MAX_RESUME_CHANGED])
            more = f" (+{len(changed_cited) - _MAX_RESUME_CHANGED} more)" if len(changed_cited) > _MAX_RESUME_CHANGED else ""
            lines.append(
                f"  • cited files that changed since (verify memories about them): {shown}{more}"
            )
        return "\n".join(lines)
    except Exception:
        return None
