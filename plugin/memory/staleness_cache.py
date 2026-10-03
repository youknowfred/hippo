"""LIF-6's derived ``stale.json`` cache: the writer SessionStart calls once per run and the
two readers recall's RET-5 penalty and RET-6 banner use on the hot path.

Split out of ``staleness.py`` by RWY-1 as pure code motion so the module keeps runway.
Every name stays importable at ``memory.staleness.<name>`` via the façade's re-exports.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

STALE_CACHE_SCHEMA_VERSION = 1
_STALE_CACHE_NAME = "stale.json"
# "short" sha length, matching git's own default `--short` width.
_SHORT_SHA_LEN = 7


def stale_cache_path(index_dir: str) -> str:
    """``<index_dir>/stale.json`` — the one path the writer and ``read_stale_cache`` below
    (RET-5's ranking penalty; RET-6's future drift banner) must agree on."""
    return os.path.join(index_dir, _STALE_CACHE_NAME)


def write_stale_cache(
    index_dir: str, stale: List[dict], evidence_drift: Optional[Dict[str, dict]] = None
) -> bool:
    """Persist ``find_stale``'s result to ``<index_dir>/stale.json`` (RET-5/RET-6 setup).

    Derived, rebuildable, gitignored — same standing as ``links.json``/``manifest.json``,
    and written the same way (tmp + ``os.replace``, ``links.write_links_cache``'s pattern)
    so a reader never sees a torn file. The shape is the MINIMUM a later bounded ranking
    penalty and a one-line "anchored to <sha>; verify before relying" banner both need,
    per stale name::

        {"schema_version": 1, "generated_at": "<iso8601>",
         "stale": {"<name>": {"changed": <len(changed_paths)>, "sha": "<short source_commit>"}}}

    ``evidence_drift`` (CLB-3) is an OPTIONAL top-level field, written only when
    non-empty (absence-emits-nothing, no schema bump — ``read_stale_cache`` never
    looks at it, so RET-5's penalty population is untouched)::

        "evidence_drift": {"<name>": {"fences": M, "missing": K, "whitespace": W}}

    Written on EVERY call, including an empty ``stale`` list — an honest
    ``{"stale": {}}`` means "checked this session, found nothing", never a skipped write,
    so a reader can trust the file's mere presence rather than guess whether staleness
    ever ran. ``read_stale_cache`` (below) is the reader half — recall.py's RET-5 salience
    blend is its first consumer, treating an absent/corrupt file as advisory — a no-op,
    never a hard error. ``read_evidence_drift`` is the CLB-3 reader (RET-6's banner).
    Never raises; returns True on a successful write, False on any failure (a bad
    ``index_dir``, a permissions error) — callers must not let a cache-write failure
    cost them the SessionStart run itself.
    """
    try:
        os.makedirs(index_dir, exist_ok=True)
        payload = {
            "schema_version": STALE_CACHE_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stale": {
                item["name"]: {
                    "changed": len(item.get("changed_paths") or []),
                    "sha": str(item.get("source_commit") or "")[:_SHORT_SHA_LEN],
                }
                for item in stale
            },
        }
        if evidence_drift:
            payload["evidence_drift"] = {
                str(name): {
                    "fences": int(rec.get("fences") or 0),
                    "missing": int(rec.get("missing") or 0),
                    "whitespace": int(rec.get("whitespace") or 0),
                }
                for name, rec in sorted(evidence_drift.items())
                if isinstance(rec, dict)
            }
        path = stale_cache_path(index_dir)
        tmp = path + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        return True
    except Exception:
        return False


def read_evidence_drift(index_dir: str) -> Dict[str, dict]:
    """CLB-3's reader half — ``{"<name>": {"fences", "missing", "whitespace"}}`` from
    ``stale.json``'s optional ``evidence_drift`` field; ``{}`` when the cache (or the
    field) is absent, corrupt, or schema-mismatched. Deliberately SEPARATE from
    ``read_stale_cache`` so RET-5's penalty population can never silently widen to
    evidence-drifted names — the banner (RET-6) is this field's only recall-side
    consumer. Same advisory, never-a-git-call posture as its sibling. Never raises.
    """
    try:
        with open(stale_cache_path(index_dir), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return {}
        if payload.get("schema_version") != STALE_CACHE_SCHEMA_VERSION:
            return {}
        drift = payload.get("evidence_drift")
        if not isinstance(drift, dict):
            return {}
        return {
            name: rec
            for name, rec in drift.items()
            if isinstance(name, str) and isinstance(rec, dict)
        }
    except Exception:
        return {}


def read_stale_cache(index_dir: str) -> Optional[Dict[str, dict]]:
    """The RET-5 reader half of ``write_stale_cache`` — ``{"<name>": {"changed", "sha"}}``,
    or ``None`` when the cache is absent, corrupt, or schema-mismatched.

    Advisory, same posture as ``links.load_edges``: this is a single small-JSON read of a
    file SessionStart already refreshed once per run (never a git call — the staleness scan
    that produced it already paid that cost, and belongs to ``_build_run_context``, not the
    hot path). A missing file (index never built, or predates LIF-6) or a schema mismatch
    both degrade to ``None`` -- recall.py's salience blend (RET-5) treats that identically to
    "nothing stale", never a hard error. Never raises.
    """
    try:
        with open(stale_cache_path(index_dir), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != STALE_CACHE_SCHEMA_VERSION:
            return None
        stale = payload.get("stale")
        if not isinstance(stale, dict):
            return None
        return {name: rec for name, rec in stale.items() if isinstance(name, str) and isinstance(rec, dict)}
    except Exception:
        return None
