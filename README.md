# hippo

Give Claude Code a memory that lives in your repo: a corpus of small markdown files it recalls
the right pieces of, on demand, every session. **New here? Start with
[How hippo thinks](CONCEPTS.md)** — the five-minute mental model (what a memory is, the
always-on floor vs. on-demand recall, the four types, why markdown-in-git).

Local, git-native agent memory for Claude Code — a markdown-in-git corpus with offline
dense+BM25 hybrid recall, git-drift staleness/provenance tracking, recall-triggered
reconsolidation, and a self-audit skill. Distributed as a Claude Code plugin.

Battle-tested in daily use since 2026-06 across a 180+ memory production corpus.

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
   starts surface staleness, recent captures, and link health. If anything seems off, run
   `/hippo:doctor` (or see [Troubleshooting](#troubleshooting)).

5. **See it work.** Ask Claude *"what do you remember about my role?"* (or run
   `/hippo:recall "my role"` directly). hippo matches your prompt against the corpus and
   surfaces the relevant memory inline — that returned memory is the whole point: the right
   context on demand, built with zero tokens and never leaving your machine. (Fill in
   `user_role.md` first, from step 3, so there's something real to recall.)

Before bootstrap has run, recall works immediately in BM25-only mode: the plugin vendors a
dependency-free BM25 scorer and frontmatter parser (`plugin/memory/_vendor/`) precisely
for that pre-bootstrap window, so a bare `python3` with none of the pinned deps still
serves real lexical recall. Bootstrap unlocks the dense half — dense recall degrades
gracefully rather than blocking or erroring.

## Automatic capture — memory that writes itself, gated by your review

hippo remembers more than what you explicitly save. When a session ends, a background hook
quietly drafts candidate memories from what actually happened — the queries you ran, the files
that changed, the decisions you confirmed — and parks them, unwritten, in a gitignored pending
queue. **Nothing enters your corpus automatically.**

The next session's SessionStart nudge tells you when that queue is worth draining. You run:

```
/hippo:consolidate
```

and hippo walks you through each captured draft one at a time — showing its evidence (which
files changed, the session's queries, any near-duplicate already in the corpus) and its
rationale — then writes only the ones you approve, as an ordinary reviewable markdown diff.
This is the part native memory doesn't have: **capture is automatic, but every write waits for
a human.** You get the recall benefit of always-on capture without ever ceding control of what
your corpus says.

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

## hippo and Claude Code's native memory

hippo **composes** with Claude Code's built-in memory — it does not replace or fork it.

- **What native memory does.** Claude Code always-loads a per-project memory location
  (`~/.claude/projects/<encoded>/memory`). It's per-machine, opaque, and unconditionally
  injected — great for a small always-on note, but not reviewable in git, not ranked, and not
  shared with teammates.
- **What hippo adds.** A **git-native, teammate-reviewable** markdown corpus with **hybrid
  dense+BM25 recall** (the right memories on demand, not everything every prompt),
  **staleness/provenance** tracking, a typed **link graph**, **reconsolidation**, and an
  **automatic capture** path up to an explicit approval gate.
- **How they compose.** `/hippo:init` points the native memory location at this repo's
  `.claude/memory/` via a symlink, so hippo's always-load **floor** (the `user`/`feedback`
  pointers) reaches context *through* native memory's own always-load — hippo adds no second
  always-load channel. Everything else is served on demand by the recall hook and the MCP tools.

That symlink is the **only** native behavior hippo depends on. The full contract — every
assumption, how it can drift, and how `/hippo:doctor` detects a break — is documented in
[`plugin/memory/NATIVE_MEMORY.md`](plugin/memory/NATIVE_MEMORY.md).

## Repo layout

This repo is both a **plugin marketplace** and the **plugin itself**:

```
.claude-plugin/marketplace.json   # marketplace manifest (lists the `hippo` plugin)
plugin/
├── .claude-plugin/plugin.json    # plugin manifest
├── memory/                       # the engine (Python package, imported as `memory`)
│   └── _vendor/                  # pre-bootstrap fallbacks (BM25 + frontmatter parser)
├── hooks/                        # UserPromptSubmit recall + SessionStart dispatcher + PreCompact nudge + SessionEnd/SubagentStop capture
├── assets/packs/                 # starter packs (core seeded by default; rest opt-in)
├── bin/hippo                     # CLI launcher for the stateless engine commands
├── requirements.txt              # fastembed, numpy, PyYAML, rank-bm25 (the venv path)
└── skills/                       # /hippo:bootstrap|init|new|recall|doctor|audit|consolidate|remove
tests/                            # hermetic test suite (no network/model download by default)
.github/workflows/ci.yml          # hermetic matrix + dense lane + shellcheck
```

New to the ideas here? Start with [How hippo thinks](CONCEPTS.md). For the deep internals
(recall, staleness, reconsolidation, archive), see the engine reference at
[`plugin/memory/README.md`](plugin/memory/README.md); for the skills, see
[`plugin/README.md`](plugin/README.md).

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

## Troubleshooting

- **Recall comes back empty.** Almost always one of three things: **(a) bootstrap never ran**
  on this machine — dense recall is silently BM25-only until `/hippo:bootstrap` finishes;
  **(b) the corpus isn't trusted yet** — a freshly cloned or downloaded corpus injects
  *nothing* until you review it, and running `/hippo:init` (or `/hippo:doctor`) here is what
  marks it trusted; **(c) `user_role.md` is still the `<FILL-ME>` template**, so the only
  thing to recall is placeholder text — edit it with your real role and context.
- **A memory I wrote never resurfaces.** Recall is on-demand and ranked, not always-on: only
  the always-load *floor* (the `user`/`feedback` pointers) is injected every prompt;
  everything else surfaces when a prompt actually matches it. Phrase your question closer to
  the memory's own wording, or confirm it's indexed with `/hippo:doctor`.
- **Recall surfaces something off-topic (before bootstrap).** Reliable *abstention* —
  returning nothing for an unrelated prompt — is dense-gated: BM25-only recall (before
  `/hippo:bootstrap` warms the dense model) can surface a weak keyword match for an off-topic
  prompt, because no lexical rule separates a coincidental keyword overlap from a real one.
  Warming the dense model enables the abstention floor; `/hippo:doctor` flags this state.
- **"Not a git repository" / staleness looks inactive.** Outside a git repo hippo runs in a
  degraded mode: recall, indexing, links, and the floor all work, but staleness tracking and
  provenance backfill need git — `git init` and commit to activate them.
- **When in doubt, run `/hippo:doctor`.** It is the one-stop diagnostic — it checks the native
  symlink, the recall index, corpus trust and drift, the corpus format version, link density,
  and unfilled templates, and prints the exact repair command for whatever it finds.

## Security

Found a vulnerability? Please report it privately — see [SECURITY.md](SECURITY.md)
for the disclosure channel, supported versions, and hippo's threat model (untrusted
shared corpora, credentials committed into memory, and prompt-injection via memory
text).

## License

MIT — see [LICENSE](LICENSE). A copy ships inside the plugin bundle
([plugin/LICENSE](plugin/LICENSE)) so installs carry the license text too. The engine code
was written by this repo's author for a private predecessor project and is relicensed here
under the same MIT terms; no third-party code was ported with it.
The pre-bootstrap fallbacks in `plugin/memory/_vendor/` (a BM25 scorer and a frontmatter
parser) are likewise original implementations, MIT like the rest — not copied vendor code.

hippo's own source ports no third-party code, but bootstrap installs a small set of
permissively-licensed Python packages (fastembed, numpy, PyYAML, rank-bm25 and their
dependencies) and downloads an embedding model. Those runtime components and their licenses
(all Apache-2.0 / MIT / BSD-3-Clause / MPL-2.0 / HPND) are inventoried in
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
