# hippo recall benchmark (POS-3)

One reproducible number, on a corpus that ships with the repo, produced with **zero tokens, zero
network, and zero LLM calls**. Run it yourself:

```bash
bench/run.sh                       # dense if bootstrapped, else BM25-only
HIPPO_DISABLE=dense bench/run.sh # force the model-free path (no ~130MB download)
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

## Other measurement tools in `bench/`

`bench/` is a repository-checkout tool, not part of the plugin: the installed plugin is the
`plugin/` directory alone, and nothing under `plugin/` imports `bench/` (a test enforces it).
Run these from the repository root, with the engine on the path:

```bash
# grid-search the knee-ratio / dense-floor constants against a labeled hard set (report-only)
PYTHONPATH=plugin python -m bench.calibrate_thresholds --memory-dir DIR --hard-set PATH \
  [--abstention-set PATH] [--index-dir DIR]

# the /dream snapshot-diff A/B over the committed fixture (tests/fixtures/dream_ab), or over a
# copy of a live corpus with --live (measure-only)
PYTHONPATH=plugin python -m bench.dream_eval [--live [--memory-dir DIR]] [--rebuilds N] [--pin]

# HCR-8: the held-out high-vs-lean eval. Build the set (sample, then pool and gold after a run),
# run one corpus and one embedder per process, then write the counts-only evidence. The set,
# the per-question results and the judgments name memories: keep them outside the repository.
python -m bench.high_eval_set sample --corpus LABEL=DIR ... --n LABEL=N ... --out PRIVATE_DIR
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.high_eval --corpus LABEL \
  --memory-dir DIR --questions PRIVATE_DIR/questions.jsonl --mlx-cache MLX --fastembed-cache FE \
  [--model Qwen/Qwen3-Embedding-8B] [--phase2] --out PRIVATE_DIR/results-LABEL-4b.json
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.rerank_latency --questions ... \
  --memory-dirs LABEL=DIR ... --mlx-cache MLX --fastembed-cache FE --out PRIVATE_DIR/rerank.json
python -m bench.high_eval_report --results PRIVATE_DIR --gold PRIVATE_DIR/gold.json \
  --out planning/HCR-8.evidence.DATE.json

# HCR-9: lean's arms of the phase-2 pass alone (lean as shipped, with whole-memory rows, with
# the MMR re-cut off, and with both). Loads no MLX model, so it runs in minutes.
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.high_eval --phase2 --lean-only \
  --corpus LABEL --memory-dir DIR --questions PRIVATE_DIR/questions.jsonl --mlx-cache MLX \
  --fastembed-cache FE --out PRIVATE_DIR/results-LABEL-leanonly.json

# HCR-9: calibrate ONE embedding space (floor, gate, count rule, band) on the two public corpora
# and any live ones, with off-topic probes and replayed prompts; explicit and hook mode per cell.
# --space lean or a high space id; --rows says what an entry row embeds for this run. The corpus
# copies and their indexes persist under --work, so a second run only pays the model load.
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.space_calibration \
  --space Qwen/Qwen3-Embedding-4B --rows whole --work SCRATCH --live LABEL=DIR ... \
  [--heldout PRIVATE_DIR/questions.jsonl --gold PRIVATE_DIR/gold.json] \
  [--replay LABEL=PROJECT/.hippo/telemetry] --mlx-cache MLX --fastembed-cache FE \
  --floors 0.42,0.44 --gates table,off,0.56/0.56 --counts table,off --bands table \
  --out PRIVATE_DIR/calibration.json

# HCR-9: the MMR re-cut on near-duplicate clusters (re-captures of a question's target and of
# its strongest wrong competitor, added to a copy of each corpus under the same --work).
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.mmr_eval --space lean \
  --rows description --work SCRATCH --live LABEL=DIR ... --questions PRIVATE_DIR/questions.jsonl \
  --gold PRIVATE_DIR/gold.json --selection PRIVATE_DIR/selection.json --dups PRIVATE_DIR \
  --out PRIVATE_DIR/mmr.json

# HCR-7: the embedding daemon against the in-process embedder. Three commands, one MLX process
# at a time, over space_calibration's --work copies: the in-process vectors, the same through
# the daemon (bytes compared: queries alone, ten at once, whole index builds), then real MCP
# servers on high against one scratch project (one daemon from simultaneous starts, each
# process's footprint, a burst of prompts, the three daemon faults). `--rerank` on
# space_calibration reads a space with the warm reranker on (the 8B arm of HCR-7's evidence).
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.embed_daemon vectors-local \
  --work SCRATCH [--live LABEL] [--heldout PRIVATE_DIR/questions.jsonl] --out PRIVATE_DIR/local.npz
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.embed_daemon vectors-daemon \
  --work SCRATCH [--live LABEL] [--heldout ...] --local PRIVATE_DIR/local.npz --out PRIVATE_DIR/vectors.json
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.embed_daemon servers \
  --project SCRATCH_PROJECT --data SCRATCH_PLUGIN_DATA --queries HARD_SET.yaml \
  [--servers 10 --rounds 30] --out PRIVATE_DIR/servers.json

# HCR-7, the daemon's lifetime and what it holds (planning/HCR-7.lifetime.evidence.*.json):
# the first prompt after the daemon dropped its idle model and the way back to high, then its
# own leave once the server is gone; the daemon's and the building server's phys_footprint
# through a high/ build; and one daemon holding two models for sessions on different ones.
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.embed_daemon lifetime \
  --project SCRATCH_PROJECT --data SCRATCH_PLUGIN_DATA --queries HARD_SET.yaml \
  [--trials 20 --idle-s 8] --out PRIVATE_DIR/lifetime.json
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.embed_daemon build-memory \
  --project SCRATCH_PROJECT_WITHOUT_A_TWIN --data SCRATCH_PLUGIN_DATA \
  [--model Qwen/Qwen3-Embedding-8B] --out PRIVATE_DIR/build-memory.json
OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.embed_daemon two-models \
  --project-a SCRATCH_4B_PROJECT --project-b SCRATCH_8B_PROJECT --data SCRATCH_PLUGIN_DATA \
  --queries HARD_SET.yaml --out PRIVATE_DIR/two-models.json
```

Every bench loads its models in its own process (`bench/__init__.py` sets
`HIPPO_EMBED_DAEMON=0`), as every evidence file before HCR-7 was read; `bench.embed_daemon`
and a run with `HIPPO_EMBED_DAEMON=1` in the environment are the ones that go through the
shared daemon.

These used to run as `python -m memory.eval_recall --calibrate` and `--ab HIPPO_DREAM`; those
flags now print where the tool lives. The other `--ab` arms (`HIPPO_SALIENCE`,
`HIPPO_OUTCOME_PRIOR`) still ship with the plugin, because `hippo doctor` reads their reports.
