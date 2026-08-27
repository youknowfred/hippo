"""DOC-7: version-sync gate — both manifest version fields must match.

Version used to live UNSYNCED in plugin.json and marketplace.json (both stuck at 0.2.0 while the
repo shipped through v0.5.0). This is the CI check that keeps them in lockstep — it runs in the
hermetic lane, so it gates every PR to main. The stricter tag-time check (tag == both manifests
== newest CHANGELOG heading) lives in .github/workflows/release.yml, which fires on a version tag
push (post-merge, when the CHANGELOG entry is guaranteed present) rather than on every PR.
"""

from __future__ import annotations

import json
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _plugin_version():
    with open(os.path.join(_ROOT, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        return json.load(fh)["version"]


def _marketplace_version():
    with open(os.path.join(_ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    plugins = data["plugins"]
    assert len(plugins) == 1, "marketplace.json is expected to declare exactly one plugin"
    return plugins[0]["version"]


def test_both_manifest_version_fields_match():
    assert _plugin_version() == _marketplace_version(), (
        "plugin.json and marketplace.json versions have drifted — DOC-7 keeps them in lockstep"
    )


def test_version_is_valid_semver():
    assert _SEMVER.match(_plugin_version()), f"{_plugin_version()!r} is not X.Y.Z"


def _newest_changelog_heading():
    with open(os.path.join(_ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^##\s+v(\d+\.\d+\.\d+)\b", line)
            if m:
                return m.group(1)
    return None


def test_newest_changelog_heading_matches_manifest_version():
    """REL-5: the CHANGELOG check, moved to PR time — the loophole that shipped two
    releases with no notes.

    DOC-7 split the gates: manifests-match at PR time, the strict four-way match
    (tag == manifests == newest CHANGELOG heading) at TAG time in release.yml. That
    split assumed every release gets a tag — but the tag is the one step nothing
    enforces, so v1.30.0 and v1.31.0 merged version bumps with no CHANGELOG entry, no
    tag ever fired the workflow, and the gap was only found in a field audit ten days
    later. This test closes the loophole structurally: a version bump cannot MERGE
    until the CHANGELOG's newest heading names the same version (same regex as
    release.yml's), so release notes exist from the moment the version does, tag or
    no tag. Backfilled entries for already-shipped versions sit BELOW the newest
    heading and are invisible to this check by construction.
    """
    heading = _newest_changelog_heading()
    assert heading is not None, "CHANGELOG.md has no '## vX.Y.Z' heading at all"
    assert heading == _plugin_version(), (
        f"newest CHANGELOG heading v{heading} != plugin.json version {_plugin_version()} — "
        "write the release's CHANGELOG entry (RELEASING.md step 4) in the same change as "
        "the version bump; REL-5 keeps notes and version inseparable at PR time"
    )
