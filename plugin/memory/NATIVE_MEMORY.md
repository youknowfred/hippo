# hippo ↔ Claude Code native memory — the coexistence contract (INT-4)

hippo does not replace Claude Code's built-in memory; it **composes** with it. This document
names every native behavior hippo depends on, so that if the harness changes one, the break is
a documented contract violation rather than a silent, mysterious failure. `/hippo:doctor` checks
this contract on every run (the `symlink` and `native_coexistence` checks).

## The one native behavior hippo relies on

Claude Code always-loads the contents of a per-project **memory** location: the directory (or
symlink) at

```
~/.claude/projects/<encoded-project-path>/memory
```

where `<encoded-project-path>` is the absolute repo path with **every non-alphanumeric
character replaced by a single `-`** (verified against the harness's real
`~/.claude/projects/` entries — see `provenance.encode_project_dir`, SHP-5; a dotted or
underscored path like `~/dev/next.js-app` must encode the same way the harness does or the link
lands where nothing reads it).

`/hippo:init` creates that `memory` entry as a **symlink pointing at this repo's
`.claude/memory/`**. That is the entire integration: because the harness always-loads whatever
is at that path, and hippo points it at the committed corpus, hippo's `MEMORY.md` floor (the
`user`/`feedback` always-load pointers) reaches context every session **through the native
mechanism** — hippo adds no second always-load channel of its own. (The MCP
`hippo://floor` resource, RUL-5, does not weaken this promise: it is an **agent-pulled**
read a Task subagent may request explicitly — nothing auto-loads it.)

**hippo relies on exactly this and nothing else about native memory:**

1. The harness reads `~/.claude/projects/<encoded>/memory` for the current project.
2. The encoding rule is "every non-`[A-Za-z0-9]` → `-`" (SHP-5).
3. A **symlink** at that path is followed to its target (so a repo-local, git-committed corpus
   can be the source of truth instead of an opaque harness-managed store).

hippo does **not** rely on any native memory *file format*, on native summarization/extraction,
on native write timing, or on any private API. The markdown-in-git corpus is hippo's single
source of authority; the native symlink is only the *delivery* path for the floor.

## How the contract can break (and how doctor detects it)

| Failure | What happens | Detected by |
|---|---|---|
| **Symlink-target drift** | The link resolves somewhere other than this corpus — the floor is drawn from a different target, or nothing. | `doctor` `symlink` (`broken`) + `native_coexistence` (DRIFT) |
| **Native-layout / encoding change** | The harness changes the projects-dir encoding, so a legacy-encoded link is read and the correct one is ignored. | `doctor` `symlink` (`legacy_wrong_encoding`) + `native_coexistence` |
| **Native memory occupies the slot** | A real directory/file (native memory taking over, or a stray write) sits where hippo's symlink should be — the floor can't inject through it. | `doctor` `native_coexistence` (native-layout change) |
| **Unexpected native write into the corpus** | Because the symlink points native memory at `.claude/memory/`, a native write lands in the corpus dir. | `doctor` `integrity` (unparseable frontmatter) surfaces non-hippo files; `native_auto_memory` counts files carrying native's stamp with no hippo provenance, and untracked ones (NAT-1); the corpus stays the git-tracked source of truth |
| **Auto memory turned off** | `autoMemoryEnabled: false` (any settings scope) or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` stops native memory loading `MEMORY.md`, so the floor never reaches context. | `doctor` `native_auto_memory` (warn, names the setting that decided it) |
| **Native directory redirected** | `autoMemoryDirectory` (any settings scope) makes native memory read that directory's `MEMORY.md` and write topic files there, bypassing the symlink. | `doctor` `native_auto_memory` (warn, names the scope and the target) |
| **A memory edited through the native path** | An edit made through `~/.claude/projects/<encoded>/memory/` gets native's frontmatter stamp (`node_type`, `originSessionId`, `modified`) and a reflowed body, so the file drifts from its consent baseline. | `doctor` `native_auto_memory` (stamp count) and the trust-drift line |

The repair in every case is the same and idempotent: re-run `/hippo:init` (ONB-5 leaves an
existing corpus untouched — it only rebuilds the machine-local symlink + index), moving any real
file/dir aside first if native memory has taken the slot.

## Verified behavior (PLT-1, 2026-10-03, Claude Code 2.1.286)

Observed with tools disabled, so a fact could only come from what Claude Code loaded; the full
receipts and method are in the root [`PLATFORM.md`](../../PLATFORM.md).

| Behavior | Observed |
|---|---|
| `MEMORY.md` through the symlink, default settings | loads |
| `autoMemoryEnabled: false`, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | does **not** load: the floor is gone |
| `autoMemoryDirectory` set (project, local, or `--settings`) | the other directory's `MEMORY.md` loads instead; the symlink is bypassed |
| A CLAUDE.md `@.claude/memory/MEMORY.md` import | loads, with auto memory on or off |
| A general-purpose subagent | receives the floor under either channel (the docs say native auto memory does not reach subagents) |
| A linked worktree | the symlink channel reads the main tree's copy (native keys its directory on the repository); an import reads the worktree's own copy |
| Topic files | not loaded until opened; no per-turn native recall step was observed |
| An edit through the native path | gains native's frontmatter stamp; the same edit through `.claude/memory/` does not |

## Why this over built-in memory?

See the "hippo and Claude Code's native memory" positioning section in the root
[`README.md`](../../README.md) — in short: native memory is an opaque, per-machine, always-loaded
store; hippo is a **git-native, reviewable, recall-ranked** corpus that reaches context *through*
native memory's always-load for its floor while serving everything else on demand via hybrid
recall. They compose; hippo does not fork native memory.
