"""CAP-LLM: OPT-IN capture-time triage — annotate a session-capture seed with SUGGESTIONS.

Today a raw CAP-2 capture becomes an undifferentiated seed dict: no ``type``
classification, no drafted ``description``, no semantic dedup check — all deferred to the
human-attended ``/hippo:consolidate`` drain. This module (default OFF, enabled by
``HIPPO_CAPTURE_LLM=1``) runs ONE bounded small-model call at capture time and writes the
result into the seed as ``llm_triage`` — suggested/draft fields the drain reviewer sees
FIRST but must still ratify per item. Nothing here changes what a seed IS: a proposal in a
gitignored queue awaiting explicit approval.

Three suggestions per seed:
  (a) a likely ``type`` classification (one of ``new_memory.VALID_TYPES``),
  (b) a candidate one-line ``description`` (+ a kebab ``name``, since /hippo:new needs one),
  (c) likely near-duplicate EXISTING memories — twice over: the model's own semantic flags
      (``llm_duplicate_flags``, judged from the neighbor list it was shown) AND the exact
      calibrated index machinery the drain itself runs at approval time
      (``new_memory.check_candidate`` — LIF-2/CAP-3's dry-run: dense-cosine 0.80 /
      normalized-BM25 0.45 thresholds), attached as ``dup_check``. A SECOND OPINION beside
      the thresholds, never a replacement for them.

Why this module exists (instead of the logic living in capture.py): capture's approval
gate is STRUCTURAL — an AST-pinned negative-capability test forbids ``capture.py`` from
importing the corpus-writing module at all. Triage legitimately reuses that module's
DRY-RUN checker (``check_candidate`` writes nothing, by contract and by test), so the
reuse lives here, one seam away, and capture imports only this module — lazily, behind the
flag. The firewall stands: nothing on the capture path can reach ``write_memory``, and the
byte-identical-corpus test now runs with triage ENABLED too.

Failure posture (the hook's contract): ANY failure — missing key, timeout, network error,
malformed model output, index trouble — returns ``None`` and the caller writes exactly
today's heuristic-only seed. The SessionEnd/SubagentStop hooks stay exit-0 regardless.
Budget: the hooks' hard timeout is 30s (plugin/hooks/hooks.json); the one LLM call
defaults to a 6s cap (``HIPPO_CAPTURE_LLM_TIMEOUT`` overrides) so triage plus the existing
git/index work stays well inside it.

Synchronous in the hook, BY DECISION (owner-ratified 2026-07-13): the deferred
alternative — triage at the /hippo:consolidate drain — already has a live LLM (the
draining agent itself), so a deferred standalone call would be redundant; and a background
process would add a daemon/queue surface hippo deliberately doesn't have. What made
synchronous costly was RE-triage: SubagentStop fires per subagent and SessionEnd fires
after, each rewriting the same per-session seed. The CARRY-OVER guard fixes that instead:
``enrich_seed`` fingerprints the evidence the prompt is built from and, when a prior seed's
triage carries the same fingerprint, reuses it verbatim (zero API calls). New evidence —
another episode, another changed file, a new decision — changes the fingerprint and
correctly re-triages.

Config: flag and timeout read env-first, then ``~/.claude/hippo-llm.json`` (the
``llm_client.file_setting`` seam — keys ``capture_triage`` / ``capture_timeout_s``), so one
machine-wide file can opt in without env plumbing while an env var still wins per-run.

Secret discipline: a seed's diff hunks already carry ``hunks_secret_flagged`` (scanned at
capture). Flagged hunks are NEVER sent to the API; unflagged prompt text is re-scanned
whole and the hunk excerpt is dropped (then the call aborted) if the lint still hits. The
model's OWN output is scanned too — a draft description that echoes a secret is flagged
``secret_flagged`` so the drain treats it like flagged hunks (scrub before any corpus use).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Dict, List, Optional

from . import llm_client
from .secrets import scan_text

# One bounded call, well under the 30s SessionEnd/SubagentStop hook timeout.
_DEFAULT_TIMEOUT_S = 6.0
# Prompt bounds — the seed's own caps are wider (a drain reads them locally); an API call
# pays per byte and per second, so the prompt takes a tighter slice of each signal.
_MAX_PROMPT_PREVIEWS = 10
_MAX_PROMPT_PATHS = 20
_MAX_PROMPT_DECISIONS = 5
_MAX_PROMPT_HUNK_CHARS = 2_000
_MAX_NEIGHBORS = 8
# Output bounds — a suggestion is one line, never a body.
_MAX_DESCRIPTION_CHARS = 240
_MAX_NAME_CHARS = 64

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def triage_enabled() -> bool:
    """The CAP-LLM opt-in — DEFAULT OFF. Env ``HIPPO_CAPTURE_LLM`` > config file > off.

    A SET env var decides entirely (the closed ``llm_client.TRUTHY`` set enables, any
    other value is an explicit off — so ``HIPPO_CAPTURE_LLM=0`` overrides a config file
    that says on); an UNSET one defers to ``capture_triage`` in ``hippo-llm.json``.
    Junk stays off, the flag-gating convention for every capability that ships dark.
    """
    env = os.environ.get("HIPPO_CAPTURE_LLM")
    if env is not None and env.strip():
        return env.strip() in llm_client.TRUTHY
    return llm_client.as_bool(llm_client.file_setting("capture_triage"))


def llm_timeout_s() -> float:
    """Per-call cap (seconds) — env ``HIPPO_CAPTURE_LLM_TIMEOUT`` > config
    ``capture_timeout_s`` > 6.0; malformed values fall to the default.

    Clamped to (0, 20]: the hook's hard ceiling is 30s and the existing capture work
    (git diff + queue write) needs its own headroom, so even an aggressive override
    cannot spend the whole budget on the API call.
    """
    raw = os.environ.get("HIPPO_CAPTURE_LLM_TIMEOUT", "").strip()
    val = None
    if raw:
        try:
            val = float(raw)
        except ValueError:
            val = None
    if val is None:
        cfg = llm_client.file_setting("capture_timeout_s")
        if isinstance(cfg, (int, float)) and not isinstance(cfg, bool):
            val = float(cfg)
    if val is None or val <= 0:
        return _DEFAULT_TIMEOUT_S
    return min(val, 20.0)


# The seed fields the triage prompt is actually built from — the carry-over identity.
_EVIDENCE_FIELDS = (
    "query_previews",
    "changed_paths",
    "decisions",
    "recalled_names",
    "diff_hunks",
    "hunks_secret_flagged",
)


def _evidence_fingerprint(seed: Dict) -> str:
    """A stable hash of exactly the evidence the prompt sees — the carry-over key.

    Deterministic (sorted keys) over the ``_EVIDENCE_FIELDS`` slice, so two captures of
    the SAME session state fingerprint identically while any new episode, changed file,
    decision, or hunk byte changes it. Never raises (junk hashes as its repr).
    """
    try:
        payload = json.dumps(
            {k: seed.get(k) for k in _EVIDENCE_FIELDS}, sort_keys=True, ensure_ascii=False
        )
    except Exception:
        payload = repr([seed.get(k) for k in _EVIDENCE_FIELDS])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _slugify(raw: str) -> str:
    """Kebab-case a model-suggested name; empty when nothing survives."""
    return _SLUG_RE.sub("-", (raw or "").strip().lower()).strip("-")[:_MAX_NAME_CHARS]


def _one_line(raw: str, limit: int) -> str:
    """Collapse to one bounded line — a suggestion is a description hook, never a body."""
    return " ".join((raw or "").split())[:limit].strip()


def _neighbor_context(seed: Dict, memory_dir: str, index_dir: Optional[str]) -> List[Dict]:
    """Up to ``_MAX_NEIGHBORS`` existing memories (name + description) for the prompt.

    Two sources, both cheap reads of the persisted index (no build, no download):
      - the session's own ``recalled_names`` — what recall already surfaced alongside this
        work is the natural "could this be a duplicate of one of THESE" shortlist;
      - a recall() probe over the seed's query previews + changed paths, ``index=``-pinned
        exactly like dream's offline probes (organic ranking only, no graph expansion, no
        tier fusion, no trust-gate git work).
    Empty on any trouble — the LLM then simply triages without a duplicate shortlist.
    """
    out: List[Dict] = []
    seen = set()
    try:
        from .build_index import default_index_dir, entry_description, load_index

        idx = load_index(index_dir or default_index_dir(memory_dir))
        if idx is None or not idx.entries:
            return []
        by_name = {}
        for e in idx.entries:
            name = e.get("name")
            if name and name not in by_name:
                by_name[name] = e

        def _add(name: Optional[str]) -> None:
            if not name or name in seen or name not in by_name or len(out) >= _MAX_NEIGHBORS:
                return
            seen.add(name)
            desc = _one_line(entry_description(by_name[name]) or "", 160)
            out.append({"name": name, "description": desc})

        for name in seed.get("recalled_names") or []:
            _add(name)
        if len(out) < _MAX_NEIGHBORS:
            probe_bits = (seed.get("query_previews") or [])[:5] + [
                os.path.basename(p) for p in (seed.get("changed_paths") or [])[:8]
            ]
            query = " ".join(probe_bits).strip()
            if query:
                from .recall import recall

                for hit in recall(query, k=_MAX_NEIGHBORS, index=idx):
                    _add(hit.get("name"))
    except Exception:
        return out
    return out


def _build_prompt(seed: Dict, neighbors: List[Dict]) -> str:
    """The single triage prompt: bounded session evidence + the neighbor shortlist.

    Flagged hunks are excluded up front (see the module docstring's secret discipline);
    the assembled prompt is re-scanned by the caller before any bytes leave the machine.
    """
    lines: List[str] = [
        "You are triaging ONE coding-session capture into a draft memory suggestion for a",
        "human to review. Respond with ONLY a JSON object, no prose, of the shape:",
        '{"name": "<kebab-case-slug>", "type": "<user|feedback|project|reference>",',
        ' "description": "<one line, <=200 chars, the durable fact this session learned>",',
        ' "duplicates": ["<names from EXISTING MEMORIES that already record substantially',
        ' the same fact — empty list if none>"]}',
        'OR, when the session shows no durable fact worth saving, exactly: {"abstain": true}',
        "",
        "Type definitions: user = who the user is (role, preferences); feedback = guidance",
        "on how the agent should work; project = ongoing project facts/constraints;",
        "reference = pointers to external resources (URLs, dashboards, tickets).",
        "Abstain honestly: a routine session with nothing durable to record is common —",
        'answer {"abstain": true} for it rather than inventing a candidate.',
        "",
        "SESSION EVIDENCE:",
    ]
    previews = (seed.get("query_previews") or [])[:_MAX_PROMPT_PREVIEWS]
    if previews:
        lines.append("queries the user asked:")
        lines.extend(f"  - {q}" for q in previews)
    paths = (seed.get("changed_paths") or [])[:_MAX_PROMPT_PATHS]
    if paths:
        lines.append("files changed/created: " + ", ".join(paths))
    decisions = (seed.get("decisions") or [])[:_MAX_PROMPT_DECISIONS]
    if decisions:
        lines.append("user-confirmed decisions:")
        lines.extend(f"  - {d}" for d in decisions)
    hunks = seed.get("diff_hunks") or ""
    if hunks and not seed.get("hunks_secret_flagged"):
        lines.append("diff excerpt:")
        lines.append(hunks[:_MAX_PROMPT_HUNK_CHARS])
    if neighbors:
        lines.append("")
        lines.append("EXISTING MEMORIES (candidate duplicates — judge by substance, not wording):")
        lines.extend(f"  - {n['name']}: {n['description']}" for n in neighbors)
    return "\n".join(lines)


def _parse_response(raw: str, known_names: List[str]) -> Optional[Dict]:
    """Validated suggestion fields from the model's text, or ``None``.

    WRT-1: an explicit ``{"abstain": true}`` answer is a VALID triage — it parses into
    ``{"abstained": True}``, never a failure (the honest opposite of the old forced-answer
    prompt). Otherwise a usable triage needs at least a non-empty description; everything
    else degrades field-by-field (an invalid type becomes None rather than sinking the
    description). ``duplicates`` is intersected with the names the model was actually
    shown — a model cannot flag a memory it invented.
    """
    obj = llm_client.extract_json(raw)
    if not isinstance(obj, dict):
        return None
    if obj.get("abstain"):
        return {"abstained": True}
    description = _one_line(str(obj.get("description") or ""), _MAX_DESCRIPTION_CHARS)
    if not description:
        return None
    from .new_memory import VALID_TYPES

    suggested_type = str(obj.get("type") or "").strip().lower()
    if suggested_type not in VALID_TYPES:
        suggested_type = None
    name = _slugify(str(obj.get("name") or ""))
    if not name:
        # Derive a fallback slug from the description's leading words — /hippo:new needs
        # SOME name and the drain can always rename.
        name = _slugify("-".join(description.split()[:6]))
    known = set(known_names)
    dups = []
    for d in obj.get("duplicates") or []:
        if isinstance(d, str) and d.strip() in known and d.strip() not in dups:
            dups.append(d.strip())
    return {
        "suggested_name": name or None,
        "suggested_type": suggested_type,
        "draft_description": description,
        "llm_duplicate_flags": dups,
    }


# WRT-1: the identifier classes the groundedness flag extracts from a draft description.
# Conservative BY DESIGN — near-zero false positives is what keeps the flag's authority:
# PR/issue refs, 7+-hex sha-likes (must contain a letter, so dates/timestamps never match),
# URLs, and version strings; nothing looser. Each entry is (pattern, needle_group) where
# needle_group names the group used for the evidence-membership check (0 = whole match).
_SHA_LIKE_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_IDENT_PATTERNS = (
    (re.compile(r"(?:#|\b(?:PR|pr|Pr|issue|Issue)\s+#?)(\d{1,6})\b"), 1),  # PR/issue ref
    (_SHA_LIKE_RE, 0),                                                     # sha-like
    (re.compile(r"https?://[^\s)\]}>'\"]+"), 0),                           # URL
    (re.compile(r"\bv\d+(?:\.\d+)+\b|\b\d+\.\d+\.\d+\b"), 0),             # version string
)


def _extract_identifiers(text: str) -> List[tuple]:
    """``(display, needle)`` pairs for every concrete identifier in ``text``.

    ``display`` is the surface form for the warning line ("PR #57"); ``needle`` is the
    lowercased core checked for evidence membership ("57"). Sha-likes without a letter
    are skipped (an all-digit run is a date/timestamp far more often than a real sha).
    Deterministic, deduped by needle, zero LLM. Never raises.
    """
    out: List[tuple] = []
    seen = set()
    for pat, grp in _IDENT_PATTERNS:
        for m in pat.finditer(text or ""):
            display = m.group(0).strip().rstrip(".,;:!?")
            needle = (m.group(grp) if grp else display).lower()
            if pat is _SHA_LIKE_RE and not re.search(r"[a-f]", needle):
                continue
            if needle and needle not in seen:
                seen.add(needle)
                out.append((display, needle))
    return out


def _ungrounded_tokens(draft: str, prompt_text: str) -> List[str]:
    """WRT-1 groundedness: draft identifiers that appear NOWHERE in the prompt evidence.

    ZERO-LLM by contract (the judged-lanes kill): deterministic extraction + substring
    membership against the EXACT prompt text the model saw — flag and prompt read
    identical bytes, so a flagged token is one the model cannot have taken from its
    evidence. Membership is deliberately LENIENT (a PR number's bare digits match any
    occurrence; case-insensitive; a version matches with or without its leading "v") —
    leniency costs missed catches, never false alarms. Never raises.
    """
    try:
        hay = (prompt_text or "").lower()
        out: List[str] = []
        for display, needle in _extract_identifiers(draft):
            if needle in hay or needle.lstrip("v") in hay:
                continue
            out.append(display)
        return out
    except Exception:
        return []


def enrich_seed(
    seed: Dict,
    memory_dir: str,
    *,
    repo_root: Optional[str] = None,
    index_dir: Optional[str] = None,
    timeout_s: Optional[float] = None,
    prior: Optional[Dict] = None,
) -> Optional[Dict]:
    """The whole triage: ONE LLM call + the drain's own dup check. ``None`` = fail open.

    Returns the ``llm_triage`` dict for the seed, or ``None`` on any failure — in which
    case the caller persists exactly today's heuristic-only seed. Read-only over the
    corpus and the index; the ONLY writes this function ever causes are the caller's own
    queue write. Never raises.

    ``prior`` is the PREVIOUS seed at the same queue path (SubagentStop and SessionEnd
    both rewrite one per-session seed): when its triage carries the same evidence
    fingerprint as this capture, the suggestions are CARRIED OVER verbatim — zero API
    calls — because the prompt would have been byte-identical. Any evidence change
    re-triages.
    """
    try:
        fingerprint = _evidence_fingerprint(seed)
        prior_triage = (prior or {}).get("llm_triage") if isinstance(prior, dict) else None
        if (
            isinstance(prior_triage, dict)
            and prior_triage.get("evidence_sha") == fingerprint
            and (prior_triage.get("draft_description") or prior_triage.get("abstained"))
        ):
            # An abstained prior carries over exactly like a drafted one (WRT-1) — the
            # prompt would be byte-identical, so re-asking would re-bill for the same "no".
            carried = dict(prior_triage)
            carried["carried_over"] = True
            return carried
        neighbors = _neighbor_context(seed, memory_dir, index_dir)
        prompt = _build_prompt(seed, neighbors)
        # Belt over the per-field exclusions: if the assembled prompt still lints dirty,
        # drop the hunk excerpt; if it STILL lints dirty, no bytes leave the machine.
        if scan_text(prompt):
            hunkless = dict(seed)
            hunkless["diff_hunks"] = ""
            prompt = _build_prompt(hunkless, neighbors)
            if scan_text(prompt):
                return None
        raw = llm_client.complete(
            prompt, timeout_s=llm_timeout_s() if timeout_s is None else timeout_s
        )
        if raw is None:
            return None
        parsed = _parse_response(raw, [n["name"] for n in neighbors])
        if parsed is None:
            return None
        if parsed.get("abstained"):
            # WRT-1: the model said "no durable fact" — an honest, first-class outcome.
            # No draft means no dup check, no output lint, no groundedness to compute;
            # evidence_sha still rides so the carry-over guard spares a re-bill.
            return {
                "abstained": True,
                "model": llm_client.model_name(),
                "generated_at": round(time.time(), 3),
                "evidence_sha": fingerprint,
            }

        # The drain's own calibrated near-duplicate machinery (CAP-3 dry-run — writes
        # nothing), pre-run here so the reviewer opens the seed already knowing the route.
        # Its own failure degrades to a note, never sinks the triage.
        dup_check = {"route": None, "neighbors": [], "note": "duplicate check skipped: error"}
        try:
            from .new_memory import check_candidate

            res = check_candidate(
                parsed["suggested_name"] or "pending-capture",
                parsed["draft_description"],
                parsed["suggested_type"] or "project",
                memory_dir=memory_dir,
                repo_root=repo_root,
            )
            dup_check = {
                "route": res.get("route"),
                "neighbors": res.get("neighbors") or [],
                "note": res.get("note"),
            }
        except Exception:
            pass

        flagged = bool(
            scan_text(
                json.dumps(
                    [parsed["draft_description"], parsed["suggested_name"]], ensure_ascii=False
                )
            )
        )
        result = {
            **parsed,
            "dup_check": dup_check,
            "model": llm_client.model_name(),
            "generated_at": round(time.time(), 3),
            "secret_flagged": flagged,
            "evidence_sha": fingerprint,
        }
        # WRT-1 groundedness flag: mechanical doubt over the draft, checked against the
        # EXACT prompt the model saw (``prompt`` here is post-belt — if the hunk excerpt
        # was dropped above, the flag sees the same hunkless text). Additive, absent when
        # clean (ED-4); computed at fresh-triage time only — a carried-over triage rides
        # with whatever it was flagged (or not) when its evidence was current.
        ungrounded = _ungrounded_tokens(parsed["draft_description"], prompt)
        if ungrounded:
            result["ungrounded_tokens"] = ungrounded
        return result
    except Exception:
        return None
