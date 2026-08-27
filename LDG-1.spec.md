# LDG-1 — external-ledger read-time join (design round)

**Status: DRAFT design round for owner review — options + recommendations, no build.**
Commissioned 2026-08-27 from the em-growth-labs growing-pains audit (slate item S2, owner
picked "take S2 into a design round"). Authored against v1.32.0's tree. Companion, not
prerequisite: `recall --for-issue` (slate S4) shares §3's id extraction but can ship
independently at any time.

## 0. The tension this resolves

The commissioning corpus ratified a boundary — *memory cites Linear by id only; state words
rot* — and then measured the boundary failing on contact: 0→16 status-bearing index lines in
the 9 days after a clean scrub, because **no surface shows the state an id points at**, so
sessions retype MERGED/Built/Live where they can see it. FLR-1 (v1.32.0) polices the
symptom. LDG removes the cause: a memory cites `GRO-901`, and the SURFACE renders the
current state at read time — joined from a ledger file already committed in the repo —
so state is never stored in memory again. Zero network, zero LLM: the join source is the
corpus's own committed snapshot (em-growth-labs: `LINEAR-SNAPSHOT.yaml`, a generated
read-only mirror, ~246KB), which fits the recall-is-$0/prompt ethos because git already
ships it to every clone.

**What LDG never does, in any direction:** call the Linear API (or any network), write to
any tracker, or write status text into a memory file. Render-only, always stamped with the
snapshot's as-of date — a status without its date is exactly the rot the boundary bans.

## 1. Declaration — the `.format` marker's third policy key

Following `volatile_paths` (VOL-1) and `floor_lint` (FLR-1): corpus-owned, committed,
travels through git, NO writer, garbled-degrades-to-absent (ED-4).

```json
"ledgers": [
  {
    "name": "linear",
    "path": "LINEAR-SNAPSHOT.yaml",
    "id_re": "\\bGRO-\\d+\\b",
    "entry_key": "id",
    "status_key": "status"
  }
]
```

- `path` — toplevel-relative, must resolve INSIDE the repo (reject `..`/absolute — the
  ledger renders into context, so the declaration must not be able to exfiltrate an
  arbitrary file's lines).
- `id_re` — the citation vocabulary. Compiled once; uncompilable → entry dropped.
- `entry_key`/`status_key` — the line-shape contract: an entry BEGINS at a line whose first
  YAML-ish token is `<entry_key>: <an id_re match>`, and its status is the first
  `<status_key>: <value>` line before the next entry. A deliberate LINE-window scan, not a
  YAML parse — the 246KB file is read once per cache build (§2), never on the hot path, and
  the scan shape survives the generated file's comments/anchors without a yaml dependency.

## 2. The three candidate shapes (the actual decision)

| | **A — derived cache (recommended)** | B — render-time scan | C — first-class citation axis |
|---|---|---|---|
| Mechanism | SessionStart builds `<index_dir>/ledger.json` (`{id: {status, ledger, as_of}}`) from the declared files, exactly like the JIT `touchmap.json`; render surfaces do one dict lookup per id | each render greps the ledger file for the ids it is about to print | ids become `cited_ids` extracted at `build_index` time into the manifest; ledger join at render as in A |
| Hot-path cost | O(1) per id (one JSON read per process, already the touchmap pattern) | one 246KB file scan per recall render — measured ~57k tokens of YAML re-read per session in the field audit; violates the corpus's own read-discipline | as A |
| New machinery | 1 module (~250 lines) + cache write in SessionStart + render hooks | render hooks only (~120 lines) | A + extractor change + manifest field + derivation-version question |
| Where ids come from | render-time `id_re` match over the text ALREADY being rendered (description/floor line) — no new stored field | same | index-time extraction (a DRV-5 event: values, not shape — but every corpus re-stamps) |
| Staleness of the join | `as_of` = the ledger file's last git commit date, stamped per cache build, rendered on every row | same date, re-derived per render (one `git log -1` per render — additional cost) | as A |
| Failure degrades to | absent cache → no annotations (today's behavior, byte-identical) | scan failure → no annotations | as A |

Direction B is listed because it is the smallest diff, and rejected for cause: it moves the
whole-file read onto the hot path that the rest of the design exists to protect. Direction C
is the most principled (ids become provenance, `--for-issue` gets an O(1) reverse index,
LDG-2 arming gets exact join keys) but drags a derivation-version event and a corpus-wide
re-stamp into what can ship without either. **A ships the user-visible value with the
touchmap's proven architecture and leaves C as a later upgrade that changes no surface
behavior — the cache's shape is the same either way.**

## 3. Render surfaces (scoped, in order)

1. **`recall` injection rows + `/hippo:recall` view** — a row whose rendered text matches a
   declared `id_re` gains one suffix: `· GRO-901 In Progress (snapshot 2026-08-26)`. Cap:
   first 2 ids per row (a round memory can cite a dozen; the row is not a dashboard).
2. **`recall --for-issue <id>`** (S4, separate item) — consumes the same cache for its
   header line; its corpus-side join (which memories cite the id) is its own logic.
3. **SessionStart worklist/staleness items** — same suffix where an item's memory
   description carries an id. Second wave: the producer budget is the scarcest surface
   (measured at 100% saturation), so annotations land here only after 1 proves the shape.
4. **JIT reminders** — deliberately NOT annotated: the reminder line is budgeted at one
   line and already elides.

Trust: every render site above is already SEC-1-gated (recall/producers short-circuit on
untrusted corpora; the JIT precedent re-checks at fire time). The cache BUILD also gates on
trust — an untrusted corpus's declaration must not direct hippo to read files at all.

## 4. LDG-2 (follow-up, gated on LDG-1) — event-driven arming

The TYPE-1 pairing: TYPE-1 exempted `type: project` from arming because whole-repo drift
meters velocity; a LEDGER STATE FLIP is the high-signal event worth re-arming on. When the
cache build sees an id's status cross into a terminal set (declared per ledger:
`"terminal": ["Done", "Canceled"]`) since the previous cache, memories whose
description/floor line carries that id join the reconsolidation worklist once — "the round
this memory tracks closed; review the line". Same ARMING-only discipline as VOL-1/TYPE-1
(detection stays blind; suppression counted; per-item human verdicts). Out of scope for
LDG-1; named here so the cache keeps the one field it needs (previous-status memory —
a `prev` map in the same JSON).

## 5. Questions for the owner

1. **Direction A (derived cache) as scoped in §2–§3 — proceed to an implementation spec on
   it, or do you want C's first-class `cited_ids` costed in full first?**
2. **Is the §1 line-window contract acceptable for `LINEAR-SNAPSHOT.yaml`, or should the
   declaration instead name a committed EXTRACT file (e.g. a generated `id → status` JSON
   the snapshot refresh script writes), keeping hippo's parser trivial at the cost of one
   more generated file in the EMGL repo?**
3. **Render cap of 2 ids per row and recall-first/SessionStart-second sequencing — agree,
   or start narrower (`--for-issue` header only) and let the recall-row annotation earn its
   bytes in a measured pass?**
4. **LDG-2's terminal-set arming: charter it now as the committed follow-up (so TYPE-1's
   exemption has its event-driven replacement on the roadmap), or hold until LDG-1 has
   field time?**
