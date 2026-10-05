"""SRF-2: the v2 MCP toolset — ten tools, annotated, with output schemas on the read tools.

The v2 set is ``recall``, ``new_memory``, ``inspect``, ``tend``, ``doctor``, ``setup``,
``trust``, ``share``, ``dream`` and ``review``. Five keep a v1 name (recall, new_memory,
doctor, dream, tend) and take the v2 schema in place; the other five are new names that
route to the v1 handlers. Every v1 name outside the v2 set stays listed and callable
through the deprecation window (v1.42–v1.43), its description opening with the v2 route
and each call's result carrying a one-line deprecation notice; OBS-2 counts every call
by the name used, which is the usage the v2.0 removal is decided on.

Also here: the MCP ``annotations`` for every tool (``readOnlyHint``/``destructiveHint``/
``idempotentHint``/``openWorldHint``), the ``outputSchema`` of the tools that return
``structuredContent``, and the protocol versions the server negotiates. Pure data; the
server reads it, nothing else does.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# Newest first. ``initialize`` answers with the client's version when it is listed here,
# else the newest one. Annotations need 2025-03-26; output schemas and structured content
# need 2025-06-18 — older sessions get neither.
PROTOCOL_VERSIONS: Tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
ANNOTATIONS_SINCE = "2025-03-26"
STRUCTURED_SINCE = "2025-06-18"

_TEXT = {"type": "string", "description": "the same answer as the text content"}


def _out(props: dict, required: List[str]) -> dict:
    return {"type": "object", "properties": {"text": _TEXT, **props}, "required": ["text", *required]}


OUTPUT_SCHEMAS: Dict[str, dict] = {
    "recall": _out({
        "query": {"type": "string"},
        "abstained": {"type": "boolean", "description": "true when nothing cleared the relevance floor"},
        "hits": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
            "score": {"type": ["number", "null"]},
            "corpus": {"type": ["string", "null"]},
            "note": {"type": ["string", "null"]},
        }, "required": ["name"]}},
    }, ["query", "abstained", "hits"]),
    "doctor": _out({
        "action": {"type": "string"},
        "checks": {"type": "array", "items": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["ok", "warn", "fail"]},
            "message": {"type": "string"},
        }, "required": ["status", "message"]}},
        "counts": {"type": "object", "properties": {
            "ok": {"type": "integer"}, "warn": {"type": "integer"}, "fail": {"type": "integer"}}},
    }, ["action"]),
    "tend": _out({
        "action": {"type": "string"},
        "pending": {"type": "integer"},
        "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "kind": {"type": "string"}, "target": {"type": "string"},
            "evidence": {"type": "string"}, "proposed": {"type": "string"}, "gate": {"type": "string"},
        }, "required": ["id", "kind"]}},
    }, ["action"]),
}

_S = {"type": "string"}
_B = {"type": "boolean"}
_I = {"type": "integer"}


def _schema(props: dict, required=()) -> dict:
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = list(required)
    return out


V2_TOOLS: List[dict] = [
    {
        "name": "recall",
        "description": (
            "Search this project's memory with the same ranking the prompt hook uses, for a "
            "mid-task question or inside a subagent. Returns the matches with type, staleness "
            "and linked memories, or says it abstained when nothing clears the floor."
        ),
        "inputSchema": _schema({"query": _S, "k": dict(_I, description="max matches (default 10)")}, ["query"]),
    },
    {
        "name": "new_memory",
        "description": (
            "Save ONE memory, right by construction (frontmatter, citations, index refresh, floor "
            "pointer). Pass check:true first to see near-duplicates and decide add, update the "
            "existing one, supersede or skip; then call again to write. Never loop it to bulk-import."
        ),
        "inputSchema": _schema({
            "name": dict(_S, description="slug; also the filename"),
            "description": dict(_S, description="the one line recall matches"),
            "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
            "body": dict(_S, description="the memory body: the fact and why"),
            "links": {"type": "array", "items": _S, "description": "related memory names"},
            "confidence": {"type": "string", "enum": ["draft", "verified", "authoritative"]},
            "check": dict(_B, description="dry run: neighbors only, writes nothing"),
        }, ["name", "description", "type"]),
    },
    {
        "name": "inspect",
        "description": (
            "Read-only views of one memory or one recall. action='why' explains why recall "
            "surfaced (or missed) memories for a query; 'traverse' lists a memory's linked "
            "neighbors (hops); 'history' replays its supersede/refine chain; 'blast_radius' "
            "shows which sessions, links and rule files it touched."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["why", "traverse", "history", "blast_radius"]},
            "query": dict(_S, description="with why"),
            "k": _I,
            "name": dict(_S, description="with traverse, history, blast_radius"),
            "hops": dict(_I, description="with traverse (default 1)"),
        }, ["action"]),
    },
    {
        "name": "tend",
        "description": (
            "The maintenance queue: re-consent, broken baselines, contradictions, merged-in "
            "duplicates, captures, memories whose cited code moved, broken links, floor "
            "overflow, citation re-derivation. action list (default) | next | show (id) | apply "
            "(id, verdict: ONE item, after the user agrees) | snooze | skip | hold (kind or id, "
            "reason) | release. Also: add_decision (text) records a decision the user made for "
            "the capture drain; restore (seed, or all) brings back an expired capture; "
            "snapshot (stamp) backs up the corpus; fixtures (step draft or "
            "confirm) and interview (step questions or respond) run the blind-spot steps; "
            "link_proposals lists co-recall link suggestions."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": [
                "list", "next", "show", "apply", "snooze", "skip", "hold", "release",
                "add_decision", "restore", "snapshot", "fixtures", "interview", "link_proposals"]},
            "id": dict(_S, description="an item id (kind:target)"),
            "kind": _S,
            "verdict": _S,
            "winner": _S,
            "loser": _S,
            "superseded_by": _S,
            "days": {"type": "number"},
            "reason": _S,
            "text": dict(_S, description="with add_decision"),
            "seed": dict(_S, description="with restore: an expired seed or session id"),
            "all": _B,
            "stamp": dict(_S, description="with snapshot: a label"),
            "step": dict(_S, description="with fixtures or interview"),
            "query": _S, "expected": _S, "category": _S, "absent": _B, "superseded": _B,
            "qid": _S, "outcome": _S,
        }),
    },
    {
        "name": "doctor",
        "description": (
            "Health checks. action='check' (default) checks the install, venv, corpus, trust, "
            "index and recall plumbing, one line each, and works before the corpus is trusted. "
            "'audit' renders the corpus content-audit material (staleness, orphans, archive "
            "candidates). 'secrets_scan' (text) lints text for secrets before it enters a memory."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["check", "audit", "secrets_scan"]},
            "text": dict(_S, description="with secrets_scan"),
            "skip_eval": dict(_B, description="with audit"),
            "window_sessions": dict(_I, description="with audit"),
        }),
    },
    {
        "name": "setup",
        "description": (
            "Set hippo up on this machine and project. action='bootstrap' (step start or "
            "status; multilingual) builds the venv and model cache in the background; 'init' "
            "wires this project (a corpus it creates is trusted; an existing one goes through "
            "trust review); 'build_index' rebuilds the recall index and link graph."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["bootstrap", "init", "build_index"]},
            "step": {"type": "string", "enum": ["status", "start"]},
            "multilingual": _B,
        }, ["action"]),
    },
    {
        "name": "trust",
        "description": (
            "Consent for this project's memory: recall and writes stay withheld until the user "
            "has reviewed what would be injected. action='status' (default) says where consent "
            "stands; 'review' shows what changed since consent and returns a digest; 'grant' "
            "(digest from review, after the user agrees) consents to exactly what was shown; "
            "'revoke' withdraws consent. Never grant on the user's behalf."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["status", "review", "grant", "revoke"]},
            "digest": dict(_S, description="with grant: the digest review returned"),
            "files": {"type": "array", "items": _S, "description": "with grant: only these files"},
            "repo_root": dict(_S, description="with revoke: another corpus"),
        }),
    },
    {
        "name": "share",
        "description": (
            "Share memories as packs. action='pack_extract' (dest, names or all) writes chosen "
            "memories into a pack dir; 'pack_install_plan' / 'pack_update_plan' (source_dir) "
            "show what a pack would add or change; 'pack_install_item' / 'pack_update_item' "
            "apply ONE item after the user agrees. Promote, publish, export and import run "
            "from the share skill."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": [
                "pack_extract", "pack_install_plan", "pack_install_item",
                "pack_update_plan", "pack_update_item"]},
            "dest": _S, "names": {"type": "array", "items": _S}, "all": _B, "pack": _S,
            "version": _S, "title": _S, "description": _S, "source_dir": _S, "name": _S,
            "source": _S, "resolved_text": _S,
        }, ["action"]),
    },
    {
        "name": "dream",
        "description": (
            "The offline link pass: replay the corpus against itself and add the links it "
            "finds (reversible, capped, stamped); returns a digest with undo handles — show it "
            "as is. apply=false reports only. action undo (edge_id or undo_since) | log | "
            "retire_ghost (edge_id, reason) | deparasite (retract) | dedup_merge (survivor, "
            "loser) | generate (stage) | sweep_drafts | archive_draft (name) | prospective."
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": [
                "pass", "undo", "retire_ghost", "log", "deparasite", "dedup_merge", "generate",
                "sweep_drafts", "archive_draft", "prospective"]},
            "apply": _B, "edge_id": _S, "reason": _S, "undo_since": _S, "retract": _B,
            "survivor": _S, "loser": _S, "stage": _B, "name": _S,
            "contradictions": dict(_B, description="with a pass: also run the LLM contradiction check"),
        }),
    },
    {
        "name": "review",
        "description": (
            "Review a memory diff like a pull request: which memories were added, updated, "
            "superseded, archived or relinked, the lints on the touched files, and (locally) how "
            "recall would shift. range is a git range (default: working tree vs HEAD); ci=true "
            "runs the lints only. Review material, never an approval."
        ),
        "inputSchema": _schema({"range": _S, "ci": _B}),
    },
]

V2_NAMES: Tuple[str, ...] = tuple(t["name"] for t in V2_TOOLS)

# v1 name -> (v2 tool, how to call it). The route the deprecation notice names.
DEPRECATED: Dict[str, Tuple[str, str]] = {
    "traverse": ("inspect", "action='traverse'"),
    "why": ("inspect", "action='why'"),
    "decision_history": ("inspect", "action='history'"),
    "blast_radius": ("inspect", "action='blast_radius'"),
    "bootstrap": ("setup", "action='bootstrap'"),
    "init": ("setup", "action='init'"),
    "build_index": ("setup", "action='build_index'"),
    "trust_corpus": ("trust", "action='review', then 'grant'"),
    "untrust": ("trust", "action='revoke'"),
    "secrets_scan": ("doctor", "action='secrets_scan'"),
    "audit": ("doctor", "action='audit'"),
    "capture": ("tend", "kind='capture' (add_decision is action='add_decision')"),
    "reconsolidate": ("tend", "kind='reverify'"),
    "resolve": ("tend", "kind='contradiction'"),
    "rederive": ("tend", "kind='derivation' (snapshot is action='snapshot')"),
    "heal_baselines": ("tend", "kind='baseline'"),
    "co_recall_proposals": ("tend", "action='link_proposals'"),
    "abstention_fixtures": ("tend", "action='fixtures'"),
    "interview": ("tend", "action='interview'"),
    "pack_extract": ("share", "action='pack_extract'"),
    "pack_install_plan": ("share", "action='pack_install_plan'"),
    "pack_install_item": ("share", "action='pack_install_item'"),
    "pack_update_plan": ("share", "action='pack_update_plan'"),
    "pack_update_item": ("share", "action='pack_update_item'"),
}


def deprecation_line(old: str) -> str:
    new, how = DEPRECATED[old]
    return (f"(`{old}` is deprecated and is removed in v2.0 — call `{new}` with {how}. "
            "It works the same until then.)")


def _ann(title: str, read_only: bool, destructive: bool = False, idempotent: bool = False,
         open_world: bool = False) -> dict:
    a = {"title": title, "readOnlyHint": read_only, "openWorldHint": open_world}
    if not read_only:
        a["destructiveHint"] = destructive
        a["idempotentHint"] = idempotent
    return a


ANNOTATIONS: Dict[str, dict] = {
    "recall": _ann("Recall memories", True),
    "new_memory": _ann("Save one memory", False),
    "inspect": _ann("Inspect a memory or a recall", True),
    "tend": _ann("Work the maintenance queue", False, destructive=True),
    "doctor": _ann("Health checks", True),
    "setup": _ann("Set up hippo", False, idempotent=True, open_world=True),
    "trust": _ann("Corpus consent", False),
    "share": _ann("Share memory packs", False, destructive=True),
    "dream": _ann("Offline link pass", False),
    "review": _ann("Review a memory diff", True),
    "recall_hook": _ann("Prompt recall (internal)", True),
    # v1 names, through the deprecation window.
    "traverse": _ann("Linked neighbors (deprecated)", True),
    "why": _ann("Recall receipt (deprecated)", True),
    "decision_history": _ann("Decision history (deprecated)", True),
    "blast_radius": _ann("Blast radius (deprecated)", True),
    "bootstrap": _ann("Bootstrap (deprecated)", False, idempotent=True, open_world=True),
    "init": _ann("Init (deprecated)", False, idempotent=True),
    "build_index": _ann("Build index (deprecated)", False, idempotent=True),
    "trust_corpus": _ann("Trust corpus (deprecated)", False),
    "untrust": _ann("Untrust (deprecated)", False, destructive=True),
    "secrets_scan": _ann("Secrets scan (deprecated)", True),
    "audit": _ann("Audit material (deprecated)", True),
    "capture": _ann("Capture queue (deprecated)", False, destructive=True),
    "reconsolidate": _ann("Reverify (deprecated)", False),
    "resolve": _ann("Contradictions (deprecated)", False),
    "rederive": _ann("Re-derive citations (deprecated)", False, destructive=True),
    "heal_baselines": _ann("Heal baselines (deprecated)", False),
    "co_recall_proposals": _ann("Link proposals (deprecated)", True),
    "abstention_fixtures": _ann("Blind-spot fixtures (deprecated)", False),
    "interview": _ann("Interview (deprecated)", False),
    "pack_extract": _ann("Pack extract (deprecated)", False),
    "pack_install_plan": _ann("Pack install plan (deprecated)", True),
    "pack_install_item": _ann("Pack install item (deprecated)", False),
    "pack_update_plan": _ann("Pack update plan (deprecated)", True),
    "pack_update_item": _ann("Pack update item (deprecated)", False, destructive=True),
}
