"""Machine-local acknowledgement for the few corpus policy keys that change what hippo DOES.

``hippo.json`` is committed policy: it travels by pull, and it sits outside the consent
fingerprint, which covers memory files only. Most of its keys tune a warning, so a pulled
edit can at worst make hippo noisier. A few do more than that:

- ``write_path`` moves where a save lands (out of the working tree, into an outbox);
- ``gate`` lets a hook refuse a write;
- ``consent`` (``{"session_writes": true}``) lets a hook record consent for the bytes a
  session's own file tool wrote into an already-trusted corpus.

A pulled edit must not switch any of them on by itself. So a consent-sensitive key is HONOURED
on a machine only while its declared value equals the value the user acknowledged there
(``hippo trust policy --accept``). Anything else reads as "not declared", which is the
safe behaviour for every key listed here: a direct write, and a gate that only warns.
The acknowledgement lives in the machine-local trust registry, beside the corpus's consent
record, and can only extend an existing one: an untrusted corpus has nothing to honour.

Deliberately NOT a wider fingerprint: thresholds and patterns stay live-read from the
committed file, so tuning a budget never asks every clone to re-consent. Only the keys
named in ``SENSITIVE_KEYS`` are held back, and only until one explicit yes.

Every reader here is a small JSON read (hot-path safe) and never raises.
"""

from __future__ import annotations

import json
from typing import List, Optional

# key -> what acknowledging it allows, in the words the CLI and doctor print.
SENSITIVE_KEYS = {
    "write_path": "where a saved memory lands (the working tree, or an outbox beside the corpus)",
    "gate": "whether a hook may refuse a write into the corpus (it only warns otherwise)",
    "consent": (  # OUT-5
        "whether a memory this machine's own Claude Code session writes with a file tool "
        "joins recall without a review (it waits for `hippo trust review` otherwise)"
    ),
}

_ENTRY_KEY = "policy"  # trusted[<corpus>]["policy"] = {key: acknowledged value}


def _canon(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "null"


def declared(memory_dir: str, key: str):
    """``hippo.json``'s value for ``key``, or None. The legacy ``.format`` marker is never
    consulted: a consent-sensitive key has exactly one home."""
    try:
        from .provenance_format import read_policy_file

        return read_policy_file(memory_dir).get(key)
    except Exception:
        return None


def _gate_root(memory_dir: str, repo_root: Optional[str]) -> Optional[str]:
    from .trust import gate_repo_root

    return gate_repo_root(memory_dir, repo_root)


def _acks(memory_dir: str, repo_root: Optional[str]) -> Optional[dict]:
    """The acknowledged ``{key: value}`` map for this corpus; None when it is untrusted or
    the gate does not apply (no git root and no corpus content)."""
    from .trust import _trusted_entry

    root = _gate_root(memory_dir, repo_root)
    entry = _trusted_entry(root) if root else None
    if not entry:
        return None
    acks = entry.get(_ENTRY_KEY)
    return acks if isinstance(acks, dict) else {}


def honoured(memory_dir: str, key: str, repo_root: Optional[str] = None):
    """The declared value of a consent-sensitive ``key`` if this machine may act on it,
    else None.

    Honoured when the ``HIPPO_TRUST_ALL`` bypass is set (CI: no user to ask), or when the
    corpus is trusted here AND the declared value equals the acknowledged one. A key that
    is not consent-sensitive is returned as declared.
    """
    try:
        value = declared(memory_dir, key)
        if value is None or key not in SENSITIVE_KEYS:
            return value
        from .trust import trust_all

        if trust_all():
            return value
        acks = _acks(memory_dir, repo_root)
        if acks is None or key not in acks:
            return None
        return value if _canon(acks[key]) == _canon(value) else None
    except Exception:
        return None


def pending(memory_dir: str, repo_root: Optional[str] = None) -> List[dict]:
    """Consent-sensitive keys whose declared value this machine has not acknowledged, as
    ``[{"key", "declared", "acknowledged", "what"}]`` (``acknowledged`` is None when there
    is none). Empty under the bypass and for a corpus that declares none of them."""
    out: List[dict] = []
    try:
        from .trust import trust_all

        if trust_all():
            return out
        acks = _acks(memory_dir, repo_root) or {}
        for key in sorted(SENSITIVE_KEYS):
            value = declared(memory_dir, key)
            if value is None:
                continue
            if key in acks and _canon(acks[key]) == _canon(value):
                continue
            out.append(
                {
                    "key": key,
                    "declared": value,
                    "acknowledged": acks.get(key),
                    "what": SENSITIVE_KEYS[key],
                }
            )
    except Exception:
        pass
    return out


def pending_line(memory_dir: str, repo_root: Optional[str] = None) -> Optional[str]:
    """One line naming what is held back and the remedy, or None when nothing is."""
    items = pending(memory_dir, repo_root)
    if not items:
        return None
    names = ", ".join(i["key"] for i in items)
    return (
        f"this corpus's hippo.json sets {names}, which this machine has not accepted yet, "
        "so hippo is ignoring it here (saves land in the working tree; the write gate only "
        "warns; a session's own memory writes wait for review). Next step: `hippo trust policy` shows each setting; `hippo trust policy "
        "--accept` turns them on for this machine."
    )


def acknowledge(
    memory_dir: str, repo_root: Optional[str] = None, keys: Optional[List[str]] = None
) -> dict:
    """Record the CURRENT declared value of each consent-sensitive key (all of them, or
    ``keys``) as acknowledged on this machine. A key ``hippo.json`` no longer declares
    loses its acknowledgement, so a later re-declaration asks again.

    Returns ``{"ok", "accepted": {key: value}, "error"}``. Refuses (``ok`` False) on an
    untrusted corpus: an acknowledgement extends a consent, it never creates one. Only an
    explicit user action may call this; no hook and no automatic pass.
    """
    res = {"ok": False, "accepted": {}, "error": None}
    try:
        from . import trust as T

        wanted = [k for k in (keys or sorted(SENSITIVE_KEYS)) if k in SENSITIVE_KEYS]
        unknown = [k for k in (keys or []) if k not in SENSITIVE_KEYS]
        if unknown:
            res["error"] = f"not a setting that needs accepting: {', '.join(unknown)}"
            return res
        root = _gate_root(memory_dir, repo_root)
        doc = T._load_registry_doc()
        trusted = doc.get("trusted")
        entry = trusted.get(T._corpus_key(root)) if root and isinstance(trusted, dict) else None
        if not isinstance(entry, dict):
            res["error"] = "this corpus is not trusted on this machine (hippo trust review)"
            return res
        acks = entry.get(_ENTRY_KEY)
        acks = dict(acks) if isinstance(acks, dict) else {}
        for key in wanted:
            value = declared(memory_dir, key)
            if value is None:
                acks.pop(key, None)
            else:
                acks[key] = value
                res["accepted"][key] = value
        entry[_ENTRY_KEY] = acks
        res["ok"] = bool(T._write_registry_doc(doc))
        if not res["ok"]:
            res["error"] = "the trust registry could not be written"
    except Exception as exc:
        res["error"] = str(exc)
    return res


def render_policy(memory_dir: str, repo_root: Optional[str] = None) -> str:
    """What ``hippo trust policy`` prints: each consent-sensitive key this corpus declares,
    its value, and whether this machine acts on it."""
    lines: List[str] = []
    waiting = {i["key"] for i in pending(memory_dir, repo_root)}
    for key in sorted(SENSITIVE_KEYS):
        value = declared(memory_dir, key)
        if value is None:
            continue
        state = "NOT accepted on this machine (ignored)" if key in waiting else "accepted"
        lines.append(f"  {key} = {_canon(value)} — {state}\n    controls {SENSITIVE_KEYS[key]}")
    if not lines:
        return "trust policy: this corpus's hippo.json sets nothing that needs accepting."
    head = "trust policy: settings in this corpus's hippo.json that change what hippo does:"
    tail = (
        "next: hippo trust policy --accept" if waiting else "nothing is waiting on this machine."
    )
    return "\n".join([head, *lines, tail])
