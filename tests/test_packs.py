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

import pytest

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
    assert r["refused"] and r["invalid"]["ghost"] == "not found"
    assert not os.path.exists(dest)  # "keeper" validated fine and still nothing landed

    r2 = pack_extract(["keeper", "dead"], dest, memory_dir=md, repo_root=str(tmp_path))
    assert r2["refused"] and "retired" in r2["invalid"]["dead"] and not os.path.exists(dest)

    assert pack_extract(["keeper"], dest, memory_dir=md, repo_root=str(tmp_path))["manifest"]
    r3 = pack_extract(["keeper"], dest, memory_dir=md, repo_root=str(tmp_path))
    assert r3["refused"] and "refusing to overwrite" in r3["error"]

    assert pack_extract([], dest, memory_dir=md, repo_root=str(tmp_path))["error"]


def test_extract_collects_every_problem_at_once(tmp_path):
    """RCH-7 — the Desktop-transcript failure mode: an agent had to probe 69 names one
    refusal at a time. Explicit names refuse as ONE batch with every reason in
    ``invalid`` and zero filesystem change, even for the names that validated fine."""
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "good-one", "a keeper")
    _corpus_mem(md, "dead", "retired", extra_meta="  invalid_after: 2026-01-01\n")
    with open(os.path.join(md, "CONVENTIONS.md"), "w", encoding="utf-8") as fh:
        fh.write("# a reference doc in the corpus dir — no frontmatter, not a memory\n")
    dest = str(tmp_path / "pack")

    r = pack_extract(
        ["good-one", "ghost", "dead", "CONVENTIONS"],
        dest, memory_dir=md, repo_root=str(tmp_path),
    )
    assert r["refused"] is True and r["manifest"] is None
    assert not os.path.exists(dest)
    assert set(r["invalid"]) == {"ghost", "dead", "CONVENTIONS"}
    assert r["invalid"]["ghost"] == "not found"
    assert "retired" in r["invalid"]["dead"]
    # SEC-18: doc names now refuse at the NAME gate (the same canonical filter "all"
    # selects through), not incidentally at the frontmatter parse.
    assert "not a memory name" in r["invalid"]["CONVENTIONS"]
    assert "3 problem(s)" in r["error"] and "zero files written" in r["error"]


def test_extract_all_selects_real_memories_and_reports_skips(tmp_path):
    """RCH-7 — ``names="all"`` owns corpus membership: docs living in the corpus dir
    (MEMORY.md / CONVENTIONS.md — the transcript's glob swept one into the batch) are
    never candidates, and a retired memory is a REPORTED skip, not a batch failure."""
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "alpha", "first lesson")
    _corpus_mem(md, "beta", "second lesson")
    _corpus_mem(md, "dead", "retired lesson", extra_meta="  invalid_after: 2026-01-01\n")
    for doc in ("MEMORY.md", "MEMORY.full.md", "CONVENTIONS.md"):
        with open(os.path.join(md, doc), "w", encoding="utf-8") as fh:
            fh.write("# not a memory\n")

    dest = str(tmp_path / "everything")
    r = pack_extract("all", dest, memory_dir=md, repo_root=str(tmp_path))
    assert r["error"] is None and r["manifest"]
    assert sorted(r["extracted"]) == ["alpha.md", "beta.md"]
    assert list(r["skipped"]) == ["dead"] and "retired" in r["skipped"]["dead"]
    listed = {e["file"] for e in _manifest(dest)["memories"]}
    assert listed == {"alpha.md", "beta.md"}  # manifest == disk; no docs, no retired


def test_extract_stamp_survives_unusual_metadata_shapes(tmp_path):
    """COR-13 — the transcript's 'would corrupt its metadata.type' refusal. Each shape
    here defeated the pre-COR-13 hand-rolled stamp walk: an unrecognized ``metadata:``
    line (flow style, trailing comment) got a DUPLICATE metadata block appended — YAML
    last-wins, ``metadata.type`` silently dropped — and non-2-space children got
    mixed-indent frontmatter that no longer parsed. Fixed, every parseable shape
    extracts with type intact, a stamp present, and the body byte-identical; a shape
    the active parser cannot read refuses as invalid (correct: an unparseable-to-hippo
    memory must not extract)."""
    from memory.packs import pack_extract
    from memory.provenance import split_frontmatter

    md = str(tmp_path / "mem")
    os.makedirs(md)
    shapes = {
        "four-space": (
            '---\nname: four-space\ndescription: "d"\nmetadata:\n'
            "    type: feedback\n---\n\nbody\n"
        ),
        "flow-style": (
            '---\nname: flow-style\ndescription: "d"\n'
            "metadata: {type: feedback}\n---\n\nbody\n"
        ),
        "trailing-comment": (
            '---\nname: trailing-comment\ndescription: "d"\n'
            "metadata:  # machine-managed\n  type: feedback\n---\n\nbody\n"
        ),
        "ends-in-list": (
            '---\nname: ends-in-list\ndescription: "d"\nmetadata:\n'
            "  type: feedback\n  tags:\n    - one\n---\n\nbody\n"
        ),
    }
    for name, text in shapes.items():
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    for name, text in shapes.items():
        dest = str(tmp_path / f"pk-{name}")
        r = pack_extract([name], dest, memory_dir=md, repo_root=str(tmp_path))
        if not parse_frontmatter(text):  # miniyaml lane: outside the frontmatter subset
            assert r["refused"] and name in r["invalid"], name
            continue
        assert r["error"] is None, f"{name}: {r['error']}"
        out = open(os.path.join(dest, f"{name}.md"), encoding="utf-8").read()
        fm = parse_frontmatter(out)
        assert fm, f"{name}: extracted copy must parse"
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        assert (fm.get("type") or meta.get("type")) == "feedback", f"{name}: type survived"
        assert (fm.get("pack") or meta.get("pack")) == f"pk-{name}", f"{name}: stamped"
        assert split_frontmatter(out)[1] == split_frontmatter(text)[1], f"{name}: body verbatim"


def test_extract_writer_damage_refuses_the_batch_with_zero_change(tmp_path, monkeypatch):
    """COR-13 architecture pin: the damage guard runs in the VALIDATE phase, so a
    writer bug on the LAST name can no longer strand the earlier files in a partial,
    manifest-less dir (the transcript's 39-files-no-manifest state). The refusal names
    the file and calls itself a hippo bug."""
    from memory import packs as P

    md = str(tmp_path / "mem")
    _corpus_mem(md, "aaa-fine", "one")
    _corpus_mem(md, "zzz-cursed", "two")

    real = P._stamp_pack

    def corrupting(text, pack, version):
        out = real(text, pack, version)
        return out.replace("type: feedback", "type: broken") if "zzz-cursed" in text else out

    monkeypatch.setattr(P, "_stamp_pack", corrupting)
    dest = str(tmp_path / "pack")
    r = P.pack_extract(
        ["aaa-fine", "zzz-cursed"], dest, memory_dir=md, repo_root=str(tmp_path)
    )
    assert r["refused"] is True and r["manifest"] is None
    assert not os.path.exists(dest)  # aaa-fine was computed first and still not stranded
    assert "zzz-cursed" in r["invalid"] and "hippo bug" in r["invalid"]["zzz-cursed"]


def test_extract_refuses_a_dest_inside_the_corpus(tmp_path):
    """Extracted pack files landing inside the corpus dir would themselves be indexed
    as memories on the next build — refuse before selection, zero-change."""
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "keeper", "a lesson")
    r = pack_extract(
        ["keeper"], os.path.join(md, "my-pack"), memory_dir=md, repo_root=str(tmp_path)
    )
    assert r["refused"] is True and "inside the corpus" in r["error"]
    assert not os.path.exists(os.path.join(md, "my-pack"))


# --------------------------------------------------------------------------- #
# RCH-5 inbound — install/update on the v0.8.0 trust spine. These contracts REPLACE the
# long-standing negative-capability pin (test_install_and_update_remain_absent_until_
# the_trust_spine_ships): the spine (SEC-5/6/7) is in the tree, so the gate is met and
# the tripwire's job is done — per its own comment, replaced, not merely deleted.
# --------------------------------------------------------------------------- #
def _pack_source(tmp_path, files: dict, *, pack="lessons", version="1.0.0"):
    """A local pack source dir: files = {stem: (description, body)}."""
    src = str(tmp_path / f"src-{pack}-{version}")
    os.makedirs(src, exist_ok=True)
    for stem, (desc, body) in files.items():
        with open(os.path.join(src, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {stem}\ndescription: "{desc}"\ntype: feedback\n---\n{body}\n')
    manifest = {
        "pack": pack, "version": version, "title": pack, "description": "test pack",
        "seed_by_default": False,
        "memories": [{"file": f"{s}.md"} for s in sorted(files)],
    }
    with open(os.path.join(src, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return src


def test_install_plan_is_read_only_review_material(tmp_path):
    from memory.packs import pack_install_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {
        "deploy_lesson": ("never deploy on friday afternoons", "body"),
        "leaky": ("has a key", "aws AKIAIOSFODNN7EXAMPLE secret"),
    })
    plan = pack_install_plan(src, memory_dir=md, repo_root=str(tmp_path))
    assert plan["error"] is None and plan["pack"] == "lessons"
    by_name = {i["name"]: i for i in plan["items"]}
    assert by_name["deploy_lesson"]["installable"] is True
    assert by_name["deploy_lesson"]["will_inject"] == "never deploy on friday afternoons"
    assert by_name["leaky"]["secrets"] and by_name["leaky"]["installable"] is False
    assert os.listdir(md) == []  # a plan writes NOTHING


def test_install_plan_refuses_malformed_manifests(tmp_path):
    from memory.packs import pack_install_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = str(tmp_path / "bad")
    os.makedirs(src)
    assert "no manifest.json" in pack_install_plan(src, memory_dir=md)["error"]
    with open(os.path.join(src, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"pack": "p", "version": "1", "memories": [{"file": "../evil.md"}]}, fh)
    assert "illegal memory file name" in pack_install_plan(src, memory_dir=md)["error"]


def test_install_item_is_per_item_stamped_locked_and_refuses(tmp_path):
    from memory.packs import lockfile_path, pack_install_item

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {
        "deploy_lesson": ("never deploy on friday afternoons", "body"),
        "leaky": ("has a key", "aws AKIAIOSFODNN7EXAMPLE secret"),
    })
    r = pack_install_item(src, "deploy_lesson", memory_dir=md, repo_root=str(tmp_path),
                          source="https://example.com/lessons.git")
    assert r["installed"] is True
    text = open(r["path"], encoding="utf-8").read()
    fm = parse_frontmatter(text)
    meta = fm.get("metadata") or {}
    assert (fm.get("pack") or meta.get("pack")) == "lessons"
    assert str(fm.get("pack_version") or meta.get("pack_version")) == "1.0.0"
    lock = json.load(open(lockfile_path(md)))
    entry = lock["packs"]["lessons"]
    assert entry["source"] == "https://example.com/lessons.git"
    assert entry["installed"]["deploy_lesson"]["base"] == text  # the future 3-way base

    # The hard gates: secrets refuse; an existing DIFFERENT target refuses; per-item
    # only. (A byte-identical re-install ADOPTS instead — INT-17; pinned separately.)
    r2 = pack_install_item(src, "leaky", memory_dir=md, repo_root=str(tmp_path))
    assert r2["installed"] is False and "secret-lint" in r2["error"]
    assert not os.path.exists(os.path.join(md, "leaky.md"))
    r3 = pack_install_item(src, "deploy_lesson", memory_dir=md, repo_root=str(tmp_path))
    assert r3["installed"] is True and r3["adopted"] is True  # idempotent re-run
    with open(os.path.join(md, "deploy_lesson.md"), "a", encoding="utf-8") as fh:
        fh.write("\nlocal edit\n")
    r3b = pack_install_item(src, "deploy_lesson", memory_dir=md, repo_root=str(tmp_path))
    assert r3b["installed"] is False and "already exists" in r3b["error"]
    r4 = pack_install_item(src, "ghost", memory_dir=md, repo_root=str(tmp_path))
    assert "not in this pack's manifest" in r4["error"]


def test_install_consents_the_bytes_on_a_fingerprinted_corpus(tmp_path, repo, memory_dir, monkeypatch):
    """SEC-6 dovetail: the per-item approval IS the review — an installed pack memory
    joins the consent baseline and is immediately recallable, not quarantined."""
    from memory import build_index as B
    from memory import recall as R
    from memory import trust as T
    from memory.packs import pack_install_item

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TRUST_FILE", str(tmp_path / "trust.json"))
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _corpus_mem(memory_dir, "existing", "an existing memory about spreadsheets")
    idx = str(tmp_path / "idx")
    B.build_index(memory_dir, idx)
    assert T.mark_trusted(repo, memory_dir=memory_dir) is True

    src = _pack_source(tmp_path, {"deploy_lesson": ("never deploy on friday afternoons", "body")})
    r = pack_install_item(src, "deploy_lesson", memory_dir=memory_dir, repo_root=repo)
    assert r["installed"] is True
    B.build_index(memory_dir, idx)
    names = {h["name"] for h in R.recall("deploy friday afternoons lesson", k=5,
                                         memory_dir=memory_dir, index_dir=idx)}
    assert "deploy_lesson" in names  # consented, not quarantined


def test_update_three_way_preserves_local_edits(tmp_path):
    """THE RCH-5 update acceptance criterion: per-item diffs, three-way merge, local
    edits preserved through an upstream change."""
    from memory.packs import pack_install_item, pack_update_item, pack_update_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    body_v1 = "line one\nline two\nline three"
    src1 = _pack_source(tmp_path, {"lesson": ("a lesson", body_v1)}, version="1.0.0")
    assert pack_install_item(src1, "lesson", memory_dir=md)["installed"]

    # Local edit at the BOTTOM; upstream v2 changes the TOP — a clean three-way.
    path = os.path.join(md, "lesson.md")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("my local note\n")
    src2 = _pack_source(
        tmp_path, {"lesson": ("a lesson", body_v1.replace("line one", "line ONE (v2)"))},
        version="2.0.0",
    )
    plan = pack_update_plan(src2, memory_dir=md)
    assert plan["error"] is None and plan["version"] == "2.0.0"
    row = plan["items"][0]
    assert row["state"] == "merged" and not row["conflict"] and "line ONE (v2)" in row["diff"]

    r = pack_update_item(src2, "lesson", memory_dir=md)
    assert r["updated"] is True
    text = open(path, encoding="utf-8").read()
    assert "line ONE (v2)" in text and "my local note" in text  # both survive
    assert 'pack_version: "2.0.0"' in text  # re-stamped to the new version


def test_update_conflict_refuses_until_resolved(tmp_path):
    from memory.packs import pack_install_item, pack_update_item, pack_update_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src1 = _pack_source(tmp_path, {"lesson": ("a lesson", "the same line")}, version="1.0.0")
    assert pack_install_item(src1, "lesson", memory_dir=md)["installed"]
    path = os.path.join(md, "lesson.md")
    edited = open(path, encoding="utf-8").read().replace("the same line", "my local version")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(edited)
    src2 = _pack_source(tmp_path, {"lesson": ("a lesson", "upstream version")}, version="2.0.0")

    plan = pack_update_plan(src2, memory_dir=md)
    assert plan["items"][0]["state"] == "conflict"
    r = pack_update_item(src2, "lesson", memory_dir=md)
    assert r["updated"] is False and "CONFLICT" in r["error"]
    assert "my local version" in open(path, encoding="utf-8").read()  # untouched

    resolved = open(path, encoding="utf-8").read().replace("my local version", "reconciled")
    r2 = pack_update_item(src2, "lesson", memory_dir=md, resolved_text=resolved)
    assert r2["updated"] is True and "reconciled" in open(path, encoding="utf-8").read()


def test_update_never_deletes_or_resurrects(tmp_path):
    from memory.packs import pack_install_item, pack_update_item, pack_update_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src1 = _pack_source(
        tmp_path,
        {"kept": ("stays upstream", "b"), "dropped": ("leaves upstream", "b")},
        version="1.0.0",
    )
    for n in ("kept", "dropped"):
        assert pack_install_item(src1, n, memory_dir=md)["installed"]
    os.remove(os.path.join(md, "kept.md"))  # the user removed one locally
    src2 = _pack_source(tmp_path, {"kept": ("stays upstream", "b2")}, version="2.0.0")

    plan = pack_update_plan(src2, memory_dir=md)
    states = {i["name"]: i["state"] for i in plan["items"]}
    assert states == {"kept": "missing-local", "dropped": "removed-upstream"}
    for n in ("kept", "dropped"):
        r = pack_update_item(src2, n, memory_dir=md)
        assert r["updated"] is False  # report-only states never apply
    assert not os.path.exists(os.path.join(md, "kept.md"))  # never resurrected
    assert os.path.exists(os.path.join(md, "dropped.md"))  # never deleted


def test_update_requires_a_lockfile_record(tmp_path):
    from memory.packs import pack_update_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {"lesson": ("a lesson", "b")})
    assert "no lockfile record" in pack_update_plan(src, memory_dir=md)["error"]


# --------------------------------------------------------------------------- #
# COR-13 — the stamp writers may never damage what they do not own, and every
# write site guards for it (install/update had NO guard before this).
# --------------------------------------------------------------------------- #
def test_install_stamp_never_rewrites_a_body_that_mentions_pack_version(tmp_path):
    """The pre-COR-13 install stamp ran a MULTILINE regex over the whole file: a body
    that merely documented ``pack_version:`` got the BODY line rewritten and the
    frontmatter never stamped — silent corruption of foreign text, on the one path
    that had no damage guard to catch it."""
    from memory.packs import pack_install_item
    from memory.provenance import split_frontmatter

    md = str(tmp_path / "mem")
    os.makedirs(md)
    body = 'Each file gets\npack_version: "9.9.9" stamped into its frontmatter.'
    src = _pack_source(tmp_path, {"pack-notes": ("notes about packs", body)})
    r = pack_install_item(src, "pack-notes", memory_dir=md, repo_root=str(tmp_path))
    assert r["installed"] is True
    text = open(r["path"], encoding="utf-8").read()
    fm = parse_frontmatter(text)
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    assert str(fm.get("pack_version") or meta.get("pack_version")) == "1.0.0"
    assert 'pack_version: "9.9.9"' in split_frontmatter(text)[1]  # body verbatim


def test_install_refuses_when_the_stamp_rewrite_is_damaged(tmp_path, monkeypatch):
    """Install writes FOREIGN text into the corpus — a stamp-writer bug must refuse
    loudly and name itself, never land corrupted bytes."""
    from memory import packs as P

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {"lesson": ("a lesson", "body")})
    monkeypatch.setattr(
        P, "_ensure_pack_stamp", lambda text, pack, version: text + "\ncorrupted tail\n"
    )
    r = P.pack_install_item(src, "lesson", memory_dir=md, repo_root=str(tmp_path))
    assert r["installed"] is False and "hippo bug" in r["error"]
    assert not os.path.exists(os.path.join(md, "lesson.md"))


def test_update_reports_stamp_damage_per_item_without_sinking_the_plan(tmp_path, monkeypatch):
    """A damaged re-stamp poisons ONE item's ``theirs`` side: that item refuses with
    the reason on its row (state ``stamp-refused``), the rest of the plan proceeds,
    and pack_update_item refuses to apply the damaged one."""
    from memory import packs as P

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src1 = _pack_source(
        tmp_path, {"ok": ("fine", "line"), "cursed": ("hexed", "line")}, version="1.0.0"
    )
    for n in ("ok", "cursed"):
        assert P.pack_install_item(src1, n, memory_dir=md)["installed"]
    src2 = _pack_source(
        tmp_path, {"ok": ("fine", "line v2"), "cursed": ("hexed", "line v2")}, version="2.0.0"
    )

    real = P._ensure_pack_stamp

    def corrupting(text, pack, version):
        out = real(text, pack, version)
        return out + "\ntail\n" if "cursed" in text else out

    monkeypatch.setattr(P, "_ensure_pack_stamp", corrupting)
    plan = P.pack_update_plan(src2, memory_dir=md)
    rows = {i["name"]: i for i in plan["items"]}
    assert rows["ok"]["state"] == "fast-forward"  # the plan is NOT sunk by the bad item
    assert rows["cursed"]["state"] == "stamp-refused"
    assert "hippo bug" in rows["cursed"]["error"]

    r = P.pack_update_item(src2, "cursed", memory_dir=md)
    assert r["updated"] is False and "hippo bug" in r["error"]
    assert P.pack_update_item(src2, "ok", memory_dir=md)["updated"] is True


# --------------------------------------------------------------------------- #
# QA sweep 2026-07-16 — COR-15 / SEC-18 / INT-17 / RCH-8.
# --------------------------------------------------------------------------- #
def test_extract_refuses_a_dest_reaching_the_corpus_through_a_symlink(tmp_path):
    """COR-15: the inside-corpus refusal must hold under symlinks. The plugin's own
    native-memory layout (`~/.claude/projects/<slug>/memory` -> corpus) makes a
    symlinked route to the corpus an ordinary, reachable dest."""
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "keeper", "a lesson")
    link = str(tmp_path / "native-memory")
    os.symlink(md, link)
    r = pack_extract(
        ["keeper"], os.path.join(link, "my-pack"), memory_dir=md, repo_root=str(tmp_path)
    )
    assert r["refused"] is True and "inside the corpus" in r["error"]
    assert not os.path.exists(os.path.join(md, "my-pack"))

    # And the mirror image: the corpus PATH is the symlink, dest names the real dir.
    real = str(tmp_path / "real-corpus")
    _corpus_mem(real, "keeper", "a lesson")
    memlink = str(tmp_path / "corpus-link")
    os.symlink(real, memlink)
    r = pack_extract(
        ["keeper"], os.path.join(real, "my-pack"), memory_dir=memlink,
        repo_root=str(tmp_path),
    )
    assert r["refused"] is True and "inside the corpus" in r["error"]
    assert not os.path.exists(os.path.join(real, "my-pack"))


def test_extract_refuses_a_dest_differing_only_by_case(tmp_path):
    """COR-15: on a case-insensitive filesystem (macOS APFS default) a dest spelled
    with different case still lands inside the corpus — the check must see it."""
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "keeper", "a lesson")
    upper = str(tmp_path / "MEM")
    if not os.path.isdir(upper):
        pytest.skip("case-sensitive filesystem — the spelling cannot collide here")
    r = pack_extract(
        ["keeper"], os.path.join(upper, "my-pack"), memory_dir=md, repo_root=str(tmp_path)
    )
    assert r["refused"] is True and "inside the corpus" in r["error"]
    assert not os.path.exists(os.path.join(md, "my-pack"))


def test_extract_refuses_names_that_are_not_bare_memory_stems(tmp_path):
    """SEC-18: an explicit name is a corpus-memory STEM, never a path. A separator or
    an absolute path would read files outside the corpus into a shareable pack — and
    the copy's write target would escape dest. Every bad name is reported in the one
    refusal, and nothing is read or written."""
    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "good", "a lesson")
    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: outside\ndescription: private\n---\nnot yours\n")
    before = outside.read_text()
    dest = str(tmp_path / "pack")
    r = pack_extract(
        ["good", "../outside", str(tmp_path / "outside"), "MEMORY"],
        dest, memory_dir=md, repo_root=str(tmp_path),
    )
    assert r["refused"] is True
    for bad in ("../outside", str(tmp_path / "outside"), "MEMORY"):
        assert bad in r["invalid"], f"{bad!r} must be named in the one refusal"
        assert "memory name" in r["invalid"][bad] or "docs" in r["invalid"][bad]
    assert "good" not in r["invalid"]
    assert not os.path.exists(dest)  # zero-change refusal
    assert outside.read_text() == before  # and the outside file was never touched


def test_install_adopts_an_identical_existing_file_and_restores_the_lockfile(tmp_path):
    """INT-17: a crash between install's file write and its lockfile write used to
    dead-end the verbs in a circle (update plan -> "new upstream" -> install ->
    "already exists, route through update" -> update -> "not an installed member").
    A byte-identical existing file now ADOPTS: the lockfile record is restored and
    every later verb works. Different content still refuses."""
    from memory import packs as P

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {
        "alpha": ("first lesson", "body a"),
        "beta": ("second lesson", "body b"),
    })
    assert P.pack_install_item(src, "alpha", memory_dir=md)["installed"]

    # Simulate the crash window: the corpus file landed, the lockfile record did not.
    raw = open(os.path.join(src, "beta.md"), encoding="utf-8").read()
    stamped = P._ensure_pack_stamp(raw, "lessons", "1.0.0")
    with open(os.path.join(md, "beta.md"), "w", encoding="utf-8") as fh:
        fh.write(stamped)

    r = P.pack_install_item(src, "beta", memory_dir=md)
    assert r["installed"] is True and r.get("adopted") is True
    lock = json.load(open(P.lockfile_path(md)))
    assert "beta" in lock["packs"]["lessons"]["installed"]
    plan = P.pack_update_plan(src, memory_dir=md)
    assert plan["new_upstream"] == []  # the triangle is closed
    states = {i["name"]: i["state"] for i in plan["items"]}
    assert states["beta"] == "unchanged"

    # An existing file with DIFFERENT content keeps the hard refusal.
    with open(os.path.join(md, "gamma.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: gamma\ndescription: "mine"\ntype: feedback\n---\nlocal text\n')
    src2 = _pack_source(tmp_path, {"gamma": ("theirs", "pack text")}, pack="other", version="2.0.0")
    r2 = P.pack_install_item(src2, "gamma", memory_dir=md)
    assert r2["installed"] is False and "already exists" in r2["error"]
    assert "local text" in open(os.path.join(md, "gamma.md"), encoding="utf-8").read()


def test_extract_rolls_back_the_in_flight_partial_file(tmp_path, monkeypatch):
    """RCH-8: the rollback set must include the file being written WHEN the failure
    hits — a disk-full mid-file used to leave that partial .md behind (and dest
    undeletable), the exact manifest-less state RCH-7 promised away."""
    import builtins

    from memory.packs import pack_extract

    md = str(tmp_path / "mem")
    _corpus_mem(md, "aaa-fine", "one")
    _corpus_mem(md, "zzz-cursed", "two")
    dest = str(tmp_path / "pack")

    real_open = builtins.open

    class _Torn:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def write(self, s):
            self._fh.write(s[:16])  # some bytes land, then the device fails
            raise OSError(28, "No space left on device")

    def failing_open(path, mode="r", *a, **k):
        fh = real_open(path, mode, *a, **k)
        if "w" in str(mode) and str(path).endswith("zzz-cursed.md"):
            return _Torn(fh)
        return fh

    monkeypatch.setattr(builtins, "open", failing_open)
    r = pack_extract(["aaa-fine", "zzz-cursed"], dest, memory_dir=md, repo_root=str(tmp_path))
    monkeypatch.undo()
    assert r["error"] and "rolled back" in r["error"]
    assert r["extracted"] == []
    assert not os.path.exists(dest), (
        f"dest must be fully rolled back, found: {os.listdir(dest) if os.path.isdir(dest) else '-'}"
    )


def test_corrupt_lockfile_refuses_loudly_instead_of_silently_resetting(tmp_path):
    """COR-17: a corrupt .packs.lock.json used to read as 'no packs installed' — update
    said 'no lockfile record', and the next install REWROTE the file from scratch,
    silently orphaning every other pack's three-way merge base. Corruption must refuse
    and name the git escape hatch (the lockfile is committed)."""
    from memory import packs as P

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {
        "alpha": ("first lesson", "body a"),
        "beta": ("second lesson", "body b"),
    })
    assert P.pack_install_item(src, "alpha", memory_dir=md)["installed"]
    lock_before = open(P.lockfile_path(md), encoding="utf-8").read()
    with open(P.lockfile_path(md), "w", encoding="utf-8") as fh:
        fh.write("{ this is not json —")

    plan = P.pack_update_plan(src, memory_dir=md)
    assert plan["error"] and "restore it from git" in plan["error"]

    r = P.pack_install_item(src, "beta", memory_dir=md)
    assert r["installed"] is False and "restore it from git" in r["error"]
    assert not os.path.exists(os.path.join(md, "beta.md"))  # refusal wrote nothing
    assert open(P.lockfile_path(md), encoding="utf-8").read() == "{ this is not json —", (
        "a refusal must not rewrite the corrupt lockfile either"
    )

    # And the update-item path refuses the same way.
    r2 = P.pack_update_item(src, "alpha", memory_dir=md)
    assert r2["updated"] is False and "restore it from git" in r2["error"]

    # Sanity: with the lockfile restored, everything works again.
    with open(P.lockfile_path(md), "w", encoding="utf-8") as fh:
        fh.write(lock_before)
    assert P.pack_update_plan(src, memory_dir=md)["error"] is None


def test_install_plan_names_a_failed_duplicate_check(tmp_path, monkeypatch):
    """RCH-9: check_candidate raising used to silently downgrade the item to
    route:'add' — presenting a possibly-near-duplicate as a clean add on the one
    surface whose whole job is showing the reviewer what they are approving."""
    import memory.new_memory as NM
    from memory.packs import pack_install_plan

    md = str(tmp_path / "mem")
    os.makedirs(md)
    src = _pack_source(tmp_path, {"deploy_lesson": ("never deploy on fridays", "body")})

    def boom(*a, **k):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(NM, "check_candidate", boom)
    plan = pack_install_plan(src, memory_dir=md, repo_root=str(tmp_path))
    assert plan["error"] is None
    item = plan["items"][0]
    assert item["route"] == "add"
    assert "index unreadable" in (item.get("route_error") or ""), (
        "a failed duplicate check must be named on the row, not silently read as "
        "'no duplicates'"
    )
