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
``staleness`` and ``session_start`` so there is ONE definition of each — the
dir-resolution half now lives in ``provenance_env`` and is re-exported here, so those
callers are unchanged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML — the pinned venv dep (full-fidelity path)
except Exception:  # pragma: no cover - bare python3 pre-bootstrap (ONB-2)
    from ._vendor import miniyaml as yaml  # type: ignore  # frontmatter-subset fallback

_FENCE = "---"


# --------------------------------------------------------------------------- #
# ED5R-3 split (pure code motion): the ENVIRONMENT layer — git shell-outs, corpus/
# tier location, the ~/.claude/projects symlink — lives in provenance_env. It had
# zero dependencies on the rest of this file, which is what made it the seam. Every
# moved name is re-imported HERE so memory.provenance.<name> keeps resolving and
# stays patchable for the façade's own call sites. Siblings never import this façade.
# --------------------------------------------------------------------------- #
from .provenance_env import (  # noqa: E402,F401
    _GIT_ROOT_CACHE,
    _MAIN_TREE_CACHE,
    _PUBLIC_GIT_HOSTS,
    _candidate_memory_dir,
    _git_url_host,
    _legacy_encode_project_dir,
    check_project_symlink,
    create_project_symlink,
    current_user_slug,
    encode_project_dir,
    ensure_self_ignoring_dir,
    git_remote_info,
    git_root,
    launch_root,
    local_memory_dir,
    main_worktree_root,
    remove_project_symlink,
    resolve_corpus_start,
    resolve_dirs,
    run_git,
    slugify_identity,
    tier_index_dir,
    user_memory_dir,
    walk_up_for_memory_dir,
)

# --------------------------------------------------------------------------- #
# COR-7 / DRV-2: corpus format + citation-derivation versioning — decomposed into
# provenance_format.py (pure code motion; the module-size ratchet fired). Façade
# re-exports: every symbol stays importable at memory.provenance.<name>, so no
# caller or test changes its dotted path. (Sibling imports nothing from here —
# the marker is JSON, not frontmatter — per CONTRIBUTING.md "Code layout".)
# --------------------------------------------------------------------------- #
from .provenance_format import (  # noqa: E402,F401
    CITATION_DERIVATION_VERSION,
    CORPUS_FORMAT_VERSION,
    HARNESS_FLOOR_READ_CAP_BYTES,
    HARNESS_FLOOR_WARN_BYTES,
    _FORMAT_MARKER_NAME,
    _read_marker,
    _write_marker_keys,
    format_marker_path,
    read_cite_derivation,
    read_corpus_format,
    read_floor_lint,
    read_volatile_paths,
    write_cite_derivation,
    write_corpus_format,
)


# --------------------------------------------------------------------------- #
# ORC / CUR: the CITATION layer — the extractor's vocabulary, the git-index oracle, the
# ONE resolver and the ONE merge policy — decomposed into provenance_citations.py (pure
# code motion; the module-size ratchet fired on the ORC-4/CUR-2 work). Façade re-exports,
# same contract as the two blocks above.
# --------------------------------------------------------------------------- #
from .provenance_citations import (  # noqa: E402,F401
    EXCLUDE_KEY,
    _CITATION_RE,
    _CODE_EXTS,
    _EXCLUDE_KEY_RE,
    _EXTENSIONLESS_NAMES,
    _frontmatter_cited_paths,
    _strip_relative_prefix,
    build_repo_file_index,
    cited_paths_for_body,
    extract_citations,
    derive_citations,
    frontmatter_excluded_paths,
    legacy_basename_repoints,
    merge_citations,
    resolve_citations,
    unresolved_citations,
)


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
    return bool(_own_scope_key_lines(fm_lines, _CITED_PATHS_KEY_RE))


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

    return "\n".join([lines[0]] + insert_frontmatter_keys(fm, new_keys) + lines[close:]), True


_PROVENANCE_KEY_RE = re.compile(r"^(\s*)(?:cited_paths|source_commit|source_commit_time)\s*:")
_CITED_PATHS_KEY_RE = re.compile(r"^(\s*)cited_paths\s*:")
_INVALID_AFTER_KEY_RE = re.compile(r"^(\s*)invalid_after\s*:")
_BLOCK_ITEM_RE = re.compile(r"^(\s*)-\s")


def _value_run_end(lines: List[str], start: int, key_indent: int) -> int:
    """First index at/after ``start`` that is NOT part of the preceding bare key's value.

    COR-20, and the ONE definition of the value-run rule — ``strip_frontmatter_keys`` and
    ``dream_generate._set_cited_paths`` each had their own walk, both consuming ONLY
    ``- item`` block lines. A flow value on its own continuation line —

        cited_paths:
          []

    (the shape an older hippo emitted for a memory with no derivable citations) — was not
    consumed when its key line was stripped/replaced, so the orphaned ``[]`` landed under
    whichever key PRECEDED ``cited_paths``, where YAML folds it into that key's scalar
    (``type: feedback`` -> ``type: feedback []``) or refuses the document outright. The
    COR-9 damage guard caught the corruption before it reached disk — but by refusing, it
    permanently blocked rederive/reverify on every memory carrying that legacy shape: the
    current writer could not round-trip its own past output.

    The rule, in YAML block-mapping terms: a ``- item`` line at the key's indent or deeper
    is part of the value (a block sequence may sit at its key's own indent); ANY other
    non-blank line strictly deeper than the key is value continuation (a flow list on its
    own line, a nested mapping, a block-scalar body). A sibling key (same indent), a
    dedent, or a blank line ends the run — so a frontmatter fence or the next key is never
    consumed.
    """
    j = start
    while j < len(lines):
        ln = lines[j]
        bm = _BLOCK_ITEM_RE.match(ln)
        if bm and len(bm.group(1)) >= key_indent:
            j += 1
            continue
        if ln.strip() and (len(ln) - len(ln.lstrip(" \t"))) > key_indent:
            j += 1
            continue
        break
    return j
# An indented KEY line — deliberately NOT `^(\s+)\S`, which also matches a block-list item
# (`    - keep-me`) and so reports the ITEM's indent as the block's key indent; nor a
# comment line, whose indent is whatever the author felt like. See
# ``insert_frontmatter_keys``.
_INDENTED_KEY_RE = re.compile(r"^(\s+)(?!-\s|#)\S")
_METADATA_OPEN_RE = re.compile(r"^metadata\s*:\s*$")


def insert_frontmatter_keys(fm: List[str], new_keys: List[str]) -> List[str]:
    """Insert rendered ``key: value`` strings into frontmatter lines ``fm``.

    Nests them under an existing ``metadata:`` block when there is one (matching that
    block's own key indent), else appends them top-level. Returns new lines; the caller
    re-joins around the fences, so the body is untouched.

    COR-9: this is the ONE implementation of a walk that had four hand-copied copies
    (``backfill_text``, ``_stamp_last_verified``, ``staleness.set_invalid_after``,
    ``links.add_typed_relation``), all sharing one bug — they took the indent from the last
    INDENTED line rather than the last indented KEY. Given::

        metadata:
          tags:
            - keep-me

    the old walk read ``    - keep-me`` and indented the new keys to four spaces, emitting a
    mapping inside a sequence — frontmatter that does not parse. The block-style
    ``cited_paths`` bug made this reachable on ordinary corpus files, but the defect is
    independent of it: any memory whose ``metadata:`` block ENDS in a block list hit it.

    COR-24: "the last indented KEY" was still the wrong line. Given a block that ends in a
    nested MAPPING — hippo's own ``edge_origin:`` dedup-review stamp is one::

        metadata:
          type: project
          edge_origin:
            some-other-memory: dedup-review

    the last indented key is the map's CHILD, so the new keys were written at four spaces
    and YAML folded ``cited_paths``/``source_commit``/``source_commit_time`` INTO
    ``edge_origin``. The damage guard refused the write — correctly — and thereby made the
    file permanently un-writable by rederive, refresh-one and reverify. The indent a new
    first-level key needs is the block's FIRST key's: a block mapping's first entry sets
    the indent every sibling must share, whatever nests beneath later ones. The insert
    POSITION is unchanged (after the block's last line, nested value included).
    """
    meta_idx = next((i for i, ln in enumerate(fm) if _METADATA_OPEN_RE.match(ln)), None)
    if meta_idx is None:
        return fm + list(new_keys)
    indent = None
    last = meta_idx
    j = meta_idx + 1
    while j < len(fm):
        ln = fm[j]
        if ln.strip() == "" or not ln.startswith((" ", "\t")):
            break
        if indent is None:
            m = _INDENTED_KEY_RE.match(ln)
            if m:
                indent = m.group(1)
        last = j
        j += 1
    indent = indent or "  "
    return fm[: last + 1] + [f"{indent}{k}" for k in new_keys] + fm[last + 1:]


def _own_scope_key_lines(fm: List[str], key_re) -> List[int]:
    """Indices of ``fm`` lines where ``key_re`` matches a key in one of the TWO scopes every
    reader looks in — top level, or a direct child of ``metadata:`` (COR-24).

    ``key_re`` alone matches at ANY indent, so a nested map's child that merely shares an
    owned key's name (``pack_info:`` → ``source_commit: …``) was stripped as if it were the
    memory's own provenance, hollowing out its parent — the same un-writable-forever
    outcome as the insert bug, from the strip side. No reader resolves a key at that depth,
    so no writer may claim it.
    """
    out: List[int] = []
    in_meta = False
    child_indent: Optional[int] = None
    for i, ln in enumerate(fm):
        if not ln.strip():
            continue
        depth = len(ln) - len(ln.lstrip(" \t"))
        if depth == 0:
            in_meta = bool(_METADATA_OPEN_RE.match(ln))
            child_indent = None
        elif in_meta and child_indent is None and _INDENTED_KEY_RE.match(ln):
            child_indent = depth
        if key_re.match(ln) and (depth == 0 or (in_meta and depth == child_indent)):
            out.append(i)
    return out


def strip_frontmatter_keys(text: str, key_re) -> str:
    """Drop every frontmatter key matching ``key_re`` — AND the block-style continuation
    lines that ARE its value — leaving the body byte-identical.

    COR-9. A per-LINE filter is not sufficient, and that is the whole bug this replaces. A
    block-style value::

        cited_paths:
          - a.py
          - b.py

    has only its KEY line matched, so a filter leaves ``- a.py`` orphaned under whichever
    key precedes it. YAML then does one of two things, both silent: it folds the items into
    that key's value (turning a ``last_verified`` date into a list of paths), or it refuses
    the document outright — at which point ``parse_frontmatter`` degrades to ``{}`` by
    design and the memory loses its name, type and provenance while still reading fine to a
    human. ``staleness.find_unparseable`` then reports the wreck, which is detection of a
    state this function MANUFACTURED from a healthy file.

    The continuation-aware walk is not new here: ``links.add_typed_relation`` and
    ``dream_generate`` both do it and say so in their docstrings. This is the oldest member
    of that family and never got the fix; it is now the shared primitive for all of them.
    """
    if not text.startswith(_FENCE):
        return text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return text
    fm = lines[1:close]
    owned = set(_own_scope_key_lines(fm, key_re))  # COR-24: never a nested map's child
    out: List[str] = []
    i = 0
    while i < len(fm):
        m = key_re.match(fm[i]) if i in owned else None
        if not m:
            out.append(fm[i])
            i += 1
            continue
        key_indent = len(m.group(1))
        inline = re.sub(r"\s+#.*$", "", fm[i].split(":", 1)[1]).strip()
        i += 1
        if inline:
            continue  # flow style (`key: [a, b]`) — the value lives on the key line
        # A bare `key:`: consume the continuation lines that ARE its value — block items,
        # or a deeper-indented flow value on its own line (COR-20; see _value_run_end).
        i = _value_run_end(fm, i, key_indent)
    return "\n".join([lines[0]] + out + lines[close:])


def _fm_scopes(fm: dict):
    """``[(prefix, mapping)]`` for both frontmatter schemas — top level, and ``metadata:``."""
    out = [("", fm)]
    if isinstance(fm.get("metadata"), dict):
        out.append(("metadata.", fm["metadata"]))
    return out


def _frontmatter_damage(before: str, after: str, may_change) -> Optional[str]:
    """Describe the damage a rewrite would do to keys its writer does not own — or None.

    COR-9, the OUTPUT symmetry of the refuse-unparseable INPUT guards in ``backfill_file``
    and ``reverify_file``: those refuse to rewrite an already-broken file, this refuses to
    CREATE one. ``may_change`` names the keys THIS writer is entitled to touch; every other
    key must survive with its value byte-identical.

    Checking "does the output still parse" is NOT enough, and that is the whole reason this
    takes ``may_change``. A dropped block-list key leaves its ``- item`` lines orphaned, and
    YAML resolves that two ways depending on what precedes them:

      - the orphans sit under a key with an inline value  -> the document does not parse,
        ``parse_frontmatter`` degrades to ``{}``, and the memory loses everything at once;
      - the orphans FOLD into that key as a multi-line plain scalar -> the document parses
        perfectly and ``last_verified`` is now the string
        ``"2026-07-01 - src/a.py - src/b.py"``.

    The second is the dangerous one: no parse check can see it, and the file reads fine to a
    human. So the invariant is value-level, not parse-level.
    """
    if split_frontmatter(before)[0] is None:
        return None
    fm_before = parse_frontmatter(before)
    if not fm_before:
        return None  # already broken on the way in — the input guards own that case
    fm_after = parse_frontmatter(after)
    if not fm_after:
        return "it would no longer parse (it parses now)"
    allowed = set(may_change) | {"metadata"}  # `metadata:` itself is compared key-by-key below
    for prefix, scope_before in _fm_scopes(fm_before):
        scope_after = fm_after if prefix == "" else (fm_after.get("metadata") or {})
        for key, val in scope_before.items():
            if key in allowed:
                continue
            if key not in scope_after:
                return f"it would silently DROP the `{prefix}{key}` key"
            if scope_after[key] != val:
                return (
                    f"it would silently CHANGE `{prefix}{key}` from {val!r} "
                    f"to {scope_after[key]!r}"
                )
    return None


_PROVENANCE_OWNED = frozenset({"cited_paths", "source_commit", "source_commit_time"})


def restore_file_bytes(
    path: str, original: str, memory_dir: str, repo_root: Optional[str] = None
) -> Optional[str]:
    """COR-16 rollback primitive: put ``original`` back into ``path`` and re-fold the
    consent baseline so the restored bytes are not misread as user drift.

    The two-write chains (dedup-merge, demote+supersede, refines apply) each land a
    first guarded write and then a second; when the second fails, the first must come
    back OUT or the operation reports "refused"/"nothing changed" over a live partial
    write. One shared implementation, like the insert/strip walks (COR-9's lesson).
    Returns an error string when the restore itself failed — the caller reports the
    PARTIAL state explicitly instead of pretending the rollback happened.
    """
    try:
        from .atomic import write_text_atomic

        write_text_atomic(path, original)
    except Exception as exc:
        return str(exc)
    try:
        from .trust import record_authored_write

        record_authored_write(memory_dir, path, repo_root)
    except Exception:
        pass
    return None


def _strip_provenance(text: str) -> str:
    """Remove any existing cited_paths/source_commit/source_commit_time keys (body verbatim)."""
    return strip_frontmatter_keys(text, _PROVENANCE_KEY_RE)


def _strip_invalid_after(text: str) -> str:
    """Remove any existing ``invalid_after`` key from the frontmatter (body verbatim).

    Used ONLY by ``reverify_file`` — a genuine human-confirmed re-verification re-opens the
    soft-invalidation validity window. Deliberately NOT applied in ``backfill_file``'s
    ``--refresh`` path: a mechanical citation re-derivation (e.g. after a resolver fix) must
    never silently clear a soft-invalidation flag without an actual content re-verification.
    """
    return strip_frontmatter_keys(text, _INVALID_AFTER_KEY_RE)


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

    ``dropped_citations`` (LIF-3): the cited paths present in the frontmatter BEFORE a
    refresh re-derivation and absent AFTER — the rename/delete case, where a citation
    silently vanishes (possibly emptying ``cited_paths``, which permanently exempts the
    memory from staleness). Always ``[]`` on the initial-backfill path (nothing recorded
    yet, so nothing can be lost) and on a refusal (nothing was re-derived); callers must
    surface a non-empty list, never swallow it.

    ``preserved_not_derived`` (CUR-1): stored citations whose file still EXISTS but which
    the body no longer yields a token for — these are KEPT, not dropped. Re-derivation
    used to treat ``cited_paths`` as wholly machine-derived and clobbered hand-curated
    entries the extractor cannot parse from prose (a bare/bold ``Dockerfile``, a
    ``.dockerignore`` never mentioned in the body); with CUR-1 a citation dies only with
    its file. Callers surface the kept set (the keep-line) so deliberate pruning stays a
    visible hand edit rather than an automatic loss.

    ``excluded`` (CUR-2): paths the memory's own ``cited_paths_exclude`` list HELD OUT of
    this derivation — a human's deliberate prune, honoured on every path including the
    initial backfill, never written by one. A stored citation the list names is dropped
    (it appears in ``dropped_citations``) but in NEITHER cause partition below: it is not
    rot, and the renderer says so on its own informational line.

    ``baseline`` (MIG-2): ``"initial"`` (no provenance yet — baselined to the file's last
    commit), ``"preserved"`` (refresh kept the stored ``source_commit``) or ``"assigned"``
    (refresh found ``cited_paths`` but NO ``source_commit``, so there was nothing to keep).

    ``dropped_gone`` / ``dropped_not_derived`` (LIF-4): the drop set, partitioned by CAUSE.
    Computed here because this is where ``repo_files`` — the only oracle that can answer
    "is it actually missing?" — is in scope. Under CUR-1 this producer only ever drops
    ``gone`` paths (``dropped_not_derived`` stays ``[]``); the key and the renderer's
    clause for it remain for pre-CUR-1 result dicts a caller may replay.

    ``extracted_but_unresolved`` (DRV-1): tokens the body offered that resolved to nothing
    (untracked file, wrong path, ambiguous basename). Note this corrects the sentence above:
    "nothing can be lost" on initial backfill is only true of the FRONTMATTER. Something can
    absolutely be lost from the BODY — a memory written before ``git add`` cites real code
    in plain sight and lands ``cited_paths: []``, the worst rot state, reporting nothing.
    That blind spot is why this key exists, and it is populated on EVERY path.
    """
    result = {
        "path": path,
        "changed": False,
        "cited": [],
        "dropped_citations": [],
        "dropped_gone": [],
        "dropped_not_derived": [],
        "preserved_not_derived": [],
        "excluded": [],
        "dropped_repointed": {},
        "extracted_but_unresolved": [],
        "source_commit": None,
        "source_commit_time": None,
        "error": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        original = text  # COR-9: `text` is re-assigned by the strip below; the guard needs this
        _, body = split_frontmatter(text)
        # DRV-1: the derivation's OTHER half — what the body offered that the oracle refused.
        result["extracted_but_unresolved"] = unresolved_citations(body, repo_files, basename_index)
        rel = os.path.relpath(path, repo_root)
        gone: List[str] = []
        not_derived: List[str] = []
        fm = parse_frontmatter(text)
        # CUR-2: the human-owned exclusion binds EVERY derivation, the first one included —
        # a memory written with `cited_paths_exclude:` must never be born citing the path.
        merged = derive_citations(body, fm, repo_files, basename_index, use_stored=False)
        result["baseline"] = "initial"  # MIG-2: which branch ran, so callers say what happened
        if refresh and _has_cited_paths(split_frontmatter(text)[0] or []):
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
            result["baseline"] = "preserved"
            if sc is None:
                # cited_paths without a source_commit (hand-written, or a legacy partial
                # backfill): there is no baseline TO preserve, so one is assigned — and the
                # result says so rather than claiming a preservation that did not happen.
                result["baseline"] = "assigned"
                sc, sct = git_last_commit_with_time(rel, repo_root)
                if sc is None:
                    sc, sct = git_head_with_time(repo_root)
            # CUR-1 (owner-ratified 2026-07-18): a re-derivation must not destroy a LIVE
            # citation it merely cannot re-derive. cited_paths conflates machine-derived
            # and hand-CURATED entries (a `Dockerfile` the prose names only bare/bold, a
            # `.dockerignore` never in the body at all), and every re-derivation surface
            # used to clobber the curated ones — the exact paths a human just restored.
            # So: a stored path whose file still EXISTS is preserved even when the body
            # yields no token for it; only a genuinely-gone file (renamed/deleted) drops.
            # The flip side is deliberate too — legacy junk (an old resolver's inflated/
            # fabricated entries) is now sticky until pruned by hand; the keep-line and
            # the worklist's `keeps` clause make it visible, and a hand edit of the
            # frontmatter is the pruning verb.
            # CUR-2/ORC-4: ONE merge policy, shared with reverify_file and rederive_preview.
            merged = derive_citations(body, fm, repo_files, basename_index)
            # LIF-4: partition HERE, where repo_files is in scope. The renderer cannot do it
            # — reconsolidate and the MCP tool call it with no repo index in hand. (With
            # CUR-1 every drop with no NAMED cause IS gone — the partition stays for the
            # result-shape contract and any pre-CUR-1 dict a caller replays.)
            gone, not_derived = partition_dropped(_uncaused_drops(merged), repo_files)
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
        cited, preserved, dropped = merged["cited"], merged["preserved"], merged["dropped"]
        new_text, changed = backfill_text(text, cited, sc, sct)
        damage = _frontmatter_damage(original, new_text, _PROVENANCE_OWNED) if changed else None
        if damage:
            # COR-9: a backfill owns the three provenance keys and nothing else. If the
            # rewrite would touch anything else, never write it — refuse loudly, exactly as
            # the unparseable-INPUT guard above does.
            result["error"] = f"refusing to write: {damage} — this is a hippo bug, please report it"
            return result
        result.update(
            {
                "cited": cited,
                "dropped_citations": dropped,
                "dropped_gone": gone,
                "dropped_not_derived": not_derived,
                "preserved_not_derived": preserved,
                "excluded": merged["excluded"],
                "dropped_repointed": merged["repointed"],
                "source_commit": sc,
                "source_commit_time": sct,
                "changed": changed,
            }
        )
        if changed and not dry_run:
            from .atomic import write_text_atomic

            write_text_atomic(path, new_text)  # COR-18: never a torn corpus file
    except Exception as exc:  # never break a corpus-wide backfill on one file
        result["error"] = str(exc)
    return result


def _is_memory_filename(name: str) -> bool:
    """THE corpus-membership filter — one definition, shared with the edge cache's
    scandir stat sweep (GRA-6), which must see exactly the files ``_iter_memory_files``
    yields or the cache-freshness check would silently drift from the graph builder.

    ``CONVENTIONS.md`` (DOC-6) is excluded the same canonical way as ``MEMORY.md`` /
    ``MEMORY.full.md`` — it is a reference doc seeded into the corpus by ``/hippo:init``, not
    a memory, and must never be indexed, recalled, floor-scanned, or counted in corpus stats.
    """
    return name.endswith(".md") and name not in ("MEMORY.md", "MEMORY.full.md", "CONVENTIONS.md")


def _iter_memory_files(memory_dir: str):
    for name in sorted(os.listdir(memory_dir)):
        if _is_memory_filename(name):
            yield os.path.join(memory_dir, name)


def heal_empty_baselines(memory_dir: str, repo_root: str) -> Tuple[List[str], Dict[str, str]]:
    """Set ``source_commit`` to HEAD for memories whose baseline is EMPTY.

    Returns ``(healed_names, failed)`` where ``failed`` maps name → reason for files
    that SHOULD have healed but whose write failed (RCH-9: a silently skipped failure
    left the memory invisible to staleness forever while the verb reported success —
    every problem comes back in the one result).

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
    failed: Dict[str, str] = {}
    try:
        head = git_head(repo_root)
        if not head or not os.path.isdir(memory_dir):
            return [], {}
        for path in _iter_memory_files(memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
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
                        try:
                            from .atomic import write_text_atomic

                            write_text_atomic(path, "\n".join(lines))  # COR-18
                        except Exception as exc:
                            failed[stem] = str(exc)  # RCH-9: named, never dropped
                            break
                        healed.append(stem)
                        break
            except Exception:
                continue  # unreadable file — the integrity producer owns those
    except Exception:
        return healed, failed
    return healed, failed


def backfill_corpus(
    memory_dir: str, repo_root: str, dry_run: bool = False, refresh: bool = False
) -> List[dict]:
    repo_files, basename_index = build_repo_file_index(repo_root)
    return [
        backfill_file(p, repo_root, repo_files, basename_index, dry_run=dry_run, refresh=refresh)
        for p in _iter_memory_files(memory_dir)
    ]


_LAST_VERIFIED_RE = re.compile(r"\s*last_verified\s*:")
_VERIFIED_BY_KEY_RE = re.compile(r"^(\s*)verified_by\s*:")


def _has_last_verified(fm_lines: List[str]) -> bool:
    return any(_LAST_VERIFIED_RE.match(ln) for ln in fm_lines)


def _strip_verified_by(text: str) -> str:
    """Remove any existing ``verified_by`` key (body verbatim) — the refresh half of
    CLB-2's per-verification stamp: reverify strips + re-stamps, so the file always
    carries exactly ONE ``verified_by``, the latest verdict's."""
    return strip_frontmatter_keys(text, _VERIFIED_BY_KEY_RE)


def _stamp_verified_by(text: str, value: str) -> str:
    """Insert ``verified_by: "<slug>@<own-ts>"`` — CLB-2's per-verification attribution.

    UNLIKE ``_stamp_last_verified`` (write-once, the FIRST confirmation), this stamp is
    REFRESHED on every human-gated reverify verdict: WHO last vouched for this memory
    and WHEN, with its own timestamp decoupled from ``last_verified``. Callers strip any
    existing key first (``_strip_verified_by``); the defensive absent-check here mirrors
    its sibling. Additive and absence-emits-nothing: never a ranking input (AST-pinned),
    read only by report-time consumers. The body is never touched. No-op on unfenced
    frontmatter (the same guard every writer here uses).
    """
    if not text.startswith(_FENCE):
        return text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return text
    fm = lines[1:close]
    if any(_VERIFIED_BY_KEY_RE.match(ln) for ln in fm):
        return text
    new_key = f"verified_by: {json.dumps(value)}"
    return "\n".join([lines[0]] + insert_frontmatter_keys(fm, [new_key]) + lines[close:])


def _stamp_last_verified(text: str, ts: str) -> str:
    """Insert an ADDITIVE ``last_verified: "<ts>"`` frontmatter key — RET-6's reinforcement
    stamp. WRITE-ONCE: callers only reach this after confirming the key is absent (this
    internal ``_has_last_verified`` check is a defensive second guard, same belt-and-suspenders
    style ``backfill_text``'s own ``_has_cited_paths`` check already has) — a file that
    already carries the key is returned byte-identical, never re-timestamped. Records WHEN a
    human first confirmed this memory (graduate/fix), distinct from ``source_commit_time``
    (WHICH commit the CITED CODE was at) — the banner-clearing signal itself is
    ``source_commit``, re-baselined on every reverify regardless of this stamp. Nests under an
    existing ``metadata:`` block, mirroring ``backfill_text``'s own new-key insertion, so a
    reader finds it wherever the file's other provenance keys already live. The body is never
    touched. No-op on unfenced frontmatter (the same guard every writer here uses).
    """
    if not text.startswith(_FENCE):
        return text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return text
    fm = lines[1:close]
    if _has_last_verified(fm):
        return text
    new_key = f"last_verified: {json.dumps(ts)}"
    return "\n".join([lines[0]] + insert_frontmatter_keys(fm, [new_key]) + lines[close:])


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

    ``dropped_citations`` (LIF-3): cited paths in the frontmatter BEFORE this re-derivation
    that are absent AFTER — same contract as ``backfill_file``'s; a drop (especially to
    zero, which makes the memory staleness-exempt) must be surfaced by the caller, never
    a silent shrink.

    RET-6 reinforcement: also stamps ``last_verified`` — but only the FIRST time this
    memory is ever re-verified (write-once via ``_stamp_last_verified``; a memory reverified
    a second time keeps its original stamp, never a running log of every re-check). This is
    supplementary provenance — the signal that actually clears RET-6's drift banner is
    ``source_commit`` itself, re-baselined to HEAD on EVERY call above, which is why a
    reinforced memory drops out of the next SessionStart's ``find_stale`` scan (and thus
    ``stale.json``) regardless of whether ``last_verified`` was already set.

    CLB-2 attribution: also REFRESHES ``verified_by: "<slug>@<own-ts>"`` on every verdict
    (strip + re-stamp — the file carries exactly one, the latest). This deliberately
    narrows the old byte-idempotence: the provenance triplet + ``last_verified`` remain
    idempotent, but a repeat verdict is itself a state change (WHO last vouched, WHEN),
    so ``changed`` is True per verdict. Never a ranking input (AST-pinned); consumers
    are report-time only (doctor/scorecard team coverage, suppressed at ≤1 git author).
    """
    result = {
        "path": path,
        "changed": False,
        "cited": [],
        "dropped_citations": [],
        "dropped_gone": [],
        "dropped_not_derived": [],
        "preserved_not_derived": [],
        "excluded": [],
        "dropped_repointed": {},
        "source_commit": None,
        "source_commit_time": None,
        "last_verified": None,
        "verified_by": None,
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
        fm = parse_frontmatter(text)
        if not fm:
            # Unparseable frontmatter: re-baselining would rewrite an already-broken file AND
            # silently move the baseline. Refuse loudly (fix the YAML first) — same guard as the
            # refresh path; find_unparseable / the integrity producer surface these.
            result["error"] = "unparseable frontmatter — refusing to re-baseline (fix the YAML)"
            return result
        sc, sct = git_head_with_time(repo_root)
        # CUR-1 + CUR-2: the ONE merge policy — see backfill_file. A human re-verifying
        # CONTENT must not silently lose the curated citations, nor regain an excluded one.
        merged = derive_citations(body, fm, repo_files, basename_index)
        cited, preserved, dropped = merged["cited"], merged["preserved"], merged["dropped"]
        # LIF-4: partition where repo_files is in scope — see backfill_file.
        gone, not_derived = partition_dropped(_uncaused_drops(merged), repo_files)
        stripped = _strip_verified_by(_strip_invalid_after(_strip_provenance(text)))
        # RET-6: last_verified is write-once — a memory that already carries one keeps its
        # FIRST confirmation timestamp; only an as-yet-never-verified memory gets stamped.
        # Stamped BEFORE backfill_text re-inserts cited_paths/source_commit/source_commit_time
        # (not after) so the key lands in the SAME relative position every call — `_strip_provenance`
        # never removes it, so an append-after-backfill_text ordering would flip on the very next
        # call (the triplet always re-lands at fm's tail while last_verified sat still), breaking
        # the triplet's idempotence contract on the SECOND reverify, not the first.
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        existing_lv = fm.get("last_verified")
        if existing_lv is None:
            existing_lv = meta.get("last_verified")
        if isinstance(existing_lv, str) and existing_lv.strip():
            lv = existing_lv
            pre_stamp = stripped  # already present -- untouched by the strip above
        else:
            lv = datetime.now(timezone.utc).isoformat()
            pre_stamp = _stamp_last_verified(stripped, lv)
        # CLB-2: verified_by refreshes on EVERY verdict (stripped above, re-stamped here) —
        # the latest vouch's identity + its own timestamp, decoupled from write-once
        # last_verified. Same pre-backfill ordering rationale as the stamp above.
        vb = f"{current_user_slug(repo_root)}@{datetime.now(timezone.utc).isoformat()}"
        pre_stamp = _stamp_verified_by(pre_stamp, vb)
        new_text, _ = backfill_text(pre_stamp, cited, sc, sct)
        changed = new_text != text  # triplet+last_verified idempotent; verified_by refreshes
        # COR-9 — see backfill_file's guard. A re-verify additionally owns `invalid_after`
        # (it strips it: a confirmation re-opens the validity window), `verified_by`
        # (strip + re-stamp on every verdict — CLB-2), and MAY ADD `last_verified` — but
        # only when the file carries none. RET-6's stamp is write-once, so an EXISTING
        # last_verified is a key this writer does not own, and saying otherwise would
        # blind the guard to a fold INTO it (the exact damage seen in the wild).
        owned = _PROVENANCE_OWNED | {"invalid_after", "verified_by"}
        if not _has_last_verified(fm_lines):
            owned = owned | {"last_verified"}
        damage = _frontmatter_damage(text, new_text, owned) if changed else None
        if damage:
            result["error"] = f"refusing to write: {damage} — this is a hippo bug, please report it"
            return result
        result.update(
            {
                "cited": cited,
                "dropped_citations": dropped,
                "dropped_gone": gone,
                "dropped_not_derived": not_derived,
                "preserved_not_derived": preserved,
                "excluded": merged["excluded"],
                "dropped_repointed": merged["repointed"],
                "source_commit": sc,
                "source_commit_time": sct,
                "last_verified": lv,
                "verified_by": vb,
                "changed": changed,
            }
        )
        if changed and not dry_run:
            from .atomic import write_text_atomic

            write_text_atomic(path, new_text)  # COR-18: never a torn corpus file
        if not dry_run:
            # SEC-6: a re-verify IS a per-item human review of this exact file — fold its
            # current bytes into the trusted-corpus consent baseline (review = consent;
            # a no-op on legacy fingerprint-less records and ungated corpora). Runs on
            # the no-op path too: "I re-read it and it's correct" consents the bytes that
            # were read, whether or not the provenance lines moved.
            # BND-3: an anomalous fold failure rides the result additively.
            try:
                from .trust import record_authored_write_disclosing

                note = record_authored_write_disclosing(os.path.dirname(path), path, repo_root)
                if note:
                    result["consent_note"] = note
            except Exception:
                pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _uncaused_drops(merged: dict) -> List[str]:
    """The drops ``merge_citations`` did NOT already attribute — an excluded path (CUR-2)
    is a human's prune and a re-pointed one (ORC-4) a named machine mistake; only what is
    left is LIF-4's to partition into gone / not-derived."""
    named = set(merged["excluded"]) | set(merged["repointed"])
    return [p for p in merged["dropped"] if p not in named]


def partition_dropped(dropped: List[str], repo_files: set) -> Tuple[List[str], List[str]]:
    """Split dropped citations into ``(gone, not_derived)`` — LIF-4.

    ``gone``        — the path is NOT in the repo file index: renamed or deleted. LIF-3's
                      original case, and the only one "no longer in the repo" describes.
    ``not_derived`` — the path IS still in the repo, but the body no longer yields a token
                      for it. Causes: an extractor gap (it cannot produce the token at all),
                      a hand-edited frontmatter entry being overwritten by the re-derivation,
                      a body edit that removed the mention, or ambiguity-by-addition (a new
                      same-basename file makes a bare citation ambiguous, which
                      ``resolve_citations`` drops by documented design).

    The distinction is not cosmetic: ``gone`` means go look at the code, ``not_derived``
    means go look at hippo or at the memory's body. Reporting the second as the first sends
    the reader hunting for a deletion that never happened.

    CUR-1 note: the in-tree producers no longer DROP the ``not_derived`` class at all —
    those paths are preserved (``preserved_not_derived``), so this partition returns an
    empty second half for every result they emit today. The function and the renderer's
    clause for it stay: a pre-CUR-1 result dict (an old journal, a replayed telemetry
    record) must still render truthfully.
    """
    gone = [p for p in dropped if p not in repo_files]
    not_derived = [p for p in dropped if p in repo_files]
    return gone, not_derived


def _rot_clause(paths: List[str], verb: str, reason: str, *, emphasise_all: bool = False) -> str:
    shown = ", ".join(paths[:6])
    more = f" (+{len(paths) - 6} more)" if len(paths) > 6 else ""
    count = f"ALL {len(paths)}" if emphasise_all else str(len(paths))
    return f"{verb} {count} cited path(s) {reason} ({shown}{more})"


def citation_rot_lines(name: str, result: dict, *, dry_run: bool = False) -> List[str]:
    """The ONE rendering of a per-file citation-drop event (LIF-3/LIF-4).

    Shared by this module's CLI (``--refresh`` / ``--refresh-one`` / ``--reverify``),
    ``reconsolidate``'s ``--reverify`` and the ``reconsolidate`` MCP tool, so the loud line
    cannot drift between surfaces. Takes a producer ``result`` dict whole — the partition is
    computed where ``repo_files`` is in scope (``backfill_file`` / ``reverify_file``), never
    re-guessed here, because two of the four call sites have no repo index to check against.

    LIF-4: this used to assert every dropped path was "no longer in the repo" while
    ``dropped`` was computed as a set-difference against the re-derived list — a membership
    test that never ran, over an oracle (``repo_files``) that was a parameter of the very
    function that computed it. A citation the extractor simply failed to re-derive was
    reported as a deleted file. ``staleness.find_citation_rot`` — this function's own
    self-declared sibling — earns the same phrase with a real membership test; this one
    borrowed the sentence without the test.

    A drop to ZERO is still called out distinctly: with no cited_paths left, ``find_stale``
    has nothing to watch — the memory becomes staleness-EXEMPT, the worst rot state, not a
    cosmetic shrink. Returns ``[]`` when nothing was dropped AND nothing was preserved.

    CUR-1: a result carrying ``preserved_not_derived`` also gets an ``ℹ kept`` line —
    informational, appended after any rot line — naming the citations that survived a
    re-derivation the extractor could not reproduce, so deliberate pruning stays visible
    and manual rather than an automatic loss.
    """
    # CUR-2: a path the memory's own `cited_paths_exclude` held out is a deliberate human
    # prune — never rot, so it leaves the ⚠ accounting and gets its own ℹ line.
    excluded = result.get("excluded") or []
    dropped = [p for p in (result.get("dropped_citations") or []) if p not in excluded]
    # CUR-1: preserved-but-not-derivable citations get an INFORMATIONAL line, not a ⚠ —
    # nothing was lost; the reader just learns the body no longer carries the token (a
    # hand-curated entry, an extractor gap, or an edited-away mention) and that pruning
    # is theirs to do deliberately, by editing the frontmatter.
    preserved = result.get("preserved_not_derived") or []
    keep_lines: List[str] = []
    if preserved:
        shown = ", ".join(preserved[:6])
        more = f" (+{len(preserved) - 6} more)" if len(preserved) > 6 else ""
        keep_lines = [
            f"ℹ kept — {name}: {len(preserved)} cited path(s) still in the repo but not "
            f"derivable from the body ({shown}{more}) — hand-curated or an extractor gap; "
            "to prune one deliberately, delete it from `cited_paths` (a path the body "
            "still names comes back on the next derivation unless you also list it under "
            "`cited_paths_exclude:`)"
        ]
    if excluded:
        shown = ", ".join(excluded[:6])
        more = f" (+{len(excluded) - 6} more)" if len(excluded) > 6 else ""
        tail = ""
        if not (result.get("cited") or []) and not dropped:
            state = "would be" if dry_run else "is now"
            tail = (f" — cited_paths {state} EMPTY, so this memory is EXEMPT from staleness "
                    "tracking")
        keep_lines.append(
            f"ℹ excluded — {name}: {len(excluded)} path(s) held out by this memory's "
            f"`cited_paths_exclude` ({shown}{more}) — a deliberate prune, honoured by every "
            f"derivation; edit that list to restore one{tail}"
        )
    if not dropped:
        return keep_lines
    cited_after = result.get("cited") or []
    # Fall back to "all gone" only if a producer predates the partition — never re-derive it
    # here from a repo index this function does not have.
    gone = result.get("dropped_gone")
    not_derived = result.get("dropped_not_derived")
    if gone is None and not_derived is None:
        gone, not_derived = dropped, []
    gone, not_derived = list(gone or []), list(not_derived or [])
    # ORC-4: a stored path only the pre-v5 basename fallback ever bound — named with the
    # token that caused it, because "the body names a DIFFERENT directory" is checkable.
    repointed = result.get("dropped_repointed") or {}
    gone = [p for p in gone if p not in repointed]
    not_derived = [p for p in not_derived if p not in repointed]

    verb = "would drop" if dry_run else "dropped"
    # "ALL n" only when this single cause accounts for the whole drop AND nothing survived —
    # with two causes in play neither one is "all", and claiming otherwise is the same kind
    # of unearned assertion LIF-4 exists to remove.
    def _all(paths):
        return not cited_after and len(paths) == len(dropped)

    clauses = []
    if gone:
        clauses.append(_rot_clause(gone, verb, "no longer in the repo", emphasise_all=_all(gone)))
    if not_derived:
        clauses.append(
            _rot_clause(
                not_derived,
                verb,
                "still in the repo but no longer derived from the body — an extractor gap, "
                "a hand-edited frontmatter entry being overwritten, or the body no longer "
                "citing them",
                emphasise_all=_all(not_derived),
            )
        )
    if repointed:
        pairs = [f"{p} ← `{tok}`" for p, tok in repointed.items()]
        clauses.append(
            _rot_clause(
                pairs,
                verb,
                "that an older extractor bound by basename alone — the body names a "
                "DIFFERENT directory, so this was never this memory's file (ORC-4)",
                emphasise_all=_all(pairs),
            )
        )
    head = f"⚠ citation rot — {name}: " + "; ".join(clauses)
    if not cited_after:
        state = "would be" if dry_run else "is now"
        return [
            f"{head} — cited_paths {state} EMPTY, so this memory is EXEMPT from staleness "
            "tracking until its body cites current code again"
        ] + keep_lines
    return [f"{head}; {len(cited_after)} citation(s) remain"] + keep_lines


# --------------------------------------------------------------------------- #
# MIG-1 — the consented re-derivation (the THIRD verb)
# --------------------------------------------------------------------------- #
def rederive_preview(path: str, repo_root: str, repo_files: set, basename_index: Dict[str, List[str]]) -> dict:
    """What re-deriving ONE memory's citations WOULD change — read-only. Never raises.

    ``{"name", "before", "after", "gained", "lost", "kept", "excluded", "unresolved",
    "changed", "error"}``. ``kept`` (CUR-1) is the preserved set: still in the repo, not
    derivable from the body, carried through unchanged by an apply. ``excluded`` (CUR-2) is
    what the memory's own ``cited_paths_exclude`` held out — which is what lets a
    deliberate prune of a DERIVABLE path stay pruned, and so lets the worklist empty.
    The review payload for the worklist below: the operator sees the attributed diff for
    THIS memory and approves THIS memory, which is what makes the fold that follows a
    legitimate SEC-6 consent rather than the gate consenting to itself.
    """
    out = {
        "name": os.path.basename(path)[:-3],
        "before": [], "after": [], "gained": [], "lost": [], "kept": [], "excluded": [],
        "repointed": {}, "unresolved": [], "changed": False, "error": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm_lines, body = split_frontmatter(text)
        if fm_lines is None:
            # MIG-2: NOT "unparseable" — that sends the reader hunting a YAML error in a
            # file that has no YAML at all. A different defect with a different remedy.
            out["error"] = (
                "no frontmatter — the file does not open with a `---` fenced block, so it "
                "is not a recall-ready memory (add frontmatter, or move it out of the corpus)"
            )
            return out
        fm = parse_frontmatter(text)
        if not fm:
            out["error"] = "unparseable frontmatter — fix the YAML first"
            return out
        before = _frontmatter_cited_paths(fm)
        # CUR-1/CUR-2: the preview MUST run the producers' ONE merge policy — the stamp's
        # earned-empty-worklist check compares this preview's `changed` against what
        # `rederive_file` would write; if only one side preserved (or excluded), a curated
        # corpus could never stamp (or worse, stamp while still differing).
        merged = derive_citations(body, fm, repo_files, basename_index)
        after = merged["cited"]
        out.update(
            before=before,
            after=after,
            gained=[p for p in after if p not in before],
            lost=[p for p in before if p not in after],
            kept=merged["preserved"],
            excluded=merged["excluded"],
            repointed=merged["repointed"],
            unresolved=unresolved_citations(body, repo_files, basename_index),
            changed=sorted(before) != sorted(after),
        )
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out


def rederive_worklist(memory_dir: str, repo_root: str) -> List[dict]:
    """Every memory whose citations would change under this plugin's extractor (MIG-1).

    Read-only. The operator reviews these, then approves them ONE AT A TIME via
    ``rederive_file`` — there is deliberately no "approve all".
    """
    repo_files, basename_index = build_repo_file_index(repo_root)
    out = []
    for path in _iter_memory_files(memory_dir):
        pv = rederive_preview(path, repo_root, repo_files, basename_index)
        if pv["changed"] or pv["error"]:
            out.append(pv)
    return out


def rederive_file(
    path: str,
    repo_root: str,
    repo_files: set,
    basename_index: Dict[str, List[str]],
    *,
    dry_run: bool = False,
) -> dict:
    """Re-derive ONE memory's cited_paths after a human reviewed THIS memory's diff (MIG-1).

    The third verb, and it exists because neither of the other two can carry a corpus-wide
    extractor fix — they are each correct, and each wrong for this:

      --refresh   re-derives and PRESERVES source_commit (right), but never folds the write
                  into the consent baseline (right — it is a bulk pass, and
                  trust.record_authored_write forbids that by name: "an unattended
                  re-baseline would be the gate consenting to itself"). So it rewrites N
                  files, drifts every one of them off its SEC-6 fingerprint, and recall
                  WITHHOLDS them — handing the user N mystery quarantines whose banner
                  blames "a git pull? a hand edit?" for hippo's own write.
      --reverify  folds (right — a re-verify IS a per-item human review) but re-baselines
                  source_commit to HEAD, which SILENTLY CLEARS every staleness flag the
                  corpus is carrying. It would trade a citation bug for the total loss of
                  the signal citations exist to serve.

    This one re-derives + PRESERVES the baseline + folds — legitimate only because the
    caller has shown the operator THIS file's attributed diff and taken THIS file's
    approval. That is the same condition reverify_file's own comment names ("a re-verify IS
    a per-item human review of this exact file"); the difference is what is being reviewed
    (the citation diff, not the memory's content), so the staleness baseline is untouched.

    NOT autonomous, and there is no bulk counterpart on purpose. Never raises.
    """
    result = {
        "path": path, "name": os.path.basename(path)[:-3], "changed": False,
        "cited": [], "dropped_citations": [], "dropped_gone": [],
        "dropped_not_derived": [], "preserved_not_derived": [], "excluded": [],
        "dropped_repointed": {}, "gained": [], "lost": [], "baseline": None, "error": None,
    }
    try:
        # MIG-2: a preview taken NOW, against the same index the write uses. The worklist
        # the operator reviewed was computed against `git ls-files` at ITS call time; a
        # sibling session staging files in between changes what this write derives, and
        # the full `cited` list alone made that easy to miss. `gained`/`lost` are relative
        # to the STORED frontmatter, so the caller can print them and drift is loud.
        pv = rederive_preview(path, repo_root, repo_files, basename_index)
        if not pv.get("error"):
            result["gained"], result["lost"] = pv["gained"], pv["lost"]
        # backfill_file(refresh=True) already does exactly the derivation half correctly —
        # it preserves source_commit AND live curated citations (CUR-1), partitions the
        # loss (LIF-4), and refuses to damage a key it does not own (COR-9). Reuse it
        # rather than re-implement its rules.
        bf = backfill_file(path, repo_root, repo_files, basename_index, dry_run=dry_run, refresh=True)
        result.update({k: bf[k] for k in
                       ("changed", "cited", "dropped_citations", "dropped_gone",
                        "dropped_not_derived", "preserved_not_derived", "excluded",
                        "dropped_repointed", "baseline", "source_commit", "error")
                       if k in bf})
        if bf.get("error"):
            return result
        if bf.get("changed") and not dry_run:
            # SEC-6: the operator reviewed THIS file's diff and approved THIS file, so its
            # new bytes join the consent baseline. Without this the migration quarantines
            # every memory it fixes. WITH it on a bulk pass it would be self-consent — which
            # is why this call lives here, behind a per-item approval, and NOT in
            # backfill_file (whose --refresh path has no reviewer).
            # BND-3: an anomalous fold failure rides the result additively.
            try:
                from .trust import record_authored_write_disclosing

                note = record_authored_write_disclosing(os.path.dirname(path), path, repo_root)
                if note:
                    result["consent_note"] = note
            except Exception:
                pass
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def rederive_worklist_lines(work: List[dict]) -> List[str]:
    """The per-memory lines of a MIG-1 worklist — ONE rendering for the CLI and the MCP
    tool (each adds its own header/footer), so the two surfaces cannot drift."""
    out: List[str] = []
    for w in work:
        if w["error"]:
            out.append(f"  ✘ {w['name']}: {w['error']}")
            continue
        out.append(f"  {w['name']}")
        if w["gained"]:
            out.append(f"      + gains  : {', '.join(w['gained'])}")
        if w["lost"]:
            out.append(f"      - loses  : {', '.join(w['lost'])}")
        for path, tok in (w.get("repointed") or {}).items():
            out.append(f"          ↳ {path} was bound by basename alone from `{tok}` — the "
                       "body names a different directory (ORC-4)")
        if w.get("kept"):
            out.append(f"      = keeps  : {', '.join(w['kept'])} (still in the repo, not "
                       "derivable from the body — preserved, CUR-1)")
        if w.get("excluded"):
            out.append(f"      ⊘ excludes: {', '.join(w['excluded'])} (this memory's "
                       "`cited_paths_exclude` — a deliberate prune, CUR-2)")
        if w["unresolved"]:
            out.append(f"      ? unresolved in body: {', '.join(w['unresolved'])}")
    return out


# CUR-2: the worklist's standing answer to "this gain is WRONG for this memory" — before
# the exclusion existed the only advice was a hand prune that the next preview undid.
REDERIVE_EXCLUDE_HINT = (
    "A gain that is wrong for a memory (another repo's file, a generic basename like "
    "`plan.json` that merely happens to be unique here)? List the path under "
    "`cited_paths_exclude:` in that memory's frontmatter (beside `cited_paths`) — every "
    "derivation honours it, none ever writes it, and the memory leaves this worklist."
)


def rederive_one_lines(base: str, r: dict, *, dry_run: bool = False) -> List[str]:
    """The ONE rendering of a ``rederive_file`` result (CLI ``--rederive-one`` and the MCP
    tool's ``action='one'``). MIG-2: says what ACTUALLY happened to the baseline, and
    prints the call-time ``gained``/``lost`` against the stored frontmatter so an index
    that moved since the operator's worklist read is loud, not buried in the full list."""
    verb = "would re-derive" if dry_run else "re-derived"
    lines = [f"{verb} {base}: cited_paths = {r['cited']}"]
    lines.append(
        f"  gained: {', '.join(r.get('gained') or []) or '(none)'}   "
        f"lost: {', '.join(r.get('lost') or []) or '(none)'}   — vs this memory's stored "
        "cited_paths, previewed at call time against the CURRENT git index. If that is not "
        "the diff you reviewed, the index moved since your worklist read (a sibling "
        "staged or removed files) — re-read before the next one."
    )
    lines += citation_rot_lines(base, r, dry_run=dry_run)
    if not dry_run and r.get("changed"):
        sha = (r.get("source_commit") or "")[:9] or "unresolved"
        if r.get("baseline") == "initial":
            what = (
                "this memory carried NO provenance, so there was no source_commit to "
                f"preserve — it was baselined to its file's last commit ({sha}), exactly "
                "as a first backfill would"
            )
        elif r.get("baseline") == "assigned":
            what = (
                "this memory carried cited_paths but NO source_commit — nothing to "
                f"preserve, so one was assigned: its file's last commit ({sha})"
            )
        else:
            what = ("source_commit PRESERVED (this is not a re-verify — no staleness flag "
                    "was cleared)")
        # BND-3: the folded-into-consent claim was unconditional — false whenever the
        # fold anomalously failed. State whichever actually happened.
        if r.get("consent_note"):
            lines.append(f"{what}; ⚠ {r['consent_note']}.")
        else:
            lines.append(
                f"{what}; the reviewed bytes were folded into the consent baseline, so "
                "the memory is not quarantined."
            )
    return lines


def snapshot_corpus(memory_dir: str, stamp: str) -> str:
    """Copy the corpus to a sibling ``memory.pre-cite2-<stamp>/`` before the first write.

    MANDATORY before MIG-1's first non-dry write, and not merely belt-and-braces: hippo
    SHIPS expecting a committed corpus (``.claude/memory/`` is deliberately absent from
    init's GITIGNORE_ENTRIES, and the README says the corpus "stays committed in git"), so
    upstream a re-derivation is undoable with ``git checkout``. A corpus the user chose to
    gitignore has NO undo — and that is the first corpus this migration will ever touch.
    Returns the snapshot path. Raises on failure: no snapshot, no migration.

    SELF-IGNORING (SEC-3), and this is load-bearing rather than tidy. The snapshot is a
    verbatim copy of the corpus, but it is NOT the corpus: a project that gitignores
    ``.claude/memory/`` does not thereby ignore ``.claude/memory.pre-cite2-*``, so the
    snapshot lands as a fresh untracked directory holding every private memory — one
    ``git add -A`` from being committed, in a repo that may well be public. Whatever
    exposure rule the corpus lives under, its backup must inherit; the copy must not be the
    thing that publishes it. Written BEFORE the payload so the window never exists.
    """
    import shutil

    dest = os.path.join(os.path.dirname(memory_dir), f"memory.pre-cite2-{stamp}")
    if os.path.exists(dest):
        raise FileExistsError(f"{dest} already exists — refusing to overwrite a snapshot")
    ensure_self_ignoring_dir(dest)  # the `*` marker lands first — no unignored window
    for entry in os.listdir(memory_dir):
        src = os.path.join(memory_dir, entry)
        dst = os.path.join(dest, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return dest


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
    parser.add_argument(
        "--heal-baselines",
        action="store_true",
        help="COR-10: set source_commit to HEAD for memories whose baseline is EMPTY (a "
        "memory with one is invisible to staleness forever). This used to run silently on "
        "every SessionStart — a hook writing to memory frontmatter, which drifted each file "
        "off its own SEC-6 fingerprint and then blamed the user for the drift. It is a "
        "write, so it lives here, where you ran it on purpose.",
    )
    parser.add_argument(
        "--rederive-worklist",
        action="store_true",
        help="MIG-1: list every memory whose cited_paths would CHANGE under this plugin's "
        "extractor, with the attributed diff. Read-only — review this, then approve one "
        "memory at a time with --rederive-one.",
    )
    parser.add_argument(
        "--rederive-one",
        metavar="NAME",
        default=None,
        help="MIG-1: re-derive ONE memory's cited_paths after you have reviewed ITS diff. "
        "Re-derives + PRESERVES source_commit (unlike --reverify, which resets it to HEAD "
        "and silently clears every staleness flag) + folds the reviewed bytes into the "
        "consent baseline (unlike --refresh, which would leave the memory quarantined). "
        "Per-item by design; there is no bulk form.",
    )
    parser.add_argument(
        "--snapshot",
        metavar="STAMP",
        default=None,
        help="MIG-1: copy the corpus to memory.pre-cite<N>-<STAMP>/ before migrating. A "
        "gitignored corpus has no `git checkout` undo — take this first.",
    )
    parser.add_argument(
        "--stamp-derivation",
        action="store_true",
        help="MIG-1's LAST step: record that this corpus's citations were derived by THIS "
        "plugin's extractor, which stops the citation-derivation nudge. Refused while any "
        "memory still derives differently — the stamp asserts a derivation, so it must be "
        "earned (an empty worklist) rather than claimed.",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md
    repo_root = args.repo_root or repo

    if args.snapshot:
        try:
            dest = snapshot_corpus(memory_dir, args.snapshot)
            print(f"snapshot: {dest}")
            return 0
        except Exception as exc:
            print(f"snapshot FAILED: {exc} — do not migrate without one")
            return 1

    if args.heal_baselines:
        healed, heal_failed = heal_empty_baselines(memory_dir, repo_root)
        print(f"healed {len(healed)} empty baseline(s)" + (f": {', '.join(healed)}" if healed else ""))
        if heal_failed:  # RCH-9: failures are part of the result
            print(f"FAILED to heal {len(heal_failed)} (still invisible to staleness):")
            for n, reason in sorted(heal_failed.items()):
                print(f"  - {n}: {reason}")
            return 1
        return 0

    if args.stamp_derivation:
        work = rederive_worklist(memory_dir, repo_root)
        if work:
            print(
                f"refused to stamp — {len(work)} memory(ies) still derive differently under "
                "this plugin's extractor. Stamping now would assert a derivation this corpus "
                "does not have, which is the one thing the marker exists to prevent. Run "
                "--rederive-worklist, apply each with --rederive-one, then stamp."
            )
            return 1
        was = read_cite_derivation(memory_dir)
        if was >= CITATION_DERIVATION_VERSION:
            print(f"already stamped cite_derivation={was} — nothing to do.")
            return 0
        if not write_cite_derivation(memory_dir):
            print("stamp FAILED to write .format — check the corpus dir is writable.")
            return 1
        print(
            f"stamped cite_derivation: {was} → {CITATION_DERIVATION_VERSION} "
            "(earned: 0 memories derive differently)."
        )
        return 0

    if args.rederive_worklist:
        work = rederive_worklist(memory_dir, repo_root)
        if not work:
            print("re-derivation worklist: empty — every memory's citations already match "
                  "this plugin's extractor.")
            return 0
        print(f"re-derivation worklist: {len(work)} memory(ies) would change\n")
        print("\n".join(rederive_worklist_lines(work)))
        print("\nReview each, then approve individually: "
              "python -m memory.provenance --rederive-one <name>")
        print(REDERIVE_EXCLUDE_HINT)
        return 0

    if args.rederive_one:
        repo_files, basename_index = build_repo_file_index(repo_root)
        name = args.rederive_one if args.rederive_one.endswith(".md") else f"{args.rederive_one}.md"
        target = os.path.join(memory_dir, name)
        r = rederive_file(target, repo_root, repo_files, basename_index, dry_run=args.dry_run)
        base = os.path.basename(target)
        if r["error"]:
            print(f"rederive {base}: refused — {r['error']}")
            return 1
        print("\n".join(rederive_one_lines(base, r, dry_run=args.dry_run)))
        return 0

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
        for ln in citation_rot_lines(base, r, dry_run=args.dry_run):
            print(ln)
        if r.get("consent_note"):  # BND-3: the one write-moment disclosure line
            print(f"⚠ {r['consent_note']}")
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
        for ln in citation_rot_lines(base, r, dry_run=args.dry_run):
            print(ln)
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
    # LIF-3: a re-derivation that DROPPED citations is a rot event, not a cosmetic shrink —
    # every drop is named per-file (drop-to-zero loudest), never buried in the counts above.
    rotted = [r for r in results if r["dropped_citations"]]
    if rotted:
        print(f"citation rot         : {len(rotted)} file(s) {'would drop' if args.dry_run else 'dropped'} cited path(s)")
        for r in rotted:
            for ln in citation_rot_lines(
                os.path.basename(r["path"]), r, dry_run=args.dry_run
            ):
                print(f"  {ln}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
