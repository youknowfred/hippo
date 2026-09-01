"""ABS-1/ABS-2 — the abstention instruments must describe mechanisms that exist.

Split out of tests/test_eval_recall.py under the module-size ratchet (CONTRIBUTING.md
"Code layout"). Both pins guard SHIPPED TEXT that was wrong for the life of the feature,
in ways no functional test could catch — the code did what it said, but what it said about
itself was false:

  ABS-1  doctor claimed `.audit-fixtures/recall_abstention_set.yaml` was "written by
         /hippo:audit". No writer has ever existed; SIG-6's similarly-named flow drafts the
         opposite polarity into recall_hard_set.yaml. The check sat inert behind it.
  ABS-2  doctor sold "warm the dense model and enable the abstention floor". Warming ADDS
         two candidate lanes, so it can only make abstention rarer — the reverse of the
         claim. Pinned with a hermetic two-arm measurement.
"""

from __future__ import annotations

import glob
import os
import re

from memory import build_index as B
from memory import eval_recall as E

from .conftest import write_file


def test_no_shipped_surface_claims_the_abstention_set_is_generated():
    """ABS-1: nothing generates recall_abstention_set.yaml — no shipped text may say it does.

    The defect this pins shipped for a full release: doctor's docstring said the fixture was
    "written by /hippo:audit" and its remediation said to "run /hippo:audit to generate one",
    so the one check that measures off-topic leakage sat inert behind an unfollowable route.
    The cause is a name collision (SIG-6's abstention BACKLOG drafter writes hard-set rows
    tagged category:abstention — the opposite polarity), which makes the wrong sentence easy
    to rewrite by accident. So this sweeps the SHIPPED tree for the claim, not just the two
    strings that were wrong: any line naming the abstention set near a generation verb fails.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = glob.glob(os.path.join(root, "plugin", "memory", "*.py")) + glob.glob(
        os.path.join(root, "plugin", "skills", "*", "SKILL.md")
    )
    # "generate/write/draft/produce" within ~80 chars of the filename, either order. The window
    # must tolerate dots (the filename itself contains ".yaml" — an earlier [^.]* version of
    # this lint passed vacuously against the real defect for exactly that reason).
    verb = r"(generat\w*|writt?en|writes|drafts?|drafted|produces?)"
    name = r"recall_abstention_set"
    # the honest phrasings — these SAY there is no writer, and must not be flagged
    honest = r"no writer|nothing generates|never generated|not generated|hand-author|NO answer"
    offenders = []
    for path in targets:
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if name not in line or re.search(honest, line, re.I):
                    continue
                if re.search(rf"{verb}.{{0,80}}?{name}", line, re.I) or re.search(
                    rf"{name}.{{0,80}}?{verb}", line, re.I
                ):
                    offenders.append(f"{os.path.relpath(path, root)}:{n}: {line.strip()}")
    assert not offenders, "shipped text claims the abstention set is generated:\n" + "\n".join(
        offenders
    )


def test_warming_the_dense_model_can_only_reduce_abstention(tmp_path, monkeypatch):
    """ABS-2: adding the dense lanes can never make recall abstain MORE often.

    doctor told users to "warm the dense model and enable the abstention floor", and the
    floor-sanity check's bm25-only branch said "abstention is dense-gated (RET-11)". Both
    inverted the effect on the metric: recall abstains iff ALL FOUR rankings are empty, so
    turning dense ON only adds candidate lanes. The floor's real job is to stop the dense
    ranker admitting the whole corpus — not to produce abstentions.

    Isolated hermetically: the probe shares NO token with the corpus (both BM25 lanes empty),
    and the fake embedder returns one constant unit vector (every cosine 1.0, above any
    floor), so the dense lanes are the ONLY difference between the two arms.
    """
    import numpy as np

    memory_dir = str(tmp_path / "mem")
    os.makedirs(memory_dir)
    write_file(
        memory_dir,
        "puppy.md",
        "---\nname: canine-care\ndescription: \"puppy feeding walks vet visits\"\n"
        "metadata:\n  type: project\n---\n\nPuppies need feeding twice daily.\n",
    )
    probes = ["kitten grooming"]  # zero token overlap with description or body

    def _constant_embedder(texts, allow_download=True):
        v = np.zeros(8, dtype="float32")
        v[0] = 1.0
        return np.vstack([v for _ in texts]).astype("float32")

    rates = {}
    for label, disable in (("off", "1"), ("on", None)):
        idx = str(tmp_path / f"idx_{label}")
        if disable:
            monkeypatch.setenv("HIPPO_DISABLE_DENSE", disable)
        else:
            monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
            monkeypatch.setattr(B, "embed_documents", _constant_embedder)
            monkeypatch.setattr(
                "memory.recall_rank.embed_query",
                lambda q, allow_download=True: _constant_embedder([q])[0],
            )
        B.build_index(memory_dir, idx)
        index = B.load_index(idx)
        assert index.dense_ready is (disable is None)
        rates[label] = E.abstention_rate(index, probes, index_dir=idx, memory_dir=memory_dir)["rate"]

    assert rates["off"] == 1.0, "BM25-only must abstain — the probe shares no token"
    assert rates["on"] == 0.0, "the dense lanes admit it, so warming STRICTLY reduced abstention"
    assert rates["on"] <= rates["off"]


# --------------------------------------------------------------------------- #
# ABS-3: the gate binds only on the corpus/fixture pair it was calibrated for
# --------------------------------------------------------------------------- #
def test_project_local_predicate_matches_only_the_audit_fixtures_convention(tmp_path, monkeypatch):
    """ABS-3: only an auto-discovered .audit-fixtures/ file drops to report-only."""
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)

    engine = os.path.join(repo, "tests", "fixtures", "recall_abstention_set.yaml")
    project = os.path.join(md, ".audit-fixtures", "recall_abstention_set.yaml")
    for p in (engine, project):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- {query: q}\n")

    assert E.is_project_local_fixture(engine) is False
    assert E.is_project_local_fixture(project) is True
    assert E.is_project_local_fixture(None) is False
    # an EXPLICIT path outside the convention still gates (a caller asked for it)
    assert E.is_project_local_fixture(os.path.join(md, "tmp", "x.yaml")) is False


def _corpus_with_probe_fixture(tmp_path, monkeypatch, where):
    """Build a one-memory corpus whose off-topic probe LEAKS, with the fixture at `where`."""
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(
        md, "a.md",
        "---\nname: alpha\ndescription: \"alpha beta gamma\"\nmetadata:\n  type: project\n---\n\nbody\n",
    )
    idx = str(tmp_path / "idx")
    B.build_index(md, idx)
    fixture = os.path.join(repo, where, "recall_abstention_set.yaml")
    os.makedirs(os.path.dirname(fixture), exist_ok=True)
    with open(fixture, "w", encoding="utf-8") as fh:
        fh.write('- query: "alpha"\n')  # shares a token -> leaks -> rate 0.0
    return md, idx, fixture


def test_project_local_fixture_reports_instead_of_failing(tmp_path, monkeypatch):
    """ABS-3: a leaking project-local fixture is REPORTED, never a merge-blocking failure.

    The rate is a real measurement and stays in the report; what changes is that a corpus
    is no longer failed against a threshold measured on somebody else's 22-memory corpus.
    """
    md, idx, fixture = _corpus_with_probe_fixture(
        tmp_path, monkeypatch, os.path.join(".claude", "memory", ".audit-fixtures")
    )
    rep = E.evaluate(memory_dir=md, index_dir=idx, abstention_set_path=fixture)
    g = rep["gates"]["abstention_rate"]
    assert g["value"] == 0.0            # it really did leak
    assert g["pass"] is None            # ...and it does not bind
    assert g["skipped"] is True
    assert "calibrated against the shipped pack corpus" in g["reported_only"]
    assert rep["ok"] is not False or all(
        v.get("pass") is not False for k, v in rep["gates"].items() if k == "abstention_rate"
    )


def test_engine_fixture_still_fails_a_leaking_corpus(tmp_path, monkeypatch):
    """ABS-3 must not defang the regression tripwire on the pair it was calibrated for."""
    md, idx, fixture = _corpus_with_probe_fixture(
        tmp_path, monkeypatch, os.path.join("tests", "fixtures")
    )
    rep = E.evaluate(memory_dir=md, index_dir=idx, abstention_set_path=fixture)
    g = rep["gates"]["abstention_rate"]
    assert g["value"] == 0.0
    assert g["pass"] is False           # still binds — CI reseeds from packs and uses this path
    assert "reported_only" not in g
    assert rep["ok"] is False


# --------------------------------------------------------------------------- #
# ABS-4: the floor sweep must measure the population the floor actually gates
# --------------------------------------------------------------------------- #
_ABS4_DIM = 32
_ABS4_QUERY = "zeta eta theta iota kappa"  # body vocabulary only — absent from the description


def _abs4_index(tmp_path, monkeypatch, vec):
    """Build ABS-4's one-memory corpus (description and body vocabularies disjoint) with
    ``vec`` as the dense embedder for documents AND queries; return ``(index, n_desc)``."""
    import numpy as np

    # Explicit delenv: these tests NEED the dense lane, and the CI hermetic lane exports
    # HIPPO_DISABLE_DENSE=1 job-wide (CONTRIBUTING's airplane-mode path) — declare the lane
    # rather than inherit the ambient one. (It was ALSO papering over a real leak: the
    # concurrency test left the var set for everything that ran after it. That is fixed at
    # the source and held by the conftest guard now; this line stands on its own reason.)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)

    memory_dir = str(tmp_path / "mem")
    os.makedirs(memory_dir)
    write_file(
        memory_dir,
        "a.md",
        "---\nname: alpha\ndescription: \"alpha beta gamma\"\nmetadata:\n  type: project\n---\n\n"
        + ("zeta eta theta iota kappa lambda mu nu xi omicron. " * 40),
    )
    monkeypatch.setattr(
        B, "embed_documents", lambda texts, allow_download=True: np.vstack([vec(t) for t in texts])
    )
    monkeypatch.setattr("memory.recall.embed_query", lambda q, allow_download=True: vec(q))

    idx = str(tmp_path / "idx")
    B.build_index(memory_dir, idx)
    index = B.load_index(idx)
    assert index.dense_ready
    n_desc = len(index.entries)
    assert index.dense.shape[0] > n_desc, "the corpus must produce body-chunk rows to test"
    return index, n_desc


def test_floor_sweep_scores_body_chunks_not_just_descriptions(tmp_path, monkeypatch):
    """ABS-4: _raw_max_cosines must cover the WIDENED matrix, as _dense_rank_rows does.

    It used to score sims[:n_desc] while its docstring claimed to compute "the exact
    quantity the dense floor gates" — true when written, false since RET-2 widened the
    matrix with body-chunk rows. Calibrating on a narrower population than the floor
    governs made the sweep optimistic about off-topic leakage, and a body chunk is exactly
    where an adjacent-technical query finds its best match. Here the query's tokens appear
    ONLY in a body, so a description-only sweep scores it at zero and the corrected one
    sees the real match.

    Fixture note (2026-09-01): the embedder gives each distinct token its OWN dimension.
    The first cut bucketed with ``hash(tok) % dim``, and builtin str hash is SALTED per
    process (PYTHONHASHSEED) — one CI draw mapped zeta/eta/iota into alpha/beta/gamma's
    buckets, scoring the zero-overlap description row 3/sqrt(15) ≈ 0.774597, ABOVE the
    genuine body match, and the bare ``full > desc_only`` failed with both sides the same
    row. Reconstructed deterministically in
    test_floor_sweep_fixture_survives_the_2026_09_01_bucket_collision below; same lesson
    as RET-1's crc32 note on tests/test_recall.py's _fake_embedder.
    """
    import numpy as np

    vocab: dict = {}

    def _vec(text):
        v = np.zeros(_ABS4_DIM, dtype="float32")
        for tok in B.tokenize(text):
            v[vocab.setdefault(tok, len(vocab))] += 1.0  # per-token dim: unsalted, collision-free
        n = np.linalg.norm(v)
        return v / n if n else v

    index, n_desc = _abs4_index(tmp_path, monkeypatch, _vec)

    qvec = _vec(_ABS4_QUERY)
    sims = index.dense @ qvec
    # round(..., 6) mirrors _raw_max_cosines' own output rounding, so `got == [full]` below
    # compares like for like; the separation asserts never lean on the 6th decimal.
    desc_only = round(float(sims[:n_desc].max()), 6)
    full = round(float(sims.max()), 6)
    assert desc_only == 0.0, "disjoint vocabularies must score exactly orthogonal"
    assert full >= desc_only + 0.25, "fixture must separate the populations by a real margin"

    got = E._raw_max_cosines(index, [_ABS4_QUERY])
    assert got == [full], "the sweep must score every row the floor gates, not just descriptions"
    assert got != [desc_only]


_CI_33534635029_BUCKETS = {
    # hash(tok) % 32 for the fixture vocabulary as CPython drew it under PYTHONHASHSEED=1161
    # — recovered by a seed scan (the one failing seed in 0..1999, so ≈5e-4 per run) that
    # reproduces the CI values exactly. zeta, eta and iota land in alpha, beta and gamma's
    # buckets: the query's support covers the description's entire support.
    "alpha": 31, "beta": 16, "gamma": 22,
    "zeta": 31, "eta": 16, "theta": 10, "iota": 22, "kappa": 29,
    "lambda": 12, "mu": 11, "nu": 23, "xi": 7, "omicron": 25,
}


def test_floor_sweep_fixture_survives_the_2026_09_01_bucket_collision(tmp_path, monkeypatch):
    """ABS-4 fixture pin: the hash-bucket draw that failed CI, reconstructed deterministically.

    2026-09-01, PR #117, run 33534635029 (macos-latest py3.12 hermetic lane): the separation
    assert failed as ``assert 0.774597 > 0.774597``. Not a rounding coincidence and not a
    product bug — the fixture's original ``hash(tok) % dim`` embedder rode the per-process
    str-hash salt, and that run's draw mapped the query's buckets over every description
    bucket. The description row — tokens exactly {alpha, beta, gamma}, because the entry
    name is ``a.md``'s 1-char stem, which tokenize drops — scored 3/sqrt(15) = 0.7745966
    against a query sharing ZERO tokens with it, beating the genuine body-chunk match
    (~0.7166). ``full``'s argmax was therefore the description row itself, so the strict
    ``>`` could never hold. Every lane runs this fixture and any of them could have drawn
    it; macos-py3.12-hermetic just did.

    Pinning the mechanism, not folklore: the historical construction is rebuilt here under
    the recovered bucket table (a plain dict — deterministic on every seed, interpreter and
    platform) and must reproduce the exact CI numbers. The shipped fixture above holds its
    margin on every seed because no bucket table applies to it at all.
    """
    import numpy as np

    def _hash_bucket_vec(text):
        v = np.zeros(_ABS4_DIM, dtype="float32")
        for tok in B.tokenize(text):
            v[_CI_33534635029_BUCKETS[tok]] += 1.0  # KeyError ⇒ corpus drifted; re-derive table
        n = np.linalg.norm(v)
        return v / n if n else v

    index, n_desc = _abs4_index(tmp_path, monkeypatch, _hash_bucket_vec)
    sims = index.dense @ _hash_bucket_vec(_ABS4_QUERY)
    desc_only = round(float(sims[:n_desc].max()), 6)
    full = round(float(sims.max()), 6)
    assert desc_only == full == 0.774597, "must reproduce run 33534635029's exact values"
    assert float(sims[:n_desc].max()) == float(sims.max()), "the description row IS the argmax"


def test_project_local_relevance_set_reports_instead_of_failing(tmp_path, monkeypatch):
    """ABS-3/ABS-5: precision@10 gets the same scoping as its twin.

    The shipped relevance set names starter-pack memories: 12 of its 13 stems do not exist
    in hippo's own corpus, so precision@10 measured 0.0125 against a 0.12 threshold — the
    metric was scoring a corpus on retrieving memories it does not contain. With a
    corpus-local set present the number is REPORTED (and becomes meaningful) rather than
    binding against a pack-calibrated threshold.
    """
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(
        md, "a.md",
        "---\nname: alpha\ndescription: \"alpha beta gamma\"\nmetadata:\n  type: project\n---\n\nbody\n",
    )
    idx = str(tmp_path / "idx")
    B.build_index(md, idx)
    fixture = os.path.join(md, ".audit-fixtures", "recall_relevance_set.yaml")
    os.makedirs(os.path.dirname(fixture), exist_ok=True)
    with open(fixture, "w", encoding="utf-8") as fh:
        fh.write('- query: "alpha"\n  relevant: [alpha]\n')

    rep = E.evaluate(memory_dir=md, index_dir=idx, relevance_set_path=fixture)
    g = rep["gates"]["precision@10"]
    assert g["pass"] is None and g["skipped"] is True
    assert "project-local relevance set" in g["reported_only"]
