"""Caption generation from word timings: SRT / WebVTT under broadcast rules.

Turning a transcript into captions is not line-wrapping. A caption that is
technically synchronised but breaks mid-clause, orphans a word, or sits on
screen for nine seconds fails QC and is unreadable, so the rules a human
captioner follows are encoded here rather than left to the caller:

- a character cap per line, and a cap on lines per block;
- a maximum and a **minimum** block duration — a 4-frame flash is unreadable
  even though it is perfectly in sync;
- breaks preferred at sentence ends, then at clause boundaries, then at long
  pauses, and only then on length alone;
- timings snapped to the words actually spoken, never interpolated;
- a minimum gap between blocks so consecutive captions do not visually merge;
- no single-word orphan line.

## Reading rate is a cap, not a target

Blocks are extended toward `min_block_seconds` when the words are quick, but
never past the next block's start. A caption that overruns its successor is
worse than a short one.

## Frame rates

SRT and WebVTT both carry wall-clock timestamps, so nothing here needs a frame
rate. Retiming across rates is a separate concern and deliberately not folded
in — a caption file that has been silently conformed is very hard to debug.
"""

from __future__ import annotations

import re
import math
from typing import Any, Dict, List, Optional, Sequence

#: Broadcast-conventional defaults. Every one is overridable per call.
DEFAULT_MAX_CHARS_PER_LINE = 42
DEFAULT_MAX_LINES = 2
DEFAULT_MAX_BLOCK_SECONDS = 7.0
DEFAULT_MIN_BLOCK_SECONDS = 0.833  # ~20 frames at 24fps; below this is a flash
DEFAULT_MIN_GAP_SECONDS = 0.084  # ~2 frames, so blocks do not visually merge
#: A silence at least this long is a natural caption break.
DEFAULT_PAUSE_BREAK_SECONDS = 0.6

_SENTENCE_END_RE = re.compile(r"[.!?]['\"”’)]*$")
_CLAUSE_END_RE = re.compile(r"[,;:—-]['\"”’)]*$")

FORMATS = ("srt", "vtt")
DEFAULT_MAX_CHARACTERS_PER_SECOND = 20.0


class CaptionError(ValueError):
    """Invalid caption parameters — refused rather than silently clamped."""


def _word_time(word: Dict[str, Any], key: str, fallback: str) -> Optional[float]:
    value = word.get(key, word.get(fallback))
    return float(value) if isinstance(value, (int, float)) else None


def _clean(word: Dict[str, Any]) -> str:
    return str(word.get("word") or "").strip()


def _wrap(text: str, max_chars: int, max_lines: int) -> Optional[List[str]]:
    """Greedy wrap; None when it will not fit in `max_lines`.

    Returning None rather than truncating is deliberate — silently dropping
    words from a caption is the worst possible failure for accessibility.
    """
    lines: List[str] = []
    current = ""
    for token in text.split():
        candidate = f"{current} {token}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = token
        if len(current) > max_chars:
            return None  # a single token longer than the line cap
    if current:
        lines.append(current)
    return lines if len(lines) <= max_lines else None


def _no_orphan(lines: List[str]) -> List[str]:
    """Rebalance so no line is a lone word when a neighbour could give one up."""
    if len(lines) < 2:
        return lines
    if len(lines[-1].split()) == 1 and len(lines[-2].split()) > 2:
        head = lines[-2].split()
        lines = lines[:-2] + [" ".join(head[:-1]), f"{head[-1]} {lines[-1]}"]
    return lines


def _break_score(word: Dict[str, Any], gap_after: float, pause_break: float) -> int:
    """Higher is a better place to end a caption block."""
    text = _clean(word)
    if _SENTENCE_END_RE.search(text):
        return 3
    if gap_after >= pause_break:
        return 2
    if _CLAUSE_END_RE.search(text):
        return 1
    return 0


def build_blocks(
    words: Sequence[Dict[str, Any]],
    *,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
    max_block_seconds: float = DEFAULT_MAX_BLOCK_SECONDS,
    min_block_seconds: float = DEFAULT_MIN_BLOCK_SECONDS,
    min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS,
    pause_break_seconds: float = DEFAULT_PAUSE_BREAK_SECONDS,
) -> List[Dict[str, Any]]:
    """Group words into caption blocks obeying every rule above."""
    profile = validate_caption_profile(
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        max_block_seconds=max_block_seconds,
        min_block_seconds=min_block_seconds,
        min_gap_seconds=min_gap_seconds,
        pause_break_seconds=pause_break_seconds,
    )
    max_chars_per_line = profile["max_chars_per_line"]
    max_lines = profile["max_lines"]
    max_block_seconds = profile["max_block_seconds"]
    min_block_seconds = profile["min_block_seconds"]
    min_gap_seconds = profile["min_gap_seconds"]
    pause_break_seconds = profile["pause_break_seconds"]
    if max_chars_per_line < 8:
        raise CaptionError("max_chars_per_line below 8 cannot hold readable text")

    timed = [
        w for w in words
        if _clean(w) and _word_time(w, "start_seconds", "start") is not None
    ]
    if not timed:
        return []

    capacity = max_chars_per_line * max_lines
    blocks: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(_clean(w) for w in current)
        lines = _wrap(text, max_chars_per_line, max_lines)
        if lines is None:
            # Never truncate caption text. A token that cannot fit inside the
            # requested line cap must survive intact so QC can report it and a
            # human can choose typography, wording, or layout. Silent word loss
            # is worse than a temporarily overlong caption.
            lines = [text]
        blocks.append({
            "start_seconds": _word_time(current[0], "start_seconds", "start"),
            "end_seconds": (
                _word_time(current[-1], "end_seconds", "end")
                or _word_time(current[-1], "start_seconds", "start")
            ),
            "lines": _no_orphan(lines),
            "word_count": len(current),
        })
        current.clear()

    for index, word in enumerate(timed):
        current.append(word)
        following = timed[index + 1] if index + 1 < len(timed) else None
        if following is None:
            break

        this_end = _word_time(word, "end_seconds", "end") or _word_time(word, "start_seconds", "start")
        next_start = _word_time(following, "start_seconds", "start")
        gap = max(0.0, (next_start or 0.0) - (this_end or 0.0))

        text_now = " ".join(_clean(w) for w in current)
        text_next = f"{text_now} {_clean(following)}"
        block_start = _word_time(current[0], "start_seconds", "start") or 0.0
        would_be_long = ((next_start or 0.0) - block_start) > max_block_seconds
        would_not_fit = len(text_next) > capacity or _wrap(text_next, max_chars_per_line, max_lines) is None

        score = _break_score(word, gap, pause_break_seconds)
        # Sentence ends and real pauses always break. A block that spans a
        # silence leaves text on screen through it, which is the thing captioners
        # break on — and short blocks are already made readable by the
        # min_block_seconds extension below, so no length guard is needed here.
        # Clause breaks (score 1) are only a preference and never force a flush.
        if would_not_fit or would_be_long or score >= 2:
            flush()
    flush()

    # Extend short blocks toward the readable minimum, never into the next one.
    for index, block in enumerate(blocks):
        limit = (
            blocks[index + 1]["start_seconds"] - min_gap_seconds
            if index + 1 < len(blocks)
            else block["end_seconds"] + min_block_seconds
        )
        if block["end_seconds"] - block["start_seconds"] < min_block_seconds:
            block["end_seconds"] = max(block["end_seconds"], min(block["start_seconds"] + min_block_seconds, limit))
        # Enforce the inter-block gap even when no extension happened.
        if index + 1 < len(blocks):
            block["end_seconds"] = min(block["end_seconds"], blocks[index + 1]["start_seconds"] - min_gap_seconds)
        block["end_seconds"] = max(block["end_seconds"], block["start_seconds"] + 1e-3)
        block["start_seconds"] = round(block["start_seconds"], 3)
        block["end_seconds"] = round(block["end_seconds"], 3)
        block["duration_seconds"] = round(block["end_seconds"] - block["start_seconds"], 3)
    return blocks


# ── serialisation ────────────────────────────────────────────────────────────


def _timestamp(seconds: float, *, comma: bool) -> str:
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(seconds, 3600.0)
    minutes, secs = divmod(rest, 60.0)
    millis = int(round((secs - int(secs)) * 1000))
    if millis == 1000:  # rounding carried into the next second
        secs, millis = int(secs) + 1, 0
    sep = "," if comma else "."
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}{sep}{millis:03d}"


def to_srt(blocks: Sequence[Dict[str, Any]]) -> str:
    out: List[str] = []
    for index, block in enumerate(blocks, start=1):
        out.append(str(index))
        out.append(
            f"{_timestamp(block['start_seconds'], comma=True)} --> "
            f"{_timestamp(block['end_seconds'], comma=True)}"
        )
        out.extend(block["lines"])
        out.append("")
    return "\n".join(out)


def to_vtt(blocks: Sequence[Dict[str, Any]]) -> str:
    out: List[str] = ["WEBVTT", ""]
    for block in blocks:
        out.append(
            f"{_timestamp(block['start_seconds'], comma=False)} --> "
            f"{_timestamp(block['end_seconds'], comma=False)}"
        )
        out.extend(block["lines"])
        out.append("")
    return "\n".join(out)


def render(blocks: Sequence[Dict[str, Any]], fmt: str) -> str:
    if fmt not in FORMATS:
        raise CaptionError(f"unknown caption format {fmt!r}; valid: {', '.join(FORMATS)}")
    return to_srt(blocks) if fmt == "srt" else to_vtt(blocks)


# ── QC and conservative repair planning ─────────────────────────────────────


def _qc_lines(block: Dict[str, Any], index: int) -> List[str]:
    raw = block.get("lines")
    if raw is None and isinstance(block.get("text"), str):
        raw = str(block["text"]).splitlines()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise CaptionError(f"blocks[{index}] requires lines or text")
    lines = [str(line).strip() for line in raw]
    if not lines or any(not line for line in lines):
        raise CaptionError(f"blocks[{index}] contains an empty caption line")
    return lines


def _qc_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise CaptionError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CaptionError(f"{field} must be a finite number") from None
    if not math.isfinite(number):
        raise CaptionError(f"{field} must be a finite number")
    return number


def validate_caption_profile(
    *,
    max_characters_per_second: Any = DEFAULT_MAX_CHARACTERS_PER_SECOND,
    max_chars_per_line: Any = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: Any = DEFAULT_MAX_LINES,
    min_block_seconds: Any = DEFAULT_MIN_BLOCK_SECONDS,
    max_block_seconds: Any = DEFAULT_MAX_BLOCK_SECONDS,
    min_gap_seconds: Any = DEFAULT_MIN_GAP_SECONDS,
    pause_break_seconds: Any = DEFAULT_PAUSE_BREAK_SECONDS,
) -> Dict[str, Any]:
    """Validate and canonicalize the thresholds shared by plan/QC paths."""
    cps = _qc_number(max_characters_per_second, "max_characters_per_second")
    chars = _qc_number(max_chars_per_line, "max_chars_per_line")
    lines = _qc_number(max_lines, "max_lines")
    minimum = _qc_number(min_block_seconds, "min_block_seconds")
    maximum = _qc_number(max_block_seconds, "max_block_seconds")
    gap = _qc_number(min_gap_seconds, "min_gap_seconds")
    pause = _qc_number(pause_break_seconds, "pause_break_seconds")
    if not chars.is_integer() or not lines.is_integer():
        raise CaptionError("max_chars_per_line and max_lines must be integers")
    chars_int = int(chars)
    lines_int = int(lines)
    if cps <= 0:
        raise CaptionError("max_characters_per_second must be greater than zero")
    if chars_int < 1 or lines_int < 1:
        raise CaptionError("max_chars_per_line and max_lines must be positive")
    if minimum <= 0 or maximum < minimum:
        raise CaptionError("caption duration bounds are invalid")
    if gap < 0:
        raise CaptionError("min_gap_seconds cannot be negative")
    if pause <= 0:
        raise CaptionError("pause_break_seconds must be greater than zero")
    return {
        "max_characters_per_second": cps,
        "max_chars_per_line": chars_int,
        "max_lines": lines_int,
        "min_block_seconds": minimum,
        "max_block_seconds": maximum,
        "min_gap_seconds": gap,
        "pause_break_seconds": pause,
    }


def normalize_blocks(blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize explicit caption blocks without changing their timing or text."""
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        raise CaptionError("blocks must be an array of caption objects")
    normalized: List[Dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise CaptionError(f"blocks[{index}] must be an object")
        start = _qc_number(block.get("start_seconds", block.get("start")), f"blocks[{index}].start_seconds")
        end = _qc_number(block.get("end_seconds", block.get("end")), f"blocks[{index}].end_seconds")
        if start < 0:
            raise CaptionError(f"blocks[{index}].start_seconds cannot be negative")
        lines = _qc_lines(block, index)
        normalized.append({
            "start_seconds": round(start, 6),
            "end_seconds": round(end, 6),
            "lines": lines,
            "text": "\n".join(lines),
        })
    if not normalized:
        raise CaptionError("blocks cannot be empty")
    return normalized


def validate_timed_words(words: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Require complete, finite, ordered word timing for QC/delivery workflows."""
    if isinstance(words, (str, bytes)) or not isinstance(words, Sequence):
        raise CaptionError("words must be an array of timed word objects")
    normalized: List[Dict[str, Any]] = []
    previous_start: Optional[float] = None
    for index, word in enumerate(words):
        if not isinstance(word, dict):
            raise CaptionError(f"words[{index}] must be an object")
        text = str(word.get("word", word.get("text", ""))).strip()
        if not text:
            raise CaptionError(f"words[{index}].word cannot be empty")
        start_value = word.get("start_seconds", word.get("start"))
        end_value = word.get("end_seconds", word.get("end"))
        if start_value is None or end_value is None:
            raise CaptionError(f"words[{index}] requires start_seconds and end_seconds")
        start = _qc_number(start_value, f"words[{index}].start_seconds")
        end = _qc_number(end_value, f"words[{index}].end_seconds")
        if start < 0 or end <= start:
            raise CaptionError(f"words[{index}] must satisfy 0 <= start_seconds < end_seconds")
        if previous_start is not None and start < previous_start:
            raise CaptionError("words must be ordered by start_seconds")
        normalized.append({
            "word": text,
            "start_seconds": start,
            "end_seconds": end,
        })
        previous_start = start
    if not normalized:
        raise CaptionError("words cannot be empty")
    return normalized


def audit_blocks(
    blocks: Sequence[Dict[str, Any]],
    *,
    max_characters_per_second: float = DEFAULT_MAX_CHARACTERS_PER_SECOND,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
    min_block_seconds: float = DEFAULT_MIN_BLOCK_SECONDS,
    max_block_seconds: float = DEFAULT_MAX_BLOCK_SECONDS,
    min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS,
) -> Dict[str, Any]:
    """Return machine-readable caption readability and timing QC.

    This is deliberately a lint pass. It does not alter cue text or timing and
    does not claim compliance with a particular broadcaster's house style.
    """
    profile = validate_caption_profile(
        max_characters_per_second=max_characters_per_second,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        min_block_seconds=min_block_seconds,
        max_block_seconds=max_block_seconds,
        min_gap_seconds=min_gap_seconds,
    )
    max_characters_per_second = profile["max_characters_per_second"]
    max_chars_per_line = profile["max_chars_per_line"]
    max_lines = profile["max_lines"]
    min_block_seconds = profile["min_block_seconds"]
    max_block_seconds = profile["max_block_seconds"]
    min_gap_seconds = profile["min_gap_seconds"]

    cues = normalize_blocks(blocks)
    issues: List[Dict[str, Any]] = []
    cps_values: List[float] = []
    durations: List[float] = []

    def issue(index: int, code: str, severity: str, message: str, **details: Any) -> None:
        issues.append({
            "cue_index": index,
            "cue_number": index + 1,
            "code": code,
            "severity": severity,
            "message": message,
            **details,
        })

    previous: Optional[Dict[str, Any]] = None
    for index, cue in enumerate(cues):
        start = cue["start_seconds"]
        end = cue["end_seconds"]
        duration = end - start
        if previous is not None:
            if start < previous["start_seconds"]:
                issue(index, "OUT_OF_ORDER", "error", "Cue starts before the preceding cue starts.")
            gap = start - previous["end_seconds"]
            if gap < 0:
                issue(index, "OVERLAP", "error", "Cue overlaps the preceding cue.", overlap_seconds=round(-gap, 3))
            elif gap < min_gap_seconds:
                issue(index, "SHORT_GAP", "warning", "Gap from the preceding cue is below the configured minimum.", gap_seconds=round(gap, 3), minimum_seconds=min_gap_seconds)

        if duration <= 0:
            issue(index, "NON_POSITIVE_DURATION", "error", "Cue must end after it starts.")
            previous = cue
            continue
        durations.append(duration)
        printable_chars = len(" ".join(cue["lines"]))
        cps = printable_chars / duration
        cps_values.append(cps)
        if duration < min_block_seconds:
            issue(index, "FLASH", "warning", "Cue display duration is below the configured readable minimum.", duration_seconds=round(duration, 3), minimum_seconds=min_block_seconds)
        if duration > max_block_seconds:
            issue(index, "LONG_DISPLAY", "warning", "Cue remains on screen beyond the configured maximum.", duration_seconds=round(duration, 3), maximum_seconds=max_block_seconds)
        if len(cue["lines"]) > max_lines:
            issue(index, "TOO_MANY_LINES", "error", "Cue exceeds the configured line count.", line_count=len(cue["lines"]), maximum=max_lines)
        for line_index, line in enumerate(cue["lines"]):
            if len(line) > max_chars_per_line:
                issue(index, "LINE_TOO_LONG", "error", "Caption line exceeds the configured character limit.", line_index=line_index, characters=len(line), maximum=max_chars_per_line)
        if len(cue["lines"]) > 1 and len(cue["lines"][-1].split()) == 1:
            issue(index, "ORPHAN_WORD", "warning", "Final caption line contains a single orphan word.")
        if cps > max_characters_per_second:
            issue(index, "READING_SPEED", "warning", "Cue reading speed exceeds the configured characters-per-second limit.", characters_per_second=round(cps, 2), maximum=max_characters_per_second)
        previous = cue

    errors = sum(row["severity"] == "error" for row in issues)
    warnings = len(issues) - errors
    return {
        "success": errors == 0,
        "passed": not issues,
        "cue_count": len(cues),
        "issue_count": len(issues),
        "error_count": errors,
        "warning_count": warnings,
        "issues": issues,
        "metrics": {
            "duration_seconds": round(
                max(cue["end_seconds"] for cue in cues)
                - min(cue["start_seconds"] for cue in cues),
                3,
            ),
            "earliest_start_seconds": round(min(cue["start_seconds"] for cue in cues), 3),
            "latest_end_seconds": round(max(cue["end_seconds"] for cue in cues), 3),
            "average_cue_seconds": round(sum(durations) / len(durations), 3) if durations else None,
            "average_characters_per_second": round(sum(cps_values) / len(cps_values), 2) if cps_values else None,
            "maximum_characters_per_second": round(max(cps_values), 2) if cps_values else None,
        },
        "profile": {
            "max_characters_per_second": max_characters_per_second,
            "max_chars_per_line": max_chars_per_line,
            "max_lines": max_lines,
            "min_block_seconds": min_block_seconds,
            "max_block_seconds": max_block_seconds,
            "min_gap_seconds": min_gap_seconds,
        },
        "limitations": [
            "This is a configurable readability lint, not certification against a broadcaster-specific delivery specification.",
            "Visual safe-area, font, contrast, and rendered burn-in require frame/render inspection.",
        ],
    }


def plan_repairs(
    blocks: Sequence[Dict[str, Any]],
    *,
    max_characters_per_second: float = DEFAULT_MAX_CHARACTERS_PER_SECOND,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
    min_block_seconds: float = DEFAULT_MIN_BLOCK_SECONDS,
    max_block_seconds: float = DEFAULT_MAX_BLOCK_SECONDS,
    min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS,
) -> Dict[str, Any]:
    """Plan conservative cue repairs; never write a file or mutate Resolve.

    Lexical caption content is preserved while surrounding whitespace and line
    wrapping may be normalized. Timings are only extended/trimmed into available
    gaps; overlaps and impossible reading-speed problems remain visible in the
    post-repair QC instead of deleting words.
    """
    cues = normalize_blocks(blocks)
    options = {
        "max_characters_per_second": max_characters_per_second,
        "max_chars_per_line": max_chars_per_line,
        "max_lines": max_lines,
        "min_block_seconds": min_block_seconds,
        "max_block_seconds": max_block_seconds,
        "min_gap_seconds": min_gap_seconds,
    }
    # Validate every threshold before using it in duration math. This also
    # captures the pre-repair report from the exact normalized cue model.
    before_qc = audit_blocks(cues, **options)
    profile = before_qc["profile"]
    max_characters_per_second = profile["max_characters_per_second"]
    max_chars_per_line = profile["max_chars_per_line"]
    max_lines = profile["max_lines"]
    min_block_seconds = profile["min_block_seconds"]
    max_block_seconds = profile["max_block_seconds"]
    min_gap_seconds = profile["min_gap_seconds"]
    repaired = [dict(cue, lines=list(cue["lines"])) for cue in cues]
    changes: List[Dict[str, Any]] = []
    for index, cue in enumerate(repaired):
        original_lines = list(cue["lines"])
        text = " ".join(original_lines)
        wrapped = _wrap(text, max_chars_per_line, max_lines)
        if wrapped is not None:
            wrapped = _no_orphan(wrapped)
            if wrapped != original_lines:
                cue["lines"] = wrapped
                cue["text"] = "\n".join(wrapped)
                changes.append({"cue_index": index, "kind": "rewrap", "before": original_lines, "after": wrapped})

        start = cue["start_seconds"]
        end = cue["end_seconds"]
        next_start = repaired[index + 1]["start_seconds"] if index + 1 < len(repaired) else None
        ceiling = next_start - min_gap_seconds if next_start is not None else start + max_block_seconds
        target_end = end
        readable_seconds = len(" ".join(cue["lines"])) / max_characters_per_second
        required_seconds = min(max_block_seconds, max(min_block_seconds, readable_seconds))
        if end - start < required_seconds:
            target_end = min(start + required_seconds, ceiling)
        elif end - start > max_block_seconds:
            target_end = start + max_block_seconds
        if target_end > start and abs(target_end - end) > 1e-9:
            cue["end_seconds"] = round(target_end, 6)
            changes.append({"cue_index": index, "kind": "timing", "before_end_seconds": end, "after_end_seconds": cue["end_seconds"]})

        # A preceding cue may still reach into this one. Trim that preceding
        # end only when it leaves a positive duration; never move spoken starts.
        if index > 0:
            previous = repaired[index - 1]
            allowed_end = cue["start_seconds"] - min_gap_seconds
            if previous["end_seconds"] > allowed_end and allowed_end > previous["start_seconds"]:
                before = previous["end_seconds"]
                previous["end_seconds"] = round(allowed_end, 6)
                changes.append({"cue_index": index - 1, "kind": "gap_trim", "before_end_seconds": before, "after_end_seconds": previous["end_seconds"]})

    options = dict(profile)
    return {
        "success": True,
        "dry_run": True,
        "changed": bool(changes),
        "change_count": len(changes),
        "changes": changes,
        "blocks": repaired,
        "before_qc": before_qc,
        "after_qc": audit_blocks(repaired, **options),
        "limitations": [
            "The planner never deletes, paraphrases, or retimes spoken starts.",
            "Unfixable overlaps, long tokens, and high reading speed remain reported for human review.",
        ],
    }


# ── chapters ─────────────────────────────────────────────────────────────────


def chapters_from_blocks(
    blocks: Sequence[Dict[str, Any]],
    *,
    min_spacing_seconds: float = 30.0,
    max_title_chars: int = 60,
) -> List[Dict[str, Any]]:
    """Derive chapters from caption blocks by topic-shift heuristic.

    A long pause landing on a sentence start is the cheapest usable signal for
    "new topic". This is explicitly a heuristic — it produces a starting point
    an editor renames, not a finished chapter list.

    The first chapter is always forced to 00:00 because that is a hard
    requirement of the YouTube description format, not a stylistic choice.
    """
    if not blocks:
        return []
    chapters: List[Dict[str, Any]] = [{
        "start_seconds": 0.0,
        "title": " ".join(blocks[0]["lines"])[:max_title_chars].strip(),
    }]
    for previous, block in zip(blocks, blocks[1:]):
        gap = block["start_seconds"] - previous["end_seconds"]
        if gap < DEFAULT_PAUSE_BREAK_SECONDS:
            continue
        if block["start_seconds"] - chapters[-1]["start_seconds"] < min_spacing_seconds:
            continue
        chapters.append({
            "start_seconds": round(block["start_seconds"], 3),
            "title": " ".join(block["lines"])[:max_title_chars].strip(),
        })
    return chapters


def chapters_to_youtube(chapters: Sequence[Dict[str, Any]]) -> str:
    """YouTube description text. Always starts at 00:00 or YouTube ignores it."""
    lines = []
    for chapter in chapters:
        total = int(chapter["start_seconds"])
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        stamp = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
        lines.append(f"{stamp} {chapter['title']}".rstrip())
    return "\n".join(lines)


def generate(
    words: Sequence[Dict[str, Any]],
    *,
    fmt: str = "srt",
    with_chapters: bool = False,
    **options: Any,
) -> Dict[str, Any]:
    """Word timings → caption text plus the block list and optional chapters."""
    if fmt not in FORMATS:
        raise CaptionError(f"unknown caption format {fmt!r}; valid: {', '.join(FORMATS)}")
    blocks = build_blocks(words, **options)
    if not blocks:
        return {
            "success": False,
            "error": "No timed words to caption.",
            "remediation": "Run transcription for this clip first, then retry.",
        }
    result: Dict[str, Any] = {
        "success": True,
        "format": fmt,
        "text": render(blocks, fmt),
        "blocks": blocks,
        "block_count": len(blocks),
        "duration_seconds": round(blocks[-1]["end_seconds"], 3),
    }
    if with_chapters:
        chapters = chapters_from_blocks(blocks)
        result["chapters"] = chapters
        result["chapters_youtube"] = chapters_to_youtube(chapters)
    return result
