"""HCR-2 bench: the high profile's warm recall on a real corpus with the real MLX model.

Measures what the acceptance line asks for, on the machine it runs on: the embedder's
cold load, its resident unified memory, the time to build the high index for the corpus,
and per-prompt latency of warm recall served from that index (``recall_warm.serve`` end to
end, the same call the MCP tool answers) against ``SERVE_BUDGET_MS``.

Nothing real is written: the corpus is copied into a temp project (trusted for the run
only, via a temp trust file), and the plugin-data and index dirs are temp dirs. Every
served call must inject and must embed with the high model, or the run fails — a served
number that never touched the model measures nothing. Only the model weights are read from ``--mlx-cache`` (fetched
beforehand with ``hippo setup --profile high --fetch --yes``) and lean's fastembed model
from ``--fastembed-cache``.

    PYTHONPATH=plugin python -m bench.high_embed --memory-dir .claude/memory \\
        --mlx-cache ~/.claude/plugins/data/hippo-hippo/mlx \\
        --fastembed-cache ~/.claude/plugins/data/hippo-hippo/fastembed
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time

PROMPTS = [
    "how does the warm recall server hand a prompt over to the command hook",
    "why did the dense index tear when two builds ran at once",
    "what is the release fast path for landing several items",
    "where does the fastembed model cache live and why not in TMPDIR",
    "how should commands be handed to the owner to run",
    "what does the floor of the memory index mean",
    "how do I run the full test suite safely",
    "which budget does the spawned recall hook have to stay under",
    "what happened with the abstention leak on off-topic prompts",
    "how are memories tied to the code they cite",
    "what is the policy on multi-agent fan-out cost",
    "how do I set up a second machine",
]


def _rss_bytes() -> int:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) * 1024


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--mlx-cache", required=True)
    ap.add_argument("--fastembed-cache", required=True)
    ap.add_argument("--profile-one", action="store_true", help="cProfile one served high call (stderr)")
    ap.add_argument("--space", default=None, help="default: the resolved high space (4B, full width)")
    args = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="hippo-high-bench-")
    project = os.path.join(tmp, "project")
    md = os.path.join(project, ".claude", "memory")
    shutil.copytree(os.path.abspath(args.memory_dir), md)
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    os.environ.update({
        "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_DATA": data,
        "HIPPO_RETRIEVAL_PROFILE": "high", "FASTEMBED_CACHE_PATH": os.path.abspath(args.fastembed_cache),
        "HIPPO_USER_MEMORY_DIR": os.path.join(tmp, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(tmp, "no-private-tier"),
        # The copy lives in a fresh temp repo the trust registry has never seen; without
        # this, recall abstains before ranking and the "served" numbers measure nothing.
        "HIPPO_TRUST_FILE": os.path.join(tmp, "trust.json"), "HIPPO_TRUST_ALL": "1",
        "HF_HUB_OFFLINE": "1",  # every model is read from its cache; nothing downloads
    })
    if args.space:
        os.environ["HIPPO_HIGH_EMBED_MODEL"] = args.space.partition("@")[0]
    subprocess.run(["git", "init", "-q", project], check=True)

    from memory import build_index as B
    from memory import embed_mlx as E
    from memory import recall_rank as RR
    from memory import recall_warm as W
    from memory import recall_warm_high as H

    mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
    E.cache_dir = lambda: mlx_cache  # read the fetched weights in place
    space = E.resolve_high_space()
    report = {"space": space, "corpus_files": len(os.listdir(md))}

    t0 = time.monotonic()
    lean = B.build_index(md, B.default_index_dir(md))  # unbounded: a fair, dense lean baseline
    report["lean_build_s"] = round(time.monotonic() - t0, 2)
    report["lean_dense_ready"] = lean["dense_ready"]
    report["entries"], report["body_chunks"] = lean["count"], len(lean["body_chunks"])

    def serve(sid, prompt):
        d = W.warm_dir(create=True)
        W._create_claim(W.claim_path(d, sid, "p"), "warm")
        t = time.monotonic()
        out = W.serve({"session_id": sid, "prompt_id": "p", "prompt": prompt, "cwd": project})
        ms = (time.monotonic() - t) * 1000.0
        W.drain()
        return out, ms

    # The same prompts served warm on lean first: the baseline the high numbers sit beside.
    os.environ["HIPPO_RETRIEVAL_PROFILE"] = "lean"
    lean_ms = [serve(f"lean{i}", p)[1] for i, p in enumerate(PROMPTS)]
    report["lean_warm_served_ms"] = {"median": round(statistics.median(lean_ms), 1),
                                     "max": round(max(lean_ms), 1)}
    os.environ["HIPPO_RETRIEVAL_PROFILE"] = "high"

    rss0 = _rss_bytes()
    t0 = time.monotonic()
    serve("kick", PROMPTS[0])  # starts the background load; served lean
    while H.status().get("status") == "loading":
        time.sleep(0.05)
    st = H.status()
    report["load_status"], report["load_reason"] = st.get("status"), st.get("reason")
    if st.get("status") != "ready":
        print(json.dumps(report, indent=2))
        return 1
    report["cold_load_ms"] = st["load_ms"]
    report["mlx_active_bytes"] = st["resident_bytes"]
    report["rss_delta_bytes"] = _rss_bytes() - rss0
    idx = B.default_index_dir(md)
    while not H.fresh(B.load_index(idx), B.load_index(H.high_index_dir(idx)), space):
        if time.monotonic() - t0 > 3600:
            report["high_build"] = "did not converge in an hour"
            print(json.dumps(report, indent=2))
            return 1
        time.sleep(0.25)
    report["high_index_ready_s_after_kick"] = round(time.monotonic() - t0, 1)

    emb = E.loaded(space)
    q_ms = []
    for p in PROMPTS:
        t = time.monotonic()
        list(emb.query_embed([p]))
        q_ms.append((time.monotonic() - t) * 1000.0)
    report["query_embed_ms"] = {"median": round(statistics.median(q_ms), 1), "max": round(max(q_ms), 1)}

    high_idx = B.load_index(H.high_index_dir(idx))
    calls = []
    real_query_embed = emb.query_embed
    emb.query_embed = lambda texts: (calls.append(list(texts)), real_query_embed(texts))[1]
    served, dense_hits, embedded = [], [], []
    for i, p in enumerate(PROMPTS):
        before = len(calls)
        out, ms = serve(f"bench{i}", p)
        assert H.status().get("engaged"), "a served call did not engage the high index"
        served.append(ms)
        embedded.append(len(calls) - before)
        assert "additionalContext" in out, f"served call {i} injected nothing: {out[:200]}"
        assert embedded[-1] >= 1, f"served call {i} never embedded with the high model"
        with E.using_space(space):
            dense_hits.append(len(RR._dense_rank_rows(p, high_idx)))
    if args.profile_one:
        import cProfile
        import pstats
        import sys

        prof = cProfile.Profile()
        prof.enable()
        serve("profiled", PROMPTS[2])
        prof.disable()
        pstats.Stats(prof, stream=sys.stderr).sort_stats("cumulative").print_stats(35)
    report["warm_served_ms"] = {
        "median": round(statistics.median(served), 1),
        "p95": round(sorted(served)[max(0, int(len(served) * 0.95) - 1)], 1),
        "max": round(max(served), 1),
        "budget": W.SERVE_BUDGET_MS,
    }
    report["warm_served_ms_each"] = [round(x, 1) for x in served]
    report["served_calls_that_embedded_with_the_high_model"] = embedded
    report["dense_rows_above_floor_per_prompt"] = dense_hits
    report["rss_after_bytes"] = _rss_bytes()
    print(json.dumps(report, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
