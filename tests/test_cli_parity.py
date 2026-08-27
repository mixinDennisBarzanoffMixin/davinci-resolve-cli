"""Offline parity guards for the bash-composable Resolve CLI.

The CLI is another transport over the existing MCP implementations, not a
second implementation.  These tests deliberately enumerate FastMCP's live
registries so a newly added MCP tool cannot silently disappear from the CLI.
They do not connect to Resolve.
"""
from __future__ import annotations

import asyncio
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _mcp(surface: str):
    if surface == "compound":
        from src.server import mcp
    elif surface == "granular":
        from src.granular import mcp
    else:  # pragma: no cover - test helper misuse
        raise ValueError(surface)
    return mcp


def _mcp_tool_registry(surface: str):
    return _mcp(surface)._tool_manager._tools


def _run_python_cli_json(*argv: str):
    from src.cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = main(["--compact", *argv])
    if rc:
        raise AssertionError(
            f"src.cli {argv!r} exited {rc}: {stderr.getvalue()}"
        )
    return json.loads(stdout.getvalue())


class PythonCliToolParityTests(unittest.TestCase):
    """Every Python MCP tool is addressable through the shared CLI registry."""

    def test_compound_registry_matches_fastmcp_exactly(self):
        from src.cli import build_registry

        registered = _mcp_tool_registry("compound")
        cli_registry = build_registry("compound")

        self.assertEqual(len(registered), 36)
        self.assertEqual(set(cli_registry), set(registered))
        # build_registry must expose the actual FastMCP Tool objects.  Sharing
        # them preserves validation, defaults, result conversion, and future
        # SDK behavior instead of making the CLI maintain a second schema.
        for name, tool in registered.items():
            self.assertIs(cli_registry[name], tool, name)
            self.assertEqual(cli_registry[name].parameters, tool.parameters, name)

    def test_granular_registry_matches_fastmcp_exactly(self):
        from src.cli import build_registry

        registered = _mcp_tool_registry("granular")
        cli_registry = build_registry("granular")

        self.assertEqual(len(registered), 353)
        self.assertEqual(set(cli_registry), set(registered))
        for name, tool in registered.items():
            self.assertIs(cli_registry[name], tool, name)
            self.assertEqual(cli_registry[name].parameters, tool.parameters, name)

    def test_list_tools_is_a_lossless_serialisation_of_registry(self):
        from src.cli import build_registry, list_tools

        for surface in ("compound", "granular"):
            registry = build_registry(surface)
            rows = list_tools(surface)
            self.assertIsInstance(rows, list)
            self.assertTrue(all(isinstance(row, dict) for row in rows))
            by_name = {row["name"]: row for row in rows}
            self.assertEqual(set(by_name), set(registry), surface)
            self.assertEqual(len(by_name), len(rows), f"duplicate {surface} CLI rows")
            for name, tool in registry.items():
                row = by_name[name]
                self.assertEqual(
                    row.get("description"), (tool.description or "").strip(), name
                )
                self.assertEqual(row.get("input_schema"), tool.parameters, name)

    def test_fastmcp_public_schemas_match_the_cli_registry(self):
        """Guard the SDK manager/public descriptor boundary as well as names."""
        from src.cli import build_registry

        for surface in ("compound", "granular"):
            registry = build_registry(surface)
            descriptors = {
                tool.name: tool for tool in asyncio.run(_mcp(surface).list_tools())
            }
            self.assertEqual(set(descriptors), set(registry))
            for name, tool in registry.items():
                self.assertEqual(descriptors[name].inputSchema, tool.parameters, name)

    def test_cli_tools_command_emits_every_registered_name(self):
        for surface, count in (("compound", 36), ("granular", 353)):
            payload = _run_python_cli_json("--surface", surface, "tools")
            rows = payload["tools"]
            self.assertEqual(payload["count"], count)
            self.assertEqual(
                {row["name"] for row in rows},
                set(_mcp_tool_registry(surface)),
                surface,
            )

    def test_shared_dispatch_runs_fastmcp_validation_without_resolve(self):
        from src.cli import call_registered_tool

        result = asyncio.run(
            call_registered_tool("compound", "knowledge", {"action": "topics"})
        )
        self.assertIsInstance(result, dict)
        self.assertNotIn("error", result)

    def test_compound_action_discovery_matches_authoritative_unknown_lists(self):
        """Discovery must not turn prose examples into phantom Bash actions."""
        from src.cli import build_registry, discover_actions
        from tests.test_action_list_drift import (
            _listed_actions,
            _module_list_constants,
        )

        server_path = PROJECT_ROOT / "src" / "server.py"
        tree = ast.parse(server_path.read_text())
        constants = _module_list_constants(tree)
        tool_names = set(build_registry("compound"))
        checked = 0
        problems = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in tool_names:
                continue
            listed, resolvable = _listed_actions(node, constants)
            if listed is None or not resolvable:
                continue
            checked += 1
            discovered = set(discover_actions(node.name))
            if discovered != listed:
                problems.append(
                    f"{node.name}: missing={sorted(listed - discovered)}, "
                    f"phantom={sorted(discovered - listed)}"
                )
        self.assertGreater(checked, 30, "too few compound action lists audited")
        self.assertEqual(problems, [], "\n".join(problems))


class PythonCliContextParityTests(unittest.TestCase):
    """Prompts and resources are CLI surfaces too, including URI templates."""

    def test_prompt_registries_match_fastmcp_by_identity(self):
        from src.cli import build_prompt_registry

        expected_counts = {"compound": 14, "granular": 0}
        for surface, count in expected_counts.items():
            registered = _mcp(surface)._prompt_manager._prompts
            cli_registry = build_prompt_registry(surface)
            self.assertEqual(len(registered), count)
            self.assertEqual(set(cli_registry), set(registered), surface)
            for name, prompt in registered.items():
                self.assertIs(cli_registry[name], prompt, name)

    def test_resource_registries_match_fastmcp_by_identity(self):
        from src.cli import (
            build_resource_registry,
            build_resource_template_registry,
        )

        # The granular surface has 24 fixed resources and four URI templates.
        # Templates are separately managed by FastMCP and must not vanish from
        # CLI discovery merely because they are not returned by list_resources.
        expected_counts = {
            "compound": (9, 0),
            "granular": (24, 4),
        }
        for surface, (resource_count, template_count) in expected_counts.items():
            manager = _mcp(surface)._resource_manager
            cli_resources = build_resource_registry(surface)
            cli_templates = build_resource_template_registry(surface)
            self.assertEqual(len(manager._resources), resource_count)
            self.assertEqual(len(manager._templates), template_count)
            self.assertEqual(set(cli_resources), set(manager._resources), surface)
            self.assertEqual(set(cli_templates), set(manager._templates), surface)
            for uri, resource in manager._resources.items():
                self.assertIs(cli_resources[uri], resource, uri)
            for uri, template in manager._templates.items():
                self.assertIs(cli_templates[uri], template, uri)

    def test_prompt_render_and_resource_read_use_the_shared_objects(self):
        from src.cli import read_registered_resource, render_registered_prompt

        prompt = asyncio.run(
            render_registered_prompt("compound", "davinci_resolve_workflow", {})
        )
        self.assertTrue(prompt)
        resource = asyncio.run(
            read_registered_resource("compound", "status://mcp_version")
        )
        self.assertTrue(resource)

    def test_cli_context_discovery_emits_live_prompts_and_resources(self):
        for surface in ("compound", "granular"):
            prompts = _run_python_cli_json("--surface", surface, "prompts")
            self.assertEqual(
                {row["name"] for row in prompts["prompts"]},
                set(_mcp(surface)._prompt_manager._prompts),
                surface,
            )

            resources = _run_python_cli_json("--surface", surface, "resources")
            rows = resources["resources"]
            concrete = {row["uri"] for row in rows if row["kind"] == "resource"}
            templates = {row["uri"] for row in rows if row["kind"] == "template"}
            self.assertEqual(
                concrete,
                {str(uri) for uri in _mcp(surface)._resource_manager._resources},
                surface,
            )
            self.assertEqual(
                templates,
                {str(uri) for uri in _mcp(surface)._resource_manager._templates},
                surface,
            )

    def test_root_parser_exposes_operational_and_context_namespaces(self):
        from src.cli import main

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--help"])
        self.assertEqual(rc, 0)
        help_text = stdout.getvalue()
        for command in (
            "call",
            "tools",
            "prompts",
            "prompt",
            "resources",
            "resource",
            "setup",
            "doctor",
            "server",
            "control-panel",
            "batch",
            "advanced",
        ):
            self.assertIn(command, help_text)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the npm launcher")
class NpmLauncherParityTests(unittest.TestCase):
    """The installed executable routes CLI and operational subcommands."""

    def _run_launcher(self, *args: str, env=None):
        proc = subprocess.run(
            ["node", "bin/davinci-resolve.mjs", *args],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip())
        return proc.stdout

    def test_package_exposes_short_and_descriptive_cli_bins(self):
        package = json.loads((PROJECT_ROOT / "package.json").read_text())
        cli_entry = "./bin/davinci-resolve.mjs"
        for name in ("davinci-resolve", "davinci-resolve-cli", "dvr"):
            self.assertEqual(package["bin"].get(name), cli_entry)
        self.assertTrue((PROJECT_ROOT / cli_entry.removeprefix("./")).exists())

    def test_launcher_help_reaches_python_advanced_and_batch_clis(self):
        root_help = self._run_launcher("--help")
        self.assertIn("dvr tools", root_help)

        advanced_help = self._run_launcher("advanced", "--help")
        self.assertIn("resolve-advanced list", advanced_help)

        env = os.environ.copy()
        env["DAVINCI_RESOLVE_MCP_PYTHON"] = sys.executable
        batch_help = self._run_launcher("batch", "--help", env=env)
        for command in (
            "plan",
            "run",
            "status",
            "list",
            "resume",
            "cancel",
            "plan-spec",
            "apply",
        ):
            self.assertIn(command, batch_help)

    def test_launcher_retains_all_management_dispatch_branches(self):
        # Do not execute setup, the long-running servers, or the control panel
        # in an offline parity test.  Their explicit launcher branches are the
        # safe assertion that they remain addressable from an installed bin.
        source = (PROJECT_ROOT / "bin" / "davinci-resolve-mcp.mjs").read_text()
        for command in (
            "setup",
            "doctor",
            "server",
            "control-panel",
            "batch",
            "advanced",
        ):
            self.assertIn(f'command === "{command}"', source)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the advanced CLI")
class AdvancedCliParityTests(unittest.TestCase):
    """The Node MCP server and CLI must share all 18 handler objects/actions."""

    @staticmethod
    def _node_eval(source: str):
        proc = subprocess.run(
            ["node", "--input-type=module", "--eval", source],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise AssertionError(
                f"Node registry audit exited {proc.returncode}:\n{proc.stderr}"
            )
        return json.loads(proc.stdout)

    def test_advanced_mcp_and_cli_import_the_same_handlers(self):
        payload = self._node_eval(
            """
            import { TOOLS as registry, TOOL_BY_NAME, actionsForTool }
              from './resolve-advanced/server/tools/index.mjs';
            import { TOOLS as mcpTools } from './resolve-advanced/server/index.mjs';
            process.stdout.write(JSON.stringify({
              sameArray: registry === mcpTools,
              sameHandlers: registry.every((tool, i) => tool === mcpTools[i]),
              mapMatches: registry.every((tool) => TOOL_BY_NAME.get(tool.name) === tool),
              tools: registry.map((tool) => ({
                name: tool.name,
                description: tool.description,
                actions: actionsForTool(tool),
              })),
            }));
            """
        )
        rows = payload["tools"]
        names = [row["name"] for row in rows]
        self.assertTrue(payload["sameArray"])
        self.assertTrue(payload["sameHandlers"])
        self.assertTrue(payload["mapMatches"])
        self.assertEqual(len(names), 18)
        self.assertEqual(sum(len(row["actions"]) for row in rows), 151)
        self.assertEqual(len(names), len(set(names)), "duplicate advanced tool names")
        self.assertTrue(all(row["description"] for row in rows))
        self.assertTrue(all(row["actions"] for row in rows))

    def test_advanced_cli_list_and_actions_match_handler_registry(self):
        cli = PROJECT_ROOT / "resolve-advanced" / "cli.mjs"
        self.assertTrue(cli.exists(), "resolve-advanced/cli.mjs is not packaged")

        registry = self._node_eval(
            """
            import { TOOLS, actionsForTool }
              from './resolve-advanced/server/tools/index.mjs';
            console.log(JSON.stringify(TOOLS.map((tool) => ({
              name: tool.name,
              description: tool.description,
              actions: actionsForTool(tool),
            }))));
            """
        )
        proc = subprocess.run(
            ["node", str(cli), "list"],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        listed = json.loads(proc.stdout)
        if isinstance(listed, dict):
            listed = listed.get("tools")
        self.assertIsInstance(listed, list)
        listed_by_name = {row["name"]: row for row in listed}
        registry_by_name = {row["name"]: row for row in registry}
        self.assertEqual(set(listed_by_name), set(registry_by_name))
        for name, expected in registry_by_name.items():
            actual = listed_by_name[name]
            self.assertEqual(actual.get("description"), expected["description"], name)
            self.assertEqual(actual.get("actions"), expected["actions"], name)

            action_proc = subprocess.run(
                ["node", str(cli), "actions", name],
                cwd=PROJECT_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(action_proc.returncode, 0, action_proc.stderr)
            actions_payload = json.loads(action_proc.stdout)
            if isinstance(actions_payload, dict):
                actions_payload = actions_payload.get("actions")
            self.assertEqual(actions_payload, expected["actions"], name)


if __name__ == "__main__":
    unittest.main()
