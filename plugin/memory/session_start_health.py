"""Environment & corpus-health producers for the SessionStart dispatcher — decomposed
out of ``session_start.py`` (REL-1), which keeps the ``PRODUCERS`` registry, the
dispatcher (``build_context``/``main``), and explicit re-exports of every moved name.

The venv/bootstrap sentinel state (COR-11), format/derivation versions (COR-7, DRV-2),
frontmatter integrity, citation rot (LIF-3), index corruption (QUA-5), the
unresolvable-baseline pair (SHP-3, GRW-6), the SEC-6 trust-drift line, and the SEC-1
untrusted-corpus nudge with the shared low-frequency cadence gate every SessionStart
nudge reuses. Producers keep the uniform ``(memory_dir, repo_root, ctx)`` call shape
(LIF-6) even where ``ctx`` is unused — one registry, one shape.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from .provenance import CORPUS_FORMAT_VERSION, read_corpus_format, run_git
from .staleness import (
    RunContext,
    count_unresolvable_baselines,
    find_citation_rot,
    find_unparseable,
    unresolvable_baseline_names,
)

# Per-producer listing bound (the dispatcher's overall char budget stays with it in
# ``session_start``). Shared with the signals sibling.
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


def cite_derivation_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """DRV-2: this corpus's cited_paths were derived by an extractor this plugin has fixed.

    The OPPOSITE direction to ``corpus_format_producer``, and deliberately louder than that
    one's older-corpus case, because the two "older" states are not alike. An older
    corpus_format is FINE — v2/v3/v4 are additive, so a v5 plugin reads a v1 corpus
    correctly and there is nothing to warn about. An older cite_derivation is a live
    DEGRADATION: those citations were produced by the pre-ORC-1 extractor, so some memories
    watch the wrong file and some sit at ``cited_paths: []`` — staleness-EXEMPT — for no
    reason but a regex. KPI-5's rule (never a silent degradation) is what makes this a
    per-session line rather than a doctor-only note, the same reasoning
    ``trust_drift_producer`` uses.

    Routes to the re-derivation rather than doing it: re-deriving rewrites user data and is
    per-item and consent-gated (MIG-1). Silent when the corpus is already current, when it
    is ahead (that is corpus_format_producer's taint case, not this one), and when it holds
    no memories at all — an EMPTY corpus has no citations, so naming the extractor that
    derived them is a nudge about nothing. ``ctx`` (LIF-6) unused.
    """
    try:
        from .provenance import (
            CITATION_DERIVATION_VERSION,
            _iter_memory_files,
            read_cite_derivation,
        )

        declared = read_cite_derivation(memory_dir)
        if declared >= CITATION_DERIVATION_VERSION:
            return None
        if next(_iter_memory_files(memory_dir), None) is None:
            return None  # nothing was derived, so nothing needs re-deriving
    except Exception:
        return None
    # ORC-3: the gap description must name what's ACTUALLY missing for THIS `declared`, not
    # just always recite the v1->v2 (ORC-1) delta — a v2 corpus never had those bugs, so
    # telling it "v1 could not see .json/.tsx/.jsx" would be describing a defect it does not
    # have. Each historical derivation bump adds one more conditional clause here, same shape
    # as CITATION_DERIVATION_VERSION's own history comment.
    gaps = []
    if declared < 2:
        gaps.append(
            "v1 could not see `.json`/`.tsx`/`.jsx` (it read package.json as package.js), "
            "`.mjs`/`.cjs` at all, or a leading `./`"
        )
    if declared < 3:
        gaps.append(
            "v2 could not see extensionless config/build filenames (`Dockerfile`, `Makefile`, "
            "`LICENSE`, etc.) at all"
        )
    if declared < 4:
        gaps.append(
            "v3 could not see `.mdc` Cursor rule sources (an imported memory's upstream "
            "fingerprint)"
        )
    return (
        f"🧬 Citation derivation — this corpus's cited_paths were derived by extractor "
        f"v{declared}; this plugin derives v{CITATION_DERIVATION_VERSION}. {'; '.join(gaps)} "
        "— so some memories watch the wrong file and some carry an empty cited_paths, which "
        "makes them EXEMPT from staleness tracking. Review the attributed diff with the "
        "`rederive` MCP tool (action='worklist'), apply it ONE memory at a time "
        "(action='one' name=…), then action='stamp' to record it and stop this line. In a "
        "terminal: `--rederive-worklist` / `--rederive-one <name>` / `--stamp-derivation` on "
        "`python -m memory.provenance`. It rewrites frontmatter, so it is per-item and asks "
        "first; take action='snapshot' first if this corpus is gitignored (no git undo)."
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


# SEC-6: how many withheld stems the trust-drift line names before folding into a count.
_MAX_TRUST_DRIFT_NAMES = 4


def trust_drift_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SEC-6: a TRUSTED corpus whose content drifted from its consent baseline — LOUD.

    Recall is actively WITHHOLDING the drifted/new files (the per-file quarantine), so
    this is first-class loud (fires every session while drift exists, like the conflict
    radar — an active degradation must never wait for a nudge cadence, KPI-5). Names the
    first few withheld stems and routes to ``/hippo:doctor`` for the delta review +
    re-consent. Silent when: the gate is inapplicable/bypassed, the corpus is untrusted
    (the untrusted nudge owns that path), the record is legacy/fingerprint-less (no
    quarantine is active — the doctor check names that upgrade), or there is no drift.
    Read-only; ``ctx`` (LIF-6) unused; never raises.
    """
    try:
        from . import trust

        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        if gate_root is None or trust.trust_all() or not trust.is_trusted(gate_root):
            return None
        drift = trust.untrusted_changes(gate_root, memory_dir)
        return trust.drift_withholding_line(drift, max_names=_MAX_TRUST_DRIFT_NAMES)
    except Exception:
        return None


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
        "injecting nothing from it until you review and trust it. A cloned or downloaded "
        "repo's memories are an unreviewed prompt-injection channel, so recall stays gated "
        "by default (SEC-12: this holds for an extracted, non-git corpus too). Run "
        "/hippo:doctor to see the memory names and trust this corpus, or /hippo:init if it's "
        "yours (set HIPPO_TRUST_ALL=1 for CI, or HIPPO_TRUST_NONGIT=1 for a hand-made non-git "
        "corpus)."
    )
