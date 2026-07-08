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
