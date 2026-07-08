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
mechanism** — hippo adds no second always-load channel of its own.

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
| **Unexpected native write into the corpus** | Because the symlink points native memory at `.claude/memory/`, a native write lands in the corpus dir. | `doctor` `integrity` (unparseable frontmatter) surfaces non-hippo files; the corpus stays the git-tracked source of truth |

The repair in every case is the same and idempotent: re-run `/hippo:init` (ONB-5 leaves an
existing corpus untouched — it only rebuilds the machine-local symlink + index), moving any real
file/dir aside first if native memory has taken the slot.

## Why this over built-in memory?

See the "hippo and Claude Code's native memory" positioning section in the root
[`README.md`](../../README.md) — in short: native memory is an opaque, per-machine, always-loaded
store; hippo is a **git-native, reviewable, recall-ranked** corpus that reaches context *through*
native memory's always-load for its floor while serving everything else on demand via hybrid
recall. They compose; hippo does not fork native memory.
