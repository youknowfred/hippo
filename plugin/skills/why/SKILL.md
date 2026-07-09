---
description: The recall receipt (glass-box) — explain WHY hippo surfaced a memory for a query (winning backend, typed edges, steering, salience) or why it did NOT (the best candidate's sub-floor near-miss score and the floor it missed, an untrusted corpus, or a no-shared-token BM25 miss). Triggers include "why did you recall that", "why was that injected", "why didn't you remember", "recall receipt", "/hippo:why". Read-only; runs the same ranking the hook uses.
---

# /hippo:why — the recall receipt

Two questions erode trust the most: "why did you surface that?" and "why did you NOT
surface the thing I know we wrote down?". The recall hook is invisible by design, so this
skill answers both deliberately — it re-runs the SAME ranking the hook would apply to the
query and prints a per-hit breakdown, or the honest abstention reason. Read-only: nothing
is written, logged, or reordered for future sessions.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

## Get the receipt

Use the user's own words as the query — the receipt explains what the hook would do for
*that* prompt, so paraphrasing it changes the answer:

```bash
"$PY" -m memory.recall_view --why "<the query, ideally the user's own phrasing>"
```

## Reading it

- **Hits** carry `[type · relevance N · won via <backend> · …]` tags: the backend(s) whose
  ranking produced the hit, `via 1-hop link` for a graph expansion, `pinned ×1.2` when a
  steer:pin lifted it, the typed-edge note (`superseded by X` / `contradicts X — verify`),
  and the salience components when that flag is on.
- **A `(rule)` pointer's** receipt names its query **containment** score and the rules
  floor — governance sections are matched by containment, not cosine.
- **Abstention** names the reason, honestly ranked: an UNTRUSTED corpus (recall withheld,
  nothing scored — trust it via /hippo:doctor), a BM25-only corpus where no memory shares
  a token, or the near-miss: "best candidate `X` scored 0.42, below the floor 0.60". A
  near-miss that SHOULD have answered the query is a capture/description problem — enrich
  it via /hippo:consolidate, or pin it (`steer: pin`).

## When NOT to use

- "What do you remember about X" (the answer, not the explanation) — `/hippo:recall`.
- "Is recall broken / empty for everything" (plumbing, not ranking) — `/hippo:doctor`.
