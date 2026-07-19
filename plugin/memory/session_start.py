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

The producer implementations live in the flat siblings ``session_start_health`` /
``session_start_signals`` (REL-1); this façade keeps the ``PRODUCERS`` registry, the
dispatcher (``build_context``/``main``), the bounding/surface-note helpers, and explicit
re-exports of every moved name, so every historical ``memory.session_start.<name>``
dotted path keeps resolving.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, Dict, List, Optional, Tuple

from .dream import dream_applied_producer
from .lint_floor import floor_producer
from .lint_links import lint_links_producer
from .provenance import resolve_dirs
from .recall import git_recent_producer, portable_floor_producer
from .merge_digest import merge_digest_producer
from .presence import presence_producer, write_presence
from .reconsolidate import (
    recalled_stale_worklist,
    reconsolidation_producer,
    watermark_stale_candidates,
)
from .staleness import RunContext, find_stale, write_stale_cache

# REL-1 decomposition: the producer implementations live in the flat, prefix-named
# siblings below; these explicit grouped re-imports keep every historical
# ``memory.session_start.<name>`` import and monkeypatch target (bootstrap, doctor,
# merge_digest, tests) resolving unchanged.
from .session_start_health import (
    _MAX_ITEMS_PER_PRODUCER,
    bootstrap_state,
    stale_venv_producer,
    corpus_format_producer,
    cite_derivation_producer,
    integrity_producer,
    citation_rot_producer,
    index_integrity_producer,
    unresolvable_baseline_producer,
    _MAX_HEAL_NAMES,
    _SQUASH_SUBJECT_RE,
    _recent_merge_signals,
    squash_merge_heal_producer,
    _MAX_TRUST_DRIFT_NAMES,
    trust_drift_producer,
    _TRUST_NUDGE_EVERY,
    _periodic_nudge_should_fire,
    _trust_nudge_should_fire,
    untrusted_corpus_nudge,
)
from .session_start_signals import (
    staleness_producer,
    pending_capture_producer,
    _BLIND_SPOT_NUDGE_EVERY,
    _MAX_BLIND_SPOT_LINES,
    blind_spot_producer,
    _MAX_RULES_CONFLICT_LINES,
    rules_conflict_producer,
    _MAX_RULES_ROT_LINES,
    rules_rot_producer,
    _MAX_CONTRADICTION_LINES,
    contradiction_inbox_producer,
    _GOV4_WATERMARK_PREFIX,
    _MAX_FLOOR_DELTA_NAMES,
    _gov4_watermark_path,
    _gov4_snapshot,
    _gov4_delta,
    floor_change_peek,
    floor_change_producer,
    _MAX_RELEVANT_ITEMS,
    _RELEVANT_DESC_CHARS,
    _diff_query,
    relevant_to_work_producer,
    _MAX_RESUME_THEMES,
    _MAX_RESUME_RELIED,
    _MAX_RESUME_CHANGED,
    _MIN_RESUME_THEMES,
    _corpus_cited_union,
    resume_card_producer,
)

# Harness caps hook output at 10,000 chars; stay comfortably under it.
_MAX_CONTEXT_CHARS = 9000
_MAX_ITEMS_PER_PRODUCER = 20

# Typed /hippo:* commands exist only in the Claude Code terminal CLI. The Claude Desktop
# app (CLAUDE_CODE_ENTRYPOINT=claude-desktop, present in the hook env) runs the same
# hooks/skills/MCP server but REJECTS typed plugin commands — so producer advice like
# "run /hippo:doctor" dead-ends there. Rather than fork every producer's wording, the
# dispatcher appends ONE mapping note when (a) the merged context names a /hippo:* command
# and (b) the surface is the Desktop app — the same append-a-suffix shape the MCP doctor
# tool already uses. Producers stay byte-identical for a given corpus state (the DOC-4
# determinism posture); the note is keyed deterministically on env state.
_DESKTOP_ENTRYPOINT = "claude-desktop"
_DESKTOP_SURFACE_NOTE = (
    "⌨ Surface note: this session is the Claude Desktop app — typed /hippo:* commands are "
    "terminal-only and will not work here. Take these routes SILENTLY — just call the tool; skip "
    "the why-not-bash preamble, it is repeated noise. When acting on (or relaying) any /hippo:* advice "
    "above, use the Desktop equivalents: /hippo:bootstrap → the hippo bootstrap MCP tool, "
    "/hippo:init → the init tool, /hippo:doctor → the doctor tool (trust/re-consent → the "
    "trust_corpus tool), /hippo:consolidate → the consolidate skill driving its MCP tools "
    "(capture, new_memory check:true, secrets_scan, reconsolidate, build_index, "
    "co_recall_proposals, abstention_fixtures, interview — per item); /hippo:pack → the pack skill "
    "driving the pack_* MCP tools (pack_extract; install: pack_install_plan then per-item "
    "pack_install_item; update: pack_update_plan then per-item pack_update_item); "
    "/hippo:dream → the dream tool; /hippo:new → the new_memory tool; /hippo:recall → the "
    "recall tool (its --list-by-type and --all-projects modes are terminal-only); "
    "/hippo:why → the why tool; "
    # INV-4 (scope ratified 2026-07-16): the two nudge-routed dead ends get real routes —
    # resolve + audit only; the other five keep their honest terminal-only preflights.
    "/hippo:resolve → the resolve tool (action='inbox', then ONE action='verdict' per "
    "pair); /hippo:audit → the audit skill driving the audit tool (read-only report "
    "material; judgment and applies stay per-item in the skill). "
    # INT-19: never promise a route that dead-ends — this list stays honest.
    "NOT available on this surface (terminal-only for now — say so, do not improvise a "
    "workaround): export-agents, import, promote, promote-rule, publish, remove, review. "
    "The corpus-repair and incident-response verbs are MCP tools on BOTH surfaces, with no "
    "/hippo:* form: rederive (action='worklist'|'one'|'snapshot'|'stamp'), heal_baselines, "
    "untrust (revoke a corpus's trust after finding it bad) and blast_radius (read-only: "
    "what a suspect memory touched)."
)


def _surface_note(ctx: str) -> str:
    """The Desktop mapping note for a merged context that names typed ``/hippo:*`` commands.

    Empty (the common case) unless BOTH hold: the context mentions a ``/hippo:`` command
    somewhere, and this process runs under the Claude Desktop app's harness. Reading the
    entrypoint from the env at call time keeps the output deterministic per surface —
    the same corpus state renders the same bytes on the same surface.
    """
    if "/hippo:" not in ctx:
        return ""
    if (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").strip() != _DESKTOP_ENTRYPOINT:
        return ""
    return _DESKTOP_SURFACE_NOTE


def _bound_with_surface_note(ctx: str, max_chars: int) -> str:
    """Bound the merged context and append the Desktop surface note when it applies.

    The note's budget is reserved BEFORE truncation so appending it can never push the
    output past ``max_chars`` — and the note is dropped entirely (never truncated into
    garbage) when ``max_chars`` is too small to carry both it and a useful signal. With
    no note this reduces exactly to the old bound: byte-identical terminal output.
    """
    if not ctx:
        return ""
    note = _surface_note(ctx)
    if note and len(note) + 200 > max_chars:
        note = ""  # never let the mapping note crowd out the signal itself
    budget = max_chars - (len(note) + 2 if note else 0)
    if len(ctx) > budget:
        ctx = ctx[: budget - 16].rstrip() + "\n…(truncated)"
    return ctx + ("\n\n" + note if note else "")


# (label, fn). Each tier appends a producer here — never a parallel hook entry. Every fn
# shares ONE call shape — ``(memory_dir, repo_root, ctx)`` — LIF-6's ``RunContext``, even
# when a given producer ignores it (see the module docstring).
PRODUCERS: List[Tuple[str, Callable[[str, str, Optional[RunContext]], Optional[str]]]] = [
    ("stale_venv", stale_venv_producer),  # environment-level — a stale venv taints everything below
    ("corpus_format", corpus_format_producer),  # a corpus NEWER than the plugin taints every reader below (COR-7)
    ("cite_derivation", cite_derivation_producer),  # citations derived by a fixed-since extractor (DRV-2)
    ("integrity", integrity_producer),  # a malformed memory must not hide
    ("citation_rot", citation_rot_producer),  # cited paths gone from the repo (LIF-3) — find_unparseable's rot sibling
    ("trust_drift", trust_drift_producer),  # SEC-6: trusted corpus drifted from its consent baseline — recall is withholding files
    ("presence", presence_producer),  # FLT-1: another live session shares this working tree (fleet visibility; empty-norm)
    ("staleness", staleness_producer),
    ("reconsolidation", reconsolidation_producer),  # recall-filtered subset of staleness; silent unless a recently-recalled memory is stale
    ("pending_capture", pending_capture_producer),  # CAP-2: surface the gitignored draft-capture queue so it never soaks silently
    ("dream_applied", dream_applied_producer),  # DRM-2: dream edges awaiting age-in — the deferred half of notify-with-undo (aged-in edges drop off)
    ("blind_spot", blind_spot_producer),  # SIG-3: recurring recall abstentions -> a low-frequency curation backlog
    ("index_integrity", index_integrity_producer),  # names on-disk index corruption (QUA-5) — recall/build_index already degrade silently
    ("unresolvable_baseline", unresolvable_baseline_producer),  # legibility for find_stale's sha-fallback path
    ("squash_merge_heal", squash_merge_heal_producer),  # GRW-6: merge detected + baselines broken -> per-item rebaseline offer
    ("rules_conflict", rules_conflict_producer),  # RUL-1: governance cites a memory the corpus disputes (superseded/contradicted/never-recalled)
    ("rules_rot", rules_rot_producer),  # RUL-2: citation-rot/staleness over the always-loaded rules plane itself
    ("contradiction_inbox", contradiction_inbox_producer),  # GOV-1: every unresolved contradicts pair, not just the co-surfaced/governance-cited ones
    ("floor_change", floor_change_producer),  # GOV-4: floor/corpus changed since this clone's last session (per-clone watermark; a seen change stays quiet)
    ("merge_digest", merge_digest_producer),  # CLB-4: incoming-merge duplicate digest — GRW-3's detector over the watermark range, human-routed
    ("relevant_to_work", relevant_to_work_producer),  # SIG-1: the first POSITIVE block — memories about the files you're editing
    ("resume_card", resume_card_producer),  # SIG-2: "where was I" — replay the last session from the episode buffer
    ("git_recent", git_recent_producer),
    ("link_health", lint_links_producer),
    ("floor", floor_producer),  # silent unless project/reference links re-bloat the MEMORY.md floor
    ("portable_floor", portable_floor_producer),  # TEA-1: deliver the user/private-tier floor (no native channel)
]


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
    # CLB-3: quoted-evidence drift — extraction + matching live HERE, in the find_stale
    # pipeline (never build_index/_ensure_index; tests/test_evidence_drift.py pins that
    # structurally), and drifted names union into the SAME watermark lane above — the one
    # semantic_reverify gate, no new write verb.
    evidence_drift: Dict[str, dict] = {}
    try:
        from .staleness_evidence import evidence_drift_map, fold_drift_candidates

        evidence_drift = evidence_drift_map(memory_dir, repo_root)
        wm_stale = fold_drift_candidates(wm_stale, evidence_drift)
    except Exception:
        evidence_drift = {}
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

            write_stale_cache(default_index_dir(memory_dir), stale, evidence_drift=evidence_drift)
    except Exception:
        pass
    # T16 JIT-1: refresh the first-touch reminder map (touchmap.json) at this SAME
    # offline, trust-gated moment — the PostToolUse lane only ever reads that derived
    # file, never the corpus (its measured budget depends on it). Gated on the lane's
    # own kill switch: HIPPO_DISABLE_JIT means byte-for-byte pre-T16 behavior, so the
    # write is skipped too rather than minting a cache nothing will read.
    try:
        if os.path.isdir(memory_dir):
            from .build_index import default_index_dir
            from .jit import jit_disabled, refresh_touch_cache

            if not jit_disabled():
                refresh_touch_cache(memory_dir, default_index_dir(memory_dir))
    except Exception:
        pass
    # RET-14: same offline moment — refresh recall's outcome-prior cache from the KPI-2
    # join (SIG-4's episode x outcome ledger reconciliation), so the hot path (gated by its
    # OWN HIPPO_OUTCOME_PRIOR flag, independent of HIPPO_SALIENCE) only ever reads a small
    # cached JSON, never re-runs the live join itself. Gated on the SAME flag being on for
    # THIS session (checked here, not imported from recall.py, to keep this module's
    # dependency graph as-is) — unlike stale.json (which has a SECOND, always-on consumer:
    # the RET-6 verify-at-use banner), outcome.json has exactly one consumer and it's
    # OFF by default, so computing the live ledger join on every session regardless would
    # be pure waste. A user who flips the flag on gets a populated cache from THIS session's
    # SessionStart onward (recall.py already degrades gracefully to no-boost on a cache
    # that predates the flag being turned on).
    if os.environ.get("HIPPO_OUTCOME_PRIOR", "").strip() not in ("", "0", "false", "False"):
        try:
            if os.path.isdir(memory_dir):
                from .build_index import default_index_dir
                from .outcome import injection_hits, write_outcome_cache

                write_outcome_cache(default_index_dir(memory_dir), injection_hits(memory_dir))
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


def build_context(
    memory_dir: str,
    repo_root: str,
    max_chars: int = _MAX_CONTEXT_CHARS,
    *,
    producer_chars: Optional[dict] = None,
) -> str:
    """Run every producer, merge their non-empty blocks, bound the total. Never raises.

    SEC-1 short-circuit: when this project's corpus exists but is NOT trusted, EVERY content
    producer stays silent (an untrusted corpus injects nothing) and the ONLY block emitted is
    the low-frequency untrusted-corpus nudge — the single legible signal on the gated path.

    Both return paths route through ``_bound_with_surface_note`` so any ``/hippo:*`` advice
    (including the untrusted nudge's) names its Desktop-app equivalent on that surface.

    MSR-6 ``producer_chars``: an OPT-IN out-param dict filled with each contributing
    producer's emitted char count ``{label: len(block)}`` — the numbers this function
    already computes to assemble the payload, measured, never estimated. The output is
    byte-identical whether or not the dict is passed (observation only). The SEC-1
    short-circuit deliberately fills NOTHING: an untrusted corpus's nudge must not
    grow a ledger row (zero-trace posture), and the caller only logs a non-empty dict.
    """
    try:
        from . import trust

        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        if gate_root is not None and not trust.is_trusted(gate_root):
            return _bound_with_surface_note(
                untrusted_corpus_nudge(memory_dir, repo_root) or "", max_chars
            )
    except Exception:
        pass
    run_ctx = _build_run_context(memory_dir, repo_root)
    blocks: List[str] = []
    for _label, fn in PRODUCERS:
        try:
            out = fn(memory_dir, repo_root, run_ctx)
        except Exception as exc:
            # RCH-9: every producer is individually guarded, so this backstop firing
            # means a real bug — exactly when silence is most expensive. Name it (the
            # doctor pattern: a visible warn carrying the exception), keep the rest.
            out = f"⚠ {_label} producer failed: {type(exc).__name__}: {exc}"
        if out:
            blocks.append(out.rstrip())
            if producer_chars is not None:
                producer_chars[_label] = len(blocks[-1])
    if not blocks:
        return ""
    return _bound_with_surface_note("\n\n".join(blocks), max_chars)


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
        # COR-10: this hook used to call heal_empty_baselines() here — a WRITE to memory
        # frontmatter from an automatic pass. trust.py states "hooks NEVER consent", which
        # is only sound if hooks never WRITE: the heal changed the file's bytes, drifted it
        # from its own SEC-6 fingerprint, and the trust-drift banner a few lines below then
        # asked the user "a git pull? a hand edit?" about a write hippo had just done to
        # itself. The heal is still available and still correct — it moved to the
        # provenance CLI (--heal-baselines), which doctor's empty-baseline check names, so
        # the write happens where a human can consent to it.
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
            write_presence(memory_dir, repo_root, session_id=session_id)  # T18 FLT-1: fleet presence doc
        except Exception:
            pass
        producer_chars: dict = {}
        ctx = build_context(memory_dir, repo_root, producer_chars=producer_chars)
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
            # MSR-6: record what this SessionStart actually injected, per producer,
            # against the char budget — fire-and-forget AFTER emission (output is
            # byte-identical with or without it). Same corpus-existence guard as the
            # session-token block above (SEC-3: a never-opted-in project grows no
            # ledger); the SEC-1 untrusted path filled no producer_chars, so it
            # writes nothing here either (zero trace).
            try:
                from .telemetry import default_telemetry_dir, log_injection_producers

                if producer_chars and os.path.isdir(memory_dir):
                    log_injection_producers(
                        producer_chars,
                        total=len(ctx),
                        cap=_MAX_CONTEXT_CHARS,
                        telemetry_dir=default_telemetry_dir(memory_dir),
                        session_id=session_id,
                    )
            except Exception:
                pass
    except Exception:
        pass  # SessionStart must never fail loudly
    return 0


if __name__ == "__main__":
    sys.exit(main())
