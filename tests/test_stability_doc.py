"""DOC-16: STABILITY.md's factual claims are pinned to the values they describe.

STABILITY.md is the document that PUBLISHES hippo's compatibility contract — the one
place a user goes to learn what will not change under them. It is also prose, and prose
rots: the 2026-07-16 audit found it claiming `corpus_format` **4** when the constant had
been **5** since v1.11.0's DRM-6 bump (eight releases of drift, in the FROZEN section, on
the number naming the format a reader's committed markdown is interpreted under), the
index schema **6** when it was **7**, no link-cache version at all, and `HIPPO_SLEEP_TIER_A`
missing from the documented env list. DOC-15 trued those up by hand; this lint is why it
should not need doing twice.

The lineage is DOC-7's: that item pinned tag == plugin.json == marketplace.json ==
CHANGELOG so a release could not misstate its own version. This pins the same class of
claim one document over — STABILITY's stated versions against the constants of record,
and its stated CLI surface against the registry.

Scope is deliberately FACTS, not policy:
  - a stated version number must equal its constant;
  - a documented env var must exist in the shipped source;
  - every shipped `HIPPO_DISABLE_*` kill switch must appear in the documented list (REL-4,
    the REVERSE direction — the founding DOC-15 incident included exactly this miss:
    `HIPPO_SLEEP_TIER_A` shipped without its doc line. Kill switches ONLY: an
    undocumented kill switch is illegible degradation (inv3) — the user facing a
    misbehaving lane cannot find the off switch the code honors. The blanket reverse
    lint over all ~60 `HIPPO_*` names is deliberately NOT built — the ranking knobs are
    undocumented on purpose, STABILITY.md's own posture);
  - the stated `bin/hippo` subcommand list must equal the registry's (INV-1 pinned the
    SCRIPT to the registry but never the published doc — the gap this closes).
What is FROZEN (which verbs, which tools) stays a human decision: adding to that list
commits hippo to a major bump to ever change it, and no test should make that call.
"""

from __future__ import annotations

import os
import re

from memory import surfaces
from memory.build_index import SCHEMA_VERSION
from memory.links import LINKS_SCHEMA_VERSION
from memory.provenance import CORPUS_FORMAT_VERSION

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(surfaces.__file__))))
_STABILITY = os.path.join(_REPO_ROOT, "STABILITY.md")

_FIX = (
    "STABILITY.md publishes hippo's compatibility contract — a wrong number there misleads "
    "every reader about what their committed corpus is interpreted under. Update the doc "
    "(or the constant), never the assertion."
)


def _doc() -> str:
    with open(_STABILITY, encoding="utf-8") as fh:
        return fh.read()


def _flat() -> str:
    """The doc with runs of whitespace collapsed.

    Load-bearing: the claims are markdown-wrapped, so ``corpus_format`` (currently\\n
    **5**) spans a line break — a naive per-line regex silently matches NOTHING and the
    lint passes while the claim rots, which is the exact failure mode this file exists to
    prevent. Every pattern below runs against the flattened text.
    """
    return re.sub(r"\s+", " ", _doc())


# (human label, regex capturing the stated number, the constant of record, its home)
_VERSION_CLAIMS = (
    (
        "corpus_format (FROZEN surface)",
        r"`corpus_format`\s*\(currently\s*\*\*(\d+)\*\*\)",
        lambda: CORPUS_FORMAT_VERSION,
        "memory/provenance.py::CORPUS_FORMAT_VERSION",
    ),
    (
        "recall index schema_version",
        r"`schema_version`,\s*currently\s*(\d+)",
        lambda: SCHEMA_VERSION,
        "memory/build_index.py::SCHEMA_VERSION",
    ),
    (
        "link cache (links.json)",
        r"`links\.json`,\s*currently\s*(\d+)",
        lambda: LINKS_SCHEMA_VERSION,
        "memory/links.py::LINKS_SCHEMA_VERSION",
    ),
)


def test_every_stated_version_matches_its_constant():
    flat = _flat()
    for label, pattern, constant, home in _VERSION_CLAIMS:
        m = re.search(pattern, flat)
        assert m, (
            f"STABILITY.md no longer states a version for {label} (pattern {pattern!r} "
            f"matched nothing). Either the claim was dropped — restore it, it is part of "
            f"the published contract — or its wording changed and this pattern needs "
            f"updating. {_FIX}"
        )
        stated, actual = int(m.group(1)), constant()
        assert stated == actual, (
            f"STABILITY.md says {label} is currently {stated}, but {home} is {actual}. "
            f"{_FIX}"
        )


def test_every_version_claim_is_actually_asserted():
    """A claim the lint cannot see is a claim that can rot. If a NEW 'currently N' number
    appears in STABILITY.md, it must join _VERSION_CLAIMS above — otherwise this file
    reads as covering the doc while quietly ignoring half of it."""
    flat = _flat()
    stated = len(re.findall(r"currently\s*(?:\*\*)?\d+", flat))
    assert stated == len(_VERSION_CLAIMS), (
        f"STABILITY.md makes {stated} 'currently <N>' claims but only "
        f"{len(_VERSION_CLAIMS)} are pinned in _VERSION_CLAIMS. Pin the new one to its "
        "constant (or, if it is not machine-checkable, reword it so it is not a bare "
        "number a reader will trust)."
    )


def _documented_operational_vars() -> list:
    """The `HIPPO_*` names STABILITY.md's documented operational list promises."""
    flat = _flat()
    m = re.search(r"these documented operational variables:(.+?)These keep their names", flat)
    assert m, "STABILITY.md's documented operational HIPPO_* list is gone or reworded"
    documented = sorted(set(re.findall(r"`(HIPPO_[A-Z0-9_]+)`", m.group(1))))
    assert documented, "the documented operational list parsed to zero variables"
    return documented


def _shipped_source() -> str:
    """Every shipped `.py`/`.sh` under plugin/memory + plugin/hooks (minus `_vendor`),
    concatenated — the one haystack both env-var directions read (doc→code and the
    REL-4 kill-switch code→doc reverse)."""
    haystack = []
    for sub in ("memory", "hooks"):
        base = os.path.join(_REPO_ROOT, "plugin", sub)
        for root, _dirs, files in os.walk(base):
            if "_vendor" in root:
                continue
            for fname in files:
                if fname.endswith((".py", ".sh")):
                    with open(os.path.join(root, fname), encoding="utf-8") as fh:
                        haystack.append(fh.read())
    return "\n".join(haystack)


def _undocumented_kill_switches(source: str, documented: list) -> list:
    """`HIPPO_DISABLE_*` names the source reads but the documented list omits — the
    REL-4 check, factored so the fixture trip-wire test proves the SAME mechanism the
    real lint runs. Kill-switch prefix ONLY: posture vars (`HIPPO_TRUST_ALL` and
    friends) and ranking knobs are deliberately out of scope."""
    shipped = sorted(set(re.findall(r"HIPPO_DISABLE_[A-Z0-9_]+", source)))
    return [v for v in shipped if v not in documented]


def test_documented_env_vars_exist_in_the_shipped_source():
    """Every HIPPO_* the doc promises to keep stable must be a var the code actually
    reads — a rename that misses the doc leaves users configuring a ghost."""
    documented = _documented_operational_vars()
    source = _shipped_source()
    missing = [v for v in documented if v not in source]
    assert not missing, (
        f"STABILITY.md documents env var(s) the shipped source never reads: {missing}. "
        "Either the var was renamed/removed (update the doc — it is a stability promise) "
        "or the name is a typo."
    )


def test_every_shipped_kill_switch_is_documented():
    """REL-4, the reverse direction: every `HIPPO_DISABLE_*` the shipped source reads
    must appear in STABILITY.md's documented operational list. An undocumented kill
    switch is illegible degradation (inv3) — the user facing a misbehaving lane cannot
    find the off switch the code honors. Green at birth (DENSE/JIT/PRESENCE, 3/3); trips
    the first time a lane ships a kill switch without its stability line."""
    source = _shipped_source()
    assert re.search(r"HIPPO_DISABLE_[A-Z0-9_]+", source), (
        "the shipped-source walk found ZERO HIPPO_DISABLE_* reads — the haystack walk "
        "broke (a vacuous pass here would let every future kill switch ship dark)"
    )
    missing = _undocumented_kill_switches(source, _documented_operational_vars())
    assert not missing, (
        f"shipped kill switch(es) {missing} are read by plugin source but missing from "
        "STABILITY.md's documented operational list — an undocumented off switch is "
        "illegible degradation (inv3). Add the doc line stating what the switch kills "
        "and what OFF restores (the HIPPO_DISABLE_JIT/PRESENCE entries are the shape)."
    )


def test_kill_switch_reverse_lint_trips_on_an_undocumented_switch():
    """The trip wire, proven on a synthetic fixture: a source that reads a
    `HIPPO_DISABLE_*` name the documented list omits must be caught by the SAME helper
    the real lint runs — this test failing means the reverse lint went vacuous."""
    fixture_source = 'if os.environ.get("HIPPO_DISABLE_SYNTHETIC_LANE"):\n    pass\n'
    missing = _undocumented_kill_switches(fixture_source, _documented_operational_vars())
    assert missing == ["HIPPO_DISABLE_SYNTHETIC_LANE"], (
        f"the kill-switch reverse lint failed to flag a synthetic undocumented switch "
        f"(got {missing}) — the mechanism the real lint relies on is broken"
    )


def test_stated_bin_hippo_subcommands_match_the_registry():
    """INV-1 pinned bin/hippo's script to surfaces.BIN_HIPPO_SUBCOMMANDS — but never to
    the PUBLISHED list, so the doc could (and did) drift on its own; `sleep` reached it
    only because T15 remembered by hand. Same registry, one more consumer."""
    flat = _flat()
    m = re.search(r"\*\*The `bin/hippo` CLI subcommands\*\*\s*—(.+?)- \*\*The MCP tool names", flat)
    assert m, "STABILITY.md's bin/hippo subcommand list is gone or reworded"
    stated = set(re.findall(r"`([a-z][a-z-]*)`", m.group(1)))
    assert stated == set(surfaces.BIN_HIPPO_SUBCOMMANDS), (
        f"STABILITY.md states bin/hippo subcommands {sorted(stated)} but the registry "
        f"(surfaces.BIN_HIPPO_SUBCOMMANDS, itself pinned to the script by INV-1) says "
        f"{sorted(surfaces.BIN_HIPPO_SUBCOMMANDS)}. Update the doc and the registry "
        "together — the published list is a stability promise."
    )
