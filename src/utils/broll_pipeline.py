"""Thin orchestration helpers for the reusable B-roll production CLI.

This layer adapts persisted project artifacts to the pure contracts in
``broll_ideation``.  It does not launch agents, generate media, touch Resolve,
or mutate source footage.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.utils import broll_ideation as ideation
from src.utils.broll_schemas import (
    BROLL_CANDIDATE_RUN_SCHEMA,
    CANDIDATE_RUN_SCHEMA_ID,
    VISUAL_TYPES,
)


class BrollPipelineError(ValueError):
    """A persisted B-roll artifact cannot safely advance to the next stage."""


_GENERATED_KINDS = {"generated_image", "generated_illustration"}
_EVIDENCE_KINDS = {"exact_asset", "evidence_image"}
_GRAPHIC_KINDS = {"motion_graphic", "diagram"}
_EVIDENCE_ORIGINS = {"approved_external", "listing_asset", "evidence_asset"}


def _placement_kind(placement: Mapping[str, Any]) -> str:
    treatment = placement.get("treatment")
    if isinstance(treatment, Mapping) and treatment.get("kind"):
        kind = str(treatment["kind"])
        return "generated_illustration" if kind == "generated_image" else kind
    visual_type = str(placement.get("visual_type") or "")
    if visual_type == "generated_image":
        return "generated_illustration"
    return visual_type


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_local_asset(
    placement: Mapping[str, Any],
    *,
    project_root: str | Path | None,
) -> dict[str, Any]:
    placement_id = str(placement.get("id") or placement.get("beat_id") or "<unknown>")
    asset = placement.get("asset")
    if not isinstance(asset, Mapping):
        raise BrollPipelineError(f"placement {placement_id} has no approved local asset")
    source = str(asset.get("src") or "").strip().replace("\\", "/")
    source_path = Path(source)
    if (
        not source
        or source_path.is_absolute()
        or any(part in {"", ".", ".."} for part in source_path.parts)
        or "://" in source
        or source.casefold().startswith(("data:", "file:"))
    ):
        raise BrollPipelineError(
            f"placement {placement_id} asset must be a safe relative path in remotion-assets"
        )
    if project_root is not None:
        assets_root = (Path(project_root).expanduser().resolve() / "remotion-assets").resolve()
        candidate_path = assets_root / source_path
        local_path = candidate_path.resolve()
        try:
            local_path.relative_to(assets_root)
        except ValueError as exc:
            raise BrollPipelineError(
                f"placement {placement_id} asset escapes remotion-assets"
            ) from exc
        if not local_path.is_file() or candidate_path.is_symlink():
            raise BrollPipelineError(
                f"placement {placement_id} local asset is missing or is not a regular file"
            )
        expected_sha = str(asset.get("sha256") or "").strip().casefold()
        if len(expected_sha) != 64 or any(character not in "0123456789abcdef" for character in expected_sha):
            raise BrollPipelineError(f"placement {placement_id} asset lacks a valid SHA-256")
        if _sha256_file(local_path) != expected_sha:
            raise BrollPipelineError(f"placement {placement_id} local asset hash does not match")
    return json.loads(json.dumps(asset))


def build_remotion_broll_manifest(
    base_manifest: Mapping[str, Any],
    placements_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    selection: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
    allow_partial: bool = False,
    artifact_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish reviewed non-source B-roll without mutating the base manifest.

    Project-source cutaways deliberately stay in Resolve's native cutaway path.
    Generated placements are fail-closed until a reviewed local derivative is
    attached; ``allow_partial`` skips pending generated work but never weakens
    identity, path, review, or integrity gates.
    """

    if not isinstance(base_manifest, Mapping):
        raise BrollPipelineError("base Remotion manifest must be an object")
    if not isinstance(selection, Mapping):
        raise BrollPipelineError("B-roll selection must be an object")
    if not isinstance(base_manifest.get("captions"), list):
        raise BrollPipelineError("base Remotion manifest must contain a captions array")
    if isinstance(placements_payload, Mapping):
        raw_placements = placements_payload.get("placements") or []
    else:
        raw_placements = placements_payload
    if not isinstance(raw_placements, Sequence) or isinstance(raw_placements, (str, bytes)):
        raise BrollPipelineError("B-roll placements must be an array")

    included: list[dict[str, Any]] = []
    included_ids: list[str] = []
    native_ids: list[str] = []
    pending_generated_ids: list[str] = []
    skipped_ids: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_placements):
        if not isinstance(raw, Mapping):
            raise BrollPipelineError(f"placement {index + 1} is not an object")
        placement_id = str(raw.get("id") or raw.get("beat_id") or "").strip()
        if not placement_id:
            raise BrollPipelineError(f"placement {index + 1} has no id")
        if placement_id in seen:
            raise BrollPipelineError(f"duplicate placement id: {placement_id}")
        seen.add(placement_id)
        kind = _placement_kind(raw)
        status = str(raw.get("status") or "")
        asset = raw.get("asset") if isinstance(raw.get("asset"), Mapping) else {}
        origin = str(asset.get("origin") or "")

        if kind == "source_cutaway":
            if origin in {"generated", "ai_generated"}:
                raise BrollPipelineError(
                    f"source cutaway {placement_id} cannot carry a generated asset"
                )
            native_ids.append(placement_id)
            continue
        if kind in _GRAPHIC_KINDS:
            if status != "ready-for-motion-graphic":
                skipped_ids.append(placement_id)
                continue
        elif kind in _GENERATED_KINDS or origin in {"generated", "ai_generated"}:
            if status != "ready-with-approved-asset" or not asset:
                pending_generated_ids.append(placement_id)
                continue
            if asset.get("exact_item") is not False:
                raise BrollPipelineError(
                    f"generated placement {placement_id} must explicitly set exact_item=false"
                )
            if str(asset.get("review_status") or "") != "approved":
                raise BrollPipelineError(f"generated placement {placement_id} lacks visual approval")
            if str(asset.get("origin") or "") not in {"generated", "ai_generated"}:
                raise BrollPipelineError(f"generated placement {placement_id} has an invalid asset origin")
            _validated_local_asset(raw, project_root=project_root)
        elif kind in _EVIDENCE_KINDS or origin in _EVIDENCE_ORIGINS:
            if status != "ready-with-approved-asset" or not asset:
                skipped_ids.append(placement_id)
                continue
            if (
                str(asset.get("rights_status") or "") != "approved"
                and str(asset.get("review_status") or "") != "approved"
            ):
                raise BrollPipelineError(f"evidence placement {placement_id} lacks rights approval")
            _validated_local_asset(raw, project_root=project_root)
        else:
            skipped_ids.append(placement_id)
            continue

        row = json.loads(json.dumps(raw))
        if "duration_seconds" not in row and "duration_sec" in row:
            row["duration_seconds"] = row["duration_sec"]
        if "on_screen_text" not in row and "on_screen_text_bg" in row:
            row["on_screen_text"] = row["on_screen_text_bg"]
        included.append(row)
        included_ids.append(placement_id)

    if pending_generated_ids and not allow_partial:
        raise BrollPipelineError(
            "generated B-roll assets are still pending approval: " + ", ".join(pending_generated_ids)
        )

    result = json.loads(json.dumps(base_manifest))
    result["placements"] = included
    result["broll_publication"] = {
        "schema_version": "dvr.remotion-broll.v1",
        "selection_sha256": str(selection.get("selection_sha256") or ideation.payload_sha256(selection)),
        "selection_seed": selection.get("seed"),
        "placements_sha256": ideation.payload_sha256(placements_payload),
        "included_ids": included_ids,
        "native_source_cutaway_ids": native_ids,
        "pending_generated_ids": pending_generated_ids,
        "skipped_ids": skipped_ids,
        "allow_partial": bool(allow_partial),
        "source_media_modified": False,
    }
    if artifact_provenance:
        provenance = result.get("provenance")
        if not isinstance(provenance, Mapping):
            provenance = {}
        result["provenance"] = {
            **json.loads(json.dumps(provenance)),
            "broll_publication": json.loads(json.dumps(artifact_provenance)),
        }
    return result


def normalize_reviewed_source_events(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Adapt the durable frame-review artifact to the ideation context schema."""

    review_status = str(payload.get("review_status") or "")
    if review_status not in {"frame_verified", "frame-reviewed", "manual-approved"}:
        raise BrollPipelineError("source events must pass frame review before use")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload.get("events") or []):
        if not isinstance(raw, Mapping):
            raise BrollPipelineError(f"source event {index + 1} is not an object")
        event_id = str(raw.get("event_id") or raw.get("id") or "").strip()
        if not event_id:
            raise BrollPipelineError(f"source event {index + 1} has no id")
        if event_id in seen:
            raise BrollPipelineError(f"duplicate source event id: {event_id}")
        seen.add(event_id)
        status = str(raw.get("status") or "")
        if status not in {"approved-source-candidate", "frame-reviewed", "manual-approved"}:
            raise BrollPipelineError(f"source event {event_id} is not approved")
        start = float(raw.get("start_seconds", 0))
        end = float(raw.get("end_seconds", 0))
        review_frame = raw.get("review_frame")
        if end <= start or not review_frame:
            raise BrollPipelineError(f"source event {event_id} lacks a valid range or review frame")
        use_for = [str(value) for value in raw.get("use_for") or [] if str(value)]
        normalized.append({
            "id": event_id,
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "label": str(raw.get("visible") or raw.get("label") or event_id),
            "event_type": str(raw.get("event_type") or (use_for[0] if use_for else "other")),
            "track_type": "video",
            "track_index": int(raw.get("track_index") or 1),
            "locator_source": "manual",
            "verification_status": "frame-reviewed",
        })
    if not normalized:
        raise BrollPipelineError("source events artifact contains no reviewed events")
    return normalized


def build_agent_prompt(
    context: Mapping[str, Any],
    *,
    candidates_per_agent: int = 5,
    generated_only: bool = False,
    visual_types: Sequence[str] | None = None,
) -> str:
    """Create one shared prompt; the runner appends each independent lens/seed."""

    if candidates_per_agent < 1:
        raise BrollPipelineError("candidates_per_agent must be positive")
    selected_visual_types = normalize_visual_types(
        visual_types,
        generated_only=generated_only,
    )
    context_sha = ideation.payload_sha256(context)
    generated_only_rules = ""
    if generated_only:
        generated_only_rules = (
            "\nThis is a GENERATED-ONLY GAP PASS. Return only candidates whose visual_type "
            "is generated_image, diagram, or motion_graphic. In this schema, generated_image "
            "means a generated editorial illustration; it never means an exact-item image. "
            "Do not return source_cutaway or exact_asset. Propose a candidate only when the "
            "frame-reviewed source_events for that moment cannot literally show the concept, "
            "such as an internal LPG/LPI mechanism, airflow, or an abstract service/parts "
            "network. A merely different visual style is not a source-footage gap. Inspect all "
            "source_event_ids on the moment before proposing a synthetic visual. Use "
            "source_event_id=null, depiction_scope conceptual or model_illustration, and include "
            "the exact phrase 'Editorial illustration — not the exact vehicle' in prompt. "
            "Include 'illustrative-non-exact' and 'source-footage-cannot-show-concept' in "
            "risk_flags. Honor the appended creative_lens and stay in that lane; do not default "
            "to the aftercare/service-network concept when assigned a mechanism, airflow, or "
            "feature-system lens. If the source literally shows the subject (vehicle, cabin, doors, "
            "seats, exterior, or price sheet), do not propose a generated substitute. Use basis "
            "a1_transcript_claim only for a fact whose research status is transcript_claim; use "
            "basis research for listing, seller, and manufacturer facts, and never attach both "
            f"bases to the same fact. Return exactly {candidates_per_agent} distinct safe "
            "candidates. If the assigned lens supports only one, use a different verified "
            "fact/source-footage gap for the other; never fill the quota with an unsafe idea or "
            "a mere style variation.\n"
        )
    visual_type_rules = ""
    if selected_visual_types:
        rendered = ", ".join(selected_visual_types)
        visual_type_rules = (
            f"\nVISUAL TYPE FILTER: every candidate visual_type must be one of: {rendered}. "
            "No other visual_type is permitted.\n"
        )
    return (
        "You are one independent B-roll ideator in a video-editing pipeline.\n"
        f"Return exactly one JSON object matching {CANDIDATE_RUN_SCHEMA_ID}. "
        f"Create at most {candidates_per_agent} candidates.\n"
        "Use the agent_index, creative_seed, and creative_lens from the appended "
        "<broll_agent_context>. Set run_id to '"
        + context_sha[:20]
        + "'; set job_id to 'agent-' plus the zero-padded one-based agent index; "
        "set agent_id the same way; set agent_role to creative_lens; set seed to creative_seed; "
        f"set context_sha256 to '{context_sha}'.\n"
        "Rules: prefer a frame-reviewed source_cutaway whenever it literally shows the "
        "thing being discussed (doors, wheel, cabin, price card, exterior). Generated imagery "
        "is only a fallback and must be conceptual or model_illustration, never exact_item. "
        "A1 recorded_speech and research determine relevance/facts. A2 is only a visual locator "
        "and cannot establish facts. Do not invent equipment, exact mechanical layout, badges, "
        "trim details, text, prices, or claims. Put factual typography in on_screen_text, not "
        "inside image prompts. Every generated prompt must say that it is an editorial "
        "illustration and include explicit must_not_show and negative_prompt safeguards.\n\n"
        + generated_only_rules
        + visual_type_rules
        + "\n"
        "CONTEXT_JSON:\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def normalize_visual_types(
    values: Sequence[str] | None,
    *,
    generated_only: bool = False,
) -> tuple[str, ...]:
    """Normalize repeatable/comma-separated CLI values against the schema."""

    tokens: list[str] = []
    for raw in values or ():
        tokens.extend(part.strip() for part in str(raw).split(",") if part.strip())
    if not tokens:
        return ("generated_image", "diagram", "motion_graphic") if generated_only else ()
    deduped = tuple(dict.fromkeys(tokens))
    unsupported = [value for value in deduped if value not in VISUAL_TYPES]
    if unsupported:
        raise BrollPipelineError("unsupported B-roll visual_type: " + ", ".join(unsupported))
    generated_types = {"generated_image", "diagram", "motion_graphic"}
    if generated_only and any(value not in generated_types for value in deduped):
        raise BrollPipelineError(
            "--generated-only visual types must be generated_image, diagram, or motion_graphic"
        )
    return deduped


def candidate_output_schema() -> dict[str, Any]:
    """Return a strict schema accepted by ``codex exec --output-schema``.

    The durable validation schema allows omitted convenience fields and uses
    conditional keywords. Structured-output backends require every object key
    in ``required`` and an explicit type for constants/enums, so this exported
    copy requires empty strings/arrays for inapplicable convenience fields and
    leaves cross-field safety rules to ``validate_candidate_run``.
    """

    def strictify(raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        node = {
            key: strictify(value)
            for key, value in raw.items()
            if key not in {"allOf", "if", "then", "uniqueItems"}
        }
        if "type" not in node:
            if "const" in node:
                value = node["const"]
                node["type"] = (
                    "boolean" if isinstance(value, bool)
                    else "integer" if isinstance(value, int)
                    else "number" if isinstance(value, float)
                    else "string"
                )
            elif node.get("enum"):
                values = node["enum"]
                node["type"] = "string" if all(isinstance(value, str) for value in values) else "number"
        if node.get("type") == "object" or (
            isinstance(node.get("type"), list) and "object" in node["type"]
        ):
            properties = node.get("properties") or {}
            adjusted = {}
            for name, value in properties.items():
                child = strictify(value)
                adjusted[name] = child
            node["properties"] = adjusted
            node["required"] = list(properties)
            node["additionalProperties"] = False
        return node

    schema = strictify(json.loads(json.dumps(BROLL_CANDIDATE_RUN_SCHEMA)))
    schema.pop("$schema", None)
    schema.pop("$id", None)
    return schema


def synthesize_selection(
    context: Mapping[str, Any],
    agent_run: Mapping[str, Any] | None,
    *,
    max_candidates: int = 10,
    quality_floor: float = 0.5,
    diversity: float = 0.45,
    seed: int | str = 0,
) -> dict[str, Any]:
    """Validate agent output, add source scouts, and select with seeded diversity."""

    candidates = ideation.build_source_cutaway_candidates(context)
    accepted_runs: list[dict[str, Any]] = []
    rejected_runs: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []
    for index, raw in enumerate((agent_run or {}).get("candidates") or []):
        if not isinstance(raw, Mapping):
            rejected_runs.append({"index": index, "errors": ["agent output is not an object"]})
            continue
        gate = ideation.validate_candidate_run(raw, context)
        if not gate["success"]:
            rejected_runs.append({
                "index": index,
                "agent_id": raw.get("agent_id"),
                "errors": gate["errors"],
                "candidate_errors": gate["candidate_errors"],
            })
            continue
        accepted_runs.append({
            "index": index,
            "agent_id": raw.get("agent_id"),
            "candidate_count": gate["candidate_count"],
            "valid_candidate_count": gate["valid_candidate_count"],
            "rejected_candidate_count": gate["rejected_candidate_count"],
        })
        candidates.extend(gate["accepted_candidates"])
        rejected_candidates.extend({
            "run_index": index,
            "agent_id": raw.get("agent_id"),
            **row,
        } for row in gate["candidate_errors"])
    if not candidates:
        raise BrollPipelineError("no valid source or agent B-roll candidates")
    selection = ideation.select_diverse_candidates(
        candidates,
        max_candidates=max_candidates,
        quality_floor=quality_floor,
        diversity=diversity,
        seed=seed,
    )
    return {
        **selection,
        "validation": {
            "accepted_agent_runs": accepted_runs,
            "rejected_agent_runs": rejected_runs,
            "rejected_agent_candidates": rejected_candidates,
            "source_candidate_count": len(ideation.build_source_cutaway_candidates(context)),
            "input_candidate_count": len(candidates),
        },
        "source_media_modified": False,
    }


def image_job_manifest(
    selection: Mapping[str, Any],
    *,
    variations: int = 2,
    seed: int | str | bytes | None = None,
) -> dict[str, Any]:
    jobs = ideation.build_image_jobs(selection, variations=variations, master_seed=seed)
    return {
        "schema_version": "dvr.broll-image-jobs.v1",
        "selection_sha256": selection.get("selection_sha256"),
        "job_count": len(jobs),
        "jobs": jobs,
        "source_media_modified": False,
    }


def selection_placements(
    context: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project selected contracts into generic Remotion/editorial placements."""

    moments = {str(row.get("id")): row for row in context.get("moments") or []}
    events = {str(row.get("id")): row for row in context.get("source_events") or []}
    placements: list[dict[str, Any]] = []
    moment_cursors: dict[str, float] = {}
    for candidate in selection.get("selected") or []:
        moment_id = str(candidate.get("moment_id") or "")
        moment = moments.get(moment_id)
        if moment is None:
            raise BrollPipelineError(f"selected candidate has unknown moment: {candidate.get('candidate_id')}")
        moment_start = float(moment["start_seconds"])
        moment_end = float(moment["end_seconds"])
        placement_start = moment_cursors.get(moment_id, moment_start)
        remaining = moment_end - placement_start
        duration = min(float(candidate.get("duration_seconds") or 0), remaining)
        if duration <= 0:
            raise BrollPipelineError(
                f"selected candidates exceed their editorial moment: {moment_id}"
            )
        moment_cursors[moment_id] = placement_start + duration
        kind = str(candidate.get("visual_type") or "motion_graphic")
        treatment_kind = {
            "generated_image": "generated_illustration",
            "exact_asset": "evidence_image",
        }.get(kind, kind)
        row: dict[str, Any] = {
            "id": str(candidate.get("candidate_id")),
            "beat_id": str(candidate.get("candidate_id")),
            "chunk_id": str(candidate.get("moment_id")),
            "start_seconds": round(placement_start, 3),
            "end_seconds": round(placement_start + duration, 3),
            "duration_sec": round(duration, 3),
            "chunk_offset_seconds": round(placement_start - moment_start, 3),
            "visual_type": kind,
            "status": (
                "ready-with-reviewed-source"
                if kind == "source_cutaway"
                else "needs-generated-asset"
                if kind == "generated_image"
                else "ready-for-motion-graphic"
            ),
            "visual_brief": candidate.get("concept"),
            "on_screen_text_bg": candidate.get("on_screen_text") or "",
            "must_not_show": list(candidate.get("must_not_show") or []),
            "fact_ids": [str(value.get("fact_id")) for value in candidate.get("fact_basis") or []],
            "treatment": {
                "kind": treatment_kind,
                "seed": ideation.json_safe_seed(
                    selection.get("seed", 0), candidate.get("candidate_id"), "motion"
                ),
                "depiction_scope": candidate.get("depiction_scope"),
                "disclosure": "Illustrative visualization" if kind == "generated_image" else None,
            },
        }
        if kind == "source_cutaway":
            event = events.get(str(candidate.get("source_event_id") or ""))
            if event is None or event.get("verification_status") not in {"frame-reviewed", "manual-approved"}:
                raise BrollPipelineError("source cutaway lost its frame-review gate")
            row["source_event"] = {
                "id": event["id"],
                "track_index": event["track_index"],
                "start_seconds": event["start_seconds"],
                "end_seconds": event["end_seconds"],
                "verification_status": event["verification_status"],
            }
        placements.append(row)
    placements.sort(key=lambda row: (float(row["start_seconds"]), row["id"]))
    return placements


def remap_placements_to_variant(
    placements: Sequence[Mapping[str, Any]],
    variant_request: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Map source-timeline chunk placements onto the compacted A-roll variant."""

    fps = float(source_snapshot.get("fps") or 0)
    timeline_start = int(source_snapshot.get("start_frame") or 0)
    if fps <= 0:
        raise BrollPipelineError("source snapshot has no valid fps")
    ranges = [
        row for row in ((variant_request.get("params") or {}).get("ranges") or [])
        if row.get("track_type") == "video" and int(row.get("track_index") or 0) == 1
    ]
    by_chunk: dict[str, Mapping[str, Any]] = {}
    for row in ranges:
        chunk_id = str(row.get("chunk_id") or "")
        if not chunk_id or chunk_id in by_chunk:
            raise BrollPipelineError("A-roll variant needs one V1 range per chunk")
        by_chunk[chunk_id] = row
    output: list[dict[str, Any]] = []
    for raw in placements:
        row = json.loads(json.dumps(raw))
        chunk_id = str(row.get("chunk_id") or "")
        mapped = by_chunk.get(chunk_id)
        if mapped is None:
            raise BrollPipelineError(f"placement chunk is absent from A-roll variant: {chunk_id}")
        duration = float(row.get("duration_sec") or row.get("duration_seconds") or 0)
        available = (int(mapped["end_frame"]) - int(mapped["start_frame"])) / fps
        offset = float(row.get("chunk_offset_seconds") or 0)
        if duration <= 0 or offset < 0 or offset + duration > available + 1e-9:
            raise BrollPipelineError(f"placement exceeds compacted chunk: {row.get('id')}")
        target_start = (int(mapped["record_frame"]) - timeline_start) / fps + offset
        row["source_timeline_start_seconds"] = row.get("start_seconds")
        row["start_seconds"] = round(target_start, 6)
        row["end_seconds"] = round(target_start + duration, 6)
        row["timing_space"] = "compacted_a_roll_variant"
        output.append(row)
    output.sort(key=lambda row: (float(row["start_seconds"]), str(row.get("id") or "")))
    return output


__all__ = [
    "BrollPipelineError",
    "build_agent_prompt",
    "build_remotion_broll_manifest",
    "candidate_output_schema",
    "image_job_manifest",
    "normalize_reviewed_source_events",
    "remap_placements_to_variant",
    "selection_placements",
    "synthesize_selection",
]
