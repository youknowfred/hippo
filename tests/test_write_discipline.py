"""INV-2: the write-discipline lint — corpus writes go through the shared primitives,
MECHANICALLY.

Two defect classes from the 2026-07-16 QA sweep, each fixed by hand there and held
fixed here by AST:

  - COR-18: eleven plain ``open(..., "w")`` corpus/registry writers existed AFTER
    ``atomic.write_text_atomic`` shipped — a torn write leaves partial bytes where a
    whole document used to be. The lint fails on ANY write-mode ``open()`` call site
    in ``plugin/memory/`` outside ``atomic.py`` unless the site is in the explicit,
    per-site, commented allowlist below. Fail-closed: a new writer must either use
    the atomic primitive or argue its exemption in one reviewable line here.
  - COR-14: the sixth and seventh hand-copied frontmatter walks (dream_generate's
    ``_set_confidence``/``_set_cited_paths``) probed for ``metadata:`` and inserted
    keys at a hard-coded indent — corrupting any 4-space-indented metadata block,
    silently. The lint flags any function outside ``provenance.py`` that probes for
    the metadata block AND mutates frontmatter lines without calling the COR-9
    primitives (``insert_frontmatter_keys`` / ``strip_frontmatter_keys`` /
    ``_frontmatter_damage``).

The allowlists are the design, not a workaround: a false positive costs one commented
entry; a silent pass is what COR-14/COR-18 already cost. A self-test at the bottom
runs the checker over the ORIGINAL COR-14 walk (verbatim shape) and a fixture torn
writer, proving the heuristics actually catch the class they exist for.
"""

from __future__ import annotations

import ast
import os

from memory import atomic as _atomic  # locates plugin/memory without hardcoding paths

_MEMORY_PKG = os.path.dirname(os.path.abspath(_atomic.__file__))

# ``atomic.py`` is the primitive's own home (its fdopen/replace IS the discipline);
# ``_vendor`` is upstream code hippo does not lint.
_EXEMPT_MODULES = {"atomic"}

_REMEDY = (
    "route the write through atomic.write_text_atomic / write_json_atomic (COR-18: a "
    "plain truncating open('w') torn by a crash — or read mid-write — leaves partial "
    "bytes where a whole document used to be), or add a justified per-site entry to "
    "WRITE_OPEN_ALLOWLIST in tests/test_write_discipline.py"
)

# --------------------------------------------------------------------------- #
# The allowlist: every write-mode open() plugin/memory is ALLOWED to keep.
# Keyed (module, enclosing function). Each entry argues its exemption — an entry
# whose justification stops holding is a one-line review, not a silent pass.
# COR-18's fixed corpus/registry writers are deliberately NOT here: they no longer
# open("w") at all, and removing any of them from this list keeps the suite green.
# --------------------------------------------------------------------------- #
WRITE_OPEN_ALLOWLIST = {
    # -- sites that already carry their own COR-17 unique-tmp + os.replace: the open()
    #    targets the TMP file; the swap is atomic at the real path --
    ("build_index", "build_index"): "manifest write: own unique-tmp + os.replace (COR-12/COR-17)",
    ("links", "write_links_cache"): "own unique-tmp + os.replace (COR-12/COR-17)",
    ("staleness", "write_stale_cache"): "own unique-tmp + os.replace (COR-17)",
    ("outcome", "write_outcome_cache"): "own unique-tmp + os.replace (COR-17)",
    ("rules_plane", "refresh_rules_cache"): "own unique-tmp + os.replace (COR-17)",
    ("telemetry", "write_user_usage_summary"): "own unique-tmp + os.replace (committed .usage summary)",
    ("telemetry", "_rotate_if_needed"): "own unique-tmp + os.replace (COR-17)",
    # -- append-mode ledgers/journals (gitignored, derived): append never truncates,
    #    and a torn TAIL line is skipped by every jsonl reader --
    ("telemetry", "log_recall_event"): "append-only gitignored ledger; torn tail skipped",
    ("telemetry", "log_injection_producers"): "append-only gitignored cost ledger (MSR-6); torn tail skipped",
    ("telemetry", "log_threat_findings"): "append-only gitignored Tier-B threat ledger (SEN-2); torn tail skipped",
    ("eval_recall", "append_run_ledger"): "append-only gitignored run ledger (MSR-1); torn tail skipped",
    ("telemetry", "log_episode"): "append-only gitignored ledger; torn tail skipped",
    ("telemetry", "log_decision"): "append-only gitignored ledger; torn tail skipped",
    ("telemetry", "log_outcome"): "append-only gitignored ledger; torn tail skipped",
    ("telemetry", "record_reconsolidation_outcome"): "append-only gitignored ledger; torn tail skipped",
    ("archive", "_journal_untracked_move"): "append-only gitignored journal; torn tail skipped",
    ("dream", "_append_contradiction_rows"): "append-only gitignored ledger; torn tail skipped",
    ("dream", "run_apply_pass"): "append-only gitignored dream ledger; corpus edge stamps stay the truth",
    ("dream", "undo_edges"): "append-only gitignored dream ledger; torn tail skipped",
    ("dream_generate", "stage_generated"): "append-only gitignored ledger; torn tail skipped",
    ("dream_generate", "sweep_drafts"): "append-only gitignored ledger; torn tail skipped",
    ("dream_generate", "archive_draft"): "append-only gitignored ledger; torn tail skipped",
    # -- whole-file rewrites of DERIVED, per-clone, rebuildable state: a torn write
    #    costs a recomputation or one re-nag, never corpus/registry truth --
    ("telemetry", "mark_session"): "session-id token; worst case a re-minted id",
    ("telemetry", "_update_usage_aggregates"): "gitignored aggregate cache; recomputed from the ledger",
    ("dream", "write_boost_ledger"): "derived replay-boost cache; rebuilt by the next pass",
    ("dream", "write_candidate_ledger"): "derived candidate cache; rebuilt by the next pass",
    ("dream", "_undo_one_edge"): "derived dream ledger rewrite; corpus edge stamps stay the truth",
    ("dream_generate", "write_proposals_ledger"): "derived proposals ledger; rebuilt by the next pass",
    ("dream_generate", "freeze_abstention_backlog"): "derived frozen-backlog snapshot",
    ("deparasite", "write_report"): "derived report artifact in the telemetry dir",
    ("session_start", "_periodic_nudge_should_fire"): "nudge cadence counter; worst case re-nags once",
    ("session_start", "_persist"): "per-clone GOV-4 watermark; worst case re-surfaces once",
    ("resolve_view", "mark_not_conflicting"): "per-clone dismiss ledger; rebuildable by re-dismissing",
    ("capture", "write_session_capture"): "gitignored pending seed, unique per-session filename",
    ("capture", "snooze_queue"): "queue-state marker; worst case the nudge re-fires",
    ("provenance", "ensure_self_ignoring_dir"): "derived-dir self-ignore marker; create-once, single '*' line",
    ("dream_eval", "_seed_soak"): "eval-harness fixture generation (bench substrate)",
    ("dream_eval", "_body"): "eval-harness fixture generation (bench substrate)",
    # -- machine-local provisioning state under CLAUDE_PLUGIN_DATA: a failure means
    #    re-bootstrap, never corpus/registry loss --
    ("bootstrap", "_spawn"): "bootstrap log append",
    ("bootstrap", "start"): "bootstrap log truncate-on-start",
    ("bootstrap", "_warm_models"): "model-preset marker",
    ("bootstrap", "_write_sentinel"): "bootstrap sentinel; a torn sentinel re-bootstraps",
    ("bootstrap", "_restamp_plugin_version"): "sentinel version restamp",
    # -- mode 'x' create-new: never truncates existing truth (O_EXCL); the crash window
    #    strands a PARTIAL NEW file, an acknowledged open question (PR #54) --
    ("new_memory", "write_memory"): "mode 'x' corpus create; partial-on-crash is PR #54's open question",
    ("packs", "pack_install_item"): "mode 'x' corpus create; INT-17's byte-identical adopt owns the crash window",
    # -- writes OUTSIDE the corpus by construction --
    ("packs", "pack_extract"): "dest pack dir outside the corpus; RCH-8 rolls back partials",
    ("packs", "_merge3"): "tempfile scratch for git merge-file",
    ("init_project", "_patch_gitignore"): "append to .gitignore: never truncates; a torn line is visible and inert",
}

# Frontmatter-walk exemptions (COR-14 lint). Empty today — the COR-9 primitives are
# the only sanctioned walk, and every writer routes through them. An entry here needs
# the same one-line argument as above.
FRONTMATTER_WALK_ALLOWLIST: dict = {}

_COR9_PRIMITIVES = {"insert_frontmatter_keys", "strip_frontmatter_keys", "_frontmatter_damage"}


def _memory_module_sources():
    for fname in sorted(os.listdir(_MEMORY_PKG)):
        stem = fname[:-3]
        if not fname.endswith(".py") or stem in _EXEMPT_MODULES:
            continue
        with open(os.path.join(_MEMORY_PKG, fname), encoding="utf-8") as fh:
            yield stem, fh.read()


class _SiteVisitor(ast.NodeVisitor):
    """Walk one module tracking the enclosing function name for every node."""

    def __init__(self):
        self.stack = ["<module>"]
        self.sites = []  # populated by subclasses

    def _qualname(self):
        return self.stack[-1]

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


# --------------------------------------------------------------------------- #
# Lint A — write-mode open()
# --------------------------------------------------------------------------- #
def _mode_of(call: ast.Call):
    """The mode argument of an open()/fdopen() call, or None for default 'r'."""
    if len(call.args) >= 2:
        node = call.args[1]
    else:
        node = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "<dynamic>"  # fail-closed: a computed mode needs an allowlist argument


class _WriteOpenVisitor(_SiteVisitor):
    def visit_Call(self, node):
        is_open = isinstance(node.func, ast.Name) and node.func.id == "open"
        is_fdopen = isinstance(node.func, ast.Attribute) and node.func.attr == "fdopen"
        if is_open or is_fdopen:
            mode = _mode_of(node)
            if mode is not None and (mode == "<dynamic>" or any(c in mode for c in "wax+")):
                self.sites.append((self._qualname(), node.lineno, mode))
        self.generic_visit(node)


def _write_open_sites(src: str):
    v = _WriteOpenVisitor()
    v.visit(ast.parse(src))
    return v.sites


def test_write_mode_opens_are_allowlisted():
    flagged = []
    for stem, src in _memory_module_sources():
        for func, lineno, mode in _write_open_sites(src):
            if (stem, func) not in WRITE_OPEN_ALLOWLIST:
                flagged.append(f"plugin/memory/{stem}.py:{lineno} ({func}, mode={mode!r})")
    assert not flagged, (
        "write-mode open() outside the allowlist — " + _REMEDY + ":\n  " + "\n  ".join(flagged)
    )


def test_write_open_allowlist_carries_no_dead_entries():
    """An allowlist entry whose site was fixed (or renamed) must be pruned — proving
    the fixed sites STAYED fixed is the point of keeping this list minimal."""
    live = set()
    for stem, src in _memory_module_sources():
        live.update((stem, func) for func, _lineno, _mode in _write_open_sites(src))
    dead = sorted(set(WRITE_OPEN_ALLOWLIST) - live)
    assert not dead, f"WRITE_OPEN_ALLOWLIST entries with no matching call site (prune them): {dead}"


# --------------------------------------------------------------------------- #
# Lint B — hand-rolled frontmatter walks (the COR-14 class)
# --------------------------------------------------------------------------- #
def _is_metadata_probe_const(value) -> bool:
    return isinstance(value, str) and "metadata" in value and ":" in value


class _WalkVisitor(_SiteVisitor):
    """Flag functions that probe for the metadata block AND mutate lines in place,
    without touching a COR-9 primitive. Creation-side renderers (append-only builds
    of NEW frontmatter) don't trip it: appends aren't in the mutation set."""

    def __init__(self):
        super().__init__()
        self.per_func: dict = {}

    def _entry(self):
        return self.per_func.setdefault(
            self._qualname(), {"probe": False, "mutate": False, "primitive": False, "line": None}
        )

    def visit_Constant(self, node):
        if _is_metadata_probe_const(node.value):
            e = self._entry()
            e["probe"] = True
            e["line"] = e["line"] or node.lineno
        self.generic_visit(node)

    def visit_Call(self, node):
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name == "insert":
            self._entry()["mutate"] = True
        if name in _COR9_PRIMITIVES:
            self._entry()["primitive"] = True
        self.generic_visit(node)

    def visit_Assign(self, node):
        # Slice-assignment only (``lines[1:close] = …``): a plain index assign is how
        # DICTS are written all over the tree, and the COR-14 corruption lived in the
        # hard-coded-indent INSERT arm, not the replace-an-existing-line arm.
        if any(
            isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Slice)
            for t in node.targets
        ):
            self._entry()["mutate"] = True
        self.generic_visit(node)


def _frontmatter_walk_sites(src: str):
    v = _WalkVisitor()
    v.visit(ast.parse(src))
    return [
        (func, e["line"])
        for func, e in v.per_func.items()
        if e["probe"] and e["mutate"] and not e["primitive"] and func != "<module>"
    ]


def test_no_hand_rolled_frontmatter_walks():
    flagged = []
    for stem, src in _memory_module_sources():
        if stem == "provenance":
            continue  # the COR-9 primitives' own home
        for func, lineno in _frontmatter_walk_sites(src):
            if (stem, func) not in FRONTMATTER_WALK_ALLOWLIST:
                flagged.append(f"plugin/memory/{stem}.py:{lineno} ({func})")
    assert not flagged, (
        "hand-rolled frontmatter walk (the COR-14 class: a metadata: probe + in-place "
        "line mutation with no COR-9 primitive in sight). Route the edit through "
        "provenance.insert_frontmatter_keys / strip_frontmatter_keys and guard it with "
        "provenance._frontmatter_damage — five hand-copies of this walk have shipped "
        "the same indent bug:\n  " + "\n  ".join(flagged)
    )


# --------------------------------------------------------------------------- #
# Self-tests: the heuristics must actually catch the classes they exist for.
# --------------------------------------------------------------------------- #

# The COR-14 shape, verbatim from pre-sweep dream_generate._set_confidence: probe for
# `metadata:`, insert at a hard-coded two-space indent, reassign lines in place.
_COR14_FIXTURE = '''
import re

def _set_confidence(path, value):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    lines = text.split("\\n")
    close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    meta_idx = next(
        (i for i in range(1, close) if re.match(r"^metadata\\s*:\\s*$", lines[i])), None
    )
    if meta_idx is not None:
        lines.insert(meta_idx + 1, f"  confidence: {value}")
    else:
        lines.insert(close, f"confidence: {value}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\\n".join(lines))
'''

_COMPLIANT_FIXTURE = '''
def _set_confidence(path, value):
    from memory.provenance import insert_frontmatter_keys, split_frontmatter
    from memory.atomic import write_text_atomic

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    fm, body = split_frontmatter(text)
    fm = insert_frontmatter_keys(fm, [f"confidence: {value}"])
    write_text_atomic(path, "---\\n" + "\\n".join(fm) + "\\n---\\n" + body)
'''


def test_lint_catches_the_cor14_shape():
    walks = _frontmatter_walk_sites(_COR14_FIXTURE)
    assert [f for f, _ in walks] == ["_set_confidence"], (
        "the frontmatter-walk heuristic no longer catches the exact COR-14 shape — "
        "the lint is dead weight; fix the heuristic before trusting it"
    )
    opens = _write_open_sites(_COR14_FIXTURE)
    assert [(f, m) for f, _l, m in opens] == [("_set_confidence", "w")], (
        "the write-open heuristic no longer catches a plain open('w') writer"
    )


def test_lint_passes_the_compliant_shape():
    assert not _frontmatter_walk_sites(_COMPLIANT_FIXTURE)
    assert not _write_open_sites(_COMPLIANT_FIXTURE)
