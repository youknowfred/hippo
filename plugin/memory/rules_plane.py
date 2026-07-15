"""The Claude-rules governance plane, read by hippo as a first-class surface (RUL, tier T2).

The rules plane — ``CLAUDE.md``, ``AGENTS.md``, ``.claude/rules/``, ``.claude/agents/``,
``.claude/skills/`` — is always-loaded, unranked, un-staled, and monotonically growing;
hippo's corpus is ranked, staleness-tracked, and review-gated. This module is the bridge:
the ONE canonical enumeration of the governance surface (the audit skill's ``GOV_GLOBS``
convention, promoted to importable API) plus the read-only joins built over it:

  - RUL-1 ``conflict_radar`` — governance files citing memories the corpus disagrees with:
    the authority-evidence gap (cited but never recalled, strength < 0.15) and the
    typed-edge leg (cited but another memory ``supersedes``/``contradicts`` it).
  - RUL-2 ``rules_rot`` — hippo's staleness discipline applied to the rules plane itself:
    backtick code-references whose path/symbol left the tree, and ``.claude/rules``
    ``paths:`` globs that match nothing (the harness lazy-load feature RUL-0 confirmed).
  - RUL-3 ``rule_dup_candidates`` — the preventive counterpart: at write time, warn when
    a draft memory RESTATES a rule already in the governance plane ("link, don't copy"),
    stopping two-plane drift before it starts.
  - RUL-4 ``refresh_rules_cache``/``load_rules_cache`` — the derived, gitignored,
    rebuildable side-index (inv1) that lets recall surface a governance section as a
    labelled low-priority "(rule)" POINTER when genuinely relevant — no import, no
    duplication, the rules plane stays the authority for its own content.
  - ``derive_paths_globs`` — the RUL-6/RUL-7 shared memory→rule scoping derivation:
    conservative ``paths:`` globs from a memory's concrete ``cited_paths``, with the
    over-scoping cap (a derived scope must never balloon into a near-unscoped
    always-load).

Relationship to the two pre-existing scan surfaces (deliberately NOT merged, inv5):
``archive._SCAN_TARGETS`` is the ARCHIVE-PROTECTION surface (adds ``docs/prompts``, omits
``AGENTS.md``, fails CLOSED to "cited" because an unreadable file must never unlock an
archive gate). The audit skill's inline ``GOV_GLOBS`` is the prototype this module
generalizes — same globs, same citation regex. A WARNING surface like this one fails the
OTHER way: an unreadable governance file yields no findings (never cry wolf from a read
error); the archive gate keeps its own fail-closed copy.

Everything here is read-only over user-owned files (inv1), off the UserPromptSubmit hot
path (inv6), surfaces loud at doctor/SessionStart (inv3), and proposes per-item decisions
without ever auto-resolving one (inv4). Never raises.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

# The governance-plane surface, verbatim from the audit skill's working prototype
# (plugin/skills/audit/SKILL.md GOV_GLOBS): the common Claude Code conventions plus
# AGENTS.md, the Linux-Foundation cross-tool standard. Glob patterns over repo_root.
GOV_GLOBS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/rules/*.md",
    ".claude/agents/*.md",
    ".claude/skills/**/*.md",
)

# A backtick-quoted memory-name-shaped token, ``.md`` optional — the same pattern the
# archive scanner and the audit skill both match (see archive._BACKTICK_TOKEN_RE for the
# empirical false-negative note that motivates the optional suffix).
_BACKTICK_TOKEN_RE = re.compile(r"`([A-Za-z0-9_-]+(?:\.md)?)`")

# The authority-evidence threshold, verbatim from the audit skill's join: a governance-cited
# memory whose recall strength (distinct-session share, soak.compute_strength_scores) sits
# below this is "governance says do X, telemetry says nobody uses it."
STRENGTH_GAP_THRESHOLD = 0.15

# The two typed relations that make a governance citation a live CONFLICT (a rule pointing
# at a memory the corpus itself has moved past). ``refines`` is deliberately absent — a
# refined memory is still authoritative.
_CONFLICT_RELATIONS = ("supersedes", "contradicts")


def gov_files(repo_root: str) -> List[str]:
    """Absolute paths of every governance-plane file under ``repo_root`` (GOV_GLOBS order,
    de-duplicated, sorted within each glob). Never raises; ``[]`` when nothing matches."""
    out: List[str] = []
    seen: Set[str] = set()
    try:
        root = Path(repo_root)
        for pattern in GOV_GLOBS:
            try:
                matches = sorted(str(p) for p in root.glob(pattern) if p.is_file())
            except Exception:
                continue
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
    except Exception:
        return []
    return out


def _rel(repo_root: str, path: str) -> str:
    """``path`` relative to ``repo_root`` for display; the absolute path on any failure."""
    try:
        return os.path.relpath(path, repo_root)
    except Exception:
        return path


def gov_citations(repo_root: str, corpus_names: Set[str]) -> Dict[str, List[str]]:
    """Which governance file cites which corpus memory: ``{stem: [repo-relative files]}``.

    A token counts only when it resolves to a REAL corpus stem (the precision gate — a
    backtick token like ``README.md`` that is not a memory never joins). Unreadable files
    are skipped: this feeds WARNING surfaces, so a read error must yield silence, not a
    fabricated finding (the archive gate's fail-closed copy covers the opposite need).
    Never raises; ``{}`` on any failure.
    """
    cited: Dict[str, List[str]] = {}
    try:
        if not corpus_names:
            return {}
        for path in gov_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            rel = _rel(repo_root, path)
            for tok in _BACKTICK_TOKEN_RE.findall(text):
                stem = tok[:-3] if tok.endswith(".md") else tok
                if stem in corpus_names and rel not in cited.setdefault(stem, []):
                    cited[stem].append(rel)  # GOV_GLOBS encounter order: CLAUDE.md first
    except Exception:
        return {}
    return cited


def conflict_radar(
    memory_dir: str, repo_root: str, *, telemetry_dir: Optional[str] = None
) -> dict:
    """RUL-1: the rule↔memory conflict radar — the audit skill's authority-gap join as a
    standing, importable query.

    Returns::

        {
          "authority_gaps":  [{"name", "strength", "cited_by"}],  # strength leg
          "edge_conflicts":  [{"name", "relation", "by", "cited_by"}],  # typed leg
          "gate_met": bool,          # soak maturity — strength leg fires only when True
          "distinct_sessions": int,
        }

    The STRENGTH leg ("governance cites ``name`` but telemetry says nobody retrieves it,
    strength < 0.15") is gated on the soak maturity bar (``soak.soak_status()['gate_met']``,
    >= 5 distinct sessions): on a fresh clone EVERY cited memory scores 0.0, so an ungated
    standing producer would nag from day one — the explicit ``/hippo:audit`` run keeps its
    ungated join for deliberate curation sessions. The TYPED-EDGE leg ("governance cites
    ``name`` but ``by`` supersedes/contradicts it") rests on authored facts, not telemetry,
    so it fires regardless of soak maturity.

    Read-only; proposes per-item decisions (route: /hippo:consolidate), never resolves one.
    Never raises; empty findings on any failure.
    """
    empty = {
        "authority_gaps": [],
        "edge_conflicts": [],
        "gate_met": False,
        "distinct_sessions": 0,
    }
    try:
        from .links import build_graph
        from .provenance import _iter_memory_files
        from .telemetry import default_telemetry_dir
        from . import soak

        names = {
            os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)
        }
        cited = gov_citations(repo_root, names)
        if not cited:
            return empty

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        status = soak.soak_status(td, memory_dir=memory_dir)
        gate_met = bool(status.get("gate_met"))
        distinct = int(status.get("distinct_sessions") or 0)

        gaps: List[dict] = []
        if gate_met:
            strength = soak.compute_strength_scores(td)
            for name in sorted(cited):
                s = strength.get(name, 0.0)
                if s < STRENGTH_GAP_THRESHOLD:
                    gaps.append(
                        {"name": name, "strength": round(s, 4), "cited_by": cited[name]}
                    )
            gaps.sort(key=lambda g: (g["strength"], g["name"]))

        conflicts: List[dict] = []
        graph = build_graph(memory_dir)
        if graph is not None:
            for name in sorted(cited):
                for rel in _CONFLICT_RELATIONS:
                    for by in sorted(graph.typed_inbound(name, rel)):
                        conflicts.append(
                            {
                                "name": name,
                                "relation": rel,
                                "by": by,
                                "cited_by": cited[name],
                            }
                        )

        return {
            "authority_gaps": gaps,
            "edge_conflicts": conflicts,
            "gate_met": gate_met,
            "distinct_sessions": distinct,
        }
    except Exception:
        return empty


# --------------------------------------------------------------------------- #
# RUL-2: staleness & citation rot over the rules plane itself
# --------------------------------------------------------------------------- #
# A backtick span whose CONTENT we inspect for code references. Distinct from
# _BACKTICK_TOKEN_RE (memory-name-shaped tokens, RUL-1): rot cares about path-like and
# dotted-symbol tokens, which that regex deliberately excludes.
_BACKTICK_SPAN_RE = re.compile(r"`([^`\n]+)`")

_PATH_REF_RE_CACHE: List["re.Pattern"] = []


def _path_ref_re() -> "re.Pattern":
    """A path-like backtick ref: optional dir segments + filename + a CODE extension, with an
    optional :line/:line-range suffix we strip. Anchored to span content so `see foo/bar.py
    for details` is prose, not a ref (that anchoring is this regex's own property — it is
    what supplies the end boundary provenance._CITATION_RE had to add explicitly in ORC-1,
    and it is why this one never suffered the prefix-shadow bug).

    ORC-2: the extension list is IMPORTED from ``provenance._CODE_EXTS``, not hand-copied.
    The copy this replaces claimed to be "the same extension gate as provenance._CITATION_RE"
    and was true only for as long as nobody edited either list — the moment ORC-1 added
    mjs/cjs/mts/cts, the fix half-landed: provenance derived `.mjs` citations while rules-rot
    silently stopped recognising the same refs. One concept, one list.

    Built lazily and cached because ``_CODE_EXTS`` lives in ``provenance`` and every other
    provenance import in this module is deliberately function-local; a module-level import
    here would break that discipline for a regex used on one line.
    """
    if not _PATH_REF_RE_CACHE:
        from .provenance import _CODE_EXTS

        _PATH_REF_RE_CACHE.append(
            re.compile(
                r"^((?:[\w.-]+/)*[\w.-]+\.(?:" + "|".join(_CODE_EXTS) + r"))"
                r"(?::\d+(?:-\d+)?)?$"
            )
        )
    return _PATH_REF_RE_CACHE[0]

# A dotted-symbol ref (``module.symbol`` / ``pkg.module.symbol``): resolved conservatively —
# the module component must map to exactly ONE ``<module>.py`` in the tree, and only a
# LOCATED-module-with-MISSING-symbol is a finding (an unresolvable module is silence, never
# a cry-wolf guess).
_SYMBOL_REF_RE = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)$")


def _expand_braces(pattern: str) -> List[str]:
    """Expand one level of ``{a,b}`` alternatives (the docs' ``src/**/*.{ts,tsx}`` form);
    recursion covers multiple groups. The pattern itself when no braces."""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    head, tail = pattern[: m.start()], pattern[m.end() :]
    out: List[str] = []
    for alt in m.group(1).split(","):
        out.extend(_expand_braces(head + alt + tail))
    return out


def _glob_to_re(pattern: str) -> "re.Pattern":
    """Translate one ``paths:`` glob to a full-path regex: ``**/`` spans zero or more
    directories, ``**`` spans anything, ``*``/``?`` stay within one path segment."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif ch == "*":
            out.append(r"[^/]*")
            i += 1
        elif ch == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def rule_paths_globs(text: str) -> List[str]:
    """The ``paths:`` glob list from one ``.claude/rules`` file's YAML frontmatter
    (the harness feature RUL-0 confirmed: block or flow list, strings only).
    ``[]`` when absent/unparseable. Never raises."""
    try:
        from .provenance import parse_frontmatter

        fm = parse_frontmatter(text)
        raw = fm.get("paths")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        return [g.strip() for g in raw if isinstance(g, str) and g.strip()]
    except Exception:
        return []


def _rule_scoped_files(repo_root: str) -> List[str]:
    """Absolute paths of the ``paths:``-bearing governance files: ``.claude/rules/*.md``
    plus ``AGENTS.md`` when present (RUL-7 — the export stamps derived ``paths:`` globs
    into its frontmatter; without this widening the dead-glob leg never sees them)."""
    try:
        out = [str(p) for p in sorted(Path(repo_root).glob(".claude/rules/*.md")) if p.is_file()]
        agents = Path(repo_root) / "AGENTS.md"
        if agents.is_file():
            out.append(str(agents))
        return out
    except Exception:
        return []


def _repo_paths_for_globs(repo_root: str, repo_files: Set[str]) -> Set[str]:
    """The path universe a ``paths:`` glob may legitimately scope: tracked files UNION
    untracked-but-not-ignored ones (a rule scoping a not-yet-committed tree is alive;
    an ignored/absent tree is not). Never raises; tracked-only on any git failure."""
    paths = set(repo_files)
    try:
        import subprocess

        out = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            paths.update(ln.strip() for ln in out.stdout.splitlines() if ln.strip())
    except Exception:
        pass
    return paths


# A collapsed glob may match at most this many times the cited files it stands for;
# beyond that the derivation falls back to literal paths. The cap is the lossy-derivation
# guard RUL-6 names: a promoted/exported scope must never become a near-unscoped
# always-load because a directory happened to share an extension.
DERIVE_OVERSCOPE_FACTOR = 3


def derive_paths_globs(cited_paths: List[str], universe: Set[str]) -> tuple:
    """The RUL-6/RUL-7 shared derivation: conservative ``paths:`` globs from a memory's
    concrete ``cited_paths``.

    Rules, most-conservative-first:

    - a path absent from ``universe`` (tracked ∪ untracked-unignored) is flagged
      ``missing`` and excluded — a derived scope is born alive or not at all (the
      memory-side staleness machinery owns moved-citation findings);
    - a SINGLE citation stays a literal path, never a directory glob — a literal is a
      glob that matches exactly itself, so drift detection is per-file and exact;
    - 2+ citations sharing one directory AND one extension may collapse to
      ``<dir>/*<ext>``, but only when the collapse stays within the over-scoping cap
      (match-set ≤ ``DERIVE_OVERSCOPE_FACTOR`` × the cited files it stands for);
      over the cap → literal paths + an ``over_scope`` flag;
    - ``**`` is never emitted; collapse never crosses a directory boundary;
    - empty ``universe`` (no git oracle) → the cited paths as literals + one
      ``no_oracle`` flag (nothing to validate against — mirror ``rules_rot``'s
      no-oracle silence rather than guessing).

    Returns ``(globs, flags)``: sorted de-duplicated glob strings plus
    ``[{"kind": "missing"|"over_scope"|"no_oracle", ...}]``. Never raises.
    """
    try:
        cited = sorted({p.strip() for p in cited_paths if isinstance(p, str) and p.strip()})
        if not cited:
            return [], []
        if not universe:
            return cited, [{"kind": "no_oracle"}]
        flags: List[dict] = []
        live: List[str] = []
        for p in cited:
            if p in universe:
                live.append(p)
            else:
                flags.append({"kind": "missing", "path": p})
        groups: Dict[tuple, List[str]] = {}
        for p in live:
            d, base = os.path.split(p)
            _root, ext = os.path.splitext(base)
            groups.setdefault((d, ext), []).append(p)
        globs: Set[str] = set()
        for (d, ext), members in sorted(groups.items()):
            if len(members) < 2:
                globs.update(members)
                continue
            candidate = (d + "/" if d else "") + "*" + ext
            try:
                rx = _glob_to_re(candidate)
                matched = sum(1 for u in universe if rx.match(u))
            except Exception:
                globs.update(members)
                continue
            if matched <= DERIVE_OVERSCOPE_FACTOR * len(members):
                globs.add(candidate)
            else:
                flags.append(
                    {"kind": "over_scope", "glob": candidate,
                     "matched": matched, "cited": len(members)}
                )
                globs.update(members)
        return sorted(globs), flags
    except Exception:
        return [], [{"kind": "no_oracle"}]


def rules_rot(repo_root: str) -> dict:
    """RUL-2: citation rot + staleness applied to the rules plane itself.

    Returns::

        {
          "code_ref_rot":    [{"file", "ref", "kind": "path"|"symbol"}],
          "dead_path_globs": [{"file", "glob"}],
        }

    CODE-REF leg (feature-independent): a backtick reference in any governance file whose
    target left the tree — a path-like ref (code extension, optional ``:line`` stripped)
    absent from ``git ls-files`` (bare basenames resolve through the basename index), or a
    dotted ``module.symbol`` ref whose module resolves to exactly one ``.py`` file that no
    longer defines the symbol (``def``/``class``/module-level assignment). Unresolvable
    modules and ambiguous basenames are SILENCE, not findings — under-flag beats cry-wolf.

    PATHS-GLOB leg (RUL-0-gated, confirmed 2026-07-08): a ``.claude/rules`` file whose
    frontmatter ``paths:`` globs match NOTHING in the tree (tracked ∪ untracked-unignored)
    silently wastes its lazy-load trigger — every glob dead means the rule can never fire.
    Glob semantics mirror the documented harness feature (``**``, braces).

    Read-only; offers per-item edits by NAMING the exact file+reference — never rewrites a
    governance file (inv1/inv4). Never raises; empty findings on any failure.
    """
    empty: dict = {"code_ref_rot": [], "dead_path_globs": []}
    try:
        from .provenance import build_repo_file_index

        repo_files, basename_index = build_repo_file_index(repo_root)
        if not repo_files:
            return empty  # non-git / empty tree: no oracle, no findings

        code_rot: List[dict] = []
        module_text_cache: Dict[str, Optional[str]] = {}
        for path in gov_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            rel = _rel(repo_root, path)
            seen_refs: Set[str] = set()
            for span in _BACKTICK_SPAN_RE.findall(text):
                span = span.strip()
                if span in seen_refs:
                    continue
                pm = _path_ref_re().match(span)
                if pm:
                    token = pm.group(1)
                    if "/" in token:
                        alive = token in repo_files
                    else:
                        alive = bool(basename_index.get(token))
                    if not alive:
                        seen_refs.add(span)
                        code_rot.append({"file": rel, "ref": span, "kind": "path"})
                    continue
                sm = _SYMBOL_REF_RE.match(span)
                if sm:
                    parts = sm.group(1).split(".")
                    module_base = parts[-2] + ".py"
                    symbol = parts[-1]
                    candidates = basename_index.get(module_base) or []
                    if len(candidates) != 1:
                        continue  # unresolvable/ambiguous module: silence, not a guess
                    mod_path = candidates[0]
                    if mod_path not in module_text_cache:
                        try:
                            with open(
                                os.path.join(repo_root, mod_path), "r", encoding="utf-8"
                            ) as fh:
                                module_text_cache[mod_path] = fh.read()
                        except Exception:
                            module_text_cache[mod_path] = None
                    mod_text = module_text_cache[mod_path]
                    if mod_text is None:
                        continue
                    defined = re.search(
                        rf"(?m)^\s*(?:def|class)\s+{re.escape(symbol)}\b"
                        rf"|^{re.escape(symbol)}\s*=",
                        mod_text,
                    )
                    if not defined:
                        seen_refs.add(span)
                        code_rot.append({"file": rel, "ref": span, "kind": "symbol"})

        dead_globs: List[dict] = []
        universe: Optional[Set[str]] = None
        for path in _rule_scoped_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            globs = rule_paths_globs(text)
            if not globs:
                continue
            if universe is None:
                universe = _repo_paths_for_globs(repo_root, repo_files)
            rel = _rel(repo_root, path)
            for glob in globs:
                try:
                    matched = False
                    for g in _expand_braces(glob):
                        rx = _glob_to_re(g)
                        if any(rx.match(p) for p in universe):
                            matched = True
                            break
                except Exception:
                    continue
                if not matched:
                    dead_globs.append({"file": rel, "glob": glob})

        return {"code_ref_rot": code_rot, "dead_path_globs": dead_globs}
    except Exception:
        return empty


# --------------------------------------------------------------------------- #
# RUL-3: write-time dedup against the rules plane (preventive)
# --------------------------------------------------------------------------- #
# Containment of the draft's content tokens inside one governance BLOCK — asymmetric on
# purpose: "the draft restates a rule" means the DRAFT's substance lives in the rule, not
# that a short rule paragraph resembles a long memory. Calibrated conservative (warn-only
# surface, but a nagging false positive still costs trust).
RULE_DUP_CONTAINMENT = 0.6
# A draft below this many content tokens can be "contained" by accident; stay silent.
_RULE_DUP_MIN_DRAFT_TOKENS = 5
# Governance blocks below this many tokens (a heading, a divider) are not rules.
_RULE_DUP_MIN_BLOCK_TOKENS = 3
# Mirror LIF-2's neighbor cap: the warning stays a decision aid, not a report.
_RULE_DUP_MAX_CANDIDATES = 3
_RULE_DUP_PREVIEW_CHARS = 100


def _gov_blocks(text: str) -> List[str]:
    """Blank-line-separated blocks of one governance file's BODY (frontmatter stripped —
    ``paths:`` globs are scoping, not rule content)."""
    try:
        from .provenance import split_frontmatter

        _fm, body = split_frontmatter(text)
        return [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
    except Exception:
        return []


def rule_dup_candidates(description: str, body: str, repo_root: str) -> List[dict]:
    """RUL-3: governance blocks a draft memory RESTATES — "this duplicates rule X; link,
    don't copy."

    Every other rules item cleans up two-plane drift AFTER it happens; this is the
    preventive gate: LIF-2's write-time dup discipline extended to the GOV_GLOBS plane.
    Scores the draft's content tokens (``build_index.tokenize``, the canonical corpus
    tokenizer) against each blank-line block of each governance file by CONTAINMENT
    (``|draft ∩ block| / |draft|``): a draft whose substance already lives inside a rule
    block clears ``RULE_DUP_CONTAINMENT``. Pure token arithmetic — no model, no index, so
    it runs at write time for free even on a dense-less corpus.

    Returns ``[{"file", "score", "preview"}]`` best-first, capped at 3. Warn-only by
    contract: callers surface the candidates and the AGENT decides (link / rewrite / keep)
    — nothing here rejects a write (inv4). Never raises; ``[]`` on any failure or when the
    draft is too short to judge (< 5 content tokens).
    """
    try:
        from .build_index import tokenize

        draft = set(tokenize(f"{description}\n{body}"))
        if len(draft) < _RULE_DUP_MIN_DRAFT_TOKENS:
            return []
        out: List[dict] = []
        for path in gov_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            rel = _rel(repo_root, path)
            best_score, best_block = 0.0, ""
            for block in _gov_blocks(text):
                btoks = set(tokenize(block))
                if len(btoks) < _RULE_DUP_MIN_BLOCK_TOKENS:
                    continue
                score = len(draft & btoks) / len(draft)
                if score > best_score:
                    best_score, best_block = score, block
            if best_score >= RULE_DUP_CONTAINMENT:
                preview = " ".join(best_block.split())
                if len(preview) > _RULE_DUP_PREVIEW_CHARS:
                    preview = preview[: _RULE_DUP_PREVIEW_CHARS - 1].rstrip() + "…"
                out.append({"file": rel, "score": round(best_score, 4), "preview": preview})
        out.sort(key=lambda c: (-c["score"], c["file"]))
        return out[:_RULE_DUP_MAX_CANDIDATES]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# RUL-4: the rules side-index — a derived, gitignored cache recall can READ hot
# --------------------------------------------------------------------------- #
# Same class as links.json / stale.json: computed OFF the hot path (SessionStart), read as
# ONE small JSON on it; absent/corrupt degrades to no rules source, never a scan (inv6).
RULES_CACHE_NAME = "rules.json"
RULES_CACHE_SCHEMA = 1

# One markdown heading — a governance file splits into heading-scoped SECTIONS so a recall
# pointer can name "CLAUDE.md § Python", not just the file.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_RULE_PREVIEW_CHARS = 140


def _split_sections(text: str) -> List[tuple]:
    """``[(heading_or_None, section_text)]`` for one governance file's body (frontmatter
    stripped). A file with no headings is one section; text before the first heading is its
    own untitled section."""
    from .provenance import split_frontmatter

    _fm, body = split_frontmatter(text)
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        stripped = body.strip()
        return [(None, stripped)] if stripped else []
    sections: List[tuple] = []
    pre = body[: matches[0].start()].strip()
    if pre:
        sections.append((None, pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sec = body[m.start() : end].strip()
        if sec:
            sections.append((m.group(2).strip(), sec))
    return sections


def _gov_signatures(repo_root: str) -> List[List]:
    """``[[rel, mtime_ns, size]]`` for every governance file — the cache fast-path key."""
    sigs: List[List] = []
    for path in gov_files(repo_root):
        try:
            st = os.stat(path)
            sigs.append([_rel(repo_root, path), st.st_mtime_ns, st.st_size])
        except Exception:
            continue
    return sigs


def load_rules_cache(index_dir: Optional[str]) -> Optional[dict]:
    """The persisted rules side-index, or ``None`` (absent/corrupt/schema-mismatched —
    recall degrades to no rules source; the doctor rules-source check makes that legible).
    ONE small-JSON read; hot-path-safe; never raises."""
    try:
        if not index_dir:
            return None
        import json

        with open(os.path.join(index_dir, RULES_CACHE_NAME), "r", encoding="utf-8") as fh:
            cache = json.load(fh)
        if not isinstance(cache, dict) or cache.get("schema") != RULES_CACHE_SCHEMA:
            return None
        return cache
    except Exception:
        return None


def refresh_rules_cache(repo_root: str, index_dir: str) -> dict:
    """Build/refresh ``<index_dir>/rules.json`` over the governance plane (RUL-4).

    The heading-scoped sections of every GOV_GLOBS file, tokenized with the canonical corpus
    tokenizer — everything recall needs to score a query against the rules plane with pure
    set arithmetic (query containment; no model, no idf — the rules plane is routinely 1-5
    sections, too small for honest BM25 mass). DERIVED and REBUILDABLE: lives in the
    gitignored index dir (``ensure_self_ignoring_dir``), never committed (inv1). Signature
    fast-path: unchanged governance mtimes/sizes → no rebuild (the same cheap-no-op posture
    as ``refresh_index``). Sections edited MID-session serve their last-built preview until
    the next SessionStart — the COR-4 posture body chunks already accept. Never raises;
    returns ``{"entries": N, "built": bool}``.
    """
    try:
        import json

        from .build_index import tokenize
        from .provenance import ensure_self_ignoring_dir

        sigs = _gov_signatures(repo_root)
        cache_path = os.path.join(index_dir, RULES_CACHE_NAME)
        existing = load_rules_cache(index_dir)
        if existing is not None and existing.get("sigs") == sigs:
            return {"entries": len(existing.get("entries") or []), "built": False}

        entries: List[dict] = []
        for path in gov_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            rel = _rel(repo_root, path)
            for heading, sec_text in _split_sections(text):
                toks = tokenize(sec_text)
                if len(toks) < _RULE_DUP_MIN_BLOCK_TOKENS:
                    continue
                title = heading or os.path.basename(rel)
                # The preview is the section BODY — the title already carries the heading.
                body_text = sec_text.split("\n", 1)[1] if heading and "\n" in sec_text else (
                    "" if heading else sec_text
                )
                preview = " ".join(body_text.split()) or title
                if len(preview) > _RULE_PREVIEW_CHARS:
                    preview = preview[: _RULE_PREVIEW_CHARS - 1].rstrip() + "…"
                # tokens are stored DISTINCT — containment scoring is set arithmetic.
                entries.append(
                    {"file": rel, "title": title, "preview": preview, "tokens": sorted(set(toks))}
                )

        if not entries:
            # No governance plane: remove a stale cache rather than serve ghosts.
            try:
                os.remove(cache_path)
            except Exception:
                pass
            return {"entries": 0, "built": False}

        ensure_self_ignoring_dir(index_dir)
        cache = {"schema": RULES_CACHE_SCHEMA, "sigs": sigs, "entries": entries}
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, cache_path)
        return {"entries": len(entries), "built": True}
    except Exception:
        return {"entries": 0, "built": False}
