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


def main(argv=None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Lint the MEMORY.md floor invariant (read-only).")
    parser.add_argument("--memory-dir", default=None)
    args = parser.parse_args(argv)

    memory_dir, _ = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir

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


if __name__ == "__main__":
    raise SystemExit(main())


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
        "over_warn": False,
        "over_cap": False,
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
