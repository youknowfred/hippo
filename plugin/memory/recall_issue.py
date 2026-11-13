"""GP-S4: recall for an issue — the memories that cite one tracker id.

``hippo recall --for-issue GRO-901`` answers "what does this repo's memory say about this
issue": every memory whose description or body cites the id as a whole token, each with
where it cites it, under a header carrying the id's status and snapshot date from LDG-1's
ledger cache (``ledger.py``). The twin of ``--for-diff`` (``recall_diff``) and shaped like
it: read-only, no index, no model, no telemetry, ``--json`` and ``--cap``, exit 0 when
nothing cites the id.

The join itself is a plain read of the corpus files (a whole-token match: the id with no
word character on either side, ``grep -w``'s rule), so it needs no ledger cache; the cache
only supplies the header's status. With no ``ledgers`` declared the verb says so in one
line and exits 0. An id that fully matches no declared ``id_re`` is refused (exit 2) with
the declared pattern named, so a typo is not mistaken for an issue nobody cites.

CLI only: the MCP ``recall`` tool has no ``for_diff`` input to copy the shape from, and the
MCP schema module sits at its size cap.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional

DEFAULT_CAP = 10
_WHERE_LINES = 5  # body lines listed per memory before "+N more"


def _type_rank():
    from .recall_diff import _TYPE_RANK

    return _TYPE_RANK


def memories_citing(issue_id: str, memory_dir: str) -> List[dict]:
    """Rows ``{name, type, steer, description, where}`` for every memory citing
    ``issue_id`` as a whole token, pins first, then feedback/user, project, reference, then
    by name. ``where`` lists ``"description"`` and ``"line N"`` (the file's line numbers,
    at most ``_WHERE_LINES`` then ``"+N more"``). Read-only; ``[]`` on failure."""
    try:
        from .build_index import _extract_steer, extract_description
        from .jit import _flatten, _memory_type
        from .provenance import _iter_memory_files, parse_frontmatter, split_frontmatter

        rx = re.compile(r"(?<!\w)" + re.escape(issue_id) + r"(?!\w)")
        rank = _type_rank()
        rows = []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            if issue_id not in text:
                continue
            fm_lines, body = split_frontmatter(text)
            desc = _flatten(extract_description(text))
            where = ["description"] if rx.search(desc) else []
            offset = (len(fm_lines) + 2) if fm_lines is not None else 0
            hits = [offset + i + 1 for i, line in enumerate(body.split("\n")) if rx.search(line)]
            where += [f"line {n}" for n in hits[:_WHERE_LINES]]
            if len(hits) > _WHERE_LINES:
                where.append(f"+{len(hits) - _WHERE_LINES} more")
            if not where:
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            fm = parse_frontmatter(text)
            steer = _extract_steer(fm if isinstance(fm, dict) else {})
            mtype = _memory_type(fm) or "project"
            r = 0 if steer == "pin" else rank.get(mtype, 2)
            rows.append((r, name, {"name": name, "type": mtype, "steer": steer,
                                   "description": desc, "where": where}))
        return [row for _r, _n, row in sorted(rows, key=lambda t: (t[0], t[1]))]
    except Exception:
        return []


def _match(issue_id: str, ledgers: List[dict]) -> Optional[str]:
    for cand in (issue_id, issue_id.upper()):
        for lg in ledgers:
            try:
                if re.fullmatch(lg["id_re"], cand):
                    return cand
            except re.error:
                continue
    return None


def run(
    issue_id: str,
    *,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    cap: int = DEFAULT_CAP,
    as_json: bool = False,
) -> int:
    """The whole ``--for-issue`` lane. 0, or 2 for an id no declared pattern matches."""
    try:
        from . import ledger as L

        if memory_dir is None:
            from .provenance import resolve_dirs

            memory_dir, rr = resolve_dirs()
            repo_root = repo_root or rr
        issue_id = (issue_id or "").strip()
        if not L.declared(memory_dir):
            msg = "No ledger is declared in this corpus's hippo.json, so there is no issue id to join on."
            print(json.dumps({"id": issue_id, "declared": False, "items": []}) if as_json else msg)
            return 0
        ledgers, dropped = L.read_declaration(memory_dir, L.corpus_top(memory_dir, repo_root))
        if not ledgers:
            why = "; ".join(f"{d.get('name')}: {d.get('reason')}" for d in dropped) or "none usable"
            msg = f"Every ledger this corpus declares was dropped ({why}); /hippo:doctor shows the same."
            print(json.dumps({"id": issue_id, "declared": True, "usable": False, "items": []}) if as_json else msg)
            return 0
        ident = _match(issue_id, ledgers)
        if ident is None:
            pats = " or ".join(lg["id_re"] for lg in ledgers)
            msg = f"{issue_id!r} is not an issue id here: the declared ledger pattern is {pats}"
            print(json.dumps({"id": issue_id, "refused": msg}) if as_json else msg)
            return 2
        if index_dir is None:
            from .build_index import default_index_dir

            index_dir = default_index_dir(memory_dir)
        cache = L.load(index_dir)
        st = cache.status(ident) if cache is not None else None
        rows = memories_citing(ident, memory_dir)
        total = len(rows)
        rows = rows[: max(0, cap)]
        if as_json:
            print(json.dumps({
                "id": ident, "declared": True,
                "status": st[0] if st else None, "as_of": st[1] if st else None,
                "ledger": st[2] if st else None, "in_cache": cache is not None,
                "total": total, "items": rows,
            }, ensure_ascii=False))
            return 0
        if st:
            head = f"{ident} {st[0]} (snapshot {st[1]})"
        elif cache is not None:
            snap = ", ".join(sorted({str(m.get("as_of")) for m in cache.ledgers})) or "?"
            head = f"{ident}, not in the ledger snapshot (snapshot {snap})"
        else:
            head = f"{ident}, status unknown (no ledger cache yet: the next SessionStart or hippo build-index builds it)"
        if not rows:
            print(f"{head} — no memory cites it.")
            return 0
        lines = [f"{head} — {total} memory(ies) cite it:"]
        for r in rows:
            marker = " [pin]" if r.get("steer") == "pin" else ""
            lines.append(f"- {r['name']} [{r['type']}]{marker} — {r['description']}  (cites it in: {', '.join(r['where'])})")
        if total > len(rows):
            lines.append(f"… and {total - len(rows)} more (raise --cap)")
        print("\n".join(lines))
        return 0
    except Exception:
        return 0
