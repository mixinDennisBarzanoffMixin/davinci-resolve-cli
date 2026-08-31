from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.utils import broll_assets as assets


def _job():
    return {
        "schema_version": "dvr.broll-image-job.v1",
        "job_id": "image-1",
        "candidate_id": "generated-1",
        "prompt": "Conceptual editorial illustration, no text",
        "negative_prompt": "No logo",
        "depiction_scope": "conceptual",
        "must_not_show": ["exact car"],
    }


class AssetGateTest(unittest.TestCase):
    def test_record_review_and_attach(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "broll" / "generated" / "image.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-render-but-enough-for-contract")
            recorded = assets.record_generated_asset(
                project_dir=root,
                image_job=_job(),
                asset_path=image,
                provider="test-provider",
            )
            self.assertEqual(recorded["review"]["status"], "needs_visual_review")
            with self.assertRaises(assets.BrollAssetError):
                assets.attach_asset_to_placements([], recorded)
            approved = assets.review_generated_asset(
                recorded,
                project_dir=root,
                approve=True,
                reviewer="editor",
            )
            approved = assets.stage_approved_asset_for_remotion(approved, project_dir=root)
            placements = [{
                "id": "generated-1",
                "visual_type": "generated_image",
                "status": "needs-generated-asset",
            }]
            attached = assets.attach_asset_to_placements(placements, approved)
            self.assertEqual(attached[0]["asset"]["origin"], "generated")
            self.assertFalse(attached[0]["asset"]["exact_item"])
            self.assertFalse(Path(attached[0]["asset"]["src"]).is_absolute())
            self.assertTrue((root / "remotion-assets" / attached[0]["asset"]["src"]).is_file())

    def test_outside_project_and_exact_item_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.png"
            outside.write_bytes(b"png")
            with self.assertRaises(assets.BrollAssetError):
                assets.record_generated_asset(
                    project_dir=root,
                    image_job=_job(),
                    asset_path=outside,
                    provider="test",
                )
            job = _job()
            job["depiction_scope"] = "exact_item"
            inside = root / "broll" / "generated" / "inside.png"
            inside.parent.mkdir(parents=True)
            inside.write_bytes(b"png")
            with self.assertRaises(assets.BrollAssetError):
                assets.record_generated_asset(
                    project_dir=root,
                    image_job=job,
                    asset_path=inside,
                    provider="test",
                )

    def test_hash_change_after_recording_blocks_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "broll" / "generated" / "image.webp"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"first")
            recorded = assets.record_generated_asset(
                project_dir=root,
                image_job=_job(),
                asset_path=image,
                provider="test",
            )
            image.write_bytes(b"changed")
            with self.assertRaises(assets.BrollAssetError):
                assets.review_generated_asset(
                    recorded,
                    project_dir=root,
                    approve=True,
                    reviewer="editor",
                )


if __name__ == "__main__":
    unittest.main()
