"""GP-S4: ``hippo recall --for-issue <ID>`` and the issue join that admits a journal.

The verb is the read-only twin of ``--for-diff``: a whole-token join of one tracker id over
the corpus, under the id's status from LDG-1's ledger cache. The hook admission lets a
journal past HOT-7's lane when it cites an id the prompt names, through a reverse map built
with the cache, and records why it was admitted. Fixtures are written from scratch;
BM25-only.
"""

from __future__ import annotations

import json
import os

import pytest

from memory import build_index as B
from memory import lanes as L
from memory import ledger as LG
from memory import recall_issue as RI
from memory.recall_hook import main as recall_main, run_recall
from memory.telemetry_rollup import read_rollups, summarize

from .conftest import write_file
from .test_ledger import _DECL, _SNAPSHOT, _git, _mem, _policy


def _tree(root):
    out = {}
    for d, _subs, files in os.walk(root):
        if ".git" in d.split(os.sep):
            continue
        for f in files:
            p = os.path.join(d, f)
            out[p] = open(p, "rb").read()
    return out


@pytest.fixture
def corpus(repo, memory_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    write_file(repo, "TRACKER.yaml", _SNAPSHOT)
    write_file(memory_dir, "MEMORY.md", "# Memory Index\n\n## User\n")
    write_file(memory_dir, "canary_plan.md", _mem("canary_plan", "Canary rollout plan for ABC-2"))
    write_file(memory_dir, "deploy_lesson.md",
               _mem("deploy_lesson", "Always stage a deploy first", "feedback")
               + "\nLearned on ABC-2.\nAnd again (ABC-2), see ABC-21.\n")
    write_file(memory_dir, "near_miss.md", _mem("near_miss", "Mentions ABC-21 and xABC-2 and ABC-2x only"))
    write_file(memory_dir, "fm_only.md",
               '---\nname: fm_only\ndescription: "Unrelated note"\nrelated: ABC-2\n---\nbody\n')
    write_file(memory_dir, "orchard-round.md",
               _mem("orchard-round", "Round notes on pruning the orchard trees") + "\nTracked as ABC-4.\n")
    _git(repo, "add", "-A")
    stamp = "2026-03-04T10:00:00+00:00"
    _git(repo, "commit", "-qm", "seed", env={"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp})
    idx = str(tmp_path / "idx")
    B.build_index(memory_dir, idx)
    return memory_dir, idx


def _cli(argv, capsys):
    rc = recall_main(argv)
    return rc, capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the verb
# --------------------------------------------------------------------------- #
def test_without_a_ledger_the_verb_says_so_and_exits_0(corpus, capsys):
    md, idx = corpus
    rc, out = _cli(["--for-issue", "ABC-2", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 0
    assert out.strip() == "No ledger is declared in this corpus's hippo.json, so there is no issue id to join on."
    rc, out = _cli(["--for-issue", "ABC-2", "--json", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 0 and json.loads(out) == {"id": "ABC-2", "declared": False, "items": []}


def test_an_id_no_pattern_matches_is_refused_with_the_pattern_named(corpus, capsys):
    md, idx = corpus
    _policy(md, {"ledgers": [_DECL]})
    rc, out = _cli(["--for-issue", "GRO-2", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 2 and r"\bABC-\d+\b" in out and "'GRO-2'" in out


def test_the_join_is_whole_token_with_where_and_the_status_header(corpus, repo, capsys):
    md, idx = corpus
    _policy(md, {"ledgers": [_DECL]})
    assert LG.refresh_ledger_cache(md, idx) is True
    before = _tree(repo)
    rc, out = _cli(["--for-issue", "abc-2", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 0
    lines = out.strip().splitlines()
    assert lines[0] == "ABC-2 In Progress (snapshot 2026-03-04) — 2 memory(ies) cite it:"
    assert lines[1].startswith("- deploy_lesson [feedback] — Always stage a deploy first")
    assert lines[1].endswith("(cites it in: line 9, line 10)")
    assert lines[2].startswith("- canary_plan [project]") and "(cites it in: description, line 7)" in lines[2]
    assert len(lines) == 3  # near_miss (ABC-21, xABC-2, ABC-2x) and fm_only never match
    assert _tree(repo) == before  # read-only: no index, cache or telemetry write
    rc, out = _cli(["--for-issue", "ABC-2", "--json", "--cap", "1", "--memory-dir", md, "--index-dir", idx], capsys)
    doc = json.loads(out)
    assert (doc["status"], doc["as_of"], doc["ledger"], doc["total"]) == ("In Progress", "2026-03-04", "tracker", 2)
    assert [it["name"] for it in doc["items"]] == ["deploy_lesson"]


def test_the_header_without_a_cache_or_for_an_id_outside_the_snapshot(corpus, capsys):
    md, idx = corpus
    _policy(md, {"ledgers": [_DECL]})
    rc, out = _cli(["--for-issue", "ABC-21", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 0 and out.startswith("ABC-21, status unknown (no ledger cache yet")
    LG.refresh_ledger_cache(md, idx)
    rc, out = _cli(["--for-issue", "ABC-21", "--memory-dir", md, "--index-dir", idx], capsys)
    assert out.startswith("ABC-21, not in the ledger snapshot (snapshot 2026-03-04) — 2 memory(ies)")
    rc, out = _cli(["--for-issue", "ABC-777", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 0 and out.strip().endswith("— no memory cites it.")


def test_every_dropped_ledger_is_one_plain_line(corpus, capsys):
    md, idx = corpus
    _policy(md, {"ledgers": [{**_DECL, "path": "../out.yaml"}]})
    rc, out = _cli(["--for-issue", "ABC-2", "--memory-dir", md, "--index-dir", idx], capsys)
    assert rc == 0 and out.startswith("Every ledger this corpus declares was dropped (tracker: path climbs")


# --------------------------------------------------------------------------- #
# the issue join in the prompt hook
# --------------------------------------------------------------------------- #
_PROMPT = "what did we decide about pruning the orchard trees in ABC-4"


def _hook(md, idx, prompt=_PROMPT, session="s1"):
    return run_recall(prompt, session_id=session, k=10, memory_dir=md, index_dir=idx, hook=True)


def _events(tmp_path):
    p = tmp_path / "tel" / "recall_events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_a_journal_citing_a_named_issue_is_admitted_and_counted_by_cause(corpus, tmp_path):
    md, idx = corpus
    _policy(md, {"journals": ["*-round.md"]})
    B.build_index(md, idx)
    assert "orchard-round" not in L.rendered_names(_hook(md, idx, session="a"))  # HOT-7 withholds it
    _policy(md, {"journals": ["*-round.md"], "ledgers": [_DECL]})
    LG.refresh_ledger_cache(md, idx)
    block = _hook(md, idx, session="b")
    assert "orchard-round" in L.rendered_names(block)
    assert "· ABC-4 Todo (snapshot 2026-03-04)" not in block  # the row's own text cites nothing
    ev = _events(tmp_path)[-1]
    assert ev["admitted"] == {"orchard-round": "issue"}
    k = summarize(read_rollups(str(tmp_path / "tel"), days=30))
    assert k["journal_admitted"] == {"issue": 1}
    # the prompt naming the journal itself is still the first cause
    _hook(md, idx, prompt="orchard-round pruning notes for ABC-4", session="c")
    assert _events(tmp_path)[-1]["admitted"] == {"orchard-round": "named"}


def test_admission_is_not_a_force_in_an_unrelated_journal_still_has_to_rank(corpus):
    md, idx = corpus
    _policy(md, {"journals": ["*-round.md"], "ledgers": [_DECL]})
    B.build_index(md, idx)
    LG.refresh_ledger_cache(md, idx)
    block = _hook(md, idx, prompt="canary rollout plan for ABC-4", session="z")
    assert "orchard-round" not in L.rendered_names(block)


def test_no_ledger_admits_nothing_and_never_reads_the_cache(corpus, monkeypatch):
    md, idx = corpus
    assert L.issue_join_admissions(_PROMPT) == {}
    assert L.issue_join_admissions(_PROMPT, None) == {}
    req = L.LaneRequest(_PROMPT, 10)
    assert req.issue() == {} and req.join is None


def test_the_reverse_map_is_bounded_per_id_and_follows_journal_edits(corpus):
    md, idx = corpus
    for i in range(10):
        write_file(md, f"r{i:02d}-round.md", _mem(f"r{i:02d}-round", f"Round {i} on ABC-1"))
    _policy(md, {"journals": ["*-round.md"], "ledgers": [_DECL]})
    LG.refresh_ledger_cache(md, idx)
    cache = LG.load(idx)
    got = cache.journals_for("about ABC-1 please")
    assert len(got) == LG.JOURNALS_PER_ID and set(got.values()) == {"ABC-1"}
    assert "r09-round" in got and "r00-round" not in got  # later names first on a tie
    assert cache.journals_for("ABC-4") == {"orchard-round": "ABC-4"}
    assert cache.journals_for("nothing named") == {}
    write_file(md, "orchard-round.md", _mem("orchard-round", "Round notes, no issue any more"))
    LG.refresh_ledger_cache(md, idx)
    assert LG.load(idx).journals_for("ABC-4") == {}  # the signature saw the journal change
    _policy(md, {"ledgers": [_DECL]})  # journals undeclared: the lanes do not act, no map
    LG.refresh_ledger_cache(md, idx)
    assert "cites" not in json.load(open(LG.cache_path(idx)))
