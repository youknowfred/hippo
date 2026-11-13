"""OWN-2: promotion as a tend kind — a lesson this corpus shares with another one.

``promote_scan`` already found feedback/user memories that are near-duplicates across the
machine's registered, trusted corpora, and only reported them in the sleep report. This
module turns the same scan, aimed at ONE corpus (the one a tend pass runs in) against each
other trusted corpus, into the tend queue's ``promote`` kind, in two shapes:

  - ``promote:<name>`` — this corpus's lesson has a twin with the same text in at least one
    other corpus. A lesson kept twice is about the person, not the project: the verdict
    ``promote`` lifts it into the user tier through ``new_memory.promote_memory`` (the
    Promote flow's own writer, every guard included). As in that flow, the project copy
    then leaves this corpus (``git rm`` when tracked, so it lands with the next commit) and
    its floor pointer goes with it.
  - ``promote:drift:<name>|<corpus>/<twin>`` — the same rule (a near-duplicate by the
    scan's own measure) reads differently in the other corpus. The evidence is the diff.
    ``adopt`` copies the other corpus's description and body into THIS memory (one CAS
    write through ``outbox_edit.edit_path``, the seam every tend edit uses, so outbox mode
    is honoured; the memory keeps its own name, type and provenance keys); ``distinct``
    marks the two as different rules (a skip that lapses when either text changes);
    ``promote`` lifts this corpus's version as above.

Never a write into the other corpus: a tend pass edits the corpus it runs in and the user
tier. The sibling sees its half the next time it is tended there.

Discipline:
  - Only registered AND trusted corpora are read (``promote_scan.trusted_corpora``); an
    untrusted one contributes nothing and is not named.
  - Off the hot path: the queue's source build (``hippo tend``, the MCP tool, doctor) and
    sleep. Bounded by the scan's caps, and cached under this corpus's telemetry dir keyed
    by a stat fingerprint of every input (both corpora's memory files, their indexes, the
    user tier's names, the threshold knobs), so a rebuild with nothing changed reads one
    small JSON file and stats a few directories.
  - Machine-local memories (OWN-1, ``machine_local: true``) are never proposed for the
    user tier, and a retired memory never appears.
  - A corpus with no other registered, trusted corpus yields nothing and writes nothing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from typing import List, Optional, Tuple

_CACHE_FILE = "promote_scan.json"
_CACHE_VERSION = 1
_LESSON_CAP = 1000  # the tend path reads a large corpus whole; the per-pair budget still bounds scoring
_DIGEST = 6
_MAX_DETAIL_LINES = 80
_RELATED_RE = re.compile(r"^\s*Related\s*:", re.IGNORECASE)
_DESCRIPTION_KEY_RE = re.compile(r"^(\s*)description\s*:")


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _parts(text: str) -> Tuple[str, str]:
    """``(description, body)`` of one memory file; the body without its Related lines."""
    from .provenance import parse_frontmatter, split_frontmatter

    desc = parse_frontmatter(text).get("description")
    body = split_frontmatter(text)[1]
    kept = [ln for ln in body.strip("\n").split("\n") if not _RELATED_RE.match(ln)]
    return (desc if isinstance(desc, str) else ""), "\n".join(kept).strip("\n")


def _norm(text: str) -> str:
    desc, body = _parts(text)
    return " ".join(f"{desc}\n{body}".split())


def _digest(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:_DIGEST]


def _shown(text: str) -> List[str]:
    desc, body = _parts(text)
    return [f"description: {desc}", ""] + body.split("\n")


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# The scan for one corpus, cached
# --------------------------------------------------------------------------- #
def _others(memory_dir: str) -> List[tuple]:
    """``[(label, root, memory_dir)]``: every OTHER registered, trusted corpus, capped.
    The label is the registered root's basename (a clash gets ``-2``, ``-3``…)."""
    from .promote_scan import MAX_PROJECTS, trusted_corpora

    me = os.path.realpath(memory_dir)
    eligible, _untrusted = trusted_corpora()
    out, seen = [], {}
    for root, md in eligible:
        if os.path.realpath(md) == me:
            continue
        base = os.path.basename(os.path.normpath(root)) or root
        seen[base] = seen.get(base, 0) + 1
        out.append((base if seen[base] == 1 else f"{base}-{seen[base]}", root, md))
    return out[:MAX_PROJECTS]


def _stat_dir(d: Optional[str]) -> List[str]:
    if not d:
        return []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        try:
            st = os.stat(os.path.join(d, n))
            out.append(f"{n}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue
    return out


def _fingerprint(memory_dir: str, others: List[tuple]) -> str:
    from .build_index import default_index_dir, dense_disabled
    from .outbox import present
    from .promote_scan import index_dir_of
    from .provenance import user_memory_dir

    parts = [str(_CACHE_VERSION), os.environ.get("HIPPO_DUP_THRESHOLD", ""), str(dense_disabled())]
    for d in (memory_dir, present(memory_dir), default_index_dir(memory_dir), user_memory_dir()):
        parts += [f"@{d}"] + _stat_dir(d)
    for label, root, md in others:
        parts += [f"@{label}|{root}|{md}"] + _stat_dir(md) + _stat_dir(index_dir_of(md))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _cache_path(memory_dir: str) -> str:
    from .telemetry import default_telemetry_dir

    return os.path.join(default_telemetry_dir(memory_dir), _CACHE_FILE)


def _write_cache(memory_dir: str, doc: dict) -> None:
    """Best-effort: the cache is derived, and a miss only costs the next build a rescan."""
    try:
        from .atomic import write_json_atomic

        path = _cache_path(memory_dir)
        if not os.path.isdir(os.path.dirname(path)):
            return  # never create a ledger dir for a corpus that has none yet
        write_json_atomic(path, doc)
    except Exception:
        pass


def scan_for(memory_dir: str) -> dict:
    """``{"pairs": [...], "notes": [...]}`` for THIS corpus against every other trusted one.

    A pair: ``{name, corpus, root, memory_dir, twin, score, same, machine_local, here,
    there}`` (``here``/``there``: short digests of each side's normalized text). Read-only
    over every corpus; the only write is the gitignored cache. Never raises."""
    empty = {"pairs": [], "notes": []}
    try:
        others = _others(memory_dir)
        if not others:
            return empty
        fp = _fingerprint(memory_dir, others)
        try:
            with open(_cache_path(memory_dir), "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and cached.get("fingerprint") == fp:
                return cached
        except Exception:
            pass
        from .build_index import default_index_dir
        from .promote_scan import _lessons_of, index_dir_of, twins

        mine = _lessons_of(memory_dir, cap=_LESSON_CAP, effective=True)
        doc = {"version": _CACHE_VERSION, "fingerprint": fp, "pairs": [], "notes": []}
        for label, root, md in others:
            theirs = _lessons_of(md, cap=_LESSON_CAP)
            if not mine or not theirs:
                continue
            found, note = twins(mine, default_index_dir(memory_dir), theirs, index_dir_of(md))
            if note:
                doc["notes"].append(f"{label}: {note}")
            for a, b, score in found:
                here, there = _digest(a["text"]), _digest(b["text"])
                doc["pairs"].append({
                    "name": a["name"], "corpus": label, "root": root, "memory_dir": md,
                    "twin": b["name"], "score": score, "same": here == there,
                    "machine_local": bool(a.get("machine_local")), "here": here, "there": there,
                })
        _write_cache(memory_dir, doc)
        return doc
    except Exception:
        return empty


# --------------------------------------------------------------------------- #
# Queue entries
# --------------------------------------------------------------------------- #
def entries(memory_dir: str) -> List[Tuple[str, str, str]]:
    """``[(target, evidence, proposed)]`` for the tend queue's ``promote`` kind."""
    from .provenance import user_memory_dir

    doc = scan_for(memory_dir)
    by_name: dict = {}
    for p in doc.get("pairs") or []:
        by_name.setdefault(p["name"], []).append(p)
    in_user_tier = os.path.isdir(user_memory_dir())
    out = []
    for name in sorted(by_name):
        pairs = by_name[name]
        same = [p for p in pairs if p["same"]]
        lifted = in_user_tier and os.path.isfile(os.path.join(user_memory_dir(), f"{name}.md"))
        if same and not pairs[0]["machine_local"] and not lifted:
            where = ", ".join(sorted({p["corpus"] for p in same}))
            best = max(p["score"] for p in same)
            out.append((
                name,
                f"the same lesson is also kept in {where} (similarity {best:.2f})",
                "promote it to the user tier, where every project on this machine recalls it",
            ))
        for p in pairs:
            if p["same"]:
                continue
            out.append((
                f"drift:{name}|{p['corpus']}/{p['twin']}",
                f"the same rule reads differently in {p['corpus']} as {p['twin']} "
                f"(similarity {p['score']:.2f}; text {p['here']} here, {p['there']} there)",
                "read the diff; adopt the other text, mark them distinct, or promote this one",
            ))
    return out


def _resolve(target: str, memory_dir: str) -> Tuple[str, List[dict]]:
    """``(name, pairs)`` an entry target stands for: every same-text pair of a promote
    target, or the one pair of a drift target."""
    pairs = scan_for(memory_dir).get("pairs") or []
    if target.startswith("drift:"):
        name, rest = target[len("drift:"):].split("|", 1)
        corpus, twin = rest.split("/", 1)
        return name, [p for p in pairs if p["name"] == name and p["corpus"] == corpus and p["twin"] == twin]
    return target, [p for p in pairs if p["name"] == target and p["same"]]


def detail_lines(target: str, memory_dir: str) -> List[str]:
    """The evidence ``tend next``/``show`` prints: where each text lives, and the texts
    (a drift item as a unified diff, this corpus's side first). Corpus text is data."""
    from .outbox import effective_path

    name, pairs = _resolve(target, memory_dir)
    mine = _read(effective_path(memory_dir, name))
    if mine is None or not pairs:
        return []
    lines = [f"here: {name}.md"]
    for p in pairs:
        how = "a different text" if target.startswith("drift:") else "the same text"
        lines.append(f"{p['corpus']}: {p['twin']}.md, {how} (similarity {p['score']:.2f})")
    refs = _referrers(name, memory_dir)
    if refs:
        shown = ", ".join(refs[:8]) + (f" (+{len(refs) - 8} more)" if len(refs) > 8 else "")
        lines.append(f"linked from {len(refs)} memory(ies) here: {shown}; after a promote those "
                     "links resolve to the user tier")
    lines.append("")
    if target.startswith("drift:"):
        p = pairs[0]
        theirs = _read(os.path.join(p["memory_dir"], f"{p['twin']}.md"))
        if theirs is None:
            return lines + [f"(the {p['corpus']} side could not be read)"]
        body = list(difflib.unified_diff(
            _shown(mine), _shown(theirs), fromfile=f"here/{name}.md",
            tofile=f"{p['corpus']}/{p['twin']}.md", lineterm="", n=2,
        ))
    else:
        body = _shown(mine)
    more = len(body) - _MAX_DETAIL_LINES
    return lines + body[:_MAX_DETAIL_LINES] + ([f"(… {more} more line(s))"] if more > 0 else [])


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #
def _referrers(name: str, memory_dir: str) -> Optional[List[str]]:
    """The memories here that link to ``name`` (None when the graph cannot be built)."""
    try:
        from .archive import _inbound_referrers

        return _inbound_referrers(name, memory_dir)
    except Exception:
        return None


def _promote(name: str, memory_dir: str, repo_root: str) -> dict:
    """The Promote flow's writer, its guards intact but one: inbound links. The item's
    evidence names every memory here that links to this one, so the yes covers them, and
    after the move those links resolve to the user tier (recall fuses it; the link lint
    and OWN-1's link cache read them as cross-tier, not dangling). An unbuildable link
    graph still fails closed, and a consequential-default finding still refuses."""
    from .new_memory import promote_memory

    force = _referrers(name, memory_dir) is not None
    r = promote_memory(name, memory_dir=memory_dir, repo_root=repo_root, dest_tier="user", force=force)
    if not r.get("promoted"):
        return {"ok": False, "message": (
            f"{name} was not promoted: {r.get('error') or 'refused'}. Nothing moved. A lift that "
            "needs a per-finding yes, a new name or force goes through the Promote flow of /hippo:share."
        )}
    note = "; ".join(r.get("warnings") or [])
    return {"ok": True, "message": (
        f"promoted {name} to the user tier ({r.get('to')}), stamped {r.get('origin')}. The project "
        "copy is removed (staged with git rm when it was tracked; commit it)"
        + ("; its floor pointer went too" if r.get("floor_removed") else "")
        + (f"; {len(r['referrers'])} link(s) here now resolve to the user tier" if r.get("referrers") else "")
        + "."
        + (f" {note}" if note else "")
    )}


def _with_text_of(mine: str, theirs: str) -> Optional[str]:
    """``mine`` with ``theirs``' description and body; every other key kept byte-identical
    (the COR-9 primitives, guarded by ``_frontmatter_damage``). None when unsafe."""
    from .provenance import (
        _frontmatter_damage, parse_frontmatter, split_frontmatter, strip_frontmatter_keys,
    )

    desc = parse_frontmatter(theirs).get("description")
    if not isinstance(desc, str) or not desc.strip():
        return None
    stripped = strip_frontmatter_keys(mine, _DESCRIPTION_KEY_RE)
    fm, _ = split_frontmatter(stripped)
    if not fm:
        return None
    head = 1 if fm[0].startswith("name:") else 0
    fm = fm[:head] + [f"description: {json.dumps(desc, ensure_ascii=False)}"] + fm[head:]
    body = split_frontmatter(theirs)[1]
    new = "---\n" + "\n".join(fm) + "\n---\n" + body.lstrip("\n")
    if _frontmatter_damage(mine, new, {"description"}) or parse_frontmatter(new).get("description") != desc:
        return None
    return new


def _adopt(name: str, pair: dict, memory_dir: str, repo_root: str) -> dict:
    from .atomic import CasConflict, read_text_cas, write_text_cas
    from .outbox_edit import edit_path, resolve
    from .trust import record_authored_write_disclosing

    theirs = _read(os.path.join(pair["memory_dir"], f"{pair['twin']}.md"))
    if theirs is None:
        return {"ok": False, "message": f"the {pair['corpus']} side ({pair['twin']}.md) could not be read."}
    path = os.path.join(memory_dir, f"{name}.md")
    try:
        mine, token = read_text_cas(resolve(path))
    except Exception as exc:
        return {"ok": False, "message": f"{name}.md could not be read: {exc}"}
    new = _with_text_of(mine, theirs)
    if new is None:
        return {"ok": False, "message": f"{name}.md was left as it is: its frontmatter could not take the "
                                        "other text without touching keys this verdict does not own."}
    target, why = edit_path(path, repo_root, memory_dir)
    if why or not target:
        return {"ok": False, "message": why or "no path to write"}
    try:
        write_text_cas(target, new, token)
    except CasConflict:
        return {"ok": False, "message": f"{name}.md changed while this ran; nothing was written. Show it again."}
    except Exception as exc:
        return {"ok": False, "message": f"{name}.md was not written: {exc}"}
    note = record_authored_write_disclosing(memory_dir, target, repo_root)
    if os.path.dirname(os.path.abspath(target)) == os.path.abspath(memory_dir):
        try:
            from .build_index import refresh_index

            refresh_index(memory_dir)
        except Exception:
            pass
    where = "its outbox copy" if target != path else f"{name}.md"
    return {"ok": True, "consent_note": note, "message": (
        f"adopted {pair['corpus']}'s text ({pair['twin']}.md) into {where}; its name, type and "
        f"provenance are its own. {pair['corpus']} itself is unchanged: tend it there."
    )}


def apply_verdict(target: str, verdict: str, memory_dir: str, repo_root: str) -> dict:
    """ONE verdict on ONE promote item -> ``{"ok", "message"[, "consent_note"]}``.
    ``distinct`` is recorded by the caller (a skip). Never raises."""
    try:
        drift = target.startswith("drift:")
        name, pairs = _resolve(target, memory_dir)
        if not pairs:
            return {"ok": False, "message": f"{target} is no longer found by the scan."}
        if verdict == "adopt" and not drift:
            return {"ok": False, "message": "adopt is for a rule that reads differently; this text is already the same."}
        if verdict == "distinct":
            return {"ok": True, "message": f"{target}: recorded as two different rules."}
        if verdict == "promote" and pairs[0].get("machine_local"):
            return {"ok": False, "message": f"{name} is kept on this machine only; it is not proposed for the user tier."}
        pair = pairs[0]
        if (pair["root"], pair["memory_dir"]) not in {(r, m) for _l, r, m in _others(memory_dir)}:
            return {"ok": False, "message": f"{pair['corpus']} is no longer a registered, trusted corpus."}
        if verdict == "adopt":
            return _adopt(name, pair, memory_dir, repo_root)
        return _promote(name, memory_dir, repo_root)
    except Exception as exc:
        return {"ok": False, "message": f"{target}: {type(exc).__name__}: {exc}"}
