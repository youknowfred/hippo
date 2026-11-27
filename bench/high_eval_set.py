"""HCR-8: build the held-out question set and its pooled labels — every step but the writing.

    python -m bench.high_eval_set sample  --corpus label=<memory dir> … --n label=N … --out <dir>
    python -m bench.high_eval_set pool    --corpus label=<memory dir> … --results <dir> --out <dir>
    python -m bench.high_eval_set gold    --dir <dir>

The questions and the judgments are written by language-model readers, not by this script,
and never by a retriever. The script does the parts that must be mechanical and repeatable:

``sample``  draws memories at random (fixed seed) from each corpus — files with frontmatter,
            at least 400 characters, not superseded or invalidated — and writes batches of 50
            ``{corpus, stem, path, style}``, alternating ``natural`` and ``paraphrase``. A
            writer is shown ONE memory's file and asked for one 8-30 word developer message
            it answers; ``natural`` may use the memory's own key terms, ``paraphrase`` must
            avoid the distinctive words of its name and description. Writers answer into
            ``questions-<n>.jsonl``; ``cat`` them into ``questions.jsonl``.
``pool``    after ``bench/high_eval.py`` has run, builds the judging batches: per question,
            the union of the top 3 of ``POOL_ARMS`` plus the source memory, shuffled, each
            shown as its file's first 650 characters, under an opaque id (the key that maps
            an id back to its question is a separate file judges are not given). A judge
            answers ``judged-<n>.jsonl``: ``{id, correct: [name, …]}``.
``gold``    folds the judgments into ``gold.json``: ``{"<corpus>::<stem>": [other correct
            memories]}`` for ``bench/high_eval_report.py --gold``, and prints how many source
            memories the judges confirmed.

Every file this writes names memories and carries their text: keep ``--out`` outside the
repository. Nothing here is committed but this script.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys

SEED = 20261126
POOL_ARMS = ("pipe/lean", "pipe/bm25", "pipe/lean+rerank/pool=30", "pipe/high", "pipe/high+rerank/pool=30",
             "pipe/high/doc=whole", "lane/high/doc=whole/ins=shipped+chunks", "lane/lean+chunks")


def _pairs(specs):
    return dict(s.split("=", 1) for s in specs)


def _eligible(memory_dir: str):
    out = []
    for f in sorted(glob.glob(os.path.join(memory_dir, "*.md"))):
        if os.path.basename(f) in ("MEMORY.md", "README.md"):
            continue
        text = open(f, encoding="utf-8", errors="ignore").read()
        if not text.startswith("---") or len(text) < 400:
            continue
        front = text.split("---")[1]
        if "invalid_after:" in front or "superseded_by:" in front:
            continue
        out.append(f)
    return out


def sample(args) -> int:
    rng = random.Random(SEED)
    rows = []
    for label, d in _pairs(args.corpus).items():
        n = int(_pairs(args.n)[label])
        pick = rng.sample(_eligible(d), n)
        rows += [{"corpus": label, "stem": os.path.basename(f)[:-3], "path": f,
                  "style": "natural" if i % 2 == 0 else "paraphrase"} for i, f in enumerate(pick)]
        print(f"{label}: {n} of {len(_eligible(d))} eligible", file=sys.stderr)
    rng.shuffle(rows)
    os.makedirs(args.out, exist_ok=True)
    for b, i in enumerate(range(0, len(rows), 50), 1):
        json.dump(rows[i:i + 50], open(os.path.join(args.out, f"batch-{b}.json"), "w"), indent=0)
    return 0


def pool(args) -> int:
    rng = random.Random(7)
    items = []
    for label, d in _pairs(args.corpus).items():
        res = json.load(open(os.path.join(args.results, f"results-{label}-4b.json"), encoding="utf-8"))
        for i, q in enumerate(res["questions"]):
            cand = {q["stem"]}
            for arm in POOL_ARMS:
                cand.update(res["arms"][arm]["top"][i][:3])
            cs = []
            for name in cand:
                try:
                    text = open(os.path.join(d, name + ".md"), encoding="utf-8", errors="ignore").read()
                except OSError:
                    text = ""
                cs.append({"name": name, "text": text[:650]})
            rng.shuffle(cs)
            items.append({"qid": f"{label}::{q['stem']}", "query": q["query"], "candidates": cs})
    rng.shuffle(items)
    per = (len(items) + args.batches - 1) // args.batches
    for b in range(args.batches):
        part, key = items[b * per:(b + 1) * per], {}
        for j, it in enumerate(part):
            oid = f"q{b + 1}-{j + 1}"
            key[oid] = it.pop("qid")
            it["id"] = oid
        json.dump(part, open(os.path.join(args.out, f"judge-{b + 1}.json"), "w"))
        json.dump(key, open(os.path.join(args.out, f"judge-key-{b + 1}.json"), "w"))
    print(f"{len(items)} questions, {sum(len(x['candidates']) for x in items)} candidates", file=sys.stderr)
    return 0


def gold(args) -> int:
    extra, confirmed, total = {}, 0, 0
    for keyf in sorted(glob.glob(os.path.join(args.dir, "judge-key-*.json"))):
        b = keyf.rsplit("-", 1)[1][:-5]
        key = json.load(open(keyf))
        for line in open(os.path.join(args.dir, f"judged-{b}.jsonl")):
            if not line.strip():
                continue
            row = json.loads(line)
            qid = key[row["id"]]
            stem = qid.split("::", 1)[1]
            total += 1
            confirmed += stem in row["correct"]
            more = sorted(set(row["correct"]) - {stem})
            if more:
                extra[qid] = more
    json.dump(extra, open(os.path.join(args.dir, "gold.json"), "w"))
    print(f"{total} judged; source confirmed {confirmed}; {sum(map(len, extra.values()))} other correct "
          f"memories on {len(extra)} questions", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--corpus", nargs="+", required=True)
    s.add_argument("--n", nargs="+", required=True)
    s.add_argument("--out", required=True)
    p = sub.add_parser("pool")
    p.add_argument("--corpus", nargs="+", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--batches", type=int, default=10)
    g = sub.add_parser("gold")
    g.add_argument("--dir", required=True)
    args = ap.parse_args(argv)
    return {"sample": sample, "pool": pool, "gold": gold}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
