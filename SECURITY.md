# Security Policy

## Reporting a vulnerability

**Please report security issues privately — do not open a public issue, PR, or
discussion for a suspected vulnerability.**

Use GitHub's private vulnerability reporting:

> **[Report a vulnerability](https://github.com/youknowfred/hippo/security/advisories/new)**
> (the repository's **Security** tab → *Report a vulnerability*)

This opens a private advisory visible only to you and the maintainers. Please include:

- what the issue lets an attacker do, and the impact;
- the affected component (a hook, the recall/index path, the trust gate, the MCP
  server, a shipped starter pack, a skill);
- reproduction steps or a proof of concept, and the hippo version (`git describe`
  or the plugin manifest `version`).

You can expect an acknowledgement within **7 days** and, for a confirmed issue, a
coordinated fix and disclosure. There is no bounty program; credit is given in the
advisory unless you prefer to remain anonymous.

## Supported versions

hippo installs as a single rolling Claude Code plugin; security fixes land in the
latest release, and installs are expected to track it. Only the most recent
released line receives security updates.

| Version | Supported          |
|---------|--------------------|
| 1.7.x   | :white_check_mark: |
| < 1.7   | :x: (please upgrade) |

## Scope and threat model

hippo is a **local** agent-memory tool: its hooks run on your machine, always exit
0, never call the network on the per-prompt hot path, and make no autonomous bulk
writes. The security surface that matters for a *public* deployment is therefore
narrower than a networked service, and centers on **untrusted memory content**:

- **Trusting a shared/cloned corpus.** Memories injected from a corpus you did not
  author are consent-gated and rendered as demarcated, quoted data with a
  foreign-corpus banner; a trusted corpus is re-checked (per-file fingerprint) and
  re-prompts on material change. If you can bypass the trust gate — inject memory
  text from an unconsented or changed corpus without the banner/quarantine — that
  is a vulnerability. See `plugin/memory/README.md` (the trust model) for the
  design.
- **Credentials committed into memory.** Memories are committed to git and recalled
  forever, so a secret pasted into a memory body is a real exposure. hippo lints for
  known credential shapes at write time and in `/hippo:doctor`, and a CI gate scans
  the shipped tree — but the lint is a *warning*, not a guarantee. If you find a
  credential shape that should be caught but isn't, please report it.
- **Prompt-injection via memory text.** Memory bodies are untrusted input to the
  agent; report any path where injected memory content escapes the quoted-data
  framing in a way that could steer the agent.

### If you accidentally committed a secret

This is operational, not a vulnerability report: remove it, **rotate the
credential**, and scrub it from git history. Follow *Purging a memory (and
scrubbing its history)* in
[`plugin/memory/README.md`](plugin/memory/README.md) for the exact procedure.
