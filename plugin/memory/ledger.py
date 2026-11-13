"""LDG-1: the external-ledger read-time join — a tracker id's status, rendered at read time.

A corpus that cites tracker ids (``GRO-901``) declares the committed file that mirrors its
tracker, and recall rows that cite an id gain the id's status from that file, stamped with
the file's date, so a status word never has to be typed into a memory again:

    "ledgers": [{"name": "linear", "path": "LINEAR-SNAPSHOT.yaml",
                 "id_re": "\\bGRO-\\d+\\b", "entry_key": "id", "status_key": "state"}]

in ``hippo.json`` (read with ``provenance_format.read_policy_key``, so the legacy ``.format``
spelling works too). It DECLARES data and is read directly, without ``hippo trust policy``
consent: it can only add one bounded, dated suffix to rows this machine already consented to.

Direction A of ``LDG-1.spec.md``: a derived cache, ``<index_dir>/ledger.json``, built off the
hot path (SessionStart, beside the JIT touch map, and ``hippo build-index``) and only for a
trusted corpus. Render sites do dict lookups against it. Never a network call, never a write
to a tracker or to a memory file. Absent cache: one failed stat, byte-identical output.

Bounds, each tested (``tests/test_ledger.py``):

  - ``path`` is toplevel-relative and must resolve inside the repo after symlink
    resolution (absolute paths, ``..`` and a symlink that leaves the tree are dropped);
  - the file is read by a LINE-WINDOW contract, no YAML parse: an entry begins at a line
    whose key is ``<entry_key>:`` (a list item may carry a leading ``- ``) with a value that
    fully matches ``id_re``; its status is the first ``<status_key>:`` line at that key's
    column before the entry's window closes (the next line indented left of that column,
    a sibling list item included). No other line is read;
  - a status is cut to one line, stripped of control characters and capped at 40 chars;
  - an uncompilable ``id_re``, an unreadable file or a malformed entry drops that ledger
    (garbled degrades to absent) and doctor names why.

``id_re`` comes from a corpus file and runs on the hot path against row text (and, for
GP-S4, against the prompt), so it is bounded: at most 120 characters, compiled once per
process, and vetted when the cache is built — no nested repetition, no alternation inside
a repetition, no backreference or lookaround, at most three repeats. Python's ``re`` has no
timeout, so the vetting is what keeps the backtracking polynomial with a small degree, and
the scan keeps the input short: it runs per whitespace-delimited token (tokens over 128
chars are skipped) over at most the first 1,000 chars of a row (4,000 of a prompt). An id is
therefore one token: an ``id_re`` that needs a space never matches.

The cache keeps a ``prev`` map — each id's status before its latest observed change — so
LDG-2 can arm on a terminal status later. Nothing else of LDG-2 is built here.

Stdlib only at import time: the prompt hook imports this module.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

CACHE_NAME = "ledger.json"
SCHEMA_VERSION = 1
POLICY_KEY = "ledgers"

MAX_LEDGERS = 4
MAX_PATTERN_CHARS = 120
MAX_REPEATS = 3
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_LINE_CHARS = 2000
MAX_IDS_PER_LEDGER = 50000
STATUS_CHARS = 40
TOKEN_CHARS = 128
ROW_SCAN_CHARS = 1000
PROMPT_SCAN_CHARS = 4000
IDS_PER_ROW = 2

_KEY_RE = re.compile(r"[A-Za-z_][\w\-]{0,39}\Z")
_NAME_RE = re.compile(r"[\w.\-]{1,40}\Z")
_TOKEN = re.compile(r"\S+")
# Control characters and the bidi/zero-width controls a status could smuggle.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #
def vet_pattern(pattern) -> Optional[str]:
    """Why ``pattern`` may not run on the hot path, or None when it may."""
    if not isinstance(pattern, str) or not pattern:
        return "id_re is not a non-empty string"
    if len(pattern) > MAX_PATTERN_CHARS:
        return f"id_re is longer than {MAX_PATTERN_CHARS} characters"
    try:
        re.compile(pattern)
    except (re.error, RecursionError, OverflowError):
        return "id_re does not compile"
    try:
        try:
            from re import _parser as sp  # Python 3.11+
            from re._constants import MAXREPEAT
        except ImportError:  # pragma: no cover - older interpreters
            import sre_parse as sp  # type: ignore
            from sre_constants import MAXREPEAT  # type: ignore
        tree = sp.parse(pattern)
    except Exception:
        return "id_re could not be checked"
    repeats = [0]

    def walk(items, in_repeat: bool) -> Optional[str]:
        for op, av in items:
            name = str(op)
            if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
                _lo, hi, sub = av
                many = hi is MAXREPEAT or hi > 1
                if many:
                    if in_repeat:
                        return "id_re nests one repetition inside another"
                    repeats[0] += 1
                why = walk(sub, in_repeat or many)
                if why:
                    return why
            elif name == "SUBPATTERN":
                why = walk(av[-1], in_repeat)
                if why:
                    return why
            elif name == "ATOMIC_GROUP":
                why = walk(av, in_repeat)
                if why:
                    return why
            elif name == "BRANCH":
                if in_repeat:
                    return "id_re repeats an alternation"
                for alt in av[1]:
                    why = walk(alt, in_repeat)
                    if why:
                        return why
            elif name in ("GROUPREF", "GROUPREF_EXISTS", "ASSERT", "ASSERT_NOT"):
                return "id_re uses a backreference or a lookaround"
        return None

    why = walk(tree, False)
    if why:
        return why
    if repeats[0] > MAX_REPEATS:
        return f"id_re has more than {MAX_REPEATS} repeats"
    return None


def corpus_top(memory_dir: str, repo_root: Optional[str] = None) -> str:
    """The repo toplevel a ledger ``path`` is relative to: the git toplevel when there is
    one, else ``repo_root``, else the dir holding ``.claude/``."""
    try:
        from .trust import gate_repo_root

        top = gate_repo_root(memory_dir, repo_root)
        if top:
            return top
    except Exception:
        pass
    if repo_root:
        return repo_root
    md = os.path.abspath(memory_dir)
    parent = os.path.dirname(md)
    return os.path.dirname(parent) if os.path.basename(parent) == ".claude" else parent


def resolve_path(top: str, rel) -> Tuple[Optional[str], Optional[str]]:
    """``(absolute path, None)`` for a ledger path inside ``top``, else ``(None, why)``."""
    if not isinstance(rel, str) or not rel.strip():
        return None, "path is not a non-empty string"
    rel = rel.strip()
    if os.path.isabs(rel) or rel.startswith(("~", "\\")):
        return None, "path is absolute"
    parts = re.split(r"[\\/]+", rel)
    if ".." in parts:
        return None, "path climbs out of the repo"
    real_top = os.path.realpath(top)
    full = os.path.realpath(os.path.join(real_top, rel))
    if not full.startswith(real_top.rstrip(os.sep) + os.sep):
        return None, "path resolves outside the repo"
    if not os.path.isfile(full):
        return None, "path is not a readable file"
    return full, None


def declared(memory_dir: str) -> bool:
    """True when the corpus declares a ``ledgers`` key at all (doctor's line, the verb)."""
    try:
        from .provenance_format import read_policy_key

        return read_policy_key(memory_dir, POLICY_KEY) is not None
    except Exception:
        return False


def read_declaration(memory_dir: str, top: str) -> Tuple[List[dict], List[dict]]:
    """``(ledgers, dropped)``: each usable ledger as ``{name, path, abs, id_re,
    entry_key, status_key}``, and each dropped one as ``{name, reason}``. Reads no
    ledger file (a stat only). Never raises."""
    try:
        from .provenance_format import read_policy_key

        raw = read_policy_key(memory_dir, POLICY_KEY)
    except Exception:
        raw = None
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [{"name": "ledgers", "reason": "ledgers is not a list"}]
    out: List[dict] = []
    dropped: List[dict] = []
    seen = set()
    for pos, item in enumerate(raw):
        label = f"#{pos + 1}"
        if not isinstance(item, dict):
            dropped.append({"name": label, "reason": "entry is not an object"})
            continue
        name = item.get("name", label)
        if not isinstance(name, str) or not _NAME_RE.match(name) or name in seen:
            dropped.append({"name": label, "reason": "name is missing, malformed or repeated"})
            continue
        if len(out) >= MAX_LEDGERS:
            dropped.append({"name": name, "reason": f"more than {MAX_LEDGERS} ledgers declared"})
            continue
        keys = {}
        bad = None
        for field, default in (("entry_key", "id"), ("status_key", "status")):
            val = item.get(field, default)
            if not isinstance(val, str) or not _KEY_RE.match(val):
                bad = f"{field} is not a plain key"
            keys[field] = val
        why = bad or vet_pattern(item.get("id_re"))
        full = None
        if not why:
            full, why = resolve_path(top, item.get("path"))
        if why:
            dropped.append({"name": name, "reason": why})
            continue
        seen.add(name)
        out.append({
            "name": name, "path": item["path"].strip(), "abs": full, "id_re": item["id_re"], **keys,
        })
    return out, dropped


# --------------------------------------------------------------------------- #
# The line-window scan (cache build only — never the hot path)
# --------------------------------------------------------------------------- #
def _value(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    cut = v.find(" #")
    return (v[:cut] if cut >= 0 else v).strip()


def clean_status(raw: str) -> str:
    """One line, no control characters, at most ``STATUS_CHARS`` chars."""
    s = (raw or "").splitlines()[0] if raw else ""
    s = " ".join(_CONTROL.sub(" ", s).split())
    return s[:STATUS_CHARS].rstrip()


def scan_ledger(path: str, id_re: str, entry_key: str, status_key: str) -> Dict[str, str]:
    """``{id: status}`` read from ``path`` by the line-window contract. Raises on an
    unreadable or oversized file (the caller drops the ledger)."""
    rx = re.compile(id_re)
    line_rx = re.compile(
        r"^(?P<ind>[ ]*)(?:-[ ]+)?(?P<key>" + re.escape(entry_key) + "|" + re.escape(status_key)
        + r")[ ]*:(?P<val>.*)$"
    )
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise ValueError("ledger file is too large")
    out: Dict[str, str] = {}
    cur: Optional[str] = None
    col = -1
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if len(line) > MAX_LINE_CHARS:
                continue
            body = line.rstrip("\n").rstrip("\r")
            stripped = body.lstrip(" ")
            if not stripped or stripped.startswith("#"):
                continue
            if cur is not None and len(body) - len(stripped) < col:
                cur = None  # the entry's window closed (a sibling item or a parent key)
            if entry_key not in stripped and status_key not in stripped:
                continue
            m = line_rx.match(body)
            if not m:
                continue
            key, kcol = m.group("key"), m.start("key")
            if key == entry_key:
                val = _value(m.group("val"))
                if rx.fullmatch(val):
                    cur, col = (val if val not in out else None), kcol
                    if cur is not None and len(out) >= MAX_IDS_PER_LEDGER:
                        raise ValueError("ledger holds too many ids")
                    continue
                if key != status_key:
                    continue
            if key == status_key and cur is not None and kcol == col:
                status = clean_status(_value(m.group("val")))
                if status:
                    out[cur] = status
                cur = None
    return out


def snapshot_date(top: str, ledger: dict) -> str:
    """The ledger file's last commit date (one git call); its mtime date outside git."""
    try:
        from .provenance import run_git

        rel = os.path.relpath(ledger["abs"], os.path.realpath(top))
        out = run_git(["log", "-1", "--format=%cs", "--", rel], top).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", out):
            return out
    except Exception:
        pass
    import datetime

    ts = os.path.getmtime(ledger["abs"])
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# The derived cache (SessionStart and build-index write; render sites read)
# --------------------------------------------------------------------------- #
def cache_path(index_dir: str) -> str:
    return os.path.join(index_dir, CACHE_NAME)


def _signature(memory_dir: str, ledgers: List[dict], extra: Iterable[str] = ()) -> str:
    import hashlib

    h = hashlib.sha256()
    decl = [{k: v for k, v in lg.items() if k != "abs"} for lg in ledgers]
    h.update(json.dumps(decl, sort_keys=True).encode("utf-8"))
    for lg in ledgers:
        st = os.stat(lg["abs"])
        h.update(f"|{lg['abs']}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8"))
    for item in extra:
        h.update(f"|{item}".encode("utf-8"))
    return h.hexdigest()[:32]


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) and doc.get("schema_version") == SCHEMA_VERSION else None
    except Exception:
        return None


def build_cache(memory_dir: str, top: str, ledgers: List[dict], dropped: List[dict],
                previous: Optional[dict] = None) -> dict:
    """The cache document for ``ledgers``. A ledger whose file cannot be read or holds no
    entry is moved to ``dropped``. Never raises."""
    states: List[list] = []
    state_ix: Dict[Tuple[str, int], int] = {}
    ids: Dict[str, int] = {}
    meta: List[dict] = []
    dropped = list(dropped)
    for lg in ledgers:
        try:
            found = scan_ledger(lg["abs"], lg["id_re"], lg["entry_key"], lg["status_key"])
        except Exception as exc:
            dropped.append({"name": lg["name"], "reason": f"the file could not be read ({type(exc).__name__})"})
            continue
        if not found:
            dropped.append({"name": lg["name"], "reason": "no entry matched the declared keys and id_re"})
            continue
        li = len(meta)
        meta.append({"name": lg["name"], "path": lg["path"], "id_re": lg["id_re"],
                     "as_of": snapshot_date(top, lg), "ids": 0})
        for ident, status in found.items():
            if ident in ids or any(ch.isspace() for ch in ident):
                continue  # an earlier ledger answers for it, or it could never be one token
            key = (status, li)
            if key not in state_ix:
                state_ix[key] = len(states)
                states.append([status, li])
            ids[ident] = state_ix[key]
            meta[li]["ids"] += 1
    prev: Dict[str, str] = {}
    if previous:
        old = LedgerCache(previous)
        old_prev = previous.get("prev") or {}
        for ident, (was, _li) in old.id_map().items():
            if ident not in ids:
                continue
            now = states[ids[ident]][0]
            if was != now:
                prev[ident] = was
            elif ident in old_prev:
                prev[ident] = old_prev[ident]
    # The ids are stored as one space-delimited string per (status, ledger), not as a map:
    # a 2,400-id map costs the hook ~0.9 ms to parse, these strings ~0.1 ms, and a lookup is
    # a substring test per status (LDG-1's measured hot-path shape).
    by_state = [[] for _ in states]
    for ident, si in ids.items():
        by_state[si].append(ident)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "ledgers": meta,
        "dropped": dropped,
        "states": states,
        "by_state": [" " + " ".join(sorted(v)) + " " for v in by_state],
        "prev": prev,
    }
    if meta:
        cites = journal_cites(memory_dir, LedgerCache(doc))
        if cites:
            doc["cites"] = cites
    return doc


# --------------------------------------------------------------------------- #
# GP-S4: the issue join's reverse map, built with the cache (the corpus is read then)
# --------------------------------------------------------------------------- #
JOURNALS_PER_ID = 8
JOURNAL_SCAN_CHARS = 256 * 1024


def journal_files(memory_dir: str) -> List[str]:
    """The corpus's journal files (lane ``journal``), only when it declares ``journals``
    (the lanes act only then, so nothing else needs the map). Never raises."""
    try:
        from .lanes import JOURNAL, journal_globs, journals_declared, lane_of
        from .provenance import _iter_memory_files

        if not journals_declared(memory_dir):
            return []
        globs = journal_globs(memory_dir)
        return [p for p in _iter_memory_files(memory_dir) if lane_of(os.path.basename(p), None, globs) == JOURNAL]
    except Exception:
        return []


def journal_cites(memory_dir: str, cache: "LedgerCache") -> str:
    """``"\\n<ID> <journal> <journal>…\\n"`` for every id a journal cites (any match of a
    declared ``id_re``, in the ledger or not), at most ``JOURNALS_PER_ID`` journals per id,
    the ones citing it most first (then the later name). One line per id, so the hook finds
    an id with one substring search instead of parsing a map."""
    found: Dict[str, Dict[str, int]] = {}
    for path in journal_files(memory_dir):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(JOURNAL_SCAN_CHARS)
        except Exception:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        if any(ch.isspace() for ch in name):
            continue
        for ident in cache.find_ids(text, cap=JOURNAL_SCAN_CHARS, unique=False):
            per = found.setdefault(ident, {})
            per[name] = per.get(name, 0) + 1
    lines = []
    for ident in sorted(found):
        by_name = sorted(found[ident].items(), reverse=True)  # later (newer-dated) names first
        top = sorted(by_name, key=lambda kv: -kv[1])[:JOURNALS_PER_ID]  # stable: count wins
        lines.append(ident + " " + " ".join(n for n, _ in top))
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


def write_cache(index_dir: str, doc: dict) -> bool:
    """Persist the cache atomically (a reader sees the old map or the new one). Never
    raises; False on failure — the caller treats that as "not refreshed"."""
    try:
        os.makedirs(index_dir, exist_ok=True)
        from .atomic import write_json_atomic

        write_json_atomic(cache_path(index_dir), doc, indent=None)
        return True
    except Exception:
        return False


def refresh_ledger_cache(memory_dir: str, index_dir: str, repo_root: Optional[str] = None) -> Optional[bool]:
    """Build and persist ``<index_dir>/ledger.json`` when the corpus declares a ledger.

    None when nothing is declared (an old cache is removed, so render sites go back to one
    failed stat) or the corpus is untrusted here (the declaration must not direct hippo to
    read any file then). True when the cache is fresh — unchanged inputs skip the rebuild —
    False when the write failed. Never raises."""
    try:
        path = cache_path(index_dir)
        if not declared(memory_dir):
            if os.path.exists(path):
                os.remove(path)
            return None
        from . import trust

        gate = trust.gate_repo_root(memory_dir, repo_root)
        if gate is not None and not trust.is_trusted(gate):
            return None
        top = gate or corpus_top(memory_dir, repo_root)
        ledgers, dropped = read_declaration(memory_dir, top)
        from .lanes import lane_state

        journals = [json.dumps(lane_state(memory_dir), sort_keys=True)]  # GP-S4: the issue join's
        # map changes when the journals declaration or a journal file does
        for p in journal_files(memory_dir):
            st = os.stat(p)
            journals.append(f"{os.path.basename(p)}:{st.st_size}:{st.st_mtime_ns}")
        sig = _signature(memory_dir, ledgers, [json.dumps(dropped, sort_keys=True), *journals])
        previous = _read_json(path)
        if previous is not None and previous.get("signature") == sig:
            return True
        doc = build_cache(memory_dir, top, ledgers, dropped, previous)
        doc["signature"] = sig
        return write_cache(index_dir, doc)
    except Exception:
        return False


def summary(index_dir: str) -> str:
    """One plain line about the cache: ids held per ledger with the snapshot date, then
    every dropped ledger with why (``hippo build-index`` and doctor print it)."""
    doc = _read_json(cache_path(index_dir))
    if doc is None:
        return "no cache built (the corpus is not trusted here, or the write failed)"
    parts = [
        f"{m.get('name')}: {m.get('ids', 0)} ids, snapshot {m.get('as_of')}"
        for m in doc.get("ledgers") or [] if isinstance(m, dict)
    ]
    parts += [f"{d.get('name')} dropped: {d.get('reason')}" for d in doc.get("dropped") or [] if isinstance(d, dict)]
    return "; ".join(parts) or "nothing declared"


# --------------------------------------------------------------------------- #
# The hot path: one stat, one JSON read per process, dict lookups
# --------------------------------------------------------------------------- #
_MEMO: Dict[str, Tuple[tuple, "LedgerCache"]] = {}
_RX_MEMO: Dict[str, Optional["re.Pattern"]] = {}


def _compiled(pattern: str):
    if pattern not in _RX_MEMO:
        try:
            _RX_MEMO[pattern] = re.compile(pattern) if len(pattern) <= MAX_PATTERN_CHARS else None
        except re.error:
            _RX_MEMO[pattern] = None
    return _RX_MEMO[pattern]


class LedgerCache:
    """A loaded ``ledger.json``: status lookups and the bounded id scan."""

    def __init__(self, doc: dict) -> None:
        self.ledgers: List[dict] = [m for m in doc.get("ledgers") or [] if isinstance(m, dict)]
        states = doc.get("states") or []
        blobs = doc.get("by_state") or []
        self.states: List[Tuple[str, int, str]] = [
            (str(s[0]), s[1] if isinstance(s[1], int) else -1, b if isinstance(b, str) else "")
            for s, b in zip(states, blobs)
            if isinstance(s, list) and len(s) == 2
        ]
        self.dropped: List[dict] = doc.get("dropped") or []
        self.doc = doc
        self._rxs = None

    def id_map(self) -> Dict[str, Tuple[str, int]]:
        """``{id: (status, ledger index)}`` — the whole map, for build-time and the verb."""
        out: Dict[str, Tuple[str, int]] = {}
        for status, li, blob in self.states:
            for ident in blob.split():
                out.setdefault(ident, (status, li))
        return out

    def patterns(self) -> list:
        if self._rxs is None:
            rxs = []
            for m in self.ledgers:
                rx = _compiled(m.get("id_re") or "")
                if rx is not None and rx not in rxs:
                    rxs.append(rx)
            self._rxs = rxs
        return self._rxs

    def find_ids(self, text: str, cap: int = ROW_SCAN_CHARS, unique: bool = True) -> List[str]:
        """Every id ``text`` cites, in order of appearance (deduped unless ``unique`` is
        False): the declared patterns run per whitespace token over the first ``cap`` chars
        (see the module docstring)."""
        rxs = self.patterns()
        if not rxs or not text:
            return []
        out: List[str] = []
        seen = set()
        for tok in _TOKEN.finditer(text[:cap]):
            t = tok.group(0)
            if len(t) > TOKEN_CHARS:
                continue
            hits = sorted((m.start(), m.group(0)) for rx in rxs for m in rx.finditer(t))
            for _pos, ident in hits:
                if ident and not (unique and ident in seen):
                    seen.add(ident)
                    out.append(ident)
        return out

    def journals_for(self, prompt: str) -> Dict[str, str]:
        """GP-S4: ``{lowercased journal name: id}`` for the journals that cite an id the
        prompt names (the first id wins when several admit one journal)."""
        cites = self.doc.get("cites")
        if not isinstance(cites, str) or not cites:
            return {}
        out: Dict[str, str] = {}
        for ident in self.find_ids(prompt, cap=PROMPT_SCAN_CHARS):
            at = cites.find("\n" + ident + " ")
            if at < 0:
                continue
            end = cites.find("\n", at + 1)
            for name in cites[at + 1 + len(ident): end if end >= 0 else None].split():
                out.setdefault(name.lower(), ident)
        return out

    def status(self, ident: str) -> Optional[Tuple[str, str, str]]:
        """``(status, as_of, ledger name)`` for a known id, else None."""
        if not ident or any(ch.isspace() for ch in ident):
            return None
        probe = f" {ident} "
        for status, li, blob in self.states:
            if probe in blob:
                meta = self.ledgers[li] if 0 <= li < len(self.ledgers) else {}
                return status, str(meta.get("as_of") or ""), str(meta.get("name") or "")
        return None

    def suffix(self, text: str) -> str:
        """`` · <ID> <status> (snapshot <date>)`` for the first ``IDS_PER_ROW`` known ids
        ``text`` cites, in order of appearance; "" when it cites none."""
        parts = []
        for ident in self.find_ids(text):
            st = self.status(ident)
            if st is None:
                continue
            parts.append(f" · {ident} {st[0]} (snapshot {st[1]})")
            if len(parts) >= IDS_PER_ROW:
                break
        return "".join(parts)


def load(index_dir: Optional[str]) -> Optional[LedgerCache]:
    """The cache under ``index_dir``, or None. One stat per call; the JSON is read once per
    process and again only when the file changes (the warm path is a long-lived process)."""
    if not index_dir:
        return None
    path = cache_path(index_dir)
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = (st.st_size, st.st_mtime_ns, st.st_ino)
    hit = _MEMO.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1]
    doc = _read_json(path)
    cache = LedgerCache(doc) if doc is not None else None
    if cache is not None:
        _MEMO[path] = (sig, cache)
    return cache


def annotate(rows: List[dict], cache: Optional[LedgerCache], render=None) -> None:
    """Set ``row["ledger"]`` (the suffix) on each project-tier memory row whose rendered
    text — its name and ``render(description)`` — cites a known id. No cache, no change."""
    if cache is None or not rows:
        return
    for r in rows:
        if r.get("corpus") not in (None, "project") or not r.get("name"):
            continue
        desc = r.get("description") or ""
        text = f"{r['name']} {render(desc) if render else desc}"
        s = cache.suffix(text)
        if s:
            r["ledger"] = s
