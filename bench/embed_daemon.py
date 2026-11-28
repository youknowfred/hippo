"""HCR-7 bench: the embedding daemon against the in-process embedder, on real models.

Three commands, run one after another (one MLX process at a time):

    PY=<plugin venv>/bin/python; export OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:.
    $PY -m bench.embed_daemon vectors-local  --work W [--live LABEL=MEMORY_DIR] --out local.npz
    $PY -m bench.embed_daemon vectors-daemon --work W [--live ...] --local local.npz --out vectors.json
    $PY -m bench.embed_daemon servers --project P --data D --queries hard_set.yaml --out servers.json

``vectors-local`` loads the model in this process (what every HCR evidence file was read on):
it embeds each corpus's rows into a twin index and each hard-set query, alone and in batches.
``vectors-daemon`` does the same through the daemon and compares the bytes: queries one at a
time, queries arriving ten at once, and whole index builds.

``servers`` starts real MCP servers (``python -m memory.mcp_server``) on the high profile
against one scratch project, drives their ``recall_hook`` tool the way the prompt hook does,
and measures what the roadmap's acceptance names: one daemon from simultaneous starts, each
server's and the daemon's ``phys_footprint``, a burst of simultaneous prompts against
``SERVE_BUDGET_MS``, and the three daemon faults (absent, slow, killed mid-call).

Three more read the daemon's lifetime and what it holds (HCR-7's eleventh ruling):

    $PY -m bench.embed_daemon lifetime     --project P --data D --queries hard_set.yaml --out lifetime.json
    $PY -m bench.embed_daemon build-memory --project P2 --data D [--model M] --out build.json
    $PY -m bench.embed_daemon two-models   --project-a P --project-b P8 --data D --queries ... --out two.json

``lifetime``: the first prompt after the daemon dropped its idle model, the way back to a
prompt served high, and the daemon's own leave once its server is gone. ``build-memory``: the
daemon's and the building server's ``phys_footprint``, sampled through a ``high/`` build.
``two-models``: one daemon holding two models, for sessions on different ones.

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
    """Kill this plugin-data dir's daemon (it has no stop request: it leaves by itself, a
    grace after the last warm server on high)."""
    from memory import embed_daemon as D

    path = D.socket_path()
    info = D.ping(path, timeout=1.0)
    if info is None:
        return
    try:
        os.kill(int(info["pid"]), signal.SIGKILL)
    except OSError:
        pass
    end = time.monotonic() + 15
    while time.monotonic() < end and D.ping(path, timeout=0.5) is not None:
        time.sleep(0.1)


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
    os.environ["HIPPO_EMBED_DAEMON_GRACE_S"] = "600"  # no session here (no heartbeat): stay for the run
    _env_for(os.path.abspath(args.work))
    from memory import embed_daemon as D
    from memory import embed_mlx as E
    from memory.build_index import load_index

    corpora = _corpora(os.path.abspath(args.work), args.live, args.heldout)
    local = np.load(args.local)
    doc = {"space": SPACE, "corpora": {}, "mlx_modules_in_this_process": None}
    _stop_daemon()
    emb = E.high_embedder(SPACE)
    assert isinstance(emb, D.DaemonEmbedder), type(emb)
    doc["daemon"] = {"load_ms": emb.load_ms, "resident_bytes": emb.resident_bytes}
    _log(f"daemon up: pid {emb.daemon_pid}, load {emb.load_ms} ms")
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
        burst, waits = _burst(emb, qs)
        rec["daemon_ten_at_once_vs_in_process_single"] = _same(ref, burst)
        rec["ten_at_once_wait_ms"] = summary(waits)
        if label not in args.skip_build:
            t0 = time.monotonic()
            tdir = _build_twin(md, "hcr7-daemon")
            rec["build_seconds"] = round(time.monotonic() - t0, 1)
            rec["daemon_built_twin_vs_in_process_twin"] = _same(
                local[f"{label}/docs"], np.array(load_index(tdir).dense))
        doc["corpora"][label] = rec
        _log(f"{label}: {json.dumps(rec)}")
    doc["single_query_ms"] = summary(single_ms)
    doc["daemon_stats"] = (D.ping() or {}).get("stats")
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
    return footprints(pid)[0]


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


def _flood(space: str, texts: int = 8) -> threading.Thread:
    """Keep the daemon's query turn busy for several seconds, the way a daemon that has gone
    slow looks to a prompt: one request of long queries (about a 1,024-token slice each)."""
    from memory import embed_daemon as D

    long_text = " ".join(f"slice {i} of a long question about deploy lanes and billing cutovers" for i in range(400))

    def one():
        try:
            D.request(D.socket_path(), {"op": "query", "space": space, "texts": [long_text] * texts}, 120.0)
        except Exception:
            pass

    t = threading.Thread(target=one, daemon=True)
    t.start()
    return t


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


def _server_env(data: str, project: str, idle_s: float, model: str = "") -> dict:
    """The environment of one real MCP server on high against scratch dirs; this process
    takes the same data dir, so ``embed_daemon.socket_path`` here is the servers' socket."""
    env = dict(os.environ)
    env.update({
        "CLAUDE_PLUGIN_DATA": data, "CLAUDE_PROJECT_DIR": project, "HIPPO_RETRIEVAL_PROFILE": "high",
        "CLAUDE_CONFIG_DIR": os.path.join(data, "claude-config"), "HIPPO_EMBED_DAEMON": "1",
        "HIPPO_EMBED_DAEMON_IDLE_S": str(idle_s),
        "HIPPO_USER_MEMORY_DIR": os.path.join(data, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(data, "no-private-tier"),
        "HIPPO_TRUST_ALL": "1", "HIPPO_TRUST_FILE": os.path.join(data, "trust.json"), "HF_HUB_OFFLINE": "1",
        "PYTHONPATH": os.path.join(_ROOT, "plugin"), "OPENBLAS_NUM_THREADS": "1",
    })
    for v in ("HIPPO_DENSE_FLOOR", "HIPPO_DISABLE", "HIPPO_DISABLE_DENSE", "HIPPO_HIGH_EMBED_MODEL",
              "HIPPO_HIGH_EMBED_DIM", "HIPPO_RERANK", "HIPPO_GRAPH_SEEDS", "HIPPO_MMR_LAMBDA"):
        env.pop(v, None)
        os.environ.pop(v, None)
    if model:
        env["HIPPO_HIGH_EMBED_MODEL"] = os.environ["HIPPO_HIGH_EMBED_MODEL"] = model
    os.environ.update({k: env[k] for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR", "HIPPO_RETRIEVAL_PROFILE",
                                           "HIPPO_EMBED_DAEMON_IDLE_S")})
    return env


def servers(args) -> int:
    os.environ["HIPPO_EMBED_DAEMON"] = "1"
    data, project = os.path.abspath(args.data), os.path.abspath(args.project)
    env = _server_env(data, project, args.idle_s)
    from memory import embed_daemon as D
    from memory import recall_warm as W
    from memory.eval_metrics import load_hard_set

    path = D.socket_path()
    queries = [r["query"] for r in load_hard_set(args.queries) if r.get("query")]
    rng = random.Random(11)
    doc: dict = {"space": SPACE, "servers": args.servers, "queries_in_the_pool": len(queries),
                 "load_average_at_start": os.getloadavg()[0], "query_wait_s": D.QUERY_WAIT_S,
                 "daemon_idle_exit_s": args.idle_s, "serve_budget_ms": W.SERVE_BUDGET_MS,
                 "cold_budget_ms": W.COLD_BUDGET_MS}
    _stop_daemon()
    for stale in glob.glob(os.path.join(data, "warm", "*")):
        os.unlink(stale)

    def stats() -> dict:
        return (D.ping(path, timeout=2.0) or {}).get("stats") or {}

    def loaded() -> bool:
        return SPACE in ((D.ping(path, timeout=1.0) or {}).get("loaded") or [])

    # ---- two starts at once, many times: one daemon -------------------------------- #
    trials, one_daemon, nothing_loaded = args.start_trials, 0, 0
    for _ in range(trials):
        pair = [threading.Thread(target=D.spawn, args=(path,)) for _ in range(2)]
        for t in pair:
            t.start()
        for t in pair:
            t.join()
        end = time.monotonic() + 30
        while time.monotonic() < end and D.ping(path, timeout=0.5) is None:
            time.sleep(0.02)
        time.sleep(6.0)  # the start that lost waits up to 5 s for the lock before it leaves
        one_daemon += len(_daemon_pids(data)) == 1
        nothing_loaded += (D.ping(path, timeout=1.0) or {}).get("loaded") == []
        _stop_daemon()
    doc["two_simultaneous_starts"] = {
        "trials": trials, "left_exactly_one_daemon": one_daemon, "ci95": wilson(one_daemon, trials),
        "and_it_had_loaded_nothing": nothing_loaded}
    _log(f"two starts: {one_daemon}/{trials} left one daemon")

    # ---- N servers, first prompt at the same moment --------------------------------- #
    fleet = [Server(i, project, data, env) for i in range(args.servers)]
    try:
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

        first = all_at_once()
        end = time.monotonic() + 300
        while time.monotonic() < end and not loaded():
            time.sleep(0.2)
        time.sleep(6.0)
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
        high = fleet[0].heartbeat().get("high") or {}
        doc["memory"] = {
            "tool": "footprint <pid>, phys_footprint", "prompts_served_per_server_before": [s.n for s in fleet],
            "server_mb": sorted(srv_mb), "server_mb_summary": summary([x for x in srv_mb if x is not None], "MB"),
            "servers_under_200_mb": sum(1 for x in srv_mb if x is not None and x < 200.0),
            "servers_mapping_an_mlx_library": sum(1 for n in mlx_maps if n),
            "daemon_mb": daemon_mb, "daemon_model_resident_bytes": high.get("resident_bytes"),
            "daemon_load_ms": high.get("load_ms"), "copies_of_the_model": len(_daemon_pids(data)),
            "all_processes_mb": round(sum(x for x in srv_mb if x) + (daemon_mb or 0), 1),
        }
        doc["no_port"] = {
            "daemon_internet_sockets": len([ln for ln in _open_files(daemon_pid, "-a", "-i").splitlines()[1:] if ln]),
            "daemon_unix_sockets": len([ln for ln in _open_files(daemon_pid, "-a", "-U").splitlines()[1:] if ln]),
            "socket_mode": oct(os.stat(path).st_mode & 0o777),
            "socket_dir_mode": oct(os.stat(os.path.dirname(path)).st_mode & 0o777),
            "socket_under_plugin_data": path.startswith(data + os.sep),
        }
        _log(f"memory: servers {sorted(srv_mb)} MB, daemon {daemon_mb} MB")

        # ---- a burst of simultaneous prompts ----------------------------------------- #
        before = stats()
        burst: list = []
        for _ in range(args.rounds):
            burst.extend(all_at_once())
            time.sleep(0.2)
        after = stats()
        doc["burst"] = dict(_served(burst, W.SERVE_BUDGET_MS), rounds=args.rounds, simultaneous=len(fleet),
                            daemon={k: after.get(k, 0) - before.get(k, 0) for k in ("queries", "busy", "expired")},
                            load_average=os.getloadavg()[0])
        _log(f"burst: {json.dumps(doc['burst'])}")

        # ---- the three faults -------------------------------------------------------- #
        restarts = {"by_the_servers": 0, "by_this_bench": 0}

        def wait_loaded(timeout=120.0, own_s=20.0):
            """The daemon is back and holds the model. The servers do that themselves (the one
            whose prompt hit the fault has its hippo-high thread revive it); past ``own_s``
            this bench does."""
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout:
                if loaded():
                    restarts["by_the_servers"] += 1
                    return True
                if time.monotonic() - t0 > own_s:
                    D.ensure(path)
                    D.request(path, {"op": "load", "space": SPACE}, 600.0)
                    restarts["by_this_bench"] += 1
                    return True
                time.sleep(0.2)
            return False

        def settle():
            """Past every server's fault hold, the daemon loaded, every server on high again."""
            time.sleep(args.hold_s)
            assert (loaded() or wait_loaded()) and engage(180.0), "the fleet did not come back to high"

        def faults_now() -> int:
            return sum(int((s.heartbeat().get("high") or {}).get("faults") or 0) for s in fleet)

        absent: list = []
        for _ in range(args.fault_rounds):
            for pid in _daemon_pids(data):
                os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)
            absent.extend(all_at_once())
            assert wait_loaded(), "the daemon did not come back"
            settle()
        doc["fault_absent"] = dict(_served(absent, W.SERVE_BUDGET_MS),
                                   what="the daemon is killed, then every server gets a prompt at once "
                                        "(it is absent, then starting)")
        _log(f"absent: {json.dumps(doc['fault_absent'])}")

        slow: list = []
        f0, s0 = faults_now(), stats()
        for _ in range(args.fault_rounds):
            flood = _flood(SPACE)
            time.sleep(1.0)
            slow.extend(all_at_once())
            flood.join(120)
            settle()
        s1 = stats()
        doc["fault_slow"] = dict(_served(slow, W.SERVE_BUDGET_MS),
                                 what="the daemon's query turn is held for several seconds by one long "
                                      "request; every server gets a prompt at once meanwhile",
                                 prompts_that_waited_and_timed_out=faults_now() - f0,
                                 refused_busy_by_the_daemon=s1.get("busy", 0) - s0.get("busy", 0),
                                 not_run_because_the_wait_ran_out=s1.get("expired", 0) - s0.get("expired", 0))
        _log(f"slow: {json.dumps(doc['fault_slow'])}")

        killed: list = []
        f0 = faults_now()
        for _ in range(args.fault_rounds):
            for s in fleet:
                _flood(SPACE)
                time.sleep(0.5)
                box: list = []
                t = threading.Thread(target=lambda s=s: box.append(s.prompt(rng.choice(queries))))
                t.start()
                time.sleep(0.15)  # the prompt's query is waiting for its turn in the daemon
                for pid in _daemon_pids(data):
                    os.kill(pid, signal.SIGKILL)
                t.join()
                killed.extend(box)
                assert wait_loaded(), "the daemon did not come back"
            settle()
        doc["fault_killed_mid_call"] = dict(
            _served(killed, W.SERVE_BUDGET_MS),
            what="a prompt's query is waiting in the daemon when the daemon is killed (SIGKILL)",
            prompts_whose_wait_was_cut_by_the_kill=faults_now() - f0, daemon_restarted=dict(restarts))
        _log(f"killed: {json.dumps(doc['fault_killed_mid_call'])}")

        # ---- doctor, and what is left ------------------------------------------------ #
        from memory.recall_warm_high import doctor_meaning

        meaning, note = doctor_meaning("dense lean", "high")
        doc["doctor_line"] = {"names_the_daemon": "in the shared embed daemon (pid" in meaning,
                              "the_daemons_own_word_on_its_state": "embed daemon running (pid" in meaning,
                              "note": note, "text": re.sub(r"pid \d+", "pid N", meaning)}
        doc["every_server_still_accepting"] = sum(1 for s in fleet if s.heartbeat().get("accept") is not False
                                                  and not s.heartbeat().get("tripped"))
    finally:
        for s in fleet:
            s.close()
    # every server gone: the daemon leaves by itself, a grace (60 s) later (nobody stops it)
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.idle_s + 180 and _daemon_pids(data):
        time.sleep(1.0)
    doc["reaped_itself"] = {"daemons_left": len(_daemon_pids(data)),
                            "seconds_after_the_last_server_exited": round(time.monotonic() - t0, 1)}
    _stop_daemon()
    doc["launchd_entries_naming_hippo"] = sum(
        1 for ln in subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout.splitlines()
        if "hippo" in ln.lower())
    doc["load_average_at_end"] = os.getloadavg()[0]
    doc["seconds"] = round(time.monotonic() - _T0)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


# --------------------------------------------------------------------------- #
# lifetime: what a prompt meets after the daemon went quiet
# --------------------------------------------------------------------------- #
def lifetime(args) -> int:
    """One real MCP server on high and the daemon it shares. Each trial: the server serves
    high, nothing asks the daemon for ``--idle-s``, and then the first prompt is timed, and
    the way back to a prompt served high. ``--expect drop`` (the daemon as shipped): it is
    still there and holds no model. ``--expect exit`` is EMB-1's rule, which HCR-7's
    eleventh ruling replaced (its process left after the idle window); it is kept to read
    that code again. Last, the server closes and the daemon's own leave is timed."""
    os.environ["HIPPO_EMBED_DAEMON"] = "1"
    data, project = os.path.abspath(args.data), os.path.abspath(args.project)
    env = _server_env(data, project, args.idle_s, args.model)
    for kv in args.env:
        k, v = kv.split("=", 1)
        env[k] = os.environ[k] = v
    from memory import embed_daemon as D
    from memory import embed_mlx as E
    from memory import recall_warm as W
    from memory.eval_metrics import load_hard_set

    doc_grace = float(env.get(D.GRACE_ENV) or D.DEFAULT_GRACE_S)
    space = E.resolve_high_space()
    path = D.socket_path()
    queries = [r["query"] for r in load_hard_set(args.queries) if r.get("query")]
    rng = random.Random(13)
    doc: dict = {"space": space, "expect": args.expect, "idle_s": args.idle_s, "trials": args.trials,
                 "grace_s": doc_grace, "serve_budget_ms": W.SERVE_BUDGET_MS,
                 "load_average_at_start": os.getloadavg()[0]}
    if not args.keep_daemon:
        _stop_daemon()
    for stale in glob.glob(os.path.join(data, "warm", "*")):
        os.unlink(stale)

    def info():
        return D.ping(path, timeout=1.0)

    def holds_model() -> bool:
        return space in ((info() or {}).get("loaded") or [])

    srv = Server(0, project, data, env)
    rows: list = []
    try:
        end = time.monotonic() + 900
        while time.monotonic() < end:
            if (srv.prompt(rng.choice(queries))["hb"].get("high") or {}).get("engaged"):
                break
            time.sleep(1.0)
        else:
            raise SystemExit("the server never served high")
        for trial in range(args.trials):
            warm = [srv.prompt(rng.choice(queries)) for _ in range(3)]
            assert all((r["hb"].get("high") or {}).get("engaged") for r in warm), "not on high before the wait"
            daemon_pid = (info() or {}).get("pid")
            loaded_mb = footprint_mb(daemon_pid) if daemon_pid else None
            t_quiet = time.monotonic()
            # ---- nothing asks the daemon (a ping is not activity) ---------------------- #
            while time.monotonic() - t_quiet < args.idle_s + 120:
                got = info()
                if args.expect == "exit" and got is None and not _daemon_pids(data):
                    break
                if args.expect == "drop" and got is not None and not got.get("loaded"):
                    break
                time.sleep(0.25 if args.idle_s < 120 else 2.0)
            else:
                raise SystemExit(f"the daemon did not {args.expect} within {args.idle_s + 120:.0f} s")
            quiet_s = time.monotonic() - t_quiet
            time.sleep(args.settle_s)
            pids = [int((info() or {}).get("pid") or 0)] if args.expect == "drop" else _daemon_pids(data)
            rec = {"quiet_s_until_the_" + args.expect: round(quiet_s, 1), "daemon_mb_holding_the_model": loaded_mb,
                   "daemon_mb_after": footprint_mb(pids[0]) if pids else 0.0, "daemon_processes_after": len(pids)}
            # ---- the first prompt after ------------------------------------------------ #
            t1 = time.monotonic()
            first = srv.prompt(rng.choice(queries))
            hb = first["hb"]
            rec.update({"first_wall_ms": round(first["wall_ms"], 1), "first_server_ms": hb.get("last_ms"),
                        "first_served_high": bool((hb.get("high") or {}).get("engaged")),
                        "first_raised": first["raised"], "first_tripped": bool(hb.get("tripped")),
                        "first_answered_with_a_block": first["injected"]})
            # ---- the way back ---------------------------------------------------------- #
            while time.monotonic() - t1 < 300 and not holds_model():
                time.sleep(0.01)
            rec["model_back_ms_after_the_first_prompt_was_sent"] = round((time.monotonic() - t1) * 1000.0, 1)
            lean_again = 0
            while time.monotonic() - t1 < 300:
                t2 = time.monotonic()
                nxt = srv.prompt(rng.choice(queries))
                if (nxt["hb"].get("high") or {}).get("engaged"):
                    rec["first_high_prompt_sent_ms_after_the_first"] = round((t2 - t1) * 1000.0, 1)
                    rec["first_high_wall_ms"] = round(nxt["wall_ms"], 1)
                    rec["first_high_server_ms"] = nxt["hb"].get("last_ms")
                    rec["daemon_load_ms"] = (nxt["hb"].get("high") or {}).get("load_ms")
                    break
                lean_again += 1
                time.sleep(0.02)
            rec["prompts_served_lean_between"] = lean_again
            rec["tripped_after"] = bool(srv.heartbeat().get("tripped"))
            rec["faults_so_far"] = int((srv.heartbeat().get("high") or {}).get("faults") or 0)
            rows.append(rec)
            _log(f"trial {trial + 1}/{args.trials}: {json.dumps(rec)}")
    finally:
        srv.close()
    if not args.keep_daemon:  # the last warm server on high is gone: the daemon leaves by itself
        t_closed, left = time.monotonic(), None
        while time.monotonic() - t_closed < doc_grace + 120:
            if info() is None:
                left = round(time.monotonic() - t_closed, 1)
                break
            time.sleep(0.5)
        doc["daemon_left_s_after_the_last_server_closed"] = left
        _log(f"the daemon left {left} s after the server closed")
    n = len(rows)

    def count(key):
        k = sum(1 for r in rows if r.get(key))
        return {"k": k, "n": n, "ci95": wilson(k, n)}

    def col(key):
        return [r[key] for r in rows if r.get(key) is not None]

    doc.update({
        "first_prompt_served_lean": {"k": n - count("first_served_high")["k"], "n": n,
                                     "ci95": wilson(n - count("first_served_high")["k"], n)},
        "first_prompt_raised": count("first_raised"), "breaker_tripped": count("tripped_after"),
        "first_prompt_answered_with_a_block": count("first_answered_with_a_block"),
        "first_prompt_wall_ms": summary(col("first_wall_ms")),
        "first_prompt_server_ms": summary(col("first_server_ms")),
        "model_back_ms_after_the_first_prompt_was_sent": summary(col("model_back_ms_after_the_first_prompt_was_sent")),
        "first_high_prompt_sent_ms_after_the_first": summary(col("first_high_prompt_sent_ms_after_the_first")),
        "first_high_prompt_server_ms": summary(col("first_high_server_ms")),
        "daemon_load_ms": summary(col("daemon_load_ms")),
        "quiet_s_until_the_" + args.expect: summary(col("quiet_s_until_the_" + args.expect), "s"),
        "daemon_mb_holding_the_model": summary(col("daemon_mb_holding_the_model"), "MB"),
        "daemon_mb_after": summary(col("daemon_mb_after"), "MB"),
        "trials_rows": rows, "load_average_at_end": os.getloadavg()[0], "seconds": round(time.monotonic() - _T0),
    })
    if not args.keep_daemon:
        _stop_daemon()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


# --------------------------------------------------------------------------- #
# build-memory: who holds what while a warm server builds its high/ twin
# --------------------------------------------------------------------------- #
def footprints(pid: int) -> tuple:
    """(``phys_footprint``, ``phys_footprint_peak``) of a process in MB, None where unread.
    The peak is the kernel's own high-water mark for the process, so a spike between two
    samples is still seen."""
    try:  # in bytes: the default format rounds anything over 10 GB to a whole gigabyte
        out = subprocess.run(["footprint", "-f", "bytes", str(pid)], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None, None

    def mb(label):
        m = re.search(label + r":\s*(\d+)\s*B\b", out)
        return round(int(m.group(1)) / 2**20, 1) if m else None

    return mb("phys_footprint"), mb("phys_footprint_peak")


def _rows_embedded(hdir: str, memo: dict) -> tuple:
    """(rows that hold a vector, rows in all) of the twin at ``hdir``, read from its manifest
    when that file changed."""
    path = os.path.join(hdir, "manifest.json")
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return 0, 0
    if memo.get("stamp") != stamp:
        try:
            with open(path, encoding="utf-8") as fh:
                m = json.load(fh)
        except Exception:
            return memo.get("got", (0, 0))
        rows = list(m.get("entries") or []) + list(m.get("body_chunks") or [])
        memo["stamp"], memo["got"] = stamp, (sum(1 for r in rows if r.get("row") is not None), len(rows))
    return memo["got"]


def build_memory(args) -> int:
    """One real MCP server on high builds a project's ``high/`` twin through the daemon.
    ``phys_footprint`` of both, sampled through the run: the daemon holding the model before
    the build, each build pass, and after the twin is level."""
    os.environ["HIPPO_EMBED_DAEMON"] = "1"
    data, project = os.path.abspath(args.data), os.path.abspath(args.project)
    env = _server_env(data, project, args.idle_s, args.model)
    from memory import embed_daemon as D
    from memory import embed_mlx as E
    from memory.build_index import default_index_dir

    space = E.resolve_high_space()
    path = D.socket_path()
    hdir = os.path.join(default_index_dir(os.path.join(project, ".claude", "memory")), "high")
    memo: dict = {}
    doc: dict = {"space": space, "tool": "footprint <pid>: phys_footprint, phys_footprint_peak",
                 "sample_every_s": args.sample_s, "load_average_at_start": os.getloadavg()[0],
                 "mlx_cache_cap_mb": E.cache_limit_bytes() // 2**20}
    _stop_daemon()
    for stale in glob.glob(os.path.join(data, "warm", "*")):
        os.unlink(stale)
    # ---- the daemon alone, holding the model, a few queries served ------------------ #
    emb = E.high_embedder(space)
    assert isinstance(emb, D.DaemonEmbedder), type(emb)
    for i in range(args.queries):
        list(emb.query_embed([f"what did we decide about deploy lane {i} and its cutover"]))
    daemon_pid = int(emb.daemon_pid)
    idle = []
    for _ in range(10):
        idle.append(footprints(daemon_pid)[0])
        time.sleep(0.5)
    doc["daemon_before_the_build"] = {"mb": summary([x for x in idle if x is not None], "MB"),
                                      "peak_so_far_mb": footprints(daemon_pid)[1],
                                      "model_resident_bytes": emb.resident_bytes, "load_ms": emb.load_ms}
    start_rows, total = _rows_embedded(hdir, memo)
    doc["rows"] = {"in_the_twin": total, "embedded_before": start_rows}
    _log(f"daemon {daemon_pid} idle {idle[-1]} MB; twin {start_rows}/{total}")
    srv = Server(0, project, data, env)
    samples: list = []
    passes: list = []
    t0 = time.monotonic()
    try:
        first = srv.prompt("what did we decide about the deploy lanes")
        doc["server_at_its_first_prompt_mb"] = footprints(srv.proc.pid)[0]
        doc["first_prompt"] = {"wall_ms": round(first["wall_ms"], 1), "raised": first["raised"],
                               "served_high": bool((first["hb"].get("high") or {}).get("engaged"))}
        last_rows, pass_start, pass_d, pass_s = start_rows, time.monotonic(), [], []
        level_at = None
        while time.monotonic() - t0 < args.max_s:
            d_mb, d_peak = footprints(daemon_pid)
            s_mb, s_peak = footprints(srv.proc.pid)
            rows, total = _rows_embedded(hdir, memo)
            samples.append([round(time.monotonic() - t0, 1), d_mb, s_mb, rows])
            if d_mb is not None:
                pass_d.append(d_mb)
            if s_mb is not None:
                pass_s.append(s_mb)
            if rows != last_rows:
                passes.append({"rows": rows - last_rows, "seconds": round(time.monotonic() - pass_start, 1),
                               "daemon_mb_max_sampled": max(pass_d) if pass_d else None,
                               "daemon_mb_median": round(pct(0.5)(pass_d), 1) if pass_d else None,
                               "daemon_peak_so_far_mb": d_peak,
                               "server_mb_max_sampled": max(pass_s) if pass_s else None,
                               "server_peak_so_far_mb": s_peak, "load_average": os.getloadavg()[0]})
                _log(f"pass {len(passes)}: {rows}/{total} {json.dumps(passes[-1])}")
                last_rows, pass_start, pass_d, pass_s = rows, time.monotonic(), [], []
            if total and rows >= total and level_at is None:
                level_at = time.monotonic()
            if (level_at is not None and time.monotonic() - level_at > 5) or (
                    args.passes and len(passes) >= args.passes):
                break
            time.sleep(args.sample_s)
        building = [x for x in samples if x[1] is not None]
        doc["build"] = {
            "seconds": round(time.monotonic() - t0, 1), "passes": passes, "level": level_at is not None,
            "rows_embedded": last_rows - start_rows,
            "daemon_mb": summary([x[1] for x in building], "MB"),
            "daemon_mb_min": min(x[1] for x in building), "daemon_peak_mb": footprints(daemon_pid)[1],
            "server_mb": summary([x[2] for x in samples if x[2] is not None], "MB"),
            "server_mb_min": min(x[2] for x in samples if x[2] is not None),
            "server_peak_mb": footprints(srv.proc.pid)[1],
        }
        # ---- after: served prompts on the finished twin, nothing building ------------ #
        after_d, after_s, served = [], [], []
        end = time.monotonic() + args.after_s
        while time.monotonic() < end:
            if level_at is not None:
                served.append(srv.prompt(f"what did we decide about deploy lane {len(served)}"))
            after_d.append(footprints(daemon_pid)[0])
            after_s.append(footprints(srv.proc.pid)[0])
            time.sleep(args.sample_s)
        doc["after_the_build"] = {
            "daemon_mb": summary([x for x in after_d if x is not None], "MB"),
            "server_mb": summary([x for x in after_s if x is not None], "MB"),
            "prompts": len(served),
            "served_high": sum(1 for r in served if (r["hb"].get("high") or {}).get("engaged")),
            "daemon_stats": (D.ping(path, timeout=2.0) or {}).get("stats")}
    finally:
        srv.close()
    doc["samples_t_daemon_mb_server_mb_rows"] = samples
    doc["load_average_at_end"] = os.getloadavg()[0]
    _stop_daemon()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


# --------------------------------------------------------------------------- #
# two-models: sessions on different high models share one daemon
# --------------------------------------------------------------------------- #
def two_models(args) -> int:
    """Two real MCP servers on one data dir, each on its own high model and project. One
    daemon serves both, so it holds both models: its ``phys_footprint`` with the first
    alone, with both, with the second dropped while the first stays busy, and after the idle
    drop of everything; each server's beside it."""
    os.environ["HIPPO_EMBED_DAEMON"] = "1"
    data = os.path.abspath(args.data)
    from memory.eval_metrics import load_hard_set

    queries = [r["query"] for r in load_hard_set(args.queries) if r.get("query")]
    rng = random.Random(17)
    specs = [(os.path.abspath(args.project_a), args.model_a), (os.path.abspath(args.project_b), args.model_b)]
    envs = [_server_env(data, project, args.idle_s, model) for project, model in specs]
    os.environ.pop("HIPPO_HIGH_EMBED_MODEL", None)
    from memory import embed_daemon as D

    path = D.socket_path()
    doc: dict = {"models": [args.model_a, args.model_b], "idle_s": args.idle_s,
                 "tool": "footprint <pid>: phys_footprint, phys_footprint_peak",
                 "load_average_at_start": os.getloadavg()[0], "steps": []}
    _stop_daemon()
    for stale in glob.glob(os.path.join(data, "warm", "*")):
        os.unlink(stale)

    def engage(srv, timeout=900.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if (srv.prompt(rng.choice(queries))["hb"].get("high") or {}).get("engaged"):
                return True
            time.sleep(1.0)
        return False

    def step(what: str, fleet: list) -> None:
        info = D.ping(path, timeout=2.0) or {}
        pid = info.get("pid")
        ds, ss = [], [[] for _ in fleet]
        for _ in range(args.samples):
            for i, srv in enumerate(fleet):
                srv.prompt(rng.choice(queries))
                ss[i].append(footprints(srv.proc.pid)[0])
            ds.append(footprints(pid)[0] if pid else None)
            time.sleep(0.5)
        rec = {"what": what, "daemon_holds": info.get("loaded"), "daemons": 1 if pid else 0,
               "daemon_mb": summary([x for x in ds if x is not None], "MB"),
               "daemon_mb_range": [min(x for x in ds if x is not None), max(x for x in ds if x is not None)],
               "daemon_peak_so_far_mb": footprints(pid)[1] if pid else None,
               "server_mb_range": [[min(x for x in col if x is not None), max(x for x in col if x is not None)]
                                   for col in ss],
               "served_high": [bool((srv.heartbeat().get("high") or {}).get("engaged")) for srv in fleet],
               "resident_bytes": [(srv.heartbeat().get("high") or {}).get("resident_bytes") for srv in fleet],
               "load_ms": [(srv.heartbeat().get("high") or {}).get("load_ms") for srv in fleet]}
        doc["steps"].append(rec)
        _log(json.dumps(rec))

    fleet: list = []
    try:
        fleet.append(Server(0, specs[0][0], data, envs[0]))
        assert engage(fleet[0]), "the first server never served high"
        step(f"one server, on {args.model_a}", fleet)
        fleet.append(Server(1, specs[1][0], data, envs[1]))
        assert engage(fleet[1]), "the second server never served high"
        assert engage(fleet[0]), "the first server stopped serving high"
        step(f"two servers, on {args.model_a} and {args.model_b}", fleet)
        pid = (D.ping(path, timeout=2.0) or {}).get("pid")
        # ---- only the first server prompts: the other's model goes on its own clock --- #
        t0 = time.monotonic()
        while time.monotonic() - t0 < args.idle_s + 15:
            fleet[0].prompt(rng.choice(queries))
            time.sleep(1.0)
        time.sleep(2.0)
        one = D.ping(path, timeout=2.0) or {}
        doc["after_only_the_first_server_prompted"] = {
            "for_s": round(time.monotonic() - t0, 1), "daemon_holds": one.get("loaded"),
            "same_process": one.get("pid") == pid, "daemon_mb": footprints(pid)[0] if pid else None,
            "first_server_still_served_high": bool((fleet[0].heartbeat().get("high") or {}).get("engaged")),
            "unloads": (one.get("stats") or {}).get("unloads")}
        _log(json.dumps(doc["after_only_the_first_server_prompted"]))
        # ---- nothing asks: every model is dropped, the process stays ----------------- #
        t0 = time.monotonic()
        while time.monotonic() - t0 < args.idle_s + 120 and (D.ping(path, timeout=1.0) or {}).get("loaded"):
            time.sleep(0.5)
        time.sleep(2.0)
        after = D.ping(path, timeout=2.0) or {}
        doc["after_the_idle_drop"] = {"quiet_s": round(time.monotonic() - t0, 1), "daemon_holds": after.get("loaded"),
                                      "same_process": after.get("pid") == pid,
                                      "daemon_mb": footprints(pid)[0] if pid else None,
                                      "unloads": (after.get("stats") or {}).get("unloads")}
        _log(json.dumps(doc["after_the_idle_drop"]))
    finally:
        for srv in fleet:
            srv.close()
    _stop_daemon()
    doc["load_average_at_end"] = os.getloadavg()[0]
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
    p.add_argument("--hold-s", type=float, default=6.0)
    p.add_argument("--idle-s", type=float, default=60.0,
                   help="HIPPO_EMBED_DAEMON_IDLE_S for the run: the idle model drop (1,800 as shipped)")
    p.add_argument("--out", required=True)
    p = sub.add_parser("lifetime")
    p.add_argument("--project", required=True, help="a scratch project whose high twin is level")
    p.add_argument("--data", required=True, help="a scratch CLAUDE_PLUGIN_DATA with mlx and fastembed linked in")
    p.add_argument("--queries", required=True, help="a hard-set YAML whose queries are the prompts")
    p.add_argument("--trials", type=int, default=20)
    p.add_argument("--idle-s", type=float, default=8.0, help="HIPPO_EMBED_DAEMON_IDLE_S for the run (1,800 as shipped)")
    p.add_argument("--expect", choices=("drop", "exit"), default="drop",
                   help="what the quiet daemon does: stays and drops its model (as shipped), or leaves (EMB-1's rule)")
    p.add_argument("--settle-s", type=float, default=0.5, help="wait after it went, before the first prompt")
    p.add_argument("--model", default="", help="HIPPO_HIGH_EMBED_MODEL for the server (default: the profile's)")
    p.add_argument("--env", action="append", default=[], metavar="K=V", help="more environment for the server and daemon")
    p.add_argument("--keep-daemon", action="store_true", help="use the daemon already on the socket, and leave it")
    p.add_argument("--out", required=True)
    p = sub.add_parser("build-memory")
    p.add_argument("--project", required=True, help="a scratch project whose high twin is missing or behind")
    p.add_argument("--data", required=True, help="a scratch CLAUDE_PLUGIN_DATA with mlx and fastembed linked in")
    p.add_argument("--model", default="", help="HIPPO_HIGH_EMBED_MODEL for the server (default: the profile's)")
    p.add_argument("--passes", type=int, default=0, help="stop after this many build passes (0: until level)")
    p.add_argument("--max-s", type=float, default=3 * 3600.0)
    p.add_argument("--after-s", type=float, default=30.0, help="how long to sample once the build is over")
    p.add_argument("--sample-s", type=float, default=1.0)
    p.add_argument("--queries", type=int, default=5, help="queries the daemon serves before the build")
    p.add_argument("--idle-s", type=float, default=1800.0)
    p.add_argument("--out", required=True)
    p = sub.add_parser("two-models")
    p.add_argument("--project-a", required=True, help="a scratch project whose high twin is level in --model-a")
    p.add_argument("--project-b", required=True, help="another, level in --model-b")
    p.add_argument("--model-a", default="Qwen/Qwen3-Embedding-4B")
    p.add_argument("--model-b", default="Qwen/Qwen3-Embedding-8B")
    p.add_argument("--data", required=True, help="a scratch CLAUDE_PLUGIN_DATA with mlx and fastembed linked in")
    p.add_argument("--queries", required=True, help="a hard-set YAML whose queries are the prompts")
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--idle-s", type=float, default=20.0, help="HIPPO_EMBED_DAEMON_IDLE_S for the run (short, so the drop is seen)")
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return {"vectors-local": vectors_local, "vectors-daemon": vectors_daemon, "servers": servers,
            "lifetime": lifetime, "build-memory": build_memory, "two-models": two_models}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
