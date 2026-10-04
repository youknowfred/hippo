"""Floor-invariant guard for MEMORY.md (read-only).

The post-trim durable floor (``MEMORY.md``) is the ONLY always-loaded memory context, so it
must stay lean. The invariant: memory pointers (``](file.md)`` links) may appear ONLY under
the two floor sections —

    ## User
    ## Working Style & Process Feedback

A project/reference ``](file.md)`` link anywhere else (e.g. under "Recalled on demand", or in
the preamble) is **re-bloat** — it re-grows the trimmed always-load. This guard flags it.

Two restore-pointer links are ALLOW-LISTED everywhere (they are not memory entries):
``MEMORY.full.md`` and ``MEMORY.md`` (the pre-trim snapshot + self references). Without the
allow-list the guard would false-positive on the real floor, which carries a
``[MEMORY.full.md](MEMORY.full.md)`` link in both the preamble and the "Recalled on demand"
nav header.

Also flags floor **link rot** — a User/Working-Style pointer whose target file is missing.

FLR-1 adds **floor governance**: the floor measured against the harness's hard-coded read
window (25,000-byte cap with silent tail truncation past it; 17,500-byte advisory warn —
``provenance_format.HARNESS_FLOOR_*``, field-verified constants) plus the corpus's own
OPT-IN ``.format`` ``floor_lint`` policy (a ``banned_re`` for status-vocabulary rot, a
``max_line``). One measurement (``floor_governance``), one phrasing
(``format_governance_summary``), three surfaces: the SessionStart ``floor`` producer, the
doctor line, and ``observe_floor_edit`` — the once-per-session PostToolUse nag that fires
at the actual editing moment (killed by ``HIPPO_DISABLE_FLOOR_NAG``). The field defect it
mechanizes: an always-loaded floor that sawtoothed over the warn line in 87% of 228
commits — and past the cap in 9 — while the only lint lived in a CI lane that never ran.

READ-ONLY over the corpus: never edits MEMORY.md (or any memory); the nag's per-session
sentinel lands in the gitignored telemetry dir. Never raises. Its one-line summary is the
SessionStart ``floor`` producer.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .staleness import RunContext

# Sections under which memory pointers ARE allowed (the always-loaded floor).
_FLOOR_SECTIONS = ("User", "Working Style & Process Feedback")

# Restore/snapshot pointers — not memory entries; allowed in any section.
_ALLOWLIST = ("MEMORY.full.md", "MEMORY.md")

# A markdown link whose target is an .md file: [text](target.md)
_MD_LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")

_MAX_ITEMS = 20
_MAX_CHARS = 1500


def _floor_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, "MEMORY.md")


def floor_violations(memory_dir: str) -> Dict[str, List[dict]]:
    """Return ``{"rebloat": [...], "missing_targets": [...]}`` for MEMORY.md.

    - ``rebloat`` — non-allow-listed ``](file.md)`` links found OUTSIDE the two floor sections
      (each ``{file, section}``; section is ``"(preamble)"`` before the first header).
    - ``missing_targets`` — floor-section pointers whose target file is absent from memory_dir.

    READ-ONLY; never raises (returns empty sets on any failure).
    """
    result: Dict[str, List[dict]] = {"rebloat": [], "missing_targets": []}
    try:
        with open(_floor_path(memory_dir), "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return result

    current = "(preamble)"
    for raw in text.split("\n"):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# "):  # the H1 title — not a section
            continue
        for m in _MD_LINK_RE.finditer(line):
            target = m.group(1)
            base = target.rsplit("/", 1)[-1]
            if base in _ALLOWLIST:
                continue
            if current in _FLOOR_SECTIONS:
                # A genuine floor pointer — check the target file exists (link rot).
                if not os.path.exists(os.path.join(memory_dir, target)):
                    result["missing_targets"].append({"file": base, "section": current})
            else:
                # A memory link outside the floor sections — re-bloat.
                result["rebloat"].append({"file": base, "section": current})
    return result


def floor_memory_names(memory_dir: str) -> set:
    """Slug names (no ``.md``) of the memory pointers pinned in the MEMORY.md floor.

    These are the User + Working-Style memories ALREADY always-loaded in full, so the recall
    DISPLAY layer (``recall.main``) drops them from per-prompt results — re-surfacing a memory
    the agent already has wastes a top-k slot + injects redundant tokens — and tops off from
    the fused tail. The COMPLEMENT of ``floor_violations``: the same section walk, but it
    COLLECTS the in-floor pointers instead of flagging the out-of-floor ones. ``MEMORY.full.md``
    / ``MEMORY.md`` restore links are excluded (not memory entries). Read-only; never raises;
    empty set on any failure.
    """
    names: set = set()
    try:
        with open(_floor_path(memory_dir), "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return names
    current = "(preamble)"
    for raw in text.split("\n"):
        stripped = raw.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# "):  # the H1 title — not a section
            continue
        if current not in _FLOOR_SECTIONS:
            continue
        for m in _MD_LINK_RE.finditer(raw):
            base = m.group(1).rsplit("/", 1)[-1]
            if base in _ALLOWLIST:
                continue
            names.add(base[:-3] if base.endswith(".md") else base)
    return names


def floor_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> "str | None":
    """SessionStart producer: SILENT when the floor invariant holds; one bounded block when not.

    Lists project/reference links that re-bloat the floor (and any floor link rot), and —
    FLR-1 — one governance line when the floor is over the harness read window or violates
    the corpus's declared ``.format`` ``floor_lint`` policy (``floor_governance``; the
    empty norm holds: a lean, clean floor still renders nothing). READ-ONLY; never raises.
    ``ctx`` (LIF-6's shared per-run ``RunContext``) is unused here — declared only so
    every producer in ``PRODUCERS`` shares ONE call shape.
    """
    try:
        v = floor_violations(memory_dir)
        rebloat = v.get("rebloat", [])
        missing = v.get("missing_targets", [])
        gov_line = format_governance_summary(floor_governance(memory_dir))
        if not rebloat and not missing and not gov_line:
            return None
        lines: List[str] = []
        if gov_line:
            lines.append(f"⚠ {gov_line} — compact (move detail into the linked files) before it grows.")
        if rebloat:
            lines.append(
                f"⚠ Memory floor re-bloat — {len(rebloat)} project/reference link(s) found "
                "OUTSIDE the User + Working-Style sections of MEMORY.md (the always-loaded floor "
                "must stay lean; these belong in on-demand recall, not the floor):"
            )
            for item in rebloat[:_MAX_ITEMS]:
                lines.append(f"  • {item['file']} (under '{item['section']}')")
            if len(rebloat) > _MAX_ITEMS:
                lines.append(f"  …and {len(rebloat) - _MAX_ITEMS} more.")
        if missing:
            lines.append(
                f"⚠ Memory floor link rot — {len(missing)} floor pointer(s) target a missing file:"
            )
            for item in missing[:_MAX_ITEMS]:
                lines.append(f"  • {item['file']} (under '{item['section']}')")
            if len(missing) > _MAX_ITEMS:
                lines.append(f"  …and {len(missing) - _MAX_ITEMS} more.")
        out = "\n".join(lines)
        if len(out) > _MAX_CHARS:
            out = out[: _MAX_CHARS - 16].rstrip() + "\n…(truncated)"
        return out
    except Exception:
        return None


def trim_safety_report(
    memory_dir: str,
    *,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    k: int = 10,
    max_queries: int = 200,
) -> dict:
    """CLM-7: would removing a floor pointer lose a recall hit? Read-only; never raises.

    Replays this clone's recent human prompts (the episode buffer, HOT-1-cleaned, newest
    ``max_queries`` distinct) through ``recall()``, which never floor-dedups, and counts per
    floor pointer how many prompts surfaced its memory in the top ``k`` on its own. A pointer
    whose memory recall carries is a trim candidate (those prompts keep the hit); one recall
    never surfaced has the floor as its only channel. Rows are biggest line first, so the
    safest bytes to trim read off the top. ``{"queries", "rows": [{line, name, bytes,
    carried}]}``.
    """
    out: dict = {"queries": 0, "rows": []}
    try:
        from .build_index import default_index_dir, load_index
        from .recall import recall
        from .recall_query import HUMAN_TURN, human_text, turn_class
        from .telemetry import default_telemetry_dir, read_episodes

        with open(_floor_path(memory_dir), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        floor_set = floor_memory_names(memory_dir)
        rows = []
        for i, line in enumerate(lines, start=1):
            m = _MD_LINK_RE.search(line)
            name = m.group(1).rsplit("/", 1)[-1][:-3] if m else None
            if name and name in floor_set:
                rows.append({"line": i, "name": name, "bytes": len(line.encode("utf-8")) + 1, "carried": 0})
        td = telemetry_dir or default_telemetry_dir(memory_dir)
        queries: List[str] = []
        for e in read_episodes(td):
            q = (e.get("query_preview") or "").strip()
            if q and turn_class(q) == HUMAN_TURN:
                q = human_text(q)
                if q and q not in queries:
                    queries.append(q)
        queries = queries[-max_queries:]
        idir = index_dir or default_index_dir(memory_dir)
        idx = load_index(idir)
        by_name = {r["name"]: r for r in rows}
        if idx is not None and by_name:
            for q in queries:
                for hit in recall(q, k=k, index=idx, index_dir=idir, memory_dir=memory_dir):
                    if hit.get("name") in by_name:
                        by_name[hit["name"]]["carried"] += 1
        out["queries"] = len(queries)
        out["rows"] = sorted(rows, key=lambda r: (-r["bytes"], r["line"]))
    except Exception:
        pass
    return out


def render_trim_report(report: dict) -> str:
    rows = report.get("rows") or []
    if not rows:
        return "floor trim report: no floor pointers to weigh."
    n = report.get("queries", 0)
    lines = [
        f"floor trim report — {len(rows)} pointer(s), {n} recent prompt(s) replayed "
        "(carried = prompts where recall surfaced the memory on its own):"
    ]
    for r in rows:
        verdict = (
            f"carried on {r['carried']}/{n} — trimming keeps those hits"
            if r["carried"]
            else "never surfaced by recall — the floor is its only channel"
        )
        lines.append(f"  L{r['line']} {r['name']} ({r['bytes']}B): {verdict}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Lint the MEMORY.md floor invariant (read-only).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument(
        "--trim-report",
        action="store_true",
        help="CLM-7: replay recent prompts and say, per floor pointer, whether recall carries "
        "its memory without the floor line (read-only)",
    )
    args = parser.parse_args(argv)

    memory_dir, _ = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    if args.trim_report:
        print(render_trim_report(trim_safety_report(memory_dir)))
        return 0

    v = floor_violations(memory_dir)
    rebloat, missing = v["rebloat"], v["missing_targets"]
    gov_line = format_governance_summary(floor_governance(memory_dir))
    if not rebloat and not missing and not gov_line:
        print("MEMORY.md floor invariant holds ✅ (memory links only under User + Working-Style)")
        return 0
    if gov_line:
        print(gov_line)
    if rebloat:
        print(f"floor re-bloat ({len(rebloat)}): project/reference links outside the floor sections")
        for item in rebloat:
            print(f"  • {item['file']} (under '{item['section']}')")
    if missing:
        print(f"floor link rot ({len(missing)}): floor pointers to missing files")
        for item in missing:
            print(f"  • {item['file']} (under '{item['section']}')")
    return 1


# --------------------------------------------------------------------------- #
# FLR-1: floor governance — size vs the harness read window, plus the corpus's own
# declared line policy (.format `floor_lint`). The lint lives where the edits happen.
# --------------------------------------------------------------------------- #
_NAG_DIR = "floor_nag"  # per-session once-only sentinel dir under the telemetry dir


def floor_nag_disabled() -> bool:
    """FLR-1's kill switch (the JIT/PRESENCE precedent): truthy env kills the edit-time
    nag lane entirely. The SessionStart producer and doctor line are NOT covered — they
    are once-per-surface state reports, not per-edit interjections."""
    return bool(os.environ.get("HIPPO_DISABLE_FLOOR_NAG", "").strip())


def floor_governance(memory_dir: str) -> dict:
    """Measure MEMORY.md against the harness read window and the corpus's declared policy.

    Returns ``{"bytes", "warn_bytes", "cap_bytes", "over_warn", "over_cap", "banned",
    "longlines", "policy_declared"}`` — ``banned`` rows are ``{"line", "token"}`` (1-based
    line numbers, first match per line), ``longlines`` rows are ``{"line", "chars"}``.
    The size halves ALWAYS run (the harness window applies to every corpus using the
    native floor); ``banned``/``longlines`` run only when ``.format`` declares
    ``floor_lint.banned_re`` / ``floor_lint.max_line`` (``read_floor_lint`` — policy is
    the corpus's own, opt-in by declaration). READ-ONLY; never raises; a missing/
    unreadable floor measures as 0 bytes with no findings.
    """
    from .provenance import read_floor_lint

    policy = read_floor_lint(memory_dir)
    out = {
        "bytes": 0,
        "warn_bytes": policy["warn_bytes"],
        "cap_bytes": policy["cap_bytes"],
        "lines": 0,
        "warn_lines": policy["warn_lines"],
        "cap_lines": policy["cap_lines"],
        "over_warn": False,
        "over_cap": False,
        "over_line_warn": False,
        "over_line_cap": False,
        "sections_over": [],
        "banned": [],
        "longlines": [],
        "policy_declared": bool(policy["banned_re"] or policy["max_line"]),
    }
    try:
        with open(_floor_path(memory_dir), "rb") as fh:
            raw = fh.read()
    except Exception:
        return out
    out["bytes"] = len(raw)
    out["over_warn"] = len(raw) > policy["warn_bytes"]
    out["over_cap"] = len(raw) > policy["cap_bytes"]
    # CLM-7: the line edge — whichever limit comes first truncates the floor.
    out["lines"] = len(raw.decode("utf-8", "replace").splitlines())
    out["over_line_warn"] = out["lines"] > policy["warn_lines"]
    out["over_line_cap"] = out["lines"] > policy["cap_lines"]
    out["sections_over"] = sections_over_budget(
        raw.decode("utf-8", "replace"), policy["section_budgets"]
    )
    if not out["policy_declared"]:
        return out
    banned_re = None
    if policy["banned_re"]:
        try:
            banned_re = re.compile(policy["banned_re"])
        except Exception:
            banned_re = None
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        return out
    for i, line in enumerate(text.split("\n"), start=1):
        if banned_re is not None:
            m = banned_re.search(line)
            if m:
                token = (m.group(0) or "").strip()[:40]
                out["banned"].append({"line": i, "token": token})
        if policy["max_line"] and len(line) > policy["max_line"]:
            out["longlines"].append({"line": i, "chars": len(line)})
    return out


def section_bytes(text: str) -> Dict[str, int]:
    """CLM-7: utf-8 bytes per ``## `` section (its header through the line before the next)."""
    sizes: Dict[str, int] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            current = line.strip()
            sizes.setdefault(current, 0)
        if current is not None:
            sizes[current] += len(line.encode("utf-8")) + 1
    return sizes


def sections_over_budget(text: str, budgets: Dict[str, int]) -> List[dict]:
    """``[{section, bytes, budget}]`` for each declared section over its byte budget."""
    if not budgets:
        return []
    sizes = section_bytes(text)
    return [
        {"section": sec, "bytes": sizes[sec], "budget": cap}
        for sec, cap in budgets.items()
        if sizes.get(sec, 0) > cap
    ]


def over_hard_edge(text: str, policy: dict) -> Optional[str]:
    """CLM-7: why ``text`` would cross a hard floor edge (either native read limit, or a
    declared section budget), or ``None``. Used to refuse hippo's own floor writes."""
    n_bytes = len(text.encode("utf-8"))
    n_lines = len(text.splitlines())
    if n_bytes > policy["cap_bytes"]:
        return f"{n_bytes:,}B, past the {policy['cap_bytes']:,}B read limit"
    if n_lines > policy["cap_lines"]:
        return f"{n_lines} lines, past the {policy['cap_lines']}-line read limit"
    over = sections_over_budget(text, policy.get("section_budgets") or {})
    if over:
        o = over[0]
        return f"'{o['section']}' at {o['bytes']:,}B, past its {o['budget']:,}B budget"
    return None


def format_governance_summary(gov: dict) -> Optional[str]:
    """ONE bounded line naming every governance finding, or ``None`` when clean.

    Shared verbatim by the SessionStart producer, the edit-time nag, and the CLI so the
    three surfaces can never describe the same state differently. Severity order: the
    read-cap breach leads (truncation is active data loss), then the warn line, then the
    declared-policy counts. Never raises.
    """
    try:
        parts: List[str] = []
        if gov.get("over_cap"):
            parts.append(
                f"{gov['bytes']:,}B — PAST the {gov['cap_bytes']:,}B read cap: the floor's "
                "tail is silently truncated in every session"
            )
        elif gov.get("over_warn"):
            parts.append(f"{gov['bytes']:,}B — over the {gov['warn_bytes']:,}B warn line")
        if gov.get("over_line_cap"):
            parts.append(
                f"{gov['lines']} lines — PAST the {gov['cap_lines']}-line read limit: lines "
                f"{gov['cap_lines'] + 1}+ never load"
            )
        elif gov.get("over_line_warn"):
            parts.append(f"{gov['lines']} lines — over the {gov['warn_lines']}-line warn line")
        for o in (gov.get("sections_over") or [])[:3]:
            parts.append(f"'{o['section']}' {o['bytes']:,}B over its {o['budget']:,}B budget")
        banned = gov.get("banned") or []
        if banned:
            preview = ", ".join(
                f"L{b['line']} `{b['token']}`" for b in banned[:3] if isinstance(b, dict)
            )
            more = f" (+{len(banned) - 3} more)" if len(banned) > 3 else ""
            parts.append(f"{len(banned)} banned-token line(s): {preview}{more}")
        longlines = gov.get("longlines") or []
        if longlines:
            parts.append(f"{len(longlines)} line(s) over the declared max")
        if not parts:
            return None
        return "MEMORY.md floor: " + "; ".join(parts)
    except Exception:
        return None


def observe_floor_edit(
    touched_path: str,
    *,
    memory_dir: str,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """FLR-1's edit-time nag: called by the PostToolUse lane AFTER a mutating file tool.

    Returns the bounded nag line(s) exactly ONCE per session — on the first MEMORY.md
    edit that leaves the floor over the warn/cap line or violating declared policy — and
    ``None`` on every other call: non-floor paths (one abspath compare, no file read —
    the empty norm this hook lane budgets for), a clean floor, a repeat in the same
    session, an untrusted corpus (SEC-1 parity at fire time via jit's ``_corpus_trusted``
    — the banned-token previews quote corpus content, so the emit path re-checks exactly
    like the JIT reminder does; fail-closed), or the ``HIPPO_DISABLE_FLOOR_NAG`` kill
    switch. Sentinel is one empty file per session under ``<telemetry_dir>/floor_nag/``
    (the write happens only when a nag actually fires, so almost every session never
    creates the dir). Never raises.
    """
    try:
        if not touched_path or floor_nag_disabled():
            return None
        try:
            if os.path.abspath(touched_path) != os.path.abspath(_floor_path(memory_dir)):
                return None
        except Exception:
            return None
        from .jit import _corpus_trusted

        if not _corpus_trusted(memory_dir, repo_root):
            return None
        gov = floor_governance(memory_dir)
        line = format_governance_summary(gov)
        if not line:
            return None
        td = telemetry_dir
        if td is None:
            from .telemetry import default_telemetry_dir

            td = default_telemetry_dir(memory_dir)
        sid = str(session_id or "unknown").replace(os.sep, "_")[:80]
        sentinel = os.path.join(td, _NAG_DIR, sid)
        if os.path.exists(sentinel):
            return None
        try:
            from .atomic import write_text_atomic

            os.makedirs(os.path.dirname(sentinel), exist_ok=True)
            write_text_atomic(sentinel, "")  # COR-18 discipline; content-free sentinel
        except Exception:
            pass  # dedup degrades to per-call; the line itself still fires
        return (
            f"{line} — the floor loads into EVERY session's context; move detail into "
            "the linked memory files, then trim (once per session; "
            "HIPPO_DISABLE_FLOOR_NAG kills this)."
        )
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
