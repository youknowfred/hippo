"""Pack EXTRACT (RCH-5, the safe early slice) — share your memories as a pack.

TEA-2 shipped starter packs that only travel INSIDE the plugin; nothing let a user turn
their own proven corpus memories into a pack another human could review and seed. This
module is that outbound path: ``pack_extract`` copies chosen memories into a pack dir
with a ``manifest.json`` in the SHIPPED packs' exact shape, so an extracted pack is
structurally indistinguishable from a first-party one (the test_packs parity contracts
apply to both).

THE GATE, stated where the code lives: install-from-source and three-way-merge update
are DELIBERATELY ABSENT. A foreign pack is the public-corpus prompt-injection threat,
and the v0.8.0 trust spine that makes accepting one reviewable (SEC-5
consent-shows-descriptions, SEC-6 fingerprint re-review, SEC-7 inject-time banner) is
not in the tree yet — so there is no ``install_pack``/``update_pack`` here, and a
negative-capability test pins that absence. Extract is safe early because it is LOCAL
and OUTBOUND: zero foreign input, ordinary files the extractor already owns. The
reviewed inbound paths remain ``/hippo:import`` and per-item ``/hippo:new``.

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

import json
import os
import re
from typing import List, Optional

_STEER_LINE_RE = re.compile(r"^\s*steer\s*:")


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
            _strip_provenance,
            parse_frontmatter,
            resolve_dirs,
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
            portable = "\n".join(
                ln for ln in portable.split("\n") if not _STEER_LINE_RE.match(ln)
            )
            portable = _stamp_pack(portable, pack, version)
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
