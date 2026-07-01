---
name: feedback_serena_tools_only
description: Serena MCP (or any code-navigation MCP) is for code navigation tools ONLY (LSP, symbol search, rename) — never for memory. All memory management uses this native memory system.
metadata:
  type: feedback
---

If a code-navigation MCP server (e.g. Serena) is active in this project, use it only for code
navigation tools (symbol search, LSP rename, find-references, etc.). Never use its own memory
tools (write_memory, read_memory, edit_memory, or equivalents) for persisting knowledge.

**Why:** Dual memory systems waste context window — a second memory store's content gets loaded
alongside this native memory system's, doubling context consumption for duplicated content.
Additionally, the two memory stores drift out of sync, creating conflicting information over time.

**How to apply:** When you need to persist knowledge across conversations, always use this
native memory system (`/memory:new`). When you need LSP-powered code intelligence, use the
code-navigation MCP. The two concerns are cleanly separated: the MCP is for code tools, this
plugin is for memory.
