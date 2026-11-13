"""OUT-2: ``hippo ship`` — the outbox, batched into one commit on one new branch, and nothing else.

A corpus that saves to the outbox (OUT-1) keeps its new and edited memories out of the working
tree. This module lands them as ONE reviewed batch: it classifies every file in the outbox
against the local default-branch ref, and with ``--yes`` writes one commit on one new branch
off that ref, with git plumbing against a temporary index. It never fetches, never pushes,
never touches HEAD, the user's index or the working tree, never overwrites a ref, and never
carries a deletion: the commit's tree is the base tree plus adds and modifications under the
corpus dir. Pushing and opening a pull request stay with the user.

Classification, per outbox memory file, in precedence order (``base_sha256`` in the manifest is
the contract: the sha256 of the committed bytes the copy was made from, None for a new memory):

- **same**: the outbox bytes equal the bytes at the base ref. It has landed. Once this
  checkout's corpus dir holds the same bytes too, a ``--yes`` run drops the copy (file,
  manifest row, hash record) and, when the bytes are exactly the ones this machine recorded
  writing, folds them into the corpus's consent baseline the way a direct hippo write is
  folded (``trust.record_authored_write``), so the landed file is not withheld the moment its
  copy goes. Until the checkout pulls, it is reported as landed and kept.
- **waiting**: shipped already (the manifest's ``shipped`` stamp, or a ``hippo/ship-*`` branch
  whose tree holds these bytes, which is how a crash between the ref and the stamp is read),
  unchanged since, and that branch still exists. Not shipped again.
- **stale**: the committed copy moved since the copy was made (or appeared under a new
  memory's name, or was deleted). Shown as a bounded diff; never overwritten, never shipped.
  ``--drop`` and ``--mine`` are the two ways out, each per item.
- **held**: a credential shape, a threat-lint class, or bytes this machine withholds (hippo
  did not record writing them: shipping would launder a dropped-in or hand-edited file). The
  file and the CLASS are named, never the matching text. Only bytes that would ship are
  scanned.
- **new**: everything else. These ship.

The floor ships as a replay, never as a file: the pointer adds and removes hippo made in the
outbox copy of ``MEMORY.md`` (against the base text recorded beside it at copy-up) are
replayed onto the floor AT THE BASE REF with a direct save's placement rules. A floor copy
with no recorded base, or with any change that is not a pointer add or remove, is stale. The
commit gate's judgment (``gate_cli.judge_sides``) runs on the result; a finding refuses
``--yes``. Because no ``git commit`` runs in a working tree, the repository's own git hooks
do not run for this commit; the pull request's checks run everything else.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import outbox as OB

BRANCH_PREFIX = "hippo/ship-"
_DIFF_MAX_LINES = 40
HOOKS_LINE = (
    "the repository's git hooks do not run for this commit (no `git commit` runs in a working "
    "tree); hippo's own gate judgment ran instead, and the pull request's checks run the rest"
)
FRESHNESS_LINE = "hippo never fetches: the base is as fresh as the last `git fetch` you ran"

# Credential shapes beyond secrets.py's table that the estate lane also refuses. Kinds only.
_EXTRA_SHAPES = [
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"), "possible mail-provider API key detected"),
    (re.compile(r"\bkey-[0-9a-f]{32}\b"), "possible mail-provider API key detected"),
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}=*"), "possible bearer token detected"),
]
_URL_PASSWORD = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s/@]+)@")


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #
def _git(top: str, *args: str, data: Optional[bytes] = None, env: Optional[dict] = None) -> Tuple[int, bytes]:
    try:
        p = subprocess.run(["git", "-C", top, *args], input=data, capture_output=True, timeout=120,
                           env={**os.environ, **(env or {})})
        return p.returncode, p.stdout
    except Exception:
        return 1, b""


def _out(top: str, *args: str, env: Optional[dict] = None) -> Optional[str]:
    rc, out = _git(top, *args, env=env)
    return out.decode("utf-8", "replace").strip() if rc == 0 else None


def corpus_location(memory_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """``(toplevel, corpus path relative to it)``, or ``(None, None)`` outside git."""
    from .gate_cli import _corpus

    return _corpus(memory_dir)


def resolve_base(top: str, base: Optional[str] = None) -> Optional[dict]:
    """``{"ref", "sha", "short", "date"}`` of the base: ``base`` when given, else the local
    remote-tracking ref of the default branch (``refs/remotes/origin/HEAD``, then
    ``origin/main``, ``origin/master``), else the local ``main``/``master``. None when none
    resolves (``init.defaultBranch`` names the local default when it is neither). Never
    fetches."""
    cands = [base] if base else []
    if not base:
        head = _out(top, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        if head:
            cands.append(head)
        cands += ["refs/remotes/origin/main", "refs/remotes/origin/master", "refs/heads/main", "refs/heads/master"]
        configured = _out(top, "config", "--get", "init.defaultBranch")
        if configured:
            cands.append(f"refs/heads/{configured}")
    for ref in cands:
        sha = _out(top, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
        if sha:
            name = _out(top, "rev-parse", "--abbrev-ref", ref) or ref
            date = _out(top, "show", "-s", "--format=%cI", sha) or ""
            return {"ref": name, "sha": sha, "short": sha[:9], "date": date}
    return None


def _tree_blobs(top: str, rev: str, rel: str) -> Dict[str, str]:
    from .gate_cli import _list

    return _list(top, rel, rev)


def _blob_bytes(top: str, sha: Optional[str]) -> Optional[bytes]:
    if not sha:
        return None
    rc, out = _git(top, "cat-file", "blob", sha)
    return out if rc == 0 else None


def _sha256(data: Optional[bytes]) -> Optional[str]:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _blob_id(top: str, data: bytes, env: Optional[dict] = None, write: bool = False) -> Optional[str]:
    args = ["hash-object", "--stdin"] + (["-w"] if write else [])
    rc, out = _git(top, *args, data=data, env=env)
    return out.decode().strip() if rc == 0 else None


def _ship_refs(top: str, rel: str) -> Dict[str, Dict[str, str]]:
    """``{branch: {file name: blob}}`` for every ``hippo/ship-*`` branch: how a ship whose
    manifest stamp never landed (a crash after the ref) is still read as in flight."""
    out = _out(top, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{BRANCH_PREFIX}*") or ""
    return {b: _tree_blobs(top, f"refs/heads/{b}", rel) for b in out.splitlines() if b.strip()}


def _ref_exists(top: str, branch: str) -> bool:
    return _out(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}") is not None


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #
def scan_classes(text: str) -> List[str]:
    """Credential-shape and threat classes for ``text`` (kinds, never the match)."""
    from .secrets import _is_placeholder, scan_text
    from .threat_lint import scan_tier_a

    found = list(scan_text(text, entropy=False))
    for pat, kind in _EXTRA_SHAPES:
        if kind not in found and pat.search(text):
            found.append(kind)
    kind = "possible URL with an inline password detected"
    if kind not in found and not any("connection string" in f for f in found):
        for m in _URL_PASSWORD.finditer(text):
            if not _is_placeholder(m.group(1)) and not m.group(1).startswith("$"):
                found.append(kind)
                break
    found += [f"threat: {k}" for k in scan_tier_a(text)]
    return found


def _bounded_diff(old: Optional[bytes], new: bytes, a: str, b: str) -> str:
    o = (old or b"").decode("utf-8", "replace").splitlines(keepends=True)
    n = new.decode("utf-8", "replace").splitlines(keepends=True)
    lines = list(difflib.unified_diff(o, n, fromfile=a if old is not None else "/dev/null", tofile=b, n=2))
    lines = [ln if ln.endswith("\n") else ln + "\n" for ln in lines]
    if len(lines) > _DIFF_MAX_LINES:
        lines = lines[:_DIFF_MAX_LINES] + [f"... ({len(lines) - _DIFF_MAX_LINES} more diff lines)\n"]
    return "".join(lines)


# --------------------------------------------------------------------------- #
# the floor replay
# --------------------------------------------------------------------------- #
_POINTER_RE = re.compile(r"^- \[(?P<title>[^\]]*)\]\((?P<name>[^)/]+)\.md\)(?: — (?P<hook>.*))?$")


def _section_of(lines: List[str], i: int) -> Optional[str]:
    for j in range(i, -1, -1):
        if lines[j].strip().startswith("## "):
            return lines[j].strip()
    return None


def floor_changes(base_text: str, copy_text: str, is_memory=None) -> Optional[Tuple[List[dict], List[str]]]:
    """``(adds, removes)`` hippo made in the floor copy against the text it was copied
    from, or None when anything else changed (a hand edit, a moved line). With ``is_memory``
    a removed bare pointer line (FLR-1: a floor diet's demote on a bare floor) is a remove."""
    from .lint_floor import bare_pointer
    from .new_memory_floor import _pointer_name

    a, b = base_text.split("\n"), copy_text.split("\n")
    adds: List[dict] = []
    removes: List[str] = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        for ln in a[i1:i2]:
            name = _pointer_name(ln)
            if name is None or not _POINTER_RE.match(ln):
                name = bare_pointer(ln)
                if name is None or is_memory is None or not is_memory(name):
                    return None
            removes.append(name)
        for j in range(j1, j2):
            ln = b[j]
            name = _pointer_name(ln)
            section = _section_of(b, j)
            if name is None or section is None or not _POINTER_RE.match(ln):
                return None
            adds.append({"name": name, "line": ln, "section": section})
    add_names = {x["name"] for x in adds}
    # A pointer removed and re-added is a moved line: not hippo's add/remove shape.
    if add_names & set(removes):
        return None
    return adds, removes


def replay_floor(target: str, adds: List[dict], removes: List[str]) -> Tuple[str, int, int]:
    """Apply the adds and removes to ``target`` (the floor at the base ref) with a direct
    save's placement rules. ``(text, applied adds, applied removes)``; an add whose pointer is
    already there and a remove whose pointer is already gone are skipped."""
    from .new_memory_floor import _pointer_name, _points_at, _with_pointer

    text, n_add, n_rm = target, 0, 0
    for name in removes:
        lines = text.split("\n")
        kept = [ln for ln in lines if not _points_at(ln, name)]
        if len(kept) != len(lines):
            text, n_rm = "\n".join(kept), n_rm + 1
    for add in adds:
        m = _POINTER_RE.match(add["line"])
        new, out = _with_pointer(text, add["section"], add["name"], m.group("title"), m.group("hook") or "")
        if new is None:
            continue
        lines = new.split("\n")
        for i, ln in enumerate(lines):  # keep the exact line hippo wrote
            if _pointer_name(ln) == add["name"] and ln != add["line"] and f"]({add['name']}.md)" in ln:
                lines[i] = add["line"]
                break
        text, n_add = "\n".join(lines), n_add + 1
    return text, n_add, n_rm


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #
def _hippo_own(name: str) -> bool:
    return name in (OB.MANIFEST_NAME, ".gitignore", OB.FLOOR_BASE_NAME, ".memory-index")


def build_plan(memory_dir: str, repo_root: Optional[str] = None, base: Optional[str] = None) -> dict:
    """Classify the outbox against the base ref. Read-only (git reads only). Never raises:
    a failure comes back as ``plan["error"]``."""
    from . import trust as T

    plan: dict = {
        "error": None, "base": None, "rel": None, "top": None, "outbox": OB.outbox_dir(memory_dir),
        "new": [], "waiting": [], "stale": [], "held": [], "landed": [], "landed_unpulled": [],
        "ignored": [], "floor": None, "trusted": True,
    }
    try:
        top, rel = corpus_location(memory_dir)
        if not top:
            plan["error"] = "this corpus is not inside a git repository, so there is nothing to ship to"
            return plan
        plan["top"], plan["rel"] = top, rel
        b = resolve_base(top, base)
        if b is None:
            plan["error"] = (f"{base!r} is not a commit here" if base else
                             "no default-branch ref to ship onto (no origin/HEAD, origin/main, "
                             "origin/master, main or master); pass --base <ref>")
            return plan
        plan["base"] = b
        gate_root = T.gate_repo_root(memory_dir, repo_root)
        plan["trusted"] = T.trust_all() or (gate_root is not None and T.is_trusted(gate_root))
        d = OB.present(memory_dir)
        if not d:
            return plan
        at_base = _tree_blobs(top, b["sha"], rel)
        manifest = OB.read_manifest(memory_dir)
        allowed = OB.outbox_hashes(memory_dir, repo_root)
        refs = None  # lazily: only a file shipped once needs the in-flight scan
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            if _hippo_own(name):
                continue
            if os.path.islink(path):
                plan["ignored"].append({"name": name, "why": "a symlink"})
                continue
            if os.path.isdir(path):
                plan["ignored"].append({"name": name, "why": "a folder"})
                continue
            if name == OB.FLOOR_NAME:
                continue  # the floor is a replay, below
            if not OB._is_memory_filename(name):
                plan["ignored"].append({"name": name, "why": "not a memory file"})
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            sha = _sha256(data)
            stem = name[:-3]
            committed = _blob_bytes(top, at_base.get(name))
            item = {"name": name, "stem": stem, "path": path, "sha256": sha,
                    "committed_sha256": _sha256(committed), "edit": committed is not None}
            row = manifest.get(name) or {}
            if committed is not None and committed == data:
                live = os.path.join(memory_dir, name)
                pulled = os.path.isfile(live) and open(live, "rb").read() == data
                item["recorded"] = allowed is None or allowed.get(stem) == sha
                plan["landed" if pulled else "landed_unpulled"].append(item)
                continue
            shipped = row.get("shipped") if isinstance(row.get("shipped"), dict) else None
            branch = shipped.get("branch") if shipped and shipped.get("sha256") == sha else None
            if branch is None and row:
                if refs is None:
                    refs = _ship_refs(top, rel)
                blob = _blob_id(top, data)
                branch = next((br for br, files in sorted(refs.items()) if files.get(name) == blob), None)
            if branch and _ref_exists(top, branch):
                plan["waiting"].append(dict(item, branch=branch))
                continue
            base_sha = row.get("base_sha256")
            if shipped and committed is not None and item["committed_sha256"] == shipped.get("sha256"):
                base_sha = item["committed_sha256"]  # an edit on top of what this machine shipped
            if base_sha != item["committed_sha256"]:
                why = ("the default branch changed this file too" if committed is not None and base_sha
                       else "the default branch now has a memory of this name" if committed is not None
                       else "the default branch deleted this file")
                plan["stale"].append(dict(item, why=why, diff=_bounded_diff(
                    committed, data, f"{b['ref']}/{name}", f"outbox/{name}")))
                continue
            classes = []
            if allowed is not None and allowed.get(stem) != sha:
                classes.append("bytes hippo did not write on this machine")
            classes += scan_classes(data.decode("utf-8", "replace"))
            if classes:
                plan["held"].append(dict(item, classes=classes))
                continue
            plan["new"].append(item)
        plan["floor"] = _floor_plan(memory_dir, top, rel, b, at_base, manifest, d)
    except Exception as exc:
        plan["error"] = f"could not read the outbox against the base: {exc}"
    return plan


def _floor_plan(memory_dir, top, rel, b, at_base, manifest, d) -> Optional[dict]:
    copy = os.path.join(d, OB.FLOOR_NAME)
    if not os.path.isfile(copy):
        return None
    with open(copy, "rb") as fh:
        copy_bytes = fh.read()
    target_bytes = _blob_bytes(top, at_base.get(OB.FLOOR_NAME))
    fp = {"path": copy, "sha256": _sha256(copy_bytes), "state": None, "adds": 0, "removes": 0}
    base_path = os.path.join(d, OB.FLOOR_BASE_NAME)
    base_text = open(base_path, encoding="utf-8").read() if os.path.isfile(base_path) else None
    from .lint_floor import memory_exists

    changes = (floor_changes(base_text, copy_bytes.decode("utf-8"), memory_exists(memory_dir))
               if base_text is not None else None)
    if target_bytes is None or changes is None:
        fp["state"] = "stale"
        fp["why"] = ("the default branch has no MEMORY.md" if target_bytes is None
                     else "no recorded base text" if base_text is None
                     else "it holds changes other than pointer adds and removes")
        fp["diff"] = _bounded_diff(target_bytes, copy_bytes, f"{b['ref']}/MEMORY.md", "outbox/MEMORY.md")
        return fp
    adds, removes = changes
    text, n_add, n_rm = replay_floor(target_bytes.decode("utf-8"), adds, removes)
    fp.update(adds=n_add, removes=n_rm, replayed=text, pending=len(adds) + len(removes))
    if n_add == 0 and n_rm == 0:
        live = os.path.join(memory_dir, OB.FLOOR_NAME)
        pulled = os.path.isfile(live) and open(live, "rb").read() == target_bytes
        fp["state"] = "landed" if pulled else "landed_unpulled"
        return fp
    row = manifest.get(OB.FLOOR_NAME) or {}
    shipped = row.get("shipped") if isinstance(row.get("shipped"), dict) else None
    if shipped and shipped.get("sha256") == fp["sha256"] and _ref_exists(top, shipped.get("branch", "")):
        fp["state"], fp["branch"] = "waiting", shipped["branch"]
        return fp
    fp["state"] = "new"
    return fp


def counts_line(plan: dict) -> str:
    waits = sorted({w["branch"] for w in plan["waiting"]} | ({plan["floor"]["branch"]}
                   if plan.get("floor") and plan["floor"].get("state") == "waiting" else set()))
    floor_new = bool(plan.get("floor") and plan["floor"]["state"] == "new")
    waiting = len(plan["waiting"])
    landed = len(plan["landed"]) + len(plan["landed_unpulled"])
    parts = [f"{len(plan['new'])} to ship" + (" (and the floor's pointer changes)" if floor_new else "")]
    parts.append(f"{waiting} waiting" + (f" on {', '.join(waits)}" if waits else ""))
    parts += [f"{len(plan['stale'])} stale", f"{len(plan['held'])} held", f"{landed} landed"]
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
# the manifest's ship-side writes (one compare-and-swap site)
# --------------------------------------------------------------------------- #
def _edit_manifest(memory_dir: str, edit) -> bool:
    """Apply ``edit(files) -> files`` to the outbox manifest by compare-and-swap. False when
    it could not be written (the caller names it)."""
    from .atomic import update_text_cas

    path = OB.manifest_path(memory_dir)
    if not os.path.isfile(path):
        return False

    def transform(text: str) -> Optional[str]:
        try:
            doc = json.loads(text) if text.strip() else {}
        except ValueError:
            doc = {}
        files = doc.get("files") if isinstance(doc, dict) and isinstance(doc.get("files"), dict) else {}
        new = edit({k: dict(v) if isinstance(v, dict) else v for k, v in files.items()})
        if new == files:
            return None
        return json.dumps({"version": 1, "files": new}, indent=2, sort_keys=True) + "\n"

    try:
        update_text_cas(path, transform)
        return True
    except Exception:
        return False


def _drop_hash(memory_dir: str, stem: str, repo_root: Optional[str]) -> bool:
    try:
        from . import trust as T
        from .provenance_env import main_worktree_root

        gate_root = T.gate_repo_root(memory_dir, repo_root)
        if gate_root is None:
            return True
        keys = {T._corpus_key(gate_root)}
        main = main_worktree_root(gate_root)
        if main:
            keys.add(T._corpus_key(main))
        doc = T._load_registry_doc()
        trusted = doc.get("trusted") if isinstance(doc.get("trusted"), dict) else {}
        touched = False
        for key in keys:
            entry = trusted.get(key)
            hashes = entry.get("outbox") if isinstance(entry, dict) else None
            if isinstance(hashes, dict) and stem in hashes:
                entry["outbox"] = {k: v for k, v in hashes.items() if k != stem}
                touched = True
        return bool(T._write_registry_doc(doc)) if touched else True
    except Exception:
        return False


def drop_item(memory_dir: str, name: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Remove one outbox file, its manifest row and its hash record (the floor: its copy and
    its recorded base text). None when all of it went, else what stayed."""
    fname = OB._fname(name)
    d = OB.outbox_dir(memory_dir)
    problems = []
    paths = [os.path.join(d, OB.FLOOR_BASE_NAME), os.path.join(d, fname)] if fname == OB.FLOOR_NAME else [os.path.join(d, fname)]
    for p in paths:  # the floor's base text goes first: a copy with no base reads as stale
        try:
            if os.path.lexists(p):
                os.remove(p)
        except OSError as exc:
            problems.append(f"{os.path.basename(p)} ({exc})")
    if os.path.isfile(OB.manifest_path(memory_dir)) and not _edit_manifest(
        memory_dir, lambda files: {k: v for k, v in files.items() if k != fname}
    ):
        problems.append("its manifest row")
    if fname != OB.FLOOR_NAME and not _drop_hash(memory_dir, fname[:-3], repo_root):
        problems.append("its trust record")
    return ("could not fully remove the outbox copy of " + fname + ": " + ", ".join(problems)) if problems else None


def set_base(memory_dir: str, name: str, base_sha256: Optional[str]) -> bool:
    """``--mine``: record the committed copy's current bytes as the copy's base."""
    fname = OB._fname(name)

    def edit(files):
        row = dict(files.get(fname) or {})
        row["base_sha256"] = base_sha256
        row.pop("shipped", None)
        return {**files, fname: row}

    return _edit_manifest(memory_dir, edit)


# --------------------------------------------------------------------------- #
# the commit
# --------------------------------------------------------------------------- #
def default_branch_name() -> str:
    return BRANCH_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _objects_env(top: str, scratch: str, write: bool) -> dict:
    """A temporary index always; for a preview also a scratch object store (the repo's own
    objects as an alternate), so building the would-be commit writes nothing into the repo."""
    env = {"GIT_INDEX_FILE": os.path.join(scratch, "index")}
    if not write:
        common = _out(top, "rev-parse", "--git-common-dir") or ".git"
        objects = os.path.join(common if os.path.isabs(common) else os.path.join(top, common), "objects")
        env.update(GIT_OBJECT_DIRECTORY=os.path.join(scratch, "objects"),
                   GIT_ALTERNATE_OBJECT_DIRECTORIES=objects)
        os.makedirs(os.path.join(scratch, "objects"), exist_ok=True)
    return env


def message(plan: dict) -> str:
    rel = plan["rel"]
    files = [f"- {rel}/{i['name']} ({'edit' if i['edit'] else 'new'})" for i in plan["new"]]
    fl = plan.get("floor")
    if fl and fl["state"] == "new":
        files.append(f"- {rel}/MEMORY.md (floor: {fl['adds']} pointer(s) added, {fl['removes']} removed)")
    n = len(plan["new"])
    subject = f"Land {n} memor{'y' if n == 1 else 'ies'} from the outbox"
    return subject + "\n\n" + "\n".join(files) + "\n"


def build_commit(plan: dict, write: bool) -> dict:
    """The would-be commit: ``{"commit", "tree", "gate", "error"}``. With ``write`` False
    everything lands in a scratch object store that is removed afterwards."""
    from . import gate_cli

    top, rel, base = plan["top"], plan["rel"], plan["base"]["sha"]
    scratch = tempfile.mkdtemp(prefix="hippo-ship-")
    out = {"commit": None, "tree": None, "gate": None, "error": None}
    saved = {k: os.environ.get(k) for k in ("GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")}
    try:
        env = _objects_env(top, scratch, write)
        if _git(top, "read-tree", base, env=env)[0] != 0:
            out["error"] = "could not read the base tree"
            return out
        entries = [(f"{rel}/{i['name']}", open(i["path"], "rb").read()) for i in plan["new"]]
        fl = plan.get("floor")
        if fl and fl["state"] == "new":
            entries.append((f"{rel}/MEMORY.md", fl["replayed"].encode("utf-8")))
        for path, data in entries:
            blob = _blob_id(top, data, env=env, write=True)
            if not blob or _git(top, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)[0] != 0:
                out["error"] = f"could not stage {path} in the temporary index"
                return out
        tree = _out(top, "write-tree", env=env)
        if not tree:
            out["error"] = "could not write the tree"
            return out
        rc, raw = _git(top, "commit-tree", tree, "-p", base, data=message(plan).encode("utf-8"), env=env)
        if rc != 0:
            out["error"] = "git could not create the commit (is user.name / user.email configured?)"
            return out
        out["tree"], out["commit"] = tree, raw.decode().strip()
        os.environ.update(env)  # the gate's reads see the scratch objects too
        out["gate"] = gate_cli.judge_sides(top, rel, base, out["commit"])
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(scratch, ignore_errors=True)


def create_branch(top: str, branch: str, commit: str) -> Optional[str]:
    """Create ``refs/heads/<branch>`` at ``commit``, create-only. None, or why not."""
    rc, _ = _git(top, "check-ref-format", f"refs/heads/{branch}")
    if rc != 0:
        return f"{branch!r} is not a valid branch name"
    rc, _ = _git(top, "update-ref", f"refs/heads/{branch}", commit, "")
    return None if rc == 0 else f"branch {branch} already exists (hippo never overwrites a ref)"


def stamp_shipped(memory_dir: str, items: List[dict], branch: str, commit: str, floor_sha: Optional[str]) -> bool:
    def edit(files):
        files = dict(files)
        for it in items:
            row = dict(files.get(it["name"]) or {})
            row["shipped"] = {"branch": branch, "commit": commit, "sha256": it["sha256"]}
            files[it["name"]] = row
        if floor_sha:
            row = dict(files.get(OB.FLOOR_NAME) or {})
            row["shipped"] = {"branch": branch, "commit": commit, "sha256": floor_sha}
            files[OB.FLOOR_NAME] = row
        return files

    return _edit_manifest(memory_dir, edit)


def land(memory_dir: str, plan: dict, repo_root: Optional[str]) -> Tuple[List[str], List[str]]:
    """Drop what landed and this checkout already has (``--yes`` only). The bytes this
    machine recorded writing join the consent baseline on the way out (authorship is
    consent, applied at landing). ``(dropped names, problem lines)``."""
    from .trust import record_authored_write

    dropped, problems = [], []
    for it in plan["landed"]:
        if it.get("recorded"):
            record_authored_write(memory_dir, os.path.join(memory_dir, it["name"]), repo_root)
        why = drop_item(memory_dir, it["name"], repo_root)
        (problems.append(why) if why else dropped.append(it["name"]))
    fl = plan.get("floor")
    if fl and fl["state"] == "landed":
        why = drop_item(memory_dir, OB.FLOOR_NAME, repo_root)
        (problems.append(why) if why else dropped.append(OB.FLOOR_NAME))
    if dropped:
        OB.refresh_index(memory_dir)
        try:  # the landed files are recalled from the corpus index now, not the outbox's
            from .build_index import refresh_index

            refresh_index(memory_dir)
        except Exception:
            pass
    return dropped, problems
