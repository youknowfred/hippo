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

## Acting on candidates (today: by hand, per item)

Until the owner flips Tier-A auto-apply on (a dated decision, post-calibration), treat the
report as a worklist for the existing per-item write paths:

- a **completion / bridge** you agree with → add the `[[wikilink]]` to the source memory's
  body yourself (one edit, reviewable in git);
- a **refines** you agree with → `"$PY" -c "from memory.links import add_typed_relation; ..."`
  or edit the frontmatter directly — additive, body-preserving;
- anything that smells like a *supersede/contradiction* is out of dream's lane — route it
  through `/hippo:resolve`.

Never bulk-apply the whole ledger — per-item judgment is the point of the gate.
