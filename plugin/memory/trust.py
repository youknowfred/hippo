"""Machine-local trust registry for memory corpora (SEC-1 — foreign-corpus gate).

The recall hook auto-executes in whatever project it lands in: clone any repo that
carries a ``.claude/memory/`` corpus and — absent this gate — its memories inject into
your context on EVERY prompt, an unreviewed prompt-injection channel that needs zero user
action. This module is the gate. Recall (and the SessionStart producers) inject ONLY from
corpora the current user has explicitly marked trusted; a freshly-cloned foreign corpus
injects nothing until the user reviews it (via ``/hippo:doctor``) and consents.

Design constraints this module is shaped by (all load-bearing):
  - The registry lives OUTSIDE any project's own git tree (``~/.claude/hippo-trust.json``),
    so a foreign repo can't ship a "trust me" marker committed into itself. The key is the
    corpus's REAL absolute ``repo_root`` (``os.path.realpath``) — the same folder-trust
    shape the harness itself uses. One canonical file, one canonical key.
  - The GATE CHECK (``is_trusted``) is a cheap file-exists + small-JSON-read — NO git, NO
    network, NO LLM — so the UserPromptSubmit hot path can call it synchronously without
    violating the pure-retrieval invariant.
  - The one-time CONSENT step cannot live here or in any hook (hooks are non-interactive,
    exit-0, and must never block a prompt). Consent is agent-driven: ``/hippo:doctor`` shows
    the memory COUNT + a SAMPLE of names (never bodies) and, on the user's yes, calls
    ``mark_trusted``. ``/hippo:init`` marks a corpus trusted the moment the user creates it
    (or explicitly re-runs init against it) — running a hippo command against a corpus IS
    the review.
  - CI override: ``HIPPO_TRUST_ALL=1`` bypasses the gate entirely (matches the codebase's
    ``HIPPO_`` env convention). ``HIPPO_TRUST_FILE`` relocates the registry (hermetic
    tests point it at a tmp path so the real ``~/.claude`` is never touched).

Fail posture: this is a SECURITY gate, so it fails CLOSED — an unresolvable ``repo_root``
that IS a real corpus, or an unreadable/corrupt registry, denies rather than injects.

SEC-12: a corpus not inside a git repo is NOT automatically waved through. The clone-
injection attack has a non-git twin — "Download ZIP" of a public repo extracts a git-less
directory that still carries ``.claude/memory/``, and treating "no git root" as "gate
inapplicable" auto-injected exactly that. So a non-git directory that carries an actual
memory corpus (``_has_memory_content``) is gated too: keyed on its real root, untrusted
by default, denied until the user consents (``/hippo:init`` on create, or ``/hippo:doctor``
after review). Two overrides keep it usable: ``HIPPO_TRUST_NONGIT`` (and the broader
``HIPPO_TRUST_ALL``) restore the old "inapplicable, proceed" behavior for hand-made local
non-git corpora. An EMPTY / non-corpus non-git directory — ``resolve_dirs``' fallback for
an ordinary non-git PROJECT, and every hermetic tmp path — stays inapplicable: there is no
injectable content to gate, so hermetic recall paths are untouched.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from .provenance import git_root

# CI/automation bypass — set to any non-empty value to skip the gate entirely.
_TRUST_ALL_ENV = "HIPPO_TRUST_ALL"
# Hermetic-test / relocation override for the registry file path.
_TRUST_FILE_ENV = "HIPPO_TRUST_FILE"
# SEC-12 opt-out — set non-empty to restore the pre-SEC-12 "non-git = gate inapplicable"
# behavior (for a deliberately hand-made non-git local corpus you don't want gated).
_TRUST_NONGIT_ENV = "HIPPO_TRUST_NONGIT"


def trust_all() -> bool:
    """True when the CI/automation override (``HIPPO_TRUST_ALL``) is set non-empty."""
    return bool(os.environ.get(_TRUST_ALL_ENV))


def trust_registry_path() -> str:
    """Absolute path to the machine-local trust registry JSON.

    ``HIPPO_TRUST_FILE`` wins (hermetic tests point it at a tmp file); otherwise the
    canonical ``~/.claude/hippo-trust.json`` — deliberately OUTSIDE any project repo so a
    foreign corpus can never commit its own trust marker.
    """
    override = os.environ.get(_TRUST_FILE_ENV)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "hippo-trust.json")


def _corpus_key(repo_root: str) -> str:
    """Canonical registry key for a corpus: its real (symlink-resolved) absolute repo root."""
    return os.path.realpath(repo_root)


def _load_registry() -> dict:
    """Read the registry into a dict of ``{repo_root: metadata}``; ``{}`` on any problem.

    Never raises — a missing file (the common case: nobody has trusted anything yet) or an
    unreadable/corrupt one both yield ``{}``, which in a fail-closed gate means "nothing is
    trusted". Only the ``"trusted"`` sub-map is returned so the on-disk schema can grow.
    """
    path = trust_registry_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        trusted = data.get("trusted")
        return trusted if isinstance(trusted, dict) else {}
    except Exception:
        return {}


def is_trusted(repo_root: Optional[str]) -> bool:
    """True when ``repo_root``'s corpus is trusted — or when the CI override is set.

    Cheap by contract (a stat + a small JSON read): the UserPromptSubmit hot path calls this
    synchronously. A falsy ``repo_root`` is "not trusted" (fail closed). Never raises.
    """
    if trust_all():
        return True
    if not repo_root:
        return False
    return _corpus_key(repo_root) in _load_registry()


def _load_registry_doc() -> dict:
    """The WHOLE registry document (not just the trusted sub-map); ``{}`` on any problem.

    Writers re-read through this so they never drop sibling keys a future schema adds;
    a corrupt/non-dict file degrades to a fresh document.
    """
    path = trust_registry_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _write_registry_doc(doc: dict) -> bool:
    path = trust_registry_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
    return True


def mark_trusted(
    repo_root: str, memory_dir: Optional[str] = None, origin: Optional[str] = None
) -> bool:
    """Record ``repo_root`` as trusted in the machine-local registry. Idempotent.

    Called by ``/hippo:init`` when a corpus is created (or init is re-run against it) and by
    ``/hippo:doctor`` after the user reviews the consent sample and consents. Creates the
    ``~/.claude`` dir + registry file if absent, preserving any existing entries. Returns
    True on a successful write (or an already-present no-op), False if the write failed —
    the caller surfaces a failure rather than pretending the corpus is now trusted.

    SEC-6: pass ``memory_dir`` to stamp the consent-time CONTENT FINGERPRINT (per-file
    sha256 over the corpus's memory files) into the record — the baseline that makes
    "trusted upstream silently ships new injected memories" detectable: recall withholds
    files whose bytes drift from this baseline until the user re-reviews the delta and
    re-consents (which is just calling this again). Without ``memory_dir`` the record is
    a LEGACY (fingerprint-less) entry — still trusted, no quarantine possible; the doctor
    check names the upgrade path.

    SEC-7: ``origin`` records HOW trust was established — ``"init"`` (the user created /
    owns this corpus) vs ``"review"`` (a foreign/cloned corpus consented after review).
    ``None`` PRESERVES an existing entry's origin (so a drift re-consent on your own
    project never relabels it foreign), and leaves it unset for a fresh entry.
    """
    try:
        key = _corpus_key(repo_root)
        doc = _load_registry_doc()
        trusted = doc.get("trusted")
        if not isinstance(trusted, dict):
            trusted = {}
        from datetime import datetime, timezone

        prior = trusted.get(key) if isinstance(trusted.get(key), dict) else {}
        entry: dict = {"trusted_at": datetime.now(timezone.utc).isoformat()}
        effective_origin = origin or prior.get("origin")
        if effective_origin:
            entry["origin"] = str(effective_origin)
        if memory_dir is not None:
            entry["fingerprint"] = corpus_fingerprint(memory_dir)
        elif isinstance(prior.get("fingerprint"), dict):
            # Re-marking without a memory_dir must never DROP an existing baseline —
            # losing it would silently disable quarantine (fail-open), the exact
            # degradation SEC-6 exists to prevent.
            entry["fingerprint"] = prior["fingerprint"]
        trusted[key] = entry
        doc["trusted"] = trusted
        return _write_registry_doc(doc)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# SEC-6: content fingerprint — re-consent on material change.
#
# ``mark_trusted`` was TOFU: path + timestamp, so a trusted public upstream could ship
# injected memories in any later commit with zero re-consent. The consent record now
# carries a per-file content baseline; recall QUARANTINES (skips, per file) any
# project-tier memory whose bytes drift from it, the SessionStart producer +
# ``/hippo:doctor`` surface the withheld delta LOUDLY (KPI-5 — never a silent
# degradation), and re-consent (``mark_trusted`` with ``memory_dir``) refreshes the
# baseline after the user reviews the changed descriptions (SEC-5's surface).
#
# AUTHORSHIP IS CONSENT: hippo's own per-item, agent-gated write primitives
# (``new_memory.write_memory``, ``provenance.reverify_file``,
# ``staleness.set_invalid_after``, ``links.add_typed_relation``) call
# ``record_authored_write`` after writing, so your own reviewed writes never nag.
# ``build_index``/hooks NEVER consent — an automatic pass re-baselining the corpus would
# be the gate consenting to itself. Hand edits outside the primitives (your editor, a
# skill's Edit-tool fold) quarantine until the next doctor re-consent — deliberate: an
# out-of-band change is exactly what a review is for. Boundary stated honestly: the
# baseline covers the memory files (the recall-injectable surface); MEMORY.md/CLAUDE.md
# are the harness's folder-trust domain, and rendering a verdict on a drift-flagged file
# consents its current bytes — the drift banner fires BEFORE any verdict, and a per-item
# verdict on a file you have read IS a review.
# --------------------------------------------------------------------------- #
def file_sha256(path: str) -> Optional[str]:
    """sha256 hex of ``path``'s bytes, or None when unreadable (callers fail CLOSED)."""
    import hashlib

    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception:
        return None


def corpus_fingerprint(memory_dir: str) -> dict:
    """``{"files": {stem: sha256}, "digest": sha256}`` over the corpus's memory files.

    The injectable surface: exactly what ``_iter_memory_files`` yields (memory files;
    MEMORY.md/CONVENTIONS.md excluded — they are the harness/docs domain, not recall
    input). ``digest`` is a single roll-up over the sorted (stem, sha) pairs for cheap
    equality. Deterministic; never raises; empty maps on any problem.
    """
    import hashlib

    files: dict = {}
    try:
        from .provenance import _iter_memory_files

        for path in _iter_memory_files(memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
            h = file_sha256(path)
            if h is not None:
                files[stem] = h
    except Exception:
        files = {}
    digest = hashlib.sha256(
        "\n".join(f"{k}:{v}" for k, v in sorted(files.items())).encode("utf-8")
    ).hexdigest()
    return {"files": files, "digest": digest}


def _trusted_entry(repo_root: Optional[str]) -> Optional[dict]:
    if not repo_root:
        return None
    entry = _load_registry().get(_corpus_key(repo_root))
    return entry if isinstance(entry, dict) else None


def consented_hashes(repo_root: Optional[str]) -> Optional[dict]:
    """The consent-time ``{stem: sha256}`` baseline for ``repo_root``, or None.

    None means "no quarantine applies": the CI bypass is set, the corpus is untrusted
    (the boolean gate already denied everything), or the record is legacy /
    fingerprint-less (pre-SEC-6 consent — the doctor check names the upgrade). Cheap by
    contract (one small-JSON read), hot-path safe. Never raises.
    """
    try:
        if trust_all():
            return None
        entry = _trusted_entry(repo_root)
        if not entry:
            return None
        fp = entry.get("fingerprint")
        files = fp.get("files") if isinstance(fp, dict) else None
        return files if isinstance(files, dict) else None
    except Exception:
        return None


def trust_origin(repo_root: Optional[str]) -> Optional[dict]:
    """``{"origin", "trusted_at"}`` for a trusted corpus, or None (SEC-7's banner input).

    ``origin`` is ``"init"`` / ``"review"`` / None (legacy record). Never raises.
    """
    try:
        entry = _trusted_entry(repo_root)
        if not entry:
            return None
        return {"origin": entry.get("origin"), "trusted_at": entry.get("trusted_at")}
    except Exception:
        return None


def record_authored_write(
    memory_dir: str, path: str, repo_root: Optional[str] = None
) -> bool:
    """Fold ONE just-written memory file into the consent baseline (authorship = consent).

    Called by hippo's own per-item, agent-gated write primitives AFTER a successful
    write — the write was reviewed (check-first / per-item verdict / explicit approval),
    so its bytes join the baseline instead of quarantining the author's own work. No-op
    (returns False) when: the gate is inapplicable (non-git), the corpus is untrusted,
    the record has no fingerprint (legacy), or the file can't be hashed — this function
    can EXTEND an existing consent, never create one. Never raises. NEVER call this from
    an automatic pass (hooks, index builds): an unattended re-baseline would be the gate
    consenting to itself.
    """
    try:
        gate_root = gate_repo_root(memory_dir, repo_root)
        if gate_root is None:
            return False
        key = _corpus_key(gate_root)
        doc = _load_registry_doc()
        trusted = doc.get("trusted")
        if not isinstance(trusted, dict) or not isinstance(trusted.get(key), dict):
            return False
        entry = trusted[key]
        fp = entry.get("fingerprint")
        if not isinstance(fp, dict) or not isinstance(fp.get("files"), dict):
            return False  # legacy record — quarantine is off, nothing to extend
        h = file_sha256(path)
        if h is None:
            return False
        import hashlib

        stem = os.path.splitext(os.path.basename(path))[0]
        fp["files"][stem] = h
        fp["digest"] = hashlib.sha256(
            "\n".join(f"{k}:{v}" for k, v in sorted(fp["files"].items())).encode("utf-8")
        ).hexdigest()
        return _write_registry_doc(doc)
    except Exception:
        return False


def drift_withholding_line(drift: dict, *, max_names: int = 6) -> Optional[str]:
    """The ONE rendering of "this corpus is trusted but recall is withholding files".

    SEC-15. Takes an ``untrusted_changes`` delta; returns None when nothing is withheld
    (legacy fingerprint-less record, or no drift) so callers can treat None as "genuinely
    clean". Shared by the SessionStart drift producer and the ``init`` MCP tool, which used
    to read only the corpus-level boolean and print a green "recall active" over a corpus
    that was actively withholding memories.
    """
    changed, added = drift.get("changed") or [], drift.get("added") or []
    if not drift.get("baseline") or not (changed or added):
        return None
    withheld = changed + [f"{n} (new)" for n in added]
    shown = ", ".join(withheld[:max_names])
    more = f" (+{len(withheld) - max_names} more)" if len(withheld) > max_names else ""
    return (
        f"🔒 Memory trust drift: {len(changed)} changed / {len(added)} new memory "
        f"file(s) since you trusted this corpus (a git pull? a hand edit?) — recall is "
        f"WITHHOLDING them until you re-review: {shown}{more}. Run /hippo:doctor to see "
        "what each would inject and re-consent."
    )


def untrusted_changes(repo_root: Optional[str], memory_dir: str) -> dict:
    """The drift delta between the corpus's live content and its consent baseline.

    ``{"baseline": bool, "changed": [stems], "added": [stems]}`` — ``baseline`` False
    means a legacy (fingerprint-less or absent) record: no quarantine is active and the
    lists are empty (the doctor check names the re-consent upgrade). Removed files are
    deliberately NOT drift: an absent file injects nothing. Read-only; never raises.
    """
    out = {"baseline": False, "changed": [], "added": []}
    try:
        base = consented_hashes(repo_root)
        if base is None:
            return out
        out["baseline"] = True
        live = corpus_fingerprint(memory_dir)["files"]
        out["changed"] = sorted(k for k, v in live.items() if k in base and base[k] != v)
        out["added"] = sorted(k for k in live if k not in base)
        return out
    except Exception:
        return out


def _has_memory_content(memory_dir: Optional[str]) -> bool:
    """Cheap 'is this a real injectable corpus?' probe: True iff ``memory_dir`` yields >=1 file.

    Early-exits on the FIRST memory file (one directory entry), so it stays hot-path-safe.
    Only ever runs on the gate's non-git branch (git corpora return before reaching it), and
    only for non-git dirs — the uncommon case. Never raises.
    """
    if not memory_dir:
        return False
    try:
        from .provenance import _iter_memory_files

        return next(_iter_memory_files(memory_dir), None) is not None
    except Exception:
        return False


def gate_repo_root(memory_dir: Optional[str], repo_root: Optional[str] = None) -> Optional[str]:
    """Resolve the ``repo_root`` the trust gate keys on, or None if the gate is inapplicable.

    Git corpora: ALWAYS resolved through ``git_root`` — never the passed path taken blind.
    ``resolve_dirs`` returns ``git_root(start) or start``, so a caller's ``repo_root`` can be a
    NON-git fallback dir; keying blind on that would wrongly deny an ordinary non-git project.
    So this asks git for the toplevel of the best start dir (supplied ``repo_root`` else
    ``memory_dir``); a git root is the key.

    SEC-12 — non-git corpora: when there is no git root, a non-git directory that carries an
    actual memory corpus (``_has_memory_content``) is the "Download ZIP of a public repo"
    shape — extracted, git-less, still auto-injecting. It is gated too, keyed on its real root
    (``_corpus_key``), so the caller's ``is_trusted`` check denies it until the user consents.
    ``HIPPO_TRUST_NONGIT`` (and ``HIPPO_TRUST_ALL``) restore the old "inapplicable, proceed"
    behavior. An EMPTY / non-corpus non-git dir (the resolve_dirs fallback for a non-git
    project, and every hermetic tmp path) has nothing injectable, so it stays inapplicable
    (None) — hermetic recall paths are untouched. Never raises.
    """
    try:
        start = repo_root or memory_dir
        if not start:
            return None
        root = git_root(start)
        if root is not None:
            return root
        # No git root. SEC-12: gate a non-git dir only if it is a REAL corpus and no opt-out
        # is set — otherwise stay inapplicable (the load-bearing hermetic/non-git-project case).
        if os.environ.get(_TRUST_NONGIT_ENV) or trust_all():
            return None
        if _has_memory_content(memory_dir):
            return _corpus_key(start)
        return None
    except Exception:
        return None


def corpus_sample(memory_dir: str, limit: int = 8) -> List[str]:
    """Up to ``limit`` memory NAMES from ``memory_dir`` — a cheap names-only listing.

    SEC-5 note: this is NOT the consent surface anymore. Consent showed names while
    injection used descriptions — the exact gap ROADMAP.v1 §4 flagged as SEC-1
    under-delivering its own acceptance criterion — so the consent review now goes
    through ``corpus_consent_sample`` (names + the descriptions that actually inject).
    Kept for cheap non-consent displays. Never raises; [] on any problem.
    """
    try:
        from .provenance import _iter_memory_files

        names = [
            os.path.splitext(os.path.basename(p))[0]
            for p in _iter_memory_files(memory_dir)
        ]
        return names[:limit]
    except Exception:
        return []


def corpus_consent_sample(
    memory_dir: str, limit: int = 8, stems: Optional[List[str]] = None
) -> List[dict]:
    """Up to ``limit`` ``{"name", "description"}`` rows — the consent-review surface (SEC-5).

    These are THE strings that enter every prompt once this corpus is trusted: the
    ``description`` frontmatter is what recall injects per hit, rendered through the SAME
    flatten/truncate the injection layer applies (``recall.inject_description``) — the
    user consents to exactly what they will get, closing SEC-1's consent/injection gap
    (consent sampled NAMES; injection used DESCRIPTIONS). Descriptions only, never
    bodies: bodies stay unreviewed-and-uninjected behind the gate; the description is
    the injectable surface being authorized.

    ``stems`` (SEC-6 drift review) restricts the rows to exactly those file stems — the
    re-consent flow reviews the changed/added DELTA, not whichever files happen to sort
    first; None keeps the whole-corpus sampling for first consent.

    The consuming review (the doctor skill) must present these as QUOTED DATA with
    explicit framing that they are untrusted until consented — a malicious description
    is itself a prompt-injection attempt against the reviewing agent, which is exactly
    why the sample is bounded, truncated, and demarcated rather than dumped raw. A file
    with unparseable frontmatter rows as ``description: ""`` (it cannot inject a
    description either). Never raises; [] on any problem.
    """
    try:
        from .provenance import _iter_memory_files, parse_frontmatter
        from .recall import inject_description  # lazy: recall top-imports this module

        wanted = set(stems) if stems is not None else None
        out: List[dict] = []
        for path in _iter_memory_files(memory_dir):
            if len(out) >= limit:
                break
            name = os.path.splitext(os.path.basename(path))[0]
            if wanted is not None and name not in wanted:
                continue
            desc = ""
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    fm = parse_frontmatter(fh.read())
                desc = inject_description(str(fm.get("description") or ""))
            except Exception:
                desc = ""
            out.append({"name": name, "description": desc})
        return out
    except Exception:
        return []


def corpus_count(memory_dir: str) -> int:
    """Total count of memory files in ``memory_dir`` (excludes MEMORY.md floor). 0 on failure."""
    try:
        from .provenance import _iter_memory_files

        return sum(1 for _ in _iter_memory_files(memory_dir))
    except Exception:
        return 0
