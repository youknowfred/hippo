# Releasing hippo (DOC-7)

This formalizes the release process the first releases (v0.2.0–v0.5.0) ran by hand. Each
release is a workstream slice of [`ROADMAP.yaml`](ROADMAP.yaml)'s `release_train`, shipped as
one PR, squash-merged, then tagged.

## Version numbers

Two files carry the version and MUST match:

- `plugin/.claude-plugin/plugin.json` → `version`
- `.claude-plugin/marketplace.json` → `plugins[0].version`

Gates:

- **PR time** — `tests/test_version_sync.py` (hermetic lane) asserts the two manifest fields
  match and are valid semver. A drift reds the build.
- **Tag time** — `.github/workflows/release.yml` (fires on a `v*` tag push, not on PRs) asserts
  the tag, both manifests, and the newest `CHANGELOG.md` heading all name the same version.

## Per-release checklist

1. **Branch off `main`**: `release-vX.Y.Z-<short-theme-slug>` (e.g.
   `release-v0.6.0-the-write-path`).
2. **One commit per roadmap item**, each message prefixed with the item id
   (`CAP-2: …`, `INT-5: …`). Keep the full test suite green after every item.
3. **Bump the version** in both manifest files (the DOC-7 step of the release) so the version
   fields match the release you're cutting.
4. **CHANGELOG entry as the final commit** — a new `## vX.Y.Z — YYYY-MM-DD — "<theme>"` section
   listing every shipped item, matching the format of the previous entries, and stating the
   **re-bootstrap** flag (see below). This is the capstone commit before opening the PR.
5. **Open ONE PR** to `main`. All seven required CI checks must be green: hermetic ×4
   ({ubuntu, macos} × {py3.10, py3.12}), the dense lane, shellcheck, and secret-scan.
   (`main` is branch-protected: the required checks are enforced by GitHub per the QUA-12
   ruleset codified in `.github/workflows/ci.yml`; the `resolution`, nightly `scale`, and
   PR-only `memory-review` lanes are not required.)
6. **Gate the merge on check EXIT STATUS**: run `gh pr checks <pr> --watch --fail-fast`
   as its **own command**, and run `gh pr merge` as a **subsequent** command only after the
   watch exits 0. Never chain a merge after a *display* command (a `gh pr view` / board
   glance has no meaningful exit status) — the v1.27.0 merge fired against a red board
   exactly that way. The gate works: on PR #86 the first watch exited non-zero on a real
   red (a hermetic macos flake), and the merge waited until a rerun took the board green.
   Branch protection backstops this mechanically — plain `gh pr merge` refuses on a red or
   missing required check (`--admin` is the explicit, deliberate escape hatch; never
   routine).
7. **Squash-merge** with the title `vX.Y.Z — <theme> (#<PR>)` — the exact pattern of the
   existing merge commits on `main`.
8. **Tag only after the main-push run is green**: the squash-merge triggers a fresh CI run
   on `main` — watch it (`gh run list --branch main`, then `gh run watch <run-id>`) and
   push the `vX.Y.Z` tag only after that run concludes green (the remedy the v1.27.0
   release used). `release.yml` verifies the four-way match on the tag push.

## The re-bootstrap flag

Every CHANGELOG entry states **`re-bootstrap: yes`** or **`re-bootstrap: no`**:

- **yes** — this release changed `plugin/requirements.txt`; users must re-run `/hippo:bootstrap`
  so the venv picks up the new/updated deps. `/hippo:doctor`'s bootstrap check (COR-11) already
  flags a stale venv by comparing the requirements hash.
- **no** — deps are unchanged; the code swap on update is sufficient.

Independently, `/hippo:doctor`'s `plugin_version` check surfaces an installed-vs-bootstrapped
version delta (DOC-7): the bootstrap sentinel records the `plugin_version` it provisioned for, so
after any update doctor can name the delta and prompt a re-bootstrap to re-record it.

## Naming and namespace invariants

Per the one-canonical-name invariant, renames are clean breaks with a version bump, never compat
shims. The plugin is `hippo`; skills are `/hippo:*`; env vars are `HIPPO_*`. A release that
renames anything says so loudly in its CHANGELOG entry (see v0.4.0's `MEMOBOT_* → HIPPO_*`).
