"""Offline caption QC and conservative repair planning."""

import unittest

from src import server
from src.utils import captions


class CaptionQcTests(unittest.TestCase):
    def test_clean_blocks_pass(self):
        result = captions.audit_blocks([
            {"start_seconds": 0.0, "end_seconds": 2.0, "lines": ["A readable caption."]},
            {"start_seconds": 2.2, "end_seconds": 4.0, "lines": ["And another one."]},
        ])
        self.assertTrue(result["success"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["issue_count"], 0)

    def test_reports_timing_layout_and_reading_problems(self):
        result = captions.audit_blocks([
            {"start_seconds": 0.0, "end_seconds": 0.2, "lines": ["This caption is much too fast for its duration."]},
            {"start_seconds": 0.1, "end_seconds": 9.0, "lines": ["one", "two", "orphan"]},
        ], max_chars_per_line=20)
        codes = {row["code"] for row in result["issues"]}
        self.assertIn("FLASH", codes)
        self.assertIn("READING_SPEED", codes)
        self.assertIn("OVERLAP", codes)
        self.assertIn("LONG_DISPLAY", codes)
        self.assertIn("TOO_MANY_LINES", codes)
        self.assertFalse(result["success"])

    def test_repair_plan_rewraps_and_uses_available_gap(self):
        result = captions.plan_repairs([
            {"start_seconds": 0.0, "end_seconds": 0.2, "lines": ["three short words here"]},
            {"start_seconds": 2.0, "end_seconds": 3.2, "lines": ["second cue"]},
        ], max_chars_per_line=12, min_block_seconds=0.8)
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["changed"])
        self.assertGreaterEqual(result["blocks"][0]["end_seconds"], 0.8)
        self.assertEqual(" ".join(result["blocks"][0]["lines"]), "three short words here")

    def test_impossible_long_token_remains_visible(self):
        result = captions.plan_repairs([
            {"start_seconds": 0.0, "end_seconds": 2.0, "lines": ["supercalifragilisticexpialidocious"]},
        ], max_chars_per_line=12)
        self.assertIn("LINE_TOO_LONG", {row["code"] for row in result["after_qc"]["issues"]})

    def test_repair_uses_free_gap_to_reduce_reading_speed(self):
        result = captions.plan_repairs([
            {"start_seconds": 0.0, "end_seconds": 0.5, "text": "twenty readable chars"},
            {"start_seconds": 3.0, "end_seconds": 4.0, "text": "next"},
        ], max_characters_per_second=10)
        self.assertGreaterEqual(result["blocks"][0]["end_seconds"], 2.0)
        self.assertNotIn(
            "READING_SPEED",
            {row["code"] for row in result["after_qc"]["issues"] if row["cue_index"] == 0},
        )

    def test_invalid_numbers_are_rejected(self):
        with self.assertRaises(captions.CaptionError):
            captions.audit_blocks([
                {"start_seconds": float("nan"), "end_seconds": 1, "text": "bad"},
            ])
        with self.assertRaises(captions.CaptionError):
            captions.audit_blocks([
                {"start_seconds": 0, "end_seconds": 1, "text": "bad profile"},
            ], max_characters_per_second=float("nan"))
        with self.assertRaises(captions.CaptionError):
            captions.plan_repairs([
                {"start_seconds": 0, "end_seconds": 1, "text": "bad profile"},
            ], min_gap_seconds=float("inf"))

    def test_generation_never_truncates_an_unwrappable_word(self):
        token = "supercalifragilisticexpialidocious"
        blocks = captions.build_blocks([
            {"word": token, "start_seconds": 0.0, "end_seconds": 1.0},
        ], max_chars_per_line=12)
        self.assertEqual(blocks[0]["lines"], [token])
        self.assertEqual(" ".join(blocks[0]["lines"]), token)
        self.assertIn(
            "LINE_TOO_LONG",
            {row["code"] for row in captions.audit_blocks(blocks, max_chars_per_line=12)["issues"]},
        )

    def test_qc_duration_is_caption_span_not_absolute_timeline_end(self):
        result = captions.audit_blocks([
            {"start_seconds": 3600, "end_seconds": 3602, "text": "timecoded"},
        ])
        self.assertEqual(result["metrics"]["duration_seconds"], 2.0)
        self.assertEqual(result["metrics"]["latest_end_seconds"], 3602.0)


class CaptionQcCliSurfaceTests(unittest.TestCase):
    def test_explicit_blocks_need_no_resolve_connection(self):
        result = server.edit_engine("caption_qc", {
            "blocks": [
                {"start_seconds": 0, "end_seconds": 2, "text": "Shell-ready captions."},
            ]
        })
        self.assertTrue(result["passed"])

    def test_explicit_words_are_grouped_then_audited(self):
        result = server.edit_engine("caption_qc", {
            "words": [
                {"word": "hello", "start_seconds": 0.0, "end_seconds": 0.4},
                {"word": "world", "start_seconds": 0.5, "end_seconds": 1.0},
            ]
        })
        self.assertEqual(result["cue_count"], 1)

    def test_word_qc_refuses_missing_end_and_unordered_words(self):
        missing = server.edit_engine("caption_qc", {
            "words": [{"word": "hello", "start_seconds": 2.0}],
        })
        self.assertEqual(missing["error"]["code"], "CAPTION_QC_INVALID")
        unordered = server.edit_engine("caption_qc", {
            "words": [
                {"word": "later", "start_seconds": 2.0, "end_seconds": 2.2},
                {"word": "earlier", "start_seconds": 1.0, "end_seconds": 1.2},
            ],
        })
        self.assertEqual(unordered["error"]["code"], "CAPTION_QC_INVALID")

    def test_repair_action_is_dry_run(self):
        result = server.edit_engine("plan_caption_repairs", {
            "blocks": [
                {"start_seconds": 0, "end_seconds": 0.1, "text": "brief caption"},
            ]
        })
        self.assertTrue(result["success"])
        self.assertTrue(result["dry_run"])

    def test_missing_source_is_structured_error(self):
        result = server.edit_engine("caption_qc", {})
        self.assertEqual(result["error"]["code"], "CAPTION_SOURCE_REQUIRED")

    def test_dual_delivery_plan_keeps_sidecar_and_overlays_in_sync(self):
        result = server.edit_engine("plan_caption_delivery", {
            "fps": 24,
            "timeline_start_frame": 86400,
            "format": "vtt",
            "preset": "pop",
            "words": [
                {"word": "dual", "start_seconds": 0.0, "end_seconds": 0.4},
                {"word": "delivery", "start_seconds": 0.45, "end_seconds": 0.9},
            ],
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["output_kind"], "dual-caption-delivery-plan")
        self.assertTrue(result["native_sidecar"]["content"].startswith("WEBVTT"))
        self.assertEqual(
            result["native_sidecar"]["cue_count"],
            result["animated_overlays"]["placement_count"],
        )
        self.assertTrue(result["native_sidecar"]["accessible_caption_artifact"])
        self.assertFalse(result["native_sidecar"]["embedded"])
        self.assertFalse(result["contract"]["source_media_modified"])

    def test_delivery_qc_uses_the_same_custom_profile_as_overlay_planning(self):
        result = server.edit_engine("plan_caption_delivery", {
            "fps": 24,
            "timeline_start_frame": 0,
            "max_chars_per_line": 60,
            "max_lines": 3,
            "words": [
                {
                    "word": "x" * 50,
                    "start_seconds": 0.0,
                    "end_seconds": 3.0,
                },
            ],
        })
        self.assertEqual(result["qc"]["profile"]["max_chars_per_line"], 60)
        self.assertEqual(result["qc"]["profile"]["max_lines"], 3)
        self.assertNotIn(
            "LINE_TOO_LONG", {issue["code"] for issue in result["qc"]["issues"]}
        )

    def test_delivery_rejects_malformed_qc_threshold_as_invalid_input(self):
        result = server.edit_engine("plan_caption_delivery", {
            "fps": 24,
            "timeline_start_frame": 0,
            "max_characters_per_second": "fast",
            "words": [
                {"word": "hello", "start_seconds": 0.0, "end_seconds": 1.0},
            ],
        })
        self.assertEqual(result["error"]["code"], "CAPTION_QC_INVALID")

        duration = server.edit_engine("plan_caption_delivery", {
            "fps": 24,
            "timeline_start_frame": 0,
            "max_block_seconds": "7",
            "words": [
                {"word": "hello", "start_seconds": 0.0, "end_seconds": 1.0},
            ],
        })
        self.assertTrue(duration["success"])
        self.assertEqual(duration["qc"]["profile"]["max_block_seconds"], 7.0)

        nonfinite = server.edit_engine("plan_caption_delivery", {
            "fps": 24,
            "timeline_start_frame": 0,
            "min_block_seconds": "nan",
            "words": [
                {"word": "hello", "start_seconds": 0.0, "end_seconds": 1.0},
            ],
        })
        self.assertEqual(nonfinite["error"]["code"], "CAPTION_QC_INVALID")


if __name__ == "__main__":
    unittest.main()
