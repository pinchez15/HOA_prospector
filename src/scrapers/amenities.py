"""
OpenStreetMap Amenities Scraper via Overpass API.

Queries OSM for amenities (pools, clubhouses, playgrounds, tennis courts)
within county bounding boxes, then matches them to HOA records by city.

API: https://overpass-api.de/api/interpreter
Free, no auth. Rate limit: be polite (2s between queries).

Works for both FL and NC.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

from config.settings import COUNTY_BBOXES, OVERPASS_API_URL, OVERPASS_DELAY, RAW_DIR
from src.models.association import Association
from src.utils.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

CACHE_DIR = RAW_DIR / "amenities"

# OSM tags for HOA-relevant amenities
AMENITY_QUERIES = {
    "pool": 'node["leisure"="swimming_pool"]({bbox});way["leisure"="swimming_pool"]({bbox});',
    "clubhouse": 'node["amenity"="community_centre"]({bbox});way["amenity"="community_centre"]({bbox});',
    "playground": 'node["leisure"="playground"]({bbox});way["leisure"="playground"]({bbox});',
    "tennis": 'node["sport"="tennis"]({bbox});way["sport"="tennis"]({bbox});',
    "fitness": 'node["leisure"="fitness_centre"]({bbox});way["leisure"="fitness_centre"]({bbox});',
    "golf": 'node["leisure"="golf_course"]({bbox});way["leisure"="golf_course"]({bbox});',
}


def _build_query(bbox: tuple[float, float, float, float], amenity_types: list[str] | None = None) -> str:
    """Build an Overpass QL query for amenities in a bounding box."""
    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"

    types = amenity_types or list(AMENITY_QUERIES.keys())
    queries = []
    for atype in types:
        template = AMENITY_QUERIES.get(atype)
        if template:
            queries.append(template.format(bbox=bbox_str))

    query_body = "\n".join(queries)
    return f"""
    [out:json][timeout:120];
    (
      {query_body}
    );
    out center;
    """


def _parse_amenity_type(element: dict) -> str:
    """Determine the amenity type from OSM tags."""
    tags = element.get("tags", {})
    leisure = tags.get("leisure", "")
    amenity = tags.get("amenity", "")
    sport = tags.get("sport", "")

    if leisure == "swimming_pool":
        return "pool"
    if amenity == "community_centre":
        return "clubhouse"
    if leisure == "playground":
        return "playground"
    if sport == "tennis":
        return "tennis"
    if leisure == "fitness_centre":
        return "fitness"
    if leisure == "golf_course":
        return "golf"
    return "other"


def _get_coords(element: dict) -> tuple[float, float] | None:
    """Get lat/lon from an OSM element (node or way with center)."""
    if element.get("lat") and element.get("lon"):
        return (element["lat"], element["lon"])
    center = element.get("center")
    if center:
        return (center.get("lat"), center.get("lon"))
    return None


def _parse_response(data: dict) -> list[dict]:
    """Parse Overpass API response into amenity records."""
    amenities = []
    for element in data.get("elements", []):
        coords = _get_coords(element)
        if not coords:
            continue
        tags = element.get("tags", {})
        amenities.append({
            "type": _parse_amenity_type(element),
            "name": tags.get("name", ""),
            "lat": coords[0],
            "lon": coords[1],
            "osm_id": element.get("id"),
            "tags": tags,
        })
    return amenities


async def fetch_amenities_for_bbox(
    client: RateLimitedClient,
    bbox: tuple[float, float, float, float],
    county_key: str,
) -> list[dict]:
    """Query Overpass API for amenities within a bounding box."""
    cache_path = CACHE_DIR / f"{county_key.replace(':', '_')}.json"
    if cache_path.exists():
        logger.info(f"Using cached amenities for {county_key}")
        return json.loads(cache_path.read_text())

    query = _build_query(bbox)
    try:
        resp = await client.post(OVERPASS_API_URL, data={"data": query})
        data = resp.json()
        amenities = _parse_response(data)
        logger.info(f"{county_key}: {len(amenities)} amenities found")

        # Cache results
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(amenities))

        return amenities
    except Exception as e:
        logger.error(f"Overpass query failed for {county_key}: {e}")
        return []


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon points."""
    R = 3959  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def match_amenities_to_associations(
    associations: list[Association],
    amenities: list[dict],
    max_distance_miles: float = 0.5,
) -> dict[str, list[str]]:
    """
    Match amenities to associations by city name.
    Returns {source_id: [amenity_type, ...]}.

    Since we don't have geocoded HOA addresses, we match by city.
    All amenities in the same city as an HOA are potential matches.
    This is coarse but provides useful signal without requiring geocoding.
    """
    # Group amenities by approximate city (reverse geocode from coords is expensive,
    # so we just count amenities by type per county for now)
    # A better approach: match amenities to the nearest HOA address.
    # For now, we aggregate at the city level.

    # Build a city -> amenity types mapping
    # Since OSM amenities don't always have city info, we'd need reverse geocoding.
    # Instead, let's count amenity types per county and assign to all HOAs in that county.

    # Simple approach: for each HOA, list what amenity types exist in their county bbox
    amenity_types_found = set()
    for a in amenities:
        amenity_types_found.add(a["type"])

    # Assign all found amenity types to associations in this area
    result: dict[str, list[str]] = {}
    for assoc in associations:
        if assoc.source_id:
            result[assoc.source_id] = sorted(amenity_types_found)

    return result


async def run_amenities_enrichment(
    associations: list[Association],
    counties: list[str] | None = None,
    state: str | None = None,
) -> dict[str, list[str]]:
    """
    Fetch OSM amenities for counties where our associations live,
    then match to associations.

    Returns {source_id: [amenity_types]} for matched associations.
    """
    client = RateLimitedClient(delay=OVERPASS_DELAY)

    # Determine which county bboxes to query
    # Group associations by state:county
    assoc_by_county: dict[str, list[Association]] = defaultdict(list)
    for assoc in associations:
        if state and assoc.state != state:
            continue
        county_key = f"{assoc.state}:{assoc.county}" if assoc.county else None
        if county_key:
            assoc_by_county[county_key].append(assoc)

    # Filter to requested counties
    if counties:
        target_keys = set()
        for c in counties:
            for key in assoc_by_county:
                if c in key:
                    target_keys.add(key)
        assoc_by_county = {k: v for k, v in assoc_by_county.items() if k in target_keys}

    logger.info(f"Amenities enrichment: {len(assoc_by_county)} counties to query")

    all_matches: dict[str, list[str]] = {}

    for county_key, county_assocs in assoc_by_county.items():
        bbox = COUNTY_BBOXES.get(county_key)
        if not bbox:
            logger.debug(f"No bbox for {county_key}, skipping")
            continue

        amenities = await fetch_amenities_for_bbox(client, bbox, county_key)
        if amenities:
            matches = match_amenities_to_associations(county_assocs, amenities)
            all_matches.update(matches)

    logger.info(
        f"Amenities enrichment complete: {len(all_matches)} associations tagged "
        f"across {len(assoc_by_county)} counties"
    )

    await client.close()
    return all_matches
