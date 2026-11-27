"""HCR-9 bench: what the MMR re-cut does when near-duplicates are in the corpus.

    OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.mmr_eval \\
        --space lean --rows description --work <bench.space_calibration's --work> \\
        --live hippo=<corpus>/.claude/memory [--live LABEL=DIR ...] \\
        --questions <private questions.jsonl> --gold <private gold.json> \\
        --selection <private selection.json> --dups <private dir of out-LABEL.jsonl> \\
        --out <private.json>

HCR-8 scored one target per question, so it could count what MMR costs (a right answer
pushed out of the top 10) and not what it is for (near-duplicate rows taking the places of
other memories). This set adds the duplicates. For a sample of held-out questions, two
re-captures were written of the question's target memory and two of its strongest wrong
competitor: the same fact saved again in other words under another name, by a writer shown
only that memory. Three corpora per run:

* ``organic``: the corpus as it is, every held-out question.
* ``competitor``: plus the competitors' re-captures. Three notes now say the wrong thing
  about each sampled question; MMR's best case is to drop two and let the target up.
* ``target``: plus the targets' re-captures. Three notes now answer each sampled question;
  any of them is a hit, and the other two are rows a reader did not need.

Arms: the re-cut as v1.48 shipped it (lambda 0.8 on the raw fused score), off (lambda 1.0), and the
re-cut with relevance scaled to the pool (``recall_rank._MMR_NORMALIZE``) at five lambdas; 0.4
is what ships since this evidence (planning/HCR-9.mmr.evidence.2026-11-27.json).
Per question the run keeps numbers only: the reciprocal rank of the first right answer, and
how many rows of the top 10 and top 5 belong to the target's or the competitor's cluster.
The output names no query and no memory; it is still private (written outside the repo).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from contextlib import ExitStack

from bench.high_eval_report import SEED, boot_mean, paired, wilson

_T0 = time.monotonic()
K = 10
ARMS = {  # name -> (HIPPO_MMR_LAMBDA, relevance scaled to the pool)
    "v1.48 (lambda 0.8, raw score)": ("0.8", False),
    "off": ("1.0", False),
    "pool-scaled, lambda 0.8": ("0.8", True),
    "pool-scaled, lambda 0.6": ("0.6", True),
    "pool-scaled, lambda 0.4": ("0.4", True),
    "pool-scaled, lambda 0.3": ("0.3", True),
    "pool-scaled, lambda 0.2": ("0.2", True),
}


def _log(msg: str) -> None:
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


def _write_dup(md: str, rec: dict) -> str:
    desc = " ".join(str(rec["description"]).split()).replace('"', "'")
    path = os.path.join(md, rec["slug"] + ".md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {rec["slug"]}\ndescription: "{desc}"\nmetadata:\n  type: {rec.get("type") or "project"}\n'
                 f'---\n\n{str(rec["body"]).strip()}\n')
    return rec["slug"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="'lean', or a high space id")
    ap.add_argument("--rows", choices=("whole", "description"), required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--live", action="append", default=[], metavar="LABEL=MEMORY_DIR")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--gold", default=None)
    ap.add_argument("--selection", required=True, help="private JSON: label -> [{stem, distractor}, ...]")
    ap.add_argument("--dups", required=True, help="private dir holding out-LABEL.jsonl")
    ap.add_argument("--labels", default="", help="rename corpora in the output, a=b,c=d")
    ap.add_argument("--mlx-cache", default=None)
    ap.add_argument("--fastembed-cache", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    lean_arm = args.space == "lean"
    work = os.path.abspath(args.work)
    pd = os.path.join(work, "plugin-data")
    os.makedirs(pd, exist_ok=True)
    os.environ.update({
        "CLAUDE_PLUGIN_DATA": pd, "HIPPO_USER_MEMORY_DIR": os.path.join(work, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(work, "no-private-tier"),
        "HIPPO_TRUST_ALL": "1", "HIPPO_TRUST_FILE": os.path.join(work, "trust.json"), "HF_HUB_OFFLINE": "1",
        "HIPPO_DENSE_TIMEOUT": "120", "HIPPO_RERANK": "0",
    })
    if args.fastembed_cache:
        os.environ["FASTEMBED_CACHE_PATH"] = os.path.abspath(os.path.expanduser(args.fastembed_cache))
    for v in ("HIPPO_DENSE_FLOOR", "HIPPO_DENSE_FLOOR_BAND", "HIPPO_DISABLE", "HIPPO_DISABLE_DENSE",
              "HIPPO_RETRIEVAL_PROFILE", "HIPPO_HIGH_EMBED_MODEL", "HIPPO_HIGH_EMBED_DIM", "HIPPO_GRAPH_SEEDS"):
        os.environ.pop(v, None)

    from memory import build_index_dense as BD
    from memory import embed_mlx as E
    from memory import recall as R
    from memory import recall_rank as RR
    from memory.build_index import DEFAULT_MODEL, build_index, default_index_dir, load_index

    space = DEFAULT_MODEL if lean_arm else args.space
    model = space.split("@", 1)[0]
    BD.WHOLE_ROW_MODELS = (frozenset(BD.WHOLE_ROW_MODELS | {model}) if args.rows == "whole"
                           else frozenset(BD.WHOLE_ROW_MODELS - {model}))
    if not lean_arm and args.mlx_cache:
        mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
        E.cache_dir = lambda: mlx_cache
    tag = f"{'lean' if lean_arm else model.rsplit('/', 1)[-1]}-{args.rows}"
    rename = dict(p.split("=", 1) for p in args.labels.split(",") if "=" in p)

    orig_eq, qcache = RR.embed_query, {}

    def cached_eq(text, allow_download=False):
        key = (E.active_space() or DEFAULT_MODEL, text)
        if key not in qcache:
            qcache[key] = orig_eq(text, allow_download=allow_download)
        return qcache[key]

    RR.embed_query = R.embed_query = cached_eq

    gold = json.load(open(args.gold, encoding="utf-8")) if args.gold else {}
    selection = json.load(open(args.selection, encoding="utf-8"))
    questions: dict = {}
    for line in open(args.questions, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("query") and not r.get("skip"):
                questions.setdefault(r["corpus"], []).append(r)

    def indexed(root: str):
        md = os.path.join(root, ".claude", "memory")
        lean_dir = default_index_dir(md)
        build_index(md, lean_dir, allow_download=True)
        idir = lean_dir
        if not lean_arm:
            idir = os.path.join(lean_dir, "high")
            for _ in range(8):
                with E.using_space(space):
                    build_index(md, idir, allow_download=True)
                hi = load_index(idir)
                if hi is not None and hi.dense_ready:
                    break
        index = load_index(idir)
        assert index is not None and index.dense_ready and index.model == space, root
        assert (index.manifest.get("row_text") or "description") == args.rows, root
        return md, lean_dir, index

    shipped_scale = RR._MMR_NORMALIZE

    def tops(md, lean_dir, index, qs):
        out = {}
        for arm, (lam, norm) in ARMS.items():
            os.environ["HIPPO_MMR_LAMBDA"] = lam
            RR._MMR_NORMALIZE = norm
            with ExitStack() as st:
                if not lean_arm:
                    st.enter_context(E.using_space(space))
                out[arm] = [[r["name"] for r in R.recall(q["query"], k=K, index=index, index_dir=lean_dir,
                                                         memory_dir=md) if r.get("corpus") != "rule"][:K]
                            for q in qs]
        os.environ.pop("HIPPO_MMR_LAMBDA", None)
        RR._MMR_NORMALIZE = shipped_scale
        return out

    per: dict = {c: {a: [] for a in ARMS} for c in ("organic", "competitor", "target")}
    doc: dict = {"space": space, "rows": args.rows, "k": K, "corpora": {}}
    for spec in args.live:
        label, src = spec.split("=", 1)
        shown = rename.get(label, label)
        base = os.path.join(work, tag, label)
        if not os.path.isdir(base):
            os.makedirs(os.path.dirname(base), exist_ok=True)
            shutil.copytree(os.path.abspath(src), os.path.join(base, ".claude", "memory"), symlinks=False)
            os.system(f"git init -q '{base}'")
        picked = {r["stem"]: r for r in selection[label]}
        dups: dict = {}
        for line in open(os.path.join(args.dups, f"out-{label}.jsonl"), encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                dups.setdefault(rec["source"], []).append(rec)
        qs_all = questions.get(label, [])
        md, lean_dir, index = indexed(base)
        names = {e["name"] for e in index.entries}
        qs_all = [q for q in qs_all if q["stem"] in names]
        qs_pick = [q for q in qs_all if q["stem"] in picked]
        right = lambda q: {q["stem"], *gold.get(f"{label}::{q['stem']}", [])}  # noqa: E731
        sims: list = []

        def run(condition: str, root: str, sources, qs):
            """Add the re-captures of ``sources`` to a copy of the base corpus and score ``qs``."""
            cluster: dict = {}
            if sources is not None:
                if os.path.isdir(root):
                    shutil.rmtree(root)
                shutil.copytree(base, root, symlinks=True)
                vmd = os.path.join(root, ".claude", "memory")
                for s in sorted(sources):
                    for rec in dups.get(s, []):
                        if rec["slug"] in names or rec["slug"] in cluster:
                            continue  # a writer's slug collided with a real memory: left out
                        cluster[_write_dup(vmd, rec)] = s
            vmd, vlean, vindex = indexed(root)
            members: dict = {}
            for slug, s in cluster.items():
                members.setdefault(s, {s}).add(slug)
            if cluster and vindex.dense is not None:
                row = {e["name"]: e["row"] for e in vindex.entries}
                sims.extend(float(vindex.dense[row[slug]] @ vindex.dense[row[s]])
                            for slug, s in cluster.items() if slug in row and s in row)
            got = tops(vmd, vlean, vindex, qs)
            for arm, lists in got.items():
                for q, top in zip(qs, lists):
                    ok = set(right(q))
                    if condition == "target":
                        ok |= members.get(q["stem"], set())
                    comp = members.get(picked[q["stem"]]["distractor"], {picked[q["stem"]]["distractor"]}) \
                        if q["stem"] in picked else set()
                    rr = next((1.0 / (i + 1) for i, n in enumerate(top) if n in ok), 0.0)
                    per[condition][arm].append({
                        "corpus": shown, "rr": rr, "hit5": any(n in ok for n in top[:5]),
                        "right10": sum(n in ok for n in top), "right5": sum(n in ok for n in top[:5]),
                        "comp10": sum(n in comp for n in top), "comp5": sum(n in comp for n in top[:5]),
                    })
            return len(cluster)

        run("organic", base, None, qs_all)
        n_comp = run("competitor", base + "+competitor", {picked[q["stem"]]["distractor"] for q in qs_pick}, qs_pick)
        sim_comp, sims[:] = list(sims), []
        n_tgt = run("target", base + "+target", {q["stem"] for q in qs_pick}, qs_pick)
        both = sorted(sim_comp + sims)
        doc["corpora"][shown] = {
            "memories": len(index.entries), "questions": len(qs_all), "sampled_questions": len(qs_pick),
            "recaptures_added": {"competitor": n_comp, "target": n_tgt},
            "recapture_cosine_to_its_source": {"n": len(both), "min": round(both[0], 3),
                                               "p50": round(both[len(both) // 2], 3), "max": round(both[-1], 3)}
            if both else {"n": 0},
        }
        _log(f"{label}: " + "; ".join(
            f"{c} " + ", ".join(str(sum(r["rr"] > 0 for r in per[c][a][-len(qs_all if c == "organic" else qs_pick):]))
                                for a in ARMS) for c in per) + "  (hits per arm, in ARMS order)")

    # ---- the counts, each with its interval ----------------------------------------- #
    def mean_ci(vals):
        return {"mean": round(sum(vals) / len(vals), 3), "ci95": boot_mean(vals, random.Random(SEED))}

    doc["conditions"] = {}
    for cond, by_arm in per.items():
        rec: dict = {"arms": {}, "against_off": {}}
        base_rows = by_arm["off"]
        for arm, rows in by_arm.items():
            n = len(rows)
            hits = sum(r["rr"] > 0 for r in rows)
            hits5 = sum(r["hit5"] for r in rows)
            out = {"n": n, "hits_at_10": hits, "hit_rate_ci95": wilson(hits, n),
                   "hits_at_5": hits5, "hit_rate_at_5_ci95": wilson(hits5, n),
                   "mrr": mean_ci([r["rr"] for r in rows])}
            if cond == "competitor":
                out["competitor_rows_in_top_10"] = mean_ci([float(r["comp10"]) for r in rows])
                out["competitor_rows_in_top_5"] = mean_ci([float(r["comp5"]) for r in rows])
            if cond == "target":
                out["right_rows_in_top_10"] = mean_ci([float(r["right10"]) for r in rows])
                out["right_rows_in_top_5"] = mean_ci([float(r["right5"]) for r in rows])
            rec["arms"][arm] = out
            if arm == "off":
                continue
            d = {"hits_and_mrr": paired([r["rr"] for r in rows], [b["rr"] for b in base_rows], random.Random(SEED))}
            d["hit_at_5_delta"] = mean_ci([float(r["hit5"]) - float(b["hit5"]) for r, b in zip(rows, base_rows)])
            for key in (("comp10", "comp5") if cond == "competitor" else ("right10", "right5") if cond == "target" else ()):
                d[f"{key}_delta"] = mean_ci([float(r[key] - b[key]) for r, b in zip(rows, base_rows)])
            rec["against_off"][arm] = d
        doc["conditions"][cond] = rec
    # per question, the corpora as they are: reciprocal ranks only, so two runs (lean, high) can be paired
    doc["organic_reciprocal_ranks"] = {arm: [r["rr"] for r in rows] for arm, rows in per["organic"].items()}
    doc["note"] = ("counts and intervals only, no query and no memory name. Intervals are 95%: Wilson for a hit "
                   "rate, percentile bootstrap (2000 resamples) for a mean and for every paired difference.")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
