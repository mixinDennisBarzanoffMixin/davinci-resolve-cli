"""Plan native Resolve V1 source cutaways onto a recoverable A-roll variant.

This module only builds and validates a dry-run request.  It never dispatches to
Resolve, copies media, renders, transcodes, or modifies a source file.  Source
seconds are resolved through the exact V1 timeline item into that media pool
item's source-frame space; target seconds are resolved into absolute record
frames on the recoverable variant.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SOURCE_EVENTS_SCHEMA_VERSION = "dvr.frame-reviewed-source-events.v1"
SOURCE_CUTAWAY_SELECTION_VERSION = "dvr.source-cutaway-selection.v1"
AROLL_TARGET_SCHEMA_VERSION = "dvr.a-roll-variant-target.v1"
SOURCE_CUTAWAY_REQUEST_VERSION = "dvr.source-cutaway-append-request.v1"

_APPROVED_FRAME_REVIEW_STATES = {"approved", "frame-reviewed", "manual-approved"}


class SourceCutawayError(ValueError):
    """A source cutaway is stale, ambiguous, unsafe, or out of bounds."""


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_item_sha256(item: Mapping[str, Any]) -> str:
    """Fingerprint the exact Media Pool identity and source/timeline mapping.

    If a snapshot contains a byte-level ``source_media_sha256`` or
    ``file_sha256``, it participates in this fingerprint.  The function still
    works for ordinary Resolve snapshots that can only expose identity and
    timing metadata.
    """
    identity = {
        "timeline_item_id": item.get("timeline_item_id"),
        "media_pool_item_id": item.get("media_pool_item_id"),
        "file_path": item.get("file_path"),
        "source_media_sha256": item.get("source_media_sha256") or item.get("file_sha256"),
        "timeline_start": item.get("start"),
        "timeline_end": item.get("end"),
        "source_start": item.get("source_start"),
        "source_end": item.get("source_end"),
        "source_fps": item.get("source_fps"),
    }
    return canonical_sha256(identity)


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceCutawayError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise SourceCutawayError(f"{field} must be finite")
    return number


def _positive_number(value: Any, field: str) -> float:
    number = _finite_number(value, field)
    if number <= 0:
        raise SourceCutawayError(f"{field} must be greater than zero")
    return number


def _integer(value: Any, field: str) -> int:
    number = _finite_number(value, field)
    rounded = round(number)
    if abs(number - rounded) > 1e-6:
        raise SourceCutawayError(f"{field} must be an integer")
    return int(rounded)


def _video_track(snapshot: Mapping[str, Any], track_index: int) -> Mapping[str, Any]:
    tracks = (((snapshot.get("tracks") or {}).get("video") or {}).get("tracks") or [])
    matches = [row for row in tracks if int(row.get("track_index") or 0) == track_index]
    if len(matches) != 1:
        raise SourceCutawayError(f"source snapshot must contain exactly one V{track_index} track")
    return matches[0]


def _source_item_for_event(
    snapshot: Mapping[str, Any],
    event: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], int, int]:
    timeline_fps = _positive_number(snapshot.get("fps"), "snapshot.fps")
    timeline_start = _integer(snapshot.get("start_frame"), "snapshot.start_frame")
    event_start_seconds = _finite_number(event.get("start_seconds"), "event.start_seconds")
    event_end_seconds = _finite_number(event.get("end_seconds"), "event.end_seconds")
    if event_start_seconds < 0 or event_end_seconds <= event_start_seconds:
        raise SourceCutawayError(f"source event {event.get('id')!r} has an invalid time range")
    event_start_frame = timeline_start + round(event_start_seconds * timeline_fps)
    event_end_frame = timeline_start + round(event_end_seconds * timeline_fps)
    snapshot_end = _integer(snapshot.get("end_frame"), "snapshot.end_frame")
    if event_start_frame < timeline_start or event_end_frame > snapshot_end:
        raise SourceCutawayError(f"source event {event.get('id')!r} is outside the source timeline")

    source_track_index = _integer(event.get("source_track_index", 1), "event.source_track_index")
    if source_track_index != 1:
        raise SourceCutawayError("native source cutaways must come from V1")
    items = list((_video_track(snapshot, source_track_index).get("items") or []))
    requested_media_id = str(event.get("media_pool_item_id") or "")
    if not requested_media_id:
        raise SourceCutawayError(f"source event {event.get('id')!r} requires media_pool_item_id")
    matching_id = [row for row in items if str(row.get("media_pool_item_id") or "") == requested_media_id]
    if not matching_id:
        raise SourceCutawayError(
            f"source event {event.get('id')!r} media_pool_item_id is not present on V1: {requested_media_id}"
        )
    containing = [
        row for row in matching_id
        if _integer(row.get("start"), "source item start") <= event_start_frame
        and _integer(row.get("end"), "source item end") >= event_end_frame
    ]
    if len(containing) != 1:
        raise SourceCutawayError(
            f"source event {event.get('id')!r} must fit inside exactly one V1 timeline item"
        )
    return containing[0], event_start_frame, event_end_frame


def _validate_frame_review(event: Mapping[str, Any]) -> None:
    review = event.get("frame_review") or {}
    status = str(review.get("status") or "").casefold()
    if status not in _APPROVED_FRAME_REVIEW_STATES:
        raise SourceCutawayError(
            f"source event {event.get('id')!r} is not frame-review approved"
        )
    if not str(review.get("reviewer") or "").strip():
        raise SourceCutawayError(f"source event {event.get('id')!r} has no frame reviewer")
    frame_hashes = list(review.get("frame_sha256s") or [])
    if not frame_hashes or any(
        len(str(value)) != 64 or any(char not in "0123456789abcdef" for char in str(value).casefold())
        for value in frame_hashes
    ):
        raise SourceCutawayError(
            f"source event {event.get('id')!r} requires reviewed frame SHA-256 evidence"
        )


def _validate_inputs(
    source_events: Mapping[str, Any],
    selection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    target: Mapping[str, Any],
) -> None:
    if source_events.get("schema_version") != SOURCE_EVENTS_SCHEMA_VERSION:
        raise SourceCutawayError("unsupported source-events schema_version")
    if selection.get("schema_version") != SOURCE_CUTAWAY_SELECTION_VERSION:
        raise SourceCutawayError("unsupported source-cutaway selection schema_version")
    if target.get("schema_version") != AROLL_TARGET_SCHEMA_VERSION:
        raise SourceCutawayError("unsupported A-roll target schema_version")

    snapshot_sha = canonical_sha256(source_snapshot)
    if source_events.get("source_snapshot_sha256") != snapshot_sha:
        raise SourceCutawayError("source-events artifact is stale: source snapshot hash differs")
    if target.get("source_snapshot_sha256") != snapshot_sha:
        raise SourceCutawayError("A-roll target is stale: source snapshot hash differs")
    events_sha = canonical_sha256(source_events)
    if selection.get("source_events_sha256") != events_sha:
        raise SourceCutawayError("source-cutaway selection is stale: source-events hash differs")

    source_timeline_id = str(source_snapshot.get("id") or "")
    if not source_timeline_id:
        raise SourceCutawayError("source snapshot requires an exact timeline id")
    if str(source_events.get("source_timeline_id") or "") != source_timeline_id:
        raise SourceCutawayError("source-events artifact belongs to a different source timeline")
    if str(target.get("source_timeline_id") or "") != source_timeline_id:
        raise SourceCutawayError("A-roll target belongs to a different source timeline")
    target_id = str(target.get("target_timeline_id") or "")
    if not target_id or target_id == source_timeline_id:
        raise SourceCutawayError("target must be a separate recoverable A-roll variant")
    if target.get("recoverable") is not True or target.get("source_preserved") is not True:
        raise SourceCutawayError("target must explicitly preserve the source and be recoverable")


def build_source_cutaway_request(
    source_events: Mapping[str, Any],
    selection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    track_index: int = 2,
) -> Dict[str, Any]:
    """Build a dry-run ``media_pool.append_to_timeline`` request for V2+.

    ``end_frame`` is exclusive, matching the repository's append contract.
    The returned dispatch payload is informational until a separate caller has
    explicitly approved it, confirmed the target timeline is current, and
    invoked the existing ``media_pool`` dispatcher.
    """
    _validate_inputs(source_events, selection, source_snapshot, target)
    destination_track = _integer(track_index, "track_index")
    if destination_track < 2:
        raise SourceCutawayError("source cutaways must be placed on V2 or higher")

    source_fps_timeline = _positive_number(source_snapshot.get("fps"), "snapshot.fps")
    target_fps = _positive_number(target.get("fps"), "target.fps")
    target_start = _integer(target.get("start_frame"), "target.start_frame")
    target_end = _integer(target.get("end_frame"), "target.end_frame")
    if target_end <= target_start:
        raise SourceCutawayError("target timeline bounds are empty")

    event_rows = list(source_events.get("events") or [])
    events: Dict[str, Mapping[str, Any]] = {}
    for event in event_rows:
        event_id = str(event.get("id") or "")
        if not event_id:
            raise SourceCutawayError("every source event requires an id")
        if event_id in events:
            raise SourceCutawayError(f"duplicate source event id: {event_id}")
        _validate_frame_review(event)
        events[event_id] = event
    if not events:
        raise SourceCutawayError("source-events artifact contains no reviewed events")

    clip_infos: List[Dict[str, Any]] = []
    mappings: List[Dict[str, Any]] = []
    record_ranges: List[Tuple[int, int, str]] = []
    seen_placements: set[str] = set()
    for index, placement in enumerate(selection.get("placements") or []):
        placement_id = str(placement.get("id") or placement.get("candidate_id") or f"placement-{index + 1:04d}")
        if placement_id in seen_placements:
            raise SourceCutawayError(f"duplicate source-cutaway placement id: {placement_id}")
        seen_placements.add(placement_id)
        if placement.get("visual_type") != "source_cutaway":
            raise SourceCutawayError(f"placement {placement_id!r} is not a source_cutaway")
        event_id = str(placement.get("source_event_id") or "")
        event = events.get(event_id)
        if event is None:
            raise SourceCutawayError(f"placement {placement_id!r} references an unknown source event")

        item, event_record_start, event_record_end = _source_item_for_event(source_snapshot, event)
        expected_source_hash = str(event.get("source_item_sha256") or "")
        actual_source_hash = source_item_sha256(item)
        if not expected_source_hash or expected_source_hash != actual_source_hash:
            raise SourceCutawayError(
                f"source event {event_id!r} is stale: exact V1 source item hash differs"
            )
        exact_media_id = str(item.get("media_pool_item_id") or "")
        if exact_media_id != str(event.get("media_pool_item_id") or ""):
            raise SourceCutawayError(f"source event {event_id!r} no longer resolves to its exact media pool item")

        duration_seconds = _positive_number(placement.get("duration_seconds"), "placement.duration_seconds")
        source_offset_seconds = _finite_number(
            placement.get("source_offset_seconds", 0), "placement.source_offset_seconds",
        )
        if source_offset_seconds < 0:
            raise SourceCutawayError("placement.source_offset_seconds cannot be negative")
        available_timeline_frames = event_record_end - event_record_start
        available_seconds = available_timeline_frames / source_fps_timeline
        # The frame-reviewed event is authoritative in timeline-frame space.
        # Do not let a positive subframe overrun through a floating tolerance,
        # then round it back inside later.  Accepted offsets and durations are
        # quantized once here and every downstream mapping derives from them.
        if source_offset_seconds + duration_seconds > available_seconds:
            raise SourceCutawayError(f"placement {placement_id!r} exceeds its frame-reviewed source event")

        source_offset_timeline_frames = round(source_offset_seconds * source_fps_timeline)
        duration_timeline_frames = round(duration_seconds * source_fps_timeline)
        if duration_timeline_frames <= 0:
            raise SourceCutawayError(f"placement {placement_id!r} rounds to an empty reviewed range")
        if source_offset_timeline_frames + duration_timeline_frames > available_timeline_frames:
            raise SourceCutawayError(f"placement {placement_id!r} exceeds its frame-reviewed source event")
        quantized_offset_seconds = source_offset_timeline_frames / source_fps_timeline
        quantized_duration_seconds = duration_timeline_frames / source_fps_timeline

        item_record_start = _integer(item.get("start"), "source item start")
        item_source_start = _integer(item.get("source_start"), "source item source_start")
        item_source_end = _integer(item.get("source_end"), "source item source_end")
        item_source_fps = _positive_number(item.get("source_fps"), "source item source_fps")
        chosen_record_start = event_record_start + source_offset_timeline_frames
        seconds_into_item = (chosen_record_start - item_record_start) / source_fps_timeline
        source_start_frame = item_source_start + round(seconds_into_item * item_source_fps)
        source_end_frame = source_start_frame + round(
            duration_timeline_frames * item_source_fps / source_fps_timeline
        )
        if source_start_frame < item_source_start or source_end_frame > item_source_end:
            raise SourceCutawayError(f"placement {placement_id!r} exceeds the exact source media range")
        if source_end_frame <= source_start_frame:
            raise SourceCutawayError(f"placement {placement_id!r} rounds to an empty source range")

        target_seconds = _finite_number(placement.get("start_seconds"), "placement.start_seconds")
        if target_seconds < 0:
            raise SourceCutawayError("placement.start_seconds cannot be negative")
        record_frame = target_start + round(target_seconds * target_fps)
        record_end_frame = record_frame + round(
            duration_timeline_frames * target_fps / source_fps_timeline
        )
        if record_frame < target_start or record_end_frame > target_end:
            raise SourceCutawayError(f"placement {placement_id!r} is outside the target timeline")
        if record_end_frame <= record_frame:
            raise SourceCutawayError(f"placement {placement_id!r} rounds to an empty target range")
        for prior_start, prior_end, prior_id in record_ranges:
            if record_frame < prior_end and record_end_frame > prior_start:
                raise SourceCutawayError(
                    f"source-cutaway placements overlap on V{destination_track}: {prior_id!r} and {placement_id!r}"
                )
        record_ranges.append((record_frame, record_end_frame, placement_id))

        clip_info = {
            "media_pool_item_id": exact_media_id,
            "start_frame": source_start_frame,
            "end_frame": source_end_frame,
            "record_frame": record_frame,
            "record_frame_mode": "absolute",
            "track_index": destination_track,
            "media_type": 1,
        }
        clip_infos.append(clip_info)
        mappings.append({
            "placement_id": placement_id,
            "source_event_id": event_id,
            "source_timeline_item_id": item.get("timeline_item_id"),
            "media_pool_item_id": exact_media_id,
            "source_item_sha256": actual_source_hash,
            "source_seconds": {
                "event_start": event.get("start_seconds"),
                "offset": quantized_offset_seconds,
                "duration": quantized_duration_seconds,
                "requested_offset": source_offset_seconds,
                "requested_duration": duration_seconds,
            },
            "source_frames": {"start": source_start_frame, "end_exclusive": source_end_frame},
            "target_seconds": {"start": target_seconds, "duration": quantized_duration_seconds},
            "target_frames": {"start": record_frame, "end_exclusive": record_end_frame},
        })
    if not clip_infos:
        raise SourceCutawayError("selection contains no source-cutaway placements")

    clip_infos_and_ranges = sorted(
        zip(clip_infos, mappings), key=lambda pair: pair[0]["record_frame"],
    )
    clip_infos = [pair[0] for pair in clip_infos_and_ranges]
    mappings = [pair[1] for pair in clip_infos_and_ranges]
    provenance = {
        "source_snapshot_sha256": canonical_sha256(source_snapshot),
        "source_events_sha256": canonical_sha256(source_events),
        "selection_sha256": canonical_sha256(selection),
        "target_sha256": canonical_sha256(target),
    }
    return {
        "schema_version": SOURCE_CUTAWAY_REQUEST_VERSION,
        "kind": "native-source-cutaway-plan",
        "success": True,
        "dry_run": True,
        "apply_authorized": False,
        "source_media_modified": False,
        "source_timeline_id": source_snapshot.get("id"),
        "target_timeline_id": target.get("target_timeline_id"),
        "destination_video_track": destination_track,
        "placement_count": len(clip_infos),
        "mappings": mappings,
        "dispatch": {
            "tool": "media_pool",
            "action": "append_to_timeline",
            "params": {"clip_infos": clip_infos},
        },
        "approval_gate": {
            "explicit_apply_required": True,
            "explicit_visual_approval_required": True,
            "current_timeline_id_must_equal": target.get("target_timeline_id"),
            "recoverable_target_required": True,
        },
        "provenance": provenance,
    }
