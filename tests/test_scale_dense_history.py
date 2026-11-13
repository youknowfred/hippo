"""OBS-8's trailing-nights store, without the model (hermetic): the rule the nightly
dense-under-load lane (tests/test_scale_dense_load.py) applies to its p95."""

from __future__ import annotations

import json

from .test_scale_dense_load import _history_check


def test_history_records_then_gates(tmp_path):
    """The trailing store's rule, without the model: seven nights only record; the eighth is
    gated at 1.25x their median, and a regressing night is not kept as the new normal."""
    path = str(tmp_path / "h" / "history.json")
    for v in (1000, 1100, 900, 1000, 1050, 950, 1000):
        verdict, _n = _history_check(path, v)
        assert verdict is None
    verdict, n = _history_check(path, 1240)
    assert n == 7 and verdict == (True, 1000.0)
    verdict, _n = _history_check(path, 1300)
    assert verdict[0] is False
    with open(path, encoding="utf-8") as fh:
        assert [x["p95_ms"] for x in json.load(fh)["nights"]][-1] == 1240
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("not json")
    assert _history_check(path, 10)[0] is None  # a torn store restarts the record, never crashes
