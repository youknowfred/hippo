"""CLM-4: adopting Claude Code's native memory directory, as a two-step.

When native auto memory has been writing to ``~/.claude/projects/<encoded>/memory``, that
slot is a real directory and init cannot link it. Adoption is a preview that writes
nothing, then a digest-bound confirm that copies, backs up, links, indexes, and stamps the
format only after a key-collision check. Nothing adopted is ever trusted by it.

Hermetic: the projects dir is the per-test ``HIPPO_CLAUDE_PROJECTS_DIR`` (conftest), the
trust and projects registries are per-test files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from memory import native_adopt as NA
from memory import trust as T
from memory.provenance_format import CORPUS_FORMAT_VERSION, read_corpus_format

_NATIVE = {
    "MEMORY.md": "# Native index\n- [Deploy](deploy.md)\n",
    "deploy.md": '---\nname: deploy\ndescription: "how we deploy"\nmetadata:\n  type: project\n---\nCanary first.\n',
    "style.md": '---\nname: style\ndescription: "code style"\n---\nTabs.\n',
}


def _tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


@pytest.fixture
def native(repo, monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    cp = os.environ["HIPPO_CLAUDE_PROJECTS_DIR"]
    slot = NA.native_slot(repo, cp)
    os.makedirs(slot)
    for name, text in _NATIVE.items():
        with open(os.path.join(slot, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    md = os.path.join(repo, ".claude", "memory")
    return repo, md, slot, cp


def _snapshot(repo, slot, tmp_path):
    return (
        _tree(repo),
        _tree(slot),
        {f: open(os.path.join(tmp_path, f), "rb").read()
         for f in ("hippo-trust.json", "hippo-projects.json") if os.path.exists(os.path.join(tmp_path, f))},
    )


def test_preview_writes_nothing_and_lists_the_exact_actions(native, tmp_path):
    repo, md, slot, cp = native
    before = _snapshot(repo, slot, tmp_path)

    plan = NA.plan_adoption(repo, md, cp, today="20261005")

    assert _snapshot(repo, slot, tmp_path) == before
    assert not os.path.exists(md)
    assert plan["applies"] and plan["files"] == ["MEMORY.md", "deploy.md", "style.md"]
    assert plan["copy"] == plan["files"] and plan["collisions"] == [] and plan["key_collisions"] == []
    assert plan["backup"] == f"{slot}.pre-hippo-20261005"
    text = NA.render_plan(plan, confirm_hint="To adopt: hippo adopt --confirm {digest}")
    assert "nothing has been written" in text
    for needle in ("copy 3 file(s)", f"rename {slot} to {plan['backup']}", f"link {slot}",
                   "build the recall index", "hippo trust review", f"stamp corpus format {CORPUS_FORMAT_VERSION}",
                   f"--confirm {plan['digest']}"):
        assert needle in text, needle


def test_confirm_runs_the_five_steps_and_trusts_nothing(native):
    repo, md, slot, cp = native
    plan = NA.plan_adoption(repo, md, cp)

    res = NA.execute_adoption(repo, md, plan["digest"], cp)

    assert res["ok"], res["error"]
    # 1. copied byte for byte
    assert _tree(md) == {k: v.encode() for k, v in _NATIVE.items()} | {".format": _tree(md)[".format"]}
    # 2. the native dir moved aside, intact
    assert _tree(res["backup"]) == {k: v.encode() for k, v in _NATIVE.items()}
    # 3. the slot is now hippo's link
    assert os.path.islink(slot) and os.path.realpath(slot) == os.path.realpath(md)
    # 4. indexed
    assert res["index"]["count"] == 2
    # 5. stamped after the key check
    assert res["format"] == "stamped" and read_corpus_format(md) == CORPUS_FORMAT_VERSION
    # trust goes through review: nothing adopted is trusted
    assert not T.is_trusted(T.gate_repo_root(md, repo))
    assert "hippo trust review" in NA.render_result(res)


def test_a_stale_digest_is_refused_and_nothing_changes(native, tmp_path):
    repo, md, slot, cp = native
    plan = NA.plan_adoption(repo, md, cp)
    with open(os.path.join(slot, "style.md"), "a", encoding="utf-8") as fh:
        fh.write("changed after the preview\n")
    before = _snapshot(repo, slot, tmp_path)
    res = NA.execute_adoption(repo, md, plan["digest"], cp)
    assert not res["ok"] and "digest" in res["error"]
    assert _snapshot(repo, slot, tmp_path) == before


def test_a_per_file_collision_refuses_and_an_identical_file_does_not(native, tmp_path):
    repo, md, slot, cp = native
    os.makedirs(md)
    with open(os.path.join(md, "deploy.md"), "w", encoding="utf-8") as fh:
        fh.write(_NATIVE["deploy.md"])  # identical: fine
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# hippo floor\n")  # different: a collision
    plan = NA.plan_adoption(repo, md, cp)
    assert plan["identical"] == ["deploy.md"] and plan["collisions"] == ["MEMORY.md"]
    assert "adoption is refused" in NA.render_plan(plan, confirm_hint="{digest}")
    before = _snapshot(repo, slot, tmp_path)
    res = NA.execute_adoption(repo, md, plan["digest"], cp)
    assert not res["ok"] and "MEMORY.md" in res["error"]
    assert _snapshot(repo, slot, tmp_path) == before


def test_a_key_collision_blocks_the_format_stamp_but_not_the_adoption(native):
    repo, md, slot, cp = native
    with open(os.path.join(slot, "odd.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: odd\nconfidence: high\nmetadata:\n  supersedes: {a: 1}\n---\nx\n')
    plan = NA.plan_adoption(repo, md, cp)
    assert len(plan["key_collisions"]) == 2
    assert any("odd.md: confidence" in k for k in plan["key_collisions"])
    assert any("odd.md: metadata.supersedes" in k for k in plan["key_collisions"])
    assert "unstamped" in plan["actions"][-1]
    res = NA.execute_adoption(repo, md, plan["digest"], cp)
    assert res["ok"] and res["format"] == "not_stamped_key_collisions"
    assert not os.path.exists(os.path.join(md, ".format"))


def test_a_failed_copy_rolls_back_and_leaves_the_native_dir_alone(native, monkeypatch, tmp_path):
    from memory import init_project as IP

    repo, md, slot, cp = native
    plan = NA.plan_adoption(repo, md, cp)
    real, calls = IP._copy_if_absent, []

    def torn(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("fault injection: torn mid-copy")
        return real(src, dst)

    monkeypatch.setattr(IP, "_copy_if_absent", torn)
    before = _tree(slot)
    res = NA.execute_adoption(repo, md, plan["digest"], cp)
    assert not res["ok"] and "removed" in res["error"]
    assert _tree(md) == {}  # the first copy came back out
    assert os.path.isdir(slot) and not os.path.islink(slot) and _tree(slot) == before


def test_nothing_to_adopt_when_the_slot_is_already_a_link_or_absent(repo, tmp_path):
    cp = str(tmp_path / "cp")
    assert NA.plan_adoption(repo, os.path.join(repo, ".claude", "memory"), cp)["applies"] is False
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    slot = NA.native_slot(repo, cp)
    os.makedirs(os.path.dirname(slot))
    os.symlink(md, slot)
    assert NA.adoption_applies(repo, cp) is False


def test_adopting_into_a_trusted_corpus_leaves_the_adopted_files_withheld(native):
    repo, md, slot, cp = native
    os.makedirs(md)
    with open(os.path.join(md, "mine.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: mine\ndescription: "already here"\n---\nx\n')
    gate_root = T.gate_repo_root(md, repo)
    assert T.mark_trusted(gate_root, memory_dir=md, origin="init")
    assert NA.execute_adoption(repo, md, NA.plan_adoption(repo, md, cp)["digest"], cp)["ok"]
    assert T.untrusted_changes(gate_root, md)["added"] == ["deploy", "style"]


# --------------------------------------------------------------------------- #
# init (the MCP tool's engine) and the CLI verb
# --------------------------------------------------------------------------- #
def test_init_previews_first_then_adopts_on_the_digest(native, tmp_path):
    from memory.init_project import init_project

    repo, md, slot, cp = native
    before = _snapshot(repo, slot, tmp_path)
    r = init_project()
    assert r["mode"] == "adopt_preview" and r["adoption"]["applies"]
    assert _snapshot(repo, slot, tmp_path) == before  # no seed, no link, no registry row

    r = init_project(adopt_digest=r["adoption"]["digest"])
    assert r["adoption"]["ok"] and r["mode"] == "existing"
    assert r["seeded"] == []  # the adopted MEMORY.md is the floor; no starter pack over it
    assert r["symlink"]["status"] == "already_correct"
    assert r["trust"]["status"] == "untrusted_needs_review"


def test_init_skip_sets_up_without_adopting(native):
    from memory.init_project import init_project

    repo, md, slot, cp = native
    r = init_project(adopt_digest="skip")
    assert r["mode"] == "fresh" and r["symlink"]["status"] == "conflict"
    assert os.path.isdir(slot) and not os.path.islink(slot)


def test_mcp_init_tool_is_the_same_two_step(native):
    from memory import mcp_server as M

    repo, md, slot, cp = native

    def call(args):
        resp = M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "init", "arguments": args}})
        return resp["result"]["content"][0]["text"]

    text = call({})
    assert "nothing has been written" in text and not os.path.exists(md)
    digest = re.search(r'adopt_digest="([0-9a-f]{12})"', text).group(1)
    assert "nested" not in text
    text = call({"adopt_digest": digest})
    assert "adopted 3 file(s)" in text and os.path.islink(slot)
    assert "NOT trusted" in text  # routed to trust_corpus, never auto-trusted


def test_hippo_adopt_verb_previews_then_confirms(native):
    repo, md, slot, cp = native
    plugin = os.path.dirname(os.path.dirname(os.path.abspath(NA.__file__)))
    env = dict(os.environ, PYTHONPATH=plugin)

    def door(*args):
        return subprocess.run([sys.executable, "-m", "memory.cli", "adopt", *args],
                              capture_output=True, text=True, timeout=120, env=env)

    r = door()
    assert r.returncode == 0 and "nothing has been written" in r.stdout, r.stderr
    assert not os.path.exists(md)
    digest = re.search(r"hippo adopt --confirm ([0-9a-f]{12})", r.stdout).group(1)
    assert door("--confirm", "0" * 12).returncode == 1
    r = door("--confirm", digest)
    assert r.returncode == 0 and "adopted 3 file(s)" in r.stdout
    assert os.path.islink(slot)


def test_init_schema_declares_the_adopt_digest():
    from memory.mcp_schemas import _TOOLS as TOOLS

    init = next(t for t in TOOLS if t["name"] == "init")
    assert "adopt_digest" in init["inputSchema"]["properties"]
    json.dumps(init)  # serializable


def test_doctor_names_adoption_when_a_native_dir_holds_the_slot(repo, monkeypatch, tmp_path):
    from memory import doctor as D
    from memory import provenance_env as PE

    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))  # check_project_symlink reads ~/.claude/projects
    slot = os.path.join(str(home), ".claude", "projects", PE.encode_project_dir(repo), "memory")
    os.makedirs(slot)
    r = D.check_symlink(D.DoctorContext(md, repo))
    assert r["status"] == "fail" and "Adopt it: run init here" in r["message"]
    assert "rm -f" not in r["message"]
