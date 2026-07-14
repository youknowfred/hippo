"""Secret-pattern lint for memory text (SEC-2 — warn at write time + corpus scan in doctor).

Memories are committed to git and recalled forever; a credential pasted into a body — an
AWS key from a stack trace, a GitHub token from a curl example, a private key block — is
committed to shared history and re-injected into context on every recall. Nothing scanned
for that at creation, and no corpus-wide check existed. This module is the ONE detector both
surfaces share: ``new_memory`` runs it over the rendered text at write time (warn, never
block — see below), and ``/hippo:doctor`` runs it over every memory file for a corpus-wide
sweep. One pattern list, one code path — never two drifting copies of the regex set.

Design constraints (all load-bearing):
  - WARN, NEVER BLOCK. A write-time secret match is agent-gated, not a refusal: the write
    ALWAYS proceeds if otherwise valid, and the match is surfaced as a ``warnings`` entry so
    the calling skill/agent decides what to do (report-then-act). Silently refusing a write
    would be a legibility failure (the invariant: no silent no-ops), and blocking on a
    false positive would be worse than missing an exotic secret — see next point.
  - HIGH PRECISION over recall. False positives on ordinary prose (a memory ABOUT secrets,
    a hex sha, a base64 snippet) are worse than missing an unusual secret format, because a
    noisy warning trains the agent to ignore it. The pattern list is deliberately SMALL and
    anchored to high-signal shapes: known token PREFIXES (AKIA…, ghp_/gho_/…) and PEM
    ``BEGIN … PRIVATE KEY`` blocks. The optional entropy catch-all is conservative (long,
    mixed-class, no whitespace) so normal words/hashes don't trip it.
  - NEVER ECHO THE SECRET. A warning names the KIND of match ("possible AWS access key
    detected"), never the matched substring — the warning text itself lands in logs / the
    agent transcript, and re-emitting the credential there would defeat the purpose.
  - No new dependency. Pure ``re`` + a tiny hand-rolled entropy heuristic.

Remediation, appended once to any non-empty finding set: if it's a real secret, remove it,
rotate the credential, and scrub it from git history before committing — pointing at SEC-4's
full purge procedure (``plugin/memory/README.md``, "Purging a memory") for the exact recipe.
"""

from __future__ import annotations

import math
import os
import re
from typing import List

# Remediation pointer appended to any non-empty warning set. SEC-4: names the purge doc so the
# same pointer reaches BOTH surfaces that interpolate this constant — write-time new_memory
# warnings (scan_with_remediation) and doctor's check_secrets — with one source of truth.
REMEDIATION = (
    "if this is a real secret, remove it, rotate the credential, and scrub it from git "
    "history before committing — see plugin/memory/README.md ('Purging a memory') for the "
    "full purge procedure"
)

# High-signal, anchored patterns → the human-readable KIND reported (never the match itself).
# SMALL by design: each shape is one a real credential has and ordinary prose does not. Every
# addition here is a KNOWN token PREFIX (or a structural shape like a JWT / PEM block / DB URI
# credential) with a length/charset floor tuned so hyphenated package names, env-var names, and
# hex/base64 prose do NOT trip it — high precision over recall (SEC-8). Order matters only where
# noted: the Anthropic `sk-ant-` shape is listed before the broader OpenAI `sk-` shape, and the
# OpenAI body is dash-free so it can never match an `sk-ant-…` key.
_PATTERNS = [
    # AWS access key IDs: the AKIA prefix + 16 more upper-alnum (20 total).
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "possible AWS access key detected"),
    # GitHub tokens: classic ghp_/gho_/ghu_/ghs_/ghr_ (+>=36) and fine-grained github_pat_.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{36,})\b"),
     "possible GitHub token detected"),
    # Slack: bot/user/app/refresh tokens (xoxb-/xoxa-/xoxp-/xoxr-/xoxs-) + incoming-webhook URLs.
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"), "possible Slack credential detected"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_+-]{20,}"),
     "possible Slack credential detected"),
    # Google API keys: the AIza prefix + 35 url-safe chars (39 total).
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "possible Google API key detected"),
    # Stripe secret/restricted keys: sk_live_/rk_live_/sk_test_/rk_test_ + >=16.
    (re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b"), "possible Stripe secret key detected"),
    # Anthropic API keys: sk-ant-… (LISTED BEFORE the broader OpenAI sk- shape below).
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "possible Anthropic API key detected"),
    # OpenAI API keys: sk-[proj-/svcacct-/admin-]?<>=20 alnum. Dash-free body → never sk-ant-.
    (re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9]{20,}\b"), "possible OpenAI API key detected"),
    # JWTs: three base64url segments, the first two JSON headers (eyJ…).
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "possible JWT detected"),
    # npm automation tokens: npm_ + >=36 alnum (env vars like npm_config_* break the run early).
    (re.compile(r"\bnpm_[A-Za-z0-9]{36,}\b"), "possible npm token detected"),
    # PyPI upload tokens: pypi- + a long base64 macaroon body (dash/underscore-free → not pypi-<pkg>).
    (re.compile(r"\bpypi-[A-Za-z0-9+/]{40,}"), "possible PyPI token detected"),
    # DB connection strings carrying an inline user:password@ credential.
    (re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://[^\s:/@]+:[^\s:/@]+@"),
     "possible connection string with credentials detected"),
    # PEM private-key blocks (RSA/EC/OPENSSH/PGP/plain).
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), "possible private key block detected"),
]

# Entropy catch-all thresholds. Conservative so normal prose / hex shas / short base64 pass:
# a token must be long AND draw from a mixed character class AND clear a Shannon-entropy bar AND
# contain one long CONTIGUOUS opaque run (see _ENTROPY_CORE_MIN_LEN below for why).
_ENTROPY_MIN_LEN = 32
_ENTROPY_MIN_BITS = 4.0
_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{%d,}" % _ENTROPY_MIN_LEN)

# The precision gate that keeps the catch-all off structured prose (SEC precision fix). The
# token class above spans `/ = _ -` because a real secret CAN contain them (standard base64 uses
# `/` and `=`; base64url uses `-` and `_`). But those same chars are the SEPARATORS in the three
# shapes that were tripping the catch-all as one long "token": filesystem paths
# (`/Library/Caches/hippo-memory/fastembed`), `KEY=value` env-var assignments
# (`CLAUDE_CODE_ENTRYPOINT=claude-desktop`), slash-joined name lists (`Slack/Google/Stripe/…`),
# and hyphenated identifiers like model names (`paraphrase-multilingual-MiniLM-L12-v2`). Each
# clears the ≥32-char / mixed-class / entropy≥4.0 bar only because concatenating several diverse
# SHORT segments across separators inflates aggregate character diversity — the exact noisy-
# warning anti-pattern the module header warns against (a false positive trains the agent to
# ignore the warning). We can't simply drop `/ = _ -` from the class: that would fragment a real
# base64/base64url secret and cut recall. Instead we split the token on those separators and
# require its LONGEST contiguous opaque run to reach this floor. A real secret is ONE long high-
# entropy run; structured text is many short low-entropy segments, so its longest run stays well
# under the floor (paths/env/lists/model-names top out ~12 chars). `+` is NOT a separator (it is
# genuine base64 content), so a `+`-bearing blob is still measured whole. 20 sits comfortably
# above the ~12-char structured-segment ceiling yet below the run length a genuine ≥32-char
# secret retains even when a lone base64/base64url separator splits it near the middle.
_ENTROPY_CORE_MIN_LEN = 20
_ENTROPY_SEP_RE = re.compile(r"[/=_-]+")
_ENTROPY_MSG = "possible high-entropy secret detected"


def _longest_core_run(token: str) -> int:
    """Length of the longest contiguous opaque run in ``token`` (split on `/ = _ -`).

    The discriminator behind the entropy catch-all's precision: a real secret is one long
    high-entropy run, while a path / KEY=value / slash-list / hyphenated identifier is short
    low-entropy segments joined by those separators. Returns 0 for an all-separator string.
    """
    return max((len(seg) for seg in _ENTROPY_SEP_RE.split(token)), default=0)


def _shannon_entropy(s: str) -> float:
    """Shannon entropy (bits/char) of ``s``. 0.0 for empty. Never raises."""
    if not s:
        return 0.0
    counts: dict = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _has_mixed_classes(s: str) -> bool:
    """True when ``s`` mixes at least two of {lower, upper, digit} — a real-token shape.

    This keeps the entropy catch-all off a long all-lowercase WORD (one class), which is what
    it is for.

    DOC-15 — what it does NOT do, despite an earlier version of this docstring saying so: it
    does not keep hex out. A lowercase hex sha mixes letters and digits, so this returns
    **True** for one and the gate never fires. What actually keeps a bare sha quiet is the
    entropy bar (hex is a 16-symbol alphabet, so its Shannon entropy lands under the
    threshold) — and that protection is incidental and thin: prefix the same sha with a label
    and ``content_digest=<sha>`` DOES trip, because the entropy is scored over the whole token
    while the run-length check is scored over its longest segment. Two different strings, one
    verdict. SEC-16 fixes that; this docstring no longer claims a gate that isn't there.
    """
    classes = 0
    if any(c.islower() for c in s):
        classes += 1
    if any(c.isupper() for c in s):
        classes += 1
    if any(c.isdigit() for c in s):
        classes += 1
    return classes >= 2


def scan_text(text: str, *, entropy: bool = True) -> List[str]:
    """Return human-readable warnings for secret-looking patterns in ``text``. [] when clean.

    Each entry names the KIND of match, never the matched substring (the warning text is
    logged / shown to the agent — echoing the credential there would leak it). Deduplicated
    and order-stable. Never raises: a scan failure returns [] rather than blocking a write.
    This is the SINGLE detector — ``new_memory`` (write-time warn) and doctor (corpus sweep)
    both call it, so the pattern set never forks into two drifting copies.

    ``entropy`` gates the soft high-entropy catch-all. It stays ON for the memory surfaces
    (write-time warn, doctor corpus sweep) where a body is short prose and an unknown-format
    credential is worth a nudge. The SEC-8 repo/pack CI gate passes ``entropy=False`` so it
    fails only on a DETERMINISTIC known-credential shape — over a whole source tree the
    entropy heuristic would flag ordinary base64/minified/hash blobs, and a gate must be
    explainable ("this file has an AWS key"), not probabilistic.
    """
    try:
        found: List[str] = []
        for pattern, message in _PATTERNS:
            if pattern.search(text) and message not in found:
                found.append(message)
        # Entropy catch-all: only flag a long, mixed-class, high-entropy token that also holds
        # one long contiguous opaque run (so a separator-joined path/env/list/identifier whose
        # entropy comes from short segments does NOT trip) — and only if a prefix pattern above
        # didn't already flag this text (avoid a redundant second line).
        if entropy and _ENTROPY_MSG not in found:
            for token in _ENTROPY_TOKEN_RE.findall(text):
                if (
                    _has_mixed_classes(token)
                    and _shannon_entropy(token) >= _ENTROPY_MIN_BITS
                    and _longest_core_run(token) >= _ENTROPY_CORE_MIN_LEN
                ):
                    found.append(_ENTROPY_MSG)
                    break
        return found
    except Exception:
        return []


def scan_with_remediation(text: str) -> List[str]:
    """``scan_text`` plus a trailing generic remediation line when anything matched. [] if clean.

    The remediation is appended ONCE (not per match) so the caller can hand the whole list to
    the agent as the ``warnings`` field. Empty in, empty out — a clean scan adds no noise.
    """
    warnings = scan_text(text)
    if warnings:
        warnings.append(REMEDIATION)
    return warnings


def scan_corpus(memory_dir: str) -> List[dict]:
    """Corpus-wide sweep: one ``{"file", "warnings"}`` entry per memory file with a match.

    Applies the SAME detector to every memory file's full text (frontmatter + body). Backs
    doctor's corpus-scan check. Files that match get a ``warnings`` list (the KIND lines only,
    no remediation — doctor prints the pointer once for the whole run). Clean files are
    omitted, so ``[]`` means the corpus is clean. Never raises; an unreadable file is skipped.
    """
    findings: List[dict] = []
    try:
        from .provenance import _iter_memory_files

        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            warnings = scan_text(text)
            if warnings:
                findings.append({"file": os.path.basename(path), "warnings": warnings})
    except Exception:
        return findings
    return findings


# --------------------------------------------------------------------------- #
# SEC-8 — the CI secret-scan gate over shipped packs + repo.
#
# The corpus sweep above (scan_corpus) protects a live install's OWN memory dir. This gate
# protects what HIPPO SHIPS: the starter packs in plugin/assets/, the docs, the source — so a
# credential can never reach a user's tree through a release. It reuses the ONE detector with
# entropy OFF (deterministic, explainable failures) and reads only the TRACKED tree.
# --------------------------------------------------------------------------- #

# Never scanned by the repo gate: the test suite ships INTENTIONAL detector vectors (fake
# AKIA…/ghp_… tokens exist so the detector itself can be tested), and caches/venvs are not
# shipped content. A real secret belongs in none of these — a leaked credential in a fixture
# is not recalled or re-injected the way a shipped memory is.
_REPO_SCAN_SKIP_DIRS = ("tests/", ".git/", ".venv/", "plugin/.venv/", ".claude/.memory-index/")

# Extensions whose bytes are not scannable text (a UnicodeDecodeError also skips these, but
# checking the extension first avoids reading a large binary just to discard it).
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar", ".onnx",
    ".bin", ".pyc", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".model",
}


def scan_files(paths) -> List[dict]:
    """Prefix-only scan (entropy OFF) over ``paths``; one ``{"file", "warnings"}`` per hit.

    The CI-gate core: the repo walk and any pack scan both feed it a file list. Never raises;
    a binary or unreadable file is skipped (not scannable text), so ``[]`` means clean.
    """
    findings: List[dict] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable → not scannable text
        warnings = scan_text(text, entropy=False)
        if warnings:
            findings.append({"file": path, "warnings": warnings})
    return findings


def _iter_repo_files(root: str) -> List[str]:
    """Absolute paths of TRACKED text files under ``root``, minus the never-shipped skip dirs.

    Prefers ``git ls-files`` (tracked only — exactly the shipped/committed surface); falls back
    to a filtered ``os.walk`` when git is unavailable (an extracted tarball, a CI shallow edge).
    """
    import subprocess

    rel: List[str] = []
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        ).stdout
        rel = [p for p in out.split("\0") if p]
    except Exception:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "node_modules")]
            for fn in filenames:
                rel.append(os.path.relpath(os.path.join(dirpath, fn), root))

    files: List[str] = []
    for r in rel:
        norm = r.replace(os.sep, "/")
        if any(norm == d.rstrip("/") or norm.startswith(d) for d in _REPO_SCAN_SKIP_DIRS):
            continue
        if os.path.splitext(norm)[1].lower() in _BINARY_EXTS:
            continue
        files.append(os.path.join(root, r))
    return files


def main(argv=None) -> int:
    """CLI: ``python -m memory.secrets --repo [ROOT]`` — the SEC-8 CI secret-scan gate.

    Scans the tracked tree (shipped packs + repo, tests excluded) for known credential shapes.
    Prints each finding by KIND (never the secret) and returns 1 on any hit, 0 when clean —
    so a CI step can gate on the exit code.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="memory.secrets",
        description="Secret-scan the shipped/committed tree (SEC-8). Exit 1 on any finding.",
    )
    ap.add_argument("--repo", metavar="ROOT", default=".", help="repo root to scan (default: .)")
    args = ap.parse_args(argv)

    files = _iter_repo_files(args.repo)
    findings = scan_files(files)
    if findings:
        print(f"secret-scan: {len(findings)} file(s) with possible committed secrets:")
        for f in findings:
            print(f"  {f['file']}: {', '.join(f['warnings'])}")
        print(REMEDIATION)
        return 1
    print(f"secret-scan: clean ({len(files)} tracked files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
