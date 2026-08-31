"""Source-safe native Resolve cutaway request planning."""

from __future__ import annotations

import copy
import unittest

from src.utils import source_cutaways as sc


def snapshot():
    return {
        "id": "source-timeline",
        "fps": 60,
        "start_frame": 216000,
        "end_frame": 216600,
        "tracks": {
            "video": {
                "track_count": 1,
                "tracks": [{
                    "track_index": 1,
                    "items": [{
                        "timeline_item_id": "v1-item",
                        "media_pool_item_id": "camera-original",
                        "file_path": "/camera/K8-original.mov",
                        "start": 216000,
                        "end": 216600,
                        "source_start": 1200,
                        "source_end": 1800,
                        "source_fps": 60,
                        "source_media_sha256": "a" * 64,
                    }],
                }],
            },
        },
    }


def event_artifact(data=None):
    data = data or snapshot()
    item = data["tracks"]["video"]["tracks"][0]["items"][0]
    return {
        "schema_version": sc.SOURCE_EVENTS_SCHEMA_VERSION,
        "source_timeline_id": data["id"],
        "source_snapshot_sha256": sc.canonical_sha256(data),
        "events": [{
            "id": "price-sheet",
            "start_seconds": 2,
            "end_seconds": 4,
            "source_track_index": 1,
            "media_pool_item_id": "camera-original",
            "source_item_sha256": sc.source_item_sha256(item),
            "frame_review": {
                "status": "approved",
                "reviewer": "editor",
                "reviewed_at": "2026-08-30T20:00:00Z",
                "frame_sha256s": ["b" * 64],
            },
        }],
    }


def selection(events=None):
    events = events or event_artifact()
    return {
        "schema_version": sc.SOURCE_CUTAWAY_SELECTION_VERSION,
        "source_events_sha256": sc.canonical_sha256(events),
        "placements": [{
            "id": "placement-price",
            "visual_type": "source_cutaway",
            "source_event_id": "price-sheet",
            "start_seconds": 3,
            "source_offset_seconds": 0.25,
            "duration_seconds": 1.5,
        }],
    }


def target(data=None):
    data = data or snapshot()
    return {
        "schema_version": sc.AROLL_TARGET_SCHEMA_VERSION,
        "source_timeline_id": data["id"],
        "source_snapshot_sha256": sc.canonical_sha256(data),
        "target_timeline_id": "a-roll-variant",
        "start_frame": 216000,
        "end_frame": 216480,
        "fps": 60,
        "recoverable": True,
        "source_preserved": True,
    }


class RequestMappingTests(unittest.TestCase):
    def test_maps_source_seconds_to_exact_source_frames_and_absolute_v2_frames(self):
        data = snapshot()
        events = event_artifact(data)
        plan = sc.build_source_cutaway_request(events, selection(events), data, target(data))
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["apply_authorized"])
        self.assertFalse(plan["source_media_modified"])
        self.assertEqual(plan["target_timeline_id"], "a-roll-variant")
        clip = plan["dispatch"]["params"]["clip_infos"][0]
        self.assertEqual(clip, {
            "media_pool_item_id": "camera-original",
            "start_frame": 1335,
            "end_frame": 1425,
            "record_frame": 216180,
            "record_frame_mode": "absolute",
            "track_index": 2,
            "media_type": 1,
        })
        self.assertTrue(plan["approval_gate"]["explicit_apply_required"])

    def test_source_and_timeline_frame_rates_are_kept_separate(self):
        data = snapshot()
        item = data["tracks"]["video"]["tracks"][0]["items"][0]
        item["source_fps"] = 24
        item["source_start"] = 480
        item["source_end"] = 720
        events = event_artifact(data)
        picked = selection(events)
        picked["placements"][0]["source_offset_seconds"] = 0
        picked["placements"][0]["duration_seconds"] = 1
        plan = sc.build_source_cutaway_request(events, picked, data, target(data))
        clip = plan["dispatch"]["params"]["clip_infos"][0]
        self.assertEqual((clip["start_frame"], clip["end_frame"]), (528, 552))
        self.assertEqual((clip["record_frame"], plan["mappings"][0]["target_frames"]["end_exclusive"]), (216180, 216240))

    def test_request_sorts_non_overlapping_placements_by_record_frame(self):
        data = snapshot()
        events = event_artifact(data)
        second = copy.deepcopy(events["events"][0])
        second["id"] = "seats"
        second["start_seconds"] = 5
        second["end_seconds"] = 7
        events["events"].append(second)
        picked = {
            "schema_version": sc.SOURCE_CUTAWAY_SELECTION_VERSION,
            "source_events_sha256": sc.canonical_sha256(events),
            "placements": [
                {"id": "later", "visual_type": "source_cutaway", "source_event_id": "seats", "start_seconds": 5, "duration_seconds": 1},
                {"id": "earlier", "visual_type": "source_cutaway", "source_event_id": "price-sheet", "start_seconds": 1, "duration_seconds": 1},
            ],
        }
        plan = sc.build_source_cutaway_request(events, picked, data, target(data))
        self.assertEqual([row["placement_id"] for row in plan["mappings"]], ["earlier", "later"])


class SafetyGateTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()
        self.events = event_artifact(self.data)
        self.selection = selection(self.events)
        self.target = target(self.data)

    def build(self, track_index=2):
        return sc.build_source_cutaway_request(
            self.events, self.selection, self.data, self.target, track_index=track_index,
        )

    def test_stale_snapshot_is_rejected(self):
        self.data["end_frame"] += 1
        with self.assertRaisesRegex(sc.SourceCutawayError, "snapshot hash"):
            self.build()

    def test_stale_selection_is_rejected(self):
        self.events["events"][0]["end_seconds"] = 4.5
        with self.assertRaisesRegex(sc.SourceCutawayError, "source-events hash"):
            self.build()

    def test_changed_exact_source_item_hash_is_rejected(self):
        self.events["events"][0]["source_item_sha256"] = "f" * 64
        self.selection = selection(self.events)
        with self.assertRaisesRegex(sc.SourceCutawayError, "source item hash"):
            self.build()

    def test_event_requires_frame_review_evidence(self):
        self.events["events"][0]["frame_review"]["status"] = "needs-frame-review"
        self.selection = selection(self.events)
        with self.assertRaisesRegex(sc.SourceCutawayError, "not frame-review approved"):
            self.build()

    def test_exact_media_pool_item_id_is_required(self):
        self.events["events"][0]["media_pool_item_id"] = "other-car"
        self.selection = selection(self.events)
        with self.assertRaisesRegex(sc.SourceCutawayError, "not present on V1"):
            self.build()

    def test_event_may_not_cross_a_v1_edit(self):
        item = self.data["tracks"]["video"]["tracks"][0]["items"][0]
        item["end"] = 216180
        item["source_end"] = 1380
        self.events = event_artifact(self.data)
        self.selection = selection(self.events)
        self.target = target(self.data)
        with self.assertRaisesRegex(sc.SourceCutawayError, "exactly one V1 timeline item"):
            self.build()

    def test_destination_must_be_v2_or_higher(self):
        with self.assertRaisesRegex(sc.SourceCutawayError, "V2 or higher"):
            self.build(track_index=1)

    def test_overlapping_target_placements_are_rejected(self):
        duplicate = copy.deepcopy(self.selection["placements"][0])
        duplicate["id"] = "overlap"
        duplicate["start_seconds"] = 4
        self.selection["placements"].append(duplicate)
        with self.assertRaisesRegex(sc.SourceCutawayError, "overlap"):
            self.build()

    def test_source_selection_may_not_exceed_reviewed_event(self):
        self.selection["placements"][0]["duration_seconds"] = 2
        with self.assertRaisesRegex(sc.SourceCutawayError, "frame-reviewed source event"):
            self.build()

    def test_positive_subframe_overrun_cannot_round_back_inside_reviewed_event(self):
        self.selection["placements"][0]["source_offset_seconds"] = 0
        self.selection["placements"][0]["duration_seconds"] = 2.0000000001
        with self.assertRaisesRegex(sc.SourceCutawayError, "frame-reviewed source event"):
            self.build()

    def test_duration_is_quantized_once_at_reviewed_timeline_fps(self):
        self.selection["placements"][0]["source_offset_seconds"] = 0
        self.selection["placements"][0]["duration_seconds"] = 1.501
        plan = self.build()
        mapping = plan["mappings"][0]
        self.assertEqual(mapping["source_seconds"]["requested_duration"], 1.501)
        self.assertEqual(mapping["source_seconds"]["duration"], 1.5)
        self.assertEqual(mapping["target_seconds"]["duration"], 1.5)
        self.assertEqual(
            mapping["source_frames"]["end_exclusive"] - mapping["source_frames"]["start"],
            90,
        )

    def test_target_timeline_bounds_are_enforced(self):
        self.selection["placements"][0]["start_seconds"] = 7.5
        self.selection["placements"][0]["duration_seconds"] = 1
        with self.assertRaisesRegex(sc.SourceCutawayError, "outside the target timeline"):
            self.build()

    def test_target_must_be_a_recoverable_separate_variant(self):
        self.target["recoverable"] = False
        with self.assertRaisesRegex(sc.SourceCutawayError, "recoverable"):
            self.build()


if __name__ == "__main__":
    unittest.main()
