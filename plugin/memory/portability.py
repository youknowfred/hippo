"""Portability lint for lifting a memory out of its repo (RCH-6 — shared primitive).

A memory that is true and useful INSIDE its repo can be wrong, meaningless, or outright
hazardous once lifted somewhere else: its ``cited_paths`` are toplevel-relative tracked
files of the SOURCE repo (they cannot resolve anywhere else — that IS the coupling), its
body may hard-code machine paths or a specific git remote, and a few memories encode
CONSEQUENTIAL DEFAULTS (attribution-stripping, CI-bypass policies) that must never spread
to a new context without an explicit, individual yes. Both lift surfaces — ``/hippo:promote``
(project → user tier) and pack extract — need the same check, so it lives here ONCE.

Structurally cloned from ``secrets.py`` (module-level compiled pattern list, pure ``re``,
never-raise), with two deliberate differences:
  - TWO SEVERITIES that route differently at the consuming surface. ``"warn"``
    (kind ``repo_coupling``) findings are shown so the agent strips/rewrites the coupling;
    ``"confirm"`` (kind ``consequential_default``) findings each require an individual
    per-item confirmation before the lift proceeds (inv4 spirit: err toward flagging).
    Like secrets, this module never blocks — routing is the caller's job.
  - THE DETAIL ECHOES THE MATCH. Unlike a credential, a repo path or remote is not itself
    sensitive and naming it is the whole point — the human fixing the memory needs to know
    WHICH path is coupled.

The consequential-default catalog is anchored to the shipped packs' individual-confirm set
(``test_packs._INDIVIDUAL_CONFIRM``: attribution-stripping + CI-bypass); a parity test in
``tests/test_portability.py`` pins the catalog to exactly that set, manifest-driven, so the
two lists cannot drift. "Machine-specific env values" from the design note are covered by
the absolute-home-path detector — the classic machine-specific value is a path into a
user's home directory.

NOT baked into ``write_memory`` and NOT a doctor sweep: portability is a lift-time concern.
In a project corpus, cited_paths coupling is not a defect — it is the provenance feature
working — so scanning every write (or the whole corpus) would flag healthy memories.
"""

from __future__ import annotations

import re
from typing import List, Optional

# --- repo coupling (severity "warn"): content that cannot mean the same thing elsewhere ---
# Absolute per-user paths (/Users/<name>/…, /home/<name>/…). The 1-char negative lookbehind
# keeps a mid-path component like "app/Users/controller.rb" from matching.
_ABS_HOME_PATH_RE = re.compile(r"(?<![\w.-])/(?:Users|home)/[A-Za-z0-9._/-]+")
# SSH-form git remotes (git@host:org/repo) name one specific repo. https://… reference links
# are deliberately NOT flagged — a URL resolves the same from any repo.
_GIT_REMOTE_RE = re.compile(r"\bgit@[\w.-]+:[\w./~-]+")

# --- consequential defaults (severity "confirm"): the packs' individual-confirm catalog ---
# Small, high-precision, one human-readable label per detector (the label is the finding's
# evidence class, mirroring the manifests' stated ``reason``). Window bounds ([^\n]{0,N})
# keep matches same-line and proximate.
_CONSEQUENTIAL = [
    (
        re.compile(r"co-?authored-by", re.I),
        "attribution policy touching Co-Authored-By trailers",
    ),
    (
        re.compile(r"generated with claude", re.I),
        "attribution policy touching 'Generated with Claude Code' lines",
    ),
    (
        re.compile(r"\bbypass\b[^\n]{0,30}\bci\b", re.I),
        "CI-bypass policy",
    ),
    (
        re.compile(
            r"\b(?:do\s+not|don'?t|skip|without)\b[^\n]{0,30}"
            r"\b(?:poll(?:ing)?|wait(?:ing)?)\b[^\n]{0,40}\b(?:ci|checks?)\b",
            re.I,
        ),
        "CI-bypass policy (skips waiting for checks)",
    ),
    (
        re.compile(r"\bmerge\b[^\n]{0,40}--admin", re.I),
        "CI-bypass policy (admin-override merge)",
    ),
]


def scan_portability(text: str, *, cited_paths: Optional[List[str]] = None) -> List[dict]:
    """Lint ``text`` for lift blockers; ``[]`` when fully portable. Never raises.

    Each finding is ``{"kind", "severity", "detail"}``: kind ``repo_coupling`` carries
    severity ``"warn"`` (strip/rewrite before lifting), kind ``consequential_default``
    carries severity ``"confirm"`` (individually confirm before lifting). ``cited_paths``
    defaults to the paths stamped in ``text``'s own frontmatter
    (``staleness.read_provenance``); pass them explicitly when the caller already has them.
    Findings are order-stable and deduplicated per detected value/detector.
    """
    try:
        if cited_paths is None:
            from .staleness import read_provenance

            cited_paths = read_provenance(text)[0]
        findings: List[dict] = []

        def _coupling(detail: str) -> None:
            findings.append(
                {"kind": "repo_coupling", "severity": "warn", "detail": detail}
            )

        for path in cited_paths or []:
            _coupling(
                f"cited path '{path}' is relative to the source repo "
                "and cannot resolve elsewhere"
            )
        for match in dict.fromkeys(_ABS_HOME_PATH_RE.findall(text)):
            _coupling(f"absolute path '{match}' is machine-specific")
        for match in dict.fromkeys(_GIT_REMOTE_RE.findall(text)):
            _coupling(f"git remote '{match}' names one specific repo")
        for pattern, label in _CONSEQUENTIAL:
            if pattern.search(text):
                findings.append(
                    {
                        "kind": "consequential_default",
                        "severity": "confirm",
                        "detail": (
                            f"consequential default — {label}; "
                            "confirm individually before lifting"
                        ),
                    }
                )
        return findings
    except Exception:
        return []
