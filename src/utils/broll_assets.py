"""Generated B-roll asset provenance and review gates.

Generated pictures are illustrative assets, never evidence of the exact item.
This module records and verifies sidecar artifacts; it never edits source media.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ASSET_SCHEMA_VERSION = "dvr.broll-generated-asset.v1"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class BrollAssetError(ValueError):
    """A generated asset is outside the project or has invalid provenance."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def record_generated_asset(
    *,
    project_dir: str | Path,
    image_job: Mapping[str, Any],
    asset_path: str | Path,
    provider: str,
    model: str | None = None,
) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    path = Path(asset_path).expanduser().resolve()
    generated_root = (root / "broll" / "generated").resolve()
    if not path.is_file() or path.is_symlink():
        raise BrollAssetError("generated asset must be an existing regular file")
    if not _inside(path, generated_root):
        raise BrollAssetError(f"generated asset must live under {generated_root}")
    if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
        raise BrollAssetError("generated asset must be PNG, JPEG, or WebP")
    if image_job.get("schema_version") != "dvr.broll-image-job.v1":
        raise BrollAssetError("unsupported image job schema")
    if image_job.get("depiction_scope") == "exact_item":
        raise BrollAssetError("generated assets must never claim to depict the exact item")
    prompt = str(image_job.get("prompt") or "").strip()
    if not prompt:
        raise BrollAssetError("image job has no prompt")
    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "asset_id": f"asset-{_sha256(path)[:20]}",
        "candidate_id": str(image_job.get("candidate_id") or ""),
        "image_job_id": str(image_job.get("job_id") or ""),
        "image_job_sha256": hashlib.sha256(
            json.dumps(dict(image_job), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "path": str(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "origin": "generated",
        "provider": str(provider),
        "model": model,
        "prompt": prompt,
        "negative_prompt": str(image_job.get("negative_prompt") or ""),
        "depiction_scope": str(image_job.get("depiction_scope") or "conceptual"),
        "depicts_exact_item": False,
        "must_not_show": list(image_job.get("must_not_show") or []),
        "created_at": _now_iso(),
        "review": {
            "status": "needs_visual_review",
            "reviewer": None,
            "reviewed_at": None,
            "notes": None,
        },
    }


def review_generated_asset(
    asset: Mapping[str, Any],
    *,
    project_dir: str | Path,
    approve: bool,
    reviewer: str,
    notes: str | None = None,
) -> dict[str, Any]:
    if asset.get("schema_version") != ASSET_SCHEMA_VERSION:
        raise BrollAssetError("unsupported generated asset schema")
    if not reviewer.strip():
        raise BrollAssetError("reviewer is required")
    root = Path(project_dir).expanduser().resolve()
    path = Path(str(asset.get("path") or "")).expanduser().resolve()
    if not _inside(path, (root / "broll" / "generated").resolve()) or not path.is_file():
        raise BrollAssetError("generated asset path is missing or outside the project")
    if _sha256(path) != asset.get("sha256"):
        raise BrollAssetError("generated asset changed after provenance was recorded")
    if asset.get("depicts_exact_item") is not False or asset.get("depiction_scope") == "exact_item":
        raise BrollAssetError("generated exact-item depictions cannot be approved")
    result = json.loads(json.dumps(asset))
    result["review"] = {
        "status": "approved" if approve else "rejected",
        "reviewer": reviewer.strip(),
        "reviewed_at": _now_iso(),
        "notes": notes,
        "checks": {
            "factual_claims": "reviewed",
            "must_not_show": "reviewed",
            "identity_confusion": "reviewed",
            "illustrative_disclosure_required": True,
        },
    }
    return result


def attach_asset_to_placements(
    placements: list[Mapping[str, Any]],
    asset: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if ((asset.get("review") or {}).get("status")) != "approved":
        raise BrollAssetError("generated asset needs visual approval before attachment")
    remotion_src = str(asset.get("remotion_src") or "").strip()
    if not remotion_src or Path(remotion_src).is_absolute() or "/" in remotion_src or "\\" in remotion_src:
        raise BrollAssetError("approved generated asset must be staged under project remotion-assets")
    candidate_id = str(asset.get("candidate_id") or "")
    output: list[dict[str, Any]] = []
    matched = False
    for raw in placements:
        row = json.loads(json.dumps(raw))
        if str(row.get("id") or row.get("beat_id") or "") == candidate_id:
            if row.get("visual_type") not in {"generated_image", "generated_illustration"}:
                raise BrollAssetError("generated asset target is not a generated-image placement")
            row["asset"] = {
                "src": remotion_src,
                "kind": "image",
                "origin": "generated",
                "exact_item": False,
                "sha256": asset["sha256"],
                "provenance_id": asset["asset_id"],
                "review_status": "approved",
            }
            row["status"] = "ready-with-approved-asset"
            matched = True
        output.append(row)
    if not matched:
        raise BrollAssetError(f"no placement found for generated candidate {candidate_id}")
    return output


def stage_approved_asset_for_remotion(
    asset: Mapping[str, Any],
    *,
    project_dir: str | Path,
) -> dict[str, Any]:
    """Copy one approved derivative into the renderer's local asset boundary.

    The generated original remains in ``broll/generated`` with immutable
    provenance. The staged copy is content-addressed and never overwrites a
    different file.
    """

    if ((asset.get("review") or {}).get("status")) != "approved":
        raise BrollAssetError("generated asset needs visual approval before staging")
    root = Path(project_dir).expanduser().resolve()
    source = Path(str(asset.get("path") or "")).expanduser().resolve()
    if not _inside(source, (root / "broll" / "generated").resolve()) or not source.is_file():
        raise BrollAssetError("generated asset path is missing or outside the project")
    digest = _sha256(source)
    if digest != asset.get("sha256"):
        raise BrollAssetError("generated asset changed after approval")
    safe_candidate = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in str(asset.get("candidate_id") or "generated")
    ).strip("-") or "generated"
    filename = f"{safe_candidate}-{digest[:16]}{source.suffix.casefold()}"
    destination_root = (root / "remotion-assets").resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / filename
    if destination.exists():
        if not destination.is_file() or _sha256(destination) != digest:
            raise BrollAssetError(f"staged asset collision: {destination}")
    else:
        temporary = destination.with_name(f".{destination.name}.pending")
        if temporary.exists():
            raise BrollAssetError(f"staged asset pending path already exists: {temporary}")
        shutil.copy2(source, temporary)
        if _sha256(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise BrollAssetError("staged asset hash differs after copy")
        temporary.replace(destination)
    result = json.loads(json.dumps(asset))
    result["remotion_src"] = filename
    result["staged_path"] = str(destination)
    result["staged_sha256"] = digest
    result["staged_at"] = _now_iso()
    return result


__all__ = [
    "ASSET_SCHEMA_VERSION",
    "BrollAssetError",
    "attach_asset_to_placements",
    "record_generated_asset",
    "review_generated_asset",
    "stage_approved_asset_for_remotion",
]
