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
