"""Source-safe, recoverable track-level dialogue cleanup planning.

Resolve exposes Voice Isolation as timeline-track state.  This module wraps
that narrow public API in an immutable plan/readback/receipt contract; it does
not render, transcode, replace, relink, or otherwise touch source media.

The preset amounts are intentionally conservative.  They are editorial
starting points, not claims of equivalence to Adobe Enhance Speech or to
Resolve's unscriptable Dialogue Leveler/FairlightFX controls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping


PLAN_SCHEMA_VERSION = "dvr.audio-cleanup-plan.v1"
RECEIPT_SCHEMA_VERSION = "dvr.audio-cleanup-receipt.v1"

# Voice Isolation's documented amount range is 0..100.  Staying below the top
# quarter leaves room for an editor to push unusually noisy material by ear.
PRESET_AMOUNTS: dict[str, int] = {
    "off": 0,
    "light": 30,
    "balanced": 50,
    "strong": 70,
}


class AudioCleanupError(ValueError):
    """An audio-cleanup request is invalid, stale, or unsafe to advance."""


class AudioCleanupApplyError(AudioCleanupError):
    """Resolve rejected a cleanup mutation or its readback was inconclusive."""


def _canonical_sha256(payload: Any) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AudioCleanupError("audio-cleanup payload must be JSON-serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _json_clone(payload: Any) -> Any:
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise AudioCleanupError("audio-cleanup payload must be JSON-serializable") from exc


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AudioCleanupError(f"{field} is required")
    return text


def _track_index(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AudioCleanupError("track_index must be a positive integer")
    return value


def _amount(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AudioCleanupError("Voice Isolation amount must be an integer from 0 to 100")
    if not 0 <= value <= 100:
        raise AudioCleanupError("Voice Isolation amount must be in the range 0..100")
    return value


def resolve_amount(*, preset: str = "balanced", amount: int | None = None) -> tuple[str, int]:
    """Resolve a named starting point, with an explicit amount as an override."""

    normalized = str(preset or "").strip().casefold()
    if normalized not in PRESET_AMOUNTS:
        raise AudioCleanupError(
            "unsupported audio-cleanup preset; choose off, light, balanced, or strong"
        )
    if amount is None:
        return normalized, PRESET_AMOUNTS[normalized]
    return "custom", _amount(amount)


def _voice_state(raw: Any, field: str = "voice_isolation") -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AudioCleanupError(f"{field} must be an object")
    enabled = raw.get("isEnabled")
    if not isinstance(enabled, bool):
        raise AudioCleanupError(f"{field}.isEnabled must be boolean")
    return {"isEnabled": enabled, "amount": _amount(raw.get("amount"))}


def _track_snapshot(raw: Any, expected_index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AudioCleanupError("audio track probe must be an object")
    if raw.get("error"):
        raise AudioCleanupError(f"audio track probe failed: {raw.get('error')}")
    if raw.get("available") is False:
        raise AudioCleanupError(f"audio track A{expected_index} is unavailable")
    actual_index = raw.get("track_index", expected_index)
    if actual_index != expected_index:
        raise AudioCleanupError(
            f"audio track probe returned A{actual_index}, expected A{expected_index}"
        )
    name = _nonempty(raw.get("name"), "audio track name")
    enabled = raw.get("enabled")
    locked = raw.get("locked")
    if not isinstance(enabled, bool) or not isinstance(locked, bool):
        raise AudioCleanupError("audio track probe requires boolean enabled and locked states")
    voice_raw = raw.get("voice_isolation")
    if voice_raw is None and isinstance(raw.get("state"), Mapping):
        voice_raw = raw.get("state")
    snapshot = {
        "track_index": expected_index,
        "name": name,
        "sub_type": str(raw.get("sub_type") or ""),
        "enabled": enabled,
        "locked": locked,
        "voice_isolation": _voice_state(voice_raw),
    }
    if "track_count" in raw:
        count = raw.get("track_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < expected_index:
            raise AudioCleanupError("audio track probe has an invalid track_count")
        snapshot["track_count"] = count
    return snapshot


def _timeline_snapshot(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise AudioCleanupError("timeline readback must be an object")
    if raw.get("error"):
        raise AudioCleanupError(f"timeline readback failed: {raw.get('error')}")
    return {
        "id": _nonempty(raw.get("id"), "timeline id"),
        "name": _nonempty(raw.get("name"), "timeline name"),
    }


def _with_plan_hash(core: Mapping[str, Any]) -> dict[str, Any]:
    payload = _json_clone(core)
    return {**payload, "plan_sha256": _canonical_sha256(payload)}


def build_track_cleanup_plan(
    *,
    timeline_id: str,
    timeline_name: str,
    track_index: int,
    track_state: Mapping[str, Any],
    preset: str = "balanced",
    amount: int | None = None,
) -> dict[str, Any]:
    """Build an immutable, dry-run Voice Isolation request for one audio track."""

    index = _track_index(track_index)
    target = {
        "id": _nonempty(timeline_id, "timeline_id"),
        "name": _nonempty(timeline_name, "timeline_name"),
    }
    snapshot = _track_snapshot(track_state, index)
    if snapshot["locked"]:
        raise AudioCleanupError(f"audio track A{index} is locked")
    selected_preset, selected_amount = resolve_amount(preset=preset, amount=amount)
    desired = {
        "isEnabled": selected_amount > 0,
        "amount": selected_amount,
    }
    core = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": "track-voice-isolation-cleanup",
        "mode": "clean",
        "dry_run": True,
        "target_timeline": target,
        "track_snapshot": snapshot,
        "preset": selected_preset,
        "desired_state": desired,
        "dispatch": {
            "tool": "timeline",
            "action": "set_voice_isolation_state",
            "params": {"track_index": index, "state": desired},
        },
        "source_media_modified": False,
    }
    return _with_plan_hash(core)


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise AudioCleanupError("audio-cleanup plan must be an object")
    payload = _json_clone(plan)
    supplied_hash = str(payload.pop("plan_sha256", ""))
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise AudioCleanupError("unsupported audio-cleanup plan schema_version")
    if payload.get("kind") != "track-voice-isolation-cleanup":
        raise AudioCleanupError("unsupported audio-cleanup plan kind")
    if payload.get("dry_run") is not True:
        raise AudioCleanupError("audio-cleanup plan must be an unapplied dry-run request")
    if supplied_hash != _canonical_sha256(payload):
        raise AudioCleanupError("audio-cleanup plan hash does not match")
    target = _timeline_snapshot(payload.get("target_timeline"))
    dispatch = payload.get("dispatch")
    if not isinstance(dispatch, Mapping):
        raise AudioCleanupError("audio-cleanup plan has no dispatch object")
    params = dispatch.get("params")
    if (
        dispatch.get("tool") != "timeline"
        or dispatch.get("action") != "set_voice_isolation_state"
        or not isinstance(params, Mapping)
    ):
        raise AudioCleanupError("audio-cleanup plan has an unsupported dispatch")
    index = _track_index(params.get("track_index"))
    snapshot = _track_snapshot(payload.get("track_snapshot"), index)
    desired = _voice_state(payload.get("desired_state"), "desired_state")
    if _voice_state(params.get("state"), "dispatch.params.state") != desired:
        raise AudioCleanupError("audio-cleanup dispatch differs from desired_state")
    if payload.get("source_media_modified") is not False:
        raise AudioCleanupError("audio-cleanup plan must preserve source media")
    return {
        **payload,
        "target_timeline": target,
        "track_snapshot": snapshot,
        "desired_state": desired,
        "plan_sha256": supplied_hash,
    }


def _setter_succeeded(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    if isinstance(result, Mapping):
        return result.get("success") is True and not result.get("error")
    return False


def _best_effort_rollback(
    set_voice_isolation: Callable[[int, Mapping[str, Any]], Any],
    get_track_state: Callable[[int], Mapping[str, Any]],
    index: int,
    before: Mapping[str, Any],
) -> bool:
    try:
        result = set_voice_isolation(index, _json_clone(before))
        if not _setter_succeeded(result):
            return False
        current = _track_snapshot(get_track_state(index), index)
        return current["voice_isolation"] == before
    except Exception:
        return False


def apply_track_cleanup_plan(
    plan: Mapping[str, Any],
    *,
    get_current_timeline: Callable[[], Mapping[str, Any]],
    get_track_state: Callable[[int], Mapping[str, Any]],
    set_voice_isolation: Callable[[int, Mapping[str, Any]], Any],
    authorized: bool = False,
) -> dict[str, Any]:
    """Apply one plan with exact preconditions and before/after readback.

    ``authorized`` is deliberately false by default so merely constructing or
    inspecting a plan cannot mutate Resolve.  Callback exceptions and false or
    ambiguous setter results never produce a success receipt.
    """

    validated = _validate_plan(plan)
    if not authorized:
        raise AudioCleanupApplyError("explicit authorization is required to apply audio cleanup")
    target = validated["target_timeline"]
    current_timeline = _timeline_snapshot(get_current_timeline())
    if current_timeline != target:
        raise AudioCleanupApplyError(
            "current Resolve timeline does not match the audio-cleanup plan target"
        )
    expected_track = validated["track_snapshot"]
    index = expected_track["track_index"]
    current_track = _track_snapshot(get_track_state(index), index)
    if current_track != expected_track:
        raise AudioCleanupApplyError("audio track state changed after the cleanup plan was built")
    if current_track["locked"]:
        raise AudioCleanupApplyError(f"audio track A{index} is locked")

    before = current_track["voice_isolation"]
    desired = validated["desired_state"]
    setter_result: Any = None
    setter_error: Exception | None = None
    try:
        setter_result = set_voice_isolation(index, _json_clone(desired))
    except Exception as exc:  # callback boundary: always inspect possible mutation
        setter_error = exc

    try:
        after_track = _track_snapshot(get_track_state(index), index)
        after = after_track["voice_isolation"]
    except Exception as exc:
        rolled_back = _best_effort_rollback(
            set_voice_isolation, get_track_state, index, before,
        )
        raise AudioCleanupApplyError(
            f"Voice Isolation readback failed; rollback_verified={str(rolled_back).lower()}"
        ) from exc

    if setter_error is not None or not _setter_succeeded(setter_result) or after != desired:
        rolled_back = True if after == before else _best_effort_rollback(
            set_voice_isolation, get_track_state, index, before,
        )
        reason = (
            f"setter raised {setter_error}"
            if setter_error is not None
            else "setter returned failure"
            if not _setter_succeeded(setter_result)
            else "readback differs from requested Voice Isolation state"
        )
        raise AudioCleanupApplyError(
            f"{reason}; rollback_verified={str(rolled_back).lower()}"
        )

    receipt_core = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "track-voice-isolation-cleanup-receipt",
        "applied": True,
        "plan_sha256": validated["plan_sha256"],
        "mode": validated.get("mode", "clean"),
        "target_timeline": target,
        "track_index": index,
        "track_name": current_track["name"],
        "track_snapshot_before": current_track,
        "track_snapshot_after": after_track,
        "before_state": before,
        "after_state": after,
        "setter_response": _json_clone(setter_result),
        "source_media_modified": False,
    }
    return {
        **receipt_core,
        "receipt_sha256": _canonical_sha256(receipt_core),
    }


def _validate_receipt(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise AudioCleanupError("audio-cleanup receipt must be an object")
    payload = _json_clone(receipt)
    supplied_hash = str(payload.pop("receipt_sha256", ""))
    if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise AudioCleanupError("unsupported audio-cleanup receipt schema_version")
    if payload.get("kind") != "track-voice-isolation-cleanup-receipt":
        raise AudioCleanupError("unsupported audio-cleanup receipt kind")
    if payload.get("applied") is not True or payload.get("source_media_modified") is not False:
        raise AudioCleanupError("audio-cleanup receipt does not describe a safe applied change")
    if supplied_hash != _canonical_sha256(payload):
        raise AudioCleanupError("audio-cleanup receipt hash does not match")
    payload["target_timeline"] = _timeline_snapshot(payload.get("target_timeline"))
    payload["track_index"] = _track_index(payload.get("track_index"))
    payload["track_name"] = _nonempty(payload.get("track_name"), "receipt track_name")
    payload["before_state"] = _voice_state(payload.get("before_state"), "before_state")
    payload["after_state"] = _voice_state(payload.get("after_state"), "after_state")
    payload["track_snapshot_before"] = _track_snapshot(
        payload.get("track_snapshot_before"), payload["track_index"],
    )
    payload["track_snapshot_after"] = _track_snapshot(
        payload.get("track_snapshot_after"), payload["track_index"],
    )
    if payload["track_snapshot_before"]["voice_isolation"] != payload["before_state"]:
        raise AudioCleanupError("receipt before_state differs from its track snapshot")
    if payload["track_snapshot_after"]["voice_isolation"] != payload["after_state"]:
        raise AudioCleanupError("receipt after_state differs from its track snapshot")
    payload["receipt_sha256"] = supplied_hash
    return payload


def build_restore_plan(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Create a hash-bound dry-run that restores a successful receipt."""

    validated = _validate_receipt(receipt)
    index = validated["track_index"]
    track_snapshot = validated["track_snapshot_after"]
    desired = validated["before_state"]
    core = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": "track-voice-isolation-cleanup",
        "mode": "restore",
        "dry_run": True,
        "target_timeline": validated["target_timeline"],
        "track_snapshot": track_snapshot,
        "preset": "restore",
        "desired_state": desired,
        "dispatch": {
            "tool": "timeline",
            "action": "set_voice_isolation_state",
            "params": {"track_index": index, "state": desired},
        },
        "restores_receipt_sha256": validated["receipt_sha256"],
        "source_media_modified": False,
    }
    return _with_plan_hash(core)


__all__ = [
    "AudioCleanupApplyError",
    "AudioCleanupError",
    "PLAN_SCHEMA_VERSION",
    "PRESET_AMOUNTS",
    "RECEIPT_SCHEMA_VERSION",
    "apply_track_cleanup_plan",
    "build_restore_plan",
    "build_track_cleanup_plan",
    "resolve_amount",
]
