from __future__ import annotations

import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src.utils import broll_agent_runner as runner


def _fake_codex(directory: Path) -> Path:
    path = directory / "fake-codex"
    path.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys
import time

args = sys.argv[1:]
output = pathlib.Path(args[args.index('--output-last-message') + 1])
prompt = sys.stdin.read()
context = json.loads(prompt.split('<broll_agent_context>')[1].split('</broll_agent_context>')[0])
index = context['agent_index']
if 'SLOW_AGENT' in prompt and index == 0:
    time.sleep(2)
if 'MALFORMED_AGENT' in prompt and index == 1:
    output.write_text('{broken', encoding='utf-8')
else:
    # Finish in reverse order to prove the merger ignores completion order.
    time.sleep(max(0, 2 - index) * 0.03)
    output.write_text(json.dumps({'agent_index': index, 'seed': context['creative_seed']}), encoding='utf-8')
print(f'agent {index} stdout')
print(f'agent {index} progress', file=sys.stderr)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class AgentPlanTest(unittest.TestCase):
    def test_print_only_plan_is_deterministic_and_does_not_launch_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = root / "schema.json"
            # Deliberately absent: planning only carries the path and should not
            # require Codex or pre-create any run artifacts.
            output = root / "runs"
            with patch.object(runner.subprocess, "Popen") as popen:
                first = runner.plan_agent_run(
                    project_dir=root,
                    prompt="Make ideas",
                    output_schema=schema,
                    output_dir=output,
                    agent_count=3,
                    seed=42,
                    codex_executable="definitely-not-installed-codex",
                )
                second = runner.plan_agent_run(
                    project_dir=root,
                    prompt="Make ideas",
                    output_schema=schema,
                    output_dir=output,
                    agent_count=3,
                    seed=42,
                    codex_executable="definitely-not-installed-codex",
                )
            self.assertEqual(first, second)
            self.assertTrue(first["print_only"])
            self.assertEqual(len({row["seed"] for row in first["jobs"]}), 3)
            self.assertTrue(all(0 <= row["seed"] <= (1 << 53) - 1 for row in first["jobs"]))
            self.assertEqual(first["seed_derivation"], "sha256-json53-v2")
            self.assertFalse(output.exists())
            popen.assert_not_called()

    def test_invalid_threshold_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(runner.BrollAgentRunnerError):
                runner.plan_agent_run(
                    project_dir=tmp,
                    prompt="ideas",
                    output_schema=Path(tmp) / "schema.json",
                    output_dir=Path(tmp) / "out",
                    agent_count=2,
                    minimum_successes=3,
                )


class AgentRunTest(unittest.TestCase):
    def _plan(self, root: Path, prompt: str = "Make ideas", count: int = 3, minimum: int = 1):
        schema = root / "schema.json"
        schema.write_text('{"type":"object"}', encoding="utf-8")
        return runner.plan_agent_run(
            project_dir=root,
            prompt=prompt,
            output_schema=schema,
            output_dir=root / "runs",
            agent_count=count,
            seed=99,
            minimum_successes=minimum,
            codex_executable=_fake_codex(root),
        )

    def test_real_subprocess_results_merge_in_agent_order_and_logs_are_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self._plan(Path(tmp))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = runner.run_agent_jobs(plan, max_workers=3, timeout_seconds=5)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(result["success"])
            self.assertEqual([row["agent_index"] for row in result["results"]], [0, 1, 2])
            self.assertEqual([row["agent_index"] for row in result["candidates"]], [0, 1, 2])
            self.assertTrue(all(Path(row["candidate_path"]).is_file() for row in result["results"]))
            self.assertIn("stdout", Path(result["results"][0]["stdout_path"]).read_text())
            self.assertIn("progress", Path(result["results"][0]["stderr_path"]).read_text())
            self.assertFalse(result["source_media_modified"])

    def test_malformed_agent_is_partial_and_minimum_threshold_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = runner.run_agent_jobs(
                self._plan(Path(tmp), "MALFORMED_AGENT", minimum=2),
                max_workers=2,
                timeout_seconds=5,
            )
            self.assertTrue(result["success"])
            self.assertTrue(result["partial_success"])
            self.assertEqual(result["successful_count"], 2)
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["results"][1]["status"], "failed")
            self.assertIn("not valid JSON", result["results"][1]["error"])

        with tempfile.TemporaryDirectory() as tmp:
            result = runner.run_agent_jobs(
                self._plan(Path(tmp), "MALFORMED_AGENT", minimum=3),
                max_workers=2,
                timeout_seconds=5,
            )
            self.assertFalse(result["success"])
            self.assertTrue(result["partial_success"])
            self.assertEqual(result["status"], "failed")

    def test_timeout_uses_process_group_termination_and_preserves_other_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self._plan(Path(tmp), "SLOW_AGENT", count=2)
            original = runner._terminate_process_group
            terminated = []

            def recording_terminate(proc, grace_seconds=0.25):
                terminated.append(proc.pid)
                return original(proc, grace_seconds)

            with patch.object(runner, "_terminate_process_group", side_effect=recording_terminate):
                result = runner.run_agent_jobs(plan, max_workers=2, timeout_seconds=1)
            self.assertEqual(len(terminated), 1)
            self.assertEqual(result["timed_out_count"], 1)
            self.assertEqual(result["successful_count"], 1)
            self.assertTrue(result["partial_success"])

    def test_existing_output_is_refused_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self._plan(Path(tmp), count=1)
            candidate = Path(plan["jobs"][0]["candidate_path"])
            candidate.parent.mkdir(parents=True)
            candidate.write_text("keep-me", encoding="utf-8")
            result = runner.run_agent_jobs(plan, max_workers=1, timeout_seconds=5)
            self.assertFalse(result["success"])
            self.assertEqual(result["results"][0]["status"], "refused")
            self.assertEqual(candidate.read_text(), "keep-me")

    def test_progress_is_callback_only_and_callback_failure_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            lock = threading.Lock()

            def progress(event):
                with lock:
                    events.append(event["event"])

            result = runner.run_agent_jobs(
                self._plan(Path(tmp), count=1),
                max_workers=1,
                timeout_seconds=5,
                progress=progress,
            )
            self.assertTrue(result["success"])
            self.assertEqual(events, ["agent_started", "agent_finished"])

        with tempfile.TemporaryDirectory() as tmp:
            result = runner.run_agent_jobs(
                self._plan(Path(tmp), count=1),
                max_workers=1,
                timeout_seconds=5,
                progress=lambda _event: (_ for _ in ()).throw(RuntimeError("display failed")),
            )
            self.assertTrue(result["success"])

    def test_parallelism_is_bounded_and_completion_order_does_not_affect_merge(self):
        jobs = [
            {
                "id": f"run:agent-{index}", "agent_index": index, "seed": index,
                "creative_lens": "lens", "candidate_path": f"/tmp/c-{index}",
                "pending_path": f"/tmp/p-{index}", "stdout_path": f"/tmp/o-{index}",
                "stderr_path": f"/tmp/e-{index}", "result_path": f"/tmp/r-{index}",
                "cwd": "/tmp", "argv": ["codex"], "prompt": "prompt",
            }
            for index in range(5)
        ]
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_run(job, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep((5 - job["agent_index"]) * 0.01)
            with lock:
                active -= 1
            return {
                **runner._result_base(job), "success": True, "status": "succeeded",
                "returncode": 0, "elapsed_seconds": 0.01, "error": None,
                "candidate": {"agent_index": job["agent_index"]},
            }

        with patch.object(runner, "_run_one", side_effect=fake_run):
            result = runner.run_agent_jobs(jobs, max_workers=2, timeout_seconds=1)
        self.assertLessEqual(peak, 2)
        self.assertEqual([row["agent_index"] for row in result["results"]], list(range(5)))


if __name__ == "__main__":
    unittest.main()
