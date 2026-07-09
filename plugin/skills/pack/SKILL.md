---
description: Extract chosen memories from this project's corpus into a shareable pack directory (manifest.json in the shipped-pack shape, portability-linted, consequential defaults marked for individual confirm). EXTRACT ONLY — pack install/update from foreign sources is gated on the v0.8.0 trust spine and does not exist yet. Use for "make a pack from these memories", "export my memories as a pack", "share these lessons", "/hippo:pack".
---

# /hippo:pack — extract memories into a shareable pack

Turn proven corpus memories into a pack another human can review and seed: a directory of
portable `.md` files plus a `manifest.json` structurally identical to the shipped starter
packs (same shape, same individual-confirm markers — a consumer gets the same protections
either way). This skill is OUTBOUND ONLY; see the hard rules for why inbound is gated.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MEMORY_DIR="$REPO_ROOT/.claude/memory"
```

## What this does, in order

1. **Choose the memories, with the user.** `/hippo:recall --list-by-type` maps the corpus;
   the user names which memories belong in the pack and what the pack is called. Ask for a
   destination directory OUTSIDE the corpus (e.g. `~/packs/<pack-name>`).

2. **Extract.** One call, every guard built in (each name must exist and be un-retired;
   an existing manifest or target file refuses the WHOLE extract — never clobber a pack):

   ```bash
   "$PY" -c \
     "import sys, json; from memory.packs import pack_extract; \
      r = pack_extract(sys.argv[1].split(','), sys.argv[2], memory_dir=sys.argv[3], \
                       repo_root=sys.argv[4], version=sys.argv[5]); \
      print(json.dumps(r, indent=1)); sys.exit(0 if r['manifest'] else 1)" \
     "<name-a>,<name-b>" "<dest-dir>" "$MEMORY_DIR" "$REPO_ROOT" "0.1.0"
   ```

   What the extract did to each file (report it): provenance (`cited_paths` /
   `source_commit`) and `steer:` governance were STRIPPED (pack files are
   repo-independent by design), and `metadata.pack` / `metadata.pack_version` were
   stamped (doctor's pack-drift check reads them back).

3. **Walk the findings with the user.** `result["findings"]` maps each memory to its
   portability findings:
   - `consequential_default` findings became `confirm: "individual"` + `reason` in the
     manifest AUTOMATICALLY — tell the user which files carry them and why (anyone
     seeding this pack will be asked for a per-item yes on exactly those files, the same
     mechanism the shipped packs use). Confirm the user wants each such memory in the
     pack at all.
   - `repo_coupling` findings (a body naming absolute paths / git remotes) do NOT block —
     offer to edit the EXTRACTED copy in `<dest>` to generalize the text, or leave it
     with the user knowingly accepting repo-specific content in a shareable pack.

4. **Hand it over.** The pack directory is ordinary reviewable markdown + one manifest —
   the user shares it however they share files (a repo, a gist, a tarball). Consumers
   review and seed it BY HAND today (read the manifest, copy the `.md` files per-item,
   honoring the individual-confirm markers) — there is deliberately no installer to point
   them at.

## Hard rules

- **Install/update DO NOT EXIST — do not improvise them.** A foreign pack is the
  public-corpus prompt-injection threat; the v0.8.0 trust spine (SEC-5 consent shows
  descriptions, SEC-6 fingerprint re-review, SEC-7 inject-time banner) is not shipped,
  so there is no `install_pack`, no `update_pack`, and this skill must never bulk-copy
  someone ELSE'S pack into a corpus. The reviewed inbound paths are `/hippo:import`
  (per-item, secret-linted) and per-item `/hippo:new`.
- **A refusal means nothing was written.** Existing manifest / existing target file /
  retired memory all refuse the whole extract with zero filesystem change.
- **Extraction never edits the source corpus.** The project's own memories are read,
  never modified — the portable rewrite happens only in the copies under `<dest>`.
- **Individual-confirm markers are derived, not optional.** Never strip a
  `confirm: "individual"` entry from an extracted manifest to make a pack "easier to
  seed" — that marker is the consumer's protection.
