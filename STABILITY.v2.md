# Stability & compatibility policy, v2 (draft)

> **This is a draft.** It takes effect at **v2.0.0** and replaces [STABILITY.md](STABILITY.md)
> then. Until v2.0.0 ships, STABILITY.md is the contract: nothing in this file freezes or
> unfreezes anything yet. The questions at the end are for the owner to settle before the cut.

hippo v1 froze a short list of names and left everything added after v1.0 unclassified. v2
classifies every surface the plugin ships. Each one is either **frozen** (you can depend on it),
**explicitly unfrozen** (it may change in any release), or a **legacy name** that v2.0.0
removes. Every entry below names where it lives in the tree, so you can check it against the
code yourself.

## The rules

- **Frozen** means a rename or removal needs a major-version bump, or ships with a deprecation
  window: the old name keeps working and says it is going, for at least two minor releases.
  Adding something new next to a frozen surface is a minor, non-breaking change.
- **A deprecation window ends.** Each use of an old name is counted (doctor's 30-day surface
  usage line, and a once-per-session count for old settings). At the end of its window a name
  is removed if the count shows no use in the last 14 days. Otherwise the window gets **one**
  dated extension, and the name is removed when that runs out. There are no permanent shims.
- **A new surface becomes eligible for freezing** after two releases in the field and a usage
  count that shows it is used. Anything younger stays unfrozen, even when it looks settled.
- Versioning is unchanged from v1: semantic versions, kept in lockstep in
  `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- The crash-safety contract and the support policy in STABILITY.md carry over unchanged and
  move into this file at the cut.

## Frozen at v2.0.0

### Skills

The nine `/hippo:*` commands you type. Each lives at `plugin/skills/<name>/SKILL.md`; the
registry of record is `V2_VERBS` in `plugin/memory/surfaces.py`.

- `setup`: set hippo up once per machine and once per project.
- `new`: save one memory, right by construction.
- `recall`: query recall, with the why receipt, lineage and graph hops.
- `tend`: work the one maintenance queue.
- `doctor`: health checks, the consent step and the content audit.
- `share`: packs, promote, publish, export and import.
- `review`: review a memory diff like a pull request.
- `dream`: the offline link pass.
- `remove`: take hippo out of a project.

A skill keeps its name. What the skill says and the steps it runs are not frozen.

### MCP tools

Ten tool names, each with the inputs it requires. Schemas live in `plugin/memory/mcp_schemas.py`
(`recall`, `new_memory`) and `plugin/memory/mcp_schemas_v2.py` (the other eight); dispatch is
`_DISPATCH` in `plugin/memory/mcp_server.py`.

| Tool | Required inputs | What it is |
|---|---|---|
| `recall` | `query` | ranked recall for a query |
| `new_memory` | `name`, `description`, `type` | save one memory |
| `inspect` | `action` | why receipts, lineage, graph hops, blast radius |
| `tend` | none | the maintenance queue |
| `doctor` | none | health checks, secrets scan, audit material |
| `setup` | `action` | bootstrap, init, index build |
| `trust` | none | corpus consent: review, grant, revoke |
| `share` | `action` | packs: extract, install, update |
| `dream` | none | the offline link pass |
| `review` | none | the memory-diff review packet |

A frozen tool keeps its name, and the inputs it requires keep their names and meanings. A
new required input would be a breaking change. Everything else about a tool is unfrozen (see
below). `recall_hook` is internal and is not in this list.

### MCP resources

Three resource URIs, served from `plugin/memory/mcp_resources.py`: `hippo://floor`,
`hippo://rules-view` and `hippo://scorecard`. The URIs are frozen; what each document says is
not.

### Environment variables

Twelve names, resolved in `plugin/memory/settings.py` (the bash twin of `HIPPO_DISABLE` is
`hippo_disabled` in `plugin/hooks/_resolve_py.sh`):

- the nine directory overrides: `HIPPO_MEMORY_DIR`, `HIPPO_INDEX_DIR`, `HIPPO_TELEMETRY_DIR`,
  `HIPPO_PENDING_DIR`, `HIPPO_LOCAL_MEMORY_DIR`, `HIPPO_USER_MEMORY_DIR`,
  `HIPPO_PROJECTS_FILE`, `HIPPO_TRUST_FILE`, `HIPPO_CLAUDE_PROJECTS_DIR`;
- `HIPPO_DISABLE`, a comma list of features to switch off. The feature names it accepts are
  frozen too: `dense`, `jit`, `presence`, `floor-nag`, `abstain-gate`, `touch-fastpath`
  (`DISABLE_FEATURES` in `settings.py`). A new feature name is an addition;
- `HIPPO_TRUST_ALL` and `HIPPO_TRUST_NONGIT`.

The `HIPPO_*` prefix stays reserved. Every other `HIPPO_*` name is unfrozen; the list is below.

### The corpus format and its marker

Your committed corpus is never silently reinterpreted. At v2.0.0 that means **corpus format 6**
as [UPGRADING.md](UPGRADING.md) documents it, with the frontmatter conventions in
[CONVENTIONS.md](plugin/assets/CONVENTIONS.md). The format marker `.claude/memory/.format`
holds exactly two keys (`MARKER_KEYS` in `plugin/memory/migrate.py`, read in
`plugin/memory/provenance_format.py`):

- `corpus_format`: an integer that only ever increases. Every increase ships a documented,
  previewed migration. From format 6 the marker is required.
- `cite_derivation`: the citation-derivation version the corpus was stamped with.

Any other key in `.format` is a legacy policy key (see the legacy table).

### The `hippo.json` schema

The committed corpus policy file, `.claude/memory/hippo.json` (path from `policy_file_path` in
`plugin/memory/provenance_format.py`). These thirteen keys are every key the tree reads. A key
name and the value shapes listed here are frozen. An unknown key is ignored, and a value of
the wrong shape reads as if the key were absent. A new key, or a new accepted value, is an
addition. What hippo does with a key's value (ranking effects, output text, doctor lines) is
not frozen.

| Key | Value | Read in | Consent |
|---|---|---|---|
| `volatile_paths` | list of repo-relative paths whose drift is expected | `provenance_format.py` | declares data |
| `floor_lint` | object: `banned_re`, `max_line`, `warn_bytes`, `cap_bytes`, `warn_lines`, `cap_lines`, `section_budgets` | `provenance_format.py`, `gate_cli.py` | declares data |
| `floor_sections` | list of `{"heading": "## …", "types": [...]}` | `provenance_format.py` | declares data |
| `fold_digests` | list of memory filename globs, or one glob | `provenance_format.py` | declares data |
| `attention` | `"calm"` or `"full"` | `attention.py` | declares data |
| `mute` | list of SessionStart signal names, or a comma string | `attention.py` | declares data |
| `acknowledged_findings` | list of `{file, rule, sha256, reason, date}` | `lint_ack.py`, `review.py` | declares data |
| `journals` | list of memory filename globs | `lanes.py` | declares data |
| `budgets` | object of positive integers: `files`, `floor_bytes`, `floor_lines`, `journals`; and `floor_headroom_bytes`, an integer from 0 to under `floor_bytes` that only the floor diet reads | `gate_cli.py`, `floor_diet.py` | declares data |
| `ledgers` | list of at most four `{name, path, id_re, entry_key, status_key}` | `ledger.py` | declares data |
| `write_path` | `"direct"` or `"outbox"` | `outbox.py` | `hippo trust policy --accept` on each machine |
| `gate` | `{"write": "warn" \| "deny" \| "off"}` | `gate_hook.py`, `doctor_checks_gate.py` | `deny` needs `hippo trust policy --accept`, and the committed file at HEAD must say `deny` too |
| `consent` | `{"session_writes": true \| false}` | `session_consent.py` | `hippo trust policy --accept` on each machine |

All paths in the table are under `plugin/memory/`. The consent rule is part of the trust
contract and will not loosen: a key that changes what hippo does on a machine
(`SENSITIVE_KEYS` in `plugin/memory/policy_consent.py`) is honoured only after that machine
accepts its current value.

### `.usage/`

The committed per-user usage summaries, `.claude/memory/.usage/<user>.json`, written only when
you opt in (`hippo soak --record-usage`) and read by curation as the union of every user's file
(`plugin/memory/telemetry_usage.py`). The directory name, the one-file-per-user layout, and the
rule that a later hippo reads every earlier file version are frozen. The files hold memory
names, counts and timestamps, never session ids.

### The `dream:links` grammar

The block dream appends to a memory's body when it applies a link (`plugin/memory/links_graph.py`):

```
<!-- dream:links -->
[[other-memory]] <!-- dream: bridge · pass=p7 · edge=p7-e2 · cofire=0.71 -->
<!-- dream: refines other-memory · pass=p7 · edge=p7-e3 · cofire=0.68 -->
<!-- /dream:links -->
```

Frozen: the two delimiter lines, the two line forms (a wikilink followed by a stamp comment,
and a comment-only `refines` stamp whose edge lives in frontmatter), and the `pass=` and
`edge=` fields, which undo depends on. Other stamp fields are additive.

### `hippo` verbs

`bin/hippo <verb>` is the one door into the engine. The table of record is `CLI_VERBS` in
`plugin/memory/cli_verbs.py`. A frozen verb keeps its name and every flag that has been in two
releases at v2.0.0; a younger flag is not frozen until it has two releases. Output text is never
frozen. Exit status 0 means success and non-zero means failure (for `review --ci`, a non-zero
exit is the gate failing).

| Verb | Class | Since | Why |
|---|---|---|---|
| `recall` | frozen | v1.0 | frozen in v1. `--for-issue` (v1.45.0) is not frozen yet |
| `new` | frozen | v1.0 | frozen in v1 |
| `build-index` | frozen | v1.0 | frozen in v1 |
| `staleness` | frozen | v1.0 | frozen in v1. `--anchors` (v1.45.0) is not frozen yet |
| `mcp` | frozen | v1.0 | frozen in v1; the plugin's MCP server entry |
| `sleep` | frozen | v1.x | frozen in v1; schedules installed on users' machines call it |
| `review` | frozen | v1.x | frozen in v1; `--ci` is the memory-diff CI gate |
| `doctor` | frozen | v1.42.0 | the health check behind `/hippo:doctor`; docs and skills name it |
| `tend` | frozen | v1.42.0 | the one maintenance queue behind `/hippo:tend` |
| `setup` | frozen | v1.42.0 | machine and project setup behind `/hippo:setup`. `--warm` waits on the warm-recall default decision and `--conventions` (v1.45.0) is young, so neither flag is frozen yet |
| `trust` | frozen | v1.42.0 | corpus consent; `hippo trust policy --accept` is the consent path the outbox and the gate depend on |
| `inspect` | frozen | v1.42.0 | the readable recall listing and receipts, the `inspect` tool's terminal twin |
| `dream` | frozen | v1.42.0 | the link pass. The deprecated tier flags are removed at v2.0.0 (see the legacy table) |
| `gate` | frozen | v1.43.0 | `hippo gate --install` writes a pre-commit hook into your repository that calls this verb, so a rename would break installed hooks |
| `migrate` | frozen | v1.42.0 | the v2.0.0 corpus migration runs through it. `--check` is frozen; the apply mode is new at v2.0.0 and its flags are not frozen yet |
| `ship` | not yet | v1.44.0 | two releases at the cut, opt-in, usage not yet measured (open question 1) |
| `adopt` | not yet | v1.42.0 | the native-memory coexistence contract applied at v2.0.0 changes what it does |
| `capture` | not yet | v1.42.0 | a per-queue view that `tend` absorbed; a candidate for removal, not a freeze |
| `reconsolidate` | not yet | v1.42.0 | the same |
| `brief` | not yet | v1.42.0 | the same |
| `resolve` | not yet | v1.42.0 | the same |
| `links` | not yet | v1.42.0 | a maintenance diagnostic; its output is for people |
| `lint-links` | not yet | v1.42.0 | the same |
| `lint-floor` | not yet | v1.42.0 | the same; `gate` covers the floor checks a script depends on |
| `publish` | not yet | v1.42.0 | a step the `share` skill drives; the skill name is the contract |
| `promote-rule` | not yet | v1.42.0 | the same |
| `promote-scan` | not yet | v1.42.0 | the same |
| `import` | not yet | v1.42.0 | the same |
| `recall-diff` | not yet | v1.42.0 | a read-only diagnostic; `review` covers the CI case |
| `blast-radius` | not yet | v1.42.0 | a read-only diagnostic |
| `archive` | not yet | v1.42.0 | a corpus edit the `tend` flow drives |
| `provenance` | not yet | v1.42.0 | citation stamping; its re-derive mode leaves once the index derives citations |
| `registry` | not yet | v1.42.0 | a machine listing for people |
| `census` | not yet | v1.42.0 | the same |
| `secrets` | not yet | v1.42.0 | the scanner the `doctor` tool serves |
| `eval` | not yet | v1.42.0 | a measurement tool; its arms move with retrieval work |
| `soak` | not yet | v1.42.0 | the same |
| `env` | not yet | v1.42.0 | plumbing the skills use to find the plugin's paths |
| `session-start` | internal | v1.42.0 | the SessionStart hook's entry; may change in any release |
| `outcome` | internal | v1.42.0 | the PostToolUse hook's entry; may change in any release |
| `pre-tool` | internal | v1.43.0 | the PreToolUse hook's entry; may change in any release |

`hippo help` (and `hippo help --all`) is part of the door itself (`plugin/memory/cli.py`).

## Explicitly unfrozen

These may change in any release without a major bump. Do not build on them.

- **Derived caches and their schemas**: the recall index and its `schema_version`
  (`plugin/memory/build_index.py`), the link cache `links.json` (`plugin/memory/links_cache.py`),
  the folded-sections sub-index `sections.json` and its `sections.<id>.npy` matrix
  (`plugin/memory/index_sections.py`), the citation binding beside the index,
  `citation_binding.json` (`plugin/memory/citation_binding.py`), the ledger cache `ledger.json`
  (`plugin/memory/ledger.py`), the staleness cache, and the tend queue files. All are
  gitignored and rebuilt from source; a schema bump is a re-index, not a corpus migration.
- **Telemetry schemas**: everything under `.hippo/telemetry/` (`plugin/memory/telemetry*.py`):
  the recall ledger, daily rollups, the usage spool, their field names (`admitted`,
  `journal_admitted`, `served_rows` and the rest) and their retention.
- **Drop-reason codes**: the reasons telemetry records for a memory recall did not serve
  (`display_overflow`, `lane_share` and the rest, in `plugin/memory/recall_hook.py` and
  `plugin/memory/recall_count.py`).
- **The outbox directory layout**: `.claude/memory.outbox/`, its `.outbox.json` manifest and
  the `.floor-base` file (`plugin/memory/outbox.py`), plus `HIPPO_OUTBOX_DIR`.
- **`hippo ship`'s output**: its text, JSON, exit codes, the `hippo/ship-<time>` branch name,
  the commit message and the manifest's `shipped` stamp (`plugin/memory/ship.py`).
- **The ledger join's behavior**: the row suffix it adds, `hippo recall --for-issue` output,
  and the issue admission of a journal. The `ledgers` key itself is frozen above.
- **The folded-section fields on a recall result** (`digest`, `line`, `section`, and `file` on
  the recall tool's structured hits; `plugin/memory/recall_sections.py`).
- **Recall output text and order**: recall is a ranker, tuned continuously.
- **doctor's wording**: check names, their order, and every line doctor or SessionStart prints
  (`plugin/memory/doctor.py`, `plugin/memory/session_start*.py`).
- **Everything about an MCP tool except its name and required inputs**: optional inputs, the
  values an `action` or `kind` input accepts, descriptions, `annotations`, `outputSchema`,
  result payloads, and the order `tools/list` returns. The `recall_hook` tool is internal and
  unfrozen in full (`plugin/memory/recall_warm.py`).
- **tend's queue kinds and verdicts** (`KINDS` in `plugin/memory/tend_queue.py`, `VERDICTS` in
  `plugin/memory/tend.py`): the kinds `trust`, `baseline`, `contradiction`, `merge`, `capture`,
  `reverify`, `promote`, `link`, `floor`, `derivation`, `rule`, and their verdicts, `adopt`,
  `demote` and `write` included (open question 4).
- **Flags younger than two releases** on any verb, and every flag of a verb that is not frozen.
- **Hook entries**: which Claude Code events hippo registers (`plugin/hooks/hooks.json`), the
  scripts, and what the hooks print. That every hook exits 0 is an invariant, below.
- **`CONVENTIONS.local.md`**, the corpus's own rules file beside its `CONVENTIONS.md` copy
  (`plugin/memory/conventions.py`), and the shipped `CONVENTIONS.md` asset's text, which carries
  its own version stamp (open question 3).
- **The plugin's options** in `/config` (`userConfig` in `plugin/.claude-plugin/plugin.json`):
  `calm_session_start`, `capture_llm`, `dream_contradictions`, `dream_generative`, `llm_model`,
  `llm_api_key` (open question 2).
- **The Python API**: importing `memory.*` directly. hippo is consumed as a plugin (skills,
  hooks, MCP, `bin/hippo`), not as a library.
- **Every `HIPPO_*` variable outside the frozen twelve**, in four groups:
  - *configuration overrides*, readable but not frozen: `HIPPO_EMBED_MODEL`,
    `HIPPO_MCP_MAX_MESSAGE_CHARS`, `HIPPO_TEA5_OPT_IN`, `HIPPO_SALIENCE`, `HIPPO_DENSE_FLOOR`,
    `HIPPO_DUP_THRESHOLD`, `HIPPO_SLEEP_TIER_A`, `HIPPO_ATTENTION`, `HIPPO_MUTE`,
    `HIPPO_CAPTURE_LLM`, `HIPPO_CAPTURE_LLM_TIMEOUT`, `HIPPO_DREAM_APPLY`,
    `HIPPO_DREAM_CONTRADICTIONS`, `HIPPO_DREAM_CONTRA_MAX_PAIRS`,
    `HIPPO_DREAM_CONTRA_MIN_COFIRE`, `HIPPO_DREAM_LLM_TIMEOUT`, `HIPPO_LLM_API_KEY`,
    `HIPPO_LLM_BASE_URL`, `HIPPO_LLM_MODEL`, `HIPPO_LLM_PROVIDER`, `HIPPO_RERANK`,
    `HIPPO_RERANK_TIMEOUT`, `HIPPO_REFRESH_TIMEOUT`, `HIPPO_DENSE_TIMEOUT`,
    `HIPPO_TELEMETRY_MAX_BYTES`, `HIPPO_CORPUS_ROOT`;
  - *ranking and tuning knobs*, which move with retrieval work: `HIPPO_KNEE_RATIO`,
    `HIPPO_MMR_LAMBDA`, `HIPPO_BODY_RRF_WEIGHT`, `HIPPO_PIN_BOOST`, `HIPPO_DENSE_FLOOR_BAND`,
    `HIPPO_LANE_BEHAVIOR_SHARE`, `HIPPO_HUB_DAMPING`, `HIPPO_QUIET_ARMING`,
    `HIPPO_SIGNAL_COUNT`, `HIPPO_COUNT_BAND`, `HIPPO_COUNT_WIDE`, `HIPPO_JOURNAL_SIGNAL`,
    `HIPPO_HOOK_COMPACT`, `HIPPO_SECTION_ROWS`,
    `HIPPO_INDEX_BINDING`, `HIPPO_ARMING_EXEMPT_TYPES`, `HIPPO_AUTHORITATIVE_BOOST`,
    `HIPPO_COOLDOWN_TURNS`, `HIPPO_DRAFT_PENALTY`, `HIPPO_EMBED_CHUNK_SIZE`,
    `HIPPO_GRAPH_SEEDS`, `HIPPO_JIT_CITED_ROWS_CAP`, `HIPPO_MAX_SNIPPET_CHARS`,
    `HIPPO_OUTCOME_PRIOR`, `HIPPO_RECENT_DAYS`, `HIPPO_RULES_RECALL_FLOOR`,
    `HIPPO_SNIPPET_SCORE_BAND`, the `HIPPO_INTENT_*` family (`HIPPO_INTENT_DENSE_DENSITY`,
    `HIPPO_INTENT_LEAN_WEIGHT`, `HIPPO_INTENT_LEXICAL_DENSITY`, `HIPPO_INTENT_MIN_TOKENS`) and
    the `HIPPO_RESCUE_*` family (`HIPPO_RESCUE_MIN_TOKENS`, `HIPPO_RESCUE_TURNS`), plus dream's
    unprefixed knobs `DREAM_COFIRE_THETA`, `DREAM_MAX_APPLY_PER_PASS`, `DREAM_AGE_SESSIONS`,
    `DREAM_REWARD_WEIGHT`, `DREAM_PROBE_K`, `DREAM_MAX_SEEDS`, `DREAM_MAX_OUT_DEGREE`,
    `DREAM_DEDUP_JACCARD`, `DREAM_MAX_GENERATE_PER_PASS`, `DREAM_DRAFT_HORIZON`,
    `DREAM_SCHEMA_MIN_CLUSTER` and `DREAM_SCHEMA_MAX_CLUSTER` (open question 6);
  - *test and relocation overrides*: `HIPPO_OUTBOX_DIR`, `HIPPO_CLAUDE_SETTINGS`,
    `HIPPO_USAGE_USER`;
  - *set by hippo itself*, never by you: `HIPPO_SURFACE` and `HIPPO_HOOK_T0_MS` (the hooks),
    `HIPPO_DERIVED_DIR` (the bash resolver), `HIPPO_DREAM` (the dream on/off measurement arm).

  Names hippo reads that belong to Claude Code or to a library (`CLAUDE_PLUGIN_ROOT`,
  `CLAUDE_PLUGIN_OPTION_*`, `CLAUDE_CODE_ENTRYPOINT`, `FASTEMBED_CACHE_PATH`, `HF_HUB_OFFLINE`)
  are not hippo's to freeze.

## Promises that do not loosen

These are behaviors, not names, and no release changes them:

- No LLM and no network call on the recall path. Hooks always exit 0. No hook blocks a write
  unless the corpus's own committed policy asks for it and the machine accepted that.
- hippo never writes to the corpus on its own. Every corpus change is a verb you ran or a
  verdict you gave.
- A setting that moves where writes land, or lets a hook refuse a write, is honoured only
  after one acceptance per machine (`hippo trust policy --accept`).
- `hippo ship` never fetches, never pushes, never touches HEAD, the index or the working tree,
  and never carries a deletion.
- The ledger join never calls the network, never writes to a tracker or a memory file, and
  reads nothing for an untrusted corpus.
- hippo never overwrites `CONVENTIONS.local.md`; `hippo setup --conventions --yes` only appends
  to it.

## Dropped: the position freeze

v1 enforced a freeze that STABILITY.md never stated: tool names, shapes **and positions**. A
comment in `plugin/memory/mcp_schemas.py` (above the incident-response tools) says the policy
freezes all three, and `test_tools_list_exposes_frozen_five_plus_setup_tools` in
`tests/test_mcp_server.py` asserts that the first five names `tools/list` returns are the v1.0
five, in their shipped order. Every later tool was appended at the end to keep that true.

v2 drops it. What replaces it: the ten tool names and their required inputs are frozen and are
checked as a set; the order `tools/list` returns is not a contract; a tool's shape beyond its
required inputs is unfrozen. At the cut the positional assertion becomes a set assertion over
the names and required inputs above.

## Every legacy name doctor sees

The deprecated spellings the tree still reads, derived from the tables in the code and from
what doctor's own checks enumerate. `tests/test_stability_v2_draft.py` fails when a legacy
name in the tree is missing here. Unless a row says otherwise, each name's fate at v2.0.0 is
the rule above: removed if the usage count shows no use in the last 14 days, otherwise one
dated extension.

### Old MCP tool names

Read in `DEPRECATED` in `plugin/memory/mcp_schemas_v2.py` and served by
`plugin/memory/mcp_server.py`; every reply says the name is going. Doctor reports use: its
permission-rule check names each allow, ask or deny rule that names one
(`plugin/memory/doctor_checks_platform.py`), and its surface usage line counts every call.

| Old name | Use instead |
|---|---|
| `traverse` | `inspect` with `action='traverse'` |
| `why` | `inspect` with `action='why'` |
| `decision_history` | `inspect` with `action='history'` |
| `blast_radius` | `inspect` with `action='blast_radius'` |
| `bootstrap` | `setup` with `action='bootstrap'` |
| `init` | `setup` with `action='init'` |
| `build_index` | `setup` with `action='build_index'` |
| `trust_corpus` | `trust` with `action='review'`, then `'grant'` |
| `untrust` | `trust` with `action='revoke'` |
| `secrets_scan` | `doctor` with `action='secrets_scan'` |
| `audit` | `doctor` with `action='audit'` |
| `capture` | `tend` with `kind='capture'` |
| `reconsolidate` | `tend` with `kind='reverify'` |
| `resolve` | `tend` with `kind='contradiction'` |
| `rederive` | `tend` with `kind='derivation'` |
| `heal_baselines` | `tend` with `kind='baseline'` |
| `co_recall_proposals` | `tend` with `action='link_proposals'` |
| `abstention_fixtures` | `tend` with `action='fixtures'` |
| `interview` | `tend` with `action='interview'` |
| `pack_extract` | `share` with `action='pack_extract'` |
| `pack_install_plan` | `share` with `action='pack_install_plan'` |
| `pack_install_item` | `share` with `action='pack_install_item'` |
| `pack_update_plan` | `share` with `action='pack_update_plan'` |
| `pack_update_item` | `share` with `action='pack_update_item'` |

### Old skill names

Each is a one-line `SKILL.md` that routes into its v2 skill (`plugin/skills/<name>/SKILL.md`,
the route rows in `plugin/memory/surfaces.py`). Doctor reports use: its surface usage line
counts each one's preflight.

| Old name | Use instead |
|---|---|
| `bootstrap` | `/hippo:setup` |
| `init` | `/hippo:setup` |
| `why` | `/hippo:recall` |
| `consolidate` | `/hippo:tend` |
| `resolve` | `/hippo:tend` |
| `audit` | `/hippo:doctor` |
| `pack` | `/hippo:share` |
| `promote` | `/hippo:share` |
| `promote-rule` | `/hippo:share` |
| `publish` | `/hippo:share` |
| `export-agents` | `/hippo:share` |
| `import` | `/hippo:share` |

### Old environment names

Read in `plugin/memory/settings.py` (`DISABLE_FEATURES`, `LEGACY_ENV`, `env_first`), in
`plugin/memory/dream_config.py`, and in `plugin/hooks/_resolve_py.sh` for the kill switches.
Doctor reports use: its settings check (`plugin/memory/doctor_checks_settings.py`) names each
one that is set, with its new spelling, and SessionStart counts each once per session.

| Old name | Use instead |
|---|---|
| `HIPPO_DISABLE_DENSE` | `HIPPO_DISABLE=dense` |
| `HIPPO_DISABLE_JIT` | `HIPPO_DISABLE=jit` |
| `HIPPO_DISABLE_PRESENCE` | `HIPPO_DISABLE=presence` |
| `HIPPO_DISABLE_FLOOR_NAG` | `HIPPO_DISABLE=floor-nag` |
| `HIPPO_DISABLE_ABSTAIN_GATE` | `HIPPO_DISABLE=abstain-gate` |
| `HIPPO_DISABLE_TOUCH_FASTPATH` | `HIPPO_DISABLE=touch-fastpath` |
| `DREAM_CONTRA_MAX_PAIRS` | `HIPPO_DREAM_CONTRA_MAX_PAIRS` |
| `DREAM_CONTRA_MIN_COFIRE` | `HIPPO_DREAM_CONTRA_MIN_COFIRE` |

### Policy keys still read from `.format`

Read in `read_policy_key` in `plugin/memory/provenance_format.py`: `hippo.json` wins, and the
same key in `.format` still answers when `hippo.json` does not set it. Doctor reports use: its
settings check names every key in `.format` other than the two marker keys
(`legacy_policy_keys`), and SessionStart counts each once per session. At v2.0.0 the format-6
migration moves each one into `hippo.json`, and the `.format` reader is removed.

| Old home | Use instead |
|---|---|
| `.format` `volatile_paths` | `hippo.json` `volatile_paths` |
| `.format` `floor_lint` | `hippo.json` `floor_lint` |
| `.format` `floor_sections` | `hippo.json` `floor_sections` |
| `.format` `fold_digests` | `hippo.json` `fold_digests` |
| `.format` `attention` | `hippo.json` `attention` |
| `.format` `mute` | `hippo.json` `mute` |

### Keys of the old `hippo-llm.json` file

The plaintext machine file `~/.claude/hippo-llm.json` (its path can be moved with
`HIPPO_LLM_CONFIG`), read in `plugin/memory/settings.py` (`LEGACY_LLM_FILE_KEYS`) and
`plugin/memory/llm_client.py`. Doctor reports use: its settings check names each key in use,
names a plaintext API key, and says what a scheduled run needs before the file stops being
read. SessionStart counts each key once per session. The file and `HIPPO_LLM_CONFIG` go with
its keys.

| Old key | Use instead |
|---|---|
| `api_key` | the plugin's `llm_api_key` option (kept in the system keychain) |
| `model` | the plugin's `llm_model` option |
| `capture_triage` | the plugin's `capture_llm` option |
| `dream_contradictions` | the plugin's `dream_contradictions` option |
| `provider` | `HIPPO_LLM_PROVIDER` |
| `base_url` | `HIPPO_LLM_BASE_URL` |
| `capture_timeout_s` | `HIPPO_CAPTURE_LLM_TIMEOUT` |
| `dream_timeout_s` | `HIPPO_DREAM_LLM_TIMEOUT` |
| `contra_max_pairs` | `HIPPO_DREAM_CONTRA_MAX_PAIRS` |
| `contra_min_cofire` | `HIPPO_DREAM_CONTRA_MIN_COFIRE` |

### CLI names that redirect to a skill

Read in `SKILL_REDIRECTS` in `plugin/memory/cli_verbs.py`: `hippo <name>` exits 1 and names the
skill to run instead. Until v1.46 each named the old skill of the same name, and nothing counted
its use. From v1.46 each names the live skill and its flow, and a typed redirect counts as one
`cli` use like any verb, so doctor's per-verb counts give the removal rule a number to read
(open question 5 was asked before that).

| Name | Says to run |
|---|---|
| `init` | `/hippo:setup`, its "Init" flow |
| `bootstrap` | `/hippo:setup`, its "Bootstrap" flow |
| `audit` | `/hippo:doctor`, its "Content audit" flow |

### Old locations

Directories hippo used before v1.42.4 and still reads or names. Doctor reports them: its
derived-directory, worktree-copy and snapshot checks (`plugin/memory/doctor_checks_worktree.py`)
name each one left behind and how to clear it. Nothing counts their use (open question 5).

| Old location | Now | Read in |
|---|---|---|
| `.claude/.memory-index` | `.hippo/index` | `LEGACY_DIRNAMES` in `plugin/memory/derived_dirs.py` |
| `.claude/.memory-telemetry` | `.hippo/telemetry` | the same |
| `.claude/.memory-pending` | `.hippo/pending` | the same |
| `memory.pre-cite*` beside the corpus | `.hippo/snapshots` | `LEGACY_PREFIX` in `plugin/memory/provenance_snapshot.py` |
| `.hippo-snapshots` | `.hippo/snapshots` | `V1423_DIRNAME` in `plugin/memory/provenance_snapshot.py` |

SessionStart moves the three derived directories into `.hippo/` on its own; the reader of the
old location is what v2.0.0 removes.

### Frontmatter spellings format 6 renames

Read in `LEGACY_KEYS` in `plugin/memory/migrate.py`. `derives-from` is today's spelling of the
derivation edge, so it is not deprecated yet. Doctor reports it: its migration line counts the
files that carry it. Its fate: the format-6 migration rewrites it, in one previewed and
revertable change.

| Spelling | Format 6 spelling |
|---|---|
| `derives-from` | `derives_from` |

### Dream tiers deprecated in v1.45.0

Three dream tiers measured no recall gain on three real corpora and are off by default from
v1.45.0: they still run when named, print one line saying they are going, and are removed at
v2.0.0 on that measurement, not on a usage count. Read in `deprecated_tiers` in
`plugin/memory/dream_deprecated.py`. Doctor reports them: its dream-tiers check names each
deprecated tier and warns when one is turned on.

| Tier | Its names |
|---|---|
| `reverse_replay` | `HIPPO_DREAM_REVERSE_REPLAY` |
| `generative` | `--generate`, `--sweep-drafts`, `--archive-draft`, `--prospective`; the dream tool's `generate`, `sweep_drafts`, `archive_draft` and `prospective` actions; `HIPPO_DREAM_GENERATIVE`; the plugin's `dream_generative` option |
| `deparasite` | `--deparasite`, `--retract`, `--dedup-merge`; the dream tool's `deparasite` and `dedup_merge` actions |

The link pass, the `dream:links` grammar, `dream-ledger.jsonl` and `HIPPO_SLEEP_TIER_A` are
unchanged.

### Legacy readers

Old record shapes rather than names. Doctor names each one it finds, and v2.0.0 removes the
reader:

- a trust record without a fingerprint, from before trust baselines (`plugin/memory/trust.py`;
  doctor's trust check says quarantine is off for it, and `hippo trust grant` replaces it);
- a project link under the old mis-encoded directory name (`plugin/memory/doctor_checks_env.py`);
- a corpus with no format marker, read as an implicit format 1, and a corpus with no
  citation-derivation stamp, read as derivation 1 (`plugin/memory/provenance_format.py`;
  doctor's format checks name the next step).

## Open questions for the owner

1. **Is `hippo ship` frozen at v2.0.0?** It will have two releases at the cut, it is opt-in,
   and no usage count has been read for it. *Recommendation:* keep it unfrozen through v2.0.0
   and decide at v2.1 on its usage count. `write_path` stays frozen in the `hippo.json` schema
   and the outbox layout stays unfrozen either way.
2. **Are the plugin's six option keys frozen?** A renamed option silently loses a saved value,
   the API key in the system keychain included. *Recommendation:* freeze the six key names
   (not their defaults) at v2.0.0, except `dream_generative`, which goes with its tier.
3. **Do the v1.45.0 additions wait for their two releases?** That is `recall --for-issue`,
   `setup --conventions`, `staleness --anchors`, `CONVENTIONS.local.md`, and the `ledgers` key,
   which this draft freezes with the rest of the `hippo.json` schema. *Recommendation:* keep the
   three flags and the local file's name unfrozen until v2.1, and freeze `ledgers` now: a
   committed file declares it, so a rename would break corpora, while its behavior stays
   unfrozen.
4. **Are an MCP tool's `action` and `kind` values, and tend's kinds and verdicts, frozen?**
   The skills name them, and `tend`'s `promote` and `rule` kinds with the `adopt`, `demote`
   and `write` verdicts arrived in v1.45.0. *Recommendation:* no at v2.0.0. Freeze names and
   required inputs only, and consider the values that have two releases and a usage count at
   v2.1.
5. **What removes a legacy name nothing counts?** The three CLI redirects and the old locations
   have no usage count, so the 14-day rule cannot apply to them. *Recommendation:* remove the
   CLI redirects at v2.0.0 with the skill names they point at, and remove the old-location
   readers at v2.0.0, since SessionStart has moved them since v1.42.4 and doctor names any
   leftover a minor ahead.
6. **Should dream's unprefixed `DREAM_*` knobs take the `HIPPO_` prefix?** Twelve tuning
   variables outside the reserved prefix are read today. *Recommendation:* rename them to
   `HIPPO_DREAM_*` in a v2.x minor with a one-window alias, or drop each one the reduced link
   pass no longer uses; do not freeze either spelling.
7. **Is `migrate` frozen with an apply mode that is new at v2.0.0?** *Recommendation:* freeze
   the verb and `--check`, which have been in the field since v1.42.0, and leave the apply
   flags unfrozen until they have two releases.
