"""Per-item publish verb (PUB-1) — entry INTO this repo's committed subset. PRINT-ONLY.

The naming triangle (the docs' load-bearing distinction):
  - promote = per-item lift ACROSS projects to the user tier (shipped);
  - pack    = per-item share OUT of the repo (shipped — ``pack_extract`` strips
    provenance, stamps pack metadata, emits a manifest, refuses an in-corpus dest);
  - publish = per-item entry INTO this repo's own committed subset (THIS verb).
A fourth shipped movement to distinguish: init's fresh-mode whole-dir nudge
(``git add .claude/memory && git commit``) is the share-everything posture — the
opposite of this verb's per-item ``git add -f`` under a dir ``.gitignore`` keeps
ignored (a new memory is invisible to plain git until ``add -f``).

Publish's defining property: BYTE-IDENTICAL in-place file + git tracking only. If this
verb ever grows a content transform it has become ``pack_extract`` and must be killed
(not_pursuing: publish-content-transform).

Vocabulary, deliberately three-way (the vetting's verdicts, do not re-gate):
  - REFUSAL (mechanical only): docs / non-memory filenames
    (``provenance._is_memory_filename``) and already-tracked files — an UPDATE to a
    committed memory rides plain git (PR #72 edited 13+ committed capstones in place).
  - GATE (the #67 entropy bar): ``review.lint_touched`` REUSED on the in-memory text —
    local/CI parity by construction; the ONLY delta is ``entropy=True``, a strict
    superset of the CI gate's ``entropy=False`` (ONE run, never "both modes").
  - ADVISORY (receipt warnings, never refusals): invalid_after-expired and
    unresolved-``contradicts`` — the committed subset IS dev history, including
    expired/contradicted records (#67's bar was secrets + eyeball only; CLB-1 keeps
    conflicts/edges advisory precisely to not automate a human posture call).

Act = PRINT-ONLY pending owner decision Q3 (ED4R-1): print the exact ``git add -f`` +
suggested commit line and STOP (the SLP-2 posture; ``mcp_tools_setup`` already prints
git-add advice). No production path writes a user repo's git index — the consent
moments are the printed command, the PR review, and the CLB-1 CI backstop. Anti-bulk:
ONE name per invocation; no 'all' affordance (``pack_extract``'s ``names='all'``
deliberately does not carry over — #67's ratified posture is per-item review-in).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


def _memory_rel(memory_dir: str, top: str, fname: str) -> str:
    mem_rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(top))
    return fname if mem_rel == "." else f"{mem_rel}/{fname}"


def _derivation_state(memory_dir: str) -> Optional[dict]:
    """The corpus ``.format`` derivation vs this plugin's — receipt disclosure only.

    A corpus whose ``cite_derivation`` trails the plugin's extractor publishes files
    whose citations will receive review-gated update PRs later; the receipt DISCLOSES
    that rather than blocking on it (the rederive lane is mid-flight by design).
    """
    try:
        from .provenance import CITATION_DERIVATION_VERSION

        with open(os.path.join(memory_dir, ".format"), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        corpus = doc.get("cite_derivation")
        if isinstance(corpus, int) and corpus < CITATION_DERIVATION_VERSION:
            return {"corpus": corpus, "plugin": CITATION_DERIVATION_VERSION}
        return None
    except Exception:
        return None


def publish_preflight(name: str, memory_dir: str, repo_root: str) -> dict:
    """The whole per-item preflight: mechanical refusals, the reused gate, advisory
    receipt warnings, cross-referenced readiness, and the printed command. Read-only —
    the file is never edited, the git index is never touched. Never raises.

    Returns ``{name, ok, refusal, gate, advisory, receipt, commands}`` where ``ok``
    means "nothing refused, gate clean — the printed commands are ready to run".
    """
    fname = name if name.endswith(".md") else f"{name}.md"
    stem = fname[:-3]
    result: Dict[str, object] = {
        "name": stem,
        "ok": False,
        "refusal": None,
        "gate": [],
        "advisory": [],
        "receipt": {},
        "commands": [],
    }
    try:
        from .provenance import _is_memory_filename, build_repo_file_index, run_git

        # MECHANICAL refusal 1: docs and non-memory filenames are never published.
        if not _is_memory_filename(fname):
            result["refusal"] = (
                f"{fname} is not a memory file — docs (MEMORY.md, CONVENTIONS.md, "
                "MEMORY.full.md) and non-.md files are never published"
            )
            return result
        path = os.path.join(memory_dir, fname)
        if not os.path.isfile(path):
            result["refusal"] = f"no memory named {stem!r} in {memory_dir}"
            return result

        top = run_git(["rev-parse", "--show-toplevel"], repo_root).strip() or repo_root
        repo_files, _basenames = build_repo_file_index(repo_root)
        rel = _memory_rel(memory_dir, top, fname)

        # MECHANICAL refusal 2: already tracked = an UPDATE riding plain git.
        if rel in repo_files:
            result["refusal"] = (
                f"{rel} is already tracked — updates to a committed memory ride plain "
                "git (edit + commit); publish is only the FIRST entry into the subset"
            )
            return result

        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()

        # THE GATE — review.lint_touched reused on the in-memory text; entropy=ON is
        # the only delta (a strict superset of the CI gate's entropy=False; one run).
        from .review import lint_touched

        findings = lint_touched({stem: text}, memory_dir, repo_root, entropy=True)
        result["gate"] = findings["gate"]
        advisory: List[dict] = list(findings["advisory"])

        # ADVISORY: invalid_after expired — dev history publishes fine; flagged only.
        from .recall_graph import _invalidation_state
        from .staleness import read_invalid_after

        raw = read_invalid_after(text)
        if raw and _invalidation_state({"invalid_after": raw}):
            advisory.append(
                {"stem": stem, "lint": "invalid_after",
                 "finding": f"invalid_after {raw} has passed — flagged, never refused (#67)"}
            )

        # ADVISORY: unresolved contradicts pairs involving this memory.
        try:
            from .resolve_view import unresolved_contradictions

            for pair in unresolved_contradictions(memory_dir, repo_root=repo_root):
                names = pair.get("pair") or []
                if stem in names:
                    other = [n for n in names if n != stem] or ["?"]
                    advisory.append(
                        {"stem": stem, "lint": "contradicts",
                         "finding": f"unresolved contradicts pair with {other[0]} — "
                         "flagged, never refused (CLB-1 keeps edges advisory)"}
                    )
        except Exception:
            pass
        result["advisory"] = advisory

        # RECEIPT — cross-references to the shipped PUB-3/PUB-2 surfaces, display-only
        # (the export_receipts composition pattern; nothing here is recomputed math).
        receipt: Dict[str, object] = {}
        try:
            from .lint_links import boundary_lint

            view = boundary_lint(memory_dir, repo_root)
            receipt["heals"] = (view.get("heals_by") or {}).get(stem, 0)
            # BND-1: the introduces twin — same one boundary_lint call, display-only.
            receipt["introduces"] = (view.get("introduces_by") or {}).get(stem, 0)
        except Exception:
            receipt["heals"] = 0
            receipt["introduces"] = 0
        try:
            from .soak import compute_strength_scores
            from .telemetry import default_telemetry_dir

            receipt["strength"] = compute_strength_scores(
                default_telemetry_dir(memory_dir)
            ).get(stem)
        except Exception:
            receipt["strength"] = None
        try:
            from .team_coverage import read_verified_by

            vb = read_verified_by(text)
            receipt["verified_by"] = vb[0] if vb else None
        except Exception:
            receipt["verified_by"] = None
        try:
            from .staleness import find_stale

            receipt["stale_changed"] = next(
                (
                    len(item.get("changed_paths") or [])
                    for item in find_stale(memory_dir, repo_root)
                    if item.get("name") == stem
                ),
                0,
            )
        except Exception:
            receipt["stale_changed"] = 0
        receipt["derivation"] = _derivation_state(memory_dir)
        result["receipt"] = receipt

        # THE ACT, print-only pending Q3: the human executes the printed command.
        result["commands"] = [
            f'git add -f "{rel}"',
            f'git commit -m "memory: publish {stem}"',
        ]
        result["ok"] = not result["gate"]
        return result
    except Exception as exc:
        result["refusal"] = f"preflight failed: {exc}"
        return result


def render_preflight(result: dict) -> str:
    """The terminal form — refusal, gate, advisories, receipt, then the printed act."""
    lines = [f"publish preflight — {result['name']}"]
    if result["refusal"]:
        lines.append(f"  REFUSED (mechanical): {result['refusal']}")
        return "\n".join(lines)
    for f in result["gate"]:
        lines.append(f"  ✘ gate [{f['lint']}]: {f['finding']}")
    for f in result["advisory"]:
        lines.append(f"  ⚠ advisory [{f['lint']}]: {f['finding']}")
    r = result.get("receipt") or {}
    bits = []
    if r.get("heals") or r.get("introduces"):
        # BND-1: state the net boundary effect when the candidate introduces its own
        # danglings; a heals-only candidate renders byte-identically to pre-BND-1.
        n, m = r.get("heals") or 0, r.get("introduces") or 0
        if m:
            bits.append(
                f"heals {n} / introduces {m} (net {m - n:+d}) boundary link(s) "
                "(see: python -m memory.lint_links --boundary)"
            )
        else:
            bits.append(f"heals {n} boundary link(s) (see: python -m memory.lint_links --boundary)")
    if r.get("strength") is not None:
        bits.append(f"soak {r['strength']:.2f}")
    if r.get("verified_by"):
        bits.append(f"verified_by {r['verified_by']}")
    if r.get("stale_changed"):
        bits.append(f"stale: {r['stale_changed']} cited file(s) drifted since baseline")
    if r.get("derivation"):
        d = r["derivation"]
        bits.append(
            f"citations derived at v{d['corpus']} (plugin is v{d['plugin']}) — published "
            "citations will receive review-gated update PRs; disclosed, not blocking"
        )
    for b in bits:
        lines.append(f"  · receipt: {b}")
    if result["ok"]:
        lines.append(
            "  ready — PRINT-ONLY pending Q3: the human executes the printed commands "
            "(the consent moments are this command, the PR review, and the CLB-1 CI gate):"
        )
        for cmd in result["commands"]:
            lines.append(f"    {cmd}")
    else:
        lines.append(
            "  NOT ready — the gate findings above are the #67 entropy bar; nothing printed."
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``python -m memory.publish <name> [--json]``. Exit 0 = ready (commands
    printed), 1 = gate findings, 2 = mechanical refusal / usage error. ONE name per
    invocation — there is deliberately no 'all' form."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m memory.publish",
        description=(
            "Per-item publish preflight (PUB-1): mechanical refusals, the reused "
            "review gate with entropy ON, advisory receipt warnings, and the exact "
            "git add -f + commit line — printed, never executed (Q3 pending)."
        ),
    )
    parser.add_argument("name", help="exactly one memory name (stem or filename)")
    parser.add_argument("--json", action="store_true", help="emit the preflight as JSON")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    memory_dir, repo_root = args.memory_dir, args.repo_root
    if memory_dir is None or repo_root is None:
        from .provenance import resolve_dirs

        md, rr = resolve_dirs()
        memory_dir = memory_dir or md
        repo_root = repo_root or rr

    result = publish_preflight(args.name, memory_dir, repo_root)
    print(json.dumps(result, ensure_ascii=False) if args.json else render_preflight(result))
    if result["refusal"]:
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
