"""REL-3: RELEASING.md's factual claims are pinned to the values they describe.

RELEASING.md is the protocol doc a release operator reads under time pressure — and it
had already rotted once: step 5 said "All six CI checks" for two releases after SEC-8
made the required set seven (secret-scan never joined the sentence), the exact class
that let STABILITY.md misstate its own FROZEN corpus_format for eight releases before
DOC-16 pinned it (see test_stability_doc.py's docstring for that founding incident).
This is the same lint one document over.

Scope is deliberately FACTS, not policy (the DOC-16 rule):
  - the stated required-check count and names must match `.github/workflows/ci.yml` —
    derived by PARSING the workflow (the QUA-12 ruleset comment, the ruleset of record
    applied to `main` 2026-07-19, cross-checked against the jobs' evaluated `name:`
    fields with their matrices expanded), never a second hardcoded list;
  - release-required must stay distinguished from present-on-PR-boards: a lane the
    workflow gates to `pull_request` (memory-review) or `schedule` (scale) — or one the
    ruleset simply omits (resolution) — must not be named in the doc's required
    enumeration;
  - every backticked repo path the doc cites (version manifests, the version-sync test,
    the tag-time release workflow) must exist in the tree.
Which checks ARE required stays a human/GitHub-settings decision; no test makes that
call. A claim the lint cannot see either joins the pins here or gets reworded so it is
not a bare trusted number (the DOC-16 scope rule).
"""

from __future__ import annotations

import itertools
import os
import re

import yaml

from memory import surfaces

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(surfaces.__file__))))
_RELEASING = os.path.join(_REPO_ROOT, "RELEASING.md")
_CI = os.path.join(_REPO_ROOT, ".github", "workflows", "ci.yml")

_FIX = (
    "RELEASING.md is the release protocol read under time pressure — a wrong check count "
    "or name there directly enables the next red-board-shaped miss. Update the doc (or "
    "ci.yml's ruleset comment), never the assertion."
)

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _doc() -> str:
    with open(_RELEASING, encoding="utf-8") as fh:
        return fh.read()


def _flat() -> str:
    """The doc with whitespace runs collapsed — claims are markdown-wrapped across line
    breaks, and a naive per-line regex silently matching NOTHING is the exact failure
    mode this file exists to prevent (test_stability_doc._flat's lesson)."""
    return re.sub(r"\s+", " ", _doc())


def _ci_text() -> str:
    with open(_CI, encoding="utf-8") as fh:
        return fh.read()


def _expanded_job_names() -> dict:
    """``{evaluated job name -> job id}`` for every job in ci.yml, matrices expanded.

    The evaluated ``name:`` field is the string GitHub reports to branch protection, so
    it is the unit both the ruleset and this lint speak in. Matrix templates are expanded
    over the declared value lists (the workflow uses plain value matrices — no
    include/exclude — and this helper asserts that stays true rather than guessing).
    """
    workflow = yaml.safe_load(_ci_text())
    out: dict = {}
    for job_id, job in workflow["jobs"].items():
        template = job.get("name", job_id)
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        assert "include" not in matrix and "exclude" not in matrix, (
            f"ci.yml job {job_id} grew a matrix include/exclude — teach "
            "_expanded_job_names about it before relying on this lint"
        )
        keys = [k for k in matrix if f"${{{{ matrix.{k} }}}}" in template]
        if not keys:
            out[template] = job_id
            continue
        for combo in itertools.product(*(matrix[k] for k in keys)):
            name = template
            for k, v in zip(keys, combo):
                name = name.replace(f"${{{{ matrix.{k} }}}}", str(v))
            out[name] = job_id
    return out


def _ruleset_required_names() -> list:
    """The required full check names, parsed from ci.yml's QUA-12 ruleset comment — the
    ruleset of record (applied to `main` 2026-07-19; Q2 r5). Names sit on the indented
    comment lines after the 'Require these status checks to pass' anchor, `·`-separated.
    """
    text = _ci_text()
    m = re.search(
        r"Require these status checks to pass.*?name:` field, not the job id\):\n(.*?)\n#\s*\(the ",
        text,
        re.DOTALL,
    )
    assert m, (
        "ci.yml's QUA-12 ruleset comment lost its required-checks block (or was "
        "reworded) — it is the ruleset of record; update this parser deliberately, "
        "with the comment, never by accident"
    )
    names = []
    for line in m.group(1).splitlines():
        stripped = line.lstrip("#").strip()
        if not stripped:
            continue
        names.extend(part.strip() for part in stripped.split("·") if part.strip())
    assert names, "the ruleset comment's required-checks block parsed to zero names"
    return names


def _base(name: str) -> str:
    """A check's base job name — the bit before its parenthetical (`hermetic (…)` →
    `hermetic`), which is how the prose doc refers to lanes."""
    return name.split(" (")[0].strip()


def test_ruleset_comment_names_real_jobs_and_excludes_gated_lanes():
    """The comment (ruleset of record) can't rot against the jobs it names: every
    required name must be an actual evaluated job name, and no lane the workflow gates
    off PR/push boards (pull_request-only, schedule-only) may be listed as required."""
    jobs = _expanded_job_names()
    required = _ruleset_required_names()
    unknown = [n for n in required if n not in jobs]
    assert not unknown, (
        f"ci.yml's ruleset comment requires check name(s) {unknown} that no job's "
        f"evaluated name matches — the comment and the jobs drifted apart. Known names: "
        f"{sorted(jobs)}. {_FIX}"
    )
    workflow = yaml.safe_load(_ci_text())
    for name in required:
        cond = workflow["jobs"][jobs[name]].get("if", "")
        assert "pull_request" not in cond and "schedule" not in cond, (
            f"required check {name!r} is event-gated ({cond!r}) — a required check that "
            f"never reports on a push board wedges every merge. {_FIX}"
        )


def test_doc_states_the_required_check_count_and_names():
    """Step 5's enumeration — the sentence that rotted ('All six') — pinned to ci.yml."""
    required = _ruleset_required_names()
    m = re.search(r"All (\w+) required CI checks must be green: (.*?)\. \(", _flat())
    assert m, (
        "RELEASING.md no longer states the required-check enumeration ('All <n> required "
        f"CI checks must be green: …'). Restore or reword it AND this pattern. {_FIX}"
    )
    stated_count = _WORD_NUMBERS.get(m.group(1).lower()) or int(m.group(1))
    assert stated_count == len(required), (
        f"RELEASING.md says 'All {m.group(1)}' required checks but ci.yml's ruleset "
        f"requires {len(required)}: {required}. {_FIX}"
    )
    enumeration = m.group(2)
    for base in sorted({_base(n) for n in required}):
        assert base in enumeration, (
            f"RELEASING.md's required-check enumeration omits the required lane "
            f"{base!r} (the 'All six' rot class — secret-scan was the omission). {_FIX}"
        )


def test_doc_never_names_a_nonrequired_lane_as_required():
    """Release-required vs present-on-PR-boards: memory-review sits on every PR board
    but is NOT release-required; naming it (or resolution/scale) in the required
    enumeration would teach the operator to wait on the wrong gate."""
    jobs = _expanded_job_names()
    required = set(_ruleset_required_names())
    non_required_bases = {_base(n) for n in jobs} - {_base(n) for n in required}
    m = re.search(r"All \w+ required CI checks must be green: (.*?)\. \(", _flat())
    assert m, f"required-check enumeration missing (see the count test). {_FIX}"
    named = [b for b in sorted(non_required_bases) if b in m.group(1)]
    assert not named, (
        f"RELEASING.md's required enumeration names non-required lane(s) {named} — "
        f"required per ci.yml's ruleset: {sorted(required)}. {_FIX}"
    )


def test_doc_hermetic_multiplier_and_matrix_match_the_workflow():
    """'hermetic ×4 ({ubuntu, macos} × {py3.10, py3.12})' — the multiplier and both
    matrix axes are numbers a reader trusts; pin them to the expanded matrix."""
    required = _ruleset_required_names()
    hermetic = [n for n in required if _base(n) == "hermetic"]
    flat = _flat()
    m = re.search(r"hermetic ×(\d+)", flat)
    assert m, f"RELEASING.md no longer states the hermetic ×N multiplier. {_FIX}"
    assert int(m.group(1)) == len(hermetic), (
        f"RELEASING.md says hermetic ×{m.group(1)} but the ruleset requires "
        f"{len(hermetic)} hermetic checks: {hermetic}. {_FIX}"
    )
    workflow = yaml.safe_load(_ci_text())
    matrix = workflow["jobs"]["hermetic"]["strategy"]["matrix"]
    for os_value in matrix["os"]:
        short = str(os_value).split("-")[0]
        assert short in flat, (
            f"RELEASING.md's hermetic matrix omits OS {short!r} (ci.yml runs "
            f"{matrix['os']}). {_FIX}"
        )
    for py_value in matrix["python"]:
        assert f"py{py_value}" in flat, (
            f"RELEASING.md's hermetic matrix omits python py{py_value} (ci.yml runs "
            f"{matrix['python']}). {_FIX}"
        )


def test_every_backticked_repo_path_in_the_doc_exists():
    """The meta-test: every `backticked` slash-bearing file path the doc cites — the
    version manifests, the version-sync gate, the tag-time release workflow, the CI
    workflow — must exist in the tree. A future path claim joins this sweep for free;
    bare filenames (`release.yml` as shorthand) and commands are out of scope."""
    doc = _doc()
    claimed = [
        tok
        for tok in re.findall(r"`([^`\s]+)`", doc)
        if "/" in tok and re.search(r"\.(?:ya?ml|py|json|md|sh)$", tok)
    ]
    assert claimed, "RELEASING.md cites zero repo paths — the doc was gutted or reworded"
    missing = [p for p in claimed if not os.path.isfile(os.path.join(_REPO_ROOT, p))]
    assert not missing, (
        f"RELEASING.md cites path(s) that do not exist in the tree: {missing}. A rename "
        f"missed the doc (update it) or the path is a typo. {_FIX}"
    )
    for expected in (
        os.path.join("plugin", ".claude-plugin", "plugin.json"),
        os.path.join(".claude-plugin", "marketplace.json"),
        os.path.join(".github", "workflows", "release.yml"),
    ):
        assert expected.replace(os.sep, "/") in claimed, (
            f"RELEASING.md no longer cites {expected} — the version-manifest pair and "
            f"the tag-time workflow are the DOC-7 spine; restore the claim. {_FIX}"
        )
