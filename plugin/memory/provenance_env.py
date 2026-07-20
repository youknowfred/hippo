"""The environment provenance runs in: git facts, and where the corpus lives on disk.

Decomposed out of ``provenance.py`` (ED5R-3, pure code motion) along the sharpest line in
that file — **nothing here reads, parses, or writes a memory file.** Every name below
answers a question about the machine instead: what git says about this repo, and where the
corpus, the tiers, and the ``~/.claude/projects`` symlink live. That is why this block had
ZERO outbound dependencies on the rest of ``provenance.py`` before the split and still has
none — it is the layer the rest of the package stands on.

Three groups:

- **git shell-outs** — ``run_git`` (the single subprocess site; never raises, ``''`` on any
  failure), the PRF-3-memoized ``git_root``, and SEC-14's ``git_remote_info`` /
  ``_PUBLIC_GIT_HOSTS`` public-host classification.
- **corpus location** — ``walk_up_for_memory_dir`` / ``resolve_dirs`` (the ambient
  ``(memory_dir, repo_root)`` pair the whole package resolves through), plus
  ``encode_project_dir`` and the ``check`` / ``create`` / ``remove_project_symlink`` trio
  that wires a project into ``~/.claude/projects`` — including the legacy encoding it
  still recognizes.
- **TEA-1 tiers** — ``user_memory_dir`` / ``local_memory_dir`` / ``tier_index_dir`` and the
  identity slug the committed-usage summaries and reverify stamps key on.

``ensure_self_ignoring_dir`` (SEC-3) comes along because it is the same kind of thing: a
filesystem fact, not a corpus one.

The ``provenance`` façade re-exports every name here, so ``memory.provenance.resolve_dirs``
and every other dotted path across the package is unchanged. One caveat for tests: a patch
of ``run_git`` aimed at a caller that lives HERE (``git_root``, ``git_remote_info``,
``current_user_slug``) must land on THIS module — the façade's re-imported binding is a
separate name in a separate namespace, so patching it reaches only the façade's own callers
(CONTRIBUTING.md "Code layout"). A "git never ran" pin wants both.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple


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
