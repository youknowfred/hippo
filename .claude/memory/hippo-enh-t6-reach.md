---
name: hippo-enh-t6-reach
description: "Enhancement Tier T6 (v1.6.0, \"Reach\") — shipped 6/6 buildable items (RCH-6,1,2,3,4 done + the RCH-5 extract slice), PR #14 MERGED 2026-07-09 (squash d291c5c). The RCH-5 install/update legs this tier had to leave GATED on the v0.8.0 trust spine have SINCE SHIPPED — the spine landed in v0.8.0 and pack_install_item/pack_update_item are live (INT-16 gave them MCP tools in v1.16.0); T7's own gates cleared too. Historical record."
metadata: 
  node_type: memory
  type: project
  originSessionId: 5491ee71-6864-44bc-b111-c99fe6faa0e1
  last_verified: "2026-07-13T04:49:53.002911+00:00"
  verified_by: "81190215_youknowfred_users.noreply.github.com@2026-07-18T23:11:22.825166+00:00"
  cited_paths: ["plugin/.claude-plugin/plugin.json", "plugin/memory/portability.py", "plugin/memory/secrets.py", "plugin/memory/history.py", "plugin/memory/registry.py"]
  source_commit: "05b0c28349a9c3088a7870e5c0eee49859ce9bab"
  source_commit_time: 1784415607
---

Tier T6 (v1.6.0, "Reach") — session 2026-07-09. **COMPLETE, 6/6 buildable items,
[PR #14](https://github.com/youknowfred/hippo/pull/14) squash-MERGED to main as
`d291c5c` on 2026-07-09** (all 7 CI checks green: dense lane, 4-way hermetic matrix,
shellcheck; owner directed the merge; head branch `enh-t6-reach` deleted remote+local —
only `main` remains, local AND remote; `git diff main enh-t6-reach` was empty before
deletion, confirming zero content loss across the squash). Build order = the tier's
listed order (RCH-6, 1, 2, 3, 4, 5) from 7 commits on top of v1.5.0's `581abeb`; suite
green after every commit. NO plugin.json/CHANGELOG bump (tagging = releasing is a
separate owner step, v1.5.0 precedent) — post-merge suite re-verified directly on
`main`: 1444 passed / 12 deselected, byte-identical to the PR's own count.

> **SHA note (added 2026-07-20):** the per-item commit SHAs listed below were BRANCH commits,
> squashed away at merge and the branch deleted — they never existed on `main` and are gone by
> design, not broken. Only the squash-merge SHA above is resolvable. Navigate by PR number or
> commit subject. See [[pre-launch-commit-shas-are-dead-in-this-repo]].

SHIPPED THIS SESSION (one id-prefixed commit each):
- **RCH-6** `fb0cab1` — portability.py, the shared lift-time primitive.
  scan_portability(text, *, cited_paths=None) — secrets.py's structure with TWO
  differences: two severities routing differently ("warn" repo_coupling = strip/rewrite;
  "confirm" consequential_default = individual per-item yes) and details that ECHO the
  match (paths are the finding, not a credential). cited_paths default from
  read_provenance. Consequential catalog (co-authored-by / generated-with-claude /
  bypass-ci / skip-waiting-for-checks / merge --admin) is parity-pinned MANIFEST-DRIVEN
  to exactly the packs' confirm=individual set — both directions, so the lists cannot
  drift. Deliberately NOT in write_memory and NOT a doctor sweep (in a project corpus,
  cited_paths coupling is the provenance feature working).
- **RCH-1** `457b628` — /hippo:promote. new_memory.promote_memory: all guards BEFORE any
  write (refusal = zero filesystem change): invalid_after refuses; GRA-5 inbound guard
  REUSED from archive (the note's step-6 "delete the file" glossed over dangling links —
  the tree's own discipline won); consequential findings refuse without
  allow_consequential. ORIGIN STAMP metadata.origin "<repo>@<sha>" (source_commit else
  git_head else bare basename) rides a new `origin` kwarg on
  write_memory/_render_frontmatter (JSON-quoted, absence-emits-nothing → NO
  corpus_format bump). The re-render IS the provenance strip (cited/sc/sct/steer/
  last_verified die; body verbatim, no_links=True). Only after the dest write: git rm
  (fallback os.remove for untracked), new _remove_floor_pointer (_append's inverse,
  _pointer_name-precise), project refresh_index. Collision → refusal naming new_name=
  (never shadow). dest_tier="private" supported; promote_candidates = dry-run listing
  (coupling-free user/feedback, consequential counts). recall_view renders "learned in
  <origin>" via the EXISTING per-hit file read — zero index-schema change (the
  build_index entry shape is untouched; origin never enters the manifest).
- **RCH-2** `846f278` — /hippo:import, Cursor .mdc adapter. **THE TIER'S PREMISE
  CORRECTION (T3/T4/T5 class, recorded in-file as implementation_correction): real
  Cursor frontmatter is NOT valid YAML — `globs: **/*.ts` (the DOMINANT shape) starts a
  value with `*` (YAML alias), yaml.safe_load raises, parse_frontmatter returns {} for
  the WHOLE block — so the note's "rule_paths_globs parses exactly the .mdc shape
  (rename-tolerant)" was wrong; verified by direct probe before coding.** Shipped:
  import_mdc.parse_mdc (YAML-first + line-based fallback + comma-splitting of inline
  glob strings). Glob MATCHING reuse held exactly: resolve_globs = rules_plane's
  _expand_braces → _glob_to_re over _repo_paths_for_globs's tracked∪untracked-unignored
  universe. Flow: check_candidate FIRST (route=review holds unless allow_duplicate;
  rule_neighbors surface alongside), globs→concrete paths→bounded "Applies to:" body
  line→backfill stamps cited_paths (born staleness-tracked; only _CODE_EXTS extensions
  cite — .md/.go etc. stay body-text only), GOV-3 rationale line "imported from <rel>
  (Cursor .mdc rule)", idempotent re-import via exclusive-create. SECRET LINT IS A
  PRE-WRITE HOLD with NO override parameter (stricter than write_memory's
  warn-after-write — untrusted foreign input). Adapter = (discover, parse) pair over a
  shared tail so claude-mem/Mem0/sectioned-CLAUDE.md drop in later.
- **RCH-3** `4fcfef1` — decision-chain replay. New history.py: decision_chain walks
  supersedes+refines TRANSITIVELY both directions (typed_inbound = successors,
  typed_outbound = predecessors, declarer = newer side); contradicts NEVER traversed —
  branch-point annotations only; chronology from STAMPED read_source_commit_time
  (survives squash; "date unknown" over guessing); invalid_after boundaries per node;
  render_decision_history = ONE narrative ("chose X (2001-09) → … — refines X → … —
  supersedes Y [branch point…]" + "standing today:" live-tips line) behind TWO surfaces:
  the 5th MCP tool `decision_history` + `/hippo:recall --history`. Pinned test renamed
  exactly_four→exactly_five; server docstring was DOUBLY stale (said "Three tools"/"two
  RESOURCES" vs live 4/3 — now five/three; in-file correction recorded). history never
  imports mcp_server (pinned; NB the pin greps SOURCE TEXT — even a docstring mentioning
  the module name trips it).
- **RCH-4** `3e1eb42` — trust-gated --all-projects recall. registry.py:
  ~/.claude/hippo-projects.json (HIPPO_PROJECTS_FILE test override; realpath keys;
  sibling-key-preserving writes; read-time self-heal SKIPS vanished memory_dirs, never
  auto-prunes). Registered at /hippo:init's trust-mark step (one python -c does
  mark_trusted + register_project), de-registered in /hippo:remove (trust deliberately
  survives removal — it records the content review). recall.recall_all_projects: NOT
  _fuse_recall_tiers (trust-blind by design for the user's OWN tiers) — every registered
  source passes gate_repo_root/is_trusted AT QUERY TIME BEFORE its index loads; the
  current project gates exactly as recall() does; survivors + normal tier loadeds →
  _merge_loaded_indexes verbatim (first-wins; single-corpus fast path POST-TAGGED so a
  lone surviving source still carries root/corpus). Labels = repo basename, ~2 suffix
  for same-basename clones, self-registration deduped. Returns {hits, searched,
  skipped_untrusted, skipped_unavailable} — describe() prints a sources trailer naming
  every skip (inv3); hits render "from <repo>" in recall_view and fall through
  _CORPUS_MARKER to "(<label>)" in format_results (project/None byte-identical). Hook
  path pinned uninvolved (source-grep on recall.main + recall()). conftest gained
  HIPPO_PROJECTS_FILE isolation suite-wide. **Golden eval before == after: self 0.98 /
  hard 1.0 / mrr 0.9213, all gates pass** (p95 36–131ms across runs = machine-load
  noise, gate ≤300).
- **RCH-5** `d4047c3` — pack EXTRACT slice only; **GATE CONFIRMED STILL CLOSED**
  (SEC-5/6/7 v0.8.0 trust spine absent — re-verified: mark_trusted stores only
  trusted_at). packs.pack_extract(names, dest, *, memory_dir, repo_root, pack=basename
  (dest), version, title, description): validate-everything-first (missing/retired
  names, existing manifest.json or target .md → refuse whole extract, zero change);
  consequential findings AUTO-DERIVE manifest confirm:"individual"+reason (the shipped
  packs' own consent mechanism — linter⇄manifest parity makes derived markers
  equivalent); provenance + steer: stripped in the COPIES (source corpus untouched);
  metadata.pack/pack_version stamped; manifest in the SHIPPED shape,
  seed_by_default:false. test_packs extended: extracted packs pass the shipped parity
  contracts VERBATIM + a NEGATIVE-CAPABILITY pin (no install_pack/update_pack/... until
  the spine ships — then the pin is REPLACED by install/update contracts, not deleted).
  /hippo:pack skill = extract-only, gate stated in hard rules (inbound = /hippo:import
  or per-item /hippo:new). **Roadmap status: in_progress** (its acceptance_criteria are
  install/update-shaped) — the tier's done_means names this tail as allowed to slip.
- Tier flip `e314473` — T6 status → done with the gated-tail comment. (All 7 commits
  above are pre-squash SHAs — UNREACHABLE from `main` after the squash-merge + branch
  deletion, `git merge-base --is-ancestor` confirms; `git log`/`git show` against them
  will fail locally once GC'd. They remain browsable on GitHub's PR #14 "Commits" tab.
  `main`'s own history holds ONE commit for the whole tier: `d291c5c`.)

SCHEMA/FORMAT: corpus_format **4 (unchanged)**, index SCHEMA_VERSION **6 (unchanged)**,
capture seed **2 (unchanged)** — origin/pack stamps are additive frontmatter
(absence-emits-nothing), the origin renders via per-hit file reads not index entries.
**Re-bootstrap: NO** (requirements.txt untouched). Three NEW skills (promote, import,
pack) → pinned skills list now **13**; MCP now **5 tools / 3 resources** (pinned).

ENGINE STATE: suite **1444 passed / 12 deselected** (T5 baseline 1371; +73). New test
files: test_portability (13), test_promote (18), test_import_mdc (13), test_history (9),
test_all_projects (15) + extensions in test_mcp_server/test_packs/test_skills_contract.
SMOKE: 11/11 on a scratch two-repo setup (promote→origin→recall-in-B with "learned in
proj-a@<sha>" — TEA-1's criterion FINALLY met live; consequential refusal zero-change;
bare-star .mdc import with globs→cited_paths + idempotent re-import; supersedes-chain
replay narrative; all-projects trusted-served/untrusted-refused + CLI trailer; pack
extract; no-git-leak: both trees pristine, user tier outside both).

DECISIONS / GOTCHAS:
(1) Smoke fixture: bash printf %s leaves `\n` LITERAL inside args → silently broken YAML
frontmatter; use %b for any arg carrying escapes. (The failure LOOKED like two engine
bugs; both were the fixture. pack_extract's frontmatter validation caught it honestly.)
(2) promote's project-side removal needed the GRA-5 inbound guard the note omitted —
when a note glosses over an invariant the tree already enforces elsewhere, the tree
wins (same lesson as T5's GRW-3 guard-sequence correction).
(3) write_memory's user-tier backfill stamps EMPTY cited_paths/source_commit on every
user-tier write (pre-existing TEA-1 behavior) — promote tests assert the SOURCE's
values don't carry, not field absence.
(4) The mcp_server negative pin greps SOURCE TEXT — a new module's docstring must not
even NAME mcp_server if a mirror pin covers it.
(5) recall(query, k, index=merged, memory_dir=md) is the clean all-projects ranking
reuse: index= skips gate+fusion (per-source gating already done), memory_dir keeps
drift-patching alive via per-entry roots.
(6) _merge_loaded_indexes' single-corpus fast path returns the index UNTAGGED — any
caller that needs provenance labels must post-tag that case.
(7) Not taken (recorded): MCP recall tool scope arg (--all-projects stays CLI/skill —
smallest blast radius); scan_portability_corpus doctor sweep (portability is lift-time;
a corpus sweep would flag healthy provenance); format_results origin rendering (hook
injection stays lean — origin is a human-view tag; " (user memory)" already marks tier).

EVAL (golden corpus, real bge-small-en-v1.5, dense+bm25): self_recall@10 **0.98** ·
hard_recall@10 **1.0** · mrr@10 **0.9213** · p95 well under gate — identical quality
numbers before and after RCH-4, and identical to T4/T5 capstones.

NEXT: **Tier T7 "Learned ranking" (v1.7.0) — HARD-GATED, read its audit_note first.**
SIG-5/SIG-6 are blocked on RET-8, which is NOT SHIPPED and greenfield (a T7 session must
build RET-8 first — cross-file ROADMAP.v1 v0.9.0 work; SIG-5's notes carry the three
deliverables). RUL-6 is blocked on LIF-7's CAP-soak gate — an OWNER JUDGMENT with no
wired metric (do NOT proxy with soak.soak_status()). **RUL-7 is the ONLY freely
buildable T7 item.** Do not fake evidence to force a gate; ship RUL-7, record the rest
blocked-with-reason. Also open from T6: RCH-5 install/update unblock when the v0.8.0
trust spine (SEC-5/6/7) ships — the negative-capability pin in test_packs is the
tripwire to replace.

DEFERRED / BLOCKED: RCH-5 install/update (trust spine, above) — everything else shipped.

**SINCE UNBLOCKED (re-verified 2026-07-16): every gate this tier recorded has cleared.** The
v0.8.0 trust spine (SEC-5/6/7) shipped ([[hippo-v080-trust-spine]]), so RCH-5's install/update
legs landed — `packs.pack_install_item`/`pack_update_item` are live, the negative-capability
pin in test_packs is long replaced, and INT-16 exposed all five pack primitives as MCP tools
in v1.16.0 ([[hippo-v1160-pack-fix]]). T7's own "hard gates" also cleared: RET-8 shipped, SIG-5
was decided (salience OFF), SIG-6 shipped, and RUL-6 shipped once the owner cleared LIF-7
(`/hippo:promote-rule` is live). Read the NEXT block below as the record of what was true in
2026-07-09, not as today's frontier — the chain's current tip is [[hippo-enh-t15-sleep]].

Related: [[hippo-enh-t5-growth]], [[hippo-enhancement-roadmap]],
[[hippo-v1-roadmap-proposal]].
