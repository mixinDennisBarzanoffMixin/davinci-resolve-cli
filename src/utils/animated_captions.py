"""Deterministic plans for animated caption *overlays*.

This module deliberately stops at a JSON-serialisable plan.  The planned
objects are Fusion title overlays on a video track; they are not Resolve
subtitle-track items and do not provide an accessible/exportable caption
stream.  Keeping that distinction in the data contract prevents a caller from
accidentally presenting burned-in motion graphics as accessibility captions.

The executor is expected to materialise each placement as a Text+/Fusion title
and to translate the semantic animation channels into the selected template's
inputs.  Planning is Resolve-independent, which makes it suitable for shell
pipelines, review, and dry runs.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import captions


SCHEMA_VERSION = "davinci-resolve-cli.animated-caption-plan/v1"
PRESETS = ("clean", "pop", "word-highlight", "karaoke")
_PRESET_ALIASES = {"word_highlight": "word-highlight"}


class AnimatedCaptionPlanError(ValueError):
    """The requested plan is ambiguous or unsafe to execute."""


def _finite_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise AnimatedCaptionPlanError(f"{field} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise AnimatedCaptionPlanError(f"{field} must be a finite number") from None
    if not result.is_finite():
        raise AnimatedCaptionPlanError(f"{field} must be a finite number")
    return result


def _fps_fraction(value: Any) -> Fraction:
    if isinstance(value, bool):
        raise AnimatedCaptionPlanError("fps must be a positive number or ratio")
    try:
        if isinstance(value, str) and "/" in value:
            result = Fraction(value.strip())
        else:
            result = Fraction(_finite_decimal(value, "fps"))
    except (ValueError, ZeroDivisionError):
        raise AnimatedCaptionPlanError("fps must be a positive number or ratio") from None
    if result <= 0:
        raise AnimatedCaptionPlanError("fps must be greater than zero")
    return result


def _seconds_fraction(value: Any, field: str) -> Fraction:
    result = Fraction(_finite_decimal(value, field))
    if result < 0:
        raise AnimatedCaptionPlanError(f"{field} cannot be negative")
    return result


def _round_half_up(value: Fraction) -> int:
    """Round a non-negative rational to nearest integer, ties away from zero."""
    if value < 0:
        raise AnimatedCaptionPlanError("internal frame conversion cannot be negative")
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + (1 if remainder * 2 >= value.denominator else 0)


def _frame(seconds: Any, fps: Fraction, field: str) -> int:
    return _round_half_up(_seconds_fraction(seconds, field) * fps)


def _word_text(word: Mapping[str, Any]) -> str:
    return str(word.get("word", word.get("text", ""))).strip()


def _word_times(word: Mapping[str, Any], index: int) -> Tuple[Fraction, Fraction]:
    start_value = word.get("start_seconds", word.get("start"))
    end_value = word.get("end_seconds", word.get("end"))
    if start_value is None or end_value is None:
        raise AnimatedCaptionPlanError(
            f"words[{index}] requires start_seconds and end_seconds"
        )
    start = _seconds_fraction(start_value, f"words[{index}].start_seconds")
    end = _seconds_fraction(end_value, f"words[{index}].end_seconds")
    if end <= start:
        raise AnimatedCaptionPlanError(
            f"words[{index}].end_seconds must be after start_seconds"
        )
    return start, end


def _validated_words(words: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(words, (str, bytes)) or not isinstance(words, Sequence):
        raise AnimatedCaptionPlanError("words must be an array of timed word objects")
    result: List[Dict[str, Any]] = []
    previous_start: Optional[Fraction] = None
    for index, word in enumerate(words):
        if not isinstance(word, Mapping):
            raise AnimatedCaptionPlanError(f"words[{index}] must be an object")
        text = _word_text(word)
        if not text:
            raise AnimatedCaptionPlanError(f"words[{index}].word cannot be empty")
        start, end = _word_times(word, index)
        if previous_start is not None and start < previous_start:
            raise AnimatedCaptionPlanError("words must be ordered by start time")
        result.append({
            "word": text,
            "start_seconds": float(start),
            "end_seconds": float(end),
        })
        previous_start = start
    if not result:
        raise AnimatedCaptionPlanError("words cannot be empty")
    return result


def _text_from_lines(lines: Sequence[str]) -> str:
    return "\n".join(line.strip() for line in lines)


def _validated_blocks(
    blocks: Sequence[Mapping[str, Any]],
    *,
    max_chars_per_line: int,
    max_lines: int,
) -> List[Dict[str, Any]]:
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        raise AnimatedCaptionPlanError("blocks must be an array of caption objects")
    result: List[Dict[str, Any]] = []
    previous_end: Optional[Fraction] = None
    for index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            raise AnimatedCaptionPlanError(f"blocks[{index}] must be an object")
        start = _seconds_fraction(block.get("start_seconds"), f"blocks[{index}].start_seconds")
        end = _seconds_fraction(block.get("end_seconds"), f"blocks[{index}].end_seconds")
        if end <= start:
            raise AnimatedCaptionPlanError(
                f"blocks[{index}].end_seconds must be after start_seconds"
            )
        if previous_end is not None and start < previous_end:
            raise AnimatedCaptionPlanError("caption blocks cannot overlap on one target track")

        raw_lines = block.get("lines")
        if raw_lines is None and isinstance(block.get("text"), str):
            raw_lines = str(block["text"]).splitlines()
        if (
            isinstance(raw_lines, (str, bytes))
            or not isinstance(raw_lines, Sequence)
            or not raw_lines
        ):
            raise AnimatedCaptionPlanError(f"blocks[{index}] requires non-empty lines or text")
        lines = [str(line).strip() for line in raw_lines]
        if any(not line for line in lines):
            raise AnimatedCaptionPlanError(f"blocks[{index}].lines cannot contain empty lines")
        if len(lines) > max_lines:
            raise AnimatedCaptionPlanError(
                f"blocks[{index}] exceeds max_lines={max_lines}"
            )
        too_long = next((line for line in lines if len(line) > max_chars_per_line), None)
        if too_long is not None:
            raise AnimatedCaptionPlanError(
                f"blocks[{index}] has a line longer than max_chars_per_line={max_chars_per_line}"
            )

        block_words = block.get("words")
        validated_block_words = (
            _validated_words(block_words) if block_words is not None else []
        )
        for word_index, word in enumerate(validated_block_words):
            word_start = _seconds_fraction(
                word["start_seconds"], f"blocks[{index}].words[{word_index}].start_seconds"
            )
            word_end = _seconds_fraction(
                word["end_seconds"], f"blocks[{index}].words[{word_index}].end_seconds"
            )
            if word_start < start or word_end > end:
                raise AnimatedCaptionPlanError(
                    f"blocks[{index}].words[{word_index}] falls outside its caption block"
                )

        result.append({
            "start_seconds": float(start),
            "end_seconds": float(end),
            "lines": lines,
            "words": validated_block_words,
        })
        previous_end = end
    if not result:
        raise AnimatedCaptionPlanError("blocks cannot be empty")
    return result


def _group_words(
    words: List[Dict[str, Any]],
    *,
    max_chars_per_line: int,
    max_lines: int,
    max_block_seconds: float,
    min_block_seconds: float,
    min_gap_seconds: float,
    pause_break_seconds: float,
) -> List[Dict[str, Any]]:
    try:
        grouped = captions.build_blocks(
            words,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
            max_block_seconds=max_block_seconds,
            min_block_seconds=min_block_seconds,
            min_gap_seconds=min_gap_seconds,
            pause_break_seconds=pause_break_seconds,
        )
    except captions.CaptionError as exc:
        raise AnimatedCaptionPlanError(str(exc)) from exc

    cursor = 0
    result: List[Dict[str, Any]] = []
    for block in grouped:
        count = int(block["word_count"])
        block_words = words[cursor:cursor + count]
        cursor += count
        result.append({
            "start_seconds": block["start_seconds"],
            "end_seconds": block["end_seconds"],
            "lines": list(block["lines"]),
            "words": block_words,
        })
    return result


def _keyframe(channel: str, unit: str, points: Sequence[Tuple[int, Any]]) -> Dict[str, Any]:
    return {
        "channel": channel,
        "unit": unit,
        "interpolation": "ease-out",
        "keyframes": [{"frame": frame, "value": value} for frame, value in points],
    }


def _animation_for(
    preset: str,
    duration_frames: int,
    word_cues: List[Dict[str, Any]],
    fps: Fraction,
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "preset": preset,
        "keyframe_space": "clip-relative-frames",
        "channels": [],
        "word_cues": [],
    }
    if preset == "clean":
        base["intent"] = "static-readable-title"
        return base

    if preset == "pop":
        enter = max(1, min(duration_frames, _round_half_up(fps * Fraction(3, 25))))
        overshoot = max(1, min(enter, _round_half_up(fps * Fraction(2, 25))))
        points = [(0, 0.82), (overshoot, 1.06)]
        if enter != overshoot:
            points.append((enter, 1.0))
        base.update({
            "intent": "quick-scale-and-opacity-entrance",
            "channels": [
                _keyframe("title.scale", "normalized", points),
                _keyframe("title.opacity", "normalized", [(0, 0.0), (enter, 1.0)]),
            ],
        })
        return base

    base["word_cues"] = word_cues
    if preset == "word-highlight":
        base.update({
            "intent": "emphasize-the-current-spoken-word",
            "word_style": {
                "mode": "active-word-color",
                "inactive_role": "caption-foreground",
                "active_role": "caption-accent",
            },
        })
    else:
        base.update({
            "intent": "advance-a-left-to-right-spoken-word-progress-mask",
            "word_style": {
                "mode": "karaoke-progress",
                "inactive_role": "caption-foreground-muted",
                "active_role": "caption-accent",
            },
        })
    return base


def _word_cues(
    words: Sequence[Mapping[str, Any]],
    *,
    fps: Fraction,
    absolute_start_frame: int,
    duration_frames: int,
    timeline_start_frame: int,
) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    for index, word in enumerate(words):
        start = timeline_start_frame + _frame(word["start_seconds"], fps, "word.start_seconds")
        end = timeline_start_frame + _frame(word["end_seconds"], fps, "word.end_seconds")
        relative_start = max(0, min(duration_frames - 1, start - absolute_start_frame))
        relative_end = max(relative_start + 1, min(duration_frames, end - absolute_start_frame))
        cues.append({
            "index": index,
            "text": str(word["word"]),
            "start_frame": relative_start,
            "end_frame_exclusive": relative_end,
        })
    return cues


def preset_catalog() -> Dict[str, Dict[str, Any]]:
    """Return stable, JSON-safe preset metadata for CLI discovery."""
    return {
        "clean": {
            "requires_word_timings": False,
            "description": "Static, readable Fusion title overlay.",
        },
        "pop": {
            "requires_word_timings": False,
            "description": "Short scale/opacity entrance on each caption block.",
        },
        "word-highlight": {
            "requires_word_timings": True,
            "description": "Timed active-word emphasis cues for a Fusion executor.",
        },
        "karaoke": {
            "requires_word_timings": True,
            "description": "Timed spoken-word progress cues for a Fusion executor.",
        },
    }


def plan_animated_captions(
    *,
    fps: Any,
    words: Optional[Sequence[Mapping[str, Any]]] = None,
    blocks: Optional[Sequence[Mapping[str, Any]]] = None,
    timeline_start_frame: int = 0,
    track_index: int = 2,
    preset: str = "pop",
    fusion_template: str = "Text+",
    max_chars_per_line: int = captions.DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = captions.DEFAULT_MAX_LINES,
    max_block_seconds: float = captions.DEFAULT_MAX_BLOCK_SECONDS,
    min_block_seconds: float = captions.DEFAULT_MIN_BLOCK_SECONDS,
    min_gap_seconds: float = captions.DEFAULT_MIN_GAP_SECONDS,
    pause_break_seconds: float = captions.DEFAULT_PAUSE_BREAK_SECONDS,
) -> Dict[str, Any]:
    """Build an executable-intent plan for timed Fusion title overlays.

    Exactly one of ``words`` and ``blocks`` is required. Word input is grouped
    with the broadcast-readable rules in :mod:`src.utils.captions`. Blocks are
    accepted only when their line lengths, order, and timing are valid.
    """
    if (words is None) == (blocks is None):
        raise AnimatedCaptionPlanError("provide exactly one of words or blocks")
    rate = _fps_fraction(fps)
    if isinstance(timeline_start_frame, bool) or not isinstance(timeline_start_frame, int):
        raise AnimatedCaptionPlanError("timeline_start_frame must be an integer")
    if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 1:
        raise AnimatedCaptionPlanError("track_index must be an integer >= 1")
    if not isinstance(fusion_template, str) or not fusion_template.strip():
        raise AnimatedCaptionPlanError("fusion_template cannot be empty")
    if isinstance(max_chars_per_line, bool) or not isinstance(max_chars_per_line, int):
        raise AnimatedCaptionPlanError("max_chars_per_line must be an integer")
    if isinstance(max_lines, bool) or not isinstance(max_lines, int):
        raise AnimatedCaptionPlanError("max_lines must be an integer")

    canonical_preset = _PRESET_ALIASES.get(preset, preset)
    if canonical_preset not in PRESETS:
        raise AnimatedCaptionPlanError(
            f"unknown preset {preset!r}; valid: {', '.join(PRESETS)}"
        )

    if words is not None:
        valid_words = _validated_words(words)
        valid_blocks = _group_words(
            valid_words,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
            max_block_seconds=max_block_seconds,
            min_block_seconds=min_block_seconds,
            min_gap_seconds=min_gap_seconds,
            pause_break_seconds=pause_break_seconds,
        )
        input_kind = "word-timings"
    else:
        valid_blocks = _validated_blocks(
            blocks or [],
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
        input_kind = "caption-blocks"

    if canonical_preset in ("word-highlight", "karaoke"):
        missing = next((i for i, block in enumerate(valid_blocks) if not block["words"]), None)
        if missing is not None:
            raise AnimatedCaptionPlanError(
                f"preset {canonical_preset!r} requires word timings; blocks[{missing}] has none"
            )

    placements: List[Dict[str, Any]] = []
    for index, block in enumerate(valid_blocks):
        start_offset = _frame(block["start_seconds"], rate, "block.start_seconds")
        end_offset = _frame(block["end_seconds"], rate, "block.end_seconds")
        duration = max(1, end_offset - start_offset)
        record_frame = timeline_start_frame + start_offset
        if (
            placements
            and record_frame < placements[-1]["timeline"]["end_frame_exclusive"]
        ):
            raise AnimatedCaptionPlanError(
                f"caption blocks {index - 1} and {index} collide after frame rounding; "
                "increase their gap or use a higher frame rate"
            )
        cues = _word_cues(
            block["words"],
            fps=rate,
            absolute_start_frame=record_frame,
            duration_frames=duration,
            timeline_start_frame=timeline_start_frame,
        )
        placements.append({
            "id": f"animated-caption-{index + 1:04d}",
            "text": _text_from_lines(block["lines"]),
            "lines": list(block["lines"]),
            "source_timing_seconds": {
                "start": block["start_seconds"],
                "end": block["end_seconds"],
            },
            "timeline": {
                "record_frame": record_frame,
                "end_frame_exclusive": record_frame + duration,
                "duration_frames": duration,
                "track_type": "video",
                "track_index": track_index,
            },
            "title": {
                "kind": "fusion-title-overlay",
                "template": fusion_template.strip(),
                "inputs": {"StyledText": _text_from_lines(block["lines"])},
            },
            "animation": _animation_for(canonical_preset, duration, cues, rate),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "output_kind": "fusion-title-overlay-plan",
        "semantics": {
            "native_subtitle_track": False,
            "accessible_caption_stream": False,
            "exportable_as_srt_or_vtt": False,
            "notice": (
                "These are timed Fusion title overlays on a video track, not native "
                "Resolve subtitle items. Keep or create a native subtitle track separately "
                "when accessibility or caption-file delivery is required."
            ),
        },
        "input_kind": input_kind,
        "timing": {
            "fps": {
                "numerator": rate.numerator,
                "denominator": rate.denominator,
                "decimal": float(rate),
            },
            "timeline_start_frame": timeline_start_frame,
            "rounding": "nearest-frame-half-up",
            "end_frames": "exclusive",
        },
        "target": {"track_type": "video", "track_index": track_index},
        "preset": canonical_preset,
        "fusion_template": fusion_template.strip(),
        "placement_count": len(placements),
        "placements": placements,
    }
