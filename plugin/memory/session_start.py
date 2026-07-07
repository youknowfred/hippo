"""SessionStart dispatcher for agent memory.

ONE process, ONE corpus load, ONE merged ``additionalContext`` for all dynamic memory
context. Producers (each ADDED here, never as a parallel hook entry — so there is a single
SessionStart producer for the memory concerns):
  - staleness   (Tier 1) — memories whose cited code drifted since they were written
                           (LIF-1: entries already soft-invalidated are counted, not
                           re-listed — demote's terminal state must not re-nag).
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
from .provenance import CORPUS_FORMAT_VERSION, read_corpus_format, resolve_dirs
from .recall import _INVALIDATION_RECENT_DAYS, _invalidation_state, git_recent_producer
from .reconsolidate import reconsolidation_producer
from .staleness import (
    count_unresolvable_baselines,
    find_citation_rot,
    find_stale,
    find_unparseable,
    invalid_after_map,
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


def stale_venv_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """One-line re-bootstrap nudge when plugin deps changed after the last bootstrap.

    The venv-in-PLUGIN_DATA model is update-safe for CODE but not DEPS: a plugin update
    that bumps requirements.txt leaves hooks running the old venv indefinitely, with new
    imports failing into silent excepts (COR-11). Delegates the sha256 compare to the
    canonical ``bootstrap_state`` (shared with doctor); nudges ONLY on ``"stale"``. Silent
    when not bootstrapped (ONB-1's pre-Python nudge owns that state) or when anything is
    unreadable. Runs once per session by construction (SessionStart).
    """
    if bootstrap_state() != "stale":
        return None
    return (
        "⚠ hippo deps changed with the last plugin update — the venv still runs the "
        "old dependency set (new imports degrade silently). Run /hippo:bootstrap to "
        "re-provision."
    )


def corpus_format_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """LOUD warning when the corpus declares a format NEWER than this plugin understands.

    COR-7: the ``.claude/memory/.format`` marker travels with the corpus through git, so a
    teammate on a newer hippo can bump the corpus format under a machine still running an
    older plugin — which would otherwise keep reading conventions it predates with NO
    signal anywhere. The fix is one-directional and user-side (update the plugin), hence a
    per-session producer. The OTHER direction (corpus OLDER than the plugin expects) is
    deliberately NOT nagged here: migrating user data is a doctor-driven, agent-gated path
    (see ``doctor.check_format_version`` + the README), not a per-session alarm. Silent on
    an undeclared corpus (no marker == format 1) and on any read problem.
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


def citation_rot_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """Count-first warning for memories citing paths that no longer exist in the repo (LIF-3).

    The current-state citation-rot report — catches a rename/delete of cited code even when
    no refresh has run yet (nothing has dropped, but staleness can't watch a vanished path,
    and the next re-derivation would silently shrink cited_paths — possibly to zero, which
    permanently exempts the memory). This producer is the ONE canonical SessionStart surface;
    the CLI sibling lives in ``memory.staleness``'s default report, next to the unparseable
    block. TOTAL rot (every citation gone) is called out distinctly on its line.
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


def staleness_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    # find_stale already orders most-recently-drifted first.
    diagnostics: dict = {}
    stale = find_stale(memory_dir, repo_root, diagnostics=diagnostics)
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
    active = [item for item in stale if item["name"] not in invalidated]
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
        if invalidated:
            lines.append(
                f"  (+{len(invalidated)} already demoted — invalid_after set; recall already "
                "ranks them down, no re-verify needed)"
            )
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


def index_integrity_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """LOUD diagnosis for on-disk recall-index corruption (QUA-5).

    ``build_index``/``recall`` already degrade gracefully (never raise) on a truncated
    manifest, a missing dense.npy, or a wrong-shape dense.npy — but until now nothing named
    WHICH of those states was present, so "memory stopped working" had no diagnosis surface.
    Silent when the index doesn't exist yet (nothing built) or is healthy.
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
    ("corpus_format", corpus_format_producer),  # a corpus NEWER than the plugin taints every reader below (COR-7)
    ("integrity", integrity_producer),  # a malformed memory must not hide
    ("citation_rot", citation_rot_producer),  # cited paths gone from the repo (LIF-3) — find_unparseable's rot sibling
    ("staleness", staleness_producer),
    ("reconsolidation", reconsolidation_producer),  # recall-filtered subset of staleness; silent unless a recently-recalled memory is stale
    ("index_integrity", index_integrity_producer),  # names on-disk index corruption (QUA-5) — recall/build_index already degrade silently
    ("unresolvable_baseline", unresolvable_baseline_producer),  # legibility for find_stale's sha-fallback path
    ("git_recent", git_recent_producer),
    ("link_health", lint_links_producer),
    ("floor", floor_producer),  # silent unless project/reference links re-bloat the MEMORY.md floor
]


# How often the untrusted-corpus nudge fires among nudge-eligible sessions (mirrors ONB-1's
# NUDGE_EVERY): loud enough to be seen, quiet enough not to nag every session.
_TRUST_NUDGE_EVERY = 5


def _trust_nudge_should_fire(repo_root: str) -> bool:
    """Low-frequency gate for the untrusted-corpus nudge (mirrors ONB-1's modulo pattern).

    Fires on the 1st nudge-eligible session and every ``_TRUST_NUDGE_EVERY``-th after, using a
    per-corpus counter file under ``CLAUDE_PLUGIN_DATA`` so trusting one corpus never silences
    the nudge for another. When ``CLAUDE_PLUGIN_DATA`` is unset (dev checkout / hermetic test
    with no plugin-data dir), there is nowhere durable to keep the counter — fail toward
    LEGIBLE and fire every session rather than swallow the signal. Never raises.
    """
    try:
        import hashlib

        data_dir = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
        if not data_dir:
            return True
        key = hashlib.sha256(os.path.realpath(repo_root).encode("utf-8")).hexdigest()[:16]
        counter_path = os.path.join(data_dir, f".trust-nudge-{key}")
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
        return count % _TRUST_NUDGE_EVERY == 0
    except Exception:
        return True


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
