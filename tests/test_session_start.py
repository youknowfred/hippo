"""Tests for memory/session_start.py — the SessionStart dispatcher.

Dispatcher logic (merge / bound / suppress / isolate / JSON) is tested by stubbing the
producer set, so these tests don't depend on git timing (that's covered in test_staleness).
The LIF-1 staleness-producer tests stub ``find_stale`` the same way but write REAL memory
files for the ``invalid_after`` read — still no git timing anywhere.
"""

from __future__ import annotations

import datetime
import json
import os

import memory.session_start as S
from memory.staleness import set_invalid_after

from .conftest import git_commit, write_file


def _producers(monkeypatch, producers):
    monkeypatch.setattr(S, "PRODUCERS", producers)


def test_build_context_merges_producer_blocks(monkeypatch):
    _producers(
        monkeypatch,
        [
            ("a", lambda md, repo, ctx=None: "ALPHA block"),
            ("b", lambda md, repo, ctx=None: "BETA block"),
        ],
    )
    ctx = S.build_context("md", "repo")
    assert "ALPHA block" in ctx and "BETA block" in ctx


def test_build_context_empty_when_nothing_to_say(monkeypatch):
    _producers(monkeypatch, [("a", lambda md, repo, ctx=None: None)])
    assert S.build_context("md", "repo") == ""


def test_producer_exception_is_isolated(monkeypatch):
    def boom(md, repo, ctx=None):
        raise RuntimeError("producer failed")

    _producers(monkeypatch, [("boom", boom), ("ok", lambda md, repo, ctx=None: "still here")])
    ctx = S.build_context("md", "repo")
    assert "still here" in ctx  # the survivor is kept (isolation)...
    assert "boom" in ctx  # ...and the failure is NAMED, not swallowed (RCH-9)


def test_output_is_bounded_under_cap(monkeypatch):
    _producers(monkeypatch, [("big", lambda md, repo, ctx=None: "x" * 50_000)])
    ctx = S.build_context("md", "repo", max_chars=500)
    assert len(ctx) <= 500
    assert ctx.endswith("(truncated)")


# --------------------------------------------------------------------------- #
# Desktop surface note — typed /hippo:* advice gains ONE mapping footer on the
# Claude Desktop app (CLAUDE_CODE_ENTRYPOINT=claude-desktop) and nowhere else;
# terminal bytes stay byte-identical (conftest strips the ambient entrypoint).
# --------------------------------------------------------------------------- #


def test_terminal_output_carries_no_surface_note(monkeypatch):
    _producers(monkeypatch, [("a", lambda md, repo, ctx=None: "Run /hippo:doctor to review.")])
    assert S.build_context("md", "repo") == "Run /hippo:doctor to review."


def test_desktop_appends_the_mapping_note_after_the_signal(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    _producers(monkeypatch, [("a", lambda md, repo, ctx=None: "Run /hippo:doctor to review.")])
    ctx = S.build_context("md", "repo")
    assert ctx.startswith("Run /hippo:doctor to review.")
    assert ctx.endswith(S._DESKTOP_SURFACE_NOTE)
    assert "terminal-only" in ctx and "trust_corpus" in ctx


def test_desktop_note_only_when_a_typed_command_is_named(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    _producers(monkeypatch, [("a", lambda md, repo, ctx=None: "plain block, no commands")])
    assert S.build_context("md", "repo") == "plain block, no commands"


def test_non_desktop_entrypoints_stay_on_terminal_wording(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    _producers(monkeypatch, [("a", lambda md, repo, ctx=None: "Run /hippo:doctor to review.")])
    assert "Surface note" not in S.build_context("md", "repo")


def test_desktop_note_never_pushes_output_past_the_cap(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    _producers(
        monkeypatch,
        [("big", lambda md, repo, ctx=None: "run /hippo:doctor " + "x" * 50_000)],
    )
    ctx = S.build_context("md", "repo", max_chars=2000)
    assert len(ctx) <= 2000
    assert "…(truncated)" in ctx
    assert ctx.endswith(S._DESKTOP_SURFACE_NOTE)  # the note survives whole, at the end


def test_desktop_note_dropped_when_cap_cannot_carry_both(monkeypatch):
    # A cap too small to hold the note AND a useful signal keeps the signal, whole —
    # the note is never itself truncated into garbage.
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    _producers(
        monkeypatch,
        [("big", lambda md, repo, ctx=None: "run /hippo:doctor " + "x" * 50_000)],
    )
    ctx = S.build_context("md", "repo", max_chars=500)
    assert len(ctx) <= 500
    assert "Surface note" not in ctx


def test_untrusted_nudge_gains_the_note_on_desktop(monkeypatch):
    # The SEC-1 short-circuit path returns ONLY the untrusted nudge — its /hippo:doctor
    # advice must map on the Desktop surface too.
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    import memory.trust as trust

    monkeypatch.setattr(trust, "gate_repo_root", lambda md, rr: "/gated/root")
    monkeypatch.setattr(trust, "is_trusted", lambda root: False)
    monkeypatch.setattr(
        S, "untrusted_corpus_nudge", lambda md, rr: "Run /hippo:doctor to trust it."
    )
    ctx = S.build_context("md", "repo")
    assert ctx.startswith("Run /hippo:doctor to trust it.")
    assert ctx.endswith(S._DESKTOP_SURFACE_NOTE)


def test_dispatcher_calls_every_producer_with_the_shared_run_context(monkeypatch):
    """LIF-6: the PRODUCERS loop shares ONE call shape — every registered fn is called
    ``(memory_dir, repo_root, run_ctx)``, the same ``RunContext`` instance, not a
    special-cased arity for a subset of producers."""
    seen = []

    def spy(md, repo, ctx=None):
        seen.append(ctx)
        return None

    _producers(monkeypatch, [("a", spy), ("b", spy)])
    S.build_context("md", "repo")
    assert len(seen) == 2
    assert seen[0] is seen[1]  # the SAME RunContext instance, computed once
    assert isinstance(seen[0], S.RunContext)


def test_staleness_producer_formats_real_output(monkeypatch):
    monkeypatch.setattr(
        S,
        "find_stale",
        lambda md, repo, diagnostics=None: [
            {"name": "m_x", "changed_paths": ["src/a.py", "src/b.py"]}
        ],
    )
    out = S.staleness_producer("md", "repo")
    assert out and "m_x" in out and "src/a.py" in out


# --------------------------------------------------------------------------- #
# LIF-1: the staleness producer suppresses already-demoted (invalid_after) entries'
# per-item lines but keeps them COUNTED — terminal states never re-nag, and nothing
# silently disappears.
# --------------------------------------------------------------------------- #
def _iso_days_ago(days):
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).isoformat()


def _leaf(name):
    return f'---\nname: {name}\ndescription: "{name} description"\n---\nbody\n'


def _stub_stale(monkeypatch, names):
    monkeypatch.setattr(
        S,
        "find_stale",
        lambda md, repo, diagnostics=None: [
            {"name": n, "changed_paths": ["src/a.py"]} for n in names
        ],
    )


def test_staleness_producer_suppresses_demoted_lines_but_keeps_the_count(memory_dir, monkeypatch):
    write_file(memory_dir, "m_keep.md", _leaf("m_keep"))
    gone = write_file(memory_dir, "m_gone.md", _leaf("m_gone"))
    set_invalid_after(gone, _iso_days_ago(5))  # "recent" — demoted, penalty engaged
    _stub_stale(monkeypatch, ["m_keep", "m_gone"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "m_keep" in out
    assert "m_gone" not in out  # per-item line suppressed — no double-nag
    assert "1 memories cite code" in out  # header counts ACTIVE entries only
    assert "(+1 already demoted" in out  # …but the demoted one is still accounted for


def test_staleness_producer_all_demoted_collapses_to_one_honest_line(memory_dir, monkeypatch):
    for n in ("m_a", "m_b"):
        set_invalid_after(write_file(memory_dir, f"{n}.md", _leaf(n)), _iso_days_ago(5))
    _stub_stale(monkeypatch, ["m_a", "m_b"])

    out = S.staleness_producer(memory_dir, "repo")
    assert out is not None  # not silent — suppression must never look like "nothing stale"
    assert "all 2 stale memories are already demoted" in out
    assert "m_a" not in out and "m_b" not in out  # no per-item re-nag lines


def test_staleness_producer_old_invalidation_suggests_the_archive_flow(memory_dir, monkeypatch):
    """LIF-1 (D): once an invalid_after entry ages past recall's old horizon, the producer
    points at the audit skill's archive flow — report-only, naming the memory."""
    old = write_file(memory_dir, "m_old.md", _leaf("m_old"))
    set_invalid_after(old, _iso_days_ago(400))  # far past _INVALIDATION_RECENT_DAYS
    _stub_stale(monkeypatch, ["m_old"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "/hippo:audit" in out and "m_old" in out  # named ONLY in the archive suggestion
    assert "old-invalidation horizon" in out


def test_staleness_producer_recent_invalidation_gets_no_archive_suggestion(memory_dir, monkeypatch):
    recent = write_file(memory_dir, "m_recent.md", _leaf("m_recent"))
    set_invalid_after(recent, _iso_days_ago(5))
    _stub_stale(monkeypatch, ["m_recent"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "/hippo:audit" not in out  # still inside the penalty window — nothing to archive yet


def test_staleness_producer_unchanged_when_nothing_is_invalidated(memory_dir, monkeypatch):
    """No invalid_after anywhere -> the exact pre-LIF-1 block (no tail, full header count)."""
    write_file(memory_dir, "m_x.md", _leaf("m_x"))
    _stub_stale(monkeypatch, ["m_x"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "1 memories cite code" in out and "m_x" in out
    assert "demoted" not in out and "/hippo:audit" not in out


def test_main_prints_session_start_json_when_stale(monkeypatch, capsys):
    monkeypatch.setattr(S, "resolve_dirs", lambda: ("md", "repo"))
    monkeypatch.setattr(
        S,
        "find_stale",
        lambda md, repo, diagnostics=None: [{"name": "m_x", "changed_paths": ["src/a.py"]}],
    )
    rc = S.main()
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "m_x" in data["hookSpecificOutput"]["additionalContext"]


def test_main_is_silent_when_nothing_stale(monkeypatch, capsys):
    monkeypatch.setattr(S, "resolve_dirs", lambda: ("md", "repo"))
    monkeypatch.setattr(S, "find_stale", lambda md, repo, diagnostics=None: [])
    rc = S.main()
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_integrity_producer_formats_real_output(monkeypatch):
    monkeypatch.setattr(S, "find_unparseable", lambda md: ["m_broken"])
    out = S.integrity_producer("md", "repo")
    assert out and "m_broken" in out and "UNPARSEABLE" in out


def test_integrity_producer_silent_when_clean(monkeypatch):
    monkeypatch.setattr(S, "find_unparseable", lambda md: [])
    assert S.integrity_producer("md", "repo") is None


def test_main_refreshes_index_for_a_new_memory(tmp_path, monkeypatch):
    """The dispatcher brings the recall index up to date so a memory written during the last
    session is indexed by this one (the SessionStart auto-refresh side effect)."""
    import os

    from memory import build_index as B

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\ntype: project\n---\nbody\n')
    B.build_index(md, B.default_index_dir(md))
    assert {e["name"] for e in B.load_index(B.default_index_dir(md)).entries} == {"a"}

    # A new memory is written; SessionStart should index it on the next start.
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: b\ndescription: "beta new"\ntype: project\n---\nbody\n')

    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, str(tmp_path)))
    monkeypatch.setattr(S, "build_context", lambda *a, **k: "")  # isolate the refresh side effect
    assert S.main() == 0
    assert {e["name"] for e in B.load_index(B.default_index_dir(md)).entries} == {"a", "b"}


# --------------------------------------------------------------------------- #
# reconsolidation producer wiring (Tier 2) — ONE dispatcher, never a parallel hook entry
# --------------------------------------------------------------------------- #
def test_reconsolidation_producer_is_registered_exactly_once():
    from memory.reconsolidate import reconsolidation_producer

    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("reconsolidation") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "reconsolidation"]
    assert fns == [reconsolidation_producer]  # the SAME function, not a re-implementation


def test_reconsolidation_producer_registered_after_staleness():
    labels = [label for label, _fn in S.PRODUCERS]
    # the recall-filtered subset is grouped right after the full staleness signal
    assert labels.index("reconsolidation") == labels.index("staleness") + 1


def test_reconsolidation_silent_when_stubbed_empty(monkeypatch):
    from memory.reconsolidate import reconsolidation_producer

    monkeypatch.setattr("memory.reconsolidate.recalled_stale_worklist", lambda *a, **k: [])
    assert reconsolidation_producer("md", "repo") is None


# --------------------------------------------------------------------------- #
# LIF-6 — de-duplicate SessionStart staleness vs reconsolidation reporting: find_stale is
# computed ONCE by the dispatcher and shared via RunContext; the staleness producer
# excludes names already claimed by the reconsolidation worklist. Unlike most of this
# file, these tests DO use real git timing (mirrors
# test_citation_rot_producer_end_to_end_after_git_mv) — the de-dup is only observable
# across two producers wired through the real find_stale/recalled_stale_worklist path.
# --------------------------------------------------------------------------- #
def _lif6_mem(name, cited, source_commit):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    return (
        f'---\nname: {name}\ndescription: "{name} description"\n'
        f'cited_paths: {cp}\nsource_commit: "{source_commit}"\n---\nbody for {name}\n'
    )


def _lif6_seed_two_stale_one_recalled(repo, memory_dir, monkeypatch):
    """m_a and m_b both cite src/foo.py and both go stale after the same drift commit;
    only m_a is recently recalled, so the worklist (``{m_a}``) is a STRICT subset of the
    stale set (``{m_a, m_b}``) — exactly the AC's corpus shape. Commits are pinned
    NEAR-NOW: find_stale's default window is wall-clock-relative, not relative to any
    fixed epoch (mirrors test_reconsolidate.py's ``_seed_stale_recalled`` pattern, needed
    here because the real dispatcher path has no ``since`` override to widen it)."""
    import time

    from .conftest import git_commit

    now = int(time.time())
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", now - 200)
    write_file(memory_dir, "m_a.md", _lif6_mem("m_a", ["src/foo.py"], c1))
    write_file(memory_dir, "m_b.md", _lif6_mem("m_b", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", now - 100)

    td = os.path.join(repo, "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"session_id": "s1", "names": ["m_a"]}) + "\n")
    return td


def test_find_stale_called_exactly_once_per_dispatcher_run(repo, memory_dir, monkeypatch):
    """LIF-6's core claim: staleness_producer + reconsolidation_producer used to each
    independently call find_stale (twice per SessionStart, same corpus, same git-log
    window); the dispatcher now computes it exactly once via ``_build_run_context`` and
    both producers read the shared RunContext instead."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _lif6_seed_two_stale_one_recalled(repo, memory_dir, monkeypatch)

    calls = []
    real_find_stale = S.find_stale

    def counting_find_stale(*a, **k):
        calls.append(1)
        return real_find_stale(*a, **k)

    monkeypatch.setattr(S, "find_stale", counting_find_stale)
    S.build_context(memory_dir, repo)
    assert calls == [1]


def test_no_memory_name_appears_twice_when_worklist_is_subset_of_stale(repo, memory_dir, monkeypatch):
    """The AC, end to end: m_a sits on BOTH the full stale set and the (strict-subset)
    reconsolidation worklist — its per-item bullet must appear in the staleness block's
    OWN per-item lines exactly ZERO times (claimed by the reconsolidation block instead),
    while m_b (stale but never recalled) still gets its own staleness line. Blocks are
    split on "\\n\\n" (build_context's own join separator) rather than a raw substring
    count, because an UNRELATED producer (git-recent) legitimately mentions "m_a" too —
    it was captured recently, which has nothing to do with this de-dup."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _lif6_seed_two_stale_one_recalled(repo, memory_dir, monkeypatch)

    ctx = S.build_context(memory_dir, repo)
    blocks = ctx.split("\n\n")
    staleness_block = next(b for b in blocks if b.startswith("⚠ Memory staleness"))
    recon_block = next(b for b in blocks if b.startswith("🧠 Reconsolidation worklist"))
    assert "  • m_a:" not in staleness_block  # claimed by the worklist, not re-listed here
    assert "  • m_b:" in staleness_block  # the non-recalled stale memory still gets its line
    assert "  • m_a:" in recon_block
    assert "already on the reconsolidation worklist" in staleness_block  # LIF-1-style tail


def test_staleness_producer_excludes_worklist_names_via_ctx(memory_dir):
    """Unit-level: given a RunContext whose worklist already claims a name, the staleness
    producer drops its per-item line and counts it in a tail (LIF-1's suppression-tail
    style, applied to the new exclusion reason)."""
    write_file(memory_dir, "m_a.md", _lif6_mem("m_a", ["src/foo.py"], "abc123"))
    write_file(memory_dir, "m_b.md", _lif6_mem("m_b", ["src/foo.py"], "abc123"))
    ctx = S.RunContext(
        stale=[
            {"name": "m_a", "changed_paths": ["src/foo.py"]},
            {"name": "m_b", "changed_paths": ["src/foo.py"]},
        ],
        worklist=[{"name": "m_a", "changed_paths": ["src/foo.py"]}],
    )
    out = S.staleness_producer(memory_dir, "repo", ctx)
    lines = out.splitlines()
    assert not any(line.strip().startswith("• m_a") for line in lines)
    assert any(line.strip().startswith("• m_b") for line in lines)
    assert "(+1 already on the reconsolidation worklist" in out


def test_staleness_producer_all_worklisted_collapses_to_one_honest_line(memory_dir):
    write_file(memory_dir, "m_a.md", _lif6_mem("m_a", ["src/foo.py"], "abc123"))
    ctx = S.RunContext(
        stale=[{"name": "m_a", "changed_paths": ["src/foo.py"]}],
        worklist=[{"name": "m_a", "changed_paths": ["src/foo.py"]}],
    )
    out = S.staleness_producer(memory_dir, "repo", ctx)
    assert out is not None
    assert "all 1 stale memories are already on the reconsolidation worklist" in out
    assert "• m_a" not in out


def test_staleness_producer_mixed_demoted_and_worklisted_collapse(memory_dir):
    gone = write_file(memory_dir, "m_gone.md", _lif6_mem("m_gone", ["src/foo.py"], "abc123"))
    set_invalid_after(gone, _iso_days_ago(5))
    write_file(memory_dir, "m_a.md", _lif6_mem("m_a", ["src/foo.py"], "abc123"))
    ctx = S.RunContext(
        stale=[
            {"name": "m_gone", "changed_paths": ["src/foo.py"]},
            {"name": "m_a", "changed_paths": ["src/foo.py"]},
        ],
        worklist=[{"name": "m_a", "changed_paths": ["src/foo.py"]}],
    )
    out = S.staleness_producer(memory_dir, "repo", ctx)
    assert "already accounted for" in out
    assert "1 demoted" in out and "1 on the reconsolidation worklist" in out


def test_producers_degrade_unchanged_when_find_stale_is_empty(tmp_path):
    """A RunContext built from an empty stale set produces the SAME silence as the
    pre-LIF-6 empty case for both staleness-derived producers."""
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    empty_ctx = S.RunContext()
    assert S.staleness_producer(md, "repo", empty_ctx) is None
    from memory.reconsolidate import reconsolidation_producer

    assert reconsolidation_producer(md, "repo", empty_ctx) is None


def test_stale_cache_written_with_documented_shape(repo, memory_dir, monkeypatch):
    """RET-5/RET-6 setup: stale.json lands in the gitignored index dir with the minimal
    ``{changed, sha}`` shape a bounded penalty / drift banner will need."""
    from memory.build_index import default_index_dir

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _lif6_seed_two_stale_one_recalled(repo, memory_dir, monkeypatch)

    S.build_context(memory_dir, repo)

    idx = default_index_dir(memory_dir)
    with open(os.path.join(idx, "stale.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["schema_version"] == 1
    assert "generated_at" in payload
    assert set(payload["stale"]) == {"m_a", "m_b"}
    for entry in payload["stale"].values():
        assert entry["changed"] == 1  # one drifted cited path each
        assert entry["sha"] and len(entry["sha"]) <= 7


def test_stale_cache_written_honestly_empty_when_nothing_stale(repo, memory_dir, monkeypatch):
    """No memories at all -> find_stale returns [] honestly, and stale.json says so too
    (a checked-and-clean run, not a skipped write)."""
    from memory.build_index import default_index_dir

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    S.build_context(memory_dir, repo)

    idx = default_index_dir(memory_dir)
    with open(os.path.join(idx, "stale.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["stale"] == {}


def test_stale_cache_not_written_for_a_nonexistent_memory_dir():
    """A bogus/nonexistent memory_dir (a hermetic test's placeholder string, or an
    untrusted-gate short-circuit) must never mint a stray index dir."""
    S.build_context("does/not/exist", "repo")
    assert not os.path.isdir("does")


def _repo_with_empty_baseline(tmp_path):
    """A git repo whose one memory carries a residual `source_commit: ""`."""
    import subprocess

    from .conftest import git_commit, write_file

    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    write_file(repo, "src/x.py", "x = 1\n")
    head = git_commit(repo, "init", 1_700_000_000)
    md = os.path.join(repo, ".claude", "memory")
    path = write_file(
        repo,
        ".claude/memory/residual.md",
        '---\nname: residual\ndescription: "left empty by a pre-COR-1 backfill"\n'
        'cited_paths: ["src/x.py"]\nsource_commit: ""\n---\nbody\n',
    )
    return repo, md, path, head


def test_main_does_not_write_to_memory_frontmatter(tmp_path, monkeypatch, capsys):
    """AC (COR-10): a hook must not write to the corpus.

    SessionStart used to heal residual `source_commit: ""` baselines here. trust.py states
    "hooks NEVER consent", which is only sound if hooks never WRITE — the heal changed the
    file's bytes, drifted it off its own SEC-6 fingerprint, and the trust-drift banner a few
    lines later then asked the user "a git pull? a hand edit?" about hippo's own write. The
    heal is still correct and still available; it moved to where a human runs it."""
    from memory.staleness import read_provenance

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, md, path, head = _repo_with_empty_baseline(tmp_path)
    before = open(path, encoding="utf-8").read()
    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, repo))

    assert S.main() == 0
    after = open(path, encoding="utf-8").read()
    assert after == before, "the hook wrote to a memory file"
    # still unhealed — that IS the point: doctor names it, the CLI heals it, a human decides
    assert 'source_commit: ""' in after
    assert not read_provenance(after)[1]


def test_cli_heal_baselines_still_heals(tmp_path, monkeypatch):
    """COR-10: the heal did not disappear — it became a thing you run on purpose."""
    from memory import provenance as P
    from memory.staleness import read_provenance

    repo, md, path, head = _repo_with_empty_baseline(tmp_path)
    monkeypatch.setattr(P, "resolve_dirs", lambda: (md, repo))
    assert P.main(["--heal-baselines"]) == 0
    _, sc = read_provenance(open(path, encoding="utf-8").read())
    assert sc == head


def test_doctor_reports_the_empty_baseline_the_hook_no_longer_heals(tmp_path, monkeypatch):
    """COR-10: removing the hook's write must not make the state invisible."""
    from memory import doctor as D

    repo, md, path, head = _repo_with_empty_baseline(tmp_path)
    r = D.check_empty_baselines(D.DoctorContext(memory_dir=md, repo_root=repo))
    assert r["status"] == "warn"
    assert "residual" in r["message"]
    assert "--heal-baselines" in r["message"]  # names the command the human runs


# --------------------------------------------------------------------------- #
# COR-11: stale-venv detection (requirements hash vs bootstrap sentinel)
# --------------------------------------------------------------------------- #
def _plugin_env(tmp_path, monkeypatch, *, req_text: str, sentinel_hash):
    import hashlib
    import json as _json

    data_dir = tmp_path / "plugin-data"
    plugin_root = tmp_path / "plugin-root"
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(plugin_root, exist_ok=True)
    (plugin_root / "requirements.txt").write_text(req_text, encoding="utf-8")
    if sentinel_hash == "current":
        sentinel_hash = hashlib.sha256(req_text.encode()).hexdigest()
    if sentinel_hash is not None:
        (data_dir / ".bootstrap-sentinel").write_text(
            _json.dumps({"requirements_hash": sentinel_hash}), encoding="utf-8"
        )
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))


def test_stale_venv_producer_nudges_on_dep_bump(tmp_path, monkeypatch):
    _plugin_env(tmp_path, monkeypatch, req_text="numpy>=2\n", sentinel_hash="0" * 64)
    out = S.stale_venv_producer("md", "repo")
    assert out and "/hippo:bootstrap" in out


def test_stale_venv_producer_silent_when_hash_current(tmp_path, monkeypatch):
    _plugin_env(tmp_path, monkeypatch, req_text="numpy>=2\n", sentinel_hash="current")
    assert S.stale_venv_producer("md", "repo") is None


def test_stale_venv_producer_silent_when_not_bootstrapped(tmp_path, monkeypatch):
    # No sentinel — ONB-1's pre-Python nudge owns that state; this producer stays out.
    _plugin_env(tmp_path, monkeypatch, req_text="numpy>=2\n", sentinel_hash=None)
    assert S.stale_venv_producer("md", "repo") is None


def test_stale_venv_producer_silent_without_plugin_data_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert S.stale_venv_producer("md", "repo") is None


def test_stale_venv_producer_registered_first():
    assert S.PRODUCERS[0][0] == "stale_venv"
    assert S.PRODUCERS[0][1] is S.stale_venv_producer


# --------------------------------------------------------------------------- #
# SHP-3 — unresolvable_baseline_producer (squash-merge / shallow-clone legibility)
# --------------------------------------------------------------------------- #
def test_unresolvable_baseline_producer_formats_real_output(monkeypatch):
    monkeypatch.setattr(S, "count_unresolvable_baselines", lambda md, repo: 3)
    out = S.unresolvable_baseline_producer("md", "repo")
    assert out and "3 memories" in out and "squash-merge" in out


def test_unresolvable_baseline_producer_silent_when_zero(monkeypatch):
    monkeypatch.setattr(S, "count_unresolvable_baselines", lambda md, repo: 0)
    assert S.unresolvable_baseline_producer("md", "repo") is None


def test_unresolvable_baseline_producer_is_registered():
    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("unresolvable_baseline") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "unresolvable_baseline"]
    assert fns == [S.unresolvable_baseline_producer]


# --------------------------------------------------------------------------- #
# LIF-3: citation_rot_producer — cited paths gone from the repo, count-first,
# ONE canonical SessionStart surface (find_unparseable's rot sibling)
# --------------------------------------------------------------------------- #
def test_citation_rot_producer_formats_real_output_count_first(monkeypatch):
    monkeypatch.setattr(
        S,
        "find_citation_rot",
        lambda md, repo: [{"name": "m_rot", "missing_paths": ["src/gone.py"], "cited_count": 2}],
    )
    out = S.citation_rot_producer("md", "repo")
    assert out and "m_rot" in out and "src/gone.py" in out
    # count-first: the COUNT leads the block, per-item lines follow inside the budget
    assert "1 memories cite paths that no longer exist" in out.splitlines()[0]


def test_citation_rot_producer_total_rot_called_out_distinctly(monkeypatch):
    """Every citation gone → a refresh would EMPTY cited_paths and the memory becomes
    staleness-exempt; the producer says so on that line, distinctly."""
    monkeypatch.setattr(
        S,
        "find_citation_rot",
        lambda md, repo: [{"name": "m_total", "missing_paths": ["src/gone.py"], "cited_count": 1}],
    )
    out = S.citation_rot_producer("md", "repo")
    assert "staleness-EXEMPT" in out


def test_citation_rot_producer_partial_rot_not_marked_exempt(monkeypatch):
    monkeypatch.setattr(
        S,
        "find_citation_rot",
        lambda md, repo: [{"name": "m_partial", "missing_paths": ["src/gone.py"], "cited_count": 3}],
    )
    out = S.citation_rot_producer("md", "repo")
    assert out and "EXEMPT" not in out  # only the drop-to-zero case earns the loud marker


def test_citation_rot_producer_silent_when_clean(monkeypatch):
    monkeypatch.setattr(S, "find_citation_rot", lambda md, repo: [])
    assert S.citation_rot_producer("md", "repo") is None


def test_citation_rot_producer_bounds_item_count(monkeypatch):
    monkeypatch.setattr(
        S,
        "find_citation_rot",
        lambda md, repo: [
            {"name": f"m_{i:03d}", "missing_paths": ["src/gone.py"], "cited_count": 2}
            for i in range(30)
        ],
    )
    out = S.citation_rot_producer("md", "repo")
    assert "30 memories" in out  # the count stays honest…
    assert "…and 10 more." in out  # …while the per-item lines respect the budget


def test_citation_rot_producer_registered_once_after_integrity():
    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("citation_rot") == 1
    # grouped right after its unparseable sibling — both are corpus-record-vs-reality holes
    assert labels.index("citation_rot") == labels.index("integrity") + 1
    fns = [fn for label, fn in S.PRODUCERS if label == "citation_rot"]
    assert fns == [S.citation_rot_producer]


def test_citation_rot_producer_end_to_end_after_git_mv(repo, memory_dir):
    """AC (LIF-3): rename a cited file (git mv + commit) → the SessionStart rot surface
    names the memory and the vanished path, before any refresh has dropped anything."""
    import subprocess

    from .conftest import git_commit

    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep", 1_700_000_000)
    write_file(
        memory_dir,
        "m_rot.md",
        f'---\nname: m_rot\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody\n',
    )
    subprocess.run(
        ["git", "mv", "src/dep.py", "src/dep_moved2.py"], cwd=repo, check=True, capture_output=True
    )
    git_commit(repo, "rename dep", 1_700_000_100)

    out = S.citation_rot_producer(memory_dir, repo)
    assert out and "m_rot" in out and "src/dep.py" in out


# --------------------------------------------------------------------------- #
# COR-6: source-gated session rotation + harness-keyed telemetry sessions
# --------------------------------------------------------------------------- #
def _session_start_env(tmp_path, monkeypatch):
    import memory.telemetry as T

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    td = str(tmp_path / ".claude" / ".memory-telemetry")
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, str(tmp_path)))
    monkeypatch.setattr(S, "build_context", lambda *a, **k: "")
    return md, td, T


def test_resume_source_does_not_rotate_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="resume") == 0
    after = T.current_session_id(td)
    assert after == before


def test_compact_source_does_not_rotate_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="compact") == 0
    assert T.current_session_id(td) == before


def test_startup_source_rotates_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="startup") == 0
    after = T.current_session_id(td)
    assert after != before


def test_clear_source_rotates_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="clear") == 0
    after = T.current_session_id(td)
    assert after != before


def test_missing_source_does_not_rotate_session_token(tmp_path, monkeypatch):
    """No hook payload at all (source=None) must not rotate — mirrors resume/compact."""
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main() == 0
    assert T.current_session_id(td) == before


def test_harness_session_id_bypasses_file_token_entirely(tmp_path, monkeypatch):
    """When the harness hands us a session_id, the file-based token is never touched."""
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup", session_id="harness-sid-1") == 0
    assert not os.path.exists(T._session_path(td))


def test_explicit_source_wins_over_stdin_payload(tmp_path, monkeypatch):
    """Explicit kwargs override whatever _read_hook_payload would have parsed from stdin."""
    monkeypatch.setattr(S, "_read_hook_payload", lambda: ("resume", None))
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    # stdin says "resume" (would NOT rotate) but the explicit source="startup" wins,
    # so a second explicit-startup call still rotates.
    assert S.main(source="startup") == 0
    assert T.current_session_id(td) != before


def test_read_hook_payload_parses_source_and_session_id(monkeypatch):
    import io

    monkeypatch.setattr(
        S.sys, "stdin", io.StringIO('{"source": "resume", "session_id": "abc123"}')
    )
    assert S._read_hook_payload() == ("resume", "abc123")


def test_read_hook_payload_empty_stdin_yields_none_none(monkeypatch):
    import io

    monkeypatch.setattr(S.sys, "stdin", io.StringIO(""))
    assert S._read_hook_payload() == (None, None)


def test_read_hook_payload_garbage_json_yields_none_none(monkeypatch):
    import io

    monkeypatch.setattr(S.sys, "stdin", io.StringIO("{not json"))
    assert S._read_hook_payload() == (None, None)


def test_read_hook_payload_tty_stdin_is_not_read(monkeypatch):
    class FakeTty:
        def isatty(self):
            return True

        def read(self):
            raise AssertionError("must not read from a tty stdin")

    monkeypatch.setattr(S.sys, "stdin", FakeTty())
    assert S._read_hook_payload() == (None, None)


# --------------------------------------------------------------------------- #
# COR-7: corpus_format producer — a corpus NEWER than the plugin must be loud;
# an OLDER one is doctor's migration path, never a per-session nag
# --------------------------------------------------------------------------- #
def test_corpus_format_producer_warns_when_corpus_is_newer(tmp_path):
    from memory.provenance import write_corpus_format

    md = str(tmp_path / "memory")
    os.makedirs(md)
    newer = S.CORPUS_FORMAT_VERSION + 1
    assert write_corpus_format(md, version=newer) is True
    out = S.corpus_format_producer(md, "repo")
    assert out is not None
    assert f"v{newer}" in out and f"v{S.CORPUS_FORMAT_VERSION}" in out  # names BOTH versions
    assert "update the hippo plugin" in out.lower()  # the one-directional remediation


def test_corpus_format_producer_silent_when_current_or_undeclared(tmp_path):
    from memory.provenance import write_corpus_format

    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert S.corpus_format_producer(md, "repo") is None  # no marker == format 1 == silent
    assert write_corpus_format(md) is True  # explicit current version
    assert S.corpus_format_producer(md, "repo") is None


def test_corpus_format_producer_silent_when_corpus_is_older(tmp_path, monkeypatch):
    """Corpus older than the plugin == a migration is pending — that path is doctor-driven
    (check_format_version names it once, on demand), not a per-session alarm."""
    from memory.provenance import write_corpus_format

    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert write_corpus_format(md, version=1) is True
    monkeypatch.setattr(S, "CORPUS_FORMAT_VERSION", 2)  # simulate a post-bump plugin
    assert S.corpus_format_producer(md, "repo") is None


def test_corpus_format_producer_silent_on_missing_corpus(tmp_path):
    assert S.corpus_format_producer(str(tmp_path / "does-not-exist"), "repo") is None


def test_corpus_format_producer_is_registered_exactly_once():
    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("corpus_format") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "corpus_format"]
    assert fns == [S.corpus_format_producer]


# --------------------------------------------------------------------------- #
# GRW-5: the commit-precise watermark lane joins the dispatcher's ONE worklist
# --------------------------------------------------------------------------- #
def test_watermark_lane_flags_uncalled_memory_end_to_end(repo, memory_dir, monkeypatch):
    """A memory NEVER recently recalled still joins the reconsolidation worklist when a
    commit since the last session's watermark touches its cited file — tagged
    [since-watermark], routed through the same block (no new producer, no new verb)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(repo, "src/quiet.py", "q = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(
        memory_dir, "m_quiet.md", _lif6_mem("m_quiet", ["src/quiet.py"], c1)
    )
    td = os.path.join(repo, "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    os.makedirs(td, exist_ok=True)
    # The last session's episode watermark = c1 (raw line — hermetic, pinned sha)…
    with open(os.path.join(td, "episode_buffer.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": 100.0,
                    "session_id": "prior",
                    "query_preview": "q",
                    "recalled_names": ["unrelated"],
                    "head_commit": c1,
                }
            )
            + "\n"
        )
    # …and a commit SINCE the watermark touches the cited file.
    write_file(repo, "src/quiet.py", "q = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    ctx = S.build_context(memory_dir, repo)
    blocks = ctx.split("\n\n")
    recon_block = next((b for b in blocks if b.startswith("🧠 Reconsolidation worklist")), "")
    assert "m_quiet [since-watermark]" in recon_block
    assert "commits landed since your last session" in recon_block


# --------------------------------------------------------------------------- #
# GRW-6 — squash_merge_heal_producer (detection + per-item rebaseline OFFER)
# --------------------------------------------------------------------------- #
def test_squash_heal_producer_needs_both_signals(monkeypatch):
    # Break without a merge signal → silent (the generic SHP-3 producer covers it)…
    monkeypatch.setattr(S, "unresolvable_baseline_names", lambda md, repo: ["m_gone"])
    monkeypatch.setattr(S, "_recent_merge_signals", lambda repo: False)
    assert S.squash_merge_heal_producer("md", "repo") is None
    # …merge signal without a break → silent (nothing to heal)…
    monkeypatch.setattr(S, "unresolvable_baseline_names", lambda md, repo: [])
    monkeypatch.setattr(S, "_recent_merge_signals", lambda repo: True)
    assert S.squash_merge_heal_producer("md", "repo") is None
    # …both → the per-item offer, naming the memory and the confirmed-graduate route.
    monkeypatch.setattr(S, "unresolvable_baseline_names", lambda md, repo: ["m_gone"])
    out = S.squash_merge_heal_producer("md", "repo")
    assert out is not None
    assert "m_gone" in out
    assert "/hippo:consolidate" in out
    assert "--outcome graduate" in out
    assert "per item" in out


def test_squash_heal_producer_caps_names(monkeypatch):
    monkeypatch.setattr(
        S, "unresolvable_baseline_names", lambda md, repo: [f"m_{i:02d}" for i in range(9)]
    )
    monkeypatch.setattr(S, "_recent_merge_signals", lambda repo: True)
    out = S.squash_merge_heal_producer("md", "repo")
    assert "m_05" in out and "m_06" not in out
    assert "(+3 more)" in out


def test_squash_heal_producer_is_registered_after_unresolvable():
    labels = [label for label, _ in S.PRODUCERS]
    assert labels.count("squash_merge_heal") == 1
    assert labels.index("squash_merge_heal") == labels.index("unresolvable_baseline") + 1


def test_squash_merge_heal_end_to_end_and_reverify_clears(repo, memory_dir, monkeypatch):
    """A REAL squash-merge: branch → memory cites the branch's file (source_commit = the
    branch sha) → squash onto main with a forge-style '(#N)' subject → branch + objects
    pruned → the sha no longer resolves. The producer fires once naming the memory; a
    confirmed per-item graduate re-baselines it via reverify_file and BOTH producers go
    silent (self-clearing)."""
    import subprocess

    from .conftest import git_commit as _commit

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")

    def git(*args):
        subprocess.run(
            ["git", "-C", repo, *args], check=True, capture_output=True, text=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "1700000200 +0000",
                 "GIT_COMMITTER_DATE": "1700000200 +0000",
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )

    write_file(repo, "src/base.py", "base = 1\n")
    _commit(repo, "init", 1_700_000_000)
    git("checkout", "-q", "-b", "feat")
    write_file(repo, "src/feature.py", "feature = 1\n")
    feat_sha = _commit(repo, "add feature", 1_700_000_100)
    write_file(
        memory_dir,
        "m_feature_design.md",
        "---\nname: m_feature_design\ndescription: \"how the feature works\"\n"
        f'cited_paths: ["src/feature.py"]\nsource_commit: "{feat_sha}"\n'
        "source_commit_time: 1700000100\n---\nthe feature design notes\n",
    )
    git("checkout", "-q", "-")
    git("merge", "--squash", "feat")
    git("commit", "-q", "-m", "feat: add the feature (#7)")
    git("branch", "-D", "feat")
    # Make the squash REAL for detection purposes: expire the reflog and prune the
    # now-unreachable branch objects (what a fresh clone of the squashed repo looks like).
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "--prune=now", "--quiet")

    names = S.unresolvable_baseline_names(memory_dir, repo)
    assert names == ["m_feature_design"], "the squash genuinely broke the baseline"
    out = S.squash_merge_heal_producer(memory_dir, repo)
    assert out is not None and "m_feature_design" in out, (
        "reflog is expired — the '(#N)' squash-subject probe must still detect the merge"
    )

    # The offered heal: agent confirms the memory still holds, renders graduate per item.
    import memory.reconsolidate as R

    res = R.semantic_reverify("m_feature_design", "graduate", memory_dir, repo)
    assert res["error"] is None and res["cleared"] is True
    assert S.unresolvable_baseline_names(memory_dir, repo) == []
    assert S.squash_merge_heal_producer(memory_dir, repo) is None, "healed → self-cleared"


# --------------------------------------------------------------------------- #
# DRV-2 — the citation-derivation nudge
# --------------------------------------------------------------------------- #
def test_cite_derivation_producer_nudges_a_corpus_derived_by_the_old_extractor(tmp_path):
    """AC (DRV-2): an OLDER derivation is a live degradation (memories watching the wrong
    file; memories staleness-EXEMPT on an empty cited_paths), so unlike an older
    corpus_format it gets a per-session line — KPI-5, never a silent degradation. A v1
    (undeclared) corpus is behind BOTH historical fixes, so both gap clauses appear."""
    from memory.session_start import cite_derivation_producer

    md = str(tmp_path)
    with open(os.path.join(md, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\n---\nbody cites src/a.py\n")
    line = cite_derivation_producer(md, md)  # undeclared == v1
    assert line and "Citation derivation" in line
    assert "v1" in line and "v3" in line
    assert "package.json as package.js" in line  # ORC-1's gap (v1 -> v2)
    assert "extensionless config/build filenames" in line  # ORC-3's gap (v2 -> v3)
    # DOC-16: NAME the verb, on both surfaces. This used to assert "/hippo:doctor" — routing
    # to a health check that then named nothing, so the loop dead-ended: nudge -> doctor ->
    # (no command). Stating a conclusion while never naming the oracle is exactly LIF-4's
    # complaint, one layer up. The line must now carry the verb itself.
    assert "rederive" in line  # the MCP tool — the ONLY form a Desktop user can call
    assert "action='worklist'" in line  # ...and how to review before writing anything
    assert "--rederive-worklist" in line  # the terminal form
    assert "per-item" in line  # still never a bulk self-migration


def test_cite_derivation_producer_nudges_a_corpus_derived_by_v2_extractor(tmp_path):
    """AC (ORC-3): a corpus already at v2 (ORC-1 applied) is NOT behind the v1 bugs — the
    message must not describe a defect this corpus does not have. Only the v2->v3 gap
    (extensionless names) applies."""
    from memory.provenance import write_cite_derivation
    from memory.session_start import cite_derivation_producer

    md = str(tmp_path)
    with open(os.path.join(md, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\n---\nbody cites src/a.py\n")
    assert write_cite_derivation(md, 2)
    line = cite_derivation_producer(md, md)
    assert line and "Citation derivation" in line
    assert "v2" in line and "v3" in line
    assert "extensionless config/build filenames" in line  # ORC-3's gap — applies
    assert "package.json as package.js" not in line  # ORC-1's gap — does NOT apply to a v2 corpus


def test_cite_derivation_producer_is_silent_on_an_empty_corpus(tmp_path):
    """A corpus with no memories has no citations, so naming the extractor that derived
    them is a nudge about nothing."""
    from memory.session_start import cite_derivation_producer

    assert cite_derivation_producer(str(tmp_path), str(tmp_path)) is None


def test_cite_derivation_producer_is_silent_once_the_corpus_is_current(tmp_path):
    from memory.provenance import write_cite_derivation
    from memory.session_start import cite_derivation_producer

    md = str(tmp_path)
    with open(os.path.join(md, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\n---\nbody\n")
    assert write_cite_derivation(md)
    assert cite_derivation_producer(md, md) is None


def test_cite_derivation_producer_is_silent_on_a_newer_corpus(tmp_path):
    """A corpus AHEAD of the plugin is corpus_format_producer's taint case, not this one —
    this producer must not double-report it."""
    from memory.provenance import write_cite_derivation
    from memory.session_start import cite_derivation_producer

    md = str(tmp_path)
    assert write_cite_derivation(md, 99)
    assert cite_derivation_producer(md, md) is None


def test_a_raising_producer_is_named_not_vanished(monkeypatch):
    """RCH-9: every producer is individually guarded, so the backstop firing means a
    real bug — which is exactly when silence is most expensive. The failure must be
    NAMED in the context (the doctor pattern: visible warn with the exception), while
    the other producers still run (isolation keeps holding)."""

    def boom(md, repo, ctx=None):
        raise RuntimeError("wired wrong")

    _producers(monkeypatch, [("boom", boom), ("ok", lambda md, repo, ctx=None: "still here")])
    ctx = S.build_context("md", "repo")
    assert "still here" in ctx
    assert "boom" in ctx and "wired wrong" in ctx, (
        "a producer crash must be named in the context, not silently dropped"
    )
