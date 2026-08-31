"""Versioned contracts for multi-agent B-roll ideation.

The schemas deliberately keep three different kinds of input separate:

* A1 is the recorded source-language speech.
* A2 is editorial/visual narration and may locate source-video events, but is
  never factual evidence.
* Research is the evidence ledger used to authorize factual claims.

Keeping those roles explicit prevents a visual guide such as "the engine is on
screen" from quietly becoming evidence that the exact item has that engine.
"""

from __future__ import annotations

from typing import Any, Dict


CONTEXT_SCHEMA_ID = "dvr.broll-context.v1"
IDEATION_JOB_SCHEMA_ID = "dvr.broll-ideation-job.v1"
CANDIDATE_RUN_SCHEMA_ID = "dvr.broll-candidate-run.v1"
SELECTION_SCHEMA_ID = "dvr.broll-selection.v1"
IMAGE_JOB_SCHEMA_ID = "dvr.broll-image-job.v1"

VISUAL_TYPES = (
    "source_cutaway",
    "generated_image",
    "motion_graphic",
    "diagram",
    "exact_asset",
)
DEPICTION_SCOPES = ("exact_item", "model_illustration", "conceptual")
STORY_FUNCTIONS = (
    "establish",
    "explain",
    "prove",
    "texture",
    "reset",
    "transition",
    "call_to_action",
)
FACT_BASES = ("research", "a1_transcript_claim")


WORD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": ["word", "start_seconds", "end_seconds"],
    "properties": {
        "word": {"type": "string"},
        "start_seconds": {"type": "number", "minimum": 0},
        "end_seconds": {"type": "number", "minimum": 0},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    },
}

BROLL_CONTEXT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": CONTEXT_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "input_hashes", "roles", "facts", "moments", "source_events"],
    "properties": {
        "schema_version": {"const": CONTEXT_SCHEMA_ID},
        "input_hashes": {
            "type": "object",
            "additionalProperties": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        },
        "roles": {
            "type": "object",
            "additionalProperties": False,
            "required": ["a1", "a2", "research"],
            "properties": {
                "a1": {"const": "recorded_speech"},
                "a2": {"const": "visual_locator_only"},
                "research": {"const": "factual_evidence"},
            },
        },
        "facts": {"type": "array", "items": {"type": "object"}},
        "moments": {"type": "array", "items": {"$ref": "#/$defs/moment"}},
        "source_events": {"type": "array", "items": {"$ref": "#/$defs/source_event"}},
        "settings": {"type": "object"},
    },
    "$defs": {
        "track_context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["role", "language", "text", "words"],
            "properties": {
                "role": {"enum": ["recorded_speech", "visual_locator_only"]},
                "language": {"type": "string"},
                "text": {"type": "string"},
                "words": {"type": "array", "items": WORD_SCHEMA},
            },
        },
        "moment": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id", "start_seconds", "end_seconds", "protected", "a1", "a2",
                "fact_ids", "source_event_ids",
            ],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "start_seconds": {"type": "number", "minimum": 0},
                "end_seconds": {"type": "number", "minimum": 0},
                "protected": {"type": "boolean"},
                "a1": {"$ref": "#/$defs/track_context"},
                "a2": {"$ref": "#/$defs/track_context"},
                "fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "source_event_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
        },
        "source_event": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id", "start_seconds", "end_seconds", "label", "event_type",
                "track_type", "track_index", "locator_source", "verification_status",
            ],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "start_seconds": {"type": "number", "minimum": 0},
                "end_seconds": {"type": "number", "minimum": 0},
                "label": {"type": "string"},
                "event_type": {"type": "string"},
                "track_type": {"const": "video"},
                "track_index": {"type": "integer", "minimum": 1},
                "locator_source": {"enum": ["a2_visual_narration", "visual_analysis", "manual"]},
                "verification_status": {"enum": ["needs-frame-review", "frame-reviewed", "manual-approved"]},
            },
        },
    },
}


BROLL_CANDIDATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "candidate_id", "moment_id", "concept", "visual_type", "story_function",
        "depiction_scope", "duration_seconds", "fact_basis", "must_not_show", "scores",
    ],
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1},
        "moment_id": {"type": "string", "minLength": 1},
        "concept": {"type": "string", "minLength": 1},
        "visual_type": {"enum": list(VISUAL_TYPES)},
        "story_function": {"enum": list(STORY_FUNCTIONS)},
        "depiction_scope": {"enum": list(DEPICTION_SCOPES)},
        "duration_seconds": {"type": "number", "minimum": 0.8},
        "source_event_id": {"type": ["string", "null"]},
        "prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "on_screen_text": {"type": "string"},
        "fact_basis": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fact_id", "basis"],
                "properties": {
                    "fact_id": {"type": "string"},
                    "basis": {"enum": list(FACT_BASES)},
                },
            },
        },
        "must_not_show": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "scores": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "a1_relevance", "a2_visual_relevance", "evidence_strength",
                "visual_clarity", "pacing_value", "risk",
            ],
            "properties": {
                "a1_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "a2_visual_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_strength": {"type": "number", "minimum": 0, "maximum": 1},
                "visual_clarity": {"type": "number", "minimum": 0, "maximum": 1},
                "pacing_value": {"type": "number", "minimum": 0, "maximum": 1},
                "risk": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "agent_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "lineage": {"type": "object"},
    },
    "allOf": [
        {
            "if": {"properties": {"visual_type": {"const": "source_cutaway"}}},
            "then": {"required": ["source_event_id"]},
        },
        {
            "if": {"properties": {"visual_type": {"const": "generated_image"}}},
            "then": {
                "required": ["prompt", "negative_prompt"],
                "properties": {"depiction_scope": {"enum": ["model_illustration", "conceptual"]}},
            },
        },
    ],
}


BROLL_CANDIDATE_RUN_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": CANDIDATE_RUN_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "run_id", "job_id", "agent_id", "agent_role", "seed",
        "context_sha256", "candidates",
    ],
    "properties": {
        "schema_version": {"const": CANDIDATE_RUN_SCHEMA_ID},
        "run_id": {"type": "string"},
        "job_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "agent_role": {"type": "string"},
        "seed": {"type": "integer", "minimum": 0},
        "context_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "candidates": {"type": "array", "items": BROLL_CANDIDATE_SCHEMA},
    },
}


BROLL_IDEATION_JOB_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IDEATION_JOB_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "job_id", "agent_id", "agent_role", "seed",
        "context_sha256", "candidate_schema", "instructions",
    ],
    "properties": {
        "schema_version": {"const": IDEATION_JOB_SCHEMA_ID},
        "job_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "agent_role": {"type": "string"},
        "seed": {"type": "integer", "minimum": 0},
        "context_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "candidate_schema": {"const": CANDIDATE_RUN_SCHEMA_ID},
        "instructions": {"type": "string"},
    },
}


BROLL_IMAGE_JOB_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IMAGE_JOB_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "job_id", "candidate_id", "seed", "prompt",
        "negative_prompt", "depiction_scope", "candidate_sha256", "selection_sha256",
    ],
    "properties": {
        "schema_version": {"const": IMAGE_JOB_SCHEMA_ID},
        "job_id": {"type": "string"},
        "candidate_id": {"type": "string"},
        "seed": {"type": "integer", "minimum": 0},
        "variation": {"type": "integer", "minimum": 1},
        "prompt": {"type": "string", "minLength": 1},
        "negative_prompt": {"type": "string"},
        "depiction_scope": {"enum": ["model_illustration", "conceptual"]},
        "candidate_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "selection_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "output_name": {"type": "string"},
        "must_not_show": {"type": "array", "items": {"type": "string"}},
    },
}
