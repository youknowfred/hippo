"""Lifecycle, collaboration & publish-boundary checks for the deterministic doctor
engine — decomposed out of ``doctor_checks_corpus.py`` (REL-1) along its TMB-2/TMB-3
section banner; ``doctor.py`` keeps the ordered check registry, the engine, and the CLI.

The T11 terminal-state + forgetting instruments (TMB-2 invalid_after terminal count,
TMB-3 archive shadowing + regret evidence), the CLB-3 quoted-evidence fences, the CLB-4
incoming-merge digest, CLB-2 team verification coverage, and the PUB-3 committed-subset
boundary. ``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

import os
from typing import Dict

from .doctor_checks_env import DoctorContext, _iter_memory_files_safe


# --------------------------------------------------------------------------- #
# TMB-2/TMB-3 (T11): the terminal-state + forgetting instruments
# --------------------------------------------------------------------------- #
def check_invalid_after_terminal(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-2: the corpus-wide terminal-state count — retirements the drift signal can't see.

    A memory retired via supersede/merge (``invalid_after`` past recall's old horizon,
    NO cited-code drift) never enters ``find_stale``'s set, so before this it was counted
    NOWHERE: recall display-filters it, the staleness producer's invalid_after map is
    stale-scoped, and archive_candidates was stale-gated 4-way. One line, ok-at-zero;
    the fix path is the shipped flows — archive via ``python -m memory.archive`` (TMB-2's
    admission leg lists them), reinstate per item via reconsolidate outcome=graduate|fix
    (``reverify_file`` strips the stamp). Cold path; the corpus scan + git-log window
    both belong here, never the hot path.
    """
    try:
        from .staleness import find_stale as _find_stale
        from .staleness import nondrift_old_invalidated

        stale_names = [item["name"] for item in _find_stale(ctx.memory_dir, ctx.repo_root)]
        retired = nondrift_old_invalidated(ctx.memory_dir, stale_names)
        if not retired:
            return {
                "status": "ok",
                "message": "invalid_after: no memories retired outside the drift signal.",
            }
        names = sorted(retired)
        shown = ", ".join(names[:4]) + (f" (+{len(names) - 4} more)" if len(names) > 4 else "")
        return {
            "status": "warn",
            "message": f"invalid_after: {len(names)} memor"
            + ("y" if len(names) == 1 else "ies")
            + f" retired past the old horizon with no code drift ({shown}) — archivable "
            "via `python -m memory.archive`; reinstate per item via reconsolidate "
            "outcome=graduate|fix.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"invalid_after terminal-state check failed: {exc}."}


def check_archive_shadowing(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-3: a stem present in BOTH ``archive/`` and the live corpus — the shadow hazard.

    ``_first_seen_times`` deliberately skips ``archive/`` because an archived stem could
    shadow a live one; ``archive.restore`` refuses collisions for the same reason. This
    check makes an EXISTING collision visible (a hand-copied file, a merge that
    resurrected an archived name). Read-only — the printed suggestion is a manual
    ``git mv``; nothing here writes.
    """
    try:
        archive_dir = os.path.join(ctx.memory_dir, "archive")
        if not os.path.isdir(archive_dir):
            return {"status": "ok", "message": "archive shadowing: no archive/ directory."}
        archived = {
            fn[:-3] for fn in os.listdir(archive_dir) if fn.endswith(".md")
        }
        from .provenance import _iter_memory_files

        live = {
            os.path.splitext(os.path.basename(p))[0]
            for p in _iter_memory_files(ctx.memory_dir)
        }
        shadows = sorted(archived & live)
        if not shadows:
            return {
                "status": "ok",
                "message": f"archive shadowing: none ({len(archived)} archived stem(s) "
                "all distinct from the live corpus).",
            }
        shown = ", ".join(shadows[:4]) + (f" (+{len(shadows) - 4} more)" if len(shadows) > 4 else "")
        return {
            "status": "warn",
            "message": f"archive shadowing: {len(shadows)} stem(s) exist in BOTH archive/ "
            f"and the live corpus ({shown}) — the live file wins everywhere, but restore "
            "would refuse and provenance history is ambiguous; resolve by hand, e.g. "
            "`git mv .claude/memory/archive/<name>.md .claude/memory/archive/<name>.superseded.md`.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"archive-shadowing check failed: {exc}."}


def check_archive_regret(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-3 (e): the evidence-only regret detector's ONE surface — doctor time, never
    per-prompt. Inert text + a logged regret event per NEW (query, stem) match; ZERO
    restore action attached (no code path from here to ``archive.restore`` — pinned).
    """
    try:
        from .archive import archive_regret
        from .telemetry import default_telemetry_dir, log_archive_regret, read_archive_regret

        td = default_telemetry_dir(ctx.memory_dir)  # this corpus's ledger home, not ambient
        matches = archive_regret(ctx.memory_dir, telemetry_dir=td)
        if not matches:
            return {
                "status": "ok",
                "message": "archive regret: no recurring abstention matches an archived memory.",
            }
        already = {(e.get("query"), e.get("stem")) for e in read_archive_regret(td)}
        for m in matches:
            if (m["query"], m["stem"]) not in already:
                log_archive_regret(m["query"], m["stem"], telemetry_dir=td)
        shown = "; ".join(
            f"\"{m['query']}\" (asked ×{m['count']}) ↔ archived `{m['stem']}`"
            for m in matches[:3]
        )
        return {
            "status": "warn",
            "message": f"archive regret: {len(matches)} recurring abstention(s) match an "
            f"ARCHIVED memory's body ({shown}) — evidence only, logged; if one is a real "
            "regret, a human can restore it by name (`python -m memory.archive --restore "
            "<stem>`); nothing restores automatically.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"archive-regret check failed: {exc}."}


def check_evidence_fences(ctx: DoctorContext) -> Dict[str, str]:
    """CLB-3: quoted-evidence coverage + drift — one line, deterministic.

    Counts memories carrying MARKED evidence fences vs memories with code fences
    but no marker (pre-marker drains — "unverifiable", out of the detector's scope
    by contract, never backfilled), then reports live drift via the same matcher
    the SessionStart pipeline runs (``staleness_evidence.evidence_drift_map`` —
    one implementation, so doctor and the RET-6 banner can never disagree). A
    corpus with no fences at all reports coverage zero and stays ``ok``; drifted
    memories flip to ``warn`` naming the first few — routing is the existing
    reverify gate, never a new verb.
    """
    try:
        from .markdown_code import FENCED_CODE_RE
        from .staleness_evidence import evidence_drift_map, extract_evidence_fences

        marked = unverifiable = 0
        for path in _iter_memory_files_safe(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            if not FENCED_CODE_RE.search(text):
                continue
            if extract_evidence_fences(text):
                marked += 1
            else:
                unverifiable += 1
        drift = evidence_drift_map(ctx.memory_dir, ctx.repo_root)
        base = (
            f"quoted evidence: {marked} memory(ies) carry evidence-marked fences; "
            f"{unverifiable} with unmarked code fences (unverifiable — pre-marker, never backfilled)"
        )
        if not drift:
            return {"status": "ok", "message": f"{base}; no evidence drift."}
        shown = ", ".join(sorted(drift)[:4])
        more = f" (+{len(drift) - 4} more)" if len(drift) > 4 else ""
        return {
            "status": "warn",
            "message": f"{base}; {len(drift)} with DRIFTED quoted evidence: {shown}{more} "
            "— re-verify each via the reconsolidation gate (reverify graduate|fix|demote).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"evidence-fence check failed: {exc}."}


def check_merge_digest(ctx: DoctorContext) -> Dict[str, str]:
    """CLB-4: incoming-merge duplicate pairs — the doctor half of the digest.

    Re-derives the SAME pairs the SessionStart producer surfaces (one derivation,
    ``merge_digest.incoming_duplicate_pairs`` — never a second detector) so a lead
    running doctor after a merge sees them without waiting for the next session
    start. Routing is identical and human: /hippo:resolve for declared
    contradictions, /hippo:consolidate's GRW-3 merge tier for the rest. The
    unreachable-watermark case renders as its own legible state.
    """
    try:
        from .merge_digest import incoming_duplicate_pairs
        from .telemetry import default_telemetry_dir

        pairs, degradation, incoming = incoming_duplicate_pairs(
            ctx.memory_dir, ctx.repo_root, default_telemetry_dir(ctx.memory_dir)
        )
        if degradation and not pairs:
            return {
                "status": "warn",
                "message": "incoming-merge dedup: last-session watermark unreachable "
                "(squash-merge/rewrite) — the incoming range could not be dup-checked; "
                "self-heals next session.",
            }
        if not pairs:
            return {
                "status": "ok",
                "message": "incoming-merge dedup: no duplicate pairs among memories "
                "merged in since the last session.",
            }
        shown = "; ".join(
            f"{p['incoming']} ⇄ {p['neighbor']} → "
            + ("/hippo:resolve" if p["route"] == "resolve" else "/hippo:consolidate")
            for p in pairs
        )
        return {
            "status": "warn",
            "message": f"incoming-merge dedup: {len(pairs)} duplicate-candidate pair(s) "
            f"across {incoming} merged-in memory file(s) — {shown} (human-routed; "
            "nothing merges automatically).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"merge-digest check failed: {exc}."}


def check_team_coverage(ctx: DoctorContext) -> Dict[str, str]:
    """CLB-2: the last_verified + verified_by consumers — verification made legible.

    The ``last_verified`` half renders for EVERY corpus (its first production
    consumer — the field was written since RET-6 but surfaced nowhere). The
    ``verified_by`` team half renders ONLY at ≥2 distinct git authors — at ≤1 the
    line says "suppressed" and carries ZERO coverage numbers (solo
    self-verification stats would only teach the reader to ignore the line).
    Counts only, no names, no timestamps — deterministic (the doctor pin).
    """
    try:
        from .team_coverage import team_coverage, verification_summary

        vs = verification_summary(ctx.memory_dir)
        base = (
            f"verification: {vs['last_verified']} of {vs['total']} memories carry "
            "last_verified (stamped by the reverify gate)"
        )
        team = team_coverage(ctx.memory_dir, ctx.repo_root)
        if team is None:
            return {
                "status": "ok",
                "message": f"{base}; team attribution suppressed (single git author).",
            }
        return {
            "status": "ok",
            "message": (
                f"{base}; team ({team['authors']} authors): {team['stamped']} verified_by "
                f"stamp(s), {team['non_author_verified']} non-author-verified, "
                f"{team['never_other_verified']} never verified by a non-author, "
                f"{team['departed']} departed-verifier stamp(s)."
            ),
        }
    except Exception as exc:
        return {"status": "warn", "message": f"team-coverage check failed: {exc}."}


def check_subset_boundary(ctx: DoctorContext) -> Dict[str, str]:
    """PUB-3: committed-subset link honesty — the view a fresh checkout sees. Never a
    gate (expected-not-error per PR #67; no CI consumer fails on it); empty norms twice
    over (no committed subset / healed boundary both render ok). Read-only; never raises."""
    try:
        from .lint_links import boundary_lint

        view = boundary_lint(ctx.memory_dir, ctx.repo_root)
        if not view["ok"] or not view["files"]:
            return {"status": "ok", "message": "subset boundary: no committed memory subset — nothing to check."}
        findings = view["dangling"] + view["typed_dangling"]
        if not findings:
            return {
                "status": "ok",
                "message": f"subset boundary: clean — every committed link resolves inside "
                f"the {view['files']}-file committed subset.",
            }
        heal = ""
        if view["heals_by"]:
            stem, n = sorted(view["heals_by"].items(), key=lambda kv: (-kv[1], kv[0]))[0]
            heal = f"; publishing {stem} would heal {n}"
        return {
            "status": "warn",
            "message": f"subset boundary: {len(findings)} committed link target(s) dangle in a "
            f"fresh checkout ({len({d['file'] for d in findings})} of {view['files']} committed "
            f"files{heal}) — expected-not-error (PR #67), never a gate; view: "
            "python -m memory.lint_links --boundary.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"subset-boundary check failed: {exc}."}
