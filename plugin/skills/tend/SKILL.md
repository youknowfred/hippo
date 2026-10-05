---
description: Work hippo's maintenance queue one item at a time — captures to draft or discard, memories whose cited code moved, contradictions, merged-in duplicates, broken baselines and links, floor overflow, citation re-derivation, and changes waiting for re-consent — each with its evidence, a likely verdict and your yes. Triggers include "tend memory", "work the memory queue", "what maintenance is queued", "/hippo:tend".
---

# /hippo:tend — the one maintenance queue

hippo derives every kind of memory upkeep into one ranked queue. This skill works it with
the user: show the top item and its evidence, say the likely verdict, ask, and apply ONE
verdict at a time. Nothing is applied without the user's yes, and there is no apply-all.
Integrity items (changes withheld from recall, invisible baselines) come first.

## Surface routing — decide first, then act silently

- **On Claude Desktop** (your context says you are in the Claude desktop app, `CLAUDE_CODE_ENTRYPOINT` is `claude-desktop`, or the preflight below stops on an unset `CLAUDE_PLUGIN_DATA`): run this same flow through the `tend` MCP tool — action='list', then 'next' (or 'show' with id=…), then per item action='apply' with id= and verdict=, or 'snooze' / 'skip' / 'hold'. Drafting a memory from a capture uses the `new_memory` tool (check:true first). Just start driving the tool; don't explain why the shell flow isn't used here.
- **In a terminal Claude Code session**: run the bash flow below, guard first.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:tend skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # the shared interpreter resolver
hippo_resolve_py
hippo_note_usage skill tend  # count this skill's use (one spool line, no Python)
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. An inline
`hippo …` command runs as written (`hippo` is on the Bash tool's PATH and finds its own
venv).

## 1. See the queue

```bash
hippo tend
```

Tell the user the count by kind in one line, and anything held or failed. An empty queue is
a fine answer.

## 2. One item at a time

```bash
hippo tend next
```

`next` prints the item's id, why it is queued, the likely verdict, its gate, the evidence
(a capture's session summary, the code that moved under a memory, both sides of a pair, a
citation diff) and the exact verdicts it accepts. Read the evidence before proposing —
the likely verdict is a starting point, not the answer. Then ask the user, and only on a
yes:

```
hippo tend apply <id> --verdict <verdict>
```

What each kind needs before its verdict:

- **capture** — draft only what is durable and not re-derivable from code or git. Run
  `hippo new --check <name> "<description>" --type <type>` first, write each approved
  memory with `hippo new`, then `--verdict done`. Nothing worth keeping: `--verdict discard`.
- **reverify** — re-read the memory against the code that moved. `graduate` if it still
  holds; edit the body and `fix` if it was wrong; `demote` (optionally
  `--superseded-by <name>`) if it is wrong and not worth fixing; `archive` to retire it.
- **contradiction** — read both. `keep_one` or `merge` take `--winner` and `--loser` (merge
  after you folded the loser's unique content into the winner); `scope_both` after you
  edited both to name their scopes; `not_conflicting` when they are both right.
- **merge** — a merged-in memory looks like a duplicate. `supersede --winner <name>`,
  `updated` after you folded it into the existing memory, or `distinct`.
- **baseline** — confirm the memory still holds, then `rebaseline`.
- **link**, **floor** — edit the file, then `done`; tend re-checks and refuses while the
  problem is still there.
- **derivation** — read the citation diff, then `apply` for that memory; when every memory
  is applied, the `corpus` item takes `stamp`. On a corpus that is not committed to git,
  take a snapshot first (`hippo provenance --snapshot <label>`).
- **trust** — re-consent is its own gate: the `trust_corpus` MCP tool shows what changed
  since consent and binds your confirmation to exactly what you read (in a terminal,
  `/hippo:doctor` walks the same review). Consent is never inferred.

## 3. Deferring

- `hippo tend snooze <id> --days N` — not now.
- `hippo tend skip <id>` — leave it until its evidence changes.
- `hippo tend hold <kind-or-id> --reason "<why>"` — an owner decision to keep something
  as it is on purpose; held items count as resolved. `hippo tend release <kind-or-id>`
  undoes it. Only hold when the user says so, with their reason.

Repeat step 2 until the queue is empty or the user stops. Finish with one line: what was
applied, what was deferred, and what is left.
