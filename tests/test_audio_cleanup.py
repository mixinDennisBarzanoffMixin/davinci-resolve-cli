from __future__ import annotations

import unittest

from src.utils import audio_cleanup as cleanup


TIMELINE = {"id": "timeline-123", "name": "A-roll Selects"}


def track(state=None, **overrides):
    payload = {
        "track_index": 1,
        "track_count": 2,
        "available": True,
        "sub_type": "stereo",
        "name": "Audio 1",
        "enabled": True,
        "locked": False,
        "voice_isolation": state or {"isEnabled": False, "amount": 0},
    }
    payload.update(overrides)
    return payload


class AudioCleanupPlanTest(unittest.TestCase):
    def test_presets_are_conservative_and_off_disables(self):
        self.assertEqual(
            cleanup.PRESET_AMOUNTS,
            {"off": 0, "light": 30, "balanced": 50, "strong": 70},
        )
        plan = cleanup.build_track_cleanup_plan(
            timeline_id=TIMELINE["id"], timeline_name=TIMELINE["name"],
            track_index=1, track_state=track(), preset="off",
        )
        self.assertEqual(plan["desired_state"], {"isEnabled": False, "amount": 0})
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["source_media_modified"])

    def test_explicit_amount_overrides_named_preset(self):
        plan = cleanup.build_track_cleanup_plan(
            timeline_id=TIMELINE["id"], timeline_name=TIMELINE["name"],
            track_index=1, track_state=track(), preset="light", amount=61,
        )
        self.assertEqual(plan["preset"], "custom")
        self.assertEqual(plan["desired_state"], {"isEnabled": True, "amount": 61})

    def test_invalid_preset_and_amounts_fail(self):
        with self.assertRaisesRegex(cleanup.AudioCleanupError, "unsupported"):
            cleanup.resolve_amount(preset="enhance-speech")
        for value in (-1, 101, 2.5, True, "50"):
            with self.subTest(value=value), self.assertRaises(cleanup.AudioCleanupError):
                cleanup.resolve_amount(amount=value)  # type: ignore[arg-type]

    def test_plan_requires_exact_unlocked_track_snapshot(self):
        with self.assertRaisesRegex(cleanup.AudioCleanupError, "locked"):
            cleanup.build_track_cleanup_plan(
                timeline_id=TIMELINE["id"], timeline_name=TIMELINE["name"],
                track_index=1, track_state=track(locked=True),
            )
        with self.assertRaisesRegex(cleanup.AudioCleanupError, "boolean enabled"):
            cleanup.build_track_cleanup_plan(
                timeline_id=TIMELINE["id"], timeline_name=TIMELINE["name"],
                track_index=1, track_state=track(enabled=None),
            )


class AudioCleanupApplyTest(unittest.TestCase):
    def setUp(self):
        self.current_state = {"isEnabled": False, "amount": 0}
        self.calls = []
        self.plan = cleanup.build_track_cleanup_plan(
            timeline_id=TIMELINE["id"], timeline_name=TIMELINE["name"],
            track_index=1, track_state=track(self.current_state), preset="balanced",
        )

    def get_timeline(self):
        return dict(TIMELINE)

    def get_track(self, index):
        return track(dict(self.current_state), track_index=index)

    def setter(self, index, state):
        self.calls.append((index, dict(state)))
        self.current_state = dict(state)
        return {"success": True}

    def apply(self, **overrides):
        callbacks = {
            "get_current_timeline": self.get_timeline,
            "get_track_state": self.get_track,
            "set_voice_isolation": self.setter,
            "authorized": True,
        }
        callbacks.update(overrides)
        return cleanup.apply_track_cleanup_plan(self.plan, **callbacks)

    def test_authorization_is_required_before_any_write(self):
        with self.assertRaisesRegex(cleanup.AudioCleanupApplyError, "authorization"):
            cleanup.apply_track_cleanup_plan(
                self.plan,
                get_current_timeline=self.get_timeline,
                get_track_state=self.get_track,
                set_voice_isolation=self.setter,
            )
        self.assertEqual(self.calls, [])

    def test_success_requires_exact_after_readback_and_returns_receipt(self):
        receipt = self.apply()
        self.assertEqual(self.calls, [(1, {"isEnabled": True, "amount": 50})])
        self.assertEqual(receipt["before_state"], {"isEnabled": False, "amount": 0})
        self.assertEqual(receipt["after_state"], {"isEnabled": True, "amount": 50})
        self.assertTrue(receipt["applied"])
        self.assertFalse(receipt["source_media_modified"])
        self.assertEqual(len(receipt["receipt_sha256"]), 64)

    def test_wrong_timeline_and_changed_track_fail_before_write(self):
        with self.assertRaisesRegex(cleanup.AudioCleanupApplyError, "timeline"):
            self.apply(get_current_timeline=lambda: {"id": "other", "name": "A-roll Selects"})
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(cleanup.AudioCleanupApplyError, "state changed"):
            self.apply(get_track_state=lambda index: track(name="Renamed"))
        self.assertEqual(self.calls, [])

    def test_false_setter_with_a_mutation_rolls_back_and_fails(self):
        def false_setter(index, state):
            self.calls.append((index, dict(state)))
            self.current_state = dict(state)
            return {"success": len(self.calls) > 1}

        with self.assertRaisesRegex(cleanup.AudioCleanupApplyError, "setter returned failure"):
            self.apply(set_voice_isolation=false_setter)
        self.assertEqual(self.current_state, {"isEnabled": False, "amount": 0})
        self.assertEqual(len(self.calls), 2)

    def test_readback_mismatch_rolls_back_and_fails(self):
        reads = 0

        def stale_read(index):
            nonlocal reads
            reads += 1
            if reads == 1:
                return track({"isEnabled": False, "amount": 0})
            if reads == 2:
                return track({"isEnabled": True, "amount": 49})
            return track(dict(self.current_state))

        with self.assertRaisesRegex(cleanup.AudioCleanupApplyError, "readback differs"):
            self.apply(get_track_state=stale_read)
        self.assertEqual(self.current_state, {"isEnabled": False, "amount": 0})

    def test_tampered_plan_is_rejected_before_callbacks(self):
        self.plan["desired_state"]["amount"] = 99
        with self.assertRaisesRegex(cleanup.AudioCleanupError, "hash"):
            self.apply()
        self.assertEqual(self.calls, [])

    def test_restore_plan_is_bound_to_receipt_and_reversible(self):
        receipt = self.apply()
        restore = cleanup.build_restore_plan(receipt)
        self.assertEqual(restore["mode"], "restore")
        self.assertEqual(restore["desired_state"], receipt["before_state"])
        self.assertEqual(restore["track_snapshot"]["voice_isolation"], receipt["after_state"])
        restored = cleanup.apply_track_cleanup_plan(
            restore,
            get_current_timeline=self.get_timeline,
            get_track_state=self.get_track,
            set_voice_isolation=self.setter,
            authorized=True,
        )
        self.assertEqual(restored["mode"], "restore")
        self.assertEqual(self.current_state, {"isEnabled": False, "amount": 0})

    def test_tampered_receipt_cannot_build_restore(self):
        receipt = self.apply()
        receipt["before_state"]["amount"] = 22
        with self.assertRaisesRegex(cleanup.AudioCleanupError, "receipt hash"):
            cleanup.build_restore_plan(receipt)


if __name__ == "__main__":
    unittest.main()
