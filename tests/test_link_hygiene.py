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


# --------------------------------------------------------------------------- #
# RCH-10 — new_memory names an unresolvable link instead of minting it silently
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    return root, md


def _mem(md, name):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "d {name}"\nmetadata:\n  type: project\n---\nBody.\n'
        )


def test_links_to_a_nonexistent_memory_warns_but_still_writes(repo):
    """WARN, never block — a forward reference is legitimate; silence is not."""
    root, md = repo
    _mem(md, "real_one")
    r = write_memory(
        "newbie", "a new fact", "project", body="Body.",
        memory_dir=md, repo_root=root, links=["real_one", "ghost_memory"],
    )
    assert not r.get("error") and os.path.exists(os.path.join(md, "newbie.md"))
    assert r["related"] == ["real_one", "ghost_memory"]  # the write is unchanged
    warned = " ".join(r.get("warnings") or [])
    assert "ghost_memory" in warned, "an unresolvable link target must be NAMED (RCH-10)"
    assert "real_one" not in warned  # the resolvable one is never mentioned


def test_cross_tier_link_warns_and_says_why(repo, tmp_path, monkeypatch):
    """The live repro: links=["user_role"] wrote clean and minted a dangling edge.
    user_role is REAL — it just lives in the user tier, and the graph is per-corpus."""
    root, md = repo
    user_tier = str(tmp_path / "user-tier")
    os.makedirs(user_tier)
    _mem(user_tier, "user_role")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user_tier)

    r = write_memory(
        "feedback_thing", "a working-style fact", "feedback", body="Body.",
        memory_dir=md, repo_root=root, links=["user_role"],
    )
    assert not r.get("error")
    warned = " ".join(r.get("warnings") or [])
    assert "user_role" in warned
    assert "tier" in warned.lower(), "cross-tier is the common cause — say so"


def test_resolvable_links_warn_nothing(repo):
    """Empty-norm: the ordinary case stays silent."""
    root, md = repo
    _mem(md, "alpha")
    _mem(md, "beta")
    r = write_memory(
        "gamma", "a fact", "project", body="Body.",
        memory_dir=md, repo_root=root, links=["alpha", "beta"],
    )
    assert not [w for w in (r.get("warnings") or []) if "link" in w.lower()]


def test_discovered_links_are_never_warned(repo):
    """_discover_links only ever returns corpus members — it must not trip the check
    (and a discovery-path warning would be hippo blaming the user for its own pick)."""
    root, md = repo
    _mem(md, "alpha")
    r = write_memory(
        "delta", "a fact about alpha", "project", body="Body.",
        memory_dir=md, repo_root=root,
    )
    assert not [w for w in (r.get("warnings") or []) if "link" in w.lower()]
