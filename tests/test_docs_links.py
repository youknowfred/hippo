"""Relative-link check over the shipped markdown (DOC-2).

The engine README was ported from the origin monorepo with links that 404 here
(docs/plans/, changelog/, tests/unit/memory_tools/). This gate keeps ported or
future docs from regressing: every RELATIVE markdown link target in shipped docs
must exist on disk. plugin/assets/ is excluded — those files are seed TEMPLATES
whose links resolve in the DESTINATION corpus (.claude/memory/), not in the repo.
"""

from __future__ import annotations

import glob
import os
import re

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_DOC_FILES = sorted(
    p
    for p in glob.glob(os.path.join(_REPO, "plugin", "**", "*.md"), recursive=True)
    + [os.path.join(_REPO, "README.md"), os.path.join(_REPO, "CONCEPTS.md")]
    if os.sep + os.path.join("plugin", "assets") + os.sep not in p
)

# [text](target) — excluding images is unnecessary (same existence rule applies).
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")


def _relative_targets(text: str):
    # Links inside fenced blocks / inline code spans are EXAMPLES, not links.
    text = _CODE_SPAN_RE.sub("", _FENCE_RE.sub("", text))
    for m in _LINK_RE.finditer(text):
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        yield target.split("#", 1)[0]  # drop a section anchor


def test_docs_exist_to_check():
    assert any(p.endswith(os.path.join("memory", "README.md")) for p in _DOC_FILES)


def test_every_relative_link_resolves():
    broken = []
    for doc in _DOC_FILES:
        with open(doc, "r", encoding="utf-8") as fh:
            text = fh.read()
        base = os.path.dirname(doc)
        for target in _relative_targets(text):
            if not os.path.exists(os.path.normpath(os.path.join(base, target))):
                broken.append(f"{os.path.relpath(doc, _REPO)} -> {target}")
    assert not broken, "dead relative links in shipped docs:\n  " + "\n  ".join(broken)
