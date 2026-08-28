from __future__ import annotations

import unittest

from src.utils.animated_caption_apply import (
    AnimatedCaptionApplyError,
    apply_animated_caption_plan,
)


class Tool:
    def __init__(self):
        self.values = {"StyledText": "", "Size": 0.05, "Opacity1": 1.0}
        self.inputs = {name: AnimInput() for name in ("Size", "Opacity1")}

    def SetInput(self, name, value):
        self.values[name] = value
        return True

    def GetInput(self, name, frame=None):
        if frame is not None and name in self.inputs:
            return self.inputs[name].keyframes.get(frame)
        return self.values.get(name)

    def GetInputList(self):
        return {index: InputInfo(name) for index, name in enumerate(self.inputs, 1)}

    def __getitem__(self, name):
        return self.inputs.get(name)

    def AddModifier(self, name, modifier):
        self.inputs[name].connected = True
        return True


class AnimInput:
    def __init__(self):
        self.connected = False
        self.keyframes = {}

    def __bool__(self):
        return True

    def GetConnectedOutput(self):
        return object() if self.connected else None

    def __setitem__(self, frame, value):
        self.keyframes[frame] = value

    def GetKeyFrames(self):
        return {index: frame for index, frame in enumerate(sorted(self.keyframes), 1)}


class InputInfo:
    def __init__(self, name):
        self.name = name

    def GetAttrs(self):
        return {"INPS_ID": self.name, "INPS_Name": self.name}


class Comp:
    def __init__(self, tool):
        self.tool = tool

    def GetToolList(self, *_args):
        return {1: self.tool}

    def FindTool(self, name):
        return self.tool if name == "Template" else None

    def Lock(self):
        return True

    def Unlock(self):
        return True


class TitleItem:
    def __init__(self, tool, duration=1000):
        self.comp = Comp(tool)
        self.duration = duration

    def GetFusionCompCount(self):
        return 1

    def GetFusionCompByIndex(self, index):
        return self.comp if index == 1 else None

    def GetDuration(self):
        return self.duration


class PlacedItem:
    def __init__(self, info):
        self.info = info

    def GetTrackTypeAndIndex(self):
        return ["video", self.info["trackIndex"]]

    def GetStart(self):
        return self.info["recordFrame"]

    def GetDuration(self):
        return self.info["endFrame"] - self.info["startFrame"]


class Timeline:
    def __init__(self, name, start=108000, tracks=1, template_ok=True, title_duration=1000):
        self.name = name
        self.start = start
        self.tracks = tracks
        self.tool = Tool()
        self.template_ok = template_ok
        self.title_duration = title_duration
        self.deleted = []
        self.existing_items = {}

    def GetStartFrame(self):
        return self.start

    def GetTrackCount(self, kind):
        return self.tracks if kind == "video" else 0

    def AddTrack(self, kind):
        if kind != "video":
            return False
        self.tracks += 1
        return True

    def InsertFusionTitleIntoTimeline(self, name):
        return TitleItem(self.tool, self.title_duration) if self.template_ok else None

    def GetItemListInTrack(self, kind, index):
        return list(self.existing_items.get((kind, index), []))

    def GetMediaPoolItem(self):
        return self

    def DeleteClips(self, items, ripple):
        self.deleted.extend(items)
        return True

    def DeleteTrack(self, kind, index):
        if kind != "video" or index != self.tracks:
            return False
        self.tracks -= 1
        return True


class MediaPool:
    def __init__(self):
        self.sources = []
        self.appended = []
        self.append_targets = []
        self.fail_append_at = None
        self.deleted_timelines = []
        self.delete_timeline_targets = []
        self.project = None

    def CreateEmptyTimeline(self, name):
        source = Timeline(name, start=0)
        self.sources.append(source)
        return source

    def AppendToTimeline(self, infos):
        info = infos[0]
        self.append_targets.append(self.project.current if self.project else None)
        if self.fail_append_at == len(self.appended):
            return []
        self.appended.append(info)
        return [PlacedItem(info)]

    def DeleteTimelines(self, timelines):
        self.delete_timeline_targets.append(self.project.current if self.project else None)
        self.deleted_timelines.extend(timelines)
        return True


class Project:
    def __init__(self, pool):
        self.pool = pool
        self.pool.project = self
        self.current = None

    def GetMediaPool(self):
        return self.pool

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True


def plan(*placements, track=3):
    return {"track_index": track, "placements": list(placements)}


class AnimatedCaptionApplyTests(unittest.TestCase):
    def setUp(self):
        self.pool = MediaPool()
        self.project = Project(self.pool)
        self.destination = Timeline("Main", tracks=1)
        self.project.current = self.destination

    def test_places_distinct_editable_titles_at_exact_relative_frames(self):
        result = apply_animated_caption_plan(
            self.project,
            self.destination,
            plan(
                {"text": "Hello", "start_frame": 12, "end_frame": 36},
                {"text": "world", "start_frame": 48, "duration_frames": 18},
            ),
            template_name="Pop Up",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["caption_count"], 2)
        self.assertFalse(result["native_subtitles"])
        self.assertTrue(result["render_verification_required"])
        self.assertEqual(self.destination.tracks, 3)
        self.assertEqual([row["recordFrame"] for row in self.pool.appended], [108012, 108048])
        self.assertEqual([row["endFrame"] for row in self.pool.appended], [24, 18])
        self.assertEqual([source.tool.values["StyledText"] for source in self.pool.sources], ["Hello", "world"])
        self.assertTrue(all(target is self.destination for target in self.pool.append_targets))
        self.assertIs(self.project.current, self.destination)
        self.assertNotEqual(result["captions"][0]["source_timeline"], result["captions"][1]["source_timeline"])

    def test_absolute_frame_mode_does_not_add_timeline_start(self):
        apply_animated_caption_plan(
            self.project,
            self.destination,
            plan({"text": "A", "start_frame": 108010, "end_frame": 108020}, track=1),
            frame_mode="absolute",
        )
        self.assertEqual(self.pool.appended[0]["recordFrame"], 108010)
        self.assertEqual(self.pool.appended[0]["endFrame"], 10)

    def test_existing_target_track_overlap_is_refused_before_mutation(self):
        self.destination.existing_items[("video", 1)] = [
            ExistingItem("User title", 108005, 108020)
        ]
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "intersects existing"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan({"text": "A", "start_frame": 108010, "end_frame": 108030}, track=1),
                frame_mode="absolute",
            )
        self.assertEqual(self.pool.sources, [])

    def test_per_caption_template_override_is_supported(self):
        result = apply_animated_caption_plan(
            self.project,
            self.destination,
            plan({"text": "A", "start_frame": 0, "end_frame": 10, "template_name": "Bounce"}),
        )
        self.assertEqual(result["captions"][0]["template_name"], "Bounce")

    def test_nested_planner_schema_is_applied_as_absolute_pop_animation(self):
        nested = {
            "target": {"track_index": 2},
            "placements": [{
                "text": "Pop",
                "timeline": {
                    "record_frame": 108024,
                    "end_frame_exclusive": 108054,
                    "duration_frames": 30,
                },
                "title": {"template": "Text+", "inputs": {"StyledText": "Pop"}},
                "animation": {
                    "preset": "pop",
                    "channels": [
                        {"channel": "title.scale", "keyframes": [
                            {"frame": 0, "value": 0.82}, {"frame": 4, "value": 1.0},
                        ]},
                        {"channel": "title.opacity", "keyframes": [
                            {"frame": 0, "value": 0.0}, {"frame": 4, "value": 1.0},
                        ]},
                    ],
                },
            }],
        }
        result = apply_animated_caption_plan(self.project, self.destination, nested)
        self.assertEqual(result["frame_mode"], "absolute")
        self.assertEqual(self.pool.appended[0]["recordFrame"], 108024)
        self.assertEqual(
            [row["channel"] for row in result["captions"][0]["animation"]["applied_channels"]],
            ["title.scale", "title.opacity"],
        )
        tool = self.pool.sources[0].tool
        self.assertEqual(tool.inputs["Size"].keyframes[0], 0.05 * 0.82)
        self.assertEqual(tool.inputs["Opacity1"].keyframes[4], 1.0)

    def test_word_highlight_expands_to_exact_active_word_title_segments(self):
        nested = {
            "placements": [{
                "text": "one two",
                "timeline": {"record_frame": 108000, "duration_frames": 24},
                "animation": {
                    "preset": "word-highlight",
                    "word_cues": [
                        {"text": "one", "start_frame": 0, "end_frame_exclusive": 5},
                        {"text": "two", "start_frame": 8, "end_frame_exclusive": 14},
                    ],
                },
            }],
        }
        result = apply_animated_caption_plan(self.project, self.destination, nested)
        self.assertTrue(result["word_aware_execution"])
        self.assertEqual(result["input_placement_count"], 1)
        self.assertEqual(result["caption_count"], 2)
        self.assertEqual(
            [row["recordFrame"] for row in self.pool.appended],
            [108000, 108008],
        )
        self.assertEqual([row["endFrame"] for row in self.pool.appended], [5, 6])
        self.assertEqual(
            [source.tool.values["StyledText"] for source in self.pool.sources],
            ["one", "two"],
        )
        execution = result["captions"][0]["word_execution"]
        self.assertEqual(execution["strategy"], "one-title-per-spoken-word")
        self.assertTrue(execution["degraded_from_requested_style"])
        self.assertIn("active word", execution["limitation"])

    def test_karaoke_expands_to_cumulative_segments_at_word_starts(self):
        nested = {
            "placements": [{
                "text": "Sing along now",
                "timeline": {"record_frame": 108100, "duration_frames": 30},
                "animation": {
                    "preset": "karaoke",
                    "word_cues": [
                        {"text": "Sing", "start_frame": 2, "end_frame_exclusive": 7},
                        {"text": "along", "start_frame": 9, "end_frame_exclusive": 15},
                        {"text": "now", "start_frame": 18, "end_frame_exclusive": 23},
                    ],
                },
            }],
        }
        result = apply_animated_caption_plan(self.project, self.destination, nested)
        self.assertEqual(result["caption_count"], 3)
        self.assertEqual(
            [row["recordFrame"] for row in self.pool.appended],
            [108102, 108109, 108118],
        )
        self.assertEqual([row["endFrame"] for row in self.pool.appended], [7, 9, 12])
        self.assertEqual(
            [source.tool.values["StyledText"] for source in self.pool.sources],
            ["Sing", "Sing along", "Sing along now"],
        )
        for caption in result["captions"]:
            self.assertEqual(
                caption["word_execution"]["strategy"],
                "cumulative-title-segments",
            )
            self.assertIn("not a continuous", caption["word_execution"]["limitation"])

    def test_word_mode_rejects_missing_or_overlapping_cues_before_mutation(self):
        missing = {
            "placements": [{
                "text": "one",
                "timeline": {"record_frame": 108000, "duration_frames": 24},
                "animation": {"preset": "word-highlight", "word_cues": [{"text": "one"}]},
            }],
        }
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "start_frame"):
            apply_animated_caption_plan(self.project, self.destination, missing)
        self.assertEqual(self.pool.sources, [])

        overlapping = {
            "placements": [{
                "text": "one two",
                "timeline": {"record_frame": 108000, "duration_frames": 24},
                "animation": {
                    "preset": "word-highlight",
                    "word_cues": [
                        {"text": "one", "start_frame": 0, "end_frame_exclusive": 8},
                        {"text": "two", "start_frame": 6, "end_frame_exclusive": 12},
                    ],
                },
            }],
        }
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "word cues overlap"):
            apply_animated_caption_plan(self.project, self.destination, overlapping)
        self.assertEqual(self.pool.sources, [])

    def test_overlap_is_refused_before_any_mutation(self):
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "overlap"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan(
                    {"text": "A", "start_frame": 0, "end_frame": 20},
                    {"text": "B", "start_frame": 10, "end_frame": 30},
                ),
            )
        self.assertEqual(self.pool.sources, [])

    def test_bad_frame_mode_and_empty_text_are_refused(self):
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "frame_mode"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan({"text": "A", "start_frame": 0, "end_frame": 10}),
                frame_mode="timecode",
            )

    def test_fractional_frame_is_refused_instead_of_truncated(self):
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "integer"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan({"text": "A", "start_frame": 1.5, "end_frame": 10}),
            )
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "text"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan({"text": " ", "start_frame": 0, "end_frame": 10}),
            )

    def test_partial_failure_rolls_back_and_restores_destination(self):
        self.pool.fail_append_at = 1
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "rejected caption"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan(
                    {"text": "A", "start_frame": 0, "end_frame": 10},
                    {"text": "B", "start_frame": 20, "end_frame": 30},
                ),
            )
        self.assertEqual(len(self.destination.deleted), 1)
        self.assertEqual(len(self.pool.deleted_timelines), 2)
        self.assertTrue(all(target is self.destination for target in self.pool.delete_timeline_targets))
        self.assertEqual(self.destination.tracks, 1)
        self.assertIs(self.project.current, self.destination)

    def test_failure_before_append_removes_source_and_added_tracks(self):
        self.pool.sources.clear()
        original_create = self.pool.CreateEmptyTimeline

        def create_bad(name):
            source = original_create(name)
            source.template_ok = False
            return source

        self.pool.CreateEmptyTimeline = create_bad
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "could not be inserted"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan({"text": "A", "start_frame": 0, "end_frame": 10}),
                track_index=3,
            )
        self.assertEqual(self.destination.tracks, 1)
        self.assertEqual(len(self.pool.deleted_timelines), 1)
        self.assertIs(self.pool.delete_timeline_targets[0], self.destination)

    def test_short_source_template_is_refused_and_rolled_back(self):
        original_create = self.pool.CreateEmptyTimeline

        def create_short(name):
            source = original_create(name)
            source.title_duration = 5
            return source

        self.pool.CreateEmptyTimeline = create_short
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "shorter than"):
            apply_animated_caption_plan(
                self.project,
                self.destination,
                plan({"text": "A", "start_frame": 0, "end_frame": 10}),
            )
        self.assertEqual(len(self.pool.deleted_timelines), 1)


class ExistingItem:
    def __init__(self, name, start, end):
        self.name = name
        self.start = start
        self.end = end

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end


if __name__ == "__main__":
    unittest.main()
