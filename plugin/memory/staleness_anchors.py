"""TND-3: derived evidence anchors — is a drifted citation's drift about what the memory says?

Today a memory arms for re-verification when ANY path it cites changed since its
``source_commit`` (``staleness.find_stale``). Most of those changes never touch what the
memory says. An evidence anchor is derived, never authored: an identifier the memory's own
text names that also occurs in the cited file at the memory's baseline. Per drifted cited
path, the anchors are looked for inside the ``source_commit..HEAD`` change of that path:

  * the path is **intact** when it has at least one anchor, no added or removed line of the
    change contains one, and every anchor still occurs in the file at HEAD;
  * a path deleted or renamed away, a path absent at the baseline, a binary file, or a path
    with NO anchor is never intact (no evidence is not evidence of intactness).

A memory is **quiet** when every drifted cited path is intact. Quiet is an ARMING class, the
same shape as VOL-1's volatile paths and TYPE-1's type exemption (``staleness_policy``):
detection stays blind, the suppression is counted and listed, every verdict stays human.

Zero corpus writes, and no derived write either: everything is computed from git objects
on demand (one ``git cat-file --batch`` call per judged batch). Nothing of this runs on
UserPromptSubmit or SessionStart in this release.

REPORT-ONLY (the replay gate, ``QUIET_ARMING_GATE_MET``): the definition was fixed on the
development half of a replay over the real historical verdicts and checked on the held-out
half. It did not reach "at least half of graduates quiet with at most 5% of fix/demote
verdicts suppressed", so quiet does NOT change arming: doctor counts what WOULD be quiet and
``hippo staleness --anchors`` lists it with the anchor evidence. Flipping the gate constant
is the one change a later definition that passes the replay needs; ``staleness_policy`` is
where it would partition (after VOL-1 and TYPE-1).

``ANCHOR_DERIVATION_VERSION`` names the definition; a change to the definition bumps it
(FMT-2 keys the index-side binding by it). ``memory_anchor_verdict`` judges ONE memory
without the staleness producer (TND-4's evidence-quiet batch); ``judge_memory`` is the pure
core over file contents (the replay and the tests call it directly).

Never raises from a public entry point; any failure degrades to "not quiet" (fail closed:
the memory stays armed exactly as before).
"""

from __future__ import annotations

import collections
import os
import re
import subprocess
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .fm_access import fm_get
from .provenance import parse_frontmatter, split_frontmatter

ANCHOR_DERIVATION_VERSION = 1

# The replay gate (TND-3): False — the measured definition missed it, so anchors are
# report-only and arming never consults them. See the module docstring.
QUIET_ARMING_GATE_MET = False

# Env override (HOT-9's shape, outside the frozen HIPPO_DISABLE set): "0" turns evidence
# anchors off entirely (no would-be-quiet report; arming is today's either way while the
# gate above is unmet). Unset or any other value: anchors on.
QUIET_ARMING_ENV = "HIPPO_QUIET_ARMING"

_GIT_TIMEOUT = 20
# A file larger than this is never judged intact (bounds the cost of one huge generated
# file; no evidence is not evidence).
_MAX_FILE_BYTES = 1_500_000

_BACKTICK = re.compile(r"`([^`\n]{1,200})`")
_TOKEN = re.compile(r"[A-Za-z0-9_]+")


# --------------------------------------------------------------------------- #
# Derivation: candidate identifiers from the memory's own text
# --------------------------------------------------------------------------- #
def _memory_prose(text: str) -> str:
    """The memory's description plus body — what the memory SAYS (frontmatter keys and
    citation lists are binding, not claims)."""
    try:
        _fm, body = split_frontmatter(text)
        desc = fm_get(parse_frontmatter(text), "description")
    except Exception:
        body, desc = text, ""
    return (desc if isinstance(desc, str) else "") + "\n" + (body or "")


def candidate_identifiers(text: str) -> List[str]:
    """Sorted identifiers the memory writes AS CODE: every whole token (``[A-Za-z0-9_]+``
    run starting with a letter or underscore, at least 3 chars) inside a backtick span of
    its description or body. Dotted names and calls contribute each part (`a.b(c)` gives
    a, b, c). Prose outside backticks is not a claim about a symbol. Never raises.

    Chosen on the replay's development half over identifier-shaped tokens anywhere in the
    prose and over the brief's starting set (backticks plus snake/camel/dotted tokens):
    those name more identifiers, so more of a file's churn lands on one, and they suppressed
    more fix verdicts for each graduate made quiet."""
    try:
        prose = _memory_prose(text)
        out = set()
        for span in _BACKTICK.findall(prose):
            for tok in _TOKEN.findall(span):
                if len(tok) >= 3 and (tok[0].isalpha() or tok[0] == "_"):
                    out.add(tok)
        return sorted(out)
    except Exception:
        return []


def _tokens(text: str) -> set:
    """Every whole identifier-ish token of ``text`` (``[A-Za-z0-9_]+`` runs), so a
    candidate occurs exactly when it is one of these."""
    return set(_TOKEN.findall(text))


def anchors_in(candidates: Iterable[str], file_text: str) -> List[str]:
    """The candidates that occur (whole-identifier) in ``file_text``, sorted."""
    toks = _tokens(file_text)
    return sorted(c for c in candidates if c in toks)


# --------------------------------------------------------------------------- #
# Judgement: one cited path, one memory
# --------------------------------------------------------------------------- #
def _changed_lines(old: str, new: str) -> List[Tuple[str, str]]:
    """``[("-"|"+", line)]`` — the lines the change removed and added, as a multiset
    difference: a line that only MOVED is not a change (its text, anchors included, is
    intact). Linear time, so one huge file never stalls SessionStart."""
    a = collections.Counter(old.splitlines())
    b = collections.Counter(new.splitlines())
    out: List[Tuple[str, str]] = [("-", line) for line in (a - b).elements()]
    out.extend(("+", line) for line in (b - a).elements())
    return out


def judge_path(candidates: Sequence[str], old: Optional[str], new: Optional[str]) -> dict:
    """Judge ONE drifted cited path from its baseline and HEAD contents (``None`` = absent
    or unreadable at that side). Returns ``{"status", "anchors", "evidence"}`` where status
    is ``intact`` or one of the not-intact reasons; ``evidence`` lists the change lines that
    hit an anchor (``"-line"``/``"+line"``, bounded) or the anchors missing at HEAD."""
    if new is None:
        return {"status": "deleted", "anchors": [], "evidence": []}
    if old is None:
        return {"status": "absent_at_baseline", "anchors": [], "evidence": []}
    if "\0" in old[:8000] or "\0" in new[:8000]:
        return {"status": "binary", "anchors": [], "evidence": []}
    if old == new:
        # Net-unchanged: a commit touched the path but the content is byte-identical.
        return {"status": "intact", "anchors": anchors_in(candidates, old), "evidence": [], "unchanged": True}
    anchors = anchors_in(candidates, old)
    if not anchors:
        return {"status": "no_anchor", "anchors": [], "evidence": []}
    aset = set(anchors)
    hits: List[str] = []
    for sign, line in _changed_lines(old, new):
        if aset & _tokens(line):
            hits.append(sign + line.strip()[:160])
    new_toks = _tokens(new)
    missing = [a for a in anchors if a not in new_toks]
    if hits or missing:
        ev = hits[:5] + [f"missing at HEAD: {a}" for a in missing[:5]]
        return {"status": "anchor_touched", "anchors": anchors, "evidence": ev}
    return {"status": "intact", "anchors": anchors, "evidence": []}


def judge_memory(text: str, changed: Dict[str, Tuple[Optional[str], Optional[str]]]) -> dict:
    """Pure core of the verdict: ``changed`` maps each drifted cited path to its
    ``(baseline, head)`` contents. Quiet iff there is at least one drifted path and every
    one is intact."""
    cands = candidate_identifiers(text)
    paths = {p: judge_path(cands, old, new) for p, (old, new) in sorted(changed.items())}
    quiet = bool(paths) and all(v["status"] == "intact" for v in paths.values())
    return {"version": ANCHOR_DERIVATION_VERSION, "quiet": quiet, "paths": paths}


# --------------------------------------------------------------------------- #
# Git layer: every needed blob in ONE ``git cat-file --batch`` call
# --------------------------------------------------------------------------- #
def anchors_enabled() -> bool:
    """False only when ``HIPPO_QUIET_ARMING=0``. Never raises."""
    try:
        return os.environ.get(QUIET_ARMING_ENV, "").strip() != "0"
    except Exception:
        return True


def quiet_arming_active() -> bool:
    """Does quiet change arming? Only when the replay gate is met AND the override is not
    "0". False in this release (report-only)."""
    return QUIET_ARMING_GATE_MET and anchors_enabled()


def _repo_toplevel(start: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT,
        )
        top = (out.stdout or "").strip()
        return top or None
    except Exception:
        return None


def _cat_blobs(repo_root: str, specs: List[str]) -> Dict[str, object]:
    """``{spec: text | None | "too_large"}`` for ``<rev>:<toplevel-relative path>`` specs,
    from ONE ``git cat-file --batch`` process. A missing object (deleted path, unresolvable
    baseline) maps to ``None``; on a git failure every spec is ``None``. Never raises."""
    specs = list(dict.fromkeys(specs))
    out: Dict[str, object] = {s: None for s in specs}
    if not specs:
        return out
    try:
        res = subprocess.run(
            ["git", "-C", repo_root, "cat-file", "--batch"],
            input=("\n".join(specs) + "\n").encode("utf-8"),
            capture_output=True, check=False, timeout=_GIT_TIMEOUT,
        )
        data = res.stdout or b""
        pos = 0
        for spec in specs:
            nl = data.find(b"\n", pos)
            if nl < 0:
                break
            hdr = data[pos:nl].decode("utf-8", "replace").split()
            pos = nl + 1
            if len(hdr) == 3 and hdr[2].isdigit():
                size = int(hdr[2])
                body = data[pos:pos + size]
                pos += size + 1
                if hdr[1] == "blob":
                    out[spec] = "too_large" if size > _MAX_FILE_BYTES else body.decode("utf-8", "replace")
            # "<spec> missing" / "ambiguous": no body follows, the spec stays None
    except Exception:
        return {s: None for s in specs}
    return out


def _read_memory(memory_dir: str, name: str) -> Optional[str]:
    try:
        from .outbox import effective_path

        with open(effective_path(memory_dir, name), "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def anchor_verdicts(
    memory_dir: str,
    items: Iterable[dict],
    *,
    repo_root: Optional[str] = None,
    head: str = "HEAD",
) -> List[dict]:
    """Judge a batch of stale-shaped items (``{"name", "changed_paths"}``, as ``find_stale``
    and the watermark producer return them) at ``head``; one ``cat-file`` call covers the
    batch. Each result: ``{"name", "version", "quiet", "reason", "source_commit", "paths"}``
    — ``reason`` is ``None`` when quiet, else the not-intact statuses. Never raises."""
    results: List[dict] = []
    try:
        from .staleness import read_provenance

        root = repo_root or _repo_toplevel(memory_dir)
        work = []
        specs: List[str] = []
        for item in items:
            name = item.get("name")
            if not name:
                continue
            text = _read_memory(memory_dir, name)
            _cited, sc = read_provenance(text) if text else ([], None)
            changed = [p for p in (item.get("changed_paths") or []) if isinstance(p, str)]
            work.append((name, text, sc, changed))
            if sc and root:
                for p in changed:
                    specs += [f"{sc}:{p}", f"{head}:{p}"]
        blobs = _cat_blobs(root, specs) if root else {}
        for name, text, sc, changed in work:
            base = {"name": name, "version": ANCHOR_DERIVATION_VERSION, "source_commit": sc}
            if not text or not sc or not root or not changed:
                why = "unreadable" if not text else "no_baseline" if not sc else "no_repo" if not root else "no_drift"
                results.append({**base, "quiet": False, "reason": why, "paths": {}})
                continue
            contents = {}
            too_large = []
            for p in changed:
                old, new = blobs.get(f"{sc}:{p}"), blobs.get(f"{head}:{p}")
                if old == "too_large" or new == "too_large":
                    too_large.append(p)
                else:
                    contents[p] = (old, new)
            v = judge_memory(text, contents)
            for p in too_large:
                v["paths"][p] = {"status": "too_large", "anchors": [], "evidence": []}
            quiet = bool(v["paths"]) and all(pv["status"] == "intact" for pv in v["paths"].values())
            reasons = sorted({pv["status"] for pv in v["paths"].values() if pv["status"] != "intact"})
            results.append({**base, "quiet": quiet, "reason": None if quiet else ",".join(reasons), "paths": v["paths"]})
    except Exception:
        return results
    return results


def memory_anchor_verdict(
    memory_dir: str,
    name: str,
    *,
    repo_root: Optional[str] = None,
    head: str = "HEAD",
    changed_paths: Optional[List[str]] = None,
) -> dict:
    """ONE memory's anchors and verdict, without the staleness producer (TND-4 batches
    evidence-quiet graduates from this). ``changed_paths`` defaults to the cited paths whose
    content differs between the memory's ``source_commit`` and ``head``. Never raises."""
    base = {"name": name, "version": ANCHOR_DERIVATION_VERSION, "quiet": False, "source_commit": None, "paths": {}}
    try:
        from .staleness import read_provenance

        text = _read_memory(memory_dir, name)
        if not text:
            return {**base, "reason": "unreadable"}
        cited, sc = read_provenance(text)
        root = repo_root or _repo_toplevel(memory_dir)
        if not sc or not root:
            return {**base, "source_commit": sc, "reason": "no_baseline" if not sc else "no_repo"}
        if changed_paths is None:
            blobs = _cat_blobs(root, [s for p in cited for s in (f"{sc}:{p}", f"{head}:{p}")])
            changed_paths = [p for p in cited if blobs.get(f"{sc}:{p}") != blobs.get(f"{head}:{p}")]
        got = anchor_verdicts(memory_dir, [{"name": name, "changed_paths": changed_paths}], repo_root=root, head=head)
        return got[0] if got else {**base, "reason": "error"}
    except Exception:
        return {**base, "reason": "error"}


# --------------------------------------------------------------------------- #
# Report surfaces: doctor's one line and ``hippo staleness --anchors``
# --------------------------------------------------------------------------- #
def armed_verdicts(memory_dir: str, repo_root: str) -> Tuple[List[dict], int]:
    """``(verdicts over the ARMED stale set, detected count)`` — the set quiet would act on:
    ``find_stale`` partitioned by VOL-1 then TYPE-1, the order every arming surface uses.
    Cold path only (doctor, the CLI listing). Never raises."""
    try:
        from .staleness import find_stale
        from .staleness_policy import arming_partition

        stale = find_stale(memory_dir, repo_root)
        armed, _vol, _type = arming_partition(memory_dir, stale)
        return anchor_verdicts(memory_dir, armed, repo_root=repo_root), len(stale)
    except Exception:
        return [], 0


def doctor_line(memory_dir: str, repo_root: str) -> Dict[str, str]:
    """Doctor's ok-glyph line: how many armed stale memories would be quiet, and how to see
    them. Always ``ok`` (a count, never a nag)."""
    try:
        if not anchors_enabled():
            return {"status": "ok", "message": f"evidence anchors: off ({QUIET_ARMING_ENV}=0)."}
        verdicts, _detected = armed_verdicts(memory_dir, repo_root)
        q = sum(1 for v in verdicts if v["quiet"])
        mode = "report-only, they stay armed" if not quiet_arming_active() else "not armed"
        return {
            "status": "ok",
            "message": f"evidence anchors: {q} of {len(verdicts)} armed stale memor"
            + ("y" if len(verdicts) == 1 else "ies")
            + f" would be quiet (cited code moved, the identifiers they name did not; {mode}). "
            "List them: hippo staleness --anchors.",
        }
    except Exception as exc:
        return {"status": "ok", "message": f"evidence anchors: check skipped ({exc})."}


def check_evidence_anchors(ctx) -> Dict[str, str]:
    """Doctor's CHECKS entry (``ctx`` is the ``DoctorContext``)."""
    return doctor_line(ctx.memory_dir, ctx.repo_root)


def render_report(memory_dir: str, repo_root: str) -> List[str]:
    """``hippo staleness --anchors``: every armed stale memory with its anchor verdict —
    quiet ones first, each drifted path with its status, its anchors and evidence lines."""
    if not anchors_enabled():
        return [f"Evidence anchors are off ({QUIET_ARMING_ENV}=0)."]
    verdicts, detected = armed_verdicts(memory_dir, repo_root)
    quiet = [v for v in verdicts if v["quiet"]]
    lines = [
        f"Evidence anchors (derivation {ANCHOR_DERIVATION_VERSION}): {len(quiet)} of {len(verdicts)} armed "
        f"stale memories would be quiet ({detected} detected before the arming policy)."
    ]
    if not quiet_arming_active():
        lines.append("Report-only: arming is unchanged; every memory listed here is still armed.")
    for v in sorted(verdicts, key=lambda v: (not v["quiet"], v["name"])):
        lines.append(f"  {'quiet' if v['quiet'] else 'armed'}  {v['name']}" + ("" if v["quiet"] else f"  ({v['reason']})"))
        for p, pv in sorted(v["paths"].items()):
            shown = ", ".join(pv["anchors"][:6]) + (f" (+{len(pv['anchors']) - 6})" if len(pv["anchors"]) > 6 else "")
            lines.append(f"      {p}: {pv['status']}" + (f"; anchors: {shown}" if shown else ""))
            for ev in pv["evidence"][:3]:
                lines.append(f"        {ev}")
    return lines
