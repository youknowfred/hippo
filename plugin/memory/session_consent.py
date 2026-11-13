"""OUT-5: consent for the bytes a session's own file tool wrote on this machine (opt-in).

Claude Code's native auto-memory directory (``~/.claude/projects/<key>/memory``) is, once
hippo's setup wires it, a symlink into the primary checkout's ``.claude/memory``. A
session's own Write or Edit of a memory file therefore lands in a trusted corpus's working
tree with no consent recorded, and hippo's recall withholds it until ``hippo trust review``
and a grant. A corpus can opt out of that review for exactly those bytes:

    .claude/memory/hippo.json: {"consent": {"session_writes": true}}

``consent`` is consent-sensitive (``policy_consent.SENSITIVE_KEYS``): it is honoured on a
machine only after ``hippo trust policy --accept`` there. Without it, or declared but not
accepted, nothing here writes anything: no note, no grant, no telemetry.

Two halves, one per hook, both riding the Python start the hooks already make for a
memory-file write (no new interpreter start anywhere; a touch outside a memory dir never
reaches this module):

- **PreToolUse** (``note_before_write``, from ``gate_hook.main``): for a Write, Edit or
  MultiEdit of a top-level memory file of a corpus this session works in, record a small
  machine-local note in the telemetry dir (gitignored), one per session and real path:
  the real path, whether the file was ABSENT, CONSENTED (its bytes hash to the consented
  hash) or UNCONSENTED before the write, and the sha256 of the bytes the tool call will
  leave (``gate_hook.simulate``, encoded as UTF-8). When the result cannot be known, no note.
- **PostToolUse** (``grant_after_write``, from ``outcome.main``): for the same file, consume
  the note and fold the bytes on disk into the consent record (``trust.record_session_write``)
  only when every condition still holds: the key is honoured; the real path is a top-level
  ``.md`` of an already-trusted corpus with a fingerprint baseline; the note is this
  session's, for this real path, and fresh; its prior state is ABSENT or CONSENTED; the
  bytes on disk hash to what the note predicted; note and payload carry the same
  ``tool_use_id`` (or neither carries one); the call was not made inside a subagent (the
  payload names no ``agent_id``); and the bytes pass the lints a review gates on (secrets,
  tier-A threats), else the file is held for review and counted. Anything else grants
  nothing and says nothing. Bytes that arrive by pull, merge, checkout or a Bash redirect never pass
  through here, so they stay withheld.

Hippo's gate governs only what hippo's own recall injects. Claude Code's native loader
reads these files with no gate at all; this changes nothing about that.

Every entry point never raises and prints nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import List, Optional, Tuple

KEY = "consent"
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
NOTE_DIRNAME = "session-writes"
NOTE_TTL_S = 15 * 60  # a pre-note older than this is stale: the tool call it predicted is long gone
MAX_NOTES = 64  # notes are consumed by the post hook; this bounds what a crashed one leaves
ABSENT, CONSENTED, UNCONSENTED = "absent", "consented", "unconsented"
_SID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def enabled(corpus_dir: str, repo_root: Optional[str] = None) -> bool:
    """True when ``corpus_dir``'s hippo.json sets ``consent.session_writes`` to true and
    this machine accepted that value. One small JSON read each; never raises."""
    try:
        from .policy_consent import honoured

        v = honoured(corpus_dir, KEY, repo_root)
        return isinstance(v, dict) and v.get("session_writes") is True
    except Exception:
        return False


def _owned_dirs(memory_dir: Optional[str], tree: Optional[str]) -> List[str]:
    """The real corpus dirs a session working in ``tree`` may consent into: the resolved
    corpus, the tree's own ``.claude/memory``, and its main checkout's when ``tree`` is a
    linked worktree (where the native auto-memory symlink points)."""
    out: List[str] = []
    cands = [memory_dir]
    if tree:
        cands.append(os.path.join(tree, ".claude", "memory"))
        try:
            from .provenance_env import main_worktree_root

            main = main_worktree_root(tree)
            if main:
                cands.append(os.path.join(main, ".claude", "memory"))
        except Exception:
            pass
    for c in cands:
        if c and os.path.isdir(c):
            r = os.path.realpath(c)
            if r not in out:
                out.append(r)
    return out


def resolve(file_path, memory_dir: Optional[str], tree: Optional[str]) -> Optional[Tuple[str, str, str]]:
    """``(corpus_dir, filename, real_path)`` when ``file_path`` names a top-level memory
    file of a corpus this session works in, else None.

    Refused: a relative path or one with a ``..`` segment; a named path whose own parent
    is not a ``memory`` dir; a real path (symlinks resolved) that leaves that corpus dir or
    sits in a subdirectory of it; anything that is not a memory file (``MEMORY.md`` and
    the docs files are the harness's)."""
    if not isinstance(file_path, str) or not os.path.isabs(file_path) or "\x00" in file_path:
        return None
    if ".." in file_path.split(os.sep):
        return None
    if os.path.basename(os.path.dirname(file_path)) != "memory":
        return None
    from .provenance import _is_memory_filename

    real = os.path.realpath(file_path)
    cdir, name = os.path.split(real)
    if not _is_memory_filename(name) or cdir not in _owned_dirs(memory_dir, tree):
        return None
    return cdir, name, real


def note_path(telemetry_dir: str, session_id, real_path: str) -> Optional[str]:
    """Where the note for one session and real path lives, or None for a session id the
    note store will not name a file after."""
    if not isinstance(session_id, str) or not _SID_RE.match(session_id) or not telemetry_dir:
        return None
    digest = hashlib.sha256(real_path.encode("utf-8", "surrogateescape")).hexdigest()[:24]
    return os.path.join(telemetry_dir, NOTE_DIRNAME, f"{session_id}-{digest}.json")


def _context(payload, memory_dir, repo_root, tree):
    """``(tool, tool_input, session_id, memory_dir, tree)`` for a file-write payload."""
    if not isinstance(payload, dict) or payload.get("tool_name") not in WRITE_TOOLS:
        return None
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return None
    from .provenance_env import launch_root, resolve_dirs

    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    return payload["tool_name"], ti, payload.get("session_id"), memory_dir, tree or repo_root or launch_root()


def _telemetry_dir(memory_dir: str) -> str:
    from .telemetry_store import default_telemetry_dir

    return default_telemetry_dir(memory_dir)


def _write_note(path: str, note: dict) -> bool:
    """Write one note (atomic), then keep the store bounded. Silent by design (crash class
    intact): a lost note costs one grant, which the review still offers."""
    try:
        from .atomic import write_json_atomic

        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        write_json_atomic(path, note, sort_keys=True)
        _prune(d)
        return True
    except Exception:
        return False


def _prune(d: str) -> None:
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
        if len(names) <= MAX_NOTES:
            return
        paths = sorted((os.path.join(d, n) for n in names), key=os.path.getmtime)
        for p in paths[: len(paths) - MAX_NOTES]:
            os.remove(p)
    except Exception:
        pass


def _call_id(payload) -> Tuple[bool, Optional[str]]:
    """``(ok, tool_use_id)``: the id the harness gave this tool call (None when it gave
    none); ``ok`` False for a present id that is not a non-empty string."""
    v = payload.get("tool_use_id")
    if v is None or v == "":
        return True, None
    return (True, v) if isinstance(v, str) else (False, None)


def _from_subagent(payload) -> bool:
    """A tool call made inside a subagent (the payload names its ``agent_id``). A harness
    that does not send ``agent_id`` cannot be told apart from the main session."""
    return bool(payload.get("agent_id"))


def note_before_write(payload, *, memory_dir: Optional[str] = None, repo_root: Optional[str] = None,
                      tree: Optional[str] = None) -> bool:
    """PreToolUse half: record the prior state and the predicted bytes. True when a note
    was written. Writes nothing unless the key is honoured for the real path's corpus and
    that corpus is trusted with a fingerprint baseline. Once a target resolves, any note
    already there for this session and real path is removed whenever no fresh one is
    written, so nothing stale or forged waits for the post hook."""
    try:
        ctx = _context(payload, memory_dir, repo_root, tree)
        if ctx is None:
            return False
        tool, ti, sid, memory_dir, tree = ctx
        target = resolve(ti.get("file_path"), memory_dir, tree)
        if target is None:
            return False
        np = note_path(_telemetry_dir(memory_dir), sid, target[2])
        if np is None:
            return False
        note = None
        try:
            note = _build_note(payload, tool, ti, sid, target)
        except Exception:
            note = None
        if note is not None and _write_note(np, note):
            return True
        try:
            os.remove(np)
        except OSError:
            pass
        return False
    except Exception:
        return False


def _build_note(payload, tool, ti, sid, target) -> Optional[dict]:
    cdir, name, real = target
    ok, call_id = _call_id(payload)
    if not ok or _from_subagent(payload):
        return None  # a subagent's write waits for review
    from . import trust

    gate_root = trust.gate_repo_root(cdir)
    if gate_root is None or not enabled(cdir, gate_root):
        return None
    base = trust.consented_hashes(gate_root)
    if base is None:
        return None  # untrusted, a legacy record, or the bypass: nothing could be granted
    prior_bytes = None
    if os.path.lexists(real):
        if not os.path.isfile(real):
            return None
        with open(real, "rb") as fh:
            prior_bytes = fh.read()
    before = None
    if prior_bytes is not None:
        try:
            before = prior_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None
    from .gate_hook import simulate

    after = simulate(tool, ti, before)
    if after is None:
        return None
    prior_sha = hashlib.sha256(prior_bytes).hexdigest() if prior_bytes is not None else None
    stem = os.path.splitext(name)[0]
    if prior_sha is None:
        prior = ABSENT
    elif base.get(stem) == prior_sha:
        prior = CONSENTED
    else:
        prior = UNCONSENTED
    return {
        "v": 1,
        "session_id": sid,
        "tool_use_id": call_id,
        "path": real,
        "tool": tool,
        "prior": prior,
        "prior_sha": prior_sha,
        "expect_sha": hashlib.sha256(after.encode("utf-8", "surrogatepass")).hexdigest(),
        "ts": time.time(),
    }


def _lint_findings(data: bytes) -> bool:
    """True when the bytes fail a lint a human review gates on (``review.GATE_LINTS``:
    secrets, and the tier-A threat classes), or are not text."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    from .secrets import scan_text
    from .threat_lint import scan_tier_a

    return bool(scan_text(text, entropy=False) or scan_tier_a(text))


def _take_note(np: str) -> Optional[dict]:
    """Read and remove one note (consumed whatever it says). None when absent or unreadable."""
    try:
        with open(np, "r", encoding="utf-8") as fh:
            note = json.load(fh)
    except Exception:
        note = None
    try:
        os.remove(np)
    except Exception:
        pass
    return note if isinstance(note, dict) else None


def grant_after_write(payload, *, memory_dir: Optional[str] = None, repo_root: Optional[str] = None,
                      tree: Optional[str] = None) -> bool:
    """PostToolUse half: consume this session's note for the file and, when every condition
    in the module docstring holds, fold the bytes on disk into the consent record. True
    when a grant was recorded. The first check is one stat of the note's path."""
    try:
        ctx = _context(payload, memory_dir, repo_root, tree)
        if ctx is None:
            return False
        _tool, ti, sid, memory_dir, tree = ctx
        fp = ti.get("file_path")
        if not isinstance(fp, str) or not os.path.isabs(fp):
            return False
        td = _telemetry_dir(memory_dir)
        np = note_path(td, sid, os.path.realpath(fp))
        if np is None or not os.path.isfile(np):
            return False
        note = _take_note(np)
        target = resolve(fp, memory_dir, tree)
        if note is None or target is None:
            return False
        cdir, _name, real = target
        try:
            age = time.time() - float(note.get("ts"))
        except Exception:
            return False
        ok, call_id = _call_id(payload)
        if (not ok or _from_subagent(payload) or note.get("tool_use_id") != call_id
                or note.get("path") != real or note.get("session_id") != sid
                or not 0 <= age <= NOTE_TTL_S or note.get("prior") not in (ABSENT, CONSENTED)):
            return False
        expect = note.get("expect_sha")
        before = note.get("prior_sha") if note.get("prior") == CONSENTED else None
        if not isinstance(expect, str) or (note.get("prior") == CONSENTED and not isinstance(before, str)):
            return False
        from . import trust

        gate_root = trust.gate_repo_root(cdir)
        if gate_root is None or not enabled(cdir, gate_root) or trust.consented_hashes(gate_root) is None:
            return False
        with open(real, "rb") as fh:
            data = fh.read()
        if hashlib.sha256(data).hexdigest() != expect:
            return False
        from .telemetry_rollup import record_session_grant

        if _lint_findings(data):
            record_session_grant(td, held=True)  # the file waits for review, as without the opt-in
            return False
        if not trust.record_session_write(cdir, real, gate_root, expect_sha=expect, before_sha=before):
            return False
        record_session_grant(td)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Doctor's read-only view: why is each withheld memory withheld?
# --------------------------------------------------------------------------- #
NATIVE, HIPPO_ELSEWHERE, OTHER = "native path", "written by hippo elsewhere", "other"
_HIPPO_PROVENANCE = ("cited_paths", "source_commit", "source_commit_time")


def classify(text: str) -> str:
    """Which route a memory file's frontmatter says it came by: Claude Code's native
    memory path (``metadata`` carries ``node_type`` and ``originSessionId`` and none of
    hippo's provenance fields), hippo (its provenance is present), or neither."""
    from .fm_access import fm_get, fm_metadata
    from .provenance import parse_frontmatter

    fm = parse_frontmatter(text)
    if any(fm_get(fm, k) is not None for k in _HIPPO_PROVENANCE):
        return HIPPO_ELSEWHERE
    meta = fm_metadata(fm)
    if "node_type" in meta and "originSessionId" in meta:
        return NATIVE
    return OTHER


def withheld_by_class(memory_dir: str, repo_root: Optional[str]) -> Optional[dict]:
    """``{class: count}`` over the memories recall withholds right now (changed or new
    against a trusted corpus's baseline), or None when no quarantine applies. Read-only."""
    try:
        from . import trust

        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        drift = trust.untrusted_changes(gate_root, memory_dir) if gate_root else {}
        if not drift.get("baseline"):
            return None
        counts = {NATIVE: 0, HIPPO_ELSEWHERE: 0, OTHER: 0}
        for stem in (drift.get("changed") or []) + (drift.get("added") or []):
            try:
                with open(os.path.join(memory_dir, stem + ".md"), "r", encoding="utf-8") as fh:
                    counts[classify(fh.read())] += 1
            except Exception:
                counts[OTHER] += 1
        return counts
    except Exception:
        return None
