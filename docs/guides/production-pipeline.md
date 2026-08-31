# Product-video production pipeline

`dvr production` is a Bash-composable assistant-editor pipeline for cars,
products, properties, and other listing-led videos. It keeps one durable JSON
contract between Resolve, transcription, research, B-roll, and Remotion.

## What it solves

The ordinary Resolve analysis route transcribes source files. It does not
reconstruct one selected timeline track with its source trims, gaps, slips, and
record positions. The production extractor does that into a scratch WAV without
changing the source. It is an ASR-oriented source reconstruction, not a
post-Fairlight stem: clip gain/fades, channel routing, automation, retimes,
voice isolation, and track effects are reported as unsupported.

Three timing layers remain separate:

1. Immutable ASR words with start/end seconds.
2. Optional readable caption cues, reflowed and machine-QC'd independently.
3. Longer editorial chunks used to keep/drop A-roll and trigger B-roll.

This prevents caption line breaks from becoming edit points.

Track roles are separate too: the primary/source-language track can produce the
published SRT while a slipped translation or director-commentary track produces
the editable A-roll chunks. `plan` prefers `guide-transcript/` for cuts and
`transcript-reviewed/` for captions, with explicit path overrides for either.

## Install and verify

```bash
dvr production setup
dvr production doctor --pretty
```

The setup installs the local Remotion workspace and, on Apple Silicon, MLX
Whisper. Model weights are not downloaded during setup. They are downloaded
only by an explicit transcription call carrying `--allow-model-download`.

## Inspect and reconstruct a track

```bash
dvr production inspect --output timeline.json --pretty

# Review the complete argv without writing audio.
dvr production extract-track --snapshot timeline.json --track 2 \
  --output scratch/a2.wav --plan-only | jq '.sources, .argv'

# Write a mono 16 kHz ASR sidecar. Source files remain untouched.
dvr production extract-track --snapshot timeline.json --track 2 \
  --output scratch/a2.wav
```

Each source occurrence carries both timeline seconds and file-relative source
seconds. A clip that starts at record time zero but has `source_start_seconds =
2.716667` is trimmed 2.716667 seconds into the file; it is not delayed by an
invented timeline offset.

## Word-level transcription

```bash
dvr production transcribe --snapshot timeline.json --track 1 \
  --language bg --quality accurate --allow-model-download \
  --initial-prompt "Kia K8, LPG, двигател, автоматик, конски сили, евро" \
  --words-only --output-dir transcript
```

`balanced` uses MLX large-v3-turbo. `accurate` uses full MLX large-v3. Outputs:

- `transcript.json` with complete word timestamps and recognizer diagnostics;
- `words.jsonl` for Bash pipelines;
- `words.tsv` and `words.txt` for review;
- `words.remotion.json` using Remotion's `Caption[]` fields;
- `transcription-qc.json` for detected language, silence probability,
  repetition/compression risk, and low average log probability.

`--words-only` suppresses SRT, VTT, and caption-block output. Without it, the
same run also creates `captions.srt`, `captions.vtt`, `captions.json`, and
`caption-qc.json` for workflows that still need caption interchange.

Existing transcripts can be streamed without retranscription:

```bash
dvr production words --transcript transcript/transcript.json --format jsonl \
  | jq 'select(.confidence < 0.75)'
dvr production words --transcript transcript/transcript.json \
  --format remotion-json --output transcript/words.remotion.json
```

A language mismatch, suspicious segment, excessive low-confidence rate, or
caption-readability warning returns `status: needs_review` and
`publication_ready: false`. Artifact generation still exits successfully so a
Bash pipeline can continue to an explicit review gate. Whisper confidence is a
triage signal, not proof that the Bulgarian wording is correct, so publication
still requires listening to flagged windows and reviewing the final words.
Resolve's native auto-caption languages do not include Bulgarian, which is why
this path is local ASR first.

Apply only corrections checked against the waveform/audio. Multi-word
replacements preserve the phrase span but are labeled as interpolated timing;
the final animated overlay remains locked until the review is explicitly
recorded:

```bash
dvr production correct --transcript transcript/transcript.json \
  --corrections corrections.json --output-dir transcript-reviewed \
  --audio-verified --reviewer "editor-name"
```

## Initialize and research any item

```bash
dvr production init --name my-item --subject "ITEM NAME" \
  --url https://seller.example/item --output-dir /path/to/run \
  --primary-track 1 --guide-track 2

# Print the argv without launching an agent:
dvr production research --project-dir /path/to/run

# Explicitly launch a read-only, JSON-schema-constrained Codex research job:
dvr production research --project-dir /path/to/run --run

# Or validate/adopt research created by another browser/agent:
dvr production research --project-dir /path/to/run --input research.json
```

The prompt treats webpage content as untrusted, separates exact-item evidence
from model/category context, preserves conflicting claims, and forbids inferring
options from trim-level availability. A B-roll beat is rejected unless it has a
fact ID, an evidence URL, and a resolved `must_not_show` list.

## Discover a music bed without pulling media

```bash
dvr production music search --project-dir /path/to/run \
  --category "car promo,product showcase" \
  --mood "elegant,confident,modern" \
  --genre "cinematic,electronic,ambient" \
  --instrument "synth,percussion,piano" \
  --keyword "luxury,technology" --energy medium \
  --min-duration 90 --max-duration 360 --limit 12

# Shortlist only; this remains blocked on license review.
dvr production music select --project-dir /path/to/run --track-id OPENVERSE_ID
```

`music search` uses the [Openverse API](https://api.openverse.org/v1/) and
writes `music-options.json` with the exact title, creator, source page, media
URL, reported license URL, attribution string, provider/source identifiers,
query provenance, and repeatable ranking seed. Facets may be repeated or
comma-separated. If `--target-duration` is omitted, the timeline duration is a
ranking hint; duration never causes an edit or download.

The default `commercial-safe` filter means only *reported* CC0, Public Domain
Mark, or CC BY metadata is requested. It is a discovery filter, not legal
clearance: [Openverse's terms](https://docs.openverse.org/terms_of_service.html)
say its aggregated license status is not verified. Listen for vocals and fit,
open the upstream `source_page`, and confirm the current creator, license,
attribution, and commercial use before obtaining a file. To record that manual
check, repeat the exact values deliberately:

```bash
dvr production music select --project-dir /path/to/run --track-id OPENVERSE_ID \
  --confirm-source-license --reviewer editor-name \
  --verified-source-page 'https://upstream.example/exact-track-page' \
  --verified-license by --notes 'Creator and CC BY terms checked on source page'
```

This still does not download, copy, transcode, or import audio. File acquisition
and Resolve attachment remain separate explicit operations so the source and
rights review cannot be bypassed by search.

## Plan cuts and B-roll

```bash
dvr production transcribe --snapshot timeline.json --track 2 --language auto \
  --quality accurate --allow-model-download --output-dir guide-transcript
dvr production chunk --transcript guide-transcript/transcript.json \
  --output guide-chunks.json
dvr production plan --project-dir /path/to/run \
  --chunks guide-chunks.json \
  --video-tracks 1 --audio-tracks 1,2 --variant-name "K8 selects v01"
```

Edit the chunks file by setting `keep` to `false` where needed, then rerun
`plan`. Without `--chunks`, the planner creates `chunks.json` from the guide
transcript when present and records its source path to prevent track mix-ups.
The generated `a-roll-variant.json` maps every kept record interval back to the
correct source frames for V1/A1/A2 and places all tracks on one shared output
cursor. It never blades the original timeline.

```bash
# Validate through Resolve, still without creating anything.
dvr production apply-a-roll --request /path/to/run/a-roll-variant.json

# Explicitly create the new timeline variant; the source timeline remains.
dvr production apply-a-roll --request /path/to/run/a-roll-variant.json --apply
```

## Evidence-gated B-roll agents

The B-roll branch keeps three inputs separate: A1 recorded speech establishes
what the presenter said, current research establishes exact-item facts, and A2
is only a visual locator. A frame-reviewed `broll/source-events.json` binds real
V1 ranges to exact Media Pool item IDs and reviewed frame hashes. That lets the
selector prefer the actual door, cabin, exterior, and price-card footage instead
of synthesizing replacements.

```bash
# Build the immutable agent context and strict structured-output schema.
dvr production broll context --project-dir /path/to/run

# Inspect the bounded jobs first, then launch independent seeded Codex workers.
dvr production broll ideate --project-dir /path/to/run --agents 6
dvr production broll ideate --project-dir /path/to/run --agents 6 --run

# Optional second pass: concepts that reviewed source footage cannot show.
dvr production broll ideate --project-dir /path/to/run --agents 3 \
  --generated-only --visual-types generated_image --run

# Validate every candidate, deduplicate agent consensus, and schedule placements.
dvr production broll select --project-dir /path/to/run \
  --agent-run /path/to/run/broll/agent-runs/AGENT_RUN/run.json \
  --seed editorial-v1 --image-seed images-v1
```

Generated-image jobs are JSON/JSONL contracts. An image host writes each output
under `broll/generated/`; the CLI then records the exact prompt and file hash,
requires visual review, and stages only an approved non-exact illustration:

```bash
dvr production broll asset-record --project-dir /path/to/run \
  --job-id IMAGE_JOB_ID --path /path/to/run/broll/generated/example.png \
  --provider image-provider --model model-name
dvr production broll asset-review --project-dir /path/to/run \
  --asset /path/to/run/broll/assets/IMAGE_JOB_ID.json \
  --approve --reviewer editor-name
```

Multiple selections in one narration chunk are scheduled sequentially, not
stacked at the same frame, and their offsets survive A-roll compaction. Once the
recoverable A-roll variant has actually been created, source cutaways have a
separate native Resolve dry-run/apply gate:

```bash
dvr production broll source-plan --project-dir /path/to/run --video-track 2
dvr production broll source-apply --project-dir /path/to/run
dvr production broll source-apply --project-dir /path/to/run --apply \
  --approve-visuals --reviewer editor-name
```

No B-roll command blades or replaces the source timeline. Ideation and
selection are sidecar-only; generated media is always disclosed as illustrative;
Resolve writes require the explicit apply and visual-approval flags.

## Remotion B-roll and animated Bulgarian captions

Publish the reviewed B-roll selection before opening Studio or rendering:

```bash
dvr production broll publish-remotion --project-dir /path/to/run
dvr production remotion studio --project-dir /path/to/run \
  --manifest remotion-broll.json
dvr production remotion render --project-dir /path/to/run \
  --manifest remotion-broll.json
```

Publication writes `remotion-broll.json`; it never overwrites the legacy
`remotion.json`. It preserves that manifest's captions and records hashes for
the selection, placements, and base manifest. Ready motion graphics, diagrams,
and approved local generated/evidence images are included. Reviewed project
footage is excluded because source cutaways are placed natively in Resolve.
Pending generated assets fail publication; use `--allow-partial` only to omit
those pending placements, never to bypass visual, rights, path, or hash checks.

```bash
dvr production remotion studio --project-dir /path/to/run

# Optional exact imagery must be local and rights-approved first:
dvr production attach-asset --project-dir /path/to/run \
  --beat-id exterior-identity --path /path/to/approved-photo.jpg \
  --exact-item --attribution "Seller gallery, permission confirmed" --approve-rights

dvr production remotion render --project-dir /path/to/run
dvr production remotion captions --project-dir /path/to/run

# Review exact Resolve record-frame placements without changing the timeline.
dvr production import-broll --project-dir /path/to/run --video-track 2 \
  --manifest remotion-broll.json

# Explicitly import and place the rendered clips on V2.
dvr production import-broll --project-dir /path/to/run --video-track 2 \
  --manifest remotion-broll.json --apply --approve-visuals
```

The Remotion workspace includes:

- a full production preview with word-highlighted Cyrillic captions;
- one renderable composition per planned B-roll beat;
- listing-image/video support when a reviewed asset is attached;
- a generated motion-graphic fallback clearly labeled illustrative.

Rendered segments are written beneath `broll-renders/`; no source is modified.
The caption command writes a timeline-rate, timeline-resolution ProRes 4444
overlay with alpha at `captions-overlay.mov` for placement above the A-roll.
The renderer never fabricates an engine or feature photograph. When exact
imagery is unavailable it produces a diagram/card and keeps the evidence and
`must_not_show` guardrails in the manifest.

`apply-a-roll --apply` records the created variant ID. B-roll import refuses to
write until that variant is current, every render matches the latest manifest,
and `--approve-visuals` explicitly records the final visual-review decision.

## Current Resolve boundary

The public scripting API cannot programmatically import SRT into a native
subtitle track or manipulate individual native subtitle cues. Keep the SRT as
the accessible sidecar, import it in Resolve's UI when needed, or use the
existing `edit_engine create_animated_captions` Fusion-title fallback. The
Remotion render is a visual overlay, not an accessibility subtitle stream.
