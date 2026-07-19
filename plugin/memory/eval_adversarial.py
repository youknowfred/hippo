"""SEN-4: the adversarial coverage report — acceptance-TEST the shipped trust spine.

Decomposed out of ``eval_recall.py`` (pure code motion, ED5R-3 — the façade re-imports
every name here, so ``memory.eval_recall.adversarial_report`` keeps resolving).

The v0.8.0 spine (SEC-5/6/7) shipped with no adversarial regression harness; a defense
with no poisoned fixtures is unproven. This report loads a poisoned-memory fixture corpus
(memory .md files under .audit-fixtures/adversarial/, NOT hard_set rows) and, per poisoned
fixture, emits FIVE deterministic booleans BY DRIVING THE SHIPPED CODE — no re-implemented
spine, no LLM:
  payload_crossed_raw     — the payload appears verbatim in recall.format_results output
                            (reached context, as demarcated DATA — worded as "crossed", not
                            "injection success")
  sec6_quarantine_withheld— a post-consent drift of this file is WITHHELD by a real recall()
                            through the SEC-6 quarantine path (None when the fixture declares
                            no clean pre-poison state — that vector isn't being tested)
  sec5_consent_byte_equal — trust.corpus_consent_sample shows this file's injectable
                            description byte-equal to recall.inject_description (SEC-5 parity)
  threat_lint_flagged     — threat_lint.scan_tier_a (SEN-2) flags the payload
  admitted                — the poisoned memory was admitted (knee/floor/MMR) for its query

Report-only, skip-if-no-fixture, never gates CI on its own. ZERO eval_recall loader change
(this is a standalone mode, like --floor-sweep). The always-on spotlighting envelope is CUT
(a per-session delimiter cannot REDUCE crossing — deferred as boundary-spoof-resistance).
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from .provenance import resolve_dirs

_ADVERSARIAL_DIRNAME = "adversarial"
_ADVERSARIAL_MANIFEST = "manifest.json"


def _adversarial_fixture_dir(memory_dir: Optional[str] = None) -> Optional[str]:
    """``.audit-fixtures/adversarial/`` when it exists (with a manifest), else None (skip)."""
    try:
        if memory_dir is None:
            memory_dir, _ = resolve_dirs()
        d = os.path.join(memory_dir, ".audit-fixtures", _ADVERSARIAL_DIRNAME)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, _ADVERSARIAL_MANIFEST)):
            return d
    except Exception:
        return None
    return None


def _sec6_withheld_via_real_recall(name: str, clean_text: str, poisoned_text: str, query: str) -> bool:
    """Drive the REAL SEC-6 path: consent to the clean file, drift it to poisoned, recall.

    Builds a throwaway git repo carrying ONLY the clean version of ``name``, marks it trusted
    (stamping the consent fingerprint over the clean bytes), then overwrites it with the
    poisoned bytes. Runs a real memory_dir recall (index is None → the SEC-6 quarantine branch
    is live) TWICE for a SOUND verdict: with quarantine OFF (HIPPO_TRUST_ALL) the drifted file
    must be admitted (proving the query genuinely matches it), and with quarantine ON it must
    be WITHHELD. Returns True only when both hold — so "withheld" can never mean "just didn't
    rank." Never raises; False on any setup failure (fail-open in the REPORT is honest — it
    means "not demonstrated," not "safe").
    """
    import subprocess
    import tempfile

    from . import trust
    from .build_index import build_index
    from .recall import recall

    tmp = tempfile.mkdtemp(prefix="hippo-adv-sec6-")
    try:
        md = os.path.join(tmp, ".claude", "memory")
        os.makedirs(md)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True, env=env)
        fpath = os.path.join(md, f"{name}.md")
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(clean_text)
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True, capture_output=True, env=env)
        subprocess.run(["git", "-C", tmp, "commit", "-qm", "clean"], check=True, capture_output=True, env=env)
        idx = os.path.join(tmp, ".claude", ".memory-index")
        build_index(md, idx)
        # Consent to the CLEAN corpus (stamps the per-file sha256 baseline).
        trust.mark_trusted(tmp, memory_dir=md, origin="review")
        # DRIFT: overwrite with the poisoned bytes (bytes now differ from the consent baseline).
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(poisoned_text)
        build_index(md, idx)

        def _recalls(trust_all: bool) -> set:
            prior = os.environ.get("HIPPO_TRUST_ALL")
            if trust_all:
                os.environ["HIPPO_TRUST_ALL"] = "1"
            else:
                os.environ.pop("HIPPO_TRUST_ALL", None)
            try:
                return {r.get("name") for r in recall(query, k=10, memory_dir=md, index_dir=idx, repo_root=tmp)}
            finally:
                if prior is None:
                    os.environ.pop("HIPPO_TRUST_ALL", None)
                else:
                    os.environ["HIPPO_TRUST_ALL"] = prior

        admitted_no_quarantine = name in _recalls(trust_all=True)   # quarantine OFF
        withheld_with_quarantine = name not in _recalls(trust_all=False)  # quarantine ON
        return admitted_no_quarantine and withheld_with_quarantine
    except Exception:
        return False
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def adversarial_report(fixture_dir: Optional[str] = None) -> dict:
    """Five-boolean coverage per poisoned fixture, by driving the shipped spine. Report-only.

    ``{"skipped": reason}`` when no fixture corpus exists (absent = skip, never a failure), else
    ``{"rows": [{name, query, payload_crossed_raw, sec6_quarantine_withheld,
    sec5_consent_byte_equal, threat_lint_flagged, admitted}], "totals": {...}}``. Never raises.
    """
    from . import trust
    from .build_index import build_index, load_index
    from .provenance import parse_frontmatter, split_frontmatter
    from .recall import format_results, inject_description, recall
    from .threat_lint import scan_tier_a

    d = fixture_dir or _adversarial_fixture_dir()
    if not d:
        return {"skipped": "no .audit-fixtures/adversarial fixture corpus"}
    try:
        with open(os.path.join(d, _ADVERSARIAL_MANIFEST), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        fixtures = manifest.get("fixtures") if isinstance(manifest, dict) else None
        if not isinstance(fixtures, list) or not fixtures:
            return {"skipped": "adversarial manifest carries no fixtures"}
        # ONE index over the poisoned corpus; supplied-index recall bypasses the trust gate
        # (measures raw ranking behavior against the poison — the crossing/admission arms).
        idx = os.path.join(d, ".idx")
        build_index(d, idx)
        index = load_index(idx)
        rows: List[dict] = []
        for fx in fixtures:
            name = str(fx.get("name") or "").strip()
            query = str(fx.get("query") or "").strip()
            payload = str(fx.get("payload") or "")
            clean_desc = fx.get("clean_description")
            fpath = os.path.join(d, f"{name}.md")
            if not name or not os.path.isfile(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as fh:
                text = fh.read()
            fm = parse_frontmatter(text)
            desc = str(fm.get("description") or "")
            _, body = split_frontmatter(text)
            results = recall(query, k=10, index=index, index_dir=idx, memory_dir=d) if query else []
            rendered = format_results(results)
            crows = trust.corpus_consent_sample(d, stems=[name])
            sec5 = bool(crows) and crows[0].get("description") == inject_description(desc)
            sec6: Optional[bool] = None
            if isinstance(clean_desc, str):
                clean_text = _render_clean_fixture(text, desc, clean_desc)
                sec6 = _sec6_withheld_via_real_recall(name, clean_text, text, query or desc)
            # Scan the UNESCAPED description + body — the actual injectable/recallable surface
            # (the file's frontmatter json-escapes an invisible codepoint to ASCII, but it
            # unescapes back to the real byte on inject; see the SEN-2 write-ticket note).
            rows.append({
                "name": name,
                "query": query,
                "payload_crossed_raw": bool(payload) and payload in rendered,
                "sec6_quarantine_withheld": sec6,
                "sec5_consent_byte_equal": sec5,
                "threat_lint_flagged": bool(scan_tier_a(f"{desc}\n{body}")),
                "admitted": name in {r.get("name") for r in results},
            })
        totals = {
            "n": len(rows),
            "crossed": sum(1 for r in rows if r["payload_crossed_raw"]),
            "sec6_withheld": sum(1 for r in rows if r["sec6_quarantine_withheld"] is True),
            "sec5_byte_equal": sum(1 for r in rows if r["sec5_consent_byte_equal"]),
            "threat_flagged": sum(1 for r in rows if r["threat_lint_flagged"]),
            "admitted": sum(1 for r in rows if r["admitted"]),
        }
        return {"rows": rows, "totals": totals}
    except Exception as exc:
        return {"skipped": f"adversarial report error: {exc}"}


def _render_clean_fixture(poisoned_text: str, poisoned_desc: str, clean_desc: str) -> str:
    """The pre-poison version of a fixture: its description swapped back to the clean one.

    The SEC-6 drift arm needs a clean baseline that differs from the poisoned file only in the
    poisoned span. Swapping the JSON-quoted description line is the minimal, deterministic
    reconstruction (the fixture author states the clean description in the manifest).
    """
    import json as _json

    return poisoned_text.replace(
        f"description: {_json.dumps(poisoned_desc)}",
        f"description: {_json.dumps(clean_desc)}",
        1,
    )
