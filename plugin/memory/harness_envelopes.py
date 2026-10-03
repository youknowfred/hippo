"""The one list of harness envelope tags: Claude Code's own traffic that reaches the
UserPromptSubmit hook as if the user had typed it.

Three planes have to tell that traffic from a real query, and each used to keep its own copy
of the tag list. The copies drifted. The desktop app's subagent hand-backs
(``<agent-message>``) and messages from other local sessions (``<cross-session-message>``)
were in none of them, so the recall hook ranked hand-backs as queries, the lived-in drafter
queued them as hard-set rows, and capture showed them back as "what you were working on".
Each plane now derives what it needs from the tuples below:

- ``recall_query`` builds ``clean_query``'s envelope-block and tag-marker regexes from them,
  and the hook's RCL-3 rescue neither revives an envelope-only prompt nor blends an envelope
  preview into a terse follow-up;
- ``eval_fixtures.draft_livedin_fixtures`` skips a preview that opens with an envelope;
- ``capture.gather_session_context`` leaves such previews out of a seed's ``query_previews``.

A new harness tag is one edit here. The module is pure and imports nothing from the package,
so the hot path and the cold planes can all depend on it without depending on each other.
"""

from __future__ import annotations

import re

# ENVELOPE tags: the BODY is harness mechanics (a background task's result, injected context,
# another agent's report), so the whole block is noise, and a preview that opens with one
# carries no retrieval intent.
HARNESS_ENVELOPE_TAGS = (
    "task-notification",
    "system-reminder",
    "local-command-stdout",
    "agent-message",  # desktop app: a subagent's hand-back, <agent-message from="a…">
    "cross-session-message",  # desktop app: another local session, from="uds:/tmp/cc-socks/…"
)
# WRAPPER tags: the markers are harness syntax but the body is the user's own words (the slash
# command they ran, its arguments), so only the markers go.
HARNESS_WRAPPER_TAGS = (
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
)
HARNESS_TAGS = HARNESS_ENVELOPE_TAGS + HARNESS_WRAPPER_TAGS

# Where a tag name ends: at anything that cannot continue it, so "<agent-messages" is not
# "<agent-message". Shared with recall_query's regexes so every plane draws the same edge.
TAG_NAME_END = r"(?![\w-])"

# query previews are TRUNCATED at the ledger's preview budget (telemetry._QUERY_PREVIEW_CHARS),
# which cuts the closing tag and often the opening tag's own attributes, so this needs only the
# opening tag's NAME: no ">", no closing tag.
_ENVELOPE_OPEN_RE = re.compile(
    r"\s*<(?:" + "|".join(HARNESS_ENVELOPE_TAGS) + r")" + TAG_NAME_END,
    re.IGNORECASE,
)


def is_envelope_preview(text: str) -> bool:
    """True when ``text`` opens with a harness envelope tag, closed or not.

    This is the test for a truncated query preview, where ``clean_query``'s block regex cannot
    fire (the closing tag was cut) and the ids left inside the envelope read as content tokens.
    A query that only MENTIONS a tag ("why does agent-message draft?", "strip the
    <agent-message> wrapper") does not open with one, so it stays a query.
    """
    return bool(text) and _ENVELOPE_OPEN_RE.match(text) is not None
