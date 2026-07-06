# hippo

Local, git-native agent memory for Claude Code — a markdown-in-git corpus with offline
dense+BM25 hybrid recall, git-drift staleness/provenance tracking, recall-triggered
reconsolidation, and a self-audit skill. Distributed as a Claude Code plugin.

Extracted from the [ic-memobot/Memosa](https://github.com) agent-memory tooling
(`scripts/memory/` + `.claude/hooks/`), where it has run in production since 2026-06
across a 180+ memory corpus.

## Repo layout

This repo is both a **plugin marketplace** and the **plugin itself**:

```
.claude-plugin/marketplace.json   # marketplace manifest (lists the `hippo` plugin)
plugin/
├── .claude-plugin/plugin.json    # plugin manifest
├── memory/                       # the engine (Python package, imported as `memory`)
├── hooks/                        # UserPromptSubmit recall + SessionStart dispatcher
├── requirements.txt              # fastembed, numpy, PyYAML, rank-bm25
└── skills/                       # /hippo:bootstrap|init|new|doctor|audit (Tier 2)
tests/                            # hermetic test suite (14 files, no network/model download)
```

## Install

```
/plugin marketplace add youknowfred/hippo
/plugin install hippo@hippo
```

See `plugin/memory/README.md` for full usage docs (recall, staleness, reconsolidation,
archive) and the bootstrap/init skills once Tier 2 ships.

## Bootstrap vs. auto-provision (design decision)

The plugin's Python dependencies (fastembed, numpy, PyYAML, rank-bm25) and the ~130MB
fastembed model cache are **not** installed automatically on enable. Bootstrapping is
**explicit** via a `/hippo:bootstrap` skill (Tier 2), run once per machine, rather than
an implicit SessionStart auto-provision. Reasoning:

- The one-time venv build + model warm is an **online** step (the only one in this
  plugin's whole lifecycle) and can take tens of seconds — doing it silently inside a
  `SessionStart` hook risks tripping the hook timeout on a slow connection, whereas an
  explicit skill invocation can show real progress and isn't budget-constrained the way
  a hook is.
- Auto-provisioning on first `SessionStart` would mean the *first* recall a user ever
  sees is unpredictably slow (or silently degrades to BM25 if the hook times out
  mid-provision) with no clear signal why — a confusing first impression for a tool
  whose whole value proposition is instant, silent recall.
  Explicit bootstrap means degraded-to-BM25 only ever happens because bootstrap
  genuinely hasn't run yet, not because it raced a hook timeout.
- It keeps the hard hook contract (`exit 0`, never downloads, never blocks) simple and
  auditable: hooks only ever *read* an already-warmed cache; they never *provision* one.

Until `/hippo:bootstrap` has run, recall works immediately in BM25-only mode (rank-bm25
is a normal pinned dependency, not gated on bootstrap) — dense recall degrades
gracefully rather than blocking or erroring.
