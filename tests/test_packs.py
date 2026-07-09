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


# --------------------------------------------------------------------------- #
# RCH-5 (extract slice): pack_extract emits packs in the SHIPPED shape — the
# parity contracts above apply verbatim to an extracted pack.
# --------------------------------------------------------------------------- #
def _corpus_mem(md, name, description, body="body", extra_meta=""):
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n'
            f"  type: feedback\n{extra_meta}---\n\n{body}\n"
        )


def test_extracted_pack_passes_the_shipped_parity_contracts(tmp_path):
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(
        md, "suite-from-root", "run the suite from the repo root",
        extra_meta=(
            "  cited_paths:\n    - src/app.py\n"
            "  source_commit: aaaabbbbccccddddaaaabbbbccccddddaaaabbbb\n"
            "  steer: pin\n"
        ),
    )
    _corpus_mem(
        md, "no-trailers", "attribution preference",
        body="Do NOT add a Co-Authored-By trailer.",
    )
    dest = str(tmp_path / "my-lessons")
    r = pack_extract(
        ["suite-from-root", "no-trailers"], dest,
        memory_dir=md, repo_root=str(tmp_path), version="0.1.0",
    )
    assert r["error"] is None and r["manifest"]
    assert sorted(r["extracted"]) == ["no-trailers.md", "suite-from-root.md"]

    # THE PARITY LEG: the same contracts the shipped packs pass, applied verbatim.
    m = _manifest(dest)
    listed = {e["file"] for e in m["memories"]}
    on_disk = {os.path.basename(p) for p in glob.glob(os.path.join(dest, "*.md"))}
    assert listed == on_disk
    assert m["pack"] == os.path.basename(dest)  # pack == dirname, the shipped rule
    assert m["version"] and m["title"] and m["description"]
    assert m["seed_by_default"] is False  # an extracted pack is never core
    for path in glob.glob(os.path.join(dest, "*.md")):
        fm = parse_frontmatter(open(path, encoding="utf-8").read())
        assert fm, f"{path}: frontmatter must parse"
        meta = fm.get("metadata") or {}
        assert meta.get("pack") == "my-lessons", f"{path}: metadata.pack"
        assert str(meta.get("pack_version")) == "0.1.0", f"{path}: metadata.pack_version"

    # Consequential defaults became individual-confirm markers, reasons stated —
    # exactly the shipped packs' mechanism (test_attribution_and_ci_bypass... above).
    flagged = {e["file"]: e for e in m["memories"] if e.get("confirm") == "individual"}
    assert set(flagged) == {"no-trailers.md"}
    assert flagged["no-trailers.md"]["reason"]

    # The portable rewrite: provenance + steer stripped from the COPIES...
    text = open(os.path.join(dest, "suite-from-root.md"), encoding="utf-8").read()
    assert "cited_paths" not in text and "source_commit" not in text
    assert "steer" not in text
    assert "body" in text  # body verbatim
    # ...while the SOURCE corpus file is byte-untouched on those fields.
    src = open(os.path.join(md, "suite-from-root.md"), encoding="utf-8").read()
    assert "cited_paths" in src and "steer: pin" in src


def test_extract_refusals_are_zero_change(tmp_path):
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "keeper", "a lesson to share")
    _corpus_mem(md, "dead", "a retired lesson", extra_meta="  invalid_after: 2026-01-01\n")
    dest = str(tmp_path / "pack-a")

    r = pack_extract(["keeper", "ghost"], dest, memory_dir=md, repo_root=str(tmp_path))
    assert r["error"] == "not found: ghost.md" and not os.path.exists(dest)

    r2 = pack_extract(["keeper", "dead"], dest, memory_dir=md, repo_root=str(tmp_path))
    assert r2["refused"] and "retired" in r2["error"] and not os.path.exists(dest)

    assert pack_extract(["keeper"], dest, memory_dir=md, repo_root=str(tmp_path))["manifest"]
    r3 = pack_extract(["keeper"], dest, memory_dir=md, repo_root=str(tmp_path))
    assert r3["refused"] and "refusing to overwrite" in r3["error"]

    assert pack_extract([], dest, memory_dir=md, repo_root=str(tmp_path))["error"]


def test_install_and_update_remain_absent_until_the_trust_spine_ships():
    # RCH-5's gate, pinned as a negative capability: a foreign pack IS the public-corpus
    # injection threat, and SEC-5/6/7 (the v0.8.0 trust spine) are not in the tree —
    # so no inbound pack primitive may exist yet. When the spine ships, this test is
    # REPLACED by the install/update contracts, not merely deleted.
    from memory import packs

    for forbidden in ("install_pack", "update_pack", "pack_install", "pack_update"):
        assert not hasattr(packs, forbidden), (
            f"packs.{forbidden} must not exist before the v0.8.0 trust spine lands"
        )
