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

A pointer may also be a bare name (``- name — text``) where a memory of that name exists:
``pointer_targets`` / ``pointer_name`` are the one definition every floor reader uses. A
bare name with no file is prose, never link rot.

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

# Sections under which memory pointers ARE allowed (the always-loaded floor) when the corpus
# declares none. GAT-3: a declared hippo.json ``floor_sections`` replaces them, read through
# the one accessor the pointer writer uses too (``_floor_sections``).
_FLOOR_SECTIONS = ("User", "Working Style & Process Feedback")

# Restore/snapshot pointers — not memory entries; allowed in any section.
_ALLOWLIST = ("MEMORY.full.md", "MEMORY.md")

# A markdown link whose target is an .md file: [text](target.md)
_MD_LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")

# FLR-1: a floor pointer may also be a bare name, `- name — text`: a top-level list item whose
# first token is a slug (optionally in backticks, optionally `name.md`) followed by an em dash,
# a spaced hyphen or a colon. It is a pointer only where a memory of that name exists
# (``memory_exists``): that test is what keeps a prose bullet prose, so a bare name with no file
# is never link rot. A line with a markdown link is link-form, read exactly as before.
_BARE_RE = re.compile(r"^[-*+][ \t]+`?([\w.\-]+?)(?:\.md)?`?(?:[ \t]*—|[ \t]+-(?:[ \t]|$)|:(?:[ \t]|$))")


def bare_pointer(line: str) -> Optional[str]:
    """The name a line has in the bare-pointer SHAPE, or None (a line with a markdown link
    never has it). Shape only: callers that do not already hold the name test existence."""
    if _MD_LINK_RE.search(line):
        return None
    m = _BARE_RE.match(line)
    if not m or f"{m.group(1)}.md" in _ALLOWLIST:
        return None
    return m.group(1)


def pointer_targets(line: str, is_memory=None) -> List[str]:
    """THE definition of the memory pointers one floor line carries, as ``.md`` targets: every
    non-allow-listed markdown link (today's form), else a bare pointer whose memory exists
    (``is_memory(name)``; without it only the link form reads). Never raises."""
    targets = [m.group(1) for m in _MD_LINK_RE.finditer(line)]
    if targets:
        return [t for t in targets if t.rsplit("/", 1)[-1] not in _ALLOWLIST]
    name = bare_pointer(line) if is_memory is not None else None
    return [f"{name}.md"] if name and is_memory(name) else []


def pointer_name(line: str, is_memory=None) -> Optional[str]:
    """The one memory name a pointer line stands for (sorting, removal, per-line rows): the
    FIRST link's, None when that link is a restore link (as before), else the bare pointer's."""
    m = _MD_LINK_RE.search(line)
    if m:
        base = m.group(1).rsplit("/", 1)[-1]
        return None if base in _ALLOWLIST else (base[:-3] if base.endswith(".md") else base)
    t = pointer_targets(line, is_memory)
    return t[0][:-3] if t else None


def memory_exists(memory_dir: str):
    """``is_memory`` for ``memory_dir``: a memory file of that name in the corpus or its outbox.
    Lazy: nothing is read until a bare-shaped line asks, then one directory listing each (a
    floor of bare pointers asks about every line). Never raises."""
    known: List[set] = []

    def exists(name: str) -> bool:
        if not known:
            names: set = set()
            try:
                from .outbox import present
                from .provenance import _is_memory_filename

                for d in (memory_dir, present(memory_dir)):
                    if d:
                        with os.scandir(d) as it:
                            names.update(e.name[:-3] for e in it
                                         if _is_memory_filename(e.name) and e.is_file())
            except Exception:
                pass
            known.append(names)
        return name in known[0]

    return exists

_MAX_ITEMS = 20
_MAX_CHARS = 1500


def _floor_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, "MEMORY.md")


def _floor_sections(memory_dir: str) -> tuple:
    """The floor's section names (no ``## ``) — declared, or today's two. Never raises."""
    from .provenance import read_floor_sections

    try:
        return tuple(h[3:].strip() for h, _ in read_floor_sections(memory_dir))
    except Exception:
        return _FLOOR_SECTIONS


def _sections_label(memory_dir: str) -> str:
    """How the floor's sections are named in a message: today's wording when undeclared."""
    sections = _floor_sections(memory_dir)
    return "User + Working-Style" if sections == _FLOOR_SECTIONS else " + ".join(sections)


def floor_violations(memory_dir: str) -> Dict[str, List[dict]]:
    """Return ``{"rebloat": [...], "missing_targets": [...]}`` for MEMORY.md.

    - ``rebloat`` — non-allow-listed ``](file.md)`` links found OUTSIDE the two floor sections
      (each ``{file, section}``; section is ``"(preamble)"`` before the first header).
    - ``missing_targets`` — floor-section pointers whose target file is absent from memory_dir.

    READ-ONLY; never raises (returns empty sets on any failure).
    """
    result: Dict[str, List[dict]] = {"rebloat": [], "missing_targets": []}
    from .outbox import effective_path, present  # OUT-1: the floor as this machine sees it

    ob = present(memory_dir)
    floor = effective_path(memory_dir, "MEMORY") if ob else _floor_path(memory_dir)
    try:
        with open(floor, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return result

    sections = _floor_sections(memory_dir)
    exists = memory_exists(memory_dir)
    current = "(preamble)"
    for raw in text.split("\n"):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# "):  # the H1 title — not a section
            continue
        for target in pointer_targets(line, exists):
            base = target.rsplit("/", 1)[-1]
            if current in sections:
                # A genuine floor pointer — check the target file exists (link rot).
                if not os.path.exists(os.path.join(memory_dir, target)) and not (
                    ob and os.path.exists(os.path.join(ob, target))
                ):
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
    try:
        with open(_floor_path(memory_dir), "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return set()
    return floor_names_in(text, _floor_sections(memory_dir), memory_exists(memory_dir))


def floor_names_in(text: str, sections: tuple, is_memory=None) -> set:
    """``floor_memory_names``' section walk over floor ``text`` (NAT-3 weighs the floor copy a
    floor edit lands in, which may be the outbox's). ``is_memory`` lets bare pointers read
    (``pointer_targets``). Never raises."""
    names: set = set()
    current = "(preamble)"
    for raw in text.split("\n"):
        stripped = raw.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# "):  # the H1 title — not a section
            continue
        if current not in sections:
            continue
        for target in pointer_targets(raw, is_memory):
            base = target.rsplit("/", 1)[-1]
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
                f"OUTSIDE the {_sections_label(memory_dir)} sections of MEMORY.md (the always-loaded floor "
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
    floor_text: Optional[str] = None,
    names: Optional[set] = None,
    pools_for=None,
) -> dict:
    """CLM-7: would removing a floor pointer lose a recall hit? Read-only; never raises.

    Replays this clone's recent human prompts (the episode buffer, HOT-1-cleaned, newest
    ``max_queries`` distinct) through ``recall()``, which never floor-dedups, and counts per
    floor pointer how many prompts surfaced its memory in the top ``k`` on its own. A pointer
    whose memory recall carries is a trim candidate (those prompts keep the hit); one recall
    never surfaced has the floor as its only channel. Rows are biggest line first, so the
    safest bytes to trim read off the top. ``{"queries", "replayed", "rows": [{line, name,
    bytes, carried, named, lost}]}`` (``replayed``: an index was there to replay against).

    NAT-3 adds ``named`` and ``lost``, judged the way the prompt hook selects, when the
    caller passes ``pools_for(queries) -> ({query: [(name, lane, admitted)]}, share)``: each
    prompt's ranking ``k`` plus a floor's worth deep, with the hook's lane request
    (``floor_diet`` supplies it, cached on disk). ``named`` counts the replayed prompts on
    which the hook named the memory when it ran (the episode's names) AND the memory ranks
    within ``k`` once the other floor memories, which collapse and take no slot, are set
    aside: the floor line was delivering a real match. ``lost`` counts those on which, with
    this line gone, the hook's own selection would not serve it: the behavior lane's share is
    already full, or it is a journal the prompt does not name. That is a recall hit the
    demotion loses; ``carried`` then reads off the same ranking, so each prompt is ranked
    once. A prompt ``pools_for`` leaves out (not ranked yet) is not judged: ``unreplayed``
    counts them, and each row's ``pending`` counts those the hook named it on. ``floor_text`` weighs that text
    instead of the committed ``MEMORY.md`` (the floor copy a floor edit lands in), and
    ``names`` weighs those pointers instead of the floor sections' (a flat floor). Without
    ``pools_for`` the replay is CLM-7's alone: plain recall's top ``k``.
    """
    out: dict = {"queries": 0, "rows": [], "replayed": False}
    try:
        if floor_text is None:
            with open(_floor_path(memory_dir), encoding="utf-8") as fh:
                floor_text = fh.read()
        lines = floor_text.split("\n")
        exists = memory_exists(memory_dir)
        floor_set = set(names) if names is not None else floor_names_in(floor_text, _floor_sections(memory_dir), exists)
        rows = []
        for i, line in enumerate(lines, start=1):
            m = _MD_LINK_RE.search(line)
            name = m.group(1).rsplit("/", 1)[-1][:-3] if m else pointer_name(line, exists)
            if name and name in floor_set and all(r["name"] != name for r in rows):
                rows.append({"line": i, "name": name, "bytes": len(line.encode("utf-8")) + 1,
                             "carried": 0, "named": 0, "lost": 0})
        by_name = {r["name"]: r for r in rows}
        queries, named = recent_prompts(memory_dir, telemetry_dir, max_queries)
        pools, share, hits = None, None, None
        if not by_name:
            pass
        elif pools_for is not None:
            pools, share = pools_for(queries)
            if pools is not None:
                waiting = [q for q in queries if q not in pools]
                out["unreplayed"] = len(waiting)
                for q in waiting:  # judged on a later call; until then it is not known safe
                    for name in named.get(q, ()):
                        if name in by_name:
                            by_name[name]["pending"] = by_name[name].get("pending", 0) + 1
                queries = [q for q in queries if q in pools]
                hits = {q: [n for n, _l, ok in pools[q] if ok][:k] for q in queries}
        else:
            hits = _plain_hits(memory_dir, index_dir, queries, k)
        replayed = hits is not None
        for q in queries if replayed else ():
            for name in hits.get(q, ()):
                if name in by_name:
                    by_name[name]["carried"] += 1
            if pools is None:
                continue
            pool = pools.get(q) or []
            for name in named.get(q, ()):
                if name in by_name and _hit_with_floor(pool, name, by_name, k):
                    by_name[name]["named"] += 1
                    if not _served_without_line(pool, name, by_name, k, share):
                        by_name[name]["lost"] += 1
        out["queries"] = len(queries)
        out["replayed"] = replayed
        out["rows"] = sorted(rows, key=lambda r: (-r["bytes"], r["line"]))
    except Exception:
        pass
    return out


def _competing(pool: list, name: str, floor: dict, on_floor: bool) -> list:
    """The pool rows that take the hook's slots for one prompt: every other floor memory
    collapses onto its floor line (no slot), an unnamed journal off the floor is withheld,
    and ``name`` itself is in the pool while its line is on the floor, and after that only
    if the hook would admit it."""
    return [(n, lane) for n, lane, ok in pool
            if (n == name and (on_floor or ok)) or (n != name and n not in floor and ok)]


def _hit_with_floor(pool: list, name: str, floor: dict, k: int) -> bool:
    """Its rank among the rows that take slots is within ``k``: a real top-k match."""
    return name in [n for n, _ in _competing(pool, name, floor, True)[:k]]


def _served_without_line(pool: list, name: str, floor: dict, k: int, share: Optional[float]) -> bool:
    """The hook's selection over the competing rows once the line is gone
    (``lanes.select`` when the corpus declares lanes, the first ``k`` otherwise)."""
    rows = _competing(pool, name, floor, False)
    if share is None:
        return name in [n for n, _ in rows[:k]]
    from .lanes import select

    picked, _cut = select([{"name": n, "lane": lane} for n, lane in rows], k, share)
    return name in {r["name"] for r in picked}


def recent_prompts(memory_dir: str, telemetry_dir: Optional[str], max_queries: int):
    """``(queries, {query: names the hook named})``: the newest ``max_queries`` distinct
    human prompts of the episode buffer, oldest first. Raises on failure."""
    from .recall_query import HUMAN_TURN, human_text, turn_class
    from .telemetry import default_telemetry_dir, read_episodes

    td = telemetry_dir or default_telemetry_dir(memory_dir)
    queries: List[str] = []
    named: Dict[str, set] = {}
    for e in read_episodes(td):
        q = (e.get("query_preview") or "").strip()
        if q and turn_class(q) == HUMAN_TURN:
            q = human_text(q)
            if q:
                if q not in named:
                    queries.append(q)
                    named[q] = set()
                named[q].update(n for n in (e.get("recalled_names") or []) if isinstance(n, str))
    queries = queries[-max_queries:] if max_queries > 0 else []
    return queries, {q: named[q] for q in queries}


def _plain_hits(memory_dir, index_dir, queries, k) -> Optional[Dict[str, set]]:
    """CLM-7's own question: plain recall's top ``k`` per prompt; None without an index."""
    from .build_index import default_index_dir, load_index
    from .recall import recall

    idir = index_dir or default_index_dir(memory_dir)
    idx = load_index(idir)
    if idx is None:
        return None
    return {q: {h.get("name") for h in recall(q, k=k, index=idx, index_dir=idir, memory_dir=memory_dir)}
            for q in queries}


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
        help="replay recent prompts and say, per floor pointer, whether recall carries "
        "its memory without the floor line (read-only)",
    )
    args = parser.parse_args(argv)

    memory_dir, repo_root = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    if args.trim_report:
        print(render_trim_report(trim_safety_report(memory_dir)))
        return 0

    v = floor_violations(memory_dir)
    rebloat, missing = v["rebloat"], v["missing_targets"]
    gov_line = format_governance_summary(floor_governance(memory_dir))
    from .floor_rules import print_report  # RUL-10: informational; never moves the exit code

    if not rebloat and not missing and not gov_line:
        print(f"MEMORY.md floor invariant holds ✅ (memory links only under {_sections_label(memory_dir)})")
        print_report(memory_dir, None if args.memory_dir else repo_root)
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
    print_report(memory_dir, None if args.memory_dir else repo_root)
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
    from .settings import disabled

    return disabled("floor-nag")  # SRF-4: HIPPO_DISABLE=floor-nag, or HIPPO_DISABLE_FLOOR_NAG


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
    try:
        with open(_floor_path(memory_dir), "rb") as fh:
            raw = fh.read()
    except Exception:
        raw = None
    return measure_floor(raw, policy)


def measure_floor(raw: Optional[bytes], policy: dict) -> dict:
    """``floor_governance``'s measurement of floor bytes ``raw`` (None: no floor) under an
    already-read ``policy`` — the one definition the write gate (``gate.py``) judges a
    before/after pair with, so the gate and the lint can never measure differently."""
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
    if raw is None:
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
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        return out
    out["banned"] = banned_lines(text, policy["banned_re"])
    for i, line in enumerate(text.split("\n"), start=1):
        if policy["max_line"] and len(line) > policy["max_line"]:
            out["longlines"].append({"line": i, "chars": len(line)})
    return out


def banned_lines(text: str, pattern: Optional[str]) -> List[dict]:
    """``[{line, token}]`` — each line of ``text`` the declared ``banned_re`` matches (first
    match per line, 1-based). ``[]`` when no pattern is declared or it does not compile."""
    if not pattern:
        return []
    try:
        banned_re = re.compile(pattern)
    except Exception:
        return []
    rows: List[dict] = []
    for i, line in enumerate(text.split("\n"), start=1):
        m = banned_re.search(line)
        if m:
            rows.append({"line": i, "token": (m.group(0) or "").strip()[:40]})
    return rows


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
