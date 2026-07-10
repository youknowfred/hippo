"""SEC-10 — SECURITY.md exists and carries the launch-required disclosure content.

Drift guards for the public-launch security policy: a private reporting channel, a
supported-versions statement, and a working pointer to the SEC-4 purge procedure.
"""

from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(__file__))
_SECURITY = os.path.join(_ROOT, "SECURITY.md")


def _text() -> str:
    with open(_SECURITY, "r", encoding="utf-8") as fh:
        return fh.read()


def test_security_md_exists():
    assert os.path.isfile(_SECURITY), "SEC-10: repo-root SECURITY.md must exist"


def test_declares_a_private_disclosure_channel():
    text = _text()
    # GitHub private vulnerability reporting, and an explicit "report privately" instruction.
    assert "security/advisories/new" in text
    assert re.search(r"privately|do not open a public", text, re.IGNORECASE)


def test_has_a_supported_versions_section():
    text = _text()
    assert re.search(r"supported versions", text, re.IGNORECASE)
    # a markdown table row (the version matrix)
    assert "| Version" in text or "|---" in text


def test_points_to_the_purge_procedure_and_target_exists():
    text = _text()
    # The SEC-4 pointer for an accidentally-committed secret.
    m = re.search(r"\(([^)]*memory/README\.md)\)", text)
    assert m, "SECURITY.md must link the purge procedure in plugin/memory/README.md"
    target = os.path.normpath(os.path.join(_ROOT, m.group(1)))
    assert os.path.exists(target), f"purge-doc link target missing: {m.group(1)}"


def test_readme_points_to_security_policy():
    with open(os.path.join(_ROOT, "README.md"), "r", encoding="utf-8") as fh:
        readme = fh.read()
    assert "SECURITY.md" in readme, "README should link SECURITY.md for discoverability"
