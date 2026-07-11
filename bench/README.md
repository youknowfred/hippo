# hippo recall benchmark (POS-3)

One reproducible number, on a corpus that ships with the repo, produced with **zero tokens, zero
network, and zero LLM calls**. Run it yourself:

```bash
bench/run.sh                       # dense if bootstrapped, else BM25-only
HIPPO_DISABLE_DENSE=1 bench/run.sh # force the model-free path (no ~130MB download)
```

## What it measures

The eval runs hippo's real recall engine over the shipped **50-memory golden dev corpus**
([`tests/golden_corpus/memory/`](../tests/golden_corpus/memory/)) and scores it against **18
hand-written cross-vocabulary paraphrase queries** ([`hard_set.yaml`](../tests/golden_corpus/hard_set.yaml))
— queries deliberately worded *unlike* the memory they should surface, so a hit means the ranker
bridged a vocabulary gap, not matched a keyword it was handed.

| Metric | BM25-only (no model) | Dense hybrid (bootstrapped) |
|---|---|---|
| **recall@10** (a correct memory in the top 10) | **1.00** | **1.00** |
| **MRR@10** (mean reciprocal rank of the first hit) | **0.912** | **0.9213** |
| **cold p95** (per-process, incl. model load) | ~48 ms | ~0.5 s |
| **per-prompt token / network / LLM cost** | **$0** | **$0** |

recall@10 and MRR@10 are **deterministic** — the same corpus + queries produce the same ranking on
any machine, so your run should reproduce these to the digit. Latency is machine-dependent (CPU,
cold vs. warm model cache); the cold p95 is the honest per-process figure the recall hook actually
pays, not a warmed-loop best case. The `$0` is structural: the hot path runs local lexical + (cached)
dense ranking and calls no model API, so recall never spends a token or a byte off your machine.

The single number to lead with: **recall@10 = 1.0 at $0 per prompt** — and, more usefully, *the
same command reproduces it on **your** corpus*, which is the point. A benchmark on the maintainer's
50 memories only tells you the ranker works; run `bench/run.sh` pointed at your own `hard_set.yaml`
(the `/hippo:audit` skill drafts one) to measure the number that matters to you.

## Why not LongMemEval / LoCoMo / BEAM?

Those benchmarks measure a different task: **autonomous extraction of facts from conversation
history** and long-context recall — an agent deciding, on its own, what to remember from a chat and
answering questions about it later. hippo deliberately does **not** do autonomous extraction: every
write to the corpus passes a human approval gate (that is the whole review-gated design). So those
suites would score a capability hippo intentionally lacks, not the one it ships.

What hippo actually does on the hot path is **retrieval over a curated, human-approved corpus** —
so the honest metric is retrieval precision (recall@k / MRR@k) on that corpus, which is exactly what
`bench/run.sh` reports. If and when hippo grows an autonomous-extraction mode, a LongMemEval-style
number becomes meaningful; until then it would be measuring the wrong thing.
