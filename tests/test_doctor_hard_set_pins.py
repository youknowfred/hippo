"""OBS-5: doctor counts the hard-set pins that no longer resolve.

A row of ``.audit-fixtures/recall_hard_set.yaml`` pins the memories that should answer its
query. When a pinned memory is renamed, archived, folded into a digest or deleted, the row
can only score a miss and the eval reads it as a recall regression. Doctor counts those
rows (read-only, no recall run) and is silent when there is no hard set or nothing drifted.
Here rather than in ``test_doctor.py``, which is at its size pin.
"""

from __future__ import annotations

import json
import os

from memory import doctor as D
from memory import doctor_checks_pins as P

from .conftest import write_file
from .test_doctor import _ctx, _mem, _seed


def _hard_set(memory_dir, rows):
    lines = []
    for q, expected in rows:
        lines.append(f'- query: "{q}"\n  expected: [{", ".join(expected)}]')
    write_file(memory_dir, ".audit-fixtures/recall_hard_set.yaml", "\n".join(lines) + "\n")


def _corpus(memory_dir, names):
    _seed(memory_dir)
    for n in names:
        write_file(memory_dir, f"{n}.md", _mem(n, f"note {n}"))


def test_no_hard_set_prints_no_line(repo, memory_dir):
    _corpus(memory_dir, ["alpha"])
    assert P.check_hard_set_pins(_ctx(memory_dir, repo)) is None


def test_every_pin_resolving_prints_no_line(repo, memory_dir):
    _corpus(memory_dir, ["alpha", "beta"])
    _hard_set(memory_dir, [("first query", ["alpha"]), ("second query", ["beta", "alpha"])])
    assert P.check_hard_set_pins(_ctx(memory_dir, repo)) is None


def test_drifted_pins_are_counted_with_the_remedy(repo, memory_dir):
    _corpus(memory_dir, ["alpha", "beta"])
    write_file(memory_dir, "archive/gamma.md", _mem("gamma", "archived"))  # archived: gone
    _hard_set(memory_dir, [
        ("one", ["alpha"]),
        ("two", ["gamma"]),            # archived
        ("three", ["renamed_away"]),   # renamed or deleted
        ("four", ["beta", "folded"]),  # partly drifted: still scores, pin stale
    ])
    r = P.check_hard_set_pins(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    msg = r["message"]
    assert "3 of 4" in msg and "2 resolve to nothing" in msg
    assert "recall_hard_set.yaml" in msg and "hippo eval --draft-update" in msg
    assert "\n" not in msg
    assert "gamma" not in msg and "renamed_away" not in msg  # counts, never names


def test_a_pin_to_an_outbox_memory_resolves(repo, memory_dir):
    from memory import outbox as OB

    _corpus(memory_dir, ["alpha"])
    write_file(OB.outbox_dir(memory_dir), "unshipped.md", _mem("unshipped", "not shipped yet"))
    _hard_set(memory_dir, [("one", ["unshipped"])])
    assert P.check_hard_set_pins(_ctx(memory_dir, repo)) is None


def test_a_pin_to_a_digest_section_slug_resolves(repo, memory_dir):
    from memory import build_index as B

    _corpus(memory_dir, ["alpha"])
    idx = B.default_index_dir(memory_dir)
    os.makedirs(idx, exist_ok=True)
    with open(os.path.join(idx, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"sections": {"slugs": {"a-section": "digest.md"}}}, fh)
    _hard_set(memory_dir, [("one", ["a-section"])])
    assert P.check_hard_set_pins(_ctx(memory_dir, repo)) is None


def _baseline(memory_dir, recall):
    write_file(memory_dir, ".audit-fixtures/recall_eval_baseline.json",
               json.dumps({"schema": 1, "metrics": {"gates": {"hard_recall@10": recall}}}))


def test_a_baseline_the_fixtures_can_no_longer_reach_is_named(repo, memory_dir):
    _corpus(memory_dir, ["alpha"])
    _hard_set(memory_dir, [("one", ["alpha"]), ("two", ["gone_a"]), ("three", ["gone_b"]), ("four", ["gone_c"])])
    _baseline(memory_dir, 0.5)  # the fixtures now support at most 1/4
    msg = P.check_hard_set_pins(_ctx(memory_dir, repo))["message"]
    assert "0.5" in msg and "0.25" in msg and "recall_eval_baseline.json" in msg


def test_a_baseline_within_reach_adds_nothing(repo, memory_dir):
    _corpus(memory_dir, ["alpha"])
    _hard_set(memory_dir, [("one", ["alpha"]), ("two", ["gone_a"])])
    _baseline(memory_dir, 0.5)
    msg = P.check_hard_set_pins(_ctx(memory_dir, repo))["message"]
    assert "recall_eval_baseline.json" not in msg


def test_an_unreadable_hard_set_prints_no_line(repo, memory_dir):
    _corpus(memory_dir, ["alpha"])
    write_file(memory_dir, ".audit-fixtures/recall_hard_set.yaml", "- query: [unclosed\n")
    assert P.check_hard_set_pins(_ctx(memory_dir, repo)) is None


def test_registered_in_doctor():
    assert ("hard_set_pins", P.check_hard_set_pins) in D.CHECKS
