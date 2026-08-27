# Resolve Workflow Gap Audit

Last reviewed: 2026-08-27

## What “100%” means

This fork exposes every method in the bundled Blackmagic Resolve scripting
reference: 361 of 361 documented methods. It also exposes every upstream MCP
tool through the `dvr` CLI.

That is **100% of the documented Resolve scripting API**, not 100% of DaVinci
Resolve. Resolve's UI, its separate FusionScript model, Workflow Integrations,
native extension SDKs, and parts of the project format expose capabilities that
the standard Resolve scripting API does not. The distinction matters most for
captions, transitions, direct timeline editing, Fairlight, and color.

## Priority 0: captions that are actually useful

Resolve 20 introduced Animated subtitle Fusion templates, applied to a subtitle
track header, with transcription-driven word highlighting. Resolve 21 added word
timing analysis for manually entered or imported captions. These are real
Resolve features, but the documented scripting API does not expose the UI
operations that apply an Animated template, update word timings, import an SRT,
or read/write an individual subtitle cue's text and timing.

Official references:

- [DaVinci Resolve 20 New Features Guide, pp. 27–28](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20_New_Features_Guide.pdf)
- [DaVinci Resolve 21 New Features Guide, pp. 77–81](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_21_New_Features_Guide.pdf)

The repository already has most of the components of a much better CLI
workflow:

- `timeline_ai create_subtitles` calls Resolve's transcription/caption engine
  and verifies that a subtitle track appeared.
- `edit_engine generate_captions` converts stored word timings into readable
  SRT or WebVTT blocks.
- `project_db set_subtitle_style` can patch limited whole-track typography and
  position while Resolve is closed.
- The offline DRP builder already contains subtitle-track and cue serializers,
  but no public action or acceptance test uses them.
- Fusion title insertion, Text+ input writes, keyframes, and exact placement by
  the verified nested-timeline route exist as separate primitives.

The missing product is one coherent command family:

```bash
# Proposed interfaces; not implemented yet.
dvr captions write --words words.json --format srt --output captions.srt
dvr captions compose --input captions.srt --format native --output captions.drt
dvr captions animate --words words.json --preset word-pop --track 3 --apply
dvr captions verify --render export.mov --expect burn-in
```

`captions animate` should generate editable Fusion title overlays on a chosen
video track, with presets such as pop, word highlight, karaoke, background box,
and safe-area placement. This is the safest fully Bash-composable path because
it uses public Fusion/title primitives; it produces title overlays, not an
accessible native subtitle track. Native subtitle output should be a separate
mode backed by the offline subtitle serializer. Applying Resolve's stock
Animated template to a subtitle-track header remains UI-only unless its
version-specific project data is safely decoded and verified.

Every applied caption workflow should finish with frame/render verification.
Resolve has accepted subtitle render settings without producing burn-in pixels,
an embedded stream, or a sidecar in live testing; a successful setter is not a
delivery proof.

## Priority 0: first-class transitions

The standard scripting API has no create, add, clone, type, alignment, or
duration setter for transitions. Existing transitions can be found as timeline
items, inspected at a basic level, and deleted. The offline advanced layer can
currently author only a centered Cross Dissolve.

The useful CLI should be:

```bash
# Proposed unified interfaces; not implemented yet.
dvr transitions list --timeline current
dvr transitions inspect --track 1 --at-frame 240
dvr transitions apply --type cross-dissolve --track 1 --at-frame 240 --frames 12
dvr transitions clone --from-frame 240 --to-frame 480
dvr transitions remove --track 1 --at-frame 240 --confirm
dvr transitions qc --timeline current
```

`list`, `inspect`, `remove`, and `qc` can use the live public API. `apply` and
`clone` require offline DRT/DRP authoring or an explicitly optional UI
companion. The offline implementation should start with dissolves and fades,
validate source handles and cut alignment, import as a new timeline version,
then render and read back the result. Arbitrary stock transitions must not be
claimed until each encoded type has a live Resolve fixture.

Fusion transition templates and DCTL transition assets are worth supporting as
versioned user-supplied inputs. Generating the asset is already possible; the
missing part is safely applying it at a cut.

## Priority 0: make the shell surface self-describing and fast

The CLI exposes all handlers, but it is not yet a great shell *environment*:

- `dvr describe timeline` describes the generic `{action, params}` wrapper,
  not each action's parameters, defaults, enums, safety class, and result shape.
- Shell completion currently completes top-level commands and compound tool
  names, not actions, granular tools, parameters, or enum values.
- stdin accepts one JSON request. JSONL is an output format, not a persistent
  multi-request session, so each command starts a fresh Python process.

The next CLI layer should add `dvr describe TOOL ACTION`, schema-backed
completion, and a single-connection request loop such as:

```bash
dvr session --input-jsonl < requests.jsonl
dvr map timeline_item get_name < item-requests.jsonl
```

Resolve calls should remain ordered by default; the application is not a safe
target for blind parallel mutation.

## Other large Resolve-vs-API gaps

| Area | Resolve can do | Standard scripting API cannot do well |
|---|---|---|
| Fusion | Full node, composition, registry, metadata, render-queue, callback, and scripting workflows | This fork exposes a strong curated graph subset, not the complete separate FusionScript object model. |
| Editing | Blade, roll, ripple, slip, slide, asymmetric trim, track targeting, speed ramps, multicam, and transition editing | No direct live setters for many of these operations. The fork uses guarded rebuilds and offline project edits for a subset. |
| Fairlight | Mixer automation, fades, EQ, dynamics, plug-ins, buses, ADR, recording, and Audio Assistant | Public methods expose only a small subset such as voice isolation and preset application. |
| Color | Primaries, curves, qualifiers, windows, tracking, ResolveFX, and arbitrary node graph editing | The Graph API cannot live-create/delete/connect nodes or directly control most grading parameters; offline DRX workarounds cover only calibrated structures. |
| Resolve AI | IntelliScript, Music Editor, Dialogue Matcher, SmartSwitch, Animated Subtitles, and Audio Assistant | Only selected lower-level AI calls are scripted. Repo-native approximations must be labeled as such. |
| Workflow events | Integration plug-ins receive lifecycle callbacks and provide UI panels | The CLI currently polls; an optional local companion could make `dvr events --follow` event-driven. |
| Photo page | Albums, tethered capture, RAW photo workflow, and batch photo export | There is no documented PhotoAlbum or tethering scripting object. |

Useful official overviews:

- [Fusion scripting and automation](https://www.blackmagicdesign.com/products/fusion/)
- [Fusion Scripting Guide](https://documents.blackmagicdesign.com/UserManuals/Fusion8_Scripting_Guide.pdf)
- [DaVinci Resolve Edit](https://www.blackmagicdesign.com/products/davinciresolve/edit)
- [DaVinci Resolve Fairlight](https://www.blackmagicdesign.com/products/davinciresolve/fairlight)
- [DaVinci Resolve Color](https://www.blackmagicdesign.com/products/davinciresolve/color)

## Recommended build order

1. `captions write|compose|animate|verify`, with native and Fusion-overlay modes.
2. `transitions list|inspect|remove|apply|clone|qc`, live where possible and
   offline where required.
3. Per-action schemas, real completion, and a persistent JSONL session.
4. A guarded full FusionScript dispatcher plus explicit Lua/Python escape hatch.
5. One coherent “shadow edit” workflow: export, patch as a new timeline version,
   diff, import, verify, and rollback.
6. Workflow Integration event companion and native SDK developer tooling.

The governing rule is simple: identify whether an operation is live API,
FusionScript, offline project authoring, database patching, or UI-only in every
result. Never turn a missing Blackmagic API into a silent success claim.
