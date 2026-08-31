"""Bounded, source-safe runner for independent B-roll ideation agents.

The runner deliberately knows nothing about the B-roll idea schema beyond the
fact that a successful agent must return one JSON object.  Callers own the
schema and semantic validation.  This module owns the less glamorous but
important process contract: stdin-only prompts, bounded concurrency, immutable
per-agent artifacts, whole-process-group timeouts, and deterministic merging.

Nothing in this module writes to stdout.  A CLI may serialize the returned plan
or run manifest while routing optional progress events to stderr.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


ProgressCallback = Callable[[Mapping[str, Any]], None]


class BrollAgentRunnerError(ValueError):
    """The requested agent run is unsafe or internally inconsistent."""


_DEFAULT_LENSES = (
    "fact-first product detail",
    "editorial visual metaphor",
    "premium materials and macro detail",
    "practical feature demonstration",
    "diagrammatic mechanism explainer",
    "spatial context and scale",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _derived_seed(root_seed: int, index: int) -> int:
    # Keep agent-visible numbers inside JavaScript/JSON's exact integer range.
    # Larger 63-bit values were observed being rounded in otherwise valid
    # structured model output, breaking reproducible lineage.
    material = f"broll-agent-v2-json53:{root_seed}:{index}".encode("ascii")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & ((1 << 53) - 1)


def _safe_run_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_." else "-" for char in value)
    cleaned = cleaned.strip("-.")
    if not cleaned or cleaned in {".", ".."}:
        raise BrollAgentRunnerError("run_id must contain a safe filename character")
    return cleaned[:96]


def _agent_prompt(base_prompt: str, *, index: int, seed: int, lens: str) -> str:
    return (
        base_prompt.rstrip()
        + "\n\n<broll_agent_context>\n"
        + json.dumps(
            {
                "agent_index": index,
                "creative_seed": seed,
                "creative_lens": lens,
                "instruction": (
                    "Explore this lens independently while preserving every factual, "
                    "rights, identity, evidence, and must-not-show constraint."
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n</broll_agent_context>\n"
    )


def plan_agent_run(
    *,
    project_dir: os.PathLike[str] | str,
    prompt: str,
    output_schema: os.PathLike[str] | str,
    output_dir: os.PathLike[str] | str,
    agent_count: int = 3,
    seed: int = 0,
    minimum_successes: int = 1,
    codex_executable: os.PathLike[str] | str = "codex",
    creative_lenses: Optional[Sequence[str]] = None,
    extra_args: Sequence[str] = (),
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a JSON-serializable job plan without locating or launching Codex.

    Paths are resolved, but directories and files are not created.  This makes
    the function suitable for a CLI ``--print-jobs`` path on a machine where
    Codex is not installed.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        raise BrollAgentRunnerError("prompt must be a non-empty string")
    if isinstance(agent_count, bool) or int(agent_count) != agent_count or agent_count < 1:
        raise BrollAgentRunnerError("agent_count must be a positive integer")
    agent_count = int(agent_count)
    if isinstance(minimum_successes, bool) or int(minimum_successes) != minimum_successes:
        raise BrollAgentRunnerError("minimum_successes must be an integer")
    minimum_successes = int(minimum_successes)
    if minimum_successes < 1 or minimum_successes > agent_count:
        raise BrollAgentRunnerError("minimum_successes must be between 1 and agent_count")
    if isinstance(seed, bool):
        raise BrollAgentRunnerError("seed must be an integer")
    try:
        root_seed = int(seed)
    except (TypeError, ValueError) as exc:
        raise BrollAgentRunnerError("seed must be an integer") from exc

    root = Path(project_dir).expanduser().resolve()
    schema = Path(output_schema).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    lenses = tuple(str(value).strip() for value in (creative_lenses or _DEFAULT_LENSES))
    if not lenses or any(not value for value in lenses):
        raise BrollAgentRunnerError("creative_lenses must contain non-empty strings")

    prompt_digest = _sha256_text(prompt)
    identity = json.dumps(
        {
            "project_dir": str(root),
            "output_schema": str(schema),
            "prompt_sha256": prompt_digest,
            "agent_count": agent_count,
            "seed": root_seed,
            "minimum_successes": minimum_successes,
            "seed_derivation": "sha256-json53-v2",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    selected_run_id = _safe_run_id(run_id or f"agents-{_sha256_text(identity)[:16]}")
    run_root = output_root / selected_run_id
    executable = str(codex_executable)
    jobs = []
    for index in range(agent_count):
        number = index + 1
        agent_seed = _derived_seed(root_seed, index)
        lens = lenses[index % len(lenses)]
        stem = f"agent-{number:03d}"
        candidate = run_root / f"{stem}.json"
        pending = run_root / f".{stem}.pending.json"
        agent_prompt = _agent_prompt(prompt, index=index, seed=agent_seed, lens=lens)
        argv = [
            executable,
            *[str(value) for value in extra_args],
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-C",
            str(root),
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(pending),
            "-",
        ]
        jobs.append(
            {
                "id": f"{selected_run_id}:{stem}",
                "agent_index": index,
                "seed": agent_seed,
                "creative_lens": lens,
                "prompt": agent_prompt,
                "prompt_sha256": _sha256_text(agent_prompt),
                "argv": argv,
                "cwd": str(root),
                "candidate_path": str(candidate),
                "pending_path": str(pending),
                "stdout_path": str(run_root / f"{stem}.stdout.log"),
                "stderr_path": str(run_root / f"{stem}.stderr.log"),
                "result_path": str(run_root / f"{stem}.result.json"),
            }
        )
    return {
        "kind": "broll-agent-job-plan",
        "version": 1,
        "print_only": True,
        "run_id": selected_run_id,
        "project_dir": str(root),
        "output_schema": str(schema),
        "output_dir": str(run_root),
        "prompt_sha256": prompt_digest,
        "seed": root_seed,
        "seed_derivation": "sha256-json53-v2",
        "agent_count": agent_count,
        "minimum_successes": minimum_successes,
        "jobs": jobs,
    }


def build_agent_jobs(**kwargs: Any) -> list[Dict[str, Any]]:
    """Compatibility convenience returning only ``plan_agent_run()['jobs']``."""

    return list(plan_agent_run(**kwargs)["jobs"])


def _write_exclusive(path: os.PathLike[str] | str, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _publish_candidate(pending_path: Path, candidate_path: Path) -> None:
    """Publish without an overwrite-capable rename.

    A hard link is atomic and fails if another file already occupies the final
    name.  Both paths are under one run directory, so cross-device links are not
    a concern.
    """

    os.link(pending_path, candidate_path)
    pending_path.unlink()


def _terminate_process_group(proc: subprocess.Popen[str], grace_seconds: float = 0.25) -> None:
    """Terminate a timed-out process and every descendant it spawned."""

    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=max(1.0, grace_seconds),
            )
        except (OSError, subprocess.SubprocessError):
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        proc.kill()
        return
    try:
        proc.wait(timeout=max(0.01, grace_seconds))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()


def _progress(callback: Optional[ProgressCallback], payload: Mapping[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(dict(payload))
    except Exception:
        # A display/progress callback must never decide whether editorial work
        # succeeds. Callers can report their own callback failures if desired.
        return


def _result_base(job: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": job["id"],
        "agent_index": int(job["agent_index"]),
        "seed": int(job["seed"]),
        "creative_lens": job.get("creative_lens"),
        "candidate_path": job["candidate_path"],
        "stdout_path": job["stdout_path"],
        "stderr_path": job["stderr_path"],
        "result_path": job["result_path"],
    }


def _run_one(
    job: Mapping[str, Any],
    *,
    timeout_seconds: float,
    progress: Optional[ProgressCallback],
) -> Dict[str, Any]:
    started = time.monotonic()
    base = _result_base(job)
    _progress(progress, {**base, "event": "agent_started"})
    paths = [
        Path(str(job[key]))
        for key in ("candidate_path", "pending_path", "stdout_path", "stderr_path", "result_path")
    ]
    collision = next((path for path in paths if path.exists()), None)
    if collision is not None:
        result = {
            **base,
            "success": False,
            "status": "refused",
            "error": f"immutable agent output already exists: {collision}",
            "elapsed_seconds": 0.0,
        }
        _progress(progress, {**result, "event": "agent_finished"})
        return result
    Path(str(job["candidate_path"])).parent.mkdir(parents=True, exist_ok=True)

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(job["cwd"]),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc: Optional[subprocess.Popen[str]] = None
    stdout = ""
    stderr = ""
    status = "failed"
    error: Optional[str] = None
    candidate_payload: Optional[Dict[str, Any]] = None
    returncode: Optional[int] = None
    try:
        proc = subprocess.Popen([str(value) for value in job["argv"]], **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(
                input=str(job["prompt"]), timeout=float(timeout_seconds)
            )
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.output or "")
            stderr = str(exc.stderr or "")
            _terminate_process_group(proc)
            tail_out, tail_err = proc.communicate()
            stdout += tail_out or ""
            stderr += tail_err or ""
            status = "timed_out"
            error = f"agent exceeded timeout of {timeout_seconds:g} seconds"
        returncode = proc.returncode
        if error is None and returncode != 0:
            status = "failed"
            error = f"Codex exited with status {returncode}"
        if error is None:
            pending = Path(str(job["pending_path"]))
            if not pending.is_file():
                error = "Codex produced no output-last-message artifact"
            else:
                try:
                    loaded = json.loads(pending.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    error = f"Codex output is not valid JSON: {exc}"
                else:
                    if not isinstance(loaded, dict):
                        error = "Codex output must be one JSON object"
                    else:
                        _publish_candidate(pending, Path(str(job["candidate_path"])))
                        candidate_payload = loaded
                        status = "succeeded"
    except OSError as exc:
        error = f"could not launch Codex: {exc}"
    finally:
        try:
            _write_exclusive(job["stdout_path"], stdout)
            _write_exclusive(job["stderr_path"], stderr)
        except FileExistsError:
            if error is None:
                error = "immutable agent log already exists"
                status = "refused"

    result = {
        **base,
        "success": status == "succeeded",
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "error": error,
        "candidate": candidate_payload,
    }
    try:
        _write_exclusive(
            job["result_path"],
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    except FileExistsError:
        result["success"] = False
        result["status"] = "refused"
        result["error"] = "immutable agent result already exists"
    _progress(progress, {**result, "candidate": None, "event": "agent_finished"})
    return result


def run_agent_jobs(
    plan_or_jobs: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    max_workers: int = 3,
    timeout_seconds: float = 600,
    minimum_successes: Optional[int] = None,
    progress: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Execute jobs concurrently and return results in agent-index order."""

    if isinstance(plan_or_jobs, Mapping):
        jobs = list(plan_or_jobs.get("jobs") or [])
        run_id = plan_or_jobs.get("run_id")
        configured_minimum = int(plan_or_jobs.get("minimum_successes") or 1)
        plan_seed = plan_or_jobs.get("seed")
    else:
        jobs = list(plan_or_jobs)
        run_id = None
        configured_minimum = 1
        plan_seed = None
    if not jobs:
        raise BrollAgentRunnerError("agent job plan contains no jobs")
    if isinstance(max_workers, bool) or int(max_workers) != max_workers or max_workers < 1:
        raise BrollAgentRunnerError("max_workers must be a positive integer")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise BrollAgentRunnerError("timeout_seconds must be greater than zero")
    if isinstance(minimum_successes, bool):
        raise BrollAgentRunnerError("minimum_successes must be an integer")
    minimum = configured_minimum if minimum_successes is None else int(minimum_successes)
    if minimum < 1 or minimum > len(jobs):
        raise BrollAgentRunnerError("minimum_successes must be between 1 and the job count")
    indexes = [int(job["agent_index"]) for job in jobs]
    if len(set(indexes)) != len(indexes):
        raise BrollAgentRunnerError("agent indexes must be unique")

    worker_count = min(int(max_workers), len(jobs))
    results = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="broll-agent") as pool:
        futures = {
            pool.submit(
                _run_one,
                job,
                timeout_seconds=float(timeout_seconds),
                progress=progress,
            ): job
            for job in jobs
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # defensive isolation between agent jobs
                job = futures[future]
                results.append(
                    {
                        **_result_base(job),
                        "success": False,
                        "status": "failed",
                        "returncode": None,
                        "elapsed_seconds": 0.0,
                        "error": f"agent worker failed: {type(exc).__name__}: {exc}",
                        "candidate": None,
                    }
                )
    results.sort(key=lambda row: int(row["agent_index"]))
    succeeded = [row for row in results if row.get("success")]
    failed = [row for row in results if not row.get("success")]
    success = len(succeeded) >= minimum
    return {
        "kind": "broll-agent-run",
        "version": 1,
        "success": success,
        "partial_success": bool(succeeded and failed),
        "status": "succeeded" if not failed else ("partial" if success else "failed"),
        "run_id": run_id,
        "seed": plan_seed,
        "agent_count": len(results),
        "minimum_successes": minimum,
        "successful_count": len(succeeded),
        "failed_count": len(failed),
        "timed_out_count": sum(row.get("status") == "timed_out" for row in results),
        "results": results,
        "candidates": [row["candidate"] for row in succeeded],
        "source_media_modified": False,
    }


def run_broll_agents(
    *,
    max_workers: int = 3,
    timeout_seconds: float = 600,
    progress: Optional[ProgressCallback] = None,
    **plan_kwargs: Any,
) -> Dict[str, Any]:
    """Plan and execute a run; useful for thin CLI adapters."""

    plan = plan_agent_run(**plan_kwargs)
    return run_agent_jobs(
        plan,
        max_workers=max_workers,
        timeout_seconds=timeout_seconds,
        progress=progress,
    )


__all__ = [
    "BrollAgentRunnerError",
    "build_agent_jobs",
    "plan_agent_run",
    "run_agent_jobs",
    "run_broll_agents",
]
