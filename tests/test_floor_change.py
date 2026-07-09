"""GOV-4: floor & corpus change governance — "changed since this clone's last session".

The always-loaded floor is the highest-trust, least-reviewed surface; it (and the corpus)
change silently via git pull. A per-clone gitignored watermark under CLAUDE_PLUGIN_DATA
makes the delta legible ONCE, then stays quiet: sorted-set diff for membership, whole-file
hash for in-place floor edits, no git calls. Unset CLAUDE_PLUGIN_DATA -> silent (a
watermark-less producer would scream "everything changed" every session).
"""

from __future__ import annotations

import json
import os

import memory.session_start as S


def _floor_md(md, pointers):
    os.makedirs(md, exist_ok=True)
    lines = ["# Project memory", "", "## User"]
    lines += [f"- [{n}]({n}.md) — hook" for n in pointers]
    lines += ["", "## Working Style & Process Feedback", ""]
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _mem(md, name, body="body"):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "d {name}"\n---\n{body}\n')


def _seeded(md, *, pointers=("alpha_note",), extra=("loose_note",)):
    _floor_md(md, pointers)
    for n in pointers:
        _mem(md, n)
    for n in extra:
        _mem(md, n)


def test_unset_plugin_data_is_silent_not_screaming(memory_dir, repo):
    """No durable watermark home -> None every session (deliberately INVERTED from the
    nudge counters' fail-toward-legible unset branch)."""
    _seeded(memory_dir)
    assert S.floor_change_producer(memory_dir, repo) is None
    assert S._gov4_watermark_path(repo) is None


def test_first_run_baselines_silently(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _seeded(memory_dir)
    assert S.floor_change_producer(memory_dir, repo) is None  # no block on first sight
    path = S._gov4_watermark_path(repo)
    with open(path, "r", encoding="utf-8") as fh:
        stored = json.load(fh)
    assert stored["floor"] == ["alpha_note"]
    assert "alpha_note" in stored["floor_hashes"]
    assert set(stored["corpus"]) == {"alpha_note", "loose_note"}


def test_floor_add_and_remove_surface_once_with_names(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _seeded(memory_dir)
    S.floor_change_producer(memory_dir, repo)  # baseline

    _mem(memory_dir, "beta_note")
    _floor_md(memory_dir, ["beta_note"])  # alpha_note out, beta_note in
    out = S.floor_change_producer(memory_dir, repo)
    assert out is not None and out.startswith("📜 Corpus changed")
    assert "+1 (beta_note)" in out and "−1 (alpha_note)" in out
    assert "git log -p -- .claude/memory/MEMORY.md" in out  # routes to review

    # surfaced-once: the SAME state does not re-nag
    assert S.floor_change_producer(memory_dir, repo) is None


def test_in_place_floor_edit_surfaces(memory_dir, repo, tmp_path, monkeypatch):
    """The extension the floor most needs: a body edit changes no pointer-set — only the
    whole-file hash catches it (the index entry hash is name+description only)."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _seeded(memory_dir)
    S.floor_change_producer(memory_dir, repo)  # baseline

    _mem(memory_dir, "alpha_note", body="the guidance CHANGED under you")
    out = S.floor_change_producer(memory_dir, repo)
    assert out is not None
    assert "edited in place: alpha_note" in out
    assert S.floor_change_producer(memory_dir, repo) is None  # seen -> quiet


def test_corpus_membership_delta_counts(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _seeded(memory_dir)
    S.floor_change_producer(memory_dir, repo)  # baseline

    _mem(memory_dir, "pulled_one")
    _mem(memory_dir, "pulled_two")
    os.remove(os.path.join(memory_dir, "loose_note.md"))
    out = S.floor_change_producer(memory_dir, repo)
    assert out is not None
    assert "corpus: added 2 / removed 1 memory file(s)" in out
    assert S.floor_change_producer(memory_dir, repo) is None


def test_peek_is_read_only_and_does_not_consume(memory_dir, repo, tmp_path, monkeypatch):
    """GOV-6's scorecard reads the same delta WITHOUT advancing the watermark — the
    producer still surfaces it afterward."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _seeded(memory_dir)
    S.floor_change_producer(memory_dir, repo)  # baseline

    _mem(memory_dir, "beta_note")
    _floor_md(memory_dir, ["alpha_note", "beta_note"])
    peek = S.floor_change_peek(memory_dir, repo)
    assert peek and peek["floor_added"] == ["beta_note"]
    out = S.floor_change_producer(memory_dir, repo)  # NOT consumed by the peek
    assert out is not None and "+1 (beta_note)" in out
    assert S.floor_change_peek(memory_dir, repo) == {
        "floor_added": [], "floor_removed": [], "floor_edited": [],
        "corpus_added": 0, "corpus_removed": 0,
    }


def test_peek_none_without_baseline_or_data_dir(memory_dir, repo, tmp_path, monkeypatch):
    _seeded(memory_dir)
    assert S.floor_change_peek(memory_dir, repo) is None  # CLAUDE_PLUGIN_DATA stripped
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    assert S.floor_change_peek(memory_dir, repo) is None  # no baseline yet


def test_watermark_is_keyed_per_clone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    a, b = str(tmp_path / "clone-a"), str(tmp_path / "clone-b")
    os.makedirs(a), os.makedirs(b)
    assert S._gov4_watermark_path(a) != S._gov4_watermark_path(b)


def test_corrupt_watermark_rebaselines_silently(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _seeded(memory_dir)
    path = S._gov4_watermark_path(repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert S.floor_change_producer(memory_dir, repo) is None  # rebaseline, no crash/nag
    with open(path, "r", encoding="utf-8") as fh:
        assert json.load(fh)["floor"] == ["alpha_note"]  # healed


def test_wired_into_producers():
    assert any(label == "floor_change" for label, _fn in S.PRODUCERS)


def test_bogus_dirs_never_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    bogus = str(tmp_path / "nope")
    assert S.floor_change_producer(bogus, str(tmp_path)) is None
