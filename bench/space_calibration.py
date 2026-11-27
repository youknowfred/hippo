"""HCR-9 bench: one embedding space's floor, gate, count rule and band, measured.

    OPENBLAS_NUM_THREADS=1 PYTHONPATH=plugin:. python -m bench.space_calibration \\
        --space Qwen/Qwen3-Embedding-4B --rows whole --work <scratch dir> \\
        --live hippo=<corpus>/.claude/memory [--live LABEL=DIR ...] \\
        [--heldout <private questions.jsonl> --gold <private gold.json>] \\
        [--replay hippo=<project>/.hippo/telemetry] \\
        --floors 0.38,0.42 --gates table,off,0.60/0.60 --counts table,off --bands table \\
        --out <private.json>

HCR-3's method (RET-1's recipe for the floor, HOT-2's for the gate, HOT-8's for the count
rule, HOT-10's band check), kept as a tool so a space whose rows change can be re-run. One
process calibrates ONE space: ``lean`` (the fastembed model) or a high space id. ``--rows``
says what an entry's dense row embeds while it runs (``build_index_dense.WHOLE_ROW_MODELS``
is swapped for this process only; nothing shipped changes).

Corpora: the two public ones (the QUA-6 golden corpus and the starter packs CI seeds), plus
any ``--live`` corpus, copied once into ``--work`` and indexed there. The copies persist, so
a later run reuses every embedded row and only pays the model load. A live corpus brings its
own ``.audit-fixtures`` hard set and off-topic probes when it has them; ``--heldout`` adds
HCR-8's held-out questions for the corpora whose label they carry. Every corpus is also
asked HCR-3's 24 extra off-topic probes (12 generic, 12 adjacent technical). ``--replay``
replays the newest distinct human prompts of a project's episode log through the hook, for
injection volume: how many prompts get a block, and how many rows.

Each cell of ``floors x gates x counts x bands`` is measured two ways on every corpus:
explicit mode (``recall()``: hard-set hits, MRR, probes abstained) and hook mode (the real
``recall_hook.run_recall`` body, a fresh session per prompt: hits on rendered rows, rows
served, rows served on probes that leaked). ``table`` means the committed entry.

The output holds counts and cosines, no query text and no memory name; it is still written
outside the repository, because the live corpora and the replayed prompts are private.
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import shutil
import sys
import time
import uuid
from contextlib import ExitStack

_T0 = time.monotonic()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = 10


def _log(msg: str) -> None:
    print(f"[{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


def qs(xs):
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    pick = lambda p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 4)  # noqa: E731
    return {"n": len(xs), "min": round(xs[0], 4), "p10": pick(.1), "p25": pick(.25), "p50": pick(.5),
            "p75": pick(.75), "p90": pick(.9), "max": round(xs[-1], 4)}


def _pairs(spec: str):
    return [tuple(float(x) for x in cell.split("/")) if cell not in ("table", "off") else cell
            for cell in spec.split(",") if cell]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="'lean', or a high space id")
    ap.add_argument("--rows", choices=("whole", "description"), required=True)
    ap.add_argument("--work", required=True, help="scratch dir: corpus copies and their indexes persist here")
    ap.add_argument("--live", action="append", default=[], metavar="LABEL=MEMORY_DIR")
    ap.add_argument("--no-public", action="store_true", help="skip the golden and starter-pack corpora")
    ap.add_argument("--heldout", default=None, help="private JSONL: corpus, stem, query (HCR-8's set)")
    ap.add_argument("--gold", default=None, help="private JSON: 'corpus::stem' -> other correct memories")
    ap.add_argument("--heldout-labels", default="", help="map a questions label to a --live label, a=b,c=d")
    ap.add_argument("--replay", action="append", default=[], metavar="LABEL=TELEMETRY_DIR")
    ap.add_argument("--replay-n", type=int, default=300)
    ap.add_argument("--probes", default=os.path.join(_ROOT, "planning", "HCR-3.evidence.2026-11-25.json"))
    ap.add_argument("--mlx-cache", default=None)
    ap.add_argument("--fastembed-cache", default=None)
    ap.add_argument("--floors", default="table")
    ap.add_argument("--gates", default="table")
    ap.add_argument("--counts", default="table")
    ap.add_argument("--bands", default="table")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--no-hook", action="store_true", help="explicit mode only (a floor sweep)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    lean_arm = args.space == "lean"
    work = os.path.abspath(args.work)
    pd = os.path.join(work, "plugin-data")
    os.makedirs(pd, exist_ok=True)
    os.environ.update({
        "CLAUDE_PLUGIN_DATA": pd, "HIPPO_USER_MEMORY_DIR": os.path.join(work, "no-user-tier"),
        "HIPPO_LOCAL_MEMORY_DIR": os.path.join(work, "no-private-tier"),
        "HIPPO_TRUST_ALL": "1", "HIPPO_TRUST_FILE": os.path.join(work, "trust.json"), "HF_HUB_OFFLINE": "1",
        "HIPPO_DENSE_TIMEOUT": "120",
    })
    if args.fastembed_cache:
        os.environ["FASTEMBED_CACHE_PATH"] = os.path.abspath(os.path.expanduser(args.fastembed_cache))
    for v in ("HIPPO_DENSE_FLOOR", "HIPPO_DENSE_FLOOR_BAND", "HIPPO_SIGNAL_COUNT", "HIPPO_COUNT_BAND",
              "HIPPO_COUNT_WIDE", "HIPPO_DISABLE", "HIPPO_DISABLE_DENSE", "HIPPO_RETRIEVAL_PROFILE",
              "HIPPO_HIGH_EMBED_MODEL", "HIPPO_HIGH_EMBED_DIM", "HIPPO_GRAPH_SEEDS", "HIPPO_RERANK"):
        os.environ.pop(v, None)

    from memory import build_index_dense as BD
    from memory import embed_mlx as E
    from memory import eval_floor as F
    from memory import recall as R
    from memory import recall_abstain as A
    from memory import recall_count as C
    from memory import recall_hook as H
    from memory import recall_rank as RR
    from memory.build_index import DEFAULT_MODEL, build_index, default_index_dir, load_index
    from memory.eval_metrics import _DEFAULT_CATEGORY, load_abstention_set, load_hard_set, resolvable_row
    from memory.lanes import rendered_names
    from memory.recall_tiers import INDEX_LOADER

    space = DEFAULT_MODEL if lean_arm else args.space
    model = space.split("@", 1)[0]
    BD.WHOLE_ROW_MODELS = (frozenset(BD.WHOLE_ROW_MODELS | {model}) if args.rows == "whole"
                           else frozenset(BD.WHOLE_ROW_MODELS - {model}))
    if not lean_arm:
        if args.mlx_cache:
            mlx_cache = os.path.abspath(os.path.expanduser(args.mlx_cache))
            E.cache_dir = lambda: mlx_cache
        why = E.unavailable_reason(E.parse_space(space)[0])
        if why:
            print(f"{space} is not servable here: {why}", file=sys.stderr)
            return 1

    # one embed per (space, query): the grid re-asks the same queries
    orig_eq, qcache = RR.embed_query, {}

    def cached_eq(text, allow_download=False):
        key = (E.active_space() or DEFAULT_MODEL, text)
        if key not in qcache:
            qcache[key] = orig_eq(text, allow_download=allow_download)
        return qcache[key]

    RR.embed_query = R.embed_query = cached_eq

    # ---- corpora: copied once, indexed in place ------------------------------------ #
    tag = f"{'lean' if lean_arm else model.rsplit('/', 1)[-1]}-{args.rows}"

    def copy_of(label: str, fill):
        root = os.path.join(work, tag, label)
        md = os.path.join(root, ".claude", "memory")
        if not os.path.isdir(md):
            os.makedirs(os.path.dirname(md), exist_ok=True)
            fill(md)
            os.system(f"git init -q '{root}'")
        return md

    def fill_golden(md):
        os.makedirs(md)
        for p in glob.glob(os.path.join(_ROOT, "tests", "golden_corpus", "memory", "*.md")):
            shutil.copy(p, md)

    def fill_packs(md):
        os.makedirs(md)
        for p in glob.glob(os.path.join(_ROOT, "plugin", "assets", "**", "*.md"), recursive=True):
            if os.path.basename(p) not in ("MEMORY.skeleton.md", "README.md", "CONVENTIONS.md"):
                shutil.copy(p, md)
        shutil.copy(os.path.join(_ROOT, "plugin", "assets", "MEMORY.skeleton.md"), os.path.join(md, "MEMORY.md"))

    corpora = {}  # label -> (memory dir, hard-set path or None, probe path or None)
    if not args.no_public:
        g = os.path.join(_ROOT, "tests", "golden_corpus")
        corpora["golden"] = (copy_of("golden", fill_golden), os.path.join(g, "hard_set.yaml"),
                             os.path.join(g, "abstention_set.yaml"))
        f = os.path.join(_ROOT, "tests", "fixtures")
        corpora["packs"] = (copy_of("packs", fill_packs), os.path.join(f, "recall_hard_set.yaml"),
                            os.path.join(f, "recall_abstention_set.yaml"))
    for spec in args.live:
        label, src = spec.split("=", 1)
        md = copy_of(label, lambda dst, _s=os.path.abspath(src): shutil.copytree(_s, dst, symlinks=False))
        fx = os.path.join(md, ".audit-fixtures")
        hard, probes = os.path.join(fx, "recall_hard_set.yaml"), os.path.join(fx, "recall_abstention_set.yaml")
        corpora[label] = (md, hard if os.path.exists(hard) else None, probes if os.path.exists(probes) else None)

    extra = json.load(open(args.probes, encoding="utf-8"))["extra_off_topic_probes"]
    relabel = dict(p.split("=", 1) for p in args.heldout_labels.split(",") if "=" in p)
    held: dict = {}
    if args.heldout:
        gold = json.load(open(args.gold, encoding="utf-8")) if args.gold else {}
        for line in open(args.heldout, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("skip") or not r.get("query"):
                continue
            more = gold.get(f"{r['corpus']}::{r['stem']}", [])
            held.setdefault(relabel.get(r["corpus"], r["corpus"]), []).append(
                {"query": r["query"], "expected": [r["stem"], *more], "category": "held-out"})

    class Arm:
        def __init__(self, md):
            self.md, self.lean_dir = md, default_index_dir(md)
            self.idir = self.lean_dir if lean_arm else os.path.join(self.lean_dir, "high")
            self.index = load_index(self.idir)
            assert self.index is not None and self.index.dense_ready and self.index.model == space, md
            assert (self.index.manifest.get("row_text") or "description") == args.rows, md

        def ctx(self):
            st = ExitStack()
            if not lean_arm:
                st.enter_context(E.using_space(space))
            return st

        def explicit(self, q):
            with self.ctx():
                return R.recall(q, k=K, index=self.index, index_dir=self.lean_dir, memory_dir=self.md)

        def hook(self, q):
            """The hook body, a fresh session per prompt: (walked rows, rendered names)."""
            cap: dict = {}
            orig = R.format_results

            def fmt(results, *a, **kw):
                cap["results"] = list(results)
                cap["out"] = orig(results, *a, **kw)
                return cap["out"]

            R.format_results = fmt
            tok = INDEX_LOADER.set(lambda d: self.index if d == self.lean_dir else load_index(d))
            try:
                with self.ctx():
                    H.run_recall(q, session_id="rig-" + uuid.uuid4().hex, k=K, memory_dir=self.md,
                                 index_dir=self.lean_dir, repo_root=os.path.dirname(os.path.dirname(self.md)),
                                 hook=True, emit=lambda s: None)
            finally:
                INDEX_LOADER.reset(tok)
                R.format_results = orig
            rows = [r for r in cap.get("results", []) if r.get("corpus") != R._RULES_SOURCE]
            return rows, rendered_names(cap.get("out") or "")

    arms: dict = {}
    doc: dict = {"space": space, "rows": args.rows, "k": K, "corpora": {},
                 "mmr_lambda": os.environ.get("HIPPO_MMR_LAMBDA") or "default",
                 "mmr_mode": os.environ.get("HIPPO_MMR") or "default",
                 "extra_probes": {k_: len(v) for k_, v in extra.items() if isinstance(v, list)}}
    for label, (md, hard, probes) in corpora.items():
        idir = default_index_dir(md)
        t0 = time.monotonic()
        build_index(md, idir, allow_download=True)  # HF_HUB_OFFLINE=1: unbounded, never online
        if not lean_arm:
            for _ in range(8):
                with E.using_space(space):
                    build_index(md, os.path.join(idir, "high"), allow_download=True)
                hi = load_index(os.path.join(idir, "high"))
                if hi is not None and hi.dense_ready:
                    break
        _log(f"{label}: indexed in {time.monotonic() - t0:.0f} s")
        if args.build_only:
            continue
        arm = Arm(md)
        names = {e.get("name") for e in arm.index.entries}
        rows = [r for r in (load_hard_set(hard) if hard else [])
                if (r.get("category") or _DEFAULT_CATEGORY) != "abstention" and resolvable_row(names, r)]
        rows += [r for r in held.get(label, []) if r["expected"][0] in names]
        pr = {"fixture": load_abstention_set(probes) if probes else [], "generic": extra["generic"],
              "adjacent": extra["adjacent"]}
        arms[label] = (arm, rows, pr)
        with arm.ctx():
            tops, gaps = F._top_and_gap(arm.index, rows)
            off = {kind: F._raw_max_cosines(arm.index, v) for kind, v in pr.items()}
        cats: dict = {}
        for r in rows:
            cats[r.get("category") or _DEFAULT_CATEGORY] = cats.get(r.get("category") or _DEFAULT_CATEGORY, 0) + 1
        doc["corpora"][label] = {
            "memories": len(arm.index.entries), "chunks": len(arm.index.body_chunks or []),
            "on_topic_rows": len(rows), "on_topic_by_category": cats,
            "probes": {kind: len(v) for kind, v in pr.items()},
            "on_topic_best_cosine": qs(tops), "on_topic_best_sorted": sorted(round(t, 4) for t in tops),
            "expected_gap_under_best": qs(gaps), "gaps_sorted": sorted(round(g, 4) for g in gaps),
            "off_topic_best_cosine": {kind: qs(v) for kind, v in off.items()},
            "off_topic_best_sorted": {kind: sorted(round(t, 4) for t in v) for kind, v in off.items()},
            "recommend_floor": F.recommend_floor(tops, [t for v in off.values() for t in v]),
        }
    if args.build_only:
        _log("built; nothing measured")
        return 0
    pooled_on = [t for c in doc["corpora"].values() for t in c["on_topic_best_sorted"]]
    pooled_off = {kind: [t for c in doc["corpora"].values() for t in c["off_topic_best_sorted"][kind]]
                  for kind in ("fixture", "generic", "adjacent")}
    doc["pooled"] = {
        "on_topic_best_cosine": qs(pooled_on), "off_topic_best_cosine": {k_: qs(v) for k_, v in pooled_off.items()},
        "expected_gap_under_best": qs([g for c in doc["corpora"].values() for g in c["gaps_sorted"]]),
    }

    replays = {}
    if not args.no_hook:
        from memory.lint_floor import recent_prompts

        for spec in args.replay:
            label, td = spec.split("=", 1)
            prompts, _ = recent_prompts(corpora[label][0], td, args.replay_n)
            replays[label] = prompts
            doc["corpora"][label]["replayed_prompts"] = len(prompts)

    # ---- the cells ------------------------------------------------------------------ #
    table = {"floor": RR._DENSE_FLOOR_BY_MODEL.get(space), "gate": A._COSINE_BY_MODEL.get(space),
             "count": C._COUNT_BY_MODEL.get(space), "band": RR._DENSE_FLOOR_BAND_DEFAULT}
    doc["table"] = table

    def configure(floor, gate, count, band) -> str:
        if floor == "table":
            RR._DENSE_FLOOR_BY_MODEL.pop(space, None) if table["floor"] is None else \
                RR._DENSE_FLOOR_BY_MODEL.__setitem__(space, table["floor"])
        else:
            RR._DENSE_FLOOR_BY_MODEL[space] = floor
        want_gate = table["gate"] if gate == "table" else (None if gate == "off" else
                                                            {"strong": gate[0], "support": gate[1]})
        A._COSINE_BY_MODEL.pop(space, None) if want_gate is None else A._COSINE_BY_MODEL.__setitem__(space, want_gate)
        want_count = table["count"] if count == "table" else (None if count == "off" else
                                                              {"band": count[0], "wide": count[1]})
        C._COUNT_BY_MODEL.pop(space, None) if want_count is None else C._COUNT_BY_MODEL.__setitem__(space, want_count)
        if band == "table":
            os.environ.pop("HIPPO_DENSE_FLOOR_BAND", None)
        else:
            os.environ["HIPPO_DENSE_FLOOR_BAND"] = str(band)
        f_ = RR._dense_floor(space)
        g_ = A._COSINE_BY_MODEL.get(space)
        c_ = C._COUNT_BY_MODEL.get(space)
        return (f"floor={f_}|gate={'off' if g_ is None else str(g_['strong']) + '/' + str(g_['support'])}"
                f"|count={'off' if c_ is None else str(c_['band']) + '/' + str(c_['wide'])}"
                f"|band={RR._dense_floor_band()}")

    def explicit_eval(arm, rows, pr):
        hits, rr_, by_cat = [], 0.0, {}
        for i, row in enumerate(rows):
            got = [h["name"] for h in arm.explicit(row["query"]) if h.get("corpus") != R._RULES_SOURCE]
            pos = [got.index(e) for e in row["expected"] if e in got]
            cat = row.get("category") or _DEFAULT_CATEGORY
            by_cat.setdefault(cat, [0, 0])[1] += 1
            if pos:
                hits.append(i)
                rr_ += 1.0 / (min(pos) + 1)
                by_cat[cat][0] += 1
        ab = {kind: sum(1 for q in v if not arm.explicit(q)) for kind, v in pr.items()}
        return {"hits": hits, "n": len(rows), "mrr": round(rr_ / max(1, len(rows)), 4),
                "by_category": {c_: f"{h}/{n}" for c_, (h, n) in by_cat.items()},
                "abstained": {kind: f"{ab[kind]}/{len(v)}" for kind, v in pr.items()}}

    def hook_eval(arm, rows, pr):
        """A hit = the expected memory is on a rendered pointer line."""
        hits, rr_, served, inj = [], 0.0, [], 0
        for i, row in enumerate(rows):
            res, shown = arm.hook(row["query"])
            live = [r for r in res if not (r.get("floor_collapsed") or r.get("cooldown_collapsed"))]
            order = [r["name"] for r in res if r["name"] in shown]
            pos = [order.index(e) for e in row["expected"] if e in order]
            if pos:
                hits.append(i)
                rr_ += 1.0 / (min(pos) + 1)
            if live:
                inj += 1
                served.append(len([r for r in live if r["name"] in shown]))
        ab, leaked = {}, []
        for kind, v in pr.items():
            z = 0
            for q in v:
                res, shown = arm.hook(q)
                if not res:
                    z += 1
                else:
                    leaked.append(len([r for r in res if r["name"] in shown
                                       and not (r.get("floor_collapsed") or r.get("cooldown_collapsed"))]))
            ab[kind] = f"{z}/{len(v)}"
        srt = sorted(served)
        return {"hits": hits, "n": len(rows), "mrr": round(rr_ / max(1, len(rows)), 4), "abstained": ab,
                "injecting_prompts": inj,
                "served_rows": {"median": srt[len(srt) // 2] if srt else None,
                                "mean": round(sum(srt) / max(1, len(srt)), 2)},
                "leaked_probes": len(leaked), "leaked_probe_rows": sum(leaked)}

    def replay_eval(arm, prompts):
        served = []
        for q in prompts:
            res, shown = arm.hook(q)
            live = [r for r in res if not (r.get("floor_collapsed") or r.get("cooldown_collapsed"))
                    and r["name"] in shown]
            if live:
                served.append(len(live))
        srt = sorted(served)
        return {"prompts": len(prompts), "injecting": len(srt), "rows_total": sum(srt),
                "rows_median": srt[len(srt) // 2] if srt else None,
                "rows_mean": round(sum(srt) / max(1, len(srt)), 2),
                "hist": {str(n): srt.count(n) for n in sorted(set(srt))}}

    doc["cells"] = {}
    grid = itertools.product(_pairs_or_floats(args.floors), _pairs(args.gates), _pairs(args.counts),
                             _pairs_or_floats(args.bands))
    for floor, gate, count, band in grid:
        key = configure(floor, gate, count, band)
        if key in doc["cells"]:
            continue
        cell: dict = {}
        for label, (arm, rows, pr) in arms.items():
            rec = {"explicit": explicit_eval(arm, rows, pr)}
            if not args.no_hook:
                rec["hook"] = hook_eval(arm, rows, pr)
                if label in replays:
                    rec["replay"] = replay_eval(arm, replays[label])
            cell[label] = rec
        doc["cells"][key] = cell
        _log(key + "  " + "  ".join(
            f"{label} {len(rec['explicit']['hits'])}/{rec['explicit']['n']} "
            f"ab {'/'.join(v.split('/')[0] for v in rec['explicit']['abstained'].values())}"
            + (f" hook {len(rec['hook']['hits'])} leak {rec['hook']['leaked_probe_rows']}" if "hook" in rec else "")
            + (f" replay {rec['replay']['injecting']}@{rec['replay']['rows_mean']}" if "replay" in rec else "")
            for label, rec in cell.items()))
    configure("table", "table", "table", "table")
    doc["seconds"] = round(time.monotonic() - _T0)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    _log(f"wrote {args.out}")
    return 0


def _pairs_or_floats(spec: str):
    return [cell if cell == "table" else float(cell) for cell in spec.split(",") if cell]


if __name__ == "__main__":
    raise SystemExit(main())
