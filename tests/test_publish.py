"""PUB-1: the per-item publish verb — print-only pending Q3.

The vocabulary is three-way and the tests hold each lane to its verdict: REFUSALS are
mechanical only (docs; already-tracked), the GATE is ``review.lint_touched`` reused
with entropy ON as the only delta, and expired/contradicted memories are ADVISORY
receipt warnings that never refuse. The load-bearing pins: publish.py never invokes
``git add``/``git commit`` (print-only — Q3 pending), never transforms content
(byte-identical in place), issues no fresh ``ls-files`` (the single-homed membership
oracle), and offers no 'all' affordance.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from .conftest import git_commit, write_file

from memory import publish as P


def _mem(name: str, body: str = "body", extra_meta: str = "") -> str:
    return (
        f"---\nname: {name}\ndescription: d for {name}\nmetadata:\n  type: project\n"
        f"{extra_meta}---\n{body}\n"
    )


def _corpus(repo: str):
    """Committed pub_a (links [[loc_c]] so loc_c heals 1); local-only loc_c."""
    write_file(repo, ".claude/memory/pub_a.md", _mem("pub_a", body="see [[loc_c]]"))
    git_commit(repo, "public subset", 1_700_000_000)
    write_file(repo, ".claude/memory/loc_c.md", _mem("loc_c"))


# --------------------------------------------------------------------------- #
# Mechanical refusals ONLY
# --------------------------------------------------------------------------- #
def test_refuses_docs_mechanically(repo, memory_dir):
    _corpus(repo)
    r = P.publish_preflight("MEMORY.md", memory_dir, repo)
    assert r["refusal"] and "not a memory file" in r["refusal"]
    assert P.publish_preflight("CONVENTIONS.md", memory_dir, repo)["refusal"]


def test_refuses_already_tracked_as_update(repo, memory_dir):
    _corpus(repo)
    r = P.publish_preflight("pub_a", memory_dir, repo)
    assert r["refusal"] and "already tracked" in r["refusal"]
    assert "ride plain git" in r["refusal"]


def test_refuses_missing_memory(repo, memory_dir):
    _corpus(repo)
    assert "no memory named" in P.publish_preflight("ghost", memory_dir, repo)["refusal"]


# --------------------------------------------------------------------------- #
# The happy path: print-only act + receipt cross-references
# --------------------------------------------------------------------------- #
def test_ready_preflight_prints_add_f_and_commit(repo, memory_dir):
    _corpus(repo)
    r = P.publish_preflight("loc_c", memory_dir, repo)
    assert r["refusal"] is None and r["ok"] is True
    assert r["commands"] == [
        'git add -f ".claude/memory/loc_c.md"',
        'git commit -m "memory: publish loc_c"',
    ]
    assert r["receipt"]["heals"] == 1  # PUB-3's column, cross-referenced display-only
    text = P.render_preflight(r)
    assert "PRINT-ONLY pending Q3" in text and 'git add -f ".claude/memory/loc_c.md"' in text


def test_preflight_touches_neither_file_nor_git_index(repo, memory_dir):
    """Publish NEVER transforms content and NEVER stages: byte-identity + clean index."""
    _corpus(repo)
    path = os.path.join(memory_dir, "loc_c.md")
    with open(path, "rb") as fh:
        before = fh.read()
    P.publish_preflight("loc_c", memory_dir, repo)
    with open(path, "rb") as fh:
        assert fh.read() == before  # byte-identical in place
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert staged == ""  # nothing staged — the act is the PRINTED command only
    tracked = subprocess.run(
        ["git", "ls-files", "--", ".claude/memory/loc_c.md"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tracked == ""  # still untracked


def test_publish_source_never_invokes_git_add_or_commit():
    """The Q3 pin: every run_git call in publish.py is read-only (rev-parse), and no
    subprocess escape hatch exists. The printed STRINGS carry git add/commit — the
    code never executes them."""
    import inspect
    import re

    src = inspect.getsource(P)
    assert "subprocess" not in src
    for args in re.findall(r"run_git\(\[([^\]]*)\]", src):
        assert "rev-parse" in args, f"non-read-only run_git call: {args}"
    assert "ls-files" not in src  # membership rides the imported oracle only


def test_no_all_affordance(repo, memory_dir, capsys):
    """One name per invocation: two positionals are a usage error; 'all' is just a
    (missing) name, not a bulk mode; no --all flag exists."""
    import inspect

    assert "--all" not in inspect.getsource(P)
    with pytest.raises(SystemExit) as exc:
        P.main(["a", "b"])
    assert exc.value.code == 2
    capsys.readouterr()
    _corpus(repo)
    r = P.publish_preflight("all", memory_dir, repo)
    assert r["refusal"] and "no memory named" in r["refusal"]


# --------------------------------------------------------------------------- #
# Gate vs advisory — the vetting's verdicts hold
# --------------------------------------------------------------------------- #
def test_gate_blocks_on_secret_and_entropy_is_the_only_delta(repo, memory_dir):
    _corpus(repo)
    write_file(
        repo,
        ".claude/memory/leaky.md",
        _mem("leaky", body="token: AKIAIOSFODNN7REALKE1"),
    )
    r = P.publish_preflight("leaky", memory_dir, repo)
    assert r["refusal"] is None  # a gate finding is NOT a mechanical refusal
    assert r["ok"] is False and any(f["lint"] == "secrets" for f in r["gate"])
    assert r["commands"]  # computed but not "ready" — render withholds them
    assert "NOT ready" in P.render_preflight(r)


def test_gate_entropy_on_catches_what_ci_entropy_off_misses(repo, memory_dir):
    """The superset claim, pinned: a high-entropy blob passes the CI-shaped scan
    (entropy=False) and is caught by the publish gate (entropy=True)."""
    from memory.review import lint_touched

    _corpus(repo)
    blob = "Z9kQ3vTn8bXr5wLm2cJp7dHs4fGa6yEuRtYiOpAsDfGhJkLzXcVbNm1QwErTyUi"
    text = _mem("entropic", body=f"artifact checksum {blob}")
    ci_shaped = lint_touched({"entropic": text}, memory_dir, repo)
    publish_shaped = lint_touched({"entropic": text}, memory_dir, repo, entropy=True)
    ci_secrets = [f for f in ci_shaped["gate"] if f["lint"] == "secrets"]
    pub_secrets = [f for f in publish_shaped["gate"] if f["lint"] == "secrets"]
    assert not ci_secrets and pub_secrets  # strictly more, one run


def test_expired_invalid_after_is_advisory_never_refusal(repo, memory_dir):
    _corpus(repo)
    write_file(
        repo,
        ".claude/memory/expired.md",
        _mem("expired", extra_meta="  invalid_after: 2020-01-01\n"),
    )
    r = P.publish_preflight("expired", memory_dir, repo)
    assert r["refusal"] is None and r["ok"] is True  # advisory, not gate, not refusal
    assert any(f["lint"] == "invalid_after" for f in r["advisory"])
    assert "never refused" in P.render_preflight(r)


def test_unresolved_contradicts_is_advisory_never_refusal(repo, memory_dir):
    _corpus(repo)
    write_file(
        repo,
        ".claude/memory/rebel.md",
        _mem("rebel", extra_meta="contradicts: [pub_a]\n"),
    )
    r = P.publish_preflight("rebel", memory_dir, repo)
    assert r["refusal"] is None and r["ok"] is True
    assert any(f["lint"] == "contradicts" for f in r["advisory"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_exit_codes(repo, memory_dir, capsys):
    _corpus(repo)
    assert P.main(["loc_c", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    assert "git add -f" in capsys.readouterr().out
    assert P.main(["pub_a", "--memory-dir", memory_dir, "--repo-root", repo]) == 2
    capsys.readouterr()
    write_file(repo, ".claude/memory/leaky.md", _mem("leaky", body="AKIAIOSFODNN7REALKE1"))
    assert P.main(["leaky", "--memory-dir", memory_dir, "--repo-root", repo]) == 1
    capsys.readouterr()


def test_cli_json_shape(repo, memory_dir, capsys):
    _corpus(repo)
    assert P.main(["loc_c", "--json", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"name", "ok", "refusal", "gate", "advisory", "receipt", "commands"}


def test_derivation_disclosure_rides_the_receipt(repo, memory_dir):
    from memory.provenance import CITATION_DERIVATION_VERSION

    _corpus(repo)
    write_file(
        repo,
        ".claude/memory/.format",
        json.dumps({"corpus_format": 5, "cite_derivation": CITATION_DERIVATION_VERSION - 1}),
    )
    r = P.publish_preflight("loc_c", memory_dir, repo)
    assert r["receipt"]["derivation"] == {
        "corpus": CITATION_DERIVATION_VERSION - 1,
        "plugin": CITATION_DERIVATION_VERSION,
    }
    assert "disclosed, not blocking" in P.render_preflight(r)
    assert r["ok"] is True  # disclosure never blocks