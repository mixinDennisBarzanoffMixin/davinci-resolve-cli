from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.utils import postroll


def _target() -> dict:
    return {
        "id": "k8-a-roll-v2",
        "fps": 60,
        "width": 1920,
        "height": 1080,
        "start_frame": 216000,
        "end_frame": 225000,
        "tracks": {"video": {"track_count": 1}},
    }


def _probe_payload(*, video: bool = True, width: int = 1920, height: int = 1080, duration: str = "2.0") -> dict:
    streams = []
    if video:
        streams.append({
            "index": 0,
            "codec_type": "video",
            "width": width,
            "height": height,
            "avg_frame_rate": "30/1",
            "duration": duration,
            "nb_frames": "60",
            "disposition": {"default": 1, "attached_pic": 0},
        })
    # An embedded silent/audio stream is intentionally present but ignored by
    # the video-only append contract.
    streams.append({"index": 1, "codec_type": "audio", "duration": duration})
    return {"streams": streams, "format": {"duration": duration}}


class ProbeTests(unittest.TestCase):
    def test_ffprobe_reads_video_metadata_and_hash_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "outro.mov"
            media.write_bytes(b"immutable outro bytes")
            original = media.read_bytes()
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

            result = postroll.probe_outro_media(media, runner=runner)
            self.assertEqual(result["width"], 1920)
            self.assertEqual(result["height"], 1080)
            self.assertEqual(result["duration_seconds"], 2.0)
            self.assertEqual(result["fps"], 30.0)
            self.assertEqual(result["frame_count"], 60)
            self.assertEqual(result["audio_stream_count"], 1)
            self.assertEqual(result["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(media.read_bytes(), original)
            self.assertEqual(calls[0][0][-1], str(media.resolve()))
            self.assertIn("-show_streams", calls[0][0])

    def test_video_stream_dimensions_and_duration_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "outro.mov"
            media.write_bytes(b"outro")

            def runner_for(payload):
                return lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 0, json.dumps(payload), ""
                )

            with self.assertRaisesRegex(postroll.PostrollError, "no video stream"):
                postroll.probe_outro_media(media, runner=runner_for(_probe_payload(video=False)))
            with self.assertRaisesRegex(postroll.PostrollError, "video width"):
                postroll.probe_outro_media(media, runner=runner_for(_probe_payload(width=0)))
            invalid_duration = _probe_payload(duration="N/A")
            invalid_duration["streams"][0]["nb_frames"] = "N/A"
            with self.assertRaisesRegex(postroll.PostrollError, "duration is unavailable"):
                postroll.probe_outro_media(media, runner=runner_for(invalid_duration))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.media = Path(self.temp.name) / "outro.mp4"
        self.media.write_bytes(b"postroll")
        self.asset = {
            "path": str(self.media.resolve()),
            "sha256": hashlib.sha256(b"postroll").hexdigest(),
            "bytes": len(b"postroll"),
            "width": 1920,
            "height": 1080,
            "duration_seconds": 2.0,
            "fps": 30.0,
            "frame_count": 60,
            "audio_stream_count": 1,
            "source_media_modified": False,
        }

    def tearDown(self):
        self.temp.cleanup()

    def build(self, target=None, **kwargs):
        return postroll.build_postroll_plan(
            self.media, target or _target(), probe=lambda path: self.asset, **kwargs
        )

    def test_30fps_asset_maps_to_60fps_timeline_and_ignores_audio(self):
        plan = self.build()
        self.assertEqual(plan["destination_video_track"], 2)
        clip = plan["dispatch"]["append"]["clip_info_template"]
        self.assertEqual(clip, {
            "start_frame": 0,
            "end_frame": 60,
            "record_frame": 225000,
            "record_frame_mode": "absolute",
            "track_index": 2,
            "media_type": 1,
        })
        self.assertEqual(plan["timing"]["record_end_frame_exclusive"], 225120)
        self.assertEqual(plan["timing"]["duration_seconds"], 2.0)
        self.assertFalse(plan["source_media_modified"])

    def test_new_track_is_chosen_above_existing_tracks_and_dimensions_are_checked(self):
        target = _target()
        target["tracks"]["video"]["track_count"] = 3
        self.assertEqual(self.build(target)["destination_video_track"], 4)
        self.asset["width"] = 1280
        with self.assertRaisesRegex(postroll.PostrollError, "do not match"):
            self.build()
        plan = self.build(require_matching_dimensions=False)
        self.assertEqual(plan["asset"]["width"], 1280)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.media = Path(self.temp.name) / "outro.mp4"
        self.media.write_bytes(b"postroll")
        asset = {
            "path": str(self.media.resolve()),
            "sha256": hashlib.sha256(b"postroll").hexdigest(),
            "width": 1920,
            "height": 1080,
            "duration_seconds": 2.0,
            "fps": 30.0,
            "frame_count": 60,
        }
        self.plan = postroll.build_postroll_plan(
            self.media, _target(), probe=lambda path: asset
        )
        self.appended = []

    def tearDown(self):
        self.temp.cleanup()

    def callbacks(self, *, current_id="k8-a-roll-v2", current_end=225000, append_count=1, read_start=225000):
        def append(rows):
            self.appended.extend(rows)
            return {"success": True, "count": append_count, "items": [{"id": "timeline-outro"}]}

        def readback(**query):
            return {
                "timeline_id": "k8-a-roll-v2",
                "end_frame": 225120,
                "count": 1,
                "items": [{
                    "start_frame": read_start,
                    "end_frame": 225120,
                    "track_index": 2,
                    "media_pool_item_id": "outro-media-id",
                }],
            }

        return {
            "get_current_timeline": lambda: {"id": current_id, "end_frame": current_end},
            "import_media": lambda path: {
                "success": True,
                "count": 1,
                "items": [{"media_pool_item_id": "outro-media-id"}],
            },
            "append_to_timeline": append,
            "readback": readback,
        }

    def test_apply_verifies_import_append_and_readback(self):
        result = postroll.apply_postroll_plan(
            self.plan, authorize=True, **self.callbacks()
        )
        self.assertFalse(result["dry_run"])
        self.assertTrue(result["apply_authorized"])
        self.assertEqual(result["media_pool_item_id"], "outro-media-id")
        self.assertEqual(len(self.appended), 1)
        self.assertEqual(self.appended[0]["media_type"], 1)
        self.assertNotIn("audio", self.appended[0])

    def test_stale_target_blocks_before_import(self):
        imported = []
        callbacks = self.callbacks(current_id="other-timeline")
        callbacks["import_media"] = lambda path: imported.append(path) or {}
        with self.assertRaisesRegex(postroll.PostrollError, "differs"):
            postroll.apply_postroll_plan(self.plan, authorize=True, **callbacks)
        self.assertEqual(imported, [])

        with self.assertRaisesRegex(postroll.PostrollError, "end frame changed"):
            postroll.apply_postroll_plan(
                self.plan, authorize=True, **self.callbacks(current_end=225001)
            )

    def test_append_count_and_readback_mismatch_are_rejected(self):
        with self.assertRaisesRegex(postroll.PostrollError, "exactly one timeline item"):
            postroll.apply_postroll_plan(
                self.plan, authorize=True, **self.callbacks(append_count=0)
            )
        with self.assertRaisesRegex(postroll.PostrollError, "does not match"):
            postroll.apply_postroll_plan(
                self.plan, authorize=True, **self.callbacks(read_start=225001)
            )


if __name__ == "__main__":
    unittest.main()
