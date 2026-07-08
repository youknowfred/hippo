"""QUA-9: property-based fuzzing over the parsing surfaces.

Hypothesis round-trips that pin the invariants the rest of the engine assumes but which were
only ever exercised on hand-picked fixtures:

  - split_frontmatter preserves the body verbatim (a suffix of the input; the no-frontmatter
    path returns the input unchanged) — the "recomposition lossless" guarantee callers rely on;
  - the additive frontmatter writers (backfill_text / set_invalid_after) NEVER touch the body
    for arbitrary frontmatter;
  - clean_query is total and its output draws only tokens present in the input (⊆);
  - tokenize / normalize_slug are total over arbitrary Unicode (locks RET-3).

Seeded with the known YAML-colon corpus (a `description:` whose VALUE contains colons is the
canonical parse-breaker). hypothesis is a TEST-ONLY dependency — CI installs it alongside
pytest/pytest-timeout; it is NOT in plugin/requirements.txt (mirrors QUA-10's pytest-timeout).
"""

from __future__ import annotations

import os
import tempfile

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from hypothesis import HealthCheck, example  # noqa: E402

from memory.build_index import tokenize  # noqa: E402
from memory.links import normalize_slug  # noqa: E402
from memory.provenance import backfill_text, split_frontmatter  # noqa: E402
from memory.recall import clean_query  # noqa: E402
from memory.staleness import set_invalid_after  # noqa: E402

# Text over a wide Unicode range incl. the tricky bits: colons (YAML), CJK, accents, emoji,
# newlines. Skip the NUL codepoint and lone surrogates ("Cs") — neither can occur in the valid
# UTF-8 on-disk files these surfaces actually read, and surrogates can't be written to disk.
_UNICODE = st.characters(min_codepoint=1, max_codepoint=0x2FA1F, exclude_categories=("Cs",))
_text = st.text(_UNICODE, max_size=400)

_YAML_COLON = '---\nname: x\ndescription: "a: b: c — colons in the value"\nmetadata:\n  type: project\n---\nbody line one\nbody line two\n'


def _body_of(text):
    return split_frontmatter(text)[1]


# --------------------------------------------------------------------------- #
# split_frontmatter — body preserved verbatim; no-frontmatter path is identity
# --------------------------------------------------------------------------- #
@settings(deadline=None)
@given(_text)
@example(_YAML_COLON)
@example("---\n---\n")            # empty frontmatter
@example("---\nname: x\n---")     # no trailing newline after close fence
@example("no frontmatter here")   # not a memory file
@example("---\nunterminated frontmatter\nbody")  # open fence, never closed
def test_split_frontmatter_body_is_verbatim(text):
    fm, body = split_frontmatter(text)
    assert isinstance(body, str)
    if fm is None:
        assert body == text  # no frontmatter → input returned unchanged
    else:
        assert isinstance(fm, list) and all(isinstance(ln, str) for ln in fm)
        assert text.endswith(body)  # the body is a verbatim suffix of the input
    # Deterministic / total.
    assert split_frontmatter(text) == (fm, body)


# --------------------------------------------------------------------------- #
# backfill_text — additive frontmatter insertion never touches the body
# --------------------------------------------------------------------------- #
@settings(deadline=None)
@given(_text, st.lists(st.text(_UNICODE, max_size=30), max_size=4), st.text(max_size=40))
@example(_YAML_COLON, ["src/app.py"], "abc123")
def test_backfill_text_never_touches_body(text, cited, sc):
    new_text, changed = backfill_text(text, cited, sc or None)
    assert isinstance(new_text, str) and isinstance(changed, bool)
    # Whatever it did to the frontmatter, the body (suffix past the close fence) is unchanged.
    assert _body_of(new_text) == _body_of(text)
    if not changed:
        assert new_text == text  # a no-op is byte-identical


# --------------------------------------------------------------------------- #
# set_invalid_after — additive frontmatter key; body byte-identical (file-based)
# --------------------------------------------------------------------------- #
# On-disk memory files use LF line endings; a bare CR would be normalized to LF by Python's
# universal-newline translation on read — a filesystem artifact, not set_invalid_after touching
# the body — so strip CR from the generated on-disk text.
_LF_TEXT = st.text(_UNICODE, max_size=200).map(lambda s: s.replace("\r", ""))


@settings(deadline=None, max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(_LF_TEXT, _LF_TEXT)
@example("name: x\ndescription: \"a: b\"", "body\nlines\n")
def test_set_invalid_after_never_touches_body(fm_body, body):
    text = "---\n" + fm_body + "\n---\n" + body
    original_body = _body_of(text)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        res = set_invalid_after(path, ts="2030-01-01T00:00:00+00:00")
        assert res["error"] is None or isinstance(res["error"], str)  # never raises
        with open(path, "r", encoding="utf-8") as fh:
            after = fh.read()
    assert _body_of(after) == original_body  # write (or refusal) left the body untouched


# --------------------------------------------------------------------------- #
# clean_query — total; output draws only tokens present in the input
# --------------------------------------------------------------------------- #
@settings(deadline=None)
@given(_text)
@example("```python\nraise ValueError('boom')\n```\nwhy did this fail")
@example("<task-notification>noise</task-notification> real question about deploys")
@example(_YAML_COLON)
def test_clean_query_total_and_output_tokens_subset_input(raw):
    out = clean_query(raw)
    assert isinstance(out, str)  # total: never raises, always a string
    # Output ⊆ input at the CHARACTER level: every token the cleaned query carries is a
    # (case-folded) substring of the raw input — clean_query drops and MINES tokens (an
    # identifier mined out of code can retokenize across a digit boundary, e.g. "0AA" → "AA"),
    # but it never invents a token whose characters aren't already in the input.
    lowered = raw.lower()
    assert all(t in lowered for t in tokenize(out))


# --------------------------------------------------------------------------- #
# tokenize / normalize_slug — total over arbitrary Unicode (RET-3)
# --------------------------------------------------------------------------- #
@settings(deadline=None)
@given(_text)
@example("café RÉSUMÉ Москва 東京 λόγος")  # accented Latin + Cyrillic + CJK + Greek
def test_tokenize_total_over_unicode(text):
    toks = tokenize(text)
    assert isinstance(toks, list)
    assert all(isinstance(t, str) and t for t in toks)  # only non-empty string tokens
    assert tokenize(text) == toks  # deterministic


@settings(deadline=None)
@given(_text)
@example("Some Memory Name.md")
@example("__weird___slug__.md.md")
def test_normalize_slug_total_and_well_formed(s):
    slug = normalize_slug(s)
    assert isinstance(slug, str)
    assert slug == slug.strip("-")            # no leading/trailing hyphen
    assert "--" not in slug                    # no run of hyphens
    assert "_" not in slug                     # underscores unified to hyphens
    assert not any(ch.isspace() for ch in slug)  # whitespace unified to hyphens
