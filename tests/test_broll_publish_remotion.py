from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from src import production_cli
from src.utils import broll_pipeline as bp


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base_manifest() -> dict:
    return {
        "fps": 25,
        "width": 1920,
        "height": 1080,
        "timelineDurationSeconds": 8,
        "captions": [{"text": "Киа", "startMs": 0, "endMs": 400, "timestampMs": 0, "confidence": 0.99}],
        "placements": [{"id": "legacy"}],
        "music": None,
    }


def _placements(generated_sha: str, evidence_sha: str) -> dict:
    return {"placements": [
        {
            "id": "native-door",
            "visual_type": "source_cutaway",
            "status": "ready-with-reviewed-source",
            "duration_sec": 1,
            "treatment": {"kind": "source_cutaway"},
        },
        {
            "id": "price-card",
            "visual_type": "motion_graphic",
            "status": "ready-for-motion-graphic",
            "duration_sec": 1.5,
            "on_screen_text_bg": "23 000 EUR",
            "treatment": {"kind": "motion_graphic"},
        },
        {
            "id": "lpg-diagram",
            "visual_type": "diagram",
            "status": "ready-for-motion-graphic",
            "duration_sec": 2,
            "treatment": {"kind": "diagram"},
        },
        {
            "id": "generated-engine",
            "visual_type": "generated_image",
            "status": "ready-with-approved-asset",
            "duration_sec": 2,
            "treatment": {"kind": "generated_illustration"},
            "asset": {
                "src": "generated.png",
                "kind": "image",
                "origin": "generated",
                "exact_item": False,
                "review_status": "approved",
                "sha256": generated_sha,
            },
        },
        {
            "id": "evidence-photo",
            "visual_type": "exact_asset",
            "status": "ready-with-approved-asset",
            "duration_sec": 1,
            "treatment": {"kind": "evidence_image"},
            "asset": {
                "src": "evidence.jpg",
                "kind": "image",
                "origin": "listing_asset",
                "exact_item": True,
                "rights_status": "approved",
                "sha256": evidence_sha,
            },
        },
        {
            "id": "pending-generated",
            "visual_type": "generated_image",
            "status": "needs-generated-asset",
            "duration_sec": 1,
            "treatment": {"kind": "generated_illustration"},
        },
    ]}


class PublishManifestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        assets = self.root / "remotion-assets"
        assets.mkdir()
        (assets / "generated.png").write_bytes(b"generated-image")
        (assets / "evidence.jpg").write_bytes(b"evidence-image")
        self.placements = _placements(_sha(assets / "generated.png"), _sha(assets / "evidence.jpg"))
        self.selection = {"selection_sha256": "selection-digest", "seed": "seed-42"}

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_generated_asset_fails_closed(self):
        with self.assertRaisesRegex(bp.BrollPipelineError, "pending-generated"):
            bp.build_remotion_broll_manifest(
                _base_manifest(), self.placements, self.selection, project_root=self.root
            )

    def test_partial_publish_preserves_captions_and_routes_source_cutaways_native(self):
        base = _base_manifest()
        result = bp.build_remotion_broll_manifest(
            base,
            self.placements,
            self.selection,
            project_root=self.root,
            allow_partial=True,
            artifact_provenance={"selection": {"path": "selection.json", "sha256": "abc"}},
        )
        self.assertEqual(result["captions"], base["captions"])
        self.assertEqual(
            [row["id"] for row in result["placements"]],
            ["price-card", "lpg-diagram", "generated-engine", "evidence-photo"],
        )
        self.assertEqual(result["placements"][0]["duration_seconds"], 1.5)
        self.assertEqual(result["placements"][0]["on_screen_text"], "23 000 EUR")
        publication = result["broll_publication"]
        self.assertEqual(publication["native_source_cutaway_ids"], ["native-door"])
        self.assertEqual(publication["pending_generated_ids"], ["pending-generated"])
        self.assertEqual(publication["selection_sha256"], "selection-digest")
        self.assertFalse(publication["source_media_modified"])
        self.assertEqual(result["provenance"]["broll_publication"]["selection"]["sha256"], "abc")
        self.assertEqual(base["placements"], [{"id": "legacy"}])

    def test_partial_mode_never_allows_generated_exact_item_or_tampering(self):
        placements = _placements(
            _sha(self.root / "remotion-assets" / "generated.png"),
            _sha(self.root / "remotion-assets" / "evidence.jpg"),
        )
        generated = next(row for row in placements["placements"] if row["id"] == "generated-engine")
        generated["asset"]["exact_item"] = True
        with self.assertRaisesRegex(bp.BrollPipelineError, "exact_item=false"):
            bp.build_remotion_broll_manifest(
                _base_manifest(), placements, self.selection, project_root=self.root, allow_partial=True
            )
        generated["asset"]["exact_item"] = False
        (self.root / "remotion-assets" / "generated.png").write_bytes(b"changed")
        with self.assertRaisesRegex(bp.BrollPipelineError, "hash does not match"):
            bp.build_remotion_broll_manifest(
                _base_manifest(), placements, self.selection, project_root=self.root, allow_partial=True
            )


class PublishCliTest(unittest.TestCase):
    def test_default_output_is_separate_and_remotion_accepts_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broll").mkdir()
            (root / "production.json").write_text("{}\n", encoding="utf-8")
            legacy = _base_manifest()
            legacy_text = json.dumps(legacy, ensure_ascii=False) + "\n"
            (root / "remotion.json").write_text(legacy_text, encoding="utf-8")
            (root / "broll" / "selection.json").write_text(
                json.dumps({"selection_sha256": "selection", "seed": 7}) + "\n",
                encoding="utf-8",
            )
            placements = {"placements": [{
                "id": "graphic",
                "visual_type": "motion_graphic",
                "status": "ready-for-motion-graphic",
                "duration_sec": 1,
                "treatment": {"kind": "motion_graphic"},
            }]}
            (root / "broll" / "placements.json").write_text(
                json.dumps(placements) + "\n", encoding="utf-8"
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = production_cli.main([
                    "broll", "publish-remotion", "--project-dir", str(root)
                ])
            self.assertEqual(code, 0, stdout.getvalue())
            published = root / "remotion-broll.json"
            self.assertTrue(published.is_file())
            self.assertEqual((root / "remotion.json").read_text(encoding="utf-8"), legacy_text)
            self.assertEqual(
                json.loads(published.read_text(encoding="utf-8"))["captions"],
                legacy["captions"],
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = production_cli.main([
                    "remotion", "render", "--project-dir", str(root),
                    "--manifest", "remotion-broll.json", "--print-command",
                ])
            response = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(response["manifest"], str(published.resolve()))
            self.assertIn(str(published.resolve()), response["command"])


if __name__ == "__main__":
    unittest.main()
