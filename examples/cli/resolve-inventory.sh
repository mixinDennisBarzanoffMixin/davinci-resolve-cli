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

jq -n '{track_type:"video", include_disabled:true}' |
  dvr timeline get_items --input - --set index=1 --output json |
  jq -r '["id", "name", "track", "start", "end"],
         (.items[] | [.id, .name, 1, .start, .end]) |
         @tsv'
