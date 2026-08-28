"""Resolve-independent tests for animated Fusion caption-overlay plans."""

from __future__ import annotations

import json
import unittest

from src import server
from src.utils import animated_captions


def timed(text: str, *, start: float = 0.0, word_seconds: float = 0.2, gap: float = 0.05):
    words = []
    cursor = start
    for token in text.split():
        words.append({
            "word": token,
            "start_seconds": cursor,
            "end_seconds": cursor + word_seconds,
        })
        cursor += word_seconds + gap
    return words


class AnimatedCaptionTimingTests(unittest.TestCase):
    def test_exact_fractional_fps_and_start_frame(self):
        plan = animated_captions.plan_animated_captions(
            fps="30000/1001",
            timeline_start_frame=86400,
            track_index=4,
            preset="clean",
            blocks=[{
                "start_seconds": 1.001,
                "end_seconds": 2.002,
                "lines": ["Exactly thirty frames"],
            }],
        )
        placement = plan["placements"][0]
        self.assertEqual(plan["timing"]["fps"]["numerator"], 30000)
        self.assertEqual(plan["timing"]["fps"]["denominator"], 1001)
        self.assertEqual(placement["timeline"]["record_frame"], 86430)
        self.assertEqual(placement["timeline"]["duration_frames"], 30)
        self.assertEqual(placement["timeline"]["track_index"], 4)

    def test_half_frame_rounding_is_half_up_not_bankers(self):
        plan = animated_captions.plan_animated_captions(
            fps=25,
            preset="clean",
            blocks=[{
                "start_seconds": 0.02,
                "end_seconds": 0.10,
                "lines": ["Half up"],
            }],
        )
        item = plan["placements"][0]["timeline"]
        self.assertEqual(item["record_frame"], 1)
        self.assertEqual(item["end_frame_exclusive"], 3)

    def test_gap_and_sentence_punctuation_create_separate_readable_blocks(self):
        words = timed("Wait. This keeps going", word_seconds=0.15, gap=0.05)
        # A real pause after the sentence makes the grouping boundary explicit.
        words[1]["start_seconds"] = 1.2
        words[1]["end_seconds"] = 1.35
        words[2]["start_seconds"] = 1.4
        words[2]["end_seconds"] = 1.55
        words[3]["start_seconds"] = 1.6
        words[3]["end_seconds"] = 1.75
        plan = animated_captions.plan_animated_captions(
            fps=24, words=words, preset="clean"
        )
        self.assertEqual(plan["placement_count"], 2)
        self.assertEqual(plan["placements"][0]["text"], "Wait.")
        self.assertEqual(plan["placements"][1]["text"], "This keeps going")
        first = plan["placements"][0]["timeline"]
        second = plan["placements"][1]["timeline"]
        self.assertLessEqual(first["end_frame_exclusive"], second["record_frame"])


class AnimatedCaptionPresetTests(unittest.TestCase):
    def test_clean_is_static_and_explicitly_not_native_subtitles(self):
        plan = animated_captions.plan_animated_captions(
            fps=24, words=timed("Clean caption"), preset="clean"
        )
        self.assertEqual(plan["output_kind"], "fusion-title-overlay-plan")
        self.assertFalse(plan["semantics"]["native_subtitle_track"])
        self.assertFalse(plan["semantics"]["accessible_caption_stream"])
        self.assertEqual(plan["placements"][0]["animation"]["channels"], [])
        json.dumps(plan)  # the complete contract is shell/JSON serialisable

    def test_pop_has_semantic_scale_and_opacity_keyframes(self):
        plan = animated_captions.plan_animated_captions(
            fps=30, words=timed("Pop caption"), preset="pop"
        )
        animation = plan["placements"][0]["animation"]
        self.assertEqual(animation["keyframe_space"], "clip-relative-frames")
        self.assertEqual(
            [channel["channel"] for channel in animation["channels"]],
            ["title.scale", "title.opacity"],
        )
        for channel in animation["channels"]:
            frames = [keyframe["frame"] for keyframe in channel["keyframes"]]
            self.assertEqual(frames, sorted(set(frames)))

    def test_word_highlight_has_clipped_ordered_word_cues(self):
        plan = animated_captions.plan_animated_captions(
            fps=24,
            timeline_start_frame=100,
            words=timed("one two three", word_seconds=0.25, gap=0.0),
            preset="word_highlight",
        )
        animation = plan["placements"][0]["animation"]
        self.assertEqual(animation["preset"], "word-highlight")
        self.assertEqual([cue["text"] for cue in animation["word_cues"]], ["one", "two", "three"])
        duration = plan["placements"][0]["timeline"]["duration_frames"]
        for cue in animation["word_cues"]:
            self.assertGreaterEqual(cue["start_frame"], 0)
            self.assertGreater(cue["end_frame_exclusive"], cue["start_frame"])
            self.assertLessEqual(cue["end_frame_exclusive"], duration)

    def test_karaoke_schema_from_blocks_with_words(self):
        plan = animated_captions.plan_animated_captions(
            fps=25,
            preset="karaoke",
            blocks=[{
                "start_seconds": 2.0,
                "end_seconds": 3.0,
                "lines": ["Sing along"],
                "words": [
                    {"word": "Sing", "start_seconds": 2.0, "end_seconds": 2.4},
                    {"word": "along", "start_seconds": 2.45, "end_seconds": 2.9},
                ],
            }],
        )
        animation = plan["placements"][0]["animation"]
        self.assertEqual(animation["word_style"]["mode"], "karaoke-progress")
        self.assertEqual(len(animation["word_cues"]), 2)

    def test_preset_catalog_declares_timing_requirements(self):
        catalog = animated_captions.preset_catalog()
        self.assertEqual(set(catalog), set(animated_captions.PRESETS))
        self.assertFalse(catalog["pop"]["requires_word_timings"])
        self.assertTrue(catalog["word-highlight"]["requires_word_timings"])
        self.assertTrue(catalog["karaoke"]["requires_word_timings"])


class AnimatedCaptionValidationTests(unittest.TestCase):
    def assert_bad(self, message: str, **kwargs):
        with self.assertRaisesRegex(animated_captions.AnimatedCaptionPlanError, message):
            animated_captions.plan_animated_captions(**kwargs)

    def test_exactly_one_input_shape_is_required(self):
        self.assert_bad("exactly one", fps=24)
        self.assert_bad(
            "exactly one", fps=24, words=timed("one"),
            blocks=[{"start_seconds": 0, "end_seconds": 1, "lines": ["one"]}],
        )

    def test_bad_rate_track_and_preset_are_rejected(self):
        self.assert_bad("fps", fps=0, words=timed("one"))
        self.assert_bad("track_index", fps=24, track_index=0, words=timed("one"))
        self.assert_bad("unknown preset", fps=24, preset="explode", words=timed("one"))

    def test_invalid_or_unordered_word_timing_is_rejected(self):
        self.assert_bad(
            "after start", fps=24,
            words=[{"word": "bad", "start_seconds": 1, "end_seconds": 1}],
        )
        self.assert_bad(
            "ordered", fps=24,
            words=[
                {"word": "late", "start_seconds": 2, "end_seconds": 3},
                {"word": "early", "start_seconds": 1, "end_seconds": 1.5},
            ],
        )

    def test_overlapping_or_unreadable_blocks_are_rejected(self):
        self.assert_bad(
            "cannot overlap", fps=24,
            blocks=[
                {"start_seconds": 0, "end_seconds": 2, "lines": ["first"]},
                {"start_seconds": 1.5, "end_seconds": 3, "lines": ["second"]},
            ],
        )
        self.assert_bad(
            "longer than", fps=24, max_chars_per_line=8,
            blocks=[{"start_seconds": 0, "end_seconds": 1, "lines": ["far too long"]}],
        )

    def test_subframe_blocks_that_collide_after_rounding_are_rejected(self):
        self.assert_bad(
            "collide after frame rounding", fps=24,
            blocks=[
                {"start_seconds": 0, "end_seconds": 0.001, "lines": ["a"]},
                {"start_seconds": 0.001, "end_seconds": 0.1, "lines": ["b"]},
            ],
        )

    def test_word_presets_refuse_blocks_without_word_timing(self):
        self.assert_bad(
            "requires word timings", fps=24, preset="karaoke",
            blocks=[{"start_seconds": 0, "end_seconds": 1, "lines": ["No timing"]}],
        )


class AnimatedCaptionCliSurfaceTests(unittest.TestCase):
    def test_presets_are_exposed_without_a_resolve_connection(self):
        result = server.edit_engine("animated_caption_presets", {})
        self.assertTrue(result["success"])
        self.assertEqual(set(result["presets"]), set(animated_captions.PRESETS))
        self.assertFalse(result["native_subtitle_track"])

    def test_explicit_timing_plan_is_exposed_without_a_resolve_connection(self):
        result = server.edit_engine("plan_animated_captions", {
            "fps": "30000/1001",
            "timeline_start_frame": 108000,
            "track_index": 3,
            "preset": "pop",
            "words": timed("Bash captions"),
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["plan"]["target"]["track_index"], 3)
        self.assertEqual(result["plan"]["preset"], "pop")

    def test_invalid_cli_values_return_structured_errors(self):
        result = server.edit_engine("plan_animated_captions", {
            "fps": 24,
            "timeline_start_frame": 0,
            "track_index": "not-a-track",
            "words": timed("Bad input"),
        })
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "ANIMATED_CAPTION_PLAN_INVALID")


if __name__ == "__main__":
    unittest.main()
