"""Contracts for source-first, multi-agent B-roll ideation."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from src.utils import broll_ideation as bi


def transcript(language, rows):
    return {
        "language": language,
        "words": [
            {"word": word, "start": start, "end": end, "confidence": 0.9}
            for word, start, end in rows
        ],
    }


A1 = transcript("bg", [
    ("Тази", 0.0, 0.3), ("K8", 0.3, 0.6), ("има", 0.6, 0.8),
    ("вентилирани", 0.8, 1.4), ("седалки", 1.4, 1.9),
    ("Цената", 4.0, 4.4), ("е", 4.4, 4.5), ("23000", 4.5, 5.0),
])
A2 = transcript("en", [
    ("camera", 0.1, 0.4), ("shows", 0.4, 0.7), ("the", 0.7, 0.8),
    ("seats", 0.8, 1.2), ("and", 1.2, 1.3), ("steering", 1.3, 1.8),
    ("wheel", 1.8, 2.1), ("he", 3.8, 4.0), ("takes", 4.0, 4.2),
    ("out", 4.2, 4.3), ("the", 4.3, 4.4), ("price", 4.4, 4.8),
    ("sheet", 4.8, 5.2),
])
RESEARCH = {
    "exact_item": {
        "price": {"amount": 23000, "currency": "EUR", "retrieved_at": "2026-08-30T12:00:00Z"},
        "facts": [
            {"id": "seats", "scope": "exact_item", "status": "seller_claim", "confidence": 0.9},
            {"id": "price", "scope": "exact_item", "status": "seller_claim", "confidence": 1.0},
        ],
    },
    "model_or_category_context": {"facts": []},
    "b_roll_beats": [
        {
            "id": "seat-beat", "fact_ids": ["seats"],
            "trigger_words_bg": ["седалки"], "trigger_words_guide": ["seats"],
        },
        {
            "id": "price-beat", "fact_ids": ["price"],
            "trigger_words_bg": ["цената"], "trigger_words_guide": ["price", "sheet"],
        },
    ],
}
CHUNKS = {"chunks": [
    {"id": "seat-moment", "start_seconds": 0, "end_seconds": 2.5, "protected": False},
    {"id": "price-moment", "start_seconds": 3.5, "end_seconds": 5.5, "protected": False},
]}


def generated_candidate(candidate_id="generated", score=0.8, moment_id="seat-moment"):
    return {
        "candidate_id": candidate_id,
        "moment_id": moment_id,
        "concept": "Elegant illustrated ventilated seat airflow",
        "visual_type": "generated_image",
        "story_function": "explain",
        "depiction_scope": "conceptual",
        "duration_seconds": 3,
        "source_event_id": None,
        "prompt": "Editorial illustration of seat airflow, no brand badges",
        "negative_prompt": "No exact vehicle claim, no text, no logo",
        "on_screen_text": "",
        "fact_basis": [{"fact_id": "seats", "basis": "research"}],
        "must_not_show": ["controls from another trim"],
        "risk_flags": [],
        "scores": {
            "a1_relevance": score,
            "a2_visual_relevance": score,
            "evidence_strength": score,
            "visual_clarity": score,
            "pacing_value": score,
            "risk": 0.05,
        },
        "agent_ids": ["agent-a"],
        "lineage": {},
    }


class ContextTests(unittest.TestCase):
    def test_context_preserves_roles_words_facts_and_short_product_token(self):
        context = bi.build_broll_context(A1, A2, RESEARCH, chunks=CHUNKS)
        first = context["moments"][0]
        self.assertEqual(first["a1"]["role"], "recorded_speech")
        self.assertEqual(first["a2"]["role"], "visual_locator_only")
        self.assertIn("K8", first["a1"]["text"])
        self.assertEqual(first["fact_ids"], ["seats"])
        self.assertEqual(context["roles"]["research"], "factual_evidence")

    def test_a2_locates_source_events_but_requires_frame_review(self):
        context = bi.build_broll_context(A1, A2, RESEARCH, chunks=CHUNKS)
        event_types = {row["event_type"] for row in context["source_events"]}
        self.assertIn("seat", event_types)
        self.assertIn("steering-wheel", event_types)
        self.assertIn("price-sheet", event_types)
        self.assertTrue(all(row["verification_status"] == "needs-frame-review" for row in context["source_events"]))

    def test_guide_context_radius_can_cover_a_delayed_visual_description(self):
        delayed_chunks = {"chunks": [{
            "id": "delayed", "start_seconds": 2.3, "end_seconds": 3.2, "protected": False,
        }]}
        context = bi.build_broll_context(
            A1, A2, RESEARCH, chunks=delayed_chunks,
            guide_lookback_seconds=1.7, guide_lookahead_seconds=0,
        )
        self.assertIn("wheel", context["moments"][0]["a2"]["text"])

    def test_context_hash_freshness_detects_changed_research(self):
        context = bi.build_broll_context(A1, A2, RESEARCH, chunks=CHUNKS)
        changed = json.loads(json.dumps(RESEARCH))
        changed["exact_item"]["price"]["amount"] = 22000
        gate = bi.validate_context_freshness(
            context, a1_transcript=A1, a2_transcript=A2, research=changed, chunks=CHUNKS,
        )
        self.assertFalse(gate["success"])
        self.assertEqual(gate["stale"], ["research"])

    def test_volatile_research_age_is_checked(self):
        gate = bi.validate_research_freshness(
            RESEARCH, now=datetime(2026, 9, 2, tzinfo=timezone.utc), volatile_max_age_hours=48,
        )
        self.assertFalse(gate["success"])
        self.assertEqual(gate["stale"][0]["field"], "price")

    def test_a2_only_research_fact_is_excluded_even_if_upstream_called_it_a_fact(self):
        research = json.loads(json.dumps(RESEARCH))
        research["exact_item"]["facts"].append({
            "id": "guide-only",
            "sources": [{
                "source_type": "transcript",
                "url": "file:guide-transcript/transcript.json#t=1,2",
                "locator": "A2 says a price",
            }],
        })
        context = bi.build_broll_context(A1, A2, research, chunks=CHUNKS)
        self.assertNotIn("guide-only", {row["id"] for row in context["facts"]})


class JobManifestTests(unittest.TestCase):
    def test_generated_only_roles_cover_distinct_gap_categories(self):
        roles = bi.GENERATED_ONLY_AGENT_ROLES
        self.assertEqual(len(roles), len(set(roles)))
        self.assertTrue(any("mechanism" in role for role in roles))
        self.assertTrue(any("airflow" in role for role in roles))
        self.assertTrue(any("aftercare" in role for role in roles))
        self.assertTrue(any("feature-system" in role for role in roles))

    def test_jobs_have_independent_reproducible_hmac_seeds_and_content_ids(self):
        context = bi.build_broll_context(A1, A2, RESEARCH, chunks=CHUNKS)
        first = bi.build_ideation_jobs(context, agent_count=4, master_seed="run-seed")
        second = bi.build_ideation_jobs(context, agent_count=4, master_seed="run-seed")
        self.assertEqual(first, second)
        seeds = [row["seed"] for row in first["jobs"]]
        self.assertEqual(len(seeds), len(set(seeds)))
        self.assertTrue(all("A2 is visual_locator_only" in row["instructions"] for row in first["jobs"]))
        self.assertTrue(all("source_cutaway" in row["instructions"] for row in first["jobs"]))


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.context = bi.build_broll_context(A1, A2, RESEARCH, chunks=CHUNKS)

    def test_source_cutaway_candidates_precede_synthetic_fallbacks(self):
        source = bi.build_source_cutaway_candidates(self.context)
        self.assertTrue(source)
        candidate = source[0]
        self.assertEqual(candidate["visual_type"], "source_cutaway")
        self.assertEqual(candidate["depiction_scope"], "exact_item")
        self.assertIn("source-event-needs-frame-review", candidate["risk_flags"])
        selection = bi.select_diverse_candidates(
            [generated_candidate(), candidate], max_candidates=1, quality_floor=0.4,
            diversity=0, seed=9,
        )
        self.assertEqual(selection["selected"][0]["visual_type"], "source_cutaway")

    def test_generated_image_may_never_claim_exact_item(self):
        unsafe = generated_candidate()
        unsafe["depiction_scope"] = "exact_item"
        errors = bi.validate_candidate(unsafe, self.context)
        self.assertTrue(any("must never" in row for row in errors))

    def test_a2_cannot_establish_facts(self):
        unsafe = generated_candidate()
        unsafe["fact_basis"] = [{"fact_id": "seats", "basis": "a2"}]
        errors = bi.validate_candidate(unsafe, self.context)
        self.assertTrue(any("cannot establish facts" in row for row in errors))

    def test_a1_basis_requires_an_a1_transcript_source(self):
        unsafe = generated_candidate()
        unsafe["fact_basis"] = [{"fact_id": "seats", "basis": "a1_transcript_claim"}]
        errors = bi.validate_candidate(unsafe, self.context)
        self.assertTrue(any("no A1 transcript basis" in row for row in errors))

    def test_candidate_run_is_bound_to_exact_context(self):
        run = {
            "schema_version": "dvr.broll-candidate-run.v1",
            "run_id": "run", "job_id": "job", "agent_id": "a", "agent_role": "creative",
            "seed": 1, "context_sha256": "0" * 64, "candidates": [generated_candidate()],
        }
        gate = bi.validate_candidate_run(run, self.context)
        self.assertFalse(gate["success"])
        self.assertTrue(any("stale" in row for row in gate["errors"]))

    def test_invalid_candidate_is_rejected_without_rejecting_valid_run_siblings(self):
        valid = generated_candidate("valid")
        invalid = generated_candidate("invalid")
        invalid["moment_id"] = "missing-moment"
        run = {
            "schema_version": "dvr.broll-candidate-run.v1",
            "run_id": "run", "job_id": "job", "agent_id": "a", "agent_role": "creative",
            "seed": 1, "context_sha256": bi.payload_sha256(self.context),
            "candidates": [invalid, valid],
        }
        gate = bi.validate_candidate_run(run, self.context)
        self.assertTrue(gate["success"])
        self.assertEqual([row["candidate_id"] for row in gate["accepted_candidates"]], ["valid"])
        self.assertEqual(gate["candidate_errors"][0]["candidate_id"], "invalid")
        self.assertEqual(gate["valid_candidate_count"], 1)
        self.assertEqual(gate["rejected_candidate_count"], 1)

    def test_run_schema_mismatch_still_rejects_whole_run(self):
        run = {
            "schema_version": "dvr.broll-candidate-run.v0",
            "run_id": "run", "job_id": "job", "agent_id": "a", "agent_role": "creative",
            "seed": 1, "context_sha256": bi.payload_sha256(self.context),
            "candidates": [generated_candidate("valid")],
        }
        gate = bi.validate_candidate_run(run, self.context)
        self.assertFalse(gate["success"])
        self.assertTrue(any("schema_version" in row for row in gate["errors"]))

    def test_dedup_is_input_order_independent_and_preserves_agent_consensus(self):
        one = generated_candidate("one", 0.7)
        two = generated_candidate("two", 0.9)
        two["agent_ids"] = ["agent-b"]
        left = bi.deduplicate_candidates([one, two])
        right = bi.deduplicate_candidates([two, one])
        self.assertEqual(left, right)
        self.assertEqual(left["candidates"][0]["candidate_id"], "two")
        self.assertEqual(left["candidates"][0]["agent_ids"], ["agent-a", "agent-b"])

    def test_source_cutaway_dedup_uses_moment_and_event_not_agent_wording(self):
        source = bi.build_source_cutaway_candidates(self.context)[0]
        rewrite = json.loads(json.dumps(source))
        rewrite["candidate_id"] = "rewritten-source"
        rewrite["concept"] = "Completely different prose for the same physical shot"
        rewrite["fact_basis"] = []
        rewrite["agent_ids"] = ["agent-rewrite"]
        result = bi.deduplicate_candidates([source, rewrite])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(
            bi.candidate_fingerprint(source),
            bi.candidate_fingerprint(rewrite),
        )

    def test_quality_floor_precedes_randomness(self):
        strong = generated_candidate("strong", 0.9)
        weak = generated_candidate("weak", 0.1, moment_id="price-moment")
        selected = bi.select_diverse_candidates(
            [strong, weak], max_candidates=2, quality_floor=0.5, seed=123,
        )
        self.assertEqual([row["candidate_id"] for row in selected["selected"]], ["strong"])
        self.assertEqual(selected["rejected"][0]["candidate_id"], "weak")

    def test_seeded_diverse_selection_is_reproducible(self):
        rows = [generated_candidate(f"c-{index}", 0.75, moment_id=("seat-moment" if index < 2 else "price-moment")) for index in range(4)]
        rows[1]["concept"] = "Macro detail of cool air flowing through a seat"
        rows[2]["concept"] = "Graphic price sheet with verified euro value"
        rows[2]["fact_basis"] = [{"fact_id": "price", "basis": "research"}]
        rows[3]["concept"] = "Abstract price reduction transition"
        rows[3]["fact_basis"] = [{"fact_id": "price", "basis": "research"}]
        first = bi.select_diverse_candidates(rows, max_candidates=2, seed=888)
        second = bi.select_diverse_candidates(list(reversed(rows)), max_candidates=2, seed=888)
        self.assertEqual(first, second)
        self.assertEqual(len(first["selected"]), 2)

    def test_image_jobs_are_jsonl_safe_reproducible_and_skip_source_cutaways(self):
        generated = generated_candidate()
        source = bi.build_source_cutaway_candidates(self.context)[0]
        selection = bi.select_diverse_candidates(
            [generated, source], max_candidates=2, quality_floor=0.4, diversity=0, seed=4,
        )
        jobs = bi.build_image_jobs(selection, variations=2, master_seed="images")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len({row["seed"] for row in jobs}), 2)
        self.assertTrue(all(0 <= row["seed"] <= (1 << 53) - 1 for row in jobs))
        self.assertTrue(all(row["depiction_scope"] != "exact_item" for row in jobs))
        decoded = [json.loads(line) for line in bi.image_jobs_jsonl(jobs).splitlines()]
        self.assertEqual(decoded, jobs)


if __name__ == "__main__":
    unittest.main()
