"""SEC-11 — supply-chain posture is enforced and documented.

A universal exact/hash lock is infeasible for hippo's CPython 3.10–3.14 matrix (no single
numpy spans it), so the deps stay RANGE-pinned. The concrete, enforceable guarantee is that
every range is BOUNDED ON BOTH SIDES — a compromised/breaking new major can never be pulled —
and that the hardened per-environment install path and the model artifact's integrity are
documented for anyone who wants reproducibility.
"""

from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(__file__))
_REQS = os.path.join(_ROOT, "plugin", "requirements.txt")


def _read() -> str:
    with open(_REQS, "r", encoding="utf-8") as fh:
        return fh.read()


def _requirement_lines() -> list:
    return [
        ln.strip()
        for ln in _read().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_every_requirement_is_upper_bounded():
    """No unbounded `>=`: each dependency caps its major so a bad release can't slip in."""
    reqs = _requirement_lines()
    assert reqs, "expected requirements.txt to declare dependencies"
    for spec in reqs:
        assert "<" in spec, f"dependency is not upper-bounded (supply-chain risk): {spec!r}"
        # and it's a real lower..upper range, not just a bare upper bound
        assert ">=" in spec or "==" in spec or "~=" in spec, f"no lower bound pinned: {spec!r}"


def test_hardened_install_recipe_is_documented():
    text = _read()
    # The per-environment hash-locked install path must be discoverable where deps live.
    assert "--generate-hashes" in text
    assert "--require-hashes" in text


def test_model_artifact_integrity_is_documented():
    text = _read()
    assert "huggingface_hub" in text
    assert re.search(r"~?130\s*MB", text)
    # names the integrity property (content-addressed / verified on fetch)
    assert "content-addressed" in text or "verifies" in text or "integrity" in text
