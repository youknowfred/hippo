"""HCR-7 bench: the embedding daemon against the in-process embedder, on real models.

Three commands, run one after another (one MLX process at a time):

    PY=<plugin venv>/bin/python; export OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:.
    $PY -m bench.embed_daemon vectors-local  --work W [--live LABEL=MEMORY_DIR] --out local.npz
    $PY -m bench.embed_daemon vectors-daemon --work W [--live ...] --local local.npz --out vectors.json
    $PY -m bench.embed_daemon servers --project P --data D --queries hard_set.yaml --out servers.json

``vectors-local`` loads the model in this process (what every HCR evidence file was read on):
it embeds each corpus's rows into a twin index and each hard-set query, alone and in batches.
``vectors-daemon`` does the same through the daemon and compares the bytes: queries one at a
time, queries arriving ten at once (coalesced into shared forward passes, and again with
``HIPPO_EMBED_DAEMON_BATCH=0``), and whole index builds.

``servers`` starts real MCP servers (``python -m memory.mcp_server``) on the high profile
against one scratch project, drives their ``recall_hook`` tool the way the prompt hook does,
and measures what the roadmap's acceptance names: one daemon from simultaneous starts, each
server's and the daemon's ``phys_footprint``, a burst of simultaneous prompts against
``SERVE_BUDGET_MS``, and the three daemon faults (absent, slow, killed mid-call).

``--work`` is ``bench.space_calibration``'s scratch layout (corpus copies under
``<work>/<tag>/<label>``, plugin data under ``<work>/plugin-data`` with the ``mlx`` and
``fastembed`` caches linked in). Outputs hold counts, timings and byte comparisons, no query
text and no memory name; they are still written outside the repository.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

_T0 = time.monotonic()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPACE = "Qwen/Qwen3-Embedding-4B"
TAG = "Qwen3-Embedding-4B-whole"


def _log(msg: str) -> None:
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Intervals: every reported number carries one
# --------------------------------------------------------------------------- #
def wilson(k: int, n: int) -> list:
    """95% Wilson interval for k of n."""
    if n == 0:
        return [None, None]
    z, p = 1.959964, k / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return [round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)]


def boot(xs, stat, n_boot: int = 2000, seed: int = 7) -> list:
    """95% percentile-bootstrap interval for ``stat`` of ``xs``."""
    xs = list(xs)
    if len(xs) < 2:
        return [None, None]
    rng = random.Random(seed)
    got = sorted(stat([xs[rng.randrange(len(xs))] for _ in xs]) for _ in range(n_boot))
    return [round(got[int(0.025 * n_boot)], 2), round(got[int(0.975 * n_boot) - 1], 2)]


def pct(p: float):
    return lambda xs: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]


def summary(xs, unit: str = "ms") -> dict:
    """Median, p95 and max with 95% bootstrap intervals (max: the observed maximum)."""
    xs = [float(x) for x in xs]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "unit": unit,
            "median": round(pct(0.5)(xs), 2), "median_ci95": boot(xs, pct(0.5)),
            "p95": round(pct(0.95)(xs), 2), "p95_ci95": boot(xs, pct(0.95)),
            "max": round(max(xs), 2)}


# --------------------------------------------------------------------------- #
# Shared setup
# --------------------------------------------------------------------------- #
def _env_for(work: str) -> str:
    pd = os.path.join(work, "plugin-data")
    os.environ.update({
        "CLAUDE_PLUGIN_DATA": pd, "HIPPO_USER_MEMORY_DIR": os.path.join(work, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(work, "no-private-tier"),
        "HIPPO_TRUST_ALL": "1", "HIPPO_TRUST_FILE": os.path.join(work, "trust.json"),
        "HF_HUB_OFFLINE": "1", "HIPPO_DENSE_TIMEOUT": "120",
    })
    for v in ("HIPPO_DENSE_FLOOR", "HIPPO_DISABLE", "HIPPO_DISABLE_DENSE", "HIPPO_RETRIEVAL_PROFILE",
              "HIPPO_HIGH_EMBED_MODEL", "HIPPO_HIGH_EMBED_DIM", "HIPPO_RERANK"):
        os.environ.pop(v, None)
    return pd


def _corpora(work: str, live: list, heldout: str = "") -> dict:
    """label -> (memory dir, [queries]) for the corpus copies under ``<work>/<TAG>``: each
    corpus's hard set, plus HCR-8's held-out questions (a private JSONL) for its label."""
    from memory.eval_metrics import load_hard_set

    more: dict = {}
    for line in open(heldout, encoding="utf-8") if heldout else ():
        if line.strip():
            r = json.loads(line)
            if r.get("query") and not r.get("skip"):
                more.setdefault(r["corpus"], []).append(r["query"])
    out = {}
    g, f = os.path.join(_ROOT, "tests", "golden_corpus"), os.path.join(_ROOT, "tests", "fixtures")
    sets = {"golden": os.path.join(g, "hard_set.yaml"), "packs": os.path.join(f, "recall_hard_set.yaml")}
    for spec in live:
        label = spec.split("=", 1)[0]
        sets[label] = os.path.join(work, TAG, label, ".claude", "memory", ".audit-fixtures", "recall_hard_set.yaml")
    for label, hard in sets.items():
        md = os.path.join(work, TAG, label, ".claude", "memory")
        if not os.path.isdir(md):
            raise SystemExit(f"no corpus copy at {md}: run bench.space_calibration --build-only first")
        qs = [r["query"] for r in (load_hard_set(hard) if os.path.exists(hard) else []) if r.get("query")]
        out[label] = (md, qs + more.get(label, []))
    return out


def _build_twin(md: str, name: str) -> str:
    """A complete twin of ``md``'s lean index in SPACE at ``<index>/<name>``; its directory."""
    from memory import embed_mlx as E
    from memory.build_index import build_index, default_index_dir, load_index

    import shutil

    idir = default_index_dir(md)
    build_index(md, idir, allow_download=True)
    tdir = os.path.join(idir, name)
    shutil.rmtree(tdir, ignore_errors=True)  # every row embedded now, none reused
    for _ in range(8):
        with E.using_space(SPACE):
            build_index(md, tdir, allow_download=True)
        hi = load_index(tdir)
        if hi is not None and hi.dense_ready:
            return tdir
    raise SystemExit(f"the twin at {tdir} never became dense-ready")


def _same(a, b) -> dict:
    """Byte comparison of two float32 matrices, row by row."""
    import numpy as np

    a, b = np.asarray(a, dtype="<f4"), np.asarray(b, dtype="<f4")
    if a.shape != b.shape:
        return {"rows": int(a.shape[0]), "shape_mismatch": [list(a.shape), list(b.shape)]}
    rows_equal = int(sum(a[i].tobytes() == b[i].tobytes() for i in range(a.shape[0])))
    out = {"rows": int(a.shape[0]), "rows_byte_identical": rows_equal,
           "identical_ci95": wilson(rows_equal, int(a.shape[0]))}
    if rows_equal != a.shape[0]:
        an = a / np.linalg.norm(a, axis=1, keepdims=True)
        bn = b / np.linalg.norm(b, axis=1, keepdims=True)
        out["max_abs_diff"] = float(np.max(np.abs(a - b)))
        out["min_cosine"] = float(np.min(np.sum(an * bn, axis=1)))
    return out


# --------------------------------------------------------------------------- #
# vectors-local / vectors-daemon
# --------------------------------------------------------------------------- #
def vectors_local(args) -> int:
    import numpy as np

    os.environ["HIPPO_EMBED_DAEMON"] = "0"
    _env_for(os.path.abspath(args.work))
    from memory import embed_mlx as E

    corpora = _corpora(os.path.abspath(args.work), args.live, args.heldout)
    emb = E.high_embedder(SPACE)
    assert type(emb).__name__ == "_MLXEmbedder", type(emb)
    _log(f"loaded in-process: {emb.load_ms:.0f} ms, {emb.resident_bytes / 2**30:.2f} GiB")
    save, doc = {}, {"space": SPACE, "load_ms": round(emb.load_ms, 1), "resident_bytes": emb.resident_bytes,
                     "corpora": {}}
    for label, (md, qs) in corpora.items():
        t0 = time.monotonic()
        tdir = _build_twin(md, "hcr7-local")
        from memory.build_index import default_index_dir, load_index

        local = np.array(load_index(tdir).dense)
        shipped = load_index(os.path.join(default_index_dir(md), "high"))
        single = np.stack([list(emb.query_embed([q]))[0] for q in qs])
        whole = np.stack(list(emb.query_embed(qs)))
        tens = np.concatenate([np.stack(list(emb.query_embed(qs[i:i + 10]))) for i in range(0, len(qs), 10)])
        again = np.stack([list(emb.query_embed([q]))[0] for q in qs])
        save.update({f"{label}/docs": local, f"{label}/q_single": single})
        doc["corpora"][label] = {
            "document_rows": int(local.shape[0]), "queries": len(qs),
            "build_seconds": round(time.monotonic() - t0, 1),
            "in_process_single_twice": _same(single, again),
            "in_process_single_vs_one_batch_of_all": _same(single, whole),
            "in_process_single_vs_batches_of_ten": _same(single, tens),
            "in_process_twin_vs_the_twin_built_for_v1_49_0": (
                _same(local, np.array(shipped.dense)) if shipped is not None and shipped.dense is not None else None),
        }
        _log(f"{label}: {json.dumps(doc['corpora'][label])}")
    np.savez(args.out, **save)
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    return 0


def _stop_daemon(space: str = SPACE) -> None:
    from memory import embed_remote as X

    info = X.hello(space, timeout=1.0)
    if info is None:
        return
    try:
        X.call(X.paths(space).sock, {"op": "shutdown"}, 2.0)
    except Exception:
        pass
    end = time.monotonic() + 15
    while time.monotonic() < end and X.hello(space, timeout=0.5) is not None:
        time.sleep(0.1)
    try:
        os.kill(int(info["pid"]), 0)
        time.sleep(1.0)
        os.kill(int(info["pid"]), signal.SIGKILL)
    except OSError:
        pass


def _burst(emb, qs, size: int = 10):
    """Embed ``qs`` ``size`` at a time, each from its own thread at the same moment (ten
    sessions prompting together). Returns the vectors in order and each call's wait in ms."""
    import numpy as np

    out, waits = [None] * len(qs), [0.0] * len(qs)
    for start in range(0, len(qs), size):
        gate = threading.Barrier(len(qs[start:start + size]))

        def one(i):
            gate.wait()
            t0 = time.monotonic()
            out[i] = list(emb.query_embed([qs[i]]))[0]
            waits[i] = (time.monotonic() - t0) * 1000.0

        threads = [threading.Thread(target=one, args=(i,)) for i in range(start, min(len(qs), start + size))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    return np.stack(out), waits


def vectors_daemon(args) -> int:
    import numpy as np

    os.environ["HIPPO_EMBED_DAEMON"] = "1"
    os.environ["HIPPO_EMBED_DAEMON_GRACE_S"] = "600"  # no warm server here: stay for the run
    _env_for(os.path.abspath(args.work))
    from memory import embed_mlx as E
    from memory import embed_remote as X
    from memory.build_index import load_index

    corpora = _corpora(os.path.abspath(args.work), args.live, args.heldout)
    local = np.load(args.local)
    doc = {"space": SPACE, "corpora": {}, "mlx_modules_in_this_process": None}
    _stop_daemon()
    os.environ["HIPPO_EMBED_DAEMON_BATCH"] = "1"
    emb = E.high_embedder(SPACE)
    assert isinstance(emb, X.RemoteEmbedder), type(emb)
    info = emb.ensure(load=True)
    doc["daemon"] = {"load_ms": info["load_ms"], "resident_bytes": info["resident_bytes"], "batch": info["batch"]}
    _log(f"daemon up: pid {info['pid']}, load {info['load_ms']} ms")
    single_ms: list = []
    for label, (md, qs) in corpora.items():
        rec: dict = {"queries": len(qs)}
        ref = local[f"{label}/q_single"]
        got = []
        for q in qs:
            t0 = time.monotonic()
            got.append(list(emb.query_embed([q]))[0])
            single_ms.append((time.monotonic() - t0) * 1000.0)
        rec["daemon_single_vs_in_process_single"] = _same(ref, np.stack(got))
        before = X.hello(SPACE, timeout=2.0)["stats"]
        burst, waits = _burst(emb, qs)
        after = X.hello(SPACE, timeout=2.0)["stats"]
        rec["daemon_ten_at_once_vs_in_process_single"] = _same(ref, burst)
        rec["ten_at_once"] = {"queries": after["queries"] - before["queries"],
                              "forward_passes": after["query_passes"] - before["query_passes"],
                              "largest_pass": after["largest_pass"], "wait_ms": summary(waits)}
        if label not in args.skip_build:
            t0 = time.monotonic()
            tdir = _build_twin(md, "hcr7-daemon")
            rec["build_seconds"] = round(time.monotonic() - t0, 1)
            rec["daemon_built_twin_vs_in_process_twin"] = _same(
                local[f"{label}/docs"], np.array(load_index(tdir).dense))
        doc["corpora"][label] = rec
        _log(f"{label}: {json.dumps(rec)}")
    doc["single_query_ms"] = summary(single_ms)
    _stop_daemon()
    os.environ["HIPPO_EMBED_DAEMON_BATCH"] = "0"  # the switch: one query per forward pass
    X._reset_for_tests()
    info = emb.ensure(load=True)
    assert info["batch"] is False
    for label, (md, qs) in corpora.items():
        before = X.hello(SPACE, timeout=2.0)["stats"]
        burst, waits = _burst(emb, qs)
        after = X.hello(SPACE, timeout=2.0)["stats"]
        doc["corpora"][label]["unbatched_ten_at_once_vs_in_process_single"] = _same(local[f"{label}/q_single"], burst)
        doc["corpora"][label]["unbatched_ten_at_once"] = {
            "queries": after["queries"] - before["queries"],
            "forward_passes": after["query_passes"] - before["query_passes"], "wait_ms": summary(waits)}
        _log(f"{label} unbatched: {json.dumps(doc['corpora'][label]['unbatched_ten_at_once'])}")
    _stop_daemon()
    doc["mlx_modules_in_this_process"] = sorted(m for m in sys.modules if m.split(".")[0] in ("mlx", "mlx_lm"))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


# --------------------------------------------------------------------------- #
# servers: real MCP servers on high, one daemon
# --------------------------------------------------------------------------- #
class Server:
    """One ``python -m memory.mcp_server`` and the prompt hook's side of its handshake."""

    def __init__(self, i: int, project: str, data: str, env: dict) -> None:
        self.sid = f"bench{i:02d}"
        self.project, self.data, self.n = project, data, 0
        self.err = open(os.path.join(data, f"server-{i:02d}.log"), "ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "memory.mcp_server"], env=env, cwd=project,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, text=True)

    def prompt(self, text: str) -> dict:
        """Serve one prompt the warm way. Returns the answer and this server's heartbeat."""
        from memory import recall_warm as W

        self.n += 1
        pid = f"p{self.n}"
        d = os.path.join(self.data, "warm")
        os.makedirs(d, exist_ok=True)
        assert W._create_claim(W.claim_path(d, self.sid, pid), "warm")
        req = {"jsonrpc": "2.0", "id": self.n, "method": "tools/call", "params": {
            "name": "recall_hook", "arguments": {"session_id": self.sid, "prompt_id": pid, "prompt": text,
                                                 "cwd": self.project}}}
        t0 = time.monotonic()
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        wall = (time.monotonic() - t0) * 1000.0
        if not line:
            return {"raised": True, "wall_ms": wall, "injected": False, "hb": {}}
        resp = json.loads(line)
        text_out = ((resp.get("result") or {}).get("content") or [{}])[0].get("text") or ""
        hb = self.heartbeat()
        return {"raised": "error" in resp or bool((resp.get("result") or {}).get("isError")),
                "wall_ms": wall, "injected": "hookSpecificOutput" in text_out, "hb": hb}

    def heartbeat(self) -> dict:
        try:
            with open(os.path.join(self.data, "warm", f"{self.sid}.server.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(10)
        except Exception:
            self.proc.kill()
        self.err.close()


def footprint_mb(pid: int):
    """``phys_footprint`` of a process in MB (macOS ``footprint``), or None."""
    try:
        out = subprocess.run(["footprint", str(pid)], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*(KB|MB|GB)", out) or re.search(
        r"Footprint:\s*([\d.]+)\s*(KB|MB|GB)", out)
    if not m:
        return None
    return round(float(m.group(1)) * {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}[m.group(2)], 1)


def _open_files(pid: int, *more: str) -> str:
    try:
        return subprocess.run(["lsof", "-nP", *more, "-p", str(pid)], capture_output=True, text=True,
                              timeout=60).stdout
    except Exception:
        return ""


def _daemon_pids(data: str) -> list:
    """Live ``memory.embed_daemon`` processes serving this run's plugin-data dir."""
    out = subprocess.run(["pgrep", "-f", "memory.embed_daemon"], capture_output=True, text=True).stdout
    pids = []
    for raw in out.split():
        env = subprocess.run(["ps", "eww", "-o", "command=", "-p", raw], capture_output=True, text=True).stdout
        if f"CLAUDE_PLUGIN_DATA={data}" in env:
            pids.append(int(raw))
    return pids


def _flood(space: str, seconds: float, stop: threading.Event, clients: int = 6) -> list:
    """Keep the daemon's MLX thread busy the way several builds at once do: ``clients``
    callers each sending long documents until ``stop``. A query then waits behind slices."""
    from memory import embed_remote as X

    text = " ".join(f"slice {i} of a long memory about deploy lanes and billing cutovers" for i in range(400))

    def one():
        end = time.monotonic() + seconds
        while not stop.is_set() and time.monotonic() < end:
            try:
                X.call(X.paths(space).sock, {"op": "embed", "space": space, "kind": "doc", "texts": [text] * 4}, 60.0)
            except Exception:
                time.sleep(0.05)

    threads = [threading.Thread(target=one, daemon=True) for _ in range(clients)]
    for t in threads:
        t.start()
    return threads


def _served(results: list, budget_ms: float) -> dict:
    """The acceptance read of a set of served prompts."""
    n = len(results)
    last = [r["hb"].get("last_ms") for r in results if r["hb"].get("last_ms") is not None]
    engaged = sum(1 for r in results if (r["hb"].get("high") or {}).get("engaged"))
    tripped = sum(1 for r in results if r["hb"].get("tripped"))
    raised = sum(1 for r in results if r["raised"])
    over = sum(1 for x in last if x > budget_ms)
    return {
        "prompts": n, "answered_with_a_block": sum(1 for r in results if r["injected"]),
        "raised": raised, "raised_ci95": wilson(raised, n),
        "breaker_tripped": tripped, "breaker_tripped_ci95": wilson(tripped, n),
        "served_high": engaged, "served_high_ci95": wilson(engaged, n),
        "served_lean": n - engaged, "served_lean_ci95": wilson(n - engaged, n),
        "over_budget": over, "over_budget_ci95": wilson(over, n), "budget_ms": budget_ms,
        "server_measured_ms": summary(last), "wall_ms_at_the_caller": summary([r["wall_ms"] for r in results]),
    }


def servers(args) -> int:
    os.environ["HIPPO_EMBED_DAEMON"] = "1"
    data, project = os.path.abspath(args.data), os.path.abspath(args.project)
    env = dict(os.environ)
    env.update({
        "CLAUDE_PLUGIN_DATA": data, "CLAUDE_PROJECT_DIR": project, "HIPPO_RETRIEVAL_PROFILE": "high",
        "CLAUDE_CONFIG_DIR": os.path.join(data, "claude-config"), "HIPPO_EMBED_DAEMON": "1",
        "HIPPO_USER_MEMORY_DIR": os.path.join(data, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(data, "no-private-tier"),
        "HIPPO_TRUST_ALL": "1", "HIPPO_TRUST_FILE": os.path.join(data, "trust.json"), "HF_HUB_OFFLINE": "1",
        "PYTHONPATH": os.path.join(_ROOT, "plugin"), "OPENBLAS_NUM_THREADS": "1",
    })
    for v in ("HIPPO_DENSE_FLOOR", "HIPPO_DISABLE", "HIPPO_DISABLE_DENSE", "HIPPO_HIGH_EMBED_MODEL",
              "HIPPO_HIGH_EMBED_DIM", "HIPPO_RERANK", "HIPPO_GRAPH_SEEDS",
              "HIPPO_MMR_LAMBDA", "HIPPO_EMBED_DAEMON_GRACE_S", "HIPPO_EMBED_DAEMON_IDLE_S"):
        env.pop(v, None)
    env["HIPPO_EMBED_DAEMON_BATCH"] = os.environ["HIPPO_EMBED_DAEMON_BATCH"] = args.batch
    os.environ.update({k: env[k] for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR", "HIPPO_RETRIEVAL_PROFILE")})
    from memory import embed_remote as X
    from memory import recall_warm as W
    from memory.eval_metrics import load_hard_set

    queries = [r["query"] for r in load_hard_set(args.queries) if r.get("query")]
    rng = random.Random(11)
    doc: dict = {"space": SPACE, "servers": args.servers, "queries_in_the_pool": len(queries),
                 "daemon_batches_queries": args.batch == "1",
                 "load_average_at_start": os.getloadavg()[0], "query_wait_s": X.QUERY_WAIT_S,
                 "serve_budget_ms": W.SERVE_BUDGET_MS, "cold_budget_ms": W.COLD_BUDGET_MS}
    _stop_daemon()
    for stale in glob.glob(os.path.join(data, "warm", "*")):
        os.unlink(stale)

    # ---- two starts at once, many times: one daemon -------------------------------- #
    trials, one_daemon, clean_losers = args.start_trials, 0, 0
    for _ in range(trials):
        a, b = X._spawn(SPACE), X._spawn(SPACE)
        end = time.monotonic() + 30
        while time.monotonic() < end and (X.hello(SPACE, timeout=0.5) is None
                                          or (a.poll() is None and b.poll() is None)):
            time.sleep(0.02)
        time.sleep(0.3)
        alive = [p for p in (a, b) if p.poll() is None]
        one_daemon += len(alive) == 1 and len(_daemon_pids(data)) == 1
        clean_losers += [p.returncode for p in (a, b) if p.poll() is not None] == [0]
        state = (X.hello(SPACE, timeout=1.0) or {}).get("state")
        assert state == "idle", f"a start loaded a model nobody asked for: {state}"
        _stop_daemon()
        for p in (a, b):
            p.wait(15)
    doc["two_simultaneous_starts"] = {
        "trials": trials, "left_exactly_one_daemon": one_daemon, "ci95": wilson(one_daemon, trials),
        "the_other_exited_0_having_loaded_nothing": clean_losers}
    _log(f"two starts: {one_daemon}/{trials} left one daemon")

    # ---- N servers, first prompt at the same moment --------------------------------- #
    fleet = [Server(i, project, data, env) for i in range(args.servers)]
    try:
        gate = threading.Barrier(len(fleet))
        first: list = [None] * len(fleet)

        def start(i):
            gate.wait()
            first[i] = fleet[i].prompt(queries[i % len(queries)])

        threads = [threading.Thread(target=start, args=(i,)) for i in range(len(fleet))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        end = time.monotonic() + 300
        while time.monotonic() < end and (X.hello(SPACE, timeout=1.0) or {}).get("state") != "loaded":
            time.sleep(0.2)
        time.sleep(2.0)
        pids = _daemon_pids(data)
        doc["simultaneous_first_prompts"] = {
            "servers": len(fleet), "daemons_alive_after": len(pids),
            "first_prompts": _served(first, W.COLD_BUDGET_MS)}
        _log(f"{len(fleet)} servers started together: {len(pids)} daemon(s)")
        assert len(pids) == 1, pids
        daemon_pid = pids[0]

        def engage(timeout=600.0):
            """Prompt every server until each serves high (its twin is level, the daemon loaded)."""
            end = time.monotonic() + timeout
            pending = set(range(len(fleet)))
            while pending and time.monotonic() < end:
                for i in sorted(pending):
                    r = fleet[i].prompt(rng.choice(queries))
                    if (r["hb"].get("high") or {}).get("engaged"):
                        pending.discard(i)
                if pending:
                    time.sleep(1.0)
            return not pending

        assert engage(), "not every server reached high"

        # ---- memory: one copy -------------------------------------------------------- #
        for _ in range(3):  # a few served prompts each, so the query path's buffers exist
            for s in fleet:
                s.prompt(rng.choice(queries))
        srv_mb = [footprint_mb(s.proc.pid) for s in fleet]
        mlx_maps = [sum(1 for ln in _open_files(s.proc.pid).splitlines() if "mlx" in ln.lower()) for s in fleet]
        daemon_mb = footprint_mb(daemon_pid)
        info = X.hello(SPACE, timeout=2.0)
        doc["memory"] = {
            "tool": "footprint <pid>, phys_footprint", "prompts_served_per_server_before": [s.n for s in fleet],
            "server_mb": sorted(srv_mb), "server_mb_summary": summary([x for x in srv_mb if x is not None], "MB"),
            "servers_under_200_mb": sum(1 for x in srv_mb if x is not None and x < 200.0),
            "servers_mapping_an_mlx_library": sum(1 for n in mlx_maps if n),
            "daemon_mb": daemon_mb, "daemon_model_resident_bytes": info["resident_bytes"],
            "daemon_load_ms": info["load_ms"], "copies_of_the_model": len(_daemon_pids(data)),
            "all_processes_mb": round(sum(x for x in srv_mb if x) + (daemon_mb or 0), 1),
        }
        doc["no_port"] = {
            "daemon_internet_sockets": len([ln for ln in _open_files(daemon_pid, "-a", "-i").splitlines()[1:] if ln]),
            "daemon_unix_sockets": len([ln for ln in _open_files(daemon_pid, "-a", "-U").splitlines()[1:] if ln]),
            "socket_mode": oct(os.stat(X.paths(SPACE).sock).st_mode & 0o777),
            "socket_dir_mode": oct(os.stat(os.path.dirname(X.paths(SPACE).sock)).st_mode & 0o777),
            "socket_under_plugin_data": X.paths(SPACE).sock.startswith(data + os.sep),
        }
        _log(f"memory: servers {sorted(srv_mb)} MB, daemon {daemon_mb} MB")

        def all_at_once() -> list:
            """One prompt to every server at the same moment."""
            gate = threading.Barrier(len(fleet))
            got: list = [None] * len(fleet)
            picks = [rng.choice(queries) for _ in fleet]

            def fire(i):
                gate.wait()
                got[i] = fleet[i].prompt(picks[i])

            threads = [threading.Thread(target=fire, args=(i,)) for i in range(len(fleet))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            return got

        # ---- a burst of simultaneous prompts ----------------------------------------- #
        before = X.hello(SPACE, timeout=2.0)["stats"]
        burst: list = []
        for _ in range(args.rounds):
            burst.extend(all_at_once())
            time.sleep(0.2)
        after = X.hello(SPACE, timeout=2.0)["stats"]
        doc["burst"] = dict(_served(burst, W.SERVE_BUDGET_MS), rounds=args.rounds, simultaneous=len(fleet),
                            daemon_queries=after["queries"] - before["queries"],
                            daemon_forward_passes=after["query_passes"] - before["query_passes"],
                            largest_pass=after["largest_pass"], load_average=os.getloadavg()[0])
        _log(f"burst: {json.dumps(doc['burst'])}")

        # ---- the three faults -------------------------------------------------------- #
        restarts = {"by_the_servers": 0, "by_this_bench": 0}

        def wait_loaded(timeout=120.0, own_s=15.0):
            """The daemon is back and holds the model. The servers do that themselves: the one
            whose prompt hit the fault woke its loader thread, which restarts the daemon. This
            bench kills it dozens of times in a few minutes, which is what the servers' start
            backoff (5 s, doubling) exists to slow down; after ``own_s`` the bench starts it."""
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout:
                info = X.hello(SPACE, timeout=1.0)
                if (info or {}).get("state") == "loaded":
                    return True
                if info is None and time.monotonic() - t0 > own_s:
                    X._reset_for_tests()
                    X.ensure(SPACE, load=True)
                    restarts["by_this_bench"] += 1
                    return True
                time.sleep(0.2)
            return False

        def settle():
            """Past every server's fault hold, the daemon loaded, every server on high again."""
            time.sleep(args.hold_s)
            assert wait_loaded() and engage(120.0), "the fleet did not come back to high"

        absent: list = []
        for _ in range(args.fault_rounds):
            for pid in _daemon_pids(data):
                os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)
            absent.extend(all_at_once())
            settle()
        doc["fault_absent"] = dict(_served(absent, W.SERVE_BUDGET_MS),
                                   what="the daemon is killed, then every server gets a prompt at once "
                                        "(it is absent, then starting)")
        _log(f"absent: {json.dumps(doc['fault_absent'])}")

        slow: list = []
        for _ in range(args.fault_rounds):
            stop = threading.Event()
            _flood(SPACE, 120.0, stop)
            time.sleep(1.5)
            slow.extend(all_at_once())
            stop.set()
            time.sleep(3.0)
            settle()
        doc["fault_slow"] = dict(_served(slow, W.SERVE_BUDGET_MS),
                                 what="six callers keep the daemon's model busy with long documents; "
                                      "every server gets a prompt at once meanwhile")
        doc["fault_slow"]["prompts_that_hit_the_fault_path"] = sum(
            1 for r in slow if not (r["hb"].get("high") or {}).get("engaged"))
        _log(f"slow: {json.dumps(doc['fault_slow'])}")

        killed: list = []
        for _ in range(args.fault_rounds):
            for s in fleet:
                stop = threading.Event()
                _flood(SPACE, 30.0, stop)
                time.sleep(1.0)
                box: list = []
                t = threading.Thread(target=lambda s=s: box.append(s.prompt(rng.choice(queries))))
                t.start()
                time.sleep(0.15)  # the prompt's query is queued behind the flood's slices
                for pid in _daemon_pids(data):
                    os.kill(pid, signal.SIGKILL)
                t.join()
                stop.set()
                killed.extend(box)
                before_own = restarts["by_this_bench"]
                assert wait_loaded(), "the daemon did not come back"
                restarts["by_the_servers"] += restarts["by_this_bench"] == before_own
            settle()
        doc["fault_killed_mid_call"] = dict(
            _served(killed, W.SERVE_BUDGET_MS),
            what="a prompt's query is waiting in the daemon when the daemon is killed (SIGKILL)")
        doc["fault_killed_mid_call"]["faults_recorded_by_servers"] = sum(
            int((s.heartbeat().get("high") or {}).get("faults") or 0) for s in fleet)
        doc["fault_killed_mid_call"]["daemon_restarted"] = restarts
        _log(f"killed: {json.dumps(doc['fault_killed_mid_call'])}")

        # ---- doctor, and what is left ------------------------------------------------ #
        from memory.recall_warm_high import doctor_meaning

        meaning, note = doctor_meaning("dense lean", "high")
        doc["doctor_line"] = {"names_the_daemon_state": "embedding daemon loaded (pid" in meaning,
                              "note": note, "text": re.sub(r"pid \d+", "pid N", meaning)}
        doc["every_server_still_accepting"] = sum(1 for s in fleet if s.heartbeat().get("accept") is not False
                                                  and not s.heartbeat().get("tripped"))
    finally:
        for s in fleet:
            s.close()
    # every server gone: the daemon reaps itself (GRACE_S), nobody stops it
    t0 = time.monotonic()
    while time.monotonic() - t0 < 180 and _daemon_pids(data):
        time.sleep(1.0)
    doc["reaped_itself"] = {"daemons_left": len(_daemon_pids(data)),
                            "seconds_after_the_last_server_exited": round(time.monotonic() - t0, 1)}
    time.sleep(5.0)
    left = subprocess.run("ps -Ao pid,ppid,etime,stat,command | grep -i 'python[^ ]* -m [m]emory.embed_daemon'",
                          shell=True, capture_output=True, text=True).stdout.strip().splitlines()
    doc["reaped_itself"]["embed_daemon_processes_on_the_machine_5s_later"] = len(left)
    for line in left:
        _log("still running: " + line[:160])
    doc["launchd_entries_naming_hippo"] = sum(
        1 for ln in subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout.splitlines()
        if "hippo" in ln.lower())
    doc["load_average_at_end"] = os.getloadavg()[0]
    doc["seconds"] = round(time.monotonic() - _T0)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("vectors-local", "vectors-daemon"):
        p = sub.add_parser(name)
        p.add_argument("--work", required=True)
        p.add_argument("--live", action="append", default=[], metavar="LABEL[=IGNORED]")
        p.add_argument("--heldout", default="", help="private JSONL of extra queries: corpus, query")
        p.add_argument("--out", required=True)
        if name == "vectors-daemon":
            p.add_argument("--local", required=True, help="vectors-local's .npz")
            p.add_argument("--skip-build", action="append", default=[], metavar="LABEL",
                           help="compare this corpus's queries only (its twin build is long)")
    p = sub.add_parser("servers")
    p.add_argument("--project", required=True, help="a scratch project whose high twin is level")
    p.add_argument("--data", required=True, help="a scratch CLAUDE_PLUGIN_DATA with mlx and fastembed linked in")
    p.add_argument("--queries", required=True, help="a hard-set YAML whose queries are the prompts")
    p.add_argument("--servers", type=int, default=10)
    p.add_argument("--rounds", type=int, default=30)
    p.add_argument("--start-trials", type=int, default=20)
    p.add_argument("--fault-rounds", type=int, default=3)
    p.add_argument("--hold-s", type=float, default=31.0)
    p.add_argument("--batch", choices=("0", "1"), default="0", help="HIPPO_EMBED_DAEMON_BATCH for the run")
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return {"vectors-local": vectors_local, "vectors-daemon": vectors_daemon, "servers": servers}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
