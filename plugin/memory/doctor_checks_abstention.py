"""The dense-floor and abstention checks for the deterministic doctor engine — decomposed
out of ``doctor_checks_recall.py``, which keeps link density (GRA-3), edge rot (GRF-1),
salience evidence (MSR-5), hot-path latency (INT-5, KPI-3), the recall channel/blind-spot/
drop-autopsy trio (MSR-3, SIG-3, MSR-4), injection precision (SIG-4, KPI-2), the rules-plane
checks (RUL-1, RUL-2, RUL-4), and the T11 succession/update instruments.

These travel together because they are one instrument in parts — RET-9's two
halves plus the structural statement they are read against:

- ``check_abstention_cold_start`` (RET-11) is the STRUCTURAL half: on the BM25-only path
  there is no semantic signal at all, so nothing below it can be measured meaningfully.
- ``check_abstention_floor_sanity`` (RET-9) is the LEAK DETECTOR: it runs the corpus's own
  off-topic fixture against the live index and reports the EMPIRICAL per-corpus rate.
- ``check_floor_calibration`` (GRF-3) is the CALIBRATION half: it compares the configured
  dense floor to the number ``eval_floor.floor_sweep`` recommended, so the detector's "this
  floor is too permissive" can be answered with "raise it to what".
- ``check_high_space_calibration`` (HCR-3) is that half again for the high profile's
  embedding space: is the resolved space calibrated at all, and its own sweep against its
  own floor. It prints nothing off the high profile.

All three are advisory by construction (inv4): they name both numbers and a remedy, and a
HUMAN edits ``recall._DENSE_FLOOR_BY_MODEL`` or sets ``HIPPO_DENSE_FLOOR``. Nothing here —
or anywhere — auto-writes a floor. ``DoctorContext`` lives in ``doctor_checks_env``; the
``doctor`` façade owns the ordered registry these are called from, and re-imports every
name below so the check IDs and their order are unchanged.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

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
                "(this is a ranking property, not an abstention one).",
            }
        return {
            "status": "warn",
            "message": "recall is serving BM25-only (dense model not warmed): an off-topic "
            "prompt that shares even one keyword with a memory surfaces a weak match, and "
            "there is no semantic signal to rank it below a real hit — no lexical threshold "
            "separates a coincidental overlap from a genuine one. Run "
            "/hippo:setup to warm the dense model and get that ranking signal. Note it "
            "will not make recall abstain MORE often: abstention needs every lane empty, and "
            "the dense lanes only add candidates.",
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
                "have NO answer for. (the abstention_fixtures flow drafts the opposite "
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
            # ABS-2: state the MECHANISM, not a knob. HOT-2: on a calibrated dense model,
            # recall injects only when the dense and lexical lanes corroborate a memory; a
            # BM25-only index (or an uncalibrated model) has no gate, so the lanes admit on
            # a single shared token and this number reports coverage, not a mis-set floor.
            if index.dense_ready:
                mechanism = (
                    "Recall injects only when the dense and lexical lanes agree on a memory "
                    "(or one lane is strong on its own), so a probe that leaks shares several "
                    "terms with, or sits very close in meaning to, something this corpus "
                    "holds. why on one of these queries names the memory it matched."
                )
            else:
                mechanism = (
                    "Without the dense model there is no corroboration gate: the lexical "
                    "lanes admit on a single shared token, so a probe that overlaps any "
                    "memory's wording surfaces something. Warming the model turns the gate on."
                )
            return {
                "status": "warn",
                "message": f"abstention floor: only {abstained}/{n} off-topic fixture queries "
                f"abstained on this {backend} corpus (rate {rate:.2f} < {GATE_ABSTENTION}) — "
                f"off-topic prompts may inject. {mechanism} Read this as a measurement of "
                "what the corpus admits, not a knob that is set wrong.",
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


# HOT-8: how much tighter than the sweep's own recommendation the count band may be.
_COUNT_BAND_TOLERANCE = 0.01


def _count_band_note(sweep: dict):
    """``(clause, warn)`` for the count rule's band against the sweep: the gap under the best
    cosine that holds the expected memory in 3 of 4 of this corpus's hard-set queries. A
    band tighter than that drops rows that were the answer. Empty for an older sweep."""
    count = sweep.get("count")
    if not isinstance(count, dict):
        return "", False
    band, rec = count.get("band"), count.get("band_recommended")
    if not isinstance(band, (int, float)) or not isinstance(rec, (int, float)):
        return "", False
    if float(band) + _COUNT_BAND_TOLERANCE >= float(rec):
        return f" Row-count band {band} covers this corpus's hard set (it asks for {rec}).", False
    return (
        f" Row-count band {band} is tighter than this corpus's hard set asks for ({rec}): "
        f"set HIPPO_COUNT_BAND={rec} to serve the rows it is cutting.",
        True,
    )


def _compare(lead: str, sweep: dict, configured: float, *, gap_ok: bool = False) -> Dict[str, str]:
    """One sweep against the floor configured for the space it was measured in: ok inside
    ``_FLOOR_CAL_TOLERANCE``, else a warn naming both numbers and the remedy. Shared by the
    lean line and the high-space line, so the two read the same way.

    ``gap_ok`` (the high line): a floor anywhere inside a cleanly separated corpus's gap is
    ok. The sweep recommends the gap's midpoint, but the high space's floor was set on the
    LOW side of a wide clean gap on purpose (RET-1's rule; it keeps the weakest real query),
    so against the midpoint a correct floor would warn on every corpus. Inside the gap no
    probe leaks and no query is cut at the dense lane, which is what the line is for."""
    recommended = sweep.get("recommended")
    if not isinstance(recommended, (int, float)):
        return {"status": "ok", "message": f"{lead}: recorded sweep is unreadable."}
    delta = round(float(recommended) - float(configured), 4)
    overlap = " (no clean on/off-topic separation on this corpus)" if sweep.get("overlap") else ""
    count_note, count_warn = _count_band_note(sweep)
    lo, hi = sweep.get("off_max"), sweep.get("on_min")
    if (gap_ok and not sweep.get("overlap") and isinstance(lo, (int, float))
            and isinstance(hi, (int, float)) and lo < configured <= hi):
        return {
            "status": "warn" if count_warn else "ok",
            "message": f"{lead}: configured {configured} sits inside this corpus's clean gap "
            f"(off-topic max {lo}, on-topic min {hi}; the sweep's midpoint is {recommended})."
            f"{count_note}",
        }
    if abs(delta) <= _FLOOR_CAL_TOLERANCE:
        return {
            "status": "warn" if count_warn else "ok",
            "message": f"{lead}: configured {configured} ≈ recommended "
            f"{recommended} (Δ{delta:+}){overlap}.{count_note}",
        }
    # ABS-4: on an OVERLAPPING corpus the recommendation is a trade, not a fix — name
    # its measured cost instead of pointing at the table, or this repeats the powerless
    # remedy ABS-2 removed one check over.
    if sweep.get("overlap"):
        remedy = (
            f"adopting it would cut {sweep.get('cut_on')} real quer(ies) and still leak "
            f"{sweep.get('leaked_off')} of {sweep.get('off_n')} probes THROUGH THE DENSE "
            "LANE, which BM25 admits past anyway — evidence, not a fix"
        )
    else:
        remedy = (
            "edit recall._DENSE_FLOOR_BY_MODEL or set HIPPO_DENSE_FLOOR yourself; "
            "advisory only, nothing auto-writes"
        )
    return {
        "status": "warn",
        "message": f"{lead}: configured {configured} vs sweep-recommended "
        f"{recommended} (Δ{delta:+}, off-topic max {sweep.get('off_max')}){overlap} — "
        f"{remedy}.{count_note}",
    }


def check_floor_calibration(ctx: DoctorContext) -> Dict[str, str]:
    """GRF-3 (RET-9's calibration half): configured dense floor vs the persisted sweep.

    Reads the gitignored ``floor_sweep.json`` the ``--floor-sweep`` CLI wrote — doctor
    never runs the sweep itself (it embeds every fixture query with the dense model;
    seconds, not a health-check budget). Advisory-only by design (inv4): the line
    NAMES both numbers and the remedy; a human edits ``recall._DENSE_FLOOR_BY_MODEL``
    or sets ``HIPPO_DENSE_FLOOR`` — nothing here (or anywhere) auto-writes the table.
    A sweep keyed to a different corpus fingerprint, or measured with a different embedding
    model than the configured one, is a WARN naming the re-run, not a comparison — a floor
    recommendation from last month's corpus says nothing about today's (HOT-10; it was an
    ok line before, so a months-old sweep read as healthy). No sweep at all stays ok.
    """
    try:
        from .build_index import DEFAULT_MODEL, _load_manifest, default_index_dir, load_index
        from .eval_floor import read_floor_sweep
        from .eval_ledger import corpus_fingerprint
        from .recall import _dense_floor

        sweep = read_floor_sweep(ctx.memory_dir)
        if sweep is None:
            return {
                "status": "ok",
                "message": "floor calibration: no sweep recorded — "
                "`hippo eval --floor-sweep` writes one.",
            }
        # HOT-10: a sweep measured in another embedding space says nothing about this one.
        if sweep.get("model") and sweep.get("model") != DEFAULT_MODEL:
            return {
                "status": "warn",
                "message": f"floor calibration: recorded sweep is STALE (measured with "
                f"{sweep.get('model')}, configured model is {DEFAULT_MODEL}) — re-run "
                "`hippo eval --floor-sweep`.",
            }
        # Staleness leg: only when an index is actually loadable — the sweep report is
        # self-contained (model + recommendation), so a deleted/rebuildable index cache
        # must not silence the comparison; it just can't prove freshness.
        index_dir = default_index_dir(ctx.memory_dir)
        if _load_manifest(index_dir) is not None:
            index = load_index(index_dir)
            if index is not None and len(index):
                if sweep.get("corpus_fingerprint") != corpus_fingerprint(index):
                    # HOT-10: a WARN, not ok — the recommendation was measured on a corpus
                    # that no longer exists, and the line used to read as healthy.
                    return {
                        "status": "warn",
                        "message": "floor calibration: recorded sweep is STALE (corpus changed "
                        f"since it ran on {sweep.get('generated_at') or 'an unknown date'}) — "
                        "re-run `hippo eval --floor-sweep`.",
                    }
        return _compare("floor calibration", sweep, _dense_floor(sweep.get("model")))
    except Exception as exc:
        return {"status": "warn", "message": f"floor-calibration check failed: {exc}."}


def check_high_space_calibration(ctx: DoctorContext) -> Optional[Dict[str, str]]:
    """HCR-3: the high profile's embedding space against the calibration tables, and its
    own sweep against its own floor. No line off the high profile (None).

    The floor, the corroboration gate and the row-count rule are each calibrated per
    embedding space (``recall_rank._DENSE_FLOOR_BY_MODEL``, ``recall_abstain._COSINE_BY_MODEL``,
    ``recall_count._COUNT_BY_MODEL``). A space with no floor or gate entry is not an error
    recall raises: it silently takes the 0.50 default floor with the gate off. That is every
    MRL-truncated ``model@dim`` width (a different cosine space from the full width; none is
    calibrated, because the bench kept the full width) and any model nobody measured, so
    this line names the resolved space and what it lacks (WARN).

    A calibrated space then gets the lean line's comparison, against the high-space report
    ``hippo eval --floor-sweep`` writes on a high machine (``floor_sweep.high.json``): stale
    when it was measured in another space or on another corpus, else configured vs
    recommended. Advisory only, like the lean line; nothing here writes a table or loads a
    model. Never raises.
    """
    try:
        from .retrieval_profile import resolve_retrieval_profile

        if resolve_retrieval_profile() != "high":
            return None
        from . import embed_mlx as E
        from .recall_abstain import _COSINE_BY_MODEL
        from .recall_count import _COUNT_BY_MODEL
        from .recall_rank import _DENSE_FLOOR_BY_MODEL, _DENSE_FLOOR_DEFAULT

        space = E.resolve_high_space()
        lead = f"high space calibration [{space}]"
        gate, count = _COSINE_BY_MODEL.get(space), _COUNT_BY_MODEL.get(space)
        lacks = [label for label, have in (
            ("dense floor", space in _DENSE_FLOOR_BY_MODEL),
            ("corroboration gate", gate is not None),
            ("row-count rule", count is not None),
        ) if not have]
        if "dense floor" in lacks or "corroboration gate" in lacks:
            model, dim = E.parse_space(space)
            what = (f"a {dim}-wide truncation of {model} is its own cosine space"
                    if "@" in space else f"{model} has not been measured")
            effects = []
            if "dense floor" in lacks:
                effects.append(f"the default dense floor {_DENSE_FLOOR_DEFAULT}")
            if "corroboration gate" in lacks:
                effects.append("no corroboration gate (an off-topic prompt that shares a term "
                               "with a memory injects)")
            if "row-count rule" in lacks:
                effects.append("no row-count rule (a served call carries up to k rows)")
            calibrated = sorted(m for m in _DENSE_FLOOR_BY_MODEL
                                if m in _COSINE_BY_MODEL and E.parse_space(m) is not None)
            # No width is calibrated, on purpose: the profile bench tried the 4B at 1024 and
            # 2048 against its full width at one floor and neither added a hit over the full
            # width (planning/HCR-6.evidence.2026-11-25.json), so the full width stays.
            choose = (f"the full width is the measured choice (unset {E.DIM_ENV} or its preset key)"
                      if "@" in space else
                      f"choose one with {E.MODEL_ENV} / {E.DIM_ENV} (or their preset keys)")
            return {
                "status": "warn",
                "message": f"{lead}: NOT CALIBRATED — no {' or '.join(lacks)} entry for this "
                f"space ({what}), so high recall runs with {', '.join(effects)}. Calibrated "
                f"high space(s): {', '.join(calibrated) or 'none'}; {choose}, or measure this "
                "one with `hippo eval --floor-sweep` before relying on it.",
            }
        have = (f"floor {_DENSE_FLOOR_BY_MODEL[space]}, gate {gate['strong']}/{gate['support']}, "
                + (f"row-count band {count['band']} / wide {count['wide']}" if count
                   else "no row-count rule (a served call carries up to k rows)"))
        from .build_index_dense import ROW_WHOLE, row_text

        if row_text(space) == ROW_WHOLE:  # HCR-9: what those numbers were measured on
            have += ", on whole-memory rows"
        from .eval_floor import read_floor_sweep

        sweep = read_floor_sweep(ctx.memory_dir, high=True)
        if sweep is None:
            return {
                "status": "ok",
                "message": f"{lead}: {have}. No sweep recorded in this space — "
                "`hippo eval --floor-sweep` writes one when the high model is servable here.",
            }
        if sweep.get("model") != space:
            return {
                "status": "warn",
                "message": f"{lead}: recorded sweep is STALE (measured in {sweep.get('model')}, "
                f"the resolved high space is {space}) — re-run `hippo eval --floor-sweep`.",
            }
        from .build_index import _load_manifest, default_index_dir, load_index
        from .eval_ledger import corpus_fingerprint
        from .recall import _dense_floor
        from .recall_warm_high import high_index_dir

        hdir = high_index_dir(default_index_dir(ctx.memory_dir))
        if _load_manifest(hdir) is not None:
            index = load_index(hdir)
            if index is not None and len(index) and sweep.get("corpus_fingerprint") != corpus_fingerprint(index):
                return {
                    "status": "warn",
                    "message": f"{lead}: recorded sweep is STALE (corpus changed since it ran "
                    f"on {sweep.get('generated_at') or 'an unknown date'}) — re-run "
                    "`hippo eval --floor-sweep`.",
                }
        return _compare(lead, sweep, _dense_floor(space), gap_ok=True)
    except Exception as exc:
        return {"status": "warn", "message": f"high-space calibration check failed: {exc}."}
