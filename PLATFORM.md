# Platform reality — dated receipts (PLT-1)

The v2 plan (`ROADMAP.v2.md` §2, §5 PLT-1) depends on Claude Code behavior that moved under
hippo during 2026. This file records what was **observed**, not what the docs say, for each
question the plan gates on. A row names its date, the Claude Code version, how it was observed,
and what it changes. When the platform moves again, re-run the row and date it again.

**How the probes ran (2026-10-03, Claude Code 2.1.286, macOS).** Headless `claude -p` on Haiku in
a throwaway git repository with a hippo-shaped corpus. Unique nonce tokens were planted in
`MEMORY.md`, in a topic file, and in a second memory directory. A probe asks the model to report
the tokens it holds **with every tool disabled**, so a token can only come from what Claude Code
loaded into context. Hook and MCP behavior was read from a stub MCP server that logs its call
arguments, from hippo's own recall ledger, and from `--debug-file` logs. Docs were fetched the
same day from code.claude.com (hooks, memory, plugins reference, settings reference).

## 1. Desktop

These rows ran on 2026-10-03 against Claude Code 2.1.286 in the Desktop app's Code tab, with
hippo 1.39.1 installed from its marketplace. The interactive-terminal rows ran the same day on
2.1.289. The method is in each row.

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Does a typed `/hippo:doctor` run in the Desktop Code tab? | **Yes.** On 2026-07-11 a typed `/hippo:init` was refused there as "isn't a recognized command here". | The maintainer typed `/hippo:doctor` into a Desktop Code-tab session. The plugin skill loaded from the installed plugin cache and followed its "Surface routing" section to the `doctor` MCP tool. Only `/hippo:doctor` was typed; every other `/hippo:*` verb loads through the same skill loader. | CLM-8 (v1.40.1) retired the 1,723-char SessionStart surface note and every "typed commands are terminal-only" claim. The 7 `terminal_only` verbs in `surfaces.py` keep their rows: their skills still say plainly that they need a terminal. |
| Does the Desktop Bash tool get the plugin env? | **No.** It has `CLAUDE_CODE_ENTRYPOINT=claude-desktop` but no `CLAUDE_PLUGIN_DATA` and no `CLAUDE_PLUGIN_ROOT`. | `env` from the Bash tool in two Desktop sessions: the maintainer's, then the session that built v1.40.1. The plugins reference says these variables are not exported to Bash tool commands. | Through v1.40.1 the skills' bash preflight stopped on Desktop. Since v1.40.2 (INT-20) it pins the substituted paths (rows below) and passes there too. The "Surface routing" sections still send the 11 routed skills to their MCP tools, and the 7 terminal-only skills stop on `CLAUDE_CODE_ENTRYPOINT=claude-desktop` themselves, until a verb's bash flow has its own Desktop receipt (SRF-1, below). |
| Is the plugin's `bin/` on the Desktop Bash `PATH`? | **Yes.** | `which hippo` resolved to `bin/hippo` in the installed plugin cache. The script finds its own plugin root, but with `CLAUDE_PLUGIN_DATA` unset it falls back to bare `python3`, not the venv. | `hippo <verb>` runs from Desktop Bash, without the venv's dependencies. |
| Does each surface get its own plugin-data dir? | **Not any more.** Every live Desktop session's hippo MCP server had `CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/hippo-hippo`, the dir a terminal marketplace install uses. On 2026-07-12 a Desktop MCP server had `…/hippo-inline`. | `ps eww` on the hippo MCP server of four live Desktop sessions. None of the 26 running Desktop `claude` processes passed `--plugin-dir`; in July Desktop passed one per plugin. The docs name the dir after the plugin's `plugin@marketplace` id, which fits `-inline` for a `--plugin-dir` load. Both dirs on this machine still hold a venv. | A terminal bootstrap now serves Desktop sessions too. `bootstrap`'s sibling-install line still matters for older versions and `--plugin-dir` loads. |
| Are `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` in a skill body substituted? | **Yes for the bare form, on Desktop (2.1.286) and in an interactive terminal (2.1.289). No for `${CLAUDE_PLUGIN_DATA:-}`.** | Desktop: the installed `doctor` and `resolve` skills, loaded through the Skill tool. `${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh` arrived as the absolute cache path, and a bare `${CLAUDE_PLUGIN_DATA}` arrived as `~/.claude/plugins/data/hippo-hippo`. The preflight's `${CLAUDE_PLUGIN_DATA:-}` arrived as written, so it expands to empty in Bash. Terminal: an interactive `claude` session on Haiku with hooks disabled loaded the installed `resolve` skill the same way; the session transcript holds the skill text as delivered, with the same three results. A `--plugin-dir` load gets `…/data/hippo-inline` instead. The plugins reference documents the substitution. | What stopped the bash flow was hippo's own preflight guard. v1.40.2 (INT-20) builds the bash route on the bare form: see the rows below and the SRF-1 input. |
| Does the terminal CLI's Bash tool get the plugin env? | **No, headless or interactive.** Headless on 2.1.286 (`CLAUDE_CODE_ENTRYPOINT=sdk-cli`) and interactive on 2.1.289 (`cli`): no `CLAUDE_PLUGIN_DATA`, no `CLAUDE_PLUGIN_ROOT`. `bin/hippo` was on `PATH`. | Headless: `claude -p` on Haiku in a throwaway directory, asked to run `env` through the Bash tool. Interactive: `claude` sessions on Haiku with hooks disabled, launched from a terminal tab, each writing its result to a file (the TUI's screen does not come back through the terminal reader); `env` held 0 `CLAUDE_PLUGIN_` variables. The plugins reference says the variables are absent from Bash commands in every session. | Through v1.40.1 every skill's bash preflight stopped in the terminal too. The 11 routed skills fell back to their MCP tools; the 7 terminal-only verbs had no working route anywhere. v1.40.2 (INT-20) fixes it: see the next two rows. |
| Does shell state carry from one Bash tool call to the next? | **No.** | Desktop 2.1.286: a plain variable, an exported variable and a function set in one Bash call were all unset in the next. The Bash tool's own instructions say the same. | A skill's later block cannot use the `$PY` its preflight set. Since v1.40.2 every block that runs hippo's python opens by pinning both paths and resolving `$PY` itself, and a contract test holds every block to setting what it reads. |
| Does the pinned preflight run a skill's bash flow end to end in the terminal? | **Yes, on 2.1.289.** | An interactive `claude` session on Haiku, with hooks disabled, the installed hippo disabled and v1.40.2's `plugin/` loaded with `--plugin-dir`. It loaded `/hippo:review` (terminal-only) and `/hippo:why` (routed) and ran each one's preflight, then its next block, as separate Bash calls copied from the loaded text. `env` held 0 `CLAUDE_PLUGIN_` variables. All four calls exited 0: `review` printed its packet, and `why` returned receipts marked `won via dense+bm25`, which needs the venv's fastembed and the data dir's model cache. | The terminal runs all 18 skills' bash flows again. On Desktop the same filled-in text passes the preflight too, but no verb's bash flow has run there end to end, so Desktop routing is unchanged. |
| Does plugin install work end to end on Desktop, bootstrap included? | **Not run.** The Desktop docs (fetched 2026-10-03) say plugins install from the desktop app. | Running it would reinstall the plugin on the maintainer's machine, and the hippo install in use here may have come from either surface. | Unchanged: the README's install step keeps its terminal route until a clean-profile run records this row. |

### Input for SRF-1 (v1.42): a bash route from Desktop

Two routes, smallest first. v1.40.2 (INT-20) built the first, which is what fixed the
terminal. The second is not built.

1. **Pin the substituted values in the preflight. Built in v1.40.2.** Every preflight now
   opens with `export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"
   CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"` and then checks the pinned values. Every later
   block that runs hippo's python pins them again, because no shell state carries between
   Bash calls. A bare reference that arrives unsubstituted expands to empty in a shell
   without the env, and the guard stops there; the contract tests run every preflight both
   ways. On Desktop the preflight now passes as well, but nothing routes through it yet: the
   11 routed skills still take their MCP tools, and the 7 terminal-only ones stop on
   `CLAUDE_CODE_ENTRYPOINT=claude-desktop`. For a verb to run its bash flow on Desktop it
   needs its own Desktop receipt end to end, and `surfaces.py` needs a way to declare a
   bash-routed Desktop verb (a routed row must name MCP tools today).
2. **Let `bin/hippo` find its own data dir.** When `CLAUDE_PLUGIN_DATA` is unset, a script
   at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/bin/hippo` can derive
   `~/.claude/plugins/data/<plugin>-<marketplace>` from its own path, using the docs' id rule
   (characters other than letters, digits, `_` and `-` become `-`). On this machine that
   gives `hippo-hippo`, which is the dir the live Desktop MCP servers use. Use it only when
   that dir holds a bootstrap sentinel, and stay on bare `python3` otherwise (a
   `--plugin-dir` load lives outside the cache). The script's header comment, which says
   `bin/` is not on `PATH`, is out of date. Route 1 does not cover a bare `hippo <verb>`
   typed into Bash outside a loaded skill, which is SRF-1's entry point. The `recall` skill's
   `bin/hippo recall` fallback passes the pinned data path in front of the call instead.

Either route could give the 7 `terminal_only` verbs (export-agents, import, promote,
promote-rule, publish, remove, review) a Desktop route. Each verb then needs its own
Desktop receipt before its `surfaces.py` row flips.

## 2. `mcp_tool` hooks

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Do `${prompt}`, `${session_id}` and `${cwd}` substitute for UserPromptSubmit? | **Yes.** | A stub server received the full prompt text, the harness session id and the working directory as its arguments. | HOT-6's session keying needs no socket and no key of hippo's own. |
| Does `plugin:hippo:hippo` reach hippo's server? | **Yes.** | hippo's recall ledger logged an MCP-channel row whose query was the substituted prompt. | The plugin-scoped name works as documented. |
| Does the tool's text reach context? | **Only as JSON.** A reply of `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": …}}` reached the model. A plain-text reply was logged as hook success and then dropped. | Two probes with the same stub, differing only in reply shape. The debug log shows "provided additionalContext" for the JSON reply and nothing for the plain one. The docs say text is "read the same way" as command-hook stdout, where plain text is added. | HOT-6's `recall_hook` tool must return the JSON envelope, not the formatted block. |
| What does a call into hippo's server cost? | **~340 ms, cold.** | The first prompt of a fresh process: hippo's in-process recall logged 229 ms; the hook's call-to-result was ~340 ms. hippo's own spawned command hook, in the same prompt, took ~370 ms. The hooks ran in parallel. A warm (second-prompt) call was not measured: `-p` runs one prompt per process. | Warm latency is the HOT-6 gate. It needs an interactive session, which the v1.42 opt-in collects. |
| Is a failure visible? | **As a non-blocking error.** | A hook naming a tool the server does not have logged `mcp_tool hook error: unknown tool` at ERROR level; the prompt continued and the model saw nothing. | A degraded warm path is silent to the model. HOT-6's `path=warm\|spawn\|failed` logging is the only signal, so it is required, not optional. |
| What happens while the server is still connecting? | **The hook waits.** | In `-p` mode the first prompt fires at launch, before servers have settled; the call still completed. This matches the docs (blocking events wait up to `MCP_TIMEOUT`). | SessionStart stays a command hook, since it is documented to be skipped at launch. |
| Is there a per-prompt id both parallel hooks see? | **Yes, `prompt_id`, from the first prompt on.** The docs list it as a common input field from 2.1.196. | 2026-10-05, Claude Code 2.1.289: a headless session fed two turns through `--input-format stream-json`. A command hook that saved its stdin got `prompt_id` in both UserPromptSubmit payloads, the first included, and `${prompt_id}` substituted into the `mcp_tool` input with the same value. | The warm handshake keys one claim file per prompt on `prompt_id`, so the command hook (the decider) and the server can never pair a decision with the wrong prompt, whichever runs first. A design keyed on timestamps cannot tell this prompt's decision from the last one's. |
| What does a warm call cost? | **About 31 ms for the whole hook phase on the second prompt**, against about 400 ms per prompt for the spawned hook on the same machine and corpus. The first prompt still paid the server's model load (about 460 ms). | Same session, Haiku, a 3-memory corpus with the dense model. Debug-log timestamps from the `mcp_tool` call to its parsed result: 463 ms, then 31 ms (the server measured 30 ms); the command hook skipped its spawn both times. A spawn-only control run of the same two turns: 397 ms and 410 ms. Not contended. | v1.42 ships warm recall opt-in (`hippo setup --warm`). The v2.0 default still needs 14 days of contended field data. |

## 3. Native memory

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Does `MEMORY.md` load through hippo's symlink by default? | **Yes.** | The floor token was reported with tools disabled. | Baseline. |
| Does `autoMemoryEnabled: false` stop the floor? | **Yes — `MEMORY.md` is not loaded at all.** | Project settings `autoMemoryEnabled: false`: the floor token was not reported. `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` did the same. | V2Q-3's option (A), "disable native writes per project", also disables hippo's floor. It is not a fallback. NAT-1's doctor line warns on it. |
| Does a redirected `autoMemoryDirectory` stop the floor? | **Yes — native reads the other directory's `MEMORY.md` instead.** | Set in project settings, in local settings, and via `--settings`: each time the other directory's token was reported and the floor token was not. | Redirecting native writes to an inbox (NAT-2's candidate) removes the symlink floor channel. The floor must then come from somewhere else. NAT-1's doctor line warns on it. |
| Does a CLAUDE.md `@.claude/memory/MEMORY.md` import load the floor? | **Yes, with auto memory off.** | The floor token was reported with `autoMemoryEnabled: false` and the import in a project CLAUDE.md. | NAT-2's candidate contract (import + redirect) delivers the floor. |
| What does each channel cost per subagent? | **The same on this version: both reach the subagent.** | A general-purpose subagent, asked with no tools to report the floor token, reported it under the symlink channel and under the import channel. A stream trace shows it made no tool call. The docs say the main conversation's auto memory is not loaded into subagents. | On 2.1.286 the floor rides every general-purpose spawn either way. A 17.9 KB floor is ~4.5k tokens per spawn under either channel, so the per-spawn cost does not separate them. Re-run this row before NAT-2 builds, since the docs predict otherwise. |
| What does a worktree read? | **Symlink: the main tree's copy. Import: the worktree's own branch copy.** | From a linked worktree whose `MEMORY.md` was edited, the symlink channel reported the main tree's token and the import channel reported the worktree's. Native auto memory keys its directory on the repository, so every worktree shares one. | Under the import channel a branch sees its own floor edits before they merge, and a stale branch sees a stale floor. NAT-2 has to choose deliberately. |
| Is there a per-turn native recall step? | **None observed.** | A topic file not named with its token in `MEMORY.md` never reached context across all probes. The docs say topic files are read on demand with file tools. | Native adds nothing per turn that competes with hippo's recall. |
| Does native stamp `modified:` into hippo files? | **Yes, when Claude edits a memory through the native memory path.** | One edit through `~/.claude/projects/<repo>/memory/` rewrote the frontmatter, adding `node_type`, `originSessionId` and `modified` under `metadata:` and reflowing the body. The same edit through the repo path `.claude/memory/` changed only the line edited. | Every edit through the native path is trust drift by construction. Field counts from NAT-1: 34 / 326 / 61 stamped files on hippo / em-growth-labs / Skyline. TND-6 and NAT-2 both inherit this. |

## 4. Plugin `userConfig`

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Does an option reach the plugin's MCP server? | **Yes, plain and sensitive.** | A probe plugin's MCP server received both options' values through `${user_config.KEY}` in its `env`. | SRF-4 can move the LLM key to a sensitive option for anything the MCP server runs. |
| Does it reach the Bash tool? | **No.** | `env` in a Bash tool call showed no `CLAUDE_PLUGIN_OPTION_*` variable. The docs agree. | hippo's LLM paths that run through Bash today (`memory.dream --generate`, capture triage) cannot read a sensitive key. They have to move into the MCP server or a hook before SRF-4 removes the plaintext config file. |
| Does it reach a command hook? | **Not with defaults alone.** | With only `default` values (no answers saved through the configuration dialog), a command hook saw no `CLAUDE_PLUGIN_OPTION_*` variable. The docs say every option is exported to hooks. Saved values were not probed, since sensitive values need the interactive dialog. | Treat hook reach as unverified. |
| Version floors | `/config` rows need 2.1.269; `options` pickers need 2.1.271 (docs). | This machine runs 2.1.286. | A string `options` picker stops the plugin loading on older versions, so CLM-2 stays a boolean (as planned). PLT-2 declares the floor. |

## 5. `PostToolBatch` and `async` hooks

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| What does `PostToolBatch` receive? | **The common fields plus `tool_calls`, each `{tool_name, tool_input, tool_response, tool_use_id}`.** It fired once for a batch of two parallel reads. | The hook wrote its stdin to a file. The docs also list `batch_id` and, per call, `success` and `output`; none of them were present. | HOT-5 option (a)/(b) can read `tool_input` paths per batch. It must not depend on `batch_id` or `success`. |
| What does `async: true` do? | **Runs in the background without blocking; abandoned if the process exits first.** | With a 3 s async hook in a longer turn, the hook started at 6.2 s, finished at 9.6 s, and the run exited at 12.3 s. With a 6 s hook in a shorter run, the hook never finished. | HOT-5 option (c) suits fire-and-forget logging in interactive sessions. Headless runs may drop the tail. |

## 6. Which Claude Code is running

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Can a hook tell which Claude Code version runs it? | **Yes, from `AI_AGENT`, which is not documented.** It reads `claude-code_<major>-<minor>-<patch>_<role>`. | 2026-10-03: a headless `claude -p` on 2.1.289 ran a SessionStart and a UserPromptSubmit command hook that dumped their env; both saw `AI_AGENT=claude-code_2-1-289_harness`. A Desktop Code-tab Bash tool on 2.1.286 sees `claude-code_2-1-286_agent`. The plugins reference documents no version variable and no minimum-version manifest field. | PLT-2 parses `AI_AGENT` strictly (the role suffix varies) and treats anything else as unknown. doctor prints the running version against the declared floor (2.1.269), and the SessionStart integrity lane names an older harness. |
