"""DRM-2 apply/undo mechanics + the notify surface (decomposed out of ``dream.py``).

Tier routing, the apply-mode default, stamp/block/frontmatter edit helpers, byte-exact
undo with refuse-on-drift, the DRM-7 ghost-edge retirement, ``--log``, and the
SessionStart producer. The orchestrating
``run_apply_pass`` + ``_apply_one`` stay in the façade (the crash-contract and
monkeypatch surfaces pin them there). Every name re-exports via the ``dream`` façade.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .dream_config import age_sessions
from .dream_ledgers import _GENERATED_KINDS, apply_ledger_path, edge_aged_in, read_apply_ledger
from .soak import soak_status
from .telemetry import default_telemetry_dir

# --------------------------------------------------------------------------- #
# DRM-2 — Tier-A auto-apply (apply-reversibly → notify → undo-window → age-in)
#
# The loop DRM-2.spec.md specifies. AUTO-APPLY IS NOT THE SHIPPED DEFAULT: a pass applies
# only when explicitly asked (--apply / MCP apply:true) or when HIPPO_DREAM_APPLY is set —
# the default flip is a DATED OWNER DECISION consuming DRM-1's calibration (owner_decisions
# item 1; do not flip it in code without that date). Every applied edge is:
#   - additive + body-prose-preserving: a stamped line inside the machine-managed
#     dream:links block (bridge/completion), or additive refines frontmatter via
#     links.add_typed_relation plus a comment stamp in the block;
#   - capped (DREAM_MAX_APPLY_PER_PASS ≤ 9) and θ/mutuality-gated (apply_eligible);
#   - secret-linted with a HARD BLOCK (the owner-ratified 2026-07-12 deviation from
#     secrets.py's WARN-never-BLOCK, scoped to THIS write path only — dream GENERATES
#     text; it does not transcribe user intent);
#   - provenance-complete in the committed append-only dream-ledger.jsonl, with an inline
#     pass=/edge= stamp so grep reconciles corpus against ledger (doctor checks this);
#   - live immediately (working tree + index rebuild) but NEVER auto-committed — git
#     history stays the owner's (DREAM-KILL-2);
#   - mechanically undoable byte-for-byte (--undo / --undo <id> / --undo-since), with
#     refuse-on-drift: a stamped line or frontmatter region edited by hand since apply is
#     never clobbered.
# --------------------------------------------------------------------------- #
_TIER_A_KINDS = ("completion", "bridge", "refines")
# Tier-C routing (DREAM-KILL-1): these kinds are NEVER auto-applied. Today's generator
# does not emit them; the routing is enforced here anyway so a future/hand-fed candidate
# stream cannot slip one through the apply path.
_GATED_KINDS = ("supersedes",)   # → surfaced in the digest, applied only by explicit owner action
_ROUTED_KINDS = ("contradicts",)  # → the /hippo:resolve inbox, never auto


def apply_mode_default() -> bool:
    """Whether a bare pass auto-applies (``HIPPO_DREAM_APPLY``; SHIPPED DEFAULT: True).

    FLIPPED ON by the dated owner decision 2026-07-12 (ROADMAP.dream.yaml
    owner_decisions item 5), consuming the DRM-1 live-corpus calibration (θ=0.90, cap 5,
    bridges-require-mutual — see ``apply_eligible``). ``HIPPO_DREAM_APPLY=0`` or
    ``--dry-run`` opts a pass back to report-only; the default may only change again
    alongside a new dated entry in owner_decisions.
    """
    _SHIPPED_APPLY = True
    raw = os.environ.get("HIPPO_DREAM_APPLY", "").strip()
    if not raw:
        return _SHIPPED_APPLY
    return raw not in ("0", "false", "False")


def _sanitize_stamp_text(s: str, limit: int = 60) -> str:
    """Stamp-safe text: quotes/newlines/comment-closers stripped, bounded."""
    s = (s or "").replace('"', "'").replace("\n", " ").replace("-->", "")
    return s[:limit].strip()


def _stamp_line(edge_id: str, pass_id: str, cand: dict) -> str:
    """The exact on-disk line for one applied edge (the grep-able provenance stamp)."""
    q = _sanitize_stamp_text(cand.get("query") or "")
    cof = float(cand.get("cofire") or 0.0)
    if cand["kind"] == "refines":
        # Deliberately bracket-free: the edge itself lives in frontmatter; this comment is
        # the stamp only, and must never read as an untyped wikilink edge.
        return (
            f"<!-- dream: refines {cand['target']} · pass={pass_id} · edge={edge_id}"
            f" · cofire={cof:.2f} -->"
        )
    return (
        f"[[{cand['target']}]] <!-- dream: {cand['kind']} · pass={pass_id} · edge={edge_id}"
        f" · cofire={cof:.2f}" + (f' · q="{q}"' if q else "") + " -->"
    )


def _insert_block_line(text: str, line: str) -> Tuple[str, dict]:
    """Insert ``line`` into the dream:links block (creating it at EOF if absent).

    Returns ``(new_text, undo_record)``. The undo record captures EXACTLY what was added:
    ``{"inserted": <line+newline>, "wrapper": bool, "lead": <bytes prepended before the
    block>}`` — enough to reverse this edit byte-for-byte, alone or in reverse-order
    composition with the pass's other edits.
    """
    from .links import DREAM_BLOCK_CLOSE, DREAM_BLOCK_OPEN

    close_marker = DREAM_BLOCK_CLOSE + "\n"
    if DREAM_BLOCK_OPEN in text and close_marker in text:
        idx = text.rindex(close_marker)
        new_text = text[:idx] + line + "\n" + text[idx:]
        return new_text, {"inserted": line + "\n", "wrapper": False, "lead": ""}
    lead = "" if text.endswith("\n") else "\n"
    appended = f"{lead}{DREAM_BLOCK_OPEN}\n{line}\n{DREAM_BLOCK_CLOSE}\n"
    return text + appended, {"inserted": line + "\n", "wrapper": True, "lead": lead}


def _frontmatter_region(text: str) -> Optional[Tuple[int, int, List[str]]]:
    """``(start_line, end_line, fm_lines)`` of the frontmatter body (between fences)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i, lines[1:i]
    return None


def _refresh_index_quiet(memory_dir: str, index_dir: Optional[str]) -> None:
    try:
        from .build_index import default_index_dir, refresh_index

        refresh_index(memory_dir, index_dir or default_index_dir(memory_dir))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# DRM-2 — undo (git-native reversibility, made one command)
# --------------------------------------------------------------------------- #
def _undo_one_edge(memory_dir: str, edge: dict) -> Tuple[bool, str]:
    """Reverse ONE applied edge's exact edit. ``(ok, reason)``; refuse-on-drift.

    Mechanics mirror apply in reverse, verified byte-exactly before any write:
      1. the stamped block line must exist EXACTLY as inserted (else: manual drift → refuse);
      2. for refines, the current frontmatter region must equal the recorded ``fm_after``
         (else drift → refuse) and is replaced with ``fm_before``;
      3. after removing the line, a block THIS edge created is removed entirely IF no other
         dream line remains in it (restoring the pre-pass bytes).
    A refusal writes NOTHING for this edge (report-then-skip, never clobber a human edit).
    """
    undo = edge.get("undo") or {}
    fname = undo.get("file")

    # DRM-6: a GENERATED memory's undo is whole-file removal — the file is entirely
    # machine-authored, so deletion is the prose-lossless reverse of staging. Byte-exact
    # refuse-on-drift via the staging-time hash: a hand-edited OR graduated draft (its
    # confidence line changed) refuses — that content earned protection; archive it or
    # use git from there.
    if undo.get("created"):
        if not fname:
            return False, "ledger row carries no undo record"
        path = os.path.join(memory_dir, fname)
        try:
            from .trust import file_sha256

            live = file_sha256(path)
        except Exception:
            live = None
        if live is None:
            return False, "staged file already missing (archived or hand-moved) — refusing"
        if live != undo.get("sha256"):
            return False, (
                "staged file was edited or graduated since staging (drift) — refusing "
                "(archive it with --archive-draft, or revert via git)"
            )
        try:
            os.remove(path)
        except Exception as exc:
            return False, f"remove failed: {exc}"
        return True, ""

    block = undo.get("block") or {}
    inserted = block.get("inserted")
    if not fname or not inserted:
        return False, "ledger row carries no undo record"
    path = os.path.join(memory_dir, fname)
    if not os.path.isfile(path):
        # The stamped source may have been moved into ``archive/`` with the edge left
        # ACTIVE — the legacy trap (pre-fix archives never retired their edges) or a hand
        # ``git mv`` outside the tooling. The stamp bytes ride the move unchanged, so the
        # byte-exact undo (and its refuse-on-drift) applies verbatim to the archived copy;
        # without this fallback the DOCUMENTED remedy (``dream --undo <edge-id>``) refuses
        # with "unreadable" and the mismatch is unfixable by any supported command.
        archived = os.path.join(memory_dir, "archive", fname)
        if os.path.isfile(archived):
            path = archived
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        return False, f"unreadable: {exc}"

    if text.count(inserted) != 1:
        return False, "stamped line missing or altered on disk (manual drift) — refusing"

    new_text = text
    # Refines: reverse the frontmatter edit first (verified against the recorded state).
    if "fm_before" in undo:
        region = _frontmatter_region(new_text)
        if region is None:
            return False, "frontmatter missing (manual drift) — refusing"
        start, end, fm_lines = region
        if fm_lines != undo.get("fm_after"):
            return False, "frontmatter drifted since apply — refusing (undo it by hand or git)"
        all_lines = new_text.split("\n")
        new_text = "\n".join(all_lines[:start] + list(undo["fm_before"]) + all_lines[end:])
        if new_text.count(inserted) != 1:
            return False, "stamp line lost while reversing frontmatter — refusing"

    new_text = new_text.replace(inserted, "", 1)

    # Remove a block this edge created if nothing else lives in it now.
    from .links import DREAM_BLOCK_CLOSE, DREAM_BLOCK_OPEN

    if block.get("wrapper"):
        empty_block = f"{block.get('lead', '')}{DREAM_BLOCK_OPEN}\n{DREAM_BLOCK_CLOSE}\n"
        if empty_block in new_text:
            new_text = new_text.replace(empty_block, "", 1)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except Exception as exc:
        return False, f"write failed: {exc}"
    return True, ""


def undo_edges(
    memory_dir: str,
    index_dir: Optional[str] = None,
    *,
    edge_id: Optional[str] = None,
    since: Optional[str] = None,
    edge_ids: Optional[List[str]] = None,
    annotate: Optional[dict] = None,
) -> Tuple[int, str]:
    """``--undo`` (latest pass) / ``--undo <edge-id>`` / ``--undo-since <ISO date|N>``.

    Reverts in reverse-apply order (so same-file edits compose back byte-for-byte), appends
    superseding ``state: "undone"`` ledger lines (append-only audit — history intact), and
    rebuilds the index. Refuse-on-drift is PER EDGE: a hand-edited stamp refuses with a
    report while clean edges still revert; exit 1 signals any refusal.

    ``edge_ids`` selects several specific edges in ONE call (one ledger append + one index
    rebuild) — the DRM-4 retraction entry point, which is why there is no second undo
    implementation anywhere. ``annotate`` merges extra provenance keys into each
    superseding ledger line (e.g. ``retracted_by``/``retract_reason``); the canonical
    ``edge_id``/``pass``/``state``/``undone_at_ts`` fields always win over it.
    """
    ledger = read_apply_ledger(memory_dir)
    active = [e for e in ledger if e.get("state") == "active"]
    if not active:
        return 0, "🌙 dream --undo: no active dream edges to revert."

    if edge_ids:
        wanted = {str(x) for x in edge_ids}
        targets = [e for e in active if e.get("edge_id") in wanted]
        if not targets:
            return 1, "🌙 dream --undo: none of the requested edges are ACTIVE (see dream --log)."
    elif edge_id:
        targets = [e for e in active if e.get("edge_id") == edge_id]
        if not targets:
            return 1, f"🌙 dream --undo: no ACTIVE edge {edge_id!r} (see dream --log)."
    elif since:
        if re.fullmatch(r"\d+", since):
            # last N distinct sessions, via the same derived count aging uses
            td = default_telemetry_dir(memory_dir)
            now = int(soak_status(td, memory_dir=memory_dir).get("distinct_sessions") or 0)
            window = int(since)
            targets = [
                e
                for e in active
                if isinstance(e.get("applied_at_distinct_count"), int)
                and now - e["applied_at_distinct_count"] < window
            ]
        else:
            targets = [e for e in active if str(e.get("applied_at_ts") or "") >= since]
        if not targets:
            return 0, f"🌙 dream --undo-since {since}: nothing in that window."
    else:
        last_pass = active[-1].get("pass")
        targets = [e for e in active if e.get("pass") == last_pass]

    undone: List[dict] = []
    refused: List[Tuple[dict, str]] = []
    fold_failures = 0  # BND-3: restored files whose consent re-fold anomalously failed
    for edge in reversed(targets):
        ok, reason = _undo_one_edge(memory_dir, edge)
        if ok:
            # SEC-6: re-fold the restored bytes (the apply fold moved the baseline to
            # the stamped content; the un-stamped restoration must move it back or the
            # file quarantines). A deleted generated file simply no-ops the fold.
            # BND-3: an anomalous re-fold failure is counted into the message's one line.
            fname = (edge.get("undo") or {}).get("file")
            if fname:
                try:
                    from .trust import record_authored_write_disclosing

                    note = record_authored_write_disclosing(
                        memory_dir, os.path.join(memory_dir, fname)
                    )
                    if note:
                        fold_failures += 1
                except Exception:
                    pass
        (undone if ok else refused).append((edge, reason) if not ok else edge)

    if undone:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with open(apply_ledger_path(memory_dir), "a", encoding="utf-8") as fh:
                for edge in undone:
                    fh.write(
                        json.dumps(
                            {
                                **(annotate or {}),
                                "edge_id": edge["edge_id"],
                                "pass": edge.get("pass"),
                                "state": "undone",
                                "undone_at_ts": now_iso,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        except Exception as exc:
            return 1, f"🌙 dream --undo: reverted {len(undone)} edge(s) but the ledger append failed: {exc}"
        _refresh_index_quiet(memory_dir, index_dir)

    lines = [f"🌙 dream --undo: reverted {len(undone)} edge(s)" + (":" if undone else ".")]
    for edge in undone:
        if edge.get("kind") in _GENERATED_KINDS:
            lines.append(
                f"  • {edge['edge_id']}  generated {edge.get('kind')} "
                f"{edge.get('memory')} removed"
            )
        else:
            lines.append(
                f"  • {edge['edge_id']}  {edge.get('source')} ↔ {edge.get('target')} restored"
            )
    for edge, reason in refused:
        lines.append(f"  ✘ {edge.get('edge_id')}: {reason}")
    if refused:
        lines.append("  (refused edges are untouched — resolve by hand or `git checkout`.)")
    if fold_failures:
        # BND-3: the undo's ONE consent-disclosure line (aggregate).
        lines.append(
            f"  ⚠ {fold_failures} restored file(s) did not rejoin the consent baseline — "
            "withheld from recall until re-consent (trust_corpus)"
        )
    return (1 if refused else 0), "\n".join(lines)


# --------------------------------------------------------------------------- #
# DRM-7 — retire a GHOST edge (an ACTIVE ledger row whose stamp is provably gone)
# --------------------------------------------------------------------------- #
_EDGE_STAMP_RE = re.compile(r"edge=([\w-]+)")


def edge_source_location(memory_dir: str, edge: dict) -> Tuple[Optional[str], str]:
    """``(file, where)`` for the memory an edge's applied bytes were written into —
    ``where`` is ``"corpus"``, ``"archive"`` or ``"gone"`` (in neither place: the memory was
    deleted or folded away OUTSIDE ``archive_memory``, which would have retired its rows).
    Shared by ``retire_ghost_edge`` and doctor's ledger check so the two name one cause."""
    fname = (edge.get("undo") or {}).get("file")
    if not fname and isinstance(edge.get("source") or edge.get("memory"), str):
        fname = f"{edge.get('source') or edge.get('memory')}.md"
    if not fname:
        return None, "gone"
    if os.path.isfile(os.path.join(memory_dir, fname)):
        return fname, "corpus"
    if os.path.isfile(os.path.join(memory_dir, "archive", fname)):
        return fname, "archive"
    return fname, "gone"


def _files_stamping_edge(memory_dir: str, edge_id: str) -> Tuple[List[str], List[str]]:
    """``(holders, unreadable)`` over every ``*.md`` in the corpus root AND ``archive/`` —
    the exact surface doctor reconciles. A holder carries a ``<!-- dream: … edge=<id>``
    line; an unreadable file can hide one, so it is never counted as absence."""
    holders: List[str] = []
    unreadable: List[str] = []
    for sub in ("", "archive"):
        d = os.path.join(memory_dir, sub) if sub else memory_dir
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".md"))
        except FileNotFoundError:
            continue
        except Exception:
            unreadable.append(sub or ".")
            continue
        for name in names:
            rel = os.path.join(sub, name) if sub else name
            try:
                with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                    for line in fh:
                        if "<!-- dream:" in line and edge_id in _EDGE_STAMP_RE.findall(line):
                            holders.append(rel)
                            break
            except Exception:
                unreadable.append(rel)
    return holders, unreadable


def retire_ghost_edge(
    memory_dir: str, edge_id: str, *, reason: Optional[str] = None
) -> Tuple[int, str]:
    """``--retire-ghost <edge-id>``: close ONE active ledger row whose stamp no longer exists.

    The gap this fills: doctor's ghost fail named two reconcile routes and both could be
    shut. ``--undo`` is byte-exact and refuses when the stamped line is missing or the
    source file is gone (correctly — it has nothing to reverse), and git history only helps
    if the stamp ever reached a commit. A memory deleted outside ``archive_memory``, or a
    stamped block lost to a hand rewrite before it was committed, left a permanent ✘ that
    no supported verb could clear — and hand-editing the ledger is the one thing the
    ledger's own contract forbids.

    Writes NOTHING but one superseding ``state: "undone"`` ledger line (append-only,
    history intact, the ``archive_draft`` idiom), and only after PROVING the ghost: the row
    is ACTIVE, and no readable ``*.md`` in the corpus root or ``archive/`` carries its
    ``edge=`` stamp — the same surface doctor reconciles, so the verb retires exactly what
    doctor calls a ghost and nothing else. A stamp found anywhere refuses toward ``--undo``
    (which reverses real bytes); an unreadable file refuses too (it could hide the stamp).
    Per-edge by signature — there is deliberately no list/``--all`` form: each ghost is a
    separate claim that bytes are gone.
    """
    edge_id = (edge_id or "").strip()
    if not edge_id:
        return 1, "🌙 dream --retire-ghost: an edge id is required (see dream --log)."
    edge = next((e for e in read_apply_ledger(memory_dir) if e.get("edge_id") == edge_id), None)
    if edge is None:
        return 1, f"🌙 dream --retire-ghost: no ledger edge {edge_id!r} (see dream --log)."
    if edge.get("state") != "active":
        return 1, (
            f"🌙 dream --retire-ghost: {edge_id} is already {edge.get('state')!r} — only an "
            "ACTIVE row can be a ghost."
        )
    holders, unreadable = _files_stamping_edge(memory_dir, edge_id)
    if holders:
        return 1, (
            f"🌙 dream --retire-ghost REFUSED: {edge_id} is not a ghost — its stamp is on disk "
            f"in {', '.join(holders)}. Reverse real bytes with `python -m memory.dream --undo "
            f"{edge_id}` (archive-aware); nothing was written."
        )
    if unreadable:
        return 1, (
            f"🌙 dream --retire-ghost REFUSED: could not read {', '.join(unreadable[:5])} — an "
            "unreadable file can hide the stamp, so absence is unproven; nothing was written."
        )
    fname, where = edge_source_location(memory_dir, edge)
    if where == "gone":
        cause = "source-deleted"
        detail = f"source {fname or '(unrecorded)'} exists in neither the corpus nor archive/"
    else:
        cause = "stamp-missing"
        detail = f"{fname} is in the {where} but carries no stamp for this edge"
    from .archive import _mark_dream_rows

    err = _mark_dream_rows(
        memory_dir,
        [edge],
        "undone",
        undone_at_ts=None,
        retired_ghost=True,
        retire_cause=cause,
        retire_reason=(reason or "").strip() or detail,
    )
    if err:
        return 1, (
            "🌙 dream --retire-ghost: the dream-ledger append failed "
            f"({err.rsplit(': ', 1)[-1]}); nothing was retired."
        )
    what = (
        f"generated {edge.get('kind')} {edge.get('memory')}"
        if edge.get("kind") in _GENERATED_KINDS
        else f"{edge.get('source')} → {edge.get('target')} ({edge.get('kind')})"
    )
    lines = [
        f"🌙 dream --retire-ghost: retired {edge_id}  {what} — {detail}.",
        "  One superseding `state: \"undone\"` line was appended to dream-ledger.jsonl "
        "(append-only; no memory file was touched). The ledger is committed — the commit "
        "stays yours.",
        "  An undone pair is a standing verdict: /dream will not auto re-apply it.",
    ]
    if "fm_before" in (edge.get("undo") or {}) and where != "gone":
        lines.append(
            f"  This was a `refines` edge: any `refines:` entry still in {fname}'s "
            "frontmatter is left as it is — an ordinary hand-owned relation now."
        )
    return 0, "\n".join(lines)


def render_log(memory_dir: str) -> str:
    """``dream --log``: every edge's current state (active / aged-in / undone), oldest first."""
    ledger = read_apply_ledger(memory_dir)
    if not ledger:
        return "🌙 dream --log: no dream edges have ever been applied here."
    td = default_telemetry_dir(memory_dir)
    now = int(soak_status(td, memory_dir=memory_dir).get("distinct_sessions") or 0)
    lines = [f"🌙 dream --log — {len(ledger)} edge(s), distinct sessions now {now}:"]
    for e in ledger:
        state = e.get("state")
        if state == "active":
            state = "aged-in" if edge_aged_in(e, now) else (
                f"active ({max(0, age_sessions() - (now - e.get('applied_at_distinct_count', now)))}"
                " session(s) to age-in)"
            )
        if e.get("kind") in _GENERATED_KINDS:
            # DRM-6 rows are staged MEMORIES: show the tier lifecycle alongside aging.
            tier = e.get("confidence") or "draft"
            marks = [tier]
            if e.get("expired"):
                marks.append("expired-awaiting-archive")
            lines.append(
                f"  • {e.get('edge_id')}  {e.get('memory')}  generated-{e.get('kind')}  "
                f"cofire={e.get('cofire')}  [{state} · {' · '.join(marks)}]"
            )
        else:
            lines.append(
                f"  • {e.get('edge_id')}  {e.get('source')} → {e.get('target')}  "
                f"{e.get('kind')}  cofire={e.get('cofire')}  [{state}]"
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# DRM-2 — the SessionStart notify surface (deferred half of notify-with-undo)
# --------------------------------------------------------------------------- #
_PRODUCER_MAX_ITEMS = 20


def dream_applied_producer(memory_dir: str, repo_root: str, ctx=None) -> Optional[str]:
    """SessionStart producer: dream edges applied but NOT yet aged in, with the undo handle.

    Aged-in edges drop off (implicit ratification by non-undo — they are trusted now);
    undone edges never appear. Silent (None) when there is nothing in the window, exactly
    like every other quiet-by-default producer. ``ctx`` (LIF-6 RunContext) is unused —
    declared so every producer shares ONE call shape. Read-only; never raises.
    """
    try:
        ledger = read_apply_ledger(memory_dir)
        active = [e for e in ledger if e.get("state") == "active"]
        if not active:
            return None
        td = default_telemetry_dir(memory_dir)
        now = int(soak_status(td, memory_dir=memory_dir).get("distinct_sessions") or 0)
        fresh = [e for e in active if not edge_aged_in(e, now)]
        if not fresh:
            return None
        window = age_sessions()
        lines = [
            f"🌙 dream applied {len(fresh)} edge(s) awaiting age-in (each becomes trusted "
            f"/dream source after {window} sessions un-undone; revert any with "
            "`python -m memory.dream --undo <edge-id>` or all recent with --undo-since):"
        ]
        for e in fresh[:_PRODUCER_MAX_ITEMS]:
            left = window - (now - e.get("applied_at_distinct_count", now))
            if e.get("kind") in _GENERATED_KINDS:
                tier = e.get("confidence") or "draft"
                lines.append(
                    f"  • {e.get('edge_id')}  {e.get('memory')} (generated {e.get('kind')}, "
                    f"{tier}{', expired — archive proposed' if e.get('expired') else ''}, "
                    f"{max(0, left)} session(s) to age-in)"
                )
            else:
                lines.append(
                    f"  • {e.get('edge_id')}  {e.get('source')} → {e.get('target')} "
                    f"({e.get('kind')}, cofire {e.get('cofire')}, {max(0, left)} session(s) left)"
                )
        if len(fresh) > _PRODUCER_MAX_ITEMS:
            lines.append(f"  …and {len(fresh) - _PRODUCER_MAX_ITEMS} more (dream --log).")
        return "\n".join(lines)
    except Exception:
        return None
