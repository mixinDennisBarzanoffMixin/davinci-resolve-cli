"""Chunk-level coverage accounting across native and rendered B-roll."""

from __future__ import annotations

import unittest

from src.utils import broll_coverage as coverage


READY = {"ready-with-reviewed-source", "published", "rendered"}


def chunks(count: int = 15):
    return [
        {
            "id": f"chunk-{index:02d}",
            "start_seconds": index * 2,
            "end_seconds": index * 2 + (0.35 if index == count else 2),
        }
        for index in range(1, count + 1)
    ]


class CoverageReportTests(unittest.TestCase):
    def test_unions_native_and_published_into_fifteen_of_fifteen(self):
        native = [
            {"id": f"native-{index}", "chunk_id": f"chunk-{index:02d}", "status": "ready-with-reviewed-source"}
            for index in range(1, 6)
        ]
        published = [
            {"id": f"render-{index}", "chunk_id": f"chunk-{index:02d}", "status": "rendered"}
            for index in range(6, 16)
        ]
        report = coverage.validate_broll_coverage(
            chunks(), native, {"placements": published}, ready_statuses=READY,
        )
        self.assertTrue(report["success"])
        self.assertEqual(report["coverage"], "15/15")
        self.assertEqual(report["missing_chunk_ids"], [])
        self.assertEqual(report["duplicate_chunk_ids"], [])
        self.assertEqual(report["short_chunks"], [{"chunk_id": "chunk-15", "duration_seconds": 0.35}])

    def test_only_explicit_caller_approved_statuses_count(self):
        kept = [
            {"id": "short", "duration_seconds": 0.2},
            {"id": "normal", "duration_seconds": 3},
        ]
        placements = [
            {"id": "pending", "chunk_id": "short", "status": "pending"},
            {"id": "rejected", "chunk_id": "normal", "status": "rejected"},
            {"id": "unstated", "chunk_id": "normal"},
        ]
        report = coverage.validate_broll_coverage(
            kept, [], placements, ready_statuses={"rendered"},
        )
        self.assertEqual(report["coverage"], "0/2")
        self.assertEqual(report["missing_chunk_ids"], ["short", "normal"])
        self.assertEqual(
            [row["reason"] for row in report["ignored_placements"]],
            ["status-not-ready", "status-not-ready", "missing-ready-status"],
        )
        self.assertEqual(report["short_chunks"][0]["chunk_id"], "short")

    def test_ready_duplicates_are_reported_without_inflating_union(self):
        kept = [{"id": "doors", "duration_seconds": 2}]
        native = [{"id": "source-door", "chunk_id": "doors", "status": "ready"}]
        rendered = [{"id": "graphic-door", "chunk_id": "doors", "status": "ready"}]
        report = coverage.validate_broll_coverage(
            kept, native, rendered, ready_statuses={"ready"},
        )
        self.assertEqual(report["coverage"], "1/1")
        self.assertTrue(report["success"])
        self.assertEqual(report["duplicate_chunk_ids"], ["doors"])
        duplicate = report["duplicate_coverage"][0]
        self.assertEqual(duplicate["chunk_id"], "doors")
        self.assertEqual(duplicate["placement_count"], 2)

        strict = coverage.validate_broll_coverage(
            kept,
            native,
            rendered,
            ready_statuses={"ready"},
            allow_duplicate_coverage=False,
        )
        self.assertFalse(strict["success"])

    def test_fail_closed_exception_carries_the_full_report(self):
        with self.assertRaises(coverage.BrollCoverageError) as caught:
            coverage.validate_broll_coverage(
                [{"id": "price"}, {"id": "seats"}],
                [{"id": "price-shot", "chunk_id": "price", "status": "ready"}],
                [],
                ready_statuses={"ready"},
                fail_closed=True,
            )
        self.assertEqual(caught.exception.report["coverage"], "1/2")
        self.assertEqual(caught.exception.report["missing_chunk_ids"], ["seats"])

    def test_ready_unknown_or_missing_chunk_ids_fail_validation(self):
        report = coverage.validate_broll_coverage(
            [{"id": "known"}],
            [],
            [
                {"id": "unknown", "chunk_id": "other", "status": "ready"},
                {"id": "unmapped", "status": "ready"},
            ],
            ready_statuses={"ready"},
        )
        self.assertFalse(report["success"])
        self.assertEqual(report["unexpected_placements"][0]["chunk_id"], "other")
        self.assertEqual(report["invalid_ready_placements"][0]["placement_id"], "unmapped")


if __name__ == "__main__":
    unittest.main()
