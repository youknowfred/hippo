# Stability & compatibility policy

Starting at **v1.0.0**, hippo commits to a frozen compatibility surface and a support policy. This
is what "1.0" means here: the things below won't change under you without a major-version bump or a
deprecation window. (Resolves ROADMAP.v1 **OQ-8**.)

The draft of the v2 policy, [STABILITY.v2.md](STABILITY.v2.md), takes effect at v2.0.0 and replaces this file then; until then, this file is the contract.

## Versioning

hippo follows semantic versioning. The version lives in lockstep in `plugin/.claude-plugin/plugin.json`
and `.claude-plugin/marketplace.json` (a tag-time check enforces the two match the CHANGELOG). A
**major** bump signals a change to the frozen surface below; **minor** adds capability
back-compatibly; **patch** fixes bugs.

## The frozen surface (v1.0)

These are stable. A rename or removal of anything here is a **major-version bump**, or it ships with
a **deprecation window** (the old name keeps working, with a warning, for at least one minor
release). New *additions* alongside them are minor, non-breaking changes.

- **The `/hippo:*` skill namespace** — the command names users type: `bootstrap`, `init`, `new`,
  `recall`, `why`, `doctor`, `audit`, `consolidate`, `resolve`, `promote`, `promote-rule`, `pack`,
  `export-agents`, `import`, `remove`. (New skills may be added; existing ones won't be renamed or
  removed silently.) v1.42.0 opened this list's deprecation window: the nine v2 verbs (`setup`,
  `new`, `recall`, `tend`, `doctor`, `share`, `review`, `dream`, `remove`) absorbed the other
  names, each of which now routes into its verb, keeps working through v1.43, and is removed in
  v2.0.
- **The `bin/hippo` CLI subcommands** — `recall`, `new`, `build-index`, `staleness`, `mcp`,
  `sleep` (added in T15 SLP as a minor, non-breaking addition), `review` (added in T12 CLB —
  the corpus review packet; its `--ci` mode is the single memory-diff CI gate).
- **The MCP tool names** — `recall`, `new_memory`, `traverse`, `why`, `decision_history` (served by
  `bin/hippo mcp`). v1.42.0 opened their deprecation window: `traverse`, `why` and
  `decision_history` now route through `inspect`, keep working unchanged through v1.43, and are
  removed in v2.0 (each call says so).
- **The `HIPPO_*` environment-variable namespace**, and specifically these documented operational
  variables: `HIPPO_MEMORY_DIR`, `HIPPO_INDEX_DIR`, `HIPPO_TELEMETRY_DIR`, `HIPPO_PENDING_DIR`,
  `HIPPO_LOCAL_MEMORY_DIR`, `HIPPO_USER_MEMORY_DIR`, `HIPPO_PROJECTS_FILE`, `HIPPO_TRUST_FILE`,
  `HIPPO_CLAUDE_PROJECTS_DIR` (added in T19 HYG: overrides the harness-owned
  `~/.claude/projects` symlink base for hermetic tests/relocation — the same class as
  `HIPPO_TRUST_FILE`),
  `HIPPO_DISABLE_DENSE`, `HIPPO_TRUST_ALL`, `HIPPO_TRUST_NONGIT`, `HIPPO_EMBED_MODEL`,
  `HIPPO_MCP_MAX_MESSAGE_CHARS`, `HIPPO_TEA5_OPT_IN`, `HIPPO_SALIENCE`, `HIPPO_DENSE_FLOOR`,
  `HIPPO_DUP_THRESHOLD`, `HIPPO_DISABLE_JIT` (added in T16 JIT: kills the default-on
  first-touch reminder lane + its touch-grain telemetry, restoring pre-T16 PostToolUse
  behavior byte-for-byte), `HIPPO_DISABLE_FLOOR_NAG` (added in FLR-1: kills the
  once-per-session PostToolUse floor-governance nag on MEMORY.md edits; the SessionStart
  floor line and the doctor check are state reports, not interjections, and stay on),
  `HIPPO_DISABLE_PRESENCE` (added in T18 FLT: kills the whole
  fleet lane — no per-session presence doc, no SessionStart fleet line, no moved-tree
  tripwire, no worktree-first nudge; docs already on disk age out via their TTL. Honest
  coverage note while ON: the lane rides PostToolUse, which sees FILE-TOOL acts only —
  Bash-mediated mutations such as `git commit`/`checkout`, pytest runs, and scripts are
  invisible to it, so the shared-tree cwd-trap class is covered only for file-tool
  mutations), `HIPPO_DISABLE_ABSTAIN_GATE` (added in v1.41.0 HOT-2: turns off the
  corroboration gate, restoring the earlier rule where recall abstains only when every
  lane comes up empty), `HIPPO_DISABLE_TOUCH_FASTPATH` (added in v1.41.0 HOT-5: every
  PostToolUse file touch spawns Python again, the pre-HOT-5 hook path),
  `HIPPO_SLEEP_TIER_A` (added in T15 SLP: the opt-in that lets a
  SCHEDULED sleep pass apply capped, reversible Tier-A dream edges — default OFF, and OFF
  keeps the runner's zero-write guarantee byte-for-byte). These keep their names and meanings.
- **The v2 environment set (from v1.42).** The frozen environment set shrinks to twelve names:
  the nine directory overrides above (`HIPPO_MEMORY_DIR`, `HIPPO_INDEX_DIR`,
  `HIPPO_TELEMETRY_DIR`, `HIPPO_PENDING_DIR`, `HIPPO_LOCAL_MEMORY_DIR`, `HIPPO_USER_MEMORY_DIR`,
  `HIPPO_PROJECTS_FILE`, `HIPPO_TRUST_FILE`, `HIPPO_CLAUDE_PROJECTS_DIR`),
  `HIPPO_DISABLE=<comma list>` (features `dense`, `jit`, `presence`, `floor-nag`,
  `abstain-gate`, `touch-fastpath`), `HIPPO_TRUST_ALL` and `HIPPO_TRUST_NONGIT`. The six
  `HIPPO_DISABLE_<FEATURE>` names are deprecated spellings of that list, and
  `DREAM_CONTRA_MAX_PAIRS` / `DREAM_CONTRA_MIN_COFIRE` of their `HIPPO_`-prefixed names. The
  other names in the list above (`HIPPO_EMBED_MODEL`, `HIPPO_MCP_MAX_MESSAGE_CHARS`,
  `HIPPO_TEA5_OPT_IN`, `HIPPO_SALIENCE`, `HIPPO_DENSE_FLOOR`, `HIPPO_DUP_THRESHOLD`,
  `HIPPO_SLEEP_TIER_A`) stay readable as overrides but become configuration, outside the
  frozen set. Settings that are not environment variables moved too: corpus policy to the
  committed `.claude/memory/hippo.json` (its keys used to sit in `.format`) and machine
  settings to the plugin's options (they used to sit in `~/.claude/hippo-llm.json`). An
  environment variable wins over a plugin option, which wins over `hippo.json`, which wins
  over the old location. **Deprecation window:** every old name keeps working unchanged
  through v1.43, `/hippo:doctor` names each one it sees with its new spelling, and each use is
  counted; the window is two minors (v1.42, v1.43) and the old names are removed at v2.0.
- **The committed on-disk corpus format** — `.claude/memory/.format`'s `corpus_format` (currently
  **5**) and the memory-file frontmatter conventions ([CONVENTIONS.md](plugin/assets/CONVENTIONS.md)).
  The format version only ever increases, and every increase ships a documented migration
  ([UPGRADING.md](UPGRADING.md)) — your committed markdown corpus is never silently reinterpreted.

## Explicitly NOT frozen

These may change at any release without a major bump — do not build on them:

- **Derived caches and their schemas** — the recall index (`schema_version`, currently 8), the link
  cache (`links.json`, currently 6), the citation binding beside the index
  (`citation_binding.json`), the staleness cache, and the telemetry ledgers under
  `.hippo/` (`.claude/.memory-*` before v1.42.4). They are
  gitignored, rebuildable-from-source artifacts; hippo may bump their schema and rebuild them freely.
  (A `schema_version` bump is a re-index, not a corpus migration — the CHANGELOG's `re-bootstrap`
  flag tells you when deps or the index change.) The folded-sections sub-index (`sections.json`
  and its `sections.<id>.npy` matrix, beside the main index, from v1.45) is in this class, as is
  the `sections` key it adds to the main manifest; a corpus with no digest section has neither.
- **The folded-section fields on a recall result** — a row for a `### <slug>` section of a
  fold digest carries `digest` (the digest's stem), `line` (its heading's 1-based line) and
  `section: true` on the result dict, and `file`/`line`/`section` on the recall tool's
  structured hits; explicit recall lists such rows after the memory rows, which are unchanged.
  Additions, unfrozen until STABILITY v2 classifies them.
- **Internal ranking-tuning knobs** — the undocumented `HIPPO_*` variables that tune the ranker
  (e.g. `HIPPO_KNEE_RATIO`, `HIPPO_MMR_LAMBDA`, `HIPPO_BODY_RRF_WEIGHT`, `HIPPO_PIN_BOOST`,
  `HIPPO_DENSE_FLOOR_BAND` (the soft band under the dense floor; 0 turns it off),
  `HIPPO_LANE_BEHAVIOR_SHARE` (the behavior lane's share of the prompt hook's slots),
  `HIPPO_HUB_DAMPING` (hub damping in graph expansion; 0 turns it off),
  `HIPPO_QUIET_ARMING` (evidence anchors; 0 turns them off — they are report-only, so
  arming is unchanged either way until a replay of past verdicts clears their gate),
  `HIPPO_SIGNAL_COUNT` (the prompt hook's count-by-evidence rule; 0 turns it off) with
  `HIPPO_COUNT_BAND` and `HIPPO_COUNT_WIDE` (its two cosine margins),
  `HIPPO_JOURNAL_SIGNAL` (a journal that leads the fused candidates is served beside the
  prompt's rows on a corpus that declares `journals`; 0 turns it off),
  `HIPPO_HOOK_COMPACT` (the prompt hook's shorter block: later rows' descriptions cut at a
  word boundary, fewer names on the collapse lines, a shorter repeat header; 0 restores the
  longer block),
  `HIPPO_SECTION_ROWS` (how many folded-section rows explicit recall lists after the memories;
  0 turns them off),
  `HIPPO_INDEX_BINDING` (readers prefer the citation binding the index derives; 0 means
  frontmatter `cited_paths` only and no sidecar write), the
  `HIPPO_INTENT_*` and `HIPPO_RESCUE_*` families). They exist for experimentation and move with
  retrieval work; the `HIPPO_*` prefix is reserved, but these specific knobs are not a contract.
- **The Python API** — importing `memory.*` internals directly. hippo is consumed as a Claude Code
  plugin (skills + hooks + MCP + `bin/hippo`), not as a library; module-level functions and
  signatures may change. Depend on the CLI and MCP surfaces, not the Python symbols.
- **`hippo` verbs beyond the seven above** — v1.42.0 made `bin/hippo` the one engine entry
  and gave every engine command a verb (`hippo help --all` lists them). The seven frozen
  subcommands keep their names and behavior; the rest stay unfrozen until STABILITY v2
  classifies them.
- **The v2 MCP toolset and its shapes** — `inspect`, `tend`, `setup`, `trust`, `share` and
  `review` (added in v1.42.0), the internal `recall_hook`, every tool's `annotations` and
  `outputSchema`, and the order `tools/list` returns. STABILITY v2 freezes the v2 names at v2.0;
  until then they are additions, not promises.
- **`hippo ship`** — the verb, its flags (`--yes`, `--json`, `--base`, `--branch`, `--drop`,
  `--mine`), its output and exit codes, the `hippo/ship-<time>` branch name, the commit message,
  the manifest's `shipped` stamp and the outbox's `.floor-base` file. A CLI-only addition (no MCP
  tool); STABILITY v2 classifies it. That it never fetches, never pushes, never touches HEAD,
  the index or the working tree, and never carries a deletion will not loosen.
- **The `recall_hook` MCP tool** — internal: the opt-in warm-recall hook (`hippo setup --warm`)
  calls it, never a model or a skill. Its inputs, its reply and the handshake files it shares with
  the recall hook may change in any release; the `hippo setup` verb is unfrozen too.
- **The ledger join** — the `ledgers` key in `hippo.json`, its line-window contract, the
  `· <ID> <status> (snapshot <date>)` row suffix, the derived `.hippo/index/ledger.json`
  (its schema, its `prev` map and its id-to-journals map), the `build-index` line, the doctor
  line, `hippo recall --for-issue` (its output, JSON and exit codes), the issue admission of a
  journal and the `admitted` / `journal_admitted` telemetry fields (their values name the
  admission: `named`, `issue`, and from v1.46 `signal`). An opt-in
  addition; STABILITY v2 classifies it. That it never calls the network, never writes to a
  tracker or a memory file, and reads nothing for an untrusted corpus will not loosen.
- **The outbox** — the `write_path` key in `hippo.json`, `.claude/memory.outbox/` and its
  `.outbox.json` manifest, the `HIPPO_OUTBOX_DIR` test override, the `write_path` field on a
  save's result, and the doctor line. An opt-in addition; STABILITY v2 classifies it. The
  rule that a setting moving where writes land needs one acceptance per machine
  (`hippo trust policy --accept`) is part of the trust contract and will not loosen.
- **Consent for a session's own writes** — the `consent` key in `hippo.json`
  (`{"session_writes": true}`), the PreToolUse note store (`.hippo/telemetry/session-writes/`,
  one small JSON per session and real path, consumed by the PostToolUse hook), the
  `session_granted` and `last_session_grant_at` fields on a consent record, the
  `consent.session_grants` and `consent.session_held` daily-rollup counters, the `hippo trust
  status` line (it names the memories consented this way) and the doctor line
  (`withheld_origin`). An opt-in addition; STABILITY v2 classifies it. That the key needs one
  acceptance per machine, that it can only extend an existing consent record with a per-file
  baseline, never create one, that a grant is bound to the tool call's `tool_use_id`, and that
  bytes the hooks did not see written (a pull, a merge, a checkout, a shell redirect), edits
  over unconsented bytes, subagent writes (`agent_id`) and writes a review lint flags stay
  withheld, will not loosen.
- **Exact recall output text and ordering** — recall is a ranker; its wording, formatting, and the
  precise order of results are tuned continuously.
- **dream's deprecated tiers (from v1.45)** — reverse replay, the generative tier (`--generate`,
  `--sweep-drafts`, `--archive-draft`, `--prospective`, the MCP actions of those names and
  `HIPPO_DREAM_GENERATIVE`) and de-parasite (`--deparasite`, `--retract`, `--dedup-merge`; MCP
  `deparasite`, `dedup_merge`) measured no recall@10 gain on three real corpora. They are off by
  default, still run when named (reverse replay with `HIPPO_DREAM_REVERSE_REPLAY=1`), print one
  line saying they are deprecated, and are removed at v2.0. Every MCP input name stays in the
  `dream` schema until then. The link pass, the `dream:links` grammar, `dream-ledger.jsonl` and
  `HIPPO_SLEEP_TIER_A` are unchanged.

## Crash safety

hippo's pitch is that your memory is ordinary markdown in git and it will not corrupt
itself — so the crash behavior of every write is a stated, tested contract, not an
implementation detail. Every irreplaceable file (corpus `.md` files and the committed
markers/fixtures beside them, the machine-wide trust and projects registries, the packs
lockfile, `MEMORY.md` floors, promoted rule files, the user tier's `.gitignore` list of
machine-local memories, the git pre-commit hook `hippo gate --install` writes) is written
through one atomic
primitive family (`memory/atomic.py`: a per-call-unique tmp in the target's directory +
`os.replace`, symlink-aware). A reader sees the old document or the new one — **never a
torn one** — and a crash mid-write strands at worst an orphaned tmp file, never partial
bytes at the real path. The usage-aggregate, usage-summary and ledger-rotation writers, which name their tmp `<file>.tmp.<pid>`, remove
such orphans on their next successful write, once the pid is dead and the file is more than
five minutes old.

On top of that primitive, every registered write site declares one of three crash
classes, and a fault-injection lane tears each site once to prove it:

- **intact** — the target keeps its prior bytes; the operation is a documented
  best-effort lane and may degrade silently (e.g. the opportunistic tier-floor
  skeleton).
- **detected** — prior bytes kept AND the failure is loud: named in the result
  envelope, a `False` return, or a propagated error. Nothing pretends the write
  landed (this covers the trust registry: a failed consent write reports "corpus
  stays gated", never a silent open gate).
- **rolled-back** — the two-write chains: when write #2 fails, write #1 is restored
  byte-exact (`provenance.restore_file_bytes`) and the operation reports refusal over
  a clean tree. The registered chains: **demote+supersede** (reconsolidation),
  **dedup-merge** (de-parasite), **dream refines** (frontmatter edge + body stamp),
  and the **pack-update lockfile** advance.

The opt-in outbox (`.claude/memory.outbox/`, for a corpus whose `hippo.json` sets
`"write_path": "outbox"`) is a second write path, and each of its sites is registered like
the corpus's: a new memory is still born whole (staged, then linked), the floor copy is made
create-only-while-absent and edited by compare-and-swap like `MEMORY.md`, and its manifest
(`.outbox.json`, the base each copy was made from) is a compare-and-swap update. Both new
sites are **detected**: a torn copy raises and leaves no partial copy (a floor edit through
it reports the missing floor instead of writing the working tree), and a torn manifest write
keeps the prior manifest and the save names what did not land. Deletions never go through
the outbox.

In outbox mode the verdicts that edit an existing memory (tend's graduate, demote, keep-one,
re-derive and baseline heal, typed edges, dream edges, the related-line fix) make no new write
site: each site above is the same compare-and-swap, only aimed at the memory's outbox copy
(made by the copy-up above on first edit), and a COR-16 rollback restores bytes into that
copy. One site is new: a removal (archive, promote) drops the removed memory's outbox
manifest row by compare-and-swap. It is **detected**: a torn drop keeps the prior manifest
and the removal names what stayed.

Two recovery stories are additionally pinned for hard kills (`kill -9` mid-verb):
`pack_extract` re-runs refuse a partially-written dest **by name** (delete it and
re-run clean), and `build_index` re-runs heal the index outright. A crash between a
pack install's file write and its lockfile record heals on re-run via the
byte-identical adopt path.

The opt-in consent for a session's own writes (`"consent": {"session_writes": true}`) adds
one site, **intact**: the PreToolUse note in the gitignored telemetry dir. A torn note leaves
no partial note, the hook says nothing either way, and the PostToolUse hook then grants
nothing, so the file waits for review exactly as it would without the opt-in. The grant
itself is a fold through the trust registry's existing atomic writer (**detected**, above).

The folded-sections sub-index adds two sites, both **intact**: its matrix is written under a
fresh name before the manifest that names it, so a torn matrix or manifest write keeps the
prior sub-index whole and loadable, the index build still returns, and the next build heals
it.

The enforcing lane is `tests/test_crash_faults.py`: it discovers every atomic-write
call site by AST, fails if one is unregistered, tears each registered site once
in-process, and runs the kill lane against the two long-running verbs. This section is
asserted against that registration — the published classes and chain names above cannot
drift from what the tests enforce.

`hippo ship` adds two sites, both **detected**: the floor copy's recorded base text (written
create-only, before the copy, so a torn write leaves no copy without its base) and the
ship's one compare-and-swap on the outbox manifest (the shipped stamp, `--drop`'s row
removal, `--mine`'s new base), which keeps the prior manifest when torn. The commit itself is
git plumbing against a temporary index, and the branch is created create-only; a crash
between the branch and the stamp is read on the next run from the branch itself (its tree
holds the bytes), so the files count as in flight, not as new.

`hippo setup --conventions --yes` adds two sites, both **detected** and both compare-and-swap:
the append of a copy's own lines to `CONVENTIONS.local.md` (its prior bytes kept when torn,
and the copy is not replaced), then the replace of `CONVENTIONS.md` with the plugin's asset
(the copy's prior bytes kept, "NOT finished" named; a re-run finds the lines already in the
local file and does not add them twice).

## Support policy

- **Marketplace = latest-only.** The supported version is whatever is currently published to the
  marketplace. There are no long-term-support branches and no backported fixes to older versions —
  update to the latest release to get a fix. (This is why the compatibility surface above matters:
  updating must be safe.)
- **Upgrades are forward-only and documented.** A format migration is described in
  [UPGRADING.md](UPGRADING.md); `/hippo:doctor` detects when your corpus format or bootstrap is
  behind and names the exact next step.

## Reconciling the pre-1.0 "clean break" invariant

`ROADMAP.yaml`'s guiding invariant *"renames are clean breaks with a version bump, not compat
shims"* was correct **pre-1.0** (exercised by INT-7 and DOC-8, both on minor bumps at near-zero
adopters). It is now **qualified to pre-1.0 and to major bumps**: post-1.0, a clean break on any
frozen-surface element requires a major-version bump or a deprecation window. The invariant still
forbids permanent compat shims — it just no longer permits a silent break on a minor.
