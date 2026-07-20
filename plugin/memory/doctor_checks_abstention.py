"""The dense-floor and abstention checks for the deterministic doctor engine — decomposed
out of ``doctor_checks_recall.py``, which keeps link density (GRA-3), edge rot (GRF-1),
salience evidence (MSR-5), hot-path latency (INT-5, KPI-3), the recall channel/blind-spot/
drop-autopsy trio (MSR-3, SIG-3, MSR-4), injection precision (SIG-4, KPI-2), the rules-plane
checks (RUL-1, RUL-2, RUL-4), and the T11 succession/update instruments.

These three travel together because they are one instrument in three parts — RET-9's two
halves plus the structural statement they are read against:

- ``check_abstention_cold_start`` (RET-11) is the STRUCTURAL half: on the BM25-only path
  there is no semantic signal at all, so nothing below it can be measured meaningfully.
- ``check_abstention_floor_sanity`` (RET-9) is the LEAK DETECTOR: it runs the corpus's own
  off-topic fixture against the live index and reports the EMPIRICAL per-corpus rate.
- ``check_floor_calibration`` (GRF-3) is the CALIBRATION half: it compares the configured
  dense floor to the number ``eval_floor.floor_sweep`` recommended, so the detector's "this
  floor is too permissive" can be answered with "raise it to what".

All three are advisory by construction (inv4): they name both numbers and a remedy, and a
HUMAN edits ``recall._DENSE_FLOOR_BY_MODEL`` or sets ``HIPPO_DENSE_FLOOR``. Nothing here —
or anywhere — auto-writes a floor. ``DoctorContext`` lives in ``doctor_checks_env``; the
``doctor`` façade owns the ordered registry these are called from, and re-imports every
name below so the check IDs and their order are unchanged.
"""

from __future__ import annotations

import os
from typing import Dict

from .doctor_checks_env import DoctorContext


def check_abstention_cold_start(ctx: DoctorContext) -> Dict[str, str]:
    """RET-11: on the BM25-only path there is no SEMANTIC signal to rank a coincidental
    keyword overlap below a real hit.

    Measured, not assumed. BM25 admits any prompt that shares even ONE keyword with a memory,
    and no lexical threshold (summed IDF mass, matched-token count, or single-token IDF)
    separates that coincidental overlap from a genuine single-keyword match without also
    dropping real hits — on the golden fixture the two classes overlap in every
    BM25-observable signal (a real "combining a keyword and an embedding ranking" query and an
    off-topic "classic French onion soup" query each match exactly one distinctive token).
    Only the dense model tells them apart, which is why no false-precision BM25 floor was
    shipped. Read-only; ``ok`` once dense is serving or nothing is indexed yet; never raises.

    ABS-2 — WHAT WARMING THE MODEL DOES AND DOES NOT DO. This check used to end "run
    /hippo:bootstrap to warm the dense model and enable the abstention floor", which inverted
    the actual effect on the abstention METRIC. recall() abstains iff ALL FOUR rankings are
    empty (``recall.py``'s ``not rankings`` hard skip); warming the model ADDS two candidate
    lanes (dense description + dense body), so it can only ever make abstention RARER, never
    more common. Demonstrated on a one-memory corpus (a puppy-care memory, probe "kitten
    nutrition and grooming schedule" — lexically disjoint, so both BM25 lanes are empty):
    dense OFF abstains at rate 1.0, dense ON at 0.0. The dense floor's job is to stop the
    dense ranker from admitting the whole corpus (cosine has no notion of "no match"), NOT to
    produce abstentions. Warming is still the right nudge — for RANKING quality, which is what
    this message now says.
    """
    try:
        from .build_index import _load_manifest, default_index_dir

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        if manifest is None:
            return {
                "status": "ok",
                "message": "abstention: no index built yet — SessionStart will build it.",
            }
        if manifest.get("dense_ready"):
            return {
                "status": "ok",
                "message": "dense model warmed — the semantic ranking signal is live "
                "(this is a ranking property, not an abstention one; ABS-2).",
            }
        return {
            "status": "warn",
            "message": "recall is serving BM25-only (dense model not warmed): an off-topic "
            "prompt that shares even one keyword with a memory surfaces a weak match, and "
            "there is no semantic signal to rank it below a real hit — no lexical threshold "
            "separates a coincidental overlap from a genuine one (RET-11). Run "
            "/hippo:bootstrap to warm the dense model and get that ranking signal. Note it "
            "will not make recall abstain MORE often: abstention needs every lane empty, and "
            "the dense lanes only add candidates (ABS-2).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"abstention cold-start check failed: {exc}."}


# Bound the per-corpus abstention sweep — doctor runs on every /hippo:doctor and each off-topic
# query is a real recall() (an embed on the dense path), so cap how many the sanity check runs.
_ABSTENTION_SANITY_MAX_QUERIES = 25


def check_abstention_floor_sanity(ctx: DoctorContext) -> Dict[str, str]:
    """RET-9: per-corpus dense-floor sanity — run the corpus's OWN off-topic fixture against the
    live index and warn when off-topic queries LEAK through (the distribution-overlap symptom).

    The dense floor (0.60 for bge) is a global default; a particular corpus can still admit
    off-topic prompts whose scores overlap its real hits. This runs the corpus-local fixture
    (``<memory_dir>/.audit-fixtures/recall_abstention_set.yaml``) against the live index and
    reports how many actually abstained — the EMPIRICAL per-corpus number, distinct from
    ``check_abstention_cold_start`` (RET-11)'s STRUCTURAL bm25-only statement, and it fires on
    the dense path too when a corpus's own floor is too permissive.
    Bounded (``_ABSTENTION_SANITY_MAX_QUERIES``), read-only, deterministic (recall() is), and
    degrades to ``ok``/``warn`` — never raises. Skips cleanly when there is no fixture or index.

    ABS-1 — THE FIXTURE IS HAND-AUTHORED; NOTHING GENERATES IT. This docstring used to say it
    was "written by ``/hippo:audit``" and the no-fixture branch below told the user to "run
    /hippo:audit to generate one". Both were false for the whole life of the check, and the
    cause was a NAME COLLISION worth pinning so the claim cannot come back: SIG-6's
    ``draft_abstention_fixtures`` flow (``/hippo:audit``, ``/hippo:consolidate`` Step 5, the
    ``abstention_fixtures`` MCP tool) drafts the ABSTENTION BACKLOG — queries recall answered
    with NOTHING and arguably should not have — and its confirmed rows land in
    ``recall_hard_set.yaml`` tagged ``category: abstention``. That is the OPPOSITE polarity
    from this file, which lists queries that SHOULD abstain. Two different fixtures, one word.
    A check whose remediation pointed at a capability that never existed sat inert instead of
    reporting a real leak, so the pointer is now the honest one: author the rows yourself.
    """
    try:
        from .build_index import _load_manifest, default_index_dir, load_index
        from .eval_recall import GATE_ABSTENTION, abstention_rate, load_abstention_set

        fixture = os.path.join(ctx.memory_dir, ".audit-fixtures", "recall_abstention_set.yaml")
        queries = load_abstention_set(fixture)
        if not queries:
            return {
                "status": "ok",
                "message": "abstention floor: no corpus-local off-topic fixture "
                "(.audit-fixtures/recall_abstention_set.yaml) — nothing generates this file; "
                "author it by hand as a list of `- query: \"...\"` rows this corpus should "
                "have NO answer for. (SIG-6's abstention_fixtures flow drafts the opposite "
                "polarity — queries that DID abstain — into recall_hard_set.yaml.)",
            }
        index_dir = default_index_dir(ctx.memory_dir)
        if _load_manifest(index_dir) is None:
            return {"status": "ok", "message": "abstention floor: no index built yet."}
        index = load_index(index_dir)
        sample = queries[:_ABSTENTION_SANITY_MAX_QUERIES]
        result = abstention_rate(index, sample, index_dir=index_dir)
        rate, n = result["rate"], result["n"]
        abstained = round(n * rate)
        backend = "dense" if index.dense_ready else "bm25-only"
        if rate < GATE_ABSTENTION:
            # ABS-2: state the MECHANISM, not a knob. recall abstains only when all four
            # lanes are empty, and the BM25 lanes admit on a single shared token with no
            # score floor of any kind — so on a corpus whose description+body vocabulary
            # already covers these probes, this number reports coverage, not a mis-set floor.
            return {
                "status": "warn",
                "message": f"abstention floor: only {abstained}/{n} off-topic fixture queries "
                f"abstained on this {backend} corpus (rate {rate:.2f} < {GATE_ABSTENTION}) — "
                "off-topic prompts may inject. Recall abstains only when EVERY lane comes up "
                "empty (dense + BM25, description + body), and the BM25 lanes admit on a single "
                "shared token with no score floor at all, so a probe that overlaps any memory's "
                "wording will surface something whatever the floor is set to. HIPPO_DENSE_FLOOR "
                "gates the dense lanes only. Read this as a measurement of what the corpus "
                "admits, not a knob that is set wrong.",
            }
        return {
            "status": "ok",
            "message": f"abstention floor: {abstained}/{n} off-topic queries correctly abstained "
            f"on this {backend} corpus (rate {rate:.2f}).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"abstention floor sanity check failed: {exc}."}


# GRF-3: how far the sweep's recommended dense floor may sit from the configured table
# entry before the advisory line escalates ok -> warn. 0.02 — inside that band the two
# agree to within measurement noise on a small fixture set; beyond it the configured
# floor is measurably mis-calibrated for THIS corpus.
_FLOOR_CAL_TOLERANCE = 0.02


def check_floor_calibration(ctx: DoctorContext) -> Dict[str, str]:
    """GRF-3 (RET-9's calibration half): configured dense floor vs the persisted sweep.

    Reads the gitignored ``floor_sweep.json`` the ``--floor-sweep`` CLI wrote — doctor
    never runs the sweep itself (it embeds every fixture query with the dense model;
    seconds, not a health-check budget). Advisory-only by design (inv4): the line
    NAMES both numbers and the remedy; a human edits ``recall._DENSE_FLOOR_BY_MODEL``
    or sets ``HIPPO_DENSE_FLOOR`` — nothing here (or anywhere) auto-writes the table.
    A sweep keyed to a different corpus fingerprint is reported stale, not compared —
    a floor recommendation from last month's corpus says nothing about today's.
    """
    try:
        from .build_index import _load_manifest, default_index_dir, load_index
        from .eval_floor import read_floor_sweep
        from .eval_ledger import corpus_fingerprint
        from .recall import _dense_floor

        sweep = read_floor_sweep(ctx.memory_dir)
        if sweep is None:
            return {
                "status": "ok",
                "message": "floor calibration: no sweep recorded — "
                "`python -m memory.eval_recall --floor-sweep` writes one (RET-9).",
            }
        # Staleness leg: only when an index is actually loadable — the sweep report is
        # self-contained (model + recommendation), so a deleted/rebuildable index cache
        # must not silence the comparison; it just can't prove freshness.
        index_dir = default_index_dir(ctx.memory_dir)
        if _load_manifest(index_dir) is not None:
            index = load_index(index_dir)
            if index is not None and len(index):
                if sweep.get("corpus_fingerprint") != corpus_fingerprint(index):
                    return {
                        "status": "ok",
                        "message": "floor calibration: recorded sweep is STALE (corpus changed "
                        "since) — re-run `python -m memory.eval_recall --floor-sweep`.",
                    }
        configured = _dense_floor(sweep.get("model"))
        recommended = sweep.get("recommended")
        if not isinstance(recommended, (int, float)):
            return {"status": "ok", "message": "floor calibration: recorded sweep is unreadable."}
        delta = round(float(recommended) - float(configured), 4)
        overlap = " (no clean on/off-topic separation on this corpus)" if sweep.get("overlap") else ""
        if abs(delta) <= _FLOOR_CAL_TOLERANCE:
            return {
                "status": "ok",
                "message": f"floor calibration: configured {configured} ≈ recommended "
                f"{recommended} (Δ{delta:+}){overlap}.",
            }
        # ABS-4: on an OVERLAPPING corpus the recommendation is a trade, not a fix — name
        # its measured cost instead of pointing at the table, or this repeats the powerless
        # remedy ABS-2 removed one check over.
        if sweep.get("overlap"):
            remedy = (
                f"adopting it would cut {sweep.get('cut_on')} real quer(ies) and still leak "
                f"{sweep.get('leaked_off')} of {sweep.get('off_n')} probes THROUGH THE DENSE "
                "LANE, which BM25 admits past anyway (ABS-2) — evidence, not a fix"
            )
        else:
            remedy = (
                "edit recall._DENSE_FLOOR_BY_MODEL or set HIPPO_DENSE_FLOOR yourself; "
                "advisory only, nothing auto-writes"
            )
        return {
            "status": "warn",
            "message": f"floor calibration: configured {configured} vs sweep-recommended "
            f"{recommended} (Δ{delta:+}, off-topic max {sweep.get('off_max')}){overlap} — "
            f"{remedy}.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"floor-calibration check failed: {exc}."}
