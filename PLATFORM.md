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

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Does a typed `/hippo:doctor` run in the Desktop Code tab? | **Not yet observed.** | Pending a person typing it: computer use cannot drive the Claude app itself, and a relayed cross-session message is not a typed command. The docs say `/` lists "skills from any installed plugins" in Desktop. | CLM-8 (retire the Desktop routing note) stays gated on this one receipt. |
| Does plugin install work end to end on Desktop, bootstrap included? | **Not run.** | Running it would reinstall the plugin on the maintainer's machine. | Unchanged: the README's install step keeps its terminal route until a clean-profile run records this row. |

## 2. `mcp_tool` hooks

| Question | Answer | Receipt | Consequence |
|---|---|---|---|
| Do `${prompt}`, `${session_id}` and `${cwd}` substitute for UserPromptSubmit? | **Yes.** | A stub server received the full prompt text, the harness session id and the working directory as its arguments. | HOT-6's session keying needs no socket and no key of hippo's own. |
| Does `plugin:hippo:hippo` reach hippo's server? | **Yes.** | hippo's recall ledger logged an MCP-channel row whose query was the substituted prompt. | The plugin-scoped name works as documented. |
| Does the tool's text reach context? | **Only as JSON.** A reply of `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": …}}` reached the model. A plain-text reply was logged as hook success and then dropped. | Two probes with the same stub, differing only in reply shape. The debug log shows "provided additionalContext" for the JSON reply and nothing for the plain one. The docs say text is "read the same way" as command-hook stdout, where plain text is added. | HOT-6's `recall_hook` tool must return the JSON envelope, not the formatted block. |
| What does a call into hippo's server cost? | **~340 ms, cold.** | The first prompt of a fresh process: hippo's in-process recall logged 229 ms; the hook's call-to-result was ~340 ms. hippo's own spawned command hook, in the same prompt, took ~370 ms. The hooks ran in parallel. A warm (second-prompt) call was not measured: `-p` runs one prompt per process. | Warm latency is the HOT-6 gate. It needs an interactive session, which the v1.42 opt-in collects. |
| Is a failure visible? | **As a non-blocking error.** | A hook naming a tool the server does not have logged `mcp_tool hook error: unknown tool` at ERROR level; the prompt continued and the model saw nothing. | A degraded warm path is silent to the model. HOT-6's `path=warm\|spawn\|failed` logging is the only signal, so it is required, not optional. |
| What happens while the server is still connecting? | **The hook waits.** | In `-p` mode the first prompt fires at launch, before servers have settled; the call still completed. This matches the docs (blocking events wait up to `MCP_TIMEOUT`). | SessionStart stays a command hook, since it is documented to be skipped at launch. |

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
