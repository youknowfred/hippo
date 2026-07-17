"""CLB-3: evidence fences + cited-code drift — quoted hunks re-verified before reuse.

GRW-1 drains capture verbatim diff hunks into memory bodies, and once a memory
quotes a code region nothing checked that the region still matches the tree — the
quoted evidence silently rots, and a reviewer reusing it can't tell. This module
is both halves of the fix:

  **The marker** (the consolidate drain contract's machine-recognizable form —
  FUTURE drains only, never backfilled): a fenced block whose info string carries
  ``evidence: <path>:<start>-<end>``, e.g. ::

      ```diff evidence: src/thing.py:120-138
      @@ -118,6 +120,8 @@
       context line
      +added line
      ```

  ``<path>`` is repo-toplevel-relative; ``<start>-<end>`` is the post-image line
  region from the hunk's ``@@`` header — an informational ANCHOR only. The match
  oracle is content contiguity (the same rule SEN-1's write ticket applies), so an
  upstream edit that merely shifts line numbers never flags.

  **The matcher** — diff-line-class aware: unified-diff prefixes are stripped via
  ``_diff_post_image`` (context + added lines kept, REMOVED lines excluded — a
  landed change's post-image is what the tree can actually contain); each fence is
  checked exact-first, then whitespace-normalized (a whitespace-only refactor is a
  MATCH at the ``whitespace`` level, deliberately not drift). Only a fence whose
  content is genuinely gone is ``missing`` — that is the drift signal.

Placement is load-bearing (inv6): extraction + matching run inside
``session_start``'s find_stale pipeline — the one off-hot-path full-text read —
and NEVER inside ``build_index``/``recall``'s ``_ensure_index`` (hot-path
reachable; ``tests/test_evidence_drift.py`` holds that structurally). Results ride
``stale.json`` as an optional, absence-emits-nothing ``evidence_drift`` field
(``staleness.write_stale_cache``), upgrade the RET-6 banner with the match level,
and union into the existing ``watermark_stale`` → ``semantic_reverify`` lane —
no new write verb, the human still renders every verdict.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .provenance import _iter_memory_files, run_git

# The marker, anchored to the END of a fence info string: ``evidence: path:start-end``.
# Strict by design — a fence without a well-formed marker is simply not evidence
# (pre-existing unmarked bodies are out of scope; doctor counts them unverifiable).
_EVIDENCE_MARKER_RE = re.compile(
    r"evidence:\s*(?P<path>[^\s:]+(?:/[^\s:]+)*):(?P<start>\d+)-(?P<end>\d+)\s*$"
)

MATCH_EXACT = "exact"
MATCH_WHITESPACE = "whitespace"
MATCH_MISSING = "missing"

# Defensive bound: fences checked per memory. Marked fences are drain-authored and
# few; a pathological body must not turn the SessionStart pass into a file-read storm.
_MAX_FENCES_PER_MEMORY = 16
# Missing-path names carried onto worklist entries (mirrors find_stale's small lists).
_MAX_DRIFT_PATHS = 4


def _diff_post_image(content: str) -> Optional[str]:
    """The post-image of a unified-diff-shaped block, or None when it isn't one.

    Moved from ``new_memory`` (SEN-1's write ticket now delegates here — one
    implementation for "what can a fresh tree actually contain"): headers
    (``diff --git``, ``index``, ``---``/``+++``, ``@@``) are dropped; a block whose
    remaining lines are not uniformly diff-prefixed is not a diff (returns None,
    the raw bytes were already tried); context + added lines are kept with their
    prefixes stripped; removed lines are excluded.
    """
    lines = content.split("\n")
    body_lines = [
        ln for ln in lines
        if ln and not ln.startswith(("diff --git", "index ", "--- ", "+++ ", "@@"))
    ]
    if not body_lines or not any(ln[:1] in "+-" for ln in body_lines):
        return None
    if not all(ln[:1] in "+- " for ln in body_lines):
        return None
    kept = [ln[1:] for ln in body_lines if ln[:1] in "+ "]
    post = "\n".join(kept).strip("\n")
    return post or None


def extract_evidence_fences(text: str) -> List[dict]:
    """``[{"path", "start", "end", "content"}]`` for every MARKED fence in ``text``.

    Reuses ``links._FENCED_CODE_RE`` (COR-20's one fence parser) and inspects each
    block's info string for the marker. Unmarked fences are skipped — they are
    documentation, not attributed evidence. Never raises; ``[]`` on any failure.
    """
    try:
        from .links import _FENCED_CODE_RE

        out: List[dict] = []
        for m in _FENCED_CODE_RE.finditer(text or ""):
            block = m.group(0).split("\n")
            marker = _EVIDENCE_MARKER_RE.search(block[0])
            if not marker:
                continue
            out.append(
                {
                    "path": marker.group("path"),
                    "start": int(marker.group("start")),
                    "end": int(marker.group("end")),
                    "content": "\n".join(block[1:-1]),
                }
            )
            if len(out) >= _MAX_FENCES_PER_MEMORY:
                break
        return out
    except Exception:
        return []


def _normalize_ws(text: str) -> str:
    """Per-line whitespace collapse (+ blank-line drop) — the ``whitespace`` level's
    comparison form on BOTH sides, so an indentation/reflow-only refactor still
    matches. Content order and line identity are preserved."""
    lines = [" ".join(ln.split()) for ln in text.strip("\n").split("\n")]
    return "\n".join(ln for ln in lines if ln)


def match_fence(content: str, file_text: str) -> str:
    """One fence vs one live file: ``exact`` | ``whitespace`` | ``missing``.

    Candidates are the raw block AND (for a diff-shaped block) its post-image —
    exact contiguous containment first, then the whitespace-normalized form.
    An empty/unreadable ``file_text`` is ``missing`` (the file itself is gone —
    the strongest drift). Pure; never raises.
    """
    if not file_text:
        return MATCH_MISSING
    candidates = [content.strip("\n")]
    post = _diff_post_image(content)
    if post:
        candidates.append(post)
    for c in candidates:
        if c and c in file_text:
            return MATCH_EXACT
    norm_file = _normalize_ws(file_text)
    for c in candidates:
        nc = _normalize_ws(c)
        if nc and nc in norm_file:
            return MATCH_WHITESPACE
    return MATCH_MISSING


def evidence_drift_map(memory_dir: str, repo_root: str) -> Dict[str, dict]:
    """``{name: {"fences", "missing", "whitespace", "paths"}}`` for memories whose
    marked evidence DRIFTED (``missing`` ≥ 1) — absence-emits-nothing, so a corpus
    with no markers (or all-matching markers, or whitespace-only refactors) yields
    ``{}`` and every downstream surface stays byte-identical.

    Runs in the SessionStart find_stale pipeline only (the AST pin in
    tests/test_evidence_drift.py holds ``_ensure_index``/``build_index`` to zero
    evidence matching). One filesystem read per distinct cited evidence path,
    cached across fences. Never raises; ``{}`` on any failure.
    """
    out: Dict[str, dict] = {}
    try:
        top = run_git(["rev-parse", "--show-toplevel"], repo_root).strip() or repo_root
        file_cache: Dict[str, str] = {}
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            fences = extract_evidence_fences(text)
            if not fences:
                continue
            missing = whitespace = 0
            bad_paths: List[str] = []
            for fence in fences:
                rel = fence["path"]
                if rel not in file_cache:
                    try:
                        with open(os.path.join(top, rel), "r", encoding="utf-8") as fh:
                            file_cache[rel] = fh.read()
                    except Exception:
                        file_cache[rel] = ""
                level = match_fence(fence["content"], file_cache[rel])
                if level == MATCH_MISSING:
                    missing += 1
                    if rel not in bad_paths:
                        bad_paths.append(rel)
                elif level == MATCH_WHITESPACE:
                    whitespace += 1
            if missing:
                name = os.path.splitext(os.path.basename(path))[0]
                out[name] = {
                    "fences": len(fences),
                    "missing": missing,
                    "whitespace": whitespace,
                    "paths": sorted(bad_paths)[:_MAX_DRIFT_PATHS],
                }
    except Exception:
        return {}
    return out


def fold_drift_candidates(wm_stale: List[dict], drift: Dict[str, dict]) -> List[dict]:
    """Union evidence-drifted names into the watermark lane — the ONE route into
    ``recalled_stale_worklist`` → ``semantic_reverify`` (inv4: no new write verb;
    a drifted memory gets re-verified by a human, exactly like commit-precise
    staleness). Names already on the lane keep their existing entry. Pure."""
    have = {item.get("name") for item in wm_stale}
    out = list(wm_stale)
    for name in sorted(drift):
        if name in have:
            continue
        out.append(
            {
                "name": name,
                "changed_paths": list(drift[name].get("paths") or []),
                "watermark": True,
                "evidence": True,
            }
        )
    return out
