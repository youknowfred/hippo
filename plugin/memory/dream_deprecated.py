"""SRF-7: dream keeps the link pass; the three tiers that did not earn their place are deprecated.

Ruling V2Q-7 kept the Tier-A linker (bridges, unlinked mentions, undeclared refines) and
cut reverse replay, the generative tier and de-parasite unless each showed at least +0.02
field recall@10. Measured on scratch copies of hippo, em-growth-labs and Skyline (dense,
offline, the OBS-5 hard sets, explicit and hook mode, everything else equal), each of the
three moved recall@10 by 0.000 on every corpus:

  - reverse replay never reaches recall: its boosts only reorder dream's own replay
    worklist and candidate list, and on all three corpora the apply set came out the same
    with and without them;
  - the generative tier has staged no memory on any of the three corpora;
  - de-parasite has applied no dedup-merge and retracted no edge on any of them, and
    applying every merge its own report proposes leaves recall@10 where it was.

So each is OFF by default and deprecated here: still reachable when asked for by name,
one plain line says it is going, and v2.0.0 deletes the code (the removal cut). Nothing
is deleted in a 1.x minor, no frozen name goes, and the linker and the ``dream:links``
grammar are untouched.

THE SWITCHES — one line per tier. Set a value to False to keep that tier: the notice
stops, and reverse replay runs inside every discovery pass again, as it did before. (The
generative tier was already opt-in and de-parasite was only ever run by name, so for those
two the switch is the notice alone.)
"""

from __future__ import annotations

from typing import List, Optional

REVERSE_REPLAY_DEPRECATED = True  # off by default; HIPPO_DREAM_REVERSE_REPLAY=1 runs it
GENERATIVE_DEPRECATED = True  # --generate / HIPPO_DREAM_GENERATIVE=1 still run it
DEPARASITE_DEPRECATED = True  # --deparasite / --dedup-merge still run it

REVERSE_REPLAY_ENV = "HIPPO_DREAM_REVERSE_REPLAY"

_LABELS = {
    "reverse_replay": "reverse replay (HIPPO_DREAM_REVERSE_REPLAY)",
    "generative": "the generative tier (generate, sweep_drafts, archive_draft, prospective)",
    "deparasite": "de-parasite (deparasite, dedup_merge)",
}


def _is_deprecated(tier: str) -> bool:
    return {
        "reverse_replay": REVERSE_REPLAY_DEPRECATED,
        "generative": GENERATIVE_DEPRECATED,
        "deparasite": DEPARASITE_DEPRECATED,
    }.get(tier, False)


def reverse_replay_enabled() -> bool:
    """Whether a discovery pass computes reverse-replay boosts. A set env var decides
    (``HIPPO_DREAM_REVERSE_REPLAY=1`` on, anything else off); unset follows the switch."""
    from .settings import env_bool

    v = env_bool(REVERSE_REPLAY_ENV)
    if v is not None:
        return v
    return not REVERSE_REPLAY_DEPRECATED


def deprecated_tiers() -> List[str]:
    """The deprecated tiers, in a fixed order (doctor and the tests read this)."""
    return [t for t in ("reverse_replay", "generative", "deparasite") if _is_deprecated(t)]


def notice(tier: str) -> Optional[str]:
    """The ONE plain line an explicitly requested deprecated tier prints; None when kept."""
    if not _is_deprecated(tier):
        return None
    name = {"reverse_replay": "Reverse replay", "generative": "The generative tier",
            "deparasite": "De-parasite"}[tier]
    return (
        f"Note: {name} is deprecated and will be removed in v2.0.0 (it measured no recall "
        "gain); the link pass is unchanged."
    )


def with_notice(text: str, tier: str) -> str:
    """``text`` with the tier's deprecation line appended (unchanged when the tier is kept)."""
    line = notice(tier)
    return f"{text}\n{line}" if line else text


def check_dream_tiers(ctx) -> dict:
    """Doctor: name what dream has deprecated, and warn when one of those tiers is turned
    on here (v2.0.0 removes it). Pure reads: two env/option lookups and the dream ledger."""
    tiers = deprecated_tiers()
    if not tiers:
        return {"status": "ok", "message": "dream: every tier is current."}
    on: List[str] = []
    try:
        from .settings import env_bool

        if "reverse_replay" in tiers and env_bool(REVERSE_REPLAY_ENV):
            on.append("reverse replay is turned on (HIPPO_DREAM_REVERSE_REPLAY)")
        if "generative" in tiers:
            from .dream_generate import generative_enabled
            from .dream_ledgers import generated_rows

            if generative_enabled():
                on.append("the generative tier is turned on (HIPPO_DREAM_GENERATIVE)")
            live = [r for r in generated_rows(ctx.memory_dir) if r.get("state") == "active"]
            if live:
                on.append(f"{len(live)} generated draft(s) are still in the corpus")
    except Exception:
        pass
    deprecated = "; ".join(_LABELS[t] for t in tiers)
    if on:
        return {
            "status": "warn",
            "message": f"dream: {'; '.join(on)}. Deprecated, removed in v2.0.0: {deprecated}. "
            "The link pass (`hippo dream`) is unchanged.",
        }
    return {
        "status": "ok",
        "message": f"dream: the link pass is current; off by default and deprecated, "
        f"removed in v2.0.0: {deprecated}.",
    }
