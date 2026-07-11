# hippo demos

## `git_drift.sh` — the staleness hero demo (POS-4)

```bash
demo/git_drift.sh
```

Builds a throwaway git repo, writes a memory that cites a function, edits that function, and shows
hippo flag the memory **stale** — because *the code it cites moved*, not because a timer expired.
This is the one behavior no calendar-decay memory tool can reproduce; run it in ~5 seconds, no
model download needed (`HIPPO_DISABLE_DENSE=1` is set for you). It leaves nothing behind.

Expected finish (step 4, after the cited function is edited):

```
⚠ Memory staleness — 1 memories cite code that changed since they were written (…):
  • session-token-rotation: src/auth.py
```

---

## DOC-10 — README demo GIF storyboard  ⚠️ NEEDS A HUMAN TO RECORD

A short terminal recording belongs at the top of the root README (an asciinema cast or a GIF).
A model can't produce a real screen recording, so **this is flagged for the maintainer to record**.
Below is the exact, tested storyboard — every command here works today (the git-drift beats are
`demo/git_drift.sh`, already verified). Keep it under ~30 seconds; type at a readable pace.

Recommended tooling: [`asciinema rec`](https://asciinema.org/) → `agg` to GIF, or `vhs` (a
scripted terminal-GIF tool — the beats below map almost 1:1 to a `.tape` file).

**Storyboard (6 beats):**

1. **Install** — inside Claude Code:
   `/plugin marketplace add youknowfred/hippo` then `/plugin install hippo@hippo`
   *(caption: "a Claude Code plugin — install in two lines")*
2. **Bootstrap** — `/hippo:bootstrap`
   *(caption: "one online step, once per machine — builds the venv + warms the model")*
3. **Init** — `/hippo:init`, then fill `user_role.md` (or accept the interactive fill)
   *(caption: "seeds .claude/memory/ + wires recall — once per project")*
4. **Remember** — type: *remember this: session tokens rotate on every privilege change — 3
   retries then hard-fail (see src/auth.py)* → hippo writes the memory
   *(caption: "say 'remember this' — it lands as a reviewable markdown diff")*
5. **Recall resurfaces it** — in a later turn, ask: *how do we handle session tokens?* → the
   memory is injected/recalled inline
   *(caption: "the right memory, on demand, at $0 per prompt")*
6. **Git-drift flag** — edit `rotate_session_token()` in `src/auth.py`, commit, start a new
   session → hippo flags: *⚠ Memory staleness — session-token-rotation: src/auth.py*
   *(caption: "and it tells you when the code a memory cites has moved")*

Beats 4–6 are exactly what `demo/git_drift.sh` runs non-interactively — record against a real
project, or against the demo repo that script builds, whichever reads more cleanly on screen.

**When recorded:** drop the asset in `demo/` (e.g. `demo/hippo.gif` or an asciinema cast id) and
add it near the top of the root `README.md`, just under the one-line description. Depends on POS-4
(done — this dir) and the POS-1 lead (done).
