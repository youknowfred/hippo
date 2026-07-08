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
5. **Open ONE PR** to `main`. All six CI checks must be green: hermetic ×4
   ({ubuntu, macos} × {py3.10, py3.12}), the dense lane, and shellcheck.
6. **Squash-merge** with the title `vX.Y.Z — <theme> (#<PR>)` — the exact pattern of the
   existing merge commits on `main`.
7. **Tag** the merge commit `vX.Y.Z` and push it. `release.yml` verifies the four-way match.

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
