"""The plugin ships only the runtime: nothing under ``plugin/`` may import ``bench/``.

The shipped plugin is the ``plugin/`` directory alone, so ``bench/`` (the benchmark and
measurement tools that need a repository checkout and its committed fixtures) is absent
on every installed copy. Two halves hold that line:

  - an AST walk over every ``plugin/**/*.py`` fails on any import of ``bench`` — a plain
    ``import``/``from`` statement, a dynamic ``importlib.import_module``/``__import__``
    of a ``bench`` name, or a ``sys.path`` edit whose arguments mention ``bench``;
  - a fresh interpreter with ``bench`` made unimportable still imports ``memory.eval_recall``
    without pulling in its eval-only siblings, and serves the abstention-fixtures tool
    (draft + confirm) end to end.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PLUGIN = os.path.join(_REPO, "plugin")

_DYNAMIC_IMPORTERS = {"import_module", "__import__", "spec_from_file_location", "find_spec"}


def _is_bench_name(name: str) -> bool:
    return name == "bench" or name.startswith("bench.")


def _mentions_bench(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts = sub.value.replace("\\", "/").replace(".", "/").split("/")
            if "bench" in parts:
                return True
    return False


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_sys_path_edit(func: ast.AST) -> bool:
    # sys.path.insert(...) / sys.path.append(...) / sys.path.extend(...)
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
    )


def bench_imports(source: str, filename: str = "<src>") -> list:
    """Every line in ``source`` that reaches for the ``bench`` package."""
    hits = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            hits += [(node.lineno, a.name) for a in node.names if _is_bench_name(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and _is_bench_name(node.module):
                hits.append((node.lineno, node.module))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _DYNAMIC_IMPORTERS and any(_mentions_bench(a) for a in node.args):
                hits.append((node.lineno, f"{name}(...bench...)"))
            elif _is_sys_path_edit(node.func) and _mentions_bench(node):
                hits.append((node.lineno, "sys.path edit naming bench"))
    return hits


def _plugin_py_files():
    for root, _dirs, files in os.walk(_PLUGIN):
        for f in sorted(files):
            if f.endswith(".py"):
                yield os.path.join(root, f)


def test_plugin_never_imports_bench():
    flagged = []
    files = list(_plugin_py_files())
    assert len(files) > 50  # the walk actually found the engine
    for path in files:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for lineno, what in bench_imports(src, path):
            flagged.append(f"{os.path.relpath(path, _REPO)}:{lineno} ({what})")
    assert not flagged, (
        "plugin/ must not import bench/ (bench is absent from every installed plugin; "
        "point the user at `PYTHONPATH=plugin python -m bench.<tool>` instead):\n  "
        + "\n  ".join(flagged)
    )


def test_checker_catches_every_import_shape():
    caught = bench_imports(
        textwrap.dedent(
            """
            import bench
            import bench.dream_eval as DE
            from bench import calibrate_thresholds
            from bench.dream_eval import main
            import importlib
            importlib.import_module("bench.dream_eval")
            __import__("bench")
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bench"))
            sys.path.append("../bench")
            """
        )
    )
    assert len(caught) == 8, caught


def test_checker_passes_the_compliant_shapes():
    assert not bench_imports(
        textwrap.dedent(
            """
            from .eval_recall import evaluate
            import benchmark_utils
            from . import bench_like
            msg = "run PYTHONPATH=plugin python -m bench.dream_eval"
            import sys
            sys.path.insert(0, "plugin")
            """
        )
    )


_BLOCKED_RUNTIME = r'''
import importlib.abc, json, os, sys

class _NoBench(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "bench" or name.startswith("bench."):
            raise ImportError("bench/ is not shipped with the plugin")
        return None

sys.meta_path.insert(0, _NoBench())
try:
    import bench  # noqa: F401
    raise SystemExit("bench was importable")
except ImportError:
    pass

import memory.eval_recall as E
eager = sorted(m for m in ("memory.eval_arms", "memory.eval_adversarial", "memory.eval_floor")
               if m in sys.modules)
assert not eager, f"importing the facade loaded eval-only siblings: {eager}"
from memory.eval_recall import GATE_ABSTENTION, abstention_rate, load_abstention_set  # noqa: F401

from memory import mcp_tools_consolidate as MC
from memory import telemetry as T
from memory.build_index import build_index, default_index_dir

md = os.environ["HIPPO_MEMORY_DIR"]
build_index(md, default_index_dir(md))
for q in ("how do I roll back a deploy", "roll back a deploy safely", "deploy roll back steps"):
    T.log_recall_event([], query=q, k=6, latency_ms=1.0,
                       telemetry_dir=os.environ["HIPPO_TELEMETRY_DIR"], session_id="s")
drafted = MC._tool_abstention_fixtures({"action": "draft"})
assert "per-item confirm" in drafted, drafted
confirmed = json.loads(MC._tool_abstention_fixtures({
    "action": "confirm", "query": "how do I roll back a deploy", "expected": ["rollback_plan"],
}))
assert confirmed.get("ok") is True, confirmed
assert not any(m == "bench" or m.startswith("bench.") for m in sys.modules)
print("RUNTIME-OK")
'''


def _mem(name, desc):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\nbody\n'


def test_runtime_imports_and_serves_abstention_fixtures_without_bench(tmp_path):
    md = tmp_path / ".claude" / "memory"
    md.mkdir(parents=True)
    (md / "deploy_runbook.md").write_text(
        _mem("deploy_runbook", "how the web service is deployed via the canary lane")
    )
    (md / "rollback_plan.md").write_text(_mem("rollback_plan", "how to roll back a bad web deploy"))
    env = {
        **os.environ,
        # Only the plugin on the path, and a cwd outside the repo: bench is unreachable
        # the way it is on an installed copy (the meta-path block makes it certain).
        "PYTHONPATH": _PLUGIN,
        "HIPPO_MEMORY_DIR": str(md),
        "HIPPO_DISABLE_DENSE": "1",
        "HIPPO_TRUST_ALL": "1",
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "HIPPO_PENDING_DIR": str(tmp_path / "pending"),
        "HIPPO_TELEMETRY_DIR": str(tmp_path / "tele"),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _BLOCKED_RUNTIME],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RUNTIME-OK" in proc.stdout
