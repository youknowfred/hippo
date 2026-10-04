"""RUL-2's data-file leg made cheap (rules_plane_data) without moving a verdict.

``rules_rot`` tries a dotted ref about to flag as a nested mapping path in every tracked
YAML/JSON/TOML file, and parsing them was 3.6s of a 3.8s run on em-growth-labs
(2026-10-03). ``may_hold`` now skips a parse when a file's raw bytes cannot hold every key
of the path, and ``vouched`` parses the small data files, then runs the tree sweep, then
parses the big ones. Pinned here: a key spelled only through escapes (the one way a key
can be absent from the bytes) still resolves; a file that cannot hold the path is never
parsed; a sweep-vouched ref never parses a big file; and the order changes no finding.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import memory.rules_plane as RP
import memory.rules_plane_data as RD

from .conftest import git_commit, write_file

_CAP = 8_000_000


def _spy_parses(monkeypatch) -> list:
    """Record each data file ``rules_rot`` actually parses (first touch per call)."""
    parsed: list = []
    real = RD.data_docs

    def spy(repo_root, rel_path, cache, max_bytes):
        if rel_path not in cache:
            parsed.append(rel_path)
        return real(repo_root, rel_path, cache, max_bytes)

    monkeypatch.setattr(RD, "data_docs", spy)
    return parsed


# ---- the prefilter never skips a match -------------------------------------------------- #
# A key that READS ``cfgmod`` without the bytes ``cfgmod`` anywhere in the file: the escapes
# that can write a word character (\x \u \U, and YAML's escaped line break, LF and CRLF).
_ESCAPE_SPELLED = [
    ("conf.yaml", '"\\x63fgmod":\n  retries: 3\n'),
    ("conf.yaml", '"\\u0063fgmod":\n  retries: 3\n'),
    ("conf.yaml", '"\\U00000063fgmod":\n  retries: 3\n'),
    ("conf.yaml", '? "cfg\\\n    mod"\n: {retries: 3}\n'),
    ("conf.yaml", '? "cfg\\\r\n    mod"\r\n: {retries: 3}\r\n'),
    ("conf.json", '{"\\u0063fgmod": {"retries": 3}}\n'),
    pytest.param(
        "conf.toml",
        '["\\u0063fgmod"]\nretries = 3\n',
        marks=pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+"),
    ),
]


@pytest.mark.parametrize("rel, text", _ESCAPE_SPELLED)
def test_data_leg_prefilter_parses_a_key_spelled_only_by_escapes(repo, memory_dir, rel, text):
    write_file(repo, "cfgmod.py", "def unrelated():\n    pass\n")
    write_file(repo, rel, text)
    git_commit(repo, "escaped config key", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Honor `cfgmod.retries` from config.")
    with open(os.path.join(repo, rel), "rb") as fh:
        assert b"cfgmod" not in fh.read()  # the key is absent from the bytes...
    assert RP.rules_rot(repo)["code_ref_rot"] == []  # ...and the ref is still alive


# Shapes a key equal to an identifier can take without any escape: each spells the key.
_SPELLED = [
    "cfgmod:\n  retries: 3\n",
    "'cfgmod': {\"retries\": 3}\n",
    "{cfgmod: {retries}}\n",  # flow keys with null values still count
    "? |-\n  cfgmod\n: {retries: 3}\n",  # a block scalar as an explicit key
    "base: &b {retries: 3}\ncfgmod:\n  <<: *b\n",  # merge: the key lives at the anchor
    "k: &n cfgmod\n*n : {retries: 3}\n",  # alias as a key
    "a: 1\n---\ncfgmod: {retries: 3}\n",  # second document
]


@pytest.mark.parametrize("text", _SPELLED)
def test_data_leg_prefilter_admits_every_spelled_key_shape(tmp_path, text):
    path = tmp_path / "conf.yaml"
    path.write_text(text, encoding="utf-8")
    docs = RD.data_docs(str(tmp_path), "conf.yaml", {}, _CAP)
    assert any(isinstance(d.get("cfgmod"), dict) and "retries" in d["cfgmod"] for d in docs)
    assert RD.may_hold(str(path), ["cfgmod", "retries"], _CAP)


_W = st.characters(categories=("Lu", "Ll", "Lt", "Lm", "Lo", "Nd"))
_KEY = st.builds(lambda head, tail: head + tail, st.sampled_from("abcXYZ_"), st.text(_W, max_size=8))
_TREE = st.recursive(
    st.integers(0, 9),
    lambda kids: st.dictionaries(_KEY, kids, min_size=1, max_size=4),
    max_leaves=12,
).filter(lambda t: isinstance(t, dict))
_DUMPS = {
    "yaml": lambda t: yaml.safe_dump(t, allow_unicode=True),
    "yaml-ascii": lambda t: yaml.safe_dump(t, allow_unicode=False),
    "yaml-double": lambda t: yaml.safe_dump(t, allow_unicode=False, default_style='"', width=20),
    "json": lambda t: json.dumps(t, ensure_ascii=False),
    "json-ascii": lambda t: json.dumps(t, ensure_ascii=True),
}


def _paths(tree, prefix=()):
    for key, value in tree.items():
        here = prefix + (key,)
        if len(here) >= 2:
            yield list(here)
        if isinstance(value, dict):
            yield from _paths(value, here)


@settings(deadline=None, max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(tree=_TREE, style=st.sampled_from(sorted(_DUMPS)))
def test_data_leg_prefilter_admits_every_path_the_emitters_write(tree, style):
    """Real emitters, escaping included: PyYAML's double-quoted ASCII style writes
    non-ASCII identifier characters as ``\\xE9``/``\\u0436``, JSON's ``ensure_ascii`` as
    ``\\u`` surrogate pairs. Every root path in the tree must survive the prefilter."""
    text = _DUMPS[style](tree)
    assert (json.loads(text) if style.startswith("json") else yaml.safe_load(text)) == tree
    raw = text.encode("utf-8")
    for parts in _paths(tree):
        assert RD.raw_may_hold(raw, parts), (style, parts, raw[:200])


# ---- the prefilter prunes --------------------------------------------------------------- #
def test_data_leg_prefilter_never_parses_a_file_that_cannot_hold_the_path(
    repo, memory_dir, monkeypatch
):
    """A genuinely rotten ``util.vanished``: the only data file parsed is the one whose
    bytes carry both tokens. Escapes that cannot write an identifier (a backslash in a
    single-quoted Windows path, a ``\\u`` inside a string with spaces) do not force one."""
    write_file(repo, "util.py", "def kept():\n    pass\n")
    write_file(repo, "mentions.yaml", "util:\n  note: the vanished helper\n")
    write_file(repo, "notes.yaml", "release:\n  path: 'C:\\Users\\x'\n  body: \"caf\\u00e9 au lait\"\n")
    write_file(repo, "other.json", '{"util": {"kept": true}}\n')
    git_commit(repo, "data files", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Call `util.kept`, never `util.vanished`.")
    parsed = _spy_parses(monkeypatch)
    rot = RP.rules_rot(repo)["code_ref_rot"]
    assert rot == [{"file": "CLAUDE.md", "ref": "util.vanished", "kind": "symbol"}]
    assert parsed == ["mentions.yaml"]
    assert not RD.may_hold(os.path.join(repo, "notes.yaml"), ["util", "vanished"], _CAP)


# ---- cheapest evidence first, same findings --------------------------------------------- #
def test_sweep_vouched_ref_never_parses_a_big_data_file(repo, memory_dir, monkeypatch):
    """``faq.restored`` is an event type (quoted in events.py), so the sweep vouches for it;
    a big data file carrying both tokens is never parsed for it. The em-growth-labs shape:
    ``comms.proposals`` and ``nightly.PGHOST`` cost 3.6s of parsing before the sweep ran."""
    monkeypatch.setattr(RD, "_EAGER_PARSE_MAX_BYTES", 40)
    write_file(repo, "app/faq.py", "def create():\n    pass\n")
    write_file(repo, "app/events.py", 'FAQ_RESTORED = "faq.restored"\n')
    write_file(repo, "roadmap.yaml", "faq:\n  note: restored items are listed in the changelog\n")
    git_commit(repo, "faq + roadmap", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Emit `faq.restored` after an undo.")
    parsed = _spy_parses(monkeypatch)
    assert RP.rules_rot(repo)["code_ref_rot"] == []
    assert "roadmap.yaml" not in parsed


def test_big_data_files_still_vouch_and_still_flag_after_the_sweep(repo, memory_dir, monkeypatch):
    """Past the sweep the big files are still consulted: a ref only a big file resolves
    stays alive, and a rotten ref whose tokens sit in a big file is parsed and flagged."""
    monkeypatch.setattr(RD, "_EAGER_PARSE_MAX_BYTES", 40)
    write_file(repo, "cfgmod.py", "def unrelated():\n    pass\n")
    write_file(repo, "util.py", "def kept():\n    pass\n")
    write_file(repo, "small.yaml", "x: 1\n")
    write_file(repo, "big_conf.yaml", "cfgmod:\n  retries: 3\n  note: padded past the eager cap\n")
    write_file(repo, "big_notes.yaml", "util:\n  note: the vanished helper is gone, padded past\n")
    git_commit(repo, "configs", 1_700_000_000)
    write_file(repo, "CLAUDE.md", "Honor `cfgmod.retries`; `util.vanished` left.")
    parsed = _spy_parses(monkeypatch)
    rot = RP.rules_rot(repo)["code_ref_rot"]
    assert rot == [{"file": "CLAUDE.md", "ref": "util.vanished", "kind": "symbol"}]
    assert set(parsed) == {"big_conf.yaml", "big_notes.yaml"}


def test_vouched_is_the_same_set_in_every_pass_order(repo, memory_dir, monkeypatch):
    """The three passes only ever SILENCE a ref, each on its own evidence: the set
    ``vouched`` returns is the union whatever the eager cap splits where."""
    write_file(repo, "cfgmod.py", "def unrelated():\n    pass\n")
    write_file(repo, "app/faq.py", "def create():\n    pass\n")
    write_file(repo, "app/events.py", 'FAQ_RESTORED = "faq.restored"\n')
    write_file(repo, "conf.yaml", "cfgmod:\n  retries: 3\n")
    write_file(repo, "faq.yaml", "faq:\n  restored: noted\n")
    git_commit(repo, "both legs", 1_700_000_000)
    from memory.provenance import build_repo_file_index

    repo_files, _ = build_repo_file_index(repo)
    refs = {
        "cfgmod.retries": (["cfgmod", "retries"], "cfgmod.py"),
        "faq.restored": (["faq", "restored"], "app/faq.py"),
        "cfgmod.vanished": (["cfgmod", "vanished"], "cfgmod.py"),
    }
    for cap in (0, 40, _CAP):
        monkeypatch.setattr(RD, "_EAGER_PARSE_MAX_BYTES", cap)
        assert RD.vouched(repo, repo_files, refs, _CAP) == {"cfgmod.retries", "faq.restored"}
