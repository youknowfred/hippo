"""TND-7 (HOT-10's other half): the sleep run keeps the dense-floor sweep current.

``hippo eval --floor-sweep`` embeds every fixture query with the dense model (2 to 8 s
cold), so it has no place on SessionStart; doctor only reads the report it leaves and warns
when that report is stale (measured on a corpus, or with a model, that is no longer this
one). A sleep run refreshes it then, and only then, and only offline:

  current / none  nothing to do (a corpus with no sweep is not given one: the sweep is
                  opt-in and needs fixtures; the line names the command that writes one)
  refreshed       the sweep re-ran on the cached model and its report is current again
  skipped_*       stale, but the run could not measure it: dense recall is off, the
                  model is not in the offline cache (hippo never downloads it here), or the
                  index has no dense vectors, or the corpus has no fixtures to measure
                  with. The line names the reason.
  failed          the sweep itself refused (e.g. no off-topic fixture); its error is named

Each outcome is counted in the sleep state (``floor_sweep_counts``), so a corpus whose
sweep is skipped every night shows it. The run reads the corpus and writes only the
derived ``floor_sweep.json`` the sweep has always written.
"""

from __future__ import annotations

from typing import Optional, Tuple

_CMD = "hippo eval --floor-sweep"


def stale_reason(memory_dir: str) -> Tuple[Optional[str], Optional[dict]]:
    """``(why it is stale or None, the sweep or None)`` — doctor's own two staleness legs:
    another embedding model, or a corpus fingerprint that is not the current index's."""
    from .build_index import DEFAULT_MODEL, _load_manifest, default_index_dir, load_index
    from .eval_floor import read_floor_sweep
    from .eval_ledger import corpus_fingerprint

    sweep = read_floor_sweep(memory_dir)
    if sweep is None:
        return None, None
    if sweep.get("model") and sweep.get("model") != DEFAULT_MODEL:
        return f"measured with {sweep.get('model')}", sweep
    index_dir = default_index_dir(memory_dir)
    if _load_manifest(index_dir) is not None:
        index = load_index(index_dir)
        if index is not None and len(index) and sweep.get("corpus_fingerprint") != corpus_fingerprint(index):
            return f"the corpus changed since {sweep.get('generated_at') or 'it ran'}", sweep
    return None, sweep


def _model_cached_offline() -> bool:
    from .build_index import _fastembed_model_cached, ensure_fastembed_cache_path

    return _fastembed_model_cached(ensure_fastembed_cache_path())


def _fixture(memory_dir: str, repo_root: str, filename: str) -> Optional[str]:
    """The sweep's own fixture probe order, anchored on THIS corpus (not the cwd's)."""
    import os

    for path in (os.path.join(memory_dir, ".audit-fixtures", filename),
                 os.path.join(repo_root, "tests", "fixtures", filename)):
        if os.path.exists(path):
            return path
    return None


def refresh(memory_dir: str, repo_root: str) -> Tuple[str, str]:
    """``(outcome, one report line)``. Never raises; never downloads a model."""
    try:
        why, sweep = stale_reason(memory_dir)
        if sweep is None:
            return "none", f"floor sweep: none recorded (`{_CMD}` writes one); nothing refreshed."
        if why is None:
            return "current", f"floor sweep: current (from {sweep.get('generated_at') or 'an earlier run'})."
        from .build_index import dense_disabled, default_index_dir, load_index

        stale = f"floor sweep: stale ({why})"
        if dense_disabled():
            return "skipped_dense_off", f"{stale}; not refreshed: dense recall is off on this machine."
        if not _model_cached_offline():
            return ("skipped_model_not_cached", f"{stale}; not refreshed: the embedding model is not "
                    "in the offline cache, and a sleep run never downloads it (run /hippo:setup).")
        index = load_index(default_index_dir(memory_dir))
        if index is None or not index.dense_ready:
            return ("skipped_no_dense_index", f"{stale}; not refreshed: the index has no dense "
                    "vectors yet (the next index refresh adds them).")
        from .eval_floor import floor_sweep

        hard = _fixture(memory_dir, repo_root, "recall_hard_set.yaml")
        probes = _fixture(memory_dir, repo_root, "recall_abstention_set.yaml")
        if hard is None or probes is None:
            missing = "recall_hard_set.yaml" if hard is None else "recall_abstention_set.yaml"
            return ("skipped_no_fixtures", f"{stale}; not refreshed: no {missing} for this "
                    "corpus (in .audit-fixtures/ beside its memories).")
        doc = floor_sweep(memory_dir, hard_set_path=hard, abstention_set_path=probes)
        if not doc.get("ok"):
            return "failed", f"{stale}; the refresh failed: {doc.get('error')}"
        if not doc.get("path"):
            return "failed", f"{stale}; the refresh ran but its report was not written."
        return ("refreshed", f"floor sweep: refreshed ({why}); recommended {doc.get('recommended')}, "
                f"configured {doc.get('configured_floor')}.")
    except Exception as exc:
        return "failed", f"floor sweep: the refresh failed: {type(exc).__name__}: {exc}"


def run_pass(memory_dir: str, repo_root: str, state: dict) -> Tuple[str, str]:
    """``refresh`` plus its count in ``state`` (the caller persists the sleep state)."""
    outcome, line = refresh(memory_dir, repo_root)
    counts = state.get("floor_sweep_counts")
    if not isinstance(counts, dict):
        counts = {}
    counts[outcome] = int(counts.get(outcome) or 0) + 1
    state["floor_sweep_counts"] = counts
    return outcome, line
