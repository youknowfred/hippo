"""COR-20 + RCH-10: two link-hygiene defects found dogfooding the 2026-07-16 drain.

Both were live on this repo's own corpus, and both are the same shape the QA sweep
kept finding: a mechanism that is silently wrong, discovered only by a human reading
a report and asking "wait, why is THAT there?".

  - COR-20: ``links.parse_wikilinks`` is a bare regex with no code-span awareness, so
    a memory that merely WRITES ABOUT wikilinks mints phantom edges. Four of the six
    dangling targets in this repo's corpus were prose — ``[[child]]``, ``[[wikilink]]``,
    ``[[wikilinks]]`` — and the lint reported them as broken references forever.
    Backticking does NOT help (the regex ignores code spans), which is the trap: the
    obvious fix reads as done and changes nothing.
  - RCH-10: ``new_memory(links=[...])`` never checked that a target resolves, so it
    silently minted a dangling edge. Reproduced live: a ``links=["user_role"]`` write
    succeeded clean, and the dangling link surfaced only in a later lint run.
    Cross-tier is the common cause and deserves its own sentence — ``user_role`` and
    ``hippo-machine-setup`` are REAL memories in the user tier, but the link graph is
    per-corpus, so a project→user-tier edge can never resolve. WARN, never block: a
    forward reference to a memory you plan to write is legitimate (RCH-9's discipline —
    name it, don't swallow it, don't refuse it).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from memory.links import build_graph, parse_wikilinks
from memory.new_memory import write_memory

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


# --------------------------------------------------------------------------- #
# COR-20 — the parser ignores code spans and fenced blocks
# --------------------------------------------------------------------------- #
def test_wikilink_in_inline_code_is_not_an_edge():
    """The exact live shape: prose ABOUT wikilinks became four phantom edges."""
    assert parse_wikilinks("append a `[[the-other-name]]` reference into ONE side") == []
    assert parse_wikilinks("untyped `[[wikilink]]` — zero schema bump") == []


def test_wikilink_in_a_fenced_block_is_not_an_edge():
    text = (
        "Here is the convention:\n\n"
        "```markdown\n"
        "Related: [[some-memory]]\n"
        "```\n\n"
        "…and that is all.\n"
    )
    assert parse_wikilinks(text) == []


def test_real_wikilinks_still_parse_around_code():
    """The fix must not cost a single real edge — prose links beside code still count."""
    text = (
        "See [[alpha]] for the rule.\n\n"
        "```python\n"
        "x = 1  # [[not-a-link]]\n"
        "```\n\n"
        "Related: [[beta]], `[[gamma]]`, [[delta]]\n"
    )
    assert parse_wikilinks(text) == ["alpha", "beta", "delta"]


def test_dream_block_stamp_lines_still_parse():
    """DRM-2's machine-managed block is NOT code-fenced — its edges must survive."""
    text = (
        "Body.\n\n"
        "<!-- dream:links -->\n"
        '[[other-memory]] <!-- dream: bridge · pass=p7 · edge=p7-e2 · cofire=0.71 -->\n'
        "<!-- /dream:links -->\n"
    )
    assert parse_wikilinks(text) == ["other-memory"]


def test_graph_drops_the_phantom_edge_end_to_end(tmp_path):
    """The corpus-level proof: a memory writing ABOUT wikilinks yields no dangling."""
    md = str(tmp_path / "memory")
    os.makedirs(md)

    def _mem(name, body):
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(
                f'---\nname: {name}\ndescription: "d {name}"\nmetadata:\n'
                f"  type: project\n---\n{body}\n"
            )

    _mem("prose", "The convention: append a `[[wikilink]]` into one side. See [[real]].")
    _mem("real", "I am real.")
    graph = build_graph(md)
    assert graph is not None
    assert graph.adjacency.get("prose") == {"real"}  # the phantom is gone, the real edge stays
    assert "wikilink" not in graph.raw_targets.get("prose", [])
