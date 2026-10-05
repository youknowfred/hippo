"""TND-6: per-file review and grant over the consent baseline — the engine behind ``hippo trust``.

``trust.py`` keeps one consent record per corpus: the sha256 of every memory file's bytes
at the moment the user consented. Recall withholds any file whose bytes no longer match.
Until this module, the only way back was a whole-corpus re-consent (``mark_trusted``),
which re-baselines files nobody was shown, or folding files one at a time by hand after
building a diff from git yourself. Untracked files could not be diffed at all, because the
record keeps hashes, not bytes.

This module gives that loop one engine, shared by the CLI and any MCP tool:

- **review** — every file that differs from the consent record: a CHANGED file as a
  unified diff from the exact consented bytes (found in the file's git history by hash,
  else in the local baseline store, else marked "no recorded baseline" and shown whole),
  a NEW file in full, a REMOVED file by name. It ends with a digest over exactly what was
  shown.
- **grant** — consent for named files (``--files``) or for everything a review showed
  (``--all-reviewed``), each requiring that digest. It updates the record for exactly
  those files; files that were not shown stay withheld. Consent is never inferred from git
  authorship: authorship is spoofable, and it is the very boundary the quarantine guards.
- **status** — trusted or not, consent age, and how many files are withheld right now.
- **revoke** — the existing untrust.

The **baseline store** is what makes untracked files diffable: whenever consent is
recorded for a file whose bytes git cannot give back (untracked, ignored, or modified
against HEAD), a content-addressed copy is kept beside the trust registry, in a
self-ignoring directory, bounded in size and pruned of copies no consent record names.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import trust

DIGEST_CHARS = 12  # the same short-token length the MCP consent flow uses
_STORE_DIRNAME = "hippo-trust-baselines"
_STORE_MAX_BLOB = 2 * 1024 * 1024  # a memory file is a few KB; anything this big is not one
_STORE_MAX_TOTAL = 64 * 1024 * 1024
_GIT_HISTORY_CAP = 200  # commits searched per file for the consented blob
_KIND_ORDER = {"changed": 0, "added": 1, "removed": 2}


# --------------------------------------------------------------------------- #
# The baseline store
# --------------------------------------------------------------------------- #
def baseline_store_dir() -> str:
    """``<dir of the trust registry>/hippo-trust-baselines`` — machine-local, never in a
    repo, and relocated with ``HIPPO_TRUST_FILE`` (hermetic tests)."""
    reg = os.path.abspath(trust.trust_registry_path())
    return os.path.join(os.path.dirname(reg), _STORE_DIRNAME)


def _is_sha(name: str) -> bool:
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name)


def _referenced_hashes() -> set:
    refs: set = set()
    trusted = trust._load_registry_doc().get("trusted")
    if not isinstance(trusted, dict):
        return refs
    for entry in trusted.values():
        fp = entry.get("fingerprint") if isinstance(entry, dict) else None
        files = fp.get("files") if isinstance(fp, dict) else None
        if isinstance(files, dict):
            refs.update(v for v in files.values() if isinstance(v, str))
    return refs


def _git_status_paths(top: str, pathspec: str) -> Optional[set]:
    """Repo-relative paths ``git status`` lists (untracked, ignored, or modified) under
    ``pathspec``; None when git could not answer."""
    try:
        out = subprocess.run(
            ["git", "-C", top, "status", "--porcelain=v1", "-z", "--untracked-files=all",
             "--ignored=matching", "--", pathspec],
            capture_output=True, timeout=20, check=False,
        )
        if out.returncode != 0:
            return None
        toks = out.stdout.decode("utf-8", "surrogateescape").split("\0")
        listed: set = set()
        i = 0
        while i < len(toks):
            tok = toks[i]
            i += 1
            if len(tok) < 4:
                continue
            listed.add(tok[3:])
            if tok[0] in "RC" or tok[1] in "RC":
                i += 1  # -z prints a rename's source path as the next token
        return listed
    except Exception:
        return None


def _git_unrecoverable(memory_dir: str, paths: List[str]) -> List[str]:
    """The subset of ``paths`` whose current bytes git could NOT give back later."""
    from .provenance import git_root

    top = git_root(memory_dir)
    if not top:
        return list(paths)
    top_real = os.path.realpath(top)
    md_rel = os.path.relpath(os.path.realpath(memory_dir), top_real)
    if md_rel.startswith(".."):
        return list(paths)
    listed = _git_status_paths(top_real, md_rel)
    if listed is None:
        return list(paths)
    out = []
    for p in paths:
        rel = os.path.relpath(os.path.realpath(p), top_real)
        if rel.startswith("..") or rel in listed:
            out.append(p)
    return out


def _store_total(store: str) -> int:
    total = 0
    try:
        for e in os.scandir(store):
            if _is_sha(e.name):
                total += e.stat().st_size
    except OSError:
        pass
    return total


def prune_store() -> int:
    """Delete stored copies no consent record names; returns how many went. Never raises."""
    store = baseline_store_dir()
    removed = 0
    try:
        if not os.path.isdir(store):
            return 0
        refs = _referenced_hashes()
        for e in os.scandir(store):
            if _is_sha(e.name) and e.name not in refs:
                try:
                    os.remove(e.path)
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed


def keep_consented_baselines(memory_dir: str, paths: Optional[List[str]] = None) -> int:
    """Copy consented bytes git cannot give back into the store; returns copies written.

    Called right after consent is recorded (``mark_trusted``, an authored-write fold, a
    grant). ``paths`` None means every memory file in ``memory_dir``. Skips a file whose
    current bytes are not what the record now holds (consent was not recorded for them),
    a file over the per-copy cap, and anything that would push the store past its total
    cap. Prunes unreferenced copies after. Best-effort; never raises.
    """
    written = 0
    try:
        from .atomic import write_bytes_atomic
        from .provenance import _iter_memory_files, ensure_self_ignoring_dir

        if paths is None:
            paths = list(_iter_memory_files(memory_dir)) if os.path.isdir(memory_dir) else []
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return 0
        refs = _referenced_hashes()
        store = baseline_store_dir()
        total = None
        for p in _git_unrecoverable(memory_dir, paths):
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            sha = hashlib.sha256(data).hexdigest()
            if sha not in refs or len(data) > _STORE_MAX_BLOB:
                continue
            dest = os.path.join(store, sha)
            if os.path.exists(dest):
                continue
            if total is None:
                ensure_self_ignoring_dir(store)
                total = _store_total(store)
            if total + len(data) > _STORE_MAX_TOTAL:
                continue
            write_bytes_atomic(dest, data)
            total += len(data)
            written += 1
        if written:
            prune_store()
    except Exception:
        pass
    return written


def _stored_bytes(sha: str) -> Optional[bytes]:
    try:
        with open(os.path.join(baseline_store_dir(), sha), "rb") as fh:
            data = fh.read()
        return data if hashlib.sha256(data).hexdigest() == sha else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Finding the consented bytes in git history
# --------------------------------------------------------------------------- #
def _git_bytes_for_hash(path: str, want_sha: str) -> Optional[bytes]:
    """The blob of ``path`` whose sha256 equals ``want_sha``, searched in the index and
    the file's history on every ref (newest first, capped). None when no version
    matches or git cannot answer. Read-only; never raises."""
    try:
        from .provenance import git_root, run_git

        top = git_root(os.path.dirname(os.path.abspath(path)))
        if not top:
            return None
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top))
        if rel.startswith("..") or "\n" in rel:
            return None
        commits = run_git(
            ["log", "--all", "--format=%H", "-n", str(_GIT_HISTORY_CAP), "--", rel], top
        ).split()
        names = [":" + rel] + [f"{c}:{rel}" for c in commits]
        out = subprocess.run(
            ["git", "-C", top, "cat-file", "--batch"],
            input=("\n".join(names) + "\n").encode("utf-8"),
            capture_output=True, timeout=30, check=False,
        ).stdout
        pos = 0
        for _ in names:
            nl = out.find(b"\n", pos)
            if nl < 0:
                break
            header = out[pos:nl].split()
            pos = nl + 1
            if len(header) != 3 or not header[2].isdigit():
                continue  # "<name> missing" / "ambiguous": no content follows
            size = int(header[2])
            data = out[pos:pos + size]
            pos += size + 1
            if header[1] == b"blob" and hashlib.sha256(data).hexdigest() == want_sha:
                return data
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Drift, review, digest
# --------------------------------------------------------------------------- #
def _stem(name: str) -> str:
    name = os.path.basename(name.strip())
    return name[:-3] if name.endswith(".md") else name


def _live_files(memory_dir: str) -> Dict[str, Dict[str, str]]:
    from .provenance import _iter_memory_files

    live: Dict[str, Dict[str, str]] = {}
    if not os.path.isdir(memory_dir):
        return live
    for p in _iter_memory_files(memory_dir):
        h = trust.file_sha256(p)
        if h is not None:
            live[os.path.splitext(os.path.basename(p))[0]] = {"path": p, "sha": h}
    return live


def corpus_state(memory_dir: str, repo_root: Optional[str]) -> dict:
    """Where this corpus stands against its consent record. Read-only; never raises.

    ``state``: ``bypassed`` (HIPPO_TRUST_ALL), ``inapplicable`` (no gate here),
    ``untrusted``, ``legacy`` (trusted, no per-file record), or ``trusted``. ``rows`` is
    the review set: for a trusted corpus every changed/new/removed file; for an untrusted
    or legacy one every file, as new (there is no consented version to diff against).
    """
    out: dict = {"state": "inapplicable", "gate_root": None, "entry": None, "rows": [],
                 "total": 0, "withheld": 0}
    try:
        if trust.trust_all():
            out["state"] = "bypassed"
            return out
        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        out["gate_root"] = gate_root
        if gate_root is None:
            return out
        live = _live_files(memory_dir)
        out["total"] = len(live)
        entry = trust._trusted_entry(gate_root)
        out["entry"] = entry
        fp = entry.get("fingerprint") if entry else None
        base = fp.get("files") if isinstance(fp, dict) else None
        rows: List[dict] = []
        if entry is None or not isinstance(base, dict):
            out["state"] = "untrusted" if entry is None else "legacy"
            for stem in sorted(live):
                rows.append({"stem": stem, "kind": "added", "baseline": None,
                             "live": live[stem]["sha"], "path": live[stem]["path"]})
            out["withheld"] = len(live) if entry is None else 0
        else:
            out["state"] = "trusted"
            for stem in sorted(live):
                b = base.get(stem)
                if b == live[stem]["sha"]:
                    continue
                rows.append({"stem": stem, "kind": "changed" if b else "added", "baseline": b,
                             "live": live[stem]["sha"], "path": live[stem]["path"]})
            for stem in sorted(set(base) - set(live)):
                rows.append({"stem": stem, "kind": "removed", "baseline": base[stem],
                             "live": None, "path": None})
            out["withheld"] = sum(1 for r in rows if r["kind"] != "removed")
        rows.sort(key=lambda r: (_KIND_ORDER[r["kind"]], r["stem"]))
        out["rows"] = rows
        return out
    except Exception:
        return out


def review_digest(gate_root: Optional[str], rows: List[dict]) -> str:
    """The token a grant must quote: a hash over the corpus key and, per reviewed file,
    its kind, the consented hash and the current hash. Any byte that changes after the
    review, in a reviewed file or in the record, changes it."""
    lines = [trust._corpus_key(gate_root) if gate_root else ""]
    for r in sorted(rows, key=lambda r: r["stem"]):
        lines.append(f"{r['stem']}\t{r['kind']}\t{r.get('baseline') or '-'}\t{r.get('live') or '-'}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def _select(rows: List[dict], files: Optional[List[str]]) -> tuple:
    """``(selected rows, unknown names)`` for a ``--files`` narrowing (None = all)."""
    if files is None:
        return rows, []
    wanted = [_stem(f) for f in files if f.strip()]
    by_stem = {r["stem"]: r for r in rows}
    return [by_stem[s] for s in wanted if s in by_stem], [s for s in wanted if s not in by_stem]


def _decode(data: bytes) -> List[str]:
    return data.decode("utf-8", "replace").splitlines(keepends=True)


def _diff(old: List[str], new: List[str], a: str, b: str) -> str:
    lines = list(difflib.unified_diff(old, new, fromfile=a, tofile=b, n=3))
    return "".join(ln if ln.endswith("\n") else ln + "\n" for ln in lines)


def build_review(memory_dir: str, repo_root: Optional[str], files: Optional[List[str]] = None) -> dict:
    """The review: ``corpus_state`` plus a rendered body per shown file and the digest.

    ``files`` narrows it to those names (a name with nothing to review is reported in
    ``unknown``). Each changed file's ``source`` is ``git`` / ``store`` / ``none`` —
    where the consented bytes came from. Read-only; never raises.
    """
    st = corpus_state(memory_dir, repo_root)
    shown, unknown = _select(st["rows"], files)
    items = []
    for r in shown:
        stem = r["stem"]
        item = dict(r)
        if r["kind"] == "removed":
            item.update(source=None, body="")
        else:
            try:
                with open(r["path"], "rb") as fh:
                    cur = _decode(fh.read())
            except OSError:
                cur = []
            old = None
            if r["kind"] == "changed" and r.get("baseline"):
                data = _git_bytes_for_hash(r["path"], r["baseline"])
                if data is not None:
                    item["source"] = "git"
                else:
                    data = _stored_bytes(r["baseline"])
                    item["source"] = "store" if data is not None else "none"
                old = _decode(data) if data is not None else None
            else:
                item["source"] = None
            if old is not None:
                item["body"] = _diff(old, cur, f"consented/{stem}.md", f"current/{stem}.md")
            else:
                item["body"] = _diff([], cur, "/dev/null", f"current/{stem}.md")
        items.append(item)
    return {**st, "items": items, "unknown": unknown,
            "digest": review_digest(st["gate_root"], shown) if shown else None,
            "narrowed": files is not None}


# --------------------------------------------------------------------------- #
# Grant / status / revoke
# --------------------------------------------------------------------------- #
def grant(
    memory_dir: str,
    repo_root: Optional[str],
    *,
    digest: str,
    files: Optional[List[str]] = None,
    all_reviewed: bool = False,
) -> dict:
    """Consent to reviewed files: exactly ``files``, or every file the full review showed.

    The digest must be the one a review printed: either the full review (then ``files``
    may name any subset of it) or a review narrowed to exactly ``files``. A digest that no
    longer matches means something changed since that review, and nothing is granted.
    Updates the consent record for the granted files only — a changed or new file's
    current hash goes in, a removed file's entry comes out — and never touches any other
    file's entry. On an untrusted corpus the first grant creates the record (origin
    ``review``), holding exactly the granted files. Never raises.
    """
    res: dict = {"ok": False, "error": None, "granted": [], "state": None, "remaining": 0}
    try:
        digest = (digest or "").strip()
        st = corpus_state(memory_dir, repo_root)
        res["state"] = st["state"]
        if st["state"] == "bypassed":
            res["error"] = "the HIPPO_TRUST_ALL bypass is set; there is no consent record to update"
            return res
        if st["state"] == "inapplicable":
            res["error"] = "the trust gate does not apply here (no git repo and no corpus content)"
            return res
        if not digest:
            res["error"] = "a grant needs --digest, from the review you read (hippo trust review)"
            return res
        if bool(files) == bool(all_reviewed):
            res["error"] = "name the files to grant (--files a.md,b.md) or pass --all-reviewed"
            return res
        rows = st["rows"]
        full = review_digest(st["gate_root"], rows) if rows else None
        if all_reviewed:
            selected = rows
            ok_digest = digest == full
        else:
            selected, unknown = _select(rows, files)
            if unknown:
                res["error"] = (
                    "nothing to grant for " + ", ".join(unknown) + " — not in the review "
                    "(unchanged since consent, or no such memory)"
                )
                return res
            ok_digest = digest in (full, review_digest(st["gate_root"], selected))
        if not selected:
            res["error"] = "nothing to grant — every memory matches what you consented to"
            return res
        if not ok_digest:
            res["error"] = (
                "the digest does not match the corpus as it is now (a reviewed file, or the "
                "consent record, changed since that review). Nothing was granted; run "
                "hippo trust review again"
            )
            return res
        doc = trust._load_registry_doc()
        trusted = doc.get("trusted") if isinstance(doc.get("trusted"), dict) else {}
        key = trust._corpus_key(st["gate_root"])
        entry = trusted.get(key) if isinstance(trusted.get(key), dict) else {}
        now = datetime.now(timezone.utc).isoformat()
        fp = entry.get("fingerprint") if isinstance(entry.get("fingerprint"), dict) else {}
        base = dict(fp.get("files")) if isinstance(fp.get("files"), dict) else {}
        for r in selected:
            if r["kind"] == "removed":
                base.pop(r["stem"], None)
            else:
                base[r["stem"]] = r["live"]
        if not entry:  # first consent: a reviewed corpus, like the MCP consent flow's
            entry = {"trusted_at": now, "origin": "review"}
        entry = dict(entry)
        entry["last_grant_at"] = now
        entry["fingerprint"] = {
            "files": base,
            "digest": hashlib.sha256(
                "\n".join(f"{k}:{v}" for k, v in sorted(base.items())).encode("utf-8")
            ).hexdigest(),
        }
        trusted[key] = entry
        doc["trusted"] = trusted
        trust._write_registry_doc(doc)
        keep_consented_baselines(memory_dir, [r["path"] for r in selected if r.get("path")])
        res["ok"] = True
        res["granted"] = [r["stem"] for r in selected]
        res["remaining"] = corpus_state(memory_dir, repo_root)["withheld"]
        return res
    except Exception as exc:
        res["error"] = f"the grant failed ({exc}); nothing was trusted"
        return res


def _age_days(iso: Optional[str]) -> Optional[int]:
    try:
        then = datetime.fromisoformat(str(iso))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - then).days)
    except Exception:
        return None


def status(memory_dir: str, repo_root: Optional[str]) -> dict:
    """Trusted or not, consent age, and the drift counts. Read-only; never raises."""
    st = corpus_state(memory_dir, repo_root)
    entry = st.get("entry") or {}
    kinds = [r["kind"] for r in st["rows"]]
    return {
        "state": st["state"],
        "gate_root": st["gate_root"],
        "origin": entry.get("origin"),
        "trusted_at": entry.get("trusted_at"),
        "age_days": _age_days(entry.get("trusted_at")),
        "last_grant_at": entry.get("last_grant_at"),
        "changed": kinds.count("changed") if st["state"] == "trusted" else 0,
        "added": kinds.count("added") if st["state"] == "trusted" else 0,
        "removed": kinds.count("removed"),
        "withheld": st["withheld"],
        "total": st["total"],
    }


def revoke(memory_dir: str, repo_root: Optional[str]) -> dict:
    """Remove this corpus's consent record (``trust.untrust``). Never raises."""
    gate_root = trust.gate_repo_root(memory_dir, repo_root) or repo_root
    if not gate_root:
        return {"ok": False, "gate_root": None, "error": "no repo root to revoke"}
    ok = trust.untrust(gate_root)
    if ok:
        prune_store()
    return {"ok": ok, "gate_root": gate_root, "error": None if ok else "registry write failed"}
