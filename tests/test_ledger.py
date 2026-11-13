"""LDG-1: the external-ledger read-time join.

The declaration's bounds (path inside the repo, a vetted ``id_re``, plain keys), the
line-window scan of a committed tracker mirror, the derived cache (trust-gated, dated,
rebuilt only when its inputs change, a ``prev`` map kept), and the render: recall rows
that cite a known id gain `` · <ID> <status> (snapshot <date>)`` in the prompt hook, in
``hippo recall`` and in the recall view, at most two ids per row, while a corpus with no
cache stays byte-identical. Fixtures are written from scratch; BM25-only.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import build_index as B
from memory import doctor as D
from memory import ledger as LG
from memory.recall_hook import run_recall
from memory.recall_view import describe

from .conftest import write_file

# A tracker mirror shaped like a generated snapshot: comment headers, a meta block, list
# items carrying `- id:`, the status under `state:` at the entry's key column, nested keys
# with their own `state:` deeper in, and a later section whose items carry other ids.
_SNAPSHOT = """\
# generated mirror — do not edit
meta:
  generated: 2026-01-01
issues:
  - id: ABC-1
    title: first thing
    state: Done
    gates:
      - gate: one
        state: Blocked
  - id: ABC-2
    title: second thing
    # a comment between keys
    state: "In Progress"
  - id: ABC-3
    title: no state at all
  - id: ABC-4
    labels:
      - owner: x
    state: Todo   # trailing comment
  - id: ABC-5
    state: "a status that runs well past the forty character cap"
  - id: ABC-6
    state: "Bad\x07\x1b[31mred"
  - id: ABC-1
    state: Duplicate
projects:
  - id: 1d2c3b4a
    state: Started
  - name: other
    state: Paused
"""

_DECL = {"name": "tracker", "path": "TRACKER.yaml", "id_re": r"\bABC-\d+\b",
         "entry_key": "id", "status_key": "state"}


def _git(repo, *args, env=None):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={**os.environ, **(env or {})})


def _mem(name, desc, kind="project"):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {kind}\n---\n{desc}\n'


def _policy(memory_dir, policy):
    write_file(memory_dir, "hippo.json", json.dumps(policy))


@pytest.fixture
def tracker(repo, memory_dir, monkeypatch, tmp_path):
    """A git repo with a committed snapshot (commit date 2026-03-04), a small corpus and a
    built BM25 index. Returns ``(memory_dir, index_dir)``."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    write_file(repo, "TRACKER.yaml", _SNAPSHOT)
    write_file(memory_dir, "MEMORY.md", "# Memory Index\n\n## User\n")
    write_file(memory_dir, "canary_rollout.md", _mem("canary_rollout", "Canary rollout plan tracked in ABC-2 and ABC-1, then ABC-4"))
    write_file(memory_dir, "billing_cutover.md", _mem("billing_cutover", "Billing cutover checklist for invoices"))
    write_file(memory_dir, "orphan_issue.md", _mem("orphan_issue", "Orchard pruning schedule from ABC-999"))
    _git(repo, "add", "-A")
    stamp = "2026-03-04T10:00:00+00:00"
    _git(repo, "commit", "-qm", "seed", env={"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp})
    idx = str(tmp_path / "idx")
    B.build_index(memory_dir, idx)
    return memory_dir, idx


# --------------------------------------------------------------------------- #
# the declaration's bounds
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pattern", [r"\bABC-\d+\b", r"[A-Z]{2,5}-\d+", r"#\d+", r"(?:GRO|ENG)-\d+"])
def test_plain_id_patterns_pass_the_vetting(pattern):
    assert LG.vet_pattern(pattern) is None


@pytest.mark.parametrize(
    "pattern,why",
    [
        (r"(a+)+b", "nests one repetition"),
        (r"(\w*)*x", "nests one repetition"),
        (r"(a|aa)*b", "repeats an alternation"),
        (r"(\w)\1", "backreference or a lookaround"),
        (r"(?=ABC)ABC-\d+", "backreference or a lookaround"),
        (r"(?<!x)ABC-\d+", "backreference or a lookaround"),
        (r"\d+\d+\d+\d+x", "more than 3 repeats"),
        ("A" * 121, "longer than 120"),
        (r"ABC-(\d+", "does not compile"),
        ("", "not a non-empty string"),
        (7, "not a non-empty string"),
    ],
)
def test_risky_or_broken_id_patterns_are_refused(pattern, why):
    assert why in (LG.vet_pattern(pattern) or "")


def test_a_ledger_path_must_stay_inside_the_repo(repo, tmp_path):
    write_file(repo, "ok.yaml", "x")
    outside = write_file(str(tmp_path), "outside.yaml", "secret")
    os.symlink(outside, os.path.join(repo, "link.yaml"))
    os.makedirs(os.path.join(repo, "sub"))
    os.symlink(str(tmp_path), os.path.join(repo, "sub", "up"))
    assert LG.resolve_path(repo, "ok.yaml")[0] == os.path.realpath(os.path.join(repo, "ok.yaml"))
    assert "absolute" in LG.resolve_path(repo, outside)[1]
    assert "absolute" in LG.resolve_path(repo, "~/x.yaml")[1]
    assert "climbs out" in LG.resolve_path(repo, "../outside.yaml")[1]
    assert "climbs out" in LG.resolve_path(repo, "sub/../../outside.yaml")[1]
    assert "outside the repo" in LG.resolve_path(repo, "link.yaml")[1]
    assert "outside the repo" in LG.resolve_path(repo, "sub/up/outside.yaml")[1]
    assert "not a readable file" in LG.resolve_path(repo, "missing.yaml")[1]
    assert "not a readable file" in LG.resolve_path(repo, "sub")[1]
    assert "non-empty string" in LG.resolve_path(repo, None)[1]


def test_read_declaration_keeps_good_ledgers_and_names_every_dropped_one(repo, memory_dir):
    write_file(repo, "TRACKER.yaml", _SNAPSHOT)
    _policy(memory_dir, {"ledgers": [
        _DECL,
        {**_DECL, "name": "bad-re", "id_re": "(a+)+"},
        {**_DECL, "name": "escape", "path": "../x.yaml"},
        {**_DECL, "name": "keys", "status_key": "state: x"},
        {**_DECL},  # a repeated name
        "not an object",
    ]})
    ledgers, dropped = LG.read_declaration(memory_dir, repo)
    assert [lg["name"] for lg in ledgers] == ["tracker"]
    assert ledgers[0]["entry_key"] == "id" and ledgers[0]["status_key"] == "state"
    reasons = {d["name"]: d["reason"] for d in dropped}
    assert "nests one repetition" in reasons["bad-re"]
    assert "climbs out" in reasons["escape"]
    assert "status_key" in reasons["keys"]
    assert len(dropped) == 5
    _policy(memory_dir, {"ledgers": {"name": "x"}})
    assert LG.read_declaration(memory_dir, repo) == ([], [{"name": "ledgers", "reason": "ledgers is not a list"}])


def test_entry_and_status_keys_default_to_id_and_status(repo, memory_dir):
    write_file(repo, "T.yaml", "- id: ABC-7\n  status: Open\n")
    _policy(memory_dir, {"ledgers": [{"name": "t", "path": "T.yaml", "id_re": r"ABC-\d+"}]})
    (lg,), _ = LG.read_declaration(memory_dir, repo)
    assert LG.scan_ledger(lg["abs"], lg["id_re"], lg["entry_key"], lg["status_key"]) == {"ABC-7": "Open"}


# --------------------------------------------------------------------------- #
# the line-window scan
# --------------------------------------------------------------------------- #
def test_the_line_window_scan_reads_only_entry_and_status_lines(tmp_path):
    path = write_file(str(tmp_path), "T.yaml", _SNAPSHOT)
    got = LG.scan_ledger(path, r"\bABC-\d+\b", "id", "state")
    assert got == {
        "ABC-1": "Done",  # the nested gate's own state is deeper in, so never read
        "ABC-2": "In Progress",
        "ABC-4": "Todo",
        "ABC-5": "a status that runs well past the forty c",
        "ABC-6": "Bad [31mred",
    }
    assert "ABC-3" not in got  # no status line in its window: nothing to render
    assert all(len(v) <= LG.STATUS_CHARS for v in got.values())
    # the later section's items close ABC-6's window; their ids do not match id_re
    assert set(got) <= {f"ABC-{i}" for i in range(1, 7)}


def test_an_id_must_fully_match_the_pattern(tmp_path):
    path = write_file(str(tmp_path), "T.yaml", "- id: ABC-1x\n  state: Done\n- id: xABC-2\n  state: Done\n")
    assert LG.scan_ledger(path, r"ABC-\d+", "id", "state") == {}


def test_an_unreadable_or_empty_ledger_is_dropped_by_the_build(repo, memory_dir, tmp_path):
    write_file(repo, "T.yaml", "nothing: here\n")
    _policy(memory_dir, {"ledgers": [{**_DECL, "path": "T.yaml"}]})
    assert LG.refresh_ledger_cache(memory_dir, str(tmp_path / "idx")) is True
    doc = json.load(open(LG.cache_path(str(tmp_path / "idx"))))
    assert doc["ledgers"] == [] and doc["by_state"] == []
    assert doc["dropped"] == [{"name": "tracker", "reason": "no entry matched the declared keys and id_re"}]


# --------------------------------------------------------------------------- #
# the derived cache
# --------------------------------------------------------------------------- #
def test_no_declaration_means_no_cache_and_an_old_one_is_removed(tracker, tmp_path):
    md, idx = tracker
    assert LG.refresh_ledger_cache(md, idx) is None
    assert not os.path.exists(LG.cache_path(idx))
    _policy(md, {"ledgers": [_DECL]})
    assert LG.refresh_ledger_cache(md, idx) is True and os.path.exists(LG.cache_path(idx))
    os.remove(os.path.join(md, "hippo.json"))
    assert LG.refresh_ledger_cache(md, idx) is None
    assert not os.path.exists(LG.cache_path(idx))


def test_an_untrusted_corpus_reads_no_ledger_and_writes_no_cache(tracker, monkeypatch):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    calls = []
    monkeypatch.setattr(LG, "scan_ledger", lambda *a: calls.append(a) or {})
    assert LG.refresh_ledger_cache(md, idx) is None
    assert not calls and not os.path.exists(LG.cache_path(idx))


def test_the_cache_holds_ids_dated_by_the_last_commit_of_the_file(tracker):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    assert LG.refresh_ledger_cache(md, idx) is True
    cache = LG.load(idx)
    assert cache.status("ABC-2") == ("In Progress", "2026-03-04", "tracker")
    assert cache.status("ABC-3") is None and cache.status("ABC-999") is None
    assert "tracker: 5 ids, snapshot 2026-03-04" == LG.summary(idx)
    doc = json.load(open(LG.cache_path(idx)))
    # the hook-read shape: one id string per status, not a per-id map (cheaper to parse)
    assert "ids" not in doc and " ABC-2 " in doc["by_state"][doc["states"].index(["In Progress", 0])]
    assert set(cache.id_map()) == {"ABC-1", "ABC-2", "ABC-4", "ABC-5", "ABC-6"}


def test_outside_git_the_snapshot_date_is_the_files_mtime(tmp_path, monkeypatch):
    top = str(tmp_path / "plain")
    md = os.path.join(top, ".claude", "memory")
    os.makedirs(md)
    path = write_file(top, "TRACKER.yaml", _SNAPSHOT)
    os.utime(path, (1767225600, 1767225600))  # 2026-01-01T00:00:00Z
    _policy(md, {"ledgers": [_DECL]})
    idx = str(tmp_path / "idx")
    assert LG.refresh_ledger_cache(md, idx) is True
    assert LG.load(idx).status("ABC-1") == ("Done", "2026-01-01", "tracker")


def test_unchanged_inputs_skip_the_rebuild_and_a_status_change_lands_in_prev(tracker, repo, monkeypatch):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    assert LG.refresh_ledger_cache(md, idx) is True
    calls = []
    real = LG.scan_ledger
    monkeypatch.setattr(LG, "scan_ledger", lambda *a: calls.append(1) or real(*a))
    assert LG.refresh_ledger_cache(md, idx) is True
    assert calls == []  # the signature matched: no re-read of the ledger
    write_file(repo, "TRACKER.yaml", _SNAPSHOT.replace("state: Done", "state: Canceled", 1))
    assert LG.refresh_ledger_cache(md, idx) is True and calls == [1]
    doc = json.load(open(LG.cache_path(idx)))
    assert doc["prev"] == {"ABC-1": "Done"}
    write_file(repo, "TRACKER.yaml", _SNAPSHOT.replace("state: Done", "state: Canceled", 1) + "\n")
    assert LG.refresh_ledger_cache(md, idx) is True
    assert json.load(open(LG.cache_path(idx)))["prev"] == {"ABC-1": "Done"}  # kept until the next change


def test_the_hot_path_reads_the_json_once_per_process(tracker, monkeypatch):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    LG.refresh_ledger_cache(md, idx)
    LG._MEMO.clear()
    reads = []
    real = LG._read_json
    monkeypatch.setattr(LG, "_read_json", lambda p: reads.append(p) or real(p))
    first = LG.load(idx)
    assert LG.load(idx) is first and len(reads) == 1


def test_bounded_scan_skips_long_tokens_and_caps_the_text(tracker):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    LG.refresh_ledger_cache(md, idx)
    cache = LG.load(idx)
    assert cache.find_ids("see ABC-2, (ABC-1) and ABC-2.") == ["ABC-2", "ABC-1"]
    assert cache.find_ids("x" * 200 + "ABC-1") == []  # one token over 128 chars
    assert cache.find_ids("pad " * 300 + "ABC-1") == []  # past the 1,000-char row cap
    assert cache.suffix("ABC-999 ABC-4 ABC-2 ABC-1") == (
        " · ABC-4 Todo (snapshot 2026-03-04) · ABC-2 In Progress (snapshot 2026-03-04)"
    )


# --------------------------------------------------------------------------- #
# the render
# --------------------------------------------------------------------------- #
def _hook(md, idx, prompt="canary rollout plan", session="s1"):
    return run_recall(prompt, session_id=session, k=10, memory_dir=md, index_dir=idx, hook=True)


def test_without_a_cache_the_hook_block_is_unchanged_and_costs_one_stat(tracker, monkeypatch):
    md, idx = tracker
    before = _hook(md, idx, session="a")
    assert "canary_rollout" in before and "snapshot" not in before
    monkeypatch.setattr(LG, "_read_json", lambda p: pytest.fail("no cache file: nothing to read"))
    real_stat, seen = os.stat, []

    def stat(p, *a, **kw):
        if str(p).endswith(LG.CACHE_NAME):
            seen.append(p)
        return real_stat(p, *a, **kw)

    monkeypatch.setattr(LG.os, "stat", stat)
    assert _hook(md, idx, session="b") == before
    assert len(seen) == 1


def test_a_row_citing_known_ids_renders_their_status_with_the_date(tracker):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    LG.refresh_ledger_cache(md, idx)
    block = _hook(md, idx)
    line = next(ln for ln in block.splitlines() if ln.startswith("  • canary_rollout"))
    # at most two ids per row, in order of appearance; ABC-4 is the third and stays out
    assert line.endswith(
        " · ABC-2 In Progress (snapshot 2026-03-04) · ABC-1 Done (snapshot 2026-03-04)"
    )
    plain = _hook(md, idx, prompt="Billing cutover checklist for invoices", session="s2")
    assert "billing_cutover" in plain and "snapshot" not in plain
    unknown = _hook(md, idx, prompt="Orchard pruning schedule", session="s3")
    assert "orphan_issue" in unknown and "snapshot" not in unknown  # ABC-999 is not in the ledger


def test_hippo_recall_and_the_recall_view_render_the_same_suffix(tracker, capsys):
    md, idx = tracker
    _policy(md, {"ledgers": [_DECL]})
    LG.refresh_ledger_cache(md, idx)
    cli = run_recall("canary rollout plan", k=10, memory_dir=md, index_dir=idx, hook=False)
    assert "· ABC-2 In Progress (snapshot 2026-03-04)" in cli
    view = describe("canary rollout plan", memory_dir=md, index_dir=idx)
    bullet = next(ln for ln in view.splitlines() if ln.startswith("  • canary_rollout"))
    assert bullet.endswith("· ABC-2 In Progress (snapshot 2026-03-04) · ABC-1 Done (snapshot 2026-03-04)")


def test_build_index_prints_the_ledger_line_only_when_declared(tracker, capsys):
    md, idx = tracker
    B.main(["--memory-dir", md, "--index-dir", idx])
    assert "ledger join" not in capsys.readouterr().out
    _policy(md, {"ledgers": [_DECL]})
    B.main(["--memory-dir", md, "--index-dir", idx])
    assert "ledger join   : tracker: 5 ids, snapshot 2026-03-04" in capsys.readouterr().out
    assert LG.load(idx).status("ABC-1")[0] == "Done"


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def _ctx(md, repo):
    from memory.doctor_checks_env import DoctorContext

    return DoctorContext(md, repo, plugin_data="", plugin_root="")


def test_doctor_prints_nothing_for_a_corpus_without_ledgers(tracker, repo):
    md, _idx = tracker
    assert D.check_ledger(_ctx(md, repo)) is None
    assert [label for label, _ in D.CHECKS].count("ledger_join") == 1


def test_doctor_names_ids_held_the_date_and_every_dropped_ledger(tracker, repo, monkeypatch):
    md, _idx = tracker
    monkeypatch.delenv("HIPPO_INDEX_DIR", raising=False)
    _policy(md, {"ledgers": [_DECL]})
    r = D.check_ledger(_ctx(md, repo))
    assert r["status"] == "ok" and "no cache yet" in r["message"]
    LG.refresh_ledger_cache(md, B.default_index_dir(md))
    r = D.check_ledger(_ctx(md, repo))
    assert r["status"] == "ok"
    assert "tracker (TRACKER.yaml): 5 ids in the cache, snapshot 2026-03-04" in r["message"]
    _policy(md, {"ledgers": [_DECL, {**_DECL, "name": "loose", "id_re": "(x+)+"}]})
    r = D.check_ledger(_ctx(md, repo))
    assert r["status"] == "warn" and "loose dropped: id_re nests one repetition" in r["message"]
    assert "\n" not in r["message"]
