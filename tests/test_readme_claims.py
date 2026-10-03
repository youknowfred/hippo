"""CON-2: README claims the telemetry falsified stay gone.

"Recall costs zero tokens" sat beside about 28.5k injected chars per session, and "no other
tool checks whether the code a memory cites has moved" was contradicted by GitHub Copilot
Memory's citation check (ROADMAP.v2 §0). Positioning prose is allowed to change; these two
sentences are not allowed back.
"""

from __future__ import annotations

import os
import re

_README = os.path.join(os.path.dirname(__file__), "..", "README.md")


def _flat() -> str:
    with open(_README, encoding="utf-8") as fh:
        return " ".join(fh.read().split()).lower()


def test_recall_is_not_claimed_to_cost_zero_tokens():
    text = _flat()
    assert not re.search(r"zero tokens|no llm, tokens|0 tokens per prompt", text)


def test_staleness_uniqueness_is_not_claimed():
    text = _flat()
    assert "no other tool checks" not in text and "nothing else reproduces" not in text


def test_native_memory_is_not_called_unranked():
    assert "static and unranked" not in _flat()
