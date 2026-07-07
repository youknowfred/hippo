"""Graceful decay — the git-reversible archive primitive (Tier 3, memory-organism
instrument-immunize roadmap).

Decay is DEMOTION, never deletion. ``archive_candidates()`` is a REPORT over the 4-WAY
INTERSECTION — withheld entirely until the ``>=5``-distinct-session curation-soak bar is
met, and excluding memories younger than that soak window (LIF-4: the cold signal below is
untrustworthy pre-soak and vacuous for a memory that hasn't been around long enough to be
recalled):
  - **cold**          — never recalled in the telemetry ledger UNIONED with the
                        rotation-surviving usage aggregates (``soak.curation_report``'s
                        ``never_recalled`` set)
  - **stale**          — cites code that has drifted (``staleness.find_stale``)
  - **zero-inbound**   — no OTHER memory ``[[wikilinks]]`` to it (via
                        ``links.LinkGraph.inbound()`` — distinct from
                        ``LinkGraph.orphans()``, which is zero-OUTBOUND)
  - **not-cited-by-CLAUDE.md** — the memory's filename is not referenced (backtick-quoted,
                        with or without ``.md``) anywhere across the project's instruction
                        surface: ``CLAUDE.md``, ``.claude/rules/*.md``, ``.claude/agents/*.md``,
                        ``.claude/skills/*.md``, ``docs/prompts/*.md``

``archive_memory(name)`` is the per-item write primitive: a single ``git mv`` into
``.claude/memory/archive/`` (a tracked, non-recursive subdir ``_iter_memory_files`` already
skips — the memory instantly drops from index/recall/staleness with no code change
elsewhere, and the move is fully git-reversible: ``git mv`` it back). GRA-5: the primitive
carries its own inbound guard — while any OTHER memory still references the target (untyped
``[[wikilinks]]`` UNIONED with GRA-4 typed inbound edges, via the one canonical
``LinkGraph.inbound()``/``typed_inbound()`` API), the move REFUSES with the referrer list
unless ``force=True``, because archiving a referenced memory instantly converts every
inbound link into dangling rot. There is deliberately NO batch/list parameter on either
function — an autonomous bulk sweep is exactly the failure mode this primitive must never
enable (mirrors ``reverify_head_only_no_bulk``). REPORT-then-move, per-item, gated by the
memory-master agent. Never deletes. Never raises.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, List, Optional, Set, Tuple

from .links import TYPED_RELATIONS, build_graph
from .provenance import _iter_memory_files, run_git
from .soak import SOAK_GATE_SESSIONS, curation_report, soak_status
from .staleness import find_stale
from .telemetry import read_events, read_usage_aggregates

_ARCHIVE_SUBDIR = "archive"

# A backtick-quoted token, with an OPTIONAL trailing ".md" -- confirmed empirically against
# the live repo: CLAUDE.md cites memory files BOTH with the suffix (`formula_graph_*.md`) AND
# without it anywhere nearby (`feedback_no_backward_compat` in rules/20-patterns.md). A regex
# anchored on a mandatory ".md" silently misses the latter, a real false-negative class.
_BACKTICK_TOKEN_RE = re.compile(r"`([A-Za-z0-9_-]+(?:\.md)?)`")

# The project's full instruction surface -- CLAUDE.md itself delegates authority into agents/
# ("Proactive invocation required") and mandates docs/prompts/evergreen-prompt-library.md
# updates (CRITICAL RULES); both cite real corpus memories nowhere near CLAUDE.md/rules/*.md.
_SCAN_TARGETS = (
    "CLAUDE.md",
    ".claude/rules",
    ".claude/agents",
    ".claude/skills",
    "docs/prompts",
)


def _corpus_names(memory_dir: str) -> Set[str]:
    try:
        return {os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)}
    except Exception:
        return set()


def _zero_inbound_names(memory_dir: str) -> Set[str]:
    """Memory stems with NO inbound ``[[wikilink]]`` from any OTHER memory.

    Distinct from ``links.LinkGraph.orphans()`` (zero OUTBOUND). Since GRA-2 this is a
    thin join over ``LinkGraph.inbound()`` — the graph builds its reverse adjacency once
    in ``_build()`` and keys everything by stem, so no local inversion or ``.md``-stripping
    remains here (one adjacency inversion in the codebase, by design). Never raises;
    ``set()`` on any failure (fails toward "has inbound", i.e. NOT a candidate — the safe
    direction for an archive gate, since this is one of four ANDed conditions).
    """
    try:
        g = build_graph(memory_dir)
        if g is None:
            return set()
        return {stem for stem in g.files if not g.inbound(stem)}
    except Exception:
        return set()


def _inbound_referrers(stem: str, memory_dir: str) -> Optional[List[str]]:
    """Sorted stems that still reference ``stem`` — untyped ``[[wikilinks]]`` UNIONED with
    GRA-4 typed inbound edges (a memory named by ``supersedes``/``contradicts``/``refines``
    is just as referenced as a wikilinked one; archiving it dangles those edges identically).

    The ONE canonical graph API (``links.build_graph`` → ``LinkGraph.inbound()`` /
    ``typed_inbound()``, GRA-2) — never a local adjacency inversion. Returns ``None`` —
    distinct from ``[]`` — when the graph cannot be built at all, so the caller can fail
    CLOSED (refuse absent ``force``): the same "fails toward has-inbound" direction
    ``_zero_inbound_names`` documents. Never raises.
    """
    try:
        g = build_graph(memory_dir)
        if g is None:
            return None
        refs: Set[str] = set(g.inbound(stem))
        for rel in TYPED_RELATIONS:
            refs |= g.typed_inbound(stem, rel)
        return sorted(refs)
    except Exception:
        return None


def _scan_files(repo_root: str) -> List[str]:
    """Resolve the project's instruction-surface files to scan for citations. Never raises."""
    out: List[str] = []
    for rel in _SCAN_TARGETS:
        full = os.path.join(repo_root, rel)
        try:
            if os.path.isfile(full):
                out.append(full)
            elif os.path.isdir(full):
                for fname in sorted(os.listdir(full)):
                    if fname.endswith(".md"):
                        out.append(os.path.join(full, fname))
        except Exception:
            continue
    return out


def _cited_by_claude_md_names(
    repo_root: str, corpus_names: Set[str], *, unreadable: Optional[List[str]] = None
) -> Set[str]:
    """Corpus memory names cited (backtick-quoted, with/without ``.md``) anywhere across the
    project's instruction surface.

    Fresh-read-per-call — no cross-call cache, mirroring ``staleness.py``/``lint_links.py``'s
    minimal-I/O convention at this corpus's scale (under a dozen scan-target files). Matches
    via backtick/code-span-anchored token extraction, NOT bare substring containment — a real
    collision exists in this corpus today (``"MEMORY"`` is a substring of ``"MEMORY.full"``).
    Never raises; on a total scan failure returns the FULL ``corpus_names`` (fail CLOSED to
    "cited" — the safe direction: an unreadable scan target must never let the gate
    conclude "not cited" for everything). A PER-FILE read failure fails closed the same
    way — an unreadable instruction-surface file could have cited anything, so it must never
    silently cause its would-be citations to read as "not cited"; ``unreadable`` (if passed)
    collects the paths that failed so callers can surface the degradation.
    """
    try:
        cited: Set[str] = set()
        for path in _scan_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                if unreadable is not None:
                    unreadable.append(path)
                return set(corpus_names)
            for tok in _BACKTICK_TOKEN_RE.findall(text):
                stem = tok[:-3] if tok.endswith(".md") else tok
                if stem in corpus_names:
                    cited.add(stem)
        return cited
    except Exception:
        return set(corpus_names)


# Marks each commit's timestamp line in the first-seen git-log scan (mirrors
# staleness._CHANGE_MARKER's parsing pattern; a distinct token to avoid any collision).
_FIRST_SEEN_MARKER = "__A__"


def _first_seen_times(memory_dir: str, repo_root: str) -> Dict[str, int]:
    """Memory stem -> unix time of the commit that FIRST ADDED ``<stem>.md``.

    "First seen" is derived from GIT — markdown-in-git is the single source of authority,
    and the add-commit is the authoritative creation record — NOT from the
    aggregates/ledger: those only know when a memory was first RECALLED, which is exactly
    the evidence a cold memory doesn't have. One bounded, timeout-guarded git call for the
    whole corpus (``git log --diff-filter=A --name-only`` scoped to the memory dir; the
    ``:/`` magic prefix anchors the pathspec to the git toplevel so a monorepo-subdir
    ``repo_root`` still matches, mirroring ``staleness._run_git_log_scoped``). The log is
    newest-first, so the LAST occurrence of a path wins — the oldest add for a
    deleted-then-re-added file. Only ``.md`` files DIRECTLY in the memory dir are mapped
    (never ``archive/`` entries, whose stems could shadow a live memory). A name absent
    from the result (untracked file, rename that reset add-history, non-git dir, git
    failure) reads as "first seen unknown" and the caller must fail toward NOT-a-candidate.
    Never raises; ``{}`` on any failure.
    """
    try:
        toplevel = run_git(["rev-parse", "--show-toplevel"], repo_root).strip()
        if not toplevel:
            return {}
        rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(toplevel))
        if rel.startswith(".."):
            return {}
        rel_posix = rel.replace(os.sep, "/")
        prefix = "" if rel_posix == "." else rel_posix + "/"
        pathspec = ":/" if rel_posix == "." else f":/{rel_posix}"
        log = run_git(
            ["log", "--diff-filter=A", f"--format={_FIRST_SEEN_MARKER}%ct", "--name-only",
             "--", pathspec],
            repo_root,
        )
        out: Dict[str, int] = {}
        cur: Optional[int] = None
        for line in log.split("\n"):
            if line.startswith(_FIRST_SEEN_MARKER):
                try:
                    cur = int(line[len(_FIRST_SEEN_MARKER):] or 0)
                except ValueError:
                    cur = None
            elif line.strip() and cur is not None:
                stem_part = line[len(prefix):]
                if line.startswith(prefix) and stem_part.endswith(".md") and "/" not in stem_part:
                    out[stem_part[:-3]] = cur  # overwrite: newest-first log -> oldest add wins
        return out
    except Exception:
        return {}


def _young_names(
    names: Set[str], memory_dir: str, repo_root: str, telemetry_dir: Optional[str]
) -> Set[str]:
    """Subset of ``names`` YOUNGER THAN THE SOAK WINDOW — not yet exposed to
    ``soak.SOAK_GATE_SESSIONS`` distinct recall-logging sessions since the memory first
    existed, so its never-recalled coldness is indistinguishable from youth.

    Exposure = distinct ledger sessions whose events are timestamped at/after the memory's
    git first-seen time (``_first_seen_times``), plus — only for a memory that predates the
    aggregates' ENTIRE observation span (``sessions.first_ts``) — the sessions the ledger
    already rotated away (aggregate count minus retained ledger count). A memory created
    mid-history can't prove exposure to rotated-away sessions, so it gets no credit for
    them: under-crediting fails toward exclusion, the safe direction for an archive gate.
    Unknown first-seen also counts as young for the same reason. Never raises; on total
    failure EVERY name is young (candidates shrink, never inflate).
    """
    try:
        first_seen = _first_seen_times(memory_dir, repo_root)
        events: List[Tuple[float, str]] = []
        for e in read_events(telemetry_dir):
            ts = e.get("ts")
            sid = e.get("session_id")
            if (
                isinstance(ts, (int, float))
                and not isinstance(ts, bool)
                and isinstance(sid, str)
                and sid
            ):
                events.append((float(ts), sid))
        agg_sessions = read_usage_aggregates(telemetry_dir)["sessions"]
        agg_first_ts = agg_sessions["first_ts"]
        rotated_away = max(0, agg_sessions["count"] - len({sid for _ts, sid in events}))
        young: Set[str] = set()
        for name in names:
            born = first_seen.get(name)
            if born is None:
                young.add(name)
                continue
            exposed = len({sid for ts, sid in events if ts >= born})
            if rotated_away and agg_first_ts is not None and born <= agg_first_ts:
                exposed += rotated_away
            if exposed < SOAK_GATE_SESSIONS:
                young.add(name)
        return young
    except Exception:
        return set(names)


def archive_candidates(
    memory_dir: str,
    repo_root: str,
    telemetry_dir: Optional[str] = None,
    *,
    since: Optional[str] = None,
    diagnostics: Optional[dict] = None,
) -> List[dict]:
    """REPORT (never autonomous) — the 4-way intersection: cold ∧ stale ∧ zero-inbound ∧
    not-cited-by-CLAUDE.md, gated by the curation-soak bar (LIF-4).

    Returns ``[{"name", "changed_paths", "citation_scan_unreadable"}]`` (every candidate is
    necessarily stale, so the stale-set shape is reused), most-recently-drifted first.
    ``citation_scan_unreadable`` is the list of instruction-surface paths (if any) that
    could not be read during the citation scan — attached to EVERY item regardless of
    whether that item itself survived the citation gate, so a fail-closed degradation is
    never silent even though it can only ever shrink (never inflate) the candidate set
    (guiding invariant: legible degradation). ``since`` passes through to ``find_stale``
    (its own default when omitted) — exposed so hermetic tests can widen the
    wall-clock-relative window (mirrors ``reconsolidate.recalled_stale_worklist``'s same
    passthrough for pinned-epoch fixtures).

    LIF-4 soak gating (report-only, no new write path):
      - Until ``soak.soak_status`` meets the ``>=5``-distinct-session bar (its own docs'
        trust threshold for the cold signal), the report is WITHHELD: ``[]`` with the
        machine-readable reason ``diagnostics["reason"] = "soak_gate_unmet"`` — a fresh
        install must never get a maximally-permissive candidate list.
      - Would-be candidates younger than the soak window (``_young_names`` — not yet
        exposed to the bar's worth of distinct sessions since their git first-seen) are
        excluded and listed under ``diagnostics["excluded_young"]``.
    ``diagnostics`` (a caller-owned dict, mirroring ``find_stale``'s same pattern) also
    always gains ``diagnostics["soak_gate"] = {"gate_met", "distinct_sessions",
    "gate_threshold"}`` once evaluation reaches the gate, so the one production caller
    (``main``) can explain a withheld/thinned report instead of printing a silent ``[]``.

    Read-only; never raises; ``[]`` when the gate is unmet or nothing satisfies all four
    conditions (the common, expected case).
    """
    try:
        corpus_names = _corpus_names(memory_dir)
        if not corpus_names:
            return []

        status = soak_status(telemetry_dir)
        if diagnostics is not None:
            diagnostics["soak_gate"] = {
                "gate_met": bool(status.get("gate_met")),
                "distinct_sessions": int(status.get("distinct_sessions") or 0),
                "gate_threshold": int(status.get("gate_threshold") or SOAK_GATE_SESSIONS),
            }
        if not status.get("gate_met"):
            if diagnostics is not None:
                diagnostics["reason"] = "soak_gate_unmet"
            return []

        report = curation_report(memory_dir, telemetry_dir)
        cold = set(report.get("never_recalled") or [])
        stale = find_stale(memory_dir, repo_root, **({"since": since} if since else {}))
        zero_inbound = _zero_inbound_names(memory_dir)
        unreadable: List[str] = []
        cited = _cited_by_claude_md_names(repo_root, corpus_names, unreadable=unreadable)

        out = [
            item
            for item in stale
            if item["name"] in cold and item["name"] in zero_inbound and item["name"] not in cited
        ]
        if out:
            # Youth gate last, over would-be candidates only — the git first-seen scan is
            # spent solely on memories the other four conditions already agree on, and the
            # excluded_young diagnostic stays meaningful (would-be candidates, not every
            # recently-created memory in the corpus).
            young = _young_names({i["name"] for i in out}, memory_dir, repo_root, telemetry_dir)
            excluded = sorted(i["name"] for i in out if i["name"] in young)
            if diagnostics is not None and excluded:
                diagnostics["excluded_young"] = excluded
            out = [i for i in out if i["name"] not in young]
        out.sort(key=lambda d: (-d["recency"], d["name"]))
        for item in out:
            item["citation_scan_unreadable"] = list(unreadable)
        return out
    except Exception:
        return []


_JOURNAL_NAME = ".archive_journal.jsonl"


def _journal_untracked_move(memory_dir: str, fname: str, src: str, dest: str) -> None:
    """Append a one-line record of an os.rename fallback move, for reversibility.

    Untracked files have no git history to fall back on, so this sidecar is the only trace
    of the pre-archive path. Best-effort only — a journal write failure must never abort an
    already-successful move.
    """
    import json
    import time

    journal = os.path.join(memory_dir, _ARCHIVE_SUBDIR, _JOURNAL_NAME)
    try:
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "name": fname,
                        "from": src,
                        "to": dest,
                        "method": "os.rename",
                        "ts": time.time(),
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def archive_memory(
    name: str, memory_dir: str, repo_root: str, *, dry_run: bool = False, force: bool = False
) -> dict:
    """``git mv`` ONE memory into ``.claude/memory/archive/`` — per-item, report-then-move.

    NEVER deletes; the move is fully git-reversible (or, for an untracked file, at least
    fully recoverable — see the ``os.rename`` fallback below). The non-recursive
    ``_iter_memory_files`` already skips the ``archive/`` subdir, so an archived memory
    instantly drops from index/recall/staleness with NO code change elsewhere. Deliberately
    NO batch/list parameter — a bulk sweep would require a separately-approved function,
    never this one. Never raises.

    GRA-5 inbound guard: unless ``force=True``, a memory that OTHER memories still
    reference (``_inbound_referrers``: untyped wikilinks ∪ typed edges) REFUSES to move —
    machine-readable ``refused: True`` + the ``referrers`` list in the result, ZERO
    filesystem change — because the move would instantly dangle every one of those links.
    The guard runs BEFORE the ``dry_run`` preview (a dry run of a referenced memory reports
    the refusal it would really hit, never a false would-move), and an unbuildable graph
    fails CLOSED (inbound unverifiable → refuse, absent force). Every SUCCESSFUL move
    (forced or zero-inbound) also carries ``referrers`` so the calling agent can rewrite
    those links in the same commit; a ``supersedes:`` edge on the successor memory (GRA-4)
    is the machine-readable forwarding-pointer pattern for exactly that rewrite.

    A memory that ``write_memory`` just created and that was never ``git add``-ed is
    UNTRACKED, and ``git mv`` refuses to move an untracked path. Falling back to a plain
    ``os.rename`` (journaled to a small sidecar log for reversibility, since git itself has
    no history of an untracked file anyway) is the only way archiving such a memory can
    ever succeed — without it, every just-written memory would be unarchivable until some
    unrelated later commit happened to stage it.
    """
    result = {"name": name, "moved": False, "refused": False, "referrers": [], "error": None}
    try:
        fname = name if name.endswith(".md") else f"{name}.md"
        src = os.path.join(memory_dir, fname)
        if not os.path.isfile(src):
            result["error"] = f"not found: {fname}"
            return result
        referrers = _inbound_referrers(fname[:-3], memory_dir)
        if referrers is None:
            # Graph unbuildable -> inbound UNVERIFIABLE. Fail closed (refuse — the
            # has-inbound direction _zero_inbound_names documents) unless forced.
            if not force:
                result["refused"] = True
                result["error"] = (
                    "could not build the link graph, so inbound referrers are unverifiable "
                    "— refusing (fail closed); re-run with --force (force=True) to archive "
                    "anyway"
                )
                return result
            referrers = []  # forced past an unverifiable graph: referrers unknown
        result["referrers"] = referrers
        if referrers and not force:
            result["refused"] = True
            result["error"] = (
                f"{len(referrers)} inbound referrer(s) still link here: "
                f"{', '.join(referrers)}. Rewrite those references first — a `supersedes:` "
                "edge on the successor memory (GRA-4) is the machine-readable forwarding "
                "pointer — or re-run with --force (force=True) to move it anyway"
            )
            return result
        if dry_run:
            result["moved"] = True  # would-move (report-only preview); no filesystem change
            return result
        archive_dir = os.path.join(memory_dir, _ARCHIVE_SUBDIR)
        os.makedirs(archive_dir, exist_ok=True)
        dest = os.path.join(archive_dir, fname)
        proc = subprocess.run(
            ["git", "-C", repo_root, "mv", src, dest],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            # git mv refuses untracked/ignored paths -- fall back to a plain rename so a
            # just-written (not yet git-add-ed) memory can still be archived.
            try:
                os.rename(src, dest)
            except OSError:
                result["error"] = (proc.stderr or proc.stdout or "git mv failed").strip()
                return result
            _journal_untracked_move(memory_dir, fname, src, dest)
        result["moved"] = True
        try:
            from . import build_index

            build_index.refresh_index(memory_dir)
        except Exception:
            pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs
    from .telemetry import default_telemetry_dir

    parser = argparse.ArgumentParser(
        description="Archive-candidate report (read-only) + per-item git-mv archive."
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--archive",
        metavar="NAME",
        default=None,
        help="git-mv ONE memory into .claude/memory/archive/ after confirming it's a "
        "genuine candidate (per-item, gated by the memory-master agent reviewing the "
        "report first). NAME is the slug, with or without .md. Refuses while other "
        "memories still link to NAME (GRA-5) unless --force",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass the GRA-5 inbound-referrer guard: archive NAME even while other "
        "memories still reference it (the referrer list is printed so those links can be "
        "rewritten in the same commit)",
    )
    args = parser.parse_args(argv)

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md
    repo_root = args.repo_root or repo

    if args.archive:
        r = archive_memory(args.archive, memory_dir, repo_root, force=args.force)
        if r["error"]:
            print(f"archive {args.archive}: refused — {r['error']}")
        else:
            print(f"archive {args.archive}: moved into .claude/memory/archive/ (git mv)")
            if r["referrers"]:
                print(
                    f"warning: {len(r['referrers'])} inbound referrer(s) now point at the "
                    f"archived memory — rewrite them in this same commit: "
                    f"{', '.join(r['referrers'])}"
                )
                print(
                    "  (a `supersedes:` edge on the successor memory — GRA-4 — is the "
                    "machine-readable forwarding pointer)"
                )
        return 0

    td = args.telemetry_dir or default_telemetry_dir(memory_dir)
    diagnostics: dict = {}
    candidates = archive_candidates(memory_dir, repo_root, telemetry_dir=td, diagnostics=diagnostics)
    unreadable: List[str] = []
    _cited_by_claude_md_names(repo_root, _corpus_names(memory_dir), unreadable=unreadable)
    if unreadable:
        print(
            f"warning: {len(unreadable)} instruction-surface file(s) unreadable during "
            f"citation scan (treated as cited, fail-closed): {', '.join(unreadable)}"
        )
    if diagnostics.get("reason") == "soak_gate_unmet":
        gate = diagnostics.get("soak_gate") or {}
        print(
            f"Archive-candidate report withheld: curation-soak gate unmet "
            f"({gate.get('distinct_sessions', 0)}/{gate.get('gate_threshold', SOAK_GATE_SESSIONS)} "
            f"distinct sessions logged)."
        )
        print(
            "The cold/never-recalled signal is not yet trustworthy — nothing is listed by "
            "design; re-run after more sessions."
        )
        return 0
    excluded_young = diagnostics.get("excluded_young") or []
    if excluded_young:
        shown = ", ".join(excluded_young[:8])
        more = f" …and {len(excluded_young) - 8} more" if len(excluded_young) > 8 else ""
        print(
            f"note: {len(excluded_young)} would-be candidate(s) excluded — younger than the "
            f"soak window (not yet exposed to {SOAK_GATE_SESSIONS} distinct sessions): {shown}{more}"
        )
    if not candidates:
        print("No archive candidates (4-way: cold ∧ stale ∧ zero-inbound ∧ not-CLAUDE.md-cited).")
        return 0
    print(f"{len(candidates)} archive candidate(s) — review each before archiving:")
    for item in candidates:
        print(f"  • {item['name']}: {', '.join(item['changed_paths'][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
