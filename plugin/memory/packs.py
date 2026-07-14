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
  - VALIDATE EVERYTHING FIRST — every name must exist and be un-retired
    (``invalid_after`` refuses), and the destination must be collision-free (an existing
    ``manifest.json`` or target ``.md`` refuses the whole extract; never clobber a pack).
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
from typing import List, Optional, Tuple

# Group 1 is the key's indent — `provenance.strip_frontmatter_keys` reads it to decide
# which `- item` continuation lines belong to this key (COR-9).
_STEER_LINE_RE = re.compile(r"^(\s*)steer\s*:")

# RCH-5 inbound: the committed lockfile of installed pack sources. Lives IN the corpus
# dir (committable — teammates share which packs/versions this project runs, and update's
# three-way base travels with the repo). Own schema, additive artifact — see module
# docstring for why this is not a corpus_format event.
_LOCKFILE_NAME = ".packs.lock.json"
_LOCK_SCHEMA = 1
_PACK_VERSION_LINE_RE = re.compile(r'^(\s*)pack_version\s*:.*$', re.M)
_MAX_PLAN_DIFF_LINES = 120  # bounded per-item diff in update plans (apply recomputes)


def _stamp_pack(text: str, pack: str, version: str) -> str:
    """Insert ``pack``/``pack_version`` under ``metadata:`` (creating the block if a
    hand-authored file lacks one). Body stays byte-identical."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    stamp = [f"  pack: {pack}", f'  pack_version: "{version}"']
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[:i] + ["metadata:"] + stamp + lines[i:])
        if lines[i].strip() == "metadata:":
            return "\n".join(lines[: i + 1] + stamp + lines[i + 1 :])
    return text


def pack_extract(
    names: List[str],
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

    ``pack`` defaults to ``basename(dest)`` (the shipped convention: manifest ``pack``
    == its directory name, which the parity tests pin). Every input is validated before
    anything is written — a refusal is a zero-filesystem-change event. Result:
    ``{"extracted", "dest", "manifest", "findings", "refused", "error"}`` where
    ``findings`` maps each memory name to its portability findings (``confirm``-severity
    ones also became the manifest's individual-confirm markers).
    """
    result = {
        "extracted": [],
        "dest": dest,
        "manifest": None,
        "findings": {},
        "refused": False,
        "error": None,
    }
    try:
        from .portability import scan_portability
        from .provenance import (
            _frontmatter_damage,
            _strip_provenance,
            parse_frontmatter,
            resolve_dirs,
            strip_frontmatter_keys,
        )
        from .staleness import read_invalid_after, read_provenance

        if not names or not isinstance(names, list):
            result["error"] = "names must be a non-empty list of memory names"
            return result
        if memory_dir is None:
            md, repo = resolve_dirs()
            memory_dir = md
            repo_root = repo_root or repo
        pack = pack or os.path.basename(os.path.abspath(dest))
        if not pack:
            result["error"] = "cannot derive a pack id from dest — pass pack="
            return result

        # --- validate EVERYTHING before writing anything -------------------------
        texts = {}
        for name in names:
            src = os.path.join(memory_dir, f"{name}.md")
            if not os.path.isfile(src):
                result["error"] = f"not found: {name}.md"
                return result
            with open(src, "r", encoding="utf-8") as fh:
                text = fh.read()
            if not parse_frontmatter(text):
                result["error"] = (
                    f"{name}.md has no parseable frontmatter — not a recall-ready memory"
                )
                return result
            boundary = read_invalid_after(text)
            if boundary is not None:
                result["refused"] = True
                result["error"] = (
                    f"{name} is retired (invalid_after {boundary}) — a retired memory "
                    "does not extract; resolve its lifecycle first"
                )
                return result
            texts[name] = text
        manifest_path = os.path.join(dest, "manifest.json")
        if os.path.isfile(manifest_path):
            result["refused"] = True
            result["error"] = (
                f"{manifest_path} already exists — refusing to overwrite a pack"
            )
            return result
        for name in names:
            if os.path.isfile(os.path.join(dest, f"{name}.md")):
                result["refused"] = True
                result["error"] = (
                    f"{name}.md already exists in {dest} — refusing to overwrite"
                )
                return result

        # --- lint, strip, stamp, write -------------------------------------------
        entries = []
        for name in names:
            text = texts[name]
            cited, _ = read_provenance(text)
            findings = scan_portability(text, cited_paths=cited)
            result["findings"][name] = findings
            reasons = [
                f["detail"] for f in findings if f.get("severity") == "confirm"
            ]
            entry: dict = {"file": f"{name}.md"}
            if reasons:
                entry["confirm"] = "individual"
                entry["reason"] = "; ".join(reasons)
            entries.append(entry)
            portable = _strip_provenance(text)
            # COR-9: `steer` is scalar today, but strip it with the same continuation-aware
            # primitive rather than a bare line filter — a pack ships to another machine,
            # so a frontmatter break here lands in someone else's corpus.
            portable = strip_frontmatter_keys(portable, _STEER_LINE_RE)
            portable = _stamp_pack(portable, pack, version)
            # COR-9: extraction owns the provenance triplet (stripped — a pack is portable),
            # `steer` (stripped — project-local), and the two pack stamps it adds. A pack
            # ships to someone else's corpus, so damage here lands on another machine.
            damage = _frontmatter_damage(
                text,
                portable,
                {
                    "cited_paths",
                    "source_commit",
                    "source_commit_time",
                    "steer",
                    "pack",
                    "pack_version",
                },
            )
            if damage:
                result["error"] = (
                    f"refusing to extract {name}: {damage} — this is a hippo bug, "
                    "please report it"
                )
                return result
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, f"{name}.md"), "w", encoding="utf-8") as fh:
                fh.write(portable)
            result["extracted"].append(f"{name}.md")

        manifest = {
            "pack": pack,
            "version": version,
            "title": title or pack,
            "description": description
            or (
                f"extracted from a hippo corpus — {len(names)} memories; review each "
                "before seeding"
            ),
            "seed_by_default": False,
            "memories": entries,
        }
        os.makedirs(dest, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=False)
            fh.write("\n")
        result["manifest"] = manifest_path
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


def _load_lockfile(memory_dir: str) -> dict:
    try:
        with open(lockfile_path(memory_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("packs"), dict):
            return doc
    except Exception:
        pass
    return {"lock_schema": _LOCK_SCHEMA, "packs": {}}


def _write_lockfile(memory_dir: str, doc: dict) -> None:
    with open(lockfile_path(memory_dir), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _ensure_pack_stamp(text: str, pack: str, version: str) -> str:
    """``_stamp_pack`` made idempotent/version-updating: an existing ``pack_version``
    line is rewritten to ``version`` (the update path re-versions instead of stacking a
    second stamp); a stamp-less file gets the full insert. Keeps install and update in
    ONE 'stamped space' so three-way merges never see the stamp as a phantom edit."""
    if _PACK_VERSION_LINE_RE.search(text):
        return _PACK_VERSION_LINE_RE.sub(rf'\1pack_version: "{version}"', text, count=1)
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
            except Exception:
                pass
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
    warn-only leniency); an existing ``<name>.md`` refuses (a same-name update routes
    through ``pack_update_item``; anything else is the agent's rename/skip decision).
    On install: the text is pack-stamped (idempotently), written exclusively, recorded
    in the COMMITTED lockfile (source/version + the installed text as the future
    three-way base), folded into the machine-local SEC-6 consent baseline (the per-item
    approval IS the review), and the index refreshed best-effort. ``source`` labels the
    lockfile provenance (pass the git URL the skill cloned; defaults to ``source_dir``).
    """
    result = {"installed": False, "path": None, "warnings": [], "error": None}
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
        target = os.path.join(memory_dir, fname)
        try:
            os.makedirs(memory_dir, exist_ok=True)
            with open(target, "x", encoding="utf-8") as fh:  # exclusive: never clobber
                fh.write(stamped)
        except FileExistsError:
            result["error"] = (
                f"{fname} already exists in the corpus — updates route through "
                "pack_update_item; anything else is a rename/skip decision"
            )
            return result
        result["installed"] = True
        result["path"] = target

        lock = _load_lockfile(memory_dir)
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
    lock = _load_lockfile(memory_dir)
    entry = lock["packs"].get(pack)
    if not isinstance(entry, dict) or not isinstance(entry.get("installed"), dict):
        return None, (
            f"pack {pack!r} has no lockfile record in this corpus — nothing to update "
            "(install first, or this pack was seeded by hand)"
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
                theirs = _ensure_pack_stamp(fh.read(), pack, new_version)
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
    deletes ours and never resurrects a memory you removed). ``new_upstream`` names
    additions that route through ``pack_install_plan``/``pack_install_item`` instead.
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
        with open(it["path"], "w", encoding="utf-8") as fh:
            fh.write(new_text)
        result["updated"] = True
        result["path"] = it["path"]

        lock = _load_lockfile(memory_dir)
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
