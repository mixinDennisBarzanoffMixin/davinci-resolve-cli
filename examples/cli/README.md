# CLI examples

These examples use `dvr`, the short alias of `davinci-resolve`. They are written
for Bash 4+ with `jq` installed.

- `resolve-inventory.sh` prints a TSV inventory of video timeline items. It is
  read-only and demonstrates JSON input, raw stdout, `jq`, and `pipefail`.
- `inspect-items.sh` feeds video-track item indexes through a Bash loop and
  emits one JSON object per line. It is read-only.
- `confirmed-action.sh` is a template for an explicit two-call confirmation
  flow. It does not name a source-media operation and must be given a protected
  tool/action intentionally.
- `timeline-query.json` is a minimal `@file` request for a read-only compound
  call.

Run from anywhere after installing/linking the CLI:

```bash
bash examples/cli/resolve-inventory.sh
bash examples/cli/inspect-items.sh >items.jsonl
dvr timeline get_items --input @examples/cli/timeline-query.json
```

The scripts never write to source media. Review the confirmation template before
using it against a live project.
