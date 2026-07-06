# memory (plugin)

Local, git-native agent memory: a markdown-in-git corpus with offline dense+BM25 hybrid recall,
git-drift staleness/provenance tracking, and a self-audit skill. See
[`memory/README.md`](memory/README.md) for the full engine documentation (recall, staleness,
reconsolidation, archive internals).

## Skills

| Skill | Run when |
|---|---|
| `/hippo:bootstrap` | Once per Mac — builds the shared venv + warms the offline model cache |
| `/hippo:init` | Once per new project — seeds `.claude/memory/` + the cross-machine symlink |
| `/hippo:new` | Whenever the agent decides to save something to memory |
| `/hippo:doctor` | Fast health check — is the plugin's own install/environment working |
| `/hippo:audit` | Deep, judgment-based self-audit of the corpus's content — staleness, drift, archive candidates |

## Operating principle: the agent is the memory master

By default, this plugin operates on the assumption that **the agent owns memory upkeep
autonomously** — not the human. When staleness or curation surfaces (a signal at session start,
or an explicit "run memory maintenance" ask), the agent should run the resolution pass itself:
read the flagged memory, check it against current reality, then resolve — still-accurate →
re-verify it; drifted → fix the body, then re-verify; obsolete → archive. The agent acts, then
reports; git is the audit/revert path, since the corpus is markdown-in-git. Verification is the
agent's judgment, never a human pre-approval checkpoint, and there is deliberately no bulk
"reverify everything at once" primitive anywhere in this engine — every resolution is a single,
deliberate, individually-justified action (see [[reverify_head_only_no_bulk]] in the shipped
corpus this engine was extracted from, and the no-bulk-primitives hard rule that carried forward
into `/hippo:audit`'s design).

This is a **default assumption seeded by the operator pack** (`assets/operator-pack/claude_is_memory_master.md`),
not a hardcoded behavior — if an operator prefers to review corpus maintenance themselves before
it happens, they should say so explicitly and delete or edit that memory; absent that
instruction, the agent should act autonomously on corpus upkeep.

## Design note: why bootstrap is explicit, not automatic

See the [repo root README](../../README.md#bootstrap-vs-auto-provision-design-decision) for the
full reasoning — in short: the one online step in this plugin's whole lifecycle (venv + model
warm) is deliberately never triggered from a hook, so hooks stay simple, offline, and
always-exit-0.
