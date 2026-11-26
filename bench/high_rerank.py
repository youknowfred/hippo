"""HCR-4 bench: the Qwen3 rerankers on a real corpus with the real MLX models.

What the acceptance line asks for, measured on the machine it runs on:

* **Budget.** Warm recall served end to end (``recall_warm.serve``, the call the MCP tool
  answers) on the high profile with the 0.6B reranker ON by default, at the production
  candidate count (``recall.DEFAULT_K``), against ``SERVE_BUDGET_MS`` — paired call for
  call with the same prompts under ``HIPPO_RERANK=0``, interleaved, in fresh sessions.
  Every ON call must have scored through the reranker, or the run fails.
* **Breaker.** Served calls that tripped their session's breaker, per arm.
* **Explicit surface.** ``recall_view.describe`` on high with the 4B: first-call load and
  per-call latency.
* **Ordering.** ``ab_runner.run_flag_arms("HIPPO_RERANK")`` (QUA-6 per-category deltas, the
  OFF-arm self-check) for each reranker, over the lean index and over the 4B embedder's
  high index, on the corpus's own hard set. The OFF arm is the fused order: inside the
  bench the warm default is held off, so the flag alone decides.

Nothing real is written (a temp copy of the corpus, temp plugin-data and index dirs, a
temp trust file), nothing downloads (``HF_HUB_OFFLINE``), and the report carries counts
and timings only — never a query or a memory name.

    PYTHONPATH=plugin:. python -m bench.high_rerank --memory-dir .claude/memory \\
        --mlx-cache ~/.claude/plugins/data/hippo-hippo/mlx \\
        --fastembed-cache ~/.claude/plugins/data/hippo-hippo/fastembed
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack

_T0 = time.monotonic()


def _log(msg: str) -> None:
    """Progress to stderr (counts and phases only); ``kill -USR1`` dumps every stack."""
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)

from bench.high_embed import PROMPTS


def _summary(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    return {"n": len(xs), "median": round(statistics.median(xs), 1),
            "p95": round(xs[max(0, int(len(xs) * 0.95) - 1)], 1), "max": round(xs[-1], 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--mlx-cache", required=True)
    ap.add_argument("--fastembed-cache", required=True)
    ap.add_argument("--hard-set", default=None,
                    help="default: <memory-dir>/.audit-fixtures/recall_hard_set.yaml")
    ap.add_argument("--rounds", type=int, default=3, help="passes over the prompts per arm")
    ap.add_argument("--skip-ab", action="store_true")
    args = ap.parse_args(argv)
    faulthandler.register(signal.SIGUSR1, all_threads=True)

    src = os.path.abspath(args.memory_dir)
    hard_set = args.hard_set or os.path.join(src, ".audit-fixtures", "recall_hard_set.yaml")
    tmp = tempfile.mkdtemp(prefix="hippo-rerank-bench-")
    project = os.path.join(tmp, "project")
    md = os.path.join(project, ".claude", "memory")
    shutil.copytree(src, md)
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    os.environ.update({
        "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_DATA": data,
        "HIPPO_RETRIEVAL_PROFILE": "high", "FASTEMBED_CACHE_PATH": os.path.abspath(args.fastembed_cache),
        "HIPPO_USER_MEMORY_DIR": os.path.join(tmp, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(tmp, "no-private-tier"),
        "HIPPO_TRUST_FILE": os.path.join(tmp, "trust.json"), "HIPPO_TRUST_ALL": "1",
        "HF_HUB_OFFLINE": "1",
    })
    os.environ["HIPPO_RERANK"] = "1"  # the warm reranker loads and scores only when it is on
    subprocess.run(["git", "init", "-q", project], check=True)

    from memory import build_index as B
    from memory import embed_mlx as E
    from memory import recall as RC
    from memory import recall_view as V
    from memory import recall_warm as W
    from memory import recall_warm_high as H
    from memory import rerank_mlx as R
    from memory.ab_runner import run_flag_arms

    mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
    E.cache_dir = lambda: mlx_cache  # read the fetched weights in place
    space = E.resolve_high_space()
    report = {
        "item": "HCR-4", "k": RC.DEFAULT_K, "space": space,
        "warm_model": R.WARM_MODEL, "explicit_model": R.EXPLICIT_MODEL,
        "explicit_pool": R.EXPLICIT_POOL,
        "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
        "serve_budget_ms": W.SERVE_BUDGET_MS, "warm_rerank_bound_s": R.WARM_TIMEOUT_SECS,
    }
    idx = B.default_index_dir(md)
    lean = B.build_index(md, idx)
    report["corpus"] = {"entries": lean["count"], "body_chunks": len(lean["body_chunks"])}

    def serve(sid, prompt):
        d = W.warm_dir(create=True)
        W._create_claim(W.claim_path(d, sid, "p"), "warm")
        t = time.monotonic()
        out = W.serve({"session_id": sid, "prompt_id": "p", "prompt": prompt, "cwd": project})
        ms = (time.monotonic() - t) * 1000.0
        W.drain()
        return out, ms

    _log("lean index built")
    t0 = time.monotonic()
    serve("kick", PROMPTS[0])  # the process's first served call: starts both loads
    while (H.status().get("status") == "loading"
           or (H.status().get("rerank") or {}).get("status") == "loading"):
        time.sleep(0.05)
    st = H.status()
    report["embedder"] = {k: st.get(k) for k in ("status", "reason", "load_ms", "resident_bytes")}
    report["warm_reranker"] = st.get("rerank")
    if (st.get("rerank") or {}).get("status") != "ready":
        print(json.dumps(report, indent=2))
        return 1
    if st.get("status") == "ready":
        while not H.fresh(B.load_index(idx), B.load_index(H.high_index_dir(idx)), space):
            if time.monotonic() - t0 > 3600:
                report["high_build"] = "did not converge in an hour"
                print(json.dumps(report, indent=2))
                return 1
            time.sleep(0.25)
        report["high_index_ready_s_after_kick"] = round(time.monotonic() - t0, 1)

    _log("models loaded, high index fresh")
    # Every ON call must score through the 0.6B; record each scoring's size and time.
    rr = R.loaded(R.WARM_MODEL)
    real_score = rr.score
    scored = []

    def _timed(query, docs):
        t = time.monotonic()
        out = real_score(query, docs)
        scored.append((len(docs), (time.monotonic() - t) * 1000.0))
        return out

    rr.score = _timed
    arms = {"rerank_on": [], "rerank_off": []}
    trips = {"rerank_on": 0, "rerank_off": 0}
    engaged = {"rerank_on": 0, "rerank_off": 0}
    candidates, rerank_ms = [], []
    n = degraded = 0
    for r in range(args.rounds):
        for i, p in enumerate(PROMPTS):
            order = ("rerank_on", "rerank_off") if (r + i) % 2 == 0 else ("rerank_off", "rerank_on")
            for arm in order:
                n += 1
                sid = f"{arm}-{n}"
                if arm == "rerank_off":
                    os.environ["HIPPO_RERANK"] = "0"
                before = len(scored)
                out, ms = serve(sid, p)
                os.environ["HIPPO_RERANK"] = "1"
                assert "additionalContext" in out, "a served call injected nothing"
                arms[arm].append(ms)
                trips[arm] += bool(W._SESSIONS[sid].tripped)
                engaged[arm] += bool(H.status().get("engaged"))
                if arm == "rerank_on":
                    end = time.monotonic() + 10  # a timed-out scoring still finishes, later
                    while len(scored) == before and time.monotonic() < end:
                        time.sleep(0.01)
                    assert len(scored) == before + 1, "an ON call never scored through the reranker"
                    candidates.append(scored[-1][0])
                    rerank_ms.append(scored[-1][1])
                    degraded += scored[-1][1] > R.WARM_TIMEOUT_SECS * 1000.0
                else:
                    assert len(scored) == before, "an OFF call scored through the reranker"
    rr.score = real_score
    report["warm_served_ms"] = {arm: _summary(xs) for arm, xs in arms.items()}
    report["warm_rerank_scoring_ms"] = _summary(rerank_ms)
    report["candidates_per_rerank"] = {"min": min(candidates), "max": max(candidates),
                                       "median": statistics.median(candidates)}
    report["on_calls_past_the_rerank_bound"] = degraded  # served in the fused order
    report["breaker_trips"] = {arm: {"tripped": trips[arm], "calls": len(arms[arm])} for arm in arms}
    report["served_on_the_high_index"] = engaged

    _log("served arms done")
    # The explicit surface: its reranker over its pool, loaded on first use.
    explicit = {"available": R.explicit_model() == R.EXPLICIT_MODEL}
    if explicit["available"]:
        ms = []
        for p in PROMPTS:
            t = time.monotonic()
            V.describe(p, memory_dir=md)
            ms.append((time.monotonic() - t) * 1000.0)
            _log(f"describe {len(ms)}: {ms[-1]:.0f} ms")
        explicit["first_call_ms"] = round(ms[0], 1)
        explicit["describe_ms_after_first"] = _summary(ms[1:])
        explicit["reranker"] = R.status(R.EXPLICIT_MODEL)
    report["explicit"] = explicit

    if not args.skip_ab and os.path.isfile(hard_set):
        os.environ["HIPPO_RERANK_TIMEOUT"] = "60"  # no arm silently degrades to fused order
        runs = {}
        bases = [("lean_index", idx, None)]
        if st.get("status") == "ready":
            bases.append(("high_index", H.high_index_dir(idx), space))
        # None: the fastembed MiniLM (today's rerank), for reference beside the Qwen3 pair.
        large = R.unavailable_reason(R.LARGE_MODEL) is None
        models = [None, R.WARM_MODEL] + ([R.LARGE_MODEL] if large else [])
        for base, index_dir, sp in bases:
            for model in models:
                with ExitStack() as stack:
                    if sp:
                        stack.enter_context(E.using_space(sp))
                    stack.enter_context(R.using_reranker(model))
                    t = time.monotonic()
                    _log(f"A/B {base} {model or 'MiniLM'} starts")
                    res = run_flag_arms("HIPPO_RERANK", memory_dir=md, index_dir=index_dir,
                                        hard_set_path=hard_set, k=RC.DEFAULT_K,
                                        telemetry_dir=os.path.join(tmp, "telemetry"))
                    res["wall_s"] = round(time.monotonic() - t, 1)
                    _log(f"A/B {base} {model or 'MiniLM'} done in {res['wall_s']} s")
                runs[f"{base}/{(model or 'ms-marco-MiniLM-L-6-v2').split('/')[-1]}"] = res
        report["ab"] = runs
    print(json.dumps(report, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
