"""Behavior tests for the universal shell CLI (no live Resolve required)."""
from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from src import cli


class ParameterParsingTests(unittest.TestCase):
    def test_json_dotted_flags_and_last_value_wins(self):
        params = cli.parse_params(
            ["--vision.summary-style", "concise", "count=3", "--enabled", "count=4"],
            inputs=['{"count":1,"keep":"yes"}'],
            sets=["count=2"],
        )
        self.assertEqual(
            params,
            {
                "count": 4,
                "keep": "yes",
                "vision": {"summary_style": "concise"},
                "enabled": True,
            },
        )

    def test_json_object_positional_and_negative_boolean(self):
        self.assertEqual(
            cli.parse_params(['{"name":"cut","items":[1,2]}', "--no-dry-run"]),
            {"name": "cut", "items": [1, 2], "dry_run": False},
        )


class OutputTests(unittest.TestCase):
    class _Stream:
        def __init__(self, encoding):
            self.encoding = encoding
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)
            if "encoding" in kwargs:
                self.encoding = kwargs["encoding"]

    def test_windows_code_page_is_reconfigured_for_unicode_output(self):
        out, err = self._Stream("cp1252"), self._Stream("cp1252")
        with patch.object(cli.sys, "stdout", out), patch.object(cli.sys, "stderr", err):
            cli._ensure_utf8_stdio()
        for stream in (out, err):
            self.assertEqual(stream.calls, [{"encoding": "utf-8", "errors": "replace"}])

    def test_utf8_stream_is_left_unchanged(self):
        out = self._Stream("utf-8")
        with patch.object(cli.sys, "stdout", out), patch.object(cli.sys, "stderr", out):
            cli._ensure_utf8_stdio()
        self.assertEqual(out.calls, [])

    def _emit(self, value, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.emit(value, output=kwargs.get("output", "json"),
                     pretty=kwargs.get("pretty", False),
                     raw_path=kwargs.get("raw_path"))
        return out.getvalue()

    def test_raw_path_supports_array_indices(self):
        self.assertEqual(
            self._emit({"jobs": [{"id": "job-7"}]}, output="raw", raw_path="jobs.0.id"),
            "job-7\n",
        )

    def test_shell_output_is_quoted_and_flattened(self):
        output = self._emit(
            {"project": {"name": "My Film"}, "items": [1, 2]}, output="shell"
        )
        self.assertIn("PROJECT_NAME='My Film'", output)
        self.assertIn("ITEMS='[1,2]'", output)

    def test_data_only_recursively_removes_operation_metadata(self):
        value = {
            "name": "Timeline 1",
            "_operation": {"status": "success"},
            "nested": [{"value": 1, "_operation": {"status": "success"}}],
        }
        self.assertEqual(
            cli.strip_operation_metadata(value),
            {"name": "Timeline 1", "nested": [{"value": 1}]},
        )


class DispatchTests(unittest.TestCase):
    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_static_tool_call_is_machine_readable(self):
        rc, output, error = self._main(
            ["resolve_control", "api_truth", "query=SaveProject", "--compact"]
        )
        self.assertEqual(rc, cli.EXIT_OK, error)
        self.assertGreaterEqual(json.loads(output)["count"], 1)

    def test_unknown_tool_is_usage_error(self):
        rc, output, error = self._main(["not_a_tool", "get"])
        self.assertEqual(rc, cli.EXIT_USAGE)
        self.assertEqual(output, "")
        self.assertIn("unknown compound tool", error)

    def test_tools_help_does_not_dump_the_catalog(self):
        rc, output, error = self._main(["tools", "--help"])
        self.assertEqual(rc, cli.EXIT_OK, error)
        self.assertIn("prefer `dvr search QUERY`", output)
        self.assertNotIn('"tools"', output)

    def test_data_only_flag_removes_lifecycle_envelope(self):
        mock = AsyncMock(return_value={"name": "Project", "_operation": {"status": "success"}})
        with patch.object(cli, "call_registered_tool", mock):
            rc, output, error = self._main([
                "project_manager", "get_current", "--compact", "--data-only",
            ])
        self.assertEqual(rc, cli.EXIT_OK, error)
        self.assertEqual(json.loads(output), {"name": "Project"})

    def test_search_is_bounded_name_and_action_discovery(self):
        result = cli.search_registry("timeline", "compound")
        self.assertLess(len(json.dumps(result)), 20_000)
        self.assertTrue(any(row["name"] == "timeline" for row in result["matches"]))

    def test_inspect_builds_one_bounded_snapshot(self):
        replies = {
            ("resolve_control", "get_version"): {"product": "Resolve", "version_string": "21.1", "_operation": {}},
            ("resolve_control", "runtime_mode"): {"running": True, "instances": 1, "_operation": {}},
            ("resolve_control", "get_page"): {"page": "edit", "_operation": {}},
            ("project_manager", "get_current"): {"name": "Demo", "id": "p1", "_operation": {}},
            ("timeline", "list"): {"timelines": [{"name": "Main"}], "_operation": {}},
            ("timeline", "probe_timeline_structure"): {
                "name": "Main", "start_frame": 100, "end_frame": 200, "item_count": 2,
                "tracks": {"video": {"track_count": 1, "tracks": [{
                    "track_index": 1, "item_count": 1, "items": [{
                        "name": "shot.mov", "media_pool_item_id": "m1", "media_status": "Online",
                        "file_path": "C:/private/shot.mov",
                    }],
                }]}}, "markers": {}, "_operation": {},
            },
            ("media_pool", "probe_media_pool"): {
                "root": {"name": "Master", "clips": [], "subfolders": []},
                "current_folder": {"name": "Master"}, "selected_clips": [], "_operation": {},
            },
        }

        async def fake_call(_surface, tool, arguments):
            return replies[(tool, arguments["action"])]

        with patch.object(cli, "call_registered_tool", side_effect=fake_call):
            result = asyncio.run(cli.inspect_live_project({"item_limit": 10}))
        self.assertEqual(result["resolve"]["page"], "edit")
        self.assertEqual(result["project"]["name"], "Demo")
        self.assertEqual(result["current_timeline"]["track_item_count"], 2)
        self.assertEqual(result["current_timeline"]["unique_media_count"], 1)
        self.assertEqual(result["resolve"]["ui_mode"], "unknown")
        self.assertNotIn("file_path", result["current_timeline"]["tracks"]["video"]["tracks"][0]["items"][0])
        self.assertNotIn("_operation", json.dumps(result))

    def test_inspect_stops_when_resolve_is_not_running(self):
        mock = AsyncMock(return_value={"running": False, "instances": 0})
        with patch.object(cli, "call_registered_tool", mock):
            result = asyncio.run(cli.inspect_live_project({}))
        self.assertIn("error", result)
        self.assertEqual(mock.await_count, 1)

    def test_tool_error_envelope_is_exit_one(self):
        rc, output, error = self._main(["knowledge", "not_an_action", "--compact"])
        self.assertEqual(rc, cli.EXIT_TOOL_ERROR, error)
        self.assertTrue(json.loads(output).get("error"))

    def test_yes_replays_confirmation_token_once(self):
        first = {"status": "confirmation_required", "confirm_token": "token-1"}
        second = {"success": True, "changed": 1}
        mock = AsyncMock(side_effect=[first, second])
        opts = {
            "surface": "compound",
            "output": "json",
            "pretty": False,
            "raw_path": None,
            "inputs": [],
            "sets": [],
            "yes": True,
        }
        with patch.object(cli, "call_registered_tool", mock):
            result = asyncio.run(cli._dispatch(["timeline", "delete_track", "index=2"], opts))
        self.assertEqual(result, second)
        self.assertEqual(mock.await_count, 2)
        replay = mock.await_args_list[1].args[2]
        self.assertEqual(replay["params"]["confirm_token"], "token-1")
        self.assertEqual(replay["params"]["index"], 2)

    def test_watch_annotations_once_emits_machine_readable_initial_event(self):
        output = io.StringIO()
        reply = {
            "timeline": {"name": "Main"},
            "annotation_count": 1,
            "annotations": [{"id": "timeline:0:intro", "name": "intro"}],
            "_operation": {"execution_id": "volatile"},
        }
        mock = AsyncMock(return_value=reply)

        with patch.object(cli, "call_registered_tool", mock):
            result = asyncio.run(cli.watch_annotations({"once": True}, output))

        self.assertTrue(result["ok"])
        event = json.loads(output.getvalue())
        self.assertEqual(event["event"], "initial")
        self.assertEqual(event["result"]["annotation_count"], 1)
        self.assertNotIn("_operation", event["result"])
        mock.assert_awaited_once_with(
            "compound",
            "timeline_markers",
            {"action": "annotation_feed", "params": {"include_paths": False, "range_mode": "auto"}},
        )


if __name__ == "__main__":
    unittest.main()
