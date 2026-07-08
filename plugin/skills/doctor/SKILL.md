---
description: Fast health check for the memory plugin's own install/environment — is it bootstrapped, is the venv healthy, is the corpus symlinked and indexed correctly. Use for "is memory working", "check memory setup", "/hippo:doctor", or when recall seems to be silently returning nothing. This is a QUICK sanity check, not a deep corpus audit — for the latter use /hippo:audit.
---

# /hippo:doctor — fast environment sanity check

A few-second diagnostic over the PLUGIN'S OWN install health — venv, bootstrap, symlink,
corpus resolution, trust, index freshness/corruption. This is deliberately NOT `/hippo:audit`:
doctor answers "is the plumbing working," audit answers "is the corpus content still
trustworthy" (a much heavier, judgment-based pass). Don't reach for audit when doctor's quick
checks are what's actually being asked.

Doctor's checks are a DETERMINISTIC engine (`memory.doctor`, DOC-4): identical state produces
identical output across models and sessions. This SKILL is a thin wrapper — it runs that engine
and presents its output verbatim, then handles the one step a non-interactive module cannot: the
untrusted-corpus consent prompt.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

## Run the engine

```bash
"$PY" -m memory.doctor
```

Print its output VERBATIM — every `✔`/`✘`/`⚠` line, in order. Do not re-word, re-order, drop,
or re-run individual checks by hand: the whole point of the engine is that the diagnostic is
reproducible, and paraphrasing reintroduces the run-to-run variance DOC-4 removed. The engine
resolves the corpus/repo the same way recall does (`resolve_dirs`) and runs, in a FIXED order:
bootstrap state, venv imports, corpus existence, project symlink (SHP-5/ONB-5), native-memory
coexistence (INT-4: symlink-target drift + native-layout change), corpus
resolution (SHP-2 nested-vs-root walk-up), git degraded-mode (SHP-4), corpus trust (SEC-1),
frontmatter integrity, index corruption (QUA-5), index count vs corpus, index format version,
pack drift, `<FILL-ME` templates, and the corpus-wide secret scan (SEC-2). Each line already
names the specific finding and the exact command to fix it.

## The one interactive step doctor still owns — untrusted-corpus consent (SEC-1)

The engine REPORTS trust state but never trusts a corpus: consent is a security boundary that
must be an explicit human yes, which a non-interactive module cannot take. When the trust line
reads `⚠ corpus UNTRUSTED (N memories) — recall injects nothing from it`, recall is gated and
this is the consent moment:

1. **Show what would be injected BEFORE asking** — the memory COUNT and a SAMPLE of memory
   NAMES (names only; never dump bodies — the whole point of the gate is that an untrusted
   corpus's content never reaches context unreviewed):
   ```bash
   "$PY" -c \
     "import json; from memory import trust; from memory.provenance import resolve_dirs; \
      md, rr = resolve_dirs(); root = trust.gate_repo_root(md, rr); \
      print(json.dumps({'count': trust.corpus_count(md), 'sample': trust.corpus_sample(md)}))"
   ```
2. **ASK** (AskUserQuestion where available, else a plain yes/no) whether they trust this corpus.
3. **On an explicit YES**, mark it and confirm:
   ```bash
   "$PY" -c \
     "import sys, json; from memory.trust import mark_trusted; \
      print(json.dumps({'trusted': mark_trusted(sys.argv[1])}))" \
     "<repo_root from the check above>"
   ```
   Report `✔ corpus now trusted — recall active from next prompt` on success (or that the marker
   write failed — say so; recall stays gated). On NO / no answer, leave it gated and report that
   re-running `/hippo:doctor` will offer to trust it again later. NEVER auto-trust without the
   explicit yes — the review IS the security boundary.

## End with ONE next action

After presenting the engine's lines (and handling consent if it applied), end with the single
most useful thing to run next if anything failed (e.g. `/hippo:bootstrap`, `/hippo:init`, or the
rebuild command a line named) — not a list of every possible remediation. If every line is a
`✔`, say so plainly.

## When NOT to use

- A deep "is my corpus content still accurate" pass — that's `/hippo:audit`.
- Routine curiosity when nothing seems wrong — SessionStart's own staleness/link-health
  producers already surface real problems for free every session; don't re-run this reflexively.
