# hippo — Road to v2.0.0, amendment 2: recall performance on the estate

**Status: RATIFIED 2026-11-13, under the owner's delegation.** On 2026-11-13 the owner asked the
v1.45.0 build lead to "prioritize directions that drive memory performance for our group of
repos, then ratify as you recommend". The ranking and the rulings in §B4 are that lead's, made
under that delegation, and they are recorded here as the ruling for every item below. This is
a companion to [`ROADMAP.v2.md`](ROADMAP.v2.md) (ratified 2026-10-03) and
[`ROADMAP.v2.amendment-1.md`](ROADMAP.v2.amendment-1.md) (ratified 2026-11-12). It edits
neither. [`ROADMAP.v2.yaml`](ROADMAP.v2.yaml) carries its rows under `amendment_2`, and that is
where their status lives. Authored at **v1.45.0**.

**Why an amendment.** v1.45.0 was measured on the estate's three largest corpora before it was
cut, and the measurements named what still costs recall there. One of them contradicted a
chartered item: OUT-4 (consent for bytes a hippo verb wrote) would have cleared none of the
memories recall was withholding, because none of them was written by a hippo verb.

**Method.** One session with four builders. Every number below was measured on 2026-11-13 on
scratch copies of the corpora (hippo, em-growth-labs "emgl", Skyline), in hook mode with the
dense model, unless it says otherwise. Per the public-repo rule, estate evidence appears as
counts and mechanisms only.

---

## B1. Ground truth, measured 2026-11-13

| # | What | Measured |
|---|---|---|
| 1 | Memories withheld from recall on two estate corpora | 67 (26 and 41). Written by a hippo verb: 0. Through Claude Code's native memory path: 56. With a file tool on a repo path: 10. Unknown: 1. On both, the native auto-memory directory is a symlink into the primary checkout's `.claude/memory` |
| 2 | emgl's hook-mode hard-set recall@10 | 0.495 on the release tree (52 of 105; 0.467 before v1.45.0 read bare floor pointers). 31 rows expect a journal. With the lanes switched off entirely: 54 of 105 |
| 3 | Characters per injecting prompt on emgl | 1,455 at a median of four rows, against the 1,000 bound v1.45.0 left open. Description text is 48% of the block |
| 4 | Lessons that reach a sibling corpus | 0 of 10 probe lessons: 6 of the 10 have no near-duplicate in any other corpus, and v1.45.0's promote kind proposes only lessons that have one |
| 5 | Speed on emgl | Explicit recall embeds the query twice when a section sub-index exists. One staleness scan takes 67 to 210 s under load and drops chunks at its timeout |
| 6 | Signals other gates are read through | The episode record lists 24.7 names per prompt, of which 8.1 were served rows. One of emgl's 105 hard-set rows pins a memory that no longer exists |

## B2. Corrections to the chartered train

- **OUT-4 is not built as chartered.** Ruling V2Q-17 asked for a classification of withheld
  memories first. It came in on 2026-11-13 (row 1 above): 0 of 67 were written by a hippo verb.
  OUT-4 stays in the ledger, unbuilt, and OUT-5 below takes the route the classification found.
- **The journals are not most of HOT-7's hard-set loss.** Of emgl's 31 journal-only rows, the
  expected journal is the first fused candidate in 3, inside the top six in 1 more, and past the
  fifteenth candidate in the rest. No admission reaches those; they are a ranking problem.

## B3. Items

Sizes as in the ratified document. HOT, OUT and OWN continue their numbering.

- **OUT-5 Consent for bytes a session wrote on this machine** `L` · deps OUT-1, GAT-1 ·
  replaces OUT-4's route — A corpus may opt in, with `"consent": {"session_writes": true}` in
  `hippo.json`, honoured only after `hippo trust policy --accept`. The PreToolUse hook then
  notes, for a Write, Edit or MultiEdit of a memory file in a corpus the session works in,
  whether the file was absent, consented or unconsented, and the hash of the bytes the tool
  call will leave. The PostToolUse hook records a per-file consent only when that note exists
  for the same tool call, the prior state was absent or consented, the bytes on disk hash to
  the prediction, the call was not made inside a subagent, and the bytes pass the secret and
  threat lints a review gates on. Bytes that arrive by pull, merge, checkout or a shell
  redirect are never granted. *Gate:* 0 withheld after a native-path Write and after an Edit
  of a consented file with the key on; the trust file byte-identical with the key off; pulled
  bytes and an Edit of an unconsented file still withheld; p95 under 20 ms added to a touch
  outside a memory dir.
- **HOT-13 Journals admitted by their own signal** `M` · deps HOT-7, HOT-8 — On a corpus that
  declares `journals`, the journal that is the first of all the fused candidates is served
  when the count rule finds evidence of its own for it: at most one per prompt, beside the
  prompt's rows and never in place of one. *Gate:* emgl hook hard-set recall@10 at or above
  0.52; median rows per injecting prompt at or under 5; journals at or under 15% of served
  rows over the replay window; hippo's and Skyline's hard sets not lower.
- **HOT-14 Characters per prompt** `M` · deps HOT-8 — In the prompt hook only, the first row
  keeps its description, later rows' descriptions are cut at a word boundary, the two collapse
  lines name fewer members, and a session's later blocks carry a shorter header. *Gate:* emgl
  at or under 1,000 characters per injecting prompt on the replay; hard-set recall unchanged on
  the three corpora; no served row's name dropped.
- **OWN-4 A promoted lesson keeps its place at home** `S` · deps OWN-2 — A lesson promoted to
  the user tier ranked lower in its home corpus afterwards (a control set went from 10 of 10 to
  6 of 8). Reproduce, find the mechanism, fix it. *Gate:* the home corpus loses none of the ten
  probe answers after promotion.
- **OWN-5 Promotion proposals for a lesson with no twin** `M` · deps OWN-2, OWN-4, FMT-2 — The
  `promote` tend kind also proposes a feedback or user memory that has no near-duplicate in
  another corpus, when it cites no repo path (its derived citation binding is empty) and was
  served or used on three or more distinct days: at most three per listing, most used first.
  Only the corpus being tended and the user tier are written, on a yes. *Gate:* sibling corpora
  gain at least 5 of the ten probe answers after the proposals are applied, and the home corpus
  loses none.
- **HOT-15 Speed on the largest corpus** `M` · deps HOT-11 — One query embedding per explicit
  recall; one history walk per staleness scan, with a partial scan counted instead of silent;
  per-process memos for what a long-lived process re-parsed on every prompt. No index schema
  bump, and results identical. *Gate:* explicit recall p50 on emgl within 20 ms of v1.44.0's;
  hook p50 not higher than v1.45.0's.

Six small defects found while v1.45.0 was built ride the same release without a row of their
own: the episode record's names, the floor lint that reads bare pointers, the merged index's
commit stamp, a promote's floor-pointer removal in outbox mode, the three CLI redirects, and
doctor's count of hard-set pins that no longer resolve. A seventh (a dream draft's cited paths
against the index binding) did not reproduce and is not changed.

## B4. Rulings (RECORDED 2026-11-13, under the owner's delegation)

- **V2Q-28 — Which directions, in what order.** **Ruled:** the six items of §B3, ranked by
  what they return to recall on the estate: withheld memories first (a withheld memory has
  recall 0), then the journals, the characters per prompt, lessons between repos, speed, and
  the signals the other gates are read through.
- **V2Q-29 — What replaces OUT-4.** *Options:* (a) build OUT-4 as chartered; (b) consent for
  the bytes the post-tool hook saw a session's own Write or Edit put into a trusted corpus,
  opt-in by a consented key; (c) wait for NAT-2's inbox channel at v2.0.0. **Ruled: (b).** As
  chartered OUT-4 clears 0 of 67. An Edit of a file whose prior bytes were not consented grants
  nothing, and pulled, merged or checked-out bytes are never granted this way. NAT-2's channel
  stays at v2.0.0.
- **V2Q-30 — Should the promote kind propose a lesson that has no twin?** **Ruled: yes,** when
  it cites no repo path and was served or used on three or more distinct days, at most three
  per listing.
- **V2Q-31 — The dense floor default.** **Ruled: it stays 0.60,** and no swept floor is applied
  automatically. HOT-8 cuts weak rows per row; the sweep stays a doctor recommendation.
- **V2Q-32 — May the prompt hook shorten a description?** **Ruled: yes,** at a word boundary,
  in hook mode only. Explicit recall is unchanged.
- **V2Q-33 — What is not in this release.** **Ruled:** TND-4, PLT-3, NAT-2's channel, the
  v2.0.0 format and removal items, and the seven questions at the end of `STABILITY.v2.md`.
  None gates recall performance.
- **V2Q-34 — The release.** **Ruled:** v1.46.0, prepared ahead on the rehearsed tree. An item
  whose measurement says it cannot meet its gate is reported with its number and not shipped.
  Merges, tags and releases still wait for the owner.
- **V2Q-35 — The floor diet as one reviewed list.** Recorded 2026-11-14. This one is the
  owner's own word, not the delegation, and it is not about recall performance; it is kept
  here because this is the open rulings list. v1.46.1 left a reviewed batch for the floor diet
  unbuilt, as a ruling not made. **Ruled 2026-11-13: build it.** The floor diet's demotions
  join the V2Q-1 (b) class (V2Q-20 added conserved-text folds the same way): one verdict over
  an enumerated list the user was shown, applied by a digest of the floor's bytes and that
  list. **Ruled 2026-11-14,** on the builder's advice: each line is judged again at its turn
  and skipped, never forced, rather than pinned to the review's rankings; a line that carries
  more than one pointer is never in the list; a line the tend state keeps is proposed past, in
  the single-item queue as well. No other tend kind gets a list form. The invariant's text in
  `ROADMAP.yaml` is amended to say so. Shipped in v1.47.0.

## B5. The train, amended

v1.46.0 is added between v1.45.0 and v2.0.0 for the six items above and the six fixes. v2.0.0
is unchanged.
