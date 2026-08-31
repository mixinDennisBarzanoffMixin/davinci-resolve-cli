"""Bash-composable Resolve production pipeline CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import importlib.util
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils import production_pipeline as pipeline


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class _RawOutput:
    text: str


def _emit(payload: Any, *, pretty: bool = False) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None) + "\n")


def _live_snapshot() -> Dict[str, Any]:
    from src import server

    structure = server.timeline("probe_timeline_structure", {
        "track_types": ["video", "audio", "subtitle"],
        "include_markers": True,
    })
    if structure.get("error"):
        raise pipeline.ProductionPipelineError(str(structure["error"]))
    settings = server.timeline("get_setting", {})
    values = settings.get("settings") or {}
    if not isinstance(values, dict):
        values = {}
    audio_count = int((((structure.get("tracks") or {}).get("audio") or {}).get("track_count") or 0))
    metadata = []
    for index in range(1, audio_count + 1):
        row = server.timeline("probe_audio_track", {"track_index": index})
        if not row.get("error"):
            metadata.append(row)
    return pipeline.normalize_snapshot(
        structure,
        fps=values.get("timelineFrameRate") or values.get("timelinePlaybackFrameRate") or 24,
        width=values.get("timelineResolutionWidth") or 1920,
        height=values.get("timelineResolutionHeight") or 1080,
        audio_tracks=metadata,
    )


def _load_snapshot(path: Optional[str]) -> Dict[str, Any]:
    return pipeline.read_json(path) if path else _live_snapshot()


def _transcribe(audio: str, output_dir: Path, args: argparse.Namespace) -> Dict[str, Any]:
    from src.utils import media_analysis

    output_dir.mkdir(parents=True, exist_ok=True)
    words_only = bool(getattr(args, "words_only", False))
    artifacts = {
        "analysis_json": str(output_dir / "analysis.json"),
        "transcript_json": str(output_dir / "transcript.json"),
    }
    if not words_only:
        artifacts.update({
            "transcript_srt": str(output_dir / "captions.srt"),
            "transcript_vtt": str(output_dir / "captions.vtt"),
        })
    requested_language = str(args.language or "auto").strip()
    recognizer_language = None if requested_language.casefold() in {"auto", "detect"} else requested_language
    initial_prompt = args.initial_prompt
    if initial_prompt is None and recognizer_language == "bg":
        initial_prompt = (
            "Kia K8, LPG, автомобил, двигател, автоматична скоростна кутия, "
            "конски сили, евро"
        )
    options = {
        "transcription": {
            "enabled": True,
            "backend": args.backend,
            "model": args.model,
            "word_timestamps": True,
            "allow_model_download": bool(args.allow_model_download),
            "timeout": args.timeout,
            "wall_clock_seconds": args.timeout,
            "initial_prompt": initial_prompt,
            # Long monologues are prone to carrying a mistaken phrase into
            # subsequent windows.  Prefer independent windows and explicitly
            # gate suspicious silence/repetition diagnostics below.
            "condition_on_previous_text": False,
            "hallucination_silence_threshold": 2.0,
        }
    }
    if recognizer_language:
        options["transcription"]["language"] = recognizer_language
    result = media_analysis._transcribe(audio, artifacts, options, media_analysis.detect_capabilities())
    if not result.get("success"):
        raise pipeline.ProductionPipelineError(
            str(result.get("error") or result.get("reason") or "transcription failed")
        )
    normalized_words = pipeline.transcript_words(result)
    bundle = None if words_only else pipeline.caption_bundle(result)
    backend = str(result.get("backend") or args.backend or "unknown")
    default_timing_provenance = (
        "whisper_cross_attention"
        if backend in {"mlx_whisper", "whisper_cli"}
        else f"{backend}_reported"
    )
    words = [
        {**row, "timing_provenance": row.get("timing_provenance") or default_timing_provenance}
        for row in normalized_words
    ]
    provenance = {
        "audio_path": str(Path(audio).resolve()),
        "audio_sha256": pipeline.file_sha256(audio),
        "resolve_audio_track": args.track,
        "snapshot_path": str(Path(args.snapshot).resolve()) if args.snapshot else None,
        "snapshot_sha256": pipeline.file_sha256(args.snapshot) if args.snapshot else None,
        "model": result.get("model") or args.model,
        "requested_language": requested_language,
        "language_mode": "detected" if recognizer_language is None else "forced",
        "decode_options": result.get("decode_options") or options["transcription"],
    }
    transcript_payload = {**result, "words": words, "provenance": provenance}
    pipeline.write_json(output_dir / "transcript.json", transcript_payload)
    removed_caption_outputs = []
    if words_only and bool(getattr(args, "overwrite", False)):
        for name in ("captions.srt", "captions.vtt", "captions.json", "caption-qc.json"):
            stale_path = output_dir / name
            if stale_path.is_file():
                stale_path.unlink()
                removed_caption_outputs.append(str(stale_path.resolve()))
    word_outputs = {
        "jsonl": output_dir / "words.jsonl",
        "tsv": output_dir / "words.tsv",
        "text": output_dir / "words.txt",
        "remotion-json": output_dir / "words.remotion.json",
    }
    for output_format, path in word_outputs.items():
        pipeline.write_text(
            path,
            pipeline.format_word_timestamps(
                transcript_payload,
                output_format=output_format,
                pretty=output_format == "remotion-json",
            ),
        )
    if bundle is not None:
        pipeline.write_json(output_dir / "captions.json", bundle["blocks"])
        pipeline.write_json(output_dir / "caption-qc.json", bundle["qc"])
        pipeline.write_text(output_dir / "captions.srt", bundle["srt"])
        pipeline.write_text(output_dir / "captions.vtt", bundle["vtt"])
    expected = requested_language.split("-")[0].casefold()
    detected = str(result.get("language") or "unknown").split("-")[0].casefold()
    suspicious = []
    for index, segment in enumerate(result.get("segments") or []):
        reasons = []
        if float(segment.get("no_speech_prob") or 0) >= 0.6:
            reasons.append("high_no_speech_probability")
        if float(segment.get("compression_ratio") or 0) >= 2.4:
            reasons.append("high_compression_ratio_repetition_risk")
        if float(segment.get("avg_logprob") or 0) < -1.0:
            reasons.append("low_average_log_probability")
        if reasons:
            suspicious.append({
                "segment_index": index,
                "reasons": reasons,
                "text": segment.get("text"),
            })
    low_confidence_words = [
        row for row in words
        if row.get("confidence") is not None and float(row["confidence"]) < 0.75
    ]
    uncertainty_lines = ["start_seconds\tend_seconds\tconfidence\tword"]
    uncertainty_lines.extend(
        f"{row['start_seconds']:.3f}\t{row['end_seconds']:.3f}\t{row['confidence']:.4f}\t{row['word']}"
        for row in low_confidence_words
    )
    pipeline.write_text(output_dir / "uncertainty.tsv", "\n".join(uncertainty_lines) + "\n")
    raw_word_count = sum(len(segment.get("words") or []) for segment in result.get("segments") or [])
    quality = {
        "status": "passed",
        "requested_language": expected,
        "language_mode": "detected" if recognizer_language is None else "forced",
        "detected_language": detected if recognizer_language is None else None,
        "language_matches": (
            detected not in {"", "unknown"}
            if recognizer_language is None
            else None
        ),
        "word_timestamps_complete": bool(words) and (not raw_word_count or raw_word_count == len(words)),
        "raw_word_count": raw_word_count,
        "normalized_word_count": len(words),
        "invalid_or_missing_word_timing_count": max(0, raw_word_count - len(words)),
        "suspicious_segments": suspicious,
        "low_confidence_word_count": len(low_confidence_words),
        "low_confidence_words": low_confidence_words,
        "caption_qc": bundle["qc"] if bundle is not None else None,
        "human_audio_review_required": True,
    }
    low_confidence_rate = len(low_confidence_words) / max(1, len(words))
    quality["low_confidence_word_rate"] = round(low_confidence_rate, 4)
    if (
        quality["language_matches"] is False
        or (bundle is not None and not bundle["qc"].get("passed"))
        or suspicious
        or low_confidence_rate > 0.10
        or quality["human_audio_review_required"]
    ):
        quality["status"] = "needs_review"
    pipeline.write_json(output_dir / "transcription-qc.json", quality)
    return {
        "success": True,
        "publication_ready": quality["status"] == "passed",
        "review_required": quality["status"] != "passed",
        "status": quality["status"],
        "backend": result.get("backend"),
        "language": result.get("language"),
        "word_count": len(words),
        "caption_count": len(bundle["blocks"]) if bundle is not None else 0,
        "output_dir": str(output_dir.resolve()),
        "word_outputs": {key: str(path.resolve()) for key, path in word_outputs.items()},
        "removed_stale_caption_outputs": removed_caption_outputs,
        "caption_qc": bundle["qc"] if bundle is not None else None,
        "transcription_qc": quality,
    }


def _cmd_inspect(args: argparse.Namespace) -> Dict[str, Any]:
    snapshot = _live_snapshot()
    if args.output:
        pipeline.write_json(args.output, snapshot)
    return snapshot


def _cmd_extract(args: argparse.Namespace) -> Dict[str, Any]:
    snapshot = _load_snapshot(args.snapshot)
    plan = pipeline.build_audio_extract_plan(
        snapshot, track_index=args.track, output_path=args.output, sample_rate=args.sample_rate
    )
    if args.plan_only:
        return plan
    return pipeline.execute_audio_extract(plan, overwrite=args.overwrite)


def _cmd_transcribe(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir)
    if (output_dir / "transcript.json").exists() and not args.overwrite:
        raise pipeline.ProductionPipelineError(
            f"transcript output already exists: {output_dir}; pass --overwrite to replace generated artifacts"
        )
    audio = args.audio
    extract_result = None
    if not audio:
        snapshot = _load_snapshot(args.snapshot)
        audio_path = output_dir / f"audio-track-{args.track}.wav"
        extract = pipeline.build_audio_extract_plan(
            snapshot, track_index=args.track, output_path=audio_path, sample_rate=16000
        )
        extract_result = pipeline.execute_audio_extract(extract, overwrite=args.overwrite)
        audio = str(audio_path)
    result = _transcribe(audio, output_dir, args)
    if extract_result:
        result["extract"] = extract_result
    return result


def _cmd_chunk(args: argparse.Namespace) -> Dict[str, Any]:
    result = pipeline.chunk_transcript(
        pipeline.read_json(args.transcript),
        pause_seconds=args.pause_seconds,
        target_seconds=args.target_seconds,
        max_seconds=args.max_seconds,
    )
    result["source_transcript"] = str(Path(args.transcript).resolve())
    if args.output:
        pipeline.write_json(args.output, result)
    return result


def _cmd_words(args: argparse.Namespace) -> Dict[str, Any] | _RawOutput:
    source_path = Path(args.transcript).expanduser().resolve()
    transcript = pipeline.read_json(source_path)
    text = pipeline.format_word_timestamps(
        transcript,
        output_format=args.format,
        pretty=bool(args.pretty),
    )
    if not args.output or args.output == "-":
        return _RawOutput(text)
    output_path = Path(args.output).expanduser().resolve()
    if output_path == source_path:
        raise pipeline.ProductionPipelineError("word output must not overwrite its source transcript")
    if output_path.exists() and not args.overwrite:
        raise pipeline.ProductionPipelineError(
            f"word output already exists: {output_path}; pass --overwrite to replace it"
        )
    pipeline.write_text(output_path, text)
    return {
        "success": True,
        "format": args.format,
        "word_count": len(pipeline.transcript_words(transcript)),
        "input": str(source_path),
        "output": str(output_path),
        "source_media_modified": False,
    }


def _cmd_correct(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    if (output_dir / "transcript.json").exists() and not args.overwrite:
        raise pipeline.ProductionPipelineError(
            f"reviewed transcript already exists: {output_dir}; pass --overwrite to replace it"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    source = pipeline.read_json(args.transcript)
    corrections_payload = pipeline.read_json(args.corrections)
    corrections = (
        corrections_payload.get("corrections")
        if isinstance(corrections_payload, dict)
        else corrections_payload
    )
    if not isinstance(corrections, list):
        raise pipeline.ProductionPipelineError("corrections file must be an array or {corrections: [...]} object")
    corrected = pipeline.apply_word_corrections(source, corrections)
    review = {
        "audio_verified": bool(args.audio_verified),
        "reviewer": args.reviewer,
        "correction_count": len(corrected["corrections"]["applied"]),
        "rejected_count": len(corrected["corrections"]["rejected"]),
        "timing_note": "Corrected multi-word spans use interpolated word timing until forced alignment is run.",
    }
    corrected["review"] = review
    bundle = pipeline.caption_bundle(corrected)
    pipeline.write_json(output_dir / "transcript.json", corrected)
    pipeline.write_json(output_dir / "captions.json", bundle["blocks"])
    pipeline.write_json(output_dir / "caption-qc.json", bundle["qc"])
    pipeline.write_json(output_dir / "review.json", review)
    pipeline.write_text(output_dir / "captions.srt", bundle["srt"])
    pipeline.write_text(output_dir / "captions.vtt", bundle["vtt"])
    return {
        "success": corrected["success"] and bundle["qc"].get("passed", False) and review["audio_verified"],
        "output_dir": str(output_dir),
        "applied": corrected["corrections"]["applied"],
        "rejected": corrected["corrections"]["rejected"],
        "caption_qc": bundle["qc"],
        "review": review,
    }


def _cmd_init(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshot = _live_snapshot()
    pipeline.write_json(root / "timeline.json", snapshot)
    pipeline.write_json(root / "research-schema.json", pipeline.RESEARCH_SCHEMA)
    prompt = pipeline.research_prompt(
        listing_url=args.url,
        subject=args.subject,
        target_language=args.language,
        transcript_path=(
            "guide-transcript-reviewed/transcript.json or guide-transcript/transcript.json "
            "for editorial intent, plus transcript-reviewed/transcript.json or "
            "transcript/transcript.json for the source-language recording"
        ),
    )
    pipeline.write_text(root / "research-prompt.md", prompt)
    manifest = {
        "name": args.name,
        "subject": args.subject,
        "listing_url": args.url,
        "language": args.language,
        "primary_audio_track": args.primary_track,
        "guide_audio_track": args.guide_track,
        "timeline": "timeline.json",
        "caption_transcript": "transcript/transcript.json",
        "edit_transcript": "guide-transcript/transcript.json",
        "chunks": "chunks.json",
        "research": "research.json",
        "broll_plan": "broll-plan.json",
        "remotion_manifest": "remotion.json",
    }
    pipeline.write_json(root / "production.json", manifest)
    return {
        "success": True,
        "production_root": str(root),
        "manifest": manifest,
        "next": [
            f"dvr production transcribe --snapshot {root / 'timeline.json'} --track {args.primary_track} --language {args.language} --output-dir {root / 'transcript'} --allow-model-download",
            f"dvr production transcribe --snapshot {root / 'timeline.json'} --track {args.guide_track} --language auto --output-dir {root / 'guide-transcript'} --allow-model-download",
            f"dvr production research --project-dir {root} --run",
            f"dvr production plan --project-dir {root}",
        ],
    }


def _cmd_research(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.project_dir).resolve()
    prompt_path = root / "research-prompt.md"
    schema_path = root / "research-schema.json"
    output_path = root / "research.json"
    candidate_path = root / "research-candidate.json"
    if not prompt_path.is_file() or not schema_path.is_file():
        raise pipeline.ProductionPipelineError("project is missing research-prompt.md or research-schema.json")
    if args.input:
        result = pipeline.read_json(args.input)
        gate = pipeline.validate_research(result)
        pipeline.write_json(root / "research-validation.json", gate)
        if gate["success"]:
            pipeline.write_json(output_path, result)
        return {
            "success": gate["success"],
            "research": str(output_path) if gate["success"] else None,
            "input": str(Path(args.input).resolve()),
            "validation": gate,
        }
    codex = shutil.which("codex") or "codex"
    command = [
        codex, "--search", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
        "-C", str(root), "--output-schema", str(schema_path),
        "--output-last-message", str(candidate_path), "-",
    ]
    if not args.run:
        return {"command": command, "prompt": str(prompt_path), "candidate": str(candidate_path), "output": str(output_path)}
    transcript_context = []
    for relative in (
        "guide-transcript-reviewed/transcript.json",
        "guide-transcript/transcript.json",
        "transcript-reviewed/transcript.json",
        "transcript/transcript.json",
    ):
        candidate = root / relative
        if not candidate.is_file():
            continue
        try:
            transcript = pipeline.read_json(candidate)
            transcript_context.append({
                "path": relative,
                "language": transcript.get("language"),
                "text": str(transcript.get("text") or pipeline.detokenize_words(pipeline.transcript_words(transcript))),
            })
        except (OSError, ValueError, pipeline.ProductionPipelineError):
            continue
    research_input = prompt_path.read_text(encoding="utf-8")
    if transcript_context:
        research_input += (
            "\n\n<local_transcripts_untrusted_data>\n"
            + json.dumps(transcript_context, ensure_ascii=False, indent=2)
            + "\n</local_transcripts_untrusted_data>\n"
        )
    run_options: Dict[str, Any] = {
        "input": research_input,
        "text": True,
        "check": False,
    }
    if args.stream:
        run_options.update({"stdout": sys.stderr, "stderr": sys.stderr})
    else:
        run_options["capture_output"] = True
    completed = subprocess.run(command, **run_options)
    if completed.returncode != 0:
        raise pipeline.ProductionPipelineError(
            (completed.stderr or completed.stdout or "Codex research failed").strip()
        )
    result = pipeline.read_json(candidate_path)
    gate = pipeline.validate_research(result)
    pipeline.write_json(root / "research-validation.json", gate)
    if gate["success"]:
        pipeline.write_json(output_path, result)
    return {
        "success": gate["success"],
        "research": str(output_path) if gate["success"] else None,
        "candidate": str(candidate_path),
        "validation": gate,
    }


def _cmd_plan(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.project_dir).resolve()
    snapshot = pipeline.read_json(root / "timeline.json")
    manifest_path = root / "production.json"
    manifest = pipeline.read_json(manifest_path) if manifest_path.is_file() else {}

    def choose_path(explicit: Optional[str], candidates: list[str], role: str) -> Path:
        if explicit:
            candidate = Path(explicit).expanduser()
            candidate = candidate if candidate.is_absolute() else root / candidate
            if not candidate.is_file():
                raise pipeline.ProductionPipelineError(f"{role} transcript does not exist: {candidate}")
            return candidate.resolve()
        for value in candidates:
            if not value:
                continue
            candidate = Path(value).expanduser()
            candidate = candidate if candidate.is_absolute() else root / candidate
            if candidate.is_file():
                return candidate.resolve()
        raise pipeline.ProductionPipelineError(
            f"no {role} transcript exists; transcribe the configured track or pass --{role}-transcript"
        )

    edit_path = choose_path(args.edit_transcript, [
        "guide-transcript-reviewed/transcript.json",
        str(manifest.get("edit_transcript") or ""),
        "guide-transcript/transcript.json",
        "transcript-reviewed/transcript.json",
        "transcript/transcript.json",
    ], "edit")
    caption_path = choose_path(args.caption_transcript, [
        "transcript-reviewed/transcript.json",
        str(manifest.get("caption_transcript") or manifest.get("transcript") or ""),
        "transcript/transcript.json",
        str(edit_path),
    ], "caption")
    edit_transcript = pipeline.read_json(edit_path)
    caption_transcript = pipeline.read_json(caption_path)
    chunks_path = Path(args.chunks).expanduser() if args.chunks else root / "chunks.json"
    if not chunks_path.is_absolute():
        chunks_path = root / chunks_path
    chunks_path = chunks_path.resolve()
    if args.chunks:
        if not chunks_path.is_file():
            raise pipeline.ProductionPipelineError(f"chunks file does not exist: {chunks_path}")
        chunks = pipeline.read_json(chunks_path)
    elif chunks_path.is_file():
        chunks = pipeline.read_json(chunks_path)
        recorded_source = str(chunks.get("source_transcript") or "")
        if recorded_source and Path(recorded_source).resolve() != edit_path:
            raise pipeline.ProductionPipelineError(
                "chunks.json belongs to a different edit transcript; pass --chunks explicitly or rebuild it"
            )
    else:
        chunks = pipeline.chunk_transcript(edit_transcript)
        chunks["source_transcript"] = str(edit_path)
        pipeline.write_json(chunks_path, chunks)
    caption_words = pipeline.transcript_words(caption_transcript)
    edit_words = pipeline.transcript_words(edit_transcript)
    matching_chunks = {**chunks, "chunks": []}
    for chunk in chunks.get("chunks") or []:
        source_start = float(chunk["start_seconds"])
        source_end = float(chunk["end_seconds"])
        source_words = [
            word for word in caption_words
            if float(word["end_seconds"]) > source_start and float(word["start_seconds"]) < source_end
        ]
        guide_words = [
            word for word in edit_words
            if float(word["end_seconds"]) > source_start and float(word["start_seconds"]) < source_end
        ]
        matching_chunks["chunks"].append({
            **chunk,
            "match_text": " ".join(filter(None, [
                str(chunk.get("text") or ""),
                pipeline.detokenize_words(source_words),
            ])),
            "match_words": guide_words + source_words,
        })
    research = pipeline.read_json(root / "research.json")
    provenance = {
        "timeline": {"path": str(root / "timeline.json"), "sha256": pipeline.file_sha256(root / "timeline.json")},
        "edit_transcript": {"path": str(edit_path), "sha256": pipeline.file_sha256(edit_path)},
        "caption_transcript": {"path": str(caption_path), "sha256": pipeline.file_sha256(caption_path)},
        "chunks": {"path": str(chunks_path), "sha256": pipeline.file_sha256(chunks_path)},
        "research": {"path": str(root / "research.json"), "sha256": pipeline.file_sha256(root / "research.json")},
    }
    broll = pipeline.plan_broll(matching_chunks, research, max_per_chunk=args.max_per_chunk)
    broll["provenance"] = provenance
    pipeline.write_json(root / "research-validation.json", broll.get("research_gate") or {})
    if not broll.get("success"):
        raise pipeline.ProductionPipelineError(
            "research validation failed; inspect research-validation.json before planning"
        )
    pipeline.write_json(root / "broll-plan.json", broll)
    remotion = pipeline.remotion_manifest(
        snapshot=snapshot,
        transcript=caption_transcript,
        broll_plan=broll,
        chunks_payload=chunks,
    )
    remotion["provenance"] = provenance
    pipeline.write_json(root / "remotion.json", remotion)
    variant = pipeline.aroll_variant_request(
        snapshot, chunks, name=args.variant_name,
        video_tracks=[int(value) for value in args.video_tracks.split(",") if value.strip()],
        audio_tracks=[int(value) for value in args.audio_tracks.split(",") if value.strip()],
    )
    variant["provenance"] = provenance
    pipeline.write_json(root / "a-roll-variant.json", variant)
    return {
        "success": True,
        "edit_transcript": str(edit_path),
        "caption_transcript": str(caption_path),
        "chunks": str(chunks_path),
        "broll_plan": str(root / "broll-plan.json"),
        "remotion_manifest": str(root / "remotion.json"),
        "a_roll_variant": str(root / "a-roll-variant.json"),
        "placements": broll["placement_count"],
    }


def _cmd_apply_aroll(args: argparse.Namespace) -> Dict[str, Any]:
    from src import server

    request = pipeline.read_json(args.request)
    if request.get("action") not in {None, "create_variant_from_ranges"}:
        raise pipeline.ProductionPipelineError("A-roll requests may only call create_variant_from_ranges")
    source_timeline_id = request.get("source_timeline_id")
    current = server.timeline("get_current", {})
    if source_timeline_id and current.get("id") != source_timeline_id:
        raise pipeline.ProductionPipelineError(
            "the current Resolve timeline differs from the A-roll request source timeline"
        )
    params = dict(request.get("params") or {})
    if not args.apply:
        params["dry_run"] = True
        result = server.timeline(request.get("action") or "create_variant_from_ranges", params)
        success = not result.get("error") and result.get("success") is not False
        return {"success": success, "dry_run": True, "result": result}
    params["dry_run"] = False
    result = server.timeline(request.get("action") or "create_variant_from_ranges", params)
    success = not result.get("error") and result.get("success") is not False
    state_path = Path(args.request).resolve().parent / "a-roll-apply.json"
    if success:
        pipeline.write_json(state_path, {
            "success": True,
            "source_timeline_id": source_timeline_id,
            "target_timeline_id": result.get("id"),
            "target_timeline_name": result.get("name"),
            "request": str(Path(args.request).resolve()),
        })
    return {
        "success": success,
        "dry_run": False,
        "source_preserved": True,
        "state": str(state_path) if success else None,
        "result": result,
    }


def _cmd_remotion(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.project_dir).resolve()
    remotion_root = Path(
        os.environ.get("DVR_REMOTION_ROOT") or Path(__file__).resolve().parents[1] / "remotion"
    ).resolve()
    if args.action == "studio":
        command = ["npm", "--prefix", str(remotion_root), "run", "studio", "--", "--props", str(root / "remotion.json")]
    elif args.action == "render":
        output = root / "broll-renders"
        output.mkdir(parents=True, exist_ok=True)
        command = ["npm", "--prefix", str(remotion_root), "run", "render-segments", "--", str(root / "remotion.json"), str(output)]
    else:
        command = [
            "npm", "--prefix", str(remotion_root), "run", "render-captions", "--",
            str(root / "remotion.json"), str(root / "captions-overlay.mov"),
        ]
    if args.print_command:
        return {"command": command}
    completed = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, check=False)
    if completed.returncode != 0:
        raise pipeline.ProductionPipelineError(f"Remotion {args.action} failed")
    return {"success": True, "action": args.action, "project_dir": str(root)}


def _cmd_attach_asset(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.approve_rights:
        raise pipeline.ProductionPipelineError(
            "asset staging requires --approve-rights after confirming reuse permission"
        )
    root = Path(args.project_dir).resolve()
    source = Path(args.path).expanduser().resolve()
    if not source.is_file():
        raise pipeline.ProductionPipelineError(f"asset does not exist: {source}")
    suffix = source.suffix.casefold()
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    video_exts = {".mp4", ".mov", ".m4v", ".webm"}
    if suffix not in image_exts | video_exts:
        raise pipeline.ProductionPipelineError("asset must be a supported image or video file")
    kind = "image" if suffix in image_exts else "video"
    digest = pipeline.file_sha256(source)
    safe_beat = "".join(char if char.isalnum() or char in "-_" else "-" for char in args.beat_id)
    staged_dir = root / "remotion-assets"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged = staged_dir / f"{safe_beat}-{digest[:12]}{suffix}"
    if not staged.exists():
        shutil.copy2(source, staged)
    asset = {
        "src": staged.name,
        "kind": kind,
        "exact_item": bool(args.exact_item),
        "attribution": args.attribution or "",
        "rights_status": "approved",
        "sha256": digest,
        "source_path": str(source),
    }
    changed = 0
    for filename in ("broll-plan.json", "remotion.json"):
        path = root / filename
        payload = pipeline.read_json(path)
        rows = payload.get("placements") or []
        for row in rows:
            if str(row.get("beat_id")) == args.beat_id:
                row["asset"] = asset
                row["status"] = "ready-with-approved-asset"
                changed += 1
        pipeline.write_json(path, payload)
    if not changed:
        raise pipeline.ProductionPipelineError(f"no planned placement has beat_id={args.beat_id!r}")
    return {
        "success": True,
        "beat_id": args.beat_id,
        "asset": asset,
        "staged_path": str(staged),
        "note": "Rerender B-roll; the prior render manifest is now stale by design.",
    }


def _cmd_import_broll(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.project_dir).resolve()
    snapshot = pipeline.read_json(root / "timeline.json")
    render_report = pipeline.read_json(root / "broll-renders" / "render-manifest.json")
    manifest_path = root / "remotion.json"
    if render_report.get("manifestSha256") != pipeline.file_sha256(manifest_path):
        raise pipeline.ProductionPipelineError("render manifest is stale; rerender after the latest plan")
    rendered = list(render_report.get("rendered") or [])
    if not rendered:
        raise pipeline.ProductionPipelineError("render manifest contains no B-roll clips")
    paths = [str(Path(row["output"]).resolve()) for row in rendered]
    missing = [path for path in paths if not Path(path).is_file()]
    if missing:
        raise pipeline.ProductionPipelineError(f"rendered B-roll file is missing: {missing[0]}")
    fps = float(snapshot["fps"])
    timeline_start = int(snapshot["start_frame"])
    apply_state_path = root / "a-roll-apply.json"
    apply_state = pipeline.read_json(apply_state_path) if apply_state_path.is_file() else {}
    target_timeline_id = apply_state.get("target_timeline_id")
    placements = [{
        "path": path,
        "beat_id": row.get("beat_id"),
        "start_frame": 0,
        "end_frame": int(row.get("durationInFrames") or round(float(row["duration_seconds"]) * fps)),
        "record_frame": timeline_start + round(float(row["start_seconds"]) * fps),
        "record_frame_mode": "absolute",
        "track_index": args.video_track,
        "media_type": 1,
    } for path, row in zip(paths, rendered)]
    plan = {
        "success": True,
        "dry_run": not args.apply,
        "source_timeline_id": snapshot.get("id"),
        "target_timeline_id": target_timeline_id,
        "target_status": "applied_variant" if target_timeline_id else "pending_a_roll_variant",
        "video_track": args.video_track,
        "would_import": paths,
        "placements": placements,
        "source_media_modified": False,
    }
    if not args.apply:
        return plan
    if not apply_state.get("target_timeline_id"):
        raise pipeline.ProductionPipelineError(
            "apply the A-roll variant first; B-roll timings are mapped to the compacted variant"
        )
    if not args.approve_visuals:
        raise pipeline.ProductionPipelineError(
            "visual review approval is required; rerun with --approve-visuals after checking every rendered clip"
        )

    from src import server

    current = server.timeline("get_current", {})
    if current.get("id") != target_timeline_id:
        raise pipeline.ProductionPipelineError(
            "the current Resolve timeline is not the applied A-roll variant"
        )
    track_count = server.timeline("get_track_count", {"track_type": "video"})
    count = int(track_count.get("count") or 0)
    while count < args.video_track:
        added = server.timeline("add_track", {"track_type": "video"})
        if added.get("error") or added.get("success") is False:
            raise pipeline.ProductionPipelineError(f"could not create destination video track V{count + 1}: {added}")
        count += 1
    imported = server.media_pool("safe_import_media", {"paths": paths})
    if imported.get("error") or int(imported.get("imported") or 0) != len(paths):
        raise pipeline.ProductionPipelineError(f"B-roll import did not return every clip: {imported}")
    clips = imported.get("clips") or []
    if len(clips) != len(placements):
        raise pipeline.ProductionPipelineError("B-roll import result cannot be matched to every placement")
    clip_infos = []
    for placement, clip in zip(placements, clips):
        clip_infos.append({
            "clip_id": clip.get("id"),
            "start_frame": placement["start_frame"],
            "end_frame": placement["end_frame"],
            "record_frame": placement["record_frame"],
            "record_frame_mode": placement["record_frame_mode"],
            "track_index": placement["track_index"],
            "media_type": 1,
        })
    appended = server.media_pool("append_to_timeline", {"clip_infos": clip_infos})
    if (
        appended.get("error")
        or appended.get("success") is False
        or int(appended.get("count") or 0) != len(clip_infos)
    ):
        raise pipeline.ProductionPipelineError(f"B-roll timeline placement failed: {appended}")
    return {**plan, "dry_run": False, "import": imported, "append": appended}


def _production_doctor() -> Dict[str, Any]:
    remotion_root = Path(
        os.environ.get("DVR_REMOTION_ROOT") or Path(__file__).resolve().parents[1] / "remotion"
    ).resolve()
    checks = {
        "ffmpeg": {"available": bool(shutil.which("ffmpeg")), "path": shutil.which("ffmpeg")},
        "codex": {"available": bool(shutil.which("codex")), "path": shutil.which("codex")},
        "npm": {"available": bool(shutil.which("npm")), "path": shutil.which("npm")},
        "remotion": {
            "available": (remotion_root / "node_modules" / "remotion").is_dir(),
            "root": str(remotion_root),
        },
        "mlx_whisper": {
            "available": importlib.util.find_spec("mlx_whisper") is not None,
            "supported_platform": platform.system() == "Darwin" and platform.machine() == "arm64",
            "python": sys.executable,
        },
    }
    required = ("ffmpeg", "npm", "remotion", "mlx_whisper")
    return {
        "success": all(checks[name]["available"] for name in required),
        "checks": checks,
        "notes": [
            "Codex is optional until the research stage.",
            "Model weights download only when transcription runs with --allow-model-download.",
        ],
    }


def _cmd_doctor(args: argparse.Namespace) -> Dict[str, Any]:
    return _production_doctor()


def _cmd_setup(args: argparse.Namespace) -> Dict[str, Any]:
    remotion_root = Path(
        os.environ.get("DVR_REMOTION_ROOT") or Path(__file__).resolve().parents[1] / "remotion"
    ).resolve()
    package_root = Path(__file__).resolve().parents[1]
    results = []
    if not args.skip_remotion:
        completed = subprocess.run(
            ["npm", "install"],
            cwd=remotion_root,
            stdout=sys.stderr,
            stderr=sys.stderr,
            check=False,
        )
        results.append({"component": "remotion", "returncode": completed.returncode})
        if completed.returncode != 0:
            raise pipeline.ProductionPipelineError("Remotion npm install failed")
    if not args.skip_transcription:
        requirements = package_root / "requirements-production.txt"
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            stdout=sys.stderr,
            stderr=sys.stderr,
            check=False,
        )
        results.append({"component": "mlx-whisper", "returncode": completed.returncode})
        if completed.returncode != 0:
            raise pipeline.ProductionPipelineError("production transcription dependency install failed")
    return {"success": True, "installed": results, "doctor": _production_doctor()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dvr production", description="Resolve → transcript → edit chunks → researched B-roll → Remotion")
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check ffmpeg, Codex, Remotion, and Bulgarian ASR")
    doctor.set_defaults(handler=_cmd_doctor)

    setup = sub.add_parser("setup", help="install Remotion and Apple-Silicon Bulgarian ASR dependencies")
    setup.add_argument("--skip-remotion", action="store_true")
    setup.add_argument("--skip-transcription", action="store_true")
    setup.set_defaults(handler=_cmd_setup)

    inspect = sub.add_parser("inspect", help="snapshot the current Resolve timeline and tracks")
    inspect.add_argument("--output")
    inspect.set_defaults(handler=_cmd_inspect)

    extract = sub.add_parser("extract-track", help="recreate one Resolve audio track as a source-safe sidecar WAV")
    extract.add_argument("--track", type=int, required=True)
    extract.add_argument("--output", required=True)
    extract.add_argument("--snapshot")
    extract.add_argument("--sample-rate", type=int, default=16000)
    extract.add_argument("--overwrite", action="store_true")
    extract.add_argument("--plan-only", action="store_true")
    extract.set_defaults(handler=_cmd_extract)

    transcribe = sub.add_parser("transcribe", help="Bulgarian word timestamps and captions from a file or Resolve audio track")
    source = transcribe.add_mutually_exclusive_group()
    source.add_argument("--audio")
    source.add_argument("--snapshot")
    transcribe.add_argument("--track", type=int, default=1)
    transcribe.add_argument("--output-dir", required=True)
    transcribe.add_argument("--language", default="bg")
    transcribe.add_argument("--backend", default="mlx_whisper")
    transcribe.add_argument("--quality", choices=("balanced", "accurate"), default="balanced")
    transcribe.add_argument("--model")
    transcribe.add_argument(
        "--initial-prompt",
        help="optional glossary/context; Bulgarian car vocabulary is supplied automatically for --language bg",
    )
    transcribe.add_argument("--allow-model-download", action="store_true")
    transcribe.add_argument("--overwrite", action="store_true")
    transcribe.add_argument(
        "--words-only",
        action="store_true",
        help="write word timestamp artifacts without SRT, VTT, or caption blocks",
    )
    transcribe.add_argument("--timeout", type=int, default=3600)
    transcribe.set_defaults(handler=_cmd_transcribe)

    words = sub.add_parser("words", help="emit word-level timestamps from an existing transcript JSON")
    words.add_argument("--transcript", required=True)
    words.add_argument(
        "--format",
        choices=("json", "jsonl", "tsv", "text", "remotion-json"),
        default="json",
    )
    words.add_argument("--output", help="write to a file instead of stdout; '-' also means stdout")
    words.add_argument("--overwrite", action="store_true")
    words.set_defaults(handler=_cmd_words)

    chunk = sub.add_parser("chunk", help="turn word timestamps into editable A-roll chunks")
    chunk.add_argument("--transcript", required=True)
    chunk.add_argument("--output")
    chunk.add_argument("--pause-seconds", type=float, default=0.8)
    chunk.add_argument("--target-seconds", type=float, default=8.0)
    chunk.add_argument("--max-seconds", type=float, default=18.0)
    chunk.set_defaults(handler=_cmd_chunk)

    correct = sub.add_parser("correct", help="apply reviewed lexical corrections without losing word timing")
    correct.add_argument("--transcript", required=True)
    correct.add_argument("--corrections", required=True)
    correct.add_argument("--output-dir", required=True)
    correct.add_argument("--overwrite", action="store_true")
    correct.add_argument("--audio-verified", action="store_true", help="confirm corrections were checked while listening to the audio")
    correct.add_argument("--reviewer", help="name/handle recorded in review.json")
    correct.set_defaults(handler=_cmd_correct)

    init = sub.add_parser("init", help="initialize a reusable production workspace from the open timeline")
    init.add_argument("--name", required=True)
    init.add_argument("--subject", required=True)
    init.add_argument("--url", required=True)
    init.add_argument("--output-dir", required=True)
    init.add_argument("--language", default="bg")
    init.add_argument("--primary-track", type=int, default=1)
    init.add_argument("--guide-track", type=int, default=2)
    init.set_defaults(handler=_cmd_init)

    research = sub.add_parser("research", help="print or run a schema-constrained Codex research job")
    research.add_argument("--project-dir", required=True)
    research.add_argument("--run", action="store_true")
    research.add_argument("--stream", action="store_true")
    research.add_argument("--input", help="validate and adopt an existing research JSON artifact")
    research.set_defaults(handler=_cmd_research)

    plan = sub.add_parser("plan", help="match research beats to transcript chunks and make render/edit manifests")
    plan.add_argument("--project-dir", required=True)
    plan.add_argument("--edit-transcript", help="timed transcript used for chunks/cuts (defaults to guide-track output)")
    plan.add_argument("--caption-transcript", help="timed transcript used for captions (defaults to reviewed primary-track output)")
    plan.add_argument("--chunks", help="explicit reviewed chunks JSON; prevents automatic chunk generation")
    plan.add_argument("--max-per-chunk", type=int, default=1)
    plan.add_argument("--variant-name", default="AI A-roll Selects")
    plan.add_argument("--video-tracks", default="1", help="comma-separated tracks to carry")
    plan.add_argument("--audio-tracks", default="1,2", help="comma-separated tracks to carry")
    plan.set_defaults(handler=_cmd_plan)

    apply_aroll = sub.add_parser("apply-a-roll", help="dry-run or create a recoverable A-roll variant")
    apply_aroll.add_argument("--request", required=True)
    apply_aroll.add_argument("--apply", action="store_true")
    apply_aroll.set_defaults(handler=_cmd_apply_aroll)

    remotion = sub.add_parser("remotion", help="open Studio or render planned B-roll segments")
    remotion.add_argument("action", choices=("studio", "render", "captions"))
    remotion.add_argument("--project-dir", required=True)
    remotion.add_argument("--print-command", action="store_true")
    remotion.set_defaults(handler=_cmd_remotion)

    attach_asset = sub.add_parser("attach-asset", help="stage a rights-approved exact image/video for a planned B-roll beat")
    attach_asset.add_argument("--project-dir", required=True)
    attach_asset.add_argument("--beat-id", required=True)
    attach_asset.add_argument("--path", required=True)
    attach_asset.add_argument("--exact-item", action="store_true")
    attach_asset.add_argument("--attribution")
    attach_asset.add_argument("--approve-rights", action="store_true")
    attach_asset.set_defaults(handler=_cmd_attach_asset)

    import_broll = sub.add_parser("import-broll", help="plan or place rendered segments on a Resolve video track")
    import_broll.add_argument("--project-dir", required=True)
    import_broll.add_argument("--video-track", type=int, default=2)
    import_broll.add_argument("--apply", action="store_true")
    import_broll.add_argument("--approve-visuals", action="store_true", help="confirm every rendered clip passed visual and factual review")
    import_broll.set_defaults(handler=_cmd_import_broll)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    pretty = "--pretty" in raw
    raw = [value for value in raw if value != "--pretty"]
    args = parser.parse_args(raw)
    args.pretty = pretty
    if getattr(args, "command", None) == "transcribe" and not args.model:
        args.model = (
            "mlx-community/whisper-large-v3-mlx"
            if args.quality == "accurate"
            else "mlx-community/whisper-large-v3-turbo"
        )
    try:
        result = args.handler(args)
        if isinstance(result, _RawOutput):
            sys.stdout.write(result.text)
            return EXIT_OK
        _emit(result, pretty=args.pretty)
        return EXIT_ERROR if isinstance(result, dict) and result.get("success") is False else EXIT_OK
    except pipeline.ProductionPipelineError as exc:
        error = {"success": False, "error": str(exc)}
        if getattr(args, "command", None) == "words":
            sys.stderr.write(json.dumps(error, ensure_ascii=False) + "\n")
        else:
            _emit(error)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if os.environ.get("DVR_DEBUG"):
            raise
        error = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        if getattr(args, "command", None) == "words":
            sys.stderr.write(json.dumps(error, ensure_ascii=False) + "\n")
        else:
            _emit(error)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
