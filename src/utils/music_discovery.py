"""Source-safe, license-aware music discovery for production pipelines.

The module deliberately separates discovery, human license verification, and
download.  Search results are suggestions, never an assertion that a track is
safe for a particular production.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence


OPENVERSE_AUDIO_ENDPOINT = "https://api.openverse.org/v1/audio/"
OPENVERSE_CONSUMER_DOCS = "https://api.openverse.org/v1/"
OPENVERSE_TERMS = "https://docs.openverse.org/terms_of_service.html"
SCHEMA_VERSION = "dvr.music-options.v1"
SELECTION_SCHEMA_VERSION = "dvr.music-selection.v1"

LICENSE_PROFILES: dict[str, tuple[str, ...]] = {
    # Keep the business-video default deliberately conservative. CC BY-SA is
    # available through an explicit profile because ShareAlike implications
    # deserve project-specific review.
    "commercial-safe": ("cc0", "pdm", "by"),
    "public-domain": ("cc0", "pdm"),
    "commercial-with-sharealike": ("cc0", "pdm", "by", "by-sa"),
    "all-open": (
        "cc0",
        "pdm",
        "by",
        "by-sa",
        "by-nd",
        "by-nc",
        "by-nc-sa",
        "by-nc-nd",
        "sampling+",
        "nc-sampling+",
    ),
}


class MusicDiscoveryError(RuntimeError):
    """Raised when music discovery input or a provider response is invalid."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_terms(values: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values or ():
        for part in str(value).split(","):
            term = " ".join(part.strip().split())
            key = term.casefold()
            if term and key not in seen:
                seen.add(key)
                output.append(term)
    return output


def resolve_licenses(profile: str, licenses: Iterable[str] | None = None) -> list[str]:
    explicit = [value.casefold() for value in _clean_terms(licenses)]
    if explicit:
        unknown = sorted(set(explicit) - set(LICENSE_PROFILES["all-open"]))
        if unknown:
            raise MusicDiscoveryError(f"unsupported Openverse license(s): {', '.join(unknown)}")
        return explicit
    if profile not in LICENSE_PROFILES:
        raise MusicDiscoveryError(f"unknown license profile: {profile}")
    return list(LICENSE_PROFILES[profile])


def build_search_queries(
    *,
    categories: Iterable[str] | None = None,
    moods: Iterable[str] | None = None,
    genres: Iterable[str] | None = None,
    instruments: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
    energy: str | None = None,
    instrumental_only: bool = True,
) -> list[str]:
    """Build broad, diverse queries instead of one over-constrained query.

    Openverse is a media search index, not a faceted production-music catalog.
    Treating every editorial adjective as mandatory often yields no results.
    We therefore try one combined intent followed by round-robin single-facet
    searches, and reserve one slot for a broad instrumental fallback.
    """

    categories = _clean_terms(categories)
    moods = _clean_terms(moods)
    genres = _clean_terms(genres)
    instruments = _clean_terms(instruments)
    keywords = _clean_terms(keywords)
    energy_terms = _clean_terms([energy] if energy else [])
    instrumental = ["instrumental"] if instrumental_only else []
    # Openverse indexes musical descriptors more reliably than editorial
    # production categories, so spend the bounded anonymous-query budget on
    # genre/mood/instrument/keyword terms before listing categories.
    groups = [genres, moods, instruments, keywords, categories]
    combined = [group[0] for group in groups if group]
    candidates: list[list[str]] = []
    if combined:
        candidates.append(combined + energy_terms + instrumental)

    max_depth = max((len(group) for group in groups), default=0)
    for index in range(max_depth):
        for group in groups:
            if index < len(group):
                # Do not force editorial energy onto every provider query.
                # Openverse typically represents it as a tag (for example
                # speed_medium), so it remains a ranking signal below.
                candidates.append([group[index], *instrumental])

    # Keep a useful baseline even when a listing-specific term (for example,
    # "automotive") is absent from the Openverse index.
    candidates.append(["cinematic", *instrumental] if instrumental_only else ["cinematic music"])

    queries: list[str] = []
    seen: set[str] = set()
    for terms in candidates:
        # Openverse limits q to 200 characters.
        query = " ".join(_clean_terms(terms))[:200].strip()
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            queries.append(query)
    fallback = "cinematic instrumental" if instrumental_only else "cinematic music"
    queries = [query for query in queries if query.casefold() != fallback.casefold()]
    return [*queries[:7], fallback]


def _fetch_json(url: str, *, timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "davinci-resolve-cli (license-aware music discovery)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        suffix = f"; retry after {retry_after} seconds" if retry_after else ""
        raise MusicDiscoveryError(f"Openverse returned HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, socket.timeout, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MusicDiscoveryError(f"Openverse request failed: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise MusicDiscoveryError("Openverse returned a non-object response")
    return payload


def _tag_names(value: Any) -> list[str]:
    output: list[str] = []
    for item in value if isinstance(value, list) else []:
        name = item.get("name") if isinstance(item, Mapping) else item
        if isinstance(name, str) and name.strip():
            output.append(name.strip())
    return output


def _stable_jitter(seed: str, track_id: str) -> float:
    digest = hashlib.sha256(f"{seed}\0{track_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _json_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_track(
    item: Mapping[str, Any],
    *,
    requested_terms: Sequence[str],
    instrumental_only: bool,
    min_duration: float | None,
    max_duration: float | None,
    target_duration: float | None,
    seed: str,
) -> dict[str, Any] | None:
    track_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "Untitled").strip()
    audio_url = str(item.get("url") or "").strip()
    landing_url = str(item.get("foreign_landing_url") or "").strip()
    license_code = str(item.get("license") or "").casefold().strip()
    category = str(item.get("category") or "").casefold().strip()
    if (
        not track_id
        or category != "music"
        or not audio_url.startswith("https://")
        or not landing_url.startswith("https://")
    ):
        return None
    if item.get("mature") is True:
        return None

    duration_raw = item.get("duration")
    duration_seconds = None
    if isinstance(duration_raw, (int, float)) and math.isfinite(float(duration_raw)) and duration_raw >= 0:
        # Openverse documents audio duration in milliseconds.
        duration_seconds = round(float(duration_raw) / 1000.0, 3)
    if duration_seconds is None and (min_duration is not None or max_duration is not None):
        return None
    if duration_seconds is not None:
        if min_duration is not None and duration_seconds < min_duration:
            return None
        if max_duration is not None and duration_seconds > max_duration:
            return None

    tags = _tag_names(item.get("tags"))
    genres = [str(value).strip() for value in item.get("genres") or [] if str(value).strip()]
    searchable = " ".join([title, str(item.get("creator") or ""), *tags, *genres]).casefold()
    score = 0.0
    matched_terms: list[str] = []
    for term in requested_terms:
        if term.casefold() in searchable:
            matched_terms.append(term)
            score += 1.5
    instrumental_signal = "unknown"
    if instrumental_only:
        if "instrumental" in searchable:
            instrumental_signal = "metadata_match"
            score += 4.0
        if "vocal" in searchable or "lyrics" in searchable:
            return None
    if license_code in {"cc0", "pdm"}:
        score += 2.0
    elif license_code == "by":
        score += 1.0
    if target_duration and duration_seconds is not None:
        relative_error = abs(duration_seconds - target_duration) / max(target_duration, 1.0)
        score += max(0.0, 2.0 - relative_error * 2.0)
    score += _stable_jitter(seed, track_id) * 0.25

    return {
        "track_id": track_id,
        "title": title,
        "creator": item.get("creator"),
        "creator_url": item.get("creator_url"),
        "duration_seconds": duration_seconds,
        "genres": genres,
        "tags": tags,
        "instrumental_signal": instrumental_signal if instrumental_only else "not_requested",
        "source_page": landing_url,
        "audio_file": {
            "url": audio_url,
            "filetype": item.get("filetype"),
            "filesize_bytes": item.get("filesize"),
            "bit_rate": item.get("bit_rate"),
            "sample_rate": item.get("sample_rate"),
        },
        "license": {
            "code": license_code,
            "version": item.get("license_version"),
            "url": item.get("license_url"),
            "attribution": item.get("attribution"),
            "requires_attribution": license_code not in {"cc0", "pdm"},
            "reported_by": "Openverse aggregated metadata",
            "verification_required": True,
        },
        "provenance": {
            "openverse_id": track_id,
            "openverse_record": f"{OPENVERSE_AUDIO_ENDPOINT}{urllib.parse.quote(track_id)}/",
            "provider": item.get("provider"),
            "source": item.get("source"),
            "foreign_identifier": item.get("foreign_identifier"),
            "last_synced_with_source": item.get("last_synced_with_source"),
        },
        "matched_terms": matched_terms,
        "score": round(score, 4),
    }


def search_openverse(
    *,
    categories: Iterable[str] | None = None,
    moods: Iterable[str] | None = None,
    genres: Iterable[str] | None = None,
    instruments: Iterable[str] | None = None,
    keywords: Iterable[str] | None = None,
    energy: str | None = None,
    instrumental_only: bool = True,
    license_profile: str = "commercial-safe",
    licenses: Iterable[str] | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    target_duration: float | None = None,
    limit: int = 12,
    per_query: int = 20,
    seed: str | None = None,
    timeout: float = 20.0,
    fetch_json: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search Openverse and return a normalized, ranked option manifest."""

    if limit < 1 or limit > 50:
        raise MusicDiscoveryError("limit must be between 1 and 50")
    # This implementation is intentionally credential-free. Openverse caps
    # anonymous page_size at 20 (authenticated clients may request more).
    if per_query < 1 or per_query > 20:
        raise MusicDiscoveryError("per_query must be between 1 and 20 for anonymous Openverse requests")
    for label, value in (
        ("min_duration", min_duration),
        ("max_duration", max_duration),
        ("target_duration", target_duration),
        ("timeout", timeout),
    ):
        if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
            raise MusicDiscoveryError(f"{label} must be a finite non-negative number")
    if timeout <= 0:
        raise MusicDiscoveryError("timeout must be greater than zero")
    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        raise MusicDiscoveryError("min_duration cannot exceed max_duration")
    resolved_licenses = resolve_licenses(license_profile, licenses)
    query_seed = seed or secrets.token_hex(16)
    queries = build_search_queries(
        categories=categories,
        moods=moods,
        genres=genres,
        instruments=instruments,
        keywords=keywords,
        energy=energy,
        instrumental_only=instrumental_only,
    )
    facets = {
        "categories": _clean_terms(categories),
        "moods": _clean_terms(moods),
        "genres": _clean_terms(genres),
        "instruments": _clean_terms(instruments),
        "keywords": _clean_terms(keywords),
        "energy": energy,
        "instrumental_only": instrumental_only,
    }
    requested_terms = _clean_terms(
        [*facets["categories"], *facets["moods"], *facets["genres"], *facets["instruments"], *facets["keywords"], energy or ""]
    )
    fetch = fetch_json or _fetch_json
    tracks_by_id: dict[str, dict[str, Any]] = {}
    provider_requests: list[dict[str, Any]] = []
    for query in queries:
        params = {
            "q": query,
            "category": "music",
            "license": ",".join(resolved_licenses),
            "filter_dead": "true",
            "mature": "false",
            "page_size": str(per_query),
        }
        url = f"{OPENVERSE_AUDIO_ENDPOINT}?{urllib.parse.urlencode(params)}"
        response = fetch(url, timeout=timeout)
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise MusicDiscoveryError("Openverse response is missing results")
        provider_requests.append({
            "query": query,
            "url": url,
            "result_count": response.get("result_count"),
            "returned_count": len(raw_results),
        })
        for item in raw_results:
            if not isinstance(item, Mapping):
                continue
            track = _normalize_track(
                item,
                requested_terms=requested_terms,
                instrumental_only=instrumental_only,
                min_duration=min_duration,
                max_duration=max_duration,
                target_duration=target_duration,
                seed=query_seed,
            )
            if track is None or track["license"]["code"] not in resolved_licenses:
                continue
            current = tracks_by_id.get(track["track_id"])
            if current is None:
                track["discovered_by_queries"] = [query]
                tracks_by_id[track["track_id"]] = track
            else:
                current.setdefault("discovered_by_queries", []).append(query)
                current["matched_terms"] = _clean_terms([
                    *current.get("matched_terms", []),
                    *track.get("matched_terms", []),
                ])
                if track["score"] > current["score"]:
                    current["score"] = track["score"]

    ranked = sorted(
        tracks_by_id.values(),
        key=lambda row: (-float(row["score"]), str(row["title"]).casefold(), row["track_id"]),
    )[:limit]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "provider": "openverse",
        "provider_notice": "Music discovery metadata provided by Openverse; this CLI is not endorsed or certified by Openverse.",
        "provider_documentation": OPENVERSE_CONSUMER_DOCS,
        "provider_terms": OPENVERSE_TERMS,
        "seed": query_seed,
        "facets": facets,
        "constraints": {
            "license_profile": license_profile,
            "licenses": resolved_licenses,
            "min_duration_seconds": min_duration,
            "max_duration_seconds": max_duration,
            "target_duration_seconds": target_duration,
        },
        "requests": provider_requests,
        "option_count": len(ranked),
        "options": ranked,
        "review": {
            "status": "license_verification_required",
            "note": (
                "Openverse aggregates license metadata and does not verify it. "
                "Open the source page and confirm the current license, attribution, creator, and intended commercial use "
                "before obtaining the file or attaching it to an edit. Instrumental-only is a search signal, not audio QC."
            ),
            "automatic_download": False,
            "automatic_import": False,
        },
    }


def select_option(
    manifest: Mapping[str, Any],
    track_id: str,
    *,
    confirm_source_license: bool = False,
    reviewer: str | None = None,
    verified_source_page: str | None = None,
    verified_license_code: str | None = None,
    verification_notes: str | None = None,
) -> dict[str, Any]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise MusicDiscoveryError("unsupported music options manifest")
    selected = next(
        (row for row in manifest.get("options") or [] if isinstance(row, Mapping) and row.get("track_id") == track_id),
        None,
    )
    if selected is None:
        raise MusicDiscoveryError(f"track not found in options: {track_id}")
    source_page = str(selected.get("source_page") or "")
    reported_license = str((selected.get("license") or {}).get("code") or "").casefold()
    if confirm_source_license:
        if not reviewer or not reviewer.strip():
            raise MusicDiscoveryError("--reviewer is required when confirming a source-page license")
        if not source_page:
            raise MusicDiscoveryError("cannot confirm a track without a source page")
        if verified_source_page != source_page:
            raise MusicDiscoveryError("verified source page must exactly match the selected option's source_page")
        if str(verified_license_code or "").casefold() != reported_license:
            raise MusicDiscoveryError("verified license code must exactly match the selected option's reported license")
    status = "license_verified_for_reviewed_use" if confirm_source_license else "needs_license_verification"
    selection = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selected_at": _now_iso(),
        "options_seed": manifest.get("seed"),
        "options_manifest_sha256": _json_sha256(manifest),
        "status": status,
        "track": dict(selected),
        "license_review": {
            "confirmed_at_source": bool(confirm_source_license),
            "reviewer": reviewer.strip() if reviewer else None,
            "reviewed_at": _now_iso() if confirm_source_license else None,
            "source_page": source_page or None,
            "verified_license_code": verified_license_code.casefold() if verified_license_code else None,
            "attribution": (selected.get("license") or {}).get("attribution"),
            "notes": verification_notes,
        },
        "next_step": (
            "User may explicitly obtain the file and attach it after separate audio/rights review."
            if confirm_source_license
            else "Open the source_page and verify the current license and attribution; selection does not download or import."
        ),
    }
    return selection
