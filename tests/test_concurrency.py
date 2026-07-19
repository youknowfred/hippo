"""QUA-4 — concurrency race tests over the telemetry ledger and the recall index.

COR-12 made the index manifest/dense writes atomic (dense.npy replaced BEFORE
manifest.json, both via tmp + os.replace). This module is the "prove it" half:

  - Ledger: multiple REAL processes append to one telemetry ledger concurrently, with
    rotation forced (a tiny HIPPO_TELEMETRY_MAX_BYTES). telemetry.py's own docstring
    concedes the single-writer-assumption rotation may drop a line under a concurrent
    writer race -- the bar here is NEVER a crash and NEVER a torn/corrupt line, not
    zero drops.

  - Index: one thread repeatedly rebuilds the index (build_index) while another
    repeatedly loads it (load_index) and calls recall() against it, asserting the
    reader never observes a manifest claiming dense_ready=True paired with a missing,
    wrong-shape, or entry-count-mismatched dense.npy.

Both use real OS-level interleaving (separate processes for the ledger -- mirroring the
real hook model; threads for the index reader, since the atomicity guarantee itself is
at the os.replace/filesystem level, not the GIL) rather than mocks, so the assertions
are about actual file-level races, not simulated ones.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
import time


def _mem(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\nbody text\n'


def _write_corpus(memory_dir: str, n: int) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for i in range(n):
        with open(os.path.join(memory_dir, f"m{i:03d}.md"), "w", encoding="utf-8") as fh:
            fh.write(_mem(f"m{i:03d}", f"memory number {i} alpha beta gamma"))


# --------------------------------------------------------------------------- #
# Ledger concurrency (telemetry.py) -- top-level worker fns (picklable for spawn)
# --------------------------------------------------------------------------- #
def _ledger_appender_worker(telemetry_dir: str, n_events: int, tag: int, err_queue) -> None:
    try:
        import memory.telemetry as T

        for i in range(n_events):
            T.log_recall_event(
                [{"name": f"mem-{tag}-{i}", "backend": "bm25"}],
                query=f"query {tag} {i}",
                k=5,
                latency_ms=1.5,
                telemetry_dir=telemetry_dir,
            )
    except BaseException as exc:  # a worker crash is itself the failure this test guards against
        err_queue.put(f"worker {tag} crashed: {exc!r}")


def _episode_appender_worker(telemetry_dir: str, n_events: int, tag: int, err_queue) -> None:
    try:
        import memory.telemetry as T

        for i in range(n_events):
            T.log_episode([f"mem-{tag}-{i}"], query=f"query {tag} {i}", telemetry_dir=telemetry_dir)
    except BaseException as exc:
        err_queue.put(f"worker {tag} crashed: {exc!r}")


def test_ledger_concurrent_appenders_no_crash_no_torn_lines_across_rotation(tmp_path, monkeypatch):
    """N processes hammer ONE ledger concurrently, forcing many rotations.

    Asserts: no worker raised/crashed, and every line surviving in the final ledger
    file is complete, valid JSON with the expected event schema (never a torn/partial
    or corrupt line) -- lost lines from the single-writer-assumption rotation race are
    tolerated (documented behavior), but corruption or a crash is not.
    """
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "4000")  # force many rotations
    telemetry_dir = str(tmp_path / "tele")
    os.makedirs(telemetry_dir)

    n_workers = 6
    n_events_per_worker = 150  # 900 total appends -> comfortably north of a few rotations

    ctx = multiprocessing.get_context("spawn")
    err_queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_ledger_appender_worker,
            args=(telemetry_dir, n_events_per_worker, tag, err_queue),
        )
        for tag in range(n_workers)
    ]
    start = time.monotonic()
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    elapsed = time.monotonic() - start

    errors = []
    while not err_queue.empty():
        errors.append(err_queue.get())
    assert errors == []  # no worker crashed

    for p in procs:
        assert p.exitcode == 0, f"worker exited with {p.exitcode}"
        assert not p.is_alive(), f"worker still running after {elapsed:.1f}s (hang)"

    ledger_path = os.path.join(telemetry_dir, "recall_events.jsonl")
    assert os.path.exists(ledger_path)

    import json

    with open(ledger_path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    assert raw == "" or raw.endswith("\n")  # never a torn trailing partial line

    lines = [ln for ln in raw.split("\n") if ln]
    assert len(lines) > 0  # rotation must never wipe out everything

    for ln in lines:
        obj = json.loads(ln)  # raises (fails the test) on any corrupt/torn line
        assert isinstance(obj, dict)
        assert set(obj.keys()) == {
            "ts",
            "session_id",
            "names",
            "scores",  # COR-8: true fused score per result, parallel to "names"
            "ranks",  # COR-8: 1-based emission rank per result, parallel to "names"
            "backend",
            "latency_ms",
            "k",
            "query_preview",
            "v",  # MEA-4: producer-version stamp (the dev-tree manifest is always readable here)
        }
        assert obj["backend"] == "bm25"
        assert obj["k"] == 5

    # The read surface (read_events) must also never choke on the concurrently-written file.
    import memory.telemetry as T

    read_back = list(T.read_events(telemetry_dir))
    assert len(read_back) == len(lines)


def test_episode_ledger_concurrent_appenders_no_crash_no_torn_lines(tmp_path, monkeypatch):
    """Same race, over log_episode's ledger (a separate file, same _rotate_if_needed path)."""
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "4000")
    telemetry_dir = str(tmp_path / "tele")
    os.makedirs(telemetry_dir)

    ctx = multiprocessing.get_context("spawn")
    err_queue = ctx.Queue()
    n_workers = 6
    procs = [
        ctx.Process(target=_episode_appender_worker, args=(telemetry_dir, 150, tag, err_queue))
        for tag in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    errors = []
    while not err_queue.empty():
        errors.append(err_queue.get())
    assert errors == []
    for p in procs:
        assert p.exitcode == 0

    import json

    ledger_path = os.path.join(telemetry_dir, "episode_buffer.jsonl")
    with open(ledger_path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    assert raw == "" or raw.endswith("\n")
    lines = [ln for ln in raw.split("\n") if ln]
    assert len(lines) > 0
    for ln in lines:
        obj = json.loads(ln)
        assert isinstance(obj, dict)
        assert set(obj.keys()) == {
            "ts",
            "session_id",
            "query_preview",
            "recalled_names",
            "head_commit",
        }


# --------------------------------------------------------------------------- #
# Index reader-during-rebuild concurrency (build_index.py)
# --------------------------------------------------------------------------- #
def _assert_manifest_dense_consistent(loaded) -> None:
    """The invariant a reader must never see violated: ``loaded.dense_ready`` (the
    SAFE, post-load-time-consistency-check flag callers actually branch on -- not the
    raw, possibly-stale ``manifest["dense_ready"]`` a racing rebuild can leave pointing
    at a dense.npy that no longer matches) must never be True paired with a dense.npy
    that's missing, wrong-shape, or has an out-of-bounds/absent per-entry row index."""
    if not loaded.dense_ready:
        return
    assert loaded.dense is not None, "dense_ready=True but dense.npy failed to load"
    n_entries = len(loaded.entries)
    assert loaded.dense.shape[0] == n_entries, (
        f"dense.npy has {loaded.dense.shape[0]} rows but manifest has {n_entries} entries"
    )
    for e in loaded.entries:
        row = e.get("row")
        assert row is not None, "dense_ready=True but an entry has row=None"
        assert 0 <= row < loaded.dense.shape[0], "entry row index out of bounds for dense.npy"


def test_index_reader_never_observes_torn_manifest_dense_pair_during_rebuild(tmp_path, monkeypatch):
    """One thread rebuilds the index in a loop; another loads it + recalls in a loop.

    The writer both (a) toggles HIPPO_DISABLE_DENSE between rebuilds -- exercising
    BOTH the dense.npy-write branch and the stale-dense-removal branch COR-12 made
    atomic -- and (b) VARIES THE CORPUS SIZE each rebuild (memories added/removed
    between SessionStart refreshes is the realistic trigger): a reader that reads
    manifest.json and dense.npy as two SEPARATE files (exactly what load_index does)
    can catch the OLD manifest (dense_ready=True, N entries) paired with the NEXT
    build's dense.npy (a different row count) even though each individual write is
    atomic -- the reader must never observe that torn pairing.

    load_index()'s manifest-read-then-dense-read gap is normally sub-millisecond, so
    naive interleaving rarely straddles it inside a fast test run. The reader widens
    that EXACT gap with a small, deliberate sleep between the two reads (reproducing
    load_index()'s own composition, not a different code path) so the race reliably
    lands within a bounded number of iterations instead of depending on rare scheduler
    luck -- this is the standard technique for deterministically exercising a TOCTOU
    window in a test.
    """
    from memory import build_index as B
    from memory import recall as R

    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, 10)

    def fake_embed_documents(texts, allow_download=True):
        import numpy as np

        return np.vstack(
            [np.full(8, float((hash(t) % 1000) + 1), dtype="float32") for t in texts]
        )

    monkeypatch.setattr(B, "embed_documents", fake_embed_documents)

    stop = threading.Event()
    errors: list = []
    n_iterations = 150

    def writer():
        try:
            for i in range(n_iterations):
                if stop.is_set():
                    return
                # Corpus size oscillates 10..29 -> each rebuild's entry count (and
                # hence dense.npy's row count) differs from the one before it.
                for f in os.listdir(md):
                    os.remove(os.path.join(md, f))
                _write_corpus(md, 10 + (i % 20))
                dense_on = i % 2 == 0
                if dense_on:
                    os.environ.pop("HIPPO_DISABLE_DENSE", None)
                else:
                    os.environ["HIPPO_DISABLE_DENSE"] = "1"
                B.build_index(md, idx, force=True)
        except BaseException as exc:  # noqa: BLE001 -- surfaced via `errors`, not silently lost
            errors.append(f"writer crashed: {exc!r}")
        finally:
            stop.set()

    checks = [0]

    def reader():
        try:
            while not stop.is_set():
                # load_index() itself is just _load_manifest() followed by _load_dense()
                # (see build_index.load_index) -- reproduced here with a deliberate
                # sleep IN BETWEEN so a rebuild landing in that gap is exercised on
                # close to every iteration.
                manifest = B._load_manifest(idx)
                if not manifest:
                    continue
                time.sleep(0.01)
                dense = B._load_dense(idx) if manifest.get("dense_ready") else None
                loaded = B.LoadedIndex(manifest, dense)
                checks[0] += 1
                _assert_manifest_dense_consistent(loaded)
                # recall() must not crash even if it observes an index mid-transition.
                R.recall("memory number 5 alpha", index=loaded, memory_dir=md)
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"reader crashed: {exc!r}")

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    writer_thread.start()
    reader_thread.start()
    writer_thread.join(timeout=60)
    stop.set()
    reader_thread.join(timeout=60)

    assert not writer_thread.is_alive(), "writer thread hung"
    assert not reader_thread.is_alive(), "reader thread hung"
    assert errors == []
    assert checks[0] > 0  # the reader actually got to run concurrently with the writer

    # Final state is fully consistent too.
    final = B.load_index(idx)
    assert final is not None
    _assert_manifest_dense_consistent(final)
