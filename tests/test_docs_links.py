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

import pytest

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


# --------------------------------------------------------------------------- #
# QUA-11: external-URL link check. `network`-marked (deselected by default so the
# hermetic suite stays airplane-mode-safe); CI's dense lane opts in via `-m network`.
# Conservative by design — only a DEFINITIVE 404/410 fails; a HEAD-hostile 403/405/429
# is retried as GET, and any transient error (timeout, DNS, connection reset) is skipped
# rather than reddening the suite on network weather.
# --------------------------------------------------------------------------- #
_EXTERNAL_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_DEAD_CODES = frozenset({404, 410})
_UA = "Mozilla/5.0 (compatible; hippo-docs-linkcheck/1.0)"


def _external_targets(text: str):
    text = _CODE_SPAN_RE.sub("", _FENCE_RE.sub("", text))
    for m in _EXTERNAL_LINK_RE.finditer(text):
        yield m.group(1).rstrip(".,)")  # trailing sentence punctuation isn't part of the URL


def _all_external_urls():
    urls = set()
    for doc in _DOC_FILES:
        with open(doc, "r", encoding="utf-8") as fh:
            text = fh.read()
        urls.update(_external_targets(text))
    return sorted(urls)


def _status_code(url: str):
    """The HTTP status for ``url``, or None if the request errored transiently (skip, don't fail).

    Tries HEAD; on a method/bot rejection (403/405/429) retries GET, since many hosts reject
    HEAD from a non-browser. A network-layer error returns None so a flaky runner never fails.
    """
    import urllib.error
    import urllib.request

    def _fetch(method):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (docs URLs only)
            return getattr(resp, "status", None) or resp.getcode()

    for method in ("HEAD", "GET"):
        try:
            return _fetch(method)
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 429):
                continue  # HEAD-hostile host — retry as GET
            return exc.code
        except Exception:
            return None  # transient (timeout/DNS/reset) — treat as inconclusive
    return None


@pytest.mark.network
def test_external_links_are_not_dead():
    dead = []
    for url in _all_external_urls():
        code = _status_code(url)
        if code in _DEAD_CODES:
            dead.append(f"{url} -> {code}")
    assert not dead, "dead external links in shipped docs:\n  " + "\n  ".join(dead)
