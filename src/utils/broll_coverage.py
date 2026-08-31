"""Pure, fail-closed coverage accounting for a B-roll production pass.

Coverage is computed from the union of ready native-source placements and
ready published/render placements.  The caller owns the definition of ready:
no placement counts unless its explicit ``status`` is present in
``ready_statuses``.  This keeps pending, rejected, and unreviewed work from
silently satisfying editorial coverage.
"""

from __future__ import annotations

import json
import math
from typing import Any, Collection, Mapping, Sequence


BROLL_COVERAGE_REPORT_VERSION = "dvr.broll-coverage-report.v1"


class BrollCoverageError(ValueError):
    """The requested B-roll coverage gate did not pass."""

    def __init__(self, message: str, report: Mapping[str, Any]):
        super().__init__(message)
        self.report = json.loads(json.dumps(report))


def _rows(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    *,
    field: str,
    container_keys: Sequence[str],
) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    rows: Any = value
    if isinstance(value, Mapping):
        rows = None
        for key in container_keys:
            if key in value:
                rows = value[key]
                break
        if rows is None:
            raise ValueError(f"{field} object has no supported row collection")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise ValueError(f"{field} must be an array")
    result: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"{field}[{index}] must be an object")
        result.append(row)
    return result


def _duration_seconds(chunk: Mapping[str, Any]) -> float | None:
    for key in ("duration_seconds", "duration_sec", "length_seconds"):
        if chunk.get(key) is not None:
            try:
                value = float(chunk[key])
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value >= 0 else None
    if chunk.get("start_seconds") is not None and chunk.get("end_seconds") is not None:
        try:
            value = float(chunk["end_seconds"]) - float(chunk["start_seconds"])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value >= 0 else None
    return None


def validate_broll_coverage(
    kept_chunks: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    native_source_placements: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    published_render_placements: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    *,
    ready_statuses: Collection[str] = (),
    short_chunk_threshold_seconds: float = 1.0,
    allow_duplicate_coverage: bool = True,
    fail_closed: bool = False,
) -> dict[str, Any]:
    """Return chunk-level B-roll coverage and optionally enforce it as a gate.

    Every kept chunk remains in the denominator, including short chunks.  A
    placement counts only when it has a non-empty ``chunk_id`` and its explicit
    status is in the caller-provided ``ready_statuses`` collection.  Duplicate
    ready coverage is reported after unioning by chunk ID, rather than inflating
    the covered count.
    """

    try:
        short_threshold = float(short_chunk_threshold_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("short_chunk_threshold_seconds must be numeric") from exc
    if not math.isfinite(short_threshold) or short_threshold < 0:
        raise ValueError("short_chunk_threshold_seconds must be finite and non-negative")

    if isinstance(ready_statuses, (str, bytes)):
        raise ValueError("ready_statuses must be a collection of complete status strings")
    normalized_ready = {
        str(status).strip().casefold() for status in ready_statuses if str(status).strip()
    }
    chunks = _rows(
        kept_chunks,
        field="kept_chunks",
        container_keys=("kept_chunks", "chunks"),
    )
    if not chunks:
        raise ValueError("kept_chunks must contain at least one chunk")

    chunk_order: list[str] = []
    chunk_set: set[str] = set()
    short_chunks: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or "").strip()
        if not chunk_id:
            raise ValueError(f"kept_chunks[{index}] has no chunk_id or id")
        if chunk_id in chunk_set:
            raise ValueError(f"duplicate kept chunk id: {chunk_id}")
        chunk_set.add(chunk_id)
        chunk_order.append(chunk_id)
        duration = _duration_seconds(chunk)
        if duration is not None and duration < short_threshold:
            short_chunks.append({"chunk_id": chunk_id, "duration_seconds": round(duration, 9)})

    sources = (
        (
            "native_source",
            _rows(
                native_source_placements,
                field="native_source_placements",
                container_keys=("placements", "mappings"),
            ),
        ),
        (
            "published_render",
            _rows(
                published_render_placements,
                field="published_render_placements",
                container_keys=("placements",),
            ),
        ),
    )
    coverage_by_chunk: dict[str, list[dict[str, Any]]] = {}
    ignored: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for source, placements in sources:
        for index, placement in enumerate(placements):
            placement_id = str(
                placement.get("id")
                or placement.get("placement_id")
                or placement.get("beat_id")
                or placement.get("candidate_id")
                or f"{source}-{index + 1:04d}"
            ).strip()
            status = str(placement.get("status") or "").strip()
            identity = {
                "source": source,
                "placement_id": placement_id,
                "status": status or None,
            }
            if not status:
                ignored.append({**identity, "reason": "missing-ready-status"})
                continue
            if status.casefold() not in normalized_ready:
                ignored.append({**identity, "reason": "status-not-ready"})
                continue
            chunk_id = str(placement.get("chunk_id") or "").strip()
            if not chunk_id:
                invalid.append({**identity, "reason": "ready-placement-missing-chunk-id"})
                continue
            row = {**identity, "chunk_id": chunk_id}
            if chunk_id not in chunk_set:
                unexpected.append(row)
                continue
            coverage_by_chunk.setdefault(chunk_id, []).append(row)

    covered_chunk_ids = [chunk_id for chunk_id in chunk_order if chunk_id in coverage_by_chunk]
    missing_chunk_ids = [chunk_id for chunk_id in chunk_order if chunk_id not in coverage_by_chunk]
    duplicate_coverage = [
        {
            "chunk_id": chunk_id,
            "placement_count": len(coverage_by_chunk[chunk_id]),
            "placements": coverage_by_chunk[chunk_id],
        }
        for chunk_id in chunk_order
        if len(coverage_by_chunk.get(chunk_id, ())) > 1
    ]
    total = len(chunk_order)
    covered = len(covered_chunk_ids)
    all_covered = covered == total
    duplicate_chunk_ids = [row["chunk_id"] for row in duplicate_coverage]
    passed = (
        all_covered
        and (allow_duplicate_coverage or not duplicate_coverage)
        and not unexpected
        and not invalid
    )
    report: dict[str, Any] = {
        "schema_version": BROLL_COVERAGE_REPORT_VERSION,
        "success": passed,
        "all_covered": all_covered,
        "coverage": f"{covered}/{total}",
        "covered_count": covered,
        "required_count": total,
        "coverage_ratio": covered / total,
        "covered_chunk_ids": covered_chunk_ids,
        "missing_chunk_ids": missing_chunk_ids,
        "duplicate_chunk_ids": duplicate_chunk_ids,
        "duplicate_coverage": duplicate_coverage,
        "unexpected_placements": unexpected,
        "invalid_ready_placements": invalid,
        "ignored_placements": ignored,
        "short_chunks": short_chunks,
        "ready_statuses": sorted(normalized_ready),
        "allow_duplicate_coverage": bool(allow_duplicate_coverage),
    }
    if fail_closed and not passed:
        problems: list[str] = []
        if missing_chunk_ids:
            problems.append("missing=" + ",".join(missing_chunk_ids))
        if duplicate_chunk_ids and not allow_duplicate_coverage:
            problems.append("duplicates=" + ",".join(duplicate_chunk_ids))
        if unexpected:
            problems.append("unexpected-ready-placements")
        if invalid:
            problems.append("invalid-ready-placements")
        raise BrollCoverageError("B-roll coverage gate failed: " + "; ".join(problems), report)
    return report


__all__ = [
    "BROLL_COVERAGE_REPORT_VERSION",
    "BrollCoverageError",
    "validate_broll_coverage",
]
