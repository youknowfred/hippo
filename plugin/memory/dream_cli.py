"""The ``python -m memory.dream`` CLI — split out of ``dream.py`` by RWY-1 as pure code
motion so the façade keeps runway (it sat at the 900-line cap). ``dream.main`` stays
importable at its old path. ``main()`` imports the ``dream`` names it uses at call time,
so a test that monkeypatches ``memory.dream.<name>`` still steers it.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .dream import (
        apply_mode_default,
        default_telemetry_dir,
        discover,
        render_log,
        retire_ghost_edge,
        run_apply_pass,
        run_report_pass,
        undo_edges,
        write_boost_ledger,
        write_candidate_ledger,
    )
    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(
        prog="memory.dream",
        description=(
            "/dream — the generative sleep pass: replay the corpus against itself and "
            "surface latent graph edges (DRM-1: report-only, zero memory writes)."
        ),
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="explicit report-only pass (the shipped default — auto-apply is OFF pending "
        "the dated owner flip; see ROADMAP.dream.yaml owner_decisions)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="run the DRM-2 Tier-A auto-apply loop this pass (capped, θ/mutuality-gated, "
        "stamped, undoable; never commits). Also enabled by HIPPO_DREAM_APPLY=1.",
    )
    parser.add_argument(
        "--undo",
        nargs="?",
        const="",
        default=None,
        metavar="EDGE_ID",
        help="revert applied dream edges: bare --undo reverts the latest pass; "
        "--undo <edge-id> exactly one. Byte-exact; refuses on manual drift.",
    )
    parser.add_argument(
        "--undo-since",
        default=None,
        metavar="DATE|N",
        help="revert edges applied since an ISO date, or within the last N distinct sessions",
    )
    parser.add_argument(
        "--log", action="store_true", help="list every dream edge (active / aged-in / undone)"
    )
    parser.add_argument(
        "--retire-ghost", default=None, metavar="EDGE_ID",
        help="DRM-7: retire ONE active ledger edge whose stamp is provably gone (source "
        "deleted outside archive/, or the stamped line lost before a commit) — appends the "
        "superseding undone line; refuses while the stamp is on disk anywhere. Per edge.",
    )
    parser.add_argument("--reason", default=None, help="with --retire-ghost: why (recorded)")
    parser.add_argument(
        "--deparasite",
        action="store_true",
        help="DRM-4: the de-parasiting counterweight — report per-memory out-degree, flag "
        "hubs over DREAM_MAX_OUT_DEGREE, and PROPOSE retractions (dream's own un-aged "
        "edges) vs gated demotions/dedup-merges. Report/propose only; zero memory writes.",
    )
    parser.add_argument(
        "--retract",
        action="store_true",
        help="with --deparasite: additionally EXECUTE the Tier-A lane — retract the "
        "flagged, un-aged dream edges via the byte-exact undo machinery. Human "
        "structures and aged-in edges stay gated regardless.",
    )
    parser.add_argument(
        "--dedup-merge",
        nargs=2,
        metavar=("SURVIVOR", "LOSER"),
        default=None,
        help="execute ONE ratified dedup-merge proposal (per-item, no batch): SURVIVOR "
        "gains supersedes:[LOSER], LOSER's validity window closes (set_invalid_after). "
        "Non-lossy — additive frontmatter only, no body byte touched, nothing deleted.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="DRM-6: the generative tier — cluster co-firing sets into schema/gist + "
        "hypothesis PROPOSALS (report-only by default; proposals land under the derived "
        "dream dir). With --stage (or HIPPO_DREAM_GENERATIVE=1 on apply passes), stages "
        "them into the corpus at confidence:draft — quarantined, capped, ledgered, "
        "undoable, self-decaying.",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="with --generate: actually stage the proposals as confidence:draft memories "
        "(explicit opt-in, like --apply; the corpus must be trusted). Never verified — "
        "graduation needs recorded outcome evidence (DREAM-KILL-1).",
    )
    parser.add_argument(
        "--sweep-drafts",
        action="store_true",
        help="DRM-6 decay: graduate evidence-confirmed drafts (draft→verified on a "
        "recorded outcome, never a glance), expire drafts past DREAM_DRAFT_HORIZON "
        "(auto-close validity + propose archive). Also runs inside every apply pass.",
    )
    parser.add_argument(
        "--archive-draft",
        default=None,
        metavar="NAME",
        help="execute ONE proposed draft archive (per-item; only dream-generated "
        "memories — human memories use the audit archive flow)",
    )
    parser.add_argument(
        "--prospective",
        action="store_true",
        help="DRM-6 metric: abstain→hit flips over the FROZEN abstention backlog, with "
        "dream-attribution (measure-only, off the hot path)",
    )
    parser.add_argument(
        "--contradictions",
        action="store_true",
        help="DRM-C: run the LLM contradiction check over this pass's high-cofire pairs "
        "(propose-only → the /hippo:resolve inbox; also enabled by "
        "HIPPO_DREAM_CONTRADICTIONS=1; needs an API key — silently skipped without one)",
    )
    parser.add_argument("--probe-k", type=int, default=None, help="co-fire probe depth (default 10)")
    parser.add_argument(
        "--max-seeds", type=int, default=None, help="cap the replay worklist (default 0 = all)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the raw discovery result as JSON instead of the report"
    )
    args = parser.parse_args(argv)

    memory_dir = args.memory_dir
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()

    # --contradictions is the CLI spelling of the env flag (the --generate/HIPPO_DREAM_
    # GENERATIVE convention): every downstream pass reads contradictions_enabled(), so the
    # in-process env set is the one wiring point.
    if args.contradictions:
        os.environ["HIPPO_DREAM_CONTRADICTIONS"] = "1"

    if args.log:
        print(render_log(memory_dir))
        return 0
    if args.deparasite:
        from .deparasite import run_deparasite_pass

        code, text = run_deparasite_pass(
            memory_dir, args.index_dir, args.telemetry_dir, retract=args.retract
        )
        print(text)
        return code
    if args.retract:
        print("🧹 --retract is a --deparasite modifier — run `dream --deparasite --retract`.")
        return 1
    if args.dedup_merge:
        from .deparasite import apply_dedup_merge

        survivor, loser = args.dedup_merge
        res = apply_dedup_merge(
            memory_dir,
            survivor,
            loser,
            telemetry_dir=args.telemetry_dir,
            index_dir=args.index_dir,
        )
        if res.get("error"):
            print(f"🧹 dedup-merge REFUSED: {res['error']}")
            return 1
        print(
            f"🧹 dedup-merge applied (non-lossy, reversible): {survivor} now supersedes "
            f"{loser}; {loser} invalid_after {res['invalid_after']['ts']}. Both files "
            "remain on disk; commit stays yours."
        )
        return 0
    if args.retire_ghost:
        code, text = retire_ghost_edge(memory_dir, args.retire_ghost, reason=args.reason)
        print(text)
        return code
    if args.undo is not None or args.undo_since:
        code, text = undo_edges(
            memory_dir,
            args.index_dir,
            edge_id=(args.undo or None),
            since=args.undo_since,
        )
        print(text)
        return code
    if args.generate:
        from .dream_generate import run_generative_pass

        code, text = run_generative_pass(
            memory_dir,
            args.index_dir,
            args.telemetry_dir,
            stage=args.stage,
            probe_k=args.probe_k,
            max_seeds=args.max_seeds,
        )
        print(text)
        return code
    if args.stage:
        print("🌱 --stage is a --generate modifier — run `dream --generate --stage`.")
        return 1
    if args.sweep_drafts:
        from .dream_generate import sweep_drafts

        code, text = sweep_drafts(memory_dir, args.telemetry_dir, args.index_dir)
        print(text)
        return code
    if args.archive_draft:
        from .dream_generate import archive_draft

        res = archive_draft(
            memory_dir,
            args.archive_draft,
            telemetry_dir=args.telemetry_dir,
            index_dir=args.index_dir,
        )
        if res.get("error"):
            print(f"🌱 archive-draft REFUSED: {res['error']}")
            return 1
        print(
            f"🌱 archived dream draft {args.archive_draft} (git-reversible move into "
            "archive/; ledger updated; commit stays yours)."
        )
        return 0
    if args.prospective:
        from .dream_generate import prospective_recall, render_prospective

        print(render_prospective(prospective_recall(memory_dir, args.telemetry_dir, args.index_dir)))
        return 0
    # --json is a READ surface (raw discovery dump) — it never applies unless --apply is
    # explicit, regardless of the shipped default.
    if args.apply or (apply_mode_default() and not args.dry_run and not args.json):
        code, text = run_apply_pass(
            memory_dir,
            args.index_dir,
            args.telemetry_dir,
            probe_k=args.probe_k,
            max_seeds=args.max_seeds,
        )
        print(text)
        return code

    if args.json:
        td = args.telemetry_dir or default_telemetry_dir(memory_dir)
        result = discover(
            memory_dir, args.index_dir, td, probe_k=args.probe_k, max_seeds=args.max_seeds
        )
        if result["status"] == "ok":
            write_candidate_ledger(td, result["pass_id"], result["candidates"])
            write_boost_ledger(
                td, result["pass_id"], (result.get("reward") or {}).get("edges") or []
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=list))
        return 1 if result["status"] == "no-index" else 0

    code, text = run_report_pass(
        memory_dir,
        args.index_dir,
        args.telemetry_dir,
        probe_k=args.probe_k,
        max_seeds=args.max_seeds,
    )
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
