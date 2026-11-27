"""HCR-8 bench: can ``high`` be made to beat ``lean``? One corpus, one embedder, many arms.

    OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.high_eval \\
        --corpus <label> --memory-dir <corpus>/.claude/memory --questions <private.jsonl> \\
        --mlx-cache ~/.claude/plugins/data/hippo-hippo/mlx \\
        --fastembed-cache ~/.claude/plugins/data/hippo-hippo/fastembed \\
        --model Qwen/Qwen3-Embedding-4B --out <private results.json>

``bench/profile_ab.py`` (HCR-6) compares the profiles as shipped over the corpus's own
38-row fixture. This asks the next question on a larger, held-out set: which knob moves
``high``. Each run takes ONE corpus and ONE high embedder, so a process never holds two big
models, and writes PER-QUESTION results (the top-k names each arm returned, its latency) to
``--out``. That file names memories and so is private: it is never committed.
``bench/high_eval_report.py`` reads those files and writes the counts-only evidence.

Two tiers of arm:

* ``lane/…`` — one ranking lane alone, no floor, no fusion: cosine over the description
  rows (and, where the arm says so, the body-chunk rows, max-pooled onto their memory).
  This is where the query instruction and the document text are compared, because a floor
  calibrated for the shipped instruction would otherwise decide the result.
* ``pipe/…`` — ``recall()`` itself, the way the warm server serves it: lean and high as
  shipped, the dense floor swept, the dense/lexical fusion weight swept, the reranker over
  a pool of fused hits, and the document variants with the floor off and on.

Nothing shipped is changed: instructions, document texts and weights are swapped in this
process only. The corpus is read in place and copied to a temp project; nothing is written
beside it. Nothing downloads.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager

_T0 = time.monotonic()

# The query instructions tried. "shipped" is embed_mlx.QUERY_INSTRUCTION, left untouched.
INSTRUCTIONS = {
    "none": None,
    "generic": "Given a web search query, retrieve relevant passages that answer the query",
    "short": "Retrieve the project memory that answers this developer message",
    "note": "Find the engineering note that records the decision, rule or past fix this message is about",
    "task": "Given a message from a developer in the middle of a task, retrieve the earlier lesson, "
            "decision or fact they need to be reminded of",
}
FLOORS = ("0.0", "0.30", "0.36", "0.48")  # beside the calibrated one
DENSE_WEIGHTS = (0.5, 1.5, 2.0, 3.0)  # RRF weight of the dense lane; BM25 stays 1.0
POOLS = (10, 30, 50)


def _log(msg: str) -> None:
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


def _footprint_bytes() -> int:
    out = subprocess.run(["footprint", str(os.getpid())], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "phys_footprint:" in line:
            num, unit = line.split(":")[1].split()[:2]
            return int(float(num) * {"KB": 2**10, "MB": 2**20, "GB": 2**30}.get(unit, 1))
    return 0


@contextmanager
def _env(pairs):
    prev = {n: os.environ.get(n) for n in pairs}
    try:
        for n, v in pairs.items():
            os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)
        yield
    finally:
        for n, v in prev.items():
            os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)


@contextmanager
def _attr(obj, name, value):
    prev = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, prev)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="a label for this corpus in the results")
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--questions", required=True, help="private JSONL: corpus, stem, style, query")
    ap.add_argument("--mlx-cache", required=True)
    ap.add_argument("--fastembed-cache", required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-Embedding-4B")
    ap.add_argument("--out", required=True, help="PRIVATE per-question results (names memories)")
    ap.add_argument("--no-doc-variants", action="store_true", help="skip re-embedding the documents")
    ap.add_argument("--no-sweeps", action="store_true", help="skip the floor and weight sweeps")
    ap.add_argument("--limit", type=int, default=0, help="first N questions only (a smoke run)")
    ap.add_argument("--phase2", action="store_true",
                    help="the follow-up arms only: whole-memory rows with the fusion weight and the "
                         "reranker, the same rows on lean, and the pipeline with MMR or the graph off")
    args = ap.parse_args(argv)

    rows = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rows = [r for r in rows if r.get("corpus") == args.corpus and r.get("query") and not r.get("skip")]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("no questions for this corpus", file=sys.stderr)
        return 1

    src = os.path.abspath(args.memory_dir)
    tmp = tempfile.mkdtemp(prefix="hippo-high-eval-")
    project = os.path.join(tmp, "project")
    md = os.path.join(project, ".claude", "memory")
    shutil.copytree(src, md, ignore=shutil.ignore_patterns(".audit-fixtures"))
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    os.environ.update({
        "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_DATA": data,
        "HIPPO_RETRIEVAL_PROFILE": "high", "HIPPO_HIGH_EMBED_MODEL": args.model,
        "FASTEMBED_CACHE_PATH": os.path.abspath(os.path.expanduser(args.fastembed_cache)),
        "HIPPO_USER_MEMORY_DIR": os.path.join(tmp, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(tmp, "no-private-tier"),
        "HIPPO_TRUST_FILE": os.path.join(tmp, "trust.json"), "HIPPO_TRUST_ALL": "1",
        "HF_HUB_OFFLINE": "1", "HIPPO_RERANK_TIMEOUT": "120", "HIPPO_DENSE_TIMEOUT": "120",
    })
    for knob in ("HIPPO_RERANK", "HIPPO_DENSE_FLOOR", "HIPPO_GRAPH_SEEDS", "HIPPO_DISABLE_DENSE",
                 "HIPPO_DISABLE", "HIPPO_HIGH_EMBED_DIM"):
        os.environ.pop(knob, None)
    subprocess.run(["git", "init", "-q", project], check=True)

    import numpy as np

    from memory import build_index as B
    from memory import embed_mlx as E
    from memory import recall as RC
    from memory import recall_rank as RR
    from memory import rerank_mlx as R

    mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
    E.cache_dir = lambda: mlx_cache
    R.default_on = lambda: False
    k = RC.DEFAULT_K
    space = E.resolve_high_space()
    queries = [r["query"] for r in rows]
    cost: dict = {"footprint_bytes": {"start": _footprint_bytes()}, "build_s": {}, "tokens": {}}

    idx = B.default_index_dir(md)
    t0 = time.monotonic()
    lean_built = B.build_index(md, idx)
    cost["build_s"]["lean"] = round(time.monotonic() - t0, 1)
    if not lean_built["dense_ready"]:
        print("the lean index is not dense", file=sys.stderr)
        return 1
    lean = B.load_index(idx)
    names = [e["name"] for e in lean.entries]
    missing = sorted({r["stem"] for r in rows} - set(names))
    _log(f"lean index: {len(lean.entries)} entries, {len(lean.body_chunks)} chunks; "
         f"{len(rows)} questions, {len(missing)} with no entry")

    emb = E.high_embedder(space)
    cost["model"] = {"space": space, "load_ms": round(emb.load_ms, 1), "resident_bytes": emb.resident_bytes,
                     "cache_cap_bytes": E.cache_limit_bytes()}
    hdir = os.path.join(tmp, "high")
    t0 = time.monotonic()
    for _ in range(8):
        with E.using_space(space):
            B.build_index(md, hdir, allow_download=True)
        hi = B.load_index(hdir)
        if hi is not None and hi.dense_ready and hi.model == space and all(
                x.get("row") is not None for x in hi.entries + hi.body_chunks):
            break
    else:
        print("the high index did not converge", file=sys.stderr)
        return 1
    cost["build_s"]["high"] = round(time.monotonic() - t0, 1)
    cost["footprint_bytes"]["high_index_built"] = _footprint_bytes()
    assert [e["name"] for e in hi.entries] == names, "lean and high index order differ"
    _log(f"high index built in {cost['build_s']['high']} s")

    # ---- document variants: other texts for the description rows ------------------- #
    def body_of(name: str) -> str:
        try:
            text = open(os.path.join(md, name + ".md"), encoding="utf-8", errors="ignore").read()
        except OSError:
            return ""
        return text

    def strip_frontmatter(text: str) -> str:
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                return parts[2].strip()
        return text

    entry_rows = np.array([e["row"] for e in hi.entries])
    chunk_rows = np.array([c["row"] for c in hi.body_chunks], dtype=int)
    chunk_entry = np.array([c["entry"] for c in hi.body_chunks], dtype=int)
    variants = {"shipped": hi}
    whole_docs = [body_of(e["name"]) or e["doc_text"] for e in hi.entries]
    if not args.no_doc_variants:
        texts = {
            # description + the body, to the embedder's 1,024-token cut
            "desc+body": [f"{e['doc_text']}\n\n{strip_frontmatter(body_of(e['name']))}" for e in hi.entries],
            # the whole file as written, frontmatter included
            "whole": [body_of(e["name"]) or e["doc_text"] for e in hi.entries],
        }
        if args.phase2:
            texts.pop("desc+body")
        for label, docs in texts.items():
            t0 = time.monotonic()
            mat = B._normalize_rows(list(emb.embed(docs)))
            cost["build_s"][f"doc:{label}"] = round(time.monotonic() - t0, 1)
            v = copy.copy(hi)
            dense = np.array(hi.dense, copy=True)
            dense[entry_rows] = mat
            v.dense = dense
            variants[label] = v
            _log(f"doc variant {label} embedded in {cost['build_s'][f'doc:{label}']} s")
        # name + description only: the body-chunk rows can never clear a floor
        v = copy.copy(hi)
        dense = np.array(hi.dense, copy=True)
        if len(chunk_rows):
            dense[chunk_rows] = 0.0
        v.dense = dense
        variants["desc-only"] = v
        for label in [x for x in ("desc+body", "whole") if x in variants]:  # the long text alone, no chunk rows
            v = copy.copy(variants[label])
            dense = np.array(variants[label].dense, copy=True)
            if len(chunk_rows):
                dense[chunk_rows] = 0.0
            v.dense = dense
            variants[f"{label}/no-chunks"] = v
    cost["footprint_bytes"]["doc_variants_built"] = _footprint_bytes()

    rr = R.reranker(R.WARM_MODEL)
    cost["reranker"] = {k_: v_ for k_, v_ in R.status(R.WARM_MODEL).items() if k_ != "model"}
    cost["footprint_bytes"]["all_models_loaded"] = _footprint_bytes()

    arms: dict = {}

    def record(name: str, tops, millis, **meta) -> None:
        arms[name] = {"top": tops, "ms": [round(m, 1) for m in millis], **meta}
        hits = sum(r["stem"] in t for r, t in zip(rows, tops))
        _log(f"{name}: {hits}/{len(rows)} in top {k}; p50 {sorted(millis)[len(millis) // 2]:.0f} ms")

    # ---- tier A: one lane alone, no floor ------------------------------------------- #
    def lane(name: str, index, qvecs, *, chunks: bool, **meta) -> None:
        mat = np.asarray(index.dense)
        e_rows = np.array([e["row"] for e in index.entries])
        c_rows = np.array([c["row"] for c in index.body_chunks], dtype=int)
        c_ent = np.array([c["entry"] for c in index.body_chunks], dtype=int)
        tops = []
        for q in qvecs:
            score = mat[e_rows] @ q
            if chunks and len(c_rows):
                np.maximum.at(score, c_ent, mat[c_rows] @ q)
            tops.append([names[i] for i in np.argsort(-score)[:k]])
        record(name, tops, [0.0] * len(qvecs), tier="lane", **meta)

    # ---- tier B: recall() itself ---------------------------------------------------- #
    def pipe(name: str, *, index, high: bool = False, pool: int = 0, env=None, dense_w=None,
             instruction: str = "shipped", **meta) -> None:
        flags = {"HIPPO_RERANK": "0"}
        flags.update(env or {})
        tops, millis = [], []
        with ExitStack() as st:
            st.enter_context(_env(flags))
            if high:
                st.enter_context(E.using_space(space))
                if instruction != "shipped":
                    ins = INSTRUCTIONS[instruction]
                    st.enter_context(_attr(E, "query_text", (lambda t: t) if ins is None
                                           else (lambda t, _i=ins: f"Instruct: {_i}\nQuery:{t}")))
            if pool:
                st.enter_context(R.using_reranker(R.WARM_MODEL))
            if dense_w is not None:
                st.enter_context(_attr(RC, "_intent_weights", lambda q, toks, _w=dense_w: (_w, 1.0)))

            def one(query: str):
                got = RC.recall(query, k=max(pool, k), index=index, index_dir=idx, memory_dir=md)
                if pool:
                    got = RR._cross_encoder_rerank(query, got)
                return [r["name"] for r in got if r.get("corpus") != "rule"][:k]

            one(queries[0])  # untimed: the arm's models wake before the measured pass
            for q in queries:
                t0 = time.perf_counter()
                tops.append(one(q))
                millis.append((time.perf_counter() - t0) * 1000.0)
        record(name, tops, millis, tier="pipe", space=space if high else "lean", pool=pool,
               env=dict(env or {}), dense_w=dense_w, instruction=instruction, **meta)

    if args.phase2:
        whole, lean_whole = variants["whole"], copy.copy(lean)
        # the control: the same whole-memory text through lean's own model (its 512-token cut)
        dense = np.array(lean.dense, copy=True)
        dense[np.array([e["row"] for e in lean.entries])] = B.embed_documents(whole_docs)
        lean_whole.dense = dense
        lean_q = [B.embed_query(q) for q in queries]
        with E.using_space(space):
            high_q = [B.embed_query(q) for q in queries]
        lane("p2/lane/lean/doc=whole", lean_whole, lean_q, chunks=False, space="lean", doc="whole")
        lane("p2/lane/lean/doc=whole+chunks", lean_whole, lean_q, chunks=True, space="lean", doc="whole")
        lane("p2/lane/high/doc=whole+chunks", whole, high_q, chunks=True, space=space, doc="whole")
        pipe("p2/lean", index=lean)
        pipe("p2/lean/doc=whole", index=lean_whole, doc="whole")
        pipe("p2/lean/doc=whole+rerank/pool=30", index=lean_whole, pool=30, doc="whole")
        pipe("p2/high/doc=whole", index=whole, high=True, doc="whole")
        for w in (2.0, 3.0, 5.0, 10.0):
            pipe(f"p2/high/doc=whole/dense_w={w}", index=whole, high=True, dense_w=w, doc="whole")
        for p_ in (10, 30):
            pipe(f"p2/high/doc=whole+rerank/pool={p_}", index=whole, high=True, pool=p_, doc="whole")
        off = {"mmr-off": {"HIPPO_MMR_LAMBDA": "1.0"}, "graph-off": {"HIPPO_GRAPH_SEEDS": "0"},
               "mmr+graph-off": {"HIPPO_MMR_LAMBDA": "1.0", "HIPPO_GRAPH_SEEDS": "0"}}
        for tag, env_ in off.items():
            pipe(f"p2/lean/{tag}", index=lean, env=env_)
            pipe(f"p2/high/{tag}", index=hi, high=True, env=env_)
            pipe(f"p2/high/doc=whole/{tag}", index=whole, high=True, env=env_, doc="whole")
        pipe("p2/high/doc=whole/mmr+graph-off/dense_w=3.0", index=whole, high=True, dense_w=3.0, doc="whole",
             env=off["mmr+graph-off"])
        pipe("p2/lean#2", index=lean)
        self_check = arms["p2/lean"]["top"] == arms.pop("p2/lean#2")["top"]
    else:
        lean_q = [B.embed_query(q) for q in queries]
        lane("lane/lean", lean, lean_q, chunks=False, space="lean")
        lane("lane/lean+chunks", lean, lean_q, chunks=True, space="lean")

        def high_qvecs(label: str):
            with ExitStack() as st:
                st.enter_context(E.using_space(space))
                if label != "shipped":
                    ins = INSTRUCTIONS[label]
                    st.enter_context(_attr(E, "query_text", (lambda t: t) if ins is None
                                           else (lambda t, _i=ins: f"Instruct: {_i}\nQuery:{t}")))
                return [B.embed_query(q) for q in queries]

        qv = {label: high_qvecs(label) for label in ["shipped"] + list(INSTRUCTIONS)}
        _log("query vectors embedded for every instruction")
        for ins, vecs in qv.items():
            lane(f"lane/high/ins={ins}", hi, vecs, chunks=False, space=space, instruction=ins)
            lane(f"lane/high/ins={ins}+chunks", hi, vecs, chunks=True, space=space, instruction=ins)
        for label, v in variants.items():
            if label == "shipped" or label.endswith("/no-chunks") or label == "desc-only":
                continue
            for ins in ("shipped", "none"):
                lane(f"lane/high/doc={label}/ins={ins}", v, qv[ins], chunks=False, space=space,
                     instruction=ins, doc=label)
                lane(f"lane/high/doc={label}/ins={ins}+chunks", v, qv[ins], chunks=True, space=space,
                     instruction=ins, doc=label)

        pipe("pipe/lean", index=lean)
        pipe("pipe/bm25", index=lean, env={"HIPPO_DISABLE": "dense"})
        for p in POOLS:
            pipe(f"pipe/lean+rerank/pool={p}", index=lean, pool=p)
        pipe("pipe/high", index=hi, high=True)
        for p in POOLS:
            pipe(f"pipe/high+rerank/pool={p}", index=hi, high=True, pool=p)
        if not args.no_sweeps:
            for f in FLOORS:
                pipe(f"pipe/high/floor={f}", index=hi, high=True, env={"HIPPO_DENSE_FLOOR": f})
                pipe(f"pipe/lean/floor={f}", index=lean, env={"HIPPO_DENSE_FLOOR": f})
            for w in DENSE_WEIGHTS:
                pipe(f"pipe/high/dense_w={w}", index=hi, high=True, dense_w=w)
                pipe(f"pipe/lean/dense_w={w}", index=lean, dense_w=w)
            for ins in INSTRUCTIONS:
                pipe(f"pipe/high/ins={ins}", index=hi, high=True, instruction=ins)
                pipe(f"pipe/high/ins={ins}/floor=0.0", index=hi, high=True, instruction=ins,
                     env={"HIPPO_DENSE_FLOOR": "0.0"})
        for label, v in variants.items():
            if label == "shipped":
                continue
            pipe(f"pipe/high/doc={label}", index=v, high=True, doc=label)
            pipe(f"pipe/high/doc={label}/floor=0.0", index=v, high=True, doc=label,
                 env={"HIPPO_DENSE_FLOOR": "0.0"})
        pipe("pipe/lean#2", index=lean)  # the baseline again: the rig leaked nothing if it repeats

        self_check = arms["pipe/lean"]["top"] == arms["pipe/lean#2"]["top"]

    # ---- the reranker's latency by pool, alone -------------------------------------- #
    rerank_ms: dict = {}
    descs = [e.get("description") or e["doc_text"] for e in lean.entries]
    for p in (5, 10, 15, 20, 30, 50):
        ms = []
        for i, q in enumerate(queries[:40]):
            docs = [descs[(i * 7 + j * 13) % len(descs)] for j in range(p)]
            t0 = time.perf_counter()
            rr.score(q, docs)
            ms.append((time.perf_counter() - t0) * 1000.0)
        ms.sort()
        rerank_ms[str(p)] = {"p50": round(ms[len(ms) // 2], 1), "p95": round(ms[int(len(ms) * 0.95) - 1], 1)}
    cost["rerank_score_ms_by_pool"] = rerank_ms
    cost["footprint_bytes"]["end"] = _footprint_bytes()
    import mlx.core as mx

    cost["mlx_peak_bytes"] = int(mx.get_peak_memory())
    cost["load_average_1m"] = round(os.getloadavg()[0], 1)

    out = {
        "corpus": args.corpus, "model": args.model, "space": space, "k": k,
        "entries": len(lean.entries), "body_chunks": len(lean.body_chunks),
        "questions": [{"stem": r["stem"], "style": r.get("style"), "query": r["query"],
                       "resolvable": r["stem"] in set(names)} for r in rows],
        "floors": {"lean": RR._DENSE_FLOOR_BY_MODEL.get(B.DEFAULT_MODEL, RR._DENSE_FLOOR_DEFAULT),
                   "high": RR._DENSE_FLOOR_BY_MODEL.get(space, RR._DENSE_FLOOR_DEFAULT)},
        "baseline_self_check": self_check, "cost": cost, "arms": arms,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    _log(f"wrote {args.out}; baseline self-check {'pass' if self_check else 'FAIL'}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if self_check else 2


if __name__ == "__main__":
    raise SystemExit(main())
