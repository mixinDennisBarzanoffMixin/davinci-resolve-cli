#!/usr/bin/env bash
set -euo pipefail

command -v dvr >/dev/null || {
  printf 'dvr is not installed or not on PATH\n' >&2
  exit 127
}
command -v jq >/dev/null || {
  printf 'jq is required\n' >&2
  exit 127
}

dvr timeline get_items track_type=video index=1 --output json |
  jq -r '.items | keys[]' |
  while IFS= read -r item_index; do
    dvr timeline_item get_name track_type=video track_index=1 \
      "item_index=$item_index" --output jsonl
  done
