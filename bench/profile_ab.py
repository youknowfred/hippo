"""HCR-6 bench: lean against high on a real corpus with the real MLX models — one command.

    OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:.:<mlx site dir> python -m bench.profile_ab \\
        --memory-dir .claude/memory \\
        --mlx-cache ~/.claude/plugins/data/hippo-hippo/mlx \\
        --fastembed-cache ~/.claude/plugins/data/hippo-hippo/fastembed \\
        --out planning/HCR-6.evidence.<date>.json

The arms (``memory.profile_ab`` runs them; the first is the baseline, run first and last):

* ``lean`` — bge-small, no rerank: what every machine serves today.
* ``lean+rerank`` — the warm reranker over lean's hits.
* ``high`` / ``high+rerank`` — the Qwen3 embedder at full width, alone and with the warm
  reranker (``high+rerank`` is the high profile's shipped warm default).
* ``…+pool`` — the reranker reads the top ``--pool`` fused hits before the cut to k.
* ``high+rerank-4b`` — the larger reranker, if its weights are on disk (setup no longer
  fetches it; the explicit surfaces score with the warm model over a pool, the
  ``high+rerank+pool`` arm).
* ``high@<dim>`` — the embedder's Matryoshka output truncated to ``--dims``, each at the
  floor the table gives that space and at the full-width space's calibrated floor.

Beside the arms the report stamps what a profile costs on this machine: each model's load
time and resident memory, the process's RSS before and after, each index's build time,
and the spawned hook's cold latency (it serves the lean index on every profile).

Nothing real is written but ``--out``: the corpus is copied into a temp project, the index
and plugin-data dirs are temp dirs, and nothing downloads (``HF_HUB_OFFLINE``). The report
carries counts and timings only — never a query or a memory name.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

_T0 = time.monotonic()


def _log(msg: str) -> None:
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


def _rss_bytes() -> int:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) * 1024


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--mlx-cache", required=True)
    ap.add_argument("--fastembed-cache", required=True)
    ap.add_argument("--hard-set", default=None,
                    help="default: <memory-dir>/.audit-fixtures/recall_hard_set.yaml")
    ap.add_argument("--pool", type=int, default=30, help="fused hits a deep-pool arm reranks")
    ap.add_argument("--dims", default="1024,2048", help="truncated widths to try; '' for none")
    ap.add_argument("--out", default=None, help="write the report here (default: stdout)")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.memory_dir)
    hard_src = args.hard_set or os.path.join(src, ".audit-fixtures", "recall_hard_set.yaml")
    tmp = tempfile.mkdtemp(prefix="hippo-profile-ab-")
    project = os.path.join(tmp, "project")
    md = os.path.join(project, ".claude", "memory")
    shutil.copytree(src, md)
    hard_set = os.path.join(tmp, "hard_set.yaml")
    shutil.copyfile(hard_src, hard_set)
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    os.environ.update({
        "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_DATA": data,
        "HIPPO_RETRIEVAL_PROFILE": "high", "FASTEMBED_CACHE_PATH": os.path.abspath(args.fastembed_cache),
        "HIPPO_USER_MEMORY_DIR": os.path.join(tmp, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(tmp, "no-private-tier"),
        "HIPPO_TRUST_FILE": os.path.join(tmp, "trust.json"), "HIPPO_TRUST_ALL": "1",
        "HF_HUB_OFFLINE": "1",
        # No arm silently degrades: a rerank past its bound keeps the fused order and a query
        # embed past its bound serves BM25 alone, and either would be scored as the arm's own
        # quality. Both bounds are read at import, so they are set before memory loads.
        "HIPPO_RERANK_TIMEOUT": "120", "HIPPO_DENSE_TIMEOUT": "120",
    })
    for knob in ("HIPPO_RERANK", "HIPPO_DENSE_FLOOR", "HIPPO_GRAPH_SEEDS",
                 "HIPPO_DISABLE_DENSE", "HIPPO_DISABLE", "HIPPO_HIGH_EMBED_DIM"):
        os.environ.pop(knob, None)  # the arms decide; a developer's shell does not
    subprocess.run(["git", "init", "-q", project], check=True)

    from memory import build_index as B
    from memory import embed_mlx as E
    from memory import recall as RC
    from memory import recall_rank as RR
    from memory import recall_warm as W
    from memory import rerank_mlx as R
    from memory.atomic import write_json_atomic
    from memory.eval_arms import cold_latency
    from memory.eval_metrics import load_hard_set
    from memory.profile_ab import Arm, run_profile_ab

    mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
    E.cache_dir = lambda: mlx_cache  # read the fetched weights in place
    R.default_on = lambda: False  # no reranker is on unless its arm says so
    k = RC.DEFAULT_K
    full = E.resolve_high_space()
    model = E.parse_space(full)[0]
    dims = [int(d) for d in args.dims.split(",") if d.strip()]
    spaces = [full] + [E.space_id(model, d) for d in dims]
    cost: dict = {"rss_bytes": {"start": _rss_bytes()}, "models": {}, "index_build_s": {}}

    idx = B.default_index_dir(md)
    t0 = time.monotonic()
    lean = B.build_index(md, idx)  # unbounded: a fair, dense lean baseline
    cost["index_build_s"]["lean"] = round(time.monotonic() - t0, 1)
    if not lean["dense_ready"]:
        print("the lean index is not dense (is the fastembed cache right?)", file=sys.stderr)
        return 1
    indexes = {None: B.load_index(idx)}
    cost["rss_bytes"]["lean_index_and_model"] = _rss_bytes()
    _log("lean index built")

    for space in spaces:
        emb = E.high_embedder(space)  # raises with the reason when this machine cannot serve it
        if space == full:
            cost["models"][model] = {"load_ms": round(emb.load_ms, 1), "resident_bytes": emb.resident_bytes}
            cost["rss_bytes"]["embedder_loaded"] = _rss_bytes()
        hdir = os.path.join(tmp, "high-" + (space.partition("@")[2] or "full"))
        t0 = time.monotonic()
        for _ in range(8):
            with E.using_space(space):
                B.build_index(md, hdir, allow_download=True)  # the high embedder never downloads
            hi = B.load_index(hdir)
            if hi is not None and hi.dense_ready and hi.model == space and all(
                    x.get("row") is not None for x in hi.entries + hi.body_chunks):
                break
        else:
            print(f"the {space} index did not converge", file=sys.stderr)
            return 1
        indexes[space] = hi
        cost["index_build_s"][space] = round(time.monotonic() - t0, 1)
        _log(f"{space} index built")

    scorings: dict = {}
    rerankers = [R.WARM_MODEL] + ([R.LARGE_MODEL] if R.unavailable_reason(R.LARGE_MODEL) is None else [])
    for name in rerankers:
        rr = R.reranker(name)
        cost["models"][name] = dict(R.status(name), model=None)
        cost["models"][name].pop("model")
        scorings[name] = 0

        def counted(query, docs, _real=rr.score, _name=name):
            scorings[_name] += 1
            return _real(query, docs)

        rr.score = counted
    cost["rss_bytes"]["all_models_loaded"] = _rss_bytes()
    _log("rerankers loaded")

    calibrated = str(RR._DENSE_FLOOR_BY_MODEL.get(full, RR._DENSE_FLOOR_DEFAULT))
    warm = R.WARM_MODEL
    arms = [
        Arm("lean"),
        Arm("lean+rerank", reranker=warm, rerank=True),
        Arm("lean+rerank+pool", reranker=warm, rerank=True, pool=args.pool),
        Arm("high", space=full),
        Arm("high+rerank", space=full, reranker=warm, rerank=True),
        Arm("high+rerank+pool", space=full, reranker=warm, rerank=True, pool=args.pool),
    ]
    if R.LARGE_MODEL in rerankers:
        arms.append(Arm("high+rerank-4b", space=full, reranker=R.LARGE_MODEL, rerank=True))
    for space in spaces[1:]:
        dim = space.partition("@")[2]
        arms.append(Arm(f"high@{dim}", space=space))
        arms.append(Arm(f"high@{dim}/calibrated-floor", space=space, env=(("HIPPO_DENSE_FLOOR", calibrated),)))
        arms.append(Arm(f"high@{dim}/calibrated-floor+rerank", space=space, reranker=warm, rerank=True,
                        env=(("HIPPO_DENSE_FLOOR", calibrated),)))

    t0 = time.monotonic()
    ab = run_profile_ab(arms, memory_dir=md, lean_index_dir=idx, indexes=indexes,
                        hard_set_path=hard_set, k=k)
    _log(f"arms done in {time.monotonic() - t0:.0f} s")
    if not ab.get("ok"):
        print(json.dumps(ab, indent=2))
        return 1
    for name, arm in ab["arms"].items():
        arm["dense_floor"] = float(dict(arm["env"]).get("HIPPO_DENSE_FLOOR") or RR._DENSE_FLOOR_BY_MODEL.get(
            B.DEFAULT_MODEL if arm["space"] == "lean" else arm["space"], RR._DENSE_FLOOR_DEFAULT))

    queries = [row["query"] for row in load_hard_set(hard_set)]
    cost["spawned_hook_cold_ms"] = cold_latency(md, idx, queries, k=k, samples=8)
    cost["serve_budget_ms"] = W.SERVE_BUDGET_MS
    cost["reranker_scorings"] = scorings
    import mlx.core as mx
    import mlx_lm

    report = {
        "item": "HCR-6",
        "date": datetime.date.today().isoformat(),
        "machine": {
            "platform": f"{platform.system()} {platform.machine()}", "python": platform.python_version(),
            "memsize_bytes": int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                                text=True).stdout.strip() or 0),
            "mlx": getattr(mx, "__version__", None), "mlx_lm": getattr(mlx_lm, "__version__", None),
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
            "load_average_1m": round(os.getloadavg()[0], 1),
        },
        "weights": {
            **{m: f"{E.HIGH_MODELS[m]['repo']}@{E.HIGH_MODELS[m]['revision'][:12]}" for m in [model]},
            **{m: f"{R.RERANK_MODELS[m]['repo']}@{R.RERANK_MODELS[m]['revision'][:12]}" for m in rerankers},
        },
        "corpus": {"entries": lean["count"], "body_chunks": len(lean["body_chunks"]),
                   "note": "private corpus and fixture: counts only, no queries, no memory names"},
        "pool": args.pool,
        "cost": cost,
        **ab,
    }
    if args.out:
        write_json_atomic(os.path.abspath(args.out), report)
        _log(f"wrote {args.out}")
    else:
        print(json.dumps(report, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
