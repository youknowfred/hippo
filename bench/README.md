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
— queries deliberately worded *unlike* the memory they should surface, a realistic paraphrase gap.

| Metric | BM25-only (no model) | Dense hybrid (bootstrapped) |
|---|---|---|
| **recall@10** (a correct memory in the top 10) | **1.00** | **1.00** |
| **MRR@10** (mean reciprocal rank of the first hit) | **0.9144** | **0.9111** |
| **cold p95** (per-process, incl. model load) | ~46 ms | ~300 ms |
| **per-prompt token / network / LLM cost** | **$0** | **$0** |

**Read the MRR@10 row as a tie, and read this corpus as unable to settle the question.** The two
backends land 0.0033 apart on 18 queries, where a single query slipping one rank is worth 0.0278 —
8.4× the gap. That is noise, not a result, in either direction. `recall@10` ties at the 1.0 ceiling
because BM25 alone clears it here on content-word overlap, which is exactly why this corpus cannot
discriminate: it is the only fixture in the suite with real lexical overlap (the others are
engineered with near-zero overlap so BM25 trivially clears their gates), and 18 paraphrase queries
is too few to resolve a difference this small. An earlier version of this page reported
0.912 → 0.9213 and told you dense "earns its keep" on the ranking gap. Both halves have since
drifted — `RET-12` stemming moved BM25 up, `recall.py` moved dense down — and the conclusion was
never re-measured. Deciding between the backends needs a bigger paraphrase hard set than this one.

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
