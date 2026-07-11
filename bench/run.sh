#!/usr/bin/env bash
# POS-3 — reproduce hippo's recall benchmark on the shipped 50-memory golden dev corpus.
#
# One command, no arguments. Builds a throwaway index over tests/golden_corpus/memory and runs
# the eval over its 18 hand-written cross-vocabulary PARAPHRASE queries (hard_set.yaml) — queries
# deliberately worded UNLIKE the memory they should find — printing recall@10, MRR@10, and the
# cold/warm latency. The number is produced entirely by local lexical + (optional) dense ranking:
# zero tokens, zero network, zero LLM calls.
#
# By default this uses the dense model if it has been bootstrapped, and degrades to BM25-only
# otherwise (both land recall@10 = 1.0 on this corpus). Force the model-free path — reproducible
# with no ~130MB model download — with:  HIPPO_DISABLE_DENSE=1 bench/run.sh
#
# Prerequisites: the plugin's Python deps must be importable (numpy, PyYAML, rank-bm25 — plus
# fastembed for the dense path), normally installed by /hippo:bootstrap or `pip install -r
# plugin/requirements.txt`. `HIPPO_DISABLE_DENSE=1` skips only the fastembed MODEL download, not
# the deps: the eval's fixture loader needs PyYAML, so a truly bare interpreter reports 0 queries
# rather than the real number. (The vendored BM25 scorer covers the recall HOT PATH pre-bootstrap;
# the eval harness here is not on that path.)
set -euo pipefail

cd -- "$(dirname -- "$0")/.."

idx="$(mktemp -d)/index"
trap 'rm -rf "$(dirname "$idx")"' EXIT

PYTHONPATH=plugin python3 -m memory.build_index \
  --memory-dir tests/golden_corpus/memory --index-dir "$idx" >/dev/null

# recall@10 + MRR@10 are the benchmark; --gate-cold adds the honest cold p95 (per-process model
# load) the hook actually pays. The RESULT gate line reflects hippo's STRICT internal CI gates,
# which include a warm-latency gate that varies by machine/backend — read the recall@10 / MRR@10
# / cold_p95_ms numbers, not just the pass/fail verdict.
PYTHONPATH=plugin python3 -m memory.eval_recall \
  --memory-dir tests/golden_corpus/memory --index-dir "$idx" \
  --hard-set tests/golden_corpus/hard_set.yaml \
  --gate-cold
