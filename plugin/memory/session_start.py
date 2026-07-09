"""SessionStart dispatcher for agent memory.

ONE process, ONE corpus load, ONE merged ``additionalContext`` for all dynamic memory
context. Producers (each ADDED here, never as a parallel hook entry — so there is a single
SessionStart producer for the memory concerns):
  - staleness   (Tier 1) — memories whose cited code drifted since they were written
                           (LIF-1: entries already soft-invalidated are counted, not
                           re-listed — demote's terminal state must not re-nag. LIF-6:
                           entries already on the reconsolidation worklist are counted,
                           not re-listed either — see below).
  - reconsolidation      — the recall-filtered subset of staleness (Tier 2).
  - relevant-to-work (SIG-1) — the FIRST positive block: memories whose cited_paths intersect
                           the session's uncommitted diff (the code you're actually editing),
                           ranked by recall strength. SILENT on a clean tree.
  - resume-card (SIG-2)  — "where was I": replay the last session from the (clone-local) episode
                           buffer — themes, relied-on memories, changed cited files. SILENT on a
                           cold start or a trivial last session.
  - blind-spot  (SIG-3)  — recurring recall abstentions (backend='none') the corpus can't answer,
                           as a low-frequency curation backlog. SILENT unless a cluster recurs.
  - git-recent  (Tier 2) — memories captured within the recent window (newest first).
  - link-health (Tier 3) — dangling/orphan wikilink count across the corpus.
  - floor                — SILENT unless project/reference links re-bloat the MEMORY.md floor
                           (memory pointers belong only under User + Working-Style).

LIF-6: the reconsolidation worklist is BY CONSTRUCTION a subset of the staleness set (both
derive from ``staleness.find_stale``), so ``build_context`` computes ``find_stale`` (and the
worklist derived from it) exactly ONCE per run — a ``staleness.RunContext`` — and threads it
POSITIONALLY through every producer below (see ``PRODUCERS``): one uniform call shape, not a
special case for the two staleness-derived producers. Producers that don't care about
staleness just declare the trailing ``ctx`` parameter and never read it. The same computed
staleness set is also persisted to the gitignored index dir as ``stale.json`` (consumed by
RET-5's recall-time salience penalty and RET-6's future drift banner; nothing HERE reads it
back — this module only writes it, via ``staleness.write_stale_cache``).

Contract (mirrors ``.claude/hooks/agent_staleness.sh``):
  - Self-suppresses (prints nothing) when no producer has anything to say.
  - Bounds the merged output below the harness's 10,000-char cap.
  - ALWAYS exits 0; a failing producer is skipped, never crashes the dispatcher.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

from .lint_floor import floor_producer
from .lint_links import lint_links_producer
from .provenance import CORPUS_FORMAT_VERSION, read_corpus_format, resolve_dirs, run_git
from .recall import (
    _INVALIDATION_RECENT_DAYS,
    _invalidation_state,
    git_recent_producer,
    portable_floor_producer,
)
from .reconsolidate import (
    recalled_stale_worklist,
    reconsolidation_producer,
    watermark_stale_candidates,
)
from .staleness import (
    RunContext,
    count_unresolvable_baselines,
    find_citation_rot,
    find_stale,
    find_unparseable,
    invalid_after_map,
    unresolvable_baseline_names,
    write_stale_cache,
)

# Harness caps hook output at 10,000 chars; stay comfortably under it.
_MAX_CONTEXT_CHARS = 9000
_MAX_ITEMS_PER_PRODUCER = 20


def bootstrap_state(
    plugin_data: Optional[str] = None, plugin_root: Optional[str] = None
) -> str:
    """Canonical sentinel-vs-requirements.txt state (COR-11's sha256 compare, ONE definition).

    Returns one of:
      - ``"no_data_dir"`` — ``CLAUDE_PLUGIN_DATA`` unset (can't locate the sentinel/venv).
      - ``"not_bootstrapped"`` — no ``.bootstrap-sentinel`` (bootstrap never ran).
      - ``"no_requirements"`` — sentinel present but ``requirements.txt`` unreadable.
      - ``"stale"`` — sentinel's recorded ``requirements_hash`` != current requirements.txt
        (a plugin update bumped deps; the venv still runs the OLD set, new imports degrade
        silently).
      - ``"current"`` — bootstrapped and deps match, OR the sentinel recorded no hash to compare.

    Both the SessionStart re-bootstrap nudge (``stale_venv_producer``) and ``doctor.check_bootstrap``
    read THIS one function rather than re-deriving the hash compare — DOC-4's one-implementation
    rule. Never raises: any unexpected error degrades to ``"current"`` (fail toward not-nagging;
    the actual venv-import check catches a truly broken venv). ``plugin_data``/``plugin_root``
    override the env for hermetic tests.
    """
    try:
        import hashlib

        data_dir = plugin_data if plugin_data is not None else (os.environ.get("CLAUDE_PLUGIN_DATA") or "")
        root = plugin_root if plugin_root is not None else (
            os.environ.get("CLAUDE_PLUGIN_ROOT")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        if not data_dir:
            return "no_data_dir"
        sentinel_path = os.path.join(data_dir, ".bootstrap-sentinel")
        if not os.path.isfile(sentinel_path):
            return "not_bootstrapped"
        req_path = os.path.join(root, "requirements.txt")
        if not os.path.isfile(req_path):
            return "no_requirements"
        with open(sentinel_path, "r", encoding="utf-8") as fh:
            recorded = (json.load(fh) or {}).get("requirements_hash") or ""
        with open(req_path, "rb") as fh:
            current = hashlib.sha256(fh.read()).hexdigest()
        if recorded and recorded != current:
            return "stale"
        return "current"
    except Exception:
        return "current"


def stale_venv_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """One-line re-bootstrap nudge when plugin deps changed after the last bootstrap.

    The venv-in-PLUGIN_DATA model is update-safe for CODE but not DEPS: a plugin update
    that bumps requirements.txt leaves hooks running the old venv indefinitely, with new
    imports failing into silent excepts (COR-11). Delegates the sha256 compare to the
    canonical ``bootstrap_state`` (shared with doctor); nudges ONLY on ``"stale"``. Silent
    when not bootstrapped (ONB-1's pre-Python nudge owns that state) or when anything is
    unreadable. Runs once per session by construction (SessionStart).

    ``ctx`` (LIF-6's shared per-run ``RunContext``) is unused here — declared only so
    every producer in ``PRODUCERS`` shares ONE call shape (see the module docstring).
    """
    if bootstrap_state() != "stale":
        return None
    return (
        "⚠ hippo deps changed with the last plugin update — the venv still runs the "
        "old dependency set (new imports degrade silently). Run /hippo:bootstrap to "
        "re-provision."
    )


def corpus_format_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """LOUD warning when the corpus declares a format NEWER than this plugin understands.

    COR-7: the ``.claude/memory/.format`` marker travels with the corpus through git, so a
    teammate on a newer hippo can bump the corpus format under a machine still running an
    older plugin — which would otherwise keep reading conventions it predates with NO
    signal anywhere. The fix is one-directional and user-side (update the plugin), hence a
    per-session producer. The OTHER direction (corpus OLDER than the plugin expects) is
    deliberately NOT nagged here: migrating user data is a doctor-driven, agent-gated path
    (see ``doctor.check_format_version`` + the README), not a per-session alarm. Silent on
    an undeclared corpus (no marker == format 1) and on any read problem. ``ctx`` (LIF-6)
    is unused — see ``stale_venv_producer`` for why it's declared anyway.
    """
    try:
        declared = read_corpus_format(memory_dir)
    except Exception:
        return None
    if declared <= CORPUS_FORMAT_VERSION:
        return None
    return (
        f"⚠ Corpus format — this corpus declares format v{declared} but this hippo plugin "
        f"only understands v{CORPUS_FORMAT_VERSION}. Update the hippo plugin: a newer-format "
        "corpus can carry conventions this version misreads or silently ignores."
    )


def integrity_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """LOUD warning for memory files whose frontmatter does not parse.

    These are otherwise a silent hole — skipped by the staleness signal AND re-baselined
    by ``provenance --refresh``. Surfaced FIRST so a malformed memory can't hide. ``ctx``
    (LIF-6) is unused — see ``stale_venv_producer`` for why it's declared anyway.
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


def citation_rot_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """Count-first warning for memories citing paths that no longer exist in the repo (LIF-3).

    The current-state citation-rot report — catches a rename/delete of cited code even when
    no refresh has run yet (nothing has dropped, but staleness can't watch a vanished path,
    and the next re-derivation would silently shrink cited_paths — possibly to zero, which
    permanently exempts the memory). This producer is the ONE canonical SessionStart surface;
    the CLI sibling lives in ``memory.staleness``'s default report, next to the unparseable
    block. TOTAL rot (every citation gone) is called out distinctly on its line. ``ctx``
    (LIF-6) is unused — see ``stale_venv_producer`` for why it's declared anyway.
    """
    rot = find_citation_rot(memory_dir, repo_root)
    if not rot:
        return None
    lines = [
        f"⚠ Citation rot — {len(rot)} memories cite paths that no longer exist in the repo "
        "(renamed or deleted since capture; staleness can't watch a vanished path). Re-point "
        "the citation in the body then `provenance --refresh-one <name>`, or re-verify the "
        "memory against current code:"
    ]
    for item in rot[:_MAX_ITEMS_PER_PRODUCER]:
        paths = ", ".join(item["missing_paths"][:4])
        more = "" if len(item["missing_paths"]) <= 4 else f" (+{len(item['missing_paths']) - 4} more)"
        total = (
            " — ALL its citations (a refresh would EMPTY cited_paths → staleness-EXEMPT)"
            if len(item["missing_paths"]) == item["cited_count"]
            else ""
        )
        lines.append(f"  • {item['name']}: {paths}{more}{total}")
    if len(rot) > _MAX_ITEMS_PER_PRODUCER:
        lines.append(f"  …and {len(rot) - _MAX_ITEMS_PER_PRODUCER} more.")
    return "\n".join(lines)


def staleness_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """LIF-6: ``ctx`` (the dispatcher's shared ``RunContext``) already carries a single
    ``find_stale`` call's result plus the reconsolidation worklist derived from it — this
    producer reads both instead of re-deriving them. When ``ctx`` is ``None`` (a standalone
    call, e.g. a test that stubs only ``find_stale``), it derives its own staleness view
    exactly as before LIF-6, with no worklist to exclude (there's nothing to de-duplicate
    against outside the dispatcher).
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
    if not stale:
        return timeout_note
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
    if timeout_note:
        lines.append(timeout_note)
    return "\n".join(lines)


def index_integrity_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """LOUD diagnosis for on-disk recall-index corruption (QUA-5).

    ``build_index``/``recall`` already degrade gracefully (never raise) on a truncated
    manifest, a missing dense.npy, or a wrong-shape dense.npy — but until now nothing named
    WHICH of those states was present, so "memory stopped working" had no diagnosis surface.
    Silent when the index doesn't exist yet (nothing built) or is healthy. ``ctx`` (LIF-6)
    is unused — see ``stale_venv_producer`` for why it's declared anyway.
    """
    try:
        from .build_index import check_index_integrity, default_index_dir

        index_dir = default_index_dir(memory_dir)
        finding = check_index_integrity(index_dir)
    except Exception:
        return None
    if not finding:
        return None
    return f"⚠ Index integrity — {finding}."


def unresolvable_baseline_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """LOUD count for memories whose staleness baseline sha isn't in this repo's history.

    A squash-merge default (or a shallow/partial CI clone) rewrites/truncates history so a
    branch-authored memory's ``source_commit`` is never reachable from mainline — SHP-3 falls
    back to the memory's own stored ``source_commit_time`` instead of silently exempting it
    from drift detection forever, but that fallback IS a degradation and must be legible.
    ``ctx`` (LIF-6) is unused — see ``stale_venv_producer`` for why it's declared anyway.
    """
    n = count_unresolvable_baselines(memory_dir, repo_root)
    if not n:
        return None
    return (
        f"⚠ {n} memories have unresolvable staleness baselines (source_commit sha not in "
        "history — likely squash-merge or a shallow clone); falling back to time-based comparison."
    )


# GRW-6: how many broken-baseline memories the healing offer NAMES (the rest are counted).
_MAX_HEAL_NAMES = 6
# Squash-merge subjects as GitHub (and most forges) write them — "feat: thing (#123)".
_SQUASH_SUBJECT_RE = re.compile(r"\(#\d+\)")


def _recent_merge_signals(repo_root: str) -> bool:
    """Cheap detection that a merge LANDED recently — reflog/log/branch probes, ORed.

    A squash-merge leaves NO merge commit, so no single probe is authoritative: the reflog
    remembers merge/pull actions in THIS clone; recent one-line subjects catch a forge's
    squash commit ("(#N)") in ANY clone (a fresh clone has no reflog history); and a
    non-current branch listed by ``branch --merged`` marks a true merge. Read-only, three
    bounded git reads, ``False`` on any failure — and only ever consulted AFTER the
    baseline break is confirmed, so a merge-looking history with nothing broken stays
    silent. Never raises.
    """
    try:
        reflog = run_git(["reflog", "-n", "50", "--format=%gs"], repo_root).lower()
        if "merge" in reflog or "pull" in reflog:
            return True
        subjects = run_git(["log", "--oneline", "-n", "20", "--format=%s"], repo_root)
        if _SQUASH_SUBJECT_RE.search(subjects):
            return True
        for ln in run_git(["branch", "--merged"], repo_root).splitlines():
            if ln.strip() and not ln.lstrip().startswith("*"):
                return True  # a NON-current local branch fully merged into HEAD
        return False
    except Exception:
        return False


def squash_merge_heal_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """GRW-6: turn the unresolvable-baseline DEGRADATION into a per-item REPAIR offer.

    ``unresolvable_baseline_producer`` above reports the fallback; this fires only when a
    recent merge event is ALSO detectable (reflog/log/branch probes) — the moment healing
    is actually warranted — and NAMES the broken memories, routing each to the consolidate
    drain where the agent confirms the memory still holds post-merge and re-baselines it
    via the shipped ``reverify_file`` path (``--outcome graduate``). Detection + offer
    ONLY: never a new write path, never bulk (semantic_reverify stays single-item by
    signature pin) — inv4. Self-clearing: healed baselines resolve again and both
    producers go silent. ``ctx`` (LIF-6) is unused.
    """
    try:
        names = unresolvable_baseline_names(memory_dir, repo_root)
        if not names or not _recent_merge_signals(repo_root):
            return None
        shown = ", ".join(names[:_MAX_HEAL_NAMES])
        more = f" (+{len(names) - _MAX_HEAL_NAMES} more)" if len(names) > _MAX_HEAL_NAMES else ""
        return (
            f"🩹 A merge landed recently and {len(names)} memories' staleness baselines no "
            "longer resolve (squash-merge rewrites history). Heal them per item via "
            "/hippo:consolidate: confirm each memory still holds post-merge, then "
            "`reconsolidate --reverify <name> --outcome graduate` re-baselines it to the "
            f"current HEAD (reverify_file re-derives its citations too). Broken: {shown}{more}."
        )
    except Exception:
        return None


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
        from .capture import default_pending_dir, pending_count

        pd = default_pending_dir(memory_dir)
        n = pending_count(pd)
    except Exception:
        return None
    if not n:
        return None
    trivial = 0
    try:
        from .capture import read_pending

        trivial = sum(1 for s in read_pending(pd) if (s.get("salience") or {}).get("trivial"))
    except Exception:
        trivial = 0
    label = f" ({trivial} trivial)" if trivial else ""
    return (
        f"📥 {n} pending capture(s){label} from a prior session await review — run "
        "/hippo:consolidate to draft them into memory (nothing is saved until you approve "
        "each one, per item)."
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


# (label, fn). Each tier appends a producer here — never a parallel hook entry. Every fn
# shares ONE call shape — ``(memory_dir, repo_root, ctx)`` — LIF-6's ``RunContext``, even
# when a given producer ignores it (see the module docstring).
PRODUCERS: List[Tuple[str, Callable[[str, str, Optional[RunContext]], Optional[str]]]] = [
    ("stale_venv", stale_venv_producer),  # environment-level — a stale venv taints everything below
    ("corpus_format", corpus_format_producer),  # a corpus NEWER than the plugin taints every reader below (COR-7)
    ("integrity", integrity_producer),  # a malformed memory must not hide
    ("citation_rot", citation_rot_producer),  # cited paths gone from the repo (LIF-3) — find_unparseable's rot sibling
    ("staleness", staleness_producer),
    ("reconsolidation", reconsolidation_producer),  # recall-filtered subset of staleness; silent unless a recently-recalled memory is stale
    ("pending_capture", pending_capture_producer),  # CAP-2: surface the gitignored draft-capture queue so it never soaks silently
    ("blind_spot", blind_spot_producer),  # SIG-3: recurring recall abstentions -> a low-frequency curation backlog
    ("index_integrity", index_integrity_producer),  # names on-disk index corruption (QUA-5) — recall/build_index already degrade silently
    ("unresolvable_baseline", unresolvable_baseline_producer),  # legibility for find_stale's sha-fallback path
    ("squash_merge_heal", squash_merge_heal_producer),  # GRW-6: merge detected + baselines broken -> per-item rebaseline offer
    ("rules_conflict", rules_conflict_producer),  # RUL-1: governance cites a memory the corpus disputes (superseded/contradicted/never-recalled)
    ("rules_rot", rules_rot_producer),  # RUL-2: citation-rot/staleness over the always-loaded rules plane itself
    ("contradiction_inbox", contradiction_inbox_producer),  # GOV-1: every unresolved contradicts pair, not just the co-surfaced/governance-cited ones
    ("floor_change", floor_change_producer),  # GOV-4: floor/corpus changed since this clone's last session (per-clone watermark; a seen change stays quiet)
    ("relevant_to_work", relevant_to_work_producer),  # SIG-1: the first POSITIVE block — memories about the files you're editing
    ("resume_card", resume_card_producer),  # SIG-2: "where was I" — replay the last session from the episode buffer
    ("git_recent", git_recent_producer),
    ("link_health", lint_links_producer),
    ("floor", floor_producer),  # silent unless project/reference links re-bloat the MEMORY.md floor
    ("portable_floor", portable_floor_producer),  # TEA-1: deliver the user/private-tier floor (no native channel)
]


# How often the untrusted-corpus nudge fires among nudge-eligible sessions (mirrors ONB-1's
# NUDGE_EVERY): loud enough to be seen, quiet enough not to nag every session.
_TRUST_NUDGE_EVERY = 5


def _periodic_nudge_should_fire(repo_root: str, *, slug: str, every: int) -> bool:
    """ONE low-frequency per-corpus gate for every SessionStart nudge that should be SEEN but
    not NAG (mirrors ONB-1's modulo pattern). Fires on the 1st eligible session and every
    ``every``-th after, using a per-corpus counter file ``.<slug>-<key>`` under
    ``CLAUDE_PLUGIN_DATA`` — so a nudge's cadence on one corpus never affects another, and two
    different nudges (``slug``) keep independent counters. When ``CLAUDE_PLUGIN_DATA`` is unset
    (dev checkout / hermetic test with no plugin-data dir) there is nowhere durable to keep the
    counter — fail toward LEGIBLE and fire every session rather than swallow the signal. Never raises.
    """
    try:
        import hashlib

        data_dir = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
        if not data_dir:
            return True
        key = hashlib.sha256(os.path.realpath(repo_root).encode("utf-8")).hexdigest()[:16]
        counter_path = os.path.join(data_dir, f".{slug}-{key}")
        try:
            with open(counter_path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            count = int(raw) if raw.isdigit() else 0
        except Exception:
            count = 0
        try:
            os.makedirs(data_dir, exist_ok=True)
            with open(counter_path, "w", encoding="utf-8") as fh:
                fh.write(str(count + 1))
        except Exception:
            pass
        return count % every == 0
    except Exception:
        return True


def _trust_nudge_should_fire(repo_root: str) -> bool:
    """Low-frequency gate for the untrusted-corpus nudge (see ``_periodic_nudge_should_fire``)."""
    return _periodic_nudge_should_fire(repo_root, slug="trust-nudge", every=_TRUST_NUDGE_EVERY)


def untrusted_corpus_nudge(memory_dir: str, repo_root: str) -> Optional[str]:
    """Low-frequency nudge when THIS project's corpus is not yet trusted (SEC-1).

    The one legible signal on the untrusted path: recall injects nothing and every other
    producer stays silent (see ``build_context``'s short-circuit), so without this the user
    would see a totally inert corpus with zero explanation. Silent when the corpus is trusted,
    when the gate is inapplicable (no resolvable git root), or when the low-frequency modulo
    says skip this session. Names the memory COUNT so the user knows something is being
    withheld and points at ``/hippo:doctor`` (which shows the sample + takes consent).
    """
    from . import trust

    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is None or trust.is_trusted(gate_root):
        return None
    if not _trust_nudge_should_fire(gate_root):
        return None
    count = trust.corpus_count(memory_dir)
    return (
        f"🔒 This project has an UNTRUSTED memory corpus ({count} memories) — hippo is "
        "injecting nothing from it until you review and trust it. A cloned repo's memories "
        "are an unreviewed prompt-injection channel, so recall stays gated by default. Run "
        "/hippo:doctor to see the memory names and trust this corpus (or set HIPPO_TRUST_ALL=1 "
        "for CI)."
    )


def _build_run_context(memory_dir: str, repo_root: str) -> RunContext:
    """LIF-6: compute ``find_stale`` (and the reconsolidation worklist derived from it)
    EXACTLY ONCE per SessionStart run, instead of ``staleness_producer`` and
    ``reconsolidation_producer`` each independently re-scanning the same corpus into the
    same git-log window. Also best-effort persists the staleness half to
    ``<index_dir>/stale.json`` (RET-5/RET-6 setup — see ``staleness.write_stale_cache``).

    The write is guarded on ``memory_dir`` actually existing on disk: a bogus/nonexistent
    dir (a hermetic test's placeholder string, an untrusted-gate short-circuit that never
    reaches here) must never mint a stray index dir next to wherever the process happens
    to be running — mirrors ``main``'s own guard around the telemetry session write.
    Never raises; degrades to an EMPTY ``RunContext`` (the same shape a clean corpus
    produces, so every producer's ctx-aware branch already tolerates it).
    """
    diagnostics: dict = {}
    stale: List[dict] = []
    worklist: List[dict] = []
    changed_paths: List[str] = []
    try:
        stale = find_stale(memory_dir, repo_root, diagnostics=diagnostics)
    except Exception:
        stale = []
    # GRW-5: the commit-precise lane — <last-session-watermark>..HEAD ∩ cited_paths — shares
    # this ONE SessionStart git-read moment and unions into the SAME worklist, so everything
    # still routes through the single semantic_reverify gate (no new verb, no .git/hooks).
    try:
        wm_stale = watermark_stale_candidates(memory_dir, repo_root)
    except Exception:
        wm_stale = []
    try:
        worklist = recalled_stale_worklist(
            memory_dir, repo_root, stale=stale, watermark_stale=wm_stale
        )
    except Exception:
        worklist = []
    # SIG-1: the session's uncommitted working-tree diff, computed ONCE (this path only runs
    # for a TRUSTED corpus — build_context short-circuits before here — so the relevant-to-work
    # producer inherits the SEC-1 gate). 'HEAD' as the watermark unions modified-tracked with
    # untracked files; [] on a clean tree or non-git corpus. Off the hot path (inv6).
    try:
        from .capture import _git_changed_paths

        changed_paths = _git_changed_paths("HEAD", repo_root)
    except Exception:
        changed_paths = []
    try:
        if os.path.isdir(memory_dir):
            from .build_index import default_index_dir

            write_stale_cache(default_index_dir(memory_dir), stale)
    except Exception:
        pass
    # RUL-4: refresh the rules side-index at the SAME offline moment (signature fast-path —
    # unchanged governance files cost one stat sweep). Trusted-only path (build_context
    # short-circuits before here), so the rules recall source inherits the SEC-1 gate.
    try:
        if os.path.isdir(memory_dir):
            from .build_index import default_index_dir
            from .rules_plane import refresh_rules_cache

            refresh_rules_cache(repo_root, default_index_dir(memory_dir))
    except Exception:
        pass
    return RunContext(
        stale=stale,
        stale_diagnostics=diagnostics,
        worklist=worklist,
        changed_paths=changed_paths,
    )


def build_context(memory_dir: str, repo_root: str, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    """Run every producer, merge their non-empty blocks, bound the total. Never raises.

    SEC-1 short-circuit: when this project's corpus exists but is NOT trusted, EVERY content
    producer stays silent (an untrusted corpus injects nothing) and the ONLY block emitted is
    the low-frequency untrusted-corpus nudge — the single legible signal on the gated path.
    """
    try:
        from . import trust

        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        if gate_root is not None and not trust.is_trusted(gate_root):
            return untrusted_corpus_nudge(memory_dir, repo_root) or ""
    except Exception:
        pass
    run_ctx = _build_run_context(memory_dir, repo_root)
    blocks: List[str] = []
    for _label, fn in PRODUCERS:
        try:
            out = fn(memory_dir, repo_root, run_ctx)
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


def _read_hook_payload() -> Tuple[Optional[str], Optional[str]]:
    """Best-effort ``(source, session_id)`` from the SessionStart hook's stdin JSON.

    ``source`` is one of ``startup``/``resume``/``clear``/``compact`` (COR-6); ``session_id``
    is the harness's own id for THIS session. Never raises: any failure (no stdin, a tty,
    unparseable JSON, a non-dict payload) yields ``(None, None)`` — the caller then falls back
    to today's "always mint/reuse a file-based token" behavior.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None, None
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return None, None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None, None
        source = payload.get("source") or None
        session_id = payload.get("session_id") or None
        return source, session_id
    except Exception:
        return None, None


def main(
    argv: Optional[List[str]] = None,
    *,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    """SessionStart entry point.

    ``source``/``session_id`` are normally sourced from the hook's stdin JSON payload (see
    ``_read_hook_payload``) — the explicit keyword args exist so tests (and any future
    non-hook caller) can drive the source/session-id-dependent behavior directly without
    piping JSON through stdin. When given explicitly they WIN over whatever stdin carries.
    """
    try:
        stdin_source, stdin_session_id = _read_hook_payload()
        if source is None:
            source = stdin_source
        if session_id is None:
            session_id = stdin_session_id
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
        # TEA-1/TEA-3: keep the machine-local user tier's (and in-repo private tier's) index
        # fresh too — OFFLINE + bounded, exactly like the project refresh — so the hot path can
        # fuse them WITH dense (a BM25-only tier would force the whole merged view to BM25).
        # Each tier's index NESTS inside the tier dir, so this never writes into the project's
        # git tree. Never raises; a no-op when no extra tier exists.
        try:
            from .build_index import refresh_index
            from .recall import _recall_tier_dirs

            for tdir, tidx, label in _recall_tier_dirs(memory_dir, None):
                if label != "project" and os.path.isdir(tdir):
                    refresh_index(tdir, tidx)
        except Exception:
            pass
        # Open a NEW telemetry session so the recall ledger can count distinct sessions
        # (the curation-soak signal) — but ONLY on a genuinely new conversation (source
        # "startup"/"clear"). "resume"/"compact" (or an unknown/missing source, e.g. no
        # hook payload at all) re-enter or continue an EXISTING conversation and must not
        # inflate the distinct-session count (COR-6) — ensure some id exists instead of
        # rotating. When the harness hands us a concrete session_id, telemetry keys on THAT
        # id directly (see telemetry.current_session_id) — the file-based token below is a
        # fallback for callers without one, so it's skipped entirely in that case (nothing to
        # mint/rotate/reuse; a shared mutable file is exactly what a harness id replaces).
        # Side effect, not a producer; never raises. Guarded on a real corpus dir so a
        # bogus/nonexistent memory_dir never creates a stray ledger dir (mirrors
        # refresh_index, which no-ops on a missing corpus).
        try:
            from .telemetry import current_session_id, default_telemetry_dir, mark_session

            if os.path.isdir(memory_dir) and not session_id:
                td = default_telemetry_dir(memory_dir)
                if source in ("startup", "clear"):
                    mark_session(td)
                else:
                    current_session_id(td)
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
