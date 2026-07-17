"""CLB-1: the corpus review packet — ``memory review [base..head]`` / ``hippo review``.

A teammate reviewing a memory PR has had no tooling: no op-classified diff, no
lints scoped to what actually changed, no preview of how the change shifts
recall. This module is that packet, ZERO-LLM by construction (a structural test
pins the import surface): everything below derives from git plumbing +
frontmatter — never a model, never the network.

Three sections, one pasteable markdown packet:

  1. **Operations** — each touched memory classified ADD / UPDATE / SUPERSEDE /
     ARCHIVE / EDGE purely from ``git diff --name-status``, typed-edge deltas
     (``links.parse_typed_relations`` at base vs head), and ``archive/`` moves.
     A hard delete (no ``archive/`` destination — outside hippo's convention) is
     named ``DELETE`` honestly rather than mislabeled.
  2. **Lints, scoped to the touched files** — reusing the shipped detectors
     verbatim: ``secrets.scan_text`` (entropy off — deterministic in a gate) and
     ``threat_lint.scan_tier_a`` (SEN-2) are the GATE classes; ``portability.
     scan_portability``, edge integrity (``lint_links.lint`` filtered to touched
     stems) and ``rules_plane.conflict_radar`` render as ADVISORY context. The
     advisory classes deliberately never gate: cited paths ARE repo coupling
     (portability would flag ~every project memory), and failing CI on an
     unresolved contradiction would turn a human-judgment inbox into a merge
     blocker (ED-1 — detection surfaces, humans decide).
  3. **Recall-impact preview** — LOCAL ONLY, never CI: recent episode-buffer
     previews replay against temp shadow indexes built from the corpus at base
     vs head, listing which memories would newly recall or stop recalling. The
     80-char preview bound is disclosed inline; an empty buffer prints an
     explicit "no local episodes to replay" line. The preview never runs under
     ``--ci``, ``HIPPO_DISABLE_DENSE=1``, or a CI environment.

``--ci`` is the SINGLE canonical CI scan for memory-file diffs: SEC-8's
memory-diff gate half AND SEN-2's threat-lint CI leg ride this one vehicle
(T10 shipped ``threat_lint.scan_files``-ready for exactly this, deliberately
not forking a second CI surface). The pre-existing ``secret-scan`` job in
ci.yml is SEC-8's OTHER half — release hygiene over the whole shipped tree
(source + packs + docs) — a different scope, not a second memory scanner.
Exit is nonzero iff a GATE finding exists on a touched file.

The identity pillar: this module contains no approval automation and no
posting path — the packet is printed, and the human merges. (Posting the
packet as a PR comment is explicitly out of scope, gated behind the trust
spine as a future, separately-reviewed behavior.)
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

from .provenance import parse_frontmatter, resolve_dirs, run_git, split_frontmatter

# The op vocabulary (hippo's canonical edges + archive moves; Mem0's
# ADD/UPDATE/DELETE taxonomy is a mapping reference only, not the vocabulary).
OPS = ("ADD", "UPDATE", "SUPERSEDE", "ARCHIVE", "EDGE", "DELETE")

# The only lint classes whose findings flip --ci's exit nonzero. Everything else
# is advisory context — see the module docstring for why this is deliberate.
GATE_LINTS = ("secrets", "threat")

_ARCHIVE_SEGMENT = "archive"
# Bounded packet: replayed previews and rendered advisory lines are capped so a
# giant range still produces a readable, pasteable packet.
_MAX_REPLAY_QUERIES = 10
_MAX_ADVISORY_LINES = 12
# A line that is nothing but wikilink scaffolding once [[targets]] are removed —
# "Related: [[x]], [[y]]" style — is edge surface, not body content.
_EDGE_ONLY_LINE_RE = re.compile(r"\s*(related:?\s*)?[·•,;\s]*", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Range + touched-file resolution
# --------------------------------------------------------------------------- #
def _git_top(repo_root: str) -> str:
    """The repo toplevel — ``git diff``/``show`` paths are toplevel-relative."""
    top = run_git(["rev-parse", "--show-toplevel"], repo_root).strip()
    return top or repo_root


def _memory_rel_prefix(memory_dir: str, top: str) -> Optional[str]:
    """Toplevel-relative corpus prefix (normally ``.claude/memory``); None if outside."""
    try:
        rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(top))
        if rel.startswith(".."):
            return None
        return rel.replace(os.sep, "/")
    except Exception:
        return None


def _resolve_range(range_expr: Optional[str], top: str) -> Tuple[str, Optional[str], str]:
    """``(base_ref, head_ref, diff_expr)`` — ``head_ref is None`` means working tree.

    Accepted forms mirror ``recall --for-diff``: ``A..B``, ``A...B`` (base becomes
    the merge-base, matching git's own three-dot diff semantics), a single ref
    (meaning ``ref..HEAD``), or nothing — the solo default, working-tree-vs-HEAD.
    """
    if not range_expr:
        return "HEAD", None, "HEAD"
    if "..." in range_expr:
        a, b = range_expr.split("...", 1)
        a, b = (a.strip() or "HEAD"), (b.strip() or "HEAD")
        mb = run_git(["merge-base", a, b], top).strip()
        return (mb or a), b, range_expr
    if ".." in range_expr:
        a, b = range_expr.split("..", 1)
        return (a.strip() or "HEAD"), (b.strip() or "HEAD"), range_expr
    return range_expr, "HEAD", f"{range_expr}..HEAD"


def _untracked_md(top: str, prefix: str) -> List[str]:
    """Untracked (non-ignored) ``.md`` paths under the corpus prefix, toplevel-relative.

    ``git status --porcelain`` reports a fully-untracked directory as one ``dir/``
    row — expand it by walking, bounded to the corpus dir. Gitignored files stay
    invisible on purpose: they are private by declaration, not review material.
    """
    out: List[str] = []
    for line in run_git(["status", "--porcelain", "--", prefix], top).splitlines():
        if not line.startswith("?? "):
            continue
        p = line[3:].strip().strip('"')
        if p.endswith("/"):
            base = os.path.join(top, p)
            for root, _dirs, files in os.walk(base):
                for fname in sorted(files):
                    if fname.endswith(".md"):
                        rel = os.path.relpath(os.path.join(root, fname), top)
                        out.append(rel.replace(os.sep, "/"))
        elif p.endswith(".md"):
            out.append(p)
    return sorted(out)


def _name_status(
    diff_expr: str, head_ref: Optional[str], top: str, prefix: str
) -> List[Tuple[str, str, Optional[str]]]:
    """``[(status, path, old_path)]`` for ``.md`` files under the corpus prefix.

    Status is the first letter of git's name-status (A/M/D/R/C — rename/copy rows
    carry their source in ``old_path``). Working-tree mode (``head_ref is None``)
    unions untracked non-ignored ``.md`` files in as ``A`` rows.
    """
    entries: List[Tuple[str, str, Optional[str]]] = []
    out = run_git(["diff", "--name-status", "-M", diff_expr, "--", prefix], top)
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st = parts[0].strip()
        if st[:1] in ("R", "C") and len(parts) >= 3:
            entries.append((st[0], parts[2].strip(), parts[1].strip()))
        elif st[:1] in ("A", "M", "D"):
            entries.append((st[0], parts[1].strip(), None))
    if head_ref is None:
        seen = {p for _st, p, _old in entries}
        entries.extend(("A", p, None) for p in _untracked_md(top, prefix) if p not in seen)
    return sorted(
        (e for e in entries if e[1].endswith(".md")), key=lambda e: (e[1], e[0])
    )


def _text_at(ref: Optional[str], path: str, top: str) -> str:
    """File content at ``ref`` (``git show``), or from the working tree when None."""
    if ref is None:
        try:
            with open(os.path.join(top, path), "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""
    return run_git(["show", f"{ref}:{path}"], top)


# --------------------------------------------------------------------------- #
# Op classification — frontmatter/edges/archive-moves only, zero LLM
# --------------------------------------------------------------------------- #
def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _in_archive(path: str, prefix: str) -> bool:
    return path.startswith(f"{prefix}/{_ARCHIVE_SEGMENT}/")


def _typed_edges(text: str) -> Dict[str, List[str]]:
    from .links import parse_typed_relations

    return parse_typed_relations(parse_frontmatter(text))


def _non_edge_view(text: str) -> Tuple[str, str]:
    """A comparable (frontmatter-minus-edges, body-minus-links) view of a memory.

    Two texts with equal views differ ONLY in edge surface — typed relations,
    ``[[wikilinks]]``, and link-scaffolding lines — which is the EDGE op's exact
    definition. Whitespace-normalized so a reflowed paragraph still compares equal.
    """
    from .links import TYPED_RELATIONS

    fm = parse_frontmatter(text)
    fm = {k: v for k, v in fm.items() if k not in TYPED_RELATIONS}
    meta = fm.get("metadata")
    if isinstance(meta, dict):
        fm["metadata"] = {k: v for k, v in meta.items() if k not in TYPED_RELATIONS}
    _fm_lines, body = split_frontmatter(text)
    body = re.sub(r"\[\[[^\]]+\]\]", " ", body or "")
    kept = [ln for ln in body.splitlines() if not _EDGE_ONLY_LINE_RE.fullmatch(ln)]
    return repr(sorted(fm.items(), key=lambda kv: kv[0])), " ".join(" ".join(kept).split())


def classify_ops(
    entries: List[Tuple[str, str, Optional[str]]],
    base_ref: str,
    head_ref: Optional[str],
    top: str,
    prefix: str,
) -> List[dict]:
    """``[{"stem", "op", "path", "detail"}]`` — one row per touched memory.

    Derivation is purely mechanical: name-status + archive/ path membership +
    typed-edge deltas between the base and head frontmatter. Precedence per file:
    ARCHIVE (a move) > SUPERSEDE (a gained ``supersedes``) > ADD (new file) >
    EDGE (only edge surface changed) > UPDATE (anything else) — and DELETE for
    the convention-breaking hard-delete, named rather than hidden.
    """
    archive_adds = {
        _stem(p): p for st, p, _old in entries if st == "A" and _in_archive(p, prefix)
    }
    live_deletes = {
        _stem(p): p for st, p, _old in entries if st == "D" and not _in_archive(p, prefix)
    }
    items: List[dict] = []
    for st, path, old in entries:
        stem = _stem(path)
        if st in ("R", "C"):
            if _in_archive(path, prefix) and not (old and _in_archive(old, prefix)):
                items.append(
                    {"stem": stem, "op": "ARCHIVE", "path": path,
                     "detail": "moved to archive/ (retirement, reversible via restore)"}
                )
            else:
                items.append(
                    {"stem": stem, "op": "ADD", "path": path,
                     "detail": f"renamed from {_stem(old or '?')}"}
                )
        elif st == "A":
            if _in_archive(path, prefix):
                if stem in live_deletes:
                    items.append(
                        {"stem": stem, "op": "ARCHIVE", "path": path,
                         "detail": "moved to archive/ (delete+add pair)"}
                    )
                else:
                    items.append(
                        {"stem": stem, "op": "ARCHIVE", "path": path,
                         "detail": "arrived already archived"}
                    )
            else:
                head_text = _text_at(head_ref, path, top)
                sup = _typed_edges(head_text).get("supersedes")
                if sup:
                    items.append(
                        {"stem": stem, "op": "SUPERSEDE", "path": path,
                         "detail": f"new memory supersedes {', '.join(sup)}"}
                    )
                else:
                    items.append(
                        {"stem": stem, "op": "ADD", "path": path, "detail": "new memory"}
                    )
        elif st == "D":
            if stem in archive_adds:
                continue  # the archive-side A row already classified this move
            items.append(
                {"stem": stem, "op": "DELETE", "path": path,
                 "detail": "deleted outright — no archive/ move; hippo's convention "
                           "is archive/ (reversible), not deletion"}
            )
        elif st == "M":
            base_text = _text_at(base_ref, path, top)
            head_text = _text_at(head_ref, path, top)
            base_edges, head_edges = _typed_edges(base_text), _typed_edges(head_text)
            gained_sup = sorted(
                set(head_edges.get("supersedes") or []) - set(base_edges.get("supersedes") or [])
            )
            if gained_sup:
                items.append(
                    {"stem": stem, "op": "SUPERSEDE", "path": path,
                     "detail": f"gained supersedes -> {', '.join(gained_sup)}"}
                )
            elif base_edges != head_edges or _wikilinks(base_text) != _wikilinks(head_text):
                if _non_edge_view(base_text) == _non_edge_view(head_text):
                    items.append(
                        {"stem": stem, "op": "EDGE", "path": path,
                         "detail": _edge_delta_detail(base_text, head_text)}
                    )
                else:
                    items.append(
                        {"stem": stem, "op": "UPDATE", "path": path,
                         "detail": "content + edges changed"}
                    )
            else:
                items.append(
                    {"stem": stem, "op": "UPDATE", "path": path, "detail": "content changed"}
                )
    return items


def _wikilinks(text: str) -> frozenset:
    from .links import parse_wikilinks

    return frozenset(parse_wikilinks(text))


def _edge_delta_detail(base_text: str, head_text: str) -> str:
    """One human line naming what edge surface moved (typed deltas + link count)."""
    from .links import TYPED_RELATIONS

    base_e, head_e = _typed_edges(base_text), _typed_edges(head_text)
    parts: List[str] = []
    for rel in TYPED_RELATIONS:
        gained = sorted(set(head_e.get(rel) or []) - set(base_e.get(rel) or []))
        lost = sorted(set(base_e.get(rel) or []) - set(head_e.get(rel) or []))
        if gained:
            parts.append(f"+{rel} {', '.join(gained)}")
        if lost:
            parts.append(f"-{rel} {', '.join(lost)}")
    delta_links = len(_wikilinks(head_text)) - len(_wikilinks(base_text))
    if delta_links:
        parts.append(f"{delta_links:+d} wikilink(s)")
    return "edges only: " + ("; ".join(parts) or "edge surface reshuffled")


# --------------------------------------------------------------------------- #
# Touched-file-scoped lints — the shipped detectors, reused verbatim
# --------------------------------------------------------------------------- #
def lint_touched(
    texts: Dict[str, str], memory_dir: str, repo_root: str
) -> Dict[str, List[dict]]:
    """``{"gate": [...], "advisory": [...]}`` findings over the touched stems.

    Per-file text lints run on the HEAD-side content of each touched memory;
    the corpus-level lints (edge integrity, conflict radar) evaluate the LIVE
    corpus and are filtered down to the touched stems. Each finding is
    ``{"stem", "lint", "finding"}`` — the KIND of a secret is reported, never
    the secret itself (``scan_text``'s own contract).
    """
    gate: List[dict] = []
    advisory: List[dict] = []
    from .portability import scan_portability
    from .secrets import scan_text
    from .threat_lint import scan_tier_a

    for stem in sorted(texts):
        text = texts[stem]
        for kind in scan_text(text, entropy=False):
            gate.append({"stem": stem, "lint": "secrets", "finding": kind})
        for warning in scan_tier_a(text):
            gate.append({"stem": stem, "lint": "threat", "finding": warning})
        for f in scan_portability(text):
            advisory.append(
                {"stem": stem, "lint": "portability",
                 "finding": f"{f.get('kind')}: {f.get('detail')}"}
            )
    touched = set(texts)
    try:
        from .lint_links import lint as _lint_links

        report = _lint_links(memory_dir)
        for row in report.get("dangling", []) or []:
            if _stem(str(row.get("file", ""))) in touched:
                advisory.append(
                    {"stem": _stem(str(row.get("file", ""))), "lint": "edges",
                     "finding": f"dangling [[{row.get('target')}]]"}
                )
        for row in report.get("typed_dangling", []) or []:
            if _stem(str(row.get("file", ""))) in touched:
                advisory.append(
                    {"stem": _stem(str(row.get("file", ""))), "lint": "edges",
                     "finding": f"{row.get('relation')} -> {row.get('target')} (dangling)"}
                )
        for row in report.get("ambiguous", []) or []:
            if _stem(str(row.get("file", ""))) in touched:
                advisory.append(
                    {"stem": _stem(str(row.get("file", ""))), "lint": "edges",
                     "finding": f"ambiguous [[{row.get('target')}]]"}
                )
    except Exception:
        pass
    try:
        from .rules_plane import conflict_radar

        radar = conflict_radar(memory_dir, repo_root)
        for row in radar.get("edge_conflicts", []) or []:
            if row.get("name") in touched or row.get("by") in touched:
                advisory.append(
                    {"stem": str(row.get("name")), "lint": "conflicts",
                     "finding": f"{row.get('relation')} by {row.get('by')} "
                                f"(cited by {row.get('cited_by')})"}
                )
    except Exception:
        pass
    return {"gate": gate, "advisory": advisory}


# --------------------------------------------------------------------------- #
# Recall-impact preview — LOCAL ONLY (the guard lives in run(), not here)
# --------------------------------------------------------------------------- #
def _recent_previews(telemetry_dir: Optional[str], cap: int = _MAX_REPLAY_QUERIES) -> List[str]:
    """The newest ``cap`` DISTINCT episode query previews, newest first."""
    from .telemetry import read_episodes

    episodes = list(read_episodes(telemetry_dir))
    seen: set = set()
    out: List[str] = []
    for e in reversed(episodes):
        q = str(e.get("query_preview") or "").strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
        if len(out) >= cap:
            break
    return out


def _extract_corpus_at_ref(top: str, ref: str, prefix: str, dest: str) -> int:
    """Materialize the corpus AT ``ref`` into ``dest`` (temp shadow files)."""
    count = 0
    names = run_git(["ls-tree", "-r", "--name-only", ref, "--", prefix], top)
    for name in names.splitlines():
        name = name.strip()
        if not name.endswith(".md"):
            continue
        text = run_git(["show", f"{ref}:{name}"], top)
        if not text:
            continue
        target = os.path.join(dest, os.path.relpath(name, prefix))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        count += 1
    return count


def _copy_worktree_corpus(top: str, prefix: str, dest: str) -> int:
    """Materialize the git-visible working-tree corpus (tracked + untracked
    non-ignored ``.md``) into ``dest`` — the same visibility rule the op list
    uses, so the head shadow index and the packet describe the same corpus."""
    count = 0
    tracked = run_git(["ls-files", "--", prefix], top).splitlines()
    for name in sorted({*(_p.strip() for _p in tracked), *_untracked_md(top, prefix)}):
        if not name.endswith(".md"):
            continue
        src = os.path.join(top, name)
        try:
            with open(src, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        target = os.path.join(dest, os.path.relpath(name, prefix))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        count += 1
    return count


def replay_previews(
    memory_dir: str,
    repo_root: str,
    *,
    base_ref: str,
    head_ref: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    k: Optional[int] = None,
) -> Optional[dict]:
    """Replay recent episode previews against base-vs-head shadow indexes.

    Returns ``None`` when the episode buffer is empty (the caller prints the
    explicit "no local episodes to replay" line — checked BEFORE any index
    work, so an empty buffer costs nothing). Otherwise ``{"deltas": [{"query",
    "newly", "gone"}], "replayed", "disclosure", "counts"}``. Shadow corpora
    live in a TemporaryDirectory; indexes are built fresh and thrown away —
    derived, gitignored-standing state, never the real index. ``recall`` is
    invoked with a preloaded ``index=`` so tier fusion and the trust gate stay
    out of the comparison (a pure corpus A/B, not a live-recall simulation).
    """
    from .telemetry import _QUERY_PREVIEW_CHARS, default_telemetry_dir

    td = telemetry_dir or default_telemetry_dir(memory_dir)
    previews = _recent_previews(td)
    if not previews:
        return None
    from .build_index import build_index, load_index
    from .recall import DEFAULT_K, recall

    k = k or DEFAULT_K
    top = _git_top(repo_root)
    prefix = _memory_rel_prefix(memory_dir, top) or ".claude/memory"
    with tempfile.TemporaryDirectory(prefix="hippo-review-shadow-") as tmp:
        base_dir, head_dir = os.path.join(tmp, "base"), os.path.join(tmp, "head")
        os.makedirs(base_dir), os.makedirs(head_dir)
        n_base = _extract_corpus_at_ref(top, base_ref, prefix, base_dir)
        if head_ref is None:
            n_head = _copy_worktree_corpus(top, prefix, head_dir)
        else:
            n_head = _extract_corpus_at_ref(top, head_ref, prefix, head_dir)
        base_idx, head_idx = os.path.join(tmp, "idx-base"), os.path.join(tmp, "idx-head")
        try:
            build_index(base_dir, base_idx)
            build_index(head_dir, head_idx)
            loaded_base, loaded_head = load_index(base_idx), load_index(head_idx)
        except Exception:
            loaded_base = loaded_head = None
        deltas: List[dict] = []
        for q in previews:
            base_names = (
                {r["name"] for r in recall(q, k, index=loaded_base)} if loaded_base else set()
            )
            head_names = (
                {r["name"] for r in recall(q, k, index=loaded_head)} if loaded_head else set()
            )
            deltas.append(
                {"query": q, "newly": sorted(head_names - base_names),
                 "gone": sorted(base_names - head_names)}
            )
    return {
        "deltas": deltas,
        "replayed": len(previews),
        "disclosure": (
            f"replayed {len(previews)} episode preview(s); the episode buffer stores "
            f"{_QUERY_PREVIEW_CHARS}-char query prefixes, so deltas are directional, "
            "not exact"
        ),
        "counts": {"base": n_base, "head": n_head},
    }


# --------------------------------------------------------------------------- #
# The packet
# --------------------------------------------------------------------------- #
def _render_ops(items: List[dict]) -> List[str]:
    lines = [f"### operations ({len(items)} touched)"]
    order = {op: i for i, op in enumerate(OPS)}
    for item in sorted(items, key=lambda d: (order.get(d["op"], 99), d["stem"])):
        lines.append(f"- {item['op']} `{item['stem']}` — {item['detail']}")
    return lines


def _render_lints(lints: Dict[str, List[dict]]) -> List[str]:
    lines = ["### lints (touched files only)"]
    if lints["gate"]:
        lines.append("GATE findings — these fail `--ci` (secret / threat Tier-A):")
        for f in lints["gate"]:
            lines.append(f"- `{f['stem']}`: {f['lint']} — {f['finding']}")
    else:
        lines.append("no gate findings (secrets / threat Tier-A clean).")
    if lints["advisory"]:
        lines.append("advisory (context for the reviewer; never a gate):")
        for f in lints["advisory"][:_MAX_ADVISORY_LINES]:
            lines.append(f"- `{f['stem']}`: {f['lint']} — {f['finding']}")
        overflow = len(lints["advisory"]) - _MAX_ADVISORY_LINES
        if overflow > 0:
            lines.append(f"- … and {overflow} more advisory finding(s)")
    return lines


def _render_preview(report: Optional[dict]) -> List[str]:
    lines = ["### recall-impact preview (local only — never CI)"]
    if report is None:
        lines.append(
            "no local episodes to replay — the preview needs this machine's episode "
            "buffer (it fills as you use recall)."
        )
        return lines
    changed = [d for d in report["deltas"] if d["newly"] or d["gone"]]
    if not changed:
        lines.append(
            f"no recall changes across {report['replayed']} replayed preview(s)."
        )
    for d in changed:
        moves = []
        if d["newly"]:
            moves.append(f"newly recalls {', '.join(d['newly'])}")
        if d["gone"]:
            moves.append(f"no longer recalls {', '.join(d['gone'])}")
        lines.append(f"- \"{d['query']}\": {'; '.join(moves)}")
    lines.append(f"({report['disclosure']})")
    return lines


def run(
    argv: Optional[List[str]] = None,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Tuple[int, str]:
    """Build the packet; return ``(exit_code, packet_text)``.

    Exit is nonzero ONLY in ``--ci`` and ONLY when a gate finding exists —
    ``iff`` in both directions, the CLB-1 acceptance's own wording.
    """
    args = _parse(argv)
    md, repo = resolve_dirs()
    md = memory_dir or args.memory_dir or md
    repo = repo_root or args.repo_root or repo
    top = _git_top(repo)
    prefix = _memory_rel_prefix(md, top) or ".claude/memory"
    base_ref, head_ref, diff_expr = _resolve_range(args.range, top)

    scope = args.range or "working tree vs HEAD"
    header = [f"## memory review — {scope}"]
    entries = _name_status(diff_expr, head_ref, top, prefix)
    if not entries:
        header.append("")
        header.append("no memory changes in this range — nothing to review.")
        return 0, "\n".join(header)

    items = classify_ops(entries, base_ref, head_ref, top, prefix)
    texts = {
        item["stem"]: _text_at(head_ref, item["path"], top)
        for item in items
        if item["op"] != "DELETE"
    }
    texts = {stem: text for stem, text in texts.items() if text}
    lints = lint_touched(texts, md, repo)

    sections: List[str] = header + [""] + _render_ops(items) + [""] + _render_lints(lints)
    if not args.ci:
        sections.append("")
        if _dense_disabled():
            sections.append(
                "recall-impact preview skipped — HIPPO_DISABLE_DENSE=1 (the preview "
                "is a local-only feature)."
            )
        elif os.environ.get("CI"):
            sections.append(
                "recall-impact preview skipped — CI environment (the preview is a "
                "local-only feature)."
            )
        else:
            report = replay_previews(md, repo, base_ref=base_ref, head_ref=head_ref)
            sections.extend(_render_preview(report))
    sections.append("")
    sections.append(
        "the human merges: this packet is review material, not an approval — "
        "nothing here can accept, merge, or post anything."
    )
    exit_code = 1 if (args.ci and lints["gate"]) else 0
    return exit_code, "\n".join(sections)


def _dense_disabled() -> bool:
    from .build_index import dense_disabled

    return dense_disabled()


def _parse(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory review",
        description="Corpus review packet: op-classified memory diff + touched-file "
        "lints + a local-only recall-impact preview. --ci is the single canonical "
        "CI scan for memory diffs (SEC-8 gate half + SEN-2 threat leg): exit 1 iff "
        "a secret/threat finding exists on a touched file.",
    )
    parser.add_argument(
        "range",
        nargs="?",
        default=None,
        help="git range (A..B, A...B, or a single ref meaning ref..HEAD); "
        "omit for working-tree-vs-HEAD (the solo default)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="gate mode: lints only (no preview), exit 1 iff a secret/threat "
        "finding exists on a touched memory file",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    code, text = run(argv)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
