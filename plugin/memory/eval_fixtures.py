"""Fixture drafting/plumbing for the eval — decomposed out of ``eval_recall.py`` (the
façade keeps the SIG-6 write gates ``draft_abstention_fixtures``/``confirm_hard_set_row``
— their crash-contract keys pin them there — plus ``evaluate``/``main``).

This sibling owns the drafts-queue plumbing (paths, note text, YAML-parseability guard)
and the two T11 deterministic fixture SYNTHESIZERS:

- **TMB-3** ``draft_forgetting_fixtures`` — one archive-absence candidate per
  ``archive/*.md`` entry (the DIRECTORY LISTING is the enumeration source; the journal
  stays reversibility metadata), query derived from the archived file's OWN description
  tokens. Zero LLM, zero fabrication.
- **TMB-4** ``draft_update_fixtures`` — walks the corpus's supersedes chains into
  ``category: update`` + premise-resistance candidates. Query assembly is
  VERBATIM-SPAN-ONLY (a literal substring of the superseded file; strip/prefix-clip are
  the only transformations — the fabrication-kill adjacency: any generative rewording
  collapses the derivation-only property separating this from the round-1-killed
  demand-gap-auto-draft) and FAILS CLOSED (skip the row) whenever a span, an unambiguous
  live chain tip, or a readable corpse is missing. GATE_UPDATE_* promotion is
  deliberately ABSENT everywhere: update numbers stay report-only until a dated owner
  decision (the LIF-7 soak_status precedent), never an automatic row-count threshold.

Both synthesizers append DRAFTS ONLY: every row still lands in the tracked fixture
exclusively through the per-item ``confirm_hard_set_row`` gate (inv4 — no bulk path).
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Tuple

from .provenance import ensure_self_ignoring_dir, resolve_dirs

# --------------------------------------------------------------------------- #
# Drafts-queue plumbing (moved verbatim from the façade; it re-imports these).
# The drafts queue lives in the PENDING dir (``.claude/.memory-pending/``), NOT in
# ``.audit-fixtures/``: draft rows carry raw ``query_preview`` text from the gitignored
# telemetry ledger, and the pending queue is the shipped home for exactly that kind of
# unreviewed session-derived text (self-ignoring ``.gitignore``, SEC-3 — the capture-seed
# precedent). The tracked fixture dir stays committable because every row in it passed
# the per-item confirm step.
# --------------------------------------------------------------------------- #
_DRAFTS_FILENAME = "recall_hard_set.drafts.yaml"
_DRAFTS_NOTE = (
    "SIG-6 candidate eval fixtures drafted from recurring recall abstentions — UNCONFIRMED. "
    "For each row: if a REAL existing memory should answer the query, put its stem in "
    "'expected' and admit the row via eval_recall.confirm_hard_set_row (per item); if no "
    "memory answers it, that is a capture gap — capture the memory first (never invent a "
    "stem to make a fixture pass), or delete the row if it is noise."
)


def _project_fixture_path(memory_dir: str, filename: str = "recall_hard_set.yaml") -> str:
    """The project-local TRACKED-fixture path (``.audit-fixtures/``, the RET-7 convention)."""
    return os.path.join(memory_dir, ".audit-fixtures", filename)


def default_drafts_path(memory_dir: str) -> str:
    """The SIG-6 drafts-queue path — inside the gitignored pending dir (see block comment)."""
    from .capture import default_pending_dir

    return os.path.join(default_pending_dir(memory_dir), _DRAFTS_FILENAME)


def _parseable_yaml(path: str) -> bool:
    """False when ``path`` exists but is not loadable YAML — the append guards refuse
    to grow a file an agent hand-edit broke (appending after a parse error only buries it)."""
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            list(yaml.safe_load_all(fh))
        return True
    except Exception:
        return False


def _tracked_queries(memory_dir: str) -> set:
    """Every query already in the tracked fixture — BOTH polarities (dup guard)."""
    from .eval_metrics import load_absence_rows, load_hard_set

    tracked_path = _project_fixture_path(memory_dir)
    out = {row["query"] for row in load_hard_set(tracked_path)}
    out |= {row["query"] for row in load_absence_rows(tracked_path)}
    return out


def _append_draft_rows(dp: str, rows_text: str) -> None:
    """Append rendered rows to the drafts queue (atomic; INV-2 — never torn), creating
    the SEC-3 self-ignoring header on first write."""
    if os.path.exists(dp):
        with open(dp, "r", encoding="utf-8") as fh:
            text = fh.read()
        if text and not text.endswith("\n"):
            text += "\n"
        text += rows_text
    else:
        ensure_self_ignoring_dir(os.path.dirname(dp))
        header_lines = ["draft: true", f"note: {json.dumps(_DRAFTS_NOTE, ensure_ascii=False)}"]
        header_lines.append(f"generated_at: {time.strftime('%Y-%m-%d')}")
        text = "\n".join(header_lines) + "\n---\n" + rows_text
    from .atomic import write_text_atomic

    write_text_atomic(dp, text)


def validate_confirm_row_kind(
    memory_dir: str, stems: List[str], absent_stems: List[str], corpse: Optional[str]
) -> Optional[str]:
    """The T11 shape/existence gates for ``confirm_hard_set_row`` — the error, or None.

    Presence XOR absence; absence stems must actually be in ``archive/`` (TMB-3: never
    fabricate a forgetting expectation — a restored/vanished target IS the skip); a
    ``superseded`` corpse belongs to presence rows and must still be live (TMB-4:
    re-draft instead of confirming a stale row). Pure checks — the write stays in the
    façade's confirm gate.
    """
    if stems and absent_stems:
        return "a row is presence OR absence, not both — pass expected= or absent="
    if corpse and absent_stems:
        return "superseded= belongs to presence (update) rows, not absence rows"
    if corpse and not os.path.exists(os.path.join(memory_dir, f"{corpse}.md")):
        return (
            f"superseded names a memory that is not live in this corpus: {corpse} "
            "— the corpse left the corpus; re-draft instead of confirming a stale row"
        )
    if absent_stems:
        not_archived = [
            s
            for s in absent_stems
            if not os.path.exists(os.path.join(memory_dir, "archive", f"{s}.md"))
        ]
        if not_archived:
            return (
                f"absent names memories that are not in archive/: {not_archived} "
                "— a forgetting row's target must actually be archived (archive it "
                "first, or drop the row)"
            )
    return None


# --------------------------------------------------------------------------- #
# TMB-3: archive-absence drafting
# --------------------------------------------------------------------------- #
def draft_forgetting_fixtures(
    memory_dir: Optional[str] = None,
    *,
    drafts_path: Optional[str] = None,
) -> dict:
    """TMB-3: enumerate archive-absence candidates into the SIG-6 drafts queue.

    One draft row per ``archive/*.md`` entry: ``{query, absent: [stem], expected: []}``,
    where ``query`` is DERIVED from the archived file's own description (the
    ``derive_self_query`` derivation — tokenize, first N content tokens; zero LLM, zero
    fabrication: the archived memory's own words asking for itself). Confirmation stays
    per-item through ``confirm_hard_set_row(absent=[stem], category='forgetting')`` —
    nothing lands in the tracked fixture from here. Skips stems whose derived query is
    empty (fail closed), already tracked (either polarity), or already drafted.
    ``{path, archived, added, kept}``.
    """
    from .build_index import extract_description, tokenize
    from .eval_metrics import _SELF_QUERY_TOKENS, _load_fixture_docs

    if memory_dir is None:
        memory_dir, _repo = resolve_dirs()
    dp = drafts_path or default_drafts_path(memory_dir)
    archive_dir = os.path.join(memory_dir, "archive")
    tracked = _tracked_queries(memory_dir)
    _meta, existing_rows = _load_fixture_docs(dp)
    drafted = {(r.get("query") or "").strip() for r in existing_rows if isinstance(r, dict)}

    added: List[dict] = []
    archived = 0
    if os.path.isdir(archive_dir):
        for fn in sorted(os.listdir(archive_dir)):
            if not fn.endswith(".md"):
                continue
            archived += 1
            stem = fn[:-3]
            try:
                with open(os.path.join(archive_dir, fn), "r", encoding="utf-8") as fh:
                    desc = extract_description(fh.read())
            except Exception:
                continue
            q = " ".join(tokenize(desc)[:_SELF_QUERY_TOKENS])
            if not q or q in tracked or q in drafted:
                continue
            added.append({"query": q, "stem": stem})
            drafted.add(q)

    summary = {
        "path": dp,
        "archived": archived,
        "added": [r["query"] for r in added],
        "kept": len(existing_rows),
    }
    if not added:
        return summary
    if os.path.exists(dp) and not _parseable_yaml(dp):
        summary["added"] = []
        summary["error"] = (
            "drafts file exists but is not parseable YAML — fix or delete it before "
            "drafting more rows"
        )
        return summary
    rows_text = "".join(
        f"- query: {json.dumps(r['query'], ensure_ascii=False)}\n"
        f"  absent: [{json.dumps(r['stem'], ensure_ascii=False)}]\n"
        f"  expected: []\n"
        for r in added
    )
    # INV-2: the drafts queue accumulates human judgments — never leave it torn.
    _append_draft_rows(dp, rows_text)
    return summary


def run_draft_forgetting_cli(memory_dir: Optional[str]) -> int:
    """The ``eval_recall --draft-forgetting`` mode body (drafts only)."""
    summary = draft_forgetting_fixtures(memory_dir)
    if summary.get("error"):
        print(f"draft-forgetting: {summary['error']}")
        return 1
    if not summary["archived"]:
        print("draft-forgetting: archive/ is empty — nothing to enumerate.")
        return 0
    print(
        f"draft-forgetting: {summary['archived']} archived memor"
        + ("y" if summary["archived"] == 1 else "ies")
        + f", {len(summary['added'])} new draft row(s) appended to {summary['path']} "
        f"({summary['kept']} existing draft(s) preserved verbatim)."
    )
    for q in summary["added"]:
        print(f"  drafted: \"{q}\"")
    if summary["added"]:
        print(
            "  confirm each PER ITEM via eval_recall.confirm_hard_set_row(query, [], "
            "absent=[<stem>], category='forgetting') — absence rows score report-only."
        )
    return 0


# --------------------------------------------------------------------------- #
# TMB-4: supersedes-chain update drafting (verbatim spans only)
# --------------------------------------------------------------------------- #
_UPDATE_SPAN_MIN_CHARS = 30    # a shorter line is a fragment, not a claim
_UPDATE_SPAN_MAX_CHARS = 140   # clipped at a word boundary — still a literal substring
_UPDATE_MAX_SPANS = 2          # span 1 -> the update row; span 2 -> premise-resistance


def _verbatim_spans(file_text: str) -> List[str]:
    """Up to ``_UPDATE_MAX_SPANS`` literal substrings of ``file_text``'s BODY.

    Candidate = a stripped body line (past the closing frontmatter fence) of at least
    ``_UPDATE_SPAN_MIN_CHARS`` that is not a heading; long lines clip at the last word
    boundary under ``_UPDATE_SPAN_MAX_CHARS``. Every transformation is
    substring-preserving (strip, prefix-clip) — the literal-substring property is the
    whole point and is pinned by test. ``[]`` when nothing qualifies (fail closed).
    """
    lines = file_text.split("\n")
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
    spans: List[str] = []
    for line in lines[body_start:]:
        s = line.strip()
        if len(s) < _UPDATE_SPAN_MIN_CHARS or s.startswith("#"):
            continue
        if len(s) > _UPDATE_SPAN_MAX_CHARS:
            clipped = s[:_UPDATE_SPAN_MAX_CHARS].rsplit(" ", 1)[0]
            s = clipped if len(clipped) >= _UPDATE_SPAN_MIN_CHARS else s[:_UPDATE_SPAN_MAX_CHARS]
        if s and s not in spans:
            spans.append(s)
        if len(spans) >= _UPDATE_MAX_SPANS:
            break
    return spans


def _supersedes_tip(edges: dict, corpse: str, live_stems) -> Optional[str]:
    """The corpse's LIVE chain tip via transitive ``typed_in['supersedes']`` (the
    ``history.decision_chain`` direction convention: successors declare the edge).
    ``None`` — skip the row, fail closed — on no successor, a fork (two successors:
    ambiguity is not resolvable deterministically), a cycle, or a tip that left the
    live corpus."""
    seen = {corpse}
    cur = corpse
    while True:
        succs = sorted(
            s
            for s in (edges.get(cur, {}).get("typed_in", {}).get("supersedes", ()) or ())
            if s in live_stems
        )
        if not succs:
            return cur if cur != corpse else None
        if len(succs) > 1:
            return None
        nxt = succs[0]
        if nxt in seen:
            return None
        seen.add(nxt)
        cur = nxt


def draft_update_fixtures(
    memory_dir: Optional[str] = None,
    *,
    index_dir: Optional[str] = None,
    drafts_path: Optional[str] = None,
) -> dict:
    """TMB-4: walk supersedes chains into ``category: update`` DRAFT rows.

    Per superseded-but-still-live memory (``links.load_edges`` typed edges; the corpse
    file must exist): up to two rows — the update row (first verbatim body span) and a
    premise-resistance row (second span: the old premise's own words, which recall must
    answer with the SUCCESSOR, not the corpse) — with ``derived_expected`` = the live
    chain tip and ``superseded`` = the corpse, plus the corpse's CURRENT GRW-7
    ``invalid_after`` state as draft-time info (scoring re-reads it live). Drafts only:
    every row still requires per-item confirmation via ``confirm_hard_set_row(query,
    [tip], category='update', superseded=corpse)``. Zero LLM, zero network, fail closed
    throughout. ``{path, chains, added, kept, skipped}``.
    """
    from .build_index import default_index_dir
    from .eval_metrics import _load_fixture_docs
    from .links import load_edges

    if memory_dir is None:
        memory_dir, _repo = resolve_dirs()
    dp = drafts_path or default_drafts_path(memory_dir)
    resolved_index_dir = index_dir or default_index_dir(memory_dir)
    edges = load_edges(resolved_index_dir) or {}
    live_stems = {
        os.path.splitext(f)[0]
        for f in os.listdir(memory_dir)
        if f.endswith(".md") and os.path.isfile(os.path.join(memory_dir, f))
    } if os.path.isdir(memory_dir) else set()
    corpses = sorted(
        tgt
        for stem, rec in edges.items()
        for tgt in (rec.get("typed_out", {}).get("supersedes", ()) or ())
        if tgt in live_stems
    )
    tracked = _tracked_queries(memory_dir)
    _meta, existing_rows = _load_fixture_docs(dp)
    drafted = {(r.get("query") or "").strip() for r in existing_rows if isinstance(r, dict)}

    added: List[dict] = []
    skipped: List[str] = []
    for corpse in dict.fromkeys(corpses):
        tip = _supersedes_tip(edges, corpse, live_stems)
        if tip is None:
            skipped.append(f"{corpse} (no unambiguous live chain tip)")
            continue
        try:
            with open(os.path.join(memory_dir, f"{corpse}.md"), "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            skipped.append(f"{corpse} (unreadable)")
            continue
        spans = _verbatim_spans(text)
        if not spans:
            skipped.append(f"{corpse} (no qualifying verbatim span)")
            continue
        from .staleness import read_invalid_after

        ia = read_invalid_after(text)
        try:
            from .recall import _invalidation_state

            state = _invalidation_state({"invalid_after": ia}) or "unstamped"
        except Exception:
            state = "unstamped"
        for kind, span in zip(("update", "premise-resistance"), spans):
            if span in tracked or span in drafted:
                continue
            added.append(
                {
                    "query": span,
                    "superseded": corpse,
                    "derived_expected": [tip],
                    "kind": kind,
                    "stamp_state": state,
                }
            )
            drafted.add(span)

    summary = {
        "path": dp,
        "chains": len(set(corpses)),
        "added": [r["query"] for r in added],
        "kept": len(existing_rows),
        "skipped": skipped,
    }
    if not added:
        return summary
    if os.path.exists(dp) and not _parseable_yaml(dp):
        summary["added"] = []
        summary["error"] = (
            "drafts file exists but is not parseable YAML — fix or delete it before "
            "drafting more rows"
        )
        return summary
    rows_text = "".join(
        f"- query: {json.dumps(r['query'], ensure_ascii=False)}\n"
        f"  superseded: {json.dumps(r['superseded'], ensure_ascii=False)}\n"
        f"  derived_expected: [{json.dumps(r['derived_expected'][0], ensure_ascii=False)}]\n"
        f"  kind: {json.dumps(r['kind'], ensure_ascii=False)}\n"
        f"  stamp_state: {json.dumps(r['stamp_state'], ensure_ascii=False)}\n"
        f"  expected: []\n"
        for r in added
    )
    # INV-2: the drafts queue accumulates human judgments — never leave it torn.
    _append_draft_rows(dp, rows_text)
    return summary


def run_draft_update_cli(memory_dir: Optional[str], index_dir: Optional[str]) -> int:
    """The ``eval_recall --draft-update`` mode body (drafts only)."""
    summary = draft_update_fixtures(memory_dir, index_dir=index_dir)
    if summary.get("error"):
        print(f"draft-update: {summary['error']}")
        return 1
    if not summary["chains"]:
        print("draft-update: no live superseded memories (no supersedes chains to walk).")
        return 0
    print(
        f"draft-update: {summary['chains']} superseded live memor"
        + ("y" if summary["chains"] == 1 else "ies")
        + f", {len(summary['added'])} new draft row(s) appended to {summary['path']} "
        f"({summary['kept']} existing draft(s) preserved verbatim)."
    )
    for q in summary["added"]:
        print(f"  drafted: \"{q}\"")
    for s in summary["skipped"]:
        print(f"  skipped (fail closed): {s}")
    if summary["added"]:
        print(
            "  confirm each PER ITEM via eval_recall.confirm_hard_set_row(query, "
            "[<tip>], category='update', superseded=<corpse>) — never in bulk."
        )
    return 0
