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
from typing import Any, Dict, Iterable, List, Mapping, Optional


class AnimatedCaptionApplyError(ValueError):
    """A plan is unsafe, malformed, or could not be applied/read back."""


def _integer(value: Any, field: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool):
        raise AnimatedCaptionApplyError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AnimatedCaptionApplyError(f"{field} must be an integer") from exc
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
        normalized.append(
            {
                "index": index,
                "text": text,
                "start_frame": start,
                "end_frame": end,
                "duration_frames": duration,
                "template_name": row.get("template_name", title.get("template")),
                "text_input": row.get("text_input") or "StyledText",
                "text_value": title_inputs.get("StyledText", text),
                "animation": row.get("animation") if isinstance(row.get("animation"), Mapping) else {},
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
    """Attach a spline and keyframe a Text+ input; return False if unavailable."""
    ids = _input_ids(tool)
    if ids and input_name not in ids:
        return False
    try:
        inp = tool[input_name]
    except Exception:
        return False
    if not inp:
        return False
    locked = False
    try:
        try:
            connected = inp.GetConnectedOutput() is not None
        except Exception:
            connected = False
        if not connected:
            try:
                comp.Lock()
                locked = True
            except Exception:
                locked = False
            tool.AddModifier(input_name, "BezierSpline")
            try:
                inp = tool[input_name]
            except Exception:
                return False
        for point in points:
            inp[_integer(point.get("frame"), "animation keyframe", minimum=0)] = point.get("value")
        return True
    except Exception:
        return False
    finally:
        if locked:
            try:
                comp.Unlock()
            except Exception:
                pass


def _apply_semantic_animation(comp: Any, tool: Any, animation: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate the planner's verified pop channels to ordinary Text+ inputs."""
    preset = str(animation.get("preset") or "clean")
    if preset in {"word-highlight", "karaoke"}:
        raise AnimatedCaptionApplyError(
            f"preset {preset!r} needs a word-aware Fusion template executor; "
            "the public Resolve API cannot apply the native Animated subtitle-track effect"
        )
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


def _rollback(media_pool: Any, destination: Any, placed: Iterable[Any], sources: Iterable[Any]) -> None:
    placed = list(placed)
    sources = list(sources)
    if placed:
        try:
            destination.DeleteClips(placed, False)
        except Exception:
            pass
    if sources:
        try:
            media_pool.DeleteTimelines(sources)
        except Exception:
            pass


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
    """Create one editable nested Fusion title for every planned placement.

    ``frame_mode='relative'`` treats plan frames as offsets from the destination
    timeline's start frame. ``absolute`` passes them through.  One source
    timeline is intentionally created per caption: Resolve instances of the same
    nested timeline share title text, so reusing one would make every caption
    display the same words.
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
    _ensure_video_track(destination_timeline, target_track)
    timeline_start = _integer(destination_timeline.GetStartFrame() or 0, "timeline start frame")
    effective_frame_mode = frame_mode
    if effective_frame_mode == "auto":
        effective_frame_mode = "absolute" if any(
            isinstance(row, Mapping) and isinstance(row.get("timeline"), Mapping)
            for row in (plan.get("placements") or [])
        ) else "relative"

    created_sources: List[Any] = []
    placed_items: List[Any] = []
    results: List[Dict[str, Any]] = []
    run_tag = uuid.uuid4().hex[:8]

    try:
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
                    "text": placement["text"],
                    "template_name": chosen_template,
                    "source_timeline": source_name,
                    "track_index": actual_track,
                    "start_frame": actual_start,
                    "duration_frames": actual_duration,
                    "animation": animation_result,
                }
            )
    except Exception:
        if rollback_on_error:
            _rollback(media_pool, destination_timeline, placed_items, created_sources)
        raise
    finally:
        try:
            project.SetCurrentTimeline(destination_timeline)
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
        "captions": results,
        "verification": "timeline item track/start/duration and TextPlus text read back",
        "render_verification_required": True,
    }


__all__ = [
    "AnimatedCaptionApplyError",
    "apply_animated_caption_plan",
]
