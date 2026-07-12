---
description: The generative sleep pass — replay the memory corpus against itself offline and surface the latent graph edges consolidate can't reach (transitive bridges, body-names-target-but-unlinked, undeclared refines), each with co-fire strength and provenance. Report-only by default (zero memory writes). Triggers include "dream", "run a dream pass", "find latent links", "what edges am I missing", "/hippo:dream". Discovery, not housekeeping — that's consolidate/audit.
---

# /hippo:dream — the generative sleep pass

hippo's other verbs are the housekeeping functions of sleep: `consolidate` drains episodic
captures into memory, reconsolidation repairs drifted memories, staleness decays them. This
is the **generative** one: an offline replay pass that re-runs recall over each memory's own
derived self-query, watches what **co-fires**, and diffs that against the link graph to
surface the latent edges the corpus is structurally missing —

- **completion** — a body already *names* another memory (plain prose, or a dangling
  `[[wikilink]]` that nearly resolves) but no edge exists. Highest precision.
- **bridge** — a transitive A–B–C pair (A–B and B–C linked, A–C absent) that co-fires:
  exactly the 2-hop miss the recall hook's 1-hop graph expansion turns into a hit once the
  edge exists.
- **refines** — an undeclared typed relation (a child memory whose slug extends a parent's,
  co-firing with it).

Every pass is **offline** (a deliberate turn, like consolidate — never the per-prompt hot
path) and gated on the soak bar (≥5 distinct sessions): a young corpus proposes nothing,
and says so. **The empty pass is the norm** — a non-empty report is signal worth reading.

**Report-only is the shipped default: a pass writes ZERO memory files.** Candidates land in
a jsonl ledger under the derived telemetry dir (gitignored) plus a printed report with the
co-fire-strength distribution and a θ sweep — the calibration surface for the (owner-gated)
Tier-A auto-apply flip. Floor memories are never an edge endpoint; `confidence: draft`
memories are quarantined from both ends; un-aged dream edges are firewalled out of the
pass's own source set (a dream never cites an unreviewed dream).

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

## Run a pass (report-only)

```
"$PY" -m memory.dream --dry-run
```

Read the report to the user roughly as printed — the status line (or the explicit
below-soak / empty-corpus reason), the candidate list, and where the ledger landed. Do not
editorialize candidates into facts: each is a *proposed* edge with its provenance (kind,
co-fire strength, graph distance, firing query), not a claim.

Machine-readable form (for scripting / inspection):

```
"$PY" -m memory.dream --json
```

Useful knobs (env or flags): `--probe-k <n>` co-fire probe depth (default 10),
`--max-seeds <n>` cap the replay worklist (default all), `DREAM_COFIRE_THETA` /
`DREAM_MAX_APPLY_PER_PASS` — the auto-apply calibration knobs the report's θ sweep feeds
(they gate nothing in report-only mode).

## Applying edges (Tier-A auto-apply — explicit, reversible, capped)

The DRM-2 loop exists behind an explicit ask (the *default* stays report-only until the
owner's dated flip): on the user's request to apply, run

```
"$PY" -m memory.dream --apply
```

It auto-applies ONLY the Tier-A class — additive, body-prose-preserving, ranking-only:
bridges/completions as stamped `[[wikilinks]]` inside a machine-managed
`<!-- dream:links -->` block, refines as additive frontmatter — capped single-digit per
pass (`DREAM_MAX_APPLY_PER_PASS`, default 5, hard max 9), θ/mutuality-gated, secret-linted
with a **hard block** (the one ratified deviation from hippo's warn-only lint), each edge
stamped `pass=`/`edge=` and recorded in the committed append-only
`.claude/memory/dream-ledger.jsonl`. Edges are live in recall immediately (index rebuilt)
but **never committed** — git history stays the owner's. Present the digest verbatim,
including the undo handles. Supersedes candidates are digest-gated (never auto);
contradicts route to `/hippo:resolve`.

Undo is one command, byte-exact, drift-refusing; applied edges age into /dream's own
source set only after 5 un-undone sessions (`DREAM_AGE_SESSIONS`):

```
"$PY" -m memory.dream --undo              # revert the latest pass
"$PY" -m memory.dream --undo <edge-id>    # revert exactly one edge
"$PY" -m memory.dream --undo-since <N|date>
"$PY" -m memory.dream --log               # every edge: active / aged-in / undone
```

Prefer per-item hand-application when the user wants to review each edge: a
**completion/bridge** is one `[[wikilink]]` added to the source body; a **refines** is
additive frontmatter. Never bulk-apply the whole ledger by hand — the cap is the point.
