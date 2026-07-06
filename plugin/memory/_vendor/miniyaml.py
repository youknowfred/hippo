"""Frontmatter-subset YAML parser for the bare-python3 pre-bootstrap path.

Exposes ``safe_load(text)`` covering exactly the shapes the memory frontmatter
schema uses (see ``new_memory._render_frontmatter`` + ``provenance.backfill_text``):

  - top-level ``key: value`` scalars (JSON-quoted, single-quoted, or bare)
  - nested block maps by indentation (``metadata:`` + indented keys, any depth)
  - flow lists (``cited_paths: ["a.py", "b.py"]`` — written by json.dumps, so
    JSON-parseable; a lenient split covers hand-written variants)
  - block lists (``- item`` lines under a key)
  - full-line ``#`` comments and blank lines

Deliberately NOT a general YAML parser. Anything outside the subset RAISES — in
particular an unquoted value containing ``": "`` (the known corpus hazard PyYAML
also rejects) and ``|``/``>`` block scalars. Failure direction matters: this parser
must never ACCEPT what PyYAML would reject, or a broken memory would be invisible
to the integrity producer in degraded mode while flagged in the venv mode. The
consumer (``provenance.parse_frontmatter``) already wraps calls in try/except.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Tuple

_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(\s|$)")


class MiniYamlError(ValueError):
    pass


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s.startswith('"'):
        try:
            return json.loads(s)
        except Exception as exc:
            raise MiniYamlError(f"bad double-quoted scalar: {s[:60]!r}") from exc
    if s.startswith("'"):
        if len(s) < 2 or not s.endswith("'"):
            raise MiniYamlError(f"bad single-quoted scalar: {s[:60]!r}")
        return s[1:-1].replace("''", "'")
    if s.startswith("["):
        try:
            return json.loads(s)
        except Exception:
            if not s.endswith("]"):
                raise MiniYamlError(f"bad flow list: {s[:60]!r}")
            inner = s[1:-1].strip()
            if not inner:
                return []
            return [_parse_scalar(part) for part in inner.split(",")]
    if s.startswith(("|", ">")):
        raise MiniYamlError("block scalars are outside the frontmatter subset")
    if ": " in s:
        # The known corpus hazard — PyYAML rejects unquoted values containing ': '
        # in this position; accepting it here would hide broken memories pre-bootstrap.
        raise MiniYamlError(f"mapping value in plain scalar (quote it): {s[:60]!r}")
    if s in ("null", "~", ""):
        return None
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: List[Tuple[int, str]], start: int, indent: int) -> Tuple[Any, int]:
    """Parse one block (map or list) whose items sit at exactly ``indent``."""
    if start < len(lines) and lines[start][1].lstrip().startswith("- "):
        items: List[Any] = []
        i = start
        while i < len(lines) and lines[i][0] == indent and lines[i][1].lstrip().startswith("- "):
            items.append(_parse_scalar(lines[i][1].lstrip()[2:]))
            i += 1
        return items, i

    out: dict = {}
    i = start
    while i < len(lines):
        line_indent, line = lines[i]
        if line_indent < indent:
            break
        if line_indent != indent:
            raise MiniYamlError(f"unexpected indentation: {line[:60]!r}")
        stripped = line.strip()
        m = _KEY_RE.match(stripped)
        if not m:
            raise MiniYamlError(f"not a mapping entry: {stripped[:60]!r}")
        key = m.group(1)
        value_part = stripped[m.end(1) + 1 :].strip()
        if value_part:
            out[key] = _parse_scalar(value_part)
            i += 1
            continue
        # Empty value: either a nested block (deeper indent follows) or null.
        if i + 1 < len(lines) and lines[i + 1][0] > indent:
            value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            out[key] = value
        else:
            out[key] = None
            i += 1
    return out, i


def safe_load(text: str) -> Any:
    """Parse the frontmatter subset. Raises MiniYamlError outside it. None for empty."""
    if text is None:
        return None
    lines: List[Tuple[int, str]] = []
    for raw in text.split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        leading_ws = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in leading_ws:
            raise MiniYamlError("tab indentation is outside the frontmatter subset")
        lines.append((_indent_of(raw), raw))
    if not lines:
        return None
    value, consumed = _parse_block(lines, 0, lines[0][0])
    if consumed != len(lines):
        raise MiniYamlError(f"trailing content: {lines[consumed][1][:60]!r}")
    return value
