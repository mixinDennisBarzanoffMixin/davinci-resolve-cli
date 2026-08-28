#!/usr/bin/env bash
set -euo pipefail

request=${1:-examples/cli/caption-request.json}
bundle=${2:-caption-delivery-plan.json}

# One timing source drives both the accessibility-capable sidecar and animated title
# overlays. This command is an offline plan and does not mutate Resolve.
dvr edit_engine plan_caption_delivery --input "@$request" >"$bundle"

# QC is machine-readable. Exit non-zero here if the profile has hard errors.
if ! jq -e '.qc.success' "$bundle" >/dev/null; then
  jq '.qc.issues' "$bundle" >&2
  exit 1
fi

# Preserve the sidecar accessibility artifact even when burned-in animation is
# also requested. The extension follows the requested/default output format.
caption_format=$(jq -er '.native_sidecar.format' "$bundle")
caption_path="captions.$caption_format"
jq -r '.native_sidecar.content' "$bundle" >"$caption_path"

# Applying the overlay plan is a separate, version-archived Resolve mutation.
# --yes authorizes the CLI's exact-token replay for this already selected
# action; omit it when an interactive preview/review step is preferred.
jq '{plan: .animated_overlays}' "$bundle" \
  | dvr edit_engine create_animated_captions --input - track_index=3 --yes
