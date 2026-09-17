"""The CITATION layer of provenance — extractor, oracle, resolver, merge policy.

What a memory body CITES and which tracked file each token pins: the extractor's
declared vocabulary (``_CODE_EXTS`` / ``_EXTENSIONLESS_NAMES`` / ``_CITATION_RE``), the
``git ls-files`` oracle (``build_repo_file_index``), the ONE resolver
(``resolve_citations`` — ``cited_paths_for_body`` and ``unresolved_citations`` are its
two halves, so a derivation and its receipt cannot disagree), and the ONE merge policy
(``derive_citations`` → ``merge_citations``) every re-derivation surface shares: what is
derived, what is preserved (CUR-1), what a human excluded (CUR-2), and what only the
pre-ORC-4 resolver ever bound.

Decomposed out of ``provenance.py`` as pure code motion when the module-size ratchet
fired (the ORC-4/CUR-2 work); every symbol stays importable at
``memory.provenance.<name>`` via the façade's explicit re-exports. Depends only on
``provenance_env`` (the git shell-out) — never on the façade — so the
sibling-never-imports-façade rule holds (see CONTRIBUTING.md "Code layout").
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import provenance_env as _env

# Code/config extensions we treat as "cited code" for the staleness signal. .md is EXCLUDED
# (memory<->memory refs are [[wikilinks]], Tier 3; doc/changelog churn isn't code drift); .mdc
# (Cursor rules) IS included (IOP-2 — imported memories fingerprint their upstream source).
#
# ORC-1: sorted LONGEST-FIRST. This is intent-preservation, NOT the fix — the trailing
# boundary in _CITATION_RE is what makes the alternation order irrelevant (the engine
# backtracks into it and finds the longer branch itself). Kept sorted anyway so the
# declared order matches the intended precedence and a future reader is not misled into
# thinking order is load-bearing here. Adding an entry is enough to support it: the
# reachability test loops over this tuple, so a shadowed entry fails immediately.
_CODE_EXTS = (
    "tsx", "jsx", "json", "yaml", "toml", "cts", "cjs", "mts", "mjs", "mdc",
    "cfg", "ini", "yml", "sh", "ts", "js", "py",
)

# ORC-3 — extensionless config/build filenames the extractor also recognizes. A bounded
# allowlist, not "any dotless capitalized word": most of these names are ALSO ordinary
# English vocabulary ("the Dockerfile pattern is common in monorepos" is not a citation),
# so unlike _CODE_EXTS a dotted extension can't do the disambiguating work here — something
# else has to. Recognized in exactly two shapes (see _CITATION_RE):
#
#   directory-qualified, anywhere — `docker/Dockerfile` — the same leniency a dotted file
#     already gets bare (a sentence does not spontaneously produce "word/Dockerfile").
#   a WHOLE backtick span, nothing else — `` `Dockerfile` `` — mirrors
#     rules_plane._path_ref_re()'s own whole-span anchor (ORC-2's precedent, reused rather
#     than reinvented): a human who backtick-quotes a bare word is asserting "this is a
#     literal token", the same deliberate signal a dotted extension supplies structurally.
#
# A bare, UNMARKED mid-sentence mention ("see the Dockerfile") is a deliberate non-match:
# nothing syntactically distinguishes it from "the Dockerfile pattern is common in
# monorepos", and resolve_citations' own rule is under-flag beats cry-wolf. Measured
# against this repo's real corpus + docs (read-only): every genuine citation found there
# was backtick-quoted (`Dockerfile`/`CODEOWNERS` in CHANGELOG.md, `.env.example` in a real
# memory); the one bare mid-list "CODEOWNERS" mention is an accepted miss, same class as
# the CUR-1 fixture's own bare "the Dockerfile" body text, which this deliberately leaves
# non-derivable (test_refresh_preserves_a_live_not_derivable_citation_end_to_end pins the
# non-derivability — and that CUR-1 preserves the stored citation anyway).
# resolve_citations itself needed NO change — it is already extension-agnostic basename
# matching, so the existing ambiguity-drop (two same-named files -> dropped) protects an
# extensionless citation exactly as it protects a dotted one today.
_EXTENSIONLESS_NAMES = (
    "Dockerfile", "Makefile", "Procfile", "Justfile", "Rakefile", "Gemfile",
    "Vagrantfile", "CODEOWNERS", "LICENSE", ".env.example", ".nvmrc", ".python-version",
)

# A path-like token: optional dir segments + filename + a code extension, with an
# optional :line or :line-range suffix (which we drop — we track files, not lines).
#
# ORC-1 — the `(?![\w])` after the extension group is load-bearing, and its absence was
# the single defect behind two whole families of wrong citations:
#
#   prefix shadow  — with no boundary, `js` matched inside `package.json` and the pattern
#                    completed, so the token became `package.js`. Same for App.tsx -> App.ts
#                    and App.jsx -> App.js. .tsx/.jsx/.json were DECLARED in _CODE_EXTS and
#                    structurally unreachable: config that the regex could not deliver.
#   truncation     — `build.pyc` -> `build.py`, `data.jsonl` -> `data.js`, `x.tsv` -> `x.ts`,
#                    `notes.shtml` -> `notes.sh`. These FABRICATE a path that was never
#                    written; when the fabrication happens to name a real sibling file,
#                    resolve_citations keeps it and the memory is silently bound to the
#                    wrong file (DRV-1's extension check is the permanent net for that).
#
# The tail is `(?!\w|\.\w)`, and each half earns its place (DRV-1):
#
#   (?!\w)   kills the shadow + truncation families above.
#   (?!\.\w) kills the residual: `test.py.bak` -> `test.py`. A dotted SUFFIX after a
#            complete extension means the token was never this file — citing `test.py`
#            from a mention of `test.py.bak` binds the memory to the wrong real file,
#            silently, which is the worst outcome in this module.
#
# Deliberately NOT `(?![\w./-])` mirroring the lookbehind: the symmetric form reads right
# and regresses prose — "the bug is in foo.py." (end of sentence) stops matching, because
# it cannot tell a suffix from a full stop. `(?!\.\w)` can: it requires a word character
# AFTER the dot, so a sentence-ending period still matches and `.bak` does not.
# Also NOT `(?![\w.]\w)`, which looks equivalent and is strictly worse — measured, it
# re-fabricates `foo.pyx -> foo.py` and `data.jsonl -> data.json`.
#
# ORC-3 adds two more alternatives, both reusing this same leading lookbehind + trailing
# `(?!\w|\.\w)` boundary rather than inventing new ones — so `Gemfile.lock` cannot truncate
# to `Gemfile` for exactly the reason `test.py.bak` cannot truncate to `test.py`:
#
#   directory-qualified extensionless — `(?:[\w.-]+/)+(?:Dockerfile|...)` — note the `+`,
#     not the dotted branch's `*`: at least one dir segment is REQUIRED here, because
#     without a directory a bare `Dockerfile` is also just an English word (see
#     _EXTENSIONLESS_NAMES). A dotted file needs no such gate — its extension already
#     supplies the signal a directory supplies here.
#   bare-in-a-whole-backtick-span — `(?<=\`)(?:Dockerfile|...)(?::\d+(?:-\d+)?)?(?=\`)` — a
#     SEPARATE top-level alternative (its own lookaround, not the shared lookbehind/tail
#     above): the backtick must sit immediately either side of the name-plus-optional-line,
#     i.e. the entire span is the reference and nothing else, same discipline
#     rules_plane._path_ref_re() enforces with `^...$`. This is capture group 2;
#     extract_citations reads `group(1) or group(2)`.
_CITATION_RE = re.compile(
    r"(?<![\w./-])("
    r"(?:[\w.-]+/)*[\w.-]+\.(?:" + "|".join(_CODE_EXTS) + r")"
    r"|(?:[\w.-]+/)+(?:" + "|".join(re.escape(n) for n in _EXTENSIONLESS_NAMES) + r")"
    r")(?!\w|\.\w)(?::\d+(?:-\d+)?)?"
    r"|(?<=`)(" + "|".join(re.escape(n) for n in _EXTENSIONLESS_NAMES) + r")(?::\d+(?:-\d+)?)?(?=`)"
)


# --------------------------------------------------------------------------- #
# Citation extraction + resolution
# --------------------------------------------------------------------------- #
def extract_citations(body: str) -> List[str]:
    """Return the de-duplicated, order-preserving list of path-like tokens in ``body``
    (line numbers stripped)."""
    seen: set = set()
    out: List[str] = []
    for m in _CITATION_RE.finditer(body or ""):
        # ORC-3: group(1) is the dotted-or-directory-qualified-extensionless branch;
        # group(2) is the whole-backtick-span bare-extensionless branch. Exactly one is
        # populated per match — the two are separate top-level alternatives.
        tok = m.group(1) or m.group(2)
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
    files = [f for f in _env.run_git(["ls-files", "--full-name"], repo_root).split("\n") if f]
    repo_files = set(files)
    basename_index: Dict[str, List[str]] = {}
    for f in files:
        basename_index.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    return repo_files, basename_index


def _strip_relative_prefix(tok: str) -> str:
    """``./src/a.py`` -> ``src/a.py``; ``../../views-kit.js`` -> ``views-kit.js``.

    LEADING dot segments only. ``git ls-files`` never emits one, so they can never be part
    of a match (ORC-1 for ``./``; ORC-4 extends it to ``../`` — a relative import names a
    file by its TAIL, and the tail is the part the oracle can check)."""
    parts = tok.split("/")
    while len(parts) > 1 and parts[0] in (".", ".."):
        parts.pop(0)
    return "/".join(parts)


def resolve_citations(
    tokens: List[str], repo_files: set, basename_index: Dict[str, List[str]]
) -> List[str]:
    """Resolve raw tokens to repo-relative paths — ONLY when a token pins exactly one file.

    - A token that is already a tracked repo path is used as-is. A leading ``./`` is
      normalised away first (ORC-1): ``git ls-files`` never emits one, so ``./src/a.py``
      missed the exact match and fell through to the basename fallback — which DROPPED it
      whenever the basename was ambiguous. A citation written MORE precisely resolved
      WORSE than the bare basename, which is exactly backwards.
    - A bare basename is kept ONLY if it resolves to exactly ONE repo file. An AMBIGUOUS
      bare basename (e.g. ``contracts.py`` -> 52 files, ``config.py`` -> 38) is DROPPED:
      it is almost always a generic/pattern mention in prose, not a pinpoint citation, and
      keeping all candidates poisons the staleness signal (any same-named file changing
      would flag the memory). Under-flag beats cry-wolf.
    - A DIRECTORY-QUALIFIED token that is not itself a tracked path keeps a basename
      candidate only when the candidate path ENDS WITH the token (ORC-4). The fallback
      used to look up ``token.rsplit("/", 1)[-1]`` and ignore every directory the token
      named, so ``apps/api/routes/admin.py`` — a file in ANOTHER repo the memory is
      about — resolved to this repo's only ``admin.py``, and ``ingest/ga4/audit.py``
      (planned, never built) resolved to ``ingest/impact/audit.py``. Measured on a
      579-memory field corpus: 21 such resolutions, 11 wrong — each one binding a memory
      to a file its claim is not about, which this module's own comments call the worst
      outcome. A ``../``-relative token is matched by its tail and must still pin exactly
      one file. The accepted miss: a slash-joined PAIR (``patterns.json/vocabulary.ts``)
      no longer resolves its last half — under-flag, the side of the trade this function
      already chose. (Deliberately NOT done here: letting a suffix pick one file among
      several same-named ones — right in principle, but on the field corpus it turned a
      21-memory worklist into 98; it needs its own measured round.)
    - Unresolvable tokens (not in the repo) are dropped.
    """
    out: List[str] = []
    seen: set = set()
    for tok in tokens:
        norm = _strip_relative_prefix(tok)
        if norm in repo_files and not tok.startswith("../"):
            cands = [norm]
        else:
            matches = basename_index.get(norm.rsplit("/", 1)[-1], [])
            cands = matches if len(matches) == 1 else []  # drop ambiguous bare basenames
            if cands and "/" in norm and not (cands[0] == norm or cands[0].endswith("/" + norm)):
                cands = []  # ORC-4: the token's directories name a DIFFERENT path
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def cited_paths_for_body(body: str, repo_files: set, basename_index: Dict[str, List[str]]) -> List[str]:
    return resolve_citations(extract_citations(body), repo_files, basename_index)


def unresolved_citations(
    body: str, repo_files: set, basename_index: Dict[str, List[str]]
) -> List[str]:
    """Tokens the extractor produced from ``body`` that the oracle could not pin (DRV-1).

    The receipt for a derivation that came back empty-handed. A token lands here when it
    resolves to nothing: the file is untracked (written but not yet ``git add``ed — the
    index is ``git ls-files``, not the filesystem), the path is wrong, or the bare basename
    is ambiguous and ``resolve_citations`` dropped it by design.

    Without this, all three are indistinguishable from "this memory cites no code" — the
    body says ``src/thing.py`` in plain sight and ``cited_paths`` is ``[]``, which
    ``citation_rot_lines`` itself calls the worst rot state (staleness-exempt). Reuses the
    one resolver rather than re-implementing its rules, so the two can never disagree.
    """
    return [
        tok
        for tok in extract_citations(body)
        if not resolve_citations([tok], repo_files, basename_index)
    ]


def _frontmatter_cited_paths(fm: dict) -> List[str]:
    """The ``cited_paths`` a PARSED frontmatter dict already carries (both schemas).

    The "before" side of ``dropped_citations`` (LIF-3). Dict-level on purpose: the two
    result-producing callers (``backfill_file``'s refresh branch, ``reverify_file``) have
    already parsed the frontmatter, and ``staleness.read_provenance`` — the text-level
    reader with the same both-schema lookup — lives in a module that imports THIS one,
    so it cannot be reused here without a cycle.
    """
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    cited = fm.get("cited_paths")
    if cited is None:
        cited = (meta or {}).get("cited_paths")
    if not isinstance(cited, list):
        return []
    return [c for c in cited if isinstance(c, str)]


# --------------------------------------------------------------------------- #
# CUR-2 — the human-owned exclusion, and the ONE merge policy
# --------------------------------------------------------------------------- #
EXCLUDE_KEY = "cited_paths_exclude"
# Group 1 is the key's indent (``strip_frontmatter_keys``' contract). Only pack
# extraction strips this key — it names THIS repo's paths, so it is not portable. No
# derivation ever writes it: the damage guard treats it as a key the writers do not own.
_EXCLUDE_KEY_RE = re.compile(r"^(\s*)cited_paths_exclude\s*:")


def frontmatter_excluded_paths(fm: dict) -> List[str]:
    """The ``cited_paths_exclude`` list a PARSED frontmatter carries (both schemas).

    CUR-2: repo paths a HUMAN has ruled are not this memory's citations, however the body
    reads — a wrong re-point pruned by hand, or a generic basename (``plan.json``,
    ``ci.yml``, ``.env.example``) that is unique in THIS repo inside a memory plainly about
    another one, the class no mechanical rule can fix. Exact repo paths; a leading ``./``
    is normalised like a body token's. A bare string reads as a one-item list.
    """
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    raw = fm.get(EXCLUDE_KEY)
    if raw is None:
        raw = (meta or {}).get(EXCLUDE_KEY)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for p in raw:
        if isinstance(p, str) and p.strip():
            p = _strip_relative_prefix(p.strip())
            if p not in out:
                out.append(p)
    return out


def legacy_basename_repoints(
    tokens: List[str], repo_files: set, basename_index: Dict[str, List[str]]
) -> Dict[str, str]:
    """``{path: token}`` — the bindings ONLY the pre-ORC-4 resolver would have made.

    A directory-qualified token that today resolves to NOTHING, whose bare basename is
    unique in the repo: derivation ≤ v4 bound the memory to that file, ignoring every
    directory the token named. These are the wrong re-points ORC-4 exists to stop — and a
    corpus derived by v4 already STORES them, where CUR-1 would otherwise preserve each one
    forever as if a human had curated it (its file exists; the body "does not yield it").
    Identifying them is what lets a re-derivation report them as a loss WITH its cause.
    """
    out: Dict[str, str] = {}
    for tok in tokens:
        norm = _strip_relative_prefix(tok)
        if "/" not in norm or resolve_citations([tok], repo_files, basename_index):
            continue
        matches = basename_index.get(norm.rsplit("/", 1)[-1], [])
        if len(matches) == 1:
            out.setdefault(matches[0], tok)
    return out


def merge_citations(
    derived: List[str],
    stored: List[str],
    excluded: List[str],
    repo_files: set,
    repointed: Optional[Dict[str, str]] = None,
) -> dict:
    """The ONE policy that turns (derived, stored, excluded) into a memory's cited_paths.

      cited     = derived − excluded, then the PRESERVED set.
      preserved = CUR-1: a stored citation whose file still exists survives a body that
                  no longer yields it — EXCEPT a path the exclusion names (CUR-2) or one
                  only the pre-ORC-4 basename fallback ever bound (``repointed``): that
                  is a machine's mistake, not a human's curation.
      dropped   = stored paths absent from ``cited`` (LIF-3's contract, all causes).
      excluded  = the paths the exclusion actually HELD OUT this time (derived or stored)
                  — the receipt, so a deliberate prune is visible rather than silent.
      repointed = ``{path: token}`` for the stored paths dropped as ORC-4 re-points.

    Before CUR-2 a deliberate prune could not hold: ``after = derived + kept`` brought a
    derivable path straight back as a gain, the worklist never emptied, and the stamp
    refused for good — hand-editing ``cited_paths`` only ever worked for NOT-derivable
    entries.
    """
    ex = set(excluded)
    legacy = repointed or {}
    preserved = [
        p for p in stored
        if p in repo_files and p not in derived and p not in ex and p not in legacy
    ]
    cited = [p for p in derived if p not in ex] + preserved
    held = [p for p in derived if p in ex] + [p for p in stored if p in ex and p not in derived]
    return {
        "cited": cited,
        "preserved": preserved,
        "dropped": [p for p in stored if p not in cited],
        "excluded": held,
        "repointed": {p: legacy[p] for p in stored if p in legacy and p not in cited},
    }


def derive_citations(
    body: str,
    fm: dict,
    repo_files: set,
    basename_index: Dict[str, List[str]],
    *,
    use_stored: bool = True,
) -> dict:
    """Extract → resolve → merge, for ONE memory — the single entry point
    ``rederive_preview``, ``backfill_file`` (initial AND refresh) and ``reverify_file``
    share, because the MIG-1 stamp is earned by the preview agreeing with the write: a
    rule applied on one side only means a corpus that can never stamp, or one that stamps
    while still differing. ``use_stored=False`` is the initial backfill (nothing recorded
    yet, so nothing to preserve or drop — but the exclusion still binds)."""
    tokens = extract_citations(body)
    return merge_citations(
        resolve_citations(tokens, repo_files, basename_index),
        _frontmatter_cited_paths(fm) if use_stored else [],
        frontmatter_excluded_paths(fm),
        repo_files,
        legacy_basename_repoints(tokens, repo_files, basename_index),
    )
