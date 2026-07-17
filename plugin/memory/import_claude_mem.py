"""IOP-4 — claude-mem read-only migration audit: the graduation path FROM the incumbent.

claude-mem (the 86K-star Claude Code memory plugin) auto-writes everything it observes;
hippo's whole pitch is the opposite — ranked, review-gated, staleness-tracked memory.
This adapter is the wedge between them: a ``(discover, parse)`` pair in ``import_mdc``'s
documented adapter shape, shipping **v1 AUDIT-ONLY** — an ``import_candidates``-shaped
report of what a migration WOULD bring over, with ZERO writes to the corpus, rules.json,
or the pending queue (``capture``'s ``_SEED_SCHEMA`` is never touched; structurally
pinned). Any later write leg reuses the per-item ``import_mdc_file`` pattern (one file,
one yes, one run) with the shipped RCH-5 ``pack_install_item`` refuse-on-secret + SEC-5
consent posture for foreign content — it does NOT live here yet, by design.

ED-3 FINDING (2026-07-17, live probe of a real store on this machine — the tier gate's
literal step zero, run before this parser was written): claude-mem's store is a WAL-mode
SQLite database at ``~/.claude-mem/claude-mem.db`` (plus a separate ``vector-db/`` dir
this adapter deliberately ignores). The memory-shaped rows live in ``observations``
(``type`` CHECK-constrained to decision/bugfix/feature/refactor/discovery/change, with
``title``/``subtitle``, JSON-array TEXT columns ``facts``/``concepts``/``files_read``/
``files_modified``, free-text ``text``/``narrative``, and a ``project`` scope column);
``session_summaries`` holds per-session episodic rollups; ``user_prompts`` stores RAW
prompt text (a privacy surface — this audit only ever COUNTS those rows, never reads
their content into a report); ``schema_versions`` recorded NINE applied migrations
(4..11 + 16) on a store only two days old at capture — the format moves fast, so the
reader introspects defensively (missing table/column degrades to an ``error``/partial
report, never a crash) and records the store's own version list in every report.

READ POSTURE: the database is opened with SQLite's ``mode=ro`` URI (not ``immutable=1``,
which would silently skip un-checkpointed WAL rows; not a plain open, which could
checkpoint the WAL on close and MUTATE a store this audit promised only to read).
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

# Report bounds: counts cover EVERY candidate; per-item detail rows are capped so a
# 10k-observation store renders a legible report (the cap is disclosed inline).
_MAX_ENTRIES = 20
_DESCRIPTION_CHARS = 120

_DEFAULT_STORE = os.path.join("~", ".claude-mem", "claude-mem.db")


def claude_mem_store(path: Optional[str] = None) -> str:
    """DISCOVER: the store path (default ``~/.claude-mem/claude-mem.db``), expanded.
    Existence is the caller's question — ``audit_report`` answers it legibly."""
    return os.path.expanduser(path or _DEFAULT_STORE)


def _json_list(raw) -> List[str]:
    """A claude-mem JSON-array TEXT column, defensively: ``[]`` unless it parses to a
    list of strings (the probe found e.g. ``facts='["…","…"]'``)."""
    try:
        val = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
        if isinstance(val, list):
            return [v for v in val if isinstance(v, str) and v.strip()]
    except Exception:
        pass
    return []


def _candidate_text(row: dict) -> str:
    """What this observation WOULD become as a memory body — title/subtitle headline,
    narrative/text, the facts list, concepts, and the files it touched. Composed for
    SCORING (dedupe/secret/portability) only; nothing is ever written from it."""
    parts: List[str] = []
    for key in ("narrative", "text"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    facts = _json_list(row.get("facts"))
    if facts:
        parts.append("\n".join(f"- {f}" for f in facts))
    concepts = _json_list(row.get("concepts"))
    if concepts:
        parts.append("Concepts: " + ", ".join(concepts))
    files = _json_list(row.get("files_modified")) + _json_list(row.get("files_read"))
    if files:
        parts.append("Files: " + ", ".join(dict.fromkeys(files)))
    return "\n\n".join(parts)


def audit_report(
    store_path: Optional[str] = None,
    *,
    repo_root: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """PARSE + report, audit-only: what would a claude-mem migration bring over?

    Returns (never raises)::

        {
          "store", "exists", "error",          # discovery + defensive-read state
          "schema_versions": [...],            # the store's own migration ledger
          "projects": {name: observation_count},
          "candidates": N,                     # observations in scope (all, or --project)
          "session_summaries": N, "user_prompts": N,   # counted, NEVER read (privacy)
          "dedupe_rate": 0.0-1.0 | None,       # share restating the governance plane
          "secret_hits": N, "portability_hits": N, "threat_hits": N,
          "entries": [{"id","type","title","project","governance_dup","secret",
                       "portability","threat"}],   # first _MAX_ENTRIES, disclosed
          "entries_capped": bool,
          "note": "audit-only — zero writes …",
        }

    Scoring reuses the shipped detectors verbatim: ``rules_plane.rule_dup_candidates``
    (the AC's dedupe rate — candidates whose substance the governance plane already
    carries), ``secrets.scan_text``, ``portability.scan_portability``, and SEN-2's
    ``threat_lint.scan_tier_a`` (foreign content gets the untrusted posture from day
    zero, exactly like the ``.mdc`` adapter). Zero writes anywhere: no corpus file, no
    rules.json, no pending-queue seed, no index refresh.
    """
    out = {
        "store": claude_mem_store(store_path),
        "exists": False,
        "error": None,
        "schema_versions": [],
        "projects": {},
        "candidates": 0,
        "session_summaries": 0,
        "user_prompts": 0,
        "dedupe_rate": None,
        "secret_hits": 0,
        "portability_hits": 0,
        "threat_hits": 0,
        "entries": [],
        "entries_capped": False,
        "note": (
            "audit-only — zero writes (no corpus file, no rules.json, no pending-queue "
            "seed); user_prompts are counted, never read; the write leg is a separate, "
            "per-item, consent-gated future step"
        ),
    }
    try:
        import sqlite3

        from .portability import scan_portability
        from .rules_plane import rule_dup_candidates
        from .secrets import scan_text
        from .threat_lint import scan_tier_a

        if repo_root is None:
            from .provenance import resolve_dirs

            _md, repo_root = resolve_dirs()

        store = out["store"]
        if not os.path.isfile(store):
            out["error"] = f"no claude-mem store at {store} — nothing to audit"
            return out
        out["exists"] = True

        try:
            conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=5)
        except Exception as exc:
            out["error"] = f"store unreadable: {exc}"
            return out
        try:
            conn.row_factory = sqlite3.Row

            def _count(table: str) -> int:
                try:
                    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except Exception:
                    return 0

            try:
                out["schema_versions"] = [
                    int(r[0])
                    for r in conn.execute(
                        "SELECT version FROM schema_versions ORDER BY version"
                    )
                ]
            except Exception:
                pass  # version ledger absent: report goes on without it

            out["session_summaries"] = _count("session_summaries")
            out["user_prompts"] = _count("user_prompts")

            try:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id, project, type, title, subtitle, text, narrative, "
                        "facts, concepts, files_read, files_modified FROM observations "
                        "ORDER BY id"
                    )
                ]
            except Exception as exc:
                out["error"] = (
                    f"observations unreadable ({exc}) — schema drifted past this "
                    "adapter; re-probe the store format (ED-3) before trusting counts"
                )
                return out
        finally:
            try:
                conn.close()
            except Exception:
                pass

        for r in rows:
            proj = r.get("project") or "(none)"
            out["projects"][proj] = out["projects"].get(proj, 0) + 1
        if project is not None:
            rows = [r for r in rows if (r.get("project") or "(none)") == project]
        out["candidates"] = len(rows)
        if not rows:
            return out

        dup_n = 0
        for i, r in enumerate(rows):
            title = (r.get("title") or "").strip()
            description = (title or (r.get("subtitle") or "").strip())[:_DESCRIPTION_CHARS]
            body = _candidate_text(r)
            scored = f"{description}\n{body}"
            dups = rule_dup_candidates(description, body, repo_root)
            secret = scan_text(scored)
            portability = scan_portability(scored, cited_paths=[])
            threat = scan_tier_a(scored)
            dup_n += bool(dups)
            out["secret_hits"] += bool(secret)
            out["portability_hits"] += bool(portability)
            out["threat_hits"] += bool(threat)
            if i < _MAX_ENTRIES:
                out["entries"].append(
                    {
                        "id": r.get("id"),
                        "type": r.get("type"),
                        "title": title or "(untitled)",
                        "project": r.get("project"),
                        "governance_dup": [d["file"] for d in dups],
                        "secret": len(secret),
                        "portability": len(portability),
                        "threat": len(threat),
                    }
                )
        out["entries_capped"] = len(rows) > _MAX_ENTRIES
        out["dedupe_rate"] = round(dup_n / len(rows), 4)
    except Exception as exc:
        out["error"] = out["error"] or f"audit failed: {exc}"
    return out


def describe_audit(report: dict) -> str:
    """Human render — counts first, the graduation story explicit, caps disclosed."""
    lines: List[str] = [f"claude-mem migration audit (read-only) — store: {report['store']}"]
    if report.get("error"):
        lines.append(f"  ✘ {report['error']}")
        return "\n".join(lines)
    sv = report.get("schema_versions") or []
    lines.append(
        f"  store schema versions: {', '.join(map(str, sv)) if sv else 'unrecorded'} "
        "(claude-mem migrates fast — a failed read here means re-probe, ED-3)"
    )
    lines.append(
        f"  {report['candidates']} observation candidate(s) across "
        f"{len(report['projects'])} project(s); {report['session_summaries']} session "
        f"summaries + {report['user_prompts']} raw user prompts (counted, never read)"
    )
    if report["dedupe_rate"] is not None:
        lines.append(
            f"  dedupe: {round(report['dedupe_rate'] * 100, 1)}% restate the governance "
            f"plane; hits — secrets {report['secret_hits']}, portability "
            f"{report['portability_hits']}, threat-lint {report['threat_hits']}"
        )
    for e in report["entries"]:
        flags = []
        if e["governance_dup"]:
            flags.append("dup:" + ",".join(e["governance_dup"]))
        if e["secret"]:
            flags.append(f"secrets:{e['secret']}")
        if e["portability"]:
            flags.append(f"portability:{e['portability']}")
        if e["threat"]:
            flags.append(f"threat:{e['threat']}")
        lines.append(
            f"    • [{e['type']}] {e['title']} ({e['project']})"
            + (" — " + "; ".join(flags) if flags else "")
        )
    if report.get("entries_capped"):
        lines.append(
            f"    … detail rows capped at {_MAX_ENTRIES} (counts above cover everything)"
        )
    lines.append(
        "  zero writes performed. A future write leg is per-item (one observation, one "
        "yes, one run) on the import_mdc_file pattern + RCH-5's refuse-on-secret."
    )
    return "\n".join(lines)
