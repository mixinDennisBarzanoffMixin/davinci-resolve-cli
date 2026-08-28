"""Focused contract tests for transition inspection and safe removal."""

import unittest
from unittest import mock

import src.server as s
from src.utils import destructive_hook


class Item:
    def __init__(self, uid, name, start, end, *, transition=False,
                 left_handle=24, right_handle=24, fusion_count=0,
                 property_error=None):
        self.uid = uid
        self.name = name
        self.start = start
        self.end = end
        self.transition = transition
        self.left_handle = left_handle
        self.right_handle = right_handle
        self.fusion_count = fusion_count
        self.property_error = property_error

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetDuration(self):
        return self.end - self.start

    def GetProperty(self, *_args):
        if self.property_error:
            raise self.property_error
        return {} if self.transition else {"Pan": 0.0}

    def GetMediaPoolItem(self):
        return None if self.transition else object()

    def GetFusionCompCount(self):
        return self.fusion_count

    def GetTrackTypeAndIndex(self):
        return ["video", 1]

    def GetLeftOffset(self, _subframe=False):
        return self.left_handle

    def GetRightOffset(self, _subframe=False):
        return self.right_handle


class EmptyPropertyMediaItem(Item):
    def GetProperty(self, *_args):
        return {}

    def GetMediaPoolItem(self):
        return object()


class Timeline:
    def __init__(self, items):
        self.items = list(items)
        self.delete_calls = []

    def GetTrackCount(self, track_type):
        return 1 if track_type == "video" else 0

    def GetItemListInTrack(self, track_type, index):
        return list(self.items) if (track_type, index) == ("video", 1) else []

    def DeleteClips(self, items, ripple):
        self.delete_calls.append((list(items), ripple))
        targets = {id(item) for item in items}
        self.items = [item for item in self.items if id(item) not in targets]
        return True


def edit_with_transition(*, left_handle=24, right_handle=24):
    left = Item("left", "A001", 0, 100, right_handle=left_handle)
    transition = Item("tx-1", "Cross Dissolve", 94, 106, transition=True)
    right = Item("right", "A002", 100, 200, left_handle=right_handle)
    return Timeline([left, transition, right]), left, transition, right


def dispatch(timeline, action, params=None, *, confirm_required=False):
    project = mock.Mock()
    project.GetCurrentTimeline.return_value = timeline
    with mock.patch.object(s, "_check", return_value=(mock.Mock(), project, None)), \
         mock.patch.object(s, "get_resolve", return_value=None), \
         mock.patch.object(s, "_confirm_token_required", return_value=confirm_required):
        return s.timeline(action, params or {})


class TransitionClassifierTest(unittest.TestCase):
    def test_transition_requires_all_three_positive_signals(self):
        transition = Item("tx", "Cross Dissolve", 0, 12, transition=True)
        yes, evidence = s._timeline_transition_evidence(transition)
        self.assertTrue(yes)
        self.assertTrue(evidence["empty_property_map"])
        self.assertFalse(evidence["has_media_pool_item"])
        self.assertEqual(evidence["fusion_comp_count"], 0)

        no, _ = s._timeline_transition_evidence(
            EmptyPropertyMediaItem("clip", "Clip", 0, 12)
        )
        self.assertFalse(no)

        no, _ = s._timeline_transition_evidence(
            Item("title", "Text+", 0, 12, transition=True, fusion_count=1)
        )
        self.assertFalse(no)

    def test_unreadable_property_map_is_never_guessed(self):
        unreadable = Item("x", "Maybe", 0, 12, transition=True,
                          property_error=RuntimeError("bridge lost"))
        yes, evidence = s._timeline_transition_evidence(unreadable)
        self.assertFalse(yes)
        self.assertIn("classification_error", evidence)


class TransitionReportTest(unittest.TestCase):
    def test_report_includes_cut_neighbors_handles_and_boundaries(self):
        timeline, _, _, _ = edit_with_transition(left_handle=3, right_handle=10)
        out = dispatch(timeline, "transition_report", {"track_type": "video"})

        self.assertEqual(out["count"], 1)
        row = out["transitions"][0]
        self.assertEqual(row["id"], "tx-1")
        self.assertEqual(row["name"], "Cross Dissolve")
        self.assertEqual((row["start"], row["end"], row["duration"]), (94, 106, 12))
        self.assertEqual(row["cut"]["frame"], 100)
        self.assertEqual(row["cut"]["left"]["id"], "left")
        self.assertEqual(row["cut"]["right"]["id"], "right")
        self.assertEqual(row["cut"]["left"]["required_outgoing_handle_frames"], 6)
        self.assertEqual(row["cut"]["right"]["required_incoming_handle_frames"], 6)
        self.assertEqual(len(row["cut"]["handle_warnings"]), 1)
        self.assertFalse(out["capabilities"]["create"])
        self.assertFalse(out["capabilities"]["set_duration"])

    def test_list_transitions_is_alias_and_validates_filters(self):
        timeline, _, _, _ = edit_with_transition()
        listed = dispatch(timeline, "list_transitions", {"track_index": 1})
        self.assertEqual(listed["count"], 1)

        bad = dispatch(timeline, "transition_report", {"track_type": "subtitle"})
        self.assertEqual(bad["error"]["code"], "INVALID_TRACK_TYPE")

    def test_unreadable_item_is_skipped_and_reported(self):
        bad = Item("bad", "Unknown", 0, 12, transition=True,
                   property_error=RuntimeError("bridge lost"))
        out = dispatch(Timeline([bad]), "transition_report")
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["summary"]["unreadable_count"], 1)
        self.assertTrue(out["warnings"])


class TransitionDeleteTest(unittest.TestCase):
    def test_delete_requires_confirmation_before_mutating(self):
        timeline, _, _, _ = edit_with_transition()
        out = dispatch(timeline, "delete_transition", {"transition_id": "tx-1"},
                       confirm_required=True)
        self.assertEqual(out["status"], "confirmation_required")
        self.assertEqual(out["preview"]["transition"]["id"], "tx-1")
        self.assertEqual(timeline.delete_calls, [])

    def test_delete_is_non_ripple_and_returns_deleted_transition(self):
        timeline, left, _, right = edit_with_transition()
        out = dispatch(timeline, "delete_transition", {"transition_id": "tx-1"})
        self.assertTrue(out["success"])
        self.assertTrue(out["deleted"])
        self.assertFalse(out["ripple"])
        self.assertEqual(out["transition"]["name"], "Cross Dissolve")
        self.assertEqual(timeline.delete_calls[0][1], False)
        self.assertEqual(timeline.items, [left, right])

    def test_delete_refuses_an_ordinary_clip_even_when_id_exists(self):
        timeline, _, _, _ = edit_with_transition()
        out = dispatch(timeline, "delete_transition", {"transition_id": "left"})
        self.assertEqual(out["error"]["code"], "NOT_A_TRANSITION")
        self.assertEqual(timeline.delete_calls, [])

    def test_delete_action_participates_in_version_and_token_governance(self):
        self.assertIn("delete_transition",
                      destructive_hook.DESTRUCTIVE_ACTIONS_BY_TOOL["timeline"])
        self.assertIn(("timeline", "delete_transition"),
                      s._TOKEN_GATED_DESTRUCTIVE_ACTIONS)


class TransitionDiscoveryTest(unittest.TestCase):
    def test_actions_and_detailed_help_are_discoverable(self):
        index = s._action_help("timeline", {})
        for action in ("transition_report", "list_transitions", "delete_transition"):
            self.assertIn(action, index["actions"])
            self.assertIn(action, index["available"])
            detail = s._action_help("timeline", {"name": action})
            self.assertTrue(detail["success"])
            self.assertIn("example", detail)


if __name__ == "__main__":
    unittest.main()
