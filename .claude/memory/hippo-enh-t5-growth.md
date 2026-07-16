---
name: hippo-enh-t5-growth
description: "Enhancement Tier T5 (v1.5.0, \"Knowledge that grows itself\") — shipped 8/8 items (GRW-1..8), PR #13 MERGED 2026-07-09 (squash af7d246) + TAGGED v1.5.0 (release commit 90600d7, release.yml green, first tag since v0.7.0); _SEED_SCHEMA 1→2 (queue-own, NO corpus bump); next tier T6 (Reach)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9daeb989-6a8b-402f-a56e-6a67e97e47d0
  last_verified: "2026-07-16T14:40:06.953363+00:00"
  cited_paths: [".github/workflows/release.yml", "plugin/.claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "plugin/memory/portability.py", "plugin/memory/registry.py"]
  source_commit: "bf57e6c1b86d1b0ef1c0090ab281459ceb8ab286"
  source_commit_time: 1784211707
---

Tier T5 (v1.5.0, "Knowledge that grows itself") — session 2026-07-09. **COMPLETE, 8/8,
[PR #13](https://github.com/youknowfred/hippo/pull/13) squash-MERGED to main as `af7d246`
on 2026-07-09** (all CI green: dense lane + 4-way hermetic matrix + shellcheck; owner
directed the merge; head branch `enh-t5-growth` deleted remote+local — only `main`
remains, local AND remote; merged-tree markers verified: _SEED_SCHEMA 2, co_recall_pairs,
watermark_stale_candidates, squash_merge_heal_producer). Shipped from 8 item commits on
top of T4's `6f4c9a3` + one GRW-4-prefixed shellcheck fix `6e18893`. Build order = the
tier's listed order (GRW-1, 2, 3, 8, 4, 5, 6, 7); suite green after every commit. CI
note: the shellcheck lane fails on ANY finding incl. info-level — SC1003 hit GRW-4's
`tr -d '"\\'` sanitizer; the fix is tr's own octal classes (`tr -d '\042\134'`), behavior
byte-identical. Local-git gotcha for the next session: `git fetch --prune <URL>
main:refs/remotes/origin/main` (URL-form remote) PRUNES the remote-tracking refs it isn't
fetching — it wiped refs/remotes/origin/* mid-cleanup here; keep the HTTP/1.1 workaround
fetch prune-less, and `git remote set-head origin main` restores origin/HEAD afterwards.

**TAGGED v1.5.0 2026-07-09** — the FIRST tag since v0.7.0 (T1–T4's target_versions were
never tagged; owner directed the release). Per RELEASING.md/DOC-7 the tag required a
release commit first (release.yml enforces tag == plugin.json == marketplace.json ==
newest CHANGELOG heading, all four): `90600d7` bumps both manifests 0.7.0→1.5.0 in
lockstep + ONE CHANGELOG v1.5.0 entry covering tiers T1–T5 (per-tier sections; honest
that intermediate versions were internal milestones; re-bootstrap: no; corpus_format 2→4
+ index schema 3→6 + seed schema 1→2 summarized as clean breaks). Annotated tag on
90600d7, subject "v1.5.0 — Knowledge that grows itself (enhancement tiers T1–T5)".
release.yml version-sync: SUCCESS. Future tiers: tagging = releasing = the manifest+
CHANGELOG bump moment; tier PRs themselves stay bump-free.

SHIPPED THIS SESSION (one id-prefixed commit each):
- **GRW-1** `5ad5628` — verbatim diff-hunk evidence + seed salience. capture._git_diff_hunks
  (tracked via `diff --unified=3 -M <watermark>`; UNTRACKED per-path via `diff --no-index
  /dev/null <path>` — run_git's check=False keeps stdout despite exit 1; binary sections
  dropped; _MAX_HUNK_BYTES=20_000 sliced ON A LINE BOUNDARY with a legible marker — run_git
  itself has NO cap, the note was right). **_SEED_SCHEMA 1→2, bumped ONCE for the tier**
  (the gitignored QUEUE's own schema, NOT a corpus_format event — no ED-4 machinery),
  adding diff_hunks + hunks_secret_flagged + salience + the decisions field GRW-4 fills.
  MANDATORY lint at capture: secrets.scan_text over hunks → flag only (queue is gitignored,
  same trust domain as the episode buffer); the consolidate SKILL gained the HARD
  refuse-the-fence gate (scan_with_remediation over the EXACT lines before any --body
  fence; write-time lint = backstop, not gate) and the T4-prebuilt hunks render hook is
  live. Salience = a value LABEL (never gate/prune): new_files/commit_landed/query-breadth/
  abstention-join; read_pending + --list order best-first, trivial sessions labelled, the
  SessionStart nudge counts them. resume_card passes include_hunks=False (no hunk
  subprocesses on its read-only path). AST no-corpus-writer pin intact.
- **GRW-2** `fe50668` — co-recall edges. telemetry.co_recall_pairs (beside
  abstention_backlog): per-session recalled names UNIONED FIRST (chatty session counts once
  structurally), distinct-session counts, _CORECALL_MIN_SESSIONS=3, below threshold → []
  (sparse map STAYS empty), capped, exclude_names param (the skill passes
  lint_floor.floor_memory_names — exclusion is caller-passed, NOT hardwired into telemetry;
  minor deviation from the note's signature, recorded here). Consumer = consolidate SKILL
  Step 4: tally → drop already-adjacent pairs (untyped+typed) → per-item proposals. **EDGE
  TYPE = option A, untyped wikilink** — zero schema bump; GRA-1's out|in expansion
  picks it up on the next links.json refresh, zero recall change.
- **GRW-3** `477383d` — merge tier on the audit sweep. **THE TIER'S PREDICTED PREMISE
  CORRECTION (same class as T4's GOV-5, recorded in-file as implementation_correction):**
  the note said "reuse the densification pass + _DUP_COSINE_THRESHOLD=0.80", but the
  densification pass reads recall()'s RRF-FUSED h["score"] (~1/61/backend, unthresholded
  top-K) — incommensurable with a cosine; and its "NOT _duplicate_neighbors" prohibition
  rested on that same false premise. Shipped the inverse: public
  new_memory.committed_duplicate_neighbors (write-time dup mechanic — dense cosine 0.80 /
  normalized BM25 0.45, own-name exclusion — fed the committed file's own text; public
  because skills never import underscore-privates), pair = candidate only when BOTH
  directions clear the calibrated threshold; invalid_after sides skipped via
  staleness.invalid_after_map. **Second note bug:** its (v)-supersedes-then-(vi)-archive
  recipe can't pass its own GRA-5 guard (the pointer IS a typed inbound edge) — shipped as
  two EXCLUSIVE endings: demote-in-place via the shipped `--superseded-by` flow, or archive
  after inbound-zeroing (guard-proven no-dangling). Merge = per-item agent edits (NO
  body-rewrite primitive exists or is simulated); "Merge candidates" report block; two-turn
  apply gate; hard rule pinning the fused-vs-similarity scale distinction. BM25 test gotcha
  worth keeping: a 4-doc corpus where the twin pair shares its vocabulary at df=2 has Okapi
  idf=ln(1)=0 → self-score 0 → honest "unscorable" refusal; committed-vs-committed dup
  tests need ≥6 distinct-vocab docs (write-time tests never hit this — the draft isn't in
  the corpus yet, df=1).
- **GRW-8** `7c7ba26` — contradiction fork on the SAME sweep. Three-way classifier
  ((a) concordant→merge, (b) disagreement→per-item contradicts / supersedes-when-clear,
  (c) neither→densification link) with the mislabel guard spelled out + pinned (reworded
  duplicate ≠ contradiction; (b) requires QUOTED opposing sentences). "Contradiction
  candidates" report block; apply arm = add_typed_relation then refresh_index
  (**add_typed_relation does NOT refresh links.json** — the GOV-1 inbox re-reads the corpus
  and sees the edge instantly, but recall's hot-path "contradicts — verify" note reads the
  cache). Verified live in smoke: new edge → resolve_view.unresolved_contradictions +
  contradiction_inbox producer, automatically.
- **GRW-4** `d0f78ec` — the WHY. telemetry.log_decision/read_decisions (decisions.jsonl,
  same keying/rotation/SEC-3 as episodes, 400-char bound); capture CLI --add-decision;
  SessionEnd folds exact-session-matched, deduped, bounded entries into seed["decisions"].
  PreCompact nudge hands the agent a RUNNABLE command with THIS session's id baked in
  (pure-bash sed extraction — still no Python spawn/no writes; sed/tr/head joined the hook
  test-harness PATH as genuine deps). Consolidate renders decisions in the GOV-3 rationale
  + folds into bodies. Transcription-not-synthesis enforced at every surface.
- **GRW-5** `e30e71e` — commit-precision re-verify. reconsolidate.watermark_stale_candidates
  (last session's watermark from the episode buffer — max-ts session, EARLIEST head_commit,
  the capture-seed convention — then `diff --name-only <wm>..HEAD` ∩ per-memory
  cited_paths; "" on unreachable sha → [] honestly, GRW-6 heals; _MAX_WATERMARK_PATHS=200).
  Merge point = new watermark_stale= kwarg on recalled_stale_worklist (exact stale= LIF-6
  shape: copies, dedup-by-name with stale-derived winning), unioned AFTER recency (precision
  beats recency — an un-recalled memory whose cited file a fresh commit touched joins),
  LIF-1 exclusions on the UNION → the ONE semantic_reverify gate. Both surfaces (dispatcher
  + CLI listing) carry it; [since-watermark] tags + only-when-present legend. The note's
  optional persisted-watermark extension NOT taken: producers only run at SessionStart, so
  it can't help the long-session case it targets.
- **GRW-6** `f776140` — squash-merge healing. staleness.unresolvable_baseline_names (per-item
  form; count keeps its pinned callers) + session_start.squash_merge_heal_producer:
  fires only on (merge signals: reflog merge/pull ∪ forge "(#N)" subjects ∪ non-current
  `branch --merged` — the current-branch "*" line must be excluded or the probe is always
  true) AND (names non-empty). Offer routes per-item through the consolidate drain →
  confirmed `--outcome graduate` → reverify_file re-baselines (the healer ALREADY existed).
  Registered directly after unresolvable_baseline (warning + offer adjacent). End-to-end
  test + smoke use a REAL squash-merge: **git show resolves unreachable-but-present objects,
  so a same-clone squash doesn't break baselines until `reflog expire --expire=now --all` +
  `gc --prune=now` (or a fresh clone)** — that's the honest reproduction.
- **GRW-7** `5dcc0aa` — successor-date stamp. Demote+superseded_by now passes the SUCCESSOR
  file's last-commit date (provenance.git_last_commit_with_time on the .md — its authorship
  moment, NOT read_source_commit_time which is its cited-CODE baseline) as ts into the
  demote arm's ONE existing set_invalid_after call (ts param shipped since LIF-1; None →
  now-UTC covers the uncommitted successor). Ledger event gains invalid_after +
  superseded_by; CLI prints the boundary. **NO new field, NO schema bump** (pinned via the
  shipped read_invalid_after). **Deliberate behavior change, note-sanctioned:**
  fix+superseded_by now REFUSES (was: edge-written-nothing-stamped, a silent
  half-supersede) — _EDGE_WRITING_OUTCOMES={demote}; the old fix-combo test flipped to
  assert the refusal; README + CONVENTIONS.md updated in the same commit.

SCHEMA/FORMAT: corpus_format **4 (unchanged)**, index SCHEMA_VERSION **6 (unchanged)** —
the audit's "none should force a break" held. The ONLY schema event was _SEED_SCHEMA 1→2
(gitignored queue's own version, GRW-1+GRW-4 coordinated, bumped once). Co-recall edges =
untyped wikilinks (no TYPED_RELATIONS change). **Re-bootstrap: NO** (requirements.txt
untouched). No plugin.json/CHANGELOG bump (T1–T4 precedent).

EVAL (golden corpus, real bge-small-en-v1.5, dense+bm25, this machine): self_recall@10
**0.98** (≥0.90) · hard_recall@10 **1.0** (≥0.80) · mrr@10 **0.9213** (≥0.60) · recall_p95
**22.43ms** (≤300) — ALL GATES PASS, byte-identical quality numbers to T4's capstone (GRW
touched zero ranking code, by design). Dense lane (-m network): 2 passed.

ENGINE STATE: suite **1371 passed / 12 deselected** (T4 baseline 1324; +47). New tests:
capture hunks/salience/decisions, telemetry co-recall + decisions ledger,
committed-dup both-directions, audit-skill content pins (merge scale + mislabel guard),
hooks PreCompact session-id nudge, watermark candidates/union, squash-heal producer + REAL
squash e2e, successor-date stamp + ledger + refusal. SMOKE: 31/31 on a scratch repo
(fail-count via temp file; `ls -a` for the dotted queue dir) — hook-path capture with
flagged secret, refuse-the-fence, co-recall threshold both ways, merge candidate at
correct BM25 scale (0.56/0.59 ≥ 0.45), contradicts→inbox→producer, watermark worklist,
real-squash heal + self-clear, successor-date stamp == actual git date, fix-combo refusal,
SessionStart blocks.

DECISIONS / GOTCHAS:
(1) **`pytest -q` on top of addopts' own -q = -qq, which SUPPRESSES the summary line** —
the T4 "trust the summary line" gotcha in a new costume; run bare `pytest` from the repo
root and read the real count.
(2) The seed keeps flagged hunks (gitignored, episode-buffer trust domain) — the flag +
skill gate + write-time backstop are the defense; nothing scrubbed at capture.
(3) Salience "new_files" counts untracked at capture; in test repos the corpus .md files
themselves are untracked and inflate scores — tests control for it; real repos commit
their corpus.
(4) GRW-2's exclude_names is a param, not hardwired: keeps telemetry decoupled from
lint_floor; every documented consumer passes floor names.
(5) Producer honesty pattern reused twice: only-when-present header legends ([since-
watermark], GRA-9's linked) keep pre-change renders byte-identical.
(6) session_start result-shape pin: semantic_reverify's result gained "invalid_after" —
one exact-dict test needed the new key added; grep for exact-shape pins when extending
result dicts.
(7) GRW-6 smoke/test craft: `git branch --merged` always lists the CURRENT branch —
exclude the "*" line or the probe is vacuously true.

NEXT: **Tier T6 "Reach" (v1.6.0)** — items [RCH-6, RCH-1, RCH-2, RCH-3, RCH-4, RCH-5].
Read each RCH item's implementation_notes + T6's audit_note FIRST. Audit highlights:
RCH-5's trust-spine gate (SEC-5/6/7, v0.8.0) CONFIRMED CLOSED — only its EXTRACT slice is
buildable; install/update stay planned until the spine ships. Net-new primitives:
portability.py, new_memory._remove_floor_pointer, origin kwarg on write_memory,
registry.py + ~/.claude/hippo-projects.json + trust-gated recall_all_projects (do NOT
reuse the deliberately-trust-bypassing _fuse_recall_tiers), 5th MCP tool (pinned
exactly-N tests: currently 4 tools / 3 resources). RCH-2 reuses T2's rules_plane glob
helpers.

DEFERRED / BLOCKED: none — all 8 T5 items shipped. (Two in-scope narrowings, recorded
above: GRW-5's persisted-watermark extension skipped with reasoning; GRW-7's fix+
superseded_by refusal is the note's sanctioned option.)

Related: [[hippo-enh-t4-governance]], [[hippo-enhancement-roadmap]],
[[hippo-v1-roadmap-proposal]].
