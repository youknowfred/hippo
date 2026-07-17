"""IOP-4 — claude-mem read-only migration audit on the shipped import-adapter tail.

Hermetic: a fixture SQLite store shaped like the live one the ED-3 probe inspected
(2026-07-17: WAL-mode db at ~/.claude-mem/claude-mem.db; ``observations`` with
CHECK-constrained type, JSON-array TEXT columns; ``schema_versions`` ledger; raw
``user_prompts``). Pins the acceptance criteria — an import_candidates-shaped report
with candidate count / dedupe rate via rule_dup_candidates / secrets+portability hit
counts and ZERO writes anywhere — plus the structural pins: no pending-queue seed
(capture/_SEED_SCHEMA never referenced), no write leg at all, read-only sqlite open.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess

from memory import import_claude_mem as C
from memory import import_mdc as I

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "src"))
    with open(os.path.join(repo, "src", "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"], check=True, env=_GIT_ENV)
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return repo, md


def _store(tmp_path, observations, *, summaries=2, prompts=3) -> str:
    """A claude-mem-shaped store: the columns the adapter reads, per the ED-3 probe."""
    path = str(tmp_path / "claude-mem.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_versions (id INTEGER PRIMARY KEY, version INTEGER UNIQUE NOT NULL, applied_at TEXT NOT NULL);
        CREATE TABLE observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, sdk_session_id TEXT NOT NULL,
          project TEXT NOT NULL, text TEXT,
          type TEXT NOT NULL CHECK(type IN ('decision','bugfix','feature','refactor','discovery','change')),
          title TEXT, subtitle TEXT, facts TEXT, narrative TEXT, concepts TEXT,
          files_read TEXT, files_modified TEXT, prompt_number INTEGER,
          created_at TEXT NOT NULL, created_at_epoch INTEGER NOT NULL);
        CREATE TABLE session_summaries (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, learned TEXT);
        CREATE TABLE user_prompts (id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_text TEXT NOT NULL);
        """
    )
    conn.executemany(
        "INSERT INTO schema_versions (version, applied_at) VALUES (?, ?)",
        [(v, "2025-12-18T00:00:00Z") for v in (4, 5, 16)],
    )
    for o in observations:
        conn.execute(
            "INSERT INTO observations (sdk_session_id, project, text, type, title, subtitle,"
            " facts, narrative, concepts, files_read, files_modified, created_at, created_at_epoch)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "s1", o.get("project", "proj-a"), o.get("text", ""), o.get("type", "discovery"),
                o.get("title", "t"), o.get("subtitle", ""),
                json.dumps(o.get("facts", [])), o.get("narrative", ""),
                json.dumps(o.get("concepts", [])), json.dumps(o.get("files_read", [])),
                json.dumps(o.get("files_modified", [])), "2025-12-18T00:00:00Z", 1_766_000_000,
            ),
        )
    for i in range(summaries):
        conn.execute("INSERT INTO session_summaries (project, learned) VALUES ('proj-a', ?)", (f"l{i}",))
    for i in range(prompts):
        conn.execute("INSERT INTO user_prompts (prompt_text) VALUES (?)", (f"raw prompt {i}",))
    conn.commit()
    conn.close()
    return path


# --------------------------------------------------------------------------- #
# the audit report — counts, dedupe rate, hit counts; import_candidates-shaped
# --------------------------------------------------------------------------- #
def test_report_counts_projects_versions_and_privacy_posture(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, _md = _repo(tmp_path)
    store = _store(tmp_path, [
        {"title": "Deploy pipeline decision", "type": "decision",
         "facts": ["artifacts are cached by hash"], "project": "proj-a"},
        {"title": "Bugfix in parser", "type": "bugfix", "narrative": "the parser dropped keys",
         "project": "proj-b"},
    ])
    r = C.audit_report(store, repo_root=repo)
    assert r["error"] is None and r["exists"] is True
    assert r["schema_versions"] == [4, 5, 16]
    assert r["projects"] == {"proj-a": 1, "proj-b": 1}
    assert r["candidates"] == 2
    assert r["session_summaries"] == 2 and r["user_prompts"] == 3
    text = C.describe_audit(r)
    assert "counted, never read" in text  # raw prompts stay a privacy surface
    assert "raw prompt" not in text       # …and no prompt content leaks into the render
    assert "zero writes" in text


def test_dedupe_rate_via_rule_dup_candidates_against_governance(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, _md = _repo(tmp_path)
    rule = "Always run the linter and the type checker before every commit lands."
    with open(os.path.join(repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Rules\n\n{rule}\n")
    store = _store(tmp_path, [
        {"title": "Lint discipline", "narrative": rule},
        {"title": "Unrelated", "narrative": "The queue consumer retries with exponential backoff."},
    ])
    r = C.audit_report(store, repo_root=repo)
    assert r["dedupe_rate"] == 0.5
    by_title = {e["title"]: e for e in r["entries"]}
    assert by_title["Lint discipline"]["governance_dup"] == ["CLAUDE.md"]
    assert by_title["Unrelated"]["governance_dup"] == []


def test_secret_and_portability_hits_are_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, _md = _repo(tmp_path)
    fake_key = "AKIAIOSFODNN7EXAMPLE"
    store = _store(tmp_path, [
        {"title": "Creds observation", "narrative": f"use key {fake_key} for deploys"},
        {"title": "Local path note", "facts": ["config lives at /Users/someone/.config/app"]},
    ])
    r = C.audit_report(store, repo_root=repo)
    assert r["secret_hits"] == 1
    assert r["portability_hits"] >= 1
    by_title = {e["title"]: e for e in r["entries"]}
    assert by_title["Creds observation"]["secret"] >= 1
    assert fake_key not in C.describe_audit(r)  # findings name kinds, never values


def test_project_filter_scopes_candidates_not_the_census(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, _md = _repo(tmp_path)
    store = _store(tmp_path, [
        {"title": "A", "project": "proj-a"},
        {"title": "B", "project": "proj-b"},
    ])
    r = C.audit_report(store, repo_root=repo, project="proj-b")
    assert r["projects"] == {"proj-a": 1, "proj-b": 1}  # census stays whole-store
    assert r["candidates"] == 1
    assert [e["title"] for e in r["entries"]] == ["B"]


def test_missing_store_and_drifted_schema_degrade_legibly(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, _md = _repo(tmp_path)
    r = C.audit_report(str(tmp_path / "nope.db"), repo_root=repo)
    assert r["exists"] is False and "no claude-mem store" in r["error"]
    assert "✘" in C.describe_audit(r)
    # a store whose observations table drifted away: legible error, never a crash
    bare = str(tmp_path / "bare.db")
    sqlite3.connect(bare).executescript("CREATE TABLE something_else (id INTEGER);")
    r2 = C.audit_report(bare, repo_root=repo)
    assert r2["exists"] is True
    assert "re-probe" in r2["error"] and "ED-3" in r2["error"]


# --------------------------------------------------------------------------- #
# AC — zero writes, structurally and behaviorally
# --------------------------------------------------------------------------- #
def test_audit_writes_nothing_anywhere(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, md = _repo(tmp_path)
    store = _store(tmp_path, [{"title": "T", "narrative": "n"}])
    store_bytes = open(store, "rb").read()
    before = {
        p: sorted(os.listdir(p)) for p in (repo, md, os.path.join(repo, "src"))
    }
    C.audit_report(store, repo_root=repo)
    after = {p: sorted(os.listdir(p)) for p in (repo, md, os.path.join(repo, "src"))}
    assert before == after                      # no corpus/rules/index/queue files
    assert open(store, "rb").read() == store_bytes  # mode=ro: the store itself untouched


def test_adapter_never_touches_the_pending_queue_or_write_paths():
    """AST pin (inv1/ED-4): no capture/_SEED_SCHEMA routing, no write_memory, no
    pack machinery, no write-mode open — the write leg deliberately does not exist.
    (AST, not raw grep — the module DOCSTRING narrates what it must not do.)"""
    src = open(C.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    imported = set()
    referenced = set()
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        if isinstance(node, ast.Call):
            fn = node.func
            called.add(fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None))
    assert "capture" not in imported and ".capture" not in imported
    assert "_SEED_SCHEMA" not in referenced and "write_session_capture" not in referenced
    for banned in ("write_memory", "import_mdc_file", "pack_install_item",
                   "check_candidate", "refresh_index"):
        assert banned not in called, f"import_claude_mem must not call {banned}"
    assert "mode=ro" in src  # the read-only sqlite URI is load-bearing (WAL, no checkpoint)


def test_sibling_never_imports_its_facade():
    tree = ast.parse(open(C.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "import_mdc" != (node.module or ""), "sibling must not import its facade"
        elif isinstance(node, ast.Import):
            assert all("import_mdc" not in a.name for a in node.names)


# --------------------------------------------------------------------------- #
# the CLI — `import --from claude-mem`, report-only
# --------------------------------------------------------------------------- #
def test_cli_from_claude_mem_prints_the_audit_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, md = _repo(tmp_path)
    monkeypatch.chdir(repo)
    store = _store(tmp_path, [{"title": "T", "narrative": "n"}])
    rc = I.main(["--from", "claude-mem", "--store", store])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidates"] == 1 and report["exists"] is True
    assert sorted(os.listdir(md)) == []  # still zero writes through the CLI


def test_cli_from_cursor_prints_candidates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo, md = _repo(tmp_path)
    rules = os.path.join(repo, ".cursor", "rules")
    os.makedirs(rules)
    with open(os.path.join(rules, "r.mdc"), "w", encoding="utf-8") as fh:
        fh.write("---\ndescription: d\nglobs: src/*.py\n---\nbody\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    rc = I.main(["--from", "cursor"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert [c["slug"] for c in report] == ["r"]
