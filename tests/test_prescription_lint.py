"""SEN-3 — ungrounded-prescription lint (agent-voiced attribution-of-intent, warn-only).

A memory that asserts "the user always wants X" — grounded in neither the captured hunk nor
a stated rationale — is the synthesized-prescription shape that amplifies sycophancy. This
lint flags exactly that, deterministically, WARN-ONLY (never blocks, never routes, never
ranks). The load-bearing pins:

  AC1  the pattern set is restricted to attribution-of-intent (not bare must/always/never)
       and has ZERO false positives against hippo's own docstrings + skill prose.
  AC2  a span is flagged ungrounded only with no fenced-hunk overlap AND no --rationale.
  AC3  the audit sweep classifies grounded / ungrounded-prescription / observation.
  AC4  lint output never reaches check_candidate's route/neighbor result or recall.
"""

from __future__ import annotations

import ast
import glob
import inspect
import os

import pytest

from memory import prescription_lint as PL


# --------------------------------------------------------------------------- #
# AC1: the pattern fires on the sycophancy shape, not on legitimate prose
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [
    "The user always wants backward compatibility.",
    "the owner prefers tabs over spaces",
    "the team really wants zero downtime",
    "the maintainer insists on squash merges",
    "the reviewer never wants a force-push",
])
def test_attribution_of_intent_matches(text):
    assert PL.prescriptive_spans(text) != []


@pytest.mark.parametrize("text", [
    "the user consents to the trusted corpus",          # observation (action verb)
    "the user reviewed the diff and confirmed it",       # observation
    "if the owner wants a 3-release arc, merge them",    # conditional
    "confirm the user wants each such memory",           # interrogative
    "whether the team prefers tabs is undecided",        # interrogative
    "the reviewer wants the top lessons, not the corpus",  # contrastive design gloss
    "memories must always cite their evidence",          # bare modal (not attribution)
    "always run the lint before fencing a hunk",         # bare imperative
])
def test_legitimate_prose_does_not_match(text):
    assert PL.prescriptive_spans(text) == []


def test_zero_false_positives_against_hippo_docstrings_and_skills():
    """AC1's binding pin: run the pattern over every plugin/memory docstring and skill body;
    it must not fire once. (The only repo strings that match are ROADMAP/EXPLORATIONS quoting
    'the user always wants' as THIS item's own example — those are specs, not scanned here.)"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(PL.__file__)))
    offenders = []
    for f in glob.glob(os.path.join(root, "memory", "*.py")):
        tree = ast.parse(open(f, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node)
                if doc and PL.prescriptive_spans(doc):
                    offenders.append((os.path.basename(f), PL.prescriptive_spans(doc)))
    for f in glob.glob(os.path.join(root, "skills", "*", "SKILL.md")):
        spans = PL.prescriptive_spans(open(f, encoding="utf-8").read())
        if spans:
            offenders.append((os.path.basename(os.path.dirname(f)), spans))
    assert offenders == [], f"prescription lint false-positived on hippo's own prose: {offenders}"


# --------------------------------------------------------------------------- #
# AC2: grounding — hunk overlap OR rationale
# --------------------------------------------------------------------------- #


def test_grounded_by_hunk_passes():
    body = (
        "the user prefers tabs for indentation.\n\n"
        "```\n- def f():\n-  return 1\n+def f():\n+\treturn 1  # tabs\n```\n"
    )
    assert PL.find_ungrounded(body) == []           # "tabs" overlaps the fenced hunk


def test_synthesized_no_support_is_flagged():
    body = "the user always wants aggressive caching everywhere."
    assert PL.find_ungrounded(body) != []


def test_rationale_only_passes():
    body = "the user prefers dark mode."
    assert PL.find_ungrounded(body, rationale="from session abc; user said so 2026-07-16") == []


def test_committed_rationale_line_grounds():
    body = "the owner insists on HTTP/1.1 pushes.\n\nRationale: from session xyz; git-push-http2 lesson"
    assert PL.find_ungrounded(body) == []


def test_prescription_inside_a_fence_is_not_flagged():
    # a verbatim quote of the user's words is transcription, not synthesis.
    body = "recorded the exchange:\n\n```\nuser: the user always wants X\n```\n"
    assert PL.prescriptive_spans(body) == []
    assert PL.find_ungrounded(body) == []


# --------------------------------------------------------------------------- #
# AC3: audit-sweep classification
# --------------------------------------------------------------------------- #


def test_classify_three_ways():
    assert PL.classify("the corpus has 50 memories and an index") == "observation"
    assert PL.classify("the user always wants X with no support") == "ungrounded-prescription"
    assert PL.classify("the user prefers Y", rationale="user said so") == "grounded"


def test_scan_corpus_counts_and_names(tmp_path):
    md = str(tmp_path / "mem")
    os.makedirs(md)
    files = {
        "obs": "---\nname: obs\ndescription: \"d\"\n---\nthe index has 3 backends.\n",
        "bad": "---\nname: bad\ndescription: \"d\"\n---\nthe user always wants no config at all.\n",
        "good": "---\nname: good\ndescription: \"d\"\n---\nthe owner prefers X.\n\nRationale: from session s\n",
    }
    for stem, txt in files.items():
        with open(os.path.join(md, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(txt)
    rep = PL.scan_corpus(md)
    assert rep["total"] == 3
    assert rep["observation"] == 1 and rep["grounded"] == 1 and rep["ungrounded"] == 1
    assert [i["name"] for i in rep["ungrounded_items"]] == ["bad"]


# --------------------------------------------------------------------------- #
# AC2 seam: write_memory warns (warn-only), never blocks
# --------------------------------------------------------------------------- #


def test_write_memory_warns_on_ungrounded_but_writes(tmp_path):
    from memory import new_memory as NM

    md = str(tmp_path / "repo" / ".claude" / "memory")
    os.makedirs(md)
    res = NM.write_memory(
        "prescriptive", "a fact", "project",
        "the user always wants everything cached forever", memory_dir=md,
    )
    assert res["created"] is True
    assert any("ungrounded prescription" in w.lower() for w in res["warnings"])


def test_write_memory_rationale_suppresses_the_warning(tmp_path):
    from memory import new_memory as NM

    md = str(tmp_path / "repo" / ".claude" / "memory")
    os.makedirs(md)
    res = NM.write_memory(
        "grounded", "a fact", "project", "the user prefers dark mode",
        memory_dir=md, rationale="from session abc; the user stated this in chat",
    )
    assert res["created"] is True
    assert not any("ungrounded prescription" in w.lower() for w in res["warnings"])


# --------------------------------------------------------------------------- #
# AC4: the confidence-never-ranking discipline — lint stays off route/neighbors/recall
# --------------------------------------------------------------------------- #


def test_check_candidate_never_carries_a_prescription_field(tmp_path):
    from memory import new_memory as NM

    md = str(tmp_path / "mem")
    os.makedirs(md)
    decision = NM.check_candidate(
        "p", "the user always wants X", "project",
        "the user always wants X with no support", memory_dir=md,
    )
    # route stays add (dup-only), and no prescription lint result leaks into the decision
    assert decision["route"] == "add"
    assert "prescription" not in " ".join(decision).lower()
    assert set(decision) == {"route", "neighbors", "rule_neighbors", "baseline", "note", "ticket"}


def test_prescription_lint_never_imported_by_hot_path():
    """AC4 AST pin: neither recall nor build_index (the hot path) may import the lint — it is
    a write-plane/audit/doctor concern only, never a ranking input."""
    from memory import build_index as B
    from memory import recall as R

    for mod in (R, B):
        tree = ast.parse(inspect.getsource(mod))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
            elif isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
        assert not any("prescription_lint" in n for n in names), (mod.__name__, names)


def test_check_candidate_source_never_calls_the_lint():
    """AC4: check_candidate must not reference prescription_lint — SEN-3 fires at write_memory
    and audit/doctor only, so lint output can never reach a route/neighbor decision."""
    from memory import new_memory as NM

    src = inspect.getsource(NM.check_candidate)
    assert "prescription_lint" not in src and "find_ungrounded" not in src
