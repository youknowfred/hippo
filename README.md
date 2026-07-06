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

## Support matrix

| Platform | Status |
|---|---|
| macOS | **Fully supported** — the primary development platform; CI runs the full suite on macOS |
| Linux | **Fully supported** — CI runs the full suite on Ubuntu. One known wart: without `CLAUDE_PLUGIN_DATA`, the fallback cache dir is `~/Library/Caches` (macOS-shaped); the XDG-aware fix ships in v0.3.0 ([ROADMAP.yaml](ROADMAP.yaml), OSP-2) |
| Windows | **Out of scope** — a decision, not an omission ([ROADMAP.yaml](ROADMAP.yaml), decision OQ-2 + non_goals): the hooks are bash and the engine is untested there. Revisit only on concrete adoption evidence |

Python 3.10 and 3.12 are exercised in CI. Bootstrap runs once per machine; init runs once
per project.

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

Until `/hippo:bootstrap` has run, recall works immediately in BM25-only mode: the plugin
vendors a dependency-free BM25 scorer and a frontmatter parser
(`plugin/memory/_vendor/`) precisely for that pre-bootstrap window, so a bare `python3`
with none of the pinned deps still serves real lexical recall. Bootstrap unlocks the
dense half — dense recall degrades gracefully rather than blocking or erroring.

## License

MIT — see [LICENSE](LICENSE). A copy ships inside the plugin bundle
([plugin/LICENSE](plugin/LICENSE)) so installs carry the license text too. The engine code
extracted from the ic-memobot/Memosa agent-memory tooling was written by this repo's author
and is relicensed here under the same MIT terms; no third-party code was ported with it.
The pre-bootstrap fallbacks in `plugin/memory/_vendor/` (a BM25 scorer and a frontmatter
parser) are likewise original implementations, MIT like the rest — not copied vendor code.
