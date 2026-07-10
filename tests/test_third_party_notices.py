"""SEC-9 — the THIRD_PARTY_NOTICES license inventory stays in sync with the real deps.

These are drift guards, not prose checks: if a dependency is added to
``plugin/requirements.txt`` (or the embedding model changes) without being inventoried,
the notices file is silently wrong for a public launch — so the suite reddens instead.
"""

from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(__file__))
_NOTICES = os.path.join(_ROOT, "THIRD_PARTY_NOTICES")
_REQS = os.path.join(_ROOT, "plugin", "requirements.txt")


def _notices_text() -> str:
    with open(_NOTICES, "r", encoding="utf-8") as fh:
        return fh.read()


def _requirement_names() -> list:
    names = []
    with open(_REQS, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # strip the version specifier: name comes before any of < > = ! ~ [ ;
            name = re.split(r"[<>=!~;\[ ]", line, 1)[0].strip()
            if name:
                names.append(name)
    return names


def test_notices_file_exists():
    assert os.path.isfile(_NOTICES), "SEC-9: repo-root THIRD_PARTY_NOTICES must exist"


def test_every_direct_dependency_is_inventoried():
    text = _notices_text().lower()
    names = _requirement_names()
    assert names, "expected requirements.txt to declare dependencies"
    for name in names:
        assert name.lower() in text, f"{name} declared in requirements.txt but not in THIRD_PARTY_NOTICES"


def test_embedding_model_and_license_are_inventoried():
    text = _notices_text()
    # The default English model, its license, and the fact that it's a downloaded artifact.
    assert "bge-small-en-v1.5" in text
    assert "MIT" in text
    assert re.search(r"~?130\s*MB", text), "the ~130MB downloaded model artifact must be documented"


def test_notices_declares_only_permissive_licenses():
    # Guard against a future entry that quietly introduces a copyleft / source-available
    # obligation: the file's own summary promises permissive-only. "GPL" covers GPL/AGPL/LGPL.
    text = _notices_text()
    for forbidden in ("GPL", "SSPL"):
        assert forbidden not in text, f"unexpected non-permissive license token: {forbidden}"


def test_readme_points_to_notices():
    with open(os.path.join(_ROOT, "README.md"), "r", encoding="utf-8") as fh:
        readme = fh.read()
    assert "THIRD_PARTY_NOTICES" in readme, "README License section should link the notices file"
