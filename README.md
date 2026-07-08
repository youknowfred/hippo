# hippo

Local, git-native agent memory for Claude Code — a markdown-in-git corpus with offline
dense+BM25 hybrid recall, git-drift staleness/provenance tracking, recall-triggered
reconsolidation, and a self-audit skill. Distributed as a Claude Code plugin.

Extracted from the ic-memobot/Memosa agent-memory tooling (a private origin repo), where
it has run in production since 2026-06 across a 180+ memory corpus.

## Quickstart

1. **Install** (inside Claude Code):

   ```
   /plugin marketplace add youknowfred/hippo
   /plugin install hippo@hippo
   ```

2. **Bootstrap — once per machine.** Builds the plugin's own venv and downloads the ~130MB
   embedding model (the ONE online step in the plugin's whole lifecycle):

   ```
   /hippo:bootstrap
   ```

3. **Init — once per project.** Seeds `.claude/memory/` from the starter packs (core by
   default — a `user_role.md` template and the memory-master policy; themed packs are
   opt-in), builds the recall index, and wires the always-loaded floor. Fill in
   `user_role.md` when it asks:

   ```
   /hippo:init
   ```

4. **Use it.** Say *"remember this: …"* (the `/hippo:new` skill writes a recall-ready
   memory), then just work — every prompt is matched against the corpus by the
   UserPromptSubmit hook and the relevant memories are injected automatically. Session
   starts surface staleness, recent captures, and link health. If anything seems off:
   `/hippo:doctor`.

Before bootstrap has run, recall works immediately in BM25-only mode: the plugin vendors a
dependency-free BM25 scorer and frontmatter parser (`plugin/memory/_vendor/`) precisely
for that pre-bootstrap window, so a bare `python3` with none of the pinned deps still
serves real lexical recall. Bootstrap unlocks the dense half — dense recall degrades
gracefully rather than blocking or erroring.

## Removal / Uninstall

To stop hippo from acting on a project, run inside Claude Code:

```
/hippo:remove
```

This removes the cross-machine symlink under `~/.claude/projects/<encoded>/memory` — the one
thing that actually stops Claude Code's native memory from injecting the floor for this project.
It then offers (a confirmed step, never automatic) to delete the derived, gitignored
`.claude/.memory-index/` and `.claude/.memory-telemetry/` dirs, and **reports** — never
deletes — the shared per-machine venv (`CLAUDE_PLUGIN_DATA/venv`) and fastembed model cache
paths, since those are shared across every project using the plugin on this machine.

`.claude/memory/` itself — the git-tracked corpus — is always left alone: it stays committed in
git, inert, until someone runs `/hippo:init` again (in this repo, a fresh clone, or a new
worktree).

## Support matrix

| Platform | Status |
|---|---|
| macOS | **Fully supported** — the primary development platform; CI runs the full suite on macOS |
| Linux | **Fully supported** — CI runs the full suite on Ubuntu. Without `CLAUDE_PLUGIN_DATA`, the fallback cache dir is XDG-aware: `${XDG_CACHE_HOME:-~/.cache}/hippo-memory` ([ROADMAP.yaml](ROADMAP.yaml), OSP-2) |
| Windows | **Out of scope** — a decision, not an omission ([ROADMAP.yaml](ROADMAP.yaml), decision OQ-2 + non_goals): the hooks are bash and the engine is untested there. Revisit only on concrete adoption evidence |

Python 3.10 and 3.12 are exercised in CI. Bootstrap runs once per machine; init runs once
per project.

## Repo layout

This repo is both a **plugin marketplace** and the **plugin itself**:

```
.claude-plugin/marketplace.json   # marketplace manifest (lists the `hippo` plugin)
plugin/
├── .claude-plugin/plugin.json    # plugin manifest
├── memory/                       # the engine (Python package, imported as `memory`)
│   └── _vendor/                  # pre-bootstrap fallbacks (BM25 + frontmatter parser)
├── hooks/                        # UserPromptSubmit recall + SessionStart dispatcher + PreCompact nudge + SessionEnd capture
├── assets/packs/                 # starter packs (core seeded by default; rest opt-in)
├── bin/hippo                     # CLI launcher for the stateless engine commands
├── requirements.txt              # fastembed, numpy, PyYAML, rank-bm25 (the venv path)
└── skills/                       # /hippo:bootstrap|init|new|recall|doctor|audit|remove
tests/                            # hermetic test suite (no network/model download by default)
.github/workflows/ci.yml          # hermetic matrix + dense lane + shellcheck
```

See [`plugin/memory/README.md`](plugin/memory/README.md) for the full engine documentation
(recall, staleness, reconsolidation, archive internals) and
[`plugin/README.md`](plugin/README.md) for the skills overview.

## Bootstrap vs. auto-provision (design decision)

The plugin's Python dependencies (fastembed, numpy, PyYAML, rank-bm25) and the ~130MB
fastembed model cache are **not** installed automatically on enable. Bootstrapping is
**explicit** via the `/hippo:bootstrap` skill, run once per machine, rather than
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

Until bootstrap runs, the SessionStart hook nudges the next step (once every few
sessions, permanently dismissable) instead of staying silent.

## License

MIT — see [LICENSE](LICENSE). A copy ships inside the plugin bundle
([plugin/LICENSE](plugin/LICENSE)) so installs carry the license text too. The engine code
extracted from the ic-memobot/Memosa agent-memory tooling was written by this repo's author
and is relicensed here under the same MIT terms; no third-party code was ported with it.
The pre-bootstrap fallbacks in `plugin/memory/_vendor/` (a BM25 scorer and a frontmatter
parser) are likewise original implementations, MIT like the rest — not copied vendor code.
