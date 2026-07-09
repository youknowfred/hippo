"""RCH-1 — /hippo:promote: lift a project memory into the user tier with provenance.

Pins TEA-1's own unmet acceptance criterion end-to-end: a lesson promoted from project A
(with an ``origin: "<repo>@<sha>"`` stamp) recalls in project B, labeled as user-tier with
its origin rendered. Hermetic: every tier is a tmp dir (BM25-only), the user tier rides
``HIPPO_USER_MEMORY_DIR`` per the conftest convention, and the git legs build real scratch
repos. Every refusal is pinned as a ZERO-filesystem-change event.
"""

from __future__ import annotations

import json
import os
import subprocess

from memory import build_index as B
from memory import new_memory as N
from memory import provenance as P
from memory import recall as R
from memory import recall_view as V

_FLOOR = """# Memory index
## User
## Working Style & Process Feedback
- [Review First](review-first.md) — always review diffs before committing
"""


def _mem(
    name: str,
    description: str,
    body: str = "body text here",
    *,
    mtype: str = "feedback",
    extra_meta: str = "",
) -> str:
    return (
        f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n'
        f"  type: {mtype}\n{extra_meta}---\n\n{body}\n"
    )


def _project(tmp_path, monkeypatch, *, user_subdir: str = "usertier"):
    """A project corpus (with a floor) + a user-tier dir wired via env."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(proj, exist_ok=True)
    with open(os.path.join(proj, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(_FLOOR)
    user = str(tmp_path / user_subdir)
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    return proj, user


def _write(proj: str, name: str, text: str) -> str:
    path = os.path.join(proj, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# --------------------------------------------------------------------------- #
# The origin kwarg — render + parse round-trip
# --------------------------------------------------------------------------- #
def test_render_frontmatter_origin_emitted_and_quoted():
    text = N._render_frontmatter("m", "d", "feedback", "b", None, "hippo@abc123")
    assert '  origin: "hippo@abc123"' in text
    fm = P.parse_frontmatter(text)
    assert fm["metadata"]["origin"] == "hippo@abc123"


def test_render_frontmatter_without_origin_is_byte_identical_to_before():
    assert N._render_frontmatter("m", "d", "feedback", "b") == N._render_frontmatter(
        "m", "d", "feedback", "b", None, None
    )


def test_write_memory_origin_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "mem")
    r = N.write_memory(
        "lifted", "a promoted lesson", "project", "body", memory_dir=md,
        origin="somerepo@deadbeef", no_links=True,
    )
    assert r["created"] and not r["error"]
    fm = P.parse_frontmatter(open(r["path"], encoding="utf-8").read())
    assert fm["metadata"]["origin"] == "somerepo@deadbeef"


# --------------------------------------------------------------------------- #
# _remove_floor_pointer — the append inverse
# --------------------------------------------------------------------------- #
def test_remove_floor_pointer_drops_exactly_the_pointer_line(tmp_path):
    md = str(tmp_path / "mem")
    os.makedirs(md)
    body = (
        "# Memory index\n## Working Style & Process Feedback\n"
        "- [Review First](review-first.md) — review diffs\n"
        "prose that mentions (review-first.md) but is not a pointer\n"
    )
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(body)
    out = N._remove_floor_pointer(md, "review-first")
    assert out == {"status": "removed", "reason": None}
    text = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    assert "- [Review First](review-first.md)" not in text
    assert "prose that mentions" in text  # non-pointer lines are never touched


def test_remove_floor_pointer_absent_pointer_is_a_legible_skip(tmp_path):
    md = str(tmp_path / "mem")
    os.makedirs(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory index\n")
    assert N._remove_floor_pointer(md, "ghost") == {
        "status": "skipped",
        "reason": "pointer not present",
    }
    assert N._remove_floor_pointer(str(tmp_path / "nowhere"), "x")["reason"] == (
        "MEMORY.md missing"
    )


# --------------------------------------------------------------------------- #
# promote_memory — the move
# --------------------------------------------------------------------------- #
def test_promote_moves_memory_with_origin_and_floor_cleanup(tmp_path, monkeypatch):
    proj, user = _project(tmp_path, monkeypatch)
    body = "Always run the suite from the repo root.\n\n**Why:** addopts carries -q.\n"
    _write(proj, "review-first", _mem("review-first", "review diffs before committing", body))

    r = N.promote_memory("review-first", memory_dir=proj, repo_root=str(tmp_path))
    assert r["error"] is None and r["promoted"] is True
    assert not os.path.exists(os.path.join(proj, "review-first.md"))
    dest = os.path.join(user, "review-first.md")
    assert r["to"] == dest and os.path.isfile(dest)

    text = open(dest, encoding="utf-8").read()
    fm = P.parse_frontmatter(text)
    # tmp_path is not a git repo -> origin is the bare repo basename (no sha to stamp).
    assert fm["metadata"]["origin"] == os.path.basename(str(tmp_path))
    assert r["origin"] == fm["metadata"]["origin"]
    assert body.rstrip("\n") in text  # body carried verbatim
    # project floor pointer dropped; user tier floor gained one (write_memory's floor).
    assert r["floor_removed"] == {"status": "removed", "reason": None}
    assert "](review-first.md)" not in open(
        os.path.join(proj, "MEMORY.md"), encoding="utf-8"
    ).read()
    assert "](review-first.md)" in open(
        os.path.join(user, "MEMORY.md"), encoding="utf-8"
    ).read()


def test_promote_origin_prefers_source_commit_and_strips_provenance(tmp_path, monkeypatch):
    proj, user = _project(tmp_path, monkeypatch)
    extra = (
        "  cited_paths:\n    - src/app.py\n"
        "  source_commit: aaaabbbbccccddddaaaabbbbccccddddaaaabbbb\n"
        "  source_commit_time: 1700000000\n"
    )
    _write(
        proj,
        "lesson",
        _mem("lesson", "a provenance-stamped lesson", extra_meta=extra),
    )
    r = N.promote_memory(
        "lesson", memory_dir=proj, repo_root=str(tmp_path), allow_consequential=False
    )
    assert r["promoted"], r["error"]
    fm = P.parse_frontmatter(open(r["to"], encoding="utf-8").read())
    assert fm["metadata"]["origin"] == (
        os.path.basename(str(tmp_path)) + "@aaaabbbbccccddddaaaabbbbccccddddaaaabbbb"
    )
    # The re-render IS the provenance strip: the SOURCE's project-scoped values never
    # carry (write_memory's own user-tier backfill stamps empty/inert fields — the
    # shipped TEA-1 behavior for every user-tier write; the coupling is what must die).
    meta = fm.get("metadata", {})
    assert not meta.get("cited_paths")
    assert not meta.get("source_commit")
    # cited_paths coupling surfaced as warn findings (never a block).
    assert any(f["kind"] == "repo_coupling" for f in r["findings"])


def test_promote_origin_uses_git_head_when_unstamped(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t",
                                    "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                                    "GIT_COMMITTER_EMAIL": "t@t"})
    proj = os.path.join(repo, ".claude", "memory")
    os.makedirs(proj)
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _write(proj, "hd", _mem("hd", "head-stamped lesson"))
    r = N.promote_memory("hd", memory_dir=proj, repo_root=repo)
    assert r["promoted"], r["error"]
    head = P.git_head(repo)
    assert head and r["origin"] == f"{os.path.basename(repo)}@{head}"


def test_promote_collision_refuses_and_project_file_untouched(tmp_path, monkeypatch):
    proj, user = _project(tmp_path, monkeypatch)
    os.makedirs(user, exist_ok=True)
    _write(user, "review-first", _mem("review-first", "an older user-tier twin"))
    src = _write(proj, "review-first", _mem("review-first", "the project lesson"))
    before = open(src, "rb").read()
    floor_before = open(os.path.join(proj, "MEMORY.md"), "rb").read()

    r = N.promote_memory("review-first", memory_dir=proj, repo_root=str(tmp_path))
    assert not r["promoted"] and "already exists" in r["error"]
    assert "new_name" in r["error"]  # the rename path is named, never silent shadowing
    assert open(src, "rb").read() == before
    assert open(os.path.join(proj, "MEMORY.md"), "rb").read() == floor_before


def test_promote_new_name_resolves_collision(tmp_path, monkeypatch):
    proj, user = _project(tmp_path, monkeypatch)
    os.makedirs(user, exist_ok=True)
    _write(user, "review-first", _mem("review-first", "an older user-tier twin"))
    _write(proj, "review-first", _mem("review-first", "the project lesson"))
    r = N.promote_memory(
        "review-first", memory_dir=proj, repo_root=str(tmp_path),
        new_name="review-first-hippo",
    )
    assert r["promoted"], r["error"]
    assert os.path.isfile(os.path.join(user, "review-first-hippo.md"))
    assert not os.path.exists(os.path.join(proj, "review-first.md"))


def test_promote_consequential_default_requires_individual_confirm(tmp_path, monkeypatch):
    proj, user = _project(tmp_path, monkeypatch)
    src = _write(
        proj, "attrib",
        _mem("attrib", "attribution policy", "Never add a Co-Authored-By trailer.\n"),
    )
    r = N.promote_memory("attrib", memory_dir=proj, repo_root=str(tmp_path))
    assert r["refused"] and "consequential" in r["error"]
    assert os.path.isfile(src)  # zero filesystem change
    r2 = N.promote_memory(
        "attrib", memory_dir=proj, repo_root=str(tmp_path), allow_consequential=True
    )
    assert r2["promoted"], r2["error"]


def test_promote_retired_memory_refuses(tmp_path, monkeypatch):
    proj, _ = _project(tmp_path, monkeypatch)
    src = _write(
        proj, "old",
        _mem("old", "a retired lesson", extra_meta="  invalid_after: 2026-01-01\n"),
    )
    r = N.promote_memory("old", memory_dir=proj, repo_root=str(tmp_path))
    assert r["refused"] and "invalid_after" in r["error"]
    assert os.path.isfile(src)


def test_promote_inbound_referrer_refuses_then_force(tmp_path, monkeypatch):
    proj, user = _project(tmp_path, monkeypatch)
    _write(proj, "target", _mem("target", "the linked-to lesson"))
    _write(proj, "referrer", _mem("referrer", "points elsewhere", "see [[target]] first\n"))
    r = N.promote_memory("target", memory_dir=proj, repo_root=str(tmp_path))
    assert r["refused"] and "referrer" in r["error"] and r["referrers"] == ["referrer"]
    assert os.path.isfile(os.path.join(proj, "target.md"))
    r2 = N.promote_memory("target", memory_dir=proj, repo_root=str(tmp_path), force=True)
    assert r2["promoted"], r2["error"]


def test_promote_to_private_tier(tmp_path, monkeypatch):
    proj, _ = _project(tmp_path, monkeypatch)
    priv = str(tmp_path / "privtier")
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", priv)
    _write(proj, "quiet", _mem("quiet", "a repo-local private lesson"))
    r = N.promote_memory(
        "quiet", memory_dir=proj, repo_root=str(tmp_path), dest_tier="private"
    )
    assert r["promoted"], r["error"]
    assert r["to"] == os.path.join(priv, "quiet.md") and os.path.isfile(r["to"])
    fm = P.parse_frontmatter(open(r["to"], encoding="utf-8").read())
    assert fm["metadata"]["origin"]  # origin stamps regardless of tier


def test_promote_rejects_project_dest_tier(tmp_path, monkeypatch):
    proj, _ = _project(tmp_path, monkeypatch)
    _write(proj, "m", _mem("m", "d"))
    r = N.promote_memory("m", memory_dir=proj, repo_root=str(tmp_path), dest_tier="project")
    assert r["error"] and "invalid dest_tier" in r["error"]
    assert os.path.isfile(os.path.join(proj, "m.md"))


def test_promote_missing_memory_is_an_error(tmp_path, monkeypatch):
    proj, _ = _project(tmp_path, monkeypatch)
    r = N.promote_memory("ghost", memory_dir=proj, repo_root=str(tmp_path))
    assert r["error"] == "not found: ghost.md"


# --------------------------------------------------------------------------- #
# TEA-1's criterion, finally met: promoted in A -> recalls in B with provenance
# --------------------------------------------------------------------------- #
def test_promoted_memory_recalls_in_second_project_with_origin(tmp_path, monkeypatch):
    proj_a, user = _project(tmp_path, monkeypatch)
    _write(
        proj_a, "http2-push-hang",
        _mem("http2-push-hang", "git push over HTTP/2 hangs on this machine workaround"),
    )
    r = N.promote_memory("http2-push-hang", memory_dir=proj_a, repo_root=str(tmp_path))
    assert r["promoted"], r["error"]

    # A SECOND project, its own corpus — the promoted lesson must fuse in.
    proj_b = str(tmp_path / "projB" / ".claude" / "memory")
    proj_b_idx = str(tmp_path / "projB" / ".claude" / ".memory-index")
    os.makedirs(proj_b)
    with open(os.path.join(proj_b, "other.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("other", "an unrelated local fact about widget rendering"))
    B.build_index(proj_b, proj_b_idx)

    hits = R.recall(
        "git push hangs over HTTP/2 workaround", k=5,
        memory_dir=proj_b, index_dir=proj_b_idx,
    )
    byname = {h["name"]: h for h in hits}
    assert "http2-push-hang" in byname, "promoted lesson must recall in project B"
    assert byname["http2-push-hang"]["corpus"] == "user"
    assert byname["http2-push-hang"]["root"] == user

    view = V.describe(
        "git push hangs over HTTP/2 workaround", 5,
        memory_dir=proj_b, index_dir=proj_b_idx,
    )
    assert "user tier" in view
    assert f"learned in {os.path.basename(str(tmp_path))}" in view


# --------------------------------------------------------------------------- #
# promote_candidates — the dry-run listing
# --------------------------------------------------------------------------- #
def test_promote_candidates_lists_only_clean_user_feedback(tmp_path, monkeypatch):
    proj, _ = _project(tmp_path, monkeypatch)
    _write(proj, "clean-feedback", _mem("clean-feedback", "portable working-style lesson"))
    _write(proj, "proj-fact", _mem("proj-fact", "a project fact", mtype="project"))
    _write(
        proj, "coupled",
        _mem("coupled", "coupled lesson", extra_meta="  cited_paths:\n    - src/a.py\n"),
    )
    _write(
        proj, "conseq",
        _mem("conseq", "attribution lesson", "no Co-Authored-By trailers here\n"),
    )
    _write(
        proj, "retired",
        _mem("retired", "dead lesson", extra_meta="  invalid_after: 2026-01-01\n"),
    )
    got = N.promote_candidates(memory_dir=proj)
    assert [c["name"] for c in got] == ["clean-feedback", "conseq"]
    byname = {c["name"]: c for c in got}
    assert byname["clean-feedback"]["consequential"] == 0
    assert byname["conseq"]["consequential"] == 1
    assert byname["conseq"]["type"] == "feedback"
