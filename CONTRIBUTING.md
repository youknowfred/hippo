# Contributing to hippo

Thanks for helping. hippo is a local, git-native memory plugin for Claude Code — the engine is a
dependency-light Python package under `plugin/memory/`, the surface is a set of `/hippo:*` skills,
and everything a user relies on is markdown in git. This guide gets you from clone to a green test
run to a mergeable change.

New to the ideas? Read [CONCEPTS.md](CONCEPTS.md) first (the five-minute mental model), then the
engine reference at [`plugin/memory/README.md`](plugin/memory/README.md) for internals.

## Dev setup

hippo ships as a self-contained plugin; for development you just need a Python venv with the four
runtime deps plus the test tooling. From the repo root:

```bash
python -m venv .venv
.venv/bin/pip install -r plugin/requirements.txt pytest pytest-timeout hypothesis
```

That mirrors exactly what CI installs. `fastembed` (the dense-embedding backend) is included; if
you'd rather skip the ~130 MB model download, you can run the whole suite BM25-only (see below).

## Running the tests

`pytest.ini` sets `pythonpath = plugin`, so run pytest from the repo root:

```bash
.venv/bin/python -m pytest                       # full hermetic suite (needs fastembed cached)
HIPPO_DISABLE_DENSE=1 .venv/bin/python -m pytest  # BM25-only — no model download, fully offline
```

`HIPPO_DISABLE_DENSE=1` is the airplane-mode path: the vendored BM25 scorer + frontmatter parser
under `plugin/memory/_vendor/` serve recall on a bare interpreter, so the suite runs without the
embedding model. This is the recommended default for iterating.

### Test markers

Three markers gate environment-sensitive tests; all three are **deselected by default** (via
`addopts = -m "not network and not slow and not scale"`), so a plain `pytest` stays hermetic and
airplane-safe:

| Marker | Meaning | Runs where |
|---|---|---|
| `network` | may download the ~130 MB embedding model on a cold cache | CI dense lane (`-m network`) |
| `slow` | real wall-clock assertions (timing-sensitive, not slow to run) | CI dense lane (`-m "network or slow"`) |
| `scale` | the ~500-memory scale-envelope lane (genuinely slow to build) | CI nightly (`-m scale`) |

If you add a test that makes a real wall-clock assertion, mark it `slow` so a loaded runner can't
flake it red. If it downloads the model, mark it `network`.

The suite runs under `filterwarnings = error` (QUA-10) — a new deprecation warning from hippo's own
code fails the build. Keep production code warning-free.

## CI checks

Every PR to `main` runs (`.github/workflows/ci.yml`):

- **hermetic** — the default suite across `{ubuntu, macos} × {py3.10, py3.12}` (dense disabled).
- **dense** — the `network`/`slow` tests against a cached model + the real `eval_recall` CLI gate.
- **shellcheck** — the hook scripts and `bin/hippo`.
- **secret-scan** — the secret-lint detector over the committed tree (a credential must never reach
  a user through a release). Run it locally before you push: `PYTHONPATH=plugin python -m memory.secrets --repo .`

Plus two non-blocking lanes: **resolution** (dependency resolution + bootstrap smoke on
`{py3.11, 3.13, 3.14}`) and the nightly **scale** lane.

## How we ship changes

hippo is developed as a sequence of small, self-describing changes, each traceable to a roadmap
item ([`ROADMAP.yaml`](ROADMAP.yaml) / [`ROADMAP.v1.md`](ROADMAP.v1.md)):

- **Branch off `main`.** One coherent change per branch/PR.
- **One id-prefixed commit per roadmap item.** Commit subjects lead with the item id and a colon —
  e.g. `CAP-6: bound the capture pending queue`. A PR bundles related items; each stays its own
  commit. If your change isn't tied to an existing item, describe it plainly — a new id isn't
  required for an outside contribution.
- **Keep the full suite green after each commit.** `HIPPO_DISABLE_DENSE=1 pytest` from the root.
- **Preserve the guiding invariants.** They're listed in `ROADMAP.yaml` (`guiding_invariants`) —
  the load-bearing ones: markdown-in-git is the only source of authority (index/telemetry/graph are
  derived, rebuildable, gitignored caches); hooks always `exit 0`, never download, never block; the
  `UserPromptSubmit` hot path stays pure retrieval (no LLM, no network, no per-prompt writes);
  destructive/corrective writes are per-item and agent-gated (no bulk autonomous sweeps).
- **PRs are squash-merged.** Write a PR body that says what shipped and why.

Post-1.0, changes to the frozen compatibility surface follow [`STABILITY.md`](STABILITY.md) — a
rename or removal there is a major-version bump or a deprecation window, not a silent break.

## Code layout

`plugin/memory/` is a flat package of focused modules. When a concern outgrows its module, it is
decomposed into **prefix-named siblings**, never subpackages — `recall.py` →
`recall_rank.py`/`recall_salience.py`/…, `doctor.py` → `doctor_checks_*.py` — and the original
module stays as the **façade**: it keeps its `python -m memory.<name>` entry point and explicitly
re-imports every moved name, so every existing dotted path keeps resolving. Two rules keep the
shape stable:

- **Siblings never import their façade.** The façade imports its siblings; siblings import their
  true dependencies (other siblings included). The import graph between a façade and its siblings
  stays one-directional.
- **Module size is ratcheted** (`tests/test_module_size.py`): a new `plugin/memory/` module caps at
  900 lines, a new test file at 1,200, and the files that pre-date the ratchet are pinned at their
  recorded size so they can only shrink. If the ratchet fires, split along a section banner rather
  than raising a pin — raising one is a deliberate, reviewed decision.

Two caveats when moving code: a few functions are **AST-pinned to their file** by structural tests
(the crash contract in `test_crash_faults.py`, the write-open allowlist in
`test_write_discipline.py`, the `FunctionDef` pins in `test_injection_cost.py`) — those registries
key on `(module, function)`, so a move updates the registry entry or the function stays put. And a
test that monkeypatches a moved function must patch the module that now *calls* it (patches land on
a namespace; a call site that moved modules looks the name up in its new home).

## Reporting bugs & security issues

- **Bugs / feature requests:** open an issue — the bug form asks for your platform, corpus size, and
  recall backend, which are the three things we need to reproduce a recall problem.
- **Security vulnerabilities:** please report them privately — see [SECURITY.md](SECURITY.md) for
  the disclosure channel (GitHub private advisories) and hippo's threat model. Do **not** open a
  public issue for a vulnerability.

## Code of conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). Be kind; assume good faith.
