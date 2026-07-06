"""Packaging gate over the shipped starter packs (TEA-2).

Pins the pack contract: manifests are valid and complete, every pack memory carries
pack/version metadata (and stack tags where stack-specific), the consequential
policies require individual confirmation, only core seeds by default, and the
MEMORY.md skeleton's floor is core-only.
"""

from __future__ import annotations

import glob
import json
import os

from memory.provenance import parse_frontmatter

_ASSETS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "plugin", "assets")
)
_PACKS_DIR = os.path.join(_ASSETS, "packs")
_PACK_DIRS = sorted(
    d for d in glob.glob(os.path.join(_PACKS_DIR, "*")) if os.path.isdir(d)
)

_INDIVIDUAL_CONFIRM = {
    "oss-attribution-no-claude.md",
    "feedback_dont_poll_ci_on_hotfix_merges.md",
}


def _manifest(pack_dir: str) -> dict:
    with open(os.path.join(pack_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_expected_packs_ship():
    names = [os.path.basename(d) for d in _PACK_DIRS]
    assert names == [
        "core", "debugging-discipline", "engineering-process", "git-workflow",
        "stack-specific",
    ]


def test_manifests_match_shipped_files_exactly():
    for pack_dir in _PACK_DIRS:
        m = _manifest(pack_dir)
        listed = {e["file"] for e in m["memories"]}
        on_disk = {os.path.basename(p) for p in glob.glob(os.path.join(pack_dir, "*.md"))}
        assert listed == on_disk, f"{m['pack']}: manifest vs disk drift"
        assert m["pack"] == os.path.basename(pack_dir)
        assert m["version"] and m["title"] and m["description"]


def test_only_core_seeds_by_default():
    defaults = {m["pack"]: m["seed_by_default"] for m in map(_manifest, _PACK_DIRS)}
    assert defaults == {
        "core": True,
        "debugging-discipline": False,
        "engineering-process": False,
        "git-workflow": False,
        "stack-specific": False,
    }


def test_core_pack_contents():
    m = _manifest(os.path.join(_PACKS_DIR, "core"))
    assert {e["file"] for e in m["memories"]} == {
        "user_role.md", "claude_is_memory_master.md",
    }


def test_every_pack_memory_carries_pack_and_version_metadata():
    for pack_dir in _PACK_DIRS:
        pack = os.path.basename(pack_dir)
        version = _manifest(pack_dir)["version"]
        for path in glob.glob(os.path.join(pack_dir, "*.md")):
            fm = parse_frontmatter(open(path, encoding="utf-8").read())
            assert fm, f"{path}: frontmatter must parse"
            meta = fm.get("metadata") or {}
            assert meta.get("pack") == pack, f"{path}: metadata.pack"
            assert str(meta.get("pack_version")) == version, f"{path}: metadata.pack_version"


def test_stack_specific_memories_carry_stack_tags():
    pack_dir = os.path.join(_PACKS_DIR, "stack-specific")
    for path in glob.glob(os.path.join(pack_dir, "*.md")):
        fm = parse_frontmatter(open(path, encoding="utf-8").read())
        assert (fm.get("metadata") or {}).get("stack"), f"{path}: metadata.stack missing"


def test_attribution_and_ci_bypass_require_individual_confirmation():
    flagged = {}
    for pack_dir in _PACK_DIRS:
        for e in _manifest(pack_dir)["memories"]:
            if e.get("confirm") == "individual":
                flagged[e["file"]] = e
    assert set(flagged) == _INDIVIDUAL_CONFIRM
    for e in flagged.values():
        assert e.get("reason"), f"{e['file']}: individual confirm needs a stated reason"


def test_skeleton_floor_is_core_only():
    with open(os.path.join(_ASSETS, "MEMORY.skeleton.md"), "r", encoding="utf-8") as fh:
        text = fh.read()
    core = {os.path.basename(p) for p in glob.glob(os.path.join(_PACKS_DIR, "core", "*.md"))}
    import re

    pointed = set(re.findall(r"\]\(([\w./-]+\.md)\)", text))
    assert pointed == core, (
        f"the skeleton floor must point at exactly the core pack; got {pointed}"
    )
