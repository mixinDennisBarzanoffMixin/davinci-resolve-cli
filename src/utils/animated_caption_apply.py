"""Apply a timed caption-title plan through Resolve's public API.

Resolve's documented API cannot apply the Resolve 20 ``Animated`` subtitle
effect to a native subtitle-track header.  It *can* insert a Fusion title into
an otherwise empty timeline, expose that timeline as a MediaPoolItem, and place
the nested timeline at an exact video track/frame.  This module turns that
verified workaround into one reusable operation.

The result is an editable title-overlay track, not a native subtitle track.  A
caller that needs accessible/sidecar captions must create those separately.
"""

from __future__ import annotations

import uuid
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional


class AnimatedCaptionApplyError(ValueError):
    """A plan is unsafe, malformed, or could not be applied/read back."""


def _integer(value: Any, field: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool):
        raise AnimatedCaptionApplyError(f"{field} must be an integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise AnimatedCaptionApplyError(f"{field} must be an integer")
        result = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        result = int(value.strip())
    else:
        raise AnimatedCaptionApplyError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise AnimatedCaptionApplyError(f"{field} must be >= {minimum}")
    return result


def _text_tool(item: Any) -> tuple[Any, Any]:
    count = _integer(item.GetFusionCompCount() or 0, "Fusion comp count", minimum=0)
    if count < 1:
        raise AnimatedCaptionApplyError("inserted Fusion title has no Fusion composition")
    comp = item.GetFusionCompByIndex(1)
    if not comp:
        raise AnimatedCaptionApplyError("inserted Fusion title composition is unavailable")

    tools = None
    try:
        tools = comp.GetToolList(False, "TextPlus")
    except Exception:
        tools = None
    for tool in (tools or {}).values():
        return comp, tool

    # Blackmagic's built-in Text+ and many title templates name their published
    # text tool Template.  Keep this as a fallback, not the only lookup.
    try:
        tool = comp.FindTool("Template")
    except Exception:
        tool = None
    if not tool:
        raise AnimatedCaptionApplyError(
            "Fusion title template contains no discoverable TextPlus tool; "
            "choose a template with editable text"
        )
    return comp, tool


def _word_segments(
    *,
    row_index: int,
    start_frame: int,
    duration_frames: int,
    animation: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Expand a word-aware block into non-overlapping static title segments.

    This intentionally uses only the Resolve operations already verified by the
    ordinary caption executor: static Text+ writes and exact nested-timeline
    placement.  It does not pretend that the scripting API can style a character
    range or attach Resolve's native Animated subtitle effect.
    """
    preset = str(animation.get("preset") or "")
    if preset not in {"word-highlight", "karaoke"}:
        return []
    raw_cues = animation.get("word_cues")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise AnimatedCaptionApplyError(
            f"placements[{row_index}] preset {preset!r} requires non-empty word_cues"
        )

    cues: List[Dict[str, Any]] = []
    previous_start: Optional[int] = None
    previous_end: Optional[int] = None
    for cue_index, cue in enumerate(raw_cues):
        if not isinstance(cue, Mapping):
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}].animation.word_cues[{cue_index}] must be an object"
            )
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}].animation.word_cues[{cue_index}].text must be non-empty"
            )
        cue_start = _integer(
            cue.get("start_frame"),
            f"placements[{row_index}].animation.word_cues[{cue_index}].start_frame",
            minimum=0,
        )
        cue_end = _integer(
            cue.get("end_frame_exclusive"),
            f"placements[{row_index}].animation.word_cues[{cue_index}].end_frame_exclusive",
            minimum=1,
        )
        if cue_end <= cue_start:
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}].animation.word_cues[{cue_index}] must have positive duration"
            )
        if cue_end > duration_frames:
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}].animation.word_cues[{cue_index}] exceeds its caption duration"
            )
        if previous_start is not None and cue_start <= previous_start:
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}] word cues must have strictly increasing start frames"
            )
        if preset == "word-highlight" and previous_end is not None and cue_start < previous_end:
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}] word cues overlap; one active-word title cannot represent "
                "simultaneous words on a single video track"
            )
        cues.append({
            "cue_index": cue_index,
            "text": text.strip(),
            "start_frame": cue_start,
            "end_frame_exclusive": cue_end,
        })
        previous_start = cue_start
        previous_end = cue_end

    segments: List[Dict[str, Any]] = []
    cumulative_words: List[str] = []
    for cue_index, cue in enumerate(cues):
        cumulative_words.append(cue["text"])
        if preset == "word-highlight":
            segment_end = cue["end_frame_exclusive"]
            segment_text = cue["text"]
            strategy = "one-title-per-spoken-word"
            limitation = (
                "Only the active word is shown. Inactive context and character-range color "
                "styling are omitted because they cannot be verified through Resolve's public API."
            )
        else:
            segment_end = (
                cues[cue_index + 1]["start_frame"]
                if cue_index + 1 < len(cues)
                else duration_frames
            )
            segment_text = " ".join(cumulative_words)
            strategy = "cumulative-title-segments"
            limitation = (
                "Words appear cumulatively at their start frames. This is not a continuous "
                "per-glyph progress mask or active-word color treatment."
            )
        if segment_end <= cue["start_frame"]:
            raise AnimatedCaptionApplyError(
                f"placements[{row_index}] word cue {cue_index} produces a zero-length title segment"
            )
        segments.append({
            "text": segment_text,
            "start_frame": start_frame + cue["start_frame"],
            "end_frame": start_frame + segment_end,
            "word_execution": {
                "preset": preset,
                "strategy": strategy,
                "degraded_from_requested_style": True,
                "source_placement_index": row_index,
                "word_index": cue_index,
                "source_word": cue["text"],
                "source_word_start_frame": cue["start_frame"],
                "source_word_end_frame_exclusive": cue["end_frame_exclusive"],
                "limitation": limitation,
            },
        })
    return segments


def _normalized_placements(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw = plan.get("placements")
    if not isinstance(raw, list) or not raw:
        raise AnimatedCaptionApplyError("plan.placements must be a non-empty array")

    normalized: List[Dict[str, Any]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise AnimatedCaptionApplyError(f"placements[{index}] must be an object")
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AnimatedCaptionApplyError(f"placements[{index}].text must be non-empty")
        timeline = row.get("timeline") if isinstance(row.get("timeline"), Mapping) else {}
        title = row.get("title") if isinstance(row.get("title"), Mapping) else {}
        title_inputs = title.get("inputs") if isinstance(title.get("inputs"), Mapping) else {}
        start_value = row.get("start_frame", timeline.get("record_frame"))
        start = _integer(start_value, f"placements[{index}].start_frame", minimum=0)
        end_value = row.get("end_frame", timeline.get("end_frame_exclusive"))
        if end_value is not None:
            end = _integer(end_value, f"placements[{index}].end_frame", minimum=1)
            duration = end - start
        else:
            duration = _integer(
                row.get("duration_frames", timeline.get("duration_frames")),
                f"placements[{index}].duration_frames",
                minimum=1,
            )
            end = start + duration
        if duration < 1:
            raise AnimatedCaptionApplyError(f"placements[{index}] must have positive duration")
        animation = row.get("animation") if isinstance(row.get("animation"), Mapping) else {}
        word_segments = _word_segments(
            row_index=index,
            start_frame=start,
            duration_frames=duration,
            animation=animation,
        )
        raw_segments = word_segments or [{
            "text": text,
            "start_frame": start,
            "end_frame": end,
            "word_execution": None,
        }]
        for segment_index, segment in enumerate(raw_segments):
            segment_text = segment["text"]
            normalized.append(
                {
                    "index": index,
                    "segment_index": segment_index,
                    "text": segment_text,
                    "start_frame": segment["start_frame"],
                    "end_frame": segment["end_frame"],
                    "duration_frames": segment["end_frame"] - segment["start_frame"],
                    "template_name": row.get("template_name", title.get("template")),
                    "text_input": row.get("text_input") or "StyledText",
                    "text_value": (
                        segment_text if word_segments else title_inputs.get("StyledText", text)
                    ),
                    "animation": animation,
                    "word_execution": segment.get("word_execution"),
                }
            )

    normalized.sort(key=lambda item: (item["start_frame"], item["end_frame"], item["index"]))
    for previous, following in zip(normalized, normalized[1:]):
        if following["start_frame"] < previous["end_frame"]:
            raise AnimatedCaptionApplyError(
                "caption title placements overlap on one video track: "
                f"{previous['start_frame']}-{previous['end_frame']} and "
                f"{following['start_frame']}-{following['end_frame']}"
            )
    return normalized


def _input_ids(tool: Any) -> set[str]:
    result: set[str] = set()
    try:
        for inp in (tool.GetInputList() or {}).values():
            attrs = inp.GetAttrs() or {}
            for key in ("INPS_ID", "INPS_Name"):
                if attrs.get(key):
                    result.add(str(attrs[key]))
    except Exception:
        pass
    return result


def _add_keyframes(comp: Any, tool: Any, input_name: str, points: List[Dict[str, Any]]) -> bool:
    """Attach, write, and read back a spline; return False on any silent no-op."""
    ids = _input_ids(tool)
    if ids and input_name not in ids:
        return False
    try:
        inp = tool[input_name]
    except Exception:
        return False
    if not inp:
        return False
    try:
        try:
            connected = inp.GetConnectedOutput() is not None
        except Exception:
            connected = False
        if not connected:
            locked = False
            try:
                comp.Lock()
                locked = True
            except Exception:
                locked = False
            try:
                if tool.AddModifier(input_name, "BezierSpline") is False:
                    return False
            finally:
                if locked:
                    try:
                        comp.Unlock()
                    except Exception:
                        pass
            try:
                inp = tool[input_name]
            except Exception:
                return False
            try:
                if inp.GetConnectedOutput() is None:
                    return False
            except Exception:
                return False
        expected: Dict[int, Any] = {}
        for point in points:
            frame = _integer(point.get("frame"), "animation keyframe", minimum=0)
            value = point.get("value")
            inp[frame] = value
            expected[frame] = value
        try:
            raw_keyframes = inp.GetKeyFrames() or {}
            actual_frames = {int(float(frame)) for frame in raw_keyframes.values()}
        except Exception:
            return False
        if not set(expected).issubset(actual_frames):
            return False
        for frame, expected_value in expected.items():
            try:
                actual_value = tool.GetInput(input_name, frame)
            except Exception:
                return False
            if isinstance(expected_value, (int, float)) and isinstance(actual_value, (int, float)):
                if not math.isclose(float(actual_value), float(expected_value), rel_tol=1e-6, abs_tol=1e-7):
                    return False
            elif actual_value != expected_value:
                return False
        return True
    except Exception:
        return False


def _apply_semantic_animation(comp: Any, tool: Any, animation: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate the planner's verified pop channels to ordinary Text+ inputs."""
    preset = str(animation.get("preset") or "clean")
    if preset in {"word-highlight", "karaoke"}:
        return {
            "preset": preset,
            "applied_channels": [],
            "warnings": [
                "Word timing is materialized as exact static title segments; no native "
                "Animated subtitle effect or unverified character-range styling was applied."
            ],
        }
    applied: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for channel in animation.get("channels") or []:
        if not isinstance(channel, Mapping) or not isinstance(channel.get("keyframes"), list):
            continue
        semantic = channel.get("channel")
        points = [dict(point) for point in channel["keyframes"] if isinstance(point, Mapping)]
        if semantic == "title.scale":
            try:
                base = float(tool.GetInput("Size") or 0.05)
            except Exception:
                base = 0.05
            scaled = [dict(point, value=base * float(point.get("value"))) for point in points]
            if _add_keyframes(comp, tool, "Size", scaled):
                applied.append({"channel": semantic, "input": "Size", "keyframe_count": len(scaled)})
            else:
                warnings.append("template has no keyframeable Text+ Size input")
        elif semantic == "title.opacity":
            mapped = next(
                (candidate for candidate in ("Opacity1", "Blend") if _add_keyframes(comp, tool, candidate, points)),
                None,
            )
            if mapped:
                applied.append({"channel": semantic, "input": mapped, "keyframe_count": len(points)})
            else:
                warnings.append("template has no keyframeable opacity input; scale animation still applies")
        else:
            warnings.append(f"unmapped semantic animation channel: {semantic}")
    if preset != "clean" and animation.get("channels") and not applied:
        raise AnimatedCaptionApplyError(
            f"template could not apply any {preset!r} animation channels"
        )
    return {"preset": preset, "applied_channels": applied, "warnings": warnings}


def _ensure_video_track(timeline: Any, track_index: int) -> None:
    current = _integer(timeline.GetTrackCount("video") or 0, "video track count", minimum=0)
    while current < track_index:
        if not timeline.AddTrack("video"):
            raise AnimatedCaptionApplyError(f"could not create destination video track V{current + 1}")
        current += 1


def _preflight_track_occupancy(
    timeline: Any,
    track_index: int,
    placements: Iterable[Mapping[str, Any]],
    *,
    frame_offset: int,
    existing_track_count: int,
) -> None:
    """Refuse any requested range that intersects an existing target-track item."""
    if track_index > existing_track_count:
        return
    try:
        existing_raw = timeline.GetItemListInTrack("video", track_index) or []
    except Exception as exc:
        raise AnimatedCaptionApplyError(
            f"could not inspect destination video track V{track_index} before mutation"
        ) from exc
    existing = existing_raw.values() if isinstance(existing_raw, Mapping) else existing_raw
    occupied: List[tuple[int, int, str]] = []
    for index, item in enumerate(existing):
        try:
            start = _integer(item.GetStart(), f"existing V{track_index} item start")
            try:
                end = _integer(item.GetEnd(), f"existing V{track_index} item end")
            except Exception:
                duration = _integer(
                    item.GetDuration(), f"existing V{track_index} item duration", minimum=1
                )
                end = start + duration
            name = str(item.GetName()) if callable(getattr(item, "GetName", None)) else f"item {index}"
        except Exception as exc:
            raise AnimatedCaptionApplyError(
                f"could not read an existing item on destination V{track_index}; "
                "refusing unverified caption placement"
            ) from exc
        occupied.append((start, end, name))
    for placement in placements:
        start = _integer(placement["start_frame"], "caption start frame") + frame_offset
        end = _integer(placement["end_frame"], "caption end frame") + frame_offset
        for existing_start, existing_end, name in occupied:
            if start < existing_end and existing_start < end:
                raise AnimatedCaptionApplyError(
                    f"caption range {start}-{end} intersects existing V{track_index} "
                    f"item {name!r} at {existing_start}-{existing_end}; choose an empty "
                    "track or move the existing item first"
                )


def _rollback(
    project: Any,
    media_pool: Any,
    destination: Any,
    placed: Iterable[Any],
    sources: Iterable[Any],
    original_video_track_count: int,
) -> List[str]:
    placed = list(placed)
    sources = list(sources)
    failures: List[str] = []
    try:
        if not project.SetCurrentTimeline(destination):
            failures.append("could not reactivate destination timeline")
    except Exception as exc:
        failures.append(f"reactivating destination raised {type(exc).__name__}: {exc}")
    if placed:
        try:
            if not destination.DeleteClips(placed, False):
                failures.append("DeleteClips returned false")
        except Exception as exc:
            failures.append(f"DeleteClips raised {type(exc).__name__}: {exc}")
    if sources:
        try:
            if not media_pool.DeleteTimelines(sources):
                failures.append("DeleteTimelines returned false")
        except Exception as exc:
            failures.append(f"DeleteTimelines raised {type(exc).__name__}: {exc}")
    try:
        current_tracks = _integer(
            destination.GetTrackCount("video") or 0,
            "video track count during rollback",
            minimum=0,
        )
        for index in range(current_tracks, original_video_track_count, -1):
            if not destination.DeleteTrack("video", index):
                failures.append(f"DeleteTrack(video, {index}) returned false")
        remaining = _integer(
            destination.GetTrackCount("video") or 0,
            "video track count after rollback",
            minimum=0,
        )
        if remaining != original_video_track_count:
            failures.append(
                f"video track count is {remaining}; expected {original_video_track_count}"
            )
    except Exception as exc:
        failures.append(f"track rollback raised {type(exc).__name__}: {exc}")
    return failures


def apply_animated_caption_plan(
    project: Any,
    destination_timeline: Any,
    plan: Mapping[str, Any],
    *,
    template_name: str = "Text+",
    track_index: Optional[int] = None,
    frame_mode: str = "auto",
    name_prefix: str = "DVR Caption",
    rollback_on_error: bool = True,
) -> Dict[str, Any]:
    """Create editable nested Fusion titles for every planned placement.

    ``frame_mode='relative'`` treats plan frames as offsets from the destination
    timeline's start frame. ``absolute`` passes them through.  One source
    timeline is intentionally created per rendered caption segment: Resolve
    instances of the same nested timeline share title text, so reusing one would
    make every segment display the same words. Word-aware presets expand into
    exact static segments rather than relying on unverified StyledText keyframes.
    """

    if frame_mode not in {"auto", "relative", "absolute"}:
        raise AnimatedCaptionApplyError("frame_mode must be 'auto', 'relative', or 'absolute'")
    if not isinstance(template_name, str) or not template_name.strip():
        raise AnimatedCaptionApplyError("template_name must be non-empty")
    if not project or not destination_timeline:
        raise AnimatedCaptionApplyError("project and destination_timeline are required")

    placements = _normalized_placements(plan)
    target_track = _integer(
        track_index if track_index is not None else plan.get(
            "track_index",
            (plan.get("target") or {}).get("track_index", 2) if isinstance(plan.get("target"), Mapping) else 2,
        ),
        "track_index",
        minimum=1,
    )
    media_pool = project.GetMediaPool()
    if not media_pool:
        raise AnimatedCaptionApplyError("current project has no media pool")
    try:
        original_current_timeline = project.GetCurrentTimeline()
    except Exception:
        original_current_timeline = destination_timeline
    original_video_track_count = _integer(
        destination_timeline.GetTrackCount("video") or 0,
        "video track count",
        minimum=0,
    )
    timeline_start = _integer(destination_timeline.GetStartFrame() or 0, "timeline start frame")
    effective_frame_mode = frame_mode
    if effective_frame_mode == "auto":
        effective_frame_mode = "absolute" if any(
            isinstance(row, Mapping) and isinstance(row.get("timeline"), Mapping)
            for row in (plan.get("placements") or [])
        ) else "relative"
    _preflight_track_occupancy(
        destination_timeline,
        target_track,
        placements,
        frame_offset=timeline_start if effective_frame_mode == "relative" else 0,
        existing_track_count=original_video_track_count,
    )

    created_sources: List[Any] = []
    placed_items: List[Any] = []
    results: List[Dict[str, Any]] = []
    run_tag = uuid.uuid4().hex[:8]

    try:
        _ensure_video_track(destination_timeline, target_track)
        for ordinal, placement in enumerate(placements, 1):
            source_name = f"{name_prefix} {run_tag} {ordinal:04d}"
            source = media_pool.CreateEmptyTimeline(source_name)
            if not source:
                raise AnimatedCaptionApplyError(f"could not create source timeline {source_name!r}")
            created_sources.append(source)
            if not project.SetCurrentTimeline(source):
                raise AnimatedCaptionApplyError(f"could not activate source timeline {source_name!r}")

            chosen_template = placement.get("template_name") or template_name
            title_item = source.InsertFusionTitleIntoTimeline(chosen_template)
            if not title_item:
                raise AnimatedCaptionApplyError(
                    f"Fusion title template {chosen_template!r} could not be inserted"
                )
            try:
                source_title_duration = _integer(
                    title_item.GetDuration(), "source Fusion title duration", minimum=1
                )
            except Exception as exc:
                raise AnimatedCaptionApplyError(
                    "could not verify the source Fusion title duration"
                ) from exc
            if source_title_duration < placement["duration_frames"]:
                raise AnimatedCaptionApplyError(
                    f"Fusion title template {chosen_template!r} provides only "
                    f"{source_title_duration} source frames, shorter than the requested "
                    f"caption duration {placement['duration_frames']}; split the cue or use "
                    "a longer/duration-adaptive template"
                )
            _comp, tool = _text_tool(title_item)
            # Do not comp.Lock() around SetInput: tested Resolve builds can accept
            # the value yet omit it from the render when written under the lock.
            written = tool.SetInput(placement["text_input"], placement["text_value"])
            if written is False:
                raise AnimatedCaptionApplyError(
                    f"template {chosen_template!r} rejected text input {placement['text_input']!r}"
                )
            readback = tool.GetInput(placement["text_input"])
            if str(readback) != placement["text_value"]:
                raise AnimatedCaptionApplyError(
                    f"title text readback mismatch for caption {placement['index']}"
                )
            animation_result = _apply_semantic_animation(
                _comp, tool, placement.get("animation") or {}
            )

            nested_item = source.GetMediaPoolItem()
            if not nested_item:
                raise AnimatedCaptionApplyError("caption source timeline has no MediaPoolItem")
            record_frame = placement["start_frame"]
            if effective_frame_mode == "relative":
                record_frame += timeline_start
            # AppendToTimeline always targets the project's CURRENT timeline.
            # Creating/authoring the source timeline above made that source
            # current, so switch back before placing its MediaPoolItem. Without
            # this readied destination Resolve may attempt to nest the timeline
            # into itself (the old mock did not model this global state).
            if not project.SetCurrentTimeline(destination_timeline):
                raise AnimatedCaptionApplyError(
                    "could not reactivate destination timeline before caption placement"
                )
            appended = media_pool.AppendToTimeline(
                [
                    {
                        "mediaPoolItem": nested_item,
                        "startFrame": 0,
                        "endFrame": placement["duration_frames"],
                        "trackIndex": target_track,
                        "recordFrame": record_frame,
                    }
                ]
            )
            if not appended:
                raise AnimatedCaptionApplyError(
                    f"Resolve rejected caption {placement['index']} at frame {record_frame}; "
                    "check source duration and destination overlap"
                )
            placed = appended[0]
            placed_items.append(placed)
            actual_track = placed.GetTrackTypeAndIndex()[1]
            actual_start = _integer(placed.GetStart(), "placed title start")
            actual_duration = _integer(placed.GetDuration(), "placed title duration", minimum=1)
            if (
                actual_track != target_track
                or actual_start != record_frame
                or actual_duration != placement["duration_frames"]
            ):
                raise AnimatedCaptionApplyError(
                    "caption placement readback mismatch: "
                    f"wanted V{target_track}@{record_frame}+{placement['duration_frames']}, "
                    f"got V{actual_track}@{actual_start}+{actual_duration}"
                )
            results.append(
                {
                    "index": placement["index"],
                    "segment_index": placement["segment_index"],
                    "text": placement["text"],
                    "template_name": chosen_template,
                    "source_timeline": source_name,
                    "track_index": actual_track,
                    "start_frame": actual_start,
                    "duration_frames": actual_duration,
                    "animation": animation_result,
                    "word_execution": placement.get("word_execution"),
                }
            )
    except Exception as exc:
        if rollback_on_error:
            rollback_failures = _rollback(
                project,
                media_pool,
                destination_timeline,
                placed_items,
                created_sources,
                original_video_track_count,
            )
            if rollback_failures:
                raise AnimatedCaptionApplyError(
                    f"{exc}; rollback incomplete: {'; '.join(rollback_failures)}"
                ) from exc
        raise
    finally:
        try:
            project.SetCurrentTimeline(original_current_timeline or destination_timeline)
        except Exception:
            pass

    return {
        "success": True,
        "kind": "fusion_title_overlays",
        "native_subtitles": False,
        "accessible_caption_track": False,
        "template_name": template_name,
        "track_index": target_track,
        "frame_mode": effective_frame_mode,
        "caption_count": len(results),
        "input_placement_count": len(plan.get("placements") or []),
        "word_aware_execution": any(row.get("word_execution") for row in results),
        "captions": results,
        "verification": "source title duration, destination track/start/duration, TextPlus text, and requested animation keyframes read back",
        "render_verification_required": True,
    }


__all__ = [
    "AnimatedCaptionApplyError",
    "apply_animated_caption_plan",
]
