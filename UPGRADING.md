# Upgrading hippo

hippo upgrades are **forward-only** (marketplace = latest-only; see [STABILITY.md](STABILITY.md))
and, where your committed data is involved, **agent-gated and per-item** — hippo never rewrites your
corpus autonomously. `/hippo:doctor` detects when something is behind and names the exact next step.
There are three kinds of "upgrade", in increasing order of involvement.

## 1. Update the plugin (usually nothing else)

Update via the marketplace (`/plugin`). Most releases need nothing more. When a release changes the
Python dependencies, the CHANGELOG entry says **re-bootstrap: yes** — run `/hippo:bootstrap` again to
rebuild the venv. `/hippo:doctor`'s `plugin_version` / `bootstrap` checks flag a stale bootstrap.

## 2. Rebuild a derived cache (automatic)

The recall index carries its own `schema_version` (currently 6), separate from your corpus format.
When the plugin's schema is newer than the persisted index, **every load path treats the stale index
as absent** and the next SessionStart refresh does one full rebuild — no action needed. The same is
true of the link cache, staleness cache, and telemetry ledgers: they are gitignored, rebuildable
artifacts. A `schema_version` bump is a re-index, never a corpus migration.

## 3. Migrate the corpus format (agent-gated, per-item)

This is the only upgrade that touches your **committed markdown**. The corpus format is stamped in
`.claude/memory/.format` (`{"corpus_format": N}`, currently **4**) and describes the on-disk
conventions your memory files follow. It is committed with the corpus (not a cache), and it only ever
increases. `read_corpus_format()` reads it (a corpus with no marker reads as format 1);
`write_corpus_format()` stamps it.

When you open an older corpus with a newer plugin, `/hippo:doctor` reports, for example:

```
⚠ corpus format is v2, this plugin writes v4 — the corpus needs a MIGRATION before newer-format
features work; hippo never migrates automatically — follow the doctor-driven path in
plugin/memory/README.md ('Corpus format versioning')
```

(That "Corpus format versioning" section in the engine reference is the canonical short form; this
file is its top-level, worked-example expansion of the same doctor-driven path.) hippo does **not**
migrate for you: a bulk autonomous rewrite of your memories would violate the
guiding invariant that corrective writes are per-item and agent-gated. Instead, the migration is a
deliberate pass you run (ask Claude to "migrate my hippo corpus to the current format") that follows
this pattern:

### Worked example — a format 2 → 3 migration (the template every migration follows)

Suppose format 3 moved a frontmatter field out of the top level and under `metadata:` (an
illustrative convention change — real bumps are itemized in the CHANGELOG). The migration is:

1. **Detect and scope.** Confirm the gap and list the files to touch:
   ```bash
   PYTHONPATH=plugin python3 -c "from memory.provenance import read_corpus_format; print(read_corpus_format('.claude/memory'))"
   ```
   This prints `2`. The files to migrate are every `*.md` under `.claude/memory/` (never `MEMORY.md`
   floor, never the gitignored caches).

2. **Edit each memory, one at a time, agent-gated.** For each file, the agent reads it, applies the
   convention change (here: move the field under `metadata:`), and leaves the body verbatim. This is
   an ordinary markdown edit — reviewable as a git diff, per item, never a blind sweep. Skip a file
   that already conforms; stop and ask if one is ambiguous.

3. **Verify before stamping.** Re-read the migrated files (or run `/hippo:doctor` /
   `/hippo:audit`) to confirm the corpus is internally consistent and nothing was dropped. The index
   will rebuild itself on the next refresh, so you don't hand-edit any cache.

4. **Stamp the new format — only after every file is migrated and verified:**
   ```bash
   PYTHONPATH=plugin python3 -c "from memory.provenance import write_corpus_format; write_corpus_format('.claude/memory', 3)"
   ```
   Then commit the whole migration (the memory edits **and** the bumped `.format` marker) as one
   reviewable commit. Because the corpus is markdown-in-git, the entire migration is a diff you can
   inspect, revert, or cherry-pick — there is no opaque migration step.

The key properties, true of every corpus migration: **doctor detects it, you drive it per item, the
body is preserved, and the `.format` stamp lands last** — so a half-finished migration never claims a
format it hasn't reached. Migrate straight to the current version (v4) by applying each intermediate
step's convention change in order, then stamping the final version once.
