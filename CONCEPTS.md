# How hippo thinks

hippo gives Claude Code a memory that is nothing more exotic than **a pile of small markdown
files in your repo's git history**. No database, no server, no cloud. This page is the
five-minute mental model — four ideas that explain the whole system:

1. [What a memory is](#a-memory-is-a-small-markdown-file)
2. [How a memory reaches Claude — the floor vs. on-demand recall](#two-ways-a-memory-reaches-claude)
3. [The four kinds of memory](#four-kinds-of-memory)
4. [Why markdown, in git](#why-markdown-in-git)

Read this first; then the [Quickstart](README.md#quickstart) is a five-minute install.

## A memory is a small markdown file

Each memory is one `.md` file under `.claude/memory/` holding **one durable fact**. It has a
frontmatter header and a prose body:

```markdown
---
name: token-refresh-not-on-mobile
description: how JWT refresh works and which client is missing it
metadata:
  type: project
  cited_paths: ["src/auth/token.py"]
---

Auth uses short-lived JWTs (15 min) refreshed via `/token/refresh`. The mobile
client does NOT implement refresh yet — it silently logs the user out at expiry.
```

The one field that matters most is **`description`**: it's the *recall hook*, the primary text
your future prompts get matched against. Write it the way you'd later ask about the fact, not
as a title. (The body is indexed too, as a secondary backstop — but the description is what
carries a memory to the right prompt.)

Because a memory is just a file, you can read it, edit it, `git diff` it, and delete it with
ordinary tools. There is no opaque store to trust.

## Two ways a memory reaches Claude

This is the central idea. A memory can reach Claude in one of two ways, and which one depends
on the memory's type.

**The floor — always loaded, every prompt.** The floor is a single lean file, `MEMORY.md`,
that holds pointer links to your most universally-relevant memories. It is small *on purpose*:
it's injected into every prompt, so it only carries the handful of facts that are always worth
knowing — who you are, how you like to work. (It reaches Claude through Claude Code's own
always-on memory, which hippo points at your repo with a symlink during `/hippo:init` — hippo
adds no second always-load channel of its own.)

**On-demand recall — matched per prompt.** Everything else is recalled *only when it fits*. On
each prompt, a hook matches your words against the whole corpus using **hybrid search** — a
local dense-embedding model plus classic keyword (BM25) scoring, fused together — and injects
just the few memories that actually match. This hot path runs entirely on your machine in
milliseconds: **no LLM call, no network, no tokens spent.** Before you've downloaded the
embedding model it still works, in keyword-only mode; the dense half is an upgrade, never a
requirement.

Why the split? Always-loading *everything* would bloat every prompt and bury the signal. The
floor carries the few always-relevant facts; recall handles the long tail, surfacing a
memory the moment a prompt makes it relevant and staying silent otherwise.

## Four kinds of memory

Every memory has a **type**, and the type decides whether it's floor-eligible or recall-only:

| Type | What it captures | Reaches Claude via |
|---|---|---|
| **`user`** | who the operator is — role, responsibilities, perspective | **the floor** (always) |
| **`feedback`** | a correction or confirmed-good approach, *and why* | **the floor** (always) |
| **`project`** | ongoing work state, decisions, non-obvious constraints in the code | on-demand recall |
| **`reference`** | a pointer to an external system (dashboard, tracker, channel) | on-demand recall |

The idea: `user` and `feedback` are the small set of facts that should shape *every* response,
so they sit on the always-loaded floor. `project` and `reference` facts are only sometimes
relevant, so they wait to be recalled. A `feedback` memory always states the **why** behind a
rule, not just the rule — that's what makes it worth always-loading. (The full field-by-field
reference lives in [CONVENTIONS.md](plugin/assets/CONVENTIONS.md), which hippo also seeds into
every corpus so the rules travel with the memories.)

## Why markdown, in git

Storing memory as reviewable files in git — instead of an opaque per-machine blob — is what
gives hippo its four defining properties:

- **You can review it.** A new memory is an ordinary diff. hippo's *automatic capture* drafts
  candidate memories from what happened in a session, but parks them unwritten in a pending
  queue — nothing enters your corpus until you approve it (`/hippo:consolidate`). Memory that
  writes itself, gated by a human.
- **It travels with the repo.** Clone the repo and you get its memory; a teammate opening the
  project shares the same reviewed corpus, instead of each person keeping private, opaque
  notes. (There's also a gitignored local tier for notes you *don't* want to publish.)
- **It has real history.** `git blame`, revert, and PR review all work on your memory, because
  it's just text in your tree.
- **It notices when the code moves.** A memory can cite the code it describes (`cited_paths`),
  and hippo records the commit that code was true at. When those files later change, recall
  flags the memory *as it surfaces* — `anchored to <sha>; 2 cited files changed — verify`. This
  is **semantic staleness**: it fires because the cited code actually moved, not because a
  calendar timer expired. A quick human re-verification clears the flag.

Everything hippo derives from these files — the recall index, caches, telemetry — is
rebuildable and gitignored. The markdown in git is the single source of truth; if a derived
cache ever disagrees, the files win.

## Where to go next

- **[Quickstart](README.md#quickstart)** — install, bootstrap, init, and see your first recall.
- **[CONVENTIONS.md](plugin/assets/CONVENTIONS.md)** — the field-by-field reference for writing
  memories (frontmatter schema, typed relations, the evidence-block convention).
- **[The engine reference](plugin/memory/README.md)** — how recall, staleness, and
  reconsolidation work under the hood (deep internals; you don't need it to *use* hippo).
- **[The skills](plugin/README.md)** — the `/hippo:*` commands (`new`, `recall`, `doctor`,
  `consolidate`, `audit`, …) you drive hippo with day to day.
