#!/usr/bin/env bash
set -euo pipefail

if (( $# < 2 )); then
  printf 'usage: %s TOOL ACTION [key=value ...]\n' "$0" >&2
  exit 2
fi

command -v dvr >/dev/null || {
  printf 'dvr is not installed or not on PATH\n' >&2
  exit 127
}
command -v jq >/dev/null || {
  printf 'jq is required\n' >&2
  exit 127
}

tool=$1
action=$2
shift 2

# First call: obtain the exact preview/confirmation envelope. Confirmation is a
# tool refusal until replayed, so exit 1 is expected here even under `set -e`.
if preview=$(dvr call "$tool" "$action" "$@" --output json); then
  printf '%s\n' "$preview"
  printf 'The action completed without requesting confirmation; nothing was replayed.\n' >&2
  exit 0
else
  status=$?
  if (( status != 1 )); then
    printf 'The preview call failed with status %d.\n' "$status" >&2
    exit "$status"
  fi
fi
printf '%s\n' "$preview" | jq . >&2

token=$(jq -er '.confirm_token' <<<"$preview") || {
  printf 'The command did not return a confirmation token; nothing was replayed.\n' >&2
  exit 3
}

printf 'Type APPLY to replay this exact request: ' >&2
IFS= read -r answer
[[ $answer == APPLY ]] || {
  printf 'Canceled.\n' >&2
  exit 130
}

dvr call "$tool" "$action" "$@" "confirm_token=$token" --output json
