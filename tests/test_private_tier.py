"""TEA-3 — the private memory tier (.claude/memory.local/): a gitignored in-repo sibling merged
into the same recall (provenance-labelled), recallable locally yet invisible in ``git status``,
degrading gracefully for teammates who lack the file.
"""

from __future__ import annotations

import os
import subprocess

from memory import build_index as B
from memory import new_memory as N
from memory import provenance as P
from memory import recall as R


def _mem(name: str, description: str, body: str = "private body") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: feedback\n---\n{body}\n'


def _seed(memory_dir: str, items: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for stem, desc in items.items():
        with open(os.path.join(memory_dir, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(_mem(stem, desc))


def test_local_memory_dir_default_is_sibling_of_project(monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_LOCAL_MEMORY_DIR", raising=False)
    proj = str(tmp_path / "repo" / ".claude" / "memory")
    assert P.local_memory_dir(proj) == str(tmp_path / "repo" / ".claude" / "memory.local")


def test_local_memory_dir_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", str(tmp_path / "elsewhere"))
    assert P.local_memory_dir("/whatever/.claude/memory") == str(tmp_path / "elsewhere")


def test_private_index_nests_inside_memory_local(monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_LOCAL_MEMORY_DIR", raising=False)
    proj = str(tmp_path / "repo" / ".claude" / "memory")
    priv = P.local_memory_dir(proj)
    # The private index must NOT be the project's sibling .memory-index (that would collide /
    # co-mingle scopes) — it nests inside memory.local.
    assert P.tier_index_dir(priv) == os.path.join(priv, ".memory-index")
    assert B.default_index_dir(proj) != P.tier_index_dir(priv)


def test_private_memory_recallable_locally_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "repo" / ".claude" / "memory")
    proj_idx = str(tmp_path / "repo" / ".claude" / ".memory-index")
    priv = str(tmp_path / "repo" / ".claude" / "memory.local")
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", priv)

    _seed(proj, {"shared-note": "a shared team convention about branch naming"})
    _seed(priv, {"my-scratch": "a private scratch note about my local debug proxy port 8899"})
    B.build_index(proj, proj_idx)
    B.build_index(priv, P.tier_index_dir(priv))

    hits = R.recall("local debug proxy port", k=5, memory_dir=proj, index_dir=proj_idx)
    names = {h["name"]: h for h in hits}
    assert "my-scratch" in names
    assert names["my-scratch"]["corpus"] == "private"
    assert names["my-scratch"]["root"] == priv


def test_private_precedence_beats_user_on_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "repo" / ".claude" / "memory")
    proj_idx = str(tmp_path / "repo" / ".claude" / ".memory-index")
    priv = str(tmp_path / "repo" / ".claude" / "memory.local")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", priv)
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    _seed(proj, {"anchor": "project anchor about widget assembly"})
    _seed(priv, {"dup": "PRIVATE copy about the shared preference topic widget"})
    _seed(user, {"dup": "USER copy about the shared preference topic widget"})
    B.build_index(proj, proj_idx)
    B.build_index(priv, P.tier_index_dir(priv))
    B.build_index(user, B.default_index_dir(user))
    hits = R.recall("shared preference topic widget", k=5, memory_dir=proj, index_dir=proj_idx)
    dup = [h for h in hits if h["name"] == "dup"]
    assert len(dup) == 1 and dup[0]["corpus"] == "private", "private precedes user in the merge"


def test_private_write_invisible_in_git_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    proj = os.path.join(repo, ".claude", "memory")
    _seed(proj, {"team-note": "a shared note"})
    with open(os.path.join(proj, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# F\n\n## User\n\n## Working Style & Process Feedback\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

    monkeypatch.delenv("HIPPO_LOCAL_MEMORY_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)

    res = N.write_memory(
        "local-only-secret",
        "a note only for this clone",
        "feedback",
        body="the local staging db password hint",
        tier="private",
    )
    assert res["created"] and res["tier"] == "private"

    priv = os.path.join(repo, ".claude", "memory.local")
    assert os.path.isfile(os.path.join(priv, "local-only-secret.md"))
    # Self-ignoring: memory.local carries a `*` .gitignore.
    with open(os.path.join(priv, ".gitignore"), encoding="utf-8") as fh:
        assert fh.read().strip() == "*"

    # Invisible to git: a habitual `git add -A` commits nothing from the private tier.
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == "", f"private write dirtied the git tree: {status!r}"
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "memory.local" not in tracked and "local-only-secret" not in tracked

    # Floor pointer landed in the private tier's own MEMORY.md, not the shared project one.
    with open(os.path.join(proj, "MEMORY.md"), encoding="utf-8") as fh:
        assert "local-only-secret" not in fh.read()
    with open(os.path.join(priv, "MEMORY.md"), encoding="utf-8") as fh:
        assert "local-only-secret.md" in fh.read()

    # The private tier's index nests inside memory.local — the project index never gains it.
    assert os.path.isdir(os.path.join(priv, ".memory-index"))
    B.build_index(proj, os.path.join(repo, ".claude", ".memory-index"))
    proj_manifest = B.load_index(os.path.join(repo, ".claude", ".memory-index"))
    assert "local-only-secret" not in {e["name"] for e in proj_manifest.entries}


def test_teammate_without_memory_local_degrades_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "repo" / ".claude" / "memory")
    proj_idx = str(tmp_path / "repo" / ".claude" / ".memory-index")
    # HIPPO_LOCAL_MEMORY_DIR points at a dir that does not exist (teammate never created it).
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", str(tmp_path / "repo" / ".claude" / "memory.local"))
    _seed(proj, {"team-note": "a shared team note about deploy cadence"})
    B.build_index(proj, proj_idx)
    # No private tier -> recall is project-only, portable producer is silent, nothing raises.
    hits = R.recall("deploy cadence", k=5, memory_dir=proj, index_dir=proj_idx)
    assert hits and all(h["corpus"] in (None, "project") for h in hits)
    assert R.portable_floor_producer(proj, proj) is None
    assert [t[2] for t in R._recall_tier_dirs(proj, proj_idx)] == ["project"]


def test_write_memory_private_tier_routes_to_memory_local(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "repo" / ".claude" / "memory")
    os.makedirs(proj, exist_ok=True)
    priv = str(tmp_path / "repo" / ".claude" / "memory.local")
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", priv)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "repo"))
    res = N.write_memory("scratch", "a private scratch", "feedback", tier="private")
    assert res["created"] and res["tier"] == "private"
    assert os.path.isfile(os.path.join(priv, "scratch.md"))
    assert not os.path.exists(os.path.join(proj, "scratch.md"))
