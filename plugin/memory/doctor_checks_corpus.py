"""Corpus/trust/content checks for the deterministic doctor engine — decomposed out of
``doctor.py`` (DOC-4), which keeps the ordered check registry, the engine, and the CLI.

Steering (GOV-2), format/derivation versions (COR-7, DRV-2), pack drift (TEA-2, RCH-5),
FILL-ME templates (ONB-4), the trust spine (SEC-1, SEC-6), secrets (SEC-2), committed-usage
privacy (SEC-14), the dream-ledger reconcile (DRM-2), and the RET-3 non-English-corpus
heuristic with its Latin-script tables. ``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from .doctor_checks_env import DoctorContext, _iter_memory_files_safe
from .provenance import parse_frontmatter


def check_steering(ctx: DoctorContext) -> Dict[str, str]:
    """GOV-2: how many memories carry an author steer — the control axis made visible.

    Informational (always ok) and manifest-only (no file reads). This line deliberately
    pre-wires the shape MUTE will need when it lands (a muted memory must be COUNTED here,
    never silently gone — inv3); today the only shipped mode is ``pin``.
    """
    try:
        from .build_index import _load_manifest, default_index_dir

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        if manifest is None:
            return {
                "status": "ok",
                "message": "steering: no index built yet — pin counts appear after the first build.",
            }
        pinned = sorted(
            str(e.get("name")) for e in manifest.get("entries", []) if e.get("steer") == "pin"
        )
        if not pinned:
            return {"status": "ok", "message": "steering: no memories pinned."}
        shown = ", ".join(pinned[:5]) + (", …" if len(pinned) > 5 else "")
        return {
            "status": "ok",
            "message": f"steering: {len(pinned)} memory(ies) pinned (bounded recall lift, "
            f"capped — never beats a genuinely stronger match): {shown}.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"steering check failed: {exc}."}


def check_format_version(ctx: DoctorContext) -> Dict[str, str]:
    """BOTH format versions on one line: index ``schema_version`` and corpus format (COR-7).

    INDEX: the persisted manifest's ``schema_version`` vs the running module's
    ``SCHEMA_VERSION``. Since COR-7 this is enforced — every load path treats a mismatched
    manifest as absent, so the state is transient (the next SessionStart refresh performs
    one full rebuild) and needs no operator action. Read via the RAW manifest reader:
    ``_load_manifest`` would hide exactly the mismatch this check exists to name.

    CORPUS: the ``.claude/memory/.format`` marker's declared format vs the plugin's
    ``CORPUS_FORMAT_VERSION`` — BOTH directions. Corpus NEWER than the plugin: this plugin
    misreads/ignores conventions it predates — update the hippo plugin (same signal the
    ``corpus_format`` SessionStart producer carries). Corpus OLDER than the plugin: user
    data needs a MIGRATION, which is doctor-driven and agent-gated per the README's
    "Corpus format versioning" section — hippo never migrates the corpus autonomously, so
    doctor names the exact state and points at the documented path instead.
    """
    try:
        from .build_index import SCHEMA_VERSION, _read_manifest_json, default_index_dir
        from .provenance import CORPUS_FORMAT_VERSION, read_corpus_format

        status = "ok"
        parts: List[str] = []

        manifest = _read_manifest_json(default_index_dir(ctx.memory_dir))
        if manifest is None:
            parts.append("no index built yet — nothing to version-check")
        else:
            on_disk = manifest.get("schema_version")
            if on_disk == SCHEMA_VERSION:
                parts.append(f"index format version current (v{SCHEMA_VERSION})")
            else:
                status = "warn"
                parts.append(
                    f"index format version is v{on_disk}, this plugin writes v{SCHEMA_VERSION} "
                    "— the stale index is ignored (treated as absent) and the next "
                    "SessionStart refresh performs one full rebuild"
                )

        declared = read_corpus_format(ctx.memory_dir)
        if declared == CORPUS_FORMAT_VERSION:
            parts.append(f"corpus format current (v{declared})")
        elif declared > CORPUS_FORMAT_VERSION:
            status = "warn"
            parts.append(
                f"corpus format is v{declared} but this plugin only understands "
                f"v{CORPUS_FORMAT_VERSION} — update the hippo plugin (a newer-format corpus "
                "can carry conventions this version misreads or silently ignores)"
            )
        else:
            status = "warn"
            parts.append(
                f"corpus format is v{declared}, this plugin writes v{CORPUS_FORMAT_VERSION} "
                "— the corpus needs a MIGRATION before newer-format features work; hippo "
                "never migrates automatically — follow the doctor-driven path in "
                "plugin/memory/README.md ('Corpus format versioning')"
            )

        # DRV-2: the derivation is a SEPARATE axis from the shape. A corpus can be format-
        # current and still hold citations produced by an extractor that has since been
        # fixed — which is precisely the state that had no name, and so went unnoticed for
        # 14 minor versions.
        from .provenance import CITATION_DERIVATION_VERSION, read_cite_derivation

        cite = read_cite_derivation(ctx.memory_dir)
        if cite >= CITATION_DERIVATION_VERSION:
            parts.append(f"citation derivation current (v{cite})")
        else:
            status = "warn"
            # DOC-16: NAME the verb. This line used to say "re-derive per memory" and stop —
            # stating a conclusion while never naming the thing that acts on it, which is
            # LIF-4's own complaint one layer up. The remediation loop dead-ended here on
            # both surfaces: the nudge routed to doctor, and doctor routed to nothing.
            parts.append(
                f"citation derivation is v{cite}, this plugin derives "
                f"v{CITATION_DERIVATION_VERSION} — cited_paths in this corpus were produced "
                "by an older extractor (v1 was blind to .json/.tsx/.jsx/.mjs and a leading "
                "./; v2 to extensionless files like Dockerfile), so some memories watch the "
                "wrong file and some are staleness-EXEMPT on an empty cited_paths. Review "
                "with the rederive MCP tool (action='worklist'), apply per memory "
                "(action='one' name=…), then action='stamp' — or in a terminal, "
                "python -m memory.provenance --rederive-worklist / --rederive-one <name> / "
                "--stamp-derivation. It rewrites frontmatter, so it is per-item, "
                "consent-gated and never automatic"
            )

        return {"status": status, "message": "; ".join(parts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"format-version check failed: {exc}."}


def check_pack_drift(ctx: DoctorContext) -> Dict[str, str]:
    """Corpus pack memories whose ``pack_version`` lags the shipped pack manifest's ``version``.

    Uses data that ALREADY exists: seeded pack memories carry ``pack``/``pack_version`` in
    frontmatter (TEA-2), and each shipped pack's ``manifest.json`` carries a ``version``. A
    memory whose recorded ``pack_version`` differs from the shipped pack's version drifted from
    the pack it came from — a legible heads-up (re-seeding is agent-gated, not automatic). Skips
    silently when the shipped packs dir isn't locatable (no CLAUDE_PLUGIN_ROOT). Deterministic:
    iterates memory files in sorted order and reports drifted names sorted.
    """
    try:
        packs_dir = os.path.join(ctx.plugin_root, "assets", "packs") if ctx.plugin_root else ""
        if not packs_dir or not os.path.isdir(packs_dir):
            return {"status": "ok", "message": "pack drift: N/A (shipped packs dir not locatable)."}
        shipped: Dict[str, str] = {}
        for name in sorted(os.listdir(packs_dir)):
            man = os.path.join(packs_dir, name, "manifest.json")
            if not os.path.isfile(man):
                continue
            try:
                with open(man, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and data.get("pack") and data.get("version") is not None:
                    shipped[str(data["pack"])] = str(data["version"])
            except Exception:
                continue
        # RCH-5: packs installed from EXTERNAL sources record their latest-known version
        # in the corpus lockfile — fold those in so drift covers non-shipped packs too.
        # A partially-updated pack then shows drift on exactly its not-yet-updated
        # members (the correct signal, not noise). Shipped manifests win a name clash.
        try:
            from .packs import _load_lockfile

            for pname, entry in (_load_lockfile(ctx.memory_dir).get("packs") or {}).items():
                if isinstance(entry, dict) and entry.get("version") is not None:
                    shipped.setdefault(str(pname), str(entry["version"]))
        except Exception:
            pass
        drifted: List[str] = []
        for path in _iter_memory_files_safe(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    fm = parse_frontmatter(fh.read())
            except Exception:
                continue
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            pack = fm.get("pack") or (meta or {}).get("pack")
            pver = fm.get("pack_version") or (meta or {}).get("pack_version")
            if not pack or pver is None:
                continue
            latest = shipped.get(str(pack))
            if latest is not None and str(pver) != latest:
                name = os.path.splitext(os.path.basename(path))[0]
                drifted.append(f"{name} (pack {pack} v{pver} → v{latest})")
        drifted.sort()
        if not drifted:
            return {"status": "ok", "message": "seeded pack memories are at the shipped versions."}
        return {
            "status": "warn",
            "message": f"{len(drifted)} pack memory(ies) lag the shipped pack version: "
            f"{', '.join(drifted)}. Re-seeding is agent-gated — review before overwriting local edits.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"pack-drift check failed: {exc}."}


def check_fill_me(ctx: DoctorContext) -> Dict[str, str]:
    """Unfilled ``<FILL-ME`` template placeholders anywhere in the corpus (ONB-4, ported here).

    A template memory (usually ``user_role.md``) that was never filled in embeds its placeholder
    text into the recall index and (for ``user`` types) floor-loads it every session. Scans EVERY
    corpus file — the memory files AND the MEMORY.md/MEMORY.full.md floor — for the literal
    ``<FILL-ME`` marker and names each hit BY NAME. Doctor never edits these: the content is facts
    about the user only they can supply. Deterministic: files scanned in sorted order.
    """
    try:
        if not os.path.isdir(ctx.memory_dir):
            return {"status": "ok", "message": "no unfilled <FILL-ME templates (no corpus)."}
        hits: List[str] = []
        for name in sorted(os.listdir(ctx.memory_dir)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(ctx.memory_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    if "<FILL-ME" in fh.read():
                        hits.append(name)
            except Exception:
                continue
        if not hits:
            return {"status": "ok", "message": "no unfilled <FILL-ME templates."}
        return {
            "status": "fail",
            "message": f"{len(hits)} file(s) still contain <FILL-ME placeholders: "
            f"{', '.join(hits)}. Edit each and fill in your own details — the next SessionStart "
            "re-indexes automatically. (Placeholder text is otherwise embedded/floor-loaded.)",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"template check failed: {exc}."}


def check_trust(ctx: DoctorContext) -> Dict[str, str]:
    """Corpus trust state (SEC-1) — is this corpus trusted, and the exact command to trust it.

    Recall is GATED: an untrusted (usually freshly-cloned) corpus injects nothing until this
    machine's user consents. Reports the four trust states deterministically and, on the untrusted
    path, prints the exact ``mark_trusted`` command doctor's consent step runs. Doctor never
    auto-trusts here — the interactive review lives in the SKILL prose; this line only reports the
    state and the command.
    """
    try:
        from . import trust

        if trust.trust_all():
            return {
                "status": "ok",
                "message": "corpus trust bypassed (HIPPO_TRUST_ALL) — recall ungated.",
            }
        gate_root = trust.gate_repo_root(ctx.memory_dir, ctx.repo_root)
        if gate_root is None:
            return {
                "status": "ok",
                "message": "corpus trust: N/A (not a git repo — the gate applies only to cloned "
                "git corpora).",
            }
        if trust.is_trusted(gate_root):
            return {"status": "ok", "message": "corpus trusted — recall active."}
        count = trust.corpus_count(ctx.memory_dir)
        return {
            "status": "warn",
            "message": f"corpus UNTRUSTED ({count} memories) — recall injects nothing from it. "
            "Review the memory names, then trust it: "
            f"python -c \"from memory.trust import mark_trusted; mark_trusted('{gate_root}')\" "
            "(or set HIPPO_TRUST_ALL=1 for CI).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"trust check failed: {exc}."}


def check_trust_drift(ctx: DoctorContext) -> Dict[str, str]:
    """SEC-6: content drift since consent — the always-available re-consent surface.

    Three deterministic states for a TRUSTED, gate-applicable corpus:
      - baseline present, no drift  -> ok.
      - baseline present, drift     -> warn, naming the withheld stems (recall's per-file
        quarantine is ACTIVE on them) + the exact re-consent command. The interactive
        review (show what each changed file would inject — the SEC-5 consent sample —
        then take the explicit yes) lives in the doctor SKILL, same as first consent.
      - baseline ABSENT (a legacy, pre-SEC-6 trust record) -> warn: trust works but
        change detection is OFF until a re-consent stamps a fingerprint.
    ok/N-A on the bypassed / non-git / untrusted paths (``check_trust`` owns those).
    """
    try:
        from . import trust

        if trust.trust_all():
            return {"status": "ok", "message": "trust drift: N/A (HIPPO_TRUST_ALL bypass)."}
        gate_root = trust.gate_repo_root(ctx.memory_dir, ctx.repo_root)
        if gate_root is None:
            return {"status": "ok", "message": "trust drift: N/A (not a git corpus)."}
        if not trust.is_trusted(gate_root):
            return {
                "status": "ok",
                "message": "trust drift: N/A (corpus untrusted — see the trust line).",
            }
        drift = trust.untrusted_changes(gate_root, ctx.memory_dir)
        if not drift.get("baseline"):
            return {
                "status": "warn",
                "message": "trust record has NO content fingerprint (pre-SEC-6 consent) — "
                "recall cannot detect upstream changes to this corpus. Re-consent to stamp "
                "one: python -c \"from memory.trust import mark_trusted; "
                f"mark_trusted('{gate_root}', memory_dir='{ctx.memory_dir}')\"",
            }
        changed, added = drift.get("changed") or [], drift.get("added") or []
        if not changed and not added:
            return {
                "status": "ok",
                "message": "corpus content matches its consent-time fingerprint.",
            }
        names = ", ".join(changed + [f"{n} (new)" for n in added])
        return {
            "status": "warn",
            "message": f"{len(changed)} changed / {len(added)} new memory file(s) since "
            f"consent — recall is WITHHOLDING them: {names}. Review what each would inject "
            "(the consent sample shows descriptions), then re-consent: "
            "python -c \"from memory.trust import mark_trusted; "
            f"mark_trusted('{gate_root}', memory_dir='{ctx.memory_dir}')\"",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"trust-drift check failed: {exc}."}


def check_secrets(ctx: DoctorContext) -> Dict[str, str]:
    """Corpus-wide secret-pattern sweep (SEC-2) — import and call the factored-out detector.

    ``secrets.scan_corpus`` is the SAME detector ``new_memory`` warns with at write time (one
    pattern set, no duplicate regexes). Reports each flagged file BY NAME with its warning
    KIND(s) — never the matched secret text — plus the remediation once. Agent-gated: doctor
    names the files; a human reviews and triggers any purge. Deterministic: ``scan_corpus`` walks
    files in sorted order.
    """
    try:
        from .secrets import REMEDIATION, scan_corpus

        findings = scan_corpus(ctx.memory_dir)
        if not findings:
            return {"status": "ok", "message": "no secret-looking content in the corpus."}
        parts = [f"{f['file']}: {'; '.join(f['warnings'])}" for f in findings]
        return {
            "status": "warn",
            "message": f"{len(findings)} file(s) contain secret-looking content — "
            f"{' | '.join(parts)}. {REMEDIATION}.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"secret scan failed: {exc}."}


def check_threat_lint(ctx: DoctorContext) -> Dict[str, str]:
    """SEN-2 corpus-wide threat sweep: Tier-A payloads per file + the Tier-B aggregate count.

    Tier-A (invisible Unicode / mixed-script confusable / exfil shape / HTML comment) is the
    SURFACED half — named per file, exactly like check_secrets. The Tier-B imperative-grammar
    count is the DARK half: one aggregate number folded into the SAME line (never a per-file
    listing — that would resurrect the surfaced flag the tier holds dark, inv3), the FP-rate
    evidence a dated owner decision needs before graduating it. Single-line message (the
    doctor line-count pin is relative — one check, one line). Never raises.
    """
    try:
        from .telemetry import threat_ledger_aggregate
        from .threat_lint import scan_corpus

        findings = scan_corpus(ctx.memory_dir)
        agg = threat_ledger_aggregate()
        tier_b = f" Tier-B (ledger, measured-only): {agg['rows']} imperative-grammar finding(s)." if agg.get("rows") else ""
        if not findings:
            return {
                "status": "ok",
                "message": f"no Tier-A threat payloads in the corpus.{tier_b}",
            }
        parts = [f"{f['file']}: {'; '.join(f['warnings'])}" for f in findings]
        return {
            "status": "warn",
            "message": f"{len(findings)} file(s) carry Tier-A threat payloads — {' | '.join(parts)}. "
            f"Inspect before they re-inject on recall (HTML comments are lint-only pending the "
            f"ED-3 owner decision).{tier_b}",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"threat scan failed: {exc}."}


def check_ungrounded_prescriptions(ctx: DoctorContext) -> Dict[str, str]:
    """SEN-3 corpus fraction: memories asserting user intent with no rationale/hunk grounding.

    Renders the ungrounded-prescription FRACTION (the sycophancy-amplification rate made
    legible, KPI-5) and names the offending stems so the audit sweep can propose per-item
    fixes (inv4). No persisted per-item field — this reads the corpus each run. Single-line;
    never raises.
    """
    try:
        from .prescription_lint import scan_corpus

        rep = scan_corpus(ctx.memory_dir)
        total = rep.get("total", 0)
        ungrounded = rep.get("ungrounded", 0)
        if not total or not ungrounded:
            return {
                "status": "ok",
                "message": f"no ungrounded prescriptions ({rep.get('grounded', 0)} grounded / "
                f"{total} memories carry no fabricated user-intent claim).",
            }
        names = ", ".join(i["name"] for i in rep.get("ungrounded_items", [])[:6])
        frac = ungrounded / total
        return {
            "status": "warn",
            "message": f"ungrounded-prescription fraction {frac:.2f} — {ungrounded}/{total} "
            f"memories assert user intent with no captured evidence or rationale ({names}). "
            "Run the audit sweep to fix per item (transcribe the WHAT, or cite the WHY).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"ungrounded-prescription scan failed: {exc}."}


def check_committed_usage_privacy(ctx: DoctorContext) -> Dict[str, str]:
    """SEC-14: TEA-5 committed per-user usage summaries are a privacy tradeoff on a shared remote.

    ``.claude/memory/.usage/<user>.json`` is COMMITTED by design (teammates union it before
    judging coldness), so it excepts the gitignore invariant — a per-user record of which
    memories each person recalls. On a repo with a remote (especially a public host) that record
    is shared with anyone who can read it. Warn when such summaries exist AND a remote is
    configured; stay ``ok`` when there are none, or the repo is local-only (nothing to leak to).
    Read-only; never raises.
    """
    try:
        from .provenance import git_remote_info
        from .telemetry import committed_usage_dir

        usage_dir = committed_usage_dir(ctx.memory_dir)
        summaries = (
            [f for f in os.listdir(usage_dir) if f.endswith(".json")]
            if os.path.isdir(usage_dir)
            else []
        )
        if not summaries:
            return {"status": "ok", "message": "no committed usage summaries (TEA-5 opt-in unused)."}
        remote = git_remote_info(ctx.repo_root)
        if not remote["url"]:
            return {
                "status": "ok",
                "message": f"{len(summaries)} committed usage summary(ies) present; repo is "
                "local-only (no remote), so recall patterns are not shared.",
            }
        where = "a PUBLIC-host remote" if remote["public_host"] else "a remote"
        return {
            "status": "warn",
            "message": f"{len(summaries)} committed per-user usage summary(ies) in "
            f".claude/memory/.usage/ on {where} ({remote['url']}) — recall patterns (memory "
            "names + counts) are shared with anyone who can read it. Remove .claude/memory/.usage/ "
            "if unintended (TEA-5/SEC-14).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"committed-usage privacy check failed: {exc}."}


def check_dream_ledger(ctx: DoctorContext) -> Dict[str, str]:
    """DRM-2: the corpus's on-disk ``dream: … edge=`` stamps must reconcile with the ledger.

    Every auto-applied dream edge leaves BOTH an inline stamp and an ACTIVE
    ``dream-ledger.jsonl`` line — grep-reconcilable by design. A stamp with no active
    ledger line (hand-copied? ledger truncated?) or an active line with no stamp (stamp
    hand-deleted instead of ``dream --undo``) means the audit record and the corpus
    disagree — a loud ``fail``, per the roadmap's acceptance criterion (a silent mismatch
    would defeat the reversibility story). Quiet ok when /dream has never applied here.
    """
    try:
        import re as _re

        from .dream import read_apply_ledger
        from .provenance import _iter_memory_files

        on_disk: set = set()
        for path in _iter_memory_files(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if "<!-- dream:" in line:
                            m = _re.search(r"edge=([\w-]+)", line)
                            if m:
                                on_disk.add(m.group(1))
            except Exception:
                continue
        active = {
            e.get("edge_id")
            for e in read_apply_ledger(ctx.memory_dir)
            if e.get("state") == "active"
        }
        if not on_disk and not active:
            return {"status": "ok", "message": "no dream edges applied (nothing to reconcile)."}
        orphans = sorted(on_disk - active)
        ghosts = sorted(active - on_disk)
        if not orphans and not ghosts:
            return {
                "status": "ok",
                "message": f"{len(active)} dream edge stamp(s) reconcile with dream-ledger.jsonl.",
            }
        parts = []
        if orphans:
            parts.append(
                f"{len(orphans)} on-disk stamp(s) with no ACTIVE ledger line: {', '.join(orphans[:5])}"
            )
        if ghosts:
            parts.append(
                f"{len(ghosts)} active ledger edge(s) with no on-disk stamp: {', '.join(ghosts[:5])}"
            )
        return {
            "status": "fail",
            "message": "dream stamp/ledger MISMATCH — " + "; ".join(parts) + ". Reconcile "
            "via `python -m memory.dream --log` (+ --undo for stray edges) or git history; "
            "never hand-edit stamped lines.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"dream-ledger check failed: {exc}."}


# --------------------------------------------------------------------------- #
# RET-3: non-English corpus served by the English default model
# --------------------------------------------------------------------------- #
# Codepoint ranges for "Latin script" alphabetic characters — Basic Latin + Latin-1 Supplement
# + Latin Extended-A/B, which together cover English plus the accented Latin of French,
# German, Spanish, Portuguese, Vietnamese (base letters), etc. Anything alphabetic OUTSIDE
# these ranges (Cyrillic, CJK, Greek, Arabic, Devanagari, ...) counts as "non-Latin" for this
# heuristic. Deliberately coarse (not a full script-detection library) — this is a doctor
# HINT, not a certified language classifier; it only needs to catch the obvious case (a corpus
# that reads as visibly non-English) without false-positiving on a mostly-English corpus that
# happens to contain a few French loanwords or names.
_LATIN_ALPHA_RANGES = (
    (0x0041, 0x005A),  # A-Z
    (0x0061, 0x007A),  # a-z
    (0x00C0, 0x00FF),  # Latin-1 Supplement letters (À-ÿ, excl. ×/÷ which aren't alphabetic anyway)
    (0x0100, 0x024F),  # Latin Extended-A/B (accented forms used by many European languages)
)
# Below this many sampled alphabetic chars, the sample is too small to call a verdict either
# way (a corpus of one or two short-description memories) — stay silent rather than guess.
_NON_ENGLISH_MIN_ALPHA_SAMPLE = 40
# ">30%" per the roadmap's acceptance criterion — a visible fraction, not a strict majority (a
# corpus that's mostly English with scattered non-Latin proper nouns should NOT fire this).
_NON_ENGLISH_ALPHA_FRACTION = 0.30


def _is_latin_alpha(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in _LATIN_ALPHA_RANGES)


def check_non_english_corpus(ctx: DoctorContext) -> Dict[str, str]:
    """Warn when the corpus reads as visibly non-English but the model is the English default.

    RET-3 / OQ-4: the release keeps ``bge-small-en-v1.5`` as the hardcoded default (an explicit
    opt-in — ``--multilingual`` — switches it), so a corpus written mostly in, say, Japanese or
    Russian would otherwise get dense embeddings from a model never trained on that language,
    with NO signal anywhere that a better-fitting preset exists. This samples every memory's
    ``description:`` (the same text the index embeds — reusing ``extract_description`` so this
    check can never disagree with what actually gets indexed) and counts alphabetic characters
    that fall OUTSIDE the Latin-script ranges. If more than
    ``_NON_ENGLISH_ALPHA_FRACTION`` of a large-enough alphabetic sample is non-Latin AND the
    manifest's recorded model is still ``ENGLISH_DEFAULT_MODEL``, this warns and names the
    `--multilingual` bootstrap preset. Silent (``ok``) on an empty/tiny corpus (nothing to
    sample, or the sample is below ``_NON_ENGLISH_MIN_ALPHA_SAMPLE``), when the model has
    already been switched away from the English default (nothing to suggest), or on any
    unexpected error. Heuristic and best-effort by design — never raises, never blocks.
    """
    try:
        from .build_index import ENGLISH_DEFAULT_MODEL, _load_manifest, default_index_dir, extract_description

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        # No index yet, or already using a non-English model -> nothing to suggest here.
        if manifest is None:
            return {"status": "ok", "message": "non-English corpus check: N/A (no index built yet)."}
        manifest_model = manifest.get("model")
        if manifest_model and manifest_model != ENGLISH_DEFAULT_MODEL:
            return {
                "status": "ok",
                "message": f"non-English corpus check: N/A (model is already '{manifest_model}', not the English default).",
            }

        total_alpha = 0
        non_latin_alpha = 0
        for path in _iter_memory_files_safe(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    desc = extract_description(fh.read())
            except Exception:
                continue
            for ch in desc:
                if not ch.isalpha():
                    continue
                total_alpha += 1
                if not _is_latin_alpha(ch):
                    non_latin_alpha += 1

        if total_alpha < _NON_ENGLISH_MIN_ALPHA_SAMPLE:
            return {
                "status": "ok",
                "message": f"non-English corpus check: N/A (only {total_alpha} alphabetic chars sampled, "
                f"below the {_NON_ENGLISH_MIN_ALPHA_SAMPLE}-char floor for this heuristic).",
            }

        fraction = non_latin_alpha / total_alpha
        if fraction <= _NON_ENGLISH_ALPHA_FRACTION:
            return {
                "status": "ok",
                "message": f"corpus reads as Latin-script/English ({fraction:.0%} non-Latin alphabetic chars).",
            }
        return {
            "status": "warn",
            "message": f"corpus is {fraction:.0%} non-Latin-alphabetic but is served by the English "
            "default embedding model — consider `/hippo:bootstrap --multilingual` (switches to "
            "a multilingual model; forces a one-time full re-embed of the corpus).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"non-English corpus check failed: {exc}."}


# --------------------------------------------------------------------------- #
# TMB-2/TMB-3 (T11): the terminal-state + forgetting instruments
# --------------------------------------------------------------------------- #
def check_invalid_after_terminal(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-2: the corpus-wide terminal-state count — retirements the drift signal can't see.

    A memory retired via supersede/merge (``invalid_after`` past recall's old horizon,
    NO cited-code drift) never enters ``find_stale``'s set, so before this it was counted
    NOWHERE: recall display-filters it, the staleness producer's invalid_after map is
    stale-scoped, and archive_candidates was stale-gated 4-way. One line, ok-at-zero;
    the fix path is the shipped flows — archive via ``python -m memory.archive`` (TMB-2's
    admission leg lists them), reinstate per item via reconsolidate outcome=graduate|fix
    (``reverify_file`` strips the stamp). Cold path; the corpus scan + git-log window
    both belong here, never the hot path.
    """
    try:
        from .staleness import find_stale as _find_stale
        from .staleness import nondrift_old_invalidated

        stale_names = [item["name"] for item in _find_stale(ctx.memory_dir, ctx.repo_root)]
        retired = nondrift_old_invalidated(ctx.memory_dir, stale_names)
        if not retired:
            return {
                "status": "ok",
                "message": "invalid_after: no memories retired outside the drift signal.",
            }
        names = sorted(retired)
        shown = ", ".join(names[:4]) + (f" (+{len(names) - 4} more)" if len(names) > 4 else "")
        return {
            "status": "warn",
            "message": f"invalid_after: {len(names)} memor"
            + ("y" if len(names) == 1 else "ies")
            + f" retired past the old horizon with no code drift ({shown}) — archivable "
            "via `python -m memory.archive`; reinstate per item via reconsolidate "
            "outcome=graduate|fix.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"invalid_after terminal-state check failed: {exc}."}


def check_archive_shadowing(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-3: a stem present in BOTH ``archive/`` and the live corpus — the shadow hazard.

    ``_first_seen_times`` deliberately skips ``archive/`` because an archived stem could
    shadow a live one; ``archive.restore`` refuses collisions for the same reason. This
    check makes an EXISTING collision visible (a hand-copied file, a merge that
    resurrected an archived name). Read-only — the printed suggestion is a manual
    ``git mv``; nothing here writes.
    """
    try:
        archive_dir = os.path.join(ctx.memory_dir, "archive")
        if not os.path.isdir(archive_dir):
            return {"status": "ok", "message": "archive shadowing: no archive/ directory."}
        archived = {
            fn[:-3] for fn in os.listdir(archive_dir) if fn.endswith(".md")
        }
        from .provenance import _iter_memory_files

        live = {
            os.path.splitext(os.path.basename(p))[0]
            for p in _iter_memory_files(ctx.memory_dir)
        }
        shadows = sorted(archived & live)
        if not shadows:
            return {
                "status": "ok",
                "message": f"archive shadowing: none ({len(archived)} archived stem(s) "
                "all distinct from the live corpus).",
            }
        shown = ", ".join(shadows[:4]) + (f" (+{len(shadows) - 4} more)" if len(shadows) > 4 else "")
        return {
            "status": "warn",
            "message": f"archive shadowing: {len(shadows)} stem(s) exist in BOTH archive/ "
            f"and the live corpus ({shown}) — the live file wins everywhere, but restore "
            "would refuse and provenance history is ambiguous; resolve by hand, e.g. "
            "`git mv .claude/memory/archive/<name>.md .claude/memory/archive/<name>.superseded.md`.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"archive-shadowing check failed: {exc}."}


def check_archive_regret(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-3 (e): the evidence-only regret detector's ONE surface — doctor time, never
    per-prompt. Inert text + a logged regret event per NEW (query, stem) match; ZERO
    restore action attached (no code path from here to ``archive.restore`` — pinned).
    """
    try:
        from .archive import archive_regret
        from .telemetry import default_telemetry_dir, log_archive_regret, read_archive_regret

        td = default_telemetry_dir(ctx.memory_dir)  # this corpus's ledger home, not ambient
        matches = archive_regret(ctx.memory_dir, telemetry_dir=td)
        if not matches:
            return {
                "status": "ok",
                "message": "archive regret: no recurring abstention matches an archived memory.",
            }
        already = {(e.get("query"), e.get("stem")) for e in read_archive_regret(td)}
        for m in matches:
            if (m["query"], m["stem"]) not in already:
                log_archive_regret(m["query"], m["stem"], telemetry_dir=td)
        shown = "; ".join(
            f"\"{m['query']}\" (asked ×{m['count']}) ↔ archived `{m['stem']}`"
            for m in matches[:3]
        )
        return {
            "status": "warn",
            "message": f"archive regret: {len(matches)} recurring abstention(s) match an "
            f"ARCHIVED memory's body ({shown}) — evidence only, logged; if one is a real "
            "regret, a human can restore it by name (`python -m memory.archive --restore "
            "<stem>`); nothing restores automatically.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"archive-regret check failed: {exc}."}


def check_evidence_fences(ctx: DoctorContext) -> Dict[str, str]:
    """CLB-3: quoted-evidence coverage + drift — one line, deterministic.

    Counts memories carrying MARKED evidence fences vs memories with code fences
    but no marker (pre-marker drains — "unverifiable", out of the detector's scope
    by contract, never backfilled), then reports live drift via the same matcher
    the SessionStart pipeline runs (``staleness_evidence.evidence_drift_map`` —
    one implementation, so doctor and the RET-6 banner can never disagree). A
    corpus with no fences at all reports coverage zero and stays ``ok``; drifted
    memories flip to ``warn`` naming the first few — routing is the existing
    reverify gate, never a new verb.
    """
    try:
        from .links import _FENCED_CODE_RE
        from .staleness_evidence import evidence_drift_map, extract_evidence_fences

        marked = unverifiable = 0
        for path in _iter_memory_files_safe(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            if not _FENCED_CODE_RE.search(text):
                continue
            if extract_evidence_fences(text):
                marked += 1
            else:
                unverifiable += 1
        drift = evidence_drift_map(ctx.memory_dir, ctx.repo_root)
        base = (
            f"quoted evidence: {marked} memory(ies) carry evidence-marked fences; "
            f"{unverifiable} with unmarked code fences (unverifiable — pre-marker, never backfilled)"
        )
        if not drift:
            return {"status": "ok", "message": f"{base}; no evidence drift."}
        shown = ", ".join(sorted(drift)[:4])
        more = f" (+{len(drift) - 4} more)" if len(drift) > 4 else ""
        return {
            "status": "warn",
            "message": f"{base}; {len(drift)} with DRIFTED quoted evidence: {shown}{more} "
            "— re-verify each via the reconsolidation gate (reverify graduate|fix|demote).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"evidence-fence check failed: {exc}."}


def check_merge_digest(ctx: DoctorContext) -> Dict[str, str]:
    """CLB-4: incoming-merge duplicate pairs — the doctor half of the digest.

    Re-derives the SAME pairs the SessionStart producer surfaces (one derivation,
    ``merge_digest.incoming_duplicate_pairs`` — never a second detector) so a lead
    running doctor after a merge sees them without waiting for the next session
    start. Routing is identical and human: /hippo:resolve for declared
    contradictions, /hippo:consolidate's GRW-3 merge tier for the rest. The
    unreachable-watermark case renders as its own legible state.
    """
    try:
        from .merge_digest import incoming_duplicate_pairs
        from .telemetry import default_telemetry_dir

        pairs, degradation, incoming = incoming_duplicate_pairs(
            ctx.memory_dir, ctx.repo_root, default_telemetry_dir(ctx.memory_dir)
        )
        if degradation and not pairs:
            return {
                "status": "warn",
                "message": "incoming-merge dedup: last-session watermark unreachable "
                "(squash-merge/rewrite) — the incoming range could not be dup-checked; "
                "self-heals next session.",
            }
        if not pairs:
            return {
                "status": "ok",
                "message": "incoming-merge dedup: no duplicate pairs among memories "
                "merged in since the last session.",
            }
        shown = "; ".join(
            f"{p['incoming']} ⇄ {p['neighbor']} → "
            + ("/hippo:resolve" if p["route"] == "resolve" else "/hippo:consolidate")
            for p in pairs
        )
        return {
            "status": "warn",
            "message": f"incoming-merge dedup: {len(pairs)} duplicate-candidate pair(s) "
            f"across {incoming} merged-in memory file(s) — {shown} (human-routed; "
            "nothing merges automatically).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"merge-digest check failed: {exc}."}
