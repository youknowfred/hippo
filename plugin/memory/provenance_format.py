"""Corpus format + citation-derivation versioning (COR-7 / DRV-2) + corpus policy keys.

The ``.claude/memory/.format`` marker's one home: the two independent version axes a
corpus declares — ``corpus_format`` (the SHAPE of a memory file) and ``cite_derivation``
(which extractor produced the ``cited_paths`` VALUES) — with their readers, their
merge-not-clobber writer, and the format/derivation history that explains every bump.
VOL-1 adds the marker's first POLICY key, ``volatile_paths`` (``read_volatile_paths``):
corpus-owned declarations that, like the version axes, must travel with the corpus
through git rather than live in any machine-local state.

Decomposed out of ``provenance.py`` as pure code motion when the module-size ratchet
fired (CUR-1/COR-20 work); every symbol stays importable at ``memory.provenance.<name>``
via the façade's explicit re-exports. Depends on nothing else in provenance — the
marker is JSON, not frontmatter — so the sibling-never-imports-façade rule holds by
construction (see CONTRIBUTING.md "Code layout").
"""

from __future__ import annotations

import json
import os
from typing import Optional

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
#   4 — IOP-2: `.mdc` (Cursor rule files) joins _CODE_EXTS, so a body naming a
#       `.cursor/rules/*.mdc` derives it as a cited path. SAME class as 3 (vocabulary grew,
#       no shape change) — lands here, not corpus_format; a .mdc-free corpus stamps clean.
#
# Kept on the corpus-level marker rather than in each file's frontmatter: a per-file key
# WOULD be a shape change (a real corpus_format v6), needs a corpus-wide rewrite just to
# introduce, and answers a question that is not per-file anyway.
CITATION_DERIVATION_VERSION = 4


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


def read_volatile_paths(memory_dir: str) -> list:
    """The corpus's declared volatile paths (VOL-1); ``[]`` when undeclared. Never raises.

    ``volatile_paths`` is the marker's first POLICY key: a corpus-level list of high-churn
    coordination files (a living roadmap, a migration runner, ``package.json``) whose
    whole-file drift carries ~zero bits about memory validity — the bodies DELEGATE to
    them ("live status lives there"), so the citation is right for recall but wrong as a
    staleness-ARMING trigger. Consumed only by ``staleness_policy`` (the arming split);
    derivation, ``find_stale`` detection, and every recall surface stay registry-blind by
    design, so an absent/empty/garbled key degrades to ``[]`` — byte-identical behavior
    to an undeclared corpus (ED-4), the same never-raise direction as
    ``read_corpus_format``.

    Deliberately a NEW key on the existing marker rather than a sibling file or a
    ``corpus_format`` bump: it must travel with the corpus through git exactly like the
    version axes (committed, clone-shared, merge-not-clobber preserved by
    ``_write_marker_keys``), and it changes no memory-file SHAPE — the packs.py
    criterion for "not a format event". Every reader pulls only its own key, so older
    plugins ignore it, the DRV-2 precedent. Entries are toplevel-relative repo paths,
    matched EXACTLY against ``cited_paths`` (same SHP-1 convention; a leading ``./`` is
    normalized off, non-strings and blanks drop, order preserved minus duplicates —
    it's a hand-committed declaration, so declaration order is the honest render order).
    There is deliberately NO writer: the registry is operator-committed corpus policy
    (edit ``.claude/memory/.format`` and commit), never something a hook or sweep grows.
    """
    raw = _read_marker(memory_dir).get("volatile_paths")
    if not isinstance(raw, list):
        return []
    out: list = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        p = entry.strip()
        if p.startswith("./"):
            p = p[2:]
        if p and p not in out:
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# FLR-1: the floor-lint POLICY key — the marker's second policy key after VOL-1
# --------------------------------------------------------------------------- #
# The harness loads MEMORY.md into EVERY session's context through a hard-coded read
# window: 25,000 bytes (past it the floor's TAIL is silently truncated in-context) with
# an advisory nag near 70% (17,500 bytes). Neither number is configurable harness-side
# (no settings key, env var, or hook — field-verified 2026-08-24 against the settings
# schema + docs), so they are constants HERE, not policy: every corpus using the native
# MEMORY.md floor is subject to them whether or not it declares anything.
HARNESS_FLOOR_READ_CAP_BYTES = 25000
HARNESS_FLOOR_WARN_BYTES = 17500


def read_floor_lint(memory_dir: str) -> dict:
    """The corpus's declared floor-lint policy (FLR-1); defaults when undeclared. Never raises.

    ``floor_lint`` is the marker's second POLICY key (VOL-1's ``volatile_paths`` set the
    pattern: corpus-owned, committed, travels through git, deliberately NO writer —
    operator-committed policy only). The field defect it mechanizes (em-growth-labs,
    2026-08): the always-loaded floor sawtoothed over the harness warn line in 87% of 228
    commits and past the read cap in 9 — silently truncating its own tail — while the one
    lint that existed lived in a CI lane that never ran. The check must live where the
    edits happen; the POLICY (what tokens are banned, how long a line may run) stays the
    corpus's own declaration.

    Returns ``{"banned_re", "max_line", "warn_bytes", "cap_bytes"}``:

    - ``banned_re`` — OPT-IN: a regex (string) whose match on a MEMORY.md line flags it
      (the linear-memory-boundary class: status vocabulary, PR refs, SHAs, chip ids).
      ``None`` when undeclared/non-string/uncompilable — an invalid pattern degrades to
      "not declared", never a raise (ED-4).
    - ``max_line`` — OPT-IN: max chars per floor line (pointer + hook, not prose).
      ``None`` when undeclared/invalid; must be a positive int.
    - ``warn_bytes`` / ``cap_bytes`` — ALWAYS present: the harness constants above,
      overridable only DOWNWARD by declaration (a corpus may hold itself leaner than the
      harness's window; declaring a laxer cap than the harness enforces would just
      un-warn real truncation, so larger values are ignored). Non-positive/invalid
      declarations fall back to the constants.
    """
    raw = _read_marker(memory_dir).get("floor_lint")
    out = {
        "banned_re": None,
        "max_line": None,
        "warn_bytes": HARNESS_FLOOR_WARN_BYTES,
        "cap_bytes": HARNESS_FLOOR_READ_CAP_BYTES,
    }
    if not isinstance(raw, dict):
        return out
    pat = raw.get("banned_re")
    if isinstance(pat, str) and pat.strip():
        try:
            import re as _re

            _re.compile(pat)
            out["banned_re"] = pat
        except Exception:
            out["banned_re"] = None
    ml = raw.get("max_line")
    if isinstance(ml, int) and not isinstance(ml, bool) and ml > 0:
        out["max_line"] = ml
    for key, ceiling in (("warn_bytes", HARNESS_FLOOR_WARN_BYTES), ("cap_bytes", HARNESS_FLOOR_READ_CAP_BYTES)):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= ceiling:
            out[key] = v
    return out
