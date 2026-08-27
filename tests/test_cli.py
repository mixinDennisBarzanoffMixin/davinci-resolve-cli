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


if __name__ == "__main__":
    unittest.main()
