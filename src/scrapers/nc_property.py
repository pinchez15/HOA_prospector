"""
North Carolina County Property Data Scraper.

Downloads free bulk property data from NC county tax offices.
Mirrors the fl_property.py pattern: download bulk files, filter for
HOA-related parcels by owner name, aggregate by community.

Currently supported:
  - Wake County: https://services.wake.gov/realdata_extracts/

Wake County data is a fixed-width text file with parcel-level data
including owner name, assessed value, year built, square footage,
property class, and address.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import zipfile
from pathlib import Path

import httpx

from config.settings import RAW_DIR, USER_AGENT

logger = logging.getLogger(__name__)

WAKE_COUNTY_DATA_URL = "https://services.wake.gov/realdata_extracts/"

HOA_KEYWORDS = [
    "homeowners", "homeowner", "home owners",
    "property owners", "condominium", "condo",
    "association", "assn", "master assoc",
    "hoa", "poa", "cooperative",
    "community", "townhome", "townhouse",
]


async def _download_file(url: str, save_path: Path) -> Path:
    """Download a file if not already cached."""
    if save_path.exists():
        logger.info(f"Already downloaded: {save_path}")
        return save_path
    save_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=300,
    ) as client:
        logger.info(f"Downloading {url}")
        resp = await client.get(url)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)
        logger.info(f"Saved {save_path} ({len(resp.content):,} bytes)")
    return save_path


async def _list_wake_files() -> list[dict]:
    """List available files on Wake County data server."""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=15,
    ) as client:
        resp = await client.get(WAKE_COUNTY_DATA_URL)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "lxml")
    files = []
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        text = link.get_text(strip=True)
        if "RealEstData" in href and href.endswith(".txt"):
            files.append({"name": text, "url": WAKE_COUNTY_DATA_URL.rstrip("/") + "/" + href.split("/")[-1]})
    return files


def _is_hoa_owner(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in HOA_KEYWORDS)


def _parse_wake_txt(file_path: Path) -> list[dict]:
    """
    Parse Wake County RealEstData TXT file.
    This is a fixed-width text file. We read the first line to detect format,
    then parse accordingly.
    """
    logger.info(f"Parsing Wake County data from {file_path}")

    # Read and detect format
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline()

    # Check if tab-delimited or fixed-width
    if "\t" in first_line:
        return _parse_wake_tab_delimited(file_path)
    else:
        return _parse_wake_fixed_width(file_path)


def _parse_wake_tab_delimited(file_path: Path) -> list[dict]:
    """Parse tab-delimited Wake County data."""
    parcels = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            owner = (row.get("OWNER1") or row.get("OWNER") or "").strip()
            if not owner or not _is_hoa_owner(owner):
                continue
            parcels.append(_extract_wake_fields(row))
    return parcels


def _parse_wake_fixed_width(file_path: Path) -> list[dict]:
    """
    Parse fixed-width Wake County RealEstData file.
    Field positions derived from analysis of the actual data:
      0-69:   Owner name (70 chars)
      60-140: Mailing address area (varies)
      ~100-160: City, State, Zip
      ~280:   Acreage area
      ~300-320: Value area

    Since exact layout varies, we extract owner name (first 70 chars)
    and search for value/year patterns in the rest of the line.
    """
    import re

    parcels = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if len(line) < 200:
                continue

            # Owner name is first ~35 chars (2 fields of ~35 each for owner1/owner2)
            owner = line[:35].strip()
            owner2 = line[35:70].strip()
            full_owner = f"{owner} {owner2}".strip() if owner2 else owner

            # Quick keyword check on owner name only (not full line — much faster)
            owner_lower = full_owner.lower()
            if not any(kw in owner_lower for kw in HOA_KEYWORDS):
                continue

            # Extract address/city/state/zip from positions ~60-160
            addr_area = line[60:160].strip()

            # Try to extract numeric values from the line
            # Values tend to appear as large numbers in the line
            numbers = re.findall(r'\b(\d{4,10})\b', line[280:])
            value = None
            if numbers:
                # The largest number is likely the assessed value
                candidates = [int(n) for n in numbers if 1000 < int(n) < 999999999]
                if candidates:
                    value = max(candidates)

            # Year built — look for 4-digit year patterns (19xx or 20xx)
            year_match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', line[200:])
            year_built = year_match.group(1) if year_match else ""

            # Acreage pattern
            acre_match = re.search(r'(\d+\.\d{2})', line[260:300])
            acreage = acre_match.group(1) if acre_match else ""

            parcels.append({
                "owner_name": full_owner,
                "physical_address": addr_area[:80] if addr_area else "",
                "city": "",
                "state": "NC",
                "zip": "",
                "county": "Wake",
                "assessed_value": str(value) if value else "",
                "year_built": year_built,
                "heated_area": "",
                "units": "",
                "acreage": acreage,
                "sale_price": "",
                "parcel_id": "",
            })

    return parcels


def _extract_wake_fields(row: dict) -> dict:
    """Extract relevant fields from a Wake County data row."""
    # Wake County field names may vary — try common variants
    def get(keys):
        for k in keys:
            v = row.get(k, "")
            if v and str(v).strip() and str(v).strip() != "NULL":
                return str(v).strip()
        return ""

    return {
        "owner_name": get(["OWNER1", "OWNER", "OWNER_NAME"]),
        "owner2": get(["OWNER2", "OWNER_NAME2"]),
        "physical_address": get(["SITE_ADDRESS", "PHYSICAL_ADDR", "ADDR1"]),
        "city": get(["SITE_CITY", "CITY", "POSTAL_CITY"]),
        "state": "NC",
        "zip": get(["SITE_ZIP", "ZIP", "ZIP_CODE"]),
        "county": "Wake",
        "assessed_value": get(["TOTAL_VALUE_ASSD", "ASSESSED_VALUE", "TOTAL_ASSESSED", "ASSESSED_VAL"]),
        "land_value": get(["LAND_VALUE", "LAND_VAL"]),
        "building_value": get(["BLDG_VALUE", "BUILDING_VALUE", "IMPRV_VALUE"]),
        "market_value": get(["TOTAL_VALUE_ASSD", "MARKET_VALUE", "TOTAL_VALUE"]),
        "year_built": get(["YEAR_BUILT", "YR_BUILT", "YEAR_BLT"]),
        "heated_area": get(["HEATED_AREA", "SQFT", "TOTAL_SQ_FT", "TOT_LVG_AREA"]),
        "units": get(["UNITS", "NO_UNITS", "NUM_UNITS"]),
        "property_class": get(["PROPERTY_CLASS", "CLASS", "LAND_CLASS", "TYPE_USE"]),
        "acreage": get(["ACREAGE", "ACRES"]),
        "sale_price": get(["SALE_PRICE", "LAST_SALE_PRICE"]),
        "sale_date": get(["SALE_DATE", "LAST_SALE_DATE"]),
        "parcel_id": get(["REID", "PARCEL_ID", "PIN", "REA_REID"]),
    }


def _safe_int(val: str) -> int | None:
    if not val:
        return None
    try:
        return int(float(val.replace(",", "").replace("$", "")))
    except ValueError:
        return None


def aggregate_by_community(parcels: list[dict]) -> list[dict]:
    """Aggregate parcel data by owner name."""
    from collections import defaultdict

    communities: dict[str, list[dict]] = defaultdict(list)
    for p in parcels:
        owner = p.get("owner_name", "").strip()
        if owner:
            communities[owner].append(p)

    results = []
    for name, parcel_list in communities.items():
        values = [_safe_int(p.get("assessed_value") or p.get("market_value", "")) for p in parcel_list]
        values = [v for v in values if v and v > 0]

        areas = [_safe_int(p.get("heated_area", "")) for p in parcel_list]
        areas = [a for a in areas if a and a > 0]

        years = [_safe_int(p.get("year_built", "")) for p in parcel_list]
        years = [y for y in years if y and 1900 < y < 2030]

        units = [_safe_int(p.get("units", "")) for p in parcel_list]
        units = [u for u in units if u and u > 0]

        # Get address from first parcel with one
        addr = next((p.get("physical_address") for p in parcel_list if p.get("physical_address")), None)
        city = next((p.get("city") for p in parcel_list if p.get("city")), None)
        zipcode = next((p.get("zip") for p in parcel_list if p.get("zip")), None)

        sale_prices = [_safe_int(p.get("sale_price", "")) for p in parcel_list]
        sale_prices = [s for s in sale_prices if s and s > 0]

        results.append({
            "owner_name": name,
            "county_name": "Wake",
            "state": "NC",
            "total_parcels": len(parcel_list),
            "total_units": sum(units) if units else None,
            "total_value": sum(values) if values else None,
            "avg_value": int(sum(values) / len(values)) if values else None,
            "avg_living_area_sqft": int(sum(areas) / len(areas)) if areas else None,
            "avg_year_built": int(sum(years) / len(years)) if years else None,
            "physical_address": addr,
            "physical_city": city,
            "physical_zip": zipcode,
            "avg_sale_price": int(sum(sale_prices) / len(sale_prices)) if sale_prices else None,
        })

    return results


async def download_wake_county() -> Path:
    """Download the latest Wake County real estate data file."""
    files = await _list_wake_files()
    if not files:
        raise RuntimeError("No Wake County data files found")

    # Use the most recent txt file
    latest = files[-1]
    save_path = RAW_DIR / "property" / f"wake_county_{latest['name']}"
    return await _download_file(latest["url"], save_path)


async def run_nc_property_enrichment(counties: list[str] | None = None) -> list[dict]:
    """Download and process NC county property data."""
    target_counties = counties or ["Wake"]
    all_enrichment: list[dict] = []

    for county in target_counties:
        if county == "Wake":
            try:
                file_path = await download_wake_county()
                parcels = _parse_wake_txt(file_path)
                logger.info(f"Wake County: {len(parcels)} HOA-related parcels")
                communities = aggregate_by_community(parcels)
                all_enrichment.extend(communities)
                logger.info(f"Wake County: {len(communities)} communities after aggregation")
            except Exception as e:
                logger.error(f"Error processing Wake County: {e}")
        else:
            logger.warning(f"County '{county}' not yet supported for NC property data")

    logger.info(f"NC property enrichment: {len(all_enrichment)} communities total")
    return all_enrichment


def export_nc_property_data(enrichment: list[dict], filename: str = "nc_property_enrichment.csv") -> Path:
    """Export NC property enrichment to CSV."""
    from config.settings import OUTPUT_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename

    if not enrichment:
        return path

    cols = list(enrichment[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(enrichment)

    logger.info(f"Exported {len(enrichment)} NC property records to {path}")
    return path
