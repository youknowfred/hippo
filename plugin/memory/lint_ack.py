"""TND-13: acknowledging a cleared lint finding — counted, not warned, re-armed by any edit.

A memory file can carry a secret-lint or threat-lint finding its owner has read and cleared
(a high-entropy run that is a public fixture id, an HTML comment that is a deliberate
template marker). Before this, such a finding warned on every doctor run and every review
packet, with no way to say "seen, fine" — and a warning that can never be cleared trains
people to stop reading the line.

The corpus policy file ``.claude/memory/hippo.json`` gains one key, ``acknowledged_findings``:

    {"acknowledged_findings": [
        {"file": "notes.md", "rule": "html-comment",
         "sha256": "<hex of the file's full bytes when reviewed>",
         "reason": "template marker, reviewed", "date": "2026-11-12"}
    ]}

A finding is ACKNOWLEDGED when an entry names its file and rule AND the file's current bytes
hash to the entry's ``sha256``. Any change to the file — one byte — re-arms it: the finding
warns again and doctor names the acknowledgement as no longer matching. An entry missing a
field, with a non-hex hash, or naming a path with a separator is dropped. The key lives only
in ``hippo.json`` (it never had a ``.format`` home), has no writer (operator-edited policy,
like ``volatile_paths``), and an absent key changes nothing anywhere.

SCOPE, each half load-bearing:

  - Only corpus-memory lint honours it: doctor's corpus secret and threat checks, and the
    review packet. The repo-wide release scan (``secrets.main`` / the CI secret-scan lane)
    never reads it; neither does the write-time ticket (a write produces new bytes, which by
    definition are not the reviewed ones) nor the ``secrets_scan`` tool (caller text, no file).
  - CONSENT. The trust fingerprint covers memory files only — ``hippo.json`` is not in it, so
    a pulled change to the list does not re-prompt. An acknowledgement arriving by
    ``git pull`` must not silence a finding the local user never saw, so locally it counts
    only when the corpus is trusted with a per-file baseline AND the acknowledged bytes are
    the CONSENTED bytes of that file (the user consented to exactly the content the
    acknowledgement covers). Otherwise the finding warns and doctor says why.
  - REVIEW. A diff must not silence its own finding by adding the acknowledgement alongside
    the content. In the review / ``--ci`` path an acknowledgement counts only if it exists in
    ``hippo.json`` at the BASE of the range (already merged); one the range introduces is
    named in the packet and takes effect only after it merges.

Never raises; stdlib only. Rule ids are lowercase slugs (``aws-access-key``,
``high-entropy-secret``, ``html-comment``) derived from the KIND the lint already reports —
never the matched text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

ACK_KEY = "acknowledged_findings"
_FIELDS = ("file", "rule", "sha256", "reason", "date")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Threat-lint KINDS carry counts ("HTML comment: 3 comment(s) …"), so their rule id comes from
# a fixed table keyed on the stable lead of each message. Secret KINDS are fixed strings
# ("possible AWS access key detected") and slug directly.
_THREAT_RULES = (
    (re.compile(r"^invisible Unicode: \d+ zero-width"), "zero-width"),
    (re.compile(r"^invisible Unicode: \d+ bidi-control"), "bidi-control"),
    (re.compile(r"^invisible Unicode: \d+ tag-block"), "tag-block"),
    (re.compile(r"^invisible Unicode: \d+ Private-Use-Area"), "private-use-area"),
    (re.compile(r"^mixed-script confusable:"), "homograph"),
    (re.compile(r"^HTML comment:"), "html-comment"),
    (re.compile(r"^exfil shape: external image embed"), "image-embed"),
    (re.compile(r"^exfil shape: URL with a data-bearing"), "data-query"),
)

HOW_TO = (
    "To acknowledge a finding you have reviewed, add an entry with its file, rule, sha256 (the "
    f"file's hash shown here), reason and date (YYYY-MM-DD) to {ACK_KEY} in "
    ".claude/memory/hippo.json and commit it; any later edit to the file re-arms the finding."
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def rule_id(warning: str) -> str:
    """The stable rule id of one secret or threat KIND line."""
    w = str(warning or "")
    for pattern, rid in _THREAT_RULES:
        if pattern.match(w):
            return rid
    m = re.match(r"^possible (.+) detected$", w)
    if m:
        return _slug(m.group(1))
    return _slug(re.sub(r"\d+", "", w.split(":", 1)[0]))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_acknowledgements(raw) -> List[dict]:
    """The valid entries of a raw ``acknowledged_findings`` value; malformed ones drop."""
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        try:
            if not isinstance(entry, dict):
                continue
            vals = {k: entry.get(k) for k in _FIELDS}
            if not all(isinstance(v, str) and v.strip() for v in vals.values()):
                continue
            vals = {k: v.strip() for k, v in vals.items()}
            name = vals["file"]
            if "/" in name or "\\" in name or os.sep in name or name in (".", ".."):
                continue
            vals["sha256"] = vals["sha256"].lower()
            if not _HEX64.match(vals["sha256"]) or not _DATE.match(vals["date"]):
                continue
            out.append(vals)
        except Exception:
            continue
    return out


def declared_in(policy_text: str) -> Tuple[bool, List[dict]]:
    """``(declared, entries)`` from a ``hippo.json`` document's text (any git ref's copy)."""
    try:
        doc = json.loads(policy_text) if policy_text and policy_text.strip() else {}
    except Exception:
        return False, []
    if not isinstance(doc, dict) or ACK_KEY not in doc:
        return False, []
    return True, parse_acknowledgements(doc.get(ACK_KEY))


def read_acknowledgements(memory_dir: str) -> Tuple[bool, List[dict]]:
    """``(declared, entries)`` from the live ``hippo.json``. Never raises."""
    try:
        from .provenance_format import read_policy_file

        pol = read_policy_file(memory_dir)
        if ACK_KEY not in pol:
            return False, []
        return True, parse_acknowledgements(pol.get(ACK_KEY))
    except Exception:
        return False, []


def _local_consent(memory_dir: str, repo_root: Optional[str]) -> Callable[[str, str], Optional[str]]:
    """A per-file check: ``(stem, sha) -> None`` when an acknowledgement may count here, else
    the plain reason it does not (see the module docstring's CONSENT rule)."""
    try:
        from .trust import consented_hashes, gate_repo_root, is_trusted, trust_all

        if trust_all():
            reason = "trust checks are bypassed on this machine, so there is no consent to check it against"
            return lambda _s, _h: reason
        root = gate_repo_root(memory_dir, repo_root)
        if root is None or not is_trusted(root):
            reason = "this corpus is not trusted on this machine"
            return lambda _s, _h: reason
        base = consented_hashes(root)
        if base is None:
            reason = "this corpus's consent has no per-file record (`hippo trust review` renews it)"
            return lambda _s, _h: reason
    except Exception:
        return lambda _s, _h: "its consent could not be checked"

    def check(stem: str, sha: str) -> Optional[str]:
        if base.get(stem) != sha:
            return "the file changed since you consented to this corpus (`hippo trust review` shows it)"
        return None

    return check


def _file_sha(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception:
        return None


def apply_corpus(findings: List[dict], memory_dir: str, repo_root: Optional[str] = None) -> dict:
    """Split doctor's ``[{"file", "warnings"}]`` corpus findings by the acknowledgement list.

    ``{"declared", "findings", "acknowledged", "stale", "unapplied", "hashes"}`` —
    ``findings`` keeps only what still warns; ``hashes`` maps each still-warning file to its
    sha256 so the line can print it for copying. Undeclared: ``findings`` is returned as is.
    """
    out = {"declared": False, "findings": findings, "acknowledged": 0, "stale": [],
           "unapplied": [], "hashes": {}}
    try:
        declared, entries = read_acknowledgements(memory_dir)
        if not declared:
            return out
        out["declared"] = True
        consent = None
        kept: List[dict] = []
        for f in findings:
            name = f["file"]
            sha = _file_sha(os.path.join(memory_dir, name))
            remaining: List[str] = []
            for w in f["warnings"]:
                rid = rule_id(w)
                match = [e for e in entries if e["file"] == name and e["rule"] == rid]
                if not match:
                    remaining.append(w)
                    continue
                if sha is None or not any(e["sha256"] == sha for e in match):
                    out["stale"].append((name, rid))
                    remaining.append(w)
                    continue
                if consent is None:
                    consent = _local_consent(memory_dir, repo_root)
                why = consent(os.path.splitext(name)[0], sha)
                if why:
                    out["unapplied"].append((name, rid, why))
                    remaining.append(w)
                    continue
                out["acknowledged"] += 1
            if remaining:
                kept.append({"file": name, "warnings": remaining})
                if sha:
                    out["hashes"][name] = sha
        out["findings"] = kept
        return out
    except Exception:
        return {"declared": False, "findings": findings, "acknowledged": 0, "stale": [],
                "unapplied": [], "hashes": {}}


def render_parts(res: dict) -> List[str]:
    """Doctor's per-file finding texts — byte-identical to the pre-list rendering when the
    corpus declares no list; with one, each KIND names its rule and the file its sha256."""
    parts = []
    for f in res["findings"]:
        if not res["declared"]:
            parts.append(f"{f['file']}: {'; '.join(f['warnings'])}")
            continue
        kinds = "; ".join(f"{w} [rule {rule_id(w)}]" for w in f["warnings"])
        sha = res["hashes"].get(f["file"])
        parts.append(f"{f['file']}{f' (sha256 {sha})' if sha else ''}: {kinds}")
    return parts


def render_tail(res: dict) -> str:
    """The acknowledgement sentences doctor appends ('' when the corpus declares no list)."""
    if not res["declared"]:
        return ""
    bits = []
    if res["acknowledged"]:
        bits.append(
            f"{res['acknowledged']} acknowledged finding(s) not warned (listed in hippo.json "
            f"{ACK_KEY}, file unchanged since it was reviewed)."
        )
    for name, rid in res["stale"]:
        bits.append(
            f"The acknowledgement for {name} ({rid}) no longer matches the file — it changed "
            "since it was reviewed, so the finding warns again."
        )
    for name, rid, why in res["unapplied"]:
        bits.append(f"The acknowledgement for {name} ({rid}) is not applied: {why}.")
    if res["findings"]:
        bits.append(HOW_TO)
    return (" " + " ".join(bits)) if bits else ""


def filter_review(
    gate: List[dict], base_entries: List[dict], files: Dict[str, Tuple[str, str]]
) -> Tuple[List[dict], int]:
    """The review path: drop gate findings acknowledged at the range's BASE.

    ``files`` maps a touched stem to ``(corpus-relative filename, head text)``. Only
    ``base_entries`` are consulted — never the head's list — so a range cannot acknowledge
    its own finding. Returns ``(kept, acknowledged_count)``. Never raises.
    """
    if not base_entries:
        return gate, 0
    try:
        kept: List[dict] = []
        n = 0
        shas: Dict[str, str] = {}
        for f in gate:
            name, text = files.get(f["stem"], ("", ""))
            if f["stem"] not in shas:
                shas[f["stem"]] = sha256_text(text)
            rid = rule_id(f["finding"])
            if name and any(
                e["file"] == name and e["rule"] == rid and e["sha256"] == shas[f["stem"]]
                for e in base_entries
            ):
                n += 1
                continue
            kept.append(f)
        return kept, n
    except Exception:
        return gate, 0


def introduced(base_entries: List[dict], head_entries: List[dict]) -> List[dict]:
    """Head entries the base does not carry — the acknowledgements a range adds or changes."""
    seen = {tuple(e[k] for k in _FIELDS) for e in base_entries}
    return [e for e in head_entries if tuple(e[k] for k in _FIELDS) not in seen]
