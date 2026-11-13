"""NAT-3: a floor diet before a floor channel.

A corpus that declares ``budgets.floor_bytes`` gets tend ``floor`` items that propose demoting
floor pointers one at a time, non-behavior first, least used first, until the floor would be
at or under the budget; a pointer whose removal loses a recall hit on CLM-7's replay is listed
last and never proposed as safe. A demotion removes that one line (the outbox's floor copy in
outbox mode) and nothing else. An undeclared corpus sees nothing new.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from memory import build_index as B
from memory import floor_diet as FD
from memory import tend as TD
from memory import tend_queue as Q
from memory.doctor_checks_env import DoctorContext
from memory.lint_floor import trim_safety_report
from memory.telemetry import default_telemetry_dir, log_episode
from memory.telemetry_store import _outcome_ledger_path

from .conftest import write_file

_PAD = "x" * 150  # every pointer line is ~170 bytes, so a few lines move the floor visibly


def _mem(name, desc, mtype):
    return f'---\nname: {name}\ndescription: "{desc}"\ntype: {mtype}\n---\n{desc}, in the body too.\n'


# name -> (description, type, section)
_MEMS = {
    "deploy_rollback": ("deploy rollback steps for the web app", "feedback", "work"),
    "tabs_rule": ("tabs not spaces in the scheduler code", "feedback", "work"),
    "owner_profile": ("the owner prefers short answers", "user", "user"),
    "billing_schema": ("billing tables and their invoice columns", "project", "work"),
    "vendor_api": ("vendor api rate limits and retry headers", "reference", "work"),
    "staging_host": ("staging host names and the deploy target", "project", "work"),
}


def _floor(names):
    user = [n for n in names if _MEMS[n][2] == "user"]
    work = [n for n in names if _MEMS[n][2] == "work"]
    line = lambda n: f"- [{n}]({n}.md) — {_MEMS[n][0]} {_PAD}"  # noqa: E731
    return ("# Agent Memory\n\n## User\n" + "".join(line(n) + "\n" for n in user)
            + "\n## Working Style & Process Feedback\n" + "".join(line(n) + "\n" for n in work))


def _corpus(tmp_path, monkeypatch, budget=None, names=tuple(_MEMS), extra_policy=None):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    os.makedirs(str(tmp_path / "tel"))
    for n, (desc, mtype, _sec) in _MEMS.items():
        write_file(md, f"{n}.md", _mem(n, desc, mtype))
    write_file(md, "MEMORY.md", _floor(names))
    policy = dict(extra_policy or {})
    if budget is not None:
        policy["budgets"] = {"floor_bytes": budget}
    if policy:
        write_file(md, "hippo.json", json.dumps(policy))
    B.build_index(md, B.default_index_dir(md))
    return md, repo


def _floor_bytes(md):
    return len(open(os.path.join(md, "MEMORY.md"), "rb").read())


def _diet_ids(md, repo):
    return [e["id"] for e in Q.build_queue(md, repo, kinds=("floor",), write_cache=False)["pending"]
            if e["target"].startswith(FD.TARGET_PREFIX)]


def _open(md, stem, times=1):
    path = _outcome_ledger_path(default_telemetry_dir(md))
    with open(path, "a", encoding="utf-8") as fh:
        for _ in range(times):
            fh.write(json.dumps({"ts": time.time(), "tool": "Read", "opened": stem, "session_id": "s"}) + "\n")


# --------------------------------------------------------------------------- #
# Undeclared: nothing new
# --------------------------------------------------------------------------- #
def test_an_undeclared_corpus_gets_no_diet_items_and_no_doctor_line(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch)
    assert FD.plan(md) is None
    assert FD.queue_entries(md, Q._entry) == []
    assert _diet_ids(md, repo) == []
    assert FD.check_floor_budget(DoctorContext(md, repo)) is None  # a lean floor: no line at all


def test_an_undeclared_floor_over_the_warn_line_gets_the_suggested_budget(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch)
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write("".join(f"- note {i} {'y' * 200}\n" for i in range(90)))  # past 17,500B
    r = FD.check_floor_budget(DoctorContext(md, repo))
    assert r["status"] == "warn"
    assert "none declared" in r["message"] and '"floor_bytes": 8192' in r["message"]
    assert _diet_ids(md, repo) == []  # a suggestion only: nothing is proposed


def test_a_floor_under_its_budget_proposes_nothing(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=100_000)
    p = FD.plan(md)
    assert p["proposals"] == [] and p["pointers"] == 6 and p["non_behavior"] == 3
    r = FD.check_floor_budget(DoctorContext(md, repo))
    assert r["status"] == "ok" and "within its declared 100,000B" in r["message"]
    assert "3 of 6 floor pointer(s) are not behavior-lane" in r["message"]


# --------------------------------------------------------------------------- #
# The order
# --------------------------------------------------------------------------- #
def test_non_behavior_goes_first_then_behavior_least_used_first(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)  # unreachable: every pointer is listed
    td = default_telemetry_dir(md)
    # billing_schema and owner_profile were used; the others were not
    log_episode(["billing_schema", "owner_profile"], query="billing invoice columns", telemetry_dir=td, session_id="s1")
    _open(md, "billing_schema", 2)
    p = FD.plan(md)
    order = [r["name"] for r in p["proposals"]]
    non_behavior = {"billing_schema", "vendor_api", "staging_host"}
    assert set(order[:3]) == non_behavior and order[2] == "billing_schema"  # used: last of its class
    assert set(order[3:]) == {"deploy_rollback", "tabs_rule", "owner_profile"}
    assert order[-1] == "owner_profile"  # the used behavior pointer goes last of all
    used = next(r for r in p["proposals"] if r["name"] == "billing_schema")
    assert used["injected"] == 1 and used["opened"] == 2
    assert "injected on 1 prompt(s), opened 2 time(s)" in used["evidence"]
    assert "neither injected nor opened" in p["proposals"][0]["evidence"]


def test_proposals_stop_once_the_floor_would_be_within_budget(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=10)
    first = FD.plan(md)["proposals"][0]
    budget = _floor_bytes(md) - first["bytes"] - 1  # one demotion is not enough; two are
    with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"budgets": {"floor_bytes": budget}}, fh)
    p = FD.plan(md)
    assert len(p["proposals"]) == 2
    assert p["proposals"][-1]["after"] <= budget < p["proposals"][0]["after"]
    assert len(_diet_ids(md, repo)) == 2


def test_a_pointer_that_loses_a_recall_hit_is_listed_last_and_never_proposed_as_safe(tmp_path, monkeypatch):
    # A journal on the floor: the hook admits a journal off the floor only when the prompt
    # names it, so a prompt that matched it without naming it loses it once the line goes.
    md, repo = _corpus(tmp_path, monkeypatch, budget=200, extra_policy={"journals": ["*_round.md"]})
    write_file(md, "sprint_round.md", _mem("sprint_round", "sprint planning notes for the round", "project"))
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- [sprint_round](sprint_round.md) — sprint planning notes {_PAD}\n")
    B.build_index(md, B.default_index_dir(md))
    td = default_telemetry_dir(md)
    log_episode(["sprint_round"], query="sprint planning notes", telemetry_dir=td, session_id="s1")
    # ...and a fact the hook matched that it would still serve: no loss.
    log_episode(["billing_schema"], query="billing invoice columns", telemetry_dir=td, session_id="s1")
    # A prompt that matched it in the over-fetch only (it does not rank): not a hit at all.
    log_episode(["vendor_api"], query="what is for lunch today", telemetry_dir=td, session_id="s1")
    rep = trim_safety_report(md, names={"sprint_round", "billing_schema", "vendor_api"}, pools_for=FD._replay_pools(md))
    rows = {r["name"]: r for r in rep["rows"]}
    assert rep["replayed"] and rows["sprint_round"]["named"] == 1 and rows["sprint_round"]["lost"] == 1
    assert rows["billing_schema"]["named"] == 1 and rows["billing_schema"]["lost"] == 0
    assert rows["vendor_api"]["named"] == 0 and rows["vendor_api"]["lost"] == 0
    entries = [e for e in Q.build_queue(md, repo, kinds=("floor",), write_cache=False)["pending"]
               if e["target"].startswith(FD.TARGET_PREFIX)]
    assert entries[-1]["target"] == "diet:sprint_round"
    assert "LOSES 1 recall hit" in entries[-1]["evidence"]
    assert entries[-1]["proposed"].startswith("keep it")
    assert all(e["proposed"].startswith("demote") for e in entries[:-1])
    # Once demoted, the prompt that names it still gets it.
    assert TD.apply("floor:diet:sprint_round", "demote", memory_dir=md, repo_root=repo)["ok"]


def test_a_flat_floor_is_weighed_and_its_out_of_section_items_are_not_doubled(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=300)
    flat = "# Agent Memory\n\n" + "".join(f"- [{n}]({n}.md) — {_MEMS[n][0]} {_PAD}\n" for n in _MEMS)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(flat)
    entries = Q.build_queue(md, repo, kinds=("floor",), write_cache=False)["pending"]
    diet = [e for e in entries if e["target"].startswith(FD.TARGET_PREFIX)]
    assert len(diet) == len(FD.plan(md)["proposals"]) >= 1
    rebloat = {e["target"] for e in entries if not e["target"].startswith(FD.TARGET_PREFIX)}
    assert not rebloat & {e["target"][len(FD.TARGET_PREFIX):] + ".md" for e in diet}


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #
def test_demote_removes_that_one_line_and_nothing_else(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)
    before = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read().split("\n")
    mem_before = open(os.path.join(md, "vendor_api.md"), "rb").read()
    first = _diet_ids(md, repo)[0]
    name = first.split(":", 2)[2]
    r = TD.apply(first, "demote", memory_dir=md, repo_root=repo)
    assert r["ok"], r
    assert "the memory file stays" in r["message"] and "of its 200B budget" in r["message"]
    after = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read().split("\n")
    assert [ln for ln in before if f"]({name}.md)" not in ln] == after
    assert open(os.path.join(md, "vendor_api.md"), "rb").read() == mem_before
    assert os.path.isfile(os.path.join(md, f"{name}.md"))
    assert first not in _diet_ids(md, repo)
    from memory.recall import recall

    desc = _MEMS[name][0]
    assert name in [h["name"] for h in recall(desc, k=5, memory_dir=md)]  # still reachable


def test_demote_is_refused_on_a_floor_item_that_is_not_a_diet_item(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch)
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write("\n## Recalled on demand\n- [Billing](billing_schema.md) — b\n")
    ids = [e["id"] for e in Q.build_queue(md, repo, kinds=("floor",), write_cache=False)["pending"]]
    assert ids == ["floor:billing_schema.md"]
    r = TD.apply(ids[0], "demote", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "floor-diet item" in r["message"]


def test_verdict_help_offers_demote_only_on_a_diet_item():
    assert TD._verdict_help("floor", "floor:x.md")[0] == "Verdicts: done"
    help_ = TD._verdict_help("floor", "floor:diet:x")
    assert help_[0] == "Verdicts: demote | done"
    assert any("the memory file stays" in ln for ln in help_)


def test_driving_tend_item_by_item_reaches_the_budget(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=10)
    budget = _floor_bytes(md) - 600
    with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"budgets": {"floor_bytes": budget}}, fh)
    r = FD.check_floor_budget(DoctorContext(md, repo))
    assert r["status"] == "warn" and f"`{FD.START_COMMAND}`" in r["message"]
    planned = len(FD.plan(md)["proposals"])
    applied = 0
    while True:
        ids = _diet_ids(md, repo)
        if not ids:
            break
        assert TD.apply(ids[0], "demote", memory_dir=md, repo_root=repo)["ok"]
        applied += 1
        assert applied < 10
    assert _floor_bytes(md) <= budget and applied == planned >= 2
    assert FD.check_floor_budget(DoctorContext(md, repo))["status"] == "ok"


# --------------------------------------------------------------------------- #
# Outbox mode: the floor copy, never the working tree
# --------------------------------------------------------------------------- #
def test_demote_in_outbox_mode_edits_the_outbox_floor_copy(repo, memory_dir, monkeypatch, tmp_path):
    import subprocess

    from memory import outbox as OB
    from memory import policy_consent as PC
    from memory import trust as T

    from .conftest import git_commit

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    for n, (desc, mtype, _sec) in _MEMS.items():
        write_file(memory_dir, f"{n}.md", _mem(n, desc, mtype))
    write_file(memory_dir, "MEMORY.md", _floor(tuple(_MEMS)))
    write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox", "budgets": {"floor_bytes": 300}}))
    write_file(repo, ".gitignore", ".hippo/\n.claude/memory.local/\n.claude/memory.outbox/\n")
    git_commit(repo, "seed", int(time.time()) - 5000)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    committed = open(os.path.join(memory_dir, "MEMORY.md"), "rb").read()
    first = _diet_ids(memory_dir, repo)[0]
    r = TD.apply(first, "demote", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"] and "outbox" in r["message"], r
    status = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status == ""
    assert open(os.path.join(memory_dir, "MEMORY.md"), "rb").read() == committed
    copy = open(os.path.join(OB.outbox_dir(memory_dir), "MEMORY.md"), encoding="utf-8").read()
    assert f"]({first.split(':', 2)[2]}.md)" not in copy
    assert first not in _diet_ids(memory_dir, repo)  # the source reads the copy it wrote


@pytest.mark.parametrize("budget", [None, 100_000])
def test_tend_list_for_a_corpus_without_floor_problems_is_unchanged(tmp_path, monkeypatch, budget):
    md, repo = _corpus(tmp_path, monkeypatch, budget=budget)
    assert Q.build_queue(md, repo, kinds=("floor",), write_cache=False)["pending"] == []


# --------------------------------------------------------------------------- #
# The replay's cost: one ranking per prompt, cached on disk, never cold in doctor
# --------------------------------------------------------------------------- #
def _count_rankings(monkeypatch):
    from memory import recall as R

    calls = {"n": 0}
    real = R.recall

    def spy(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(R, "recall", spy)
    return calls


def _with_prompts(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)
    td = default_telemetry_dir(md)
    for q in ("billing invoice columns", "deploy rollback steps", "vendor api rate limits"):
        log_episode(["billing_schema"], query=q, telemetry_dir=td, session_id="s1")
    return md, repo, td


def test_each_prompt_is_ranked_once_and_a_second_call_reads_the_cache(tmp_path, monkeypatch):
    md, repo, td = _with_prompts(tmp_path, monkeypatch)
    calls = _count_rankings(monkeypatch)
    first = FD.plan(md)
    assert calls["n"] == 3  # one ranking per prompt, not one per prompt per pointer or pass
    assert os.path.isfile(FD._cache_path(md))
    second = FD.plan(md)
    assert calls["n"] == 3 and [r["name"] for r in second["proposals"]] == [r["name"] for r in first["proposals"]]
    log_episode([], query="staging host names", telemetry_dir=td, session_id="s1")
    FD.plan(md)
    assert calls["n"] == 4  # a new prompt costs its own ranking only


@pytest.mark.parametrize("damage", ["torn", "stale"])
def test_a_torn_or_stale_cache_reads_as_absent(tmp_path, monkeypatch, damage):
    md, repo, _td = _with_prompts(tmp_path, monkeypatch)
    calls = _count_rankings(monkeypatch)
    FD.plan(md)
    path = FD._cache_path(md)
    if damage == "torn":
        with open(path, "r+", encoding="utf-8") as fh:
            fh.truncate(os.path.getsize(path) // 2)
    else:
        time.sleep(0.01)
        B.build_index(md, B.default_index_dir(md))  # the index moved: every ranking is stale
    FD.plan(md)
    assert calls["n"] == 6


def test_a_cold_replay_fills_in_batches_and_an_unjudged_pointer_waits(tmp_path, monkeypatch):
    md, repo, _td = _with_prompts(tmp_path, monkeypatch)
    monkeypatch.setattr(FD, "REPLAY_BATCH", 2)
    calls = _count_rankings(monkeypatch)
    entries = FD.queue_entries(md, Q._entry)
    assert calls["n"] == 2  # the two newest prompts; the oldest waits for the next call
    waiting = [e for e in entries if e["proposed"].startswith("wait")]
    assert [e["target"] for e in waiting] == ["diet:billing_schema"]  # named on the oldest
    assert "not judged yet" in waiting[0]["evidence"] and entries[-1] is waiting[0]
    p = FD.plan(md)
    assert calls["n"] == 3 and not any(r["waiting"] for r in p["proposals"])


def test_doctor_never_runs_the_replay(tmp_path, monkeypatch):
    from memory.doctor_checks_lifecycle import check_tend_queue

    md, repo, _td = _with_prompts(tmp_path, monkeypatch)
    calls = _count_rankings(monkeypatch)
    ctx = DoctorContext(md, repo)
    assert "floor" in check_tend_queue(ctx)["message"]
    line = FD.check_floor_budget(ctx)["message"]
    assert calls["n"] == 0
    assert "runs on `hippo tend`, not here" in line
    FD.plan(md)  # what `hippo tend` does
    assert "runs on `hippo tend`" not in FD.check_floor_budget(ctx)["message"]
    n = calls["n"]
    check_tend_queue(ctx)
    assert calls["n"] == n
