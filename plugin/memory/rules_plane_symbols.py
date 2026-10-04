"""RUL-2's symbol leg: is a dotted ``module.symbol`` backtick ref really rot?

Decomposed out of ``rules_plane.py``, which keeps ``rules_rot`` and the data-file leg;
this sibling never imports its façade. The leg finds the module BY NAME (the only
``<module>.py`` in the tree), so a name match is a guess, and a guess must never become a
finding: under-flag beats cry-wolf. On 2026-10-04 every one of the seven symbol findings
in fred-em/Skyline's ``.claude/rules`` was false, and so were all eight in four other
repos checked beside it. The classes, each answered here:

  1. ``async def stage_faq_reorder(`` — the old regex admitted only ``def``/``class``.
  2. ``YOAST_META_FIELDS: dict[str, YoastMetaField] = {`` — an annotated module constant.
  3. ``settings.move_destination_host`` — ``settings`` is the pydantic object in
     config.py (``settings = Settings()``, imported everywhere as
     ``from skyline.config import settings``); the only ``settings.py`` is a route.
  4. ``wp_post_contract.acf`` — a table's column (``__tablename__ = "wp_post_contract"``,
     ``acf: Mapped[...]``); the only ``wp_post_contract.py`` is a helper module (which
     happens to carry an ``acf:`` class field — counted, but a table need not have one).
  5. ``faq.restored`` — an event-type string (``FAQ_RESTORED = "faq.restored"``); likewise
     a Railway ``${{nightly.PGHOST}}`` reference and a ``comms.proposals`` schema.table.
  6. ``ingest.ads.tests`` — the qualifier contradicts the pick: it names the module
     ``ingest/ads/tests.py``, not a ``tests`` symbol in the one ``ads.py`` elsewhere.

``defined_names`` answers 1-2 (and the class attributes of 4) by parsing the resolved
module; ``qualifier_contradicts`` answers 6; ``alive_elsewhere`` answers 3-5 in one pass
over the tree. What still flags: a ref whose module resolves and fits its qualifier, that
the module does not bind, that no data mapping holds, that no quoted string carries, and
whose token is bound as nothing else in the tree, or is, but whose symbol is defined
nowhere. That is a symbol that left.

Read-only; never raises out of ``rules_rot`` (every reader here swallows its own failure
into silence).
"""

from __future__ import annotations

import ast
import os
import re
import warnings
from typing import Dict, List, Optional, Set, Tuple

# A star import or a PEP 562 module ``__getattr__``: the module may bind ANY name.
_WILDCARD = "*"


def _target_names(node: ast.AST, out: Set[str]) -> None:
    """Names an assignment target binds (``a``, ``a, *b``, ``[a, b]``) — never an
    attribute or subscript target, which binds nothing in this scope."""
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _target_names(elt, out)
    elif isinstance(node, ast.Starred):
        _target_names(node.value, out)


def _scope_bindings(body: List[ast.stmt], out: Set[str]) -> None:
    """Names one scope's statements bind, descending into if/try/with/for/while/match
    blocks (still the same scope) and never into a def/class body (a new one)."""
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(stmt.name)
            continue
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                _target_names(target, out)
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
            _target_names(stmt.target, out)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if item.optional_vars is not None:
                    _target_names(item.optional_vars, out)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                if alias.name == "*":
                    out.add(_WILDCARD)
                else:
                    out.add(alias.asname or alias.name.split(".")[0])
        elif type(stmt).__name__ == "TypeAlias":  # 3.12 ``type X = ...``
            _target_names(stmt.name, out)
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.stmt):
                _scope_bindings([child], out)
            elif isinstance(getattr(child, "body", None), list):  # except handler / match case
                _scope_bindings(child.body, out)


def defined_names(text: str) -> Optional[Set[str]]:
    """Every name ``module.NAME`` can reach in one module's source: module scope
    (def/async def/class, plain/annotated/augmented assignment — bare ``NAME: T`` too —
    and imports, because a façade's re-export is a live symbol), every class body
    (attributes, annotated fields, methods), and every def/class name at any depth (the
    pre-fix regex admitted indented defs, and ``module.method`` is common shorthand).
    Function locals are NOT names. Contains ``_WILDCARD`` when a star import or a module
    ``__getattr__`` makes any name possible.

    ``None`` when the source does not parse — including newer syntax than the running
    interpreter knows — and the caller falls back to a regex. Warnings are silenced: an
    invalid escape in someone's source must not print from a doctor run.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except Exception:
        return None
    module_scope: Set[str] = set()
    _scope_bindings(tree.body, module_scope)
    if "__getattr__" in module_scope:
        module_scope.add(_WILDCARD)
    names = set(module_scope)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                _scope_bindings(node.body, names)
    return names


def _regex_defines(text: str, symbol: str) -> bool:
    """The unparseable-source fallback: generous on purpose (any-indent assignment and
    annotation, any import line naming the symbol, any star import or module
    ``__getattr__``), because a guess here can only ever silence, never flag."""
    s = re.escape(symbol)
    return bool(
        re.search(
            rf"(?m)^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+{s}\b"
            rf"|^[ \t]*{s}[ \t]*(?::|=(?!=))"
            rf"|^[ \t]*(?:from[ \t]+\S+[ \t]+)?import\b[^\n]*\b{s}\b"
            rf"|^[ \t]*from[ \t]+\S+[ \t]+import[ \t]+\*"
            rf"|^def[ \t]+__getattr__\b",
            text,
        )
    )


def module_defines(
    repo_root: str, mod_path: str, symbol: str, cache: Dict[str, object]
) -> Optional[bool]:
    """Does the resolved module bind ``symbol``? ``None`` when it cannot be read (the
    caller stays silent). ``cache`` holds, per module, its parsed name set — or its raw
    text when it would not parse — for the life of one ``rules_rot`` call."""
    if mod_path not in cache:
        try:
            with open(os.path.join(repo_root, mod_path), "r", encoding="utf-8") as fh:
                text = fh.read()
            names = defined_names(text)
            cache[mod_path] = names if names is not None else text
        except Exception:
            cache[mod_path] = None
    entry = cache[mod_path]
    if entry is None:
        return None
    if isinstance(entry, str):
        return _regex_defines(entry, symbol)
    return symbol in entry or _WILDCARD in entry  # type: ignore[operator]


def qualifier_contradicts(
    parts: List[str], mod_path: str, basename_index: Dict[str, List[str]]
) -> bool:
    """True when the ref's own spelling says the by-name pick is the wrong file: its
    package qualifier is not the picked file's directory tail (``ingest.ads.tests``
    against ``ingest/nightly_steps/ads.py``; ``CHARTER.template.md`` against
    ``lib/template.py``), or the whole dotted path is itself a module or package in the
    tree (``ingest/ads/tests.py``) — a module citation, not a missing symbol."""
    if len(parts) > 2:
        want = "/".join(parts[:-1]) + ".py"
        if not (mod_path == want or mod_path.endswith("/" + want)):
            return True
    whole = "/".join(parts)
    for target, base in ((whole + ".py", parts[-1] + ".py"), (whole + "/__init__.py", "__init__.py")):
        for f in basename_index.get(base) or []:
            if f == target or f.endswith("/" + target):
                return True
    return False


# --------------------------------------------------------------------------- #
# The repo-wide sweep: what the rest of the tree says about a ref about to flag
# --------------------------------------------------------------------------- #
# Read cap per file: the sweep is raw text, never a parse, so it can afford the same
# ceiling the data-file leg sized by its motivating 1.7MB file. Above it: skipped
# (silence on that file's evidence, which can only keep a finding, never invent one).
_SWEEP_MAX_BYTES = 8_000_000

_FROM_IMPORT_RE = re.compile(
    r"(?m)^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n]*)"
)
# Per-hit context checks, applied to the few dozen characters around one whole-word
# occurrence (see _hits) — never to a whole file.
_ASSIGNS = re.compile(r"[ \t]*(?::|=(?!=))")  # ``NAME =`` / ``NAME: T`` / ``NAME: T = ...``
_ANNOTATES = re.compile(r"[ \t]*:(?!=)")
_REBINDS = re.compile(r"[ \t]*=(?!=)")
_DEF_PREFIX = re.compile(r"[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+")
_IMPORT_AS = re.compile(r"\bimport[ \t]+([\w.]+)[ \t]+as[ \t]+\Z")
_TABLE_NAMED = re.compile(r"(?:(?:__tablename__|db_table)[ \t]*[:=][ \t]*|\b(?:Table|create_table)\(\s*)[\"']\Z")
_TABLE_CREATED = re.compile(
    r"\bcreate[ \t]+table[ \t]+(?:if[ \t]+not[ \t]+exists[ \t]+)?(?:\w+\.)?\Z", re.IGNORECASE
)
# Context bound per hit: enough for any binding/def/import/table shape on its line, and
# for ``Table(`` / ``op.create_table(`` putting the name on the next line — while a
# multi-megabyte one-line file (minified JS) costs a bounded slice per hit, not a line.
_CTX = 160


def _sweep_exts() -> Tuple[str, ...]:
    """Provenance's code-extension list (one concept, one list — ORC-2), minus ``.mdc``:
    a Cursor rule file is a rule, and a rule must not vouch for a rule. Markdown is
    absent from the list already, so no governance file can vouch for itself."""
    from .provenance import _CODE_EXTS

    return tuple("." + e for e in _CODE_EXTS if e != "mdc")


def _word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _hits(text: str, word: str, ctx: Optional[int] = None):
    """``(start, line_before, line_after)`` for every whole-word occurrence of ``word``,
    the line context clipped to ``ctx`` characters each side when given. A ``str.find``
    walk: a common token in a big file costs C-speed scans plus a small check per hit,
    where a whole-file regex with no literal prefix tries every position (measured: 6 of
    9 seconds of a full sweep over a 115MB tree, before this shape)."""
    n, size = len(word), len(text)
    i = text.find(word)
    while i >= 0:
        j = i + n
        if (i == 0 or not _word_char(text[i - 1])) and (j == size or not _word_char(text[j])):
            lo = 0 if ctx is None else max(0, i - ctx)
            hi = size if ctx is None else min(size, j + ctx)
            nl = text.rfind("\n", lo, i)
            line_end = text.find("\n", j, hi)
            yield i, text[nl + 1 if nl >= 0 else lo : i], text[j : line_end if line_end >= 0 else hi]
        i = text.find(word, j)


def _quoted_occurrence(text: str, ref: str) -> bool:
    """``ref`` sits inside a quoted string on one line: ``"faq.restored"``,
    ``'PGHOST=${{nightly.PGHOST}}'``, a one-line docstring, a JSON/YAML string value.
    Quote parity on the line, not a string-literal regex: a 10k-character YAML line full
    of apostrophes must cost one scan, not a backtracking blow-up. A multi-line
    docstring's prose (no quote on the line) is not a string literal, and stays silent.
    ``faq.restored.v2`` is a different name, not an occurrence."""
    for _i, before, after in _hits(text, ref):
        if after[:1] == "." and _word_char(after[1:2] or " "):
            continue
        if any(before.count(q) % 2 == 1 and q in after for q in ('"', "'")):
            return True
    return False


def _names_a_table(text: str, token: str) -> bool:
    """``token`` is a TABLE's name in this source — ``__tablename__ = "wp_post_contract"``,
    Django's ``db_table``, ``Table("x", ...)``/``op.create_table("x", ...)``, or a
    ``CREATE TABLE`` statement — so ``token.column`` may name a column. Deliberately NOT
    "the token appears in quotes": a module's own name is a string all over its tree
    (``LANE = "faq_manage"``, logger names), and that alone silenced a moved symbol."""
    for i, before, after in _hits(text, token, _CTX):
        window = text[max(0, i - _CTX) : i]
        quote = text[i - 1 : i]
        if quote in ('"', "'") and after[:1] == quote and _TABLE_NAMED.search(window):
            return True
        if _TABLE_CREATED.search(before):
            return True
    return False


def _binds_other_than_module(text: str, token: str, mod_dir: str) -> bool:
    """Does this Python source bind ``token`` as something OTHER than the resolved module
    — a module-level ``settings = Settings()`` or ``settings: Settings``, a class field
    ``settings: Settings``, an attribute ``self.settings = ...``, an alias
    ``import x.config as settings``, an object import ``from skyline.config import
    settings``? A function local named after the module (``faq = make_faq()``) is not
    counted. A ``from <pkg> import token`` whose package's last component is the
    resolved module's own directory imports the MODULE (``from skyline.services import
    faq_manage``), and a bare ``from . import token`` is read the same way."""
    for _i, before, after in _hits(text, token, _CTX):
        if before == "" and _ASSIGNS.match(after):
            return True  # module level: ``settings = Settings()`` / ``settings: Settings``
        if before and not before.strip() and _ANNOTATES.match(after):
            return True  # a class field: ``    settings: Settings``
        if before.endswith(".") and _REBINDS.match(after):
            return True  # an attribute: ``self.settings = ...``
        m = _IMPORT_AS.search(before)
        if m and m.group(1).rsplit(".", 1)[-1] != token:
            return True  # ``import x.config as settings``
    for m in _FROM_IMPORT_RE.finditer(text):
        if token not in m.group(2):
            continue
        source = m.group(1).lstrip(".")
        for item in re.sub(r"#[^\n]*", "", m.group(2)).strip().strip("()").split(","):
            words = item.split()
            if not words:
                continue
            name = words[0]
            alias = words[2] if len(words) == 3 and words[1] == "as" else name
            if alias != token:
                continue
            if name != token:
                return True  # ``from x import cfg as settings``
            if source and source.rsplit(".", 1)[-1] != mod_dir:
                return True  # ``from skyline.config import settings``
    return False


def _defines_anywhere(text: str, symbol: str) -> bool:
    """``symbol`` is defined in this Python source at any indentation — a def, a class,
    an assignment, an annotated field (``move_destination_host: str = ...``,
    ``acf: Mapped[dict | None]``)."""
    for _i, before, after in _hits(text, symbol, _CTX):
        if not before.strip() and _ASSIGNS.match(after):
            return True
        if _DEF_PREFIX.fullmatch(before):
            return True
    return False


def alive_elsewhere(
    repo_root: str, repo_files: Set[str], refs: Dict[str, Tuple[List[str], str]]
) -> Set[str]:
    """The subset of ``refs`` (span -> (parts, resolved module path)) that the rest of the
    tree vouches for. ONE streaming pass over tracked code/data text, run only when some
    ref is about to flag, so a healthy rules plane never reads the tree at all. A ref is
    alive when EITHER:

      - it appears inside a quoted string anywhere (class 5): the dotted name lives in
        some other namespace — an event type, a deploy reference, a SQL table; or
      - its module token is bound as something other than the module (class 3: a
        module-level object, a field, an attribute, an object import, an alias) or names
        a table (class 4: ``__tablename__ = "wp_post_contract"``), AND its symbol is
        defined somewhere in the Python tree. Both halves, because either alone is too
        weak: a common token is bound somewhere in any big repo, and a symbol that MOVED
        to another module is defined somewhere too — that move is exactly the rot this
        leg exists to catch.

    Never raises; an unreadable file, or one whose check fails, contributes no evidence
    (which can only keep a finding, never invent one).
    """
    if not refs:
        return set()
    try:
        exts = _sweep_exts()
    except Exception:
        return set()
    quoted: Set[str] = set()
    bound: Set[str] = set()
    defined: Set[str] = set()
    pending = dict(refs)
    for rel_path in sorted(repo_files):
        if not pending:
            break
        if not rel_path.endswith(exts):
            continue
        full = os.path.join(repo_root, rel_path)
        try:
            if os.path.getsize(full) > _SWEEP_MAX_BYTES:
                continue
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:
            continue
        is_py = rel_path.endswith(".py")
        for span, (parts, mod_path) in list(pending.items()):
            token, symbol = parts[-2], parts[-1]
            try:
                if span in text and _quoted_occurrence(text, span):
                    quoted.add(span)
                if span not in bound and token in text:
                    if _names_a_table(text, token):
                        bound.add(span)
                    elif is_py and _binds_other_than_module(
                        text, token, os.path.basename(os.path.dirname(mod_path))
                    ):
                        bound.add(span)
                if is_py and span not in defined and symbol in text and _defines_anywhere(text, symbol):
                    defined.add(span)
            except Exception:
                continue  # this file's evidence for this ref is lost; the ref may still flag
            if span in quoted or (span in bound and span in defined):
                del pending[span]
    return quoted | (bound & defined)
