"""GAT-3: a corpus declares its floor sections, and a floor line is never an over-long
description.

- ``read_floor_sections`` (the ONE accessor): absent / garbled degrades to today's two
  sections; per-entry faults drop that entry.
- ``new_memory`` files a pointer under the declared section for its type; an undeclared
  corpus is byte-identical to before; the tier skeleton writes the declared headings.
- ``lint_floor`` accepts pointers in every declared section (a ``types: []`` one included)
  and still flags one outside.
- The pointer line: hook when given, else the description when it fits the corpus's
  ``floor_lint.max_line``; otherwise the save is refused before anything is written —
  the dry run reports the same refusal.
- The MCP tool accepts ``hook``.

Hermetic: tmp corpora only; dense disabled.
"""

from __future__ import annotations

import json
import os

import pytest

import memory.lint_floor as floor
from memory import new_memory as NM
from memory.provenance import DEFAULT_FLOOR_SECTIONS, floor_section_for_type, read_floor_sections

_FLOOR = """# Proj — Agent Memory Index (durable floor)
> Always-loaded floor preamble.
## User
- [User Role](user_role.md) — solo founder.
## Working Style & Process Feedback
- [Some Feedback](feedback_x.md) — a process hook.
## Recalled on demand
> nav only.
"""

_DECLARED = [
    {"heading": "## User", "types": ["user"]},
    {"heading": "## Working Style & Process Feedback", "types": ["feedback"]},
    {"heading": "## Projects", "types": ["project"]},
    {"heading": "## In flight", "types": []},
]


def _env(tmp_path, monkeypatch, floor_text=_FLOOR, policy=None):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(floor_text)
    for n in ("user_role", "feedback_x"):
        with open(os.path.join(md, f"{n}.md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {n}\ndescription: d\n---\nbody\n")
    if policy is not None:
        with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
            json.dump(policy, fh)
    return md


def _write(md, tmp_path, name, desc, mtype, **kw):
    return NM.write_memory(name, desc, mtype, memory_dir=md, repo_root=str(tmp_path),
                           no_links=True, **kw)


def _floor_text(md):
    with open(os.path.join(md, "MEMORY.md"), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# The accessor
# --------------------------------------------------------------------------- #
def test_undeclared_is_todays_two_sections(tmp_path):
    md = str(tmp_path)
    assert read_floor_sections(md) == DEFAULT_FLOOR_SECTIONS
    assert floor_section_for_type(md, "user") == "## User"
    assert floor_section_for_type(md, "feedback") == "## Working Style & Process Feedback"
    assert floor_section_for_type(md, "project") is None
    assert NM._FLOOR_SECTION_BY_TYPE == {
        "user": "## User", "feedback": "## Working Style & Process Feedback"}


@pytest.mark.parametrize("raw", [
    "## User", {"heading": "## User"}, [], [{"heading": "User", "types": ["user"]}],
    [{"heading": "## X", "types": "user"}], [{"heading": "## X", "types": ["bogus"]}],
    [{"heading": "## X\n## Y", "types": []}], [42, None], [{"heading": "##", "types": []}],
])
def test_garbled_declaration_degrades_to_default(tmp_path, raw):
    with open(tmp_path / "hippo.json", "w") as fh:
        json.dump({"floor_sections": raw}, fh)
    assert read_floor_sections(str(tmp_path)) == DEFAULT_FLOOR_SECTIONS


def test_garbled_hippo_json_degrades_to_default(tmp_path):
    (tmp_path / "hippo.json").write_text("{not json")
    assert read_floor_sections(str(tmp_path)) == DEFAULT_FLOOR_SECTIONS


def test_bad_entries_drop_individually(tmp_path):
    with open(tmp_path / "hippo.json", "w") as fh:
        json.dump({"floor_sections": [
            {"heading": "## Me", "types": ["user"]},
            {"heading": "### Not a section", "types": ["feedback"]},
            {"heading": "## Again me", "types": ["user", "feedback"]},  # repeats 'user'
            {"heading": "## Me", "types": ["project"]},  # repeats a heading
            {"heading": "  ## Lessons  ", "types": ["feedback", "feedback"]},
            {"heading": "## Hand kept", "types": []},
        ]}, fh)
    assert read_floor_sections(str(tmp_path)) == (
        ("## Me", ("user",)), ("## Lessons", ("feedback",)), ("## Hand kept", ()))


# --------------------------------------------------------------------------- #
# Routing + the undeclared byte pin
# --------------------------------------------------------------------------- #
def _three_writes(md, tmp_path):
    _write(md, tmp_path, "user_zz", "operator runs the deploys", "user", title="User Zz")
    _write(md, tmp_path, "feedback_aa", "always say why", "feedback", hook="say why")
    _write(md, tmp_path, "proj_fact", "the cache ttl is five minutes", "project")
    return _floor_text(md)


def test_undeclared_corpus_is_byte_identical_to_before(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch)
    got = _three_writes(md, tmp_path)
    assert got == (
        "# Proj — Agent Memory Index (durable floor)\n"
        "> Always-loaded floor preamble.\n"
        "## User\n"
        "- [User Role](user_role.md) — solo founder.\n"
        "- [User Zz](user_zz.md) — operator runs the deploys\n"
        "## Working Style & Process Feedback\n"
        "- [Feedback Aa](feedback_aa.md) — say why\n"
        "- [Some Feedback](feedback_x.md) — a process hook.\n"
        "## Recalled on demand\n"
        "> nav only.\n"
    )
    assert floor.floor_violations(md) == {"rebloat": [], "missing_targets": []}


def test_garbled_declaration_writes_exactly_like_undeclared(tmp_path, monkeypatch):
    a = _env(tmp_path / "a", monkeypatch)
    b = _env(tmp_path / "b", monkeypatch, policy={"floor_sections": {"## User": ["user"]}})
    assert _three_writes(a, tmp_path / "a") == _three_writes(b, tmp_path / "b")


def test_declared_sections_route_by_type(tmp_path, monkeypatch):
    text = _FLOOR.replace("## Recalled on demand", "## Projects\n## In flight\n## Recalled on demand")
    md = _env(tmp_path, monkeypatch, floor_text=text, policy={"floor_sections": _DECLARED})
    res = _write(md, tmp_path, "proj_fact", "the cache ttl is five minutes", "project")
    assert res["floor"] == {"status": "appended", "reason": None}
    lines = _floor_text(md).split("\n")
    i = lines.index("## Projects")
    assert lines[i + 1] == "- [Proj Fact](proj_fact.md) — the cache ttl is five minutes"
    assert lines[i + 2] == "## In flight"
    # reference is declared nowhere: no pointer, today's skip reason.
    res = _write(md, tmp_path, "ref_x", "the dashboard url", "reference")
    assert res["floor"]["status"] == "skipped"
    assert "(ref_x.md)" not in _floor_text(md)


def test_feedback_under_a_renamed_declared_heading(tmp_path, monkeypatch):
    text = _FLOOR.replace("## Working Style & Process Feedback", "## Lessons")
    md = _env(tmp_path, monkeypatch, floor_text=text, policy={"floor_sections": [
        {"heading": "## User", "types": ["user"]}, {"heading": "## Lessons", "types": ["feedback"]}]})
    res = _write(md, tmp_path, "feedback_zz", "say why", "feedback")
    assert res["floor"] == {"status": "appended", "reason": None}
    assert "## Working Style" not in _floor_text(md)
    assert floor.floor_violations(md) == {"rebloat": [], "missing_targets": []}


def test_tier_skeleton_writes_declared_headings(tmp_path):
    from memory.new_memory_floor import _ensure_tier_floor

    plain = tmp_path / "plain"
    _ensure_tier_floor(str(plain), "user")
    assert (plain / "MEMORY.md").read_text().endswith(
        "NOT the native symlink.\n\n## User\n\n## Working Style & Process Feedback\n")
    declared = tmp_path / "declared"
    declared.mkdir()
    (declared / "hippo.json").write_text(json.dumps({"floor_sections": _DECLARED}))
    _ensure_tier_floor(str(declared), "user")
    assert (declared / "MEMORY.md").read_text().endswith(
        "## User\n\n## Working Style & Process Feedback\n\n## Projects\n\n## In flight\n")


# --------------------------------------------------------------------------- #
# lint_floor against declared sections
# --------------------------------------------------------------------------- #
def test_lint_accepts_every_declared_section_and_flags_outside(tmp_path, monkeypatch):
    text = _FLOOR.replace(
        "## Recalled on demand",
        "## Projects\n- [P](proj_p.md) — p\n## In flight\n- [Lane](lane.md) — hand kept\n"
        "## Recalled on demand\n- [Leak](leak.md) — re-bloat",
    )
    md = _env(tmp_path, monkeypatch, floor_text=text, policy={"floor_sections": _DECLARED})
    for n in ("proj_p", "lane", "leak"):
        open(os.path.join(md, f"{n}.md"), "w").close()
    v = floor.floor_violations(md)
    assert v["rebloat"] == [{"file": "leak.md", "section": "Recalled on demand"}]
    assert v["missing_targets"] == []
    assert {"proj_p", "lane", "user_role", "feedback_x"} <= floor.floor_memory_names(md)
    out = floor.floor_producer(md, str(tmp_path))
    assert "leak.md" in out and "In flight" in out
    # The same floor, undeclared: the two extra sections are re-bloat, as today.
    os.remove(os.path.join(md, "hippo.json"))
    assert {r["file"] for r in floor.floor_violations(md)["rebloat"]} == {
        "proj_p.md", "lane.md", "leak.md"}


def test_section_budgets_work_against_a_declared_heading(tmp_path, monkeypatch):
    text = _FLOOR.replace("## Recalled on demand", "## Projects\n## Recalled on demand")
    md = _env(tmp_path, monkeypatch, floor_text=text, policy={
        "floor_sections": _DECLARED, "floor_lint": {"section_budgets": {"## Projects": 40}}})
    res = _write(md, tmp_path, "proj_fact", "the cache ttl is five minutes", "project")
    assert res["floor"]["status"] == "skipped" and "'## Projects'" in res["floor"]["reason"]


# --------------------------------------------------------------------------- #
# The pointer line and its limit
# --------------------------------------------------------------------------- #
_LIMIT = {"floor_lint": {"max_line": 80}}
_LONG = "a description far longer than the floor line limit " * 3


def _corpus_files(md):
    return sorted(os.listdir(md)), _floor_text(md)


def test_over_long_description_without_hook_is_refused_with_nothing_written(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    before = _corpus_files(md)
    res = _write(md, tmp_path, "feedback_long", _LONG, "feedback")
    assert res["created"] is False and res["path"] is None
    msg = res["error"]
    assert "80-character floor line limit" in msg and "floor_lint.max_line" in msg
    n = len(f"- [Feedback Long](feedback_long.md) — {_LONG}".rstrip())
    assert f"{n} characters" in msg and "hook" in msg and "Nothing was written" in msg
    assert _corpus_files(md) == before
    assert not os.path.exists(os.path.join(tmp_path, ".claude", ".memory-index"))


def test_refusal_on_a_user_tier_writes_no_skeleton(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch)
    tier = tmp_path / "usertier"
    tier.mkdir()
    (tier / "hippo.json").write_text(json.dumps(_LIMIT))
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tier))
    res = NM.write_memory("feedback_long", _LONG, "feedback", tier="user", no_links=True)
    assert res["error"] and res["error"].startswith("refused:")
    assert sorted(os.listdir(tier)) == ["hippo.json"]
    assert md  # the project corpus untouched too


def test_same_save_with_short_hook_succeeds_and_line_is_the_hook(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    res = _write(md, tmp_path, "feedback_long", _LONG, "feedback", hook="say why, briefly")
    assert res["created"] is True and res["floor"]["status"] == "appended"
    assert "- [Feedback Long](feedback_long.md) — say why, briefly" in _floor_text(md).split("\n")
    assert _LONG.strip() not in _floor_text(md)


def test_over_long_hook_is_refused_too(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    res = _write(md, tmp_path, "feedback_long", "short", "feedback", hook=_LONG)
    assert res["created"] is False and "the hook you gave" in res["error"]
    assert not os.path.exists(os.path.join(md, "feedback_long.md"))


def test_description_that_fits_is_used_as_before(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    res = _write(md, tmp_path, "feedback_ok", "always say why", "feedback")
    assert res["created"] is True
    assert "- [Feedback Ok](feedback_ok.md) — always say why" in _floor_text(md)


def test_no_declared_limit_means_no_refusal(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch)
    res = _write(md, tmp_path, "feedback_long", _LONG, "feedback")
    assert res["created"] is True and res["floor"]["status"] == "appended"


def test_type_without_a_pointer_is_never_refused(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    res = _write(md, tmp_path, "proj_long", _LONG, "project")
    assert res["created"] is True and res["floor"]["status"] == "skipped"


def test_twenty_saves_leave_no_over_length_pointer(tmp_path, monkeypatch):
    md = _env(tmp_path, monkeypatch, policy={
        "floor_sections": _DECLARED, "floor_lint": {"max_line": 120}})
    for i in range(20):
        mtype = ("user", "feedback", "project", "reference")[i % 4]
        desc = f"memory number {i} " + ("with a description that runs on and on " * (i % 5))
        res = _write(md, tmp_path, f"m{i:02d}_{mtype}", desc, mtype)
        if res["error"]:
            assert res["error"].startswith("refused:")
            res = _write(md, tmp_path, f"m{i:02d}_{mtype}", desc, mtype, hook=f"memory {i}")
        assert res["created"] is True
    pointers = [ln for ln in _floor_text(md).split("\n") if floor._MD_LINK_RE.search(ln)]
    assert len(pointers) == 2 + 15  # the two fixtures + user/feedback/project saves
    assert [ln for ln in pointers if len(ln) > 120] == []
    assert floor.floor_governance(md)["longlines"] == []
    assert floor.floor_violations(md)["rebloat"] == []


def test_check_dry_run_reports_the_same_refusal(tmp_path, monkeypatch, capsys):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    before = _corpus_files(md)
    d = NM.check_candidate("feedback_long", _LONG, "feedback", memory_dir=md)
    real = _write(md, tmp_path, "feedback_long", _LONG, "feedback")
    assert d["refused"] == real["error"]
    assert NM.check_candidate("feedback_long", _LONG, "feedback", memory_dir=md,
                              hook="short")["refused"] is None
    rc = NM.main(["feedback_long", _LONG, "--type", "feedback", "--memory-dir", md, "--check"])
    assert rc == 1 and "refused:" in capsys.readouterr().out
    assert _corpus_files(md) == before


def test_cli_hook_flag(tmp_path, monkeypatch, capsys):
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    assert NM.main(["feedback_long", _LONG, "--type", "feedback", "--memory-dir", md,
                    "--no-links"]) == 1
    assert "refused:" in capsys.readouterr().out
    assert NM.main(["feedback_long", _LONG, "--type", "feedback", "--memory-dir", md,
                    "--no-links", "--hook", "short line"]) == 0
    assert "(feedback_long.md) — short line" in _floor_text(md)


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #
def test_mcp_new_memory_accepts_hook(tmp_path, monkeypatch):
    from memory import mcp_server as M
    from memory.mcp_schemas import _TOOLS as TOOLS
    from memory.mcp_schemas_v2 import V2_TOOLS

    for tools in (TOOLS, V2_TOOLS):
        schema = next(t for t in tools if t["name"] == "new_memory")["inputSchema"]
        assert schema["properties"]["hook"]["type"] == "string"
        assert "hook" not in schema["required"]
    md = _env(tmp_path, monkeypatch, policy=_LIMIT)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")

    def call(args):
        resp = M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "new_memory", "arguments": args}})
        return resp["result"]["content"][0]["text"]

    base = {"name": "feedback_long", "description": _LONG, "type": "feedback"}
    assert "refused:" in call({**base, "check": True})
    assert "refused:" in call(base)
    assert not os.path.exists(os.path.join(md, "feedback_long.md"))
    assert "created" in call({**base, "hook": "short line"})
    assert "(feedback_long.md) — short line" in _floor_text(md)
