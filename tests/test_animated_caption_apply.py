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

    def GetInput(self, name):
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
    def __init__(self, tool):
        self.comp = Comp(tool)

    def GetFusionCompCount(self):
        return 1

    def GetFusionCompByIndex(self, index):
        return self.comp if index == 1 else None


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
    def __init__(self, name, start=108000, tracks=1, template_ok=True):
        self.name = name
        self.start = start
        self.tracks = tracks
        self.tool = Tool()
        self.template_ok = template_ok
        self.deleted = []

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
        return TitleItem(self.tool) if self.template_ok else None

    def GetMediaPoolItem(self):
        return self

    def DeleteClips(self, items, ripple):
        self.deleted.extend(items)
        return True


class MediaPool:
    def __init__(self):
        self.sources = []
        self.appended = []
        self.fail_append_at = None
        self.deleted_timelines = []

    def CreateEmptyTimeline(self, name):
        source = Timeline(name, start=0)
        self.sources.append(source)
        return source

    def AppendToTimeline(self, infos):
        info = infos[0]
        if self.fail_append_at == len(self.appended):
            return []
        self.appended.append(info)
        return [PlacedItem(info)]

    def DeleteTimelines(self, timelines):
        self.deleted_timelines.extend(timelines)
        return True


class Project:
    def __init__(self, pool):
        self.pool = pool
        self.current = None

    def GetMediaPool(self):
        return self.pool

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

    def test_word_aware_native_effect_is_not_faked(self):
        nested = {
            "placements": [{
                "text": "one two",
                "timeline": {"record_frame": 108000, "duration_frames": 24},
                "animation": {"preset": "word-highlight", "word_cues": [{"text": "one"}]},
            }],
        }
        with self.assertRaisesRegex(AnimatedCaptionApplyError, "word-aware"):
            apply_animated_caption_plan(self.project, self.destination, nested)

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
        self.assertIs(self.project.current, self.destination)


if __name__ == "__main__":
    unittest.main()
