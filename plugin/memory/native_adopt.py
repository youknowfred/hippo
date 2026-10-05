"""CLM-4: adopt Claude Code's native memory directory into this repo's hippo corpus.

init links ``~/.claude/projects/<encoded>/memory`` to the repo's ``.claude/memory`` so the
harness loads hippo's floor. When native auto memory has already been writing there, that
slot is a REAL directory, and ``create_project_symlink`` reports a conflict and stops. The
only way through was by hand: copy the directory into the repo, move the original aside,
link, index, review trust, and stamp the corpus format once nothing in the adopted files
uses hippo's keys for something else.

This module is that path as a two-step, shared by ``hippo adopt`` (the init skill) and the
MCP ``init`` tool:

- **preview** (``plan_adoption``) writes nothing. It lists the files, every per-file
  collision (a corpus file of the same name with different bytes), every frontmatter key
  collision, the exact actions, and a digest over all of it.
- **confirm** (``execute_adoption``) requires that digest and refuses on any collision. It
  copies the files byte for byte, renames the native directory to a dated backup, creates
  the link, builds the index, and stamps the corpus format only if no adopted memory uses
  a hippo key for something else. Nothing adopted is trusted: the files go through the
  normal consent review (``hippo trust review``).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
from typing import Dict, List, Optional

DIGEST_CHARS = 12
_BACKUP_TAG = "pre-hippo"

# The frontmatter keys hippo gives a meaning to, and the shape each must have. An adopted
# memory that uses one for something else would be misread the moment the corpus is
# stamped with the format that defines it (formats 2-5 are additive key conventions).
_RELATION_KEYS = ("supersedes", "contradicts", "refines", "derives-from")
_LIST_KEYS = ("cited_paths", "cited_paths_exclude")
_TEXT_KEYS = ("source_commit", "source_commit_time", "verified_by")
_DATE_KEYS = ("invalid_after", "last_verified")
_CONFIDENCE = ("draft", "verified", "authoritative")


def native_slot(repo_root: str, claude_projects_dir: Optional[str] = None) -> str:
    """``<claude projects dir>/<encoded repo root>/memory`` — the path the harness reads."""
    from .machine_census import claude_projects_root
    from .provenance_env import encode_project_dir

    base = claude_projects_dir or claude_projects_root()
    return os.path.join(base, encode_project_dir(repo_root), "memory")


def adoption_applies(repo_root: str, claude_projects_dir: Optional[str] = None) -> bool:
    """True when the native slot is a real directory (not hippo's link, not absent)."""
    slot = native_slot(repo_root, claude_projects_dir)
    return os.path.isdir(slot) and not os.path.islink(slot)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_files(slot: str) -> List[str]:
    """Every regular file under ``slot`` (relative, sorted). Symlinks are never followed."""
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(slot, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append(os.path.relpath(full, slot))
    return out


def _bad_shape(key: str, value) -> Optional[str]:
    if value is None:
        return None
    if key in _RELATION_KEYS or key in _LIST_KEYS:
        items = [value] if isinstance(value, str) and key in _RELATION_KEYS else value
        if isinstance(items, list) and all(isinstance(i, str) for i in items):
            return None
        return "a list of names" if key in _RELATION_KEYS else "a list of paths"
    if key == "steer":
        return None if value == "pin" else "'pin'"
    if key == "confidence":
        return None if value in _CONFIDENCE else "one of draft/verified/authoritative"
    if key in _TEXT_KEYS:
        return None if isinstance(value, (str, int)) else "a string"
    if key in _DATE_KEYS:
        return None if isinstance(value, (str, _dt.date)) else "a date"
    return None


def key_collisions(texts: Dict[str, str]) -> List[str]:
    """``"<file>: <key> holds <type>, but hippo reads it as <shape>"`` for every adopted
    memory that uses one of hippo's frontmatter keys for something else."""
    from .provenance import parse_frontmatter

    keys = _RELATION_KEYS + _LIST_KEYS + _TEXT_KEYS + _DATE_KEYS + ("steer", "confidence")
    out: List[str] = []
    for rel in sorted(texts):
        fm = parse_frontmatter(texts[rel]) or {}
        scopes = [("", fm)]
        if isinstance(fm.get("metadata"), dict):
            scopes.append(("metadata.", fm["metadata"]))
        for prefix, scope in scopes:
            for key in keys:
                if key not in scope:
                    continue
                want = _bad_shape(key, scope[key])
                if want:
                    out.append(
                        f"{rel}: {prefix}{key} holds {type(scope[key]).__name__} "
                        f"{scope[key]!r:.40}, but hippo reads it as {want}"
                    )
    return out


def _backup_path(slot: str, today: Optional[str] = None) -> str:
    stamp = today or _dt.date.today().strftime("%Y%m%d")
    base = f"{slot}.{_BACKUP_TAG}-{stamp}"
    cand, n = base, 2
    while os.path.lexists(cand):
        cand, n = f"{base}-{n}", n + 1
    return cand


def plan_adoption(
    repo_root: str, memory_dir: str, claude_projects_dir: Optional[str] = None,
    today: Optional[str] = None,
) -> dict:
    """The preview. Writes nothing; never raises (an unreadable source is a reported error).

    ``applies`` False means there is nothing to adopt (no native directory, or the slot is
    already a link). Otherwise: ``files`` (relative paths), ``copy`` (to write),
    ``identical`` (already in the corpus byte for byte), ``collisions`` (same name,
    different bytes: adoption refuses), ``key_collisions``, ``backup``, ``actions`` and
    ``digest``.
    """
    from .provenance_format import CORPUS_FORMAT_VERSION, format_marker_path

    slot = native_slot(repo_root, claude_projects_dir)
    plan: dict = {"applies": False, "slot": slot, "memory_dir": memory_dir, "error": None}
    if not (os.path.isdir(slot) and not os.path.islink(slot)):
        return plan
    plan["applies"] = True
    try:
        rels = _source_files(slot)
        copy, identical, collisions, texts = [], [], [], {}
        rows = []
        for rel in rels:
            with open(os.path.join(slot, rel), "rb") as fh:
                data = fh.read()
            sha = _sha(data)
            dest = os.path.join(memory_dir, rel)
            if os.path.lexists(dest):
                try:
                    with open(dest, "rb") as fh:
                        same = _sha(fh.read()) == sha
                except OSError:
                    same = False
                (identical if same else collisions).append(rel)
                state = "identical" if same else "collision"
            else:
                copy.append(rel)
                state = "copy"
            rows.append(f"{rel}\t{sha}\t{state}")
            if rel.endswith(".md") and os.sep not in rel and rel != "MEMORY.md":
                texts[rel] = data.decode("utf-8", "replace")
        backup = _backup_path(slot, today)
        kc = key_collisions(texts)
        stamp = not kc and not os.path.exists(format_marker_path(memory_dir))
        actions = [
            f"copy {len(copy)} file(s) from {slot} into {memory_dir}, byte for byte"
            + (f" ({len(identical)} already there, identical)" if identical else ""),
            f"rename {slot} to {backup} (your backup; nothing is deleted)",
            f"link {slot} to {memory_dir}",
            "build the recall index",
            "trust nothing adopted: review it with `hippo trust review`, then grant what you approve",
            f"stamp corpus format {CORPUS_FORMAT_VERSION}" if stamp else (
                "leave the corpus format unstamped: resolve the key collisions first"
                if kc else "leave the corpus format marker as it is"
            ),
        ]
        plan.update(files=rels, copy=copy, identical=identical, collisions=collisions,
                    key_collisions=kc, backup=backup, stamp_format=stamp, actions=actions)
        h = "\n".join([os.path.realpath(slot), os.path.realpath(memory_dir), backup] + rows + kc)
        plan["digest"] = hashlib.sha256(h.encode("utf-8")).hexdigest()[:DIGEST_CHARS]
        return plan
    except Exception as exc:
        plan["error"] = f"could not read the native directory: {exc}"
        return plan


def execute_adoption(
    repo_root: str, memory_dir: str, digest: str, claude_projects_dir: Optional[str] = None,
    today: Optional[str] = None,
) -> dict:
    """The confirm step. Re-plans, requires the preview's digest, refuses on collisions,
    then runs the five actions. A failure before the link exists rolls back what it wrote
    (copied files removed, the native directory renamed back). Never raises."""
    from .init_project import _copy_if_absent
    from .provenance_env import create_project_symlink
    from .provenance_format import write_corpus_format

    plan = plan_adoption(repo_root, memory_dir, claude_projects_dir, today)
    res: dict = {"ok": False, "error": None, "plan": plan, "copied": [], "backup": None,
                 "symlink": None, "index": None, "format": None}
    if not plan["applies"]:
        res["error"] = f"nothing to adopt: {plan['slot']} is not a directory of its own"
        return res
    if plan.get("error"):
        res["error"] = plan["error"]
        return res
    if (digest or "").strip() != plan["digest"]:
        res["error"] = (
            "the digest does not match the native directory or the corpus as they are now; "
            "nothing was changed. Preview again"
        )
        return res
    if plan["collisions"]:
        res["error"] = (
            f"{len(plan['collisions'])} file(s) already exist in {memory_dir} with different "
            f"content ({', '.join(plan['collisions'])}); resolve them by hand, then preview "
            "again. Nothing was changed"
        )
        return res
    slot, backup = plan["slot"], plan["backup"]
    copied: List[str] = []

    def _undo_copies() -> None:
        for p in reversed(copied):
            try:
                os.remove(p)
            except OSError:
                pass

    try:
        for rel in plan["copy"]:
            dest = os.path.join(memory_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            # init's own seed copy: byte-faithful, atomic, never over an existing file.
            status = _copy_if_absent(os.path.join(slot, rel), dest)
            if status != "seeded":
                raise OSError(f"{rel}: {status or 'source vanished'} since the preview")
            copied.append(dest)
    except Exception as exc:
        _undo_copies()
        res["error"] = f"copy failed ({exc}); the copied files were removed, nothing else changed"
        return res
    try:
        os.rename(slot, backup)
    except Exception as exc:
        _undo_copies()
        res["error"] = f"could not move the native directory aside ({exc}); the copies were removed"
        return res
    link = create_project_symlink(repo_root, memory_dir, os.path.dirname(os.path.dirname(slot)))
    res["symlink"] = link
    if link.get("status") not in ("created", "already_correct"):
        try:
            os.rename(backup, slot)
            _undo_copies()
            res["error"] = f"the link failed ({link.get('error')}); everything was put back"
        except Exception as exc:
            res["error"] = (
                f"the link failed ({link.get('error')}) and the rollback failed too ({exc}): "
                f"the native directory is at {backup}"
            )
        return res
    res["copied"], res["backup"] = [os.path.relpath(p, memory_dir) for p in copied], backup
    res["ok"] = True
    try:
        from .build_index import build_index, default_index_dir

        m = build_index(memory_dir, default_index_dir(memory_dir), allow_download=False)
        res["index"] = {"count": m.get("count"), "dense_ready": bool(m.get("dense_ready"))}
    except Exception as exc:
        res["index"] = {"error": str(exc)}
    if plan["key_collisions"]:
        res["format"] = "not_stamped_key_collisions"
    elif plan["stamp_format"]:
        res["format"] = "stamped" if write_corpus_format(memory_dir) else "stamp_failed"
    else:
        res["format"] = "marker_present"
    try:
        from .registry import register_project

        register_project(repo_root, memory_dir)
    except Exception:
        pass
    return res


def render_plan(plan: dict, *, confirm_hint: str) -> str:
    """The preview as text. ``confirm_hint`` names the surface's confirm call."""
    if not plan.get("applies"):
        return f"adopt: nothing to adopt — {plan.get('slot')} is not a directory of its own."
    if plan.get("error"):
        return f"adopt: {plan['error']}."
    lines = [
        f"adopt preview — nothing has been written. Claude Code's native memory for this repo "
        f"is a directory of its own ({plan['slot']}, {len(plan['files'])} file(s)), so the "
        "link init makes cannot be created until it is adopted.",
        f"into: {plan['memory_dir']}",
    ]
    if plan["collisions"]:
        lines.append(
            f"collisions: {len(plan['collisions'])} file(s) exist in the corpus with different "
            f"content — adoption is refused until you resolve them: {', '.join(plan['collisions'])}"
        )
    else:
        lines.append("collisions: none")
    if plan["key_collisions"]:
        lines.append("frontmatter keys hippo reads differently (the format will not be stamped):")
        lines += [f"  - {k}" for k in plan["key_collisions"]]
    lines.append("on confirm:")
    lines += [f"  {i}. {a}" for i, a in enumerate(plan["actions"], 1)]
    lines.append(f"digest: {plan['digest']}")
    if not plan["collisions"]:
        lines.append(confirm_hint.format(digest=plan["digest"]))
    return "\n".join(lines)


_CLI_NEXT_STEP = (
    "Nothing adopted is trusted yet. Next step: `hippo trust review`, then grant what you "
    "approve."
)


def render_result(res: dict, next_step: str = _CLI_NEXT_STEP) -> str:
    if not res.get("ok"):
        return f"adopt: refused — {res.get('error')}."
    idx = res.get("index") or {}
    fmt = {
        "stamped": "corpus format stamped",
        "not_stamped_key_collisions": "corpus format NOT stamped (key collisions listed in the preview)",
        "stamp_failed": "corpus format stamp FAILED to write",
        "marker_present": "corpus format marker left as it was",
    }.get(res.get("format"), "")
    return "\n".join([
        f"✔ adopted {len(res['copied'])} file(s); the native directory is now {res['backup']}",
        f"✔ linked {(res.get('symlink') or {}).get('expected_path')}",
        f"✔ index built ({idx.get('count')} memories)" if not idx.get("error")
        else f"⚠ index build failed: {idx['error']}",
        f"✔ {fmt}" if res.get("format") == "stamped" else f"⚠ {fmt}",
        next_step,
    ])


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs
    from .provenance_env import foreign_corpus_owner

    parser = argparse.ArgumentParser(
        prog="hippo adopt",
        description="Adopt Claude Code's native memory directory for this repo into its "
        "hippo corpus. Without --confirm it only previews.",
    )
    parser.add_argument("--confirm", default=None, metavar="DIGEST",
                        help="run the adoption the preview described (its digest)")
    parser.add_argument("--memory-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--repo-root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    md, rr = resolve_dirs()
    repo_root = args.repo_root or rr
    memory_dir = args.memory_dir or md
    if not args.memory_dir and foreign_corpus_owner(memory_dir, repo_root):
        memory_dir = os.path.join(repo_root, ".claude", "memory")  # this repo's own corpus
    if args.confirm is None:
        plan = plan_adoption(repo_root, memory_dir)
        print(render_plan(plan, confirm_hint="To adopt: hippo adopt --confirm {digest}"))
        return 0
    res = execute_adoption(repo_root, memory_dir, args.confirm)
    print(render_result(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
