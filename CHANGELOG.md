# Changelog

All notable changes to hippo are recorded here. Format is loosely
[Keep a Changelog](https://keepachangelog.com/)-shaped, kept plain. The release
process is formalized in [`RELEASING.md`](RELEASING.md) (DOC-7, v0.6.0): entries
are written by hand as the final commit of each release PR, `plugin.json` and
`marketplace.json` versions are kept in lockstep by `tests/test_version_sync.py`
and the tag-time `release.yml`, and every entry states a **re-bootstrap** flag.

## v1.16.0 — 2026-07-15 — "Nothing half-written"

**re-bootstrap: no** — `plugin/requirements.txt` byte-identical; corpus format still **5**, index
schema still **7**, citation derivation still **3**. No migration. This release is a field report
made code: a Desktop session asked for "a memory pack of everything" and hit four walls in one
sitting — the pack skill's preflight aborted (so the agent hand-rolled venv paths around every
guard the skill encodes), a doc file swept in by an "all `.md`" glob refused the whole batch, each
refusal surfaced ONE reason per call (68 memories got probed one by one), and a mid-batch writer
refusal stranded a partial pack with no manifest. Behind the last wall was the real find: a
frontmatter writer that corrupts `metadata.type`.

- **COR-13 — the pack stamp writers join the COR-9 discipline.** `_stamp_pack` was the FIFTH
  hand-copied frontmatter-insertion walk — and the last still carrying the family's corruption
  modes. A `metadata:` line it failed to recognize (flow-style `metadata: {…}`, a trailing
  comment) got a DUPLICATE `metadata:` block appended; YAML last-wins, and every original
  metadata key silently dropped — `type` first among them, the transcript's exact "would corrupt
  its `metadata.type`" refusal. Non-2-space children got 2-space stamps: a mixed-indent document
  that no longer parses. The writer is now `insert_frontmatter_keys` (indent read from the
  block's own keys; unrecognized shapes degrade to a top-level append — doctor already reads both
  scopes — never a duplicate block). Worse on the inbound side: `_ensure_pack_stamp` ran its
  `pack_version` regex MULTILINE over the whole file, so installing a memory whose body merely
  *mentions* `pack_version:` rewrote the BODY line and never stamped the frontmatter — and
  install/update had NO damage guard, so that one corrupted **silently** instead of refusing. The
  rewrite is now frontmatter-scoped, and every pack write site guards (`_stamp_damage`: the
  COR-9 value-level check plus body byte-identity, the half no frontmatter check can see):
  extract refuses in its validate phase, install refuses before writing, and update marks the ONE
  poisoned item `stamp-refused` (reason on its row) without sinking the rest of the plan.
- **RCH-7 — extract validates everything, reports everything, writes last.** `pack_extract` now
  computes and damage-checks EVERY portable rewrite before the first byte lands, so every refusal
  is genuinely zero-filesystem-change — the old damage check ran mid-write-loop, and a refusal on
  file #40 left 39 files and no manifest, exactly the state the module docstring promised could
  not exist — and every refusal carries the COMPLETE `invalid` map (name → reason): a 69-name
  batch with three bad names reports all three in one call. `names="all"` selects through
  `_is_memory_filename` — THE corpus-membership filter — so "pack up everything" is one call and
  `MEMORY.md`/`CONVENTIONS.md` can never poison a batch again (agents: never glob the corpus
  dir); non-extractable memories land in `skipped` (name → reason), reported, never silent. A
  `dest` inside the corpus refuses up front (the extracted `.md` files would be indexed as
  memories on the next build). A mid-write I/O failure rolls the written files back.
- **INT-16 — the pack verbs reach the second surface.** Five MCP tools in the skill's own flow
  order — `pack_extract`; install: `pack_install_plan` → per-item `pack_install_item`; update:
  `pack_update_plan` → per-item `pack_update_item` — all SEC-1 trust-gated, plans rendering
  foreign pack text as demarcated quoted data (the SEC-5 discipline), item calls per-item by
  construction, and the extract tool's text carrying the complete `invalid`/`skipped` reason maps
  in-band (the transcript's "the reason isn't in the fields I printed" can't recur). The pack
  skill's preflight now routes Desktop to these tools — the pre-INT-16 text said "re-run it from
  a terminal", and the observed agent response was to drive the python primitives by hand around
  the preflight, with none of the skill's guardrails. The SessionStart Desktop surface note maps
  `/hippo:pack` accordingly. The STABILITY.md frozen five keep their names, shapes and positions.

## v1.15.2 — 2026-07-15 — "The verb has a name"

**re-bootstrap: no** — `plugin/requirements.txt` byte-identical; corpus format still **5**,
index schema still **7**, citation derivation still **3**. No engine change and no migration:
this release is entirely about REACHING the repair verbs v1.15.0 shipped. Two new MCP tools
(`rederive`, `heal_baselines`) and one new CLI flag (`--stamp-derivation`). The STABILITY.md
frozen five keep their names, shapes and positions.

v1.15.0 shipped MIG-1's re-derivation and COR-10's baseline heal as **CLI verbs only**. The
DRV-2 nudge that routes to them is a HOOK, so it fires on *both* surfaces — a Desktop user was
told to migrate, sent to `/hippo:doctor`, and doctor named nothing callable. INT-13 closed
exactly this class of gap for consolidate in v1.14.0; v1.15.0 reopened a small one.

- **INT-14 — the `rederive` MCP tool.** Mirrors the CLI: `action='worklist'` (read-only, the
  attributed diff per memory), `'one'` (name=…, ONE reviewed memory), `'snapshot'` (stamp=…).
  Deliberately no bulk form on either surface — the per-item review is what makes the SEC-6
  fold legitimate rather than the gate consenting to itself.
- **INT-15 — `heal_baselines`.** Not a gap but a **regression**: `heal_empty_baselines` used
  to run inside the SessionStart hook, which fires on both surfaces, so every user got it for
  free. COR-10 correctly moved it off the hook (a hook must not write to the corpus — it
  drifts each file off its own SEC-6 fingerprint, after which the drift banner blames the user
  for hippo's own write), but moved it to a CLI verb only the terminal can reach. Terminal
  kept the capability; Desktop lost it outright. Restored as a tool, still human-invoked and
  never automatic — that is the whole point of COR-10.
- **The one this uncovered: MIG-1 shipped four of its five steps.** `write_cite_derivation`
  existed and only *tests* called it — no CLI flag, no MCP tool. A migration could be
  performed but never **completed**, so the nudge fired forever. Found live on this repo's own
  corpus: `cite_derivation: 2` under a v3 plugin, an **empty** worklist, and no way to clear
  it. The stamp had been hand-rolled with a one-liner during the v1.15.0 migration and nobody
  noticed the verb didn't exist.
  `rederive action='stamp'` / `--stamp-derivation` closes it — and the stamp is **earned, not
  claimed**: it REFUSES while any memory still derives differently, because it asserts a
  derivation, which is precisely the thing the marker exists to let you verify. An empty
  worklist is the proof. The module's own thesis, applied to its own last step.
- **DOC-16 — name the verb.** The nudge said "review the re-derivation" and doctor said
  "re-derive per memory"; **neither named a command, on either surface**. The loop dead-ended:
  nudge → doctor → nothing. That is LIF-4's complaint one layer up — state a conclusion, never
  name the oracle. Both now name the MCP tool *and* the terminal form; the empty-worklist case
  names the stamp; and the Desktop surface note lists the two repair tools, which have no
  `/hippo:*` form by design.

The repair tools form their own category in the tool contract — not consolidate steps; they
exist purely to undo a defect hippo itself shipped. A new assertion pins that the frozen five
keep their POSITIONS too, and it earned its place immediately: the tools were first written
into the middle of the consolidate block and the test caught it.

## v1.15.1 — 2026-07-14 — "The third file class"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged. Corpus format stays **5**; this is
a `cite_derivation` bump only (2 → 3 — see v1.15.0's DRV-2 for why that is the correct axis, not
`corpus_format`). **Operator action: same as v1.15.0's** — `/hippo:doctor` names any corpus
behind the current extractor and routes to the existing per-item, consent-gated re-derivation
(MIG-1); nothing migrates automatically.

v1.15.0's motivating bug report named three uncitable file classes. That release closed two of
them (ORC-1); this closes the third, deliberately deferred at the time and pinned with a test
rather than mistaken for coverage.

- **ORC-3 — extensionless filenames become citable, narrowly.** `Dockerfile`, `Makefile`,
  `LICENSE` and friends have no dotted extension for the extractor to key on at all, and the naive
  fix — match these names anywhere — fails this project's own rule: under-flag beats cry-wolf,
  because most of them are ALSO ordinary English words ("the Dockerfile pattern is common in
  monorepos" is not a citation). Measured before designing, against this repo's real corpus and
  docs: every genuine citation found there was backtick-quoted, and `resolve_citations` — already
  extension-agnostic basename matching — needed no change at all; its existing ambiguity-drop
  protects an extensionless name exactly as it protects a `.py` one, so a monorepo with several
  same-named files still correctly falls back to a directory-qualified mention (the same pattern
  this repo's own README already uses for `LICENSE` vs `plugin/LICENSE`). Landed shape:
  directory-qualified anywhere (`docker/Dockerfile`), or a whole backtick span and nothing else
  (`` `Dockerfile` ``) — reusing `rules_plane._path_ref_re()`'s own whole-span anchor rather than
  inventing a second one, extended in step under ORC-2's single-source-of-truth rule. A bare,
  unmarked mid-sentence mention stays deliberately non-citable — a narrow, measured fix, not a
  broad noisy one. `CITATION_DERIVATION_VERSION` moves to **3**.

## v1.15.0 — 2026-07-14 — "Say what you measured"

**re-bootstrap: no** — `plugin/requirements.txt` is byte-identical. Corpus format stays **5**
and the index schema stays **7**: this release changes VALUES, not shapes. It does add one new
axis, `cite_derivation` (see DRV-2), on the existing `.claude/memory/.format` marker — additive,
and the marker file is now merged rather than clobbered so the two axes cannot erase each other.
The `/hippo:*` skill set and the five frozen MCP tools keep their exact names and shapes
(STABILITY.md). **Operator action: one.** Corpora written before this release carry citations
derived by the old extractor; `/hippo:doctor` and SessionStart now name that state and route to
a per-item, consent-gated re-derivation (MIG-1). Nothing migrates automatically.

This release started as one bug report from another repo — a memory whose entire purpose was to
catch drift between `Dockerfile`, `package.json` and `scripts/*.mjs` turned out to be blind to
all three of those file classes, and had silently fallen back to watching an unrelated file. It
did not fail loudly; it reported healthy while watching nothing.

The root cause was not the regex. **hippo sells provenance and kept none of its own: at every
seam it stated a conclusion it never computed — with the oracle already in scope, and the receipt
already thrown away.** `dropped` was a set-difference while `repo_files` — the actual answer —
was a parameter of the same function. `init` read a corpus-level boolean while `untrusted_changes`
sat there with both arguments in hand. The secrets gate scored entropy on one string and run
length on another. And nothing versioned the DERIVATION, so a corpus derived by a broken
extractor and one derived by a fixed extractor both declared `corpus_format: 5` and were
indistinguishable. That is why a 14-minor-version-old bug had to be found by hand.

### The data-integrity core

- **COR-9 — a frontmatter writer may never damage a key it does not own.** `_strip_provenance`
  removed `cited_paths:` with a per-LINE filter. A block-style value IS its `- item` lines, so
  the filter dropped the key and orphaned its value; YAML then either FOLDED the orphans into the
  preceding key (`last_verified` silently becoming the string `"2026-07-01 - src/a.py -
  src/b.py"` — the file still parses, nothing reports it) or refused the document, at which point
  `parse_frontmatter` degrades to `{}` by design and the memory loses name/type/provenance at
  once. Only hand-edited, imported or pack-authored memories carry block style (hippo's own
  `_flow_list` emits flow), which is why it survived 14 minor versions. Fixed with ONE
  continuation-aware strip primitive — the walk already existed in `links.add_typed_relation`
  and `dream_generate`; `_strip_provenance` was the oldest member of that family and never got
  it. Plus ONE insertion primitive: four hand-copied walks all took the indent from the last
  INDENTED LINE rather than the last indented KEY, so any `metadata:` block ending in a list
  indented new keys into the sequence — a second, independent break. And a value-level output
  guard: each writer declares the keys it owns, and touching anything else refuses the write and
  names the key. Value-level, not parse-level, because no parse check can see the fold.
- **LIF-4 — the rot line reports the cause it measured.** `citation_rot_lines` asserted every
  dropped citation was "no longer in the repo" and never tested repo membership, sending readers
  to hunt renames that never happened. Now partitioned at the PRODUCER (where `repo_files` is in
  scope) into `gone` (renamed/deleted — the phrase is earned) and `not_derived` (still in the
  repo; an extractor gap, a hand-edited entry being overwritten, or a body edit). Ships
  regardless of the extractor fix, and gates MIG-1: re-deriving a corpus is the largest
  citation-rot event it will ever see, and the old renderer would have called every drop a
  deletion.
- **COR-10 — a memory file is written only where a human can consent to it.** `trust.py` says
  "hooks NEVER consent", which is only sound if hooks never WRITE — and SessionStart wrote,
  drifting each healed file off its own SEC-6 fingerprint, after which the drift banner asked the
  user "a git pull? a hand edit?" about hippo's own write. The heal MOVED to
  `provenance --heal-baselines` (doctor names it); no exemption was granted, because `trust.py`
  forbids the shortcut by name. Also fixes four mis-keyed consent folds — on a non-git corpus the
  fold keyed on `<root>/.claude/memory` while every gate READER keyed on `<root>`, so an authored
  write stayed quarantined forever. Fixed by deriving the key centrally, so gate and fold cannot
  disagree — including for callers not yet written.

### The extractor

- **ORC-1 — the extractor's declared config is its contract.** `_CITATION_RE` had a leading
  lookbehind and NO trailing boundary, so a match was accepted mid-token. `package.json` extracted
  as `package.js`; `.tsx`/`.jsx`/`.json` were DECLARED in `_CODE_EXTS` and structurally
  unreachable — config the regex could not deliver. A second family FABRICATED paths nobody wrote
  (`data.jsonl` → `data.js`, `build.pyc` → `build.py`), and when the invention names a real
  sibling the memory is silently bound to the WRONG file. Adds the boundary, adds
  `mjs`/`cjs`/`mts`/`cts` (an orthogonal MEMBERSHIP gap the boundary does not touch), and
  normalises a leading `./` (git never emits one, so a MORE precise citation resolved WORSE than
  a bare basename). The test that would have caught this on day one now exists: a data-driven
  loop over EVERY `_CODE_EXTS` entry, so adding an extension auto-tests its own reachability.
- **ORC-2 — one extension gate.** `rules_plane` hand-copied the extension list while claiming to
  be "the same extension gate as `provenance._CITATION_RE`". True until someone edited one — and
  ORC-1 edited one. Measured immediately after: provenance derived `.mjs`, rules_plane returned
  None. The fix half-landed. Parity is now a test over a shared vector set, not a comment.
- **DRV-1 — every derived citation carries what it cost.** The planned resolve-time
  extension-consistency check is DROPPED: it cannot fire (`resolve_citations` can never change a
  token's extension) and cannot catch `test.py.bak → test.py`, the case it was specified for.
  The real fix is the extractor tail `(?!\w|\.\w)` — a dotted SUFFIX after a complete extension
  means the token was never this file, and citing `test.py` from `test.py.bak` binds the memory
  to the wrong REAL file. Also adds `extracted_but_unresolved`: `new_memory` DISCARDED
  `backfill_file`'s return, making one outcome indistinguishable from "cites no code" — the body
  names real files, none resolve (usually: not `git add`ed yet, since the oracle is the git
  index, not the filesystem), and the memory is born `cited_paths: []`, staleness-EXEMPT, in
  silence.
- **DRV-2 — version the derivation.** The missing axis. Corpus-level side-marker, deliberately
  NOT a `corpus_format` bump (shape unchanged — the repo's own criterion), because that is
  exactly the trap that made a corpus-wide rewrite feel like a regex tweak.

### The migration

- **MIG-1 — the third verb.** Neither existing verb can carry a corpus-wide extractor fix, and
  each is correct: `--refresh` re-derives and PRESERVES the baseline but never folds (right — it
  is a bulk pass, and self-consent is forbidden), so it would quarantine every memory it fixed;
  `--reverify` folds but re-baselines `source_commit` to HEAD, silently clearing every staleness
  flag in the corpus. Trapped between two correct invariants, so the deliverable is the verb that
  satisfies both: `--rederive-worklist` (read-only, attributed diff), `--rederive-one NAME`
  (re-derive + preserve + fold, legitimate ONLY because a human reviewed THIS file's diff), and
  `--snapshot STAMP`. The snapshot is self-ignoring: a project that gitignores `.claude/memory/`
  does not thereby ignore `.claude/memory.pre-cite2-*`, and the first real snapshot taken landed
  as an untracked copy of a private corpus in a public repo. A copy inherits the original's
  exposure; it never widens it.

### Precision, honesty, speed

- **SEC-16 — the secrets gate judges the string it measured.** Three predicates ran over TWO
  strings: length on the longest segment, entropy and class-mixing on the whole token. Both field
  symptoms fall out of that: `content_digest=<sha>` fired while the identical bare `<sha>` scanned
  clean (the label WAS the secret, as far as the gate could tell), and — the more serious half —
  **AWS's own documented example secret access key scanned CLEAN**, because `_longest_core_run`
  splits on `/`, which is standard-base64 CONTENT: the 40-char key fragments to 13/7/18, every
  piece under the floor of 20. The docstring promised 20 sat below what a real secret retains
  "even when a LONE separator splits it" — real keys carry several. All three predicates now score
  the core run; thresholds re-derived over 23 vectors rather than adopted. Honest scope: a long
  camelCase identifier still trips it, and no threshold can fix that —
  `getUserAuthenticationTokenFromCache` scores 4.01 bits while AWS's real secret core scores 3.68.
  Pinned as a known limitation.
- **SEC-17 — a placeholder is not a credential.** `postgres://${{PGUSER}}:${{PGPASSWORD}}@…` was
  reported as a leaked credential. It contains none — it is a variable reference, the documented
  CORRECT way to avoid hardcoding one. Judged on the password half only, so a real password beside
  a placeholder still fires. Not cosmetic: packs hard-REFUSE an install on any finding and
  capture_triage silently DROPS the capture — these failed closed.
- **SEC-15 — init reports the drift it can compute.** It printed "✔ corpus already trusted —
  recall active" over a corpus that was actively WITHHOLDING memories, reading the corpus-level
  boolean while the per-file fingerprint quarantine ran independently.
- **DOC-15 — retire the assertions that measure otherwise.** `bench/README.md` claimed MRR@10
  0.912 → 0.9213 under a line promising reproduction "to the digit"; measured, it is 0.9144 →
  0.9111 — REVERSED. Rewritten as the tie it is (0.0033 apart on 18 queries where one rank slip
  is worth 0.0278), with the reason this corpus structurally cannot settle the question. The
  default is NOT flipped on a tie. Also a `GATE_RECALL_P95_MS` that exists nowhere, and a
  class-mixing rationale that never fired.
- **PRF-3** — `git_root` memoized: a SessionStart spawned it 5×, recall 3×, for a
  process-constant. Measured 390→375ms (4%) — smaller than projected, reported as measured;
  free, since the rendered output is sha256-identical. A test pins the thing NOT to do:
  `build_repo_file_index` must never be cached this way, or a file created mid-session resolves
  to nothing.
- **PRF-4** — `load_index` honours `dense_disabled()`. `HIPPO_DISABLE_DENSE=1` was enforced at
  exactly one boundary (`_get_model` raising), which stops every consumer that must EMBED a
  query — but `_mmr_rerank` reads the STORED matrix and needs no model, so it walked past and
  kept reranking on a dense index, changing result order. numpy is now never imported on the
  BM25 lane.
- **GRA-7** — the link banner says "orphan memo(s) (no outbound links)" rather than a bare
  "orphan", which read as rot beside the real rot it was appended to.
- **ONB-6** — tests for the one init branch that mutates a TRACKED file (9/9 prior invocations
  hit `absent_not_created`). **No behaviour change, deliberately**: the intuitive "ask git via
  check-ignore" fix is actively WRONG — `.git/info/exclude` and `core.excludesFile` are
  machine-local and never travel to a clone, so honouring them would let one developer's private
  exclude deprive every teammate of coverage, in exactly the scenario the patch exists for.

## v1.14.0 — 2026-07-13 — "Consolidate everywhere, and a second opinion"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged. The Desktop consolidate tools
live entirely in the dependency-free, stdlib-only MCP server; the new standalone-LLM client is
stdlib-`urllib` only (honoring the dependency-light philosophy). Every persisted shape is
untouched: corpus format still 5, index schema still 7, capture-seed schema still 2. The
`/hippo:*` skill set (16) and the five frozen MCP tools keep their exact names and shapes
(STABILITY.md). Everything here is **additive** — six new MCP tools, one new flag on an existing
tool, the surface-mapping text that routes to them, and two new LLM enrichers that ship dark. No
operator action — opting the LLM surface in is one edit to `~/.claude/hippo-llm.json` (or an env
flag).

This release lands two independently-developed workstreams that merged cleanly (disjoint engine
code, one shared version bump): **INT-13** closes the last major terminal-only gap on hippo's
second surface, and the **standalone-LLM surface** adds hippo's first-ever propose-only LLM
enrichers — both gated so every write to the corpus still passes a human approval gate, and
recall stays $0/prompt.

### Desktop-safe consolidation (INT-13)

The release closes the last major terminal-only gap on hippo's second surface:
`/hippo:consolidate` — sleep-time consolidation, the write-side maintenance turn — hard-failed
on the Claude Desktop app, because its five steps run through the skill's bash blocks and the
agent's Bash tool never inherits `CLAUDE_PLUGIN_DATA` there (only hippo's MCP server and hooks
receive it; confirmed live 2026-07-13 with a pending capture sitting undrainable in the queue
while doctor/bootstrap/trust all worked). Setup got its tools in v1.10.0 (INT-9..12);
consolidation gets its own here.

### INT-13 — the consolidate-flow tools

`/hippo:consolidate`'s five steps as **thin, per-item MCP primitives** — deliberately NOT one
monolithic "consolidate" tool: consolidation is agent-orchestrated with per-item approval, and
nothing on this surface may batch writes past that gate. The skill remains the doctrine; each
tool wraps the SAME engine call its bash blocks run (no behavior fork):

- **`capture(action, path, text)`** — Step 1's queue verbs: `list` (highest-value first, with
  provenance and the queue dir — each seed is a plain JSON file the agent can read directly for
  the full evidence), `discard` ONE processed seed, `snooze` the SessionStart nudge,
  `add_decision` ONE user-confirmed WHY (GRW-4; keyed like the CLI, so it rides the same
  session's seed). One hardening over the CLI: a model-invoked `discard` is **contained to the
  pending queue** (realpath check, seeds only, never dotfiles) — the CLI trusts a human-typed
  path; a tool call must not. When a seed's hunks are secret-flagged, the listing maps the gate
  to the `secrets_scan` tool for this surface.
- **`secrets_scan(text)`** — the GRW-1 hard gate as a primitive: lint the exact lines BEFORE
  any verbatim hunk is fenced into a committed body; any finding = scrub and re-scan, never
  fence (`write_memory`'s write-time lint stays the backstop, not the gate). Pure function
  over caller-supplied text; reads nothing from the corpus.
- **`new_memory` grew `check: true`** — the CAP-3 dry-run (near-duplicate routing, RUL-3
  governance echoes, the GOV-3 proposal-time baseline; writes nothing), so the drain checks
  BEFORE it writes and a duplicate routes to update/supersede, never a new file. The SEC-13
  trust gate covers the check too — the dry-run READS the corpus's descriptions, so an
  untrusted corpus refuses it exactly as it refuses the write (a test pins the no-leak).
- **`reconsolidate(action, name, outcome, superseded_by)`** — Step 2: the LIF-1 worklist
  (`worklist`, GRW-5 watermark lane included — the tool and the SessionStart producer describe
  the SAME list) plus the ONE per-item verdict gate (`reverify`): `graduate` / `fix` / `demote`
  (chains `invalid_after`; optional `superseded_by` writes the GRA-4 edge and stamps the GRW-7
  boundary at the successor's commit date) / `snooze` (the skill's fourth verdict — the CLI
  spells it `--snooze`; one enum here). The engine's refusals (e.g. graduate+successor) and the
  LIF-3 citation-rot lines travel to this surface verbatim.
- **`build_index()`** — Step 3: refresh the recall index + persisted `links.json`. Runs the
  full `memory.build_index` under the freshly-resolved venv python when one exists (the v1.10.2
  stale-interpreter discipline — a server that booted pre-bootstrap never dense-blinds the
  rebuild), else the never-downgrade in-process incremental refresh.
- **`co_recall_proposals()`** — Step 4: the GRW-2 tally verbatim (floor names excluded,
  already-linked pairs dropped), read-only — an empty result on a sparse map stays the designed
  outcome. The approved append remains a per-item agent edit of ONE body, then `build_index`.
- **`abstention_fixtures(action, query, expected)`** — Step 5: the SIG-6 blind-spot loop —
  `draft` recurring abstained queries into the gitignored drafts queue (existing rows preserved
  verbatim), `confirm` ONE judged row into the tracked eval fixture (`category: abstention`);
  refuses stems that don't exist — never fabricate a memory to make a fixture pass.

Trust posture (SEC-1/SEC-13): `reconsolidate`, `co_recall_proposals`, and
`abstention_fixtures` are gated like recall/traverse/new_memory (they render memory names or
write corpus files); `capture` stays ungated by design (the queue is gitignored session-local
ephemera — the same trust domain as the episode buffer, never arriving via a clone — and its
corpus writes all route through the gated `new_memory`); `secrets_scan` is pure;
`build_index` writes only the gitignored index (init already builds pre-consent).

### The surface mappings (DOC)

- The consolidate SKILL.md preflight no longer claims "no Desktop-safe MCP-tool equivalent
  yet" — the guard now routes Desktop to the tools step by step, and a body note carries the
  full 1:1 mapping. The terminal bash flow is byte-for-byte unchanged.
- `doctor`'s MCP-surface footer and the SessionStart Desktop surface note both map
  `/hippo:consolidate` to the flow tools (the v1.10.1 right-invocation-per-surface
  discipline).
- READMEs: the plugin README documents the sixteen-tool surface — including a line for the
  `dream` verb tool, whose absence had left the count silently stale at "nine" — and the
  top-level Troubleshooting names the consolidate drain among the plain-words Desktop asks.

Tests: `tests/test_mcp_consolidate_tools.py` covers every tool against real corpora, queues,
and ledgers — the drain recipe end to end, discard containment, the trust gates (with
`HIPPO_TRUST_ALL` deleted), all four reverify verdicts plus the graduate+successor refusal,
the wikilink→`build_index`→`links.json` loop, co-recall threshold/already-linked behavior, the
draft→confirm fixture loop with the fabricated-stem refusal, and pins that doctor's footer,
the skill's preflight, and the surface note each name every flow tool the server actually
serves. Verified live against this repo's own corpus: the tools surface the real pending queue
(6 seeds) and the real 8-item reconsolidation worklist in byte-parity with the SessionStart
producers.
### The standalone-LLM surface — opt-in, default OFF (LLM-CLIENT / CAP-LLM / DRM-C)

hippo's first standalone LLM/API calls — and the invariant they had to survive: every write
to the corpus still passes a human approval gate. Both features are PROPOSE-ONLY enrichers
of queues a human already reviews, with no auto-apply tier anywhere (neither has the
direct-text evidence that lets dream's Tier-A completions auto-apply). Recall stays
$0/prompt — nothing here touches the hot path.

- **LLM-CLIENT — `memory/llm_client.py`, the one seam for standalone calls.**
  `complete(prompt, *, timeout_s) -> str | None`: `None` on ANY failure (no key, timeout,
  junk response, unknown provider), never raises — every consumer fails open to exactly its
  un-enriched behavior. Provider-agnostic (`_PROVIDERS` registry; Anthropic shipped),
  defaulting to the `claude-haiku-4-5` ALIAS rather than a dated snapshot (owner decision:
  tier refreshes arrive without a release). Config is centralized in ONE machine-local file,
  `~/.claude/hippo-llm.json` (the `hippo-trust.json` dotfile family; `HIPPO_LLM_CONFIG`
  relocates), layered per key as env var > file > default — the file is the durable
  machine-wide setting, env vars stay per-run overrides.
- **CAP-LLM — capture-time triage (`memory/capture_triage.py`), opt-in `capture_triage`.**
  One bounded small-model call (6s default, clamped ≤20s of the hooks' 30s budget) at
  SessionEnd/SubagentStop annotates the pending seed with SUGGESTIONS the
  `/hippo:consolidate` reviewer ratifies per item: likely type + kebab name, a drafted
  description, and near-duplicates twice over — the model's semantic flags BESIDE a pre-run
  of the drain's own calibrated `check_candidate` (never instead of it). A carry-over guard
  fingerprints the prompt evidence so the multi-fire hook path (SubagentStop×N, then
  SessionEnd) re-bills only when the session's evidence actually changed. Secret
  discipline: flagged hunks never leave the machine, the assembled prompt is re-linted, the
  model's own output is scanned and flagged. The structural approval gate is untouched —
  capture.py still never imports the corpus writer (the AST pin holds), and the
  byte-identical-corpus test now runs with triage ENABLED.
- **DRM-C — dream contradiction discovery, opt-in `dream_contradictions` (or
  `dream --contradictions`).** Cofire is a similarity — it can say two memories are ABOUT
  the same thing, never that they DISAGREE — so dream never proposed `contradicts` edges
  and the `/hippo:resolve` inbox only ever showed edges a human typed. DRM-C judges dream's
  own high-cofire pairs (the same `result["pairs"]` surface, no separate corpus scan) with
  one bounded call each: "conflict in substance, or merely related?". Conflict verdicts
  become `kind: "contradicts"` candidates — Tier-C via the pre-existing `_ROUTED_KINDS`
  routing, never admitted by `apply_eligible` — persisted in the derived
  `<telemetry>/dream/contradictions.jsonl` and merged by `resolve_view` into the SAME
  inbox and keep/supersede/merge/dismiss verdict flow (proposals clear themselves on any
  corpus outcome by read-time subtraction). Bounded: pool gated at cofire ≥ θ, attempts
  capped at 6/pass (hard-max 12), declared/superseded pairs skipped, judged pairs never
  re-billed, LLM failures simply not proposed.

Tests: `tests/test_llm_client.py` (the fail-open contract, config-file precedence layering, the
alias default), `tests/test_capture_triage.py` (flag-off unchanged, flag-on mocked, fail-open,
the carry-over guard, secret discipline, and the corpus-byte-identical pin with triage ENABLED),
and `tests/test_dream_contradictions.py` (flag-off byte-identical, propose-only into the resolve
inbox, the full proposal lifecycle, bounds, and fail-open) — all hermetic (`urllib`
monkeypatched; no live call).

## v1.12.0 — 2026-07-13 — "Sharper recall"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged (the new stemming pass is
dependency-free, matching the vendored-BM25 philosophy); every manifest schema and the MCP
surface are otherwise the same shape as v1.11.3. No operator action.

A retrieval/ranking pass across five owner-commissioned items (RET-12..RET-16), each shipped
behind its own env flag where it changes ranking behavior, plus fixes from a 5-lens
adversarial review of the whole diff before merge.

- **RET-12 — light BM25 stemming.** `build_index.stem()`/`bm25_terms()` collapses
  morphological variants ("embed"/"embeds"/"embedding") to one BM25 term — plural -s/-es/-ies
  and verbal -ing/-ed, with Porter's doubled-consonant exceptions, kept deliberately OUT of
  `tokenize()` itself (that function stays the fuzz-tested, substring-safe primitive
  `clean_query`'s Hypothesis test depends on; stemming is a separate pass applied only at the
  specific BM25-postings call sites). `SCHEMA_VERSION` 6→7 forces one reindex. Caught and fixed
  during review: a naive "-ies→-y" rule mangled "movies"→"movy", and a sibilant-plural rule
  stripped the entire `-ize`/`-yze` verb family ("tokenizes"→"tokeniz" instead of "tokenize") —
  exactly this codebase's own vocabulary. MRR@10 0.912→0.9144 on the golden corpus.
- **RET-13 — graph expansion now also seeds from typed `refines`/`derives-from` relations**
  (owner-directed), not just untyped `[[wikilinks]]` — a memory a top hit refines or derives
  from is usually also relevant. A DRAFT memory's own outbound `refines`/`derives-from` are
  excluded from seeding either way: a dream-generated (or hand-authored) draft could otherwise
  manufacture apparent corroboration from its own self-declared lineage regardless of query
  relevance, defeating DRM-6's "a draft must never answer alone" quarantine.
- **RET-14 — the KPI-2 outcome prior.** `outcome.py`'s injection-precision signal (was an
  injected memory's cited file actually touched, same session) is now an optional ranking
  prior (`HIPPO_OUTCOME_PRIOR`, default off, independent of `HIPPO_SALIENCE` — RET-10 found
  recency/usage moved nothing on the golden eval, and outcome evidence is a qualitatively
  different signal worth measuring on its own). A new SessionStart-refreshed `outcome.json`
  cache means the hot path never re-runs the live episode×outcome ledger join.
- **RET-15 — a threshold calibration tool.** `memory.calibrate_thresholds` (`eval_recall.py
  --calibrate`) grid-searches `HIPPO_KNEE_RATIO`/`HIPPO_DENSE_FLOOR` against the eval harness
  instead of hand-tuning by feel each time a regression surfaces — report-only, never mutates
  a shipped default. Run against the golden corpus: the knee ratio (0.5) is already optimal.
- **RET-16 — cross-encoder rerank on the hot path.** The `Xenova/ms-marco-MiniLM-L-6-v2`
  cross-encoder RCL-5 shipped for `/hippo:recall`'s explicit surface now optionally reranks
  `recall()`'s own result set too (`HIPPO_RERANK`, default off — the hot path has a protected
  p95 latency gate the explicit surface doesn't, and this is a genuine per-prompt cost, not a
  rare tail one: the hook is a fresh subprocess per prompt, so there's no warm-cache
  amortization across prompts). Bounded under a 5s timeout (matched to the dense query
  timeout's own budget for the same model class).

## v1.11.3 — 2026-07-13 — "The honest guard"

**re-bootstrap: no** — the shared preflight guard's message text changed in all 16 skills;
`plugin/requirements.txt`, every manifest schema, the skill set (16), the MCP surface, and the
recall path are otherwise unchanged from v1.11.2. No operator action.

- **Fixed the ONB-7 preflight guard's false "Claude Code is too old" diagnosis.** The shared
  `CLAUDE_PLUGIN_DATA` guard in all 16 skills was written in v0.2.0 — four months before Claude
  Desktop plugin support existed — on the assumption that an unset variable always meant an
  outdated Claude Code binary. In fact, the agent's Bash tool never inherits plugin-scoped env
  vars on at least some surfaces, even on a fully current, correctly-bootstrapped install — only
  hippo's own MCP server and hooks receive them, since those are harness-launched subprocesses,
  not agent-run Bash commands. The guard now says so instead of misdiagnosing the cause, and
  names the matching MCP tool (`init`/`bootstrap`/`doctor`/`dream`/`recall`/`why`/`new_memory`)
  where one exists in place of the skill's bash flow, or states plainly that the skill has no
  Desktop-safe path yet where none does.

## v1.11.2 — 2026-07-12 — "Quieter doctor"

**re-bootstrap: no** — code-only. `plugin/requirements.txt`, every manifest schema, the skill set
(16), the MCP surface, and the recall path are byte-identical to v1.11.1. No operator action.

Two doctor-precision bug fixes — each removes a `/hippo:doctor` ⚠ that was either unclearable or a
false alarm.

- **DOC-7 — bootstrap re-stamps a stale sentinel version.** A version-only (`re-bootstrap: no`)
  update left the bootstrap sentinel stamped with the OLD `plugin_version` — `start()`'s
  already-current fast path returned before the worker (and `_write_sentinel`) ever ran — so
  `check_plugin_version` nagged to "run /hippo:bootstrap," a remedy that hit the same fast path and
  no-oped. `start()` now refreshes just that label on the fast path (offline; no venv rebuild, no
  download; `requirements_hash`/`bootstrapped_at` preserved), so the delta is actually clearable —
  every future version-only release self-heals instead of nagging forever.
- **SEC — the secret-scanner entropy catch-all no longer fires on structured prose.** Its token
  class spans `/ = _ -` (a real base64/base64url secret can contain them), so a filesystem path, a
  `KEY=value` assignment, a slash-joined name list, or a hyphenated model name was read as one long
  "token" and flagged as a "possible high-entropy secret." The catch-all now additionally requires
  the token's *longest contiguous opaque run* (split on those separators) to reach 20 chars — a real
  secret is one long run; structured text is short segments — killing the false positives while
  every specific credential pattern and genuine high-entropy blob still flags. On the dogfood corpus
  this drops the secret nudge from 15 files to 0.

## v1.11.1 — 2026-07-12 — "The sleep model"

**re-bootstrap: no** — docs only. `plugin/requirements.txt`, every manifest schema, the skill set
(16), the MCP surface, and the recall path are byte-identical to v1.11.0. No operator action.

Documentation follow-through for v1.11.0's `/dream`: the README under-told how deeply the memory
system is shaped *like memory*, and had gone stale (said 15 skills; never named `/dream`; the word
"dream" appeared zero times). This release surfaces the biomimetic architecture honestly and
de-stales the command surface — no code changes.

- **README — new "Why it's called hippo" section**: names the **hippocampus** etymology and maps
  six verbs (recall, consolidate, reconsolidate, forget, dream) to their real mechanisms; adds a
  `/hippo:dream` command entry and a `consolidate` vs. `dream` disambiguation line; adds the
  generative-replay layer to the native-memory comparison; fixes the skill count **15 → 16** (two
  spots). The lead's four differentiators stay load-bearing and untouched.
- **CONCEPTS — optional fifth idea, "The sleep model"**: an *operation → what hippo actually does →
  where the analogy ends* table that converts previously-undefined jargon (reconsolidation,
  sleep-time, salience) into a coherent frame.
- **Anti-hype discipline**: analogy verbs only, every analog tied to a code seam and its honest
  limit in the same breath; the non-biological mechanisms (**git-drift staleness**, the `/dream`
  **aging firewall**) are named as *departures* from biology, never dressed as biomimicry. The
  test throughout: delete the neuroscience word and each sentence still states a true, diffable
  mechanism.

## v1.11.0 — 2026-07-12 — "The generative sleep pass"

**re-bootstrap: no** — `plugin/requirements.txt` is byte-identical to v1.10.2. Two persisted
shapes moved, each on its own contract: **corpus format 4 → 5** (a committed corpus convention —
purely additive, see the migration note below) and **links.json schema 2 → 3** (a derived cache —
self-heals with one rebuild, zero operator action). Index schema stays 6, capture-seed schema
stays 2. The skill set grows 15 → 16 (**`/hippo:dream`**) and the MCP tool surface 9 → 10
(**`dream`**) + the same 3 resources; every pre-existing tool keeps its exact name and shape
(STABILITY.md). The recall hot path is untouched except for one deliberate, owner-ratified
behavior change: the `confidence` tier is now **load-bearing in ranking** (below).

This release ships **`/dream` — the generative sleep pass** (`ROADMAP.dream.yaml`, the whole
DRM workstream: PRs #43, #44, #45). hippo's other verbs are the housekeeping functions of sleep;
this is the generative one: an offline replay pass that re-runs recall over each memory's own
derived self-query, watches what **co-fires**, and diffs that against the link graph to surface
the latent edges the corpus is structurally missing. Its identity move is **REVERSIBLE
AUTONOMY**: memory lives in git, so the safe-additive class auto-applies and offers undo after,
instead of gating everything on a human — and every autonomous half is severed behind a dated
owner decision, never a metric-proxied gate.

### The edge backbone (DRM-1..3)

- **DRM-1 — replay harness + candidate ledger**: `python -m memory.dream --dry-run` replays the
  corpus against itself (soak-gated ≥5 distinct sessions; floor and `confidence: draft` memories
  are never endpoints) and emits candidate edges by kind — **completion** (a body already names
  the target), **bridge** (a co-firing transitive A–B–C gap, exactly what 1-hop graph expansion
  turns into a hit), **refines** (an undeclared slug-prefix relation) — each with co-fire
  strength, graph distance, and the firing query, plus the θ-sweep calibration surface. The
  empty pass is the norm, and says so.
- **DRM-2 — Tier-A auto-apply + notify-with-undo + the aging firewall**: a bare pass auto-applies
  ONLY the additive, body-prose-preserving, ranking-only class above the live-calibrated bar
  (**θ=0.90, cap 5/pass hard-max 9, bridges require MUTUAL co-fire** — ratified 2026-07-12 after
  a report-only calibration pass), as stamped `[[wikilinks]]` in a machine-managed
  `<!-- dream:links -->` block or additive `refines` frontmatter. Every edge is secret-linted
  with a **hard BLOCK** (the one owner-ratified deviation from hippo's warn-only lint — dream
  *generates* text), recorded in the committed append-only `dream-ledger.jsonl` with an inline
  stamp (doctor reconciles the two), live in recall immediately, and **never committed** — git
  history stays yours. `--undo` / `--undo <edge-id>` / `--undo-since` revert byte-exactly and
  refuse on manual drift; applied edges age into /dream's own source set only after 5 un-undone
  sessions (`DREAM_AGE_SESSIONS`) — /dream never consumes its own un-aged output (the
  dream-cites-a-dream firewall). `supersedes` candidates stay digest-gated; `contradicts` route
  to `/hippo:resolve`. Opt-outs: `HIPPO_DREAM_APPLY=0`, `--dry-run`, MCP `apply:false`.
- **DRM-3 — /dream's own proof harness**: `eval --ab HIPPO_DREAM` runs the recall eval twice over
  one frozen snapshot — the OFF arm asserted byte-identical to the pinned pre-dream baseline, the
  ON arm admitting dream edges. First run: **multi-hop recall 0.0 → 1.0 with the matched
  single-hop control flat** and both conversions attributed to their enabling edge. En-route fix:
  dream:links blocks are stripped from body-chunk indexing unconditionally, so stamp text can
  never perturb lexical ranking in either arm.

### The counterweights (DRM-4..5)

- **DRM-4 — de-parasiting**: `dream --deparasite` reports per-memory out-degree, flags hubs over
  `DREAM_MAX_OUT_DEGREE` (8), and splits remedies along the reversibility gradient — /dream's own
  un-aged edges retract via `--retract` (the one auto-executable lane; a retracted or undone pair
  is NEVER auto-re-applied), everything touching a human memory stays per-item gated, and
  near-duplicates get non-lossy `--dedup-merge` proposals (survivor `supersedes`, loser
  `invalid_after`; nothing deleted). Protected hubs (floor / co-recalled / cited) are never
  proposed for depression — and dream's own edges confer no protection.
- **DRM-5 — reward-gated reverse replay**: memories with a **recorded outcome** (injected, then a
  cited file touched that session — the KPI-2 join, now exposed per-memory as
  `outcome.injection_hits`) anchor a backward walk along their decision chain; the upstream
  lineage earns replay priority and candidate ORDERING under the cap (`DREAM_REWARD_WEIGHT`
  0.01/hit, saturating at 5 — calibrated so a boost promotes within its co-fire neighborhood,
  never across the distribution). Strictly reward-gated and ranking-only: θ always reads the raw
  co-fire; no outcome → no boost.

### The quarantined generative payload (DRM-6, behind a flag)

- **`dream --generate`** clusters the co-firing sets: mutual components of 3–8 members propose
  **schema/gist parents** (`[[child]]` links, `derives-from` frontmatter, cited paths inherited
  from the children); strong mutual pairs with NO graph path propose **hypotheses**. Report-only
  everywhere by default; staging (`--stage`, or `HIPPO_DREAM_GENERATIVE=1` for apply passes)
  creates them **only at `confidence: draft`** — capped 2/pass (hard max 5), hard secret-BLOCKed,
  stamped + ledgered like edges, trust-folded, and undoable (whole-file removal, sha-verified
  refuse-on-drift; a graduated draft refuses). The firewall extends **node-level** to generative
  output: drafts and graduated-but-unaged generated memories are invisible to the next pass's
  topology entirely.
- **Self-decay rides every apply pass, flag or no flag**: graduation to `verified` happens ONLY
  on recorded outcome evidence (no self-graduation path exists — pinned); a draft unconfirmed
  past `DREAM_DRAFT_HORIZON` (10 distinct sessions) auto-closes its validity window and
  **proposes** its archive (`--archive-draft <name>` executes one per item). The sweep prints a
  **graduation-rate hallucination alarm** when decided drafts mostly die unconfirmed.
- **Prospective recall**: the recurring-abstention backlog freezes at first staging;
  `--prospective` counts abstain→hit flips over that frozen baseline with via-dream attribution
  — the metric the tier must move to earn its keep.
- Live calibration on hippo's own corpus: at θ=0.90 the dense release-history family fused into
  one 19-member mutual component — so `DREAM_SCHEMA_MAX_CLUSTER` (8) deliberately equals DRM-4's
  hub cap, and an oversized component is reported as a *θ-under-discriminates* signal, never
  staged. The flag ships **OFF** (DREAM-KILL-1: Tier B/C is never auto-applied as verified).

### Ranking + schema changes (the DRM-6 prerequisites)

- **`confidence` is load-bearing in recall ranking** (closes GOV-7's display-only gap):
  `draft` ×0.5 (the quarantine weight — an equivalent verified memory always outranks a draft)
  and `authoritative` ×1.1 (a bounded promotion, deliberately below the ×1.2 pin boost), applied
  pre-cut in the penalized loop; verified/unset take no multiply, so an ungraded corpus is
  byte-identical. Plus the **abstention guard**: a result set consisting only of drafts collapses
  back to the abstention shape — drafts accompany verified content or seed expansion toward it,
  never answer alone. Overrides: `HIPPO_DRAFT_PENALTY`, `HIPPO_AUTHORITATIVE_BOOST`.
- **`derives-from`** joins the typed-relation set (derivation provenance: a parent names the
  children it was abstracted from; hand-authored use welcome). `add_typed_relation` accepts it,
  decision chains and DRM-5 reward propagation follow it, and the **corpus format bumps 4 → 5**
  — purely additive: no per-memory edits; review that no existing frontmatter uses the key, then
  stamp the marker (see UPGRADING.md). links.json bumps 2 → 3 and self-heals.
- **SEC-6 fold on the dream write paths** (latent DRM-2 gap, fixed): apply now folds every
  stamped file into the consent baseline and undo re-folds the restoration — on a fingerprinted
  corpus, stamped files would otherwise have quarantined out of recall as new-since-consent
  bytes, the opposite of "live immediately". Never bit a real corpus (zero edges had ever been
  applied live).

New surfaces: skill `plugin/skills/dream/` (`/hippo:dream`), MCP tool `dream` (actions
`pass/undo/log/deparasite/dedup_merge/generate/sweep_drafts/archive_draft/prospective`), CLI
`python -m memory.dream` (`--dry-run/--apply/--undo/--log/--deparasite/--retract/--dedup-merge/`
`--generate/--stage/--sweep-drafts/--archive-draft/--prospective/--json`), doctor's
`check_dream_ledger`, and the `dream_applied_producer` SessionStart nudge with undo handles.

## v1.10.2 — 2026-07-12 — "Fresh interpreter"

**re-bootstrap: no** — `plugin/requirements.txt` and every persisted shape unchanged (corpus
format 4, index schema 6, capture-seed schema 2); skills (15) and the MCP tool surface (9 + 3
resources) keep their names and shapes. One defect fix, found **live** within hours of v1.10.0
by the very Desktop bootstrap flow it shipped.

- **INT-13** — **the stale-interpreter fix**: the MCP server's interpreter is frozen at session
  start, so a server that booted pre-bootstrap runs bare python3 forever — and everything the
  setup tools did *in-process* after a mid-session bootstrap lied. `doctor` reported a healthy,
  fully-warmed venv as corrupt (with delete-the-venv-and-redownload advice), and `init`'s index
  rebuild silently couldn't embed dense vectors while the bootstrap status promised dense "from
  the next prompt". The terminal skills never had this bug (`_resolve_py.sh` re-resolves `$PY`
  per command); the setup tools now do the same per-invocation resolution: `doctor` runs the
  engine under the freshly-resolved venv python via subprocess (the in-process fallback carries
  an explicit staleness caveat), `init` builds the index under it (`init_project` grew
  `dense_python=`; subprocess failure falls back in-process with a warning), and the bootstrap
  wording names the real remaining step — run `init` once so the index rebuilds with dense
  vectors. Verified against the live failure: a bare-python3 server + a freshly-bootstrapped
  venv now reports "venv healthy" and produces a `dense_ready` index; the recall hook upgrades
  to `dense+bm25`.

## v1.10.1 — 2026-07-12 — "The right invocation per surface"

**re-bootstrap: no** — `plugin/requirements.txt`, every persisted shape (corpus format 4, index
schema 6, capture-seed schema 2), the 15 skills, and the frozen MCP tool names/shapes (STABILITY.md)
are all unchanged. This is a remediation-wording patch on v1.10.0.

v1.10.0 made setup work on the Claude Desktop app; this patch makes hippo's own *advice* follow.
Hook-emitted nudges still told every surface to type `/hippo:*` commands — which only the terminal
CLI accepts (verified live 2026-07-12: a Desktop session's SessionStart nudge advised
`/hippo:bootstrap`, which the app rejects). Remediation strings are now surface-aware, keyed
deterministically on the harness's `CLAUDE_CODE_ENTRYPOINT` env marker (`claude-desktop` in the
Desktop app's hook/MCP env):

- **ONB-1 first-run nudges** (`hooks/memory_session_start.sh`): on the Desktop surface the
  bootstrap/init nudges name the v1.10.0 MCP setup tools ("run bootstrap once per machine, then
  init once per project — just ask for it") instead of typed commands. Terminal wording is
  byte-identical to v1.10.0.
- **SessionStart dispatcher** (`memory/session_start.py`): when the merged producer context names
  any `/hippo:*` command on the Desktop surface, `build_context` appends ONE mapping note
  (bootstrap/init/doctor/trust → the MCP tools; consolidate/resolve/audit/new/recall/why → skills,
  invoked by asking) — the same append-a-suffix shape the MCP doctor tool has used since v1.10.0.
  Producers stay byte-identical for a given corpus state (the DOC-4 determinism posture); the note
  is cap-aware (its budget is reserved before truncation, and it is dropped whole — never truncated
  into garbage — when the cap can't carry both it and the signal). The SEC-1 untrusted-corpus nudge
  routes through the same seam.
- **MCP untrusted-corpus refusals** (`memory/mcp_server.py`): the SEC-1/SEC-13 refusals in
  `new_memory`/`traverse`/`decision_history` and the `hippo://floor` / `hippo://scorecard` /
  `hippo://rules-view` resources now share ONE remedy that names this server's own `doctor` +
  `trust_corpus` (+ `init`) tools first and the typed terminal commands second — a refusal that
  names only `/hippo:doctor` dead-ends the exact client it refused.
- **Doctor engine untouched**: `doctor.render()` keeps its wording verbatim — the MCP doctor tool's
  existing suffix already maps it per surface, preserving the byte-identical engine promise. The
  PreCompact capture nudge is also unchanged: it addresses the agent, and skills are
  agent-invocable on both surfaces.
- **Tests**: surface-note unit tests (note only on `claude-desktop`, only when a command is named,
  never past the cap, untrusted path included), Desktop + terminal ONB-1 nudge tests end-to-end
  through the real shell hook (`_run_hook` gained an `entrypoint` knob), an MCP refusal-remedy
  sweep, and a conftest scrub of the ambient `CLAUDE_CODE_ENTRYPOINT` so a suite run from inside a
  Desktop session cannot flip outcomes.

## v1.10.0 — 2026-07-12 — "Second surface"

**re-bootstrap: no** — `plugin/requirements.txt` is byte-identical to v1.9.0, and every persisted
shape is unchanged (corpus format still 4, index schema still 6, capture-seed schema still 2). The
`/hippo:*` skill set (15) is unchanged, the five frozen MCP tools keep their exact names and
shapes (STABILITY.md), and the recall hot path is untouched. Everything here is **additive**: four
new MCP tools and two new engine modules behind them.

The release makes hippo first-class on its **second surface**: the Claude Desktop app's local
sessions run installed plugins' hooks, skills, and MCP servers through the same engine as the
terminal CLI (verified live against the desktop harness) — they only reject *typed* `/hippo:*`
commands. Setup was the one thing stuck in the terminal; these tools unstick it. Install remains
the single terminal step; bootstrap, init, consent, and diagnostics now work from either surface
(and from subagents). The full no-terminal onboarding path — wire a cloned corpus, review it,
consent, recall — is exercised end-to-end over the real stdio transport in the test suite.

### The setup tools (INT-9..12)

- **INT-9** — **`trust_corpus`**: the SEC-1 consent flow as a two-step tool. A review call
  **never trusts** — it returns the memory count, the exact description strings recall would
  inject (quoted as untrusted data), and a **consent digest**; the confirm call requires that
  digest, binding consent to the reviewed bytes (a corpus that changes in between refuses — a
  TOCTOU guard the single-sitting terminal flow never needed). First consent stamps the SEC-6
  fingerprint + SEC-7 `origin="review"`; drift re-consent reviews exactly the changed/added
  delta (`corpus_consent_sample` grew a `stems` filter) and preserves the existing origin.
- **INT-10** — **`init`** + `memory/init_project.py`: the mechanical `/hippo:init` flow as one
  tested engine function (the DOC-4 engine/skill shape). Fresh project: core pack + `MEMORY.md`
  skeleton + format marker + `CONVENTIONS.md`, then the machine wiring; existing corpus: wiring
  only. Idempotent, never overwrites a memory file, never commits. One deliberate divergence from
  the skill, SEC-1-load-bearing: a **model-invoked init never auto-trusts a pre-existing corpus**
  (typing `/hippo:init` is itself the user's review; a tool call is not) — only a corpus the call
  *creates* is trusted (`origin="init"`), and consent otherwise routes through `trust_corpus`.
- **INT-11** — **`bootstrap`** + `memory/bootstrap.py`: the one online step (venv + ~130MB model
  warm) as kick-off-and-poll — `start` detaches a stdlib-only worker (own session, sentinel
  written LAST, log + live-pid lock under `CLAUDE_PLUGIN_DATA`), `status` polls it. This is
  load-bearing for the desktop app: the harness hands **each surface its own plugin-data dir**
  (`hippo-<marketplace>` vs `hippo-inline`), so a terminal bootstrap's venv is invisible to
  desktop sessions — without an in-surface bootstrap, desktop recall stays BM25-only forever.
  `status` names a sibling surface's install so "why is it downloading again?" answers itself.
- **INT-12** — **`doctor`**: the deterministic DOC-4 diagnostic engine verbatim, plus a mapping
  from each report line's named fix to the tool that runs it on this surface. Deliberately
  ungated (doctor is the pre-consent review entry point), with a pinned boundary: the report
  exposes state, never the injectable description strings.

### Docs (DOC-15)

- The README Quickstart callout and Troubleshooting now tell the accurate two-surface story
  (install from the terminal once; use hippo from terminal or desktop; typed commands and
  cloud/remote are the real limits), including the per-surface bootstrap entry. plugin/README's
  MCP section documents the nine-tool surface and the model-invoked-init consent rule.

## v1.9.0 — 2026-07-11 — "Out in the open"

**re-bootstrap: no** — `plugin/requirements.txt` is byte-identical to v1.8.0
(`fastembed>=0.4,<0.8`, `numpy>=1.26,<3`, `PyYAML>=6.0,<7.0`, `rank-bm25>=0.2.2,<0.3`), and every
persisted shape is unchanged (corpus format still 4, index schema still 6, capture-seed schema
still 2). The `/hippo:*` skill set (15) and the MCP tool set (5 tools / 3 resources) are unchanged;
nothing here touches the recall hot path's behavior. This is a **launch release**: it carries no
new engine risk, only the measurement, positioning, community, and brand work that makes the repo
safe to hand a stranger — and it accompanies flipping the repository **public**.

Where v1.8.0 **"Safe in the open"** made a cloned corpus safe *by review of what injects*, v1.9.0
takes hippo **out** into the open: a reproducible benchmark anyone can re-run, a self-authored
comparison against the mid-2026 field, a written 1.0-grade stability contract, a stranger's
contribution path, and — at last — a face. It folds the roadmap's **v0.9.0 "Proven for strangers"**
measurement spine, the **v0.10.0 "Legible to strangers"** doc remainder, and the **v1.0 "Launch"**
positioning/community milestone into one semver step. Every item reached `main` through a reviewed
PR (noted per group).

### Measurement spine — "Proven for strangers" (RET-9/10/11, PRF-4/5, PR #26)

- **RET-9** — a per-corpus **abstention doctor check**: `/hippo:doctor` flags a corpus whose
  recall can't cleanly abstain on off-topic queries, rather than letting it silently inject noise.
- **RET-10** — the salience-weighting experiment was **run and DECIDED OFF**: on the category-tagged
  eval it moved nothing (the signal was vacuous), so it ships disabled rather than as dead
  complexity. (Owner-resolved OQ-10.)
- **RET-11** — a BM25-only abstention floor was **empirically rejected**: on-topic and off-topic
  queries overlap in every BM25 signal measured, so the honest abstention gate stays **dense-gated**;
  the finding is captured as a doctor check, not a false promise.
- **PRF-4/5** — the cold-latency gate moved from **p50 to p95** (the figure the recall hook actually
  pays), with the measured dense@500-memory p95 (~407 ms) recorded against the BM25 budget.

### "Proven for strangers" finish (CAP-6/7, GRA-8, INT-8, QUA-11, PR #27)

- **CAP-6** — the pending-capture queue gained a **value-first bound + snooze/dismiss**, so the
  SessionStart nudge defers a snoozed queue instead of nagging unboundedly.
- **CAP-7** — an end-to-end **capture → approval** integration test over the real drain path.
- **GRA-8** — `hippo links` grew `--components/--degree/--export (json|dot|mermaid)`; the link-graph
  component count feeds the trust scorecard as an informational line.
- **INT-8** — the README MCP table now lists all **5 tools + 3 resources** with mid-turn/subagent
  discoverability notes, and `doctor.check_mcp_launch` exercises the real `serve()` handshake so a
  broken MCP launch is a caught regression, not a silent one.
- **QUA-11** — a CI **resolution lane** (py3.11/3.13/3.14 dependency bootstrap) and a
  network-marked external-URL docs check.

### Legible to strangers — first-run docs (ONB-10, DOC-12, PR #28)

- **ONB-10** — `/hippo:init` can offer an optional **`user_role.md` fill** via a question prompt,
  under a hard rule: only user-supplied answers are written, never inferred or synthesized.
- **DOC-12** — a README **## Commands** reference covering all 15 skills, with the
  recall-vs-doctor / doctor-vs-audit / consolidate-vs-audit distinctions, and the stale "8 skills"
  layout block corrected.

### Positioning & launch collateral (POS-1..7 + demo + benchmark, PR #29)

- **POS-1/2** — the README lead is re-cut to hippo's durable, plain-language capability line, and a
  **"Compared to other memory tools"** table places it honestly against claude-mem, memsearch/
  memweave, native memory, and supermemory.
- **POS-3** — a **reproducible benchmark** (`bench/run.sh`, `bench/README.md`): the real recall
  engine over the shipped 50-memory golden corpus and 18 cross-vocabulary paraphrase queries —
  **recall@10 = 1.0**, **MRR@10 0.912 → 0.9213** (BM25-only → dense hybrid), at **$0 per prompt**,
  deterministic to the digit; with the principled *why not LongMemEval/LoCoMo/BEAM*.
- **POS-4** — `demo/git_drift.sh`: a no-download hero demo that writes a memory citing a function,
  edits the function, and shows hippo flag it stale — the one behavior no competitor reproduces.
- **POS-5/6/7** — the `$0`/private rationale, the shipping-products landscape, and a native-memory
  section for the GA `memory_20250818` tool + Auto Memory.

### Community on-ramp (COM-1/2/5/6/7, QUA-12, PR #30)

- **COM-1/2** — `CONTRIBUTING.md` (the CI-matching venv recipe, markers, checks, conventions),
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue forms (platform/backend/corpus-size), a PR
  template, and `CODEOWNERS`.
- **COM-5** — `STABILITY.md` ratifies **OQ-8**: the frozen 1.0 surface (`/hippo:*`, `bin/hippo`, the
  5 MCP tools, the `HIPPO_*` namespace, documented env vars, corpus format 4) — and, explicitly,
  what is *not* frozen (caches, schemas, tuning knobs, the Python API).
- **COM-6** — `UPGRADING.md`: a worked corpus-format 2→3 migration and the three upgrade kinds.
- **COM-7** — manifests attribute to **youknowfred** with keywords/author mirrored across both;
  README CI/version/MIT badges.
- **QUA-12** — `release.yml` now cuts a **GitHub Release** from the CHANGELOG extract with
  SHA-pinned actions, plus `dependabot.yml` and a codified branch-protection ruleset.

### Concepts & brand

- **DOC-9** (PR #25) — a **"How hippo thinks"** concepts page (`CONCEPTS.md`) and a README
  mental-model lead: what a memory is, the always-on floor vs. on-demand recall, the four types,
  why markdown-in-git.
- **BRAND** (PR #37) — the **waterline-hippo** logo, wordmark, and lockup (`assets/logo/`, one file
  for light+dark grounds; a `prefers-color-scheme` lockup at the README top), and
  `author.url → youknowfred.com` in both manifests.

### Security & hygiene (pre-launch sweep)

- **SEC-1 gate coverage** — the MCP `traverse` and `decision_history` tools now honor the trust
  gate that `recall` / `why` / `new_memory` and every resource already enforce. Both rendered
  memory **names + typed edges + dates** from an untrusted foreign corpus into agent context
  without consent (metadata only — never descriptions or bodies — so the exposure was narrow);
  they now withhold until the corpus is reviewed, with regression tests that run with the
  trust-all test override removed.
- **Fixture scrub** — a `tests/` fixture hard-coded a real personal absolute path; replaced with
  a synthetic path (no behavior change). `.gitignore` gained explicit `.env*` /
  `.claude/*.local.json` entries so local secrets can't be committed by accident.
- **py3.13/3.14** — a test's `re.split(..., 1)` passed `maxsplit` positionally, which newer
  Python deprecates and the suite escalates to an error; now `maxsplit=1`. Shipped code was
  never affected.

## v1.8.0 — 2026-07-10 — "Safe in the open"

**re-bootstrap: no** — `plugin/requirements.txt`'s dependency constraints are byte-identical to
v1.7.0 (`fastembed>=0.4,<0.8`, `numpy>=1.26,<3`, `PyYAML>=6.0,<7.0`, `rank-bm25>=0.2.2,<0.3`);
SEC-11 added upper-bound *documentation* and a hardened-install recipe, not a version change.
Persisted shapes are ALL unchanged since v1.7.0 (corpus format still 4, index schema still 6,
capture-seed schema still 2) — the trust spine's content fingerprint lives in the machine-local
trust record, outside the corpus. The `/hippo:*` skill set grew from 14 to 15 (RUL-6 added
`/hippo:promote-rule`); the MCP tool set is unchanged.

This release delivers the roadmap's **v0.8.0 "Safe in the open"** security tier
(`ROADMAP.v1.md` §6) — the milestone that makes cloning hippo's own public repo, or any public
repo carrying a hippo corpus, safe *by review of what actually injects* — together with the
S-effort slice of the **v0.10.0 "Legible to strangers"** first-run polish, and the ranking,
evaluation, and reach work that merged since v1.7.0. Every item below reached `main` through a
reviewed PR — the numbers are noted per group below; several PRs bundled multiple items (e.g.
#19, #21), and this first-run-polish set ships with the release PR itself.

### Security — the "Safe in the open" tier (SEC-5..14)

**Trust spine (SEC-5/6/7, PR #19).**

- **SEC-5** — consent surfaces the memory **descriptions** that actually inject (not just
  filenames), rendered through the injection layer's own formatter (byte-equal, test-pinned),
  so you review the strings that will enter every prompt.
- **SEC-6** — the trust record stores a per-file **content fingerprint**; recall QUARANTINES a
  drifted or newly-added project-tier file, per file, until you re-review — defeating
  trusted-upstream supply-chain injection. hippo's own write primitives fold their writes into
  the baseline (authorship = consent); index builds never do.
- **SEC-7** — an inject-time **provenance banner** + defensive demarcation for foreign/cloned
  corpus lines; a corpus trusted by review (vs one you authored) is labelled as such at use.

**Launch-security tail (SEC-8..14, PRs #21/#22).**

- **SEC-8** — broadened the secret detector (Slack/Google/Stripe/OpenAI/Anthropic/JWT/npm/PyPI/
  connection-string patterns, high-precision floors) and added a `secret-scan` CI job over the
  tracked tree.
- **SEC-9** — repo-root `THIRD_PARTY_NOTICES` inventories the four direct deps, their transitive
  tree, and both embedding models, verified against installed venv metadata and drift-guarded.
- **SEC-10** — repo-root `SECURITY.md`: private vulnerability disclosure channel, supported
  versions, and threat model.
- **SEC-12** — the trust gate now covers a **non-git directory that carries a real corpus**, so
  a zip download is no longer a trust bypass; an empty non-git dir stays inapplicable.
- **SEC-13** — the MCP `new_memory` tool runs the same resource gate as reads (no
  write-without-review asymmetry); `serve()` rejects an over-long line before parsing it.
- **SEC-14** — `--record-usage` refuses to write committed telemetry on a repo that has a git
  remote unless explicitly opted in (local-only proceeds); a new doctor check surfaces
  committed-usage privacy.
- **SEC-11** — every dependency is **bounded on both sides**, so a compromised new major can't
  be pulled at bootstrap; a test enforces the no-unbounded-`>=` invariant, and
  `requirements.txt` documents a per-environment hardened hash-install recipe and the model's
  content-addressed integrity check. Ranges stay ranges on purpose: no single numpy version
  spans CPython 3.10–3.14, so a universal exact lock is infeasible.

### Ranking, evaluation & reach

- **GRA-1** (PR #18) — fixed the dense-side knee that suppressed graph expansion; multi-hop
  neighbours the dense floor previously starved now surface (golden set byte-identical).
- **RET-8** (PR #16) — a category-tagged eval suite, so recall precision is measurable
  per-category on any corpus (unblocks the salience decision).
- **SIG-6** (PR #17) — abstention blind-spots feed the eval fixtures; the T7 signals close-out.
- **RCH-5** (PR #19) — trust-gated pack install/update: a curated memory subset lifts and
  re-applies across projects, gated on the same trust spine.
- **RUL-6** (PR #23) — glob-scoped rule promotion (`/hippo:promote-rule`, the 15th skill):
  promote a proven memory into a path-scoped rule, propose-only, derived from its cited paths.
  Unblocked by the owner clearing the LIF-7 capture-soak gate.

### First-run polish for strangers (v0.10.0 S-slice)

- **DOC-14** — scrubbed origin-repo jargon ("the ic-memobot/Memosa … private origin repo") from
  every stranger-facing surface (README intro, `plugin.json` description, `requirements.txt`
  header); the License section keeps its relicensing provenance with only the internal name
  removed.
- **ONB-8** — the README Quickstart now ends on an **observable first recall** ("what do you
  remember about my role?" / `/hippo:recall`) — the KPI-1 metric it never exercised before.
- **ONB-9** — `/hippo:init`'s closing narration ends with a **try-it-now** nudge naming the exact
  next command, completing the init → observed-recall funnel.
- **DOC-11** — a README **Troubleshooting** section (empty-recall triage, on-demand vs
  always-load-floor recall, non-git degraded mode, `/hippo:doctor`).
- **CAP-8** — the README now surfaces the **automatic-capture → `/hippo:consolidate` approval
  loop** with a worked example — hippo's strongest differentiator versus native memory.

### Docs

- **explorations-r2** (PR #20) — a round-2 enhancement catalog + draft post-v1 roadmap
  (`EXPLORATIONS2.md`, `ROADMAP.enhancements2.yaml`), owner-triage pending; no shipped behavior.

## v1.7.0 — 2026-07-09 — "Reach — knowledge that travels"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged across both tiers below; the code
swap on update is sufficient. Persisted shapes are ALL unchanged since v1.5.0 (corpus format
still 4, index schema still 6, capture-seed schema still 2): T6's origin/pack stamps are
additive absence-emits-nothing frontmatter rendered from per-hit file reads, and T7 adds no
persisted surface at all.

**Covers tiers T6 and T7 (T7 partial by design).** T6 ("Reach", PR #14) and T7's one
unblocked item (PR #15) were each merged to `main` as their own reviewed, CI-green PR and are
released together here. The REST of T7 is hard-gated and NOT in this release: SIG-5/SIG-6
wait on RET-8 (not yet built), RUL-6 waits on the LIF-7 CAP-soak owner judgment — the tier
stays `in_progress` in `ROADMAP.enhancements.yaml` with dated gate notes, and the salience
default stays OFF (no ranking behavior changed in this release).

The theme: knowledge stops being repo-bound. A lesson promotes up with provenance, foreign
rules import in, decisions replay their own history, recall spans every trusted project,
curated subsets extract as packs, and the floor fans out to the cross-tool rule plane — every
one of them per-item, propose-first, and drift-checked.

### T6 — Reach (v1.6.0, PR #14)

- **RCH-6** — the portability linter, the shared lift-time primitive: `repo_coupling` findings
  route to strip/rewrite ("warn"), `consequential_default` findings (attribution/CI-bypass
  policies) each demand an individual yes ("confirm") — manifest-parity-pinned to the shipped
  packs so the two catalogs cannot drift.
- **RCH-1** — `/hippo:promote`: lift ONE proven-portable memory into the user (or private)
  tier with an origin stamp — recall everywhere then answers "learned in `<repo>@<sha>`".
  All guards run before any write; a refusal is a zero-filesystem-change event.
- **RCH-2** — `/hippo:import`, the Cursor `.mdc` adapter: foreign rules become ranked, deduped,
  secret-linted memories with globs landing as `cited_paths` (born staleness-tracked). Ships
  the tier's premise correction: real Cursor frontmatter is NOT valid YAML (`globs: **/*.ts` —
  a bare `*` is a YAML alias), so a tolerant line-based fallback does the parsing.
- **RCH-3** — decision-chain replay: `supersedes`/`refines` walked transitively into ONE
  chronological narrative with branch-point annotations (`contradicts` never traversed),
  behind the 5th MCP tool `decision_history` and `/hippo:recall --history`.
- **RCH-4** — trust-gated `--all-projects` recall: every registered corpus passes the trust
  gate AT QUERY TIME before its index loads; hits label their source repo; every skip is
  named in the output trailer. Golden eval byte-identical before/after.
- **RCH-5** — pack EXTRACT only: curated subsets leave as install-shaped packs (provenance
  stripped, consequential markers auto-derived). Install/update stay gated on the v0.8.0
  trust spine — a negative-capability pin in the suite is the tripwire that flips when the
  spine ships.

### T7 — Learned ranking, the unblocked slice (v1.7.0, PR #15)

- **RUL-7** — `/hippo:export-agents`, the AGENTS.md fan-out: the project floor renders as a
  PROPOSED `AGENTS.md` diff (the Linux-Foundation cross-tool rules file) — propose-only, the
  module never writes; a marker-delimited managed block preserves hand-maintained content
  byte-verbatim; project tier only (user/private tiers never enter a committed file). Glob
  scoping derives from `cited_paths` via the new RUL-6-shared `derive_paths_globs` (single
  citation stays a literal path; same-dir collapses capped at 3× over-coverage; `**` never
  emitted). Once applied, the exported file is drift-checked by the EXISTING doctor/
  SessionStart channels: `archive._SCAN_TARGETS` and the rules-plane dead-glob leg now cover
  `AGENTS.md`, so a moved cited path flags loud with zero new reporting surface.

New skills since v1.5.0: `/hippo:promote`, `/hippo:import`, `/hippo:pack`,
`/hippo:export-agents` (pinned skill list 10 → 14). MCP: 5 tools (+`decision_history`),
3 resources. Suite: 1462 hermetic tests; eval gates unchanged and green throughout
(self 0.98 / hard 1.0 / mrr 0.92 on the golden corpus).

## v1.5.0 — 2026-07-09 — "Knowledge that grows itself"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged across every tier below; the code
swap on update is sufficient. Persisted-shape changes since v0.7.0, each a clean break per the
usual discipline (stamp-only additive corpus bumps, one full index rebuild via the manifest
schema gate — no migration code, no compat shims): **corpus format 2 → 4** (steer:pin in GOV-2,
author confidence in GOV-7), **index schema 3 → 6** (manifest `head_commit` in RCL-6, then the
two GOV bumps in lockstep), and the gitignored capture queue's own seed schema 1 → 2 (GRW-1/4 —
not a corpus artifact).

**First tagged release since v0.7.0.** The five post-v0.7.0 enhancement tiers were each merged
to `main` as their own reviewed, CI-green PR and are released together here; per-tier detail
lives in `ROADMAP.enhancements.yaml` (every item now `status: done`) and the tier PRs.

The theme of the release: the corpus stops being something you maintain and starts maintaining
itself — recall gets precise, rules join the plane, governance gets legible, and capture,
graph, and staleness all close their loops with the human still holding every write gate.

### T1 — Positive context & dark signals (v1.1.0, PR #9)

- **SIG-1** — the first positive SessionStart producer: memories relevant to the uncommitted
  working-tree diff are surfaced because of WHAT YOU ARE DOING, not just what you typed.
- **SIG-2** — a "where you left off" resume card replayed from the episode buffer.
- **SIG-3** — silent recall abstentions become a recurring blind-spot backlog (doctor check +
  rare SessionStart nudge) instead of vanishing.
- **SIG-4** — KPI-2 finally measured: a PostToolUse read-signal ledger for injection precision.

### T2 — The rules bridge (v1.2.0, PR #10)

- **RUL-0** — the `.claude/rules` `paths:` scoping claim verified live before anything built on
  it (unscoped rules always-load; `paths:`-scoped rules lazy-inject on matching file reads).
- **RUL-1** — a loud rule↔memory conflict radar (governance cites what the corpus disputes).
- **RUL-2** — staleness over the rules plane itself (rules rot like memories do).
- **RUL-3** — write-time dedup against rules: link, don't copy.
- **RUL-4** — rules surfaced as an on-demand recall source (labelled `(rule)`, never duplicated).
- **RUL-5** — `hippo://floor` + rules MCP resources so subagents share the plane.

### T3 — Retrieval precision (v1.3.0, PR #11)

- **RCL-1** — per-query intent routing on the hot path (hot-path-safe, eval-gated).
- **RCL-2** — floor-dedup + within-session cooldown: injected tokens earn their place.
- **RCL-3** — terse-follow-up rescue rides the same session-scoped episode read.
- **RCL-4** — MMR intra-block diversity (applied after the knee — the ordering bug the tier's
  own hermetic fixtures caught).
- **RCL-6** — body-hit evidence snippets (index schema 3 → 4 for manifest `head_commit`).
- **RCL-5** — an off-hot-path cross-encoder rerank on the explicit surfaces only.

### T4 — A corpus you can govern (v1.4.0, PR #12)

- **GOV-1** — a drainable contradiction inbox + `/hippo:resolve` (verdicts: supersede, scope,
  merge, dismiss — the dismiss ledger is per-clone and refuses to run without a durable home).
- **GOV-2** — `steer: pin`, the first author control axis (corpus format 2 → 3; bounded ×1.2
  boost, closed enum, mute deliberately deferred to the salience keystone).
- **GOV-3** — consolidation proposals carry evidence: a `--check` baseline (HEAD at proposal
  time) and a fenced `Rationale:` in the written body.
- **GOV-4** — floor/corpus change governance: a per-clone watermark diff surfaces what changed
  underneath you, exactly once.
- **GOV-5** — `/hippo:why` glass-box: per-hit "won via <backend>", salience components, and an
  honesty-ordered abstention receipt (with the fused-vs-cosine scale premise correction).
- **GOV-7** — author confidence tier (`draft|verified|authoritative`; corpus format 3 → 4;
  display-only — pinned never to touch ranking).
- **GOV-6** — the doctor trust scorecard: one deterministic line aggregating contested pairs,
  rule conflicts, rot, blind spots, orphans, pins, drafts, and the floor delta.

### T5 — Knowledge that grows itself (v1.5.0, PR #13)

- **GRW-1** — capture quotes its evidence: bounded VERBATIM diff hunks in the SessionEnd seed
  (tracked + untracked, binary-stripped, line-boundary byte cap), secret-linted at capture and
  hard-gated again before any hunk lands in a memory body; plus a per-seed salience label that
  orders the pending queue and marks trivial sessions (label, never a gate). Seed schema 1 → 2.
- **GRW-2** — Hebbian co-recall: pairs that co-surface across ≥3 distinct sessions become
  per-item untyped `[[wikilink]]` proposals at consolidate (sparse maps stay empty by design).
- **GRW-3** — a merge tier for near-duplicate COMMITTED memories on the audit sweep, scored by
  the calibrated write-time dup mechanic in both directions (never recall's fused scores — the
  scale premise correction recorded in-file), with a per-item fold/rewrite/close recipe enforced
  structurally by archive's inbound guard.
- **GRW-8** — a contradiction-adjudication fork on that same sweep (merge / contradicts / link),
  with the mislabel guard pinned: a reworded duplicate is NOT a contradiction. Accepted edges
  drain through the GOV-1 inbox automatically.
- **GRW-4** — the WHY captured in-session: an agent-driven decisions ledger
  (`capture --add-decision`, PreCompact nudge with the session id baked in) folded into the
  seed — transcription of what the user confirmed, never synthesis.
- **GRW-5** — commit-precision re-verify: commits since the last session's episode watermark
  that touch cited files join the reconsolidation worklist (`[since-watermark]`), recalled
  recently or not — no `.git/hooks` surface added.
- **GRW-6** — squash-merge healing: when a merge is detectable AND staleness baselines actually
  broke, a producer names the broken memories and routes each to a confirmed per-item
  `graduate` rebaseline (the healer was always `reverify_file`; now the break gets an offer).
- **GRW-7** — supersession is an auditable boundary: demote `--superseded-by` stamps the
  loser's `invalid_after` at the SUCCESSOR's commit date (ledger records boundary + successor;
  no new field, no schema bump). `fix --superseded-by` now refuses — the old combination wrote
  an edge and stamped nothing, a silent half-supersede.

New skills since v0.7.0: `/hippo:resolve`, `/hippo:why` (pinned skill list now 10). MCP: 4
tools (+`why`), 3 resources (+`hippo://scorecard`). Suite: 1371 hermetic tests; eval gates
unchanged and green throughout (self 0.98 / hard 1.0 / mrr 0.92 / p95 <30ms on the golden
corpus).

## v0.7.0 — 2026-07-08 — "Team & fleet — memory that survives more than one human and one repo"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged; the code swap on update is
sufficient. No corpus format (still 2) or index schema (still 3) change this release: the
multi-corpus fusion is a purely in-memory merge at recall time, so no persisted manifest shape
changed and every golden/byte-identity pin holds.

New env vars: `HIPPO_USER_MEMORY_DIR` (TEA-1 user-tier location, default `~/.claude/hippo-memory`),
`HIPPO_LOCAL_MEMORY_DIR` (TEA-3 private-tier location, default `.claude/memory.local`), and
`HIPPO_USAGE_USER` (TEA-5 usage-summary identity override). New surfaces: `/hippo:new --tier
{project|user|private}`, `python -m memory.soak --record-usage`, a SessionStart `portable_floor`
producer, a new `scale` pytest marker, and a nightly CI lane. New pytest marker: `scale`.

The theme of the release: memory stops being trapped in one person's one clone. A person-scoped
lesson learned in project A is now known in project B; a team corpus carries no one's personal
policies; a private note is recallable locally yet invisible in git; usage signals say plainly
when they only speak for this clone; and there is finally a documented way to make a memory
truly forgotten. The sharpest invariant — a user/private-tier memory is recallable everywhere
yet its content NEVER enters a project's git — is adversarially pinned.

### Shipped this release

- **TEA-1** — Two-tier corpus: a machine-local **user tier** (`~/.claude/hippo-memory`,
  `HIPPO_USER_MEMORY_DIR`) holding person-scoped `user`/`feedback` memories, indexed and recalled
  ALONGSIDE the project corpus via true two-corpus fusion (a single in-memory `LoadedIndex`, so
  BM25/dense/RRF/floor/knee/graph all run once, unchanged), with each hit provenance-labelled
  (`corpus`/`root`) and the floor drawn from BOTH (recall-dedup union + a bounded SessionStart
  `portable_floor` producer, since the user tier has no native always-load channel). Machine-local
  only (OQ-5). Each tier keeps its OWN gitignored index — no merged manifest is ever written to
  disk — so **no user-tier content enters the project's git**; `/hippo:new --tier user` routes the
  file and its floor pointer to the user tier's own `MEMORY.md`. An adversarial test pins that a
  user-tier write leaves the project git tree pristine (`status`/`ls-files`/manifest all clean).
- **TEA-3** — Private memory tier (`.claude/memory.local/`): a gitignored in-repo sibling merged
  into the same recall (labelled `private memory`), created by init and self-ignoring (SEC-3 `*`
  `.gitignore`) so it is invisible in `git status` and uncommittable even without the patch, while
  staying fully recallable locally. Its index nests inside the tier (its plain sibling would
  collide with the project's). A teammate who lacks the dir degrades to silence, never an error.
- **TEA-5** — Usage signals honest about scope: every coldness surface (soak CLI, archive report,
  audit skill) now LABELS the signal clone-local vs cross-clone, so "never recalled in THIS clone"
  is never mistaken for team-wide dead weight. Opt-in committed per-user summaries
  (`.claude/memory/.usage/<user>.json`, written by `soak --record-usage`, no session ids) that
  `curation_report`/`soak_status` UNION before judging coldness. `provenance.current_user_slug`
  is the first identity derivation in shipped code.
- **SEC-4** — Documented purge procedure: a `plugin/memory/README.md` section (remove the file →
  `git filter-repo` history scrub → index rebuild → ledger clear → recall verification),
  contrasted with the reversible `/hippo:archive` and whole-project `/hippo:remove`. The pointer
  is single-sourced in `secrets.REMEDIATION`, so both the write-time warning and doctor's secret
  check name it.
- **PRF-3** — 500-memory scale lane: a deterministic generated ~500-memory BM25 corpus asserting
  recall latency (warm p95 < 300ms), bounded output with a 45-memory match set (≤ `DEFAULT_K`,
  ≤ 9000 chars), and build/refresh time budgets — each failure naming the budget it broke.
  `scale`-marked so it stays off the hermetic and per-PR dense lanes; a new nightly CI job
  (`schedule:` 07:00 UTC) runs it.

## v0.6.0 — 2026-07-07 — "The write path — capture up to the approval gate; memory reaches every agent"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged; the code swap on update is
sufficient. (`hypothesis` was added for QUA-9's fuzz tests, but as a CI/test-only install line,
NOT a runtime dependency — mirroring QUA-10's pytest-timeout.) No corpus format or index schema
change this release.

New env var: `HIPPO_PENDING_DIR` (CAP-2 override for the gitignored draft-capture queue,
`.claude/.memory-pending/`). New surfaces: two skills (`/hippo:recall`, `/hippo:consolidate`),
a stdio MCP server, and three capture hooks (`PreCompact`, `SessionEnd`, `SubagentStop`).

The theme of the release: durable facts stop dying with the session. Capture is now automated —
but only ever UP TO an explicit approval gate, never past it. Nothing a capture pass produces
can reach `.claude/memory/` without a per-item, agent-gated write; that boundary is structural
(the capture module has no corpus writer) and adversarially tested.

### Shipped this release

- **CAP-1** — a `PreCompact` hook nudges the model to persist durable facts via `/hippo:new`
  before compaction discards session detail. Prompt-level, no Python spawn, no corpus writes.
- **CAP-2** — the `SessionEnd` draft-capture pass finally consumes the soaking episode buffer:
  it snapshots a session's episode replay (queries + recalled names + HEAD watermark) plus
  `git diff` since that watermark into ONE seed in the gitignored `.claude/.memory-pending/`
  queue, for per-item approval next session. The approval gate is structural — `memory.capture`
  imports no corpus writer — and a SessionStart producer surfaces the queue so it never soaks
  silently.
- **CAP-3** — `new_memory --check`: a dry-run that scores a captured candidate against the
  corpus with LIF-2's near-duplicate machinery WITHOUT writing, so approving a duplicate routes
  to update/supersede instead of a new file.
- **CAP-4** — `/hippo:consolidate`, a sleep-time skill that drains the capture queue
  (check-first), works the reconsolidation worklist, and refreshes the graph in one deliberate
  turn — keeping the hook path pure retrieval.
- **INT-1** — `/hippo:recall`, the read-side verb: "what do you remember about X" / list by
  type, reusing the exact hook ranking and annotating each hit with type, staleness, and graph
  neighbors.
- **INT-2** — a dependency-free stdio MCP server (`recall` / `new_memory` / `traverse` tools)
  giving mid-turn and subagent memory access; the hook path never imports it and still works
  with it absent.
- **INT-3** — a `SubagentStop` capture path (subagent discoveries become capture candidates)
  plus the Task-prompt injection pattern for policy-critical delegations.
- **INT-4** — the native-memory coexistence contract: a doctor check for symlink-target drift +
  native-layout change, a compatibility doc (`plugin/memory/NATIVE_MEMORY.md`), and a README
  positioning section.
- **INT-5** — one Python launch per prompt (`memory.recall --stdin-json` reads the hook payload
  and emits the output JSON itself, replacing three spawns + jq), and a doctor p95 hot-path
  latency check over the ledger.
- **QUA-9** — property-based (Hypothesis) fuzzing over the parsing surfaces: `split_frontmatter`
  body-preservation, `backfill`/`set_invalid_after` never touching the body, `clean_query`
  totality + input-bounded output, and `tokenize`/`normalize_slug` totality over Unicode.
- **DOC-7** — release engineering: `plugin.json` / `marketplace.json` bumped to 0.6.0 and kept
  in lockstep by a version-sync test + a tag-time `release.yml`; a doctor installed-vs-
  bootstrapped version-delta check; and `RELEASING.md` formalizing the branch → per-item commits
  → CHANGELOG capstone → squash-merge → tag process this file's header used to defer.

## v0.5.0 — 2026-07-07 — "The graph earns its keep: typed relations and closed lifecycle loops"

### Format changes

This release introduces corpus format versioning (COR-7) and bumps it once:

- **Corpus format 1 → 2** (GRA-4) — additive: `supersedes:` / `contradicts:` /
  `refines:` typed relations may now appear in frontmatter alongside untyped
  `[[wikilinks]]`. Existing corpora keep working unchanged; `/hippo:doctor`
  reports the corpus's stamped format vs. what the plugin expects and names
  the exact next step (stamp the marker, no autonomous migration).
- **Index schema 2 → 3** (RET-5) — adds a `source_commit_time` field to each
  manifest entry. COR-7's enforcement means a schema mismatch now costs
  exactly one full rebuild instead of silently serving a stale shape; nothing
  the operator needs to do by hand.

New env vars: `HIPPO_DUP_THRESHOLD` (LIF-2 near-duplicate cosine/BM25
threshold override) and `HIPPO_SALIENCE` (RET-5 salience-fusion ranking
blend — **default off**; the eval numbers on this release's fixtures showed
zero regression but also zero measurable lift, so it ships opt-in rather than
on-by-default until a corpus with real usage/staleness signal can prove it).

### Shipped this release

- **COR-7** — enforces index schema versioning and adds corpus format
  versioning, with a doctor-driven migration surface for both.
- **GRA-4** — typed edges (`supersedes` / `contradicts` / `refines`): recall
  demotes and annotates superseded memories pre-cut, flags contradictions,
  and `lint_links` catches dangling typed targets.
- **GRA-5** — `archive_memory` refuses (without `--force`) when inbound
  links — untyped or typed — still point at the target, and reports the
  referrer list either way.
- **GRA-9** — the reconsolidation worklist grows a report-only 1-hop
  "linked" column so a stale memory's neighbors surface for review too.
- **LIF-1** — demote gets a terminal state: it now chains straight into
  soft-invalidation (no second manual command), and a snooze/ack primitive
  stops re-nagged items from re-appearing every session.
- **LIF-2** — write-time near-duplicate/conflict detection (warn-only):
  `write_memory` surfaces nearest-neighbor matches and the `/hippo:new`
  skill routes the add / update-existing / supersede / skip decision.
- **LIF-3** — citation rot (a renamed/deleted cited file silently dropping
  out of `cited_paths`) is now surfaced instead of vanishing unnoticed.
- **LIF-4** — usage aggregates now survive telemetry-ledger rotation, and
  `archive_candidates` finally enforces its own ≥5-session soak gate.
- **LIF-5** — a missing/renamed `MEMORY.md` floor section is repaired or
  loudly reported — never a silent no-op.
- **LIF-6** — staleness and reconsolidation SessionStart producers share one
  computed stale set, so no memory is reported twice.
- **RET-5** — salience fusion (recency/usage/staleness ranking priors),
  shipped behind `HIPPO_SALIENCE` (default off) per this release's honest
  eval numbers.
- **RET-6** — drifted injections carry a one-line verify-at-use banner;
  reverifying a memory clears it on the next SessionStart.
- **TEA-4** — floor pointers insert at a deterministic sorted position
  instead of always appending at the section tail, so concurrent teammate
  writes to the same floor section merge cleanly instead of colliding.
- **QUA-8** — the skills-contract suite now extracts and checks every fenced
  code block in every `SKILL.md` for real (compiles, resolves every
  `memory.*` reference and call signature against the live package).
- **QUA-10** — `pytest.ini` hardening: `slow` marker, `filterwarnings =
  error` with targeted ignores, a timeout default, and the suite's one
  permanent skip repointed at the shipped operator-pack assets (now a real
  packaging gate — the suite ends at 0 skipped).
- **DOC-6** — `CONVENTIONS.md` seeded into every corpus by `/hippo:init`,
  documenting the frontmatter schema, type taxonomy, floor rule, typed
  relations, and evidence-block convention as actually shipped.

## v0.4.0 — 2026-07-06 — "Recall precision: earn every injected token, in every language"

### Breaking

Every `MEMOBOT_*` environment variable is renamed to `HIPPO_*` (suffix
unchanged). This is a **clean break** — per the one-canonical-name invariant
there are NO alias shims and NO fallback reads of the old prefix. Any
`MEMOBOT_*` var still set in a shell profile, CI secret, or `.env` file is now
silently ignored by every module; `/hippo:doctor` gained a check that warns
(by name) when a stale `MEMOBOT_*` var is present in the environment, but it
will not repair anything for you — rename it yourself.

Rename table (old -> new, suffix identical in every case):

| Old (removed)                    | New (canonical)                |
|-----------------------------------|---------------------------------|
| `MEMOBOT_DISABLE_DENSE`           | `HIPPO_DISABLE_DENSE`           |
| `MEMOBOT_TRUST_ALL`               | `HIPPO_TRUST_ALL`               |
| `MEMOBOT_TELEMETRY_DIR`           | `HIPPO_TELEMETRY_DIR`           |
| `MEMOBOT_MEMORY_DIR`              | `HIPPO_MEMORY_DIR`              |
| `MEMOBOT_TRUST_FILE`              | `HIPPO_TRUST_FILE`              |
| `MEMOBOT_TELEMETRY_MAX_BYTES`     | `HIPPO_TELEMETRY_MAX_BYTES`     |
| `MEMOBOT_INDEX_DIR`               | `HIPPO_INDEX_DIR`               |
| `MEMOBOT_EMBED_MODEL`             | `HIPPO_EMBED_MODEL`             |
| `MEMOBOT_RECENT_DAYS`             | `HIPPO_RECENT_DAYS`             |
| `MEMOBOT_DENSE_TIMEOUT`           | `HIPPO_DENSE_TIMEOUT`           |
| `MEMOBOT_REFRESH_TIMEOUT`         | `HIPPO_REFRESH_TIMEOUT`         |
| `MEMOBOT_EMBED_CHUNK_SIZE`        | `HIPPO_EMBED_CHUNK_SIZE`        |
| `MEMOBOT_GRAPH_SEEDS`             | `HIPPO_GRAPH_SEEDS`             |
| `MEMOBOT_BODY_RRF_WEIGHT`         | `HIPPO_BODY_RRF_WEIGHT`         |
| `MEMOBOT_DENSE_FLOOR`             | `HIPPO_DENSE_FLOOR`             |
| `MEMOBOT_KNEE_RATIO`              | `HIPPO_KNEE_RATIO`              |

Unrelated vars are untouched: `FASTEMBED_CACHE_PATH`, `HF_HUB_OFFLINE`,
`TRANSFORMERS_OFFLINE`, `XDG_CACHE_HOME`, and every `CLAUDE_*` var belong to
other systems and keep their names. `plugin.json` / `marketplace.json`
version numbers are intentionally NOT bumped in this release — that sync is
DOC-7's job (v0.6.0).

### Shipped this release

- **RET-1** — relevance floor + knee cutoff, so a low-signal query surfaces
  nothing rather than padding out to a fixed count.
- **RET-2** — body-aware indexing; recall is no longer capped at the
  `description:` field's discipline.
- **RET-3** — Unicode-aware tokenization plus an opt-in `--multilingual`
  bootstrap preset for non-English corpora.
- **RET-4** — mines fence/traceback identifiers instead of stripping them.
- **RET-7** — records the serving backend and audits the eval-set generation
  phase.
- **GRA-1** — 1-hop neighbor expansion in recall: the graph's first
  load-bearing ranking read.
- **GRA-2** — stem-normalized `LinkGraph` identity plus `inbound()` /
  `isolates()` backlink primitives.
- **GRA-3** — bootstraps the link graph at write time.
- **GRA-6** — persists the resolved edge list in the index (`links.json`).
- **PRF-1** — persists BM25 statistics; stops rebuilding the scorer per query.
- **PRF-2** — promotes `cold_latency` from report-only to a gated check.
- **COR-8** — emits true fused scores and cross-checks index vs. query
  embedding model.
- **COR-9** — makes soft-alias collisions ambiguous instead of
  first-claimant-wins.
- **QUA-6** — pins gate constants and adds a golden-corpus dense benchmark.
- **QUA-7** — adds subprocess tests for the `bin/hippo` launcher.
- **DOC-8** — this rename, plus a doctor check for stale `MEMOBOT_*` vars.

## Earlier releases

Pre-v0.4.0 releases (v0.2.0 "Truthful snap-in", v0.3.0 "Any repo, any
machine") predate this file — see `ROADMAP.yaml`'s `release_train` section
and the corresponding merged PRs (#3, #4) for their shipped-item lists.
