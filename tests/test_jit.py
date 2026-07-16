"""JIT-1: the first-touch reminder — point-of-action recall, bounded and boring.

Every recall moment hippo has is prompt-shaped; the moment a feedback lesson matters most
is the ACT — the first touch of the file the lesson is about, possibly an hour after the
injection fell out of the context window. The lane under test here:

  - SessionStart (the same offline moment that writes ``stale.json``) writes a derived
    reverse index — ``<index_dir>/touchmap.json`` — mapping each cited repo-relative path
    to the ``steer:pin``/feedback-type memories that cite it (JIT-1's ``reminders`` map)
    and to ALL citing memories (JIT-2's ``cited`` map).
  - The PostToolUse hook's existing single Python spawn calls ``jit.observe_touch``: one
    O(1) derived-cache read on the empty-norm path (most touches emit nothing, ever), and
    on the FIRST touch of a mapped file per session, ONE bounded line —
    ``memory <name>: <description>`` — as hook additionalContext, never again that session.

Restraint is the design (the RATIFIED default-on decision leans on it): first-touch-only,
type-scoped, floor-excluded, session-capped, recall_events-suppressed, kill-switched
(``HIPPO_DISABLE_JIT``), and SEC-1 trust-gated on the emit path.
"""

from __future__ import annotations

import builtins
import json
import os

from memory import jit as J
from memory import telemetry as T
from memory.build_index import default_index_dir
from memory.telemetry import default_telemetry_dir

from .conftest import write_file


# --------------------------------------------------------------------------- #
# Corpus helpers
# --------------------------------------------------------------------------- #
def _mem(
    md: str,
    name: str,
    *,
    mtype: str = "project",
    cited=None,
    steer: str = "",
    desc: str = "a lesson about this code",
    nested: bool = True,
):
    """One memory file; ``nested=True`` uses the ``metadata:`` schema, else top-level keys."""
    cited = cited or []
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    if nested:
        steer_line = f"  steer: {steer}\n" if steer else ""
        cited_line = f"  cited_paths: {cp}\n" if cited else ""
        body = (
            f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n'
            f"{steer_line}{cited_line}---\nBody.\n"
        )
    else:
        steer_line = f"steer: {steer}\n" if steer else ""
        cited_line = f"cited_paths: {cp}\n" if cited else ""
        body = (
            f'---\nname: {name}\ndescription: "{desc}"\ntype: {mtype}\n'
            f"{steer_line}{cited_line}---\nBody.\n"
        )
    write_file(md, f"{name}.md", body)


def _cache(memory_dir: str) -> dict:
    """Build + write + read back the touch cache (the SessionStart moment, in-test)."""
    idx = default_index_dir(memory_dir)
    assert J.refresh_touch_cache(memory_dir, idx) is True
    cache = J.read_touch_cache(idx)
    assert cache is not None
    return cache


def _observe(memory_dir: str, repo: str, rel: str, sid: str = "s1"):
    return J.observe_touch(
        rel, memory_dir=memory_dir, repo_root=repo, session_id=sid
    )


# --------------------------------------------------------------------------- #
# The derived cache: what gets in, what stays out
# --------------------------------------------------------------------------- #
def test_cache_maps_pin_and_feedback_only_into_reminders(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _mem(memory_dir, "pinned-note", mtype="project", steer="pin", cited=["src/db.py"])
    _mem(memory_dir, "plain-project", mtype="project", cited=["src/api.py"])
    _mem(memory_dir, "plain-reference", mtype="reference", cited=["src/ref.py"])
    _mem(memory_dir, "fb-no-cites", mtype="feedback")
    cache = _cache(memory_dir)

    assert set(cache["reminders"]) == {"src/app.py", "src/db.py"}
    assert [e["name"] for e in cache["reminders"]["src/app.py"]] == ["fb-lesson"]
    assert [e["name"] for e in cache["reminders"]["src/db.py"]] == ["pinned-note"]
    # JIT-2's full reverse index carries EVERY cited path, project/reference included.
    assert set(cache["cited"]) == {"src/app.py", "src/db.py", "src/api.py", "src/ref.py"}
    assert cache["cited"]["src/api.py"] == ["plain-project"]


def test_cache_reads_top_level_frontmatter_schema_too(repo, memory_dir):
    # The corpus uses BOTH frontmatter schemas (COR-14's lesson): a top-level-only read
    # would leave the lane permanently inert for nested files, and vice versa.
    _mem(memory_dir, "top-level-fb", mtype="feedback", cited=["src/a.py"], nested=False)
    _mem(memory_dir, "top-level-pin", mtype="project", steer="pin", cited=["src/b.py"], nested=False)
    cache = _cache(memory_dir)
    assert set(cache["reminders"]) == {"src/a.py", "src/b.py"}


def test_cache_excludes_floor_linked_memories_from_reminders(repo, memory_dir):
    # A floor-linked feedback memory is ALREADY always-loaded (MEMORY.md floor) — reminding
    # about it duplicates context the model has by construction. It stays in the JIT-2
    # ``cited`` map (evidence is not a nag).
    _mem(memory_dir, "floor-lesson", mtype="feedback", cited=["src/app.py"])
    _mem(memory_dir, "unfloored-lesson", mtype="feedback", cited=["src/app.py"])
    write_file(
        memory_dir,
        "MEMORY.md",
        "# Memory\n\n## User\n\n## Working Style & Process Feedback\n"
        "- [Floor lesson](floor-lesson.md) — always loaded\n",
    )
    cache = _cache(memory_dir)
    assert [e["name"] for e in cache["reminders"]["src/app.py"]] == ["unfloored-lesson"]
    assert set(cache["cited"]["src/app.py"]) == {"floor-lesson", "unfloored-lesson"}


def test_cache_honest_empty_write_and_schema_gate(repo, memory_dir):
    idx = default_index_dir(memory_dir)
    assert J.refresh_touch_cache(memory_dir, idx) is True
    cache = J.read_touch_cache(idx)
    # An honest empty cache means "checked, found nothing" — never a skipped write.
    assert cache == {"reminders": {}, "cited": {}}
    # Corrupt / wrong-schema files degrade to None (a cache miss), never an error.
    with open(J.touch_cache_path(idx), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert J.read_touch_cache(idx) is None
    with open(J.touch_cache_path(idx), "w", encoding="utf-8") as fh:
        json.dump({"schema_version": 999, "reminders": {}, "cited": {}}, fh)
    assert J.read_touch_cache(idx) is None


def test_cache_prebounds_the_reminder_line(repo, memory_dir):
    _mem(memory_dir, "long-lesson", mtype="feedback", cited=["src/app.py"], desc="x" * 500)
    cache = _cache(memory_dir)
    entry = cache["reminders"]["src/app.py"][0]
    line = f"memory {entry['name']}: {entry['description']}"
    assert len(line) <= J.MAX_LINE_CHARS


def test_cache_sanitizes_newlines_out_of_descriptions(repo, memory_dir):
    # A YAML double-quoted scalar with a blank continuation line parses to an embedded
    # newline — the reminder is contractually ONE line, so the cache must flatten it.
    write_file(
        memory_dir,
        "multi.md",
        '---\nname: multi\ndescription: "one\n\n  two"\nmetadata:\n  type: feedback\n'
        '  cited_paths: ["src/app.py"]\n---\nBody.\n',
    )
    cache = _cache(memory_dir)
    desc = cache["reminders"]["src/app.py"][0]["description"]
    assert "\n" not in desc


# --------------------------------------------------------------------------- #
# observe_touch — the JIT-1 emission lane
# --------------------------------------------------------------------------- #
def test_first_touch_emits_one_line_then_never_again(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"], desc="check the canary first")
    _cache(memory_dir)
    cited, ctx = _observe(memory_dir, repo, "src/app.py")
    assert ctx == "memory fb-lesson: check the canary first"
    assert cited == ["fb-lesson"]
    # Second touch of the same file, same session: silent forever (this session).
    _cited2, ctx2 = _observe(memory_dir, repo, "src/app.py")
    assert ctx2 is None
    # A fresh session starts over.
    _cited3, ctx3 = _observe(memory_dir, repo, "src/app.py", sid="s2")
    assert ctx3 is not None


def test_uncited_touch_is_the_empty_norm(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _cache(memory_dir)
    assert _observe(memory_dir, repo, "src/other.py") == (None, None)


def test_project_and_reference_types_never_emit(repo, memory_dir):
    _mem(memory_dir, "proj", mtype="project", cited=["src/app.py"])
    _mem(memory_dir, "ref", mtype="reference", cited=["src/app.py"])
    _cache(memory_dir)
    _cited, ctx = _observe(memory_dir, repo, "src/app.py")
    assert ctx is None


def test_absent_cache_is_silent(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    # No refresh_touch_cache: the lane must degrade to nothing, not scan the corpus.
    assert _observe(memory_dir, repo, "src/app.py") == (None, None)


def test_session_line_cap(repo, memory_dir):
    for i in range(5):
        _mem(memory_dir, f"fb-{i}", mtype="feedback", cited=[f"src/f{i}.py"])
    _cache(memory_dir)
    lines = []
    for i in range(5):
        _cited, ctx = _observe(memory_dir, repo, f"src/f{i}.py")
        if ctx:
            lines.extend(ctx.splitlines())
    assert len(lines) == J.MAX_LINES_PER_SESSION


def test_same_memory_never_emits_twice_via_a_second_file(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/a.py", "src/b.py"])
    _cache(memory_dir)
    _cited, ctx = _observe(memory_dir, repo, "src/a.py")
    assert ctx and "fb-lesson" in ctx
    _cited2, ctx2 = _observe(memory_dir, repo, "src/b.py")
    assert ctx2 is None


def test_multi_memory_touch_emits_pins_first_all_bounded(repo, memory_dir):
    _mem(memory_dir, "zz-pinned", mtype="project", steer="pin", cited=["src/app.py"])
    _mem(memory_dir, "aa-lesson", mtype="feedback", cited=["src/app.py"], desc="y" * 400)
    _cache(memory_dir)
    _cited, ctx = _observe(memory_dir, repo, "src/app.py")
    assert ctx is not None
    lines = ctx.splitlines()
    assert lines[0].startswith("memory zz-pinned:")  # steer:pin outranks type-scoped
    assert lines[1].startswith("memory aa-lesson:")
    assert all(len(ln) <= J.MAX_LINE_CHARS for ln in lines)


def test_recall_events_suppression_same_session_only(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _cache(memory_dir)
    td = default_telemetry_dir(memory_dir)
    # The model already saw this memory injected THIS session — never duplicate it.
    T.log_recall_event(
        [{"name": "fb-lesson", "score": 1.0, "rank": 1, "backend": "bm25"}],
        query="q", k=3, latency_ms=1.0, telemetry_dir=td, session_id="s1",
    )
    _cited, ctx = _observe(memory_dir, repo, "src/app.py", sid="s1")
    assert ctx is None
    # ... but an injection in a DIFFERENT session does not suppress this one.
    _cited2, ctx2 = _observe(memory_dir, repo, "src/app.py", sid="s2")
    assert ctx2 is not None


def test_kill_switch_silences_the_lane(repo, memory_dir, monkeypatch):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _cache(memory_dir)
    monkeypatch.setenv("HIPPO_DISABLE_JIT", "1")
    assert _observe(memory_dir, repo, "src/app.py") == (None, None)


def test_untrusted_corpus_emits_no_context_but_still_measures(repo, memory_dir, monkeypatch):
    # SEC-1 parity: the reminder INJECTS corpus content, so a trust revocation mid-stream
    # (the cache was written by an earlier, trusted SessionStart) must silence the lane.
    # The JIT-2 provenance half is measurement into a gitignored ledger — it keeps working.
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _cache(memory_dir)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    cited, ctx = _observe(memory_dir, repo, "src/app.py")
    assert ctx is None
    assert cited == ["fb-lesson"]


def test_observe_touch_never_reads_the_corpus(repo, memory_dir, monkeypatch):
    # The acceptance criterion is mechanism, not just speed: the hook lane does O(1)
    # reads of DERIVED files only — no corpus scan, no index rebuild, no model.
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _cache(memory_dir)
    md_real = os.path.realpath(memory_dir)
    opened = []
    real_open = builtins.open

    def counting_open(file, *args, **kwargs):
        try:
            p = os.path.realpath(str(file))
            if p.startswith(md_real + os.sep) or p == md_real:
                opened.append(p)
        except Exception:
            pass
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)
    _observe(memory_dir, repo, "src/app.py")
    _observe(memory_dir, repo, "src/other.py")
    assert opened == [], f"observe_touch read corpus files on the hook path: {opened}"


def test_state_dir_is_pruned(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    _cache(memory_dir)
    td = default_telemetry_dir(memory_dir)
    state_dir = os.path.join(td, "jit")
    os.makedirs(state_dir, exist_ok=True)
    for i in range(J.MAX_STATE_FILES + 8):
        with open(os.path.join(state_dir, f"old-{i:03d}.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
    _observe(memory_dir, repo, "src/app.py")
    remaining = [f for f in os.listdir(state_dir) if f.endswith(".json")]
    assert len(remaining) <= J.MAX_STATE_FILES


# --------------------------------------------------------------------------- #
# The SessionStart wiring (the same offline moment as stale.json)
# --------------------------------------------------------------------------- #
def test_session_start_refreshes_the_touch_cache(repo, memory_dir):
    from memory import session_start as SS

    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    SS.build_context(memory_dir, repo)
    cache = J.read_touch_cache(default_index_dir(memory_dir))
    assert cache is not None and "src/app.py" in cache["reminders"]


def test_session_start_skips_the_cache_when_killed(repo, memory_dir, monkeypatch):
    from memory import session_start as SS

    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=["src/app.py"])
    monkeypatch.setenv("HIPPO_DISABLE_JIT", "1")
    SS.build_context(memory_dir, repo)
    assert not os.path.exists(J.touch_cache_path(default_index_dir(memory_dir)))


# --------------------------------------------------------------------------- #
# Glass-box + measurement-only guarantees
# --------------------------------------------------------------------------- #
def test_module_imports_no_ranking_corpus_writer_or_registry():
    """The lane is hook-path: it must not import recall (ranking), new_memory (corpus
    writes), or surfaces (INV-1: the registry is a build-time artifact)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(J))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.lstrip("."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.lstrip("."))
    assert "recall" not in imported
    assert "new_memory" not in imported
    assert "surfaces" not in imported
