"""GAT-4: one versioned CONVENTIONS.md.

The asset carries its version on its first line and its bytes are pinned here, so an edit
without a bump fails. A corpus's copy is classified by its bytes (current / behind / edited
/ missing); ``hippo setup --conventions`` previews by default and, on ``--yes``, moves the
copy's own lines (in no shipped version) into CONVENTIONS.local.md before replacing it,
never discarding a line it did not show. The local file is never overwritten and never
treated as a memory.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from memory import conventions as C
from memory import doctor as D
from memory import setup_cli
from memory.provenance import _is_memory_filename

# Bump BOTH when the asset's text changes, and record the previous text in
# plugin/assets/CONVENTIONS.history.json (its sha256 under its version, its lines' keys).
_PINNED_VERSION = 7
_PINNED_SHA256 = "6ce369bab0718a192c6f27000020acc733b83c7040145e1bd7c48034be02f67f"


def _asset_text():
    return C.asset_bytes().decode("utf-8")


def _write(memory_dir, name, text):
    with open(os.path.join(memory_dir, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(memory_dir, name):
    with open(os.path.join(memory_dir, name), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# The stamp and the pin
# --------------------------------------------------------------------------- #
def test_asset_carries_its_version_on_a_line_of_its_own():
    data = C.asset_bytes()
    assert data.split(b"\n", 1)[0] == f"<!-- hippo-conventions: {_PINNED_VERSION} -->".encode()
    assert C.stamp_of(data) == _PINNED_VERSION


def test_asset_bytes_are_pinned_so_an_edit_needs_a_bump():
    got = hashlib.sha256(C.asset_bytes()).hexdigest()
    assert got == _PINNED_SHA256, (
        "plugin/assets/CONVENTIONS.md changed: raise the version in its first line and in "
        "_PINNED_VERSION, record the previous text's sha256 and line keys in "
        "plugin/assets/CONVENTIONS.history.json, then re-pin _PINNED_SHA256 here"
    )


def test_history_holds_every_earlier_version_and_never_the_current_one():
    h = C.history()
    assert sorted(h["versions"]) == list(range(1, _PINNED_VERSION))
    assert hashlib.sha256(C.asset_bytes()).hexdigest() not in h["versions"].values()
    assert len(set(h["versions"].values())) == len(h["versions"])
    assert all(len(k) == 12 for k in h["lines"])


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def test_current_copy_is_ok(memory_dir):
    _write(memory_dir, "CONVENTIONS.md", _asset_text())
    assert C.classify(memory_dir)["state"] == "current"
    line = C.doctor_line(memory_dir)
    assert line["status"] == "ok" and f"version {_PINNED_VERSION}" in line["message"]


def test_missing_copy_is_ok_and_names_the_command(memory_dir):
    line = C.doctor_line(memory_dir)
    assert C.classify(memory_dir)["state"] == "missing"
    assert line["status"] == "ok" and "hippo setup --conventions" in line["message"]


def test_a_byte_equal_earlier_version_is_behind(memory_dir, monkeypatch):
    old = "# Memory corpus conventions\n\nan earlier shipped text\n"
    real = C.history()
    real["versions"][3] = hashlib.sha256(old.encode()).hexdigest()
    monkeypatch.setattr(C, "history", lambda: real)
    _write(memory_dir, "CONVENTIONS.md", old)
    c = C.classify(memory_dir)
    assert (c["state"], c["version"], c["own"]) == ("behind", 3, [])
    line = C.doctor_line(memory_dir)
    assert line["status"] == "warn"
    assert "version 3, behind" in line["message"] and "hippo setup --conventions" in line["message"]


def test_the_unstamped_asset_shipped_before_the_stamp_is_behind(memory_dir):
    """Version 6 is the asset minus the stamp and its paragraph — rebuild it and compare."""
    h = C.history()
    assert 6 in h["versions"]
    text = _asset_text()
    lines = text.split("\n")
    assert lines[0].startswith("<!-- hippo-conventions:")
    v6 = "\n".join(lines[1:])
    start = v6.index("The comment on the first line is this text's version.")
    end = v6.index("\n\n", start) + 1
    v6 = v6[:start - 1] + v6[end:]
    if hashlib.sha256(v6.encode()).hexdigest() != h["versions"][6]:
        pytest.skip("the asset moved past the text that sits on top of version 6")
    _write(memory_dir, "CONVENTIONS.md", v6)
    assert C.classify(memory_dir)["state"] == "behind"
    assert C.classify(memory_dir)["version"] == 6


def test_edited_copy_names_only_its_own_lines(memory_dir, monkeypatch):
    real = C.history()
    real["lines"].add(C._line_key("a line from an earlier shipped text"))
    monkeypatch.setattr(C, "history", lambda: real)
    text = _asset_text().replace("## Frontmatter schema\n", "## Frontmatter schema\n\nOur rule: no emoji.\n", 1)
    text = text.replace("\n## ", "\na line from an earlier shipped text\n\n## ", 1)
    text += "Our second rule: tag every memory.   \n"
    _write(memory_dir, "CONVENTIONS.md", text)
    c = C.classify(memory_dir)
    assert c["state"] == "edited"
    assert c["own"] == ["Our rule: no emoji.", "Our second rule: tag every memory."]
    line = C.doctor_line(memory_dir)
    assert line["status"] == "warn" and "2 line(s)" in line["message"]
    assert "CONVENTIONS.local.md" in line["message"]


# --------------------------------------------------------------------------- #
# hippo setup --conventions
# --------------------------------------------------------------------------- #
def _edited(memory_dir):
    text = _asset_text()
    text = text.replace("## Frontmatter schema\n", "## Frontmatter schema\n\nOur rule: no emoji.\n", 1)
    text = text.replace("Every memory is a", "Every single memory is a", 1)  # an edited asset line
    _write(memory_dir, "CONVENTIONS.md", text)
    return text


def test_preview_shows_diff_and_moved_lines_and_writes_nothing(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    before = _edited(memory_dir)
    assert setup_cli.main(["--conventions"]) == 0
    out = capsys.readouterr().out
    assert "+Our rule: no emoji." not in out  # the diff is old -> asset: own lines are removals
    assert "-Our rule: no emoji." in out and "-Every single memory is a" in out
    assert "  Our rule: no emoji." in out and "CONVENTIONS.local.md" in out
    assert "Nothing was written." in out
    assert _read(memory_dir, "CONVENTIONS.md") == before
    assert not os.path.exists(os.path.join(memory_dir, "CONVENTIONS.local.md"))


def test_yes_moves_own_lines_then_replaces_and_keeps_local_bytes(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    _edited(memory_dir)
    prior_local = "# Our local rules\n\nAlready here.\n"
    _write(memory_dir, "CONVENTIONS.local.md", prior_local)
    assert setup_cli.main(["--conventions", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "moved 2 line(s) into CONVENTIONS.local.md" in out
    assert _read(memory_dir, "CONVENTIONS.md") == _asset_text()
    local = _read(memory_dir, "CONVENTIONS.local.md")
    assert local.startswith(prior_local)
    assert local.endswith("Our rule: no emoji.\nEvery single memory is a `.md` file whose frontmatter block (between the opening/closing `---`\n")
    assert C.classify(memory_dir)["state"] == "current"
    assert setup_cli.main(["--conventions", "--yes"]) == 0
    assert "Nothing to do" in capsys.readouterr().out
    assert _read(memory_dir, "CONVENTIONS.local.md") == local  # never written twice


def test_behind_copy_is_replaced_with_no_local_file(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    old = "# Memory corpus conventions\n\nan earlier shipped text\n"
    real = C.history()
    real["versions"][2] = hashlib.sha256(old.encode()).hexdigest()
    monkeypatch.setattr(C, "history", lambda: real)
    _write(memory_dir, "CONVENTIONS.md", old)
    assert setup_cli.main(["--conventions"]) == 0
    assert "is version 2" in capsys.readouterr().out
    assert setup_cli.main(["--conventions", "--yes"]) == 0
    assert _read(memory_dir, "CONVENTIONS.md") == _asset_text()
    assert not os.path.exists(os.path.join(memory_dir, "CONVENTIONS.local.md"))


def test_missing_copy_is_seeded_on_yes(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert setup_cli.main(["--conventions"]) == 0
    assert not os.path.exists(os.path.join(memory_dir, "CONVENTIONS.md"))
    assert setup_cli.main(["--conventions", "--yes"]) == 0
    assert _read(memory_dir, "CONVENTIONS.md") == _asset_text()


def test_a_torn_replace_leaves_the_copy_and_a_rerun_adds_nothing_twice(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    before = _edited(memory_dir)

    def torn(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(C, "_write_copy", torn)
    assert setup_cli.main(["--conventions", "--yes"]) == 1
    assert "NOT finished" in capsys.readouterr().out
    assert _read(memory_dir, "CONVENTIONS.md") == before
    local = _read(memory_dir, "CONVENTIONS.local.md")
    monkeypatch.undo()
    monkeypatch.chdir(repo)
    assert C.plan(memory_dir)["write_local"] is False
    assert setup_cli.main(["--conventions", "--yes"]) == 0
    assert _read(memory_dir, "CONVENTIONS.local.md") == local
    assert _read(memory_dir, "CONVENTIONS.md") == _asset_text()


def test_a_copy_edited_after_the_read_is_refused(repo, memory_dir, monkeypatch):
    _edited(memory_dir)
    real_plan = C.plan

    def plan_then_edit(md):
        p = real_plan(md)
        with open(os.path.join(md, "CONVENTIONS.md"), "a", encoding="utf-8") as fh:
            fh.write("a line written meanwhile\n")
        return p

    monkeypatch.setattr(C, "plan", plan_then_edit)
    r = C.apply(memory_dir)
    assert not r["ok"] and "CasConflict" in r["error"]
    assert _read(memory_dir, "CONVENTIONS.md").endswith("a line written meanwhile\n")


def test_off_is_refused(repo, memory_dir, monkeypatch):
    monkeypatch.chdir(repo)
    assert setup_cli.main(["--conventions", "--off"]) == 2


# --------------------------------------------------------------------------- #
# The local file is a doc, not a memory; the doctor line is registered
# --------------------------------------------------------------------------- #
def test_local_file_is_not_a_memory():
    assert _is_memory_filename("CONVENTIONS.local.md") is False
    from memory.outcome_opened import _stem

    assert _stem("/x/.claude/memory/CONVENTIONS.local.md") is None


def test_doctor_registers_the_conventions_line_once():
    labels = [label for label, _ in D.CHECKS]
    assert labels.count("conventions") == 1
    assert labels.index("conventions") == labels.index("write_gate") + 1
