"""Contract tests over the shipped SKILL.md files.

The skills ARE the lifecycle — their embedded code blocks run verbatim in consumers'
shells. This file pins the cross-skill contracts that prose reviews miss: the ONB-7/DOC-3/
OSP-6/COR-7/LIF-2 guarantees below, plus the full QUA-8 skills-contract suite at the bottom
of the file — every fenced code block in every SKILL.md is extracted and checked for real
(python compiles + every memory-package reference resolves + every resolved call's keyword
arguments bind to the real signature; bash syntax-checks; every referenced path exists or
follows the canonical trio naming). The QUA-8 AC, literally: renaming ``evaluate()`` fails
this suite before it ships.
"""

from __future__ import annotations

import ast
import glob
import importlib
import inspect
import os
import re
import subprocess

_PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_SKILLS_DIR = os.path.join(_PLUGIN_DIR, "skills")
_ALL_SKILLS = sorted(glob.glob(os.path.join(_SKILLS_DIR, "*", "SKILL.md")))
_RESOLVE_PY_SH = os.path.join(_PLUGIN_DIR, "hooks", "_resolve_py.sh")

# The shared ONB-7 preflight guard: unset/empty CLAUDE_PLUGIN_DATA must stop a skill
# BEFORE any code block expands it (`uv venv "/venv"` provisions a root-owned path).
_GUARD = '[ -n "${CLAUDE_PLUGIN_DATA:-}" ] ||'


def test_shipped_skills_are_exactly_these():
    names = sorted(os.path.basename(os.path.dirname(p)) for p in _ALL_SKILLS)
    assert names == [
        "audit", "bootstrap", "consolidate", "doctor", "init", "new", "promote", "recall",
        "remove", "resolve", "why",
    ]


def test_every_skill_carries_the_plugin_data_guard():
    for path in _ALL_SKILLS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert _GUARD in text, (
            f"{os.path.relpath(path)} lacks the shared CLAUDE_PLUGIN_DATA preflight "
            "guard (ONB-7) — an unset var makes its code blocks expand to root paths"
        )


def test_guard_appears_before_any_plugin_data_expansion():
    """The guard must come BEFORE the first code line that expands the variable."""
    for path in _ALL_SKILLS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        guard_pos = text.find(_GUARD)
        # First expansion that is NOT the guard itself / prose backticks: look for the
        # brace-expansion form used in runnable blocks.
        for m in re.finditer(r"\$\{CLAUDE_PLUGIN_DATA\}", text):
            assert guard_pos != -1 and guard_pos < m.start(), (
                f"{os.path.relpath(path)}: ${{CLAUDE_PLUGIN_DATA}} expanded at "
                f"offset {m.start()} before the ONB-7 guard at {guard_pos}"
            )
            break  # only the first expansion matters


# --------------------------------------------------------------------------- #
# DOC-3: frontmatter description budget + shape
# --------------------------------------------------------------------------- #
_DESCRIPTION_BUDGET = 500  # chars — well under the 1024 truncation/validation limit


def _description_of(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^description: (.*)$", text, re.M)
    assert m, f"{os.path.relpath(path)}: no frontmatter description"
    return m.group(1)


def test_skill_descriptions_fit_the_budget():
    for path in _ALL_SKILLS:
        desc = _description_of(path)
        assert len(desc) <= _DESCRIPTION_BUDGET, (
            f"{os.path.relpath(path)}: description is {len(desc)} chars "
            f"(budget {_DESCRIPTION_BUDGET}) — trigger phrases get buried past the "
            "truncation point; move flags/caveats to the body"
        )


def test_skill_descriptions_carry_their_trigger_phrase():
    """Consistency: every description names its own /hippo:<name> invocation."""
    for path in _ALL_SKILLS:
        name = os.path.basename(os.path.dirname(path))
        assert f"/hippo:{name}" in _description_of(path), os.path.relpath(path)


# --------------------------------------------------------------------------- #
# OSP-6: one canonical PY-resolution snippet (hooks/_resolve_py.sh), reused
# everywhere instead of eight hand-rolled copies (two hooks, bin/hippo, five
# skills). This pins the shape, not just presence, so a future surface can't
# silently reintroduce its own inline PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"
# dance.
# --------------------------------------------------------------------------- #
_HOOKS_DIR = os.path.join(_PLUGIN_DIR, "hooks")
_BIN_HIPPO = os.path.join(_PLUGIN_DIR, "bin", "hippo")
_BOTH_HOOKS = [
    os.path.join(_HOOKS_DIR, "memory_session_start.sh"),
    os.path.join(_HOOKS_DIR, "memory_user_prompt.sh"),
]
_ALL_PY_RESOLVING_SURFACES = _BOTH_HOOKS + [_BIN_HIPPO] + _ALL_SKILLS

# A hand-rolled duplicate of the resolver's own fallback dance — the exact
# drift-prone pattern OSP-6 eliminates. Any surface OTHER than _resolve_py.sh
# itself containing this is a regression back to eight independent copies.
_INLINE_DUPLICATE_RE = re.compile(
    r'PY="\$\{CLAUDE_PLUGIN_DATA:-\}/venv/bin/python"'
)
# How a surface "routes through" the canonical resolver: sourcing the shared
# file (a plain `source`/`.` line naming _resolve_py.sh).
_SOURCE_LINE_RE = re.compile(r'(?:^|\s)\.\s+["\'$][^\n]*_resolve_py\.sh')


def test_resolve_py_snippet_exists_and_defines_the_canonical_function():
    assert os.path.isfile(_RESOLVE_PY_SH), "plugin/hooks/_resolve_py.sh is missing"
    with open(_RESOLVE_PY_SH, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "hippo_resolve_py()" in text
    assert 'PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"' in text
    assert 'PY="python3"' in text
    assert "export PYTHONPATH=" in text


def test_no_surface_hand_rolls_its_own_py_resolution():
    """No hook/bin/skill outside _resolve_py.sh itself duplicates the fallback dance."""
    for path in _ALL_PY_RESOLVING_SURFACES:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert not _INLINE_DUPLICATE_RE.search(text), (
            f"{os.path.relpath(path, _PLUGIN_DIR)} hand-rolls its own PY-resolution "
            "dance instead of sourcing the canonical hooks/_resolve_py.sh (OSP-6)"
        )


def test_both_hooks_source_the_canonical_resolver():
    for path in _BOTH_HOOKS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert _SOURCE_LINE_RE.search(text), (
            f"{os.path.relpath(path, _PLUGIN_DIR)} does not source _resolve_py.sh"
        )
        assert "hippo_resolve_py" in text


def test_bin_hippo_sources_the_canonical_resolver():
    with open(_BIN_HIPPO, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert _SOURCE_LINE_RE.search(text), "bin/hippo does not source _resolve_py.sh"
    assert "hippo_resolve_py" in text


def test_every_skill_routes_through_the_canonical_resolver():
    """Every SKILL.md either sources _resolve_py.sh directly, or (like bootstrap,
    which builds the venv itself rather than choosing between venv/bare-python3)
    never performs a PY-selection fallback at all."""
    for path in _ALL_SKILLS:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        name = os.path.basename(os.path.dirname(path))
        if name == "bootstrap":
            # Bootstrap constructs the venv; it never chooses between venv/bare
            # python3, so it has nothing to route through the resolver.
            continue
        assert _SOURCE_LINE_RE.search(text) or "hippo_resolve_py" in text, (
            f"{os.path.relpath(path, _PLUGIN_DIR)} does not route PY resolution "
            "through hooks/_resolve_py.sh (OSP-6)"
        )


# --------------------------------------------------------------------------- #
# COR-7: init's corpus-format seeding snippet stays in lockstep with the constant.
# Steps 1-2 of init run BEFORE $PY is resolved, so the SKILL.md snippet writes a
# LITERAL number instead of calling memory.provenance.write_corpus_format — this
# parity pin (the same cross-language pattern as test_fastembed_cache_path.py)
# is what makes a CORPUS_FORMAT_VERSION bump a one-constant change: bump the
# constant and this test names the exact init surface that must follow.
# --------------------------------------------------------------------------- #
_INIT_SKILL = os.path.join(_SKILLS_DIR, "init", "SKILL.md")


def test_init_skill_seeds_the_canonical_corpus_format():
    from memory.provenance import CORPUS_FORMAT_VERSION

    with open(_INIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert ".claude/memory/.format" in text, (
        "init SKILL.md no longer seeds the corpus format marker (COR-7 step 2b)"
    )
    literals = re.findall(r'\{"corpus_format":\s*(\d+)\}', text)
    assert literals, "init SKILL.md carries no {\"corpus_format\": N} literal to pin"
    assert all(int(v) == CORPUS_FORMAT_VERSION for v in literals), (
        f"init SKILL.md seeds corpus_format {sorted(set(literals))}, but "
        f"memory.provenance.CORPUS_FORMAT_VERSION is {CORPUS_FORMAT_VERSION} — "
        "update the SKILL.md snippet in the same change that bumps the constant"
    )


# --------------------------------------------------------------------------- #
# DOC-6: init seeds CONVENTIONS.md into every corpus, idempotently, on BOTH the
# fresh-project path and the existing-corpus (teammate-clone/worktree/second-machine)
# path — unlike steps 1-2b, which are fresh-corpus-only.
# --------------------------------------------------------------------------- #
def test_init_skill_seeds_conventions_md_idempotently():
    with open(_INIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "${CLAUDE_PLUGIN_ROOT}/assets/CONVENTIONS.md" in text, (
        "init SKILL.md no longer seeds CONVENTIONS.md from the plugin bundle (DOC-6)"
    )
    assert ".claude/memory/CONVENTIONS.md" in text, (
        "init SKILL.md no longer copies CONVENTIONS.md into the corpus (DOC-6)"
    )
    assert "[ -f .claude/memory/CONVENTIONS.md ]" in text, (
        "init SKILL.md's CONVENTIONS.md seeding step must skip (not overwrite) an "
        "already-present copy — the same idempotent seeding-step style every other "
        "init step uses"
    )


def test_init_skill_conventions_step_runs_on_the_existing_corpus_path():
    """Step 2c must NOT be grouped with the fresh-corpus-only 1-2b range the preflight
    branch skips — a corpus created before CONVENTIONS.md existed still needs it."""
    with open(_INIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "steps 2c-5" in text or "step 2c plus the machine-local setup" in text, (
        "init SKILL.md's existing-corpus preflight path no longer names step 2c as "
        "one of the steps that still runs (DOC-6 must survive teammate-clone/"
        "worktree/second-machine re-runs, not just a fresh project)"
    )


# --------------------------------------------------------------------------- #
# LIF-2: the new skill must ROUTE the write-time duplicate decision to the agent —
# all four Mem0-style branches named, supersede wired to the shipped per-item
# primitive (reconsolidate's --superseded-by, which writes the GRA-4 edge).
# --------------------------------------------------------------------------- #
_NEW_SKILL = os.path.join(_SKILLS_DIR, "new", "SKILL.md")


def test_new_skill_routes_the_duplicate_decision():
    with open(_NEW_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    for branch in ("**add**", "**update-existing**", "**supersede**", "**skip**"):
        assert branch in text, f"new SKILL.md lost the {branch} branch of the LIF-2 decision flow"
    assert "--superseded-by" in text, (
        "new SKILL.md's supersede branch must route through the per-item "
        "reconsolidate --superseded-by primitive (GRA-4 edge on the successor)"
    )
    assert "never blocked" in text.lower(), (
        "new SKILL.md must state creation is never blocked (warn-only, agent-gated)"
    )


# --------------------------------------------------------------------------- #
# GRW-3 + GRW-8: the audit skill's merge tier + contradiction fork ride ONE
# neighbor sweep, with the load-bearing guards pinned as text — the correct
# similarity scale (never recall()'s fused scores against a cosine threshold),
# the both-directions rule, the archive guard as the structural no-dangling
# enforcer, and the reworded-duplicate-is-not-a-contradiction mislabel guard.
# --------------------------------------------------------------------------- #
_AUDIT_SKILL = os.path.join(_SKILLS_DIR, "audit", "SKILL.md")


def test_audit_skill_merge_tier_uses_the_calibrated_dup_scale():
    with open(_AUDIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "committed_duplicate_neighbors" in text, (
        "the merge tier must score pairs with the write-time dup mechanic (calibrated "
        "cosine/BM25 scale), not recall()'s RRF-fused scores"
    )
    assert "BOTH directions" in text, "the both-directions rule is the false-pair filter"
    assert "invalid_after_map" in text, (
        "a demoted (invalid_after-bearing) side is supersede territory, never a merge"
    )
    assert "merge_candidates" in text, "Phase 1 must emit the merge_candidates JSON key"


def test_audit_skill_merge_recipe_is_per_item_with_the_inbound_guard():
    with open(_AUDIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "NO body-rewrite primitive" in text, (
        "the merge recipe is per-item agent edits — no body-rewrite primitive exists or "
        "may be simulated"
    )
    assert "inbound guard REFUSES" in text, (
        "archive's GRA-5 refusal is the structural proof the inbound rewrite happened"
    )
    assert "--superseded-by <survivor>" in text, (
        "the demote-in-place ending routes through the shipped supersede flow"
    )


def test_audit_skill_contradiction_fork_carries_the_mislabel_guard():
    with open(_AUDIT_SKILL, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "three-way fork" in text, "GRW-8's (a)/(b)/(c) classifier fork must be spelled out"
    assert "reworded" in text and "NOT a contradiction" in text, (
        "the mislabel guard — a reworded duplicate is not a contradiction — is the AC"
    )
    assert "Contradiction candidates" in text, "the deterministic report block must exist"
    assert '"contradicts"' in text, (
        "the (b) arm proposes links.add_typed_relation(..., 'contradicts', ...) per item"
    )
    assert "GOV-1" in text and "/hippo:resolve" in text, (
        "accepted contradicts edges drain through the GOV-1 inbox"
    )
    assert "refresh_index" in text, (
        "add_typed_relation writes frontmatter but not links.json — the apply arm must refresh"
    )


# =============================================================================================
# QUA-8: the full skills-contract suite.
#
# (A) Extract every fenced code block from every SKILL.md.
# (B) python: every block must compile; every memory-package reference it makes — an import,
#     an attribute chain rooted in one, or a `-m memory.<mod>` module target — must resolve
#     against the REAL installed package (importlib + getattr, never a hand-maintained symbol
#     list); every keyword argument on a resolved call must bind to that callable's real
#     `inspect.signature` (a removed/renamed kwarg fails the suite, same as a removed symbol).
# (C) bash: every block syntax-checks via `bash -n` (shellcheck the BINARY is not available in
#     CI's hermetic lanes — this deliberately stays a syntax check, never a shellcheck lint).
# (D) paths: every ${CLAUDE_PLUGIN_ROOT}-relative path must exist under plugin/; every
#     project-local .claude/... path that looks like an index/telemetry-dir reference must be
#     the one canonical spelling (no compat shims — a guiding invariant of this project).
#
# None of the six skills fence a ```python block directly — every runnable snippet is python
# invoked FROM bash (a `-c "..."` one-liner or a `"$PY" - <<'PYEOF'` stdin heredoc), so (B)
# extracts python OUT of the bash fences rather than looking for a language tag that doesn't
# exist. Extraction covers exactly the three python-invocation shapes the six skills actually
# use (see _PY_INTERPRETER below) — an exotic new shape is a job for a human, not a silent
# no-op: see test_no_silent_skips_in_the_python_contract.
# =============================================================================================

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)\n[ \t]*```", re.DOTALL)


def _fenced_blocks(path: str):
    """(lang, body) for every fenced block in a SKILL.md, in document order. ``lang`` is ""
    for an untagged fence (the illustrative example-output blocks in new/SKILL.md)."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return _FENCE_RE.findall(text)


def _bash_blocks(path: str):
    """Only blocks explicitly tagged bash/sh — the ones committed to BE real shell. Untagged
    fences (CLI-usage synopses using `<required>`/`{a|b}`/`[optional]` docs notation, or
    illustrative example output) are never bash and must not be syntax-checked as such."""
    return [body for lang, body in _fenced_blocks(path) if lang in ("bash", "sh")]


# The three python-invocation shapes the six skills actually use, all launched via the
# canonical $PY (OSP-6) or a bare python3 (bootstrap's pre-provision PYVER probe).
_PY_INTERPRETER = r'(?:"\$PY"|"\$\{CLAUDE_PLUGIN_DATA\}/venv/bin/python"|\bpython3)'
_HEREDOC_PY_RE = re.compile(_PY_INTERPRETER + r"\s+-\s+<<-?'(\w+)'\n(.*?)\n\1\b", re.DOTALL)
# A double-quoted `-c "..."` shell string, possibly spanning several source lines via
# backslash-newline continuation (POSIX: a literal backslash-newline inside double quotes is
# spliced away, same as unquoted) — `\\.` (with DOTALL) matches that continuation as one of
# the "escaped char" alternatives, so the join below just has to strip it back out.
_DQ_PYC_RE = re.compile(_PY_INTERPRETER + r'\s+-c\s+\\\n\s*"((?:\\.|[^"\\])*)"', re.DOTALL)
_SQ_PYC_RE = re.compile(_PY_INTERPRETER + r"\s+-c\s+'([^']*)'")


def _python_snippets(skill_path: str):
    """[(origin, source), ...] — every embedded python snippet in one SKILL.md's bash blocks."""
    label = os.path.relpath(skill_path, _PLUGIN_DIR)
    out = []
    for code in _bash_blocks(skill_path):
        for delim, body in _HEREDOC_PY_RE.findall(code):
            out.append((f"{label} (heredoc <<{delim}>>)", body))
        for raw in _DQ_PYC_RE.findall(code):
            out.append((f'{label} (-c "...")', raw.replace("\\\n", "")))
        for raw in _SQ_PYC_RE.findall(code):
            out.append((f"{label} (-c '...')", raw))
    return out


_ALL_PY_SNIPPETS = [s for _path in _ALL_SKILLS for s in _python_snippets(_path)]


def _resolve_dotted_chain(node: ast.AST):
    """A pure Name/Attribute chain (e.g. `trust.gate_repo_root`) as ['trust', 'gate_repo_root'];
    None if the chain includes anything else (a call, subscript, f-string, ...) — those aren't
    a static package reference and are out of (B)'s scope by construction, not by omission."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None


def _check_python_snippet(origin: str, source: str):
    """Resolve every memory-package reference in one already-parsed-clean python snippet.

    Returns (resolution_failures, signature_failures, resolved_calls, skips) — four lists of
    (origin, message) pairs (resolved_calls is (qualname, callable) instead). Never raises on
    a CONTRACT violation (that's the caller's job, via assert, so pytest reports every offender
    in one run instead of stopping at the first) — only a genuine bug in this checker itself
    would raise here.
    """
    tree = ast.parse(source)  # caller already knows this compiles (test_..._compile ran first)
    aliases: dict[str, object] = {}  # local name -> the REAL resolved module/function/class
    resolution_failures = []
    signature_failures = []
    skips = []
    resolved_calls = []

    # Pass 1: imports. Mirrors Python's own `from X import Y` resolution order (attribute on
    # the imported module first, then a submodule import) so `from memory import trust` and
    # `from memory.trust import mark_trusted` both resolve the same honest way.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                continue
            if not (node.module == "memory" or node.module.startswith("memory.")):
                continue
            for alias in node.names:
                if alias.name == "*":
                    skips.append((origin, f"`from {node.module} import *` — wildcard import: "
                                           "no static symbol list to check"))
                    continue
                local = alias.asname or alias.name
                try:
                    mod = importlib.import_module(node.module)
                    if hasattr(mod, alias.name):
                        aliases[local] = getattr(mod, alias.name)
                    else:
                        aliases[local] = importlib.import_module(f"{node.module}.{alias.name}")
                except (ImportError, AttributeError) as exc:
                    resolution_failures.append((
                        origin,
                        f"`from {node.module} import {alias.name}` does not resolve against "
                        f"the real memory package ({exc}) — renamed/removed symbol",
                    ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "memory" or alias.name.startswith("memory."):
                    local = alias.asname or alias.name.split(".")[0]
                    try:
                        aliases[local] = importlib.import_module(alias.name)
                    except ImportError as exc:
                        resolution_failures.append(
                            (origin, f"`import {alias.name}` does not resolve ({exc})")
                        )

    # Pass 2: every attribute chain rooted at a tracked import — e.g. `eval_recall.evaluate`,
    # where `eval_recall` was bound above — must resolve to a real attribute, walking through
    # intermediate submodule imports as needed. A chain NOT rooted at a tracked import (e.g.
    # `graph.isolates()`, where `graph` is a local var holding a LinkGraph instance, not an
    # import) is out of scope: this is a static package-symbol check, not a type checker.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _resolve_dotted_chain(node)
        if not chain or chain[0] not in aliases:
            continue
        obj = aliases[chain[0]]
        resolved_path = chain[0]
        for part in chain[1:]:
            resolved_path += "." + part
            if hasattr(obj, part):
                obj = getattr(obj, part)
            elif inspect.ismodule(obj):
                try:
                    obj = importlib.import_module(f"{obj.__name__}.{part}")
                except ImportError:
                    resolution_failures.append(
                        (origin, f"`{resolved_path}` does not exist — renamed/removed symbol")
                    )
                    break
            else:
                resolution_failures.append(
                    (origin, f"`{resolved_path}` does not exist — renamed/removed symbol")
                )
                break

    # Pass 3: calls to a resolved callable — bind keyword arguments against the REAL signature.
    # This is the roadmap's literal AC: `eval_recall.evaluate(repo_root=..., hard_set_path=...,
    # relevance_set_path=...)` in audit/SKILL.md resolves `eval_recall` via pass 1, resolves
    # `.evaluate` via pass 2, and lands here — a renamed/removed kwarg on evaluate() fails now.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        resolved = None
        qualname = None
        if isinstance(func, ast.Name) and func.id in aliases:
            resolved, qualname = aliases[func.id], func.id
        elif isinstance(func, ast.Attribute):
            chain = _resolve_dotted_chain(func)
            if chain and chain[0] in aliases:
                obj, ok = aliases[chain[0]], True
                for part in chain[1:]:
                    if hasattr(obj, part):
                        obj = getattr(obj, part)
                    else:
                        ok = False
                        break
                if ok:
                    resolved, qualname = obj, ".".join(chain)
        if resolved is None or not callable(resolved):
            continue  # not a memory-package call (builtin, stdlib, local helper, ...)

        starred = [kw for kw in node.keywords if kw.arg is None]
        named = [kw.arg for kw in node.keywords if kw.arg is not None]
        if starred:
            skips.append((origin, f"call to `{qualname}` uses a **kwargs splat — keyword "
                                   "names not statically checkable"))
        if named:
            try:
                sig = inspect.signature(resolved)
            except (TypeError, ValueError) as exc:
                skips.append((origin, f"no signature available for `{qualname}` ({exc})"))
            else:
                accepted = set(sig.parameters)
                has_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                for kw in named:
                    if kw not in accepted and not has_var_kw:
                        signature_failures.append((
                            origin,
                            f"`{qualname}(..., {kw}=...)` — no such parameter on "
                            f"{qualname}{sig} (renamed/removed keyword argument)",
                        ))
        if qualname:
            resolved_calls.append((qualname, resolved))

    return resolution_failures, signature_failures, resolved_calls, skips


def test_embedded_python_snippets_compile():
    """(A)+(B): every python snippet extracted from a bash block must be valid python."""
    failures = []
    for origin, source in _ALL_PY_SNIPPETS:
        try:
            ast.parse(source)
        except SyntaxError as exc:
            failures.append(f"{origin}: does not compile — {exc}")
    assert not failures, "\n".join(failures)


def test_memory_package_references_resolve():
    """(B): every `from memory.X import Y` / attribute-chain reference resolves against the
    REAL memory package via importlib + getattr — a renamed/removed symbol fails here."""
    failures = []
    for origin, source in _ALL_PY_SNIPPETS:
        resolution_failures, _sig, _calls, _skips = _check_python_snippet(origin, source)
        failures.extend(f"{o}: {m}" for o, m in resolution_failures)
    assert not failures, "\n".join(failures)


def test_memory_package_calls_bind_real_signatures():
    """(B): every keyword argument on a resolved memory-package call binds to that callable's
    real `inspect.signature` — a renamed/removed kwarg (e.g. evaluate()'s own params) fails."""
    failures = []
    for origin, source in _ALL_PY_SNIPPETS:
        _res, signature_failures, _calls, _skips = _check_python_snippet(origin, source)
        failures.extend(f"{o}: {m}" for o, m in signature_failures)
    assert not failures, "\n".join(failures)


def test_no_silent_skips_in_the_python_contract():
    """(B) is deliberately pragmatic about exotic dynamic constructs (a wildcard import, a
    `**kwargs` splat, a callable with no introspectable signature) rather than brittle — but
    "pragmatic" must never mean "silent". Today's six skills use only the direct
    import/attribute/call pattern the resolver fully checks, so this list is empty; a skip
    appearing here means a skill grew a construct outside that pattern and needs a human look
    (extend the resolver, or replace this assertion with an explicit allowlist naming exactly
    which skip is accepted and why)."""
    skips = []
    for origin, source in _ALL_PY_SNIPPETS:
        _res, _sig, _calls, snippet_skips = _check_python_snippet(origin, source)
        skips.extend(f"{o}: {m}" for o, m in snippet_skips)
    assert not skips, "\n".join(skips)


def test_eval_recall_evaluate_reference_is_covered_by_the_contract():
    """The roadmap's literal AC, pinned directly: renaming `evaluate()` must fail this suite.
    Prove the audit skill's `eval_recall.evaluate(...)` call is actually among what the
    resolver sees and validates — a regression here means the AC target silently fell out of
    this contract's coverage (e.g. the skill switched to a shape the extractor doesn't parse)."""
    resolved_qualnames = set()
    for origin, source in _ALL_PY_SNIPPETS:
        _res, _sig, calls, _skips = _check_python_snippet(origin, source)
        resolved_qualnames.update(qualname for qualname, _callable in calls)
    assert "eval_recall.evaluate" in resolved_qualnames, (
        "no extracted snippet resolved a call to eval_recall.evaluate — the QUA-8 contract "
        "no longer covers its own pinned acceptance criterion"
    )


def test_bash_blocks_pass_shellcheck_syntax():
    """(C): `bash -n` syntax-checks every bash/sh-tagged block verbatim, as it ships. The
    `shellcheck` BINARY is not available in CI's hermetic lanes — this stays a syntax check
    only, never a shellcheck lint (see plugin/hooks/*.sh for the real shellcheck pass, which
    runs where the binary IS available)."""
    failures = []
    for path in _ALL_SKILLS:
        label = os.path.relpath(path, _PLUGIN_DIR)
        for i, code in enumerate(_bash_blocks(path), start=1):
            result = subprocess.run(
                ["bash", "-n", "-c", code], capture_output=True, text=True,
            )
            if result.returncode != 0:
                failures.append(f"{label} bash block #{i}: {result.stderr.strip()}")
    assert not failures, "\n".join(failures)


# `-m memory.<mod>` module references appear in bash text, not necessarily inside a
# bash-tagged fence (new/SKILL.md's CLI-usage synopsis is deliberately untagged — see
# _bash_blocks) — scan every fenced block's raw body, any language, for this one.
_DASH_M_RE = re.compile(r"-m\s+memory\.([A-Za-z_][A-Za-z0-9_.]*)")


def test_dash_m_module_references_resolve():
    """(B): every `python -m memory.<mod>` target names a real, importable memory submodule."""
    failures = []
    for path in _ALL_SKILLS:
        label = os.path.relpath(path, _PLUGIN_DIR)
        for _lang, code in _fenced_blocks(path):
            for mod_suffix in sorted(set(_DASH_M_RE.findall(code))):
                try:
                    importlib.import_module(f"memory.{mod_suffix}")
                except ImportError as exc:
                    failures.append(f"{label}: `-m memory.{mod_suffix}` does not resolve ({exc})")
    assert not failures, "\n".join(failures)


# (D): every ${CLAUDE_PLUGIN_ROOT}-relative path referenced anywhere must ship under plugin/.
_PLUGIN_ROOT_PATH_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")


def test_claude_plugin_root_paths_exist_under_plugin():
    failures = []
    for path in _ALL_SKILLS:
        label = os.path.relpath(path, _PLUGIN_DIR)
        for _lang, code in _fenced_blocks(path):
            for rel in sorted(set(_PLUGIN_ROOT_PATH_RE.findall(code))):
                if not os.path.exists(os.path.join(_PLUGIN_DIR, rel)):
                    failures.append(f"{label}: ${{CLAUDE_PLUGIN_ROOT}}/{rel} does not exist under plugin/")
    assert not failures, "\n".join(failures)


# (D): a project-local .claude/... path that LOOKS like it's naming the index or telemetry
# dir must spell it the one canonical way — no compat shim, no near-miss (missing dot, a
# hyphen/underscore swap, ...). Derived from the real functions rather than hand-copied, so
# this test can never itself drift from the constant it's pinning.
_CLAUDE_PATH_RE = re.compile(r"(.{0,2})(\.claude/[A-Za-z0-9_.\-/]+)")
_INDEX_HINT_RE = re.compile(r"memory[-_]?index", re.IGNORECASE)
_TELEMETRY_HINT_RE = re.compile(r"memory[-_]?telemetry", re.IGNORECASE)


def test_project_local_claude_paths_use_the_canonical_trio(monkeypatch):
    from memory.build_index import default_index_dir
    from memory.telemetry import default_telemetry_dir

    monkeypatch.delenv("HIPPO_INDEX_DIR", raising=False)
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    anchor = os.sep + "__hippo_qua8_contract_anchor__"
    canonical_index = os.path.relpath(default_index_dir(f"{anchor}/.claude/memory"), anchor)
    canonical_telemetry = os.path.relpath(default_telemetry_dir(f"{anchor}/.claude/memory"), anchor)

    failures = []
    for path in _ALL_SKILLS:
        label = os.path.relpath(path, _PLUGIN_DIR)
        for _lang, code in _fenced_blocks(path):
            for pre, token in _CLAUDE_PATH_RE.findall(code):
                if pre.endswith("~/") or pre.endswith("~"):
                    continue  # home-relative (e.g. ~/.claude/hippo-data) — not project-local
                if _INDEX_HINT_RE.search(token):
                    if not (token == canonical_index or token.startswith(canonical_index + "/")):
                        failures.append(
                            f"{label}: `{token}` looks like an index-dir reference but isn't "
                            f"the canonical `{canonical_index}`"
                        )
                elif _TELEMETRY_HINT_RE.search(token):
                    if not (token == canonical_telemetry or token.startswith(canonical_telemetry + "/")):
                        failures.append(
                            f"{label}: `{token}` looks like a telemetry-dir reference but "
                            f"isn't the canonical `{canonical_telemetry}`"
                        )
                # A bare `.claude/memory[...]` reference needs no further check here — that
                # IS the canonical (git-tracked, no leading dot) corpus dir already.
    assert not failures, "\n".join(failures)
