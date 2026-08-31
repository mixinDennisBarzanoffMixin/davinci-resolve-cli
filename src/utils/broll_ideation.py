"""Source-first, evidence-grounded, multi-agent B-roll ideation primitives.

Everything in this module is offline and side-effect free.  It builds durable
JSON contracts for later CLI/agent/image-generation stages; it does not modify
Resolve, source media, or production artifacts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.utils.broll_schemas import (
    BROLL_CANDIDATE_RUN_SCHEMA,
    BROLL_CANDIDATE_SCHEMA,
    CANDIDATE_RUN_SCHEMA_ID,
    CONTEXT_SCHEMA_ID,
    FACT_BASES,
    IDEATION_JOB_SCHEMA_ID,
    IMAGE_JOB_SCHEMA_ID,
    SELECTION_SCHEMA_ID,
    STORY_FUNCTIONS,
    VISUAL_TYPES,
)


class BrollIdeationError(ValueError):
    """An ideation contract is incomplete, unsafe, or stale."""


DEFAULT_AGENT_ROLES = (
    "source-cutaway-scout",
    "literal-product-detail-scout",
    "mechanism-diagram-scout",
    "editorial-pacing-scout",
    "skeptical-fact-critic",
    "creative-concept-scout",
)

GENERATED_ONLY_AGENT_ROLES = (
    "lpg-lpi-mechanism-diagram-scout",
    "seat-airflow-and-thermal-concept-scout",
    "aftercare-service-network-abstract-scout",
    "verified-feature-system-motion-graphic-scout",
    "skeptical-generated-gap-critic",
    "minimal-editorial-transition-scout",
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SOURCE_EVENT_PATTERNS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("exterior", "Exterior / vehicle lineup", ("car", "cars", "exterior", "front", "white", "black", "автомобил")),
    ("door", "Door opening or closing", ("door", "opened", "closed", "врата")),
    ("interior", "Interior / dashboard", ("interior", "inside", "dashboard", "салон")),
    ("steering-wheel", "Steering wheel", ("wheel", "steering", "волан")),
    ("seat", "Seats or seat controls", ("seat", "seats", "heating", "massage", "седал")),
    ("rear", "Rear of the vehicle", ("rear", "back of the car", "задната", "заден")),
    ("price-sheet", "Price sheet or crossed-out price", ("sheet", "label", "price", "crossed", "pricing", "цена")),
    ("feature-control", "Feature control or equipment detail", ("feature", "control", "button", "extras", "опции")),
    ("engine", "Engine or powertrain visual", ("engine", "motor", "двигател", "lpg")),
)


def canonical_json(payload: Any) -> bytes:
    """Stable UTF-8 encoding used for content IDs and freshness checks."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _seed_key(master_seed: Optional[int | str | bytes]) -> Tuple[bytes, str]:
    if master_seed is None:
        raw = secrets.token_bytes(32)
        return raw, raw.hex()
    if isinstance(master_seed, bytes):
        raw = master_seed
        public = master_seed.hex()
    else:
        public = str(master_seed)
        raw = public.encode("utf-8")
    return hashlib.sha256(raw).digest(), public


def derive_seed(master_seed: int | str | bytes, *labels: Any) -> int:
    """Derive an independent 63-bit seed with HMAC rather than linear offsets."""
    key, _ = _seed_key(master_seed)
    message = "\x1f".join(str(value) for value in labels).encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def json_safe_seed(master_seed: int | str | bytes, *labels: Any) -> int:
    """Derive a seed that stays exact in JavaScript-backed JSON consumers."""

    return derive_seed(master_seed, *labels) & ((1 << 53) - 1)


def _tokens(text: str) -> set[str]:
    # Two-character product names such as K8 must survive tokenization.
    return {value.casefold() for value in _WORD_RE.findall(text) if len(value) >= 2}


def _normal_words(transcript: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw = list(transcript.get("words") or [])
    if not raw:
        for segment in transcript.get("segments") or []:
            raw.extend(segment.get("words") or [])
    words: List[Dict[str, Any]] = []
    for row in raw:
        word = str(row.get("word") or row.get("text") or "").strip()
        if not word:
            continue
        start = row.get("start_seconds", row.get("start"))
        end = row.get("end_seconds", row.get("end"))
        if start is None or end is None:
            raise BrollIdeationError("every transcript word needs start/end timing")
        start_seconds = float(start)
        end_seconds = float(end)
        if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or end_seconds < start_seconds:
            raise BrollIdeationError(f"invalid word timing for {word!r}")
        words.append({
            "word": word,
            "start_seconds": round(start_seconds, 6),
            "end_seconds": round(end_seconds, 6),
            "confidence": row.get("confidence", row.get("probability")),
        })
    return sorted(words, key=lambda row: (row["start_seconds"], row["end_seconds"]))


def _words_in_window(words: Sequence[Mapping[str, Any]], start: float, end: float) -> List[Dict[str, Any]]:
    return [
        dict(row) for row in words
        if float(row["end_seconds"]) > start and float(row["start_seconds"]) < end
    ]


def _word_text(words: Sequence[Mapping[str, Any]]) -> str:
    text = " ".join(str(row.get("word") or "") for row in words).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def _research_facts(research: Mapping[str, Any]) -> List[Dict[str, Any]]:
    exact = list(((research.get("exact_item") or {}).get("facts") or []))
    model = list(((research.get("model_or_category_context") or {}).get("facts") or []))
    facts: List[Dict[str, Any]] = []
    for raw in exact + model:
        row = dict(raw)
        sources = list(row.get("sources") or [])
        # A prior research artifact may accidentally promote A2 visual-guide
        # narration into a fact. Do not carry an A2-only claim into ideation;
        # A2 is an event locator regardless of how an upstream artifact labels
        # it. Facts with an independent listing/manufacturer/A1 source remain.
        a2_only = bool(sources) and all(
            "guide-transcript" in str(source.get("url") or "").casefold()
            or str(source.get("locator") or "").casefold().startswith("a2 ")
            for source in sources
            if isinstance(source, Mapping)
        )
        if not a2_only:
            facts.append(row)
    return facts


def infer_source_events_from_a2(
    a2_transcript: Mapping[str, Any],
    *,
    track_index: int = 1,
    pad_seconds: float = 0.45,
) -> List[Dict[str, Any]]:
    """Locate provisional V1 cutaways from A2's visual narration.

    These are location hints, not facts.  Every inferred event remains
    ``needs-frame-review`` until a person or visual-analysis stage verifies V1.
    """
    words = _normal_words(a2_transcript)
    events: List[Dict[str, Any]] = []
    occupied: set[Tuple[str, int]] = set()
    for index, word in enumerate(words):
        surrounding = words[max(0, index - 3): min(len(words), index + 4)]
        text = _word_text(surrounding).casefold()
        text_tokens = _tokens(text)
        for event_type, label, needles in _SOURCE_EVENT_PATTERNS:
            # Token containment avoids treating "camera" as an exterior event
            # merely because it contains the substring "car".
            if not any(_tokens(needle) and _tokens(needle) <= text_tokens for needle in needles):
                continue
            bucket = int(float(word["start_seconds"]) // 2)
            if (event_type, bucket) in occupied:
                continue
            occupied.add((event_type, bucket))
            start = max(0.0, float(surrounding[0]["start_seconds"]) - pad_seconds)
            end = max(start + 0.8, float(surrounding[-1]["end_seconds"]) + pad_seconds)
            events.append({
                "id": f"source-event-{len(events) + 1:04d}",
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "label": label,
                "event_type": event_type,
                "track_type": "video",
                "track_index": int(track_index),
                "locator_source": "a2_visual_narration",
                "verification_status": "needs-frame-review",
            })
    return events


def _validate_source_events(events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(events):
        row = dict(raw)
        event_id = str(row.get("id") or f"source-event-{index + 1:04d}")
        if event_id in seen:
            raise BrollIdeationError(f"duplicate source event id: {event_id}")
        seen.add(event_id)
        start = float(row.get("start_seconds", 0))
        end = float(row.get("end_seconds", 0))
        if end <= start:
            raise BrollIdeationError(f"source event {event_id} has an empty time range")
        normalized.append({
            "id": event_id,
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "label": str(row.get("label") or row.get("event_type") or "Source cutaway"),
            "event_type": str(row.get("event_type") or "other"),
            "track_type": "video",
            "track_index": int(row.get("track_index") or 1),
            "locator_source": str(row.get("locator_source") or "manual"),
            "verification_status": str(row.get("verification_status") or "needs-frame-review"),
        })
    return normalized


def build_broll_context(
    a1_transcript: Mapping[str, Any],
    a2_transcript: Mapping[str, Any],
    research: Mapping[str, Any],
    *,
    chunks: Optional[Mapping[str, Any]] = None,
    source_events: Optional[Sequence[Mapping[str, Any]]] = None,
    guide_lookback_seconds: float = 1.5,
    guide_lookahead_seconds: float = 1.5,
    video_track_index: int = 1,
) -> Dict[str, Any]:
    """Build role-preserving moments from A1, A2, and researched facts."""
    lookback = float(guide_lookback_seconds)
    lookahead = float(guide_lookahead_seconds)
    if lookback < 0 or lookahead < 0:
        raise BrollIdeationError("guide context radii cannot be negative")
    a1_words = _normal_words(a1_transcript)
    a2_words = _normal_words(a2_transcript)
    if not a1_words and not a2_words:
        raise BrollIdeationError("at least one timed transcript must contain words")

    if chunks is not None:
        windows = list(chunks.get("chunks") or [])
    else:
        # A2 is the preferred visual guide.  Its segments are natural source
        # event windows; fall back to a single timed span when segments are absent.
        windows = [
            {
                "id": f"moment-{index + 1:04d}",
                "start_seconds": segment.get("start_seconds", segment.get("start", 0)),
                "end_seconds": segment.get("end_seconds", segment.get("end", 0)),
                "protected": False,
            }
            for index, segment in enumerate(a2_transcript.get("segments") or [])
            if segment.get("end_seconds", segment.get("end")) is not None
        ]
        if not windows:
            all_words = a2_words or a1_words
            windows = [{
                "id": "moment-0001",
                "start_seconds": all_words[0]["start_seconds"],
                "end_seconds": all_words[-1]["end_seconds"],
                "protected": False,
            }]

    events = _validate_source_events(
        source_events if source_events is not None
        else infer_source_events_from_a2(a2_transcript, track_index=video_track_index)
    )
    facts = _research_facts(research)
    fact_by_id = {str(row.get("id")): row for row in facts if row.get("id")}
    beats = list(research.get("b_roll_beats") or [])
    moments: List[Dict[str, Any]] = []
    for index, window in enumerate(windows):
        start = float(window.get("start_seconds", window.get("start", 0)))
        end = float(window.get("end_seconds", window.get("end", 0)))
        if end <= start:
            raise BrollIdeationError(f"moment {index + 1} has an empty time range")
        a1_slice = _words_in_window(a1_words, start, end)
        a2_slice = _words_in_window(a2_words, max(0.0, start - lookback), end + lookahead)
        a1_tokens = _tokens(_word_text(a1_slice))
        a2_tokens = _tokens(_word_text(a2_slice))
        fact_ids: set[str] = set()
        for beat in beats:
            bg = _tokens(" ".join(str(value) for value in beat.get("trigger_words_bg") or []))
            guide = _tokens(" ".join(str(value) for value in beat.get("trigger_words_guide") or []))
            if a1_tokens & bg or a2_tokens & guide:
                fact_ids.update(str(value) for value in beat.get("fact_ids") or [] if str(value) in fact_by_id)
        event_ids = [
            row["id"] for row in events
            # Guide lookback/lookahead helps interpret delayed A2 narration,
            # but it must not move the actual V1 event into another editorial
            # moment. Source-event timing is a picture fact, not language
            # context, so bind it only on a real interval overlap.
            if row["end_seconds"] > start and row["start_seconds"] < end
        ]
        moments.append({
            "id": str(window.get("id") or f"moment-{index + 1:04d}"),
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "protected": bool(window.get("protected", False)),
            "a1": {
                "role": "recorded_speech",
                "language": str(a1_transcript.get("language") or "unknown"),
                "text": _word_text(a1_slice),
                "words": a1_slice,
            },
            "a2": {
                "role": "visual_locator_only",
                "language": str(a2_transcript.get("language") or "unknown"),
                "text": _word_text(a2_slice),
                "words": a2_slice,
            },
            "fact_ids": sorted(fact_ids),
            "source_event_ids": event_ids,
        })
    context = {
        "schema_version": CONTEXT_SCHEMA_ID,
        "input_hashes": {
            "a1": payload_sha256(a1_transcript),
            "a2": payload_sha256(a2_transcript),
            "research": payload_sha256(research),
            **({"chunks": payload_sha256(chunks)} if chunks is not None else {}),
        },
        "roles": {
            "a1": "recorded_speech",
            "a2": "visual_locator_only",
            "research": "factual_evidence",
        },
        "facts": facts,
        "moments": moments,
        "source_events": events,
        "settings": {
            "guide_lookback_seconds": lookback,
            "guide_lookahead_seconds": lookahead,
            "video_track_index": int(video_track_index),
        },
    }
    return context


def validate_context_freshness(
    context: Mapping[str, Any],
    *,
    a1_transcript: Mapping[str, Any],
    a2_transcript: Mapping[str, Any],
    research: Mapping[str, Any],
    chunks: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    current = {
        "a1": payload_sha256(a1_transcript),
        "a2": payload_sha256(a2_transcript),
        "research": payload_sha256(research),
        **({"chunks": payload_sha256(chunks)} if chunks is not None else {}),
    }
    recorded = dict(context.get("input_hashes") or {})
    stale = [name for name, digest in current.items() if recorded.get(name) != digest]
    missing = [name for name in recorded if name not in current]
    return {"success": not stale and not missing, "stale": stale, "missing_inputs": missing, "current_hashes": current}


def validate_research_freshness(
    research: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    volatile_max_age_hours: float = 48.0,
) -> Dict[str, Any]:
    """Check volatile listing values without pretending static facts expire equally."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    volatile: List[Dict[str, Any]] = []
    price = (research.get("exact_item") or {}).get("price") or {}
    if price:
        volatile.append({"field": "price", "retrieved_at": price.get("retrieved_at")})
    for row in research.get("freshness") or []:
        if row.get("volatile", True):
            volatile.append(dict(row))
    stale: List[Dict[str, Any]] = []
    missing: List[str] = []
    for row in volatile:
        raw = str(row.get("retrieved_at") or "")
        if not raw:
            missing.append(str(row.get("field") or "unknown"))
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            missing.append(str(row.get("field") or "unknown"))
            continue
        age_hours = (now_utc - parsed.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours > volatile_max_age_hours:
            stale.append({**row, "age_hours": round(age_hours, 3)})
    return {
        "success": not stale and not missing,
        "stale": stale,
        "missing_retrieval_time": missing,
        "volatile_max_age_hours": float(volatile_max_age_hours),
    }


def build_ideation_jobs(
    context: Mapping[str, Any],
    *,
    agent_count: int = 6,
    candidates_per_agent: int = 4,
    master_seed: Optional[int | str | bytes] = None,
    roles: Sequence[str] = DEFAULT_AGENT_ROLES,
) -> Dict[str, Any]:
    """Create immutable, content-addressed manifests for independent agents."""
    if agent_count < 1 or candidates_per_agent < 1:
        raise BrollIdeationError("agent and candidate counts must be positive")
    if not roles:
        raise BrollIdeationError("at least one ideation role is required")
    context_sha = payload_sha256(context)
    seed_key, public_master_seed = _seed_key(master_seed)
    jobs: List[Dict[str, Any]] = []
    for index in range(agent_count):
        role = str(roles[index % len(roles)])
        seed = derive_seed(seed_key, context_sha, role, index)
        agent_id = f"ideator-{index + 1:03d}"
        instructions = (
            f"Produce up to {candidates_per_agent} candidates using role {role!r}. "
            "Prefer a verified or frame-reviewable V1 source_cutaway whenever A2 locates "
            "doors, seats, wheel, price sheet, exterior, controls, or another useful source "
            "event. Use A1 and research for semantic relevance. A2 is visual_locator_only "
            "and must never be cited as factual evidence. Generated imagery is a fallback, "
            "must be model_illustration or conceptual, and must include must_not_show guards."
        )
        core = {
            "schema_version": IDEATION_JOB_SCHEMA_ID,
            "agent_id": agent_id,
            "agent_role": role,
            "seed": seed,
            "context_sha256": context_sha,
            "candidate_schema": CANDIDATE_RUN_SCHEMA_ID,
            "instructions": instructions,
        }
        job_id = "ideation-" + payload_sha256(core)[:20]
        jobs.append({**core, "job_id": job_id})
    core_manifest = {
        "schema_version": "dvr.broll-ideation-jobs.v1",
        "context_sha256": context_sha,
        "master_seed": public_master_seed,
        "jobs": jobs,
    }
    return {**core_manifest, "manifest_sha256": payload_sha256(core_manifest)}


def build_source_cutaway_candidates(context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Turn A2-located V1 moments into preferred, review-gated candidates."""
    moments = {str(row.get("id")): row for row in context.get("moments") or []}
    events = {str(row.get("id")): row for row in context.get("source_events") or []}
    out: List[Dict[str, Any]] = []
    for moment_id, moment in moments.items():
        if moment.get("protected"):
            continue
        for event_id in moment.get("source_event_ids") or []:
            event = events.get(str(event_id))
            if not event:
                continue
            duration = min(
                float(event["end_seconds"]) - float(event["start_seconds"]),
                float(moment["end_seconds"]) - float(moment["start_seconds"]),
                6.0,
            )
            if duration < 0.8:
                continue
            verified = event.get("verification_status") in {"frame-reviewed", "manual-approved"}
            out.append({
                "candidate_id": "source-" + payload_sha256({"moment": moment_id, "event": event_id})[:20],
                "moment_id": moment_id,
                "concept": str(event.get("label") or "V1 source cutaway"),
                "visual_type": "source_cutaway",
                "story_function": "prove",
                "depiction_scope": "exact_item",
                "duration_seconds": round(duration, 3),
                "source_event_id": event_id,
                "prompt": "",
                "negative_prompt": "",
                "on_screen_text": "",
                # A visually reviewed source event proves only what is visible
                # in the shot. Do not attach every nearby research fact (for
                # example, a price beat adjacent to a door shot) as though the
                # cutaway itself established it. Factual overlays remain an
                # explicit agent/editor decision.
                "fact_basis": [],
                "must_not_show": [],
                "risk_flags": ([] if verified else ["source-event-needs-frame-review"]),
                "scores": {
                    "a1_relevance": 0.8 if (moment.get("a1") or {}).get("text") else 0.4,
                    "a2_visual_relevance": 1.0,
                    "evidence_strength": 0.7 if verified else 0.45,
                    "visual_clarity": 0.9 if verified else 0.65,
                    "pacing_value": 0.8,
                    "risk": 0.08 if verified else 0.22,
                },
                "agent_ids": ["source-event-extractor"],
                "lineage": {
                    "locator_source": event.get("locator_source"),
                    "verification_status": event.get("verification_status"),
                    "context_sha256": payload_sha256(context),
                },
            })
    return out


def _schema_errors(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
    try:
        from jsonschema import Draft202012Validator  # type: ignore[import-not-found]
    except ImportError:
        return []
    return [
        "schema " + ".".join(str(value) for value in row.absolute_path) + ": " + row.message
        for row in sorted(Draft202012Validator(schema).iter_errors(dict(payload)), key=lambda item: list(item.absolute_path))
    ]


def validate_candidate(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
) -> List[str]:
    """Return validation errors, including rules JSON Schema cannot express."""
    errors: List[str] = _schema_errors(candidate, BROLL_CANDIDATE_SCHEMA)
    visual_type = str(candidate.get("visual_type") or "")
    scope = str(candidate.get("depiction_scope") or "")
    if visual_type not in VISUAL_TYPES:
        errors.append(f"unsupported visual_type: {visual_type!r}")
    if str(candidate.get("story_function") or "") not in STORY_FUNCTIONS:
        errors.append("unsupported story_function")
    if visual_type == "generated_image" and scope == "exact_item":
        errors.append("generated media must never depict or claim to depict the exact item")
    fact_rows = {str(row.get("id")): row for row in context.get("facts") or [] if row.get("id")}
    facts = set(fact_rows)
    for row in candidate.get("fact_basis") or []:
        basis = str(row.get("basis") or "")
        fact_id = str(row.get("fact_id") or "")
        if basis not in FACT_BASES:
            if basis in {"a2", "a2_visual_narration", "guide"}:
                errors.append("A2 is visual_locator_only and cannot establish facts")
            else:
                errors.append(f"unsupported fact basis: {basis!r}")
        if fact_id not in facts:
            errors.append(f"unknown fact_id: {fact_id}")
        elif basis == "a1_transcript_claim":
            sources = list((fact_rows.get(fact_id) or {}).get("sources") or [])
            has_a1_source = any(
                "transcript" in str(source.get("source_type") or "").casefold()
                and "guide-transcript" not in str(source.get("url") or "").casefold()
                and not str(source.get("locator") or "").casefold().startswith("a2 ")
                for source in sources
                if isinstance(source, Mapping)
            )
            if not has_a1_source:
                errors.append(f"fact_id {fact_id} has no A1 transcript basis")
    moments = {str(row.get("id")): row for row in context.get("moments") or []}
    moment = moments.get(str(candidate.get("moment_id") or ""))
    if moment is None:
        errors.append("candidate references an unknown moment")
    elif moment.get("protected"):
        errors.append("candidate references a protected A-roll moment")
    if visual_type == "source_cutaway":
        event_id = str(candidate.get("source_event_id") or "")
        events = {str(row.get("id")): row for row in context.get("source_events") or []}
        if not event_id or event_id not in events:
            errors.append("source_cutaway requires a known source_event_id")
        elif moment is not None and event_id not in (moment.get("source_event_ids") or []):
            errors.append("source event is not located in the candidate moment")
    try:
        duration = float(candidate.get("duration_seconds") or 0)
        if duration < 0.8:
            errors.append("candidate duration is below the 0.8 second floor")
    except (TypeError, ValueError):
        errors.append("candidate duration must be numeric")
    return errors


def validate_candidate_run(run: Mapping[str, Any], context: Mapping[str, Any]) -> Dict[str, Any]:
    # Validate the run envelope independently from its candidates.  One agent
    # can return several useful ideas plus one bad moment/event pairing; that
    # candidate must not launder through validation, but it must not discard its
    # valid siblings either.  Context and envelope/schema mismatches still make
    # the entire run unusable.
    envelope_schema = json.loads(json.dumps(BROLL_CANDIDATE_RUN_SCHEMA))
    envelope_schema["properties"]["candidates"] = {"type": "array", "items": {}}
    errors: List[str] = _schema_errors(run, envelope_schema)
    if run.get("schema_version") != CANDIDATE_RUN_SCHEMA_ID:
        errors.append("unsupported candidate run schema_version")
    context_sha = payload_sha256(context)
    if run.get("context_sha256") != context_sha:
        errors.append("candidate run is stale: context_sha256 differs")
    seen: set[str] = set()
    candidate_errors: List[Dict[str, Any]] = []
    accepted_candidates: List[Dict[str, Any]] = []
    for candidate in run.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            candidate_errors.append({"candidate_id": None, "errors": ["candidate must be an object"]})
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        current = validate_candidate(candidate, context)
        if not candidate_id:
            current.append("candidate_id is required")
        elif candidate_id in seen:
            current.append("duplicate candidate_id in run")
        seen.add(candidate_id)
        if current:
            candidate_errors.append({"candidate_id": candidate_id or None, "errors": current})
        else:
            accepted_candidates.append(dict(candidate))
    return {
        "success": not errors,
        "errors": errors,
        "candidate_errors": candidate_errors,
        "accepted_candidates": accepted_candidates,
        "candidate_count": len(run.get("candidates") or []),
        "valid_candidate_count": len(accepted_candidates),
        "rejected_candidate_count": len(candidate_errors),
    }


def candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    if candidate.get("visual_type") == "source_cutaway":
        # A source cutaway is the concrete V1 event at a concrete editorial
        # moment. Agents are free to describe it differently, but prose must not
        # turn one physical shot into several apparently diverse candidates.
        return payload_sha256({
            "visual_type": "source_cutaway",
            "moment_id": candidate.get("moment_id"),
            "source_event_id": candidate.get("source_event_id"),
        })
    normalized_concept = " ".join(sorted(_tokens(str(candidate.get("concept") or ""))))
    identity = {
        "moment_id": candidate.get("moment_id"),
        "visual_type": candidate.get("visual_type"),
        "source_event_id": candidate.get("source_event_id"),
        "concept_tokens": normalized_concept,
        "fact_ids": sorted(str(row.get("fact_id")) for row in candidate.get("fact_basis") or []),
    }
    return payload_sha256(identity)


def score_candidate(candidate: Mapping[str, Any]) -> Dict[str, float]:
    scores = candidate.get("scores") or {}
    def value(name: str) -> float:
        return min(1.0, max(0.0, float(scores.get(name) or 0)))
    base = (
        0.25 * value("a1_relevance")
        + 0.25 * value("a2_visual_relevance")
        + 0.20 * value("evidence_strength")
        + 0.15 * value("visual_clarity")
        + 0.15 * value("pacing_value")
        - 0.30 * value("risk")
    )
    source_bonus = 0.12 if candidate.get("visual_type") == "source_cutaway" else 0.0
    # Frame review is still required, but a located V1 shot should be attempted
    # before synthesizing a replacement.
    total = min(1.0, max(0.0, base + source_bonus))
    return {"base": round(base, 6), "source_cutaway_bonus": source_bonus, "total": round(total, 6)}


def deduplicate_candidates(candidates: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for raw in candidates:
        row = dict(raw)
        groups.setdefault(candidate_fingerprint(row), []).append(row)
    kept: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    for fingerprint, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (-score_candidate(row)["total"], str(row.get("candidate_id") or "")))
        winner = rows[0]
        agents = sorted({
            str(value) for row in rows for value in row.get("agent_ids") or [] if str(value)
        })
        winner = {**winner, "agent_ids": agents}
        kept.append(winner)
        for duplicate in rows[1:]:
            duplicates.append({
                "candidate_id": duplicate.get("candidate_id"),
                "duplicate_of": winner.get("candidate_id"),
                "fingerprint": fingerprint,
            })
    kept.sort(key=lambda row: str(row.get("candidate_id") or ""))
    return {"candidates": kept, "duplicates": duplicates}


def _similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    a = _tokens(str(left.get("concept") or ""))
    b = _tokens(str(right.get("concept") or ""))
    lexical = len(a & b) / max(1, len(a | b))
    same_visual = 1.0 if left.get("visual_type") == right.get("visual_type") else 0.0
    same_story = 1.0 if left.get("story_function") == right.get("story_function") else 0.0
    return min(1.0, 0.65 * lexical + 0.2 * same_visual + 0.15 * same_story)


def _candidate_jitter(seed: int | str | bytes, candidate: Mapping[str, Any]) -> float:
    raw = derive_seed(seed, candidate_fingerprint(candidate), "selection-jitter")
    return (raw / float((1 << 63) - 1)) * 0.02


def select_diverse_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int = 10,
    quality_floor: float = 0.50,
    diversity: float = 0.45,
    seed: int | str | bytes = 0,
) -> Dict[str, Any]:
    """Select good candidates with seeded tie variation and MMR diversity."""
    if max_candidates < 0:
        raise BrollIdeationError("max_candidates cannot be negative")
    if not 0 <= quality_floor <= 1 or not 0 <= diversity <= 1:
        raise BrollIdeationError("quality_floor and diversity must be between zero and one")
    deduped = deduplicate_candidates(candidates)
    eligible: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for candidate in deduped["candidates"]:
        score = score_candidate(candidate)
        if score["total"] < quality_floor:
            rejected.append({
                "candidate_id": candidate.get("candidate_id"),
                "reason": "below-quality-floor",
                "score": score,
            })
            continue
        eligible.append({**candidate, "selection_score": score})

    selected: List[Dict[str, Any]] = []
    remaining = list(eligible)
    while remaining and len(selected) < max_candidates:
        ranked: List[Tuple[float, float, str, Dict[str, Any]]] = []
        for candidate in remaining:
            similarity = max((_similarity(candidate, prior) for prior in selected), default=0.0)
            jitter = _candidate_jitter(seed, candidate)
            mmr = (1.0 - diversity) * candidate["selection_score"]["total"] - diversity * similarity + jitter
            # Source cutaways win exact ties before generated fallbacks.
            source_rank = 1.0 if candidate.get("visual_type") == "source_cutaway" else 0.0
            ranked.append((mmr, source_rank, str(candidate.get("candidate_id") or ""), candidate))
        ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
        mmr, _, _, chosen = ranked[0]
        selected.append({**chosen, "mmr_score": round(mmr, 6)})
        remaining = [row for row in remaining if row.get("candidate_id") != chosen.get("candidate_id")]

    core = {
        "schema_version": SELECTION_SCHEMA_ID,
        "seed": str(seed),
        "settings": {
            "max_candidates": max_candidates,
            "quality_floor": quality_floor,
            "diversity": diversity,
        },
        "selected": selected,
        "rejected": rejected,
        "duplicates": deduped["duplicates"],
    }
    return {**core, "selection_sha256": payload_sha256(core)}


def build_image_jobs(
    selection: Mapping[str, Any],
    *,
    variations: int = 3,
    master_seed: Optional[int | str | bytes] = None,
) -> List[Dict[str, Any]]:
    """Emit content-addressed jobs only for safe generated-image candidates."""
    if variations < 1:
        raise BrollIdeationError("variations must be positive")
    selection_sha = str(selection.get("selection_sha256") or payload_sha256(selection))
    seed_key, _ = _seed_key(master_seed)
    jobs: List[Dict[str, Any]] = []
    for candidate in selection.get("selected") or []:
        if candidate.get("visual_type") != "generated_image":
            continue
        if candidate.get("depiction_scope") == "exact_item":
            raise BrollIdeationError("generated media must never depict or claim to depict the exact item")
        prompt = str(candidate.get("prompt") or "").strip()
        if not prompt:
            raise BrollIdeationError(f"generated candidate {candidate.get('candidate_id')} has no prompt")
        candidate_sha = payload_sha256(candidate)
        for variation in range(1, variations + 1):
            seed = json_safe_seed(seed_key, selection_sha, candidate_sha, variation)
            core = {
                "schema_version": IMAGE_JOB_SCHEMA_ID,
                "candidate_id": str(candidate.get("candidate_id")),
                "seed": seed,
                "variation": variation,
                "prompt": prompt,
                "negative_prompt": str(candidate.get("negative_prompt") or ""),
                "depiction_scope": str(candidate.get("depiction_scope")),
                "candidate_sha256": candidate_sha,
                "selection_sha256": selection_sha,
                "output_name": f"{candidate.get('candidate_id')}-v{variation:02d}.png",
                "must_not_show": list(candidate.get("must_not_show") or []),
            }
            jobs.append({**core, "job_id": "image-" + payload_sha256(core)[:20]})
    return jobs


def image_jobs_jsonl(jobs: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in jobs)
