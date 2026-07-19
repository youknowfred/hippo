"""Consolidate-flow tools (INT-13) + corpus repair (INT-14/15) for the stdio MCP server:
capture, secrets_scan, reconsolidate, rederive, heal_baselines, build_index,
co_recall_proposals, and abstention_fixtures — /hippo:consolidate's steps as thin,
per-item primitives. Decomposed out of ``mcp_server.py`` as pure code motion; the façade
re-imports every name, so ``memory.mcp_server.<name>`` stays importable."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from .mcp_tools_core import _UNTRUSTED_REMEDY
from .mcp_tools_setup import _fresh_python, _subprocess_env


# --------------------------------------------------------------------------- #
# Consolidate-flow tools (INT-13) — /hippo:consolidate's five steps as thin,
# per-item primitives. Each wraps the SAME engine call the skill's bash blocks
# run (no behavior fork); every write stays one approval-gated item per call.
# --------------------------------------------------------------------------- #
def _tool_capture(args: Dict[str, Any]) -> str:
    """CAP-2/CAP-6/GRW-4: the pending-queue verbs of ``memory.capture``'s CLI, re-served.

    Deliberately UNGATED by SEC-1: the queue is gitignored session-local ephemera (the same
    trust domain as the episode buffer — it never arrives via a clone), and the drain's
    corpus writes all route through ``new_memory``, which carries the SEC-13 gate."""
    from .capture import (
        _SNOOZE_WINDOW_SESSIONS,
        _format_listing,
        corrupt_pending,
        default_pending_dir,
        discard_pending,
        read_pending,
        snooze_queue,
    )
    from .provenance import resolve_dirs

    memory_dir, _repo_root = resolve_dirs()
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        seeds = read_pending(memory_dir=memory_dir)
        out = [_format_listing(seeds)]
        broken = corrupt_pending(memory_dir=memory_dir)
        if broken:
            # RCH-9: the nudge's bare file count includes these — the listing must
            # name what it cannot read, or a captured session vanishes untraced.
            out.append(
                f"⚠ {len(broken)} corrupt seed file(s) skipped (unreadable JSON — "
                "inspect or delete them in the queue dir): " + ", ".join(broken)
            )
        if seeds:
            out.append("")
            out.append(
                f"queue dir: {default_pending_dir(memory_dir)} — each seed is a readable "
                "JSON file; open it for the full evidence (query previews, decisions, "
                "verbatim diff hunks)."
            )
            if any(s.get("hunks_secret_flagged") for s in seeds):
                out.append(
                    "on this MCP surface, scan_with_remediation = the secrets_scan tool — "
                    "lint the exact hunk lines there before fencing ANY into a body."
                )
            out.append(
                "Drain per item: draft the fact → new_memory (check:true) → secrets_scan "
                "any verbatim hunk → new_memory (the real write) → capture "
                "(action='discard', path=<seed>). Nothing is approved in bulk."
            )
        return "\n".join(out)
    if action == "discard":
        path = str(args.get("path") or "").strip()
        if not path:
            return "capture discard: 'path' is required — a seed path or filename from action='list'."
        pd = os.path.realpath(default_pending_dir(memory_dir))
        candidate = path if os.path.isabs(path) else os.path.join(pd, path)
        real = os.path.realpath(candidate)
        base = os.path.basename(real)
        # Containment: the CLI trusts a human-typed path; a model-invoked remove must only
        # ever touch seeds inside the pending queue (never dotfiles — the queue's own
        # .gitignore and snooze marker are queue state, not seeds).
        if os.path.dirname(real) != pd or not base.endswith(".json") or base.startswith("."):
            return (
                "capture discard REFUSED — the path must name a seed file inside the "
                f"pending queue ({pd}); this tool never removes anything else."
            )
        ok = discard_pending(real)
        return f"discarded: {real}" if ok else f"nothing to discard at {real}"
    if action == "snooze":
        ok = snooze_queue(memory_dir=memory_dir)
        return (
            f"pending-capture nudge snoozed for {_SNOOZE_WINDOW_SESSIONS} sessions "
            "(seeds kept; the nudge re-nags after it expires)"
            if ok
            else "could not record the snooze (unwritable pending dir)"
        )
    if action == "add_decision":
        from .telemetry import log_decision

        text = str(args.get("text") or "").strip()
        if not text:
            return (
                "capture add_decision: 'text' is required — ONE user-confirmed decision, "
                "quoted or faithfully paraphrased in the user's own terms (transcription, "
                "never synthesis)."
            )
        ok = log_decision(text)
        return (
            "decision recorded — it will ride this session's capture seed as its durable WHY"
            if ok
            else "nothing recorded (empty text or unwritable ledger)"
        )
    return "capture: pass action='list' (default), 'discard' (path=…), 'snooze', or 'add_decision' (text=…)."


def _tool_secrets_scan(args: Dict[str, Any]) -> str:
    """The GRW-1 hard gate as a primitive: ``secrets.scan_with_remediation`` over the exact
    lines the caller intends to fence. Ungated — a pure function over caller-supplied text
    that reads nothing from the corpus and touches nothing on disk."""
    from .secrets import scan_with_remediation

    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return "secrets_scan: 'text' is required — the exact lines you intend to fence into a memory body."
    warnings = scan_with_remediation(text)
    if not warnings:
        return "✔ clean — no secret patterns found; these lines are safe to fence into a memory body."
    return "\n".join(
        [
            "✘ HARD GATE — secret lint flagged these lines; do NOT fence them into a memory "
            "body (a seed is gitignored, a body is committed and recalled forever). Drop or "
            "scrub the flagged lines, then scan again until clean:"
        ]
        + [f"  {w}" for w in warnings]
    )


def _tool_reconsolidate(args: Dict[str, Any]) -> str:
    """LIF-1: the worklist + the ONE per-item verdict gate (``semantic_reverify``/``snooze``),
    mirroring the ``memory.reconsolidate`` CLI (watermark lane included — the tool and the
    SessionStart producer must describe the SAME worklist). EVD-1 adds action='brief': the
    per-entry evidence card (``reconsolidate_brief``) that retires this tool's old
    hand-diff instruction — cold-path, read-only, verdict vocabulary untouched."""
    from . import trust
    from .provenance import resolve_dirs
    from .reconsolidate import (
        _SNOOZE_WINDOW_SESSIONS,
        _linked_note,
        recalled_stale_worklist,
        semantic_reverify,
        snooze,
        watermark_stale_candidates,
    )
    from .staleness_policy import DIAG_KEY, suppressed_count_note

    memory_dir, repo_root = resolve_dirs()
    # SEC-1: gate like traverse (the worklist renders memory names + typed-edge neighbors)
    # and like new_memory (a reverify verdict WRITES corpus frontmatter).
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "reconsolidate: withheld — this project's memory corpus is untrusted (SEC-1: "
            "the worklist exposes memory names and a verdict writes corpus files, gated "
            "just as recall and new_memory are). " + _UNTRUSTED_REMEDY
        )
    action = str(args.get("action") or "worklist").strip().lower()
    if action == "worklist":
        # VOL-1: both lanes report suppressed names into the one diagnostics dict; the
        # listing prints the count below — suppression is never silent on this surface.
        diagnostics: Dict[str, Any] = {}
        worklist = recalled_stale_worklist(
            memory_dir,
            repo_root,
            watermark_stale=watermark_stale_candidates(
                memory_dir, repo_root, diagnostics=diagnostics
            ),
            diagnostics=diagnostics,
        )
        suppressed = diagnostics.get(DIAG_KEY) or []
        if not worklist:
            empty = "No recently-recalled memory is currently stale."
            return empty + (f" {suppressed_count_note(len(suppressed))}" if suppressed else "")
        out = [
            f"{len(worklist)} memories need re-grounding (recently recalled + stale, or "
            "[since-watermark] commit-precise hits) — re-ground EACH against current code, "
            "then render ONE verdict per item via action='reverify' "
            "(outcome=graduate|fix|demote|snooze):"
        ]
        for item in worklist:
            wm_tag = " [since-watermark]" if item.get("watermark") else ""
            out.append(
                f"  • {item['name']}{wm_tag}{_linked_note(item)}: "
                + ", ".join(item["changed_paths"][:6])
            )
        if suppressed:
            out.append("  " + suppressed_count_note(len(suppressed)))
        out.append(
            "Evidence per item: action='brief' (name=…) renders the cited-path diff from "
            "the entry's own baseline — diffstat + hunk headers, secret-linted bodies when "
            "clean (EVD-1; no more hand-diffing)."
        )
        return "\n".join(out)
    if action == "brief":
        name = str(args.get("name") or "").strip()
        if not name:
            return "reconsolidate brief: 'name' is required (one entry per call)."
        from .reconsolidate_brief import brief_for_name, render_brief

        brief = brief_for_name(name, memory_dir, repo_root)
        if brief is None:
            return (
                f"nothing to brief: {name} is not in the current stale set "
                "(no cited-code drift recorded, or no citation provenance)"
            )
        return "\n".join(render_brief(brief))
    if action == "reverify":
        name = str(args.get("name") or "").strip()
        outcome = str(args.get("outcome") or "").strip().lower()
        if not name or not outcome:
            return (
                "reconsolidate reverify: 'name' and 'outcome' "
                "(graduate|fix|demote|snooze) are both required."
            )
        base = name if name.endswith(".md") else f"{name}.md"
        if outcome == "snooze":
            # The skill's fourth verdict — the CLI spells it --snooze; one enum here.
            r = snooze(name, memory_dir)
            if r["error"]:
                return f"snooze {base}: refused — {r['error']}"
            return (
                f"snooze {base}: ack logged — the worklist skips it until "
                f"{_SNOOZE_WINDOW_SESSIONS} new sessions have started (a deferral, not a "
                "verdict; it expires and re-nags)"
            )
        superseded_by = str(args.get("superseded_by") or "").strip() or None
        r = semantic_reverify(
            name, outcome, memory_dir, repo_root, superseded_by=superseded_by
        )
        if r["error"]:
            return f"reverify {base}: refused — {r['error']}"
        bits = [f"outcome={r['outcome']}"]
        bits.append("staleness flag cleared" if r["cleared"] else "staleness flag unchanged")
        if outcome == "demote":
            # LIF-1: name the chained action so the one-command demote is legible.
            boundary = (
                f" to {r['invalid_after']} (the successor's commit date)"
                if superseded_by and r.get("invalid_after")
                else ""
            )
            bits.append(
                f"invalid_after set{boundary} — recall's pre-cut penalty engages with no second command"
                if r["invalidated"]
                else "invalid_after unchanged"
            )
        if superseded_by:
            bits.append(
                f"supersedes edge written to {superseded_by}"
                if r["edge_written"]
                else "supersedes edge already present"
            )
        bits.append("logged" if r["logged"] else "not logged")
        out = [f"reverify {base}: " + "; ".join(bits)]
        # TMB-5: the succession replay's per-query lines — the same rendering the CLI prints.
        from .reconsolidate import succession_replay_lines

        out.extend(
            succession_replay_lines(
                os.path.splitext(base)[0], superseded_by or "", r.get("succession_replay")
            )
        )
        # LIF-3: the ONE shared rot rendering — a graduate/fix re-derivation that dropped
        # citations must be as loud here as on the provenance CLI.
        from .provenance import citation_rot_lines

        out.extend(citation_rot_lines(base, r))
        return "\n".join(out)
    return (
        "reconsolidate: pass action='worklist' (default), action='brief' (name=…), or "
        "action='reverify' (name=…, outcome=…)."
    )


def _tool_rederive(args: Dict[str, Any]) -> str:
    """INT-14 — MIG-1's consented re-derivation on the second surface.

    MIG-1 shipped three CLI verbs and no MCP entrypoint, so the loop dead-ended on Desktop:
    the DRV-2 SessionStart nudge fires (it is a hook — both surfaces), routes to doctor,
    doctor reports the stale derivation… and nothing here could act on it. This is the same
    gap INT-13 closed for consolidate, reopened by a release that only thought in CLI verbs.

    Mirrors the CLI exactly — ``action='worklist'`` (read-only, the attributed diff),
    ``action='one'`` (name=…, ONE memory, after its diff was reviewed), ``action='snapshot'``
    (stamp=…, the mandatory backup). There is deliberately NO bulk form on either surface:
    the per-item review is what makes the SEC-6 fold legitimate rather than the gate
    consenting to itself (see ``provenance.rederive_file``).
    """
    from . import trust
    from .provenance import (
        CITATION_DERIVATION_VERSION,
        build_repo_file_index,
        citation_rot_lines,
        read_cite_derivation,
        rederive_file,
        rederive_worklist,
        resolve_dirs,
        snapshot_corpus,
        write_cite_derivation,
    )

    memory_dir, repo_root = resolve_dirs()
    # SEC-1: gate like reconsolidate — the worklist renders memory names, and 'one' WRITES
    # corpus frontmatter.
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "rederive: withheld — this project's memory corpus is untrusted (SEC-1: the "
            "worklist exposes memory names and 'one' writes corpus files, gated just as "
            "recall and reconsolidate are). " + _UNTRUSTED_REMEDY
        )

    action = str(args.get("action") or "worklist").strip().lower()

    if action == "snapshot":
        stamp = str(args.get("stamp") or "").strip()
        if not stamp:
            return "rederive: action='snapshot' needs stamp=<label> (e.g. '20260715-cite3')."
        try:
            return (
                f"snapshot: {snapshot_corpus(memory_dir, stamp)}\n"
                "Self-ignoring (a `*` .gitignore lands before the payload), so the backup "
                "cannot publish a corpus its project keeps private."
            )
        except FileExistsError as exc:
            return f"rederive: refused — {exc}"
        except Exception as exc:
            return f"rederive: snapshot FAILED — {exc}. Do not migrate without one."

    if action == "stamp":
        # MIG-1 step 5, and the step that had no verb on ANY surface: `write_cite_derivation`
        # existed and only tests called it, so a migration could be performed but never
        # COMPLETED — the nudge fired forever.
        #
        # The stamp is EARNED, not claimed: it asserts "these citations were derived by vN",
        # which is exactly the thing the marker exists to let you verify. So refuse while any
        # memory still differs, and let an empty worklist be the proof.
        work = rederive_worklist(memory_dir, repo_root)
        if work:
            return (
                f"rederive: refused to stamp — {len(work)} memory(ies) still derive "
                "differently under this plugin's extractor. Stamping now would assert a "
                "derivation this corpus does not have, which is the one thing the marker "
                "exists to prevent. Run action='worklist', apply each with action='one', "
                "then stamp."
            )
        was = read_cite_derivation(memory_dir)
        if was >= CITATION_DERIVATION_VERSION:
            return f"rederive: already stamped cite_derivation={was} — nothing to do."
        if not write_cite_derivation(memory_dir):
            return "rederive: stamp FAILED to write .format — check the corpus dir is writable."
        return (
            f"stamped cite_derivation: {was} → {CITATION_DERIVATION_VERSION} "
            f"(earned: 0 memories derive differently). The citation-derivation nudge stops."
        )

    if action == "worklist":
        work = rederive_worklist(memory_dir, repo_root)
        if not work:
            declared = read_cite_derivation(memory_dir)
            if declared < CITATION_DERIVATION_VERSION:
                return (
                    "re-derivation worklist: empty — every memory's citations already match "
                    f"this plugin's extractor (v{CITATION_DERIVATION_VERSION}), but the "
                    f"corpus still declares v{declared}, so the nudge keeps firing. Nothing "
                    "to migrate; just record it: rederive action='stamp'."
                )
            return (
                "re-derivation worklist: empty — every memory's citations already match this "
                "plugin's extractor."
            )
        out = [f"re-derivation worklist: {len(work)} memory(ies) would change", ""]
        for w in work:
            if w["error"]:
                out.append(f"  ✘ {w['name']}: {w['error']}")
                continue
            out.append(f"  {w['name']}")
            if w["gained"]:
                out.append(f"      + gains  : {', '.join(w['gained'])}")
            if w["lost"]:
                out.append(f"      - loses  : {', '.join(w['lost'])}")
            if w.get("kept"):
                out.append(
                    f"      = keeps  : {', '.join(w['kept'])} (still in the repo, not "
                    "derivable from the body — preserved, CUR-1)"
                )
            if w["unresolved"]:
                out.append(f"      ? unresolved in body: {', '.join(w['unresolved'])}")
        out += [
            "",
            "Review EACH diff, then apply one at a time: rederive action='one' name=<name>.",
            "This rewrites frontmatter and has no undo on a gitignored corpus — take "
            "rederive action='snapshot' stamp=<label> first.",
        ]
        return "\n".join(out)

    if action == "one":
        name = str(args.get("name") or "").strip()
        if not name:
            return "rederive: action='one' needs name=<memory slug> (with or without .md)."
        fname = name if name.endswith(".md") else f"{name}.md"
        target = os.path.join(memory_dir, fname)
        if not os.path.isfile(target):
            return f"rederive: memory not found: {fname}"
        repo_files, basename_index = build_repo_file_index(repo_root)
        dry = bool(args.get("dry_run"))
        r = rederive_file(target, repo_root, repo_files, basename_index, dry_run=dry)
        if r["error"]:
            return f"rederive {fname}: refused — {r['error']}"
        verb = "would re-derive" if dry else "re-derived"
        lines = [f"{verb} {fname}: cited_paths = {r['cited']}"]
        lines += citation_rot_lines(fname, r, dry_run=dry)
        if not dry and r["changed"]:
            lines.append(
                "source_commit PRESERVED (this is not a re-verify — no staleness flag was "
                "cleared); the reviewed bytes were folded into the consent baseline, so the "
                "memory is not quarantined."
            )
        return "\n".join(lines)

    return (
        "rederive: pass action='worklist' (default), 'one' (name=…), 'snapshot' (stamp=…), "
        "or 'stamp'."
    )


def _tool_heal_baselines(args: Dict[str, Any]) -> str:
    """INT-15 — the COR-10 heal on the second surface.

    A v1.15.0 REGRESSION, and the reason this tool exists rather than a doc line: before
    COR-10, ``heal_empty_baselines`` ran inside the SessionStart hook, which fires on BOTH
    surfaces, so every user got it for free. COR-10 correctly moved it off the hook (a hook
    must not write to the corpus — it drifts each file off its own SEC-6 fingerprint and then
    the drift banner blames the user for hippo's own write) — but it moved it to a CLI verb,
    which only the terminal can reach. Terminal users kept the capability; Desktop users lost
    it outright.

    Deliberately a human-invoked TOOL, never automatic: that is the whole point of COR-10.
    Restoring parity must not restore the hook write.
    """
    from . import trust
    from .provenance import heal_empty_baselines, resolve_dirs

    memory_dir, repo_root = resolve_dirs()
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "heal_baselines: withheld — this project's memory corpus is untrusted (SEC-1: "
            "this writes corpus files). " + _UNTRUSTED_REMEDY
        )
    healed, failed = heal_empty_baselines(memory_dir, repo_root)
    if not healed and not failed:
        return "heal_baselines: nothing to heal — no memory carries an empty staleness baseline."
    lines = []
    if healed:
        lines.append(
            f"healed {len(healed)} empty baseline(s) to HEAD: {', '.join(healed)}\n"
            "Each was invisible to staleness, reconsolidation and archive gating; they are "
            "now tracked. This can never CLEAR a flag — an empty baseline never raised one."
        )
    if failed:
        # RCH-9: a failure is part of the result, not a silent skip.
        lines.append(
            f"✘ {len(failed)} baseline(s) could NOT be healed (still invisible to "
            "staleness — fix and re-run):"
        )
        lines += [f"  - {n}: {reason}" for n, reason in sorted(failed.items())]
    return "\n".join(lines)


def _tool_build_index(args: Dict[str, Any]) -> str:
    """Step 3: refresh the index + persisted links.json. Runs the full ``memory.build_index``
    under the freshly-resolved venv python when one exists (dense vectors — the same
    ``_fresh_python`` discipline as doctor/init, so a server that booted pre-bootstrap never
    dense-blinds the rebuild); else falls back to the in-process never-downgrade
    ``refresh_index``. Ungated: it writes only the gitignored index dir (init already builds
    pre-consent), and its output is counts, never content."""
    from .build_index import default_index_dir, refresh_index
    from .provenance import resolve_dirs

    memory_dir, _repo_root = resolve_dirs()
    py = _fresh_python()
    if py is not None:
        try:
            import subprocess

            out = subprocess.run(
                [py, "-m", "memory.build_index"],
                capture_output=True, text=True, timeout=600, env=_subprocess_env(),
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    manifest = refresh_index(memory_dir)
    if manifest is None:
        return (
            "build_index: no index was produced — is there a corpus here? Run the init "
            "tool first."
        )
    dense = (
        "hybrid" if manifest.get("dense_ready") else "BM25-only (run the bootstrap tool for dense)"
    )
    return (
        f"index refreshed — {manifest.get('count')} memories, {dense}\n"
        f"index dir: {default_index_dir(memory_dir)}\n"
        "links.json re-persisted — new [[wikilinks]] and typed edges are live for the next recall."
    )


def _tool_co_recall_proposals(args: Dict[str, Any]) -> str:
    """GRW-2 (Step 4): the SKILL.md tally verbatim — ``co_recall_pairs`` (floor excluded)
    fused with ``links.build_graph`` adjacency so already-linked pairs drop. Read-only; the
    approved append stays a per-item agent edit of ONE body, never a write here."""
    from . import trust
    from .lint_floor import floor_memory_names
    from .links import build_graph
    from .provenance import resolve_dirs
    from .telemetry import co_recall_pairs, default_telemetry_dir

    memory_dir, repo_root = resolve_dirs()
    # SEC-1: proposals render memory names — gate exactly as traverse does.
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "co_recall_proposals: withheld — this project's memory corpus is untrusted "
            "(SEC-1: proposals expose memory names, gated just as recall is). "
            + _UNTRUSTED_REMEDY
        )
    pairs = co_recall_pairs(
        default_telemetry_dir(memory_dir),
        exclude_names=floor_memory_names(memory_dir),  # floor names would dominate every pair
    )
    adjacent = set()
    graph = build_graph(memory_dir)
    if graph:
        for src, outs in graph.adjacency.items():
            adjacent.update(frozenset((src, tgt)) for tgt in outs)
        for src, rels in graph.typed.items():
            for tgts in rels.values():
                adjacent.update(frozenset((src, tgt)) for tgt in tgts)
    fresh = [p for p in pairs if frozenset(p["pair"]) not in adjacent]
    if not fresh:
        return (
            "no co-recall pairs above threshold — the sparse map stays empty (by design; "
            "already-linked pairs are dropped and floor names are excluded)"
        )
    # MEA-3: the null model at the proposal surface — propose only pairs whose lift beats
    # the floor; collapse the chance-level rest into ONE countable line (inv3: suppressed,
    # never invisible). Deparasite's two reads keep RAW counts (permissive-protection
    # default — recorded on the item); cap and ordering untouched this round.
    from .telemetry import _CORECALL_LIFT_FLOOR

    strong = [p for p in fresh if p.get("lift") is None or p["lift"] >= _CORECALL_LIFT_FLOOR]
    weak = [p for p in fresh if p.get("lift") is not None and p["lift"] < _CORECALL_LIFT_FLOOR]
    suppressed_line = (
        f"  {len(weak)} chance-level pair(s) suppressed — lift < {_CORECALL_LIFT_FLOOR} "
        "(observed ≈ expected from the members' own session frequencies: a frequency "
        "confound, not an association; countable here, never invisible)"
    )
    if not strong:
        return (
            "no co-recall pairs above the lift floor — nothing proposed:\n"
            + suppressed_line
            + "\nRaw counts still feed deparasite's protection read unchanged (the "
            "permissive default); no edge proposal is manufactured from a frequency confound."
        )
    out = [
        f"{len(strong)} co-recall edge proposal(s) — pairs that surfaced together across "
        "distinct sessions ABOVE chance (already-linked pairs dropped, floor names excluded):"
    ]
    for p in strong:
        a, b = p["pair"]
        ms = p.get("member_sessions") or {}
        uni = p.get("session_universe")
        detail = f"lift {p['lift']} vs independence" if p.get("lift") is not None else "lift n/a"
        if uni:
            detail += f"; {a} in {ms.get(a)}/{uni}, {b} in {ms.get(b)}/{uni} sessions"
        out.append(f"  {a} <-> {b}   (co-recalled in {p['sessions']} distinct sessions; {detail})")
    if weak:
        out.append(suppressed_line)
    out.append(
        "For EACH pair: read both memories and judge whether the association is real — "
        "would someone recalling one genuinely need the other? On explicit approval, append "
        "a [[the-other-name]] reference into ONE side's body (its Related: line if present) "
        "— a per-item agent edit; this tool never writes — then run the build_index tool so "
        "links.json carries the edge. If no, skip it; the tally keeps its count."
    )
    return "\n".join(out)


def _tool_abstention_fixtures(args: Dict[str, Any]) -> str:
    """SIG-6 (Step 5): ``draft_abstention_fixtures`` + the per-item ``confirm_hard_set_row``
    gate. SEC-1-gated as ONE loop: draft renders corpus stems (current_hits) and confirm
    writes into ``.claude/memory/.audit-fixtures/`` — corpus reads and writes both."""
    from . import trust
    from .provenance import resolve_dirs

    memory_dir, repo_root = resolve_dirs()
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "abstention_fixtures: withheld — this project's memory corpus is untrusted "
            "(SEC-1: fixture rows name corpus memories and the confirm step writes into "
            ".claude/memory/, gated just as recall and new_memory are). " + _UNTRUSTED_REMEDY
        )
    action = str(args.get("action") or "draft").strip().lower()
    if action == "draft":
        from .eval_recall import draft_abstention_fixtures, draft_livedin_fixtures

        r = draft_abstention_fixtures()
        # MEA-2: the fourth lane refreshes in the same cold-path step — outcome-confirmed
        # verbatim queries into the SAME pending queue; same per-item confirm gate.
        lv = draft_livedin_fixtures()
        return (
            "abstention drafts refreshed — unconfirmed rows (expected: []) are gitignored "
            "queue state; nothing is tracked until a per-item confirm:\n"
            + json.dumps(r, indent=2)
            + "\nlived-in drafts refreshed (MEA-2, the fourth lane — outcome-confirmed "
            "verbatim queries; judge derived_expected, confirm with category='single-hop'):\n"
            + json.dumps(lv, indent=2)
        )
    if action == "confirm":
        from .eval_recall import confirm_hard_set_row

        query = str(args.get("query") or "").strip()
        expected = args.get("expected")
        expected = [str(x) for x in expected] if isinstance(expected, list) else []
        absent = args.get("absent")
        absent = [str(x) for x in absent] if isinstance(absent, list) else None
        if not query or (not expected and not absent):
            return (
                "abstention_fixtures confirm: 'query' and a non-empty 'expected' stem list "
                "are both required — and only after judging that those memories genuinely "
                "answer the query (never fabricate a memory to make a fixture pass). "
                "TMB-3 forgetting rows pass absent=[archived stems] instead of expected."
            )
        kwargs: Dict[str, Any] = {}
        cat = str(args.get("category") or "").strip()
        if cat:
            kwargs["category"] = cat
        if absent:
            kwargs["absent"] = absent
        sup = str(args.get("superseded") or "").strip()
        if sup:
            kwargs["superseded"] = sup
        return json.dumps(confirm_hard_set_row(query, expected, **kwargs), indent=2)
    return "abstention_fixtures: pass action='draft' (default) or action='confirm' (query=…, expected=[…])."
