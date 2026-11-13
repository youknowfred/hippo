"""GAT-4: one versioned ``CONVENTIONS.md`` — classify a corpus's copy, refresh it on a yes.

The plugin's asset (``plugin/assets/CONVENTIONS.md``) carries its version on its first line,
``<!-- hippo-conventions: N -->`` (an HTML comment, so it survives a Markdown render). A
corpus's copy is classified by its bytes, never by trust:

  current  byte-equal to the shipped asset
  behind   byte-equal to an EARLIER shipped text (``CONVENTIONS.history.json`` beside the
           asset keeps each earlier version's sha256, the unstamped texts 1-6 recovered from
           this repo's history included)
  edited   anything else: some lines are the corpus's own
  missing  no copy (optional: hippo never requires one)

A corpus's own rules belong in ``CONVENTIONS.local.md`` beside the copy. hippo never
requires that file and never overwrites it; ``hippo setup --conventions --yes`` is the one
writer, and it only appends: an ``edited`` copy's lines that are in no shipped version (the
history file keeps a 12-hex sha256 of every distinct right-stripped line of the earlier
texts; the current asset's lines are read live) are appended there first, then the copy is
replaced. The preview prints the full diff and every line it would move, so no line is ever
discarded unseen. Lines of the asset the corpus deleted come back; that is the point.

Where a refresh lands (``destination``): the outbox, when the corpus's ``write_path`` is
``"outbox"`` and this machine honours it (the copy and the local file are written there, with
a manifest row and a hash record each, and ``hippo ship`` carries them; the working tree is
not touched); else the corpus dir, which from a linked worktree is THAT worktree's own corpus
dir, never the main tree's tracked file. Reads follow the write: an outbox copy hippo recorded
writing is the copy that is classified.

Bumping the asset: change its text, raise N in its first line, add the PREVIOUS text's
sha256 under its version in the history file and its lines' hashes to ``lines``
(``tests/test_conventions_version.py`` pins the asset's hash, so an edit without a bump fails).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
from typing import Dict, List, Optional

NAME = "CONVENTIONS.md"
LOCAL_NAME = "CONVENTIONS.local.md"
_STAMP_RE = re.compile(r"^<!-- hippo-conventions: (\d+) -->$")
_CMD = "hippo setup --conventions"


def _assets_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def asset_bytes() -> bytes:
    with open(os.path.join(_assets_dir(), NAME), "rb") as fh:
        return fh.read()


def stamp_of(data: bytes) -> Optional[int]:
    """The version a text's first line declares, or None when it carries no stamp."""
    first = data.split(b"\n", 1)[0].decode("utf-8", "replace").rstrip("\r")
    m = _STAMP_RE.match(first)
    return int(m.group(1)) if m else None


def history() -> dict:
    with open(os.path.join(_assets_dir(), "CONVENTIONS.history.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    return {"versions": {int(k): v for k, v in doc["versions"].items()}, "lines": set(doc["lines"])}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _line_key(line: str) -> str:
    return hashlib.sha256(line.rstrip().encode("utf-8")).hexdigest()[:12]


def own_lines(text: str, asset_text: str, shipped_line_keys) -> List[str]:
    """The copy's lines that are in no shipped version, in order (blank lines never count)."""
    shipped = set(shipped_line_keys) | {_line_key(ln) for ln in asset_text.splitlines()}
    return [ln.rstrip() for ln in text.splitlines() if ln.strip() and _line_key(ln) not in shipped]


def _eff(memory_dir: str, overlay: Optional[str], name: str) -> str:
    """``name`` in the outbox when a copy is there, else in the corpus dir."""
    if overlay:
        p = os.path.join(overlay, name)
        if os.path.isfile(p):
            return p
    return os.path.join(memory_dir, name)


def destination(memory_dir: str, repo_root: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Where ``--yes`` writes: ``{"mode", "dir", "overlay"}``.

    ``outbox``: the corpus asks for the outbox and this machine honours it; ``dir`` is the
    corpus dir (read only) and ``overlay`` the outbox, which takes every write. ``worktree``:
    a direct write from a linked worktree whose corpus resolved to the main tree; ``dir`` is
    the worktree's own corpus dir, so the main tree's tracked file is never written from a
    branch that is not its own. ``direct``: ``dir`` is ``memory_dir``. A pinned
    ``HIPPO_MEMORY_DIR`` / ``HIPPO_CORPUS_ROOT`` is used as it is. Never raises."""
    out: Dict[str, Optional[str]] = {"mode": "direct", "dir": memory_dir, "overlay": None}
    try:
        from . import outbox as OB

        if OB.write_target(memory_dir, repo_root) == "outbox":
            out.update(mode="outbox", overlay=OB.outbox_dir(memory_dir))
            return out
        from .provenance_env import resolve_corpus_start

        info = resolve_corpus_start()
        if info.get("tree") == "main-tree" and info.get("main_tree") and info.get("linked_worktree"):
            rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(info["main_tree"]))
            cand = os.path.join(info["linked_worktree"], rel)
            if not rel.startswith("..") and os.path.isdir(cand):
                out.update(mode="worktree", dir=cand)
    except Exception:
        pass
    return out


def unrecorded(memory_dir: str, overlay: Optional[str], repo_root: Optional[str] = None) -> List[str]:
    """The conventions files in the outbox that hippo did not record writing on this machine
    and that are not a plain copy of the committed file (a copy-up torn before its write).
    They are never read as the corpus's copy and never written over."""
    out: List[str] = []
    if not overlay:
        return out
    try:
        from . import outbox as OB

        allowed = OB.outbox_hashes(memory_dir, repo_root)
        if allowed is None:
            return out
        for name in (NAME, LOCAL_NAME):
            p = os.path.join(overlay, name)
            if not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                sha = _sha(fh.read())
            if allowed.get(os.path.splitext(name)[0]) == sha:
                continue
            committed = os.path.join(memory_dir, name)
            if os.path.isfile(committed):
                with open(committed, "rb") as fh:
                    if _sha(fh.read()) == sha:
                        continue
            out.append(name)
    except Exception:
        pass
    return out


def classify(memory_dir: str, overlay: Optional[str] = None) -> Dict[str, object]:
    """``{"state", "version", "current", "own": [lines], "local": bool, "in_outbox": bool}``.
    With ``overlay`` (the outbox) a copy there is the one classified. Never raises: an
    unreadable copy or asset reads as ``edited``/``unknown`` with an ``error``."""
    out: Dict[str, object] = {"state": "missing", "version": None, "current": None, "own": [],
                              "local": os.path.isfile(_eff(memory_dir, overlay, LOCAL_NAME)),
                              "in_outbox": False}
    try:
        asset = asset_bytes()
        hist = history()
        out["current"] = stamp_of(asset)
        path = _eff(memory_dir, overlay, NAME)
        out["in_outbox"] = bool(overlay) and os.path.dirname(path) == overlay
        if not os.path.isfile(path):
            return out
        with open(path, "rb") as fh:
            data = fh.read()
        h = _sha(data)
        if h == _sha(asset):
            out.update(state="current", version=out["current"])
            return out
        for v, vh in hist["versions"].items():
            if vh == h:
                out.update(state="behind", version=v)
                return out
        out["state"] = "edited"
        out["version"] = stamp_of(data)
        out["own"] = own_lines(data.decode("utf-8", "replace"), asset.decode("utf-8"), hist["lines"])
        return out
    except Exception as exc:
        out.update(state="unknown", error=f"{type(exc).__name__}: {exc}")
        return out


def doctor_line(memory_dir: str) -> Dict[str, str]:
    overlay = None
    try:
        from . import outbox as OB

        overlay = OB.present(memory_dir)
        if overlay and unrecorded(memory_dir, overlay):
            overlay = None
    except Exception:
        overlay = None
    c = classify(memory_dir, overlay)
    cur, state = c.get("current"), c["state"]
    local = f" Local rules: {LOCAL_NAME}." if c.get("local") else ""
    if c.get("in_outbox"):
        local += " This copy is in the outbox; `hippo ship` carries it into a branch."
    if state == "current":
        return {"status": "ok", "message": f"conventions: {NAME} is the plugin's version {cur}.{local}"}
    if state == "missing":
        return {"status": "ok", "message": f"conventions: no {NAME} in this corpus (optional; "
                f"`{_CMD}` previews adding the plugin's version {cur}).{local}"}
    if state == "behind":
        return {"status": "warn", "message": f"conventions: {NAME} is version {c['version']}, behind "
                f"the plugin's {cur}. `{_CMD}` previews the refresh.{local}"}
    if state == "edited":
        n = len(c["own"])
        moved = (f"moving its {n} line(s) of its own into {LOCAL_NAME} and " if n else "")
        return {"status": "warn", "message": f"conventions: {NAME} is edited ({n} line(s) in no "
                f"shipped version). `{_CMD}` previews {moved}replacing it with the plugin's "
                f"version {cur}.{local}"}
    return {"status": "warn", "message": f"conventions: could not read {NAME} ({c.get('error')})."}


def check_conventions(ctx) -> Dict[str, str]:
    """The doctor line (CHECKS ``conventions``)."""
    return doctor_line(ctx.memory_dir)


def _diff(old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{NAME} (this corpus)", tofile=f"{NAME} (plugin version)", n=1,
    ))


# The one line hippo itself writes into the local file (``_local_block``'s header), exactly.
_MOVED_RE = re.compile(
    r"^<!-- moved from CONVENTIONS\.md by `hippo setup --conventions --yes` on \d{4}-\d{2}-\d{2}: "
    r"the lines that were in no shipped version -->$"
)


def without_own_markers(text: str) -> str:
    """``text`` minus hippo's own "moved from" header lines: what a lint of the corpus's
    lines in the local file should read (an HTML comment is a threat-lint class)."""
    return "".join(ln for ln in text.splitlines(keepends=True) if not _MOVED_RE.match(ln.rstrip("\r\n")))


def _local_block(own: List[str], when: Optional[str] = None) -> str:
    when = when or time.strftime("%Y-%m-%d")
    return (f"<!-- moved from {NAME} by `{_CMD} --yes` on {when}: the lines that were in no "
            f"shipped version -->\n" + "".join(f"{ln}\n" for ln in own))


def plan(memory_dir: str, overlay: Optional[str] = None) -> Dict[str, object]:
    """What ``--yes`` would do: ``{"state", "classification", "diff", "move": [lines],
    "write_local": bool, "replace": bool, "seed": bool}``."""
    c = classify(memory_dir, overlay)
    p: Dict[str, object] = {"state": c["state"], "classification": c, "diff": "", "move": [],
                            "write_local": False, "replace": False, "seed": False}
    if c["state"] == "missing":
        p["seed"] = True
    elif c["state"] in ("behind", "edited"):
        with open(_eff(memory_dir, overlay, NAME), encoding="utf-8", errors="replace") as fh:
            old = fh.read()
        p["diff"] = _diff(old, asset_bytes().decode("utf-8"))
        p["replace"] = True
        own = list(c["own"])
        if own:
            have = set()
            lp = _eff(memory_dir, overlay, LOCAL_NAME)
            if os.path.isfile(lp):
                with open(lp, encoding="utf-8", errors="replace") as fh:
                    have = {ln.rstrip() for ln in fh.read().splitlines()}
            p["move"] = own
            # a re-run after a torn replace: the lines are already there, never twice
            p["write_local"] = not set(own) <= have
    return p


def _append_local(memory_dir: str, own: List[str]) -> None:
    """Append-only, compare-and-swap: the local file's existing bytes are kept as they are."""
    from .atomic import read_text_cas, write_text_cas

    lp = os.path.join(memory_dir, LOCAL_NAME)
    text, token = read_text_cas(lp) if os.path.isfile(lp) else ("", None)
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    write_text_cas(lp, text + sep + _local_block(own), token)


def _write_copy(path: str, token: Optional[str]) -> None:
    """The asset over the copy whose bytes had ``token`` (None: only while still absent)."""
    from .atomic import write_text_cas

    write_text_cas(path, asset_bytes().decode("utf-8"), token)


def _apply_outbox(memory_dir: str, overlay: str, repo_root: Optional[str]) -> Dict[str, object]:
    """``apply`` for a corpus that saves to the outbox: every write lands in ``overlay``.

    The committed files are copied up first (which records the bytes each copy was made
    from, what ``hippo ship`` classifies against), then the same two compare-and-swap writes
    run on the copies, and each written file gets its manifest row and hash record."""
    from . import outbox as OB
    from .atomic import content_token

    did: List[str] = []
    try:
        bad = unrecorded(memory_dir, overlay, repo_root)
        if bad:
            return {"ok": False, "did": did, "error": "the outbox holds " + " and ".join(bad)
                    + " that hippo did not write on this machine; remove it from "
                    + overlay + " first"}
        first = plan(memory_dir, overlay)
        if not (first["write_local"] or first["replace"] or first["seed"]):
            return {"ok": True, "did": did, "error": None}
        OB.ensure(memory_dir)
        OB.copy_up(memory_dir, NAME, repo_root)
        path = os.path.join(overlay, NAME)
        token = content_token(path)  # taken BEFORE the plan reads it: any later edit is refused
        p = plan(memory_dir, overlay)
        warnings: List[Optional[str]] = []
        if p["write_local"]:
            OB.copy_up(memory_dir, LOCAL_NAME, repo_root)
            _append_local(overlay, p["move"])
            warnings.append(OB.record_write(memory_dir, os.path.join(overlay, LOCAL_NAME), repo_root))
            did.append(f"moved {len(p['move'])} line(s) into {LOCAL_NAME}")
        if p["replace"] or p["seed"]:
            _write_copy(path, token)
            warnings.append(OB.record_write(memory_dir, path, repo_root))
            did.append(("seeded " if p["seed"] else "replaced ") + f"{NAME} with the plugin's version "
                       f"{p['classification']['current']}")
        did += [w for w in warnings if w]
        return {"ok": True, "did": did, "error": None}
    except Exception as exc:
        return {"ok": False, "did": did, "error": f"{type(exc).__name__}: {exc}"}


def apply(memory_dir: str, overlay: Optional[str] = None, repo_root: Optional[str] = None) -> Dict[str, object]:
    """Carry out ``plan``: append the corpus's own lines to the local file, THEN replace (or
    seed) the copy. Each write is compare-and-swap against the bytes just read, so a file
    that changed meanwhile is refused, never overwritten. ``{"ok", "did": [...], "error"}``.
    With ``overlay`` (the outbox) the writes land there (``_apply_outbox``)."""
    from .atomic import content_token

    if overlay:
        return _apply_outbox(memory_dir, overlay, repo_root)
    path = os.path.join(memory_dir, NAME)
    token = content_token(path)  # taken BEFORE the plan reads it: any later edit is refused
    p = plan(memory_dir)
    did: List[str] = []
    try:
        if p["write_local"]:
            _append_local(memory_dir, p["move"])
            did.append(f"moved {len(p['move'])} line(s) into {LOCAL_NAME}")
        if p["replace"] or p["seed"]:
            _write_copy(path, token)
            did.append(("seeded " if p["seed"] else "replaced ") + f"{NAME} with the plugin's version "
                       f"{p['classification']['current']}")
        return {"ok": True, "did": did, "error": None}
    except Exception as exc:
        return {"ok": False, "did": did, "error": f"{type(exc).__name__}: {exc}"}


def run(args) -> int:
    """``hippo setup --conventions [--yes]``: preview by default."""
    from .provenance import resolve_dirs

    resolved, repo = resolve_dirs()
    flag = _CMD
    if not os.path.isdir(resolved):
        print(f"{flag}: no corpus at {resolved}. Nothing was written.")
        return 1
    dest = destination(resolved, repo)
    memory_dir, overlay = dest["dir"], dest["overlay"]
    where = memory_dir
    if dest["mode"] == "outbox":
        where = f"the outbox ({overlay}), not the corpus dir; `hippo ship` carries it into a branch"
    elif dest["mode"] == "worktree":
        where = f"{memory_dir} (this linked worktree's corpus; the main tree's copy is not touched)"
    if getattr(args, "off", False):
        print(f"{flag}: --off does not apply; the copy is optional, delete it by hand if you want none.")
        return 2
    if getattr(args, "status", False):
        print(doctor_line(resolved)["message"])
        return 0
    bad = unrecorded(memory_dir, overlay, repo)
    if bad:
        print(f"{flag}: the outbox holds {' and '.join(bad)} that hippo did not write on this "
              f"machine. Remove it from {overlay} first. Nothing was written.")
        return 1
    p = plan(memory_dir, overlay)
    cur = p["classification"].get("current")
    if p["state"] == "current":
        print(f"{flag}: {NAME} is already the plugin's version {cur}. Nothing to do.")
        return 0
    if p["state"] == "unknown":
        print(f"{flag}: could not read {NAME} ({p['classification'].get('error')}). Nothing was written.")
        return 1
    if not args.yes:
        if p["seed"]:
            print(f"`{flag} --yes` would add the plugin's {NAME} (version {cur}) to {where}.")
        else:
            what = (f"version {p['classification']['version']}" if p["state"] == "behind"
                    else "edited")
            src = os.path.dirname(_eff(memory_dir, overlay, NAME))
            print(f"{NAME} in {src} is {what}; the plugin's is version {cur}. The change:\n")
            print(p["diff"].rstrip("\n"))
            if p["move"]:
                n = len(p["move"])
                if p["write_local"]:
                    print(f"\nThese {n} line(s) are this corpus's own (in no shipped version). "
                          f"`{flag} --yes` would first append them to {LOCAL_NAME}:")
                else:
                    print(f"\nThese {n} line(s) are this corpus's own (in no shipped version) and "
                          f"are already in {LOCAL_NAME}, so they are not added again:")
                for ln in p["move"]:
                    print(f"  {ln}")
                print(f"Then it would replace {NAME} with the plugin's version.")
            else:
                print(f"\n`{flag} --yes` would replace {NAME} with the plugin's version; no line "
                      "of it is this corpus's own (every line is in a shipped version).")
        if dest["mode"] != "direct" and not p["seed"]:
            print(f"\n`{flag} --yes` writes {where}.")
        print("\nNothing was written.")
        return 0
    r = apply(memory_dir, overlay, repo)
    if not r["ok"]:
        done = ("; ".join(r["did"]) + "; ") if r["did"] else ""
        print(f"{flag}: {done}NOT finished ({r['error']}). Re-run to preview what is left.")
        return 1
    print(f"{flag}: " + "; ".join(r["did"]) + ".")
    if dest["mode"] == "outbox":
        print(f"Written to the outbox ({overlay}), not the corpus dir: the working tree is "
              "untouched, and `hippo ship` carries it into a branch.")
    elif dest["mode"] == "worktree":
        print(f"Written in {memory_dir}: this linked worktree's corpus. The main tree's copy is not touched.")
    return 0
