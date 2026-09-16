---
name: davinci-cli
description: Use the dvr command-line interface to inspect or automate DaVinci Resolve, edit timelines, grade, manage media and projects, render, run durable batches, or work offline with .drp/.drt/.drx files. CLI-first; prefer dvr over optional MCP transports.
---

# DaVinci Resolve CLI

Use the global `dvr` command. It calls the same registered handlers as the MCP
servers with much less schema overhead. Keep stdout machine-readable; diagnostics
belong on stderr.

## Fast path: what is open?

Start with one read-only, bounded snapshot of Resolve, the project, current
timeline, tracks, clips, markers, and Media Pool:

```text
dvr --compact inspect
```

The default caps item samples at 10 per track/folder and Media Pool depth at 1.
Adjust with `item_limit=N` and `folder_depth=0..4`. Use `full=true` only when an
unbounded payload is needed. Absolute media paths are omitted by default; add
`include_paths=true` only when path diagnosis is relevant.

On an older build without `inspect`, use this staged fallback:

```text
dvr --version
dvr --compact resolve_control runtime_mode
dvr --compact resolve_control get_version
dvr --compact resolve_control get_page
dvr --compact project_manager get_current
dvr --compact timeline list
dvr --compact timeline get_current
dvr --compact media_pool probe_media_pool depth=1
dvr --compact timeline probe_timeline_structure include_clip_properties=false
```

Stop after a failed prerequisite. If no project/current timeline exists, report
the state and listed candidates. Do not load or switch projects, databases,
pages, or timelines merely to inspect them.

`probe_timeline_structure` can be enormous and exposes absolute media paths.
Narrow with `track_types`, or query one track with
`timeline get_items track_type=video index=1`.

## Cheap discovery

```text
dvr search timeline
dvr actions timeline
dvr describe timeline probe_timeline_structure
dvr advanced actions drp
```

- `search` is bounded name/action discovery.
- `actions TOOL` is for compound tools.
- Run `describe TOOL ACTION` before an unfamiliar mutation.
- Granular tools use `dvr describe TOOL --surface granular`.
- `dvr tools --surface all` is a last resort; it is intentionally huge.
- Use `dvr prompts` and `dvr prompt davinci_resolve_workflow` for maintained
  workflow guidance; use `dvr prompt analyze_media` for analysis policy.

## Calls, inputs, and output

| Need | Form |
|---|---|
| Guarded live workflow | `dvr TOOL ACTION key=value ...` |
| One direct Resolve wrapper | `dvr granular TOOL key=value ...` |
| Offline artifact/database work | `dvr advanced TOOL ACTION key=value ...` |
| Durable job | `dvr batch ...` |
| Product-video automation | `dvr production ...` |

Prefer `key=value` for simple parameters. For structured payloads use
`--input @file` or `--input -`; inline JSON quoting is shell-dependent.
Inputs merge by layer, not token position: input files/stdin first, then `--set`,
then positional/flag parameters. Later layers override earlier ones.

PowerShell rules:

- Quote file tokens: `--input '@request.json'`.
- Prefer `'JSON' | dvr TOOL ACTION --input -` for generated JSON.
- Use `ConvertFrom-Json` when `jq` is absent; never assume either dependency.
- `-o shell` is POSIX syntax and must not be sourced in PowerShell.
- Install completion with the output of `dvr completion powershell`.

Useful output controls:

```text
dvr --compact --data-only TOOL ACTION
dvr --raw jobs.0.id TOOL ACTION
```

`--data-only` removes `_operation` envelopes recursively. `--raw PATH` already
implies raw output. Trust exit status: 0 success, 1 tool error/refusal (including
confirmation required), 2 usage/input, 3 internal/dependency, 130 interruption.

For three or more reads not covered by `inspect`, use `dvr session` and JSONL.
It keeps imports, registries, and the Resolve connection warm; repeated one-shot
startup is much slower. Run sessions through redirected pipes, not an interactive
PTY (which may echo/wrap protocol text). The session process can exit 0 even when
individual requests fail; inspect every response envelope's `ok` and `exit_code`.

## Visual inspection trap

Media Pool thumbnails and `timeline thumbnail_contact_sheet` are source-derived,
not proof of Fusion or grade output. Retrieval may require the Color page and may
be unavailable on some builds. Do not change pages for a structural inspection.
For a visual claim about a grade/Fusion result, use a Resolve-rendered frame or
`gallery_stills grab_and_export` after the task authorizes that workflow.

## Safety

- Inspect before mutating; prefer guarded compound actions.
- Never modify, transcode, proxy, relink, or derive source media unless explicitly
  requested. Put generated artifacts in a separate output location.
- Before grading, inspect Resolve-rendered frames and preserve a recoverable grade.
- Check `resolve_control get_version`; API support changes between patch releases.
- A failed connection does not authorize restarting, closing, or reconfiguring
  Resolve. Run `dvr doctor` only for installation/connection diagnosis.
- Resolve-target analysis normally persists results and writes project metadata/
  markers. Host vision is incomplete until `media_analysis commit_vision`;
  `pending_host_vision_analysis` is not success.
