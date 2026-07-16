"""Packs (RCH-5) — extract your memories as a pack; install/update packs from sources.

TEA-2 shipped starter packs that only travel INSIDE the plugin; nothing let a user turn
their own proven corpus memories into a pack another human could review and seed. This
module is that outbound path: ``pack_extract`` copies chosen memories into a pack dir
with a ``manifest.json`` in the SHIPPED packs' exact shape, so an extracted pack is
structurally indistinguishable from a first-party one (the test_packs parity contracts
apply to both).

INBOUND (install/update) shipped WITH the v0.8.0 trust spine (SEC-5/6/7) — the gate its
long-standing negative-capability pin waited on. A foreign pack IS the public-corpus
prompt-injection threat, so the inbound path is per-item and layered:

  - ``pack_install_plan`` (read-only) validates the manifest shape and, per memory,
    reports the SEC-5-style consent surface (name + the description that would inject),
    secret-lint findings, portability findings, the manifest's own individual-confirm
    markers, dup/conflict routing against the existing corpus (``check_candidate``), and
    name collisions. NOTHING installs from a plan.
  - ``pack_install_item`` installs ONE explicitly-approved memory: secret findings REFUSE
    (unlike ``write_memory``'s warn-only — foreign content gets the hard gate), an
    existing target refuses (never clobber), the file lands as ordinary markdown-in-git
    (inv1), the machine-local SEC-6 baseline absorbs it (``trust.record_authored_write``
    — the per-item approval IS the review), and the COMMITTED lockfile
    (``.claude/memory/.packs.lock.json``) records source/version/base text so update can
    three-way-merge later. Additive project-local artifact, deliberately NOT a
    corpus_format bump (the memory-file shapes are unchanged — same reasoning as
    ``.audit-fixtures``/audit-history side files).
  - ``pack_update_plan`` / ``pack_update_item``: per-item three-way merge —
    base = the lockfile's text-as-installed, ours = the corpus file (local edits),
    theirs = the new source (re-stamped into the same versioned space) — via
    ``git merge-file``; a conflict REFUSES (the agent resolves by hand and passes
    ``resolved_text``); local edits survive a clean merge by construction. Fail-safe:
    any merge machinery failure reads as a conflict, never a silent overwrite.

  Sources are LOCAL DIRECTORIES only — the module stays offline/pure; the /hippo:pack
  skill does any `git clone` into a temp dir first, and the URL rides into the lockfile
  as provenance via ``source=``.

Extraction discipline (all zero-change-on-refusal, like promote):
  - VALIDATE EVERYTHING FIRST, WRITE LAST (RCH-7) — every name must exist, parse and be
    un-retired (``invalid_after`` refuses), the destination must be outside the corpus
    and collision-free (an existing ``manifest.json`` or target ``.md`` refuses; never
    clobber a pack), and every portable rewrite is computed AND damage-checked before
    the first byte lands. A refusal therefore reports EVERY problem at once
    (``invalid``: name → reason) with zero filesystem change — never one error per
    call, never a partial manifest-less dir. ``names="all"`` selects through the
    canonical corpus-membership filter (docs like ``MEMORY.md``/``CONVENTIONS.md`` are
    never candidates) and reports non-extractable memories in ``skipped`` instead of
    refusing, so "pack up everything" is one call, not a glob.
  - Portability lint per file (RCH-6, the shared primitive): ``consequential_default``
    findings do not block — they become the manifest's ``confirm: "individual"`` +
    ``reason`` markers, EXACTLY the mechanism the shipped packs use to force per-item
    consent at seed time (the linter's findings and the shipped manifests' markers are
    parity-pinned to each other, so derived markers mean a consumer of an extracted pack
    gets the same protection). ``repo_coupling`` findings ride out on the result for the
    extracting agent to resolve or accept.
  - The copied text is made portable the same way promote does it: provenance
    (``cited_paths``/``source_commit``/``source_commit_time``) and project governance
    (``steer:``) are stripped — pack files are repo-independent by design — and
    ``metadata.pack`` / ``metadata.pack_version`` are stamped (doctor's pack-drift check
    reads them back).
"""

from __future__ import annotations

import difflib
import json
import os
import re
from typing import List, Optional, Tuple, Union

# Group 1 is the key's indent — `provenance.strip_frontmatter_keys` reads it to decide
# which `- item` continuation lines belong to this key (COR-9).
_STEER_LINE_RE = re.compile(r"^(\s*)steer\s*:")

# RCH-5 inbound: the committed lockfile of installed pack sources. Lives IN the corpus
# dir (committable — teammates share which packs/versions this project runs, and update's
# three-way base travels with the repo). Own schema, additive artifact — see module
# docstring for why this is not a corpus_format event.
_LOCKFILE_NAME = ".packs.lock.json"
_LOCK_SCHEMA = 1
# Matched per-FRONTMATTER-line only (COR-13) — deliberately not re.M over the whole file,
# which is how the pre-COR-13 install stamp rewrote a BODY that merely mentioned the key.
_PACK_VERSION_LINE_RE = re.compile(r"^(\s*)pack_version\s*:")
_MAX_PLAN_DIFF_LINES = 120  # bounded per-item diff in update plans (apply recomputes)

# The frontmatter keys each pack writer owns (COR-9's may_change contract): extraction
# strips the provenance triplet and `steer` and adds the two pack stamps; install/update
# stamping owns the two stamps alone. Anything else surviving a rewrite changed is a bug.
_EXTRACT_OWNED = frozenset(
    {"cited_paths", "source_commit", "source_commit_time", "steer", "pack", "pack_version"}
)
_STAMP_OWNED = frozenset({"pack", "pack_version"})


def _dest_inside_corpus(dest: str, memory_dir: str) -> bool:
    """True when ``dest`` (which need not exist yet) is, or sits inside, the corpus.

    COR-15: a lexical ``commonpath`` check is not containment. The corpus is routinely
    REACHED through a symlink — the native-memory layout
    (``~/.claude/projects/<slug>/memory`` -> corpus) is one hippo itself wires up — and
    the default macOS filesystem is case-insensitive, so a differently-spelled dest can
    land inside the corpus while comparing unequal as a string. Identity, not spelling:
    resolve ``dest``'s nearest EXISTING ancestor and walk it to the root comparing
    ``(st_dev, st_ino)`` against the corpus dir — inodes are immune to both symlinks
    and case. When the corpus dir does not exist, fall back to comparing resolved
    paths lexically (there is nothing to stat, and nothing to pollute either).
    """
    try:
        target = os.stat(memory_dir)
        target_key = (target.st_dev, target.st_ino)
    except OSError:
        try:
            real_dest = os.path.realpath(dest)
            real_mem = os.path.realpath(memory_dir)
            return os.path.commonpath([real_dest, real_mem]) == real_mem
        except ValueError:
            return False
    probe = os.path.abspath(dest)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    p = os.path.realpath(probe)
    while True:
        try:
            st = os.stat(p)
            if (st.st_dev, st.st_ino) == target_key:
                return True
        except OSError:
            pass
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


def _stamp_pack(text: str, pack: str, version: str) -> str:
    """Insert ``pack``/``pack_version`` into the frontmatter — nested under a block-style
    ``metadata:`` when one exists (at that block's OWN child indent), else appended
    top-level (doctor's pack-drift check and the install plan read both scopes). Body
    stays byte-identical.

    COR-13: this was the FIFTH hand-copied frontmatter-insertion walk — the family
    ``provenance.insert_frontmatter_keys`` (COR-9) consolidated — and the last one still
    carrying the family's corruption modes. The hand-rolled walk recognized ``metadata:``
    only as that exact line, so a flow-style ``metadata: {…}`` or a trailing comment got
    a DUPLICATE ``metadata:`` block appended — YAML last-wins, and every original
    metadata key (``type`` first among them) silently dropped. And its stamp indent was
    hard-coded to two spaces, so a block whose children indent differently became a
    mixed-indent document that no longer parses. The shared walk handles both: indent is
    read from the block's own keys, and an unrecognized shape degrades to a top-level
    append — never a duplicate block, never a lost key.
    """
    from .provenance import insert_frontmatter_keys, split_frontmatter

    if split_frontmatter(text)[0] is None:
        return text
    lines = text.split("\n")
    close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    fm = insert_frontmatter_keys(
        lines[1:close], [f"pack: {pack}", f'pack_version: "{version}"']
    )
    return "\n".join([lines[0]] + fm + lines[close:])


def _stamp_damage(before: str, after: str, owned) -> Optional[str]:
    """COR-13: the one damage question every pack writer answers BEFORE its bytes land —
    did the rewrite touch any frontmatter key outside ``owned``, or the body at all?

    ``provenance._frontmatter_damage`` is value-level over both schema scopes; the body
    comparison closes the half it cannot see. A pack writer's body contract is
    byte-identity, and the pre-COR-13 install stamp corrupted exactly there — in the
    half no frontmatter check inspects.
    """
    from .provenance import _frontmatter_damage, split_frontmatter

    if split_frontmatter(before)[1] != split_frontmatter(after)[1]:
        return "it would rewrite the BODY (a pack stamp owns frontmatter keys only)"
    return _frontmatter_damage(before, after, owned)


def pack_extract(
    names: Union[List[str], str],
    dest: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    pack: Optional[str] = None,
    version: str = "0.1.0",
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Copy chosen corpus memories into ``dest`` as a reviewable pack. Never raises.

    ``names`` is a list of memory names, or the string ``"all"``: every file the
    canonical corpus-membership filter admits (``MEMORY.md`` / ``CONVENTIONS.md`` and
    friends are docs, not memories, and are never candidates — callers must never glob
    the corpus dir themselves), with retired/unparseable ones reported per-name in
    ``skipped`` rather than sinking the batch. Explicit names refuse the batch instead
    — but with EVERY problem collected into ``invalid`` (name → reason) in ONE pass,
    never one refusal per call.

    Two phases, and only the second touches the filesystem (RCH-7): every name is
    validated AND its portable rewrite computed and damage-checked BEFORE the first
    write, so every refusal — unknown name, non-memory file, retired memory, collision,
    writer damage — is a zero-filesystem-change event carrying the complete reason map.
    A mid-write I/O failure rolls the written files back: there is no state in which
    ``dest`` holds a partial, manifest-less pack.

    ``pack`` defaults to ``basename(dest)`` (the shipped convention: manifest ``pack``
    == its directory name, which the parity tests pin). Result:
    ``{"extracted", "dest", "manifest", "findings", "invalid", "skipped", "refused",
    "error"}`` where ``findings`` maps each extracted memory to its portability findings
    (``confirm``-severity ones also became the manifest's individual-confirm markers).
    """
    result = {
        "extracted": [],
        "dest": dest,
        "manifest": None,
        "findings": {},
        "invalid": {},
        "skipped": {},
        "refused": False,
        "error": None,
    }
    try:
        from .portability import scan_portability
        from .provenance import (
            _is_memory_filename,
            _strip_provenance,
            parse_frontmatter,
            resolve_dirs,
            strip_frontmatter_keys,
        )
        from .staleness import read_invalid_after, read_provenance

        select_all = names == "all"
        if not select_all and (not names or not isinstance(names, list)):
            result["error"] = "names must be a non-empty list of memory names, or 'all'"
            return result
        if memory_dir is None:
            md, repo = resolve_dirs()
            memory_dir = md
            repo_root = repo_root or repo
        pack = pack or os.path.basename(os.path.abspath(dest))
        if not pack:
            result["error"] = "cannot derive a pack id from dest — pass pack="
            return result
        if _dest_inside_corpus(dest, memory_dir):
            result["refused"] = True
            result["error"] = (
                f"dest {dest} is inside the corpus {memory_dir} — extracted pack files "
                "would be indexed as memories; choose a directory outside the corpus"
            )
            return result
        if select_all:
            try:
                names = sorted(
                    f[:-3] for f in os.listdir(memory_dir) if _is_memory_filename(f)
                )
            except OSError as exc:
                result["error"] = f"cannot list {memory_dir}: {exc}"
                return result
            if not names:
                result["error"] = f"no memories found in {memory_dir}"
                return result
        else:
            names = list(dict.fromkeys(names))  # de-dup, order preserved

        # --- phase 1: validate EVERYTHING, compute EVERY rewrite — zero writes -----
        # Problems collect into `invalid` (explicit names) / `skipped` ("all" mode) in
        # one pass: a 69-name batch with three bad names reports all three, never a
        # probe-one-refusal-at-a-time loop.
        portable_texts = {}
        entries = []
        for name in names:
            # SEC-18: an explicit name is a corpus-memory STEM. A path separator or an
            # absolute path would READ files outside the corpus into a shareable pack —
            # and the copy's write target (`dest/<name>.md`) would escape dest. Doc
            # names (MEMORY/CONVENTIONS) are refused the same canonical way "all" never
            # selects them. Collected like every other problem: one refusal, all names.
            if (
                not isinstance(name, str)
                or not name
                or os.path.isabs(name)
                or os.path.basename(name) != name
                or not _is_memory_filename(f"{name}.md")
            ):
                (result["skipped"] if select_all else result["invalid"])[str(name)] = (
                    "not a memory name — pass the bare stem of a corpus memory "
                    "(no path separators, and docs like MEMORY.md are not memories)"
                )
                continue
            src = os.path.join(memory_dir, f"{name}.md")
            problem = None
            text = None
            if not os.path.isfile(src):
                problem = "not found"
            else:
                with open(src, "r", encoding="utf-8") as fh:
                    text = fh.read()
                if not parse_frontmatter(text):
                    problem = "no parseable frontmatter — not a recall-ready memory"
                else:
                    boundary = read_invalid_after(text)
                    if boundary is not None:
                        problem = (
                            f"retired (invalid_after {boundary}) — resolve its "
                            "lifecycle first"
                        )
            if problem:
                # "all" selected it mechanically → skip it and say why; an explicit
                # name was ASKED for → the whole batch refuses (below), every reason
                # named.
                (result["skipped"] if select_all else result["invalid"])[name] = problem
                continue
            cited, _ = read_provenance(text)
            findings = scan_portability(text, cited_paths=cited)
            portable = _strip_provenance(text)
            # COR-9: `steer` is scalar today, but strip it with the same continuation-aware
            # primitive rather than a bare line filter — a pack ships to another machine,
            # so a frontmatter break here lands in someone else's corpus.
            portable = strip_frontmatter_keys(portable, _STEER_LINE_RE)
            portable = _stamp_pack(portable, pack, version)
            # COR-9/13: extraction owns the provenance triplet (stripped — a pack is
            # portable), `steer` (stripped — project-local), and the two pack stamps it
            # adds; the body is byte-identical. A pack ships to someone else's corpus,
            # so damage here lands on another machine.
            damage = _stamp_damage(text, portable, _EXTRACT_OWNED)
            if damage:
                # A writer bug is never skippable, even under "all" — refuse the batch
                # loudly (still zero-change, still with every name listed) so the bug
                # gets reported instead of shipping a silently thinner pack.
                result["invalid"][name] = (
                    f"{damage} — this is a hippo bug, please report it"
                )
                continue
            result["findings"][name] = findings
            reasons = [
                f["detail"] for f in findings if f.get("severity") == "confirm"
            ]
            entry: dict = {"file": f"{name}.md"}
            if reasons:
                entry["confirm"] = "individual"
                entry["reason"] = "; ".join(reasons)
            entries.append(entry)
            portable_texts[name] = portable

        manifest_path = os.path.join(dest, "manifest.json")
        if os.path.isfile(manifest_path):
            result["invalid"]["manifest.json"] = (
                f"already exists at {manifest_path} — refusing to overwrite a pack"
            )
        for name in portable_texts:
            if os.path.isfile(os.path.join(dest, f"{name}.md")):
                result["invalid"][name] = (
                    f"{name}.md already exists in {dest} — refusing to overwrite"
                )
        if result["invalid"]:
            result["refused"] = True
            result["findings"] = {}
            shown = list(result["invalid"].items())[:3]
            headline = "; ".join(f"{n}: {r}" for n, r in shown)
            more = len(result["invalid"]) - len(shown)
            result["error"] = (
                f"{len(result['invalid'])} problem(s) refused the extract of "
                f"{len(names)} name(s) — zero files written; {headline}"
                + (f" (+{more} more — 'invalid' carries every name and reason)" if more else "")
            )
            return result
        if not portable_texts:
            result["refused"] = True
            result["error"] = (
                "nothing to extract — every selected memory was skipped; "
                "'skipped' carries each name and reason"
            )
            return result

        # --- phase 2: the ONLY writes — every byte was computed and guarded above ---
        created_dest = not os.path.isdir(dest)
        written: List[str] = []
        try:
            os.makedirs(dest, exist_ok=True)
            for name, portable in portable_texts.items():
                path = os.path.join(dest, f"{name}.md")
                # RCH-8: into the rollback set BEFORE the bytes — a failure MID-write
                # (disk full on file N) must unlink the partial file N too, not just
                # the N-1 complete ones, or dest holds exactly the manifest-less
                # partial state the two-phase split promises away.
                written.append(path)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(portable)
                result["extracted"].append(f"{name}.md")
            manifest = {
                "pack": pack,
                "version": version,
                "title": title or pack,
                "description": description
                or (
                    f"extracted from a hippo corpus — {len(portable_texts)} memories; "
                    "review each before seeding"
                ),
                "seed_by_default": False,
                "memories": entries,
            }
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=False)
                fh.write("\n")
            result["manifest"] = manifest_path
        except BaseException as exc:
            # A mid-write failure must not strand a partial, manifest-less pack — the
            # one filesystem state the two-phase split cannot rule out up front.
            # BaseException, not Exception: a Ctrl-C mid-loop deserves the same
            # rollback, and then propagates (RCH-8) — only I/O-class failures are
            # swallowed into the result envelope.
            for p in written + [manifest_path]:
                try:
                    if os.path.isfile(p):
                        os.unlink(p)
                except OSError:
                    pass
            if created_dest:
                try:
                    os.rmdir(dest)
                except OSError:
                    pass
            result["extracted"] = []
            result["error"] = f"write failed, partial pack rolled back: {exc}"
            if not isinstance(exc, Exception):
                raise
    except Exception as exc:
        result["error"] = result["error"] or f"pack extract failed: {exc}"
    return result


# --------------------------------------------------------------------------- #
# RCH-5 inbound — install / update, per-item, on the v0.8.0 trust spine.
# --------------------------------------------------------------------------- #
def load_pack_manifest(source_dir: str) -> Tuple[Optional[dict], Optional[str]]:
    """Parse + shape-validate a pack source's ``manifest.json``.

    Returns ``(manifest, None)`` on the SHIPPED shape (``pack`` non-empty str,
    ``version`` present, ``memories`` a list of ``{file: <name>.md, ...}`` rows whose
    files all exist in ``source_dir``), else ``(None, reason)``. A malformed foreign
    manifest must fail loudly BEFORE any per-file work — shape first, content second.
    """
    path = os.path.join(source_dir, "manifest.json")
    if not os.path.isfile(path):
        return None, f"no manifest.json in {source_dir}"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        return None, f"manifest.json is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "manifest.json is not a JSON object"
    if not isinstance(data.get("pack"), str) or not data["pack"].strip():
        return None, "manifest has no non-empty 'pack' id"
    if data.get("version") is None:
        return None, "manifest has no 'version'"
    memories = data.get("memories")
    if not isinstance(memories, list) or not memories:
        return None, "manifest has no 'memories' list"
    for row in memories:
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            return None, "every memories[] row must be an object with a 'file'"
        fname = row["file"]
        if not fname.endswith(".md") or os.path.basename(fname) != fname:
            return None, f"illegal memory file name in manifest: {fname!r}"
        if not os.path.isfile(os.path.join(source_dir, fname)):
            return None, f"manifest names {fname} but the source dir has no such file"
    return data, None


def lockfile_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, _LOCKFILE_NAME)


def _load_lockfile(memory_dir: str) -> Tuple[dict, Optional[str]]:
    """``(doc, error)``. A MISSING lockfile is a fresh start; a PRESENT-but-corrupt one
    is an error the caller must refuse on (COR-17). It used to read as "no packs
    installed": update said "no lockfile record", and the next install rewrote the
    file from scratch — silently orphaning every other pack's three-way merge base.
    The lockfile is committed, so corruption (a bad hand-edit, unresolved git conflict
    markers) has a real escape hatch worth naming."""
    path = lockfile_path(memory_dir)
    if not os.path.isfile(path):
        return {"lock_schema": _LOCK_SCHEMA, "packs": {}}, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("packs"), dict):
            return doc, None
        reason = "not the expected {lock_schema, packs} shape"
    except Exception as exc:
        reason = str(exc)
    return {"lock_schema": _LOCK_SCHEMA, "packs": {}}, (
        f"{_LOCKFILE_NAME} is unreadable ({reason}) — refusing rather than silently "
        "resetting it (that would orphan every installed pack's merge base); it is "
        "committed, so restore it from git, or delete it to knowingly start fresh"
    )


def _write_lockfile(memory_dir: str, doc: dict) -> None:
    from .atomic import write_json_atomic

    # SEC-19/COR-17: atomic — a torn lockfile write silently wiped every pack's
    # three-way base, surfacing only on the NEXT update attempt.
    write_json_atomic(lockfile_path(memory_dir), doc, sort_keys=True)


def _ensure_pack_stamp(text: str, pack: str, version: str) -> str:
    """``_stamp_pack`` made idempotent/version-updating: an existing FRONTMATTER
    ``pack_version`` line is rewritten to ``version`` (the update path re-versions
    instead of stacking a second stamp); a stamp-less file gets the full insert. Keeps
    install and update in ONE 'stamped space' so three-way merges never see the stamp as
    a phantom edit.

    COR-13: the rewrite is scoped to the frontmatter block. It used to be a MULTILINE
    regex over the whole file, so a BODY that merely mentioned ``pack_version:`` (notes
    about packs, a fenced example) had its body line rewritten AND its frontmatter never
    stamped — a writer touching text it does not own, on the one path that had no
    damage guard to catch it.
    """
    from .provenance import split_frontmatter

    if split_frontmatter(text)[0] is not None:
        lines = text.split("\n")
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        for idx in range(1, close):
            m = _PACK_VERSION_LINE_RE.match(lines[idx])
            if m:
                lines[idx] = f'{m.group(1)}pack_version: "{version}"'
                return "\n".join(lines)
    return _stamp_pack(text, pack, version)


def _merge3(base: str, ours: str, theirs: str) -> Tuple[str, bool]:
    """Three-way merge via ``git merge-file`` (plumbing — works outside any repo).

    Returns ``(merged_text, conflict)``. ANY machinery failure reads as a conflict with
    ``ours`` returned untouched — fail safe to "needs a human", never a silent overwrite.
    """
    import subprocess
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for label, content in (("ours", ours), ("base", base), ("theirs", theirs)):
                p = os.path.join(td, label)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(content)
                paths[label] = p
            proc = subprocess.run(
                ["git", "merge-file", "-p", paths["ours"], paths["base"], paths["theirs"]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode < 0:
                return ours, True
            return proc.stdout, proc.returncode != 0
    except Exception:
        return ours, True


def pack_install_plan(
    source_dir: str, *, memory_dir: Optional[str] = None, repo_root: Optional[str] = None
) -> dict:
    """READ-ONLY per-item review material for installing a pack from a LOCAL source dir.

    Never writes. Per memory: the SEC-5-style consent surface (name + the description
    that would inject), secret-lint findings (these will REFUSE at install), portability
    findings, the manifest's own individual-confirm marker/reason, dup/conflict routing
    against the existing corpus (``new_memory.check_candidate`` — a near-duplicate routes
    to update-existing/supersede/skip, not a blind add), and target collisions.
    ``installable`` is False for secret-flagged or colliding items. The consuming skill
    presents each row as QUOTED DATA (a foreign pack is untrusted text — the same
    demarcation discipline as the doctor consent step) and installs only explicitly
    approved names, one ``pack_install_item`` call each.
    """
    result = {"pack": None, "version": None, "source": source_dir, "items": [], "error": None}
    try:
        from .new_memory import check_candidate
        from .portability import scan_portability
        from .provenance import parse_frontmatter, resolve_dirs
        from .recall import inject_description
        from .secrets import scan_with_remediation

        if memory_dir is None:
            memory_dir, repo = resolve_dirs()
            repo_root = repo_root or repo
        manifest, err = load_pack_manifest(source_dir)
        if err:
            result["error"] = err
            return result
        result["pack"], result["version"] = manifest["pack"], str(manifest["version"])
        for row in manifest["memories"]:
            fname = row["file"]
            name = fname[:-3]
            with open(os.path.join(source_dir, fname), "r", encoding="utf-8") as fh:
                text = fh.read()
            fm = parse_frontmatter(text)
            item = {
                "name": name,
                "will_inject": inject_description(str(fm.get("description") or "")),
                "type": fm.get("type"),
                "confirm": row.get("confirm"),
                "reason": row.get("reason"),
                "secrets": scan_with_remediation(text),
                "portability": scan_portability(text),
                "collision": os.path.isfile(os.path.join(memory_dir, fname)),
                "route": "add",
                "neighbors": [],
            }
            if not fm:
                item["secrets"] = item["secrets"] or []
                item["error"] = "unparseable frontmatter — not a recall-ready memory"
            try:
                chk = check_candidate(
                    name,
                    str(fm.get("description") or ""),
                    str(fm.get("type") or "project"),
                    memory_dir=memory_dir,
                    repo_root=repo_root,
                )
                item["route"] = chk.get("route", "add")
                item["neighbors"] = chk.get("neighbors", [])
            except Exception as exc:
                # RCH-9: a failed dup-check must not read as "no duplicates" — the
                # plan is the consent surface; say the check did not run.
                item["route_error"] = (
                    f"duplicate check failed ({exc}) — route 'add' is UNVERIFIED; "
                    "check your corpus for near-duplicates by hand"
                )
            item["installable"] = not item["secrets"] and not item["collision"] and not item.get("error")
            result["items"].append(item)
    except Exception as exc:
        result["error"] = result["error"] or f"pack install plan failed: {exc}"
    return result


def pack_install_item(
    source_dir: str,
    name: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    source: Optional[str] = None,
) -> dict:
    """Install ONE explicitly-approved memory from a pack source. Per-item by design.

    Hard gates (refuse, nothing written): the manifest must validate; the file must
    parse; secret-lint findings REFUSE (foreign content never gets ``write_memory``'s
    warn-only leniency); an existing ``<name>.md`` with DIFFERENT content refuses (a
    same-name update routes through ``pack_update_item``; anything else is the agent's
    rename/skip decision). An existing byte-identical file ADOPTS instead (INT-17):
    the lockfile record is restored — the escape hatch for a crash between install's
    file write and lockfile write, and for hand-seeded copies of this pack, which
    otherwise circle install -> update -> install with every verb refusing.
    On install: the text is pack-stamped (idempotently), written exclusively, recorded
    in the COMMITTED lockfile (source/version + the installed text as the future
    three-way base), folded into the machine-local SEC-6 consent baseline (the per-item
    approval IS the review), and the index refreshed best-effort. ``source`` labels the
    lockfile provenance (pass the git URL the skill cloned; defaults to ``source_dir``).
    """
    result = {"installed": False, "adopted": False, "path": None, "warnings": [], "error": None}
    try:
        from .provenance import parse_frontmatter, resolve_dirs
        from .secrets import scan_with_remediation

        if memory_dir is None:
            memory_dir, repo = resolve_dirs()
            repo_root = repo_root or repo
        manifest, err = load_pack_manifest(source_dir)
        if err:
            result["error"] = err
            return result
        fname = f"{name}.md"
        if not any(r["file"] == fname for r in manifest["memories"]):
            result["error"] = f"{fname} is not in this pack's manifest"
            return result
        with open(os.path.join(source_dir, fname), "r", encoding="utf-8") as fh:
            raw = fh.read()
        if not parse_frontmatter(raw):
            result["error"] = f"{fname} has no parseable frontmatter — refusing to install"
            return result
        secrets = scan_with_remediation(raw)
        if secrets:
            result["error"] = (
                f"secret-lint flagged {fname} — a foreign pack file never installs with "
                f"findings: {'; '.join(secrets)}"
            )
            return result
        pack, version = manifest["pack"], str(manifest["version"])
        stamped = _ensure_pack_stamp(raw, pack, version)
        # COR-13: the write-site guard install never had — the stamp rewrite owns the
        # two pack keys and the body must be byte-identical. Foreign text a writer bug
        # corrupted must never land in the corpus; refuse and name the bug instead.
        damage = _stamp_damage(raw, stamped, _STAMP_OWNED)
        if damage:
            result["error"] = (
                f"refusing to install {fname}: {damage} — this is a hippo bug, "
                "please report it"
            )
            return result
        # COR-17: the lockfile is checked BEFORE the corpus write — a corrupt lockfile
        # refuses with zero filesystem change, never installs-then-resets.
        lock, lock_err = _load_lockfile(memory_dir)
        if lock_err:
            result["error"] = lock_err
            return result
        target = os.path.join(memory_dir, fname)
        adopted = False
        try:
            os.makedirs(memory_dir, exist_ok=True)
            with open(target, "x", encoding="utf-8") as fh:  # exclusive: never clobber
                fh.write(stamped)
        except FileExistsError:
            # INT-17: byte-identical content ADOPTS instead of refusing. A crash between
            # install's file write and its lockfile write (or a hand-seeded copy of this
            # pack) used to dead-end the verbs in a circle: update plan said "new
            # upstream -> install", install said "route through update", update said
            # "not an installed member". Identical bytes mean the only thing missing is
            # the lockfile record — restore it. Different bytes keep the hard refusal.
            try:
                with open(target, "r", encoding="utf-8") as fh:
                    existing = fh.read()
            except OSError:
                existing = None
            if existing != stamped:
                result["error"] = (
                    f"{fname} already exists in the corpus with different content — "
                    "updates route through pack_update_item; a byte-identical file "
                    "would be adopted (its lockfile record restored); anything else "
                    "is a rename/skip decision"
                )
                return result
            adopted = True
        result["installed"] = True
        result["adopted"] = adopted
        result["path"] = target

        entry = lock["packs"].setdefault(
            pack, {"source": source or source_dir, "version": version, "installed": {}}
        )
        entry["source"] = source or entry.get("source") or source_dir
        entry["version"] = version
        from .trust import file_sha256

        entry["installed"][name] = {"base": stamped, "sha256": file_sha256(target)}
        _write_lockfile(memory_dir, lock)

        # SEC-6: the per-item approval that led here IS the review — consent the bytes.
        try:
            from .trust import record_authored_write

            record_authored_write(memory_dir, target, repo_root)
        except Exception:
            pass
        try:
            from .build_index import refresh_index

            refresh_index(memory_dir)
        except Exception:
            pass
    except Exception as exc:
        result["error"] = result["error"] or f"pack install failed: {exc}"
    return result


def _update_states(source_dir: str, memory_dir: str) -> Tuple[Optional[dict], Optional[str]]:
    """Shared install-state walk for update plan/apply: per installed item, classify
    against (base = lockfile text-as-installed, ours = corpus file, theirs = new source
    re-stamped). Returns ``({pack, version, items: {name: state-dict}}, error)``."""
    manifest, err = load_pack_manifest(source_dir)
    if err:
        return None, err
    pack, new_version = manifest["pack"], str(manifest["version"])
    lock, lock_err = _load_lockfile(memory_dir)
    if lock_err:
        return None, lock_err
    entry = lock["packs"].get(pack)
    if not isinstance(entry, dict) or not isinstance(entry.get("installed"), dict):
        return None, (
            f"pack {pack!r} has no lockfile record in this corpus — nothing to update. "
            "Install first: pack_install_item records the lockfile base, and a "
            "byte-identical hand-seeded file is ADOPTED (record restored) rather "
            "than refused (INT-17)"
        )
    manifest_files = {r["file"] for r in manifest["memories"]}
    items: dict = {}
    for name, rec in sorted(entry["installed"].items()):
        fname = f"{name}.md"
        ours_path = os.path.join(memory_dir, fname)
        base = str(rec.get("base") or "")
        it: dict = {"state": None, "proposed": None, "conflict": False, "path": ours_path}
        if not os.path.isfile(ours_path):
            it["state"] = "missing-local"  # user removed/archived it — never resurrect
        elif fname not in manifest_files:
            it["state"] = "removed-upstream"  # keep ours; report only
        else:
            with open(ours_path, "r", encoding="utf-8") as fh:
                ours = fh.read()
            with open(os.path.join(source_dir, fname), "r", encoding="utf-8") as fh:
                theirs_raw = fh.read()
            theirs = _ensure_pack_stamp(theirs_raw, pack, new_version)
            # COR-13: same write-site guard as install, per item — a damaged re-stamp
            # poisons the three-way's `theirs` side, so it refuses THIS item (state
            # reported with the reason) without sinking the rest of the plan.
            stamp_damage = _stamp_damage(theirs_raw, theirs, _STAMP_OWNED)
            if stamp_damage:
                it["state"] = "stamp-refused"
                it["error"] = (
                    f"{stamp_damage} — this is a hippo bug, please report it"
                )
                items[name] = it
                continue
            if theirs == base:
                it["state"] = "local-only" if ours != base else "unchanged"
            elif ours == base:
                it["state"] = "fast-forward"
                it["proposed"] = theirs
            else:
                merged, conflict = _merge3(base, ours, theirs)
                it["state"] = "conflict" if conflict else "merged"
                it["conflict"] = conflict
                it["proposed"] = None if conflict else merged
            it["ours"] = ours
            it["theirs"] = theirs
        items[name] = it
    new_files = sorted(
        f[:-3] for f in manifest_files if f[:-3] not in entry["installed"]
    )
    return {"pack": pack, "version": new_version, "items": items, "new_upstream": new_files}, None


def pack_update_plan(
    source_dir: str, *, memory_dir: Optional[str] = None, repo_root: Optional[str] = None
) -> dict:
    """READ-ONLY per-item update review: three-way state + a bounded diff per memory.

    States: ``unchanged`` / ``local-only`` (your edits, upstream quiet — nothing to do) /
    ``fast-forward`` (upstream-only change) / ``merged`` (both changed, clean three-way —
    local edits preserved) / ``conflict`` (both changed the same region — a human
    resolves) / ``removed-upstream`` and ``missing-local`` (report-only; update never
    deletes ours and never resurrects a memory you removed) / ``stamp-refused``
    (COR-13: re-stamping the new upstream text would damage keys the stamp does not
    own — a hippo bug, named in the row's ``error``; the item refuses without sinking
    the rest of the plan). ``new_upstream`` names additions that route through
    ``pack_install_plan``/``pack_install_item`` instead.
    """
    result = {"pack": None, "version": None, "items": [], "new_upstream": [], "error": None}
    try:
        from .provenance import resolve_dirs

        if memory_dir is None:
            memory_dir, repo = resolve_dirs()
            repo_root = repo_root or repo
        states, err = _update_states(source_dir, memory_dir)
        if err:
            result["error"] = err
            return result
        result["pack"], result["version"] = states["pack"], states["version"]
        result["new_upstream"] = states["new_upstream"]
        for name, it in states["items"].items():
            row = {"name": name, "state": it["state"], "conflict": it["conflict"], "diff": ""}
            if it.get("error"):
                row["error"] = it["error"]  # stamp-refused rows carry the COR-13 reason
            if it.get("proposed") is not None:
                diff_lines = list(
                    difflib.unified_diff(
                        it["ours"].splitlines(),
                        it["proposed"].splitlines(),
                        fromfile=f"{name}.md (yours)",
                        tofile=f"{name}.md (after update)",
                        lineterm="",
                    )
                )[:_MAX_PLAN_DIFF_LINES]
                row["diff"] = "\n".join(diff_lines)
            result["items"].append(row)
    except Exception as exc:
        result["error"] = result["error"] or f"pack update plan failed: {exc}"
    return result


def pack_update_item(
    source_dir: str,
    name: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    resolved_text: Optional[str] = None,
) -> dict:
    """Apply ONE explicitly-approved pack update. Per-item by design.

    ``fast-forward``/``merged`` states write the computed text; a ``conflict`` REFUSES
    unless the agent passes ``resolved_text`` (the human-reviewed hand-merge). The new
    text is secret-linted (refuse on findings — same hard gate as install), written, the
    lockfile base advances to the NEW upstream text (so the next update merges from the
    right ancestor), the SEC-6 baseline absorbs the bytes, and the index refreshes.
    ``unchanged``/``local-only``/``removed-upstream``/``missing-local`` refuse with the
    state named — there is nothing this call should do to them.
    """
    result = {"updated": False, "path": None, "state": None, "error": None}
    try:
        from .provenance import resolve_dirs
        from .secrets import scan_with_remediation

        if memory_dir is None:
            memory_dir, repo = resolve_dirs()
            repo_root = repo_root or repo
        states, err = _update_states(source_dir, memory_dir)
        if err:
            result["error"] = err
            return result
        it = states["items"].get(name)
        if it is None:
            result["error"] = f"{name} is not an installed member of pack {states['pack']!r}"
            return result
        result["state"] = it["state"]
        if it["state"] == "stamp-refused":
            result["error"] = f"refusing to update {name}: {it['error']}"
            return result
        if it["state"] in ("unchanged", "local-only", "removed-upstream", "missing-local"):
            result["error"] = f"nothing to apply for {name} (state: {it['state']})"
            return result
        if it["state"] == "conflict" and resolved_text is None:
            result["error"] = (
                f"{name} has a three-way CONFLICT — resolve by hand and pass "
                "resolved_text (the reviewed merge), or skip it"
            )
            return result
        new_text = resolved_text if resolved_text is not None else it["proposed"]
        secrets = scan_with_remediation(new_text)
        if secrets:
            result["error"] = (
                f"secret-lint flagged the updated {name}.md — refusing: {'; '.join(secrets)}"
            )
            return result
        from .atomic import write_text_atomic

        # COR-18: atomic — this write REPLACES a file holding the user's local edits;
        # a torn in-place write was the one way to lose them (they exist nowhere else).
        write_text_atomic(it["path"], new_text)
        result["updated"] = True
        result["path"] = it["path"]

        lock, lock_err = _load_lockfile(memory_dir)
        if lock_err:
            # The lockfile went corrupt between the plan's check and now — roll the
            # file write back (COR-16) rather than leaving an update whose base can
            # never advance.
            from .provenance import restore_file_bytes

            undo_err = restore_file_bytes(it["path"], it["ours"], memory_dir, repo_root)
            result["updated"] = False
            result["error"] = lock_err + (
                f" — AND the rollback failed ({undo_err}): {name}.md now carries the "
                "update without a lockfile base; restore both from git"
                if undo_err
                else " — the file write was rolled back"
            )
            return result
        entry = lock["packs"][states["pack"]]
        entry["version"] = states["version"]
        from .trust import file_sha256

        entry["installed"][name] = {"base": it["theirs"], "sha256": file_sha256(it["path"])}
        _write_lockfile(memory_dir, lock)
        try:
            from .trust import record_authored_write

            record_authored_write(memory_dir, it["path"], repo_root)
        except Exception:
            pass
        try:
            from .build_index import refresh_index

            refresh_index(memory_dir)
        except Exception:
            pass
    except Exception as exc:
        result["error"] = result["error"] or f"pack update failed: {exc}"
    return result
