"""Every `memory/*.py` CLI's `if __name__ == "__main__":` guard is the module's LAST
top-level statement.

Run as `python -m memory.<mod>`, the guard executes main() at the point the interpreter
reaches it — any def below it does not exist yet. Importers finish the whole module
first, so in-process tests and the hook/doctor callers can never see this; v1.36.0
shipped `lint_floor`'s guard above the helpers its main() calls and the CLI crashed
with a NameError while the suite stayed green. This is the structural ratchet for every
module; `test_floor_governance` carries the behavioural subprocess pin for lint_floor.
"""

from __future__ import annotations

import ast
import glob
import os

_MEMORY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "memory"
)


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    sides = [node.test.left, *node.test.comparators]
    names = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
    main = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
    return names and main


def test_main_guard_is_the_last_top_level_statement():
    paths = sorted(glob.glob(os.path.join(_MEMORY_DIR, "*.py")))
    assert paths, f"no modules found under {_MEMORY_DIR}"
    offenders = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            body = ast.parse(fh.read(), filename=path).body
        for i, node in enumerate(body):
            if _is_main_guard(node) and i != len(body) - 1:
                offenders.append(f"{os.path.basename(path)}:{node.lineno}")
    assert not offenders, (
        "__main__ guard followed by more top-level code (a `python -m` run executes "
        f"main() before the later defs exist): {offenders}"
    )
