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
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML — the pinned venv dep (full-fidelity path)
except Exception:  # pragma: no cover - bare python3 pre-bootstrap (ONB-2)
    from ._vendor import miniyaml as yaml  # type: ignore  # frontmatter-subset fallback

# Code/config extensions we treat as "cited code" for the staleness signal.
# .md is intentionally EXCLUDED — memory<->memory references are [[wikilinks]] (Tier 3),
# and doc/changelog churn is not "code drift". .mdc (Cursor rules) is INCLUDED (IOP-2):
# an imported memory cites its upstream .mdc source so drift/deletion flags via find_stale.
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
# the LIF-4 fixture's own bare "the Dockerfile" body text, which this deliberately leaves
# non-derivable (test_refresh_partitions_a_real_not_derived_drop_end_to_end pins it).
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


_GIT_ROOT_CACHE: Dict[str, Optional[str]] = {}


def git_root(start: Optional[str] = None) -> Optional[str]:
    """The git toplevel containing ``start`` (default cwd), or None. Memoized (PRF-3).

    Measured: a SessionStart spawns 5 of these and a recall 3, at ~7ms per subprocess, for a
    value that cannot change for a given ``start`` inside one process — a repo's toplevel is
    process-constant. Caching it is ~28ms off SessionStart and ~14ms off recall for no
    behaviour change (the hook's rendered output is byte-identical; a test pins that).

    Keyed on ``start`` rather than global, because the answer genuinely differs per start dir
    and the MCP server is a long-lived process serving one repo but resolving several paths.

    NOT the same call as ``build_repo_file_index``, which must NOT be cached this way: its
    answer changes the moment a file is `git add`ed, and caching it in the long-lived MCP
    server would make a file created mid-session resolve to nothing — reintroducing the exact
    silent citation drop this module was just fixed to prevent.
    """
    key = start or os.getcwd()
    if key not in _GIT_ROOT_CACHE:
        out = run_git(["rev-parse", "--show-toplevel"], key).strip()
        _GIT_ROOT_CACHE[key] = out or None
    return _GIT_ROOT_CACHE[key]


# SEC-14: well-known PUBLIC git-hosting hosts. A repo on one of these MAY be world-readable —
# we cannot tell public from private by URL alone, so this is the strongest signal we have that
# a COMMITTED per-user usage summary (TEA-5) could become public. Any remote at all means
# "shared with whoever can pull"; these get the loudest warning.
_PUBLIC_GIT_HOSTS = (
    "github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "gitea.com", "sr.ht",
)


def _git_url_host(url: str) -> Optional[str]:
    """Host of a git remote URL — ``https://host/…``, ``git@host:…``, or ``ssh://…``. None if unclear."""
    try:
        u = url.strip()
        m = re.match(r"^[\w.+-]+@([^:/]+):", u)  # scp-like: git@host:path
        if m:
            return m.group(1).lower()
        m = re.match(r"^[a-zA-Z][\w+.-]*://(?:[^@/]+@)?([^:/]+)", u)  # scheme://[user@]host[:port]/…
        if m:
            return m.group(1).lower()
        return None
    except Exception:
        return None


def git_remote_info(repo_root: Optional[str]) -> dict:
    """``{"url", "host", "public_host"}`` for the repo's push remote (SEC-14). All-None if none.

    Reads ``origin`` (else the first configured remote). ``public_host`` is True when the URL
    points at a well-known public-hosting service — a repo there MAY be world-readable (not
    determinable from the URL), the strongest signal that a committed per-user usage summary
    could become public. Never raises; used only by user/agent-gated surfaces (the soak CLI's
    --record-usage confirmation and a doctor check), never on the hot path.
    """
    out = {"url": None, "host": None, "public_host": False}
    try:
        if not repo_root:
            return out
        url = run_git(["config", "--get", "remote.origin.url"], repo_root).strip()
        if not url:
            remotes = run_git(["remote"], repo_root).split()
            if remotes:
                url = run_git(["config", "--get", f"remote.{remotes[0]}.url"], repo_root).strip()
        if not url:
            return out
        host = _git_url_host(url)
        out.update(url=url, host=host,
                   public_host=bool(host and any(host == h or host.endswith("." + h) for h in _PUBLIC_GIT_HOSTS)))
        return out
    except Exception:
        return out


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


# --------------------------------------------------------------------------- #
# TEA-1: the machine-local USER tier — a second corpus, recalled ALONGSIDE the
# project corpus in every project so a person-scoped lesson learned in project A is
# known in project B. Decided machine-local only (OQ-5): no remote/sync; the location
# is a plain dir so a dotfiles symlink Just Works for users who sync themselves.
# --------------------------------------------------------------------------- #
def user_memory_dir() -> str:
    """The machine-local user-tier corpus dir (``~/.claude/hippo-memory`` by default).

    ``HIPPO_USER_MEMORY_DIR`` overrides it (hermetic tests; a dotfiles-relocated home).
    Returns a PATH, not a promise the dir exists — the fusion layer treats an absent dir
    as "no user tier" (recall proceeds project-only), so an unconfigured machine pays
    nothing. Sits beside the existing machine-local ``~/.claude/hippo-trust.json`` registry
    and the native-memory symlink under ``~/.claude/projects/``; deliberately NOT under
    ``platform_cache_dir`` (that is a rebuildable cache — user memories are data).
    """
    override = os.environ.get("HIPPO_USER_MEMORY_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "hippo-memory")


def local_memory_dir(project_memory_dir: str) -> str:
    """TEA-3: the in-repo, gitignored PRIVATE tier dir — a sibling of the project corpus
    (``.claude/memory.local`` beside ``.claude/memory``).

    ``HIPPO_LOCAL_MEMORY_DIR`` overrides it (hermetic tests). Holds memories a user wants recall
    over on THIS clone but never published to teammates: it is gitignored (init patches
    ``.gitignore`` AND it self-ignores, SEC-3), so it is invisible in ``git status`` and can
    never be committed, while staying fully recallable locally (fused like the user tier). A
    teammate who clones the repo simply lacks the dir; its floor pointers degrade to silence.
    """
    override = os.environ.get("HIPPO_LOCAL_MEMORY_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(project_memory_dir)), "memory.local")


def slugify_identity(raw: str) -> str:
    """The ONE identity slugification (TEA-5's rule): ``[a-z0-9_.-]``, any other char →
    ``_``, leading/trailing separators trimmed, capped at 64, ``"unknown"`` floor.
    Shared by ``current_user_slug`` and every git-log-identity JOIN (CLB-2's
    verified_by consumers) so a stamped slug and a slugified ``git log`` author can
    never disagree about the same human. Pure; never raises."""
    try:
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", (raw or "").lower()).strip("._-")
        return (slug or "unknown")[:64]
    except Exception:
        return "unknown"


def current_user_slug(repo_root: str) -> str:
    """A filesystem-safe slug identifying the current user, for TEA-5's committed per-user usage
    summary (``.usage/<user>.json``) and CLB-2's ``verified_by`` stamp.

    Preference order: ``HIPPO_USAGE_USER`` override (hermetic tests) → git ``user.email`` → git
    ``user.name`` → ``$USER``/``$LOGNAME`` → ``"unknown"``. Slugified via ``slugify_identity``
    (the one shared rule). Never raises — identity derivation must degrade to ``"unknown"``,
    never crash a curation pass."""
    try:
        raw = (os.environ.get("HIPPO_USAGE_USER") or "").strip()
        if not raw:
            raw = run_git(["config", "user.email"], repo_root).strip()
        if not raw:
            raw = run_git(["config", "user.name"], repo_root).strip()
        if not raw:
            raw = (os.environ.get("USER") or os.environ.get("LOGNAME") or "").strip()
        return slugify_identity(raw)
    except Exception:
        return "unknown"


def tier_index_dir(tier_dir: str) -> str:
    """Index-cache location for a NON-project tier — nested at ``<tier_dir>/.memory-index``.

    The project tier keeps its historical SIBLING cache (``.claude/.memory-index`` via
    ``build_index.default_index_dir``); the user tier (and TEA-3's private tier) NEST their
    index inside the tier dir instead. Two reasons: (1) the private tier's sibling would be
    ``.claude/.memory-index`` — colliding with the project's — so nesting keeps them distinct;
    (2) a nested user-tier index lives under ``~/.claude/hippo-memory`` (outside every repo)
    and a nested private-tier index is swept up by ``memory.local``'s own self-ignoring
    ``.gitignore``, so neither can ever reach a project's git.
    """
    return os.path.join(tier_dir, ".memory-index")


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


# --------------------------------------------------------------------------- #
# COR-7: corpus format versioning
# --------------------------------------------------------------------------- #
# The version of the CORPUS's own on-disk conventions (frontmatter schemas, marker files,
# floor layout) that this plugin reads and writes. Distinct from the INDEX's
# ``build_index.SCHEMA_VERSION``: the index is a derived cache (a mismatch is healed by one
# silent rebuild), while the corpus is the git-tracked single source of authority — a
# format change there is a MIGRATION of user data, per-item and agent-gated, never
# automatic (see plugin/memory/README.md, "Corpus format versioning"). Declared by a
# ``.claude/memory/.format`` marker committed WITH the corpus (it describes the corpus; it
# is NOT a rebuildable cache), JSON ``{"corpus_format": N}``. A corpus with NO marker reads
# as format 1 — every pre-v0.5.0 corpus predates the marker, so absence must mean the
# baseline, never an error. A breaking corpus change bumps this ONE constant; init's
# seeding snippet and doctor's check follow it (a parity test pins the init skill's
# literal to this constant so the two can't drift).
#
# Format history:
#   1 — the pre-versioning baseline (frontmatter with cited_paths/source_commit/
#       invalid_after, [[wikilink]] bodies, MEMORY.md floor).
#   2 — GRA-4 typed edges: frontmatter may carry `supersedes:`/`contradicts:`/`refines:`
#       lists (top-level or under `metadata:`). Purely ADDITIVE — a v1 corpus with no
#       typed relations is read identically by a v2 plugin, so the migration is just
#       reviewing that no frontmatter key collides and stamping the marker
#       (`write_corpus_format`); see plugin/memory/README.md "Corpus format versioning".
#   3 — GOV-2 steering: frontmatter may carry `steer: pin` (top-level or under
#       `metadata:`) — the author's bounded, always-on recall lift (build_index carries it
#       into the manifest; recall multiplies a capped _PIN_BOOST pre-cut). Purely ADDITIVE,
#       same migration shape as v2: verify no existing frontmatter uses `steer` for
#       something else, then stamp the marker. MUTE is deliberately NOT part of v3 — it
#       stays gated on the salience keystone (SIG-5/T7) and will be its own convention.
#   4 — GOV-7 confidence tier: frontmatter may carry `confidence: draft|verified|
#       authoritative` (top-level or under `metadata:`) — the AUTHOR's trust dial,
#       display-only at inject/recall_view, NEVER a ranking input (the popularity=
#       correctness trap; AST-pinned in tests). Closed enum; unknown values read as
#       unset (today's default). Purely ADDITIVE — same stamp-only migration as v2/v3.
CORPUS_FORMAT_VERSION = 5
_FORMAT_MARKER_NAME = ".format"

# DRV-2 — the version of the DERIVATION, deliberately a separate axis from the format.
#
# `corpus_format` versions the SHAPE of a memory file, and its own history above says so:
# v2/v3/v4 are each "purely ADDITIVE… the migration is just… stamping the marker". packs.py
# states the criterion outright ("deliberately NOT a corpus_format bump — the memory-file
# shapes are unchanged"). By that rule the ORC-1 extractor fix is NOT a format event: it
# changes no shape, only VALUES.
#
# That is exactly the trap. Nothing versioned the derivation, so a corpus whose cited_paths
# came from the shadowed regex and one derived by the fixed regex both declare
# `{"corpus_format": 5}` and are indistinguishable. There was no question you could ask a
# hippo corpus that meant "were these values produced by an extractor I trust?" — which is
# why a 14-minor-version-old bug had to be found by hand, in another repo, by an agent
# noticing a memory was watching the wrong file.
#
# History:
#   1 — the shipped v1.14.0 extractor: no trailing boundary (so `package.json` derived as
#       `package.js`, and `.tsx`/`.jsx`/`.json` were declared-but-unreachable), no
#       mjs/cjs/mts/cts, no `./` normalisation.
#   2 — ORC-1 + DRV-1: trailing `(?!\w|\.\w)`, the mjs family, `./` normalisation.
#   3 — ORC-3: extensionless config/build filenames (_EXTENSIONLESS_NAMES — Dockerfile,
#       Makefile, LICENSE, etc.) become citable in two bounded shapes: directory-qualified
#       anywhere, or a whole backtick span. A bare unmarked mid-sentence mention stays
#       non-derivable, deliberately. resolve_citations itself is UNCHANGED — already
#       extension-agnostic basename matching — only the extractor's vocabulary grew.
#
# Kept on the corpus-level marker rather than in each file's frontmatter: a per-file key
# WOULD be a shape change (a real corpus_format v6), needs a corpus-wide rewrite just to
# introduce, and answers a question that is not per-file anyway.
CITATION_DERIVATION_VERSION = 3


def format_marker_path(memory_dir: str) -> str:
    """``<memory_dir>/.format`` — the corpus marker's one canonical location."""
    return os.path.join(memory_dir, _FORMAT_MARKER_NAME)


def _read_marker(memory_dir: str) -> dict:
    """The marker file's raw dict; ``{}`` when absent/unreadable/wrong-shape. Never raises."""
    try:
        p = format_marker_path(memory_dir)
        if not os.path.isfile(p):
            return {}
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_marker_keys(memory_dir: str, **keys) -> bool:
    """Merge ``keys`` into the marker, PRESERVING every key already there.

    Read-modify-write, not clobber: ``corpus_format`` and ``cite_derivation`` are
    independent axes living in one file, and a writer that rewrote the whole object would
    silently erase the other one's answer.
    """
    try:
        from .atomic import write_text_atomic

        data = _read_marker(memory_dir)
        data.update(keys)
        # INV-2: the marker is COMMITTED corpus truth (format + derivation axes in one
        # file) — a torn write would have the corpus declaring garbage to every reader.
        write_text_atomic(format_marker_path(memory_dir), json.dumps(data) + "\n")
        return True
    except Exception:
        return False


def read_cite_derivation(memory_dir: str) -> int:
    """The corpus's declared citation-derivation version; ``1`` when undeclared (DRV-2).

    An undeclared corpus IS derivation 1 — every corpus written before DRV-2 was derived by
    the pre-ORC-1 extractor, so the default is the truth rather than a guess. Same
    never-raise, degrade-to-baseline contract as ``read_corpus_format``.
    """
    v = _read_marker(memory_dir).get("cite_derivation")
    return v if isinstance(v, int) and not isinstance(v, bool) else 1


def write_cite_derivation(memory_dir: str, version: Optional[int] = None) -> bool:
    """Stamp the citation-derivation version (default: this plugin's).

    MUST be the LAST step of a completed re-derivation, never a fix on its own: stamping
    cite_derivation=2 over citations that were derived by extractor 1 asserts exactly the
    thing DRV-2 exists to let you verify. Like ``write_corpus_format``, deliberately has no
    bulk-migration counterpart — see MIG-1's per-item worklist.
    """
    return _write_marker_keys(
        memory_dir,
        cite_derivation=int(version if version is not None else CITATION_DERIVATION_VERSION),
    )


def read_corpus_format(memory_dir: str) -> int:
    """The corpus's declared format version; ``1`` when undeclared. Never raises.

    A missing marker IS format 1 (the pre-versioning baseline every existing corpus is
    on), so no corpus ever needs backfilling to be readable. An unreadable/corrupt/
    wrong-shape marker also degrades to 1 — the never-raise direction; doctor's format
    check reports against whatever this returns, so a garbled marker at worst reads as
    the baseline rather than blocking recall.
    """
    v = _read_marker(memory_dir).get("corpus_format")
    return v if isinstance(v, int) and not isinstance(v, bool) else 1


def write_corpus_format(memory_dir: str, version: Optional[int] = None) -> bool:
    """Stamp the corpus format marker (default: this plugin's ``CORPUS_FORMAT_VERSION``).

    Returns True on success, False on any failure (missing dir, permissions) — callers
    surface the failure rather than pretending the corpus is stamped. Deliberately has NO
    bulk-migration counterpart: stamping a NEWER version onto an old corpus is the final,
    explicit step of a doctor-driven migration, never something a hook or sweep does.

    DRV-2: merges rather than clobbers — ``cite_derivation`` shares this file and is an
    independent axis; a whole-object rewrite would erase it.
    """
    return _write_marker_keys(
        memory_dir,
        corpus_format=int(version if version is not None else CORPUS_FORMAT_VERSION),
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
    - Unresolvable tokens (not in the repo) are dropped.
    """
    out: List[str] = []
    seen: set = set()
    for tok in tokens:
        norm = tok[2:] if tok.startswith("./") else tok
        if norm in repo_files:
            cands = [norm]
        else:
            matches = basename_index.get(norm.rsplit("/", 1)[-1], [])
            cands = matches if len(matches) == 1 else []  # drop ambiguous bare basenames
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

    return "\n".join([lines[0]] + insert_frontmatter_keys(fm, new_keys) + lines[close:]), True


_PROVENANCE_KEY_RE = re.compile(r"^(\s*)(?:cited_paths|source_commit|source_commit_time)\s*:")
_INVALID_AFTER_KEY_RE = re.compile(r"^(\s*)invalid_after\s*:")
_BLOCK_ITEM_RE = re.compile(r"^(\s*)-\s")
# An indented KEY line — deliberately NOT `^(\s+)\S`, which also matches a block-list item
# (`    - keep-me`) and so reports the ITEM's indent as the block's key indent. See
# ``insert_frontmatter_keys``.
_INDENTED_KEY_RE = re.compile(r"^(\s+)(?!-\s)\S")


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
    """
    meta_idx = next((i for i, ln in enumerate(fm) if re.match(r"^metadata\s*:\s*$", ln)), None)
    if meta_idx is None:
        return fm + list(new_keys)
    indent = "  "
    last = meta_idx
    j = meta_idx + 1
    while j < len(fm):
        ln = fm[j]
        if ln.strip() == "" or not ln.startswith((" ", "\t")):
            break
        m = _INDENTED_KEY_RE.match(ln)
        if m:
            indent = m.group(1)
        last = j
        j += 1
    return fm[: last + 1] + [f"{indent}{k}" for k in new_keys] + fm[last + 1:]


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
    out: List[str] = []
    i = 0
    while i < len(fm):
        m = key_re.match(fm[i])
        if not m:
            out.append(fm[i])
            i += 1
            continue
        key_indent = len(m.group(1))
        inline = re.sub(r"\s+#.*$", "", fm[i].split(":", 1)[1]).strip()
        i += 1
        if inline:
            continue  # flow style (`key: [a, b]`) — the value lives on the key line
        # Block style (a bare `key:`): consume the `- item` lines that are its value. A
        # sibling key ends the run (it is not a `- ` line), as does any dedent below the
        # key's own indent — those items belong to something else.
        while i < len(fm):
            bm = _BLOCK_ITEM_RE.match(fm[i])
            if not bm or len(bm.group(1)) < key_indent:
                break
            i += 1
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

    ``dropped_gone`` / ``dropped_not_derived`` (LIF-4): the same set, partitioned by CAUSE.
    Computed here because this is where ``repo_files`` — the only oracle that can answer
    "is it actually missing?" — is in scope.

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
        cited = cited_paths_for_body(body, repo_files, basename_index)
        # DRV-1: the derivation's OTHER half — what the body offered that the oracle refused.
        result["extracted_but_unresolved"] = unresolved_citations(body, repo_files, basename_index)
        rel = os.path.relpath(path, repo_root)
        dropped: List[str] = []
        gone: List[str] = []
        not_derived: List[str] = []
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
            dropped = [p for p in _frontmatter_cited_paths(fm) if p not in cited]
            # LIF-4: partition HERE, where repo_files is in scope. The renderer cannot do it
            # — reconsolidate and the MCP tool call it with no repo index in hand.
            gone, not_derived = partition_dropped(dropped, repo_files)
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
        cited = cited_paths_for_body(body, repo_files, basename_index)
        dropped = [p for p in _frontmatter_cited_paths(fm) if p not in cited]
        # LIF-4: partition where repo_files is in scope — see backfill_file.
        gone, not_derived = partition_dropped(dropped, repo_files)
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
            try:
                from .trust import record_authored_write

                record_authored_write(os.path.dirname(path), path, repo_root)
            except Exception:
                pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


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
    cosmetic shrink. Returns ``[]`` when nothing was dropped.
    """
    dropped = result.get("dropped_citations") or []
    if not dropped:
        return []
    cited_after = result.get("cited") or []
    # Fall back to "all gone" only if a producer predates the partition — never re-derive it
    # here from a repo index this function does not have.
    gone = result.get("dropped_gone")
    not_derived = result.get("dropped_not_derived")
    if gone is None and not_derived is None:
        gone, not_derived = dropped, []
    gone, not_derived = list(gone or []), list(not_derived or [])

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
    head = f"⚠ citation rot — {name}: " + "; ".join(clauses)
    if not cited_after:
        state = "would be" if dry_run else "is now"
        return [
            f"{head} — cited_paths {state} EMPTY, so this memory is EXEMPT from staleness "
            "tracking until its body cites current code again"
        ]
    return [f"{head}; {len(cited_after)} citation(s) remain"]


# --------------------------------------------------------------------------- #
# MIG-1 — the consented re-derivation (the THIRD verb)
# --------------------------------------------------------------------------- #
def rederive_preview(path: str, repo_root: str, repo_files: set, basename_index: Dict[str, List[str]]) -> dict:
    """What re-deriving ONE memory's citations WOULD change — read-only. Never raises.

    ``{"name", "before", "after", "gained", "lost", "unresolved", "changed", "error"}``.
    The review payload for the worklist below: the operator sees the attributed diff for
    THIS memory and approves THIS memory, which is what makes the fold that follows a
    legitimate SEC-6 consent rather than the gate consenting to itself.
    """
    out = {
        "name": os.path.basename(path)[:-3],
        "before": [], "after": [], "gained": [], "lost": [], "unresolved": [],
        "changed": False, "error": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm = parse_frontmatter(text)
        if not fm:
            out["error"] = "unparseable frontmatter — fix the YAML first"
            return out
        _, body = split_frontmatter(text)
        before = _frontmatter_cited_paths(fm)
        after = cited_paths_for_body(body, repo_files, basename_index)
        out.update(
            before=before,
            after=after,
            gained=[p for p in after if p not in before],
            lost=[p for p in before if p not in after],
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
        "dropped_not_derived": [], "error": None,
    }
    try:
        # backfill_file(refresh=True) already does exactly the derivation half correctly —
        # it preserves source_commit, partitions the loss (LIF-4), and refuses to damage a
        # key it does not own (COR-9). Reuse it rather than re-implement its rules.
        bf = backfill_file(path, repo_root, repo_files, basename_index, dry_run=dry_run, refresh=True)
        result.update({k: bf[k] for k in
                       ("changed", "cited", "dropped_citations", "dropped_gone",
                        "dropped_not_derived", "error") if k in bf})
        if bf.get("error"):
            return result
        if bf.get("changed") and not dry_run:
            # SEC-6: the operator reviewed THIS file's diff and approved THIS file, so its
            # new bytes join the consent baseline. Without this the migration quarantines
            # every memory it fixes. WITH it on a bulk pass it would be self-consent — which
            # is why this call lives here, behind a per-item approval, and NOT in
            # backfill_file (whose --refresh path has no reviewer).
            try:
                from .trust import record_authored_write

                record_authored_write(os.path.dirname(path), path, repo_root)
            except Exception:
                pass
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


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
        for w in work:
            if w["error"]:
                print(f"  ✘ {w['name']}: {w['error']}")
                continue
            print(f"  {w['name']}")
            if w["gained"]:
                print(f"      + gains  : {', '.join(w['gained'])}")
            if w["lost"]:
                print(f"      - loses  : {', '.join(w['lost'])}")
            if w["unresolved"]:
                print(f"      ? unresolved in body: {', '.join(w['unresolved'])}")
        print("\nReview each, then approve individually: "
              "python -m memory.provenance --rederive-one <name>")
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
        verb = "would re-derive" if args.dry_run else "re-derived"
        print(f"{verb} {base}: cited_paths = {r['cited']}")
        for ln in citation_rot_lines(base, r, dry_run=args.dry_run):
            print(ln)
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
