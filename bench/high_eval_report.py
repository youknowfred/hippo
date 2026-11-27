"""HCR-8: the counts-only evidence from ``bench/high_eval.py``'s private per-question results.

    python -m bench.high_eval_report --results <private dir or files…> [--gold <private.json>] \\
        --out planning/HCR-8.evidence.<date>.json

Every number carries an interval, so a two-row gap is not read as a result:

* hits out of n: a Wilson 95% interval on the proportion;
* MRR: a percentile bootstrap over questions (2,000 resamples, fixed seed);
* each arm against a reference arm: the PAIRED difference in hit rate and in MRR over the
  same questions, with a paired-bootstrap 95% interval and the count of questions only one
  of the two found (the discordant pairs). "resolved" is true only when the interval
  excludes zero.

Results are pooled across the corpora that ran an arm, and also given per corpus and per
question style. ``--gold`` adds, per question, other memories a judge accepted as correct
(pooled judging); without it the one memory the question was written from is the only
right answer. The output names no query and no memory.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import math
import os
import random
import sys

SEED, BOOT = 20261126, 2000


def wilson(h: int, n: int):
    if not n:
        return [0.0, 0.0]
    z, p = 1.96, h / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - w) / d, 4), round((c + w) / d, 4)]


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(q * len(xs))))]


def boot_mean(vals, rng):
    n = len(vals)
    means = [sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT)]
    return [round(pct(means, 0.025), 4), round(pct(means, 0.975), 4)]


def rr_of(top, gold):
    for i, name in enumerate(top):
        if name in gold:
            return 1.0 / (i + 1)
    return 0.0


def summarize(rrs, ms, rng):
    n = len(rrs)
    hits = sum(r > 0 for r in rrs)
    hits5 = sum(r >= 0.2 for r in rrs)
    out = {"n": n, "hits": hits, "hit_rate": round(hits / n, 4), "hit_rate_ci95": wilson(hits, n),
           "hits_at_5": hits5, "hits_at_1": sum(r == 1.0 for r in rrs),
           "mrr": round(sum(rrs) / n, 4), "mrr_ci95": boot_mean(rrs, rng)}
    live = sorted(m for m in ms if m > 0)
    if live:
        out["latency_ms"] = {"p50": round(pct(live, 0.5), 1), "p95": round(pct(live, 0.95), 1)}
    return out


def paired(a, b, rng):
    """a against b over the same questions: difference in hit rate and MRR, paired bootstrap."""
    n = len(a)
    dh = [(x > 0) - (y > 0) for x, y in zip(a, b)]
    dm = [x - y for x, y in zip(a, b)]
    hci, mci = boot_mean(dh, rng), boot_mean(dm, rng)
    return {"n": n, "only_this_found": sum(d > 0 for d in dh), "only_reference_found": sum(d < 0 for d in dh),
            "hit_rate_delta": round(sum(dh) / n, 4), "hit_rate_delta_ci95": hci,
            "mrr_delta": round(sum(dm) / n, 4), "mrr_delta_ci95": mci,
            "resolved": {"hits": not (hci[0] <= 0 <= hci[1]), "mrr": not (mci[0] <= 0 <= mci[1])}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--gold", default=None)
    ap.add_argument("--references", default="pipe/lean,pipe/lean+rerank/pool=30,pipe/high")
    ap.add_argument("--method", default=None, help="a JSON file describing how the set was made")
    ap.add_argument("--rerank-summary", default=None,
                    help="the counts-only summary of bench/rerank_latency.py's run, rescored with --gold")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    files = []
    for path in args.results:
        files += sorted(glob.glob(os.path.join(path, "results-*.json"))) if os.path.isdir(path) else [path]
    runs = [json.load(open(f, encoding="utf-8")) for f in files]
    extra = json.load(open(args.gold, encoding="utf-8")) if args.gold else {}
    refs = [r for r in args.references.split(",") if r]

    # (model tag) -> arm -> list of (corpus, style, rr, ms) aligned by question
    report = {"item": "HCR-8", "date": datetime.date.today().isoformat(), "runs": [], "models": {}}
    by_model: dict = {}
    for run in runs:
        tag = run["model"].rsplit("-", 1)[-1].lower()
        golds = []
        for q in run["questions"]:
            more = extra.get(f"{run['corpus']}::{q['stem']}", [])
            golds.append({q["stem"], *more})
        cost = run["cost"]
        report["runs"].append({
            "corpus": run["corpus"], "model": run["model"], "entries": run["entries"],
            "body_chunks": run["body_chunks"], "questions": len(run["questions"]),
            "questions_whose_memory_is_indexed": sum(q["resolvable"] for q in run["questions"]),
            "extra_gold_memories": sum(len(g) - 1 for g in golds),
            "baseline_self_check": "pass" if run["baseline_self_check"] else "FAIL",
            "floors": run["floors"], "cost": cost,
        })
        for arm, rec in run["arms"].items():
            if arm.startswith("p2/"):
                arm = arm[3:] + " [p2]"  # the follow-up pass: its own baseline, same questions
            rows = by_model.setdefault(tag, {}).setdefault(arm, [])
            for q, gold, top, ms in zip(run["questions"], golds, rec["top"], rec["ms"]):
                rows.append((run["corpus"], q.get("style") or "", rr_of(top, gold), ms))

    for tag, arms in sorted(by_model.items()):
        out = {"arms": {}, "against": {}}
        for arm, rows in arms.items():
            if arm.endswith("#2"):
                continue
            rng = random.Random(SEED)
            rec = {"pooled": summarize([r[2] for r in rows], [r[3] for r in rows], rng), "by_corpus": {},
                   "by_style": {}}
            for key, pos in (("by_corpus", 0), ("by_style", 1)):
                for val in sorted({r[pos] for r in rows}):
                    sub = [r for r in rows if r[pos] == val]
                    rec[key][val] = summarize([r[2] for r in sub], [r[3] for r in sub], random.Random(SEED))
            out["arms"][arm] = rec
        for ref in refs:
            if ref not in arms:
                continue
            base = arms[ref]
            out["against"][ref] = {}
            for arm, rows in arms.items():
                if arm == ref or arm.endswith("#2") or len(rows) != len(base):
                    continue
                rec = {"pooled": paired([r[2] for r in rows], [b[2] for b in base], random.Random(SEED)),
                       "by_style": {}}
                for val in sorted({r[1] for r in rows}):
                    idx = [i for i, r in enumerate(rows) if r[1] == val]
                    rec["by_style"][val] = paired([rows[i][2] for i in idx], [base[i][2] for i in idx],
                                                  random.Random(SEED))
                out["against"][ref][arm] = rec
        report["models"][tag] = out
    if args.method:
        report["method"] = json.load(open(args.method, encoding="utf-8"))
    if args.rerank_summary:
        report["lean_rerank_latency"] = json.load(open(args.rerank_summary, encoding="utf-8"))
    report["note"] = ("private corpora and questions: counts and intervals only, no query and no memory "
                      "name. Intervals are 95%: Wilson for a hit rate, percentile bootstrap "
                      f"({BOOT} resamples, seed {SEED}) for MRR and for every paired difference.")
    text = json.dumps(report, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
