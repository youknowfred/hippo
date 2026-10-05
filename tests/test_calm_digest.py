"""CLM-1: the calm SessionStart digest — integrity lane, orientation, one action, one count."""

from __future__ import annotations

import os

from memory import build_index as B
from memory import session_start as SS
from memory.session_start_calm import CALM_BUDGET, calm_digest


def test_parts_in_order_and_inside_the_budget():
    blocks = [
        ("trust_drift", "⚠ trust drift: 3 files withheld"),
        ("resume_card", "🧭 Where you left off\n" + "\n".join(f"  • line {i} " + "x" * 80 for i in range(40))),
        ("staleness", "⚠ 40 stale memories ..."),
        ("contradiction_inbox", "⚖ 5 pairs ..."),
        ("link_health", "links ..."),
    ]
    out = calm_digest(blocks)
    assert len(out) <= CALM_BUDGET
    assert out.startswith("⚠ trust drift")
    assert out.index("🧭") < out.index("➡ Next:")
    assert "➡ Next: settle the conflicting memories" in out  # contradictions outrank staleness
    last = out.splitlines()[-1]
    assert last.startswith("2 more item(s) queued (stale memories, link health)")
    assert 'say "tend memory"' in last  # the counted line points at the one queue
    assert "  …" in out  # orientation was cut at a whole line


def test_the_integrity_lane_is_never_cut():
    huge = "⚠ index integrity: " + "y" * 3000
    out = calm_digest([("index_integrity", huge), ("resume_card", "🧭 resume"), ("staleness", "s")])
    assert huge in out and "➡ Next:" in out


def test_calm_mode_is_the_short_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(SS, "PRODUCERS", [
        ("integrity", lambda md, rr, ctx: "⚠ malformed memory"),
        ("resume_card", lambda md, rr, ctx: "🧭 resume"),
        ("staleness", lambda md, rr, ctx: "stale " * 2000),
    ])
    monkeypatch.setattr(SS, "_build_run_context", lambda md, rr: None)
    monkeypatch.setenv("HIPPO_ATTENTION", "calm")
    out = SS.build_context(str(tmp_path), str(tmp_path))
    assert "⚠ malformed memory" in out and "🧭 resume" in out and "stale stale" not in out
    monkeypatch.setenv("HIPPO_ATTENTION", "full")
    assert "stale stale" in SS.build_context(str(tmp_path), str(tmp_path))


def test_a_corrupt_index_still_shows_the_integrity_lane_in_calm_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_ATTENTION", "calm")
    md = tmp_path / "proj" / ".claude" / "memory"
    md.mkdir(parents=True)
    (md / "a.md").write_text('---\nname: a\ndescription: "alpha"\ntype: project\n---\nbody\n')
    (md / "MEMORY.md").write_text("# Memory\n\n## User\n")
    B.build_index(str(md))
    manifest = os.path.join(B.default_index_dir(str(md)), "manifest.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        fh.write('{"schema_version": 7, "entries": [tru')  # truncated mid-write
    out = SS.build_context(str(md), str(tmp_path / "proj"))
    assert "⚠ Index integrity —" in out
