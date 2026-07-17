---
description: Deep, judgment-based self-audit of the LOCAL memory tooling and corpus (the `memory` package + `.claude/memory/`) — cross-references signals no single tool combines, reads the top flagged memories, and produces one ranked report. Triggers: "audit memory", "memory self-audit", "how healthy is my memory system", "/hippo:audit". Doctor answers "is the plumbing working"; audit judges corpus CONTENT. Report-only by default (mode flags in the body). NOT for auditing your product's own code.
---

# /hippo:audit — Self-Audit of the Agent-Memory Tooling & Corpus

A genuinely insightful health check of the `memory` package + the `.claude/memory/` corpus —
the tooling that runs the agent's own memory, not your product's own code. Cross-references
signals no single CLI tool combines, reads and judges the top-priority flagged memories, and
produces one ranked report.

This is **not** a mechanical dump of `eval_recall`/`soak`/`staleness`/`reconsolidate`/`archive`/
`links`/`lint_floor` output concatenated together — all of that is already directly runnable by
hand, and the `SessionStart` hook already gives you a free summarized slice of it every session.
The value this skill adds is the cross-referencing, the deep-dive judgment, and the prioritized
synthesis. If a run of this skill produces six labeled appendix sections and a ranked list that
just points at them, it has failed its own purpose — see Hard Rules.

## Why this is corpus self-audit, not your product's own code audit

This skill's scope is the memory ENGINE and its CORPUS — it never reads, audits, or reasons
about your product's own architecture, features, or business logic. If your project has its own
product-level audit workflow (a scaling audit, a security audit, a synthesis-quality audit,
etc.), that is a completely separate concern from this skill and should stay that way — don't
route product-code audits through this skill, and don't fold this skill's corpus-maintenance
scope into a product audit pipeline. The `memory` package already has its own docs and its own
test fixtures (`tests/` in the engine repo) — it's a first-class subsystem with its own tooling
literacy; this skill matches that separation rather than blurring it.

## When NOT to use

- **Your product's own code/architecture audits** — use whatever audit workflow your project has
  for that; this skill has no opinion on your product code.
- **"Is my memory stale right now?"** — already answered for free every session by the
  `SessionStart` hook's staleness + reconsolidation producers. Don't re-run this heavyweight
  skill for something the banner you're already looking at already told you.
- **A single memory you already know is wrong** — just fix it and re-verify that one memory. No
  need for a full audit pass.

## Inputs / mode flags

- **`--apply`** — after the report is written, execute the approved verdicts via the tools' own
  single-item, no-bulk primitives (`reconsolidate.semantic_reverify`, `staleness.set_invalid_after`,
  `archive.archive_memory`). Absent this flag, the run is **report-only by default** — a bad
  bulk re-baseline of provenance across dozens of memories does not revert as cleanly as a bad
  code change does, so applying anything is opt-in, not the default.
- **`--deep-dive-n N`** (default `8`) — caps how many Phase 2 candidates get the full
  read-body + git-diff treatment in Phase 3. Corpus-wide cross-referencing in Phase 2 stays
  cheap and uncapped regardless of N.
- **`--window-sessions N`** (default `30`) — passed to `reconsolidate.recalled_stale_worklist(window_sessions=N)`.
- **`--skip-eval`** — skip the `eval_recall.evaluate()` cluster (useful for a fast
  drift/curation/archive-only pass, or when the dense/fastembed model cache is cold and you
  don't want to pay the load).
- **`--generate-eval-set [N]`** (N default `12`) — RET-7: (re)generate this project's own
  `.claude/memory/.audit-fixtures/recall_hard_set.yaml` from a fresh sample of THIS corpus
  (see Phase 0.5). **Never runs on its own** — regeneration is explicit, one invocation at a
  time, never autonomous; absent this flag Phase 0.5 is skipped entirely and Phase 1 just
  discovers whatever fixture (if any) already exists on disk, exactly as before this item.

No `--full` / `--semi-attended` / tier flags. This is a single-pass audit with one apply gate,
not a multi-PR roadmap.

## Phase 0 — Preflight

- **Guard `CLAUDE_PLUGIN_DATA` first** (shared across all hippo skills — the venv paths
  below expand it):
  ```bash
  [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell — this does NOT necessarily mean Claude Code is too old: on some surfaces (e.g. Claude Desktop) the agent's Bash tool never inherits plugin-scoped env vars even on a fully current, correctly-bootstrapped install, since only hippo's MCP server and hooks (not the general Bash tool) receive them. If this is Desktop, run this SAME audit through hippo's MCP tools instead of the bash blocks: Phase 0.6's drafting → the abstention_fixtures tool (action='draft', then per-item action='confirm'); Phase 1's gather script → the audit tool (skip_eval / window_sessions mirror the flags; it returns the same cross-referenced JSON, READ-ONLY — it never writes the history file, so do the Phase 1/3 history bookkeeping with your own file tools on .claude/state/memory-audit-history.json); Phases 2-4 are judgment and reporting, exactly as written below; Phase 5 applies stay per-item through the existing tools — reconsolidate (action='reverify', outcome=…), dream (action='dedup_merge'), and hand edits for link-densification. If this IS a genuine terminal Claude Code session and you still see this, Claude Code likely is too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
  ```
- Confirm every tool imports cleanly:
  ```bash
  . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
  hippo_resolve_py
  "$PY" -c \
    "from memory import eval_recall, soak, staleness, reconsolidate, archive, links, lint_links, lint_floor, telemetry, provenance"
  ```
  (`$PY` falls back to bare `python3` pre-bootstrap; BM25-only tools still import fine, only
  `eval_recall`'s dense-model-dependent paths need the venv.) Halt if this fails — a broken tool
  means every downstream signal in this audit is suspect, not just cosmetic.
- Discover corpus scale fresh, never hardcode it: `ls .claude/memory/*.md | wc -l` and
  `wc -l .claude/memory/*.md | tail -1`. State the actual numbers in the report header.
- **Discover optional hard-set/relevance-set fixtures, don't assume they exist.** Look for
  `.claude/memory/.audit-fixtures/recall_hard_set.yaml` and
  `.claude/memory/.audit-fixtures/recall_relevance_set.yaml` in THIS project. These are
  hand-curated query→expected-memory judgments calibrated against THIS project's own corpus
  content — they do not ship with the plugin (a fresh install has no calibration data yet) and
  must never be copied from another project's corpus, since the judgments are meaningless
  against different memory content. If absent, Phase 1 runs `eval_recall.evaluate()` WITHOUT a
  hard-set (self-recall/precision-only gates still run; hard-set/MRR gates report "no fixture —
  skipped" rather than a false pass or fail). If you want these gates going forward, either
  hand-curate a small hard-set file for your own corpus over time — see the engine repo's own
  `tests/fixtures/recall_hard_set.yaml` for the expected YAML shape as a reference — or generate
  a first draft with `--generate-eval-set` (Phase 0.5 below).
- Load prior run history if present: `.claude/state/memory-audit-history.json` (recommend
  git-tracking this file, same precedent as any other durable project state). If absent, this is
  run #1 — say so explicitly in the report; a fresh corpus with no history is not the same
  finding as "nothing recurs," and conflating the two is exactly the kind of overclaim Phase 4's
  honesty section exists to prevent.

## Phase 0.5 — Eval-set generation (RET-7, only under `--generate-eval-set`, agent-gated)

**Skipped entirely unless `--generate-eval-set` was passed.** This is what makes "uniform
recall efficacy" checkable on ANY project's own corpus, not just the engine repo's own
hand-curated `tests/fixtures/` — every install gets a way to measure whether hybrid recall
actually works against ITS OWN memory content, without depending on someone hand-writing
paraphrase queries from scratch.

1. **Sample up to N (default `12`) memories across `metadata.type`.** Read every memory's
   frontmatter (`description`) and, for a diverse sample, a short excerpt of its body (first
   ~200 chars past the frontmatter is enough — this is sampling for query material, not a
   Phase 3 deep-dive). Spread the sample across whatever `type` values exist in this corpus
   (e.g. `user`/`project`/`reference`/`feedback`) rather than letting one dominant type crowd
   out the rest — a hard-set that only ever tests one type isn't measuring "this corpus's
   recall quality," just one slice of it.

2. **Synthesize a cross-vocabulary PARAPHRASE query per sampled memory — the agent does this,
   not a script.** For each sampled memory, write ONE query that:
   - **Never reuses a verbatim substring of the memory's `description`.** Copying the
     description's own words (or a near-identical rewording) tests nothing dense embeddings
     don't already get for free via lexical overlap — this is the exact failure mode
     `recall_hard_set.yaml`'s own header warns about (BM25 alone would already pass a
     verbatim-ish query).
   - **Paraphrases with synonyms and a different register** — e.g. task-phrased as a question
     ("how do I..."), or restated from the *consumer's* point of view rather than the
     memory's own descriptive voice. If the memory's description says "reranker cross-encoder
     fallback circuit breaker," a good paraphrase query is "which model re-scores search
     results if the primary one errors out" — not "reranker fallback logic."
   - **Targets exactly one memory as `expected`** (a list, per the schema — usually length 1;
     only list more than one stem if the query genuinely and legitimately maps to a small
     cluster, mirroring `tests/fixtures/recall_hard_set.yaml`'s own convention).
   Skip a sampled memory if you cannot produce a genuine paraphrase for it (e.g. its
   description is already terse enough that any query restates it near-verbatim) rather than
   forcing a low-quality row — fewer honest rows beat a padded fixture.

3. **Record the run's own serving backend truthfully in the header** — check
   `hippo_resolve_py`'s resolved `$PY` actually has a warm/available dense model (the same
   check Phase 0's import line already exercises) before deciding what to write:
   `generated_with_backend: dense+bm25` only if dense is genuinely available this run, else
   `generated_with_backend: bm25-only`. **Never write `dense+bm25` speculatively** — an
   inflated claim here is exactly what RET-7's `backend_mismatch` flag exists to catch on a
   LATER run, so getting it right at generation time avoids a false alarm against your own
   fixture. Add `generated_at: <today's date>`.

4. **Write the draft to `.claude/memory/.audit-fixtures/recall_hard_set.yaml`**, YAML shape:
   ```yaml
   generated_with_backend: dense+bm25
   generated_at: 2026-07-06
   ---
   - query: "<paraphrase query>"
     expected: [<memory_stem>]
   ```
   If a fixture already exists at that path, **do not silently overwrite it** — show the human
   both the existing file and the newly-generated draft, and let them choose to replace,
   merge, or discard the draft. This is a corrective/generative write, so it follows the same
   per-item, human-in-the-loop discipline as every other write this skill makes.

5. **Present the full generated file to the human and ask before it is used.** State the
   sample size, the type spread, and explicitly flag any query you were unsure paraphrased
   well enough. This is an **agent-gated write** — Phase 1's `eval_recall.evaluate()` call
   must not consume a freshly-generated fixture the human hasn't at least skimmed once. If the
   human wants changes, make them and re-present; only proceed to Phase 1 once they confirm.

6. **Regeneration is explicit, never autonomous.** This phase never runs unless
   `--generate-eval-set` was passed on THIS invocation — a routine `/hippo:audit` run never
   silently regenerates or touches an existing hard-set fixture.

Once confirmed, Phase 1 picks up the (possibly just-written) fixture via its existing
discovery logic — no separate wiring needed; `_default_fixture_path` already probes
`.claude/memory/.audit-fixtures/` first (see Phase 1's `hard_set`/`rel_set` resolution below).

## Phase 0.6 — Abstention-fixture drafting (SIG-6 — routine drafting, gated admission)

Phase 0.5 samples what someone thought to test; this phase grows the same fixture from what
users actually ASK. The SIG-3 blind-spot backlog (recurring queries recall abstained on) is
turned into CANDIDATE fixture rows — so KPI-4's yardstick self-populates from real
un-answered traffic. Runs on every audit, no flag: the drafter only APPENDS to a gitignored
drafts QUEUE (`.claude/.memory-pending/recall_hard_set.drafts.yaml` — raw ledger query text
stays in the SEC-3 self-ignoring pending dir, the capture-seed precedent), which is
bookkeeping like the Phase 1 history-file write, not a corpus mutation. ADMISSION into the
tracked fixture is the gated act, and it is always per-item + human-approved.

```bash
"$PY" - <<'PYEOF'
import json
from memory.eval_recall import draft_abstention_fixtures
print(json.dumps(draft_abstention_fixtures(), indent=2))
PYEOF
```

(Under `--skip-eval`, pass `draft_abstention_fixtures(probe=False)` — the recall probes are
what would pay a cold dense-model load; without them `current_hits` stays `[]` and the header
honestly claims no backend.)

The summary names what was `added` vs `kept` (existing draft rows — including judgments you
filled on a prior run — are preserved byte-verbatim; re-drafting never clobbers them) and
`skipped_tracked` (clusters whose loop already closed). Then judge every UNCONFIRMED row in
the drafts file, newly added or not. Exactly one verdict each:

- **(a) A real, existing memory should answer it** — fill `expected: [<stem>]` in the drafts
  file and present the row to the human: the query, the proposed stem, and whether recall
  CURRENTLY surfaces it (`current_hits` is judgment material, not a verdict). An admitted row
  that currently FAILS is legitimate — it documents a recall gap the corpus should close —
  but say so explicitly: it reddens `hard_recall@10` until the gap is fixed, and admitting
  that tripwire is precisely the human's call. On explicit approval, admit it (see below).
- **(b) No existing memory answers it** — a CAPTURE gap, not fixture material. Route it to
  `/hippo:consolidate` (the SIG-3 nudge's own path) and leave the row drafted. **Never create
  a memory just to make a fixture admissible** — that inverts the loop (the killed
  demand-gap-auto-draft); capture decisions are made on their own merits in the drain.
- **(c) Noise** (tool spew, a malformed preview, a never-again question) — delete the row
  from the drafts file.

Admission, per approved row (never a loop over the file):

```bash
"$PY" - <<'PYEOF'
import json
from memory.eval_recall import confirm_hard_set_row
print(json.dumps(confirm_hard_set_row("<the query>", ["<stem>"]), indent=2))
PYEOF
```

The row lands in `.claude/memory/.audit-fixtures/recall_hard_set.yaml` tagged
`category: abstention` (RET-8's per-category bucket — the eval now measures the gap-closing
loop end to end), the fixture's existing bytes are preserved verbatim above the append, and
the drafts-queue row drains. The primitive REFUSES fabricated stems, duplicate queries, and
empty judgments — a refusal is a verdict to report, never a thing to work around.

## Phase 1 — Gather (sequential, in-process — not parallel subagents)

Run every signal source **in one sequential pass in this session**, not via spawned subagents.
Every signal here is a git-local, sub-second-to-a-few-second call over a corpus that is (for any
project this plugin is realistically installed on) well under a thousand files — Phase 2's whole
value is cheap **in-process set/dict arithmetic across all of them at once**; a subagent boundary
would force that state through serialization for zero parallelism win. Revisit this only if a
corpus grows 5-10x past what's realistic today (see Hard Rules).

Run this as one script (adjust flags per the invocation; `PY` is resolved by
`hippo_resolve_py` in Phase 0 — `${CLAUDE_PLUGIN_DATA}/venv/bin/python` with
`PYTHONPATH=${CLAUDE_PLUGIN_ROOT}` set, or bare `python3` pre-bootstrap):

```bash
"$PY" - <<'PYEOF'
import json, os, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

from memory.provenance import resolve_dirs
from memory import eval_recall, soak, staleness, reconsolidate, archive, links, lint_links, lint_floor, telemetry
from memory.build_index import memory_doc_text
from memory.recall import recall

SKIP_EVAL = False          # --skip-eval
WINDOW_SESSIONS = 30       # --window-sessions
LINK_SIM_K = 3             # GRA-3 densification: candidates recalled per sampled memory
LINK_SIM_MAX_SAMPLE = 200  # cap: corpus-size assumption (see Hard Rules) — recall() per file
                           # is O(1) index lookups, not a re-embed, but stay bounded regardless

memory_dir, repo_root = resolve_dirs()
repo_root_p = Path(repo_root)

# Optional, project-local hard-set/relevance-set fixtures — NEVER assume these exist (see
# Phase 0). A fresh install / new project has no calibration data yet.
fixtures_dir = repo_root_p / ".claude" / "memory" / ".audit-fixtures"
hard_set = str(fixtures_dir / "recall_hard_set.yaml") if (fixtures_dir / "recall_hard_set.yaml").exists() else None
rel_set = str(fixtures_dir / "recall_relevance_set.yaml") if (fixtures_dir / "recall_relevance_set.yaml").exists() else None

# --- raw signals (evaluate() does NOT apply main()'s CLI defaults — pass repo_root and both
# fixture paths explicitly; None is a supported, gracefully-degraded input for a project with
# no hand-curated hard-set yet) ---
ev = {} if SKIP_EVAL else eval_recall.evaluate(
    repo_root=repo_root, hard_set_path=hard_set, relevance_set_path=rel_set
)
soak_status = soak.soak_status()
curation = soak.curation_report(memory_dir)
strength = soak.compute_strength_scores()          # absence == 0.0, never an explicit key
unparseable = staleness.find_unparseable(memory_dir)
stale = staleness.find_stale(memory_dir, repo_root)                 # full, uncapped
worklist = reconsolidate.recalled_stale_worklist(
    memory_dir, repo_root, window_sessions=WINDOW_SESSIONS
)
archive_cands = archive.archive_candidates(memory_dir, repo_root)
graph = links.build_graph(memory_dir)
link_report = lint_links.lint(memory_dir)
floor = lint_floor.floor_violations(memory_dir)

# links.py speaks STEMS ("foo", never "foo.md") since GRA-2 — same identity as
# staleness/soak/archive, so graph output joins below with no conversion step.
names = set(graph.files) if graph else set()
never_recalled = set(curation["never_recalled"])
orphans = set(graph.orphans()) if graph else set()
unparseable_set = set(unparseable)

# --- Join 1: cascading blind spot (invisible to 3 tools at once — auto-included in Phase 3
# regardless of ranking score, per Phase 2) ---
join_cascading_blindspot = sorted(unparseable_set & never_recalled & orphans)

# --- authority-citation scan: does anything in THIS project's own governance docs (an
# AGENTS.md/CLAUDE.md-equivalent, rule files, agent/skill definitions) cite a memory by name,
# yet recall telemetry says nobody actually retrieves it? Adjust GOV_GLOBS to whatever your
# project's own governance-doc convention is — this default covers the common Claude Code
# conventions (CLAUDE.md, .claude/rules, .claude/agents, .claude/skills). ---
GOV_GLOBS = ["CLAUDE.md", "AGENTS.md", ".claude/rules/*.md", ".claude/agents/*.md",
             ".claude/skills/**/*.md"]
CITATION_RE = re.compile(r"`([A-Za-z0-9_-]+(?:\.md)?)`")
cited_tokens = set()
for pattern in GOV_GLOBS:
    for gf in repo_root_p.glob(pattern):
        try:
            text = gf.read_text()
        except OSError:
            continue
        for m in CITATION_RE.finditer(text):
            cited_tokens.add(m.group(1)[:-3] if m.group(1).endswith(".md") else m.group(1))
cited_by_governance = names & cited_tokens

# --- Join 2: authority-evidence mismatch ---
join_authority_gap = sorted(
    name for name in cited_by_governance if strength.get(name, 0.0) < 0.15
)

# --- IOP-1 foreign-dialect radar: the rule dialects hippo does NOT own (.cursor/rules
# Cursor .mdc, .github/instructions Copilot, watch-only unratified .agents/rules) —
# censused by glob-presence alone, divergence-vs-governance via rule_dup_candidates,
# existence-only .mdc citation/glob rot. Report-only, and its FOREIGN_GLOBS surface
# never merges into the GOV_GLOBS authority scan above (inv5: un-owned foreign content
# is never hippo authority, never a recall pointer, never an import candidate here). ---
from memory.rules_foreign import foreign_radar
foreign_dialects = foreign_radar(str(repo_root_p))

# --- Join 5: graph-isolated watch-list — LinkGraph.isolates() (zero-inbound AND
# zero-outbound; the graph inverts adjacency once internally, never re-derive it here).
# NEVER archive-eligible on its own — strictly weaker than
# archive.archive_candidates()'s real 4-way gate. ---
join_graph_isolated_watchlist = graph.isolates() if graph else []

# --- GRA-3 link-densification pass: SUGGESTIONS only, never an autonomous body edit. ---
# Reuses recall() over each sampled memory's OWN doc_text (name + description — the exact
# query new_memory's write-time discovery uses, GRA-3) to find its highest-similarity
# EXISTING neighbors that are NOT already an outbound edge. This is read-only: it proposes
# high-similarity pairs for the agent to review in Phase 3/5 and hand-add as [[wikilinks]]
# ONE memory at a time if approved — it never writes to any memory body itself. Skipped
# gracefully (empty list) when the corpus has no graph yet or recall degrades to nothing.
link_density_suggestions = []
if graph and names:
    for name in sorted(names)[:LINK_SIM_MAX_SAMPLE]:
        try:
            text = (Path(memory_dir) / f"{name}.md").read_text()
        except OSError:
            continue
        query = memory_doc_text(name, text)
        existing_out = graph.adjacency.get(name, set())
        hits = recall(query, k=LINK_SIM_K + 1, memory_dir=memory_dir, repo_root=repo_root)
        candidates = [
            {"name": h["name"], "score": h["score"]}
            for h in hits
            if h["name"] != name and h["name"] not in existing_out
        ][:LINK_SIM_K]
        if candidates:
            link_density_suggestions.append({"memory": name, "candidates": candidates})

# --- GRW-3 merge-candidate pass: near-duplicate COMMITTED pairs, SUGGESTIONS only. ---
# Committed-vs-committed duplicate detection reuses the WRITE-TIME dup checker
# (new_memory.committed_duplicate_neighbors: dense cosine >= 0.80 when available,
# normalized BM25 >= 0.45 otherwise — the calibrated [0,1]-ish similarity scales) fed each
# sampled memory's own on-disk text. The densification hits above carry recall()'s
# RRF-FUSED scores (~1/60 per backend) — NEVER compare those to a cosine threshold; that
# scale mismatch is exactly why the merge tier calls the dup checker instead. A pair is a
# merge CANDIDATE only when BOTH directions clear the threshold (one-way similarity is not
# a merge signal) and NEITHER side already carries invalid_after (a demoted memory is
# supersede territory — merging it would resurrect a claim someone demoted).
merge_candidates = []
if names:
    from memory.new_memory import committed_duplicate_neighbors
    invalidated = set(staleness.invalid_after_map(sorted(names), memory_dir))
    dup_hits = {}
    for name in sorted(names)[:LINK_SIM_MAX_SAMPLE]:
        neighbors, _dup_note = committed_duplicate_neighbors(name, memory_dir)
        dup_hits[name] = {n["name"]: n["score"] for n in neighbors}
    for a in sorted(dup_hits):
        for b, s_ab in sorted(dup_hits[a].items()):
            if b <= a or a in invalidated or b in invalidated:
                continue  # canonical order (count each unordered pair once) + demote guard
            s_ba = dup_hits.get(b, {}).get(a)
            if s_ba is None:
                continue  # one-way similarity is not a merge signal
            merge_candidates.append({"pair": [a, b], "score_a_to_b": s_ab, "score_b_to_a": s_ba})

# --- SEN-3: ungrounded-prescription sweep. Classify each memory grounded / ungrounded-
# prescription / observation; the ungrounded ones are agent-voiced "the user always wants X"
# claims backed by neither a Rationale line nor a fenced hunk — the synthesized-prescription
# shape that amplifies sycophancy. Report-only; the agent proposes per-item fixes in Phase 5
# (transcribe the WHAT, or cite the WHY), never a bulk rewrite (inv4). ---
from memory import prescription_lint
ungrounded_prescriptions = prescription_lint.scan_corpus(memory_dir)

# --- Join 4: per-memory staleness-baseline age (eval_recall's own metric is a corpus-wide
# median only — this decomposes it so an outlier-driven half-life is distinguishable from
# broad aging) ---
ages, commit_time_cache = {}, {}
for name in names:
    try:
        text = (Path(memory_dir) / f"{name}.md").read_text()
    except OSError:
        continue
    _, source_commit = staleness.read_provenance(text)
    if not source_commit:
        continue
    if source_commit not in commit_time_cache:
        out = subprocess.run(
            ["git", "-C", repo_root, "show", "-s", "--format=%ct", source_commit],
            capture_output=True, text=True,
        )
        commit_time_cache[source_commit] = (
            int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
        )
    ct = commit_time_cache[source_commit]
    if ct:
        ages[name] = round((datetime.now(timezone.utc).timestamp() - ct) / 86400.0, 1)

# --- Join 6: graduation-rate history filtered to currently-stale names ---
stale_names = {item["name"] for item in stale}
recon_ledger = Path(telemetry.default_telemetry_dir(memory_dir)) / "reconsolidation_events.jsonl"
history_for_stale = {}
if recon_ledger.exists():
    for line in recon_ledger.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("name") in stale_names:
            history_for_stale.setdefault(row["name"], []).append(row)

# --- Join 3: cross-run worklist recurrence — this skill's ONE deliberate exception to
# "everything derived, nothing new" (see Hard Rules). ---
history_path = repo_root_p / ".claude" / "state" / "memory-audit-history.json"
history_path.parent.mkdir(parents=True, exist_ok=True)
history = json.loads(history_path.read_text()) if history_path.exists() else {}
today = datetime.now(timezone.utc).date().isoformat()
recurrence = {}
for item in worklist:
    name = item["name"]
    prior = history.get(name, {})
    seen_count = prior.get("seen_count", 0) + 1
    recurrence[name] = seen_count
    history[name] = {
        "first_seen_run": prior.get("first_seen_run", today),
        "seen_count": seen_count,
        "last_verdict": prior.get("last_verdict"),   # Phase 3 fills this in; unset here
        "last_seen_at": today,
    }
history_path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n")
# NOT committed here (see Phase 5) — a plan-only run still benefits next run since this is
# read from disk, not from git state.

print(json.dumps({
    "run_date": today,
    "corpus_size": len(names),
    "fixtures_present": {"hard_set": hard_set is not None, "relevance_set": rel_set is not None},
    # RET-7: surfaced at the top level (not just buried in `eval_recall`) so Phase 2/4 can't
    # accidentally skip past it — `ev` is {} under --skip-eval, hence the guarded .get().
    "recall_backend": ev.get("backend"),
    "recall_backend_mismatch": ev.get("backend_mismatch", False),
    "soak_status": soak_status,
    "eval_recall": ev,
    "curation": {"never_recalled_count": len(never_recalled),
                 "bm25_fallback_rate": curation.get("bm25_fallback_rate")},
    "unparseable": sorted(unparseable_set),
    "stale": stale,
    "worklist": worklist,
    "worklist_recurrence": recurrence,
    "archive_candidates": archive_cands,
    "link_report": link_report,
    "floor_violations": floor,
    "joins": {
        "cascading_blindspot": join_cascading_blindspot,
        "authority_evidence_gap": join_authority_gap,
        "graph_isolated_watchlist": join_graph_isolated_watchlist,
        "staleness_ages": ages,
    },
    "graduation_history_for_stale": history_for_stale,
    "link_density_suggestions": link_density_suggestions,
    "merge_candidates": merge_candidates,
    "ungrounded_prescriptions": ungrounded_prescriptions,
    "foreign_dialects": foreign_dialects,
}, indent=2, default=str))
PYEOF
```

Save the printed JSON to your scratchpad — Phase 2/3 reason over it directly rather than
re-running any of the above.

## Phase 2 — Cross-reference (read the JSON, do not re-derive it)

The joins above are already computed as plain data; this phase is about **interpreting** them:

1. **Cascading blind spot** (`joins.cascading_blindspot`) — a memory here is broken frontmatter +
   zero recall evidence + links-to-nothing, *simultaneously invisible to `find_stale`,
   `recalled_stale_worklist`, AND `archive_candidates`*. This is the single worst-case category
   in the corpus. Auto-include every hit in Phase 3's deep-dive set regardless of
   `--deep-dive-n` — it's always small and always worth a look.
2. **Authority-evidence gap** (`joins.authority_evidence_gap`) — a governance doc cites this
   memory by name, yet recall telemetry says almost nobody's queries actually surface it. Two
   readings are possible and only Phase 3's actual read can tell them apart: (a) the citing doc
   already inlines the content, so low recall is fine; or (b) the citing doc's claim has drifted
   from what agents actually pull at runtime. Report which governance file cited it and the
   exact strength score (or "absent — never recalled in this clone; TEA-5: cross-clone only if
   committed .usage/*.json is present").
3. **Worklist recurrence** (`joins.worklist_recurrence` / `>= 3`) — a name recalled-and-stale for
   the 3rd+ consecutive audit run with no `last_verdict` on record is a stronger finding than a
   first-time flag. Escalate these into Phase 3 even if they'd otherwise rank below the
   `--deep-dive-n` cutoff.
4. **Staleness half-life shape** (`joins.staleness_ages` vs. `eval_recall`'s corpus-wide median)
   — compute median/p90/max over `staleness_ages`. If `max > 3x median`, state explicitly
   "outlier-driven, not broad aging — see `<name>`, baselined `N` days ago" instead of letting
   the bare corpus-wide median imply uniform drift.
5. **Graduation history vs. currently-stale** — a name with a prior `demote` entry that is stale
   *again* is a materially stronger demote signal than a fresh flag. Weight these higher in
   Phase 3 selection.
6. **Graph-isolated watch-list** — report as a clearly-separate, explicitly-labeled section.
   **Never treat it as archive-eligible.** Only `archive_candidates` output (the real 4-way gate)
   may ever become an archive proposal in Phase 5.
7. **Link-densification suggestions** (`link_density_suggestions`, GRA-3) — each entry is "this
   memory's highest-similarity EXISTING neighbors that aren't already an outbound edge", i.e. a
   *candidate* edge, not a confirmed one. A shared vocabulary is not the same as a meaningful
   relationship (the same caution `/hippo:new`'s own Related-line curation carries) — judge each
   pair by whether the BODY content actually relates, not just the description text. Report every
   entry; don't pre-filter by score alone. **Suggestions only — the agent applies approved ones
   per-item in Phase 5; there is no bulk/autonomous body edit anywhere in this pass.**
8. **Merge candidates** (`merge_candidates`, GRW-3) — pairs of COMMITTED memories that clear the
   calibrated near-duplicate threshold in BOTH directions. Read both bodies before calling
   anything a merge: a merge candidate is a **concordant restatement** — two files saying the
   same thing — and folding them into one canonical memory un-splits their recall signal. A pair
   that **disagrees** (opposing claims about the same thing) is a CONTRADICTION, never a merge —
   merging it would silently erase one side of a dispute someone needs to adjudicate. Neither
   verdict is applied here: merges route through Phase 5's per-item merge recipe, disagreements
   through a typed `contradicts`/`supersedes` edge.
9. **Contradiction adjudication — the three-way fork (GRW-8).** Every high-similarity pair the
   sweep surfaces — a `merge_candidates` row OR a `link_density_suggestions` candidate whose
   bodies you actually read — gets exactly ONE of three verdicts:
   - **(a) concordant restatement** → merge candidate (GRW-3, Phase 5 merge recipe). Two
     wordings of the same claim. High similarity + no opposing assertion.
   - **(b) genuine disagreement** → contradiction candidate: the two bodies make OPPOSING
     claims about the same thing (one says "always X", the other "never X"; different values
     for the same constant; incompatible instructions for the same situation). Propose a
     per-item typed edge — `contradicts` when the dispute needs adjudication, or `supersedes`
     (on the winner) when one side is clearly the current truth and the other is history.
   - **(c) neither** → plain densification link candidate, exactly as item 7 already treats it.
   THE MISLABEL GUARD, spelled out because the two cases score identically: a **reworded
   duplicate is NOT a contradiction** — similarity says the pair is ABOUT the same thing;
   only the claims' actual CONTENT can say whether they disagree. Never render (b) from
   scores, titles, or descriptions alone; cite the two opposing sentences in the report row.
   Accepted `contradicts` edges drain through the GOV-1 inbox (`/hippo:resolve` — the
   SessionStart contradiction-inbox producer picks them up automatically) and, when a
   governance doc cites either side, light the T2 rules-conflict radar; recall annotates the
   pair with its "contradicts … — verify" note on every co-surface until resolved.

10. **Ungrounded prescriptions** (`ungrounded_prescriptions`, SEN-3) — the corpus split into
   `observation` / `grounded` / `ungrounded`, with `ungrounded_items` naming each memory that
   asserts user intent (the `the-<subject>-always-wants-X` shape) backed by neither a
   `Rationale:` line nor a fenced hunk overlapping the claim. This is the synthesized-prescription shape that amplifies
   sycophancy: a fabricated standing preference, recalled forever and reinforced every session.
   Read each flagged body before proposing anything — the fix is per item and is one of:
   **transcribe** (rewrite the claim as the observation the evidence actually supports),
   **ground** (add a `--rationale`/`Rationale:` citing the WHY), or **cut** (drop a preference
   nothing supports). Never a bulk rewrite (inv4); warn-only, never a block. A high fraction is
   a corpus-health signal (the transcription-not-synthesis discipline slipping), not a per-file
   emergency.

11. **Foreign-dialect radar** (`foreign_dialects`, IOP-1) — the rule dialects hippo does NOT
   own. Report the census honestly (all globs empty → "no other dialects found" — one line and
   move on). Each `divergence` row is a foreign file whose substance a governance block already
   contains: a same-rule-diverged pair in the making — the fix is a per-item human choice
   (converge the two by hand, or delete one side; "link, don't copy"), NEVER an import proposal
   from this pass and never an auto-edit. `mdc_citation_rot` / `mdc_dead_globs` are
   existence-only findings on Cursor's own files (a `.mdc` has no drift baseline — that framing
   would be dishonest); the fix is editing the named `.mdc` in the user's own editor. Un-owned
   content never becomes hippo authority: if a diverged pair should live in the corpus, route
   the user to `/hippo:import` (its own per-item consent flow), not through this audit.

**Signal-maturity tag** — apply to every join above that depends on the recall-telemetry window
(authority-evidence gap, staleness-half-life shape, graduation history, archive candidacy
itself), not just the archive section: pull `soak_status['distinct_sessions']` and
`soak_status['gate_met']` and state the exact count and the exact gap to the 5-session bar next
to each of these findings. A vague immaturity hedge is not honesty; an exact, checkable number
is.

## Phase 3 — Read-and-judge (bounded to `--deep-dive-n`, default 8)

Select candidates in this priority order until the cap is filled (recurrence >= 3 and
cascading-blind-spot hits are **not** capped): (1) worklist recurrence >= 3, (2) cascading blind
spot (all of them), (3) authority-evidence gap, (4) graduation-history-vs-stale hits, (5)
staleness-half-life outliers, (6) remaining plain worklist items by recency. Items beyond the cap
are **not dropped** — list them by name only in the report's appendix with "re-run with
`--deep-dive-n` to expand."

For each selected item, concretely:

1. `Read` the full memory body — never just its frontmatter/description.
2. For every path in its `cited_paths`/`changed_paths`: `git log --oneline -5 -- <path>` since
   its `source_commit`, then `git diff <source_commit>..HEAD -- <path>` (bounded — `git diff
   --stat` first; if it reports >500 changed lines, fall back to `Grep`-ing for the memory's
   claimed symbol/function/constant around the cited line rather than reading the raw diff).
3. **Before rendering GRADUATE, corroborate beyond the cited path.** A clean diff on the cited
   path does **not** prove the memory is still correct — the invariant it describes may have
   moved to a different file or symbol during a refactor, in which case the diff is silent while
   the claim is now false. `Grep` the memory's claimed symbols/function names/constants across
   the whole repo before trusting a clean diff.
4. Render exactly one verdict from `reconsolidate.py`'s own outcome vocabulary — do not invent
   new labels:
   - **graduate** — the diff (plus Grep corroboration) confirms the claim still holds verbatim.
     Recommend `semantic_reverify(name, "graduate", ...)`.
   - **fix** — the claim is now wrong in a way you can correct inline; edit the memory body
     yourself in this session, then recommend `semantic_reverify(name, "fix", ...)`.
   - **demote** — the claim is wrong or no longer worth tracking. Recommend
     `semantic_reverify(name, "demote", ...)` — the staleness flag stays **set** by design, and
     the call itself chains `invalid_after` onto the memory (LIF-1), so recall's pre-cut penalty
     demotes it immediately — no separate `staleness --invalidate` step. If this is also an
     authority-evidence-gap hit, say so explicitly (demoting the memory doesn't fix the
     governance doc that still cites it — that needs its own follow-up edit).
   - **escalate** (local addition — use only when a real verdict genuinely requires domain
     knowledge this skill can't independently verify, e.g. a production-tuned constant) — no
     autonomous verdict, flag the specific open question for the operator.
   - **unverifiable** (local addition) — the cited path was deleted or `source_commit` no longer
     resolves (a rebase/squash) — route to a human-decision appendix, never force-fit into
     demote.

   When the **operator** explicitly defers an item instead of rendering a verdict, ack it with
   `"$PY" -m memory.reconsolidate --snooze <name>` (LIF-1) — the worklist stops re-nagging it
   for the next 5 sessions and the ack is logged in the reconsolidation ledger. A snooze is a
   deferral, not a verdict: never record it as one of the outcomes above.
5. Every verdict needs a 2-4 sentence justification **citing the specific diff hunk, commit, or
   Grep result you actually read**. A verdict with no cited evidence is a re-labeled
   `staleness.find_stale()` row, not a judgment.

**Record what was judged, in every run — report-only or apply.** After Phase 3 finishes, write
the rendered verdicts back into the history file (bookkeeping, not a corpus mutation — happens
regardless of `--apply`):

```bash
"$PY" - <<'PYEOF'
import json
from pathlib import Path
history_path = Path(".claude/state/memory-audit-history.json")
history = json.loads(history_path.read_text())
verdicts = {"<name>": "<graduate|fix|demote|escalate|unverifiable>"}  # fill in from this run
for name, verdict in verdicts.items():
    if name in history:  # only worklist items have a history entry (see Phase 1)
        history[name]["last_verdict"] = verdict
history_path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n")
PYEOF
```

## Phase 4 — Report

Write to `.claude/memory/.audit-reports/audit-<YYYY-MM-DD>.md` (create the directory if
absent — it's project-local, not committed by default unless the operator wants an audit trail
in git). **The "This week" section must stand alone as a complete answer.** Hard cap: 5 ranked
items + up to 2 recurrence-escalated items.

```markdown
# Memory Tooling Self-Audit — <date>
Run mode: <report-only|apply>  |  Deep-dive N: <n>  |  Corpus: <N files, N lines> (discovered fresh)
Fixtures: hard-set <present|absent>, relevance-set <present|absent>
Recall backend this run: <dense+bm25|bm25-only> <— dense path unverified this run if bm25-only; ⚠ FIXTURE/BACKEND MISMATCH if eval_recall.evaluate() set backend_mismatch>
Prior audit runs on record: <count from history file, or "none — first run">
Soak state: <distinct_sessions>/5 sessions (gate_met=<bool>)
Usage-signal scope: <CLONE-LOCAL — only this clone's recalls | cross-clone — unions committed .usage/*.json> (TEA-5)

## This week (ranked)
1. **[<TAG>] <memory name>** — <verdict> — <one-sentence why>
   <2-4 sentence evidence citing the actual diff/commit/grep you read>
   Recommended action: `<exact function call>`
... (up to 5, +2 recurrence-escalated if any fired)

## Signal immaturity notice
<Exact soak_status counts + gap-to-threshold for every telemetry-dependent claim in this
report. Never a vague hedge.>

## Deep-dive verdicts
| Memory | Verdict | Why (1 line) | Recurrence | Action taken/proposed |
|---|---|---|---|---|

## Cross-reference appendix (provenance only — not re-analyzed prose)
- Cascading blind spot: <names>
- Authority-evidence gap: <names + citing file + strength>
- Worklist recurrence: <names + seen_count>
- Staleness half-life: median=<>d, p90=<>d, max=<>d (<name>) — <outlier-driven|broadly-aging>
- Graduation history ∩ currently-stale: <names + prior verdicts>
- Graph-isolated watch-list (never archive-eligible on its own): <names>

## Link-densification suggestions (GRA-3 — SUGGESTIONS only, none auto-applied)
| Memory | Candidate | Score | Judged relevant? | Applied this run? |
|---|---|---|---|---|
<one row per candidate reviewed; "Applied this run?" is always "no" unless --apply AND the
operator approved that specific pair — see Phase 5>

## Merge candidates (GRW-3 — both-direction near-duplicates, SUGGESTIONS only, none auto-applied)
| Pair | a→b | b→a | Verdict | Applied this run? |
|---|---|---|---|---|
<one row per merge_candidates pair; Verdict ∈ {merge — concordant restatement, contradiction —
they disagree (route to a typed edge, never a merge), distinct — leave both}; "Applied this
run?" is always "no" unless --apply AND the operator named that specific pair — see Phase 5>

## Contradiction candidates (GRW-8 — proposals only, none auto-applied)
| Pair | Opposing claims (quote both sides, one line each) | Proposed edge | Applied this run? |
|---|---|---|---|
<one row per pair verdicted (b) in Phase 2's three-way fork; "Proposed edge" ∈
{contradicts — needs adjudication, supersedes <winner> — one side clearly current}; a row
with no quoted opposing claims is invalid — similarity alone never makes a contradiction>

## Abstention-fixture drafts (SIG-6 — admission per-item, human-approved)
| Draft query | Asked | current_hits | Verdict | Admitted this run? |
|---|---|---|---|---|
<one row per drafts-queue row judged in Phase 0.6; Verdict ∈ {admit → <stem>, capture gap —
route to /hippo:consolidate, noise — deleted}; "Admitted this run?" is "yes" only for rows
the human explicitly approved (confirm_hard_set_row), each tagged category: abstention>

## Raw scorecard (reference only)
eval_recall gates: <table, or "SKIPPED — no hard-set fixture for this project yet">
  backend=<dense+bm25|bm25-only> (from `eval_recall`'s own report — never re-derived here)
soak: <soak_status + curation_report summary>  |  links/floor: <lint_links + lint_floor summary>

## Below deep-dive cutoff (name only)
<names not deep-dived this run>

## Applied this run (--apply only)
<table: memory, action, function called + return value, git commit>
```

## Phase 5 — Apply (only under `--apply`)

Every write goes through the tools' **existing single-item, no-bulk primitives** — this skill
never adds a batch wrapper around them:

- **graduate / fix (body already hand-edited)** → `reconsolidate.semantic_reverify(name,
  outcome, memory_dir, repo_root)`. Executes **same-turn** under `--apply`. **RET-6
  reinforcement (automatic, no extra step):** this call routes through
  `provenance.reverify_file`, which re-baselines `source_commit` to HEAD and — the FIRST
  time this memory is ever reverified — additively stamps a write-once `last_verified`
  timestamp. If this memory was carrying a `SessionStart`/recall verify-at-use banner
  ("anchored to `<sha>`; N cited files changed since — verify before relying"), it clears
  itself on the **next** `SessionStart` staleness scan (`find_stale`/`stale.json`) with no
  separate command — the banner's presence is derived purely from `stale.json`, and a
  reinforced memory simply stops appearing in it. Do not chase the banner with any other
  primitive; graduate/fix already IS the clear.
- **demote** → same call with `outcome="demote"` — staleness flag stays set by design; still
  logged. The call chains `invalid_after` onto the memory itself (LIF-1, recorded in the
  ledger event as `invalidated`), so the recall demotion is immediate — do **not** follow up
  with a separate `staleness.set_invalid_after` for the same memory.
- **archive** → `archive.archive_memory(name, memory_dir, repo_root)`, but **only** for names
  `archive.archive_candidates()` itself independently returned in Phase 1 — never from the
  graph-isolated watch-list or any other heuristic this skill invents. Archive gets a **two-turn
  confirmation gate**: the first `--apply` invocation proposes archive candidates in the report
  and takes no `git mv` action; only a follow-up invocation that **explicitly names the specific
  memories to archive** executes the move. The primitive carries its own inbound guard (GRA-5):
  it refuses — `refused: True` plus the `referrers` list in the result, no `git mv` — while any
  other memory still references the target via a `[[wikilink]]` or a typed
  `supersedes`/`contradicts`/`refines` edge. Candidates are zero-untyped-inbound by
  construction, so a refusal here means the graph changed since Phase 1 or a typed edge points
  at the target — rewrite the referring memories (or record a `supersedes:` edge on the
  successor, the machine-readable forwarding pointer) rather than reaching for `force=True`;
  when the operator explicitly chooses to force (keeping the typed forwarding pointer in
  place), the result still lists the referrers — rewrite any plain wikilinks among them in the
  same commit.
- **link-densification (GRA-3)** → same **two-turn confirmation gate** as archive: the first
  `--apply` invocation only PRODUCES the suggestions table above and applies NOTHING (there is
  no bulk primitive for this — appending a wikilink is a body edit, and body edits are exactly
  the kind of write this project never automates). A follow-up invocation that **explicitly
  names the specific memory + candidate pair(s)** to link appends a single `[[candidate]]`
  reference into that ONE memory's body (a plain text append, mirroring how `new_memory`'s own
  Related line is additive-only) and re-runs `build_index.refresh_index` so the new edge is
  immediately reflected in `links.json`. Never touch more than the named pairs; never infer
  additional edges beyond what was explicitly approved.
- **merge (GRW-3)** → same **two-turn confirmation gate**: the first `--apply` invocation only
  produces the merge-candidates table; a follow-up invocation that **explicitly names the
  specific pair** executes ONE merge, per item. There is NO body-rewrite primitive anywhere in
  the tooling and this skill must not simulate one — a merge is a sequence of ordinary
  single-item edits YOU make, with `archive`'s inbound guard as the structural no-dangling
  enforcer:
  1. Pick the SURVIVOR (the better-named, better-grounded side — usually the one with
     provenance/citations and inbound links; say why in the report).
  2. Fold the loser's unique body content into the survivor's body by hand (`Edit`), including
     its `Rationale:`/`Related:` lines where still true. Add one provenance line to the
     survivor's body — `(merged from <loser>, <date>)` — so the fold is legible in git history.
  3. For every untyped referrer in `links.build_graph(memory_dir).inbound(<loser>)`: hand-edit
     that referrer's `[[<loser>]]` → `[[<survivor>]]` (one memory at a time).
  4. For every typed relation `rel` and referrer in `typed_inbound(<loser>, rel)`:
     `links.add_typed_relation(<referrer-path>, rel, "<survivor>")`, then hand-remove the stale
     `<loser>` entry from that referrer's frontmatter list.
  5. Close the loser out — choose ONE ending, per item:
     - **demote-in-place** (keeps a machine-readable forwarding pointer): `"$PY" -m
       memory.reconsolidate --reverify <loser> --outcome demote --superseded-by <survivor>` —
       the shipped supersede flow writes the `supersedes:` edge on the survivor and chains
       `invalid_after` onto the loser, so recall demotes it immediately while the pointer
       stays queryable.
     - **archive** (clean removal): `archive.archive_memory(<loser>, memory_dir, repo_root)` —
       its GRA-5 inbound guard REFUSES while ANY `[[wikilink]]` or typed edge still points at
       the loser, which structurally proves steps 3-4 actually zeroed the inbound set. Do not
       reach for `force=True` to skip the rewrite; a refusal here means a referrer was missed.
       (Note the two endings are exclusive by construction: the demote ending's `supersedes:`
       pointer is itself a typed inbound edge, so an archive AFTER it would refuse — pick the
       ending first.)
  6. Re-run `build_index.refresh_index` so `links.json` and the index reflect the fold.
- **contradiction edge (GRW-8)** → same **two-turn confirmation gate**: the first `--apply`
  invocation only produces the contradiction-candidates table; a follow-up invocation that
  **explicitly names the specific pair** writes ONE typed edge, per item:
  - dispute needs adjudication → `links.add_typed_relation("<path-of-the-declaring-side>",
    "contradicts", "<other-name>")` — the pair lands in the GOV-1 contradiction inbox and
    stays there until `/hippo:resolve` renders a verdict; recall's typed note flags every
    co-surface with "contradicts … — verify" in the meantime.
  - one side is clearly current → prefer the shipped supersede flow over a bare edge:
    `"$PY" -m memory.reconsolidate --reverify <loser> --outcome demote --superseded-by
    <winner>` (edge + `invalid_after` in one per-item verdict).
  Then re-run `build_index.refresh_index` — `add_typed_relation` writes the frontmatter but
  NOT `links.json`, and recall's hot-path contradiction note reads the cache (the inbox
  re-reads the corpus, so it sees the edge either way).
- Before committing, run the engine repo's own hermetic test suite if you have it vendored
  locally, or at minimum re-run Phase 0's import check — confirm the corpus is still valid after
  any frontmatter/git-mv changes. If red, do not commit; report and halt for review.
- Commit the history-file update + any reverify/invalidate/archive changes as **one plain commit**
  — no tier structure, no PR, for this kind of corpus-metadata/git-mv change. Message style:
  `memory audit <date>: N reverified, M invalidated, K archived`.
- In report-only mode, the Phase 1 history-file write still happens (local bookkeeping, not a
  corpus mutation) but is **not committed**.

## Hard rules

- **No bulk anything.** `semantic_reverify`, `set_invalid_after`, `archive_memory` all take
  exactly one name. This skill must never simulate a batch by looping them silently in one turn
  without the Phase 3 per-item justification attached to each.
- **Fixture admission is per-item and human-approved (SIG-6).** `confirm_hard_set_row` is
  called once per explicitly-approved row — never looped over the drafts queue in one silent
  sweep. And never CREATE a memory to make a fixture admissible: the primitive refuses stems
  that don't exist, and satisfying it by fabricating the memory first is the exact inversion
  (fixture drives corpus) the killed demand-gap-auto-draft was killed for. The corpus grows
  only through the consolidate drain's own merits; the fixture then measures it.
- **The graph-isolated watch-list never feeds an archive action.** Only
  `archive.archive_candidates()`'s real 4-way gate may.
- **Link-densification never auto-edits a body.** `link_density_suggestions` is read-only
  output from Phase 1; Phase 5 appends a wikilink ONLY for pairs the operator explicitly named
  in a follow-up invocation, one memory at a time — never a bulk sweep across every suggestion
  in the table, no matter how high the score.
- **Merge candidates come only from the both-direction dup check — never from the
  densification scores.** `link_density_suggestions` carries `recall()`'s RRF-fused scores
  (rank aggregates, ~1/60 per contributing backend); the merge tier's thresholds are dense
  cosine / normalized BM25 similarities. Comparing one scale to the other is a category
  error, and a one-way hit is not a merge signal. A merge is also never rendered without
  reading BOTH bodies — "reworded duplicate" and "opposing claims" look identical in any
  similarity score.
- **A contradiction verdict requires quoted opposing claims (GRW-8).** The three-way fork's
  (b) arm is rendered from body CONTENT only — never from similarity, titles, or
  descriptions; a contradiction-candidates row must quote the two opposing sentences. And
  the fork never auto-writes: every `contradicts`/`supersedes` edge is a per-item, two-turn
  proposal like every other write in this skill.
- **Never claim the never-recalled/cold signal is actionable while `soak_status()['gate_met']`
  is False.** State the exact session count and gap instead.
- **Always name the coldness signal's SCOPE (TEA-5).** "Never recalled" is CLONE-LOCAL unless
  `curation_report()['committed_usage_present']` is True — a memory a teammate hits daily reads
  as cold on your clone. When flagging a cold/dead-weight memory on a team, say the signal is
  clone-local and point at `python -m memory.soak --record-usage` (each clone commits
  `.claude/memory/.usage/`) as the fix before any archive is proposed.
- **The "This week" section is capped and self-contained** — 5 ranked + up to 2
  recurrence-escalated items, no more.
- **`.claude/state/memory-audit-history.json` is this skill's one deliberate exception** to
  "everything derived, nothing new to keep in sync" — nothing in the shipped tooling tracks
  cross-run worklist recurrence otherwise. Keep its schema minimal; Phase 0 must tolerate its
  absence gracefully. If this pattern proves durably valuable, it should graduate into a real
  telemetry ledger with its own tests rather than living as a skill-owned side file forever.
- **This design depends on zero underscore-prefixed private helpers** from `archive.py`/
  `reconsolidate.py` — the authority-citation scan is reimplemented locally in Phase 1's
  script specifically to avoid coupling to internals that could be renamed without a
  deprecation cycle, and graph isolation comes from the PUBLIC `LinkGraph.inbound()`/
  `isolates()` primitives (GRA-2) rather than a hand-rolled adjacency inversion. If those
  modules' real predicates ever change, re-sync the local reimplementation.
- **Corpus-size assumption.** This design (single session, no fan-out, `--deep-dive-n` read by
  hand) is sized for "well under a thousand memories." If a corpus grows 5-10x, revisit: Phase 1
  stays cheap, but Phase 2's per-memory staleness-age decomposition and Phase 3's by-hand
  deep-dive would need either a higher default N, sub-scoping by `metadata.type`, or eventually
  a subagent-fan-out escalation. Don't solve this preemptively.
- **A fresh corpus SKIPS `token_reduction` — this is expected, not a bug.** A corpus with no
  `MEMORY.full.md` pre-trim snapshot (every fresh install — core pack or a few seeded packs,
  before the project has grown its own corpus) has nothing to compare the trimmed floor
  against, so the gate reports `skipped`, excluded from the RESULT. Report it as exactly
  that — do NOT treat the skip as a failure, and do NOT recommend the operator "add filler
  memories" or fabricate a MEMORY.full.md to force the gate on.
- **Never conflate "evaluate() returned ok=True" with "the scorecard metrics look healthy."**
  The gates and the report-only scorecard metrics are orthogonal — none of the latter can move
  `ok` or any `gates` entry. And if no hard-set fixture exists for this project, say so plainly
  rather than letting an absent gate read as a passing one.
- **(RET-7) A BM25-only pass is never reported as "hybrid recall verified."** Always state
  `recall_backend` (`dense+bm25` or `bm25-only`) next to the eval_recall summary — a
  `bm25-only` pass proves BM25 alone clears the bar, not that the dense half of hybrid recall
  works. If `recall_backend_mismatch` is `True` (this project's hard-set fixture was
  `generated_with_backend: dense+bm25` but this run only served `bm25-only`), call it out as
  its own explicitly-flagged line in the report, not folded silently into the scorecard table
  — this is precisely the "mismatched-backend pass masquerading as hybrid health" this item
  exists to make impossible to miss.
- **(RET-7) `--generate-eval-set` never runs implicitly.** A routine `/hippo:audit` invocation
  (no flag) must never regenerate, overwrite, or even touch an existing
  `.claude/memory/.audit-fixtures/recall_hard_set.yaml` — Phase 0.5 is entered ONLY when that
  exact flag is present on THIS invocation. And even when it IS present, the generated draft
  is presented to the human for review before Phase 1 ever consumes it — never write-then-run
  in the same breath without that confirmation step.

## Prompt templates

### (A) Routine health check (default, safe)

> Run `/hippo:audit`. Report-only — don't apply anything. I want to see what's actually worth
> my attention this week beyond what the SessionStart banner already told me.

### (B) Fast drift-only pass (no dense model load)

> Run `/hippo:audit --skip-eval --deep-dive-n 5`. I just want the curation/staleness/archive
> signals, not the recall-quality gates.

### (C) Apply mode after reviewing a prior report

> Run `/hippo:audit --apply`. I've read the last report — go ahead and execute the
> graduate/fix/demote verdicts. Leave any archive proposals for a follow-up; don't move files yet.

### (D) Follow-up archive confirmation

> The audit report proposed archiving `<name1>` and `<name2>`. Archive exactly those two via
> `archive.archive_memory` and commit.

### (E) Follow-up link-densification confirmation

> The audit report suggested linking `<memory-a>` to `<memory-b>` (and `<memory-c>` to
> `<memory-d>`). I reviewed both — go ahead and add exactly those two wikilinks, nothing else
> from the table, and commit.

### (F) Generate this project's own recall eval set (RET-7)

> Run `/hippo:audit --generate-eval-set`. This project has no `recall_hard_set.yaml` yet (or
> the corpus has grown a lot since the last one) — sample memories across types, write
> cross-vocabulary paraphrase queries, and show me the draft before you use it. Then run the
> eval gates against it and tell me the recall@10/MRR and which backend actually served this
> run.
