---
name: davinci-cli
description: Use the dvr command-line interface to inspect or automate DaVinci Resolve, edit timelines, grade, manage media and projects, render, run durable batches, or work offline with .drp/.drt/.drx files. This fork is CLI-first; prefer dvr over its optional MCP compatibility transports.
---

# DaVinci Resolve CLI

Use the user's CLI-first fork through the global `dvr` command. MCP is an
optional transport over the same underlying handlers, not the primary agent
interface. Prefer `dvr` because it is compact, shell-composable, discoverable,
and produces machine-readable output without loading hundreds of MCP schemas.

## Start and discover

For work against a running Resolve session, begin with:

```text
dvr doctor
dvr --compact resolve_control get_version
dvr --compact resolve_control runtime_mode
```

Discover the installed build instead of guessing names or copying a stale
catalog:

```text
dvr tools --surface all
dvr actions TOOL
dvr describe TOOL ACTION
dvr advanced actions TOOL
dvr prompts
dvr resources
```

Use `dvr prompt NAME` and `dvr resource URI` when detailed workflow knowledge
is needed. They expose the repository's maintained prompts and references
without requiring the many per-domain skills to be globally installed.

## Choose the smallest CLI surface

| Need | Form |
|---|---|
| Guarded live workflow | `dvr TOOL ACTION key=value ...` |
| One live Resolve API wrapper | `dvr granular TOOL key=value ...` |
| Offline artifact/database work | `dvr advanced TOOL ACTION key=value ...` |
| Durable analysis or project-spec job | `dvr batch ...` |
| Product-video automation | `dvr production ...` |

Pass complex requests with `--input JSON`, `--input @file`, or `--input -`.
Use `--compact` for agent-readable JSON, `--raw PATH -o raw` for one scalar,
and JSONL for streaming operations. Keep stdout as data; diagnostics belong on
stderr.

## CLI versus MCP

- `dvr` and the MCP servers call the same registered Python or Node handlers,
  so validation, safety gates, and operation semantics stay aligned.
- CLI is the default for Codex: fewer exposed schemas, easy discovery, shell
  pipelines, durable batch execution, and precise JSON output.
- Use MCP only when the user explicitly requests it, when the host must receive
  MCP-native image/content blocks, or when no usable shell is available.
- Do not start an MCP server merely to perform a command that `dvr` exposes.

## Safety and correctness

- Inspect before mutating and use the CLI's guarded compound surface unless a
  granular or offline action is specifically needed.
- Never modify, transcode, proxy, relink, or derive source media unless the user
  explicitly asks. Put generated artifacts in a separate output location.
- Before grading, inspect Resolve-rendered frames and preserve a recoverable
  grade version.
- Check the exact Resolve build with `resolve_control get_version`; API support
  changes between patch releases.
- A failed connection is a diagnosis task, not permission to restart, close,
  or reconfigure Resolve.
