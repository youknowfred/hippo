---
description: Run this once per Mac (per machine, not per project) to build the shared plugin venv and warm the offline embedding model cache. Use when a user says "bootstrap memory", "set up hippo", "/hippo:bootstrap", or when /hippo:doctor reports the venv/model cache is missing. Idempotent — safe to re-run; it no-ops via a sentinel file if already bootstrapped.
---

# /hippo:bootstrap — one-time-per-Mac venv + model warm

This is the **one online step** in this plugin's entire lifecycle. Every other operation
(recall, staleness, reconsolidation, archive) is offline-only by hard contract. Bootstrap
exists precisely so those hooks never have to be.

## What this does

1. **Idempotency check first.** Read `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel` (a small
   JSON file: `{"requirements_hash": "<sha256 of plugin/requirements.txt>", "bootstrapped_at": "..."}`).
   If it exists AND its `requirements_hash` matches the CURRENT `${CLAUDE_PLUGIN_ROOT}/requirements.txt`
   hash, report "already bootstrapped, nothing to do" and STOP. A stale hash (plugin updated,
   deps changed) means re-provision, not skip.
2. **Build the venv.** `uv venv "${CLAUDE_PLUGIN_DATA}/venv"` if `uv` is on PATH, else
   `python3 -m venv "${CLAUDE_PLUGIN_DATA}/venv"` as a fallback. Then install:
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
   `ensure_fastembed_cache_path()` pins the cache under `${CLAUDE_PLUGIN_DATA}/fastembed` (never
   `$TMPDIR` — see [[hippo_plugin_schema_gotchas]] sibling lesson: a hook can never re-warm a
   wiped `/var/folders` cache, so this step MUST land the model somewhere durable).
4. **Write the sentinel** on success: `{"requirements_hash": "<hash>", "bootstrapped_at": "<ISO
   timestamp>"}` to `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel`. This is what step 1 checks —
   without it, every session would re-attempt a multi-second venv build.
5. **Report** what happened: fresh bootstrap vs. re-provision (dep change detected) vs. already
   current. If `uv` was unavailable and the `venv` fallback was used, say so (slower but works).

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

## After bootstrap

Recall works in full hybrid (dense+BM25) mode from the next session onward. Before bootstrap,
recall already works in BM25-only mode (`rank-bm25` is a normal pinned dependency, not gated
on this skill) — bootstrap only unlocks the dense half.
