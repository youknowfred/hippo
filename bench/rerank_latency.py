"""HCR-8 bench: what gets lean + the 0.6B reranker inside the warm budget, and at what cost.

    OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.rerank_latency \\
        --questions <private.jsonl> --memory-dirs label=<dir> … \\
        --mlx-cache ~/.claude/plugins/data/hippo-hippo/mlx --out <private.json>

This scores lean's candidate pool with the warm reranker under each lever and records the
new order and the time, so quality and latency come from the same calls:

* pool size (how many fused hits are scored);
* passage length (the description cut to N characters);
* slice size (padded tokens per forward pass: one big batch against several small ones);
* a shared-prefix cache: the reranker's prompt opens with the same system text, the same
  instruction and the same query for every pair of a call, so that prefix is run once and
  its key/value cache is reused for every document (when ``mlx_lm`` offers the cache API).

It needs the candidate pool, not just the top 10, so it re-runs lean recall to depth 50
per question (bge-small only: no big model is loaded here, only the 0.6B reranker).
Nothing shipped is changed; the levers are applied to the loaded reranker in this process.
Output is private (it names memories); ``high_eval_report`` does not read it — the summary
table goes into the evidence by hand from this file's ``summary`` block, which is counts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

_T0 = time.monotonic()


def _log(msg: str) -> None:
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


def _pcts(ms):
    ms = sorted(ms)
    return {"p50": round(ms[len(ms) // 2], 1), "p95": round(ms[max(0, int(len(ms) * 0.95) - 1)], 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--memory-dirs", nargs="+", required=True, help="label=dir")
    ap.add_argument("--mlx-cache", required=True)
    ap.add_argument("--fastembed-cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-corpus", type=int, default=0, help="first N questions of each corpus")
    args = ap.parse_args(argv)

    rows_all = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    tmp = tempfile.mkdtemp(prefix="hippo-rerank-lat-")
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    os.environ.update({
        "CLAUDE_PLUGIN_DATA": data, "HIPPO_RETRIEVAL_PROFILE": "lean",
        "FASTEMBED_CACHE_PATH": os.path.abspath(os.path.expanduser(args.fastembed_cache)),
        "HIPPO_USER_MEMORY_DIR": os.path.join(tmp, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(tmp, "no-private-tier"),
        "HIPPO_TRUST_FILE": os.path.join(tmp, "trust.json"), "HIPPO_TRUST_ALL": "1",
        "HF_HUB_OFFLINE": "1", "HIPPO_RERANK_TIMEOUT": "120", "HIPPO_DENSE_TIMEOUT": "120",
        "HIPPO_RERANK": "0",
    })
    from memory import build_index as B
    from memory import embed_mlx as E
    from memory import recall as RC
    from memory import rerank_mlx as R

    mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
    E.cache_dir = lambda: mlx_cache
    k, depth = RC.DEFAULT_K, 50

    # ---- lean's candidate pool, to depth 50, per question ---------------------------- #
    cases = []  # (corpus, stem, style, query, [(name, description)])
    for spec in args.memory_dirs:
        label, src = spec.split("=", 1)
        rows = [r for r in rows_all if r.get("corpus") == label and r.get("query") and not r.get("skip")]
        if args.per_corpus:
            rows = rows[: args.per_corpus]
        project = os.path.join(tmp, label)
        md = os.path.join(project, ".claude", "memory")
        shutil.copytree(os.path.abspath(src), md, ignore=shutil.ignore_patterns(".audit-fixtures"))
        subprocess.run(["git", "init", "-q", project], check=True)
        os.environ["CLAUDE_PROJECT_DIR"] = project
        idx = B.default_index_dir(md)
        B.build_index(md, idx)
        lean = B.load_index(idx)
        for r in rows:
            got = RC.recall(r["query"], k=depth, index=lean, index_dir=idx, memory_dir=md)
            pool = [(h["name"], h.get("description") or "") for h in got if h.get("corpus") != "rule"]
            cases.append((label, r["stem"], r.get("style"), r["query"], pool))
        _log(f"{label}: {len(rows)} questions pooled to depth {depth}")

    rr = R.reranker(R.WARM_MODEL)
    mx = rr._mx
    cache_api = None
    try:
        from mlx_lm.models.cache import make_prompt_cache

        cache_api = make_prompt_cache
    except Exception:
        pass

    def score_prefix_cached(query: str, docs):
        """Run the shared prefix once, then every document's tail against a copy of its cache."""
        head = f"<Instruct>: {R.RERANK_INSTRUCTION}\n<Query>: {query}\n<Document>: "
        pre = rr._prefix + rr._tok.encode(head, add_special_tokens=False).ids
        tails = [list(e.ids)[:512] + rr._suffix for e in rr._tok.encode_batch(list(docs), add_special_tokens=False)]
        cache = cache_api(rr._net)
        rr._net.model(mx.array([pre]), cache=cache)
        n = len(tails)
        width = max(len(t) for t in tails)
        for c in cache:  # one prefix, n rows: repeat keys and values along the batch axis
            keys, values = c.keys[..., : c.offset, :], c.values[..., : c.offset, :]
            c.keys, c.values = mx.repeat(keys, n, axis=0), mx.repeat(values, n, axis=0)
        grid = mx.array([t + [rr._pad] * (width - len(t)) for t in tails])
        hidden = rr._net.model(grid, cache=cache)
        last = hidden[mx.arange(n), mx.array([len(t) - 1 for t in tails])]
        logits = rr._head(last).astype(mx.float32)
        p_yes = mx.softmax(mx.stack([logits[:, rr._no], logits[:, rr._yes]], axis=1), axis=1)[:, 1]
        mx.eval(p_yes)
        return [float(x) for x in p_yes.tolist()]

    def run(name: str, *, pool: int, chars: int = 0, slice_tokens: int = 0, prefix: bool = False):
        prev = R._SLICE_TOKENS
        if slice_tokens:
            R._SLICE_TOKENS = slice_tokens
        tops, ms = [], []
        try:
            for warm in (True, False):
                for _c, _s, _st, query, cand in (cases[:3] if warm else cases):
                    head = cand[:pool]
                    docs = [(d[:chars] if chars else d) for _n, d in head]
                    t0 = time.perf_counter()
                    if len(docs) >= 2:
                        scores = score_prefix_cached(query, docs) if prefix else rr.score(query, docs)
                        order = sorted(range(len(head)), key=lambda i: scores[i], reverse=True)
                    else:
                        order = list(range(len(head)))
                    if not warm:
                        ms.append((time.perf_counter() - t0) * 1000.0)
                        tops.append([head[i][0] for i in order][:k])
        finally:
            R._SLICE_TOKENS = prev
        rrs = []
        for (_c, stem, _st, _q, _cand), top in zip(cases, tops):
            rrs.append(1.0 / (top.index(stem) + 1) if stem in top else 0.0)
        out[name] = {"pool": pool, "chars": chars, "slice_tokens": slice_tokens or prev, "prefix_cache": prefix,
                     "top": tops, "rerank_ms": [round(m, 1) for m in ms]}
        summary[name] = {"n": len(rrs), "hits": sum(r > 0 for r in rrs), "mrr": round(sum(rrs) / len(rrs), 4),
                         "rerank_ms": _pcts(ms)}
        _log(f"{name}: {summary[name]}")

    out: dict = {}
    summary: dict = {}
    base = [c[4][:k] for c in cases]
    rrs = [1.0 / ([n for n, _ in top].index(c[1]) + 1) if c[1] in [n for n, _ in top] else 0.0
           for c, top in zip(cases, base)]
    summary["no-rerank"] = {"n": len(rrs), "hits": sum(r > 0 for r in rrs), "mrr": round(sum(rrs) / len(rrs), 4)}
    out["no-rerank"] = {"top": [[n for n, _ in top] for top in base]}
    for pool in (10, 15, 20, 30, 50):
        run(f"pool={pool}", pool=pool)
    for chars in (80, 160):
        run(f"pool=30/chars={chars}", pool=30, chars=chars)
    for st in (1024, 4096, 16384):
        run(f"pool=30/slice={st}", pool=30, slice_tokens=st)
    run("pool=20/slice=16384", pool=20, slice_tokens=16384)
    run("pool=50/slice=16384", pool=50, slice_tokens=16384)
    if cache_api is not None:
        try:
            for pool in (10, 20, 30, 50):
                run(f"pool={pool}/prefix-cache", pool=pool, prefix=True)
        except Exception as exc:  # an mlx_lm whose cache does not take a batch: recorded, not fatal
            summary["prefix-cache"] = {"error": repr(exc)[:200]}
            _log(f"prefix cache failed: {exc!r}")
    else:
        summary["prefix-cache"] = {"error": "mlx_lm has no make_prompt_cache"}

    result = {"cases": [{"corpus": c[0], "stem": c[1], "style": c[2]} for c in cases],
              "load_average_1m": round(os.getloadavg()[0], 1), "cache_cap_bytes": E.cache_limit_bytes(),
              "reranker": {x: y for x, y in R.status(R.WARM_MODEL).items() if x != "model"},
              "summary": summary, "arms": out}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    _log(f"wrote {args.out}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
