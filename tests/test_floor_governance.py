"""FLR-1: floor governance, pinned end-to-end.

The field defect (em-growth-labs, 2026-08): the always-loaded MEMORY.md floor sawtoothed
over the harness's 17,500-byte warn line in 87% of 228 commits and past the 25,000-byte
read cap in 9 — silently truncating its own tail out of every session's context — while
the one lint that existed lived in a CI lane that never ran. FLR-1 puts the measurement
where the edits happen: ONE measurement (``floor_governance``), ONE phrasing
(``format_governance_summary``), three surfaces (the SessionStart ``floor`` producer, the
doctor line, the once-per-session PostToolUse nag).

Pins here:
  - ``read_floor_lint``: defaults when undeclared/garbled; bad regex degrades to
    not-declared (ED-4); warn/cap override DOWNWARD only; bools are not ints.
  - ``floor_governance``: size halves always run; policy halves only when declared;
    a missing floor measures 0 with no findings; never raises.
  - the empty norm: a lean, clean floor renders NOTHING on any surface.
  - the nag: fires once per session, only on the corpus's own MEMORY.md, only trusted,
    killed by HIPPO_DISABLE_FLOOR_NAG; rides record_from_payload's ONE context_out.
"""

from __future__ import annotations

import json
import os

from memory.doctor import DoctorContext
from memory.doctor_checks_corpus import check_floor_governance
from memory.lint_floor import (
    floor_governance,
    floor_producer,
    format_governance_summary,
    observe_floor_edit,
)
from memory.outcome import record_from_payload
from memory.provenance_format import (
    HARNESS_FLOOR_READ_CAP_BYTES,
    HARNESS_FLOOR_WARN_BYTES,
    read_floor_lint,
)

from .conftest import write_file


def _corpus(tmp_path, floor_text: str, policy: dict | None = None) -> str:
    mem = tmp_path / ".claude" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text(floor_text, encoding="utf-8")
    if policy is not None:
        (mem / ".format").write_text(json.dumps({"corpus_format": 5, "floor_lint": policy}))
    return str(mem)


_CLEAN_FLOOR = "# Memory index\n\n## User\n\n- [a](a.md) — a lean pointer\n"
_M3_RE = r"MERGED|SHIPPED|#\d{3,}|task_[0-9a-f]{6,}|\b[0-9a-f]{8}\b"


# --------------------------------------------------------------------------- #
# read_floor_lint
# --------------------------------------------------------------------------- #
def test_read_floor_lint_defaults_when_undeclared(tmp_path):
    """No marker, or a marker without the key, reads as harness-constants-only."""
    mem = _corpus(tmp_path, _CLEAN_FLOOR)
    got = read_floor_lint(mem)
    assert got == {
        "banned_re": None,
        "max_line": None,
        "warn_bytes": HARNESS_FLOOR_WARN_BYTES,
        "cap_bytes": HARNESS_FLOOR_READ_CAP_BYTES,
    }


def test_read_floor_lint_garbage_and_bad_regex_degrade(tmp_path):
    """A non-dict key, an uncompilable regex, and bool/negative sizes all degrade to
    defaults — declaration mistakes must never raise or half-apply (ED-4)."""
    mem = _corpus(tmp_path, _CLEAN_FLOOR)
    marker = os.path.join(mem, ".format")
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump({"floor_lint": ["not", "a", "dict"]}, fh)
    assert read_floor_lint(mem)["banned_re"] is None
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(
            {"floor_lint": {"banned_re": "([unclosed", "max_line": True, "warn_bytes": -5}}, fh
        )
    got = read_floor_lint(mem)
    assert got["banned_re"] is None  # uncompilable → not declared
    assert got["max_line"] is None  # bool is not an int here
    assert got["warn_bytes"] == HARNESS_FLOOR_WARN_BYTES  # negative → default


def test_read_floor_lint_overrides_downward_only(tmp_path):
    """A corpus may hold itself LEANER than the harness window; a laxer declaration is
    ignored — it would just un-warn real truncation."""
    mem = _corpus(tmp_path, _CLEAN_FLOOR, {"warn_bytes": 10_000, "cap_bytes": 90_000})
    got = read_floor_lint(mem)
    assert got["warn_bytes"] == 10_000
    assert got["cap_bytes"] == HARNESS_FLOOR_READ_CAP_BYTES


# --------------------------------------------------------------------------- #
# floor_governance + format_governance_summary
# --------------------------------------------------------------------------- #
def test_governance_size_half_always_runs(tmp_path):
    """Over-warn detection needs NO declaration — the harness window applies to every
    corpus using the native floor."""
    big = "# Memory index\n\n## User\n\n" + ("- filler line\n" * 1400)  # ~19.6KB: warn zone
    mem = _corpus(tmp_path, big)
    gov = floor_governance(mem)
    assert gov["bytes"] > HARNESS_FLOOR_WARN_BYTES
    assert gov["over_warn"] is True and gov["over_cap"] is False
    assert gov["banned"] == [] and gov["longlines"] == []  # policy halves stay off
    assert "over the 17,500B warn line" in format_governance_summary(gov)


def test_governance_cap_breach_leads_the_summary(tmp_path):
    """Past the read cap the summary says TRUNCATION, not just 'over the warn line' —
    the cap is active data loss and outranks the advisory."""
    huge = "# Memory index\n\n## User\n\n" + ("- filler line\n" * 3000)
    mem = _corpus(tmp_path, huge)
    gov = floor_governance(mem)
    assert gov["over_cap"] is True
    assert "PAST the 25,000B read cap" in format_governance_summary(gov)


def test_governance_policy_halves_run_only_when_declared(tmp_path):
    """The same floor text flags banned tokens + longlines ONLY once .format declares
    the policy — the engine is hippo's, the policy is the corpus's."""
    line = "- round — MERGED deadbeef12 task_abc123 " + ("x" * 300) + "\n"
    text = "# Memory index\n\n## User\n\n" + line
    mem_off = _corpus(tmp_path / "off", text)
    gov_off = floor_governance(mem_off)
    assert gov_off["banned"] == [] and gov_off["longlines"] == []
    mem_on = _corpus(tmp_path / "on", text, {"banned_re": _M3_RE, "max_line": 300})
    gov_on = floor_governance(mem_on)
    assert [b["token"] for b in gov_on["banned"]] == ["MERGED"]  # first match per line
    assert gov_on["banned"][0]["line"] == 5
    assert len(gov_on["longlines"]) == 1 and gov_on["longlines"][0]["chars"] > 300
    summary = format_governance_summary(gov_on)
    assert "1 banned-token line(s)" in summary and "`MERGED`" in summary


def test_governance_missing_floor_is_zero_not_error(tmp_path):
    mem = tmp_path / ".claude" / "memory"
    mem.mkdir(parents=True)
    gov = floor_governance(str(mem))
    assert gov["bytes"] == 0 and format_governance_summary(gov) is None


# --------------------------------------------------------------------------- #
# the empty norm across surfaces
# --------------------------------------------------------------------------- #
def test_clean_floor_renders_nothing_anywhere(tmp_path, monkeypatch):
    """A lean, clean floor: producer None, summary None, nag None, doctor ok — the
    empty norm every hippo surface budgets for."""
    mem = _corpus(tmp_path, _CLEAN_FLOOR, {"banned_re": _M3_RE, "max_line": 300})
    write_file(str(tmp_path), ".claude/memory/a.md", "---\nname: a\n---\nbody\n")
    assert format_governance_summary(floor_governance(mem)) is None
    assert floor_producer(mem, str(tmp_path)) is None
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    assert (
        observe_floor_edit(
            os.path.join(mem, "MEMORY.md"),
            memory_dir=mem,
            telemetry_dir=str(tmp_path / "tel"),
            session_id="s",
        )
        is None
    )
    res = check_floor_governance(
        DoctorContext(memory_dir=mem, repo_root=str(tmp_path))
    )
    assert res["status"] == "ok" and "under the" in res["message"]


def test_producer_renders_governance_without_rebloat(tmp_path):
    """The producer fires on governance alone — the pre-FLR-1 triggers (re-bloat,
    link rot) are not required for the size warning to surface."""
    big = "# Memory index\n\n## User\n\n" + ("- filler line\n" * 1400)  # warn zone
    mem = _corpus(tmp_path, big)
    out = floor_producer(mem, str(tmp_path))
    assert out is not None and "over the 17,500B warn line" in out
    assert "re-bloat" not in out


def test_doctor_warns_on_governance_findings(tmp_path):
    line = "- round — MERGED and a sha deadbeef12\n"
    mem = _corpus(tmp_path, "# Memory index\n\n## User\n\n" + line, {"banned_re": _M3_RE})
    res = check_floor_governance(DoctorContext(memory_dir=mem, repo_root=str(tmp_path)))
    assert res["status"] == "warn" and "banned-token" in res["message"]


# --------------------------------------------------------------------------- #
# the nag lane
# --------------------------------------------------------------------------- #
def _dirty_corpus(tmp_path, policy=None) -> str:
    text = "# Memory index\n\n## User\n\n- r — MERGED deadbeef12\n"
    return _corpus(tmp_path, text, policy if policy is not None else {"banned_re": _M3_RE})


def test_nag_once_per_session_and_scoped_to_the_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    mem = _dirty_corpus(tmp_path)
    tel = str(tmp_path / "tel")
    floor = os.path.join(mem, "MEMORY.md")
    first = observe_floor_edit(floor, memory_dir=mem, telemetry_dir=tel, session_id="s1")
    assert first is not None and "banned-token" in first and "EVERY session" in first
    # the sentinel landed in the telemetry dir, never the corpus
    assert os.path.isdir(os.path.join(tel, "floor_nag"))
    assert observe_floor_edit(floor, memory_dir=mem, telemetry_dir=tel, session_id="s1") is None
    # a different session fires again
    assert observe_floor_edit(floor, memory_dir=mem, telemetry_dir=tel, session_id="s2") is not None
    # a non-floor path is None regardless of session
    other = str(tmp_path / "other.md")
    assert observe_floor_edit(other, memory_dir=mem, telemetry_dir=tel, session_id="s3") is None


def test_nag_kill_switch_and_trust_gate(tmp_path, monkeypatch):
    mem = _dirty_corpus(tmp_path)
    tel = str(tmp_path / "tel")
    floor = os.path.join(mem, "MEMORY.md")
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    monkeypatch.setenv("HIPPO_DISABLE_FLOOR_NAG", "1")
    assert observe_floor_edit(floor, memory_dir=mem, telemetry_dir=tel, session_id="k") is None
    monkeypatch.delenv("HIPPO_DISABLE_FLOOR_NAG")
    # SEC-1 parity: an untrusted GIT corpus emits nothing (fail-closed, like the JIT
    # lane's own pin — delenv, not "0": any set value reads as the trust-all escape).
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_TRUST_FILE", str(tmp_path / "no-trust.json"))
    assert observe_floor_edit(
        floor, memory_dir=mem, repo_root=str(tmp_path), telemetry_dir=tel, session_id="t"
    ) is None


def test_nag_rides_record_from_payload_context(tmp_path, monkeypatch):
    """The integration pin: a mutating file-tool payload on the corpus's MEMORY.md puts
    the nag on record_from_payload's ONE context_out (QUA-2 — same list the JIT and
    fleet lanes append to); a Read payload does not."""
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    monkeypatch.setenv("HIPPO_DISABLE_JIT", "1")
    monkeypatch.setenv("HIPPO_DISABLE_PRESENCE", "1")
    repo = str(tmp_path)
    mem = _dirty_corpus(tmp_path)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    floor = os.path.join(mem, "MEMORY.md")
    ctx: list = []
    ok = record_from_payload(
        {"tool_name": "Edit", "tool_input": {"file_path": floor}, "session_id": "s"},
        memory_dir=mem,
        repo_root=repo,
        context_out=ctx,
    )
    assert ok is True
    assert any("banned-token" in line for line in ctx)
    ctx2: list = []
    record_from_payload(
        {"tool_name": "Read", "tool_input": {"file_path": floor}, "session_id": "s9"},
        memory_dir=mem,
        repo_root=repo,
        context_out=ctx2,
    )
    assert ctx2 == []
