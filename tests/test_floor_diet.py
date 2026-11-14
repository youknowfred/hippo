"""NAT-3: a floor diet before a floor channel.

A corpus that declares ``budgets.floor_bytes`` gets tend ``floor`` items that propose demoting
floor pointers one at a time, non-behavior first, least used first, until the floor would be
under the budget by the diet's headroom; a pointer whose memory is too new to judge on use
waits, and one whose removal loses a recall hit on CLM-7's replay is listed last and never
proposed as safe. A demotion removes that one line (the outbox's floor copy in
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


_DAY = 86400


def _backdate(path, days=40):
    """A non-git corpus dates a memory by its file's mtime: put it well outside any window."""
    then = time.time() - days * _DAY
    os.utime(path, (then, then))
    return path


def _corpus(tmp_path, monkeypatch, budget=None, names=tuple(_MEMS), extra_policy=None):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    os.makedirs(str(tmp_path / "tel"))
    for n, (desc, mtype, _sec) in _MEMS.items():
        _backdate(write_file(md, f"{n}.md", _mem(n, desc, mtype)))
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


def _open(md, stem, times=1, days_ago=0):
    path = _outcome_ledger_path(default_telemetry_dir(md))
    with open(path, "a", encoding="utf-8") as fh:
        for _ in range(times):
            fh.write(json.dumps({"ts": time.time() - days_ago * _DAY, "tool": "Read", "opened": stem,
                                 "session_id": "s"}) + "\n")


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
# Age: a memory newer than the window its use is counted over is not "least used"
# --------------------------------------------------------------------------- #
def _git_corpus(repo, memory_dir, monkeypatch, tmp_path, fresh_days):
    """Three facts committed 60 days ago and one committed ``fresh_days`` ago, all on the floor."""
    from .conftest import git_commit

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    os.makedirs(str(tmp_path / "tel"))
    now = int(time.time())
    old = ("billing_schema", "vendor_api", "staging_host")
    for n in old:
        write_file(memory_dir, f"{n}.md", _mem(n, _MEMS[n][0], _MEMS[n][1]))
    write_file(memory_dir, "hippo.json", json.dumps({"budgets": {"floor_bytes": 200}}))
    git_commit(repo, "old facts", now - 60 * _DAY)
    write_file(memory_dir, "fresh_fact.md", _mem("fresh_fact", "queue worker restart order", "project"))
    lines = [f"- [{n}]({n}.md) — {_MEMS[n][0]} {_PAD}" for n in old]
    lines.append(f"- [fresh_fact](fresh_fact.md) — queue worker restart order {_PAD}")
    write_file(memory_dir, "MEMORY.md", "# Agent Memory\n\n## User\n\n## Working Style & Process Feedback\n"
               + "\n".join(lines) + "\n")
    git_commit(repo, "a fresh fact", now - fresh_days * _DAY)
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    return old


def test_a_memory_committed_inside_the_ledger_span_is_not_least_used(repo, memory_dir, monkeypatch, tmp_path):
    old = _git_corpus(repo, memory_dir, monkeypatch, tmp_path, fresh_days=2)
    for n in old:  # the ledgers hold 10 days; each old fact was opened in them, the new one never
        _open(memory_dir, n, days_ago=10)
    p = FD.plan(memory_dir)
    order = [r["name"] for r in p["proposals"]]
    assert order[-1] == "fresh_fact" and set(order[:-1]) == set(old)  # zero use, yet not first
    row = p["proposals"][-1]
    assert row["young"] and row["use"] == 0 and not row["unsafe"]
    assert "age: first committed 2 day(s) ago, younger than the 10 days the ledgers hold" in row["evidence"]
    assert not any(r["young"] or "age:" in r["evidence"] for r in p["proposals"][:-1])
    entries = FD.queue_entries(memory_dir, Q._entry)
    assert entries[-1]["target"] == "diet:fresh_fact" and entries[-1]["proposed"].startswith("wait: too new")
    assert all(e["proposed"].startswith("demote") for e in entries[:-1])
    # The user may still overrule it: the item is live and takes the verdict.
    assert TD.apply("floor:diet:fresh_fact", "demote", memory_dir=memory_dir, repo_root=repo)["ok"]


@pytest.mark.parametrize("fresh_days, young", [(2, True), (8, False)])
def test_short_or_empty_ledgers_still_give_a_new_memory_a_week(repo, memory_dir, monkeypatch, tmp_path, fresh_days, young):
    _git_corpus(repo, memory_dir, monkeypatch, tmp_path, fresh_days=fresh_days)
    rows = {r["name"]: r for r in FD.plan(memory_dir)["proposals"]}  # no ledger rows at all
    assert rows["fresh_fact"]["young"] is young
    assert (f"younger than the {FD.MIN_AGE_DAYS}-day minimum" in rows["fresh_fact"]["evidence"]) is young
    assert not rows["billing_schema"]["young"]


def test_a_corpus_outside_git_dates_a_memory_by_its_file(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)  # no git: files last changed 40 days ago
    write_file(md, "fresh_fact.md", _mem("fresh_fact", "queue worker restart order", "project"))
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- [fresh_fact](fresh_fact.md) — queue worker restart order {_PAD}\n")
    p = FD.plan(md)
    assert [r["name"] for r in p["proposals"] if r["young"]] == ["fresh_fact"]
    assert p["proposals"][-1]["name"] == "fresh_fact"
    assert "age: not in git history, file last changed 0 day(s) ago" in p["proposals"][-1]["evidence"]


def test_ages_cost_one_history_scan_however_many_pointers(repo, memory_dir, monkeypatch, tmp_path):
    from memory import archive as A

    _git_corpus(repo, memory_dir, monkeypatch, tmp_path, fresh_days=2)
    calls = []
    real = A.run_git
    monkeypatch.setattr(A, "run_git", lambda args, root: calls.append(args[0]) or real(args, root))
    assert len(FD.plan(memory_dir)["proposals"]) == 4
    assert calls.count("log") == 1


# --------------------------------------------------------------------------- #
# Headroom: a finished diet leaves room for the next floor pointer
# --------------------------------------------------------------------------- #
def _set_budgets(md, **budgets):
    path = os.path.join(md, "hippo.json")
    doc = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {}
    doc["budgets"] = budgets
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def test_a_finished_diet_leaves_room_for_the_next_floor_pointer_to_ship(repo, memory_dir, monkeypatch, tmp_path, capsys):
    """The gate refuses a change that leaves the floor over its budget and bigger. A diet that
    stops a few bytes under the budget makes the next save's floor pointer that change."""
    import subprocess

    from memory import new_memory as N
    from memory import policy_consent as PC
    from memory import ship_cli as SC
    from memory import trust as T

    from .conftest import git_commit

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    os.makedirs(str(tmp_path / "tel"))
    names = [f"habit_{i:02d}" for i in range(30)]
    for n in names:
        write_file(memory_dir, f"{n}.md", _mem(n, f"working habit {n} for the scheduler", "feedback"))
    write_file(memory_dir, "MEMORY.md", "# Agent Memory\n\n## User\n\n## Working Style & Process Feedback\n"
               + "".join(f"- [{n}]({n}.md) — working habit {_PAD}\n" for n in names))
    write_file(memory_dir, "hippo.json", json.dumps({"budgets": {"floor_bytes": 10}}))
    write_file(repo, ".gitignore", ".hippo/\n.claude/memory.local/\n.claude/memory.outbox/\n")
    git_commit(repo, "seed", 1_700_000_000)
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    # The emgl shape: a budget that six demotions land 5 bytes under (it was 40 bytes there).
    budget = FD.plan(memory_dir)["proposals"][5]["after"] + 5
    _set_budgets(memory_dir, floor_bytes=budget)
    for _ in range(40):
        ids = _diet_ids(memory_dir, repo)
        if not ids:
            break
        assert TD.apply(ids[0], "demote", memory_dir=memory_dir, repo_root=repo)["ok"]
    assert _diet_ids(memory_dir, repo) == [] and _floor_bytes(memory_dir) <= budget  # the diet is finished
    # The diet lands, and the corpus saves through the outbox from here on.
    write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox", "budgets": {"floor_bytes": budget}}))
    git_commit(repo, "the diet", 1_700_000_100)
    for args in (("update-ref", "refs/remotes/origin/main", "HEAD"),
                 ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")):
        subprocess.run(["git", "-C", repo, *args], check=True)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    r = N.write_memory("lint_before_push", "run the linter before every push", "feedback", "the body",
                       memory_dir=memory_dir, repo_root=repo)
    assert r["created"] and r.get("write_path") == "outbox" and r["floor"]["status"] == "appended", r
    capsys.readouterr()
    rc = SC.main(["--memory-dir", memory_dir, "--yes"])
    assert rc == 0, capsys.readouterr().out  # before: "a finding against this ship", exit 1


def test_the_diet_runs_to_the_budget_less_its_headroom(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=800)  # the floor is about 1,200B
    p = FD.plan(md)
    assert (p["budget"], p["headroom"], p["target"]) == (800, 50, 750)  # a sixteenth by default
    assert p["proposals"][-1]["after"] <= 750
    entry = FD.queue_entries(md, Q._entry)[0]
    assert "of its 800B budget" in entry["proposed"] and "the diet runs to 750B" in entry["proposed"]
    _set_budgets(md, floor_bytes=800, floor_headroom_bytes=300)  # declared: bytes, read beside the budget
    p = FD.plan(md)
    assert (p["headroom"], p["target"]) == (300, 500) and p["proposals"][-1]["after"] <= 500
    _set_budgets(md, floor_bytes=800, floor_headroom_bytes=0)  # none: the diet stops at the budget
    p = FD.plan(md)
    assert p["target"] == 800 and p["proposals"][-1]["after"] <= 800
    assert "the diet runs to" not in FD.queue_entries(md, Q._entry)[0]["proposed"]
    for bad in (800, 5000, -1, True, "64", 12.5):  # not a byte count under the budget: the default
        _set_budgets(md, floor_bytes=800, floor_headroom_bytes=bad)
        assert FD.plan(md)["headroom"] == 50, bad


def test_doctor_says_when_the_floor_is_inside_the_headroom(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=100_000)
    size = _floor_bytes(md)
    _set_budgets(md, floor_bytes=size + 10)  # within the budget, 10B to spare
    r = FD.check_floor_budget(DoctorContext(md, repo))
    assert r["status"] == "ok" and f"within its declared {size + 10:,}B" in r["message"]
    assert "10B to spare" in r["message"] and f"`{FD.START_COMMAND}`" in r["message"]
    assert len(_diet_ids(md, repo)) == 1  # one line restores the room
    _set_budgets(md, floor_bytes=size - 10)  # over: the warn line names where the diet ends
    r = FD.check_floor_budget(DoctorContext(md, repo))
    target = (size - 10) - (size - 10) // 16
    assert r["status"] == "warn" and f"it runs to {target:,}B" in r["message"]
    # The gate still judges against the budget itself, not the diet's target.
    from memory.gate import judge_floor
    from memory.provenance_format import read_floor_lint

    floor = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    _set_budgets(md, floor_bytes=size + 100)
    grown = floor + "- [vendor_api](vendor_api.md) — again\n"  # 40B: inside the budget, over the target
    assert len(grown.encode()) > FD.plan(md)["target"]
    assert judge_floor(floor, grown, read_floor_lint(md), {"floor_bytes": size + 100}) == []


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


def test_demote_on_a_diet_id_the_queue_no_longer_proposes_writes_nothing(tmp_path, monkeypatch):
    """`done` re-checks a floor id that left the queue; `demote` writes, so it needs a live item."""
    md, repo = _corpus(tmp_path, monkeypatch, budget=100_000)  # under budget: no diet items
    assert _diet_ids(md, repo) == []
    before = open(os.path.join(md, "MEMORY.md"), "rb").read()
    r = TD.apply("floor:diet:vendor_api", "demote", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "not in the maintenance queue now" in r["message"]
    assert open(os.path.join(md, "MEMORY.md"), "rb").read() == before
    assert TD.apply("floor:diet:vendor_api", "done", memory_dir=md, repo_root=repo)["ok"]  # gone: fixed


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
    assert _floor_bytes(md) <= budget - budget // 16 and applied == planned >= 2  # to the target
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


# --------------------------------------------------------------------------- #
# A line that carries more than one pointer: the item says what else leaves with it
# --------------------------------------------------------------------------- #
def _joined(md):
    """Put staging_host's pointer on vendor_api's line (two pointers, one floor line)."""
    path = os.path.join(md, "MEMORY.md")
    lines = open(path, encoding="utf-8").read().split("\n")
    rider = next(ln for ln in lines if "](staging_host.md)" in ln)
    lines.remove(rider)
    i = next(i for i, ln in enumerate(lines) if "](vendor_api.md)" in ln)
    lines[i] = lines[i] + " · " + rider[2:]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return lines[i]


def test_a_diet_item_says_which_other_pointers_ride_on_its_line(tmp_path, monkeypatch, capsys):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)
    line = _joined(md)
    entries = {e["id"]: e for e in FD.queue_entries(md, Q._entry)}
    assert "floor:diet:staging_host" not in entries  # it has no line of its own to weigh
    ev = entries["floor:diet:vendor_api"]["evidence"]
    assert "its line also points at 1 other memory, which leaves the floor with it: staging_host" in ev
    assert not any("also points at" in e["evidence"] for i, e in entries.items() if i != "floor:diet:vendor_api")
    assert TD.main(["show", "floor:diet:vendor_api", "--memory-dir", md, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert f"floor line: {line}" in out and "leaves the floor with it: staging_host" in out
    r = TD.apply("floor:diet:vendor_api", "demote", memory_dir=md, repo_root=repo)
    assert r["ok"] and "staging_host" in r["message"] and "left the floor with it" in r["message"], r
    floor = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    assert "staging_host" not in floor and os.path.isfile(os.path.join(md, "staging_host.md"))


def test_a_one_pointer_line_reads_as_before(tmp_path, monkeypatch, capsys):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)
    first = FD.queue_entries(md, Q._entry)[0]
    assert "also points at" not in first["evidence"]
    assert TD.main(["show", first["id"], "--memory-dir", md, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert "floor line: - [" in out and "leaves the floor with it" not in out
    r = TD.apply(first["id"], "demote", memory_dir=md, repo_root=repo)
    assert r["ok"] and "left the floor with it" not in r["message"]
