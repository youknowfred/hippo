"""OWN-2's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane rather than in it, which sits at its module-size cap. The lane passes in
its own tear machinery (``arm``/``snap``/``assert_unchanged``) and its scenario substrate
(``git_repo``/``mem``), and lists what ``scenarios`` returns in ``_SCENARIOS``;
``CRASH_CONTRACT`` there declares the same two sites.
"""

from __future__ import annotations

import os


def scenarios(arm, snap, assert_unchanged, git_repo, mem):
    """``[((module, function), class, scenario)]`` for the promote tend kind's write sites."""

    def cache_intact(tmp_path, monkeypatch):
        """A torn scan-cache write costs only the cache: the scan still returns its pairs and
        the previous cache keeps its bytes."""
        from memory import promote_scan as PS
        from memory import promote_tend as PT

        _root, md = git_repo(tmp_path)
        lesson = {"name": "l", "text": '---\ndescription: "d"\n---\nB.\n', "machine_local": False}
        monkeypatch.setattr(PT, "_others", lambda _md: [("sib", str(tmp_path / "sib"), str(tmp_path / "sib-md"))])
        monkeypatch.setattr(PS, "_lessons_of", lambda *a, **k: [lesson])
        monkeypatch.setattr(PS, "twins", lambda *a, **k: ([(lesson, lesson, 0.9)], None))
        os.makedirs(os.path.dirname(PT._cache_path(md)), exist_ok=True)
        assert PT.scan_for(md)["pairs"]
        before = snap(PT._cache_path(md))
        monkeypatch.setenv("HIPPO_DUP_THRESHOLD", "0.5")  # a new fingerprint: rescan, then write
        arm(monkeypatch, "promote_tend", "_write_cache")
        assert PT.scan_for(md)["pairs"]
        assert_unchanged(before)

    def adopt_detected(tmp_path, monkeypatch):
        """A torn adopt write is named in the refusal and the memory keeps its bytes."""
        from memory import promote_tend as PT

        root, md = git_repo(tmp_path)
        path = mem(md, "here-rule", body="On staging.")
        other = tmp_path / "other-md"
        other.mkdir()
        mem(str(other), "there-rule", body="On a clone.")
        before = snap(path)
        arm(monkeypatch, "promote_tend", "_adopt")
        r = PT._adopt("here-rule", {"memory_dir": str(other), "twin": "there-rule", "corpus": "sib"}, md, root)
        assert not r["ok"] and "was not written" in r["message"]
        assert_unchanged(before)

    return [
        (("promote_tend", "_write_cache"), "intact", cache_intact),
        (("promote_tend", "_adopt"), "detected", adopt_detected),
    ]
