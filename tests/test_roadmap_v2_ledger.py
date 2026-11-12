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


# ---- amendment 1: its rows stay in step with their design record, apart from the 2026-10-03 ids
def _amendment():
    return yaml.safe_load(_read("ROADMAP.v2.yaml"))["amendment_1"]


def _amendment_section(start, end):
    md = _read("ROADMAP.v2.amendment-1.md")
    return md[md.index(start):md.index(end)]


def test_every_amendment_item_has_exactly_one_row():
    am = _amendment()
    design = re.findall(r"^- \*\*([A-Z]{3}-\d+) ", _amendment_section("## A4. Items", "## A5. The train"), re.M)
    new = [row["id"] for row in am["items"]]
    changed = [row["id"] for row in am["changed"]]
    assert len(new) == len(set(new))
    assert sorted(design) == sorted(new + [c for c in changed if re.fullmatch(r"[A-Z]{3}-\d+", c)])


def test_amendment_rows_never_reuse_a_ratified_id():
    ledger = yaml.safe_load(_read("ROADMAP.v2.yaml"))
    am = ledger["amendment_1"]
    ratified = {row["id"] for row in ledger["items"]}
    assert not ratified & {row["id"] for row in am["items"]}
    chartered = ratified | {row["id"] for row in ledger["threads"]}
    assert {row["id"] for row in am["changed"]} <= chartered
    assert not {r["id"] for r in ledger["owner_rulings"]} & {r["id"] for r in am["owner_rulings"]}


def test_amendment_rows_use_the_ledger_vocabulary():
    ledger = yaml.safe_load(_read("ROADMAP.v2.yaml"))
    am = ledger["amendment_1"]
    versions = {r["version"] for r in ledger["releases"]} | {"v2.x"}
    new = {row["id"] for row in am["items"]}
    known = new | {row["id"] for row in ledger["items"]} | {row["id"] for row in ledger["threads"]}
    rulings = {r["id"] for r in am["owner_rulings"]}
    for row in am["items"]:
        assert row["status"] in {"built", "planned", "partial", "done"}, row
        assert row["size"] in {"S", "M", "L"}, row
        assert row["release"] in versions, row
        assert set(row.get("deps", [])) <= known, row
        assert row.get("ruling", next(iter(rulings))) in rulings, row
    for row in am["changed"]:
        assert row["ruling"] in rulings, row


def test_every_amendment_ruling_is_recorded_in_the_design_record():
    asked = re.findall(r"^- \*\*(V2Q-\d+) ", _amendment_section("## A6. Owner rulings", "## A7. KPIs added"), re.M)
    assert asked == [r["id"] for r in _amendment()["owner_rulings"]]
    assert all(r["status"] == "ratified" and r["ruling"] for r in _amendment()["owner_rulings"])
