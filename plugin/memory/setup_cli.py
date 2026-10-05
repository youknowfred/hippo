"""``hippo setup`` — opt-in machine settings, previewed before anything is written.

HOT-6 adds the first one, ``--warm``: warm recall, where each prompt's recall is answered by
the session's own hippo MCP server (the embedding model already loaded) through a
UserPromptSubmit ``mcp_tool`` hook in Claude Code's USER settings. The hook is deliberately
not in the plugin's ``hooks.json``: a user who never opts in pays nothing. The command hook
stays installed as the fallback, and the two settle every prompt so exactly one injects
(``recall_warm``).

  hippo setup --warm                  preview: the exact entry and the file, nothing written
  hippo setup --warm --yes            write it (a timestamped backup first; idempotent)
  hippo setup --warm --off [--yes]    remove it (preview without --yes)
  hippo setup --warm --status         read-only: configured where, the servers, the paths

Writes go through ``atomic.update_text_cas`` (Claude Code writes this file too), keep every
other key, and never touch a file that does not parse. ``HIPPO_CLAUDE_SETTINGS`` points the
user settings file elsewhere (``native_memory.user_settings_path``), which is how the tests
stay off the real one. A later setting is one more ``_SETTINGS`` row and its flag.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

WARM_SERVER = "plugin:hippo:hippo"
WARM_TOOL = "recall_hook"
WARM_TIMEOUT_S = 5
_EVENT = "UserPromptSubmit"


def warm_hook_entry() -> dict:
    """The one hook entry ``--warm`` adds (the official ``mcp_tool`` hook shape)."""
    return {
        "type": "mcp_tool",
        "server": WARM_SERVER,
        "tool": WARM_TOOL,
        "input": {
            "prompt": "${prompt}",
            "session_id": "${session_id}",
            "prompt_id": "${prompt_id}",
            "cwd": "${cwd}",
        },
        "timeout": WARM_TIMEOUT_S,
    }


def _is_warm_hook(hook) -> bool:
    return (
        isinstance(hook, dict)
        and hook.get("type") == "mcp_tool"
        and hook.get("server") == WARM_SERVER
        and hook.get("tool") == WARM_TOOL
    )


def _groups(doc: dict) -> List[dict]:
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    groups = hooks.get(_EVENT) if isinstance(hooks, dict) else None
    return [g for g in groups if isinstance(g, dict)] if isinstance(groups, list) else []


def warm_hooks(doc: dict) -> List[dict]:
    """Every warm-recall hook entry in a settings document."""
    out: List[dict] = []
    for group in _groups(doc):
        for hook in group.get("hooks") or []:
            if _is_warm_hook(hook):
                out.append(hook)
    return out


def with_warm_hook(doc: dict) -> dict:
    """``doc`` with exactly one current warm hook: an equal entry is left alone, an older
    one is replaced in place, extra copies are dropped, and a missing one is appended as
    its own group. Every other key and hook keeps its place."""
    out = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    entry = warm_hook_entry()
    placed = False
    for group in _groups(out):
        hooks = group.get("hooks")
        if not isinstance(hooks, list):
            continue
        kept = []
        for hook in hooks:
            if _is_warm_hook(hook):
                if placed:
                    continue
                hook = entry
                placed = True
            kept.append(hook)
        group["hooks"] = kept
    if not placed:
        hooks_obj = out.get("hooks")
        if not isinstance(hooks_obj, dict):
            hooks_obj = {}
            out["hooks"] = hooks_obj
        groups = hooks_obj.get(_EVENT)
        if not isinstance(groups, list):
            groups = []
            hooks_obj[_EVENT] = groups
        groups.append({"hooks": [entry]})
    return out


def without_warm_hook(doc: dict) -> dict:
    """``doc`` with every warm hook removed. A group, event list or ``hooks`` object that
    only the removal emptied goes too; anything that was already empty stays."""
    out = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    hooks_obj = out.get("hooks")
    if not isinstance(hooks_obj, dict) or not isinstance(hooks_obj.get(_EVENT), list):
        return out
    groups = []
    for group in hooks_obj[_EVENT]:
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            kept = [h for h in group["hooks"] if not _is_warm_hook(h)]
            if not kept and len(kept) != len(group["hooks"]):
                continue
            group["hooks"] = kept
        groups.append(group)
    if groups or not hooks_obj[_EVENT]:
        hooks_obj[_EVENT] = groups
    else:
        del hooks_obj[_EVENT]
        if not hooks_obj:
            del out["hooks"]
    return out


def _render(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _read(path: str) -> Tuple[Optional[dict], Optional[str]]:
    """``(settings, error)``: ``({}, None)`` for a missing file, ``(None, why)`` for a file
    that cannot be read or does not parse as a JSON object."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return None, f"cannot read it ({exc.strerror or exc})"
    if not text.strip():
        return {}, None
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return None, f"it is not valid JSON ({exc})"
    if not isinstance(doc, dict):
        return None, "it is not a JSON object"
    return doc, None


def settings_path() -> str:
    from .native_memory import user_settings_path

    return user_settings_path()


def warm_state(repo_root: Optional[str] = None) -> Dict[str, object]:
    """Where warm recall is configured: ``{"configured", "current", "file", "user_file"}``.
    Reads the local, project and user settings (most specific first); writes nothing."""
    from .native_memory import _settings_layers

    user = settings_path()
    state: Dict[str, object] = {"configured": False, "current": False, "file": None, "user_file": user}
    files = []
    if repo_root:
        files += [
            os.path.join(repo_root, ".claude", "settings.local.json"),
            os.path.join(repo_root, ".claude", "settings.json"),
        ]
    files.append(user)
    try:
        layers = _settings_layers(repo_root)
    except Exception:
        layers = []
    for (_label, doc), path in zip(layers, files):
        found = warm_hooks(doc)
        if found:
            state.update(
                configured=True,
                current=any(h == warm_hook_entry() for h in found),
                file=path,
            )
            break
    return state


def _backup(path: str) -> Optional[str]:
    """Copy the settings file beside itself before the first write; the copy's name. The
    copy keeps the original's permission bits from its first byte (settings can hold
    secrets in ``env``): an empty placeholder is created with them, then filled atomically."""
    from .atomic import write_bytes_atomic

    try:
        with open(path, "rb") as fh:
            data = fh.read()
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while True:
        dest = f"{path}.hippo-backup-{stamp}" + (f"-{n}" if n > 1 else "")
        try:
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode))
            break
        except FileExistsError:
            n += 1
    try:
        write_bytes_atomic(dest, data)
    except BaseException:
        os.unlink(dest)  # no empty backup left behind; the settings write never starts
        raise
    return dest


def _apply(path: str, transform) -> str:
    """Write ``transform(settings)`` with compare-and-swap; returns the backup's path ("" if
    the file did not exist). Raises on a file that will not parse or will not write."""
    from .atomic import update_text_cas, write_text_cas

    def _tx(text: str) -> Optional[str]:
        doc = json.loads(text) if text.strip() else {}
        if not isinstance(doc, dict):
            raise ValueError("the settings file is not a JSON object")
        new = transform(doc)
        return None if new == doc else _render(new)

    if not os.path.exists(path):
        new = transform({})
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        write_text_cas(path, _render(new), None)
        return ""
    backup = _backup(path) or ""
    update_text_cas(path, _tx)
    return backup


def _clear_handshake_files() -> int:
    """After ``--off``: drop this machine's warm handshake files, and the dir once empty, so
    a live session's hook stops handing prompts to a server the harness no longer calls and
    every hook goes back to one stat. Returns how many files went."""
    from .recall_warm import warm_dir

    d = warm_dir()
    if not d:
        return 0
    n = 0
    for name in os.listdir(d):
        if name.endswith((".server.json", ".claim", ".lost")) or ".tmp." in name:
            try:
                os.unlink(os.path.join(d, name))
                n += 1
            except OSError:
                pass
    try:
        os.rmdir(d)
    except OSError:
        pass  # something else lives there: leave the dir
    return n


_WHAT_IT_DOES = (
    "Warm recall: this session's hippo MCP server answers each prompt's recall, with the\n"
    "embedding model already loaded, instead of a fresh Python process per prompt. hippo's own\n"
    "recall hook stays installed and decides, prompt by prompt, which one answers, so recall\n"
    "never runs twice. The fresh process still answers when the server can't: often the first\n"
    "prompt of a session, a session still running an older hippo after an update (restart it),\n"
    "a server busy with another tool, and the rest of any session in which a served recall\n"
    "failed or ran slow. No network and no money, as before; the server keeps the model in\n"
    "memory, and a few small handshake files live in the plugin's data directory. It takes\n"
    "effect in new sessions, and in running ones once Claude Code reloads its settings."
)


def _status_lines(repo_root: Optional[str], memory_dir: Optional[str]) -> List[str]:
    from .recall_warm import server_states

    st = warm_state(repo_root)
    lines = []
    if st["configured"]:
        lines.append(f"warm recall: on — the hook is in {st['file']}.")
        if not st["current"]:
            lines.append(
                "  the entry differs from this version's; `hippo setup --warm --yes` updates it."
            )
    else:
        lines.append(f"warm recall: off — `hippo setup --warm` previews turning it on ({st['user_file']}).")
    states = server_states()
    live = [s for s in states if s.get("alive")]
    if states:
        top = states[0]
        age = max(0, int(time.time() - float(top.get("updated") or 0)))
        why = top.get("tripped") or top.get("unfit") or ""
        lines.append(
            f"  servers: {len(live)} live of {len(states)} recorded; the most recent "
            f"(v{top.get('version') or '?'}, {age}s ago) last took the "
            f"{top.get('last_path') or 'no'} path"
            + (f" and is not serving: {why}" if why else "")
            + "."
        )
    if memory_dir:
        counts = path_counts(memory_dir)
        if any(counts[k] for k in ("warm", "spawn", "failed")):
            lines.append("  " + format_path_counts(counts) + ".")
    return lines


def path_counts(memory_dir: str) -> Dict[str, object]:
    """30-day hook path counts and walls for ``memory_dir``'s corpus, from the rollups."""
    from .telemetry import default_telemetry_dir
    from .telemetry_rollup import read_rollups, summarize

    k = summarize(read_rollups(default_telemetry_dir(memory_dir), days=30))
    surface = k.get("surface") or {}
    return {
        "warm": int(surface.get("hook:user_prompt:warm", 0)),
        "spawn": int(surface.get("hook:user_prompt:spawn", 0)),
        "failed": int(surface.get("hook:user_prompt:failed", 0)),
        "warm_p50": k.get("warm_wall_p50"),
        "warm_p95": k.get("warm_wall_p95"),
        "spawn_p50": k.get("wall_p50"),
        "spawn_p95": k.get("wall_p95"),
    }


def format_path_counts(c: Dict[str, object]) -> str:
    msg = f"30 days: warm {c['warm']} · spawn {c['spawn']} · failed {c['failed']}"
    walls = []
    if c.get("warm_p95"):
        walls.append(f"warm wall p50 ≤{c['warm_p50']}ms / p95 ≤{c['warm_p95']}ms")
    if c.get("spawn_p95"):
        walls.append(f"spawn wall p50 ≤{c['spawn_p50']}ms / p95 ≤{c['spawn_p95']}ms")
    return msg + (f" ({'; '.join(walls)})" if walls else "")


def _warm(args) -> int:
    path = settings_path()
    doc, err = _read(path)
    if args.status:
        try:
            from .provenance import resolve_dirs

            memory_dir, repo_root = resolve_dirs()
            memory_dir = memory_dir if memory_dir and os.path.isdir(memory_dir) else None
        except Exception:
            memory_dir, repo_root = None, None
        print("\n".join(_status_lines(repo_root, memory_dir)))
        return 0
    if doc is None:
        print(f"hippo setup: not touching {path}: {err}. Fix the file, then re-run.", file=sys.stderr)
        return 1
    if args.off:
        new = without_warm_hook(doc)
        if new == doc:
            cleared = _clear_handshake_files() if args.yes else 0
            print(f"Warm recall is already off: {path} has no hippo warm-recall hook."
                  + (f" Cleared {cleared} leftover handshake file(s)." if cleared else " Nothing to do."))
            return 0
        if not args.yes:
            print(f"`hippo setup --warm --off --yes` would remove this hook from {path} "
                  f"(hooks.{_EVENT}), keeping every other setting:\n")
            print(_indent(json.dumps(warm_hooks(doc), indent=2)))
            print("\nNothing was written.")
            return 0
        try:
            backup = _apply(path, without_warm_hook)
        except Exception as exc:
            print(f"hippo setup: could not update {path}: {exc}. Nothing was changed.", file=sys.stderr)
            return 1
        cleared = _clear_handshake_files()
        print(f"Warm recall is off: removed the hook from {path}."
              + (f" Backup: {backup}." if backup else "")
              + (f" Cleared {cleared} handshake file(s)." if cleared else "")
              + " Recall runs as a fresh process per prompt again.")
        return 0
    new = with_warm_hook(doc)
    if new == doc:
        print(f"Warm recall is already on: {path} has the current hook. Nothing to do.\n"
              "Undo: `hippo setup --warm --off --yes`.")
        return 0
    if not args.yes:
        verb = "update the hippo warm-recall hook in" if warm_hooks(doc) else "add this hook to"
        print(_WHAT_IT_DOES + "\n")
        print(f"`hippo setup --warm --yes` would {verb} {path} (hooks.{_EVENT}), keeping every "
              "other setting and saving a timestamped backup first:\n")
        print(_indent(json.dumps({"hooks": [warm_hook_entry()]}, indent=2)))
        print("\nNothing was written. Undo later with `hippo setup --warm --off --yes`.")
        return 0
    try:
        backup = _apply(path, with_warm_hook)
    except Exception as exc:
        print(f"hippo setup: could not update {path}: {exc}. Nothing was changed.", file=sys.stderr)
        return 1
    from .recall_warm import warm_dir

    warm_dir(create=True)  # so a session's very first handshake finds it (best effort)
    print(f"Warm recall is on: added the hook to {path}."
          + (f" Backup: {backup}." if backup else "")
          + " Undo: `hippo setup --warm --off --yes`.")
    return 0


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


# setting -> (its handler, one line for the bare `hippo setup` listing)
_SETTINGS = {
    "warm": (_warm, "warm recall: the session's MCP server answers each prompt's recall"),
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hippo setup",
        description="Opt-in machine settings. Each previews the exact change first and "
        "writes only with --yes.",
    )
    parser.add_argument("--warm", action="store_true", help=_SETTINGS["warm"][1])
    parser.add_argument("--yes", action="store_true", help="apply the change (default: preview only)")
    parser.add_argument("--off", action="store_true", help="remove the setting instead")
    parser.add_argument("--status", action="store_true", help="show the setting's state; writes nothing")
    args = parser.parse_args(argv)
    chosen = [name for name in _SETTINGS if getattr(args, name)]
    if not chosen:
        print("hippo setup: choose a setting.")
        for name, (_fn, summary) in _SETTINGS.items():
            print(f"  --{name}  {summary}")
        print("Each previews first; add --yes to apply, --off to remove, --status to inspect.")
        return 2
    if args.status and (args.yes or args.off):
        parser.error("--status is read-only; drop --yes/--off")
    return _SETTINGS[chosen[0]][0](args)


if __name__ == "__main__":
    raise SystemExit(main())
