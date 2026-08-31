from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import urllib.parse
import unittest
from unittest import mock

from src import production_cli
from src.utils import music_discovery as music


def _track(track_id: str = "track-1", **overrides):
    value = {
        "id": track_id,
        "title": "Elegant Drive Instrumental",
        "creator": "Example Artist",
        "creator_url": "https://artist.example/profile",
        "duration": 157000,
        "category": "music",
        "url": f"https://media.example/{track_id}.mp3",
        "foreign_landing_url": f"https://source.example/{track_id}",
        "license": "by",
        "license_version": "4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Elegant Drive Instrumental by Example Artist (CC BY 4.0)",
        "genres": ["cinematic", "electronic"],
        "tags": [{"name": "confident"}, {"name": "instrumental"}],
        "provider": "example-provider",
        "source": "example-source",
        "foreign_identifier": f"foreign-{track_id}",
        "last_synced_with_source": "2026-08-29T00:00:00Z",
        "mature": False,
        "filetype": "mp3",
        "filesize": 123456,
        "bit_rate": 192000,
        "sample_rate": 44100,
    }
    value.update(overrides)
    return value


class SearchQueryTests(unittest.TestCase):
    def test_query_builder_is_diverse_bounded_and_keeps_fallback(self):
        queries = music.build_search_queries(
            categories=["car promo,product demo"],
            moods=["elegant", "confident", "modern"],
            genres=["electronic", "cinematic"],
            instruments=["synth", "percussion"],
            keywords=["luxury", "technology"],
            energy="medium",
        )
        self.assertLessEqual(len(queries), 8)
        self.assertEqual(queries[-1], "cinematic instrumental")
        self.assertIn("elegant instrumental", queries)
        self.assertIn("electronic instrumental", queries)
        self.assertEqual(len(queries), len({query.casefold() for query in queries}))

    def test_default_license_profile_excludes_noncommercial_and_sharealike(self):
        self.assertEqual(music.resolve_licenses("commercial-safe"), ["cc0", "pdm", "by"])
        with self.assertRaises(music.MusicDiscoveryError):
            music.resolve_licenses("commercial-safe", ["made-up-license"])


class OpenverseNormalizationTests(unittest.TestCase):
    def test_search_preserves_attribution_provenance_and_never_claims_download(self):
        requested_urls = []

        def fetch(url, *, timeout):
            requested_urls.append((url, timeout))
            return {"result_count": 1, "results": [_track()]}

        result = music.search_openverse(
            moods=["elegant", "confident"],
            genres=["cinematic"],
            energy="medium",
            target_duration=157.4,
            seed="repeatable",
            fetch_json=fetch,
        )
        self.assertEqual(result["option_count"], 1)
        self.assertIn("not endorsed", result["provider_notice"])
        option = result["options"][0]
        self.assertEqual(option["duration_seconds"], 157.0)
        self.assertEqual(option["license"]["code"], "by")
        self.assertEqual(
            option["license"]["attribution"],
            "Elegant Drive Instrumental by Example Artist (CC BY 4.0)",
        )
        self.assertEqual(option["provenance"]["foreign_identifier"], "foreign-track-1")
        self.assertEqual(option["instrumental_signal"], "metadata_match")
        self.assertIn("url", option["audio_file"])
        self.assertNotIn("download_url", option)
        self.assertGreater(len(option["discovered_by_queries"]), 1)
        self.assertFalse(result["review"]["automatic_download"])
        self.assertFalse(result["review"]["automatic_import"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requested_urls[0][0]).query)
        self.assertEqual(query["category"], ["music"])
        self.assertEqual(query["license"], ["cc0,pdm,by"])

    def test_bounds_reject_unknown_duration_and_obvious_vocals(self):
        def fetch(_url, *, timeout):
            return {
                "result_count": 3,
                "results": [
                    _track("unknown", duration=None),
                    _track("vocal", title="Vocal Song", tags=[{"name": "lyrics"}]),
                    _track("valid", duration=120000),
                ],
            }

        result = music.search_openverse(
            min_duration=90,
            max_duration=180,
            seed="fixed",
            fetch_json=fetch,
        )
        self.assertEqual([row["track_id"] for row in result["options"]], ["valid"])

    def test_fixed_seed_produces_repeatable_ranking(self):
        def fetch(_url, *, timeout):
            return {"result_count": 2, "results": [_track("a"), _track("b")]}

        first = music.search_openverse(seed="same", fetch_json=fetch)
        second = music.search_openverse(seed="same", fetch_json=fetch)
        self.assertEqual(
            [row["track_id"] for row in first["options"]],
            [row["track_id"] for row in second["options"]],
        )

    def test_invalid_numeric_constraints_fail_before_network(self):
        with self.assertRaisesRegex(music.MusicDiscoveryError, "finite non-negative"):
            music.search_openverse(target_duration=float("nan"), fetch_json=lambda *_a, **_k: {})
        with self.assertRaisesRegex(music.MusicDiscoveryError, "min_duration cannot exceed"):
            music.search_openverse(min_duration=20, max_duration=10, fetch_json=lambda *_a, **_k: {})
        with self.assertRaisesRegex(music.MusicDiscoveryError, "anonymous Openverse"):
            music.search_openverse(per_query=21, fetch_json=lambda *_a, **_k: {})


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = music.search_openverse(
            seed="selection",
            fetch_json=lambda _url, *, timeout: {"result_count": 1, "results": [_track()]},
        )

    def test_shortlist_keeps_license_unverified_and_does_not_fetch(self):
        selected = music.select_option(self.manifest, "track-1")
        self.assertEqual(selected["status"], "needs_license_verification")
        self.assertFalse(selected["license_review"]["confirmed_at_source"])
        self.assertIn("does not download or import", selected["next_step"])
        self.assertEqual(len(selected["options_manifest_sha256"]), 64)

    def test_confirmation_requires_exact_source_license_and_reviewer(self):
        with self.assertRaisesRegex(music.MusicDiscoveryError, "reviewer"):
            music.select_option(self.manifest, "track-1", confirm_source_license=True)
        with self.assertRaisesRegex(music.MusicDiscoveryError, "source page"):
            music.select_option(
                self.manifest,
                "track-1",
                confirm_source_license=True,
                reviewer="editor",
                verified_source_page="https://wrong.example",
                verified_license_code="by",
            )
        selected = music.select_option(
            self.manifest,
            "track-1",
            confirm_source_license=True,
            reviewer="editor",
            verified_source_page="https://source.example/track-1",
            verified_license_code="BY",
            verification_notes="License and creator checked on the source page.",
        )
        self.assertEqual(selected["status"], "license_verified_for_reviewed_use")
        self.assertEqual(selected["license_review"]["verified_license_code"], "by")


class ProductionCliParserTests(unittest.TestCase):
    def test_nested_music_commands_parse(self):
        search = production_cli._parser().parse_args([
            "music", "search", "--project-dir", "/tmp/run", "--mood", "elegant,confident",
        ])
        self.assertIs(search.handler, production_cli._cmd_music_search)
        self.assertEqual(search.mood, ["elegant,confident"])
        select = production_cli._parser().parse_args([
            "music", "select", "--project-dir", "/tmp/run", "--track-id", "abc",
        ])
        self.assertIs(select.handler, production_cli._cmd_music_select)

    def test_search_writes_manifest_and_stdout_is_one_json_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "production.json").write_text(
                json.dumps({"subject": "Example car", "listing_url": "https://listing.example/car"}),
                encoding="utf-8",
            )
            (root / "timeline.json").write_text(
                json.dumps({"duration_seconds": 123.5}), encoding="utf-8",
            )
            result = {
                "schema_version": music.SCHEMA_VERSION,
                "option_count": 0,
                "options": [],
                "review": {"automatic_download": False, "automatic_import": False},
            }
            output = io.StringIO()
            with mock.patch.object(music, "search_openverse", return_value=result) as search:
                with redirect_stdout(output):
                    code = production_cli.main([
                        "music", "search", "--project-dir", str(root), "--mood", "calm",
                    ])
            self.assertEqual(code, 0)
            emitted = json.loads(output.getvalue())
            persisted = json.loads((root / "music-options.json").read_text(encoding="utf-8"))
            self.assertEqual(emitted, persisted)
            self.assertEqual(emitted["production_context"]["timeline_duration_seconds"], 123.5)
            self.assertEqual(search.call_args.kwargs["target_duration"], 123.5)
            self.assertEqual(output.getvalue().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
