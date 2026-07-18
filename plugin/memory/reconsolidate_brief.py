"""EVD-1's reverify brief — per-entry evidence for the reconsolidation verdict flow.

The reconsolidation queue is chronic and the verdict flow runs at volume, yet every
human verdict used to require hand-gathering its evidence: the worklist renders path
NAMES only, and the reconsolidate tool description literally instructed "read the
memory, diff its cited paths". This module is that write-only ledger's READER (ED4R-2):
per worklist entry, a deterministic git-mined summary from the entry's OWN carried
baseline (``source_commit`` — on every entry since LIF-6) to HEAD — diffstat + bounded
hunk HEADERS/function context — composed with what already rides the entry
(``changed_paths``, ``recency``, ``linked`` neighbors, the CLB-3 evidence-drift fence
counts, ``invalid_after`` state). The honest value claim, verbatim from the roadmap:
evidence per verdict for a measured high-volume flow (and a rubber-stamp exposer — a
one-word graduate now stands next to what actually changed); deliberately NO
time-savings claim, because nothing measures one.

Raw hunk BODIES render only under the capture lane's secret discipline — the
``hunks_secret_flagged`` scan (``secrets.scan_text`` plus the SEN-2 Tier-A threat scan,
the same pair ``capture.build_seed`` applies to quoted hunks) and the
``capture_triage._MAX_PROMPT_HUNK_CHARS`` cap (imported, never re-declared — inv5);
otherwise the brief stays diffstat/header-only. Hunk headers quote function-context
code fragments, so they pass the same scan. A display surface must not become the
secret-exfil path the write lanes already lint against.

COLD PATH ONLY (inv6 — the ``resolve_evidence`` precedent): surfaced via the
reconsolidate MCP tool (action='brief'), this module's own CLI
(``python -m memory.reconsolidate_brief <name>``), and /hippo:consolidate Step 2 —
never a SessionStart producer, never the UserPromptSubmit hot path
(source/AST-pinned in tests/test_reconsolidate_brief.py). Zero persisted state
(inv1 — git history is the record), zero corpus writes (AST-pinned: no write
primitive is ever called here); the verdict vocabulary is untouched — the brief
renders evidence for all four human paths (graduate / fix / demote / snooze) and
applies nothing (LIF-1: the verdict stays the agent's, and the standing
auto-drain kill binds).

Read-only; every leg degrades to an honest empty/unknown; never raises.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .capture_triage import _MAX_PROMPT_HUNK_CHARS
from .provenance import run_git
from .secrets import scan_text
from .staleness import _SHORT_SHA_LEN, read_evidence_drift, read_invalid_after
from .threat_lint import scan_threats

# Render bounds — a brief is a bounded evidence card, never a full diff dump. Diffstat and
# header caps are line-based (each renders as one line); bodies inherit the capture lane's
# character cap verbatim (_MAX_PROMPT_HUNK_CHARS above).
_MAX_DIFFSTAT_LINES = 20
_MAX_HUNK_HEADER_LINES = 30
_MAX_RENDERED_PATHS = 6  # same width as the worklist's own changed_paths render


def _short(sha: object) -> str:
    return str(sha or "")[:_SHORT_SHA_LEN]


def _baseline_resolvable(sha: str, repo_root: str) -> bool:
    """True when ``sha`` names a commit in THIS repo's history (SHP-3's squash-merge /
    shallow-clone class is the honest-degradation branch: no diff can be rendered)."""
    return bool(run_git(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], repo_root).strip())


def _diffstat(sha: str, paths: List[str], repo_root: str) -> List[str]:
    """Bounded ``git diff --stat`` lines for the baseline→HEAD range, cited paths only."""
    out = run_git(["diff", "--stat", f"{sha}..HEAD", "--", *paths], repo_root)
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    if len(lines) > _MAX_DIFFSTAT_LINES:
        lines = lines[:_MAX_DIFFSTAT_LINES] + [f"… (+{len(lines) - _MAX_DIFFSTAT_LINES} more stat lines)"]
    return lines


def _hunk_headers(sha: str, paths: List[str], repo_root: str) -> Tuple[List[str], Optional[str]]:
    """``(header lines, withheld_reason)`` — per-file markers + ``@@`` hunk headers.

    ``--unified=0`` keeps only change clusters; the ``@@ … @@ <function>`` tail is git's
    own function-context line. Headers quote code fragments, so they pass the SAME secret
    discipline as bodies — a Tier-A/secret hit withholds them (rare; diffstat survives,
    it renders path names and counts only).
    """
    out = run_git(["diff", "--unified=0", f"{sha}..HEAD", "--", *paths], repo_root)
    headers: List[str] = []
    for ln in out.splitlines():
        if ln.startswith("diff --git "):
            marker = ln.split(" b/", 1)
            headers.append(marker[1] if len(marker) == 2 else ln)
        elif ln.startswith("@@"):
            headers.append("  " + ln)
    blob = "\n".join(headers)
    if blob and (scan_text(blob) or scan_threats(blob)["tier_a"]):
        return [], "withheld (secret/threat-scan hit in a function-context fragment)"
    if len(headers) > _MAX_HUNK_HEADER_LINES:
        headers = headers[:_MAX_HUNK_HEADER_LINES] + [
            f"… (+{len(headers) - _MAX_HUNK_HEADER_LINES} more hunk headers)"
        ]
    return headers, None


def _bodies(sha: str, paths: List[str], repo_root: str) -> Tuple[Optional[str], Optional[str], bool]:
    """``(text, withheld_reason, truncated)`` — raw hunk bodies under the capture lane's
    secret discipline: the ``hunks_secret_flagged`` scan pair decides render-or-withhold,
    and ``_MAX_PROMPT_HUNK_CHARS`` caps what renders (the ``capture_triage`` slice)."""
    out = run_git(["diff", "--unified=3", "-M", f"{sha}..HEAD", "--", *paths], repo_root).strip("\n")
    if not out:
        return None, None, False
    if scan_text(out):
        return None, "withheld (secret-scan hit) — diffstat/header-only", False
    if scan_threats(out)["tier_a"]:
        return None, "withheld (Tier-A threat-scan hit) — diffstat/header-only", False
    if len(out) > _MAX_PROMPT_HUNK_CHARS:
        return out[:_MAX_PROMPT_HUNK_CHARS], None, True
    return out, None, False


def entry_brief(
    entry: dict, memory_dir: str, repo_root: Optional[str], *, index_dir: Optional[str] = None
) -> dict:
    """The EVD-1 evidence brief for ONE worklist entry — deterministic, read-only, zero
    persisted state.

    ``entry`` is a ``find_stale``/``recalled_stale_worklist`` item (``name`` /
    ``changed_paths`` / ``recency`` / ``source_commit`` [/ ``linked`` / ``watermark``]).
    Git legs run only when the entry's own baseline resolves in this repo (SHP-3
    otherwise degrades to an honest note); the cache legs (CLB-3 evidence drift,
    ``invalid_after``) each fail independently toward absent. Never raises.
    """
    brief = {
        "name": str(entry.get("name") or ""),
        "source_commit": str(entry.get("source_commit") or ""),
        "baseline_resolvable": False,
        "changed_paths": list(entry.get("changed_paths") or []),
        "recency": entry.get("recency"),
        "watermark": bool(entry.get("watermark")),
        "linked": list(entry.get("linked") or []),
        "diffstat": [],
        "hunk_headers": [],
        "headers_withheld": None,
        "bodies": None,
        "bodies_withheld": None,
        "bodies_truncated": False,
        "evidence_drift": None,
        "invalid_after": None,
    }
    try:
        sc, paths = brief["source_commit"], brief["changed_paths"]
        if repo_root and sc:
            brief["baseline_resolvable"] = _baseline_resolvable(sc, repo_root)
        if repo_root and paths and brief["baseline_resolvable"]:
            brief["diffstat"] = _diffstat(sc, paths, repo_root)
            brief["hunk_headers"], brief["headers_withheld"] = _hunk_headers(sc, paths, repo_root)
            brief["bodies"], brief["bodies_withheld"], brief["bodies_truncated"] = _bodies(
                sc, paths, repo_root
            )
        try:
            from .build_index import default_index_dir

            rec = read_evidence_drift(index_dir or default_index_dir(memory_dir)).get(brief["name"])
            if isinstance(rec, dict):
                brief["evidence_drift"] = rec
        except Exception:
            pass
        try:
            with open(os.path.join(memory_dir, f"{brief['name']}.md"), "r", encoding="utf-8") as fh:
                brief["invalid_after"] = read_invalid_after(fh.read())
        except Exception:
            pass
        return brief
    except Exception:
        return brief


def render_brief(brief: dict) -> List[str]:
    """The brief as bounded, deterministic listing lines — evidence, then the four human
    verdict paths (rendered for ALL of them; nothing here suggests, gates, or applies)."""
    lines: List[str] = []
    name = brief.get("name") or "?"
    wm = " [since-watermark]" if brief.get("watermark") else ""
    when = ""
    rec = brief.get("recency")
    if isinstance(rec, (int, float)) and not isinstance(rec, bool) and rec > 0:
        when = f"; newest drift {datetime.fromtimestamp(int(rec), timezone.utc).date().isoformat()}"
    lines.append(f"{name}{wm} — baseline {_short(brief.get('source_commit')) or 'unknown'} → HEAD{when}")
    if brief.get("on_worklist") is False:
        lines.append(
            "  note: not on the current worklist (not recently recalled, snoozed, or terminal)"
            " — evidence rendered anyway"
        )
    paths = brief.get("changed_paths") or []
    more = f" (+{len(paths) - _MAX_RENDERED_PATHS} more)" if len(paths) > _MAX_RENDERED_PATHS else ""
    lines.append(f"  drifted cited path(s): {', '.join(paths[:_MAX_RENDERED_PATHS]) or 'none recorded'}{more}")
    if not brief.get("baseline_resolvable"):
        lines.append(
            f"  baseline {_short(brief.get('source_commit')) or '(none)'} is not in this repo's "
            "history (squash-merge or shallow clone — SHP-3's class): no diff can be rendered; "
            "re-read the cited paths at HEAD"
        )
    if brief.get("diffstat"):
        lines.append("  diffstat:")
        lines.extend("    " + ln for ln in brief["diffstat"])
    if brief.get("headers_withheld"):
        lines.append(f"  hunk headers: {brief['headers_withheld']}")
    elif brief.get("hunk_headers"):
        lines.append("  hunks (headers/function context):")
        lines.extend("    " + ln for ln in brief["hunk_headers"])
    if brief.get("bodies_withheld"):
        lines.append(f"  hunk bodies: {brief['bodies_withheld']} (the capture lane's discipline)")
    elif brief.get("bodies"):
        cap = " (truncated at the capture lane's cap)" if brief.get("bodies_truncated") else ""
        lines.append(f"  hunk bodies (secret-linted{cap}):")
        lines.extend("    " + ln for ln in brief["bodies"].splitlines())
    drift = brief.get("evidence_drift")
    if isinstance(drift, dict):
        lines.append(
            f"  evidence drift (CLB-3): {drift.get('fences', 0)} fenced snippet(s) — "
            f"{drift.get('missing', 0)} missing at HEAD, {drift.get('whitespace', 0)} whitespace-only"
        )
    if brief.get("invalid_after"):
        lines.append(
            f"  invalid_after: {brief['invalid_after']} (terminal — recall's pre-cut penalty "
            "already engages)"
        )
    if brief.get("linked"):
        lines.append(f"  linked (review-adjacent): {', '.join(brief['linked'])}")
    lines.append(
        "  verdict (yours — LIF-1): graduate | fix | demote | snooze, via the reconsolidate "
        "tool (action='reverify') or python -m memory.reconsolidate --reverify NAME --outcome …"
    )
    return lines


def brief_for_name(
    name: str,
    memory_dir: str,
    repo_root: Optional[str],
    *,
    telemetry_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    since: Optional[str] = None,
) -> Optional[dict]:
    """The brief for ONE named memory — worklist entry first (it already carries
    ``linked``/``watermark``), full ``find_stale`` fallback for stale-but-not-listed names
    (snoozed, terminal, or never recently recalled — a human may still want evidence).
    ``None`` when the name is in neither set (nothing drifted, or no citation
    provenance). One name per call — the per-item discipline the whole flow keeps.

    Lazy imports keep the cold-path split legible: ``reconsolidate`` (which carries the
    write engine) is reached only from HERE, never at module import — this module calls
    no write primitive anywhere (AST-pinned).
    """
    slug = name[: -len(".md")] if name.endswith(".md") else name
    entry: Optional[dict] = None
    on_worklist = False
    try:
        from .reconsolidate import recalled_stale_worklist, watermark_stale_candidates

        worklist = recalled_stale_worklist(
            memory_dir,
            repo_root or "",
            telemetry_dir=telemetry_dir,
            watermark_stale=watermark_stale_candidates(
                memory_dir, repo_root or "", telemetry_dir=telemetry_dir
            ),
            **({"since": since} if since else {}),
        )
        entry = next((dict(e) for e in worklist if e.get("name") == slug), None)
        on_worklist = entry is not None
    except Exception:
        entry = None
    if entry is None:
        try:
            from .reconsolidate import _attach_linked_neighbors
            from .staleness import find_stale

            stale = find_stale(memory_dir, repo_root or "", **({"since": since} if since else {}))
            entry = next((dict(e) for e in stale if e.get("name") == slug), None)
            if entry is not None:
                _attach_linked_neighbors([entry], memory_dir)
        except Exception:
            entry = None
    if entry is None:
        return None
    brief = entry_brief(entry, memory_dir, repo_root, index_dir=index_dir)
    brief["on_worklist"] = on_worklist
    return brief


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(
        description="Reverify brief — read-only evidence for ONE reconsolidation worklist "
        "entry (diffstat + hunk headers from the entry's own source_commit baseline; "
        "secret-linted bodies when clean). The verdict stays yours."
    )
    parser.add_argument("name", help="the memory slug, with or without .md — one entry per call")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--since",
        default=None,
        help="find_stale window override (hermetic tests widen the wall-clock window)",
    )
    args = parser.parse_args(argv)

    memory_dir, repo_root = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    repo_root = args.repo_root or repo_root
    brief = brief_for_name(
        args.name, memory_dir, repo_root, telemetry_dir=args.telemetry_dir, since=args.since
    )
    if brief is None:
        print(
            f"nothing to brief: {args.name} is not in the current stale set "
            "(no cited-code drift recorded, or no citation provenance)"
        )
        return 1
    for ln in render_brief(brief):
        print(ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
