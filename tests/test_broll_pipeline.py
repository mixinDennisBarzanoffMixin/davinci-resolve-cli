from __future__ import annotations

import unittest

from src.utils import broll_ideation as bi
from src.utils import broll_pipeline as bp


def _transcript(language, words):
    return {
        "language": language,
        "words": [
            {"word": word, "start": start, "end": end, "confidence": 0.95}
            for word, start, end in words
        ],
    }


A1 = _transcript("bg", [("врата", 0.0, 0.4), ("цена", 2.0, 2.4)])
A2 = _transcript("en", [("door", 0.0, 0.4), ("price", 2.0, 2.4), ("sheet", 2.4, 2.8)])
RESEARCH = {
    "exact_item": {
        "facts": [{"id": "door-fact"}, {"id": "price-fact"}],
        "price": {"amount": 1, "currency": "EUR", "retrieved_at": "2026-08-30T00:00:00Z"},
    },
    "model_or_category_context": {"facts": []},
    "b_roll_beats": [
        {"fact_ids": ["door-fact"], "trigger_words_bg": ["врата"], "trigger_words_guide": ["door"]},
        {"fact_ids": ["price-fact"], "trigger_words_bg": ["цена"], "trigger_words_guide": ["price"]},
    ],
}
CHUNKS = {"chunks": [
    {"id": "doors", "start_seconds": 0.0, "end_seconds": 1.0, "protected": False},
    {"id": "price", "start_seconds": 2.0, "end_seconds": 3.0, "protected": False},
]}


def _review_payload():
    return {
        "review_status": "frame_verified",
        "events": [{
            "event_id": "open-door",
            "start_seconds": 0.0,
            "end_seconds": 1.0,
            "review_frame": "/tmp/door.jpg",
            "visible": "The exact car door opens",
            "use_for": ["door"],
            "status": "approved-source-candidate",
        }],
    }


def _context():
    return bi.build_broll_context(
        A1,
        A2,
        RESEARCH,
        chunks=CHUNKS,
        source_events=bp.normalize_reviewed_source_events(_review_payload()),
    )


def _generated(context):
    return {
        "schema_version": "dvr.broll-candidate-run.v1",
        "run_id": "run",
        "job_id": "agent-001",
        "agent_id": "agent-001",
        "agent_role": "diagram",
        "seed": 12,
        "context_sha256": bi.payload_sha256(context),
        "candidates": [{
            "candidate_id": "generated-one",
            "moment_id": "price",
            "concept": "Editorial conceptual price transition",
            "visual_type": "generated_image",
            "story_function": "explain",
            "depiction_scope": "conceptual",
            "duration_seconds": 1.0,
            "source_event_id": None,
            "prompt": "Editorial conceptual pricing background, no text",
            "negative_prompt": "No exact car, no logo, no text",
            "on_screen_text": "1 EUR",
            "fact_basis": [{"fact_id": "price-fact", "basis": "research"}],
            "must_not_show": ["exact item"],
            "risk_flags": [],
            "scores": {
                "a1_relevance": 0.9,
                "a2_visual_relevance": 0.7,
                "evidence_strength": 0.9,
                "visual_clarity": 0.8,
                "pacing_value": 0.7,
                "risk": 0.05,
            },
            "agent_ids": ["agent-001"],
            "lineage": {},
        }],
    }


class SourceEventTest(unittest.TestCase):
    def test_only_reviewed_events_are_adapted(self):
        events = bp.normalize_reviewed_source_events(_review_payload())
        self.assertEqual(events[0]["verification_status"], "frame-reviewed")
        bad = _review_payload()
        bad["review_status"] = "pending"
        with self.assertRaises(bp.BrollPipelineError):
            bp.normalize_reviewed_source_events(bad)


class AgentPromptTest(unittest.TestCase):
    def test_prompt_preserves_role_and_exact_context_hash(self):
        context = _context()
        prompt = bp.build_agent_prompt(context)
        self.assertIn("A2 is only a visual locator", prompt)
        self.assertIn(bi.payload_sha256(context), prompt)
        self.assertIn("never exact_item", prompt)

    def test_generated_only_prompt_forbids_synthetic_replacements_for_visible_subjects(self):
        prompt = bp.build_agent_prompt(
            _context(),
            candidates_per_agent=2,
            generated_only=True,
            visual_types=["generated_image"],
        )
        self.assertIn("GENERATED-ONLY GAP PASS", prompt)
        self.assertIn("generated_image, diagram, or motion_graphic", prompt)
        self.assertIn("Do not return source_cutaway or exact_asset", prompt)
        self.assertIn("Editorial illustration — not the exact vehicle", prompt)
        self.assertIn("source-footage-cannot-show-concept", prompt)
        self.assertIn("Honor the appended creative_lens", prompt)
        self.assertIn("VISUAL TYPE FILTER", prompt)
        self.assertIn("must be one of: generated_image", prompt)
        self.assertIn("basis research for listing, seller, and manufacturer facts", prompt)
        self.assertIn("Return exactly 2 distinct safe candidates", prompt)
        self.assertIn("price sheet), do not propose a generated substitute", prompt)

    def test_visual_type_filter_accepts_repeated_and_comma_values_and_rejects_unknowns(self):
        self.assertEqual(
            bp.normalize_visual_types(["generated_image,diagram", "generated_image"]),
            ("generated_image", "diagram"),
        )
        with self.assertRaises(bp.BrollPipelineError):
            bp.normalize_visual_types(["generated_illustration"])
        with self.assertRaises(bp.BrollPipelineError):
            bp.normalize_visual_types(["source_cutaway"], generated_only=True)

    def test_codex_schema_is_strict_and_typed(self):
        schema = bp.candidate_output_schema()
        self.assertEqual(schema["properties"]["schema_version"]["type"], "string")
        candidate = schema["properties"]["candidates"]["items"]
        self.assertEqual(set(candidate["required"]), set(candidate["properties"]))
        self.assertIn("null", candidate["properties"]["source_event_id"]["type"])
        self.assertNotIn("allOf", candidate)


class SynthesisTest(unittest.TestCase):
    def test_source_and_valid_generated_candidates_survive(self):
        context = _context()
        result = bp.synthesize_selection(
            context,
            {"candidates": [_generated(context)]},
            max_candidates=2,
            quality_floor=0.3,
            diversity=0,
            seed=99,
        )
        self.assertEqual(result["validation"]["accepted_agent_runs"][0]["agent_id"], "agent-001")
        self.assertEqual({row["visual_type"] for row in result["selected"]}, {"source_cutaway", "generated_image"})
        jobs = bp.image_job_manifest(result, variations=2, seed="images")
        self.assertEqual(jobs["job_count"], 2)
        self.assertTrue(all(0 <= row["seed"] <= (1 << 53) - 1 for row in jobs["jobs"]))
        self.assertTrue(all(row["depiction_scope"] != "exact_item" for row in jobs["jobs"]))

    def test_stale_agent_output_is_rejected_but_source_candidate_remains(self):
        context = _context()
        stale = _generated(context)
        stale["context_sha256"] = "0" * 64
        result = bp.synthesize_selection(
            context,
            {"candidates": [stale]},
            max_candidates=2,
            quality_floor=0.3,
            seed=9,
        )
        self.assertTrue(result["validation"]["rejected_agent_runs"])
        self.assertEqual([row["visual_type"] for row in result["selected"]], ["source_cutaway"])

    def test_one_invalid_candidate_does_not_discard_valid_siblings(self):
        context = _context()
        mixed = _generated(context)
        invalid = dict(mixed["candidates"][0])
        invalid["candidate_id"] = "bad-moment"
        invalid["moment_id"] = "missing-moment"
        mixed["candidates"].insert(0, invalid)
        result = bp.synthesize_selection(
            context,
            {"candidates": [mixed]},
            max_candidates=2,
            quality_floor=0.3,
            diversity=0,
            seed=99,
        )
        self.assertFalse(result["validation"]["rejected_agent_runs"])
        accepted = result["validation"]["accepted_agent_runs"][0]
        self.assertEqual(accepted["valid_candidate_count"], 1)
        self.assertEqual(accepted["rejected_candidate_count"], 1)
        rejected = result["validation"]["rejected_agent_candidates"][0]
        self.assertEqual(rejected["candidate_id"], "bad-moment")
        self.assertIn("generated-one", [row["candidate_id"] for row in result["selected"]])

    def test_placements_include_reviewed_source_trim_and_generated_disclosure(self):
        context = _context()
        selection = bp.synthesize_selection(
            context,
            {"candidates": [_generated(context)]},
            max_candidates=2,
            quality_floor=0.3,
            diversity=0,
            seed=99,
        )
        placements = bp.selection_placements(context, selection)
        source = next(row for row in placements if row["visual_type"] == "source_cutaway")
        generated = next(row for row in placements if row["visual_type"] == "generated_image")
        self.assertEqual(source["source_event"]["verification_status"], "frame-reviewed")
        self.assertEqual(generated["treatment"]["disclosure"], "Illustrative visualization")
        self.assertLessEqual(generated["treatment"]["seed"], (1 << 53) - 1)

    def test_multiple_candidates_in_one_moment_are_scheduled_without_overlap(self):
        context = _context()
        first = dict(_generated(context)["candidates"][0])
        first["duration_seconds"] = 0.4
        second = dict(first)
        second["candidate_id"] = "generated-two"
        second["concept"] = "Second conceptual pricing treatment"
        placements = bp.selection_placements(
            context,
            {"seed": "schedule", "selected": [first, second]},
        )
        self.assertEqual(
            [(row["start_seconds"], row["end_seconds"], row["chunk_offset_seconds"]) for row in placements],
            [(2.0, 2.4, 0.0), (2.4, 2.8, 0.4)],
        )

        variant = {"params": {"ranges": [{
            "chunk_id": "price", "track_type": "video", "track_index": 1,
            "start_frame": 120, "end_frame": 180, "record_frame": 1060,
        }]}}
        mapped = bp.remap_placements_to_variant(
            placements,
            variant,
            {"fps": 60, "start_frame": 1000},
        )
        self.assertEqual([row["start_seconds"] for row in mapped], [1.0, 1.4])

    def test_placements_remap_to_compacted_variant_chunk_start(self):
        context = _context()
        selection = bp.synthesize_selection(
            context,
            {"candidates": [_generated(context)]},
            max_candidates=2,
            quality_floor=0.3,
            diversity=0,
            seed=99,
        )
        placements = bp.selection_placements(context, selection)
        variant = {"params": {"ranges": [
            {"chunk_id": "doors", "track_type": "video", "track_index": 1, "start_frame": 0, "end_frame": 60, "record_frame": 1000},
            {"chunk_id": "price", "track_type": "video", "track_index": 1, "start_frame": 120, "end_frame": 180, "record_frame": 1060},
        ]}}
        mapped = bp.remap_placements_to_variant(
            placements,
            variant,
            {"fps": 60, "start_frame": 1000},
        )
        starts = {row["chunk_id"]: row["start_seconds"] for row in mapped}
        self.assertEqual(starts, {"doors": 0.0, "price": 1.0})
        self.assertTrue(all(row["timing_space"] == "compacted_a_roll_variant" for row in mapped))


if __name__ == "__main__":
    unittest.main()
