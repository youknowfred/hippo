---
description: Run this once per machine (not per project) to build the shared plugin venv and warm the offline embedding model cache. Use when a user says "bootstrap memory", "set up hippo", "/hippo:bootstrap", or when /hippo:doctor reports the venv/model cache is missing. Idempotent — safe to re-run; it no-ops via a sentinel file if already bootstrapped. Pass `--multilingual` to switch to a multilingual embedding model for non-English corpora.
---

# /hippo:bootstrap — one-time-per-machine venv + model warm

This is the **one online step** in this plugin's entire lifecycle. Every other operation
(recall, staleness, reconsolidation, archive) is offline-only by hard contract. Bootstrap
exists precisely so those hooks never have to be.

## Preflight (shared across all hippo skills)

Every code block below expands the plugin data dir variable — unset, `uv venv "/venv"`
would provision into a root-owned path. Run this guard FIRST and stop if it fails:

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
```

## What this does

1. **Idempotency check first.** Read `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel` (a small
   JSON file: `{"requirements_hash": "<sha256 of plugin/requirements.txt>", "bootstrapped_at": "..."}`).
   If it exists AND its `requirements_hash` matches the CURRENT `${CLAUDE_PLUGIN_ROOT}/requirements.txt`
   hash, report "already bootstrapped, nothing to do" and STOP. A stale hash (plugin updated,
   deps changed) means re-provision, not skip.
2. **Build the venv.** First check whether the system `python3` is inside the supported
   window — **3.9 through 3.13** — this plugin's pinned deps (`numpy>=1.26,<3`, matched to
   `fastembed`'s numpy-2 support) target:
   ```bash
   PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
   PYOK=1
   case "$PYVER" in
     3.9|3.10|3.11|3.12|3.13) PYOK=1 ;;
     "") PYOK=0 ;;
     *) PYOK=0 ;;
   esac
   ```
   - If `PYOK=1` (or version detection failed but `python3` exists — best effort, don't block
     on a detection quirk): `uv venv "${CLAUDE_PLUGIN_DATA}/venv"` if `uv` is on PATH, else
     `python3 -m venv "${CLAUDE_PLUGIN_DATA}/venv"` as a fallback.
   - If `PYOK=0` (system `python3` is outside 3.9–3.13, e.g. a brand-new 3.14+ default on a
     fresh machine) **and `uv` is on PATH**: prefer a pinned interpreter instead of the
     unsupported system one — `uv venv --python 3.12 "${CLAUDE_PLUGIN_DATA}/venv"` (uv fetches
     3.12 itself if it isn't already installed).
   - If `PYOK=0` **and `uv` is NOT on PATH**: don't attempt `python3 -m venv` — it would fail
     deep inside a numpy source build with an opaque traceback. Fail loudly and actionably
     instead: `echo "✘ system python3 is $PYVER, outside hippo's supported window (3.9–3.13),
     and uv is not on PATH to fetch a supported interpreter. Install uv
     (https://docs.astral.sh/uv/) and re-run bootstrap, or install a 3.9–3.13 python3 and
     re-run."; exit 1`
   Then install:
   ```bash
   uv pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt" --python "${CLAUDE_PLUGIN_DATA}/venv/bin/python"
   # fallback if uv is absent:
   "${CLAUDE_PLUGIN_DATA}/venv/bin/pip" install -q -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
   ```
3. **Warm the model cache OFFLINE-SAFE.** This is the actual online step — it downloads the
   ~130MB `bge-small-en-v1.5` ONNX model via `fastembed` the FIRST time only. Run:
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "from memory.build_index import ensure_fastembed_cache_path; ensure_fastembed_cache_path(); from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
   ```
   `ensure_fastembed_cache_path()` pins the cache under `${CLAUDE_PLUGIN_DATA}/fastembed` —
   never `$TMPDIR`. The lesson behind that pin: unset, fastembed caches under
   `$TMPDIR/fastembed_cache`, which macOS purges on a schedule — and the hooks are offline by
   hard contract, so they can never re-download a wiped model. Recall would silently degrade
   to BM25 until someone re-ran bootstrap. This step MUST land the model somewhere durable.

   Also warm the RCL-5 cross-encoder (a small ~80MB model, `/hippo:recall` and the MCP recall
   tool's offline rerank — never the hot path). Best-effort: a failure here must NOT fail
   bootstrap (the rerank already degrades to the un-reranked order on any cache miss):
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "from memory.build_index import ensure_fastembed_cache_path; ensure_fastembed_cache_path(); from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')" \
     || true
   ```
4. **Write the sentinel** on success: `{"requirements_hash": "<hash>", "bootstrapped_at": "<ISO
   timestamp>", "plugin_version": "<version field from ${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json>"}`
   to `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel`. This is what step 1 checks — without it, every
   session would re-attempt a multi-second venv build. `plugin_version` records WHICH plugin
   version this venv was provisioned for, so `/hippo:doctor` can flag an installed-vs-bootstrapped
   version delta after an update (DOC-7); an older sentinel that predates this field simply reads
   as "unknown" and prompts a re-bootstrap to record it.
5. **Report** what happened: fresh bootstrap vs. re-provision (dep change detected) vs. already
   current. If `uv` was unavailable and the `venv` fallback was used, say so (slower but works).
   If the system `python3` was outside the supported window and `uv --python 3.12` was used
   instead, say that too — the venv's interpreter deliberately differs from `python3` on PATH.

## `--multilingual` — opt-in multilingual embedding preset (RET-3 / OQ-4)

The default dense model (`BAAI/bge-small-en-v1.5`) is English-only — trained and evaluated on
English text. This plugin's Unicode tokenization (BM25 side) works correctly for ANY language
unconditionally, but the DENSE half only understands what its model was trained on. If your
corpus is mostly written in a non-English language (Japanese, Russian, etc. — `/hippo:doctor`
will flag this for you if it notices), switch to a multilingual model instead:

1. Run the SAME venv-build steps above first (`--multilingual` doesn't skip provisioning), then
   **write the model preset** so the choice persists across sessions without an env var:
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "import json, os; os.makedirs(os.environ['CLAUDE_PLUGIN_DATA'], exist_ok=True); \
      json.dump({'embed_model': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'}, \
      open(os.path.join(os.environ['CLAUDE_PLUGIN_DATA'], 'model.json'), 'w'))"
   ```
   `resolve_embed_model()` (in `memory/build_index.py`) reads this file — `HIPPO_EMBED_MODEL`
   still overrides it if set, otherwise every subsequent build/recall picks up the multilingual
   model automatically. This is the SAME preset file `/hippo:doctor`'s non-English-corpus check
   points users at.
2. **Warm THAT model** (mirrors step 3 above, but for the multilingual id — a separate ~220MB
   ONNX download the first time):
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "from memory.build_index import ensure_fastembed_cache_path; ensure_fastembed_cache_path(); from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
   ```
3. **Rebuild the index — this is a FULL re-embed, not incremental.** The index manifest records
   which model embedded it (`manifest["model"]`); `build_index`'s cache-reuse check only trusts
   a prior row when `old_manifest["model"] == DEFAULT_MODEL`, so switching models makes EVERY
   existing row a cache miss — every memory gets re-embedded from scratch, once, under the new
   model. Expect this to take noticeably longer than an incremental rebuild on a large corpus:
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -m memory.build_index \
     --memory-dir <memory_dir> --index-dir <index_dir> --force
   ```

**When to use this**: your corpus (memory descriptions) is visibly written in a non-English
language, OR `/hippo:doctor` warns "corpus is N% non-Latin-alphabetic but is served by the
English default embedding model." **When NOT to**: an English (or mostly-English) corpus —
the multilingual model trades some English-specific accuracy for broad language coverage, so
switching without a real multilingual corpus is a pure downgrade. Switching back to English
later is the same procedure in reverse (rewrite `model.json` to the English id, or delete it
to fall back to the default, then `--force` rebuild again).

## Hard rules (do not violate)

- **Never run this from a hook.** Model warm is an online step; the `SessionStart` and
  `UserPromptSubmit` hooks are offline-only by contract (never download, always exit 0). This
  skill is the ONLY place network access for the model cache is allowed.
- **Never skip the hash check.** A dep bump without a re-provision leaves a venv missing a
  newly-added package, and every recall silently degrades to whatever the OLD deps support
  (e.g. dense recall breaking silently if a `fastembed` major bump changes its cache format).
- **Never write the sentinel before both the venv AND the model warm actually succeed.** A
  partial bootstrap that gets marked complete means the next real session trusts a broken
  install and gets no retry.
- If `uv` and `python3` are BOTH unavailable, fail loudly with a clear message — don't silently
  produce a broken venv path that later hooks will treat as "bootstrapped."
- If system `python3` is outside the supported window (3.9–3.13) and `uv` is ALSO unavailable,
  fail loudly with an actionable message (install `uv`, or install a supported `python3`) —
  don't let `python3 -m venv` limp forward into an opaque numpy source-build traceback.

## After bootstrap

Recall works in full hybrid (dense+BM25) mode from the next session onward. Before bootstrap,
recall already works in BM25-only mode — the plugin vendors a dependency-free BM25 scorer and
frontmatter parser (`memory/_vendor/`) so a bare `python3` with none of the pinned deps still
serves real lexical recall. Bootstrap unlocks the dense half (and the full pinned deps).
