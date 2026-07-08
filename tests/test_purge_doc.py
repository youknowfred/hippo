"""SEC-4 — the documented purge procedure: a doctor-referenced doc that covers removing a
memory, scrubbing git history, clearing index rows + ledgers, and verifying via recall that it
no longer surfaces. The single-sourced pointer lives in ``secrets.REMEDIATION`` so BOTH the
write-time warning and doctor's secret check name it.
"""

from __future__ import annotations

import os

from memory import doctor as D
from memory import secrets as S

from .conftest import write_file

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PURGE_DOC = os.path.join(_REPO, "plugin", "memory", "README.md")


def _mem(name, description, body="body"):
    return f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: project\n---\n{body}\n'


def test_remediation_names_the_purge_doc():
    assert "Purging a memory" in S.REMEDIATION
    assert "plugin/memory/README.md" in S.REMEDIATION


def test_purge_doc_section_exists_and_covers_every_step():
    with open(_PURGE_DOC, encoding="utf-8") as fh:
        text = fh.read()
    assert "## Purging a memory" in text
    section = text.split("## Purging a memory", 1)[1].split("\n## ", 1)[0]
    # The five acceptance steps: remove file, scrub history, clear index, clear ledgers, verify.
    assert "git rm" in section
    assert "git filter-repo" in section  # history scrub recipe
    assert "memory.build_index" in section  # clear index rows via rebuild
    assert ".memory-telemetry" in section  # clear ledgers
    assert "memory.recall" in section  # verify it no longer surfaces
    # It contrasts with the reversible/whole-project neighbours so a reader doesn't conflate them.
    assert "/hippo:archive" in section and "/hippo:remove" in section


def test_scan_with_remediation_carries_the_purge_pointer():
    flagged = S.scan_with_remediation("aws key AKIAIOSFODNN7EXAMPLE here")
    assert flagged  # something was flagged
    assert any("Purging a memory" in w for w in flagged)
    # A clean scan appends nothing.
    assert S.scan_with_remediation("ordinary safe prose") == []


def test_doctor_check_secrets_references_the_purge_doc(tmp_path):
    memory_dir = str(tmp_path / ".claude" / "memory")
    os.makedirs(memory_dir, exist_ok=True)
    write_file(memory_dir, "leak.md", _mem("leak", "note", body="key is AKIAIOSFODNN7EXAMPLE here"))
    r = D.check_secrets(D.DoctorContext(memory_dir, str(tmp_path)))
    assert r["status"] == "warn"
    assert "Purging a memory" in r["message"]  # SEC-4 pointer reaches doctor via REMEDIATION
