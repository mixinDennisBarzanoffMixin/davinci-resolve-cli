"""Source-safe planning and verified Resolve append support for outro media.

The module reads an existing media file with ``ffprobe`` and hashes it. It does
not transcode, copy, move, delete, relink, or otherwise modify that file. Resolve
operations are available only through caller-supplied callbacks so a CLI layer
can keep connection, approval, and UI concerns outside this pure utility.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


POSTROLL_PLAN_SCHEMA_VERSION = "dvr.postroll-append-plan.v1"


class PostrollError(ValueError):
    """The outro, target, operation result, or readback is unsafe or invalid."""


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PostrollError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        requirement = "greater than zero" if positive else "finite"
        raise PostrollError(f"{field} must be {requirement}")
    return number


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    number = _number(value, field)
    rounded = round(number)
    if abs(number - rounded) > 1e-6:
        raise PostrollError(f"{field} must be an integer")
    result = int(rounded)
    if minimum is not None and result < minimum:
        raise PostrollError(f"{field} must be at least {minimum}")
    return result


def _frame_rate(value: Any) -> float:
    text = str(value or "").strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        denominator_value = _number(denominator, "video frame-rate denominator")
        if denominator_value == 0:
            raise PostrollError("video frame rate is unavailable")
        return _number(numerator, "video frame-rate numerator", positive=True) / denominator_value
    return _number(text, "video frame rate", positive=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _duration_seconds(video: Mapping[str, Any], payload: Mapping[str, Any], fps: float) -> float:
    candidates = [video.get("duration"), (payload.get("format") or {}).get("duration")]
    for candidate in candidates:
        try:
            return _number(candidate, "outro duration", positive=True)
        except PostrollError:
            pass
    frames = video.get("nb_frames")
    if frames not in {None, "", "N/A"}:
        return _integer(frames, "video nb_frames", minimum=1) / fps
    raise PostrollError("outro video duration is unavailable")


def probe_outro_media(
    media_path: str | Path,
    *,
    ffprobe: str = "ffprobe",
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Read and validate one local outro with ffprobe; never alter the file."""

    path = Path(media_path).expanduser().resolve()
    if not path.is_file():
        raise PostrollError(f"outro media does not exist or is not a file: {path}")
    command = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PostrollError(f"ffprobe failed: {exc}") from exc
    if int(getattr(completed, "returncode", 1)) != 0:
        detail = str(getattr(completed, "stderr", "") or "").strip()
        raise PostrollError(f"ffprobe failed for outro media: {detail or 'unknown error'}")
    try:
        payload = json.loads(str(getattr(completed, "stdout", "") or ""))
    except json.JSONDecodeError as exc:
        raise PostrollError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list):
        raise PostrollError("ffprobe response has no stream list")
    videos = [
        stream for stream in streams
        if isinstance(stream, Mapping)
        and stream.get("codec_type") == "video"
        and not bool((stream.get("disposition") or {}).get("attached_pic"))
    ]
    if not videos:
        raise PostrollError("outro media contains no video stream")
    video = next(
        (stream for stream in videos if bool((stream.get("disposition") or {}).get("default"))),
        videos[0],
    )
    width = _integer(video.get("width"), "video width", minimum=1)
    height = _integer(video.get("height"), "video height", minimum=1)
    fps = _frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    duration = _duration_seconds(video, payload, fps)
    raw_frame_count = video.get("nb_frames")
    frame_count = (
        _integer(raw_frame_count, "video nb_frames", minimum=1)
        if raw_frame_count not in {None, "", "N/A"}
        else round(duration * fps)
    )
    if frame_count < 1:
        raise PostrollError("outro video rounds to zero source frames")
    return {
        "path": str(path),
        "sha256": _file_sha256(path),
        "bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "duration_seconds": duration,
        "fps": fps,
        "frame_count": frame_count,
        "video_stream_index": _integer(video.get("index", 0), "video stream index", minimum=0),
        "audio_stream_count": sum(
            1 for stream in streams
            if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
        ),
        "probe_command": command,
        "source_media_modified": False,
    }


def _video_track_count(snapshot: Mapping[str, Any]) -> int:
    direct = snapshot.get("video_track_count")
    nested = ((snapshot.get("tracks") or {}).get("video") or {}).get("track_count")
    value = direct if direct is not None else nested
    return _integer(value or 0, "target video track count", minimum=0)


def build_postroll_plan(
    media_path: str | Path,
    target_snapshot: Mapping[str, Any],
    *,
    track_index: int | None = None,
    probe: Callable[[str | Path], Mapping[str, Any]] = probe_outro_media,
    require_matching_dimensions: bool = True,
) -> dict[str, Any]:
    """Build a video-only V2+ append plan at the target's exact end frame."""

    if not isinstance(target_snapshot, Mapping):
        raise PostrollError("target snapshot must be an object")
    timeline_id = str(target_snapshot.get("id") or "").strip()
    if not timeline_id:
        raise PostrollError("target snapshot requires an exact timeline id")
    timeline_fps = _number(target_snapshot.get("fps"), "target fps", positive=True)
    timeline_width = _integer(target_snapshot.get("width"), "target width", minimum=1)
    timeline_height = _integer(target_snapshot.get("height"), "target height", minimum=1)
    start_frame = _integer(target_snapshot.get("start_frame"), "target start_frame")
    end_frame = _integer(target_snapshot.get("end_frame"), "target end_frame")
    if end_frame < start_frame:
        raise PostrollError("target timeline has invalid frame bounds")
    track_count = _video_track_count(target_snapshot)
    destination = (
        max(2, track_count + 1)
        if track_index is None
        else _integer(track_index, "track_index", minimum=2)
    )
    asset = dict(probe(media_path))
    for field in ("path", "sha256", "width", "height", "duration_seconds", "fps", "frame_count"):
        if field not in asset:
            raise PostrollError(f"outro probe result is missing {field}")
    if require_matching_dimensions and (
        _integer(asset["width"], "outro width", minimum=1) != timeline_width
        or _integer(asset["height"], "outro height", minimum=1) != timeline_height
    ):
        raise PostrollError(
            f"outro dimensions {asset['width']}x{asset['height']} do not match "
            f"timeline {timeline_width}x{timeline_height}"
        )
    source_fps = _number(asset["fps"], "outro fps", positive=True)
    source_frames = _integer(asset["frame_count"], "outro frame_count", minimum=1)
    planned_duration = source_frames / source_fps
    target_frames = round(planned_duration * timeline_fps)
    if target_frames < 1:
        raise PostrollError("outro rounds to zero target frames")
    target_end_after_append = end_frame + target_frames
    clip_template = {
        "start_frame": 0,
        "end_frame": source_frames,
        "record_frame": end_frame,
        "record_frame_mode": "absolute",
        "track_index": destination,
        # Resolve mediaType 1 appends video only, ignoring any embedded/silent audio.
        "media_type": 1,
    }
    return {
        "schema_version": POSTROLL_PLAN_SCHEMA_VERSION,
        "kind": "native-video-only-postroll-plan",
        "success": True,
        "dry_run": True,
        "apply_authorized": False,
        "source_media_modified": False,
        "target_timeline_id": timeline_id,
        "destination_video_track": destination,
        "required_video_track_count": destination,
        "asset": asset,
        "timing": {
            "source_fps": source_fps,
            "source_start_frame": 0,
            "source_end_frame_exclusive": source_frames,
            "timeline_fps": timeline_fps,
            "record_frame": end_frame,
            "record_end_frame_exclusive": target_end_after_append,
            "duration_seconds": planned_duration,
        },
        "dispatch": {
            "import": {
                "action": "import_media",
                "params": {"file_paths": [asset["path"]]},
            },
            "append": {
                "action": "append_to_timeline",
                "clip_info_template": clip_template,
            },
        },
        "approval_gate": {
            "explicit_apply_required": True,
            "current_timeline_id_must_equal": timeline_id,
            "current_end_frame_must_equal": end_frame,
            "destination_track_must_exist_or_be_created": destination,
            "asset_sha256_must_equal": asset["sha256"],
        },
    }


def _result_count(result: Mapping[str, Any], operation: str) -> int:
    if result.get("count") is not None:
        return _integer(result.get("count"), f"{operation} count", minimum=0)
    for key in ("items", "media_pool_items", "imported", "appended"):
        rows = result.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            return len(rows)
    raise PostrollError(f"{operation} result has no verifiable count")


def _result_items(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("items", "media_pool_items", "imported", "appended"):
        rows = result.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def _media_pool_item_id(result: Mapping[str, Any]) -> str:
    direct = result.get("media_pool_item_id") or result.get("id")
    if direct:
        return str(direct)
    items = _result_items(result)
    if len(items) == 1:
        value = items[0].get("media_pool_item_id") or items[0].get("id")
        if value:
            return str(value)
    raise PostrollError("import result does not identify exactly one media pool item")


def apply_postroll_plan(
    plan: Mapping[str, Any],
    *,
    authorize: bool,
    get_current_timeline: Callable[[], Mapping[str, Any]],
    import_media: Callable[[str], Mapping[str, Any]],
    append_to_timeline: Callable[[list[dict[str, Any]]], Mapping[str, Any]],
    readback: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply a postroll plan through callbacks and verify the exact append."""

    if not authorize:
        raise PostrollError("explicit apply authorization is required")
    if plan.get("schema_version") != POSTROLL_PLAN_SCHEMA_VERSION or plan.get("dry_run") is not True:
        raise PostrollError("unsupported or already-applied postroll plan")
    gate = plan.get("approval_gate") or {}
    current = get_current_timeline()
    expected_id = str(gate.get("current_timeline_id_must_equal") or "")
    if str(current.get("id") or "") != expected_id:
        raise PostrollError("the current Resolve timeline differs from the postroll target")
    current_end = _integer(current.get("end_frame"), "current timeline end_frame")
    expected_end = _integer(gate.get("current_end_frame_must_equal"), "planned target end_frame")
    if current_end != expected_end:
        raise PostrollError("the current Resolve timeline end frame changed after planning")
    asset = plan.get("asset") or {}
    path = Path(str(asset.get("path") or "")).expanduser().resolve()
    if not path.is_file() or _file_sha256(path) != str(gate.get("asset_sha256_must_equal") or ""):
        raise PostrollError("the outro asset is missing or changed after planning")

    imported = import_media(str(path))
    if not isinstance(imported, Mapping) or imported.get("error") or imported.get("success") is False:
        raise PostrollError(f"outro import failed: {imported}")
    if _result_count(imported, "import") != 1:
        raise PostrollError("outro import must return exactly one media pool item")
    media_pool_item_id = _media_pool_item_id(imported)
    template = (((plan.get("dispatch") or {}).get("append") or {}).get("clip_info_template") or {})
    clip_info = {**dict(template), "media_pool_item_id": media_pool_item_id}
    if clip_info.get("media_type") != 1:
        raise PostrollError("postroll append must remain video-only with media_type=1")
    appended = append_to_timeline([clip_info])
    if not isinstance(appended, Mapping) or appended.get("error") or appended.get("success") is False:
        raise PostrollError(f"outro append failed: {appended}")
    if _result_count(appended, "append") != 1:
        raise PostrollError("outro append must return exactly one timeline item")

    timing = plan.get("timing") or {}
    expected_start = _integer(timing.get("record_frame"), "planned record_frame")
    expected_record_end = _integer(
        timing.get("record_end_frame_exclusive"), "planned record end_frame",
    )
    destination = _integer(plan.get("destination_video_track"), "destination track", minimum=2)
    verified = readback(
        timeline_id=expected_id,
        track_index=destination,
        start_frame=expected_start,
        end_frame=expected_record_end,
        media_pool_item_id=media_pool_item_id,
    )
    if not isinstance(verified, Mapping):
        raise PostrollError("postroll readback must return an object")
    if str(verified.get("timeline_id") or verified.get("id") or "") != expected_id:
        raise PostrollError("postroll readback came from a different timeline")
    if _integer(verified.get("end_frame"), "readback timeline end_frame") != expected_record_end:
        raise PostrollError("postroll readback timeline end frame does not match the append")
    if _result_count(verified, "readback") != 1:
        raise PostrollError("postroll readback must find exactly one appended item")
    rows = _result_items(verified)
    if len(rows) != 1:
        raise PostrollError("postroll readback must describe exactly one appended item")
    row = rows[0]
    actual_start = _integer(row.get("start_frame", row.get("start")), "readback item start")
    actual_end = _integer(row.get("end_frame", row.get("end")), "readback item end")
    actual_track = _integer(row.get("track_index"), "readback item track", minimum=1)
    actual_media_id = str(row.get("media_pool_item_id") or "")
    if (actual_start, actual_end, actual_track, actual_media_id) != (
        expected_start,
        expected_record_end,
        destination,
        media_pool_item_id,
    ):
        raise PostrollError("postroll readback item does not match the planned append")
    return {
        **json.loads(json.dumps(plan)),
        "dry_run": False,
        "apply_authorized": True,
        "success": True,
        "source_media_modified": False,
        "media_pool_item_id": media_pool_item_id,
        "clip_info": clip_info,
        "import_result": dict(imported),
        "append_result": dict(appended),
        "readback": dict(verified),
    }


__all__ = [
    "POSTROLL_PLAN_SCHEMA_VERSION",
    "PostrollError",
    "apply_postroll_plan",
    "build_postroll_plan",
    "probe_outro_media",
]
