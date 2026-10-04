"""CON-4: ROADMAP.v2.yaml is the one live ledger, in step with the ratified design record."""

from __future__ import annotations

import os
import re

import yaml

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(name):
    with open(os.path.join(_ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _design_ids():
    md = _read("ROADMAP.v2.md")
    sec5 = md[md.index("## 5. Workstreams and items"):md.index("## 6. The release train")]
    return re.findall(r"^- \*\*([A-Z]{3}-\d+) ", sec5, re.M)


def test_every_design_item_has_exactly_one_ledger_row():
    ledger = yaml.safe_load(_read("ROADMAP.v2.yaml"))
    ids = [row["id"] for row in ledger["items"]]
    assert sorted(ids) == sorted(_design_ids())
    assert len(ids) == len(set(ids))


def test_rows_use_the_ledger_vocabulary():
    ledger = yaml.safe_load(_read("ROADMAP.v2.yaml"))
    versions = {r["version"] for r in ledger["releases"]}
    for row in ledger["items"] + ledger["threads"]:
        assert row["status"] in {"planned", "built", "partial", "done"}, row
        assert row["release"].split(" ")[0] in versions | {"v2.x"}, row


def test_the_closed_trains_say_so():
    for n in ("", "2", "3", "4", "5"):
        assert _read(f"ROADMAP.enhancements{n}.yaml").startswith("# HISTORY"), n
