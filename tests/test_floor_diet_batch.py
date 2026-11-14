"""NAT-3: the floor diet as one reviewed batch.

``hippo tend diet`` shows every demotion the diet would serve, in order, each with its
evidence, the literal floor line and what else is on it, and ends with a digest bound to the
floor's bytes and the listed set. ``hippo tend apply floor:diet --verdict demote --digest <d>``
applies exactly that list, less any names the user leaves out. Each item is judged again as
its turn comes; one that is no longer safe is skipped and named, never forced. "wait" and
"keep it" items are never in the batch, the single-item path is unchanged, and a corpus that
declares no floor budget is untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time

import pytest

from memory import build_index as B
from memory import floor_diet as FD
from memory import floor_diet_batch as FB
from memory import tend as TD
from memory import tend_queue as Q
from memory.telemetry import default_telemetry_dir, log_episode

from .conftest import write_file
from .test_floor_diet import _MEMS, _PAD, _corpus, _diet_ids, _floor_bytes, _joined, _mem, _set_budgets


def _floor(md):
    return open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()


def _tree(root):
    """Every file under ``root`` with its bytes' hash: what "the corpus was not written" means."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def _reachable(tmp_path, monkeypatch, extra_policy=None):
    """The six-pointer fixture with a budget three or four demotions reach."""
    md, repo = _corpus(tmp_path, monkeypatch, budget=800, extra_policy=extra_policy)
    assert FD.plan(md)["target"] == 750 and _floor_bytes(md) > 800
    return md, repo


def _apply(md, repo, digest, leave_out=None):
    return TD.apply(FB.BATCH_ID, "demote", memory_dir=md, repo_root=repo, digest=digest, leave_out=leave_out)


def _with_journal(tmp_path, monkeypatch):
    """Every pointer must go (the budget is out of reach), and one of them is a journal: the
    hook serves a journal off the floor only when the prompt names it."""
    md, repo = _corpus(tmp_path, monkeypatch, budget=200, extra_policy={"journals": ["*_round.md"]})
    write_file(md, "sprint_round.md", _mem("sprint_round", "sprint planning notes for the round", "project"))
    os.utime(os.path.join(md, "sprint_round.md"), (time.time() - 40 * 86400,) * 2)
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- [sprint_round](sprint_round.md) — sprint planning notes {_PAD}\n")
    B.build_index(md, B.default_index_dir(md))
    return md, repo


# --------------------------------------------------------------------------- #
# The review
# --------------------------------------------------------------------------- #
def test_the_review_shows_every_item_its_line_and_a_digest(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    before = _tree(md)
    r = FB.review(md, repo)
    assert r["ok"] and _tree(md) == before  # nothing in the corpus dir was written
    rv, text = r["review"], r["message"]
    names = [it["name"] for it in rv["items"]]
    assert len(names) >= 2
    pos = [text.index(f"floor:diet:{n}") for n in names]
    assert pos == sorted(pos)  # in the order the diet serves them
    for it in rv["items"]:
        assert it["evidence"] in text and all(f"line: {ln}" in text for ln in it["lines"])
    # Where the batch would leave the floor, against the target and the budget.
    assert rv["after"] == rv["items"][-1]["after"] <= 750
    assert f"{rv['after']:,}B" in text and "750B target" in text and "800B budget" in text
    # The digest is bound to the floor's bytes and the listed set, and the command quotes it.
    assert len(rv["digest"]) == 12
    assert f"hippo tend apply floor:diet --verdict demote --digest {rv['digest']}" in text
    assert FB.review(md, repo)["review"]["digest"] == rv["digest"]  # the same floor, the same list
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write("- a note\n")
    assert FB.review(md, repo)["review"]["digest"] != rv["digest"]  # the floor's bytes moved


def test_a_line_that_carries_other_pointers_is_never_in_the_batch(tmp_path, monkeypatch):
    """The other pointers on a line have no use count and no replay of their own, so the diet
    cannot say they are safe to take off the floor. A batch demotes only what was judged."""
    md, repo = _reachable(tmp_path, monkeypatch)
    joined = _joined(md)  # staging_host rides on vendor_api's line
    assert FD.queue_entries(md, Q._entry)[0]["target"] == "diet:vendor_api"  # the single path's first item
    r = FB.review(md, repo)
    rv, text = r["review"], r["message"]
    assert rv["items"] and "vendor_api" not in [it["name"] for it in rv["items"]]
    assert rv["after"] <= 750  # the batch reaches the target with other lines
    solo = next(x for x in rv["rest"] if x["name"] == "vendor_api")
    assert solo["riders"] == ["staging_host"] and solo["lines"] == [joined]
    assert solo["proposed"].startswith("single verdict only") and "staging_host" in solo["proposed"]
    tail = text.split("Not in this batch", 1)[1]
    assert "floor:diet:vendor_api" in tail and f"line: {joined}" in tail
    assert "also on this line (it leaves the floor too): staging_host" in tail
    out = _apply(md, repo, rv["digest"])
    assert out["ok"] and out["skipped"] == [] and joined in _floor(md).split("\n")
    assert "staging_host" not in out["message"]  # nothing rode out with this batch


def test_the_batch_is_the_sequence_the_single_item_path_would_serve(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    twin = str(tmp_path / "twin")
    shutil.copytree(repo, twin)
    md2 = os.path.join(twin, ".claude", "memory")
    served = []
    while True:  # the item-by-item path, on a copy
        ids = _diet_ids(md2, twin)
        if not ids:
            break
        assert TD.apply(ids[0], "demote", memory_dir=md2, repo_root=twin)["ok"]
        served.append(ids[0])
    rv = FB.review(md, repo)["review"]
    assert [it["id"] for it in rv["items"]] == served
    r = _apply(md, repo, rv["digest"])
    assert r["ok"] and r["applied"] == [it["name"] for it in rv["items"]] and r["skipped"] == []
    assert _floor(md) == _floor(md2) and _floor_bytes(md) == rv["after"]


def test_a_demotion_can_make_a_later_line_unsafe_and_the_review_weighs_that_first(tmp_path, monkeypatch):
    """With lanes declared the hook gives the behavior lane a share of its slots. Eight habits
    on the floor all match one prompt; each is served without its line while the others still
    collapse onto theirs. Once four are demoted they hold the lane's four slots, and each of
    the other four loses that hit. No prompt arrived in between: the batch's own demotions
    moved the judgment, so the list is built by weighing the floor again after each line."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    os.makedirs(str(tmp_path / "tel"))
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    habits = [f"habit_{i}" for i in range(1, 9)]
    old = (time.time() - 40 * 86400,) * 2
    for i, n in enumerate(habits):  # habit_1 matches the prompt best, habit_8 least
        os.utime(write_file(md, f"{n}.md", f'---\nname: {n}\ndescription: "deploy checklist habit {i + 1}"\n'
                            f"type: feedback\n---\n{' '.join(['deploy checklist steps'] * (12 - i))}\n"), old)
    for i in range(8):  # eight facts off the floor fill the other slots
        os.utime(write_file(md, f"fact_{i}.md", f'---\nname: fact_{i}\ndescription: "deploy checklist fact {i}"\n'
                            f"type: project\n---\ndeploy checklist steps deploy checklist steps detail {i}\n"), old)
    write_file(md, "MEMORY.md", "# Agent Memory\n\n## User\n\n## Working Style & Process Feedback\n"
               + "".join(f"- [{n}]({n}.md) — deploy checklist habit {_PAD}\n" for n in habits))
    write_file(md, "hippo.json", json.dumps({"budgets": {"floor_bytes": 100}, "journals": []}))
    B.build_index(md, B.default_index_dir(md))
    log_episode(habits, query="deploy checklist steps", telemetry_dir=default_telemetry_dir(md), session_id="s1")
    # The queue judges every line against the floor as it is: all eight read as safe.
    assert [e["proposed"].split(":")[0] for e in FD.queue_entries(md, Q._entry)] == ["demote"] * 8
    rv = FB.review(md, repo)["review"]
    assert [it["name"] for it in rv["items"]] == habits[:4]
    assert [(x["name"], x["proposed"].split(":")[0]) for x in rv["rest"]] == [(n, "keep it") for n in habits[4:]]
    assert all("LOSES 1 recall hit" in x["evidence"] for x in rv["rest"])
    r = _apply(md, repo, rv["digest"])
    assert r["ok"] and r["applied"] == habits[:4] and r["skipped"] == []  # nothing approved was refused
    assert all(f"]({n}.md)" in _floor(md) for n in habits[4:])
    assert "The diet proposes next: floor:diet:habit_5 (keep it)" in r["message"]


def test_the_review_finishes_a_cold_replay_so_nothing_waits(tmp_path, monkeypatch):
    md, repo = _corpus(tmp_path, monkeypatch, budget=200)
    td = default_telemetry_dir(md)
    for q in ("billing invoice columns", "deploy rollback steps", "vendor api rate limits"):
        log_episode(["billing_schema"], query=q, telemetry_dir=td, session_id="s1")
    monkeypatch.setattr(FD, "REPLAY_BATCH", 1)  # `hippo tend` would rank one prompt per call
    assert any(e["proposed"].startswith("wait") for e in FD.queue_entries(md, Q._entry))
    FD._write_replay_cache(md, {})  # cold again
    rv = FB.review(md, repo)["review"]
    assert len(rv["items"]) == 6 and rv["rest"] == []


def test_a_floor_within_its_target_and_an_undeclared_corpus_have_nothing_to_review(tmp_path, monkeypatch, capsys):
    md, repo = _corpus(tmp_path, monkeypatch, budget=100_000)
    r = FB.review(md, repo)
    assert r["ok"] and r["review"]["items"] == [] and r["review"]["digest"] is None
    assert "nothing to review" in r["message"]
    assert not os.path.exists(Q.state_path(md))  # no review, so nothing recorded
    assert TD.hold("floor:x.md", "why", memory_dir=md, repo_root=repo)["ok"]
    assert sorted(json.load(open(Q.state_path(md)))) == ["hold", "skip", "snooze"]  # the state file as before
    os.remove(Q.state_path(md))
    os.remove(os.path.join(md, "hippo.json"))  # undeclared: no diet at all
    before = _tree(md)
    r = FB.review(md, repo)
    assert not r["ok"] and "budgets" in r["message"] and "floor_bytes" in r["message"]
    assert not _apply(md, repo, "0" * 12)["ok"]
    assert _tree(md) == before and not os.path.exists(Q.state_path(md))
    assert TD.main(["diet", "--memory-dir", md, "--repo-root", repo]) == 1
    assert "floor_bytes" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #
def test_the_verdict_applies_exactly_the_reviewed_set(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    listed = [it["name"] for it in rv["items"]]
    floor_before = _floor(md).split("\n")
    files_before = {n: open(os.path.join(md, f"{n}.md"), "rb").read() for n in _MEMS}
    r = _apply(md, repo, rv["digest"])
    assert r["ok"], r
    assert r["applied"] == listed and r["skipped"] == [] and r["left_out"] == []
    gone = [ln for it in rv["items"] for ln in it["lines"]]
    assert _floor(md).split("\n") == [ln for ln in floor_before if ln not in gone]  # those lines, nothing else
    assert {n: open(os.path.join(md, f"{n}.md"), "rb").read() for n in _MEMS} == files_before
    assert f"{len(listed)} demoted" in r["message"] and "0 skipped" in r["message"]
    assert f"{_floor_bytes(md):,}B" in r["message"] and "750B target" in r["message"]
    assert "the diet is finished" in r["message"] and _diet_ids(md, repo) == []
    again = _apply(md, repo, rv["digest"])  # a digest is spent once the floor it names is gone
    assert not again["ok"] and "not the one that review showed" in again["message"]


@pytest.mark.parametrize("how", ["no review", "wrong digest", "no digest", "floor moved"])
def test_the_verdict_is_refused_unless_it_quotes_the_review_of_this_floor(tmp_path, monkeypatch, how):
    md, repo = _reachable(tmp_path, monkeypatch)
    digest = "abcdefabcdef" if how == "no review" else FB.review(md, repo)["review"]["digest"]
    if how == "wrong digest":
        digest = "0" * 12
    if how == "no digest":
        digest = None
    if how == "floor moved":  # one byte is enough
        with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
            fh.write("\n")
    before = _floor(md)
    r = _apply(md, repo, digest)
    assert not r["ok"] and "hippo tend diet" in r["message"], r
    if how == "floor moved":
        assert "not the one that review showed" in r["message"]
    assert _floor(md) == before


def test_named_items_are_left_out_and_the_result_says_what_comes_next(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    names = [it["name"] for it in rv["items"]]
    keep = names[0]
    kept_line = rv["items"][0]["lines"][0]
    r = _apply(md, repo, rv["digest"], leave_out=[f"floor:diet:{keep}"])  # an id or a bare name
    assert r["ok"] and r["left_out"] == [keep] and r["applied"] == names[1:], r
    assert kept_line in _floor(md).split("\n")
    assert f"left out at your word: floor:diet:{keep}" in r["message"]
    size = _floor_bytes(md)
    assert size > 750 and f"{size:,}B" in r["message"] and f"{size - 750:,}B over its 750B target" in r["message"]
    assert f"The diet proposes next: floor:diet:{keep}" in r["message"]
    assert f"hippo tend hold floor:diet:{keep}" in r["message"]  # how to keep it for good


def test_an_unknown_name_to_leave_out_refuses_the_whole_verdict(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    before = _floor(md)
    r = _apply(md, repo, rv["digest"], leave_out=["vendor_apy"])  # a typo must not demote the real one
    assert not r["ok"] and "vendor_apy" in r["message"] and "Nothing was demoted" in r["message"]
    assert _floor(md) == before
    assert _apply(md, repo, rv["digest"])["ok"]  # the review still stands


def test_a_held_item_is_planned_around(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    first = FB.review(md, repo)["review"]["items"][0]
    assert TD.hold(first["id"], "the owner wants it on the floor", memory_dir=md, repo_root=repo)["ok"]
    r = FB.review(md, repo)
    rv = r["review"]
    assert first["name"] not in [it["name"] for it in rv["items"]]
    assert rv["after"] <= 750  # the batch still reaches the target, with other lines
    assert [h["id"] for h in rv["held"]] == [first["id"]]
    assert f"{first['id']} (held: the owner wants it on the floor)" in r["message"]
    assert _apply(md, repo, rv["digest"])["ok"] and first["lines"][0] in _floor(md).split("\n")


def test_a_hold_placed_after_the_review_is_honoured_by_the_verdict(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    spared = rv["items"][1]
    assert TD.hold(spared["id"], "keep it after all", memory_dir=md, repo_root=repo)["ok"]
    r = _apply(md, repo, rv["digest"])  # the hold is tend state, not floor bytes: the digest stands
    assert r["ok"] and [n for n, _w in r["skipped"]] == [spared["name"]]
    assert "your tend state keeps it on the floor (held: keep it after all)" in r["skipped"][0][1]
    assert spared["lines"][0] in _floor(md).split("\n")


# --------------------------------------------------------------------------- #
# Each item is judged again at its turn: skipped, never forced
# --------------------------------------------------------------------------- #
def test_an_item_a_later_prompt_made_unsafe_is_skipped_and_named(tmp_path, monkeypatch):
    """The emgl case: the reviewing session's own prompts enter the replay window between the
    review and the verdict. The batch judges on the prompts on record when it applies."""
    md, repo = _with_journal(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    listed = [it["name"] for it in rv["items"]]
    assert "sprint_round" in listed and len(listed) == 7 and rv["rest"] == []
    # After the review: a prompt the hook matched the journal on, without naming it.
    log_episode(["sprint_round"], query="sprint planning notes", telemetry_dir=default_telemetry_dir(md),
                session_id="reviewing-session")
    r = _apply(md, repo, rv["digest"])
    assert r["ok"], r
    assert r["applied"] == [n for n in listed if n != "sprint_round"]
    assert [n for n, _why in r["skipped"]] == ["sprint_round"]
    assert "loses 1 recall hit" in r["skipped"][0][1]
    assert "](sprint_round.md)" in _floor(md)  # still on the floor
    assert "6 demoted, 1 skipped" in r["message"]
    assert "skipped floor:diet:sprint_round: it now loses 1 recall hit" in r["message"]
    size = _floor_bytes(md)
    assert f"{size:,}B" in r["message"] and "over its 188B target" in r["message"]
    assert "The diet proposes next: floor:diet:sprint_round (keep it)" in r["message"]
    # Forcing it stays what it was: one explicit verdict on that one item.
    assert TD.apply("floor:diet:sprint_round", "demote", memory_dir=md, repo_root=repo)["ok"]


def test_a_batch_whose_replay_cannot_run_demotes_nothing(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    log_episode(["billing_schema"], query="billing invoice columns", telemetry_dir=default_telemetry_dir(md),
                session_id="s1")
    rv = FB.review(md, repo)["review"]
    assert rv["items"]
    shutil.rmtree(B.default_index_dir(md))  # no index: the prompts on record cannot be replayed
    before = _floor(md)
    r = _apply(md, repo, rv["digest"])
    assert r["ok"] and r["applied"] == [] and len(r["skipped"]) == len(rv["items"])
    assert all("replay is not finished" in why for _n, why in r["skipped"])
    assert _floor(md) == before and "0 demoted" in r["message"]


def test_a_failure_part_way_is_a_named_skip_and_the_result_still_counts_what_went(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    names = [it["name"] for it in rv["items"]]
    real = FD.demote

    def flaky(name, *a, **kw):
        if name == names[1]:
            raise OSError("disk says no")
        return real(name, *a, **kw)

    monkeypatch.setattr(FD, "demote", flaky)
    r = _apply(md, repo, rv["digest"])
    assert r["ok"] and r["applied"] == [names[0], *names[2:]], r  # not "refused": lines did go
    assert [n for n, _w in r["skipped"]] == [names[1]] and "disk says no" in r["skipped"][0][1]
    assert f"{len(names) - 1} demoted, 1 skipped" in r["message"]
    assert rv["items"][1]["lines"][0] in _floor(md).split("\n")


def test_a_line_that_is_not_the_reviewed_one_is_not_removed(tmp_path, monkeypatch):
    """Another writer changed a reviewed line after the batch began: the removal is exact."""
    from memory.new_memory_floor import _remove_floor_pointer

    md, repo = _reachable(tmp_path, monkeypatch)
    line = next(ln for ln in _floor(md).split("\n") if "](vendor_api.md)" in ln)
    before = _floor(md).replace(line, line + " · [tabs_rule](tabs_rule.md)")
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(before)
    r = _remove_floor_pointer(md, "vendor_api", expect=[line])
    assert r["status"] == "skipped" and "not the line that was reviewed" in r["reason"]
    assert _floor(md) == before
    assert _remove_floor_pointer(md, "vendor_api", expect=[line + " · [tabs_rule](tabs_rule.md)"])["status"] == "removed"


# --------------------------------------------------------------------------- #
# "wait" and "keep it" are never in the batch
# --------------------------------------------------------------------------- #
def test_wait_and_keep_it_items_are_shown_and_never_in_the_batch(tmp_path, monkeypatch):
    md, repo = _with_journal(tmp_path, monkeypatch)
    log_episode(["sprint_round"], query="sprint planning notes", telemetry_dir=default_telemetry_dir(md),
                session_id="s1")  # the journal loses a hit: "keep it"
    write_file(md, "fresh_fact.md", _mem("fresh_fact", "queue worker restart order", "project"))
    with open(os.path.join(md, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- [fresh_fact](fresh_fact.md) — queue worker restart order {_PAD}\n")  # "wait: too new"
    B.build_index(md, B.default_index_dir(md))
    r = FB.review(md, repo)
    rv, text = r["review"], r["message"]
    assert sorted(it["name"] for it in rv["items"]) == sorted(_MEMS)
    assert [(x["name"], x["proposed"].split(":")[0]) for x in rv["rest"]] == [
        ("fresh_fact", "wait"), ("sprint_round", "keep it")]
    tail = text.split("Not in this batch", 1)[1]
    assert "floor:diet:fresh_fact" in tail and "floor:diet:sprint_round" in tail
    assert "hippo tend apply <id> --verdict demote" in tail
    out = _apply(md, repo, rv["digest"])
    assert out["ok"] and sorted(out["applied"]) == sorted(_MEMS) and out["skipped"] == []
    assert "](fresh_fact.md)" in _floor(md) and "](sprint_round.md)" in _floor(md)
    # Neither can be reached through the batch, even by name.
    rv2 = FB.review(md, repo)["review"]
    assert rv2["items"] == [] and rv2["digest"] is None and len(rv2["rest"]) == 2
    # Demoting one stays a single explicit verdict, as now.
    assert TD.apply("floor:diet:fresh_fact", "demote", memory_dir=md, repo_root=repo)["ok"]


# --------------------------------------------------------------------------- #
# The surfaces: CLI and the tend tool
# --------------------------------------------------------------------------- #
def test_the_cli_reviews_and_applies(tmp_path, monkeypatch, capsys):
    md, repo = _reachable(tmp_path, monkeypatch)
    where = ["--memory-dir", md, "--repo-root", repo]
    assert TD.main(["diet", *where]) == 0
    out = capsys.readouterr().out
    rv = FB.build_review(md, repo)
    assert f"digest: {rv['digest']}" in out and "Nothing is demoted until that runs" in out
    assert TD.main(["show", "floor:diet", *where]) == 0  # the batch's id shows the same review
    assert f"digest: {rv['digest']}" in capsys.readouterr().out
    assert TD.main(["apply", "floor:diet", "--verdict", "demote", *where]) == 1  # no digest
    assert "refused" in capsys.readouterr().out
    assert TD.main(["apply", "floor:diet", "--verdict", "done", "--digest", rv["digest"], *where]) == 1
    capsys.readouterr()
    keep = rv["items"][0]["name"]
    assert TD.main(["apply", "floor:diet", "--verdict", "demote", "--digest", rv["digest"],
                    "--except", keep, *where]) == 0
    out = capsys.readouterr().out
    assert f"{len(rv['items']) - 1} demoted" in out and f"left out at your word: floor:diet:{keep}" in out


def test_a_single_diet_item_points_at_the_review(tmp_path, monkeypatch, capsys):
    md, repo = _reachable(tmp_path, monkeypatch)
    assert any("hippo tend diet" in ln for ln in TD._verdict_help("floor", "floor:diet:x"))
    assert not any("hippo tend diet" in ln for ln in TD._verdict_help("floor", "floor:x.md"))
    # Doctor starts the diet at the review: `hippo tend next --kind floor` serves the floor
    # kind's other items first, and one line at a time.
    from memory.doctor_checks_env import DoctorContext

    line = FD.check_floor_budget(DoctorContext(md, repo))
    assert line["status"] == "warn" and "Start the diet: `hippo tend diet`" in line["message"]


def test_the_tend_tool_reviews_and_applies(tmp_path, monkeypatch):
    from memory import mcp_tools_tend as MT
    from memory.mcp_schemas import _TOOLS
    from memory.mcp_schemas_v2 import V2_TOOLS

    md, repo = _reachable(tmp_path, monkeypatch)
    monkeypatch.setattr(MT, "_corpus_gate", lambda *a: (None, md, repo))
    text = MT._tool_tend({"action": "diet"})
    rv = FB.build_review(md, repo)
    assert f"digest: {rv['digest']}" in text and "action='apply'" in text
    assert "refused" in MT._tool_tend({"action": "apply", "id": "floor:diet", "verdict": "demote"})
    keep = rv["items"][0]["name"]
    out = MT._tool_tend({"action": "apply", "id": "floor:diet", "verdict": "demote",
                         "digest": rv["digest"], "except": [keep]})
    assert f"{len(rv['items']) - 1} demoted" in out and keep in out
    for tools in (_TOOLS, V2_TOOLS):  # both schemas offer it
        schema = next(t for t in tools if t["name"] == "tend")["inputSchema"]["properties"]
        assert "diet" in schema["action"]["enum"] and "digest" in schema and "except" in schema


def test_the_review_is_recorded_in_the_tend_state_and_survives_a_snooze(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    rv = FB.review(md, repo)["review"]
    rec = Q.read_state(md)["review"][FB.BATCH_ID]
    assert rec["digest"] == rv["digest"] and rec["names"] == [it["name"] for it in rv["items"]]
    assert rec["floor_sha256"] == hashlib.sha256(_floor(md).encode("utf-8")).hexdigest()
    assert "MEMORY" not in json.dumps(rec) and "](" not in json.dumps(rec)  # names and hashes, no floor text
    other = [i for i in _diet_ids(md, repo)][-1]
    assert TD.snooze(other, 1, memory_dir=md, repo_root=repo)["ok"]  # another tend-state write
    assert Q.read_state(md)["review"][FB.BATCH_ID]["digest"] == rv["digest"]


def test_a_review_that_cannot_be_recorded_prints_no_digest(tmp_path, monkeypatch):
    md, repo = _reachable(tmp_path, monkeypatch)
    monkeypatch.setattr(TD, "_write_state", lambda *_a: False)
    r = FB.review(md, repo)
    assert r["ok"] and r["review"]["digest"] is None
    assert "could not be recorded" in r["message"] and "--digest" not in r["message"]


# --------------------------------------------------------------------------- #
# Outbox mode: the floor copy, never the working tree
# --------------------------------------------------------------------------- #
def test_the_batch_in_outbox_mode_edits_the_outbox_floor_copy(repo, memory_dir, monkeypatch, tmp_path):
    from memory import outbox as OB
    from memory import policy_consent as PC
    from memory import trust as T

    from .conftest import git_commit

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    for n, (desc, mtype, _sec) in _MEMS.items():
        write_file(memory_dir, f"{n}.md", _mem(n, desc, mtype))
    from .test_floor_diet import _floor as _seed_floor

    write_file(memory_dir, "MEMORY.md", _seed_floor(tuple(_MEMS)))
    write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox", "budgets": {"floor_bytes": 800}}))
    write_file(repo, ".gitignore", ".hippo/\n.claude/memory.local/\n.claude/memory.outbox/\n")
    git_commit(repo, "seed", int(time.time()) - 40 * 86400)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    committed = open(os.path.join(memory_dir, "MEMORY.md"), "rb").read()
    rv = FB.review(memory_dir, repo)["review"]
    assert len(rv["items"]) >= 2
    r = _apply(memory_dir, repo, rv["digest"])
    assert r["ok"] and r["skipped"] == [] and "outbox" in r["message"], r
    assert subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    assert open(os.path.join(memory_dir, "MEMORY.md"), "rb").read() == committed
    copy = open(os.path.join(OB.outbox_dir(memory_dir), "MEMORY.md"), encoding="utf-8").read()
    assert not any(f"]({it['name']}.md)" in copy for it in rv["items"])
    assert len(copy.encode("utf-8")) == rv["after"]
