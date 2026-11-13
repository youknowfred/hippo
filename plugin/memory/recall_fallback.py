"""OBS-11: why a recall ran BM25-only — a closed set of reason tokens, and doctor's line.

A hook recall whose backend label is ``bm25`` carries one of these tokens beside the label,
on the raw recall event (``backend_reason``) and in the daily rollup
(``hook.backend_reason``). The token is a value the code already holds at the point it falls
back — the branch taken in ``_dense_rank_rows``, the exception it caught, or the shape of the
result — so naming it costs nothing on the hot path: no probe, no stat, no extra read.

Every path to a ``bm25`` label has a token, so "every BM25-only recall carries a reason" holds
by construction; a cause this table does not name is recorded as ``unknown``, never as a
missing field. Rollup rows written before reasons were kept count as ``unrecorded``.
"""

from __future__ import annotations

from typing import Dict, List, Optional

DENSE_DISABLED = "dense_disabled"  # HIPPO_DISABLE=dense / HIPPO_DISABLE_DENSE at query time
NO_DENSE_INDEX = "no_dense_index"  # the index was built without vectors
DENSE_MATRIX_UNUSABLE = "dense_matrix_unusable"  # manifest says dense, the matrix did not load
FUSED_WITHOUT_DENSE = "fused_without_dense"  # a user/private/outbox tier has no vectors
MODEL_MISMATCH = "model_mismatch"  # index embedded with another model than configured
MODEL_NOT_WARMED = "model_not_warmed"  # the model is not in the offline cache
DENSE_TIMEOUT = "dense_timeout"  # the query embed ran past HIPPO_DENSE_TIMEOUT
DENSE_ERROR = "dense_error"  # the dense lane raised anything else
NO_DENSE_MATCH = "no_dense_match"  # dense ran; no description row cleared its floor
RULES_ONLY = "rules_only"  # only a rules-plane section matched (lexical by design)
UNKNOWN = "unknown"
UNRECORDED = "unrecorded"  # doctor only: rollup rows from before reasons were kept

REASONS = (
    DENSE_DISABLED, NO_DENSE_INDEX, DENSE_MATRIX_UNUSABLE, FUSED_WITHOUT_DENSE, MODEL_MISMATCH,
    MODEL_NOT_WARMED, DENSE_TIMEOUT, DENSE_ERROR, NO_DENSE_MATCH, RULES_ONLY, UNKNOWN,
)

# The plain-words remedy doctor prints beside the top reason. No roadmap ids, no `python -m`.
REMEDY = {
    DENSE_DISABLED: "dense is turned off by HIPPO_DISABLE (or HIPPO_DISABLE_DENSE) in this "
    "environment; remove it to get dense recall back",
    NO_DENSE_INDEX: "the index was built without dense vectors; run /hippo:setup to warm the "
    "model, then `hippo build-index`",
    DENSE_MATRIX_UNUSABLE: "the dense matrix did not match the manifest (missing or torn); "
    "`hippo build-index` rebuilds it",
    FUSED_WITHOUT_DENSE: "a user, private or outbox tier has no dense vectors (or another "
    "model's), so the fused recall ran BM25-only; SessionStart re-embeds it once the model is "
    "warm (/hippo:setup)",
    MODEL_MISMATCH: "the index was embedded with a different model than the one configured; "
    "`hippo build-index` re-embeds it",
    MODEL_NOT_WARMED: "the dense model is not in the offline cache; run /hippo:setup to warm it",
    DENSE_TIMEOUT: "the query embed ran past HIPPO_DENSE_TIMEOUT (5 s by default); warm recall "
    "(`hippo setup --warm`) keeps the model loaded between prompts",
    DENSE_ERROR: "the dense lane raised an error; `hippo build-index` rebuilds the index, and "
    "the index lines above say whether it is healthy",
    NO_DENSE_MATCH: "dense ran but no memory cleared its floor, so BM25 matched alone; nothing "
    "to fix",
    RULES_ONLY: "only a rules-plane section matched, which is lexical by design; nothing to fix",
    UNKNOWN: "a cause this version does not name",
}


def unavailable_reason(index) -> str:
    """The token for ``_dense_rank_rows``' no-matrix branch, from values already loaded.
    ``dense_disabled`` is the settings read ``load_index`` itself made (environment only)."""
    from .build_index import dense_disabled

    if dense_disabled():
        return DENSE_DISABLED
    manifest = getattr(index, "manifest", None) or {}
    if manifest.get("fused_without_dense"):
        return FUSED_WITHOUT_DENSE
    if not manifest.get("dense_ready"):
        return NO_DENSE_INDEX
    return DENSE_MATRIX_UNUSABLE


def exception_reason(exc: BaseException) -> str:
    """The token for an exception the dense lane caught."""
    from .build_index import DenseTimeout, DenseUnavailable

    if isinstance(exc, DenseTimeout):
        return DENSE_TIMEOUT
    if isinstance(exc, DenseUnavailable) and exc.reason in REASONS:
        return exc.reason
    return DENSE_ERROR


def result_reason(results: List[dict], why: List[str]) -> Optional[str]:
    """The reason for a recall's results, or ``None`` when its label is not ``bm25``.
    ``why`` is what ``_dense_rank_rows`` appended when it returned nothing."""
    if not results or results[0].get("backend") != "bm25":
        return None
    if results[0].get("via") == "rules":
        return RULES_ONLY
    if why:
        return why[0] if why[0] in REASONS else UNKNOWN
    return NO_DENSE_MATCH


def window_reasons(rows: List[dict]) -> Dict[str, int]:
    """Reason counts over rollup rows; the part of each day's ``bm25`` count no reason
    covers (rows from before reasons were kept) counts as ``unrecorded``."""
    out: Dict[str, int] = {}
    for row in rows:
        hook = row.get("hook") or {}
        reasons = hook.get("backend_reason") or {}
        for k, v in reasons.items():
            out[k] = out.get(k, 0) + int(v)
        gap = int((hook.get("backend") or {}).get("bm25") or 0) - sum(int(v) for v in reasons.values())
        if gap > 0:
            out[UNRECORDED] = out.get(UNRECORDED, 0) + gap
    return out


def check_bm25_reasons(ctx) -> Dict[str, str]:
    """Doctor: over 30 days of rollups, how many hook recalls ran BM25-only, the top named
    reason and its remedy. Informational: ``ok`` always (no neighbouring check sets a
    threshold on the backend mix); ``warn`` only when the read itself fails."""
    try:
        from .telemetry import default_telemetry_dir
        from .telemetry_rollup import read_rollups

        rows = read_rollups(default_telemetry_dir(ctx.memory_dir), days=30)
        ran = sum(int(v) for r in rows for v in ((r.get("hook") or {}).get("backend") or {}).values())
        counts = window_reasons(rows)
        total = sum(counts.values())
        if not total:
            note = f" of {ran} recalls that ran" if ran else " (no hook recalls recorded yet)"
            return {"status": "ok", "message": f"BM25-only recalls (30 days): none{note}."}
        named = sorted(((k, n) for k, n in counts.items() if k != UNRECORDED), key=lambda kv: (-kv[1], kv[0]))
        msg = f"BM25-only recalls (30 days): {total} of {ran} recalls that ran"
        if named:
            top, n = named[0]
            msg += f"; top reason {top} ({n}): {REMEDY.get(top, REMEDY[UNKNOWN])}"
            if len(named) > 1:
                msg += "; then " + ", ".join(f"{k} ({v})" for k, v in named[1:4])
        if counts.get(UNRECORDED):
            msg += f"; {counts[UNRECORDED]} recorded before reasons were kept"
        return {"status": "ok", "message": msg + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"BM25-only reasons check failed: {exc}."}
