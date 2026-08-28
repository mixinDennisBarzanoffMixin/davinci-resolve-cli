# Bash-composable DaVinci Resolve CLI

`davinci-resolve`, `davinci-resolve-cli`, and `dvr` are equivalent command
names. This guide uses `dvr` because it keeps pipelines readable.

The CLI exposes three surfaces through one executable:

- **compound** — the curated action-dispatch tools from `src/server.py`;
- **granular** — one command per Resolve scripting API wrapper from
  `src/granular/`;
- **advanced** — offline `.drp`, `.drt`, `.drx`, database, conform, editorial,
  delivery, and provenance tools from `resolve-advanced/`.

It also keeps the existing setup, diagnosis, server, control-panel, and durable
batch workflows available as passthrough subcommands.

## Install from the fork

Python 3.10 or newer and Node.js 18.17 or newer are required. DaVinci Resolve
must allow external scripting for live compound and granular calls. Advanced
commands work on files and do not require Resolve to be running.

```bash
git clone https://github.com/mixinDennisBarzanoffMixin/davinci-resolve-cli.git
cd davinci-resolve-cli
npm install
npm link

dvr setup
dvr doctor
```

`npm link` installs all three command names from the checkout. For a local-only
installation, replace it with `npm install -g .`. Run `dvr doctor` before
automating live calls; it checks Python, Resolve's scripting module, connection
settings, and the in-app bridge.

## Command grammar

```text
dvr tools [--surface compound|granular|all]
dvr describe TOOL [ACTION] [--surface compound|granular]
dvr advanced describe TOOL
dvr actions TOOL
dvr prompts
dvr prompt NAME [PARAM ...]
dvr resources
dvr resource URI

dvr call TOOL [ACTION] [PARAM ...]
dvr TOOL ACTION [PARAM ...]
dvr granular TOOL [PARAM ...]
dvr advanced TOOL ACTION [PARAM ...]

dvr batch <plan|run|status|list|resume|cancel|plan-spec|apply> [ARGS ...]
dvr setup [ARGS ...]
dvr doctor [ARGS ...]
dvr server [ARGS ...]
dvr control-panel [ARGS ...]
dvr completion <bash|zsh|fish>
dvr session
```

`dvr call` targets the compound surface unless `--surface` selects another
surface. The shorter `dvr TOOL ACTION` form is an exact compound-call shortcut.
Granular tools do not have a separate action argument; their function
parameters follow the tool name. Advanced tools always have an action.

```bash
# Curated live surface
dvr resolve_control get_version

# One live API wrapper
dvr granular get_project_setting setting_name=timelineFrameRate

# Offline artifact surface (schema uses camelCase here)
dvr advanced drt parse drtPath=/path/to/timeline.drt
```

Start with discovery instead of copying a possibly stale list from a document:

```bash
dvr tools --surface compound
dvr tools --surface granular | jq -r '.tools[].name'
dvr describe timeline
dvr describe timeline get_items
dvr actions timeline
dvr describe get_project_setting --surface granular
dvr advanced actions project_read
dvr prompts
dvr resources
```

`prompts`/`prompt` and `resources`/`resource` expose the MCP prompt and resource
catalogues without requiring an MCP host. Prompt arguments use the same
parameter grammar as tool calls. Quote resource URIs that contain shell
metacharacters such as `?`, `&`, or `#`.

`describe TOOL ACTION` reports registered action help when present, then falls
back to the tool's documented signature, and finally to an explicitly unknown,
open parameter object. It never presents an inferred compound-action signature
as a strict runtime JSON schema.

## Parameters

All of these forms create the same parameter object:

```bash
dvr timeline get_items track_type=video index=1
dvr timeline get_items --track-type video --index 1
dvr timeline get_items --set track_type=video --set index=1
dvr call timeline get_items --input '{"track_type":"video","index":1}'
```

The accepted forms are:

- `key=value` for concise shell calls;
- `--key value` for familiar option syntax;
- `--flag` for a boolean `true` value;
- `--set key=value` (or `-s`) when a key might look like a global option;
- `--input JSON` (or `-i`) for a complete JSON object;
- `--input @path/to/request.json` to read that object from a file;
- `--input -` to read one JSON object from stdin.

JSON literals are decoded when unambiguous. Numbers, booleans, `null`, arrays,
and objects therefore keep their types; other values remain strings. Quote
shell metacharacters and values containing whitespace.

```bash
dvr render set_settings \
  'custom_name=Review v07' \
  target_dir=/Volumes/Delivery \
  export_video=true \
  video_quality=85
```

Dotted keys build nested objects. Later command-line values override the same
key from `--input`, which makes checked-in request files easy to specialize:

```bash
dvr media_analysis analyze_project \
  --input @analysis-defaults.json \
  --set vision.summary_style=concise \
  --set depth=deep
```

Use stdin for generated requests:

```bash
jq -n --arg color Blue '{color: $color, include_hidden: false}' |
  dvr call timeline_markers get_all --input -
```

Do not combine two stdin consumers. If a command needs media bytes or a second
stream, put the request in a file and use `--input @request.json`.

## Output formats

For universal tool calls, stdout is reserved for result data. Progress,
warnings, and diagnostics go to stderr, so redirecting or piping stdout does
not capture prose. Passthrough commands retain their existing stream contract:
use `dvr batch --json ...` for machine-readable batch output, and remember that
a followed batch run produces JSONL progress events despite the legacy flag
name. `dvr server` reserves stdout for MCP JSON-RPC.

```text
--output json     one JSON value (default)
--output jsonl    one compact JSON value per line
--output raw      only the selected scalar/value
--output shell    shell-escaped KEY=value assignments
--pretty          indent JSON (default on a terminal)
--compact         compact JSON (default when redirected)
--raw PATH        extract a dotted result path before serialization
```

The short form of `--output` is `-o`. `--raw PATH` uses dot-separated object
keys and numeric array components, such as `jobs.0.id`. Combine it with `-o raw`
when the value will feed another program. Raw scalars print as text; objects and
arrays print as compact JSON.

```bash
project_name=$(dvr project_manager get_current --raw name -o raw)
printf 'Current project: %s\n' "$project_name"

dvr timeline get_items track_type=video index=1 -o json |
  jq -r '.items[] | [.id, .name] | @tsv'

dvr batch run /Volumes/Card_A --json |
  jq -c 'select(.event == "clip_done")'
```

JSONL deliberately remains valid a line at a time. A single ordinary call
produces one line; streaming commands may produce multiple event lines. Use
`jq -s` when an array is needed after the stream completes.

Shell output flattens leaves to uppercase underscore-separated variable names
and shell-quotes values. Arrays and objects are JSON; a top-level scalar is
named `RESULT`. It is intended for controlled local results:

```bash
dvr project_manager get_current -o shell >current-project.env
set -a
. ./current-project.env
set +a
```

Never source shell output from an untrusted project or artifact. JSON plus `jq`
is the safer default when values can contain attacker-controlled text.

## Pipelines

Read-only inventory:

```bash
dvr timeline get_items track_type=video index=1 -o json |
  jq -r '.items[] | [.id, .name, .start, .end] | @tsv'
```

Run one offline read for each database. `xargs -0` preserves spaces and
newlines:

```bash
find /path/to/resolve-databases -name Project.db -print0 |
  xargs -0 -n1 sh -c \
    'dvr advanced project_read report "projectDb=$1" --output jsonl' _
```

When a parameter must be joined to the value rather than supplied as a separate
argument, use a small Bash loop; it is clearer and does not depend on `xargs`
placeholder parsing:

```bash
dvr timeline get_items track_type=video index=1 -o json |
  jq -r '.items | keys[]' |
  while IFS= read -r item_index; do
    dvr timeline_item get_name track_type=video track_index=1 \
      "item_index=$item_index" -o jsonl
  done
```

Build a request file once and fan out variations:

```bash
jq -n '{track_type:"video", include_disabled:false}' >request.json

for index in 1 2 3; do
  dvr timeline get_items -i @request.json "index=$index" -o jsonl
done | jq -s 'map(.items) | flatten'
```

Offline inspection is equally composable:

```bash
dvr advanced project_read report projectDb=/path/to/Project.db -o json |
  jq '.timelines'
```

## Persistent JSONL session

`dvr session` keeps one Python process, event loop, tool registries, and live
Resolve proxy warm across sequential compound and granular requests. It is for
low-latency shell coprocesses and request files; it deliberately does not run
requests concurrently. Advanced Node actions remain ordinary `dvr advanced`
calls and are not accepted inside this Python session.

Each nonblank input line is either a direct request or a complete CLI `argv`
request. Every line produces one correlated response envelope, and malformed or
refused requests do not terminate the session:

```jsonl
{"id":"current","tool":"timeline","action":"get_current","params":{}}
{"id":"items","tool":"timeline","action":"get_items","params":{"track_type":"video","index":1}}
{"id":"granular","surface":"granular","tool":"get_project_unique_id","params":{}}
{"id":"catalog","argv":["actions","timeline"]}
{"id":"done","quit":true}
```

```bash
dvr session <requests.jsonl >responses.jsonl
jq -c 'select(.ok == false)' responses.jsonl
```

Responses contain `id`, `ok`, and either `result` or a structured `error` plus
`exit_code`. A request cannot start another session or use `--input -`, because
that would consume the protocol stream. Direct requests may set `yes:true` for
the same exact-token confirmation replay used by ordinary `--yes` calls.

## Exit codes and `set -e`

The universal CLI uses these statuses:

| Code | Meaning |
| ---: | --- |
| 0 | operation succeeded |
| 1 | tool returned an error/failure envelope |
| 2 | invalid command, argument, or input document |
| 3 | internal error or missing dependency |
| 130 | interrupted by the user |

Connection failures, advanced-tool exceptions, and structured error envelopes
are failures even if a transport successfully returned the envelope.

Passthrough commands preserve their native exit status. In particular, the
existing batch runner uses:

| Code | Batch meaning |
| ---: | --- |
| 0 | completed successfully |
| 2 | completed with one or more clip/spec failures |
| 3 | fatal error or non-terminal failure |
| 130 | interrupted and canceled with `SIGINT` |

Use ordinary shell control flow rather than parsing error text:

```bash
set -euo pipefail

if ! project_json=$(dvr project_manager get_current -o json); then
  printf 'Resolve is unavailable\n' >&2
  exit 1
fi

jq -e '.id != null' <<<"$project_json" >/dev/null
```

With a pipeline, enable `pipefail`; otherwise Bash normally reports only the
last process's status.

## Safety and confirmation

The CLI does not weaken the repository's source-media rules. Reading and
analyzing source media is allowed. Modifying, transcoding, proxying, relinking,
replacing, or creating derivatives of source media requires an explicit request
for that operation. Analysis outputs belong in sidecars, scratch space, or the
configured analysis root.

Protected Resolve operations return a confirmation-required envelope and a
token. That first call exits 1 because the requested mutation did not run, so a
`set -e` script must capture that expected status. The safest automation is a
visible two-step flow:

```bash
if preview=$(dvr timeline delete_track track_type=video index=3 -o json); then
  printf 'Action completed without requesting confirmation\n' >&2
  exit 0
else
  status=$?
  (( status == 1 )) || exit "$status"
fi
printf '%s\n' "$preview" | jq . >&2

token=$(jq -er '.confirm_token' <<<"$preview")
dvr timeline delete_track track_type=video index=3 confirm_token="$token"
```

`--yes` may replay one confirmation-token response automatically. It is an
explicit opt-in for already-reviewed automation, not a global disable switch:

```bash
dvr timeline delete_track track_type=video index=3 --yes
```

The replay is bounded to the returned token and request. It must not approve a
different action, invent missing confirmation data, accept interactive prompts,
or retry arbitrary failures. Prefer a dry run or plan action where one exists,
and keep `--yes` out of aliases and shell profiles.

For grading existing timelines, inspect representative Resolve-rendered frames
first and preserve a recoverable grade version. For destructive timeline work,
retain the server's automatic archive/confirmation behavior rather than trying
to bypass it in Bash.

## Setup, doctor, batch, and server

These commands pass remaining arguments to the existing implementation:

```bash
dvr setup --clients manual
dvr setup --dry-run --clients all
dvr doctor

dvr batch plan /Volumes/Card_A --depth standard --json
dvr batch run /Volumes/Card_A --depth standard --no-follow --json
dvr batch status "$job_id" --project-root "$project_root" --json

dvr server                         # compound MCP over stdio
dvr server --full                  # granular MCP over stdio
dvr server --transport streamable-http
dvr control-panel
```

The `server` command remains an MCP compatibility mode. Ordinary shell calls do
not launch an MCP client or speak JSON-RPC over stdio; they invoke the same tool
implementations through the CLI dispatcher.

## Shell completion

Generate completion from the executable so it tracks the installed tool and
action catalogue. Completion includes compound, granular, and advanced tool and
action names; compound documented parameters, granular schema flags, and common
enum values are completed where known. Advanced Zod parameter flags are not yet
extracted.

```bash
# Bash, current session
source <(dvr completion bash)

# Bash, persistent (choose a directory sourced by your Bash installation)
dvr completion bash >~/.local/share/bash-completion/completions/dvr

# Zsh
mkdir -p ~/.zfunc
dvr completion zsh >~/.zfunc/_dvr

# Fish
dvr completion fish >~/.config/fish/completions/dvr.fish
```

Completion should remain side-effect free: it may enumerate static catalogues
and schemas, but it should not connect to Resolve, open a project, or inspect a
user artifact merely because Tab was pressed.

## Migrating from MCP

An MCP call contains a tool name and an argument object. Translate them
directly:

```json
{
  "name": "timeline",
  "arguments": {
    "action": "get_items",
    "params": {"track_type": "video", "index": 1}
  }
}
```

becomes either:

```bash
dvr timeline get_items track_type=video index=1
```

or, for generated data:

```bash
jq -n '{track_type:"video", index:1}' |
  dvr call timeline get_items --input -
```

The mappings are:

| MCP use | CLI equivalent |
| --- | --- |
| Compound server tool + `action`, `params` | `dvr TOOL ACTION PARAM...` |
| Granular server tool + arguments | `dvr granular TOOL PARAM...` |
| Advanced server tool + `action`, `args` | `dvr advanced TOOL ACTION PARAM...` |
| List tools | `dvr tools --surface ...` |
| Read schema/help | `dvr describe TOOL [ACTION] --surface ...` |
| List/call prompts | `dvr prompts`; `dvr prompt NAME PARAM...` |
| List/read resources | `dvr resources`; `dvr resource URI` |
| Start the old server | `dvr server` or `dvr server --full` |

MCP host approval prompts do not exist in a non-interactive Bash process.
Scripts must handle the returned confirmation envelope or opt in with `--yes`.
Likewise, host-chat vision cannot be assumed: if media analysis returns
`pending_host_vision_analysis`, read the returned frame paths, produce the
required schema, and call `media_analysis commit_vision`; or explicitly request
a technical-only analysis with `include_visuals=false`.
