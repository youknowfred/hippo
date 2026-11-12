"""Acknowledging a cleared lint finding: counted, not warned; any edit re-arms it.

Covers the policy key's parse (malformed entries drop), doctor's corpus secret + threat
checks (acknowledged findings counted, a one-byte edit brings each back and names the
stale acknowledgement, the consent rule for a list that arrived by pull), byte-identical
output on a corpus with no list, the repo-wide release scan ignoring the list, and the
review / --ci path honouring only acknowledgements at the BASE of the range.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from memory import doctor as D
from memory import lint_ack as A
from memory import review as RV
from memory import secrets as S
from memory import trust as T

from .conftest import git_commit, write_file

# Intentional detector vectors (tests are exempt from the repo secret scan by design).
_ENTROPY_VEC = "wJalrXUtnFEMI/" + "K7MDENG/bPxRfiCYEXAMPLEKEY"
_FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def _mem(name: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "a note"\ntype: project\n---\n{body}\n'


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _ack(file: str, rule: str, sha: str, **kw) -> dict:
    e = {"file": file, "rule": rule, "sha256": sha, "reason": "reviewed, a fixture", "date": "2026-11-12"}
    e.update(kw)
    return e


def _policy(memory_dir: str, entries, **extra) -> None:
    doc = dict(extra)
    if entries is not None:
        doc[A.ACK_KEY] = entries
    with open(os.path.join(memory_dir, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _ctx(memory_dir: str, repo: str) -> D.DoctorContext:
    return D.DoctorContext(memory_dir, repo, plugin_data="", plugin_root="")


@pytest.fixture
def consented(monkeypatch):
    """Run the real trust gate (the suite bypasses it by default)."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)

    def _consent(repo, memory_dir):
        assert T.mark_trusted(repo, memory_dir=memory_dir)

    return _consent


def _corpus(memory_dir):
    leak = write_file(memory_dir, "leak.md", _mem("leak", f"fixture id {_ENTROPY_VEC} here"))
    note = write_file(memory_dir, "note.md", _mem("note", "a template <!-- marker --> line"))
    return leak, note


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #
def test_malformed_entries_drop_without_raising():
    good = _ack("a.md", "html-comment", "ab" * 32)
    raw = [
        good,
        "not a dict",
        None,
        {k: v for k, v in good.items() if k != "reason"},       # missing a field
        dict(good, sha256="zz" * 32),                             # non-hex hash
        dict(good, sha256="ab" * 31),                             # wrong length
        dict(good, file="sub/a.md"),                              # a path separator
        dict(good, file="..\\a.md"),
        dict(good, file=".."),
        dict(good, date="12 Nov 2026"),
        dict(good, rule=""),
        dict(good, reason=7),
    ]
    assert A.parse_acknowledgements(raw) == [good]
    for junk in (None, "x", 3, {"file": "a.md"}):
        assert A.parse_acknowledgements(junk) == []
    assert A.declared_in("{not json") == (False, [])
    assert A.declared_in("[1, 2]") == (False, [])
    assert A.declared_in('{"acknowledged_findings": "nope"}') == (True, [])


def test_upper_case_hash_is_normalised():
    e = A.parse_acknowledgements([_ack("a.md", "x", "AB" * 32)])
    assert e and e[0]["sha256"] == "ab" * 32


def test_rule_ids_name_the_kind_never_the_text():
    assert A.rule_id("possible high-entropy secret detected") == "high-entropy-secret"
    assert A.rule_id("possible AWS access key detected") == "aws-access-key"
    assert A.rule_id("HTML comment: 3 comment(s) (hidden-instruction channel; lint-only, not neutralized)") == "html-comment"
    assert A.rule_id("invisible Unicode: 2 zero-width character(s)") == "zero-width"
    assert A.rule_id("exfil shape: external image embed (auto-fetched beacon)") == "image-embed"


def test_garbled_policy_file_reads_as_undeclared(memory_dir):
    with open(os.path.join(memory_dir, "hippo.json"), "w", encoding="utf-8") as fh:
        fh.write("{garbled")
    assert A.read_acknowledgements(memory_dir) == (False, [])


# --------------------------------------------------------------------------- #
# Doctor: acknowledged findings stop warning, are counted, and re-arm on any edit
# --------------------------------------------------------------------------- #
def test_acknowledged_entropy_and_html_comment_findings_are_counted_not_warned(repo, memory_dir, consented):
    leak, note = _corpus(memory_dir)
    _policy(memory_dir, [
        _ack("leak.md", "high-entropy-secret", _sha(leak)),
        _ack("note.md", "html-comment", _sha(note)),
    ])
    consented(repo, memory_dir)

    sec = D.check_secrets(_ctx(memory_dir, repo))
    thr = D.check_threat_lint(_ctx(memory_dir, repo))
    assert sec["status"] == "ok", sec
    assert thr["status"] == "ok", thr
    assert "1 acknowledged finding(s) not warned" in sec["message"]
    assert "1 acknowledged finding(s) not warned" in thr["message"]
    assert _ENTROPY_VEC not in sec["message"]


def test_one_byte_edit_rearms_each_and_doctor_names_the_stale_acknowledgement(repo, memory_dir, consented):
    leak, note = _corpus(memory_dir)
    _policy(memory_dir, [
        _ack("leak.md", "high-entropy-secret", _sha(leak)),
        _ack("note.md", "html-comment", _sha(note)),
    ])
    consented(repo, memory_dir)
    for path in (leak, note):
        with open(path, "ab") as fh:
            fh.write(b"x")
    consented(repo, memory_dir)  # even re-consented, the reviewed bytes are gone

    sec = D.check_secrets(_ctx(memory_dir, repo))
    thr = D.check_threat_lint(_ctx(memory_dir, repo))
    assert sec["status"] == "warn" and "leak.md" in sec["message"]
    assert thr["status"] == "warn" and "note.md" in thr["message"]
    assert "acknowledgement for leak.md (high-entropy-secret) no longer matches the file" in sec["message"]
    assert "acknowledgement for note.md (html-comment) no longer matches the file" in thr["message"]
    # The warn line tells the user how to acknowledge, and gives the hash to copy.
    assert A.ACK_KEY in sec["message"] and _sha(leak) in sec["message"]
    assert "[rule high-entropy-secret]" in sec["message"]
    assert "[rule html-comment]" in thr["message"] and _sha(note) in thr["message"]
    assert _ENTROPY_VEC not in sec["message"]
    for r in (sec, thr):
        assert "\n" not in r["message"]


def test_acknowledgement_only_covers_its_own_rule(repo, memory_dir, consented):
    note = write_file(memory_dir, "note.md", _mem("note", f"<!-- c --> and {_FAKE_AWS}"))
    _policy(memory_dir, [_ack("note.md", "html-comment", _sha(note))])
    consented(repo, memory_dir)
    sec = D.check_secrets(_ctx(memory_dir, repo))
    assert sec["status"] == "warn" and "aws-access-key" in sec["message"]
    assert _FAKE_AWS not in sec["message"]


def test_untrusted_corpus_does_not_honour_the_list(repo, memory_dir, consented):
    """The list is not in the consent fingerprint: an acknowledgement that arrived by pull
    into a corpus this machine never consented to must not silence anything."""
    leak, _note = _corpus(memory_dir)
    _policy(memory_dir, [_ack("leak.md", "high-entropy-secret", _sha(leak))])
    sec = D.check_secrets(_ctx(memory_dir, repo))
    assert sec["status"] == "warn" and "leak.md" in sec["message"]
    assert "is not applied: this corpus is not trusted on this machine" in sec["message"]


def test_drifted_file_does_not_honour_the_list(repo, memory_dir, consented):
    """Consented to the OLD bytes; the file and its acknowledgement both arrived later (a
    pull) — the acknowledged content was never consented here, so it still warns."""
    write_file(memory_dir, "leak.md", _mem("leak", "harmless"))
    consented(repo, memory_dir)
    leak = write_file(memory_dir, "leak.md", _mem("leak", f"fixture id {_ENTROPY_VEC} here"))
    _policy(memory_dir, [_ack("leak.md", "high-entropy-secret", _sha(leak))])
    sec = D.check_secrets(_ctx(memory_dir, repo))
    assert sec["status"] == "warn"
    assert "the file changed since you consented to this corpus" in sec["message"]


# --------------------------------------------------------------------------- #
# No list: byte-identical output
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("policy", [None, {"fold_digests": ["folded-*.md"]}])
def test_corpus_with_no_list_renders_byte_identical_output(repo, memory_dir, policy):
    _corpus(memory_dir)
    if policy is not None:
        _policy(memory_dir, None, **policy)
    sec = D.check_secrets(_ctx(memory_dir, repo))
    assert sec == {
        "status": "warn",
        "message": "1 file(s) contain secret-looking content — leak.md: possible high-entropy "
        f"secret detected. {S.REMEDIATION}.",
    }
    thr = D.check_threat_lint(_ctx(memory_dir, repo))
    assert thr["message"].startswith(
        "1 file(s) carry Tier-A threat payloads — note.md: HTML comment: 1 comment(s) "
        "(hidden-instruction channel; lint-only, not neutralized). Inspect before they "
        "re-inject on recall (HTML comments are lint-only by the dated spike decision, "
        "2026-07-16)."
    )
    assert "acknowledg" not in sec["message"] + thr["message"]


def test_clean_corpus_with_no_list_is_unchanged(repo, memory_dir):
    write_file(memory_dir, "fine.md", _mem("fine", "nothing here"))
    assert D.check_secrets(_ctx(memory_dir, repo)) == {
        "status": "ok", "message": "no secret-looking content in the corpus."
    }


# --------------------------------------------------------------------------- #
# The repo-wide release scan never reads the list
# --------------------------------------------------------------------------- #
def test_repo_wide_scan_ignores_the_list(repo, memory_dir, capsys):
    leak = write_file(memory_dir, "leak.md", _mem("leak", f"key {_FAKE_AWS}"))
    _policy(memory_dir, [_ack("leak.md", "aws-access-key", _sha(leak))])
    git_commit(repo, "corpus", 1_700_000_000)
    assert S.main(["--repo", repo]) == 1
    out = capsys.readouterr().out
    assert "leak.md" in out and _FAKE_AWS not in out


# --------------------------------------------------------------------------- #
# Review / --ci: only the BASE of the range counts
# --------------------------------------------------------------------------- #
def _review(repo, memory_dir, argv):
    return RV.run(argv, memory_dir=memory_dir, repo_root=repo)


def test_ci_cannot_be_silenced_by_an_acknowledgement_in_the_same_diff(repo, memory_dir):
    git_commit(repo, "seed", 1_700_000_000)
    leak = write_file(memory_dir, "leak.md", _mem("leak", f"key {_FAKE_AWS}"))
    _policy(memory_dir, [_ack("leak.md", "aws-access-key", _sha(leak))])
    git_commit(repo, "content + its own acknowledgement", 1_700_000_100)
    code, text = _review(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 1
    assert "this range adds 1 acknowledgement(s) to hippo.json — leak.md (aws-access-key)" in text
    assert _FAKE_AWS not in text


def test_ci_honours_an_acknowledgement_already_at_the_base(repo, memory_dir):
    note = write_file(memory_dir, "note.md", _mem("note", "a template <!-- marker --> line"))
    _policy(memory_dir, [_ack("note.md", "html-comment", _sha(note))])
    git_commit(repo, "reviewed acknowledgement", 1_700_000_000)
    # The file is touched by the range (a rename carries the same bytes) …
    os.rename(note, os.path.join(memory_dir, "note-2.md"))
    _policy(memory_dir, [
        _ack("note.md", "html-comment", _sha(os.path.join(memory_dir, "note-2.md"))),
        _ack("note-2.md", "html-comment", _sha(os.path.join(memory_dir, "note-2.md"))),
    ])
    git_commit(repo, "rename", 1_700_000_100)
    code, text = _review(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    # … but the base only acknowledged note.md, so the renamed file still gates.
    assert code == 1 and "note-2" in text

    # A touched file whose bytes match a BASE acknowledgement is counted, not gated.
    write_file(memory_dir, "other.md", _mem("other", "a template <!-- marker --> line"))
    git_commit(repo, "add other", 1_700_000_200)
    sha = _sha(os.path.join(memory_dir, "other.md"))
    _policy(memory_dir, [_ack("other.md", "html-comment", sha)])
    git_commit(repo, "acknowledge other", 1_700_000_300)
    with open(os.path.join(memory_dir, "other.md"), "a", encoding="utf-8") as fh:
        fh.write("tail\n")
    git_commit(repo, "edit other", 1_700_000_400)
    code, text = _review(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 1 and "other" in text  # an edit re-arms it at review time too
    with open(os.path.join(memory_dir, "other.md"), "rb") as fh:
        data = fh.read()
    with open(os.path.join(memory_dir, "other.md"), "wb") as fh:
        fh.write(data[: -len(b"tail\n")])
    git_commit(repo, "revert edit", 1_700_000_500)
    code, text = _review(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 0
    assert "1 gate finding(s) acknowledged in hippo.json at the base of this range" in text


def test_review_with_no_list_is_unchanged(repo, memory_dir):
    git_commit(repo, "seed", 1_700_000_000)
    write_file(memory_dir, "leak.md", _mem("leak", f"key {_FAKE_AWS}"))
    git_commit(repo, "leak", 1_700_000_100)
    code, text = _review(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 1
    assert "### lints (touched files only)\nGATE findings — these fail `--ci` (secret / threat Tier-A):\n" \
           "- `leak`: secrets — possible AWS access key detected" in text
    assert "acknowledg" not in text
