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

No `--full` / `--semi-attended` / tier flags. This is a single-pass audit with one apply gate,
not a multi-PR roadmap.

## Phase 0 — Preflight

- **Guard `CLAUDE_PLUGIN_DATA` first** (shared across all hippo skills — the venv paths
  below expand it):
  ```bash
  [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
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
  skipped" rather than a false pass or fail). If you want these gates going forward, hand-curate
  a small hard-set file for your own corpus over time — see the engine repo's own
  `tests/fixtures/recall_hard_set.yaml` for the expected YAML shape as a reference.
- Load prior run history if present: `.claude/state/memory-audit-history.json` (recommend
  git-tracking this file, same precedent as any other durable project state). If absent, this is
  run #1 — say so explicitly in the report; a fresh corpus with no history is not the same
  finding as "nothing recurs," and conflating the two is exactly the kind of overclaim Phase 4's
  honesty section exists to prevent.

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
   exact strength score (or "absent — never recalled").
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
     `semantic_reverify(name, "demote", ...)` — the staleness flag stays **set** by design; if
     this is also an authority-evidence-gap hit, say so explicitly (demoting the memory doesn't
     fix the governance doc that still cites it — that needs its own follow-up edit).
   - **escalate** (local addition — use only when a real verdict genuinely requires domain
     knowledge this skill can't independently verify, e.g. a production-tuned constant) — no
     autonomous verdict, flag the specific open question for the operator.
   - **unverifiable** (local addition) — the cited path was deleted or `source_commit` no longer
     resolves (a rebase/squash) — route to a human-decision appendix, never force-fit into
     demote.
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
Prior audit runs on record: <count from history file, or "none — first run">
Soak state: <distinct_sessions>/5 sessions (gate_met=<bool>)

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

## Raw scorecard (reference only)
eval_recall gates: <table, or "SKIPPED — no hard-set fixture for this project yet">
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
  outcome, memory_dir, repo_root)`. Executes **same-turn** under `--apply`.
- **demote** → same call with `outcome="demote"` — staleness flag stays set by design; still
  logged.
- **archive** → `archive.archive_memory(name, memory_dir, repo_root)`, but **only** for names
  `archive.archive_candidates()` itself independently returned in Phase 1 — never from the
  graph-isolated watch-list or any other heuristic this skill invents. Archive gets a **two-turn
  confirmation gate**: the first `--apply` invocation proposes archive candidates in the report
  and takes no `git mv` action; only a follow-up invocation that **explicitly names the specific
  memories to archive** executes the move.
- **link-densification (GRA-3)** → same **two-turn confirmation gate** as archive: the first
  `--apply` invocation only PRODUCES the suggestions table above and applies NOTHING (there is
  no bulk primitive for this — appending a wikilink is a body edit, and body edits are exactly
  the kind of write this project never automates). A follow-up invocation that **explicitly
  names the specific memory + candidate pair(s)** to link appends a single `[[candidate]]`
  reference into that ONE memory's body (a plain text append, mirroring how `new_memory`'s own
  Related line is additive-only) and re-runs `build_index.refresh_index` so the new edge is
  immediately reflected in `links.json`. Never touch more than the named pairs; never infer
  additional edges beyond what was explicitly approved.
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
- **The graph-isolated watch-list never feeds an archive action.** Only
  `archive.archive_candidates()`'s real 4-way gate may.
- **Link-densification never auto-edits a body.** `link_density_suggestions` is read-only
  output from Phase 1; Phase 5 appends a wikilink ONLY for pairs the operator explicitly named
  in a follow-up invocation, one memory at a time — never a bulk sweep across every suggestion
  in the table, no matter how high the score.
- **Never claim the never-recalled/cold signal is actionable while `soak_status()['gate_met']`
  is False.** State the exact session count and gap instead.
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
