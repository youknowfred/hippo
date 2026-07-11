# Stability & compatibility policy

Starting at **v1.0.0**, hippo commits to a frozen compatibility surface and a support policy. This
is what "1.0" means here: the things below won't change under you without a major-version bump or a
deprecation window. (Resolves ROADMAP.v1 **OQ-8**.)

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
  removed silently.)
- **The `bin/hippo` CLI subcommands** — `recall`, `new`, `build-index`, `staleness`, `mcp`.
- **The MCP tool names** — `recall`, `new_memory`, `traverse`, `why`, `decision_history` (served by
  `bin/hippo mcp`).
- **The `HIPPO_*` environment-variable namespace**, and specifically these documented operational
  variables: `HIPPO_MEMORY_DIR`, `HIPPO_INDEX_DIR`, `HIPPO_TELEMETRY_DIR`, `HIPPO_PENDING_DIR`,
  `HIPPO_LOCAL_MEMORY_DIR`, `HIPPO_USER_MEMORY_DIR`, `HIPPO_PROJECTS_FILE`, `HIPPO_TRUST_FILE`,
  `HIPPO_DISABLE_DENSE`, `HIPPO_TRUST_ALL`, `HIPPO_TRUST_NONGIT`, `HIPPO_EMBED_MODEL`,
  `HIPPO_MCP_MAX_MESSAGE_CHARS`, `HIPPO_TEA5_OPT_IN`, `HIPPO_SALIENCE`, `HIPPO_DENSE_FLOOR`,
  `HIPPO_DUP_THRESHOLD`. These keep their names and meanings.
- **The committed on-disk corpus format** — `.claude/memory/.format`'s `corpus_format` (currently
  **4**) and the memory-file frontmatter conventions ([CONVENTIONS.md](plugin/assets/CONVENTIONS.md)).
  The format version only ever increases, and every increase ships a documented migration
  ([UPGRADING.md](UPGRADING.md)) — your committed markdown corpus is never silently reinterpreted.

## Explicitly NOT frozen

These may change at any release without a major bump — do not build on them:

- **Derived caches and their schemas** — the recall index (`schema_version`, currently 6), the link
  cache, the staleness cache, and the telemetry ledgers under `.claude/.memory-*`. They are
  gitignored, rebuildable-from-source artifacts; hippo may bump their schema and rebuild them freely.
  (A `schema_version` bump is a re-index, not a corpus migration — the CHANGELOG's `re-bootstrap`
  flag tells you when deps or the index change.)
- **Internal ranking-tuning knobs** — the undocumented `HIPPO_*` variables that tune the ranker
  (e.g. `HIPPO_KNEE_RATIO`, `HIPPO_MMR_LAMBDA`, `HIPPO_BODY_RRF_WEIGHT`, `HIPPO_PIN_BOOST`, the
  `HIPPO_INTENT_*` and `HIPPO_RESCUE_*` families). They exist for experimentation and move with
  retrieval work; the `HIPPO_*` prefix is reserved, but these specific knobs are not a contract.
- **The Python API** — importing `memory.*` internals directly. hippo is consumed as a Claude Code
  plugin (skills + hooks + MCP + `bin/hippo`), not as a library; module-level functions and
  signatures may change. Depend on the CLI and MCP surfaces, not the Python symbols.
- **Exact recall output text and ordering** — recall is a ranker; its wording, formatting, and the
  precise order of results are tuned continuously.

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
