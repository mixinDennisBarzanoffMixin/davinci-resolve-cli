# Resolve Workflow Gap Audit

Last reviewed: 2026-08-28

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

The first CLI layer is now implemented:

```bash
dvr advanced captions parse inputPath=captions.srt
dvr advanced captions write inputPath=captions.srt outputFormat=vtt outputPath=captions.vtt
dvr advanced captions compose_native inputPath=captions.srt outputPath=captions.drt frameRate=24
dvr edit_engine caption_qc --input @caption-request.json
dvr edit_engine plan_caption_repairs --input @caption-blocks.json
dvr edit_engine plan_caption_delivery --input @caption-request.json fps=24 format=srt preset=word-highlight
dvr edit_engine plan_animated_captions --input @caption-request.json fps=24 track_index=3 preset=pop
dvr edit_engine create_animated_captions --input @caption-request.json track_index=3 preset=pop fusion_template=Text+
dvr advanced fusion generate_animated_caption_template outputPath=CaptionPop.setting entrance=pop position=lower-center
```

`create_animated_captions` generates one editable nested Fusion title per cue on
a chosen video track. `clean` and `pop` are implemented; `pop` writes Text+
scale/opacity keyframes, and any installed Fusion title template can be selected.
`word-highlight` materializes exact active-word title segments and `karaoke`
materializes cumulative title segments at every word start. These editable
public-API fallbacks report that they are degraded from native per-character
color/progress styling instead of claiming visual equivalence.

The CLI also generates reusable `.setting` title macros with configurable fill,
stroke, shadow, title-safe position, and duration-adaptive fade/pop/punch
entrances driven by Fusion Anim Curves. Structural validation and install-folder
guidance are included; Resolve import, Inspector mapping, and rendered pixels
remain pending live acceptance. Fusion documents Anim Curves as duration-adaptive
template animation in the [Fusion manual](https://documents.blackmagicdesign.com/UserManuals/FusionManual.pdf?_v=1724310010000).

`caption_qc` audits overlap, gaps, flashes, long displays, line limits, orphan
words, and reading speed. `plan_caption_repairs` only rewraps and uses available
gaps—never deletes or paraphrases words; it may normalize surrounding whitespace.
`plan_caption_delivery` derives an accessibility-capable SRT/VTT sidecar artifact
and animated plan from one timing source. The returned text is not embedded or
imported yet, and the overlays are not an accessible native subtitle track.
Native subtitle-track output is the separate
`compose_native` DRT mode. Applying Resolve's stock Animated template to a
subtitle-track header remains UI-only unless its version-specific project data
is safely decoded and verified.

Every applied caption workflow should finish with frame/render verification.
Resolve has accepted subtitle render settings without producing burn-in pixels,
an embedded stream, or a sidecar in live testing; a successful setter is not a
delivery proof.

## Priority 0: first-class transitions

The standard scripting API has no create, add, clone, type, alignment, or
duration setter for transitions. Existing transitions can be found as timeline
items, inspected at a basic level, and deleted. The offline advanced layer can
currently author only a centered Cross Dissolve.

Live inspection/removal and the fixture-grounded offline workflow are now
implemented:

```bash
dvr timeline list_transitions track_type=video track_index=1
dvr timeline transition_report track_type=video
dvr timeline delete_transition transition_id=...   # confirmation-token gated
dvr advanced drp place_transition drpPath=in.drp outputPath=out.drp track=1 atFrame=240 durationPreset=standard frameRate=24
dvr advanced drp list_transitions drpPath=out.drp
dvr advanced drp validate_transitions drpPath=out.drp
dvr advanced drp clone_transition drpPath=out.drp outputPath=clone.drp sourceTransitionDbId=... atFrame=480
dvr advanced drp set_transition_duration drpPath=clone.drp outputPath=retimed.drp transitionDbId=... durationSeconds=1 frameRate=24
dvr advanced drp delete_transition drpPath=retimed.drp outputPath=clean.drp transitionDbId=...
```

`transition_report` conservatively identifies transition timeline items and
reports their track/range, neighboring cut, handle availability, and warnings.
`delete_transition` only accepts an ID that passes the same discriminator and
never ripples. Offline inventory fingerprints the opaque effect payload;
arbitrary existing centered transitions can be cloned exactly,
duration-adjusted, structurally validated, and non-ripple deleted without
inventing their encoding. New synthesis remains limited to the centered Cross
Dissolve captured from Resolve 21. Start/end alignment, audio transitions,
source-handle proof, Resolve import acceptance, and render proof remain open.
Arbitrary stock transition synthesis must not be claimed until each encoded
type has a live Resolve fixture.

Fusion transition templates and DCTL transition assets are worth supporting as
versioned user-supplied inputs. Generating the asset is already possible; the
missing part is safely applying it at a cut.

## Priority 0: make the shell surface self-describing and fast

The next shell layer is implemented:

```bash
dvr describe timeline get_items
source <(dvr completion bash)
dvr session < requests.jsonl > results.jsonl
```

Action descriptions prefer registered help, fall back to documented signatures,
and label unknown/open schemas honestly. Bash/zsh/fish completion discovers
compound tools/actions/documented parameters, granular schema flags, advanced
tool/action names, common enums, and output options dynamically. Advanced Zod
parameter flags are not yet extracted. `dvr session` processes correlated
compound/granular JSONL requests in one warm Python process and continues after
per-line errors; advanced Node actions remain ordinary one-shot CLI calls.
Resolve calls remain ordered because the application is not a safe target for
blind parallel mutation. Remaining work is a generated completion cache, strict
compound action schemas, and advanced Zod parameter completion.

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

1. Live-import acceptance tests and render verification for native captions,
   generated title templates, and exact word-timed overlay fallbacks.
2. Capture import/render/readback fixtures for more transition types and
   alignments; add source-handle verification.
3. Strict compound action schemas, cached completion, and advanced Zod flags.
4. A guarded full FusionScript dispatcher plus explicit Lua/Python escape hatch.
5. One coherent “shadow edit” workflow: export, patch as a new timeline version,
   diff, import, verify, and rollback.
6. Workflow Integration event companion and native SDK developer tooling.

The governing rule is simple: identify whether an operation is live API,
FusionScript, offline project authoring, database patching, or UI-only in every
result. Never turn a missing Blackmagic API into a silent success claim.
