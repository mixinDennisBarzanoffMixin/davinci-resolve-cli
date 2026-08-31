from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import production_cli
from src.utils import production_pipeline as pipeline


def snapshot():
    return pipeline.normalize_snapshot(
        {
            "name": "Timeline 1",
            "id": "timeline-id",
            "start_frame": 216000,
            "end_frame": 216600,
            "tracks": {
                "video": {"track_count": 1, "tracks": [{
                    "track_index": 1,
                    "items": [{
                        "timeline_item_id": "v-item",
                        "media_pool_item_id": "camera",
                        "start": 216000,
                        "end": 216600,
                        "source_start": 0,
                        "source_end": 600,
                        "source_fps": 60,
                    }],
                }]},
                "audio": {"track_count": 2, "tracks": [
                    {"track_index": 1, "items": [{
                        "timeline_item_id": "a1-item",
                        "media_pool_item_id": "camera",
                        "file_path": __file__,
                        "name": "Bulgarian",
                        "start": 216000,
                        "end": 216600,
                        "source_start": 0,
                        "source_end": 600,
                        "source_fps": 60,
                        "source_start_seconds": 0,
                        "source_end_seconds": 10,
                    }]},
                    {"track_index": 2, "items": [{
                        "timeline_item_id": "a2-item",
                        "media_pool_item_id": "narration",
                        "file_path": __file__,
                        "name": "Delayed narration",
                        "start": 216000,
                        "end": 216600,
                        "source_start": 163,
                        "source_end": 763,
                        "source_fps": 60,
                        "source_start_seconds": 2.716667,
                        "source_end_seconds": 12.716667,
                    }]},
                ]},
            },
        },
        fps=60,
        width=1920,
        height=1080,
        audio_tracks=[
            {"track_index": 1, "name": "Audio 1"},
            {"track_index": 2, "name": "Translation"},
        ],
    )


def research_fixture():
    return json.loads(
        (Path(__file__).parents[1] / "examples" / "production" / "kia-k8-research.json").read_text()
    )


class AudioExtractPlanTest(unittest.TestCase):
    def test_source_trim_is_preserved_for_delayed_track(self):
        plan = pipeline.build_audio_extract_plan(
            snapshot(), track_index=2, output_path="/tmp/a2.wav"
        )
        self.assertEqual(plan["track_name"], "Translation")
        self.assertEqual(plan["sources"][0]["source_start_seconds"], 2.716667)
        self.assertEqual(plan["sources"][0]["delay_ms"], 0)
        filters = plan["argv"][plan["argv"].index("-filter_complex") + 1]
        self.assertIn("atrim=start=2.716667:end=12.716667", filters)
        self.assertIn("adelay=0", filters)

    def test_record_gap_becomes_audio_delay(self):
        data = snapshot()
        data["tracks"]["audio"]["tracks"][0]["items"][0]["start"] += 30
        plan = pipeline.build_audio_extract_plan(data, track_index=1, output_path="/tmp/a1.wav")
        self.assertEqual(plan["sources"][0]["delay_ms"], 500)


class TranscriptProductsTest(unittest.TestCase):
    def test_caption_and_editorial_chunk_timings_are_independent(self):
        transcript = {
            "language": "bg",
            "words": [
                {"word": "Това", "start": 0.0, "end": 0.3},
                {"word": "е", "start": 0.35, "end": 0.45},
                {"word": "Kia.", "start": 0.5, "end": 0.9},
                {"word": "Цената", "start": 2.0, "end": 2.4},
                {"word": "е", "start": 2.45, "end": 2.55},
                {"word": "23 014 евро.", "start": 2.6, "end": 3.2},
            ],
        }
        captions = pipeline.caption_bundle(transcript)
        chunks = pipeline.chunk_transcript(transcript, target_seconds=1, max_seconds=4)
        self.assertIn("Това е Kia.", captions["srt"])
        self.assertEqual(chunks["language"], "bg")
        self.assertEqual(chunks["chunk_count"], 2)
        self.assertTrue(all(row["keep"] for row in chunks["chunks"]))

    def test_reviewed_phrase_correction_preserves_full_timing_span(self):
        transcript = {"language": "bg", "words": [
            {"word": "може", "start": 1.0, "end": 1.2, "probability": 0.4},
            {"word": "бе", "start": 1.2, "end": 1.3, "probability": 0.2},
            {"word": "нага", "start": 1.3, "end": 1.8, "probability": 0.1},
            {"word": "да", "start": 1.9, "end": 2.0, "probability": 0.9},
        ]}
        corrected = pipeline.apply_word_corrections(
            transcript, [{"from": "може бе нага", "to": "могат веднага"}]
        )
        self.assertTrue(corrected["success"])
        self.assertEqual([row["word"] for row in corrected["words"][:2]], ["могат", "веднага"])
        self.assertEqual(corrected["words"][0]["start_seconds"], 1.0)
        self.assertEqual(corrected["words"][1]["end_seconds"], 1.8)
        self.assertEqual(corrected["words"][2]["word"], "да")
        self.assertEqual(corrected["words"][0]["timing_provenance"], "interpolated_within_reviewed_phrase")

    def test_bulgarian_hyphen_token_is_not_given_an_extra_space(self):
        transcript = {"words": [
            {"word": "газ", "start": 0, "end": 0.3},
            {"word": "-течна", "start": 0.3, "end": 0.6},
            {"word": "фаза.", "start": 0.6, "end": 1.0},
        ]}
        self.assertIn("газ-течна фаза.", pipeline.caption_bundle(transcript)["srt"])

    def test_word_timestamp_formats_preserve_utf8_and_complete_timing(self):
        transcript = {"words": [
            {"word": "Киа", "start": 1.0, "end": 1.25, "probability": 0.9},
            {"word": "K8", "start": 1.3, "end": 1.6},
        ]}
        jsonl = pipeline.format_word_timestamps(transcript, output_format="jsonl")
        self.assertEqual(len(jsonl.splitlines()), 2)
        self.assertEqual(json.loads(jsonl.splitlines()[0])["word"], "Киа")
        tsv = pipeline.format_word_timestamps(transcript, output_format="tsv")
        self.assertEqual(tsv.splitlines()[0], "start_seconds\tend_seconds\tconfidence\tword")
        self.assertIn("1.000000\t1.250000\t0.9\tКиа", tsv)
        text = pipeline.format_word_timestamps(transcript, output_format="text")
        self.assertIn("[00:00:01.000 --> 00:00:01.250] Киа", text)

    def test_word_timestamp_formats_drop_non_finite_confidence(self):
        transcript = {"words": [
            {"word": "Киа", "start": 1.0, "end": 1.25, "confidence": float("nan")},
            {"word": "K8", "start": 1.3, "end": 1.6, "confidence": "Infinity"},
        ]}
        remotion = json.loads(pipeline.format_word_timestamps(
            transcript, output_format="remotion-json"
        ))
        self.assertEqual([row["confidence"] for row in remotion], [None, None])

    def test_remotion_word_json_uses_exact_caption_fields_and_spacing(self):
        transcript = {"words": [
            {"word": "Това", "start": 0, "end": 0.2, "probability": 0.8},
            {"word": "е", "start": 0.2, "end": 0.3},
            {"word": ".", "start": 0.3, "end": 0.4},
        ]}
        captions = json.loads(pipeline.format_word_timestamps(
            transcript, output_format="remotion-json"
        ))
        self.assertEqual(
            set(captions[0]),
            {"text", "startMs", "endMs", "timestampMs", "confidence"},
        )
        self.assertEqual(captions[0], {
            "text": "Това", "startMs": 0, "endMs": 200,
            "timestampMs": None, "confidence": 0.8,
        })
        self.assertEqual([row["text"] for row in captions], ["Това", " е", "."])


class WordTimestampCliTest(unittest.TestCase):
    def test_transcribe_accepts_words_only_mode(self):
        args = production_cli._parser().parse_args([
            "transcribe", "--output-dir", "/tmp/words", "--words-only",
        ])
        self.assertTrue(args.words_only)

    def test_words_writes_jsonl_to_stdout_without_status_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.json"
            pipeline.write_json(transcript, {"words": [
                {"word": "Здравей", "start": 2, "end": 2.5},
            ]})
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = production_cli.main([
                    "words", "--transcript", str(transcript), "--format", "jsonl",
                ])
            self.assertEqual(code, production_cli.EXIT_OK)
            self.assertEqual(json.loads(stdout.getvalue())["word"], "Здравей")

    def test_words_can_write_an_explicit_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "transcript.json"
            output = Path(tmp) / "words.tsv"
            pipeline.write_json(transcript, {"words": [
                {"word": "дума", "start": 2, "end": 2.5},
            ]})
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = production_cli.main([
                    "words", "--transcript", str(transcript), "--format", "tsv",
                    "--output", str(output),
                ])
            self.assertEqual(code, production_cli.EXIT_OK)
            self.assertIn("2.000000\t2.500000\t\tдума", output.read_text())
            receipt = json.loads(stdout.getvalue())
            self.assertEqual(receipt["word_count"], 1)
            self.assertFalse(receipt["source_media_modified"])

    def test_words_error_uses_stderr_and_keeps_stdout_clean(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), patch("sys.stderr", stderr):
            code = production_cli.main([
                "words", "--transcript", "/definitely/missing/transcript.json",
                "--format", "jsonl",
            ])
        self.assertEqual(code, production_cli.EXIT_ERROR)
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(json.loads(stderr.getvalue())["success"])

    def test_words_only_omits_backend_caption_paths_and_removes_stale_outputs(self):
        def fake_transcribe(_audio, artifacts, _options, _capabilities):
            self.assertNotIn("transcript_srt", artifacts)
            self.assertNotIn("transcript_vtt", artifacts)
            return {
                "success": True,
                "backend": "mock",
                "language": "bg",
                "segments": [],
                "words": [{"word": "Киа", "start": 1.0, "end": 1.25, "confidence": 0.9}],
            }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            audio = output_dir / "audio.wav"
            audio.write_bytes(b"test-audio")
            stale_names = ("captions.srt", "captions.vtt", "captions.json", "caption-qc.json")
            for name in stale_names:
                (output_dir / name).write_text("stale", encoding="utf-8")
            args = SimpleNamespace(
                language="bg", backend="mock", model="mock-model", initial_prompt=None,
                allow_model_download=False, timeout=10, track=1, snapshot=None,
                words_only=True, overwrite=True,
            )
            with patch("src.utils.media_analysis._transcribe", side_effect=fake_transcribe), \
                    patch("src.utils.media_analysis.detect_capabilities", return_value={}):
                result = production_cli._transcribe(str(audio), output_dir, args)
            self.assertTrue(result["success"])
            self.assertTrue(result["review_required"])
            self.assertEqual(len(result["removed_stale_caption_outputs"]), len(stale_names))
            self.assertFalse(any((output_dir / name).exists() for name in stale_names))
            transcript = pipeline.read_json(output_dir / "transcript.json")
            self.assertEqual(transcript["words"][0]["timing_provenance"], "mock_reported")


class ResearchAndBrollTest(unittest.TestCase):
    def test_empty_research_is_not_success(self):
        gate = pipeline.validate_research({
            "exact_item": {"facts": []}, "b_roll_beats": [],
        })
        self.assertFalse(gate["success"])
        self.assertTrue(any("no exact-item facts" in row for row in gate["errors"]))

    def test_unsupported_research_beat_is_rejected(self):
        research = research_fixture()
        research["b_roll_beats"].append({
            "id": "fake", "fact_ids": ["hud"], "trigger_words_bg": ["дисплей"],
            "trigger_words_guide": ["display"], "duration_sec": 2,
            "visual_type": "exact_photo", "visual_brief": "HUD",
            "on_screen_text_bg": "HUD", "evidence_urls": ["https://example.test"],
            "must_not_show": ["unsupported HUD"],
        })
        gate = pipeline.validate_research(research)
        self.assertFalse(gate["success"])
        self.assertIn("price-open", [row["id"] for row in gate["accepted"]])
        self.assertEqual(gate["rejected"][-1]["id"], "fake")

    def test_trigger_words_produce_auditable_placement(self):
        chunks = {"chunks": [{
            "id": "chunk-1", "start_seconds": 1, "end_seconds": 5,
            "text": "Цената и пробег на автомобила са добри",
        }]}
        research = research_fixture()
        planned = pipeline.plan_broll(chunks, research)
        self.assertEqual(planned["placement_count"], 1)
        self.assertEqual(planned["placements"][0]["match"]["tokens"], ["пробег", "цената"])

    def test_dropped_and_protected_chunks_do_not_place_broll_and_time_is_compacted(self):
        chunks = {"chunks": [
            {"id": "drop", "start_seconds": 0, "end_seconds": 5, "text": "цената пробег", "keep": False},
            {"id": "protected", "start_seconds": 5, "end_seconds": 10, "text": "цената пробег", "keep": True, "protected": True},
            {"id": "keep", "start_seconds": 10, "end_seconds": 14, "text": "цената пробег", "keep": True},
        ]}
        planned = pipeline.plan_broll(chunks, research_fixture())
        self.assertEqual(planned["placement_count"], 1)
        self.assertEqual(planned["placements"][0]["chunk_id"], "keep")
        self.assertEqual(planned["placements"][0]["start_seconds"], 0.05)
        self.assertEqual(planned["placements"][0]["source_start_seconds"], 10.05)


class VariantPlanTest(unittest.TestCase):
    def test_variant_uses_source_frames_and_shared_cursor_for_all_tracks(self):
        chunks = {"chunks": [
            {"id": "one", "start_seconds": 1, "end_seconds": 3, "keep": True},
            {"id": "drop", "start_seconds": 3, "end_seconds": 5, "keep": False},
            {"id": "two", "start_seconds": 5, "end_seconds": 6, "keep": True},
        ]}
        request = pipeline.aroll_variant_request(snapshot(), chunks, name="selects")
        rows = request["params"]["ranges"]
        by_chunk = {}
        for row in rows:
            by_chunk.setdefault(row["chunk_id"], []).append(row)
        self.assertEqual({row["record_frame"] for row in by_chunk["one"]}, {216000})
        self.assertEqual({row["record_frame"] for row in by_chunk["two"]}, {216120})
        a2_first = next(row for row in by_chunk["one"] if row["track_type"] == "audio" and row["track_index"] == 2)
        self.assertEqual((a2_first["start_frame"], a2_first["end_frame"]), (223, 343))
        self.assertEqual(request["selected_tracks"]["audio"], [1, 2])
        self.assertFalse(request["params"]["pack"])


class ResearchPromptTest(unittest.TestCase):
    def test_prompt_and_schema_are_utf8_json_artifacts(self):
        prompt = pipeline.research_prompt(
            listing_url="https://example.test/item", subject="Kia K8", transcript_path="transcript.json"
        )
        self.assertIn("untrusted content", prompt)
        self.assertIn("must_not_show", prompt)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            pipeline.write_json(path, pipeline.RESEARCH_SCHEMA)
            self.assertEqual(json.loads(path.read_text())["type"], "object")

    def test_example_research_matches_runtime_schema_and_semantics(self):
        self.assertTrue(pipeline.validate_research(research_fixture())["success"])


class ProductionCliContractTest(unittest.TestCase):
    def test_audio_clean_and_outro_are_dry_run_first_commands(self):
        audio = production_cli._parser().parse_args([
            "audio-clean", "--project-dir", "/tmp/project", "--track", "2",
            "--preset", "strong",
        ])
        self.assertIs(audio.handler, production_cli._cmd_audio_clean)
        self.assertFalse(audio.apply)
        self.assertEqual(audio.track, 2)
        self.assertEqual(audio.preset, "strong")

        outro = production_cli._parser().parse_args([
            "add-outro", "--project-dir", "/tmp/project", "--path", "/tmp/outro.mp4",
        ])
        self.assertIs(outro.handler, production_cli._cmd_add_outro)
        self.assertFalse(outro.apply)
        self.assertFalse(outro.approve_visuals)

    def test_broll_validate_can_require_all_kept_chunks(self):
        args = production_cli._parser().parse_args([
            "broll", "validate", "--project-dir", "/tmp/project", "--require-all-kept",
        ])
        self.assertIs(args.handler, production_cli._cmd_broll_validate)
        self.assertTrue(args.require_all_kept)

    def test_broll_ideate_accepts_generated_only_gap_pass(self):
        args = production_cli._parser().parse_args([
            "broll", "ideate", "--project-dir", "/tmp/project", "--generated-only",
            "--visual-types", "generated_image,diagram", "--visual-types", "motion_graphic",
        ])
        self.assertTrue(args.generated_only)
        self.assertEqual(args.visual_types, ["generated_image,diagram", "motion_graphic"])

    def test_research_command_enables_codex_web_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.write_text(root / "research-prompt.md", "research")
            pipeline.write_json(root / "research-schema.json", pipeline.RESEARCH_SCHEMA)
            result = production_cli._cmd_research(SimpleNamespace(
                project_dir=str(root), input=None, run=False, stream=False,
            ))
            self.assertEqual(result["command"][1:3], ["--search", "exec"])

    def test_broll_dry_run_uses_absolute_record_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            render_dir = root / "broll-renders"
            render_dir.mkdir()
            clip = render_dir / "placement-0001.mp4"
            clip.write_bytes(b"render")
            pipeline.write_json(root / "timeline.json", {
                "id": "source", "fps": 60, "start_frame": 216000,
            })
            pipeline.write_json(root / "remotion-broll.json", {"placements": []})
            pipeline.write_json(render_dir / "render-manifest.json", {
                "manifestSha256": pipeline.file_sha256(root / "remotion-broll.json"),
                "rendered": [{
                    "output": str(clip), "beat_id": "engine", "start_seconds": 1,
                    "duration_seconds": 3, "durationInFrames": 180,
                }],
            })
            result = production_cli._cmd_import_broll(SimpleNamespace(
                project_dir=str(root), manifest="remotion-broll.json", video_track=2,
                apply=False, approve_visuals=False,
            ))
            placement = result["placements"][0]
            self.assertEqual(result["manifest"], str((root / "remotion-broll.json").resolve()))
            self.assertEqual(placement["record_frame"], 216060)
            self.assertEqual(placement["record_frame_mode"], "absolute")


if __name__ == "__main__":
    unittest.main()
