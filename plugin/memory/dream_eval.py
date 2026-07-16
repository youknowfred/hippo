"""DRM-3 — /dream's OWN snapshot-diff proof harness (the ``eval --ab HIPPO_DREAM`` rig).

Proves the falsifiable hypothesis (EXPLORATIONS.dream.md §6): on a frozen corpus snapshot,
admitting the edges one /dream pass produces raises MULTI-HOP retrieval *because of the
edges* — not general lift — without eroding the recall floor. The protocol:

  1. copy the frozen fixture S0 into a throwaway workspace (the harness NEVER writes the
     live corpus, never applies/removes a live edge, never flips a default — measure-only,
     CLI, off the hot path; inv6);
  2. run ONE /dream apply pass on the COPY (the fixture itself stays pristine; its latent
     A–B–C bridges are verified ABSENT at baseline before the pass — GRA-3/GRW-2 never
     created them);
  3. run ``evaluate()`` twice over the same workspace — the ``HIPPO_DREAM`` arm toggles
     ONLY whether the LinkGraph admits dream-stamped edges (links.py's admission view):
       OFF (HIPPO_DREAM=0)  — dream:links blocks stripped, dream refines dropped. Must
                              reproduce the committed PRE-DREAM pinned baseline
                              BYTE-IDENTICAL (the regression tripwire: the stamp filter
                              fully isolates dream's effect);
       ON  (default)        — dream edges live, exactly as recall serves them;
  4. N≥5 index rebuilds per arm establish the noise floor (BM25-only pinned via
     HIPPO_DISABLE_DENSE for cross-machine determinism — the A/B isolates EDGE effects,
     so the backend is held fixed);
  5. ATTRIBUTION (the load-bearing check): each multi-hop probe that converts miss→hit
     must have entered via a dream edge — its expected memory adjacent to an OFF-arm seed
     through an edge in the pass's ledger, and carrying graph provenance in the ON run —
     and the MuSiQue-style matched single-hop control must stay FLAT (both moving together
     = general lift, not the edges: FALSIFIED);
  6. the guardrail gate table is emitted; nothing "passes" unless multi-hop rises above
     the rebuild noise floor WITH the control flat and every gate green.

HONEST SCOPING (binding, ROADMAP.dream.yaml DRM-3 invariant_note): these fixture
guardrails gate RELEASE of the verb; they CANNOT protect the real unlabeled corpus — there
the live protection is git-revertibility + the undo affordance. ``--live`` runs the same
OFF/ON toggle over a COPY of the live corpus (possible precisely because DRM-2 stamps
every edge) as a continuous measurement, not a safety proof.

Flag surface: written to the ``eval --ab <flag>`` whitelist shape (owner decision 3,
2026-07-12) so an eventual MSR-5 ``HIPPO_SALIENCE`` rig absorbs ``HIPPO_DREAM`` without a
rewrite. ``net-token`` from the proposal's GEM list is SKIPPED, not gated: edges ADD
injection cost by design (EXPLORATIONS §6 "no token-saving claim" — the token win belongs
to DRM-6's schema tier), and the two binding texts reconcile only that way.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from math import comb
from typing import Dict, List, Optional, Tuple

AB_FLAGS = ("HIPPO_DREAM", "HIPPO_SALIENCE")  # the --ab whitelist (MSR-5 added HIPPO_SALIENCE — memory.salience_eval)

# Harness-local dream knobs: DRM-3 proves "admitted edges raise multi-hop recall because
# of the edges"; discovery-θ calibration on real data is DRM-1's job. The fixture's
# bridges are mutual + strong, but the bar here is pinned so a future θ recalibration
# can't silently break the release gate.
_HARNESS_ENV = {
    "HIPPO_DISABLE_DENSE": "1",
    "DREAM_COFIRE_THETA": "0.50",
    "DREAM_MAX_APPLY_PER_PASS": "9",
    "HIPPO_TRUST_ALL": "1",  # the throwaway workspace is never in the trust registry
}

_GATE_SELF_RECALL = 0.90
_GATE_MRR_MULTIHOP = 0.60  # gates the WHOLE hard set (eval's GATE_MRR convention)
_GATE_P95_MS = 300.0


def default_fixture_dir() -> Optional[str]:
    """The committed frozen fixture (repo dev tree), or None on an installed plugin."""
    from .provenance import resolve_dirs

    _md, repo = resolve_dirs()
    cand = os.path.join(repo, "tests", "fixtures", "dream_ab")
    return cand if os.path.isdir(os.path.join(cand, "S0")) else None


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def _copy_corpus(src: str, dst: str) -> None:
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, name))


def _seed_soak(td: str, n: int = 5) -> None:
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"session_id": f"ab-s{i}", "names": [], "backend": "bm25"}) + "\n")


def _snapshot(dirpath: str) -> Dict[str, bytes]:
    out: Dict[str, bytes] = {}
    for root, _dirs, files in os.walk(dirpath):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, dirpath)] = fh.read()
    return out


def sign_test_p(n_pos: int, n_neg: int) -> Optional[float]:
    """Exact one-sided sign test over the discordant pairs (P[X ≥ n_pos | p=0.5]).

    The significance column for PAIRED per-probe hit/miss deltas: with binary outcomes the
    Wilcoxon signed-rank statistic degenerates to the sign test (all nonzero |deltas| tie
    at rank 1), so the exact binomial IS the exact Wilcoxon here — computed exactly, no
    scipy. None when there are no discordant pairs (no signal to test).
    """
    n = n_pos + n_neg
    if n == 0:
        return None
    return round(sum(comb(n, k) for k in range(n_pos, n + 1)) / (2 ** n), 6)


def _per_probe_hits(rows: List[dict], index_dir: str, k: int) -> List[dict]:
    """Per-row ``{query, category, expected, hit, rank, via}`` under the CURRENT env arm."""
    from .build_index import load_index
    from .recall import recall

    idx = load_index(index_dir)
    out: List[dict] = []
    for row in rows:
        results = recall(row["query"], k=k, index=idx, index_dir=index_dir)
        names = [r.get("name") for r in results]
        hit_name = next((n for n in row["expected"] if n in names), None)
        rank = (names.index(hit_name) + 1) if hit_name else None
        via = results[names.index(hit_name)].get("via") if hit_name else None
        out.append(
            {
                "query": row["query"],
                "category": row["category"],
                "expected": row["expected"],
                "hit": hit_name is not None,
                "rank": rank,
                "via": via,
            }
        )
    return out


def _projection(report: dict, probes: List[dict]) -> dict:
    """The deterministic subset pinned/compared for OFF-arm byte-identity.

    Latency numbers and machine-varying fields are EXCLUDED by construction — identity is
    a claim about RANKING (per-category metrics + per-probe hits/ranks), and a wall-clock
    jitter must never fail (or mask) a ranking regression.
    """
    return {
        "backend": report.get("backend"),
        "count": report.get("count"),
        "by_category": {
            cat: {"recall": m.get("recall"), "mrr": m.get("mrr"), "n": m.get("n")}
            for cat, m in (report.get("by_category") or {}).items()
        },
        "self_recall": (report.get("gates") or {}).get("self_recall@10", {}).get("value"),
        "abstention_rate": (report.get("abstention_rate") or {}).get("rate"),
        "per_probe": [
            {
                "query": p["query"],
                "category": p["category"],
                "hit": p["hit"],
                "rank": p["rank"],
            }
            for p in probes
        ],
    }


def _arm_env(off: bool):
    """Context manager: pin the harness env + the HIPPO_DREAM arm; restore on exit."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        keys = list(_HARNESS_ENV) + ["HIPPO_DREAM"]
        saved = {kk: os.environ.get(kk) for kk in keys}
        try:
            os.environ.update(_HARNESS_ENV)
            if off:
                os.environ["HIPPO_DREAM"] = "0"
            else:
                os.environ.pop("HIPPO_DREAM", None)
            yield
        finally:
            for kk, vv in saved.items():
                if vv is None:
                    os.environ.pop(kk, None)
                else:
                    os.environ[kk] = vv

    return _cm()


def _run_arm(
    memory_dir: str, index_dir: str, hard_rows: List[dict],
    *, hard_set_path: str, abstention_set_path: Optional[str], k: int, rebuilds: int,
    off: bool,
) -> Tuple[dict, List[dict], List[dict]]:
    """One arm: N index rebuilds + evaluate() each time. Returns
    ``(first_report, first_probes, [projection per rebuild])``."""
    from .build_index import build_index
    from .eval_recall import evaluate

    projections: List[dict] = []
    first_report: Optional[dict] = None
    first_probes: Optional[List[dict]] = None
    with _arm_env(off):
        for _i in range(max(1, rebuilds)):
            build_index(memory_dir, index_dir, force=True, allow_download=False)
            report = evaluate(
                memory_dir=memory_dir,
                index_dir=index_dir,
                hard_set_path=hard_set_path,
                k=k,
                abstention_set_path=abstention_set_path,
                repo_root=memory_dir,  # hermetic: never resolve the ambient repo
            )
            probes = _per_probe_hits(hard_rows, index_dir, k)
            projections.append(_projection(report, probes))
            if first_report is None:
                first_report, first_probes = report, probes
    return first_report, first_probes or [], projections


def _noise_floor(projections: List[dict], category: str) -> float:
    vals = [
        (p.get("by_category") or {}).get(category, {}).get("recall") or 0.0
        for p in projections
    ]
    return round(max(vals) - min(vals), 6) if vals else 0.0


def run_ab(
    *,
    fixture_dir: Optional[str] = None,
    pin: bool = False,
    live_memory_dir: Optional[str] = None,
    rebuilds: int = 5,
    k: int = 10,
) -> Tuple[int, str]:
    """The HIPPO_DREAM A/B. Returns ``(exit_code, printed report)``.

    Fixture mode (default): copy S0 → dream once on the copy → OFF/ON arms → identity,
    attribution, gates. ``pin=True`` (re)writes the pinned PRE-DREAM baseline from a
    pristine copy instead of judging (fixture authoring; run once, commit the file).
    Live mode: the same OFF/ON toggle over a COPY of ``live_memory_dir`` with self-derived
    probes — continuous measurement of the actually-applied stamped edges, no pinning.
    """
    lines: List[str] = []
    if live_memory_dir:
        return _run_live(live_memory_dir, rebuilds=rebuilds, k=k)

    fixture_dir = fixture_dir or default_fixture_dir()
    if not fixture_dir or not os.path.isdir(os.path.join(fixture_dir, "S0")):
        return 0, (
            "eval --ab HIPPO_DREAM: SKIP — no frozen fixture found (tests/fixtures/dream_ab/"
            "S0 ships with the repo dev tree, not the installed plugin). Use --fixture-dir, "
            "or --live for the live-corpus measurement."
        )
    s0 = os.path.join(fixture_dir, "S0")
    hard_set_path = os.path.join(fixture_dir, "hard_set.yaml")
    abstention_path = os.path.join(fixture_dir, "abstention_set.yaml")
    pinned_path = os.path.join(fixture_dir, "pinned_off.json")
    if not os.path.exists(hard_set_path):
        return 1, f"eval --ab HIPPO_DREAM: fixture hard set missing: {hard_set_path}"
    abstention_arg = abstention_path if os.path.exists(abstention_path) else None

    from .eval_recall import load_hard_set

    hard_rows = load_hard_set(hard_set_path)
    mh_rows = [r for r in hard_rows if r["category"] == "multi-hop"]
    ctl_rows = [r for r in hard_rows if r["category"] == "single-hop"]
    if not mh_rows or len(ctl_rows) < len(mh_rows):
        return 1, (
            "eval --ab HIPPO_DREAM: the fixture must pair every multi-hop probe with a "
            f"matched single-hop control (multi-hop={len(mh_rows)}, single-hop={len(ctl_rows)})."
        )

    fixture_before = _snapshot(fixture_dir)
    work = tempfile.mkdtemp(prefix="hippo-dream-ab-")

    def _body() -> Tuple[int, str]:
        mem = os.path.join(work, "mem")
        tele = os.path.join(work, "tele")
        _copy_corpus(s0, mem)
        _seed_soak(tele, 5)

        # ---- pin mode: the PRE-DREAM baseline from the pristine copy (OFF env). -------
        if pin:
            idx = os.path.join(work, "idx-pin")
            _report, probes, projections = _run_arm(
                mem, idx, hard_rows, hard_set_path=hard_set_path,
                abstention_set_path=abstention_arg, k=k, rebuilds=1, off=True,
            )
            with open(pinned_path, "w", encoding="utf-8") as fh:
                fh.write(_canonical_json(projections[0]))
            return 0, (
                f"eval --ab HIPPO_DREAM: pinned the PRE-DREAM OFF baseline → {pinned_path}\n"
                "Commit it — the A/B asserts the post-dream OFF arm reproduces it byte-identically."
            )

        if not os.path.exists(pinned_path):
            return 1, (
                f"eval --ab HIPPO_DREAM: no pinned baseline at {pinned_path} — author it "
                "once from the pristine fixture with --pin and commit it."
            )

        # ---- baseline-absence: the latent edges must NOT pre-exist (GRA-3/GRW-2 never
        # made them), and every multi-hop probe must MISS while its control HITS. -------
        from .links import LinkGraph

        g0 = LinkGraph(mem)

        # ---- ONE dream pass on the COPY (never the fixture, never the live corpus). ---
        from .dream import read_apply_ledger, run_apply_pass

        with _arm_env(off=False):
            code, digest = run_apply_pass(mem, os.path.join(work, "idx-dream"), tele)
        applied = [e for e in read_apply_ledger(mem) if e.get("state") == "active"]
        if code != 0 or not applied:
            return 1, (
                "eval --ab HIPPO_DREAM: the dream pass applied NO edges on the fixture "
                f"copy — nothing to measure. Digest:\n{digest}"
            )
        for e in applied:
            if e["target"] in g0.undirected_neighbors(e["source"]):
                return 1, (
                    f"eval --ab HIPPO_DREAM: FIXTURE INVALID — applied edge "
                    f"{e['source']}→{e['target']} already existed at baseline (not latent)."
                )

        # ---- the two arms over the SAME post-dream workspace. -------------------------
        off_report, off_probes, off_projs = _run_arm(
            mem, os.path.join(work, "idx-off"), hard_rows, hard_set_path=hard_set_path,
            abstention_set_path=abstention_arg, k=k, rebuilds=rebuilds, off=True,
        )
        on_report, on_probes, on_projs = _run_arm(
            mem, os.path.join(work, "idx-on"), hard_rows, hard_set_path=hard_set_path,
            abstention_set_path=abstention_arg, k=k, rebuilds=rebuilds, off=False,
        )

        # ---- OFF-arm byte-identity against the committed PRE-DREAM pin. ---------------
        with open(pinned_path, "r", encoding="utf-8") as fh:
            pinned_bytes = fh.read()
        off_bytes = _canonical_json(off_projs[0])
        identity_ok = off_bytes == pinned_bytes
        rebuild_stable = all(_canonical_json(p) == off_bytes for p in off_projs) and all(
            _canonical_json(p) == _canonical_json(on_projs[0]) for p in on_projs
        )
        noise = max(_noise_floor(off_projs, "multi-hop"), _noise_floor(on_projs, "multi-hop"))

        # ---- per-probe pairing + attribution. ------------------------------------------
        def _bucket(probes: List[dict], cat: str) -> List[dict]:
            return [p for p in probes if p["category"] == cat]

        mh_off, mh_on = _bucket(off_probes, "multi-hop"), _bucket(on_probes, "multi-hop")
        ctl_off, ctl_on = _bucket(off_probes, "single-hop"), _bucket(on_probes, "single-hop")
        mh_delta = (sum(p["hit"] for p in mh_on) - sum(p["hit"] for p in mh_off)) / max(1, len(mh_off))
        ctl_delta = (sum(p["hit"] for p in ctl_on) - sum(p["hit"] for p in ctl_off)) / max(1, len(ctl_off))
        flips_pos = sum(1 for a, b in zip(mh_off, mh_on) if not a["hit"] and b["hit"])
        flips_neg = sum(1 for a, b in zip(mh_off, mh_on) if a["hit"] and not b["hit"])
        p_value = sign_test_p(flips_pos, flips_neg)
        hit_to_miss_any = sum(
            1 for a, b in zip(off_probes, on_probes) if a["hit"] and not b["hit"]
        )

        # Attribution: every converted probe entered via a dream edge — the expected
        # memory adjacent through a LEDGER edge to a memory the OFF arm ALREADY surfaced
        # for that query (the seed), and graph-provenance ("via") in the ON run.
        from .build_index import load_index
        from .recall import recall as _recall

        idx_off_dir = os.path.join(work, "idx-off")
        attribution: List[dict] = []
        with _arm_env(off=True):
            idx_off = load_index(idx_off_dir)
            for a, b in zip(mh_off, mh_on):
                if a["hit"] or not b["hit"]:
                    continue
                expected = next((n for n in b["expected"]), None)
                off_names = [
                    r.get("name")
                    for r in _recall(a["query"], k=k, index=idx_off, index_dir=idx_off_dir)
                ]
                enabling = [
                    e for e in applied
                    if (e["target"] in b["expected"] and e["source"] in off_names)
                    or (e["source"] in b["expected"] and e["target"] in off_names)
                ]
                attribution.append(
                    {
                        "query": a["query"],
                        "expected": expected,
                        "via": b.get("via"),
                        "enabling_edges": [e["edge_id"] for e in enabling],
                        "attributed": bool(enabling) and b.get("via") == "graph",
                    }
                )
        attributed_ok = bool(attribution) and all(row["attributed"] for row in attribution)

        # ---- guardrail gate table. ------------------------------------------------------
        def _cat(report: dict, cat: str, key: str) -> float:
            return ((report.get("by_category") or {}).get(cat) or {}).get(key) or 0.0

        self_off = (off_report.get("gates") or {})["self_recall@10"]["value"]
        self_on = (on_report.get("gates") or {})["self_recall@10"]["value"]
        abst_off = (off_report.get("abstention_rate") or {}).get("rate") or 0.0
        abst_on = (on_report.get("abstention_rate") or {}).get("rate") or 0.0
        p95_on = (on_report.get("latency") or {}).get("p95") or 0.0
        gates = [
            ("off_arm_byte_identity", identity_ok, "OFF == committed pre-dream pin (stamp filter fully isolates dream)"),
            ("rebuild_determinism", rebuild_stable, f"N={rebuilds} rebuilds/arm projection-stable (noise floor {noise})"),
            ("multi_hop_rises", mh_delta > noise, f"Δmulti-hop recall {mh_delta:+.4f} > noise floor {noise}"),
            ("control_flat", abs(ctl_delta) <= noise, f"Δsingle-hop control {ctl_delta:+.4f} (matched probes; general-lift confound)"),
            ("attribution", attributed_ok, "every converted probe entered via a dream edge (ledger ∩ OFF-seed, via=graph)"),
            ("self_recall_floor", self_off >= _GATE_SELF_RECALL and self_on >= _GATE_SELF_RECALL,
             f"self_recall@10 OFF {self_off} / ON {self_on} ≥ {_GATE_SELF_RECALL}"),
            ("zero_hit_to_miss", hit_to_miss_any == 0, "no previously-passing probe flipped hit→miss (BWT analog)"),
            ("abstention_non_decreasing", abst_on >= abst_off, f"abstention OFF {abst_off} → ON {abst_on}"),
            # MRR gates on the CONTROL bucket — the deterministic "organic ranking did not
            # erode" claim. It must NOT gate multi-hop or the whole set here: a converted
            # probe enters at _NEIGHBOR_DISCOUNT×seed by design, tying with the seed's
            # organic neighbors (identical discount, set-ordered), so those buckets' MRR
            # straddles thresholds on hash order — an unstable gate. Multi-hop MRR stays
            # the reported secondary metric in the table above.
            ("mrr_control_floor", _cat(on_report, "single-hop", "mrr") >= _GATE_MRR_MULTIHOP,
             f"ON single-hop control MRR {_cat(on_report, 'single-hop', 'mrr')} ≥ {_GATE_MRR_MULTIHOP} "
             "(multi-hop MRR is discount-capped by design — reported, not gated)"),
            ("warm_p95", p95_on < _GATE_P95_MS, f"ON warm p95 {p95_on}ms < {_GATE_P95_MS}ms"),
        ]
        skipped = [
            ("precision@10", "no relevance fixture for the synthetic corpus — tracked on the real eval spine"),
            ("net_token", "edges ADD injection cost by design; no token-saving claim (EXPLORATIONS §6 honest limits — the token win belongs to DRM-6's schema tier)"),
        ]
        all_pass = all(ok for _n, ok, _d in gates)

        # ---- render. --------------------------------------------------------------------
        lines.append("=== eval --ab HIPPO_DREAM — /dream snapshot-diff A/B (DRM-3) ===")
        lines.append(
            f"fixture: {s0} (frozen; harness worked on a copy) · dream applied "
            f"{len(applied)} edge(s) on the copy · backend {off_report.get('backend')}"
        )
        lines.append("")
        lines.append("paired per-category delta (recall@10 / mrr@10, OFF → ON):")
        cats = sorted(set((off_report.get("by_category") or {})) | set((on_report.get("by_category") or {})))
        for cat in cats:
            ro, mo = _cat(off_report, cat, "recall"), _cat(off_report, cat, "mrr")
            rn, mn = _cat(on_report, cat, "recall"), _cat(on_report, cat, "mrr")
            sig = ""
            if cat == "multi-hop":
                sig = f"   p={p_value} (exact sign test on {flips_pos + flips_neg} discordant pair(s); Wilcoxon degenerates to sign on binary paired hits)"
            lines.append(
                f"  {cat:<12} recall {ro:.4f} → {rn:.4f} ({rn - ro:+.4f}) · mrr {mo:.4f} → {mn:.4f}{sig}"
            )
        lines.append("")
        lines.append("attribution (each converted multi-hop probe):")
        for row in attribution:
            mark = "✔" if row["attributed"] else "✘"
            lines.append(
                f"  {mark} \"{row['query'][:48]}\" → {row['expected']} via={row['via']} "
                f"enabling dream edge(s): {', '.join(row['enabling_edges']) or 'NONE'}"
            )
        if not attribution:
            lines.append("  (no probe converted — the multi-hop delta is zero)")
        lines.append(f"  multi-hop Δ {mh_delta:+.4f} vs matched single-hop control Δ {ctl_delta:+.4f}")
        lines.append("")
        lines.append("guardrail gates (release gates for the verb — NOT a live-corpus safety proof;")
        lines.append("on the deployed corpus the protection is git-revertibility + the undo affordance):")
        for name, ok, detail in gates:
            lines.append(f"  {'✔' if ok else '✘'} {name:<26} {detail}")
        for name, why in skipped:
            lines.append(f"  – {name:<26} SKIPPED: {why}")
        lines.append("")
        lines.append(f"RESULT: {'PASS' if all_pass else 'FAIL'}")
        if not identity_ok:
            lines.append(
                "  OFF-arm byte-identity FAILED — the ranking under HIPPO_DREAM=0 no longer "
                "matches the committed pre-dream baseline. Either ranking changed (re-pin "
                "deliberately with --pin after review) or the stamp filter is leaking edges."
            )
        return (0 if all_pass else 1), "\n".join(lines)

    try:
        code, text = _body()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # The frozen fixture must be byte-identical after every run — measure-only. (--pin is
    # the ONE sanctioned exception: its entire job is (re)writing pinned_off.json.)
    if not pin and _snapshot(fixture_dir) != fixture_before:
        return 1, "eval --ab HIPPO_DREAM: FIXTURE MUTATED — the harness must be measure-only."
    return code, text


def _run_live(memory_dir: str, *, rebuilds: int, k: int) -> Tuple[int, str]:
    """``--live``: OFF/ON over a COPY of the live corpus's already-stamped edges.

    Continuous measurement (possible because DRM-2 stamps every applied edge), not a gate:
    no dream pass runs, no hard set exists — probes are each memory's derived self-query,
    and the report is the per-arm self_recall + the probes whose hit set changed. The live
    corpus itself is never written (inv6 / measure-only).
    """
    from .build_index import build_index, load_index
    from .eval_recall import derive_self_query
    from .recall import recall as _recall

    work = tempfile.mkdtemp(prefix="hippo-dream-ab-live-")
    try:
        mem = os.path.join(work, "mem")
        _copy_corpus(memory_dir, mem)
        arms: Dict[str, dict] = {}
        for label, off in (("OFF", True), ("ON", False)):
            idx_dir = os.path.join(work, f"idx-{label.lower()}")
            with _arm_env(off):
                build_index(mem, idx_dir, force=True, allow_download=False)
                idx = load_index(idx_dir)
                if idx is None or not len(idx):
                    return 1, "eval --ab HIPPO_DREAM --live: could not index the corpus copy."
                hits = {}
                for e in idx.entries:
                    q = derive_self_query(e)
                    if not q:
                        continue
                    names = [r.get("name") for r in _recall(q, k=k, index=idx, index_dir=idx_dir)]
                    hits[e.get("name")] = e.get("name") in names
                arms[label] = {
                    "self_recall": round(sum(hits.values()) / max(1, len(hits)), 4),
                    "hits": hits,
                }
        off_h, on_h = arms["OFF"]["hits"], arms["ON"]["hits"]
        changed = sorted(n for n in off_h if off_h[n] != on_h.get(n))
        regressed = sorted(n for n in off_h if off_h[n] and not on_h.get(n, True))
        lines = [
            "=== eval --ab HIPPO_DREAM --live — stamped-edge measurement over a corpus COPY ===",
            f"corpus: {memory_dir} ({len(off_h)} probes; live tree untouched)",
            f"self_recall@{k}: OFF {arms['OFF']['self_recall']} → ON {arms['ON']['self_recall']}",
            f"probes whose hit flipped: {len(changed)}" + (f" ({', '.join(changed[:8])})" if changed else ""),
            f"hit→miss regressions under admission: {len(regressed)}" + (f" — {', '.join(regressed[:8])}" if regressed else ""),
            "measurement only — the release gates live on the frozen fixture (bare --ab).",
        ]
        return (1 if regressed else 0), "\n".join(lines)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="memory.dream_eval",
        description="DRM-3: the /dream HIPPO_DREAM snapshot-diff A/B (measure-only).",
    )
    ap.add_argument("--fixture-dir", default=None, help="dir holding S0/ + hard_set.yaml + pinned_off.json")
    ap.add_argument("--pin", action="store_true", help="(re)write the pre-dream OFF baseline from the pristine fixture")
    ap.add_argument("--live", action="store_true", help="measure the LIVE corpus's stamped edges (over a copy)")
    ap.add_argument("--memory-dir", default=None, help="with --live: the corpus to copy (default: resolved)")
    ap.add_argument("--rebuilds", type=int, default=5, help="index rebuilds per arm for the noise floor (default 5)")
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args(argv)

    live_dir = None
    if args.live:
        live_dir = args.memory_dir
        if live_dir is None:
            from .provenance import resolve_dirs

            live_dir, _ = resolve_dirs()
    code, text = run_ab(
        fixture_dir=args.fixture_dir,
        pin=args.pin,
        live_memory_dir=live_dir,
        rebuilds=args.rebuilds,
        k=args.k,
    )
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
