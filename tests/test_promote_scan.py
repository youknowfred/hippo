"""EXT-2: cross-project promotion mining — report-only; promote stays per-item.

TEA-1 gave lessons a user tier and /hippo:promote lifts one memory with provenance —
but finding WHICH lessons deserve promotion was human hunch. The sweep under test
reads the machine's projects registry, keeps ONLY SEC-1-trusted corpora (an untrusted
corpus contributes nothing — not even names), mines feedback/user-type memories for
near-duplicates appearing in >=2 projects (REUSING new_memory's calibrated
``_duplicate_neighbors``, never a new similarity stack), and renders per-item
proposals that route through the EXISTING /hippo:promote flow. It writes nothing,
anywhere, ever — the empty pass is the designed norm.
"""

from __future__ import annotations

import json
import os
import subprocess

from memory import promote_scan as PS
from memory import registry as REG
from memory import trust as TRUST
from memory.build_index import build_index, default_index_dir

from .conftest import write_file

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


# A realistic corpus is never 1-2 docs; BM25's idf mass is degenerate there (the dedup
# machinery says so itself and refuses to fabricate a ratio), so every fixture project
# carries a handful of varied filler notes alongside its lesson(s).
_FILLERS = (
    ("build-pipeline-note", "the release pipeline stages artifacts before the tag step"),
    ("schema-migration-note", "database schema migrations run through the idempotent runner"),
    ("logging-format-note", "structured logs use the shared field naming convention"),
    ("retry-backoff-note", "outbound requests wrap the exponential backoff helper"),
    ("cache-invalidation-note", "the edge cache invalidates on the content hash, not the path"),
    ("worker-queue-note", "background jobs drain through the priority worker queue"),
)


def _project(tmp_path, slug: str, lessons: list, *, index: bool = True, git: bool = True) -> str:
    """One registered project: a repo with a corpus of ``(name, mtype, desc)`` memories."""
    root = str(tmp_path / slug)
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    if git:
        subprocess.run(["git", "init", "-q", root], check=True, env=_GIT_ENV)
    for name, desc in _FILLERS:
        write_file(
            md,
            f"{name}.md",
            f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\nBody.\n',
        )
    for name, mtype, desc in lessons:
        write_file(
            md,
            f"{name}.md",
            f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n'
            f'  source_commit: "aaaabbbbccccdddd"\n---\nBody of {name}.\n',
        )
    if index:
        build_index(md, default_index_dir(md))
    assert REG.register_project(root, md) is True
    return root


_LESSON = "always run the canary deploy lane before touching the production entrypoint"


def test_finds_cross_project_duplicate_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    a = _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    b = _project(tmp_path, "proj-b", [("deploy-canary-rule", "feedback", _LESSON)])
    res = PS.scan()
    assert res["projects_scanned"] == 2
    assert len(res["proposals"]) == 1
    prop = res["proposals"][0]
    sides = {s["repo"]: s for s in prop["sides"]}
    assert set(sides) == {a, b}
    assert sides[a]["name"] == "canary-first"
    assert sides[b]["name"] == "deploy-canary-rule"
    # repo@sha per side — the origin stamp /hippo:promote will carry.
    for side in prop["sides"]:
        assert side["sha"] == "aaaabbbbcccc"[:7] or side["sha"].startswith("aaaabbb")
    # ... and the rendered report routes through the EXISTING per-item promote flow.
    text = PS.render_report(res)
    assert "/hippo:promote" in text
    assert "canary-first" in text and "deploy-canary-rule" in text


def test_untrusted_corpus_contributes_nothing_not_even_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    a = _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    _b = _project(tmp_path, "proj-b", [("secret-lesson-name", "feedback", _LESSON)])
    assert TRUST.mark_trusted(a) is True  # only A is trusted; B stays quarantined
    res = PS.scan()
    assert res["projects_scanned"] == 1
    assert res["projects_untrusted"] == 1
    assert res["proposals"] == []
    text = PS.render_report(res)
    assert "secret-lesson-name" not in text, "an untrusted corpus must not leak even names"
    assert "untrusted" in text  # the skip is counted, legibly


def test_project_and_reference_types_are_not_mined(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _project(tmp_path, "proj-a", [("canary-note", "project", _LESSON)])
    _project(tmp_path, "proj-b", [("canary-copy", "feedback", _LESSON)])
    res = PS.scan()
    assert res["proposals"] == [], "only lesson-shaped types (feedback/user) are mined"


def test_empty_norm_distinct_lessons_propose_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    _project(tmp_path, "proj-b", [("branch-naming", "feedback", "use kebab-case branch names with the ticket id prefix")])
    res = PS.scan()
    assert res["proposals"] == []
    assert PS.render_report(res) == "" or "nothing" in PS.render_report(res).lower()


def test_mirrored_pair_renders_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    _project(tmp_path, "proj-b", [("canary-too", "feedback", _LESSON)])
    res = PS.scan()
    assert len(res["proposals"]) == 1, "A→B and B→A must collapse to ONE proposal"


def test_report_only_writes_nothing_anywhere(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    a = _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    b = _project(tmp_path, "proj-b", [("canary-too", "feedback", _LESSON)])

    def _tree(root):
        out = {}
        for dp, _dn, fns in os.walk(root):
            for f in fns:
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    out[p] = fh.read()
        return out

    before = {**_tree(a), **_tree(b)}
    res = PS.scan()
    assert res["proposals"]
    PS.render_report(res)
    assert {**_tree(a), **_tree(b)} == before, "the sweep must never write anything"


def test_missing_index_degrades_legibly(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    _project(tmp_path, "proj-b", [("canary-too", "feedback", _LESSON)], index=False)
    res = PS.scan()
    # The pair against the index-less corpus is skipped, and the skip is NAMED.
    assert res["proposals"] == []
    assert any("no index" in n for n in res["notes"])


def test_cli_json_and_exit_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    _project(tmp_path, "proj-b", [("canary-too", "feedback", _LESSON)])
    assert PS.main(["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["proposals"] and doc["projects_scanned"] == 2


def test_cli_empty_registry_exits_zero(tmp_path, capsys):
    assert PS.main([]) == 0  # no registered projects at all — the quietest norm
    out = capsys.readouterr().out
    assert "0 trusted project" in out or "nothing" in out.lower() or out.strip() == ""


# --------------------------------------------------------------------------- #
# The sleep morning report gains the section (SLP-1's registry-driven rail)
# --------------------------------------------------------------------------- #
def test_sleep_report_carries_promotion_section_when_nonempty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    a = _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    _project(tmp_path, "proj-b", [("canary-too", "feedback", _LESSON)])
    from memory import sleep as SLEEP

    md = os.path.join(a, ".claude", "memory")
    rc = SLEEP.main(["--memory-dir", md, "--repo-root", a])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Cross-project promotion candidates" in out
    assert "/hippo:promote" in out


def test_sleep_report_empty_norm_omits_the_section(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    a = _project(tmp_path, "proj-a", [("canary-first", "feedback", _LESSON)])
    from memory import sleep as SLEEP

    md = os.path.join(a, ".claude", "memory")
    rc = SLEEP.main(["--memory-dir", md, "--repo-root", a])
    assert rc == 0
    assert "Cross-project promotion candidates" not in capsys.readouterr().out
