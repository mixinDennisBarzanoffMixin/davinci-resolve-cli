"""Focused offline tests for action discovery, completion, and JSONL sessions."""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ActionDescribeTests(unittest.TestCase):
    def test_spaced_alternative_parameter_names_are_preserved(self):
        from src.cli import _documented_parameters

        rows = _documented_parameters("timeline_start_frame | item_name, kind?")
        self.assertEqual([row["name"] for row in rows], ["timeline_start_frame", "item_name", "kind"])
        self.assertFalse(rows[0]["required"])
        self.assertEqual(rows[0]["alternative_group"], ["timeline_start_frame", "item_name"])

    def test_registered_action_help_is_exposed_without_resolve(self):
        from src.cli import _dispatch, _global_options

        rest, opts = _global_options(["describe", "timeline", "get_items"])
        result = asyncio.run(_dispatch(rest, opts))
        action = result["action"]
        self.assertEqual(action["name"], "get_items")
        self.assertEqual(action["source"], "registered_action_help")
        self.assertIn("track_type", {row["name"] for row in action["parameters"]})
        self.assertTrue(action["input_schema"]["additionalProperties"])
        self.assertIn("example", action)

    def test_docstring_fallback_is_explicitly_open_not_fake_strict_schema(self):
        from src.cli import build_registry, describe_action

        result = describe_action(build_registry()["timeline"], "compound", "get_current")
        self.assertEqual(result["source"], "tool_docstring")
        self.assertEqual(result["signature"], "get_current()")
        self.assertEqual(result["input_schema"]["x-dvr-schema-confidence"], "documented_open_object")

    def test_catalog_only_fallback_says_parameter_contract_is_unknown(self):
        from src.cli import build_registry, describe_action

        result = describe_action(
            build_registry()["render"], "compound", "list_loudness_standards"
        )
        self.assertEqual(result["source"], "action_catalog")
        self.assertFalse(result["documented"])
        self.assertEqual(result["input_schema"]["x-dvr-schema-confidence"], "unknown")
        self.assertIn("no action-specific", result["note"])


class DynamicCompletionTests(unittest.TestCase):
    def test_registry_tools_actions_and_documented_flags_complete(self):
        from src.cli import completion_candidates

        self.assertIn("timeline", completion_candidates(["time"]))
        self.assertIn("get_items", completion_candidates(["timeline", "get_"]))
        flags = completion_candidates(["timeline", "get_items", "--tr"])
        self.assertEqual(flags, ["--track-index", "--track-type"])

    def test_common_enum_values_complete(self):
        from src.cli import completion_candidates

        self.assertEqual(
            completion_candidates(["timeline", "get_items", "--track-type", "a"]),
            ["audio"],
        )
        self.assertEqual(completion_candidates(["--surface", "g"]), ["granular"])
        self.assertIn(
            "get_project_unique_id",
            completion_candidates(["--surface", "granular", "granular", "get_project_"]),
        )

    def test_advanced_tools_actions_and_scoped_enums_complete(self):
        from src.cli import completion_candidates

        self.assertIn("drp", completion_candidates(["advanced", "dr"]))
        self.assertIn(
            "place_transition",
            completion_candidates(["advanced", "drp", "place_"]),
        )
        self.assertEqual(
            completion_candidates([
                "advanced", "drp", "place_transition", "--duration-preset", "st"
            ]),
            ["standard"],
        )
        self.assertEqual(
            completion_candidates(["render", "load_preset", "--preset", "p"]),
            [],
            "caption preset values must not leak into unrelated actions",
        )

    def test_production_words_command_completes(self):
        from src.cli import completion_candidates

        self.assertEqual(completion_candidates(["production", "wo"]), ["words"])

    def test_production_music_subcommands_complete(self):
        from src.cli import completion_candidates

        self.assertEqual(completion_candidates(["production", "mu"]), ["music"])
        self.assertEqual(completion_candidates(["production", "music", "s"]), ["search", "select"])

    def test_production_broll_subcommands_complete(self):
        from src.cli import completion_candidates

        self.assertEqual(completion_candidates(["production", "br"]), ["broll"])
        self.assertEqual(
            completion_candidates(["production", "broll", "source-"]),
            ["source-apply", "source-plan"],
        )

    def test_hidden_completion_endpoint_preserves_option_words(self):
        from contextlib import redirect_stdout

        from src.cli import main

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["__complete", "--surface", "g"]), 0)
        self.assertEqual(output.getvalue(), "granular\n")

    def test_shell_adapters_delegate_to_dynamic_endpoint(self):
        from src.cli import _completion

        for shell in ("bash", "zsh", "fish"):
            script = _completion(shell)
            self.assertIn("dvr __complete", script, shell)

    def test_launcher_completion_does_not_sync_or_create_managed_install(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as parent:
            managed = Path(parent) / "must-not-be-created"
            env = dict(os.environ)
            env["DAVINCI_RESOLVE_MCP_INSTALL_ROOT"] = str(managed)
            env["DAVINCI_RESOLVE_MCP_PYTHON"] = sys.executable
            proc = subprocess.run(
                ["node", "bin/davinci-resolve.mjs", "__complete", "advanced", "dr"],
                cwd=project_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("drp", proc.stdout.splitlines())
            self.assertFalse(managed.exists())


class JsonlSessionTests(unittest.TestCase):
    @staticmethod
    def _run(source: str):
        from src.cli import _global_options, run_jsonl_session

        _rest, opts = _global_options([])
        output = io.StringIO()
        asyncio.run(run_jsonl_session(io.StringIO(source), output, opts))
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_direct_and_argv_requests_continue_after_bad_json(self):
        rows = self._run(
            '\n'.join(
                [
                    json.dumps({"id": "direct", "tool": "knowledge", "action": "topics"}),
                    '{not json}',
                    json.dumps({"id": "argv", "argv": ["actions", "timeline"]}),
                ]
            )
        )
        self.assertEqual([row["id"] for row in rows], ["direct", None, "argv"])
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[1]["exit_code"], 2)
        self.assertTrue(rows[2]["ok"])
        self.assertIn("get_items", rows[2]["result"]["actions"])

    def test_tool_refusal_is_an_error_envelope_not_a_dead_session(self):
        rows = self._run(
            '\n'.join(
                [
                    json.dumps({"id": 1, "argv": ["not_a_tool", "anything"]}),
                    json.dumps({"id": 2, "argv": ["actions", "knowledge"]}),
                ]
            )
        )
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["exit_code"], 2)
        self.assertTrue(rows[1]["ok"])

    def test_quit_acknowledges_and_stops_before_later_lines(self):
        rows = self._run(
            '\n'.join(
                [
                    json.dumps({"id": "q", "quit": True}),
                    json.dumps({"id": "never", "argv": ["actions", "timeline"]}),
                ]
            )
        )
        self.assertEqual(rows, [{
            "id": "q",
            "ok": True,
            "exit_code": 0,
            "result": {"stopped": True},
        }])

    def test_protocol_stdin_cannot_be_consumed_as_parameter_input(self):
        rows = self._run(json.dumps({"id": 1, "argv": ["timeline", "get_current", "--input", "-"]}))
        self.assertFalse(rows[0]["ok"])
        self.assertIn("protocol stream", rows[0]["error"]["message"])


if __name__ == "__main__":
    unittest.main()
