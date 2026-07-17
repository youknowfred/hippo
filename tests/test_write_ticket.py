"""SEN-1 — the write ticket: a deterministic pre-write verifier inside ``check_candidate``.

The consolidate skill's secret gate was PROCEDURAL text (the agent was told to run the
lint before fencing hunks); the fenced-hunk fidelity check and the archive-shadow check
were done by the reviewer's eye or not at all. SEN-1 mechanizes all three as ticket
fields on the existing dry-run battery:

  - secret lint      : ``secrets.scan_with_remediation`` over the RENDERED candidate —
                       the same detector write_memory warns with AFTER the write, now
                       surfaced BEFORE it (closing CAP-3's one gap).
  - hunk fidelity    : each fenced code block is compared byte-wise against the files the
                       body cites, at a FRESH ``git_head`` fetched at verify time (never
                       parsed out of a rationale string).
  - archive shadow   : a candidate stem colliding with ``archive/<name>.md`` warns —
                       writing it would resurrect a retired stem's name.

Contract pins (the load-bearing ones):
  - WARN-ONLY / NO AUTONOMOUS REJECTION: no ticket finding ever flips the route or
    blocks a write; ``check_candidate`` never raises, whatever breaks underneath.
  - The artifact is a "write ticket" — NEVER a "receipt" (GOV-5's shipped glass-box
    owns that word; inv5).
  - No new frontmatter field, no persistent ledger: the ticket lives on the result
    dict / rendered text only.
"""

from __future__ import annotations

import os

import pytest

from memory import new_memory as NM
from tests.conftest import git_commit, write_file


def _mk(memory_dir):
    os.makedirs(memory_dir, exist_ok=True)
    return memory_dir


# --------------------------------------------------------------------------- #
# Secret lint rides the dry run
# --------------------------------------------------------------------------- #


def test_ticket_secret_lint_rides_check_candidate_dry_run(tmp_path):
    md = _mk(str(tmp_path / "mem"))
    body = "the deploy key is AKIA" + "ABCDEFGHIJKLMNOP" + " — rotate it"
    decision = NM.check_candidate("leaky", "a fact with a credential", "project", body, memory_dir=md)
    ticket = decision["ticket"]
    assert any("AWS access key" in w for w in ticket["secret_warnings"])
    # remediation appended once, exactly like write_memory's own warn set
    assert any("purge" in w.lower() or "rotate" in w.lower() for w in ticket["secret_warnings"])
    # the finding is warn-only: the route stays whatever the dup battery said (no
    # neighbors in an empty corpus -> add), and nothing was written.
    assert decision["route"] == "add"
    assert os.listdir(md) == []


def test_ticket_clean_candidate_has_empty_secret_warnings(tmp_path):
    md = _mk(str(tmp_path / "mem"))
    decision = NM.check_candidate("clean", "an ordinary fact", "project", "nothing secret here", memory_dir=md)
    assert decision["ticket"]["secret_warnings"] == []


# --------------------------------------------------------------------------- #
# Fenced-hunk fidelity vs a fresh git HEAD
# --------------------------------------------------------------------------- #

_SNIPPET = "def compute(x):\n    return x * 41  # the answer minus one\n"


def _repo_with_cited_file(repo):
    write_file(repo, "src/mod.py", "# header\n" + _SNIPPET + "# footer\n")
    sha = git_commit(repo, "add mod", 1_700_000_000)
    return sha


def test_ticket_fidelity_matches_verbatim_block_at_head(repo, memory_dir):
    sha = _repo_with_cited_file(repo)
    body = "the hot loop lives in src/mod.py:\n\n```python\n" + _SNIPPET + "```\n"
    decision = NM.check_candidate(
        "hotloop", "where the hot loop lives", "project", body,
        memory_dir=memory_dir, repo_root=repo,
    )
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["head"] == sha
    assert fid["checked"] == 1 and fid["matched"] == 1 and fid["mismatched"] == []
    assert not any("fidelity" in w for w in decision["ticket"]["warnings"])


def test_ticket_fidelity_warns_on_byte_mismatch(repo, memory_dir):
    _repo_with_cited_file(repo)
    drifted = _SNIPPET.replace("* 41", "* 42")
    body = "the hot loop lives in src/mod.py:\n\n```python\n" + drifted + "```\n"
    decision = NM.check_candidate(
        "hotloop", "where the hot loop lives", "project", body,
        memory_dir=memory_dir, repo_root=repo,
    )
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["checked"] == 1 and fid["matched"] == 0 and len(fid["mismatched"]) == 1
    assert any("fidelity" in w for w in decision["ticket"]["warnings"])
    # warn-only: the route is untouched by a fidelity mismatch
    assert decision["route"] == "add"


def test_ticket_fidelity_accepts_diff_post_image(repo, memory_dir):
    _repo_with_cited_file(repo)
    hunk = (
        "@@ -1,3 +1,4 @@\n"
        " # header\n"
        "+def compute(x):\n"
        "+    return x * 41  # the answer minus one\n"
        " # footer\n"
    )
    body = "the change to src/mod.py, verbatim:\n\n```diff\n" + hunk + "```\n"
    decision = NM.check_candidate(
        "hotloop-diff", "the compute hunk", "project", body,
        memory_dir=memory_dir, repo_root=repo,
    )
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["checked"] == 1 and fid["matched"] == 1


def test_ticket_fidelity_unverifiable_states_are_notes_not_warnings(tmp_path, repo, memory_dir):
    # (a) no git repo at all -> note, no warning, never a crash
    md = _mk(str(tmp_path / "plain-mem"))
    body = "```python\n" + _SNIPPET + "```\n"
    decision = NM.check_candidate("nogit", "fenced but ungitted", "project", body, memory_dir=md)
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["blocks"] == 1
    assert fid["note"] and "HEAD" in fid["note"]
    assert not any("fidelity" in w for w in decision["ticket"]["warnings"])

    # (b) git repo, fenced block, but the body cites no resolvable path -> note, no warning
    _repo_with_cited_file(repo)
    decision = NM.check_candidate(
        "nocite", "fenced but pathless", "project", "```python\n" + _SNIPPET + "```\n",
        memory_dir=memory_dir, repo_root=repo,
    )
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["note"] and "cite" in fid["note"]
    assert not any("fidelity" in w for w in decision["ticket"]["warnings"])

    # (c) a tiny command block is too weak a claim to verify -> skipped, not warned
    decision = NM.check_candidate(
        "tinyblock", "a command note", "project",
        "run this in src/mod.py's repo:\n\n```\npytest -q\n```\n",
        memory_dir=memory_dir, repo_root=repo,
    )
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["checked"] == 0 and fid["mismatched"] == []


def test_ticket_no_fenced_blocks_is_silent(repo, memory_dir):
    _repo_with_cited_file(repo)
    decision = NM.check_candidate(
        "prose", "plain prose about src/mod.py", "project", "no fences here, just src/mod.py",
        memory_dir=memory_dir, repo_root=repo,
    )
    fid = decision["ticket"]["fence_fidelity"]
    assert fid["blocks"] == 0 and fid["note"] is None
    assert not any("fidelity" in w for w in decision["ticket"]["warnings"])


# --------------------------------------------------------------------------- #
# Archive-shadow collision
# --------------------------------------------------------------------------- #


def test_ticket_archive_shadow_warns_on_collision(tmp_path):
    md = _mk(str(tmp_path / "mem"))
    arch = os.path.join(md, "archive")
    os.makedirs(arch)
    with open(os.path.join(arch, "old-lesson.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: old-lesson\ndescription: \"retired\"\n---\n")
    decision = NM.check_candidate("old-lesson", "a new fact under a retired name", "project", memory_dir=md)
    shadow = decision["ticket"]["archive_shadow"]
    assert shadow["collides"] is True
    assert any("archive" in w for w in decision["ticket"]["warnings"])
    # warn-only, still routes add (empty live corpus)
    assert decision["route"] == "add"


def test_ticket_archive_shadow_clear_when_no_archive(tmp_path):
    md = _mk(str(tmp_path / "mem"))
    decision = NM.check_candidate("fresh", "a new fact", "project", memory_dir=md)
    assert decision["ticket"]["archive_shadow"]["collides"] is False


def test_write_memory_archive_shadow_warns_but_still_writes(tmp_path):
    md = _mk(str(tmp_path / "mem"))
    arch = os.path.join(md, "archive")
    os.makedirs(arch)
    with open(os.path.join(arch, "old-lesson.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: old-lesson\ndescription: \"retired\"\n---\n")
    res = NM.write_memory("old-lesson", "a new fact under a retired name", "project", memory_dir=md)
    assert res["created"] is True  # warn, never block
    assert any("archive" in w for w in res["warnings"])


# --------------------------------------------------------------------------- #
# The contract pins: never-raise, no-autonomous-rejection, naming
# --------------------------------------------------------------------------- #


def test_ticket_never_raises_and_never_flips_route(tmp_path, monkeypatch):
    """THE SEN-1 pin: whatever breaks under the ticket, check_candidate still answers."""
    md = _mk(str(tmp_path / "mem"))
    # archive is a FILE (listing/isdir probes get a weird tree), secrets + git_head raise
    with open(os.path.join(md, "archive"), "w", encoding="utf-8") as fh:
        fh.write("not a directory")

    from memory import secrets as S

    def boom(*a, **k):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(S, "scan_with_remediation", boom)
    import memory.provenance as P

    monkeypatch.setattr(P, "git_head", boom)

    decision = NM.check_candidate(
        "sturdy", "a fact that survives broken checkers", "project",
        "```python\nx = 1  # long enough to be a checkable block, honest\n```\n",
        memory_dir=md,
    )
    assert decision["route"] in ("add", "review")
    assert isinstance(decision["ticket"], dict)
    # degraded fields, not exceptions
    assert decision["ticket"]["secret_warnings"] == []
    assert decision["ticket"]["archive_shadow"]["collides"] in (False, None)


def test_ticket_findings_never_reject_the_write(tmp_path):
    """A candidate flagged three ways still writes: every check is warn-only (ED-1/inv4)."""
    md = _mk(str(tmp_path / "mem"))
    arch = os.path.join(md, "archive")
    os.makedirs(arch)
    with open(os.path.join(arch, "triple.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: triple\ndescription: \"retired\"\n---\n")
    body = "key AKIA" + "ABCDEFGHIJKLMNOP" + "\n\n```python\nnot in any file, long enough to check\n```\n"
    res = NM.write_memory("triple", "flagged three ways", "project", body, memory_dir=md)
    assert res["created"] is True
    assert os.path.isfile(os.path.join(md, "triple.md"))


def test_ticket_renders_verbatim_and_never_says_receipt(tmp_path, capsys):
    md = _mk(str(tmp_path / "mem"))
    arch = os.path.join(md, "archive")
    os.makedirs(arch)
    with open(os.path.join(arch, "shadowed.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: shadowed\ndescription: \"retired\"\n---\n")

    decision = NM.check_candidate("shadowed", "a shadowed candidate", "project", memory_dir=md)
    block = NM.render_write_ticket(decision["ticket"])
    assert "write ticket" in block
    assert "secret lint" in block and "hunk fidelity" in block and "archive shadow" in block
    # inv5: "receipt" is GOV-5's word (the recall receipt). The ticket never uses it.
    assert "receipt" not in block.lower()

    # the CLI --check surface prints the ticket at the same approval-prompt step
    rc = NM.main(["shadowed", "a shadowed candidate", "--type", "project", "--check", "--memory-dir", md])
    out = capsys.readouterr().out
    assert rc == 0
    assert "write ticket" in out
    assert "archive shadow" in out and "⚠" in out


def test_write_memory_result_carries_the_same_ticket_shape(tmp_path):
    md = _mk(str(tmp_path / "mem"))
    res = NM.write_memory("plain", "an ordinary write", "project", "body", memory_dir=md)
    assert res["created"] is True
    ticket = res["ticket"]
    assert set(ticket) >= {"secret_warnings", "fence_fidelity", "archive_shadow", "warnings"}
    # a clean write carries an empty warning set — the ticket adds no noise
    assert ticket["warnings"] == []


def test_ticket_module_never_coins_receipt_in_source():
    """inv5 source pin: the SEN-1 additions in new_memory.py never name the artifact a
    'receipt' — that word is GOV-5's shipped recall glass-box."""
    import inspect

    src = inspect.getsource(NM)
    sen1 = src[src.find("SEN-1") :] if "SEN-1" in src else ""
    assert sen1, "expected the SEN-1 ticket block to exist in new_memory.py"
    assert "write ticket" in sen1
    assert "receipt" not in sen1.lower()
