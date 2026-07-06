"""Tests for the SEC-2 secret-pattern lint — write-time warning + doctor corpus scan.

Hermetic: build a tmp memory dir; new_memory tests disable dense + pin CLAUDE_PROJECT_DIR
to tmp (same pattern as test_creation_convention.py). The single detector lives in
``memory.secrets`` and is exercised directly AND through both surfaces (new_memory, doctor).
"""

from __future__ import annotations

import os

from memory import secrets as S

# Obviously-fake placeholders that match the shapes but are not live credentials.
_FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # AKIA + 16 = the canonical AWS docs example
_FAKE_GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"  # ghp_ + 36 chars
_FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


_CLEAN_FLOOR = """# IC Memobot — Auto-Memory Index (durable floor)
> Always-loaded floor: the User + Working-Style memories.
## User
- [User Role](user_role.md) — solo founder.
## Working Style & Process Feedback
"""


def _floor(md, body):
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(body)


def _nm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic resolve_dirs
    md = str(tmp_path / ".claude" / "memory")
    _floor(md, _CLEAN_FLOOR)
    return md


# --------------------------------------------------------------------------- #
# secrets.scan_text — the single detector both surfaces share
# --------------------------------------------------------------------------- #
def test_scan_text_flags_aws_key():
    warnings = S.scan_text(f"the key is {_FAKE_AWS_KEY} in the trace")
    assert any("AWS access key" in w for w in warnings)


def test_scan_text_flags_github_token():
    assert any("GitHub token" in w for w in S.scan_text(f"token={_FAKE_GH_TOKEN}"))


def test_scan_text_flags_private_key_block():
    assert any("private key" in w for w in S.scan_text(_FAKE_PEM))


def test_scan_text_clean_on_ordinary_prose():
    prose = (
        "This memory documents how the deploy pipeline works: run the build, push to the "
        "registry, and the webhook triggers a rollout. Nothing secret here at all."
    )
    assert S.scan_text(prose) == []


def test_scan_text_no_false_positive_on_hex_sha():
    # A 40-char git sha is single-class-ish (hex) — must NOT trip the entropy catch-all.
    assert S.scan_text("baseline sha 3f9a1c2e4b6d8f0a1c2e4b6d8f0a1c2e4b6d8f0a") == []


def test_scan_text_never_echoes_the_secret():
    for w in S.scan_text(f"key {_FAKE_AWS_KEY}"):
        assert _FAKE_AWS_KEY not in w  # warning names the KIND, never the matched text


def test_scan_with_remediation_appends_pointer_only_when_flagged():
    flagged = S.scan_with_remediation(f"key {_FAKE_AWS_KEY}")
    assert any("AWS access key" in w for w in flagged)
    assert any("rotate the credential" in w for w in flagged)
    # clean text → empty (no remediation noise)
    assert S.scan_with_remediation("ordinary safe prose about deploys") == []


def test_scan_text_never_raises_on_bad_input():
    assert S.scan_text("") == []


# --------------------------------------------------------------------------- #
# new_memory — WARN-not-block at write time
# --------------------------------------------------------------------------- #
def test_write_memory_aws_key_in_body_warns_but_creates(tmp_path, monkeypatch):
    """AWS-style key in a body triggers the warning path AND the file is still created."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory(
        "leaky_project_mem",
        "a project note that unfortunately pasted a credential",
        "project",
        body=f"we hit an error using the key {_FAKE_AWS_KEY} against s3",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    # The write is NOT blocked — the file exists and created is True.
    assert res["created"] is True and res["error"] is None
    assert os.path.exists(os.path.join(md, "leaky_project_mem.md"))
    # ...but a non-empty warnings list surfaced the AWS-key match + remediation pointer.
    assert res["warnings"], "expected a non-empty warnings list for an AWS-style key"
    assert any("AWS access key" in w for w in res["warnings"])
    assert any("rotate the credential" in w for w in res["warnings"])
    # never echo the actual secret back to the caller
    assert all(_FAKE_AWS_KEY not in w for w in res["warnings"])


def test_write_memory_clean_body_has_empty_warnings(tmp_path, monkeypatch):
    """A normal, secret-free memory produces an empty warnings list (no false positive)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory(
        "clean_project_mem",
        "a perfectly ordinary project note about the build",
        "project",
        body="The build runs in CI and publishes an artifact. Nothing secret in here.",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None
    assert res["warnings"] == []


def test_write_memory_result_always_has_warnings_key(tmp_path, monkeypatch):
    """The result-dict shape always carries a warnings list, even on the early-return paths."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    bad = NM.write_memory("x", "d", "bogus", memory_dir=md, repo_root=str(tmp_path))
    assert bad["warnings"] == []  # invalid-type early return still has the key


# --------------------------------------------------------------------------- #
# secrets.scan_corpus — doctor's corpus-wide sweep
# --------------------------------------------------------------------------- #
def test_scan_corpus_flags_only_the_leaky_file(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "clean.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: clean\ndescription: d\n---\njust a normal note\n")
    with open(os.path.join(md, "leaky.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: leaky\ndescription: d\n---\nkey {_FAKE_AWS_KEY} here\n")
    # MEMORY.md floor is excluded by _iter_memory_files even if it contained a match.
    _floor(md, _CLEAN_FLOOR)

    findings = S.scan_corpus(md)
    files = {f["file"] for f in findings}
    assert files == {"leaky.md"}  # clean.md omitted, MEMORY.md excluded
    leaky = next(f for f in findings if f["file"] == "leaky.md")
    assert any("AWS access key" in w for w in leaky["warnings"])
    assert all(_FAKE_AWS_KEY not in w for w in leaky["warnings"])


def test_scan_corpus_clean_corpus_returns_empty(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: a\ndescription: d\n---\nordinary content\n")
    assert S.scan_corpus(md) == []


def test_scan_corpus_missing_dir_never_raises(tmp_path):
    assert S.scan_corpus(str(tmp_path / "no_such_dir")) == []
