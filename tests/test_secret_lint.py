"""Tests for the SEC-2 secret-pattern lint — write-time warning + doctor corpus scan.

Hermetic: build a tmp memory dir; new_memory tests disable dense + pin CLAUDE_PROJECT_DIR
to tmp (same pattern as test_creation_convention.py). The single detector lives in
``memory.secrets`` and is exercised directly AND through both surfaces (new_memory, doctor).
"""

from __future__ import annotations

import os

from memory import secrets as S

# Obviously-fake placeholders that match the shapes but are not live credentials.
_FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # AKIA + 16 = the canonical AWS docs example
_FAKE_GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"  # ghp_ + 36 chars
_FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


_CLEAN_FLOOR = """# IC Memobot — Auto-Memory Index (durable floor)
> Always-loaded floor: the User + Working-Style memories.
## User
- [User Role](user_role.md) — solo founder.
## Working Style & Process Feedback
"""


def _floor(md, body):
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(body)


def _nm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic resolve_dirs
    md = str(tmp_path / ".claude" / "memory")
    _floor(md, _CLEAN_FLOOR)
    return md


# --------------------------------------------------------------------------- #
# secrets.scan_text — the single detector both surfaces share
# --------------------------------------------------------------------------- #
def test_scan_text_flags_aws_key():
    warnings = S.scan_text(f"the key is {_FAKE_AWS_KEY} in the trace")
    assert any("AWS access key" in w for w in warnings)


def test_scan_text_flags_github_token():
    assert any("GitHub token" in w for w in S.scan_text(f"token={_FAKE_GH_TOKEN}"))


def test_scan_text_flags_private_key_block():
    assert any("private key" in w for w in S.scan_text(_FAKE_PEM))


def test_scan_text_clean_on_ordinary_prose():
    prose = (
        "This memory documents how the deploy pipeline works: run the build, push to the "
        "registry, and the webhook triggers a rollout. Nothing secret here at all."
    )
    assert S.scan_text(prose) == []


def test_scan_text_no_false_positive_on_hex_sha():
    # DOC-15 corrected the REASON, SEC-16 made it true. Hex is not "single-class-ish": it
    # mixes letters and digits, so the old _has_mixed_classes gate returned True and never
    # fired for the shape it was documented as excluding. Hex IS single-CASE, which is what
    # the gate now tests — a property no base64/base64url secret has.
    assert S.scan_text("baseline sha 3f9a1c2e4b6d8f0a1c2e4b6d8f0a1c2e4b6d8f0a") == []


def test_scan_text_labelled_hex_no_longer_false_positives():
    """SEC-16: the same digest gets the same verdict whether or not it carries a label.

    This was the field false positive (a content-addressed asset store's digest, flagged as
    "possible high-entropy secret"). The cause was that the three predicates scored two
    DIFFERENT strings: entropy and class-mixing over the whole token, run-length over its
    longest segment — so the label itself pushed the token over the entropy bar and the
    label WAS the secret, as far as the gate could tell. All three now score the core run.
    """
    sha = "a3f5b8c2d4e6f7a9b1c3d5e7f9a2b4c6d8e0f2a4b6c8d0e2f4a6b8c0d2e4f6a8"
    assert S.scan_text(sha) == []
    assert S.scan_text(f"content_digest={sha}") == []  # the label no longer decides
    assert S.scan_text(f"sha256:{sha}") == []


def test_scan_text_catches_aws_secret_access_key_with_slashes():
    """SEC-16 (the false NEGATIVE, and the more serious half).

    `_longest_core_run` splits on `/`, but `/` is standard-base64 CONTENT, not a separator.
    AWS's OWN DOCUMENTED example secret access key fragments into 13/7/18 — every piece
    under the old floor of 20 — and scanned CLEAN. The old docstring promised 20 sat "below
    the run length a genuine >=32-char secret retains even when a LONE separator splits it
    near the middle"; a real key carries several, so the promise was false at the boundary
    it named.
    """
    aws_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # AWS docs' own example value
    assert any("high-entropy" in w for w in S.scan_text(f"aws_secret_access_key = {aws_secret}"))


def test_entropy_gate_scores_one_string_not_two():
    """The invariant behind both fixes: length, case-mixing and entropy are all measured on
    the SAME string (the core run), so no prefix/suffix can change the verdict on a payload."""
    sha = "a3f5b8c2d4e6f7a9b1c3d5e7f9a2b4c6d8e0f2a4b6c8d0e2f4a6b8c0d2e4f6a8"
    for label in ("", "digest=", "sha256:", "content_digest=", "x" * 40 + "="):
        assert S.scan_text(f"{label}{sha}") == [], f"the label {label!r} changed the verdict"


def test_camelcase_identifier_false_positive_is_known_and_unfixed():
    """Honest scope pin — SEC-16 does NOT claim to fix precision generally.

    A long camelCase identifier still trips the catch-all, and no entropy threshold can fix
    it: measured, `getUserAuthenticationTokenFromCache` scores 4.01 bits while AWS's real
    secret key core scores 3.68. The identifier is MORE 'random' by this metric than the
    secret. Separating them needs a different signal (dictionary-word structure), not a
    tuned bar. Pinned so the limitation is visible rather than folklore."""
    assert S.scan_text("the handler is getUserAuthenticationTokenFromCache in the cache") != []


def test_scan_text_never_echoes_the_secret():
    for w in S.scan_text(f"key {_FAKE_AWS_KEY}"):
        assert _FAKE_AWS_KEY not in w  # warning names the KIND, never the matched text


def test_scan_with_remediation_appends_pointer_only_when_flagged():
    flagged = S.scan_with_remediation(f"key {_FAKE_AWS_KEY}")
    assert any("AWS access key" in w for w in flagged)
    assert any("rotate the credential" in w for w in flagged)
    # clean text → empty (no remediation noise)
    assert S.scan_with_remediation("ordinary safe prose about deploys") == []


def test_scan_text_never_raises_on_bad_input():
    assert S.scan_text("") == []


# --------------------------------------------------------------------------- #
# new_memory — WARN-not-block at write time
# --------------------------------------------------------------------------- #
def test_write_memory_aws_key_in_body_warns_but_creates(tmp_path, monkeypatch):
    """AWS-style key in a body triggers the warning path AND the file is still created."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory(
        "leaky_project_mem",
        "a project note that unfortunately pasted a credential",
        "project",
        body=f"we hit an error using the key {_FAKE_AWS_KEY} against s3",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    # The write is NOT blocked — the file exists and created is True.
    assert res["created"] is True and res["error"] is None
    assert os.path.exists(os.path.join(md, "leaky_project_mem.md"))
    # ...but a non-empty warnings list surfaced the AWS-key match + remediation pointer.
    assert res["warnings"], "expected a non-empty warnings list for an AWS-style key"
    assert any("AWS access key" in w for w in res["warnings"])
    assert any("rotate the credential" in w for w in res["warnings"])
    # never echo the actual secret back to the caller
    assert all(_FAKE_AWS_KEY not in w for w in res["warnings"])


def test_write_memory_clean_body_has_empty_warnings(tmp_path, monkeypatch):
    """A normal, secret-free memory produces an empty warnings list (no false positive)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory(
        "clean_project_mem",
        "a perfectly ordinary project note about the build",
        "project",
        body="The build runs in CI and publishes an artifact. Nothing secret in here.",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None
    assert res["warnings"] == []


def test_write_memory_result_always_has_warnings_key(tmp_path, monkeypatch):
    """The result-dict shape always carries a warnings list, even on the early-return paths."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    bad = NM.write_memory("x", "d", "bogus", memory_dir=md, repo_root=str(tmp_path))
    assert bad["warnings"] == []  # invalid-type early return still has the key


# --------------------------------------------------------------------------- #
# secrets.scan_corpus — doctor's corpus-wide sweep
# --------------------------------------------------------------------------- #
def test_scan_corpus_flags_only_the_leaky_file(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "clean.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: clean\ndescription: d\n---\njust a normal note\n")
    with open(os.path.join(md, "leaky.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: leaky\ndescription: d\n---\nkey {_FAKE_AWS_KEY} here\n")
    # MEMORY.md floor is excluded by _iter_memory_files even if it contained a match.
    _floor(md, _CLEAN_FLOOR)

    findings = S.scan_corpus(md)
    files = {f["file"] for f in findings}
    assert files == {"leaky.md"}  # clean.md omitted, MEMORY.md excluded
    leaky = next(f for f in findings if f["file"] == "leaky.md")
    assert any("AWS access key" in w for w in leaky["warnings"])
    assert all(_FAKE_AWS_KEY not in w for w in leaky["warnings"])


def test_scan_corpus_clean_corpus_returns_empty(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: a\ndescription: d\n---\nordinary content\n")
    assert S.scan_corpus(md) == []


def test_scan_corpus_missing_dir_never_raises(tmp_path):
    assert S.scan_corpus(str(tmp_path / "no_such_dir")) == []


# --------------------------------------------------------------------------- #
# SEC-8 — broadened prefix set. Every FAKE below matches the SHAPE but is not a live
# credential; they live only in this (never-shipped, scan-excluded) test file.
# --------------------------------------------------------------------------- #
_FAKE_GH_PAT = "github_pat_" + "0123456789abcdefghijklmnopqrstuvwxyzAB"
_FAKE_SLACK = "xoxb-2401234567890-2409876543210-AbCdEfGhIjKlMnOpQrStUvWx"
_FAKE_SLACK_HOOK = "https://hooks.slack.com/services/T00000000/B00000000/abcdefABCDEF0123456789"
_FAKE_GOOGLE = "AIzaSyD1aB2cD3eF4gH5iJ6kL7mN8oP9qR0sT1u"
_FAKE_STRIPE = "sk_live_0123456789abcdefABCDEF00"
_FAKE_OPENAI = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"
_FAKE_ANTHROPIC = "sk-ant-api03-AbCdEf0123456789AbCdEf0123456789AbCd"
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF1234567890abcDEF1234"
_FAKE_NPM = "npm_0123456789abcdefghijklmnopqrstuvwxyzAB"
_FAKE_PYPI = "pypi-AgEIcHlwaS5vcmc" + "A" * 40
_FAKE_CONNSTR = "postgres://appuser:s3cr3tPword@db.internal:5432/prod"
# 32-char mixed-class token with no known prefix — the entropy catch-all's territory.
_HIGH_ENTROPY = "Xk7Qm2Rv9Lp4Wn8Zt3Bc6Fj1Hd5Gy0Ns"


def test_broadened_prefixes_each_flag_their_kind():
    cases = [
        (_FAKE_GH_PAT, "GitHub token"),
        (_FAKE_SLACK, "Slack credential"),
        (_FAKE_SLACK_HOOK, "Slack credential"),
        (_FAKE_GOOGLE, "Google API key"),
        (_FAKE_STRIPE, "Stripe secret key"),
        (_FAKE_OPENAI, "OpenAI API key"),
        (_FAKE_ANTHROPIC, "Anthropic API key"),
        (_FAKE_JWT, "JWT"),
        (_FAKE_NPM, "npm token"),
        (_FAKE_PYPI, "PyPI token"),
        (_FAKE_CONNSTR, "connection string"),
    ]
    for token, kind in cases:
        warnings = S.scan_text(f"leaked here: {token} in a note")
        assert any(kind in w for w in warnings), f"{kind} not flagged for {token!r}"
        assert all(token not in w for w in warnings), "must never echo the matched secret"


def test_anthropic_and_openai_keys_do_not_cross_report():
    # The sk-ant- shape is Anthropic, never the broader OpenAI sk- shape, and vice-versa.
    a = S.scan_text(_FAKE_ANTHROPIC)
    assert any("Anthropic" in w for w in a) and not any("OpenAI" in w for w in a)
    o = S.scan_text(_FAKE_OPENAI)
    assert any("OpenAI" in w for w in o) and not any("Anthropic" in w for w in o)


def test_broadened_prefixes_high_precision_near_misses_are_clean():
    # Hyphenated package names, env-var names, prose "sk-", a schema-only DB URL, a lone JWT
    # segment — none is a credential; the length/charset floors keep them clean.
    for prose in [
        "the pypi-simple-repository-api is the index protocol",
        "set npm_config_registry to your mirror before install",
        "we use sk-based key derivation in the docs example",
        "connect to the postgres:// endpoint (no creds in this string)",
        "a JWT header alone looks like eyJhbGciOiJIUzI1NiJ9 with no payload",
        "risk-averse task-oriented ask-me-anything phrasing",
    ]:
        assert S.scan_text(prose) == [], f"false positive on: {prose!r}"


def test_entropy_flag_gates_only_the_catch_all():
    # entropy ON (memory surfaces): the high-entropy token is flagged.
    assert any("high-entropy" in w for w in S.scan_text(_HIGH_ENTROPY, entropy=True))
    # entropy OFF (the SEC-8 repo/pack gate): the soft catch-all is suppressed...
    assert S.scan_text(_HIGH_ENTROPY, entropy=False) == []
    # ...but a DETERMINISTIC prefix still fires with entropy off (that's the whole gate).
    assert any("AWS access key" in w for w in S.scan_text(_FAKE_AWS_KEY, entropy=False))


# --------------------------------------------------------------------------- #
# Entropy catch-all PRECISION: `/ = _ -` are STRUCTURAL separators in ordinary prose as well as
# genuine base64/base64url secret content. When a path, `KEY=value` assignment, slash-joined
# name list, or hyphenated identifier (e.g. a model name) is read as one token, concatenating its
# several diverse SHORT segments across those separators inflates aggregate diversity enough to
# clear the ≥32-char / mixed-class / entropy≥4.0 bar and fire the soft catch-all — the exact
# noisy-warning anti-pattern the module header warns against (a false positive trains the agent
# to ignore the warning). The fix gates on the longest CONTIGUOUS opaque run (split on those
# separators): structured text has only short segments, a real secret is one long run. Every
# string below is ordinary text that appeared in the real dogfood corpus; each must scan CLEAN.
def test_entropy_no_false_positive_on_structural_prose():
    for prose in [
        # filesystem paths — slashes separate short lowercase segments, not a secret
        "the model cache lives at /Library/Caches/hippo-memory/fastembed on this machine",
        "index dir /Users/dev/GitHub/hippo/.claude/.memory-index/vectors is derived state",
        # KEY=value env-var assignments
        "the surface note branches on CLAUDE_CODE_ENTRYPOINT=claude-desktop being set",
        "export HIPPO_DISABLE_DENSE=1 keeps the hermetic suite airplane-mode-safe",
        # slash-joined name lists (e.g. the detector's own prefix inventory)
        "specific patterns cover Slack/Google/Stripe/OpenAI/Anthropic/JWT/npm/PyPI shapes",
        "skip caches like venv/pyc/pytest/hypothesis/DS_Store/egg-info in the walk",
        # hyphenated identifiers — the multilingual embedding model name, verbatim from the corpus
        "the --multilingual flag swaps in paraphrase-multilingual-MiniLM-L12-v2 for embeddings",
        "default embedder sentence-transformers/all-MiniLM-L6-v2 stays offline after bootstrap",
    ]:
        assert S.scan_text(prose) == [], f"entropy false positive on structural prose: {prose!r}"


def test_entropy_gate_is_the_longest_opaque_run():
    # The precision knob directly: a token whose entropy comes from short delimited segments has
    # a short longest-run and is CLEAN; the same class-mix as one contiguous run trips.
    assert S._longest_core_run("paraphrase-multilingual-MiniLM-L12-v2") < S._ENTROPY_CORE_MIN_LEN
    assert S._longest_core_run("A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6") >= S._ENTROPY_CORE_MIN_LEN
    # a UUID (hex + hyphens) is single-class per segment AND short-run — doubly clean
    assert S.scan_text("run id 550e8400-e29b-41d4-a716-446655440000 in the trace") == []


def test_entropy_still_flags_genuine_high_entropy_tokens():
    # The precision fix must NOT weaken the base64/hex-blob backstop — a real opaque secret with
    # no known prefix still trips the catch-all. The token class keeps `/ = + - _` so base64 and
    # base64url blobs are captured whole, and each below retains a long contiguous opaque run.
    for token in [
        "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0",         # random 40-char mixed-class alnum
        "dGhpcyBpcyBhIHJlYWxseSByYW5kb20gc2VjcmV0-_A1b2",   # base64url token with - _
        "aGVsbG8gd29ybGR0aGlzaXNhbG9uZ3NlY3JldA==",         # base64 with trailing = padding
        "abcdEFGH1234+ijklMNOP5678/qrstUVWX90ABcdEF==",     # standard base64 with + and /
    ]:
        assert any("high-entropy" in w for w in S.scan_text(token)), \
            f"genuine high-entropy secret no longer flagged: {token!r}"


# --------------------------------------------------------------------------- #
# SEC-8 — the repo/pack scan gate (scan_files / _iter_repo_files / main CLI).
# --------------------------------------------------------------------------- #
def test_scan_files_flags_planted_secret_and_omits_clean(tmp_path):
    leaky = tmp_path / "pack_note.md"
    leaky.write_text(f"a shipped note that pasted {_FAKE_STRIPE}\n", encoding="utf-8")
    clean = tmp_path / "ok.md"
    clean.write_text("an ordinary shipped note about the build\n", encoding="utf-8")

    findings = S.scan_files([str(leaky), str(clean)])
    files = {os.path.basename(f["file"]) for f in findings}
    assert files == {"pack_note.md"}
    assert any("Stripe" in w for f in findings for w in f["warnings"])


def test_scan_files_skips_binary_and_unreadable(tmp_path):
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02\xff" + _FAKE_AWS_KEY.encode())  # invalid utf-8 → skipped
    findings = S.scan_files([str(binary), str(tmp_path / "does_not_exist.md")])
    assert findings == []


def test_scan_files_uses_entropy_off(tmp_path):
    # A shipped file full of a high-entropy blob (no known prefix) must NOT fail the gate.
    f = tmp_path / "note.md"
    f.write_text(f"reference hash {_HIGH_ENTROPY}\n", encoding="utf-8")
    assert S.scan_files([str(f)]) == []


def test_iter_repo_files_excludes_tests_dir(tmp_path):
    # No git here → os.walk fallback. A planted token under tests/ must be skipped; one under
    # a shipped dir must be included.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "vectors.py").write_text(f"X = '{_FAKE_AWS_KEY}'\n", encoding="utf-8")
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / "shipped.md").write_text("clean shipped content\n", encoding="utf-8")

    files = S._iter_repo_files(str(tmp_path))
    rel = {os.path.relpath(f, str(tmp_path)).replace(os.sep, "/") for f in files}
    assert "plugin/shipped.md" in rel
    assert not any(r.startswith("tests/") for r in rel)


def test_shipped_assets_tree_is_clean():
    # Regression pin: the starter packs hippo SHIPS carry no credential shape. If this reddens,
    # a pack file added a secret — the CI gate would have caught it; so does the suite.
    import glob

    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "assets")
    md_files = glob.glob(os.path.join(root, "**", "*.md"), recursive=True)
    assert md_files, "expected shipped starter-pack files to exist"
    assert S.scan_files(md_files) == []


def test_main_cli_returns_1_on_finding_and_0_when_clean(tmp_path, capsys):
    # Not a git dir → os.walk fallback covers the whole tree.
    (tmp_path / "clean.md").write_text("nothing secret here\n", encoding="utf-8")
    assert S.main(["--repo", str(tmp_path)]) == 0
    assert "clean" in capsys.readouterr().out

    (tmp_path / "leaky.md").write_text(f"oops {_FAKE_GOOGLE}\n", encoding="utf-8")
    assert S.main(["--repo", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "Google API key" in out
    assert _FAKE_GOOGLE not in out  # the CLI reports the KIND, never the secret


# --------------------------------------------------------------------------- #
# SEC-17 — a placeholder is not a credential
# --------------------------------------------------------------------------- #
def test_connstr_placeholder_template_is_not_a_credential():
    """AC (SEC-17): the field false positive. A variable-reference template contains no
    credential at all — it is the documented CORRECT way to avoid hardcoding one — and the
    rule flagged it because it shares the `scheme://user:pass@` SHAPE.

    Not cosmetic: packs hard-REFUSES an install on any finding and capture_triage silently
    DROPS the capture, so these fail closed."""
    for template in [
        "postgres://${{PGUSER}}:${{PGPASSWORD}}@${{PGHOST}}:${{PGPORT}}/${{PGDATABASE}}",
        "postgres://user:${DB_PASS}@host:5432/db",
        "postgres://user:$DB_PASS@host/db",
        "mysql://root:<PASSWORD>@localhost/app",
        "redis://user:{{redis_pw}}@cache:6379",
        "mongodb+srv://user:%(pw)s@cluster.mongodb.net",
        "amqps://user:REDACTED@broker",
    ]:
        assert S.scan_text(template, entropy=False) == [], f"placeholder flagged: {template!r}"


def test_connstr_still_fires_on_a_real_password_beside_a_placeholder_user():
    """The username is not the secret — a real password beside a placeholder user is still
    a leak, so the allowlist is judged on the PASSWORD half only."""
    assert any(
        "connection string" in w
        for w in S.scan_text("postgres://${{PGUSER}}:hunter2SuperSecret@host/db", entropy=False)
    )


def test_connstr_still_fires_on_a_real_credential():
    assert any(
        "connection string" in w
        for w in S.scan_text("postgres://admin:s3cr3tP4ss@db.internal:5432/prod", entropy=False)
    )
    # and the pinned fake in this suite must keep firing
    assert any("connection string" in w for w in S.scan_text(_FAKE_CONNSTR, entropy=False))


def test_connstr_has_a_length_floor_like_every_other_variable_span_rule():
    """It was the only variable-span rule in _PATTERNS with no floor at all, against the
    module's own stated inclusion criterion."""
    assert S.scan_text("postgres://a:b@h", entropy=False) == []
