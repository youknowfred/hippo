# Settings and gates

The reference for everything a corpus or a machine can configure: where each setting lives,
the `hippo.json` keys, the outbox and `hippo ship`, and the write and commit gates. None of it
is needed to start using hippo; the [README](README.md) covers setup and daily use, and the
[engine reference](plugin/memory/README.md) covers internals.

- [Where settings live](#where-settings-live)
- [Environment variables](#environment-variables)
- [Recall lanes](#recall-lanes)
- [Tracker statuses at read time](#tracker-statuses-at-read-time) (Linear, or any tracker with a committed mirror)
- [Folded sections](#folded-sections)
- [Acknowledging a cleared lint finding](#acknowledging-a-cleared-lint-finding)
- [Saving without touching the working tree](#saving-without-touching-the-working-tree-opt-in) and [`hippo ship`](#hippo-ship-one-branch-one-commit-nothing-pushed)
- [The write gate](#the-write-gate), [consent for a session's own writes](#consent-for-a-sessions-own-memory-writes-opt-in) and [the commit gate](#at-commit-time-hippo-gate-and-declared-budgets)

## Where settings live

hippo's settings live in three places, and the first one that answers wins:

1. **An environment variable**: a per-shell or CI override.
2. **hippo's plugin options**: your machine's settings. Claude Code asks for them when you
   enable the plugin and lists them in `/config`: the calm SessionStart switch, the LLM
   opt-ins (capture triage, the dream contradiction check, the deprecated dream drafts
   tier), the LLM model, and the LLM API key (kept in your system keychain).
3. **`.claude/memory/hippo.json`**: the corpus's committed policy, shared with your team.

The `hippo.json` keys:

| Key | What it sets |
|---|---|
| `volatile_paths` | Files that change by design. A memory citing one is still recalled, but that drift alone does not put it on the re-verify worklist. |
| `floor_lint` | The floor's own limits: `max_line`, `banned_re`, `section_budgets` ([the write gate](#the-write-gate)). |
| `floor_sections` | The `MEMORY.md` headings that hold floor pointers, in place of the defaults. |
| `fold_digests` | Filename globs of digest files, whose `### <slug>` sections are recall units ([folded sections](#folded-sections)). |
| `journals` | Filename globs of journals: the commit gate's journal budget counts them, and the [recall lanes](#recall-lanes) act once it is declared. |
| `ledgers` | Committed tracker mirrors whose statuses recall renders ([tracker statuses](#tracker-statuses-at-read-time)). |
| `attention` | `"calm"` or `"full"`: the SessionStart report's length. |
| `mute` | SessionStart signals to hide. |
| `acknowledged_findings` | Lint findings you reviewed and cleared ([below](#acknowledging-a-cleared-lint-finding)). |
| `write_path` | `"outbox"` or `"direct"`: where a saved memory lands ([the outbox](#saving-without-touching-the-working-tree-opt-in)). |
| `gate` | The write gate's posture: warn, `"deny"` or `"off"` ([the write gate](#the-write-gate)). |
| `consent` | `{"session_writes": true}`: consent for a session's own memory writes ([below](#consent-for-a-sessions-own-memory-writes-opt-in)). |
| `budgets` | Hard numbers the commit gate holds the corpus to ([below](#at-commit-time-hippo-gate-and-declared-budgets)). |

Three of these change what a machine does with bytes that arrive by pull, so each takes one
`hippo trust policy --accept` per machine before it acts: `write_path`, `gate` set to
`"deny"`, and `consent`. The rest only describe the corpus and act as soon as they are
committed.

## Environment variables

The frozen environment set is the nine directory overrides (`HIPPO_MEMORY_DIR`,
`HIPPO_INDEX_DIR`, `HIPPO_TELEMETRY_DIR`, `HIPPO_PENDING_DIR`, `HIPPO_LOCAL_MEMORY_DIR`,
`HIPPO_USER_MEMORY_DIR`, `HIPPO_PROJECTS_FILE`, `HIPPO_TRUST_FILE`,
`HIPPO_CLAUDE_PROJECTS_DIR`), `HIPPO_DISABLE=<list>` (any of `dense`, `jit`, `presence`,
`floor-nag`, `abstain-gate`, `touch-fastpath`), `HIPPO_TRUST_ALL` and `HIPPO_TRUST_NONGIT`.
[STABILITY.md](STABILITY.md) is the contract for them. Every other `HIPPO_*` name these docs
mention (`HIPPO_CORPUS_ROOT`, `HIPPO_SIGNAL_COUNT`, `HIPPO_HOOK_COMPACT`, …) is a tuning
override outside the frozen set; the engine reference lists them under
[Environment overrides](plugin/memory/README.md#environment-overrides).

The spellings from before v1.42 still work and stop at v2.0: `HIPPO_DISABLE_DENSE` and its
five siblings (now `HIPPO_DISABLE=dense`, …), `DREAM_CONTRA_MAX_PAIRS` and
`DREAM_CONTRA_MIN_COFIRE` (now with a `HIPPO_` prefix), the policy keys in
`.claude/memory/.format` (move them to `hippo.json`; the format number stays in `.format`), and
`~/.claude/hippo-llm.json` (move its settings to the plugin options, and its key out of the
plain-text file). `/hippo:doctor` lists every old name it sees with its new spelling.

## Recall lanes

Each memory sits in one lane: **behavior** (`type: user` or `feedback`),
**fact** (`project`, `reference`, anything else) or **journal** (a file matching
`fold_digests` or a `journals` glob). A corpus whose `hippo.json` declares `journals` as a
list (`[]` counts) changes what the prompt hook injects, and only that: a journal is injected
only when the prompt names it (`round-12`, `[[round-12]]` or `round-12.md`), when, with a
ledger declared, the prompt names an issue id it cites (below), or when it earns its place
by its own signal: it leads every candidate the ranker fused for the prompt and has evidence
of its own under the count rule. At most one journal per prompt enters that last way, and it
is an extra row beside the others, never in place of one (`HIPPO_JOURNAL_SIGNAL=0` turns it
off; it needs the dense model). The hook
picks behavior memories first, up to 4 of its 10 slots and 40% of the prompt's character
budget, then facts, with any lane's unused room going to the others. `hippo recall` and the
`recall` tool are unchanged and still return journals. Nothing is withheld from a corpus that
declares no `journals`. Doctor's recall-lanes line reports each lane's share of what the hook
injected beside its share of the corpus. `journals` only names files; like `mute`, it needs no
`hippo trust policy --accept`.

## Tracker statuses at read time

A memory should cite a tracker issue by its id and leave the status to the tracker. A status
typed into a memory is wrong the day the issue moves, and nothing in the repo changed to say
so. If the repo commits a mirror of its tracker, declare it and recall shows each cited id's
status beside the row, dated, so nobody has to type it into a memory.

The example is a Linear mirror, the setup this feature was built for: a `LINEAR-SNAPSHOT.yaml`
that the repo's own script refreshes from Linear and commits. Any tracker works the same way
(Jira, GitHub Issues, a hand-kept file), because hippo reads only the committed file. It
never calls Linear's API or any other tracker's, holds no tracker credential, and writes
nothing back. Up to four ledgers can be declared.

```
hippo recall --for-issue ENG-901    # the memories that cite one issue, under its status
```

The declaration:

```json
{"ledgers": [{"name": "linear", "path": "LINEAR-SNAPSHOT.yaml", "id_re": "\\bENG-\\d+\\b",
              "entry_key": "id", "status_key": "state"}]}
```

A row whose name or description cites `ENG-901` then ends `· ENG-901 In Progress (snapshot
2026-08-26)`, at most two ids per row, in the prompt hook's rows, `hippo recall` and
`hippo inspect`. The date is the mirror's last commit. hippo reads the file line by line: an
entry starts at an `id: <an id_re match>` line (`- id:` in a list too) and its status is the
first `state:` line at that key's indent before the next entry; nothing else in the file is
read. The `path` must stay inside the repo, `id_re` must be a plain pattern (no nested
repeats, alternation under a repeat, backreferences or lookarounds; 120 characters at most),
and a status is cut to 40 characters on one line. The join is a derived cache,
`.hippo/index/ledger.json`, built at SessionStart and by `hippo build-index` for a trusted
corpus only, and never a network call or a write to the tracker or the corpus. A ledger that
cannot be used is dropped and `/hippo:doctor` says why. `ledgers` only declares data, so it
needs no `hippo trust policy --accept`; a corpus without it is unchanged. When the corpus also
declares `journals`, a journal the recall lanes would withhold is let through when it cites an
issue id the prompt names (`what's left on ENG-901?`); it still has to rank, and the journal
cap still applies. The cache holds the map from each id to the journals citing it (8 at most
per id), so the hook never reads the corpus for it.

## Folded sections

In a file matching `fold_digests`, each `### <slug>` section (a heading
that is one slug, nothing after it) is a recall unit of its own, found by its name, its first
line and its opening text, and shown as `slug (digest.md:LINE)` so you open the right place.
A section whose slug is also a live memory's file name is skipped (the file wins). Sections
never take a memory's place: `hippo recall` and the `recall` tool list up to 5 matching
sections after the memories (`HIPPO_SECTION_ROWS` changes the number; a query no memory
matches stays unanswered), and the prompt hook shows a section only when the prompt names its
slug. A `[[slug]]` link to a folded section is not a broken link and never resolves to the
whole digest. Sections live in a sub-index beside the main one
(`.hippo/index/sections.json`); a corpus with no sections gets none.

## Acknowledging a cleared lint finding

When a memory file carries a secret or threat finding
you have reviewed and cleared, add an entry to `acknowledged_findings` in `hippo.json` —
`{"file": "notes.md", "rule": "html-comment", "sha256": "<the file's sha256>", "reason": "…",
"date": "YYYY-MM-DD"}` — and commit it. `/hippo:doctor` then counts the finding instead of
warning it; its warn line names each finding's rule and the file's sha256 once the key exists
(or run `shasum -a 256 .claude/memory/notes.md`). Any change to the file re-arms the finding,
and doctor says the acknowledgement no longer matches. Locally an acknowledgement counts only
on a corpus you trust, for the exact bytes you consented to; the review packet and
`hippo review --ci` count only acknowledgements already present at the base of the range, so a
change can never acknowledge its own finding. The release secret scan ignores the list.
A floor line that an always-loaded rule already says (`hippo lint-floor` lists these, and they
never fail anything) is acknowledged the same way with `"file": "MEMORY.md"` and
`"rule": "restates-rule"`; `MEMORY.md` is outside the consent baseline, so that one entry needs
only a matching hash.

## Saving without touching the working tree (opt-in)

When several checkouts write one corpus, every saved memory is a change someone has to commit.
A corpus can opt out of that: `"write_path": "outbox"` in `hippo.json` sends each new memory,
and the floor pointer it adds, to `.claude/memory.outbox/`, a gitignored folder beside the
corpus (it also ignores itself). The corpus folder is not touched, so `git status` stays clean.
Recall reads the outbox like a tier, so a saved memory is recalled from the next prompt; an
outbox copy of a memory takes precedence over the committed one.

- **Where.** Always `.claude/memory.outbox/` beside the corpus; the setting cannot name a path.
  A linked git worktree uses its main tree's corpus, so one clone has one outbox for all its
  worktrees.
- **When a save goes there.** Only when this machine accepted the setting: because
  `hippo.json` arrives by pull, a setting that changes where writes land takes one
  `hippo trust policy --accept` per machine (and a trusted corpus). Until then saves land in the
  working tree as before, and the save and `/hippo:doctor` say what is waiting. Any value other
  than `"outbox"` or `"direct"` behaves as `"direct"`, and doctor names it. The user and private
  tiers are never redirected.
- **Trust.** hippo records the hash of every file it writes into the outbox in this machine's
  trust registry, and recall injects an outbox file only while its bytes match. A file that
  arrived another way (dropped in, pulled, edited by hand) is withheld and counted by doctor;
  if it shadows a committed memory, recall uses the committed one. `hippo trust review` shows
  such a file (as `outbox:<name>`) and `hippo trust grant` consents it.
- **Edits go there too.** Every `/hippo:tend` verdict that changes an existing memory (graduate,
  demote, keep one of a contradiction, re-derive citations, heal a baseline), and the other
  hippo edits (typed edges, dream edges, the related-line fix), copy the memory into the outbox
  on first edit and change the copy, so `git status` stays clean and the item does not come
  back: tend reads what it wrote. A committed file this machine withholds is refused with the
  reason, never copied. Removals are not edits: archive and promote still change the corpus
  folder (commit those yourself) and remove the memory's outbox copy with it. Packs keep
  writing the corpus folder and refuse a memory that has an outbox copy.
- **Reads do not depend on the setting.** Turning it off never hides memories still in the
  outbox: recall and doctor keep seeing them.
- **The floor.** The floor pointer goes into the outbox's copy of `MEMORY.md`. Claude Code
  still loads the committed floor, so a memory whose pointer is only in the outbox copy stays
  recallable on demand.
- **Landing them.** `hippo ship` lands the outbox as one reviewed batch; see below.
  `/hippo:remove` reports the outbox and never deletes it.

### `hippo ship`: one branch, one commit, nothing pushed

`hippo ship` previews; `hippo ship --yes` acts. It makes ONE commit holding the outbox's new
and edited memories on ONE new branch (`hippo/ship-<UTC time>`, or `--branch <name>`) off the
local default-branch ref (`origin/HEAD`, else `origin/main`, `origin/master`, else your local
`main`/`master`; `--base <ref>` picks another), and stops. It never fetches, so the base is as
fresh as your last `git fetch`; it never pushes, never touches your checkout, your staged
changes or `HEAD`, never overwrites a branch, and never carries a deletion. Push the branch
and open the pull request yourself. Because no `git commit` runs in your working tree, the
repository's git hooks do not run for that commit; hippo runs its own gate judgment (the
corpus's committed `budgets` and `floor_lint`) and refuses while a finding stands, and the
pull request's checks run the rest.

Each outbox file is one of: **new** (ships), **waiting** (already on a ship branch that still
exists, unchanged since: not shipped again), **stale** (the committed copy changed since the
outbox copy was made, appeared under the same name, or was deleted: shown as a diff and never
shipped; `hippo ship --mine <name>` keeps the copy as an edit of the current version,
`hippo ship --drop <name>` removes the copy), **held** (a credential shape, a threat-lint
class, or bytes hippo did not write on this machine: the file and the class are named, never
the matching text), or **landed** (the default branch already has these bytes). The floor is
never committed whole: the pointer adds and removes hippo made in the outbox copy of
`MEMORY.md` are replayed onto the floor at the base, so both sides' pointers survive. Files
stay in the outbox, and recallable, until they land; once the default branch and this checkout
both hold them, the next `hippo ship --yes` clears them (and folds the bytes this machine wrote
into the corpus's consent, so the landed file is not withheld). `--json` prints the plan.

## The write gate

Before a file tool writes into the corpus, a `PreToolUse` hook simulates the write and asks
one question: does it leave a budget over **and** make it worse? It checks the floor
(`MEMORY.md` bytes and lines against the harness read window, lines over `floor_lint.max_line`,
lines matching `floor_lint.banned_re`, each `floor_lint.section_budgets` entry) and runs the
same `banned_re` over memory bodies (frontmatter excluded). Standing debt never objects: an
edit that holds or shrinks a measure always passes, so a corpus that is over budget today can
still be edited.

By default a worsening write goes ahead with one line in the model's context naming each
budget, its before and after numbers, and the way out. A corpus can ask for more, or less, in
`hippo.json`:

```json
{"gate": {"write": "deny"}}
```

`"deny"` refuses a worsening write, but only where that value is in the **committed**
`hippo.json` and this machine accepted it (`hippo trust policy`, then `hippo trust policy
--accept`): a pulled or uncommitted change can never turn refusal on by itself. `"off"`
silences the check for the corpus; any other value only warns. `/hippo:doctor` states which of
these applies. The hook always exits 0, and a write outside a corpus is rejected in bash
before any Python starts.

### Consent for a session's own memory writes (opt-in)

Once setup links Claude Code's native memory dir to `.claude/memory/`, a memory that a session
writes with its own file tool lands in the corpus with no consent recorded, so hippo's recall
withholds it until `hippo trust review` and a grant. A corpus can skip that review for exactly
those bytes:

```json
{"consent": {"session_writes": true}}
```

This setting needs `hippo trust policy --accept` on each machine before it does anything. Once
accepted, the `PreToolUse` hook notes the file's state before a Write, Edit or MultiEdit
(absent, consented, or not) and the bytes the tool will leave, in a small note in the
gitignored telemetry dir. The `PostToolUse` hook then consents to the bytes on disk only when
the corpus is already trusted with a per-file record, the file sat directly in a corpus this
session works in (symlinks resolved; the main checkout's when the session runs in a linked
worktree), it was absent or consented before, the bytes on disk are exactly the ones
predicted, the note belongs to this same tool call (its `tool_use_id`), the call was not made
inside a subagent (the payload names no `agent_id`; a harness that does not send `agent_id`
cannot be told apart from the main session), and the bytes pass the lints a review gates on
(secrets; hidden characters, homographs, HTML comments, exfiltration shapes, tool mimicry).
A write that fails a lint is held for review and counted. Anything else grants nothing and
says nothing.

The prediction is exact for a Write (it leaves exactly `content`) and for an Edit or
`replace_all` Edit of an LF file or a file with a UTF-8 BOM. An Edit of a CRLF file with a
multi-line `new_string` is not predicted (the tool writes the inserted newline as CRLF), so
such an edit grants nothing: it waits for review.

What this does and does not change:

- Claude Code's native loader already reads these files (`MEMORY.md` and whatever the model
  opens through it) with no gate at all. hippo's gate only governs what hippo's own recall
  injects.
- The gate never defended against this machine's own session when it has a shell (it can run
  `hippo trust grant` or `hippo trust policy --accept` itself) or file-tool access to the trust
  registry under your home directory. This setting changes what a session limited to file tools
  inside the project can get into hippo's recall without you: its own new, or
  already-consented-then-edited, memory files that pass the lints above.
- With the setting on, those bytes join hippo's recall without a human review. With it off (or
  not accepted), nothing changes.
- Bytes that arrive by pull, merge, checkout or a shell redirect are still withheld, and so are
  an edit to a file whose bytes were not consented, a subagent's write and a write a lint
  flags. An untrusted corpus stays untrusted, and the recall block still labels memory text as
  quoted data.

`hippo trust status` counts and names the memories consented this way, the daily rollup counts
the grants and the held writes, and `/hippo:doctor` sorts withheld memories by how they arrived (the native path,
written by hippo elsewhere, other), naming this setting when the native-path count is not zero.

### At commit time: `hippo gate` and declared budgets

`hippo gate --staged` runs the floor checks on what the next commit records (HEAD against the
index, read with git plumbing, so untracked files never count), with the same rule: it fails
(exit 1) only when the change leaves a budget over and makes it worse. The banned pattern over
memory bodies is not checked at commit time: a status word in a body is the write gate's
warning, never a failed commit. `hippo gate --commit
<rev>` judges one commit against its first parent, for CI or a replay of history. A corpus can
declare hard numbers of its own in `hippo.json`:

```json
{"budgets": {"files": 1200, "floor_bytes": 20000, "floor_lines": 150, "journals": 300}}
```

`files` counts memory files directly in the corpus dir, `journals` counts files matching
`fold_digests` or a `journals` glob list, and `floor_bytes`/`floor_lines` are the floor's own
budget. They sit beside the harness read window rather than replacing it: at commit time the
floor is also held to the read limit (25,000 bytes or 200 lines, or a lower declared
`floor_lint` cap), while the write-time warn line stays advice. Each key is optional; a corpus
with no `budgets` and no `floor_lint` is not checked. The budgets are read from the side being
committed, so raising one deliberately in the same change is judged by the new number.

A declared `floor_bytes` also puts the floor on a diet: `hippo tend` lists one demotion per
floor line until the floor would fit, lines whose memory is not a user or feedback memory
first, the least used first (injected or opened in the last 30 days, as far back as the local
ledgers reach). Each item says whether a replay of recent prompts shows the memory reaching a
prompt only through its floor line; such a line is listed last and never proposed as safe.
The replay ranks at most 50 prompts per `hippo tend` call and keeps each ranking in a
gitignored cache, so later calls read it back; a line whose prompts are not all replayed yet
says "wait". `hippo doctor` never runs the replay.
`--verdict demote` removes that one line and nothing else: the memory file stays, and recall
still finds it. `hippo doctor` shows the floor against the budget and how to start, and
suggests 8,192 bytes when a floor without a budget is over the warn line.

`hippo gate --install` shows the pre-commit hook it would write and where (it follows
`core.hooksPath` and linked worktrees); `--install --yes` writes it. It never overwrites a
pre-commit hook it did not write (it prints the two lines to add by hand instead), it exits 0
when hippo is not installed, so it cannot stop commits on a machine without the plugin, and
`git commit --no-verify` skips it for one commit.
