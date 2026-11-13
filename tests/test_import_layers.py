"""RWY-5: the foundation → engine edge lint — foundation modules never import the recall
orchestrator, MECHANICALLY.

Two such edges existed (both lazy, in-function imports): ``trust.corpus_consent_sample``
pulled ``recall.inject_description`` and ``staleness.nondrift_old_invalidated`` pulled
``recall._invalidation_state``. Each was a low layer reaching up into the layer that
imports it; they were removed by moving the shared function DOWN (``inject_description``
→ ``trust``, ``_invalidation_state`` + its horizon → ``staleness``), with recall and
recall_graph re-importing the names so every old dotted path still resolves.

The lint parses every foundation module with ``ast`` and walks the WHOLE tree, so a lazy
import inside a function body (the shape both original edges had) counts exactly like a
top-level one. It runs in the default hermetic lane, which is a required CI check.

Scope is direct edges, including lazy ones. A foundation module importing some other
non-foundation module that in turn imports recall is not checked here.
"""

from __future__ import annotations

import ast
import os

from memory import atomic as _atomic  # locates plugin/memory without hardcoding paths

_MEMORY_PKG = os.path.dirname(os.path.abspath(_atomic.__file__))

# The foundation layer: storage primitives, frontmatter/provenance, trust, staleness,
# telemetry, settings and the derived-dir/registry plumbing. These sit BELOW recall:
# recall imports them, so none may import recall back. Prefix families are matched by
# prefix, so a new ``trust_*``/``staleness_*``/``provenance_*``/``telemetry_*`` sibling
# joins the set automatically. To extend the set with another module, first check it is
# clean (no import of any module in RECALL_ORCHESTRATOR, top-level or lazy), then add it
# here. A module that cannot be made clean is not foundation, so leave it out.
FOUNDATION_MODULES = frozenset({"atomic", "fm_access", "settings", "derived_dirs", "registry"})
FOUNDATION_PREFIXES = ("provenance", "trust", "staleness", "telemetry")

# The recall orchestrator: the ``recall`` façade (``recall()`` itself) plus every
# ``recall_*`` sibling that imports the façade or another orchestrator member, i.e. the
# entry points layered ON recall(): ``recall_hook`` (the prompt hook / CLI), ``recall_view``
# (the inspect/why views) and ``recall_warm`` (the warm-path runner, which calls
# ``recall_hook``). Importing any of them drags in the whole engine.
RECALL_ORCHESTRATOR = frozenset({"recall", "recall_hook", "recall_view", "recall_warm"})

# Leaf helpers: ``recall_*`` siblings the façade imports that never import the façade or
# any orchestrator member themselves (rankers, budget, query hygiene, the graph plane,
# salience, tiers, abstention, the diff preview, the BM25-only reasons). They are not forbidden by this lint;
# they are listed so a NEW ``recall_*`` module must be classified on purpose, and
# ``test_orchestrator_classification_matches_the_import_graph`` re-derives the split.
RECALL_LEAF_HELPERS = frozenset(
    {
        "recall_abstain",
        "recall_budget",
        "recall_diff",
        "recall_fallback",
        "recall_graph",
        "recall_issue",
        "recall_query",
        "recall_rank",
        "recall_salience",
        "recall_tiers",
    }
)


def _module_names() -> list:
    return sorted(
        name[:-3]
        for name in os.listdir(_MEMORY_PKG)
        if name.endswith(".py") and os.path.isfile(os.path.join(_MEMORY_PKG, name))
    )


def _is_foundation(module: str) -> bool:
    return module in FOUNDATION_MODULES or module.split("_")[0] in FOUNDATION_PREFIXES


def _imported_memory_modules(tree: ast.AST) -> list:
    """``[(lineno, module)]`` for every in-package module this tree imports, at ANY depth
    (function bodies included). Handles ``from .x import y``, ``from . import x``,
    ``from memory.x import y``, ``from memory import x`` and ``import memory.x``."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 or node.module == "memory" or (
                node.module or ""
            ).startswith("memory."):
                mod = node.module or ""
                if node.level == 0:
                    mod = mod[len("memory") :].lstrip(".")
                if mod:
                    out.append((node.lineno, mod.split(".")[0]))
                else:  # ``from . import x`` / ``from memory import x``: names are modules
                    out.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("memory."):
                    out.append((node.lineno, a.name.split(".")[1]))
    return out


def _edges_of(module: str) -> list:
    path = os.path.join(_MEMORY_PKG, module + ".py")
    with open(path, "r", encoding="utf-8") as fh:
        return _imported_memory_modules(ast.parse(fh.read(), filename=path))


def _orchestrator_violations(modules) -> list:
    bad = []
    for module in modules:
        for lineno, dep in _edges_of(module):
            if dep in RECALL_ORCHESTRATOR:
                bad.append(f"{module}.py:{lineno} imports {dep}")
    return bad


def test_foundation_modules_never_import_the_recall_orchestrator():
    foundation = [m for m in _module_names() if _is_foundation(m)]
    assert foundation, "no foundation modules found — the package path resolved wrong"
    bad = _orchestrator_violations(foundation)
    assert not bad, (
        "foundation -> recall orchestrator edges (a lazy import counts too) — move the "
        "needed function down into the foundation module (or a lower one) and let recall "
        "re-import it:\n  " + "\n  ".join(bad)
    )


def test_declared_foundation_modules_exist():
    """A renamed or deleted foundation module must leave the declared set, so the set
    stays an honest list of what is actually checked."""
    names = set(_module_names())
    missing = sorted(FOUNDATION_MODULES - names)
    assert not missing, f"declared foundation modules not found: {missing}"
    for prefix in FOUNDATION_PREFIXES:
        assert any(n == prefix or n.startswith(prefix + "_") for n in names), prefix


def test_orchestrator_classification_matches_the_import_graph():
    """Every ``recall_*`` module is classified, and the classification is the import
    graph's: the orchestrator is the façade closed over "imports an orchestrator member",
    and no leaf helper imports one."""
    recall_modules = {m for m in _module_names() if m == "recall" or m.startswith("recall_")}
    unclassified = sorted(recall_modules - RECALL_ORCHESTRATOR - RECALL_LEAF_HELPERS)
    assert not unclassified, (
        f"classify these as orchestrator or leaf helper in {os.path.basename(__file__)}: "
        f"{unclassified}"
    )
    assert not RECALL_ORCHESTRATOR & RECALL_LEAF_HELPERS

    derived = {"recall"}
    changed = True
    while changed:
        changed = False
        for m in sorted(recall_modules - derived):
            if any(dep in derived for _, dep in _edges_of(m)):
                derived.add(m)
                changed = True
    assert derived == set(RECALL_ORCHESTRATOR), (
        f"import graph says the orchestrator is {sorted(derived)}, "
        f"declared {sorted(RECALL_ORCHESTRATOR)}"
    )


def test_checker_catches_lazy_and_absolute_imports():
    """Self-test: the walker sees a lazy in-function import (the shape both original
    edges had) and every spelling of an in-package import."""
    src = (
        "import os\n"
        "from .atomic import write_text_atomic\n"
        "def f():\n"
        "    from .recall import inject_description\n"
        "    from . import recall_hook\n"
        "    from memory.recall_view import x\n"
        "    import memory.recall_warm\n"
        "    from memory import recall\n"
    )
    deps = [dep for _, dep in _imported_memory_modules(ast.parse(src))]
    assert deps.count("atomic") == 1
    assert {"recall", "recall_hook", "recall_view", "recall_warm"} <= set(deps)
    assert deps.count("recall") == 2
    assert "os" not in deps
