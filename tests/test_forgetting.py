"""TMB-3: forgetting correctness & archive reversibility.

Five instruments, all detection-first: the archive-shadowing doctor check (read-only),
the hermetic pin that corpus builds never traverse ``archive/``, the report-only
``forgetting`` eval category (absence-polarity — an archived stem SURFACING is the
failure — through SIG-6's confirm flow, absent-from-archive = skip), the decoupled
``archive.restore`` primitive (per-item, journaled, collision-REFUSING — the guard is
load-bearing: it closes the exact shadowing hazard ``_first_seen_times`` skips
``archive/`` to avoid), and the evidence-only regret detector (abstention clusters vs
archived bodies via vendored BM25; inert text + a logged regret event; NO wiring to
restore — AST-pinned).
"""

from __future__ import annotations

import ast
import inspect
import json
import os

import memory.archive as A
import memory.build_index as B
import memory.doctor as D
import memory.eval_recall as E


def _mem(md, name, description, body="Body."):
    os.makedirs(md, exist_ok=True)
    path = os.path.join(md, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "{description}"\n---\n{body}\n')
    return path


def _archived(md, name, description, body="Archived body."):
    adir = os.path.join(md, "archive")
    os.makedirs(adir, exist_ok=True)
    path = os.path.join(adir, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "{description}"\n---\n{body}\n')
    return path


def _snap_tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as fh:
                out[p] = fh.read()
    return out


# --------------------------------------------------------------------------- #
# (b) the hermetic pin: corpus builds never traverse archive/
# --------------------------------------------------------------------------- #
def test_build_index_never_traverses_archive(memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _mem(memory_dir, "alive", "a live memory about deploys")
    _archived(memory_dir, "buried", "xylophonic quasar marmalade", body="xylophonic quasar marmalade body")
    idx_dir = B.default_index_dir(memory_dir)
    B.build_index(memory_dir, idx_dir)
    idx = B.load_index(idx_dir)
    names = {e["name"] for e in idx.entries}
    assert names == {"alive"}  # the archived stem never enters the index
    blob = json.dumps([e.get("doc_text") for e in idx.entries]) + json.dumps(
        [c.get("text") for c in idx.body_chunks]
    )
    assert "xylophonic" not in blob  # nor any of its content, anywhere in the build


# --------------------------------------------------------------------------- #
# (a) check_archive_shadowing — read-only, suggestion only
# --------------------------------------------------------------------------- #
def test_archive_shadowing_clean_then_synthetic_shadow(memory_dir, repo):
    ctx = D.DoctorContext(memory_dir, repo)
    _mem(memory_dir, "alpha", "clean corpus")
    r = D.check_archive_shadowing(ctx)
    assert r["status"] == "ok" and "no archive/" in r["message"]
    _archived(memory_dir, "beta", "distinct stem — no collision")
    r = D.check_archive_shadowing(ctx)
    assert r["status"] == "ok" and "none" in r["message"]  # zero false positives
    _archived(memory_dir, "alpha", "the SAME stem as a live memory")
    before = _snap_tree(memory_dir)
    r = D.check_archive_shadowing(ctx)
    assert r["status"] == "warn"
    assert "alpha" in r["message"] and "git mv" in r["message"]
    assert "\n" not in r["message"]
    assert _snap_tree(memory_dir) == before  # printed suggestion only — zero write


def test_archive_shadowing_registered_before_trailing_env_check():
    labels = [label for label, _fn in D.CHECKS]
    assert "archive_shadowing" in labels and "archive_regret" in labels
    assert labels[-1] == "stale_memobot_env"


# --------------------------------------------------------------------------- #
# (c) the forgetting category — SIG-6 confirm flow + absence-polarity scoring
# --------------------------------------------------------------------------- #
def test_confirm_absent_row_gates_and_lands(memory_dir):
    fp = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    _mem(memory_dir, "alive", "a live memory")
    # not actually archived -> refused (fail closed; never fabricate a forgetting row)
    r = E.confirm_hard_set_row("old retry policy", [], memory_dir=memory_dir,
                               fixture_path=fp, absent=["gone"])
    assert r["ok"] is False and "not in archive/" in r["reason"]
    # presence and absence together -> refused
    _archived(memory_dir, "gone", "retired retry policy")
    r = E.confirm_hard_set_row("old retry policy", ["alive"], memory_dir=memory_dir,
                               fixture_path=fp, absent=["gone"])
    assert r["ok"] is False and "presence OR absence" in r["reason"]
    # a real absence row lands, tagged forgetting by default
    r = E.confirm_hard_set_row("old retry policy", [], memory_dir=memory_dir,
                               fixture_path=fp, absent=["gone"])
    assert r["ok"] is True and r["absent"] == ["gone"] and r["category"] == "forgetting"
    # the presence loader ignores it (zero change to every presence metric)…
    assert E.load_hard_set(fp) == []
    # …and the absence loader is its one consumer
    assert E.load_absence_rows(fp) == [
        {"query": "old retry policy", "absent": ["gone"], "category": "forgetting"}
    ]
    # dup guard covers absence rows too
    r = E.confirm_hard_set_row("old retry policy", [], memory_dir=memory_dir,
                               fixture_path=fp, absent=["gone"])
    assert r["ok"] is False and "already a tracked fixture row" in r["reason"]


def test_absence_polarity_scoring_held_leak_and_skip(memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _mem(memory_dir, "alive", "kubernetes ingress timeout is ninety seconds")
    _archived(memory_dir, "gone", "retired deploy retry policy")
    idx_dir = B.default_index_dir(memory_dir)
    B.build_index(memory_dir, idx_dir)
    idx = B.load_index(idx_dir)
    rows = [
        {"query": "retired deploy retry policy", "absent": ["gone"], "category": "forgetting"},
        {"query": "anything at all", "absent": ["vanished"], "category": "forgetting"},
    ]
    m = E.absence_polarity_metrics(idx, rows, memory_dir, k=10, index_dir=idx_dir)
    # row 1 scored + held (the archived stem cannot rank: it isn't indexed);
    # row 2 skipped (its target is absent from archive/ — the expectation ended)
    assert m == {"n": 1, "skipped": 1, "held": 1, "absence": 1.0}

    # THE LEAK: a live shadow with the archived stem's name resurrects the content —
    # exactly the hazard the restore collision-guard refuses to create silently.
    _mem(memory_dir, "gone", "retired deploy retry policy resurrected")
    B.build_index(memory_dir, idx_dir)
    idx = B.load_index(idx_dir)
    m = E.absence_polarity_metrics(idx, rows[:1], memory_dir, k=10, index_dir=idx_dir)
    assert m["n"] == 1 and m["held"] == 0 and m["absence"] == 0.0


def test_forgetting_category_line_is_report_only_and_absent_emits_nothing(
    memory_dir, monkeypatch, capsys
):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _mem(memory_dir, "alive", "kubernetes ingress timeout is ninety seconds")
    idx_dir = B.default_index_dir(memory_dir)
    B.build_index(memory_dir, idx_dir)
    idx = B.load_index(idx_dir)
    fp = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    # no forgetting rows -> no report key (baselines/fingerprints stay byte-identical)
    rep = E.evaluate(memory_dir, idx_dir, None)
    assert "forgetting" not in rep
    # with an absence row -> the key appears, and no gate consumes it
    _archived(memory_dir, "gone", "retired deploy retry policy")
    E.confirm_hard_set_row("retired deploy retry policy", [], memory_dir=memory_dir,
                           fixture_path=fp, absent=["gone"])
    rep = E.evaluate(memory_dir, idx_dir, fp)
    assert rep["forgetting"] == {"n": 1, "skipped": 0, "held": 1, "absence": 1.0}
    assert "forgetting" not in rep["gates"]  # report-only — never a gate
    assert rep["hard_set_n"] == 0  # presence metrics untouched by absence rows


def test_draft_forgetting_fixtures_enumerates_the_listing(memory_dir):
    _archived(memory_dir, "gone-one", "retired deploy retry policy")
    _archived(memory_dir, "gone-two", "obsolete ingress timeout rule")
    dp = os.path.join(memory_dir, "drafts.yaml")
    s = E.draft_forgetting_fixtures(memory_dir, drafts_path=dp)
    assert s["archived"] == 2 and len(s["added"]) == 2
    meta, rows = E._load_fixture_docs(dp)
    assert meta.get("draft") is True
    by_stem = {r["absent"][0]: r for r in rows}
    assert set(by_stem) == {"gone-one", "gone-two"}
    # the query is DERIVED from the archived file's own description (no fabrication)
    assert by_stem["gone-one"]["query"] == "retired deploy retry policy"
    assert by_stem["gone-one"]["expected"] == []
    # idempotent: nothing re-drafts
    s = E.draft_forgetting_fixtures(memory_dir, drafts_path=dp)
    assert s["added"] == []
    # a confirmed row stops being drafted on later runs too
    fp = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    E.confirm_hard_set_row("retired deploy retry policy", [], memory_dir=memory_dir,
                           fixture_path=fp, absent=["gone-one"])
    os.remove(dp)
    s = E.draft_forgetting_fixtures(memory_dir, drafts_path=dp)
    assert s["added"] == ["obsolete ingress timeout rule"]


# --------------------------------------------------------------------------- #
# (d) restore — per-item, journaled, collision-REFUSING
# --------------------------------------------------------------------------- #
def test_restore_moves_back_journals_and_index_recovers(memory_dir, repo, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _archived(memory_dir, "gone", "retired deploy retry policy")
    r = A.restore("gone", memory_dir, repo)
    assert r["restored"] is True and r["error"] is None
    assert os.path.isfile(os.path.join(memory_dir, "gone.md"))
    assert not os.path.exists(os.path.join(memory_dir, "archive", "gone.md"))
    journal = os.path.join(memory_dir, "archive", A._JOURNAL_NAME)
    rows = [json.loads(ln) for ln in open(journal, encoding="utf-8") if ln.strip()]
    assert any(row["name"] == "gone.md" and row["method"].startswith("restore(") for row in rows)
    # restored = recallable again: the refreshed index carries it
    idx = B.load_index(B.default_index_dir(memory_dir))
    assert "gone" in {e["name"] for e in idx.entries}


def test_restore_refuses_collision_never_overwrites(memory_dir, repo):
    _archived(memory_dir, "gone", "the ARCHIVED claim")
    live = _mem(memory_dir, "gone", "the LIVE claim")
    before = _snap_tree(memory_dir)
    r = A.restore("gone", memory_dir, repo)
    assert r["restored"] is False and r["refused"] is True
    assert "overwrite" in r["error"] and "gone.md" in r["error"]
    assert _snap_tree(memory_dir) == before  # zero filesystem change on refusal
    with open(live, encoding="utf-8") as fh:
        assert "the LIVE claim" in fh.read()  # current truth intact


def test_restore_missing_and_dry_run(memory_dir, repo):
    r = A.restore("never-archived", memory_dir, repo)
    assert r["restored"] is False and "not archived" in r["error"]
    _archived(memory_dir, "gone", "retired")
    before = _snap_tree(memory_dir)
    r = A.restore("gone", memory_dir, repo, dry_run=True)
    assert r["restored"] is True  # would-restore preview
    assert _snap_tree(memory_dir) == before  # …with zero filesystem change


def test_restore_is_single_item_only_no_bulk_path():
    """Mirrors the no-bulk-archive pin: one stem, no batch/list/--all parameter."""
    sig = inspect.signature(A.restore)
    params = set(sig.parameters)
    assert "name" in params
    assert "names" not in params and "bulk" not in params and "all" not in params


def test_restore_cli_flag(memory_dir, repo, capsys, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _archived(memory_dir, "gone", "retired deploy retry policy")
    rc = A.main(["--restore", "gone", "--memory-dir", memory_dir, "--repo-root", repo])
    out = capsys.readouterr().out
    assert rc == 0 and "moved back into the live corpus" in out
    rc = A.main(["--restore", "gone", "--memory-dir", memory_dir, "--repo-root", repo])
    out = capsys.readouterr().out
    assert "refused" in out  # nothing left in archive/ under that name


# --------------------------------------------------------------------------- #
# (e) the regret detector — evidence only, logged, zero restore wiring
# --------------------------------------------------------------------------- #
def _seed_abstentions(td, query, n=3):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(
                json.dumps(
                    {"session_id": f"s{i}", "names": [], "backend": "none",
                     "query_preview": query}
                )
                + "\n"
            )


def test_regret_detector_matches_archived_body(memory_dir, tmp_path):
    td = str(tmp_path / "tele")
    _archived(memory_dir, "gone", "retired deploy retry policy",
              body="The deploy retry policy waits three seconds between attempts.")
    _seed_abstentions(td, "what is the deploy retry policy")
    matches = A.archive_regret(memory_dir, telemetry_dir=td)
    assert len(matches) == 1
    assert matches[0]["stem"] == "gone" and matches[0]["count"] == 3
    assert matches[0]["overlap"] >= A._REGRET_MIN_OVERLAP
    # a cluster sharing nothing with archived bodies never matches
    td2 = str(tmp_path / "tele2")
    _seed_abstentions(td2, "unrelated billing question entirely")
    assert A.archive_regret(memory_dir, telemetry_dir=td2) == []


def test_doctor_regret_logs_event_once_and_restores_nothing(memory_dir, repo, tmp_path, monkeypatch):
    from memory.telemetry import read_archive_regret

    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    _archived(memory_dir, "gone", "retired deploy retry policy",
              body="The deploy retry policy waits three seconds between attempts.")
    _seed_abstentions(td, "what is the deploy retry policy")
    ctx = D.DoctorContext(memory_dir, repo)
    before = _snap_tree(memory_dir)
    r = D.check_archive_regret(ctx)
    assert r["status"] == "warn"
    assert "gone" in r["message"] and "nothing restores automatically" in r["message"]
    assert "\n" not in r["message"]
    assert _snap_tree(memory_dir) == before  # inert: the archive is untouched
    events = list(read_archive_regret(td))
    assert [(e["query"], e["stem"]) for e in events] == [("what is the deploy retry policy", "gone")]
    D.check_archive_regret(ctx)  # a second run re-reports but does NOT re-log
    assert len(list(read_archive_regret(td))) == 1


def test_no_code_path_connects_detector_to_restore():
    """The AC's structural pin: restore execution is reachable ONLY from the human CLI
    path (archive.main) — never from the detector, the doctor checks, or anything the
    detector's output feeds."""
    for module, allowed in ((A, {"main"}), (D, set())):
        tree = ast.parse(inspect.getsource(module))
        callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else (
                        fn.id if isinstance(fn, ast.Name) else None
                    )
                    if name == "restore":
                        callers.add(node.name)
        assert callers <= allowed, (
            f"restore() reachable from {sorted(callers - allowed)} in {module.__name__} — "
            "the detector must stay evidence-only (no auto-restore wiring, TMB-3)"
        )
