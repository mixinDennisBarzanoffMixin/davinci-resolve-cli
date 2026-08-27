# Existing batch CLI gap audit

This audit covers `src/batch_cli.py` as it existed before the general-purpose
`davinci-resolve` CLI was added. It explains why expanding the batch runner is
not enough to provide a Bash-composable interface to the repository.

## What the batch runner does well

The batch runner is a useful, intentionally narrow frontend to two existing
engines:

- `plan` and `run` discover media from filesystem paths and drive durable,
  slice-based media-analysis jobs.
- `status`, `list`, `resume`, and `cancel` manage those jobs in
  `<project-root>/jobs.sqlite`.
- `plan-spec` and `apply` preview or reconcile a declarative Resolve project
  specification.
- `run` and `resume` trap `SIGINT`, cancel the job, and return 130.
- Batch state is durable, work is bounded to at most 25 clips per slice, and a
  partial job has a distinct exit status.
- The analysis engine forces persisted, non-dry-run execution. It writes
  analysis artifacts rather than altering source media.

Those are good properties for unattended analysis. They are not a general CLI
contract.

## Missing shell interface

| Area | Existing behavior | Gap for a complete CLI |
| --- | --- | --- |
| Resolve coverage | Filesystem analysis and project specs only | No access to the compound tools, granular API wrappers, or offline advanced tools |
| Discovery | Static `argparse` help | No machine-readable tool/action catalogue or parameter schemas |
| Input | Positional paths plus a few fixed flags | No JSON stdin, `@file`, `key=value`, dotted keys, or arbitrary action parameters |
| Output | Human text or `--json` | No explicit JSON/JSONL distinction, raw field extraction, or shell-assignment output |
| Streams | Progress is flushed line by line | Diagnostics and ordinary human output are also written to stdout, so stdout is not a clean data channel |
| Errors | 0, 2, 3, and 130 | JSON error envelopes are not normalized across the rest of the repository; usage errors remain `argparse`'s behavior |
| Confirmation | `apply` offers `--dry-run`; analysis runs directly | No general confirmation-token replay mechanism for protected Resolve actions |
| Composition | Paths must be argv entries | Cannot consume `find -print0`, a JSON-producing command, or one request per JSONL line |
| Selection | Analysis accepts raw files/directories | Cannot target the selected Resolve clip, bin, project, timeline, sequence, or an explicit set of Resolve clip IDs even though the server engine can |
| Analysis controls | Exposes depth, trust, and summary style | Most analysis parameters are unavailable, including explicit visual/transcription/writeback controls and capability overrides |
| Job addressing | Every status operation needs both job ID and project root | No configured default root, environment override, job URI, or lookup by ID |
| Retry policy | `resume` requeues only rows left in `running`; failed rows stay failed | No retry-failed, retry limit/backoff, or failed-subset export |
| Cancellation | `SIGINT` marks the job canceled between slices | The clip currently executing is not preempted, and there is no cancel timeout/escalation |
| Status scale | `status` uses the engine defaults and `list` has a numeric limit | No CLI switches to omit clip/event detail, paginate, follow, or wait for a terminal state |
| Automation | No completion generator | No Bash/Zsh/Fish completions for commands, tools, actions, or known options |
| Offline work | None | Cannot call `.drp`, `.drt`, `.drx`, database, conform, editorial, delivery, or provenance actions from the advanced server |
| Server lifecycle | None | Cannot use the same executable for setup, doctor, server, and CLI calls |

## Output-contract problems

`--json` has two meanings. `plan`, `status`, `list`, `plan-spec`, and `apply`
emit one JSON document, while followed `run` and `resume` emit a sequence of JSON
objects, one per line. The latter is JSONL, not a single JSON document. A script
cannot choose a parser from the flag alone.

Human-readable errors and progress both go to stdout. That makes commands such
as the following unsafe because an error message becomes pipeline data:

```bash
davinci-resolve-mcp batch status "$job_id" --project-root "$root" |
  awk '{print $2}'
```

A general CLI needs a strict rule: data on stdout, diagnostics on stderr. JSON
must mean one complete JSON value, while JSONL must mean one independently
parseable value per line.

## Coverage and safety gaps

The batch runner creates records from raw paths, so those records have no
Resolve `clip_id` or Media Pool identity. This is correct for offline file
analysis, but it cannot provide Resolve metadata or Media Pool marker writeback.
Calling that path "full media analysis" would therefore be misleading. A
complete CLI must expose the compound `media_analysis` actions as well as this
filesystem job runner so callers can select a Resolve clip/bin/project and
complete any deferred `commit_vision` step.

The runner also has no generic understanding of tool annotations or
confirmation envelopes. Protected actions elsewhere in the server return a
confirmation token. Bash callers need either an explicit second call carrying
that token or an opt-in `--yes` replay; non-interactive execution must never
silently turn a preview into a mutation.

## Recommended boundary

Keep `src/batch_cli.py` as a specialized durable-job frontend and expose it as
`dvr batch ...`. Put the universal behaviors in the top-level CLI:

1. Discover compound, granular, and advanced tools dynamically.
2. Normalize `key=value`, dynamic flags, JSON, stdin, and `@file` into one
   request object.
3. Normalize success/error envelopes and stdout/stderr behavior.
4. Provide `json`, `jsonl`, `raw`, and `shell` serializers.
5. Apply one confirmation policy before dispatch, independent of surface.
6. Preserve each specialized command's native exit code when using passthrough
   commands such as `batch`, `setup`, `doctor`, or `server`.

That boundary gives existing batch users compatibility while making every
repository capability available to ordinary shell scripts.
