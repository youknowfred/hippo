# Past bge-small: a high-ceiling retrieval profile

**Type:** research + design exploration (not an implementation plan).
**Drafted:** 2026-11-25 · **Status:** RATIFIED as recommended (Fred, in-session, 2026-11-25) —
all four owner decisions in §4 resolved per their recommendations.
**Question:** Fred runs hippo on a 128 GB-unified-memory Mac and wants retrieval that uses
that ceiling. What is the best candidate upgrade, keeping today's lean path available for
less capable machines?

---

## TL;DR — recommendation

Add an opt-in **`high` retrieval profile** — same pipeline, bigger brains — rather than a
new retrieval system:

1. **Dense embedder: `Qwen/Qwen3-Embedding-4B`** (8B as a knob) replacing
   `bge-small-en-v1.5` in the high profile. Apache-2.0, instruction-aware, 32K context,
   MRL-truncatable dims. As of 2026-11-23 the 8B sits #1 on the RTEB retrieval
   leaderboard and ~75 on MTEB(eng v2) vs bge-small's low-60s class — this is the single
   biggest quality lever available, and it is a *config* change to a pipeline that already
   keys floor/manifest/cache by model id. (Freshness check, 2026-11-25: no Qwen4-Embedding
   or Qwen3.5-Embedding exists — Qwen4 is so far an API-only chat flagship, Qwen4-Max
   2026-11-16, no open weights. When a successor embedding family lands, it absorbs
   through the same model-id seam.)
2. **Reranker: `Qwen3-Reranker-0.6B` always-on in warm mode** (the `HIPPO_RERANK` lane,
   currently a 2022-era ms-marco MiniLM, off by default), with the 4B reranker on the
   explicit surfaces (`/hippo:recall`, MCP tool) that have no p95 budget.
3. **Serving: in-process via MLX** (Apple-Silicon path), loaded once by the HOT-6 warm
   server. The spawn path never loads it — it degrades to the lean profile, which is the
   existing fallback ladder doing its job.
4. **Graph lane (all profiles, zero model cost): replace GRA-1's 1-hop expansion with
   personalized PageRank** over the persisted `links.json`, HippoRAG-2 style — fused top
   hits as the personalization vector, power iteration over a ≤2k-node graph is
   sub-millisecond numpy. The literature (arXiv 2502.14802) credits exactly this move with
   the associative-memory gains dense retrieval can't reach; hippo already owns the graph.

Keep **lean** (bge-small + RRF, today's default) and the **BM25-only floor** unchanged.
Profile chosen at `hippo setup` (auto-offer on Apple Silicon with RAM ≥ 64 GB), recorded
next to the existing `embed_model` bootstrap config.

**TrustGraph stays rejected.** The 2026-06-23 verdict (ic-memobot,
`docs/plans/active/trustgraph-for-agent-memory-exploration-2026-06-23.md`) rejected the
*platform* — Pulsar + Cassandra + object store + graph DB + vector DB + Grafana/Loki, no
lightweight mode — as trading a drift problem for an ops problem, and adopted the *ideas*
(Option B). 128 GB of RAM buys back none of the ops burden, and hippo has since built the
valuable concepts natively: hybrid dense recall, provenance, code-tied staleness, typed
edges, graph expansion, dream's latent-edge discovery. The gap TrustGraph would fill
today is model quality — and that needs a better model, not a platform.

---

## 1. Where recall stands today (so we upgrade the right part)

The hot path (`recall.py` + `recall_rank.py` + `recall_graph.py`) is already a real
hybrid retrieval system:

- **Lexical:** BM25 postings fast path (PRF-1) + body-chunk backstops (RET-2).
- **Dense:** `bge-small-en-v1.5` (384-dim, ONNX int8 via fastembed 0.7.4), calibrated
  per-model cosine floor (RET-1) + soft band (HOT-10), knee cutoff.
- **Fusion:** RRF (k=60) → invalidation/supersede penalties → GRA-1 1-hop expansion
  (top-3 seeds, 0.5 discount) → MMR diversity (RCL-4).
- **Rerank:** cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` — unconditional on explicit
  surfaces, opt-in (`HIPPO_RERANK=1`) and 5s-bounded on the hot path.
- **Budgets:** spawn-path gate `GATE_P95_MS = 300`; warm server (HOT-6)
  `SERVE_BUDGET_MS = 1500` / `COLD_BUDGET_MS = 4000` with a breaker.
- **Fallback ladder:** dense degrades to BM25-only on a cold cache — the lean-Mac story
  the owner wants kept is already load-bearing architecture, not a feature to add.

The weak links are the two **models** (a 2023 110-token-budget 384-dim embedder; a 2022
6-layer rerank head) — not the pipeline. Both are keyed by model id throughout
(`resolve_embed_model`, `_DENSE_FLOOR_BY_MODEL`, manifest/matrix binding, cache pre-check),
so swapping them is the designed-for extension point.

## 2. The candidate field

| Candidate | Quality ceiling | Hot-path fit | Cost / friction | Verdict |
|---|---|---|---|---|
| **A. fastembed in-family bump** (`jina-embeddings-v3` + `jina-reranker-v2` + `answerai-colbert-small`) | Moderate (2024-class, ~65 MTEB) | Good (ONNX, same dep) | Near zero — but jina-v3 weights are CC-BY-NC, wrong license to ship as a default | Fallback option only |
| **B. Qwen3-Embedding/Reranker via MLX** | **Best open, Nov 2026** (8B #1 RTEB; 4B within ~1pt) | Query-embed warm fits easily; 0.6B rerank should fit 1500 ms (bench to confirm) | One optional dep (mlx runner) in a `high`-profile venv; Apple-Silicon only; ~4–9 GB disk | **Recommended** |
| **C. Same models via llama.cpp GGUF** (`llama-cpp-python` in-process, `--pooling rank` for rerank) | Same as B | Same as B | Cross-platform Metal/CUDA; wheel-build friction; community reports of broken early reranker GGUFs (use ggml-org's) | The portable variant of B — owner's call |
| **D. External server (Ollama / LM Studio / llama-server)** | Same as B | Fine | A server to babysit — against the no-server posture that made hippo hippo | Reject as default; possible later as a provider knob |
| **E. Full TrustGraph install** | Adds graph-RAG + ontology extraction | N/A (REST round-trips) | The 06-23 verdict's ops stack, unchanged; authority moves off markdown-in-git | **Stays rejected** |
| **F. PPR graph lane** (HippoRAG-2-style, code only) | Associative/multi-hop gains orthogonal to A–C | Sub-ms | Zero deps, ~a screen of numpy | **Do regardless**, all profiles |

Why B over C as the lead: hippo's field machines are Macs, MLX is the lowest-friction
high-performance path there, and the lean/floor profiles already cover every non-Mac or
low-RAM environment. If cross-platform `high` ever matters, C slots into the same seam.

## 3. What the high profile touches (sized, not planned)

- **Profile plumbing:** a `retrieval_profile` in bootstrap config beside `embed_model`;
  `resolve_embed_model` already honors overrides. Auto-offer at setup on arm64-darwin with
  ≥ 64 GB (`sysctl hw.memsize`).
- **Floor recalibration:** `_DENSE_FLOOR_BY_MODEL` entries for the new embedder via the
  existing RET-1 method (golden-corpus on-topic vs off-topic separation). Qwen3 is
  instruction-aware — the query side gets a task instruction, which typically *widens*
  the separation; must calibrate, not guess.
- **Dims & index:** MRL lets us truncate 4B's native dims to 1024–2048; even full dims at
  2k memories is a ~16–32 MB matrix. IDX-1's bound-matrix scheme carries the model id in
  the manifest already; profiles can't tear each other.
- **Warm posture:** high profile is warm-mode-first; the spawn hook serves lean. That rule
  already exists as the HOT-6 claim protocol — high just makes the warm/spawn split also a
  big-model/small-model split.
- **Rerank seam:** `_cross_encoder_rerank` is one shared function; it needs a per-profile
  model id and an MLX code path beside the fastembed one.
- **PPR lane:** replace the 1-hop discount pull in `recall_graph.py` with power iteration;
  A/B it against GRA-1 with the existing GRF-4 reachability audit as the instrument.

**All latency figures below 300/1500 ms quoted above are estimates to be benched, not
observations** — the decision gate is the existing eval: `ab_runner.run_flag_arms` over
the live corpus, QUA-6 golden-set recall deltas, `GATE_P95_MS`/warm-budget telemetry.
No default flips without the A/B evidence file.

## 4. Owner decisions — ALL RESOLVED per recommendation (Fred, 2026-11-25, "okay i approve as recommended")

- **Q1 — embedder size:** RESOLVED — 4B default with 8B knob. (8B ≈ 2× disk/RAM/latency
  for ~1 MTEB point.)
- **Q2 — runtime:** RESOLVED — MLX-only high profile; llama.cpp GGUF stays the named
  seam if cross-platform `high` ever matters.
- **Q3 — hot-path rerank:** RESOLVED — `HIPPO_RERANK` defaults ON (0.6B) inside the high
  profile's warm mode; explicit surfaces get the 4B.
- **Q4 — PPR:** RESOLVED — replaces 1-hop expansion, gated on the A/B evidence file
  (GRF-4 reachability audit as instrument).

## 5. Aside — a local generative tier (out of scope here, noted for a later exploration)

Asked during ratification: Qwen4 and Qwen3.8-Flash. Neither changes the retrieval pick
(no Qwen4 retrieval family exists), but **Qwen3.8-Flash-Next** (open weights 2026-08-26;
125B-A6B MoE + 51B n-gram table; Qwen4-architecture preview; GGUF/llama.cpp supported) is
a credible candidate for the *generative* verbs that have no p95 budget — dream's
latent-edge discovery, capture triage, contradiction adjudication — as a local
`llm_client` backend on ≥128 GB machines. Decoder MoE ≠ retrieval encoder: it has no
place in the dense or rerank lanes (wrong training objective; an order of magnitude over
the warm budget). License field on the model card unverified at drafting — check before
building on it. Separate exploration if pursued.
