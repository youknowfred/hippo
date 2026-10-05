"""SRF-3: ``hippo env`` — the plugin paths and interpreter, as shell exports.

Claude Code fills ``${CLAUDE_PLUGIN_ROOT}`` and ``${CLAUDE_PLUGIN_DATA}`` into a skill's
SKILL.md when it loads it, and into nothing else: a skill's supporting flow file, read on
demand, arrives with the bare forms intact, and the Bash tool inherits neither variable. A
block in such a file opens with ``eval "$(hippo env)"`` instead: ``bin/hippo`` already
knows its plugin root, has derived the data dir of a marketplace install, and resolved the
interpreter, so this prints them for the calling shell. Output is ``export`` lines only,
shell-quoted, so ``eval`` is safe.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import List, Optional


def exports() -> List[str]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
    pairs = [
        ("CLAUDE_PLUGIN_ROOT", root),
        ("CLAUDE_PLUGIN_DATA", data),
        ("PY", sys.executable),
        ("PYTHONPATH", root),
    ]
    return [f"export {k}={shlex.quote(v)}" for k, v in pairs]


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="hippo env",
        description='Print the plugin root, data dir and interpreter as shell exports: eval "$(hippo env)"',
    )
    parser.parse_args(argv)
    print("\n".join(exports()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
