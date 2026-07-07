"""Citation provenance for agent-memory files.

Extracts ``path:line`` code citations from a memory file BODY and records them as
additive frontmatter — ``cited_paths`` (the repo-relative files the memory talks about)
and ``source_commit`` (the memory file's own last-edit commit, the staleness baseline).

Hard guarantees (Tier 1 of the agent-memory-activation roadmap):
  - The memory BODY is NEVER modified — only the frontmatter block gains two keys.
  - Idempotent — re-running on an already-backfilled file is a no-op.
  - Handles BOTH frontmatter schemas in the corpus: a ``metadata:`` block (keys go under
    it, beside ``originSessionId``) and the flat top-level style (keys go top-level).
  - Never raises into a caller's hot path; git/IO failures degrade to empty/None.

Also exposes the shared dir-resolution + frontmatter-split helpers used by
``staleness`` and ``session_start`` so there is ONE definition of each.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML — the pinned venv dep (full-fidelity path)
except Exception:  # pragma: no cover - bare python3 pre-bootstrap (ONB-2)
    from ._vendor import miniyaml as yaml  # type: ignore  # frontmatter-subset fallback

# Code/config extensions we treat as "cited code" for the staleness signal.
# .md is intentionally EXCLUDED — memory<->memory references are [[wikilinks]] (Tier 3),
# and doc/changelog churn is not "code drift".
_CODE_EXTS = ("py", "ts", "tsx", "js", "jsx", "sh", "yaml", "yml", "json", "toml", "ini", "cfg")

# A path-like token: optional dir segments + filename + a code extension, with an
# optional :line or :line-range suffix (which we drop — we track files, not lines).
_CITATION_RE = re.compile(
    r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.(?:" + "|".join(_CODE_EXTS) + r"))(?::\d+(?:-\d+)?)?"
)

_FENCE = "---"


# --------------------------------------------------------------------------- #
# Shared helpers (single source of truth for the package)
# --------------------------------------------------------------------------- #
def run_git(args: List[str], repo_root: str) -> str:
    """Run a git command under ``repo_root``; return stdout, or '' on any failure."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        return out.stdout or ""
    except Exception:
        return ""


def git_root(start: Optional[str] = None) -> Optional[str]:
    out = run_git(["rev-parse", "--show-toplevel"], start or os.getcwd()).strip()
    return out or None


def encode_project_dir(repo_root: str) -> str:
    """Encode an absolute repo path the way Claude Code names ``~/.claude/projects/<encoded>``.

    SHP-5: the harness's real rule (verified against this machine's actual
    ``~/.claude/projects/`` entries, not folklore) is a literal one-for-one transliteration —
    every character that is NOT ``[A-Za-z0-9]`` becomes a single ``-``. No collapsing runs of
    hyphens, no stripping the leading hyphen produced by the path's initial ``/``. A prior
    ``tr '/' '-'`` implementation only touched slashes, so any path containing a ``.`` (or other
    punctuation) landed under a directory the harness never reads. This is the ONE place that
    formula lives — init and doctor both call it instead of hand-rolling their own bash regex.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", repo_root)


def _legacy_encode_project_dir(repo_root: str) -> str:
    """The PRE-FIX (buggy) encoding — only '/' transliterated, leading '-' stripped+re-added.

    Kept ONLY so ``check_project_symlink`` can recognize a symlink created by the old
    formula and name it as a legacy artifact needing repair — never used to CREATE a new
    symlink (that would just reintroduce the bug it exists to detect).
    """
    return "-" + repo_root.replace("/", "-").lstrip("-")


def check_project_symlink(repo_root: str, memory_dir: str, claude_projects_dir: Optional[str] = None) -> dict:
    """Verify the ``~/.claude/projects/<encoded>/memory`` symlink the way Claude Code reads it.

    SHP-5: per the roadmap, doctor must verify FROM THE DIRECTION Claude Code reads — resolve
    the expected symlink's target and compare it against ``memory_dir`` — never by
    recomputing/re-deriving a formula and trusting it blind (that is exactly how the original
    bug went undetected: doctor re-derived the same wrong formula the encoder used).

    Returns a dict with:
      - ``status``: one of ``"ok"``, ``"missing"``, ``"broken"`` (exists, wrong target),
        ``"legacy_wrong_encoding"`` (a pre-fix-formula symlink exists for this repo instead)
      - ``expected_path``: the correctly-encoded symlink path
      - ``legacy_path``: the pre-fix-formula path, when relevant (else None)
      - ``repair_command``: exact shell command to fix it, when status != "ok"

    Never raises — filesystem races/permission errors degrade to ``status="missing"``.
    """
    claude_projects_dir = claude_projects_dir or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    encoded = encode_project_dir(repo_root)
    expected_link = os.path.join(claude_projects_dir, encoded, "memory")
    legacy_encoded = _legacy_encode_project_dir(repo_root)
    legacy_link = os.path.join(claude_projects_dir, legacy_encoded, "memory")

    result = {
        "status": "missing",
        "expected_path": expected_link,
        "legacy_path": legacy_link if legacy_encoded != encoded else None,
        "repair_command": None,
    }
    try:
        if os.path.islink(expected_link) or os.path.exists(expected_link):
            target = os.path.realpath(expected_link)
            if os.path.isdir(target) and os.path.realpath(memory_dir) == target:
                result["status"] = "ok"
            else:
                result["status"] = "broken"
                result["repair_command"] = f'rm -f "{expected_link}" && mkdir -p "$(dirname "{expected_link}")" && ln -s "{memory_dir}" "{expected_link}"'
            return result

        if legacy_encoded != encoded and (os.path.islink(legacy_link) or os.path.exists(legacy_link)):
            result["status"] = "legacy_wrong_encoding"
            result["repair_command"] = f'mkdir -p "$(dirname "{expected_link}")" && ln -s "{memory_dir}" "{expected_link}"  # then: rm "{legacy_link}"'
            return result

        result["repair_command"] = f'mkdir -p "$(dirname "{expected_link}")" && ln -s "{memory_dir}" "{expected_link}"'
        return result
    except Exception:
        return result


def create_project_symlink(repo_root: str, memory_dir: str, claude_projects_dir: Optional[str] = None) -> dict:
    """Create (or confirm) the ``~/.claude/projects/<encoded>/memory`` symlink.

    ONB-5: the machine-local half of init that a cloned/second-machine corpus still needs —
    factored out of init's inline bash so it has one real unit-tested implementation instead
    of only living as a shell snippet in ``SKILL.md``. Uses the SAME ``encode_project_dir``
    formula (SHP-5) as ``check_project_symlink`` — never a second hand-rolled encoding.

    Idempotent: a symlink already pointing at ``memory_dir`` is a no-op. A symlink pointing
    somewhere ELSE is left untouched and reported as a conflict rather than clobbered — that
    shape usually means a prior manual setup.

    Returns a dict with:
      - ``status``: one of ``"created"``, ``"already_correct"``, ``"conflict"``
      - ``expected_path``: the symlink path this call created/checked
      - ``error``: set only when ``status == "conflict"`` — the existing (wrong) target

    Never raises — filesystem errors degrade to ``status="conflict"`` with the exception text
    in ``error`` so the caller can surface it rather than silently doing nothing.
    """
    claude_projects_dir = claude_projects_dir or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    encoded = encode_project_dir(repo_root)
    link_dir = os.path.join(claude_projects_dir, encoded)
    expected_link = os.path.join(link_dir, "memory")

    try:
        if os.path.islink(expected_link) or os.path.exists(expected_link):
            target = os.path.realpath(expected_link)
            if os.path.isdir(target) and os.path.realpath(memory_dir) == target:
                return {"status": "already_correct", "expected_path": expected_link, "error": None}
            return {
                "status": "conflict",
                "expected_path": expected_link,
                "error": f"already exists and points at {target!r}, not {memory_dir!r}",
            }
        os.makedirs(link_dir, exist_ok=True)
        os.symlink(memory_dir, expected_link)
        return {"status": "created", "expected_path": expected_link, "error": None}
    except Exception as exc:
        return {"status": "conflict", "expected_path": expected_link, "error": str(exc)}


def remove_project_symlink(repo_root: str, memory_dir: str, claude_projects_dir: Optional[str] = None) -> dict:
    """Remove the ``~/.claude/projects/<encoded>/memory`` symlink (ONB-6 — `/hippo:remove`).

    The inverse of ``create_project_symlink`` — same encoding formula (SHP-5), never a second
    hand-rolled derivation. Only ever removes a symlink that resolves to THIS project's
    ``memory_dir``; a symlink pointing somewhere else is left untouched and reported as
    ``"conflict"`` rather than deleted, since that shape means it belongs to a different corpus
    (or a prior manual setup) this call has no business touching.

    Never deletes ``memory_dir`` itself — the git-tracked corpus is out of scope for this
    function entirely; it only ever unlinks the machine-local symlink that points AT it.

    Returns a dict with:
      - ``status``: one of ``"removed"``, ``"absent"`` (nothing to remove — no-op),
        ``"conflict"`` (exists but points elsewhere)
      - ``expected_path``: the symlink path this call acted on / would have acted on
      - ``error``: set only when ``status == "conflict"`` — the existing (wrong) target

    Never raises — filesystem errors degrade to ``status="conflict"`` with the exception text
    in ``error`` so the caller can surface it rather than silently doing nothing.
    """
    claude_projects_dir = claude_projects_dir or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    encoded = encode_project_dir(repo_root)
    expected_link = os.path.join(claude_projects_dir, encoded, "memory")

    try:
        if not (os.path.islink(expected_link) or os.path.exists(expected_link)):
            return {"status": "absent", "expected_path": expected_link, "error": None}
        if not os.path.islink(expected_link):
            return {
                "status": "conflict",
                "expected_path": expected_link,
                "error": f"{expected_link!r} exists but is not a symlink — refusing to remove it",
            }
        target = os.path.realpath(expected_link)
        if os.path.realpath(memory_dir) != target:
            return {
                "status": "conflict",
                "expected_path": expected_link,
                "error": f"points at {target!r}, not {memory_dir!r} — left untouched",
            }
        os.remove(expected_link)
        return {"status": "removed", "expected_path": expected_link, "error": None}
    except Exception as exc:
        return {"status": "conflict", "expected_path": expected_link, "error": str(exc)}


def _candidate_memory_dir(d: str) -> str:
    return os.path.join(d, ".claude", "memory")


def walk_up_for_memory_dir(start: str) -> Tuple[str, str]:
    """Find the nearest existing ``.claude/memory`` at or above ``start``.

    Returns ``(memory_dir, reason)``. ``reason`` is one of ``"nested"`` (found at
    ``start`` itself — the per-package corpus that wins per OQ-1), ``"root-fallthrough"``
    (found by ascending past ``start``), or ``"none-found"`` (no existing corpus anywhere
    in the walk; caller falls back to today's behavior). The walk stops (inclusive) at
    the git toplevel when resolvable, and NEVER ascends past ``$HOME`` — an outer safety
    bound so a repo with an unusual structure, or no git repo at all, can't walk
    arbitrarily far up the filesystem.
    """
    start = os.path.abspath(start)
    nested = _candidate_memory_dir(start)
    if os.path.isdir(nested):
        return nested, "nested"

    toplevel = git_root(start)
    home = os.path.expanduser("~")
    cur = start
    while True:
        parent = os.path.dirname(cur)
        if parent == cur:
            break  # filesystem root
        cur = parent
        cand = _candidate_memory_dir(cur)
        if os.path.isdir(cand):
            return cand, "root-fallthrough"
        if toplevel and os.path.abspath(cur) == os.path.abspath(toplevel):
            break  # stop AT (inclusive) the git toplevel
        if os.path.abspath(cur) == os.path.abspath(home):
            break  # never ascend past $HOME
    return "", "none-found"


def resolve_dirs() -> Tuple[str, str]:
    """Return ``(memory_dir, repo_root)``.

    Honors ``HIPPO_MEMORY_DIR`` (used by hermetic tests) explicitly — that path is
    used as-is, no walk-up. Otherwise (OQ-1, SHP-2): a per-package corpus at
    ``<CLAUDE_PROJECT_DIR-or-cwd>/.claude/memory`` wins when present (nested wins);
    else ascend toward the git toplevel looking for a corpus (root-fallthrough) — a
    subdirectory launch (``claude`` started from ``packages/web`` in a monorepo) must
    still recall the repo-root corpus instead of silently no-op'ing. If no corpus
    exists anywhere in the walk, fall back to today's behavior (the raw
    ``CLAUDE_PROJECT_DIR``-derived path) so ``/hippo:init`` still has somewhere to seed.
    ``repo_root`` reuses ``git_root()`` (the actual toplevel) when resolvable — more
    correct for git-command purposes than a subdir ``CLAUDE_PROJECT_DIR``.
    """
    start = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    repo_root = git_root(start) or start

    explicit = os.environ.get("HIPPO_MEMORY_DIR")
    if explicit:
        return explicit, repo_root

    memory_dir, _reason = walk_up_for_memory_dir(start)
    if not memory_dir:
        memory_dir = _candidate_memory_dir(start)
    return memory_dir, repo_root


def ensure_self_ignoring_dir(path: str) -> None:
    """mkdir -p a DERIVED dir + drop a ``.gitignore`` containing ``*`` inside it.

    The standard self-ignoring cache pattern (SEC-3): the index and telemetry dirs
    stay invisible to ``git status`` even in projects that never ran init's
    .gitignore patch — a habitual ``git add .`` can never commit prompt previews or
    index blobs. Idempotent (an existing .gitignore, even user-edited, is left
    alone); never raises.
    """
    try:
        os.makedirs(path, exist_ok=True)
        gi = os.path.join(path, ".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w", encoding="utf-8") as fh:
                fh.write("*\n")
    except Exception:
        pass


def split_frontmatter(text: str) -> Tuple[Optional[List[str]], str]:
    """Split a memory file into ``(frontmatter_lines, body_text)``.

    ``frontmatter_lines`` are the lines BETWEEN the opening and closing ``---`` fences
    (excluding the fences). Returns ``(None, text)`` when there is no frontmatter.
    The body is returned verbatim so callers can guarantee byte-identical bodies.
    """
    if not text.startswith(_FENCE):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            body = "\n".join(lines[i + 1:])
            return lines[1:i], body
    return None, text


def parse_frontmatter(text: str) -> dict:
    """YAML-parse the frontmatter block into a dict (``{}`` on any problem)."""
    fm_lines, _ = split_frontmatter(text)
    if fm_lines is None:
        return {}
    try:
        data = yaml.safe_load("\n".join(fm_lines))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Citation extraction + resolution
# --------------------------------------------------------------------------- #
def extract_citations(body: str) -> List[str]:
    """Return the de-duplicated, order-preserving list of path-like tokens in ``body``
    (line numbers stripped)."""
    seen: set = set()
    out: List[str] = []
    for m in _CITATION_RE.finditer(body or ""):
        tok = m.group(1)
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def build_repo_file_index(repo_root: str) -> Tuple[set, Dict[str, List[str]]]:
    """Return ``(repo_files, basename_index)`` from ``git ls-files``.

    ``--full-name`` (SHP-1): without it, ``ls-files`` emits paths CWD-relative to
    ``repo_root`` — so when ``repo_root`` is a monorepo subdir (``CLAUDE_PROJECT_DIR``
    pointing below the git toplevel), this index would be subdir-relative while
    ``staleness._path_change_times`` (``git log --name-only``, always toplevel-relative,
    unaffected by ``-C``) is not. That mismatch means ``find_stale``'s
    ``path_times.get(p, 0) > base`` NEVER matches for a subdir-rooted corpus — a silent,
    permanent false-negative for the flagship staleness signal. ``--full-name`` makes this
    index toplevel-relative too, matching git log's convention everywhere in this module.
    """
    files = [f for f in run_git(["ls-files", "--full-name"], repo_root).split("\n") if f]
    repo_files = set(files)
    basename_index: Dict[str, List[str]] = {}
    for f in files:
        basename_index.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    return repo_files, basename_index


def resolve_citations(
    tokens: List[str], repo_files: set, basename_index: Dict[str, List[str]]
) -> List[str]:
    """Resolve raw tokens to repo-relative paths — ONLY when a token pins exactly one file.

    - A token that is already a tracked repo path is used as-is.
    - A bare basename is kept ONLY if it resolves to exactly ONE repo file. An AMBIGUOUS
      bare basename (e.g. ``contracts.py`` -> 52 files, ``config.py`` -> 38) is DROPPED:
      it is almost always a generic/pattern mention in prose, not a pinpoint citation, and
      keeping all candidates poisons the staleness signal (any same-named file changing
      would flag the memory). Under-flag beats cry-wolf.
    - Unresolvable tokens (not in the repo) are dropped.
    """
    out: List[str] = []
    seen: set = set()
    for tok in tokens:
        if tok in repo_files:
            cands = [tok]
        else:
            matches = basename_index.get(tok.rsplit("/", 1)[-1], [])
            cands = matches if len(matches) == 1 else []  # drop ambiguous bare basenames
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def cited_paths_for_body(body: str, repo_files: set, basename_index: Dict[str, List[str]]) -> List[str]:
    return resolve_citations(extract_citations(body), repo_files, basename_index)


def git_last_commit(rel_path: str, repo_root: str) -> Optional[str]:
    """The commit that last touched ``rel_path`` — the memory's staleness baseline."""
    sha = run_git(["log", "-1", "--format=%H", "--", rel_path], repo_root).strip()
    return sha or None


def git_last_commit_with_time(rel_path: str, repo_root: str) -> Tuple[Optional[str], Optional[int]]:
    """``(sha, committer_epoch)`` for the commit that last touched ``rel_path``.

    ONE ``git log`` call carries both ``%H`` and ``%ct`` (SHP-3) — avoids a second git
    process per file just to fetch the timestamp alongside the sha already fetched by
    ``git_last_commit``. Returns ``(None, None)`` on no history / any failure.
    """
    out = run_git(["log", "-1", "--format=%H %ct", "--", rel_path], repo_root).strip()
    parts = out.split()
    if len(parts) != 2:
        return None, None
    sha, ct = parts
    try:
        return sha, int(ct)
    except ValueError:
        return sha, None


def git_head(repo_root: str) -> Optional[str]:
    """Current HEAD sha, or None (no commits yet / not a git repo / git failure).

    ``--verify --quiet`` (not bare ``rev-parse HEAD``): on an unborn branch, bare
    rev-parse echoes the literal string "HEAD" to stdout — which would become a bogus
    baseline. The full-sha shape check is belt for any other echo-through.
    """
    sha = run_git(["rev-parse", "--verify", "--quiet", "HEAD"], repo_root).strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def git_head_with_time(repo_root: str) -> Tuple[Optional[str], Optional[int]]:
    """``(sha, committer_epoch)`` for HEAD (SHP-3's sibling of ``git_head``)."""
    head = git_head(repo_root)
    if not head:
        return None, None
    out = run_git(["show", "-s", "--format=%ct", head], repo_root).strip()
    try:
        return head, int(out)
    except ValueError:
        return head, None


# --------------------------------------------------------------------------- #
# Backfill (surgical, idempotent, body-preserving)
# --------------------------------------------------------------------------- #
def _has_cited_paths(fm_lines: List[str]) -> bool:
    return any(re.match(r"\s*cited_paths\s*:", ln) for ln in fm_lines)


def _flow_list(paths: List[str]) -> str:
    return "[" + ", ".join(json.dumps(p) for p in paths) + "]"


def backfill_text(
    text: str,
    cited_paths: List[str],
    source_commit: Optional[str],
    source_commit_time: Optional[int] = None,
) -> Tuple[str, bool]:
    """Return ``(new_text, changed)``.

    Inserts ``cited_paths`` + ``source_commit`` (+ ``source_commit_time`` when given,
    SHP-3) into the frontmatter ONLY. The body is left byte-identical. No-op
    (``changed=False``) when there is no frontmatter or the file already carries
    ``cited_paths``.
    """
    if not text.startswith(_FENCE):
        return text, False
    lines = text.split("\n")
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            close = i
            break
    if close is None:
        return text, False

    fm = lines[1:close]
    if _has_cited_paths(fm):
        return text, False  # idempotent

    cp_line_val = _flow_list(cited_paths)
    sc_val = json.dumps(source_commit if source_commit is not None else "")
    new_keys = [f"cited_paths: {cp_line_val}", f"source_commit: {sc_val}"]
    if source_commit_time is not None:
        new_keys.append(f"source_commit_time: {json.dumps(source_commit_time)}")

    # Locate a `metadata:` block; if present, the new keys nest under it.
    meta_idx = next((i for i, ln in enumerate(fm) if re.match(r"^metadata\s*:\s*$", ln)), None)
    if meta_idx is not None:
        indent = "  "
        last = meta_idx
        j = meta_idx + 1
        while j < len(fm):
            ln = fm[j]
            if ln.strip() == "" or not ln.startswith((" ", "\t")):
                break
            m = re.match(r"^(\s+)\S", ln)
            if m:
                indent = m.group(1)
            last = j
            j += 1
        new = [f"{indent}{k}" for k in new_keys]
        fm2 = fm[: last + 1] + new + fm[last + 1:]
    else:
        fm2 = fm + new_keys

    new_text = "\n".join([lines[0]] + fm2 + lines[close:])
    return new_text, True


def _strip_provenance(text: str) -> str:
    """Remove any existing cited_paths/source_commit/source_commit_time lines (body verbatim)."""
    if not text.startswith(_FENCE):
        return text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return text
    fm = [
        ln
        for ln in lines[1:close]
        if not re.match(r"\s*(cited_paths|source_commit|source_commit_time)\s*:", ln)
    ]
    return "\n".join([lines[0]] + fm + lines[close:])


def _strip_invalid_after(text: str) -> str:
    """Remove any existing ``invalid_after`` line from the frontmatter (body verbatim).

    Used ONLY by ``reverify_file`` — a genuine human-confirmed re-verification re-opens the
    soft-invalidation validity window. Deliberately NOT applied in ``backfill_file``'s
    ``--refresh`` path: a mechanical citation re-derivation (e.g. after a resolver fix) must
    never silently clear a soft-invalidation flag without an actual content re-verification.
    """
    if not text.startswith(_FENCE):
        return text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return text
    fm = [ln for ln in lines[1:close] if not re.match(r"\s*invalid_after\s*:", ln)]
    return "\n".join([lines[0]] + fm + lines[close:])


def backfill_file(
    path: str,
    repo_root: str,
    repo_files: set,
    basename_index: Dict[str, List[str]],
    dry_run: bool = False,
    refresh: bool = False,
) -> dict:
    """Backfill one memory file. Returns a small result dict; never raises.

    With ``refresh=True``, an already-backfilled file has its ``cited_paths`` RE-DERIVED
    (e.g. after a resolver fix) while its existing ``source_commit``/``source_commit_time``
    baseline is PRESERVED, so the staleness comparison is unchanged. The body is always
    left byte-identical.

    ``source_commit_time`` (SHP-3) is the committer epoch of ``source_commit``, recorded
    alongside it — the fallback baseline ``staleness.find_stale`` uses when the sha itself
    is unresolvable (squash-merge / shallow clone erases it from history).
    """
    result = {
        "path": path,
        "changed": False,
        "cited": [],
        "source_commit": None,
        "source_commit_time": None,
        "error": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        _, body = split_frontmatter(text)
        cited = cited_paths_for_body(body, repo_files, basename_index)
        rel = os.path.relpath(path, repo_root)
        if refresh and _has_cited_paths(split_frontmatter(text)[0] or []):
            fm = parse_frontmatter(text)
            if not fm:
                # Frontmatter carries provenance (it has a cited_paths line) but does NOT
                # yaml-parse. Re-deriving here would FALL THROUGH to git_last_commit and
                # silently re-baseline source_commit (gaming the staleness signal), while
                # rewriting an already-broken file. Refuse loudly — fix the YAML first.
                # (find_unparseable / the SessionStart integrity producer surface these.)
                result["error"] = "unparseable frontmatter — refusing to refresh (fix the YAML)"
                return result
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            sc = fm.get("source_commit") or meta.get("source_commit")
            sct = fm.get("source_commit_time")
            if sct is None:
                sct = meta.get("source_commit_time")
            if sc is None:
                sc, sct = git_last_commit_with_time(rel, repo_root)
                if sc is None:
                    sc, sct = git_head_with_time(repo_root)
            text = _strip_provenance(text)  # drop old provenance; body untouched
        else:
            # A file with no commit history yet (just created by write_memory, or
            # hand-authored and not yet committed) still gets a REAL baseline: HEAD —
            # "reflects code as of now". An empty baseline would make the memory
            # invisible to staleness/reconsolidation/archive gating until a manual
            # commit + refresh (COR-1: memories must be BORN staleness-tracked).
            sc, sct = git_last_commit_with_time(rel, repo_root)
            if sc is None:
                sc, sct = git_head_with_time(repo_root)
        new_text, changed = backfill_text(text, cited, sc, sct)
        result.update({"cited": cited, "source_commit": sc, "source_commit_time": sct, "changed": changed})
        if changed and not dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
    except Exception as exc:  # never break a corpus-wide backfill on one file
        result["error"] = str(exc)
    return result


def _is_memory_filename(name: str) -> bool:
    """THE corpus-membership filter — one definition, shared with the edge cache's
    scandir stat sweep (GRA-6), which must see exactly the files ``_iter_memory_files``
    yields or the cache-freshness check would silently drift from the graph builder."""
    return name.endswith(".md") and name not in ("MEMORY.md", "MEMORY.full.md")


def _iter_memory_files(memory_dir: str):
    for name in sorted(os.listdir(memory_dir)):
        if _is_memory_filename(name):
            yield os.path.join(memory_dir, name)


def heal_empty_baselines(memory_dir: str, repo_root: str) -> List[str]:
    """Set ``source_commit`` to HEAD for memories whose baseline is EMPTY. Returns healed names.

    An empty baseline (written when a memory was backfilled before its repo had any
    commits, or by a pre-COR-1 plugin in a dirty worktree) makes a memory INVISIBLE to
    staleness, reconsolidation, and archive gating. Healing it to HEAD turns tracking ON
    ("reflects code as of now") — it can never SILENCE an existing flag, because an empty
    baseline never flags anything; this is the opposite of a bulk re-baseline, which the
    engine deliberately refuses everywhere else. Only the one ``source_commit: ""`` line
    inside the frontmatter is rewritten; bodies stay byte-identical. Files whose
    frontmatter does not parse are skipped (the integrity producer surfaces those).
    Never raises; a no-op when HEAD is unresolvable (repo with no commits yet).
    """
    healed: List[str] = []
    try:
        head = git_head(repo_root)
        if not head or not os.path.isdir(memory_dir):
            return []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                fm = parse_frontmatter(text)
                if not fm:
                    continue  # no/unparseable frontmatter — not this function's job
                meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
                has_key = "source_commit" in fm or "source_commit" in (meta or {})
                current = fm.get("source_commit") or (meta or {}).get("source_commit")
                if not has_key or current:
                    continue  # never touch a real baseline (no blind re-baseline)
                lines = text.split("\n")
                close = next(
                    (i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None
                )
                if close is None:
                    continue
                for i in range(1, close):
                    m = re.match(r"^(\s*source_commit\s*:\s*)(\"\"|''|)\s*$", lines[i])
                    if m:
                        lines[i] = f'{m.group(1)}"{head}"'
                        with open(path, "w", encoding="utf-8") as fh:
                            fh.write("\n".join(lines))
                        healed.append(os.path.splitext(os.path.basename(path))[0])
                        break
            except Exception:
                continue  # never break the sweep on one file
    except Exception:
        return healed
    return healed


def backfill_corpus(
    memory_dir: str, repo_root: str, dry_run: bool = False, refresh: bool = False
) -> List[dict]:
    repo_files, basename_index = build_repo_file_index(repo_root)
    return [
        backfill_file(p, repo_root, repo_files, basename_index, dry_run=dry_run, refresh=refresh)
        for p in _iter_memory_files(memory_dir)
    ]


# --------------------------------------------------------------------------- #
# Re-verify (human-confirmed staleness re-baseline to HEAD — distinct from --refresh)
# --------------------------------------------------------------------------- #
def reverify_file(
    path: str,
    repo_root: str,
    repo_files: set,
    basename_index: Dict[str, List[str]],
    *,
    dry_run: bool = False,
) -> dict:
    """Re-baseline ONE memory's staleness provenance to HEAD after a HUMAN re-verifies it.

    UNLIKE ``backfill_file(refresh=True)`` — which PRESERVES the old ``source_commit`` (so a
    refresh can never clear a flag) — this re-derives ``cited_paths`` AND re-baselines
    ``source_commit`` to **HEAD**: "I just re-read this memory and confirmed it still matches the
    code as of now." That is the only correct baseline for a human-confirmed clear — and it is
    deliberately a PER-MEMORY operation. (There is no bulk re-baseline: re-baselining to the
    file's last *touch* would anchor to the mechanical provenance-backfill commit — which left the
    body byte-identical — and silence genuine pre-backfill drift. Verification can't be done in
    bulk; clear flags one memory at a time, after actually re-reading each.)

    The BODY is left byte-identical. REFUSES (no write) on unparseable frontmatter — mirrors the
    refresh guard — so a malformed file is never silently re-baselined. Idempotent (no-op when the
    derived provenance already matches the file). Never raises. NOT autonomous: invoked by a human
    who has looked at the drift; never fires on a hook or a timer.

    Also STRIPS ``invalid_after`` when present (Tier 3, graceful decay) — a genuine
    re-verification re-opens the soft-invalidation validity window, exactly like it
    re-baselines the staleness window. Mirrors the rest of this function's per-item,
    HEAD-baseline, refuse-unparseable contract; nothing else about that contract changes.
    """
    result = {
        "path": path,
        "changed": False,
        "cited": [],
        "source_commit": None,
        "source_commit_time": None,
        "error": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm_lines, body = split_frontmatter(text)
        if fm_lines is None:
            result["error"] = "no frontmatter — run backfill first"
            return result
        if not _has_cited_paths(fm_lines):
            result["error"] = "no provenance yet — run backfill first"
            return result
        if not parse_frontmatter(text):
            # Unparseable frontmatter: re-baselining would rewrite an already-broken file AND
            # silently move the baseline. Refuse loudly (fix the YAML first) — same guard as the
            # refresh path; find_unparseable / the integrity producer surface these.
            result["error"] = "unparseable frontmatter — refusing to re-baseline (fix the YAML)"
            return result
        sc, sct = git_head_with_time(repo_root)
        cited = cited_paths_for_body(body, repo_files, basename_index)
        new_text, _ = backfill_text(_strip_invalid_after(_strip_provenance(text)), cited, sc, sct)
        changed = new_text != text  # idempotent: a no-op when provenance already matches
        result.update({"cited": cited, "source_commit": sc, "source_commit_time": sct, "changed": changed})
        if changed and not dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Backfill cited_paths/source_commit frontmatter.")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not write")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-derive cited_paths on already-backfilled files (preserve source_commit baselines)",
    )
    parser.add_argument(
        "--reverify",
        metavar="NAME",
        default=None,
        help="re-baseline ONE memory's source_commit to HEAD after the content has been "
        "re-verified against current code (clears a staleness flag; --refresh deliberately "
        "CANNOT). Per-memory and verification-gated by design — there is NO bulk re-baseline "
        "(blind bulk re-baseline anchors to the mechanical backfill touch and silences real "
        "drift). NAME is the slug, with or without .md",
    )
    parser.add_argument(
        "--refresh-one",
        metavar="NAME",
        default=None,
        help="re-derive cited_paths on ONE memory (e.g. after hand-editing its body) WITHOUT "
        "touching the rest of the corpus — the scoped sibling of --refresh, which always "
        "re-derives every already-backfilled memory's citations (dropping references to any "
        "file that's since been renamed/deleted, corpus-wide, whether you wanted that review "
        "or not). Preserves source_commit exactly like --refresh does. NAME is the slug, with "
        "or without .md",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md
    repo_root = args.repo_root or repo

    if args.reverify:
        repo_files, basename_index = build_repo_file_index(repo_root)
        name = args.reverify if args.reverify.endswith(".md") else f"{args.reverify}.md"
        target = os.path.join(memory_dir, name)
        r = reverify_file(target, repo_root, repo_files, basename_index, dry_run=args.dry_run)
        base = os.path.basename(target)
        if r["error"]:
            print(f"reverify {base}: refused — {r['error']}")
        elif r["changed"]:
            verb = "would re-baseline" if args.dry_run else "re-baselined"
            print(f"reverify {base}: {verb} source_commit -> HEAD ({(r['source_commit'] or '')[:9]})")
        else:
            print(f"reverify {base}: already current (no change)")
        return 0

    if args.refresh_one:
        repo_files, basename_index = build_repo_file_index(repo_root)
        name = args.refresh_one if args.refresh_one.endswith(".md") else f"{args.refresh_one}.md"
        target = os.path.join(memory_dir, name)
        r = backfill_file(target, repo_root, repo_files, basename_index, dry_run=args.dry_run, refresh=True)
        base = os.path.basename(target)
        if r["error"]:
            print(f"refresh-one {base}: refused — {r['error']}")
        elif r["changed"]:
            verb = "would refresh" if args.dry_run else "refreshed"
            print(f"refresh-one {base}: {verb} cited_paths ({len(r['cited'])} citation(s)); source_commit unchanged")
        else:
            print(f"refresh-one {base}: already current (no change)")
        return 0

    results = backfill_corpus(memory_dir, repo_root, dry_run=args.dry_run, refresh=args.refresh)
    changed = [r for r in results if r["changed"]]
    errored = [r for r in results if r["error"]]
    with_cites = [r for r in results if r["cited"]]
    print(f"memory files scanned : {len(results)}")
    print(f"with code citations  : {len(with_cites)}")
    print(f"{'would change' if args.dry_run else 'changed'}        : {len(changed)}")
    if errored:
        print(f"errors               : {len(errored)}")
        for r in errored[:10]:
            print(f"  ! {os.path.basename(r['path'])}: {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
