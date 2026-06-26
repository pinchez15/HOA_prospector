"""
Florida Department of Revenue — Property Assessment Roll (NAL) Downloader.

Downloads free NAL (Name-Address-Legal) property assessment CSV data
from the FL DOR Data Portal. All 67 counties available.

Key fields for CAM prospecting:
  - JV (Just Value / Market Value)
  - AV_SD (Assessed Value, School District)
  - TOT_LVG_AREA (Total Living Area sqft)
  - NO_BULDNG (Number of Buildings)
  - NO_RES_UNTS (Number of Residential Units)
  - EFF_YR_BLT / ACT_YR_BLT (Year Built)
  - IMP_QUAL (Improvement Quality)
  - CONST_CLASS (Construction Class)
  - LND_VAL (Land Value)
  - SALE_PRC1 (Last Sale Price)
  - OWN_NAME (Owner Name — can match to HOA names)
  - PHY_ADDR1/PHY_CITY/PHY_ZIPCD (Physical Address)
  - DOR_UC (Dept of Revenue Use Code — identifies condos, common areas, etc.)

DOR Use Codes relevant to HOAs:
  04 = Condominium
  05 = Cooperatives
  08 = Multi-family (less than 10 units)
  09 = Multi-family (10+ units)
  38 = Common areas / recreation (HOA amenities)
  39 = Hotels/motels
  86 = Recreational / swimming pools

Source: https://floridarevenue.com/property/dataportal/Pages/default.aspx
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import zipfile
from pathlib import Path

import httpx

from config.settings import RAW_DIR, USER_AGENT

logger = logging.getLogger(__name__)

NAL_API_URL = (
    "https://floridarevenue.com/property/dataportal/_api/web/"
    "GetFolderByServerRelativeUrl('/property/dataportal/Documents/"
    "PTO%20Data%20Portal/Tax%20Roll%20Data%20Files/NAL/2025F')/Files"
)

BASE_DL_URL = (
    "https://floridarevenue.com/property/dataportal/Documents/"
    "PTO%20Data%20Portal/Tax%20Roll%20Data%20Files/NAL/2025F/"
)

# Columns we care about for enrichment
ENRICHMENT_COLS = [
    "CO_NO", "PARCEL_ID", "DOR_UC", "PA_UC",
    "JV", "AV_SD", "TV_SD", "LND_VAL",
    "TOT_LVG_AREA", "NO_BULDNG", "NO_RES_UNTS",
    "EFF_YR_BLT", "ACT_YR_BLT", "IMP_QUAL", "CONST_CLASS",
    "NCONST_VAL", "SPEC_FEAT_VAL",
    "SALE_PRC1", "SALE_YR1", "SALE_MO1",
    "OWN_NAME", "OWN_ADDR1", "OWN_CITY", "OWN_STATE", "OWN_ZIPCD",
    "PHY_ADDR1", "PHY_CITY", "PHY_ZIPCD",
    "S_LEGAL",
]

# DOR Use Codes relevant to HOA/condo/community properties
HOA_USE_CODES = {
    "004": "condominium",
    "005": "cooperative",
    "008": "multifamily_small",
    "009": "multifamily_large",
    "038": "common_area_recreation",
    "086": "recreational_swimming",
    "094": "right_of_way_common_element",
    "095": "common_element",
    "028": "parking_lot_mobile_home",
}

# Also match with 2-digit versions (some counties use different padding)
HOA_USE_CODES.update({
    "04": "condominium",
    "05": "cooperative",
    "08": "multifamily_small",
    "09": "multifamily_large",
    "38": "common_area_recreation",
    "86": "recreational_swimming",
    "94": "right_of_way_common_element",
    "95": "common_element",
    "28": "parking_lot",
})


async def list_available_counties() -> list[dict]:
    """List all available county NAL files from the FL DOR SharePoint API."""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        timeout=30,
    ) as client:
        resp = await client.get(NAL_API_URL)
        resp.raise_for_status()
        data = resp.json()

    files = data.get("value", [])
    counties = []
    for f in files:
        name = f.get("Name", "")
        if name.endswith(".zip"):
            # Parse county name and code from filename like "Baker 12 Final NAL 2025.zip"
            parts = name.replace(".zip", "").split()
            if len(parts) >= 2:
                # County name may be multi-word (e.g. "Saint Johns")
                code_idx = None
                for i, p in enumerate(parts):
                    if p.isdigit() and len(p) == 2:
                        code_idx = i
                        break
                if code_idx:
                    county_name = " ".join(parts[:code_idx])
                    county_code = parts[code_idx]
                    counties.append({
                        "name": county_name,
                        "code": county_code,
                        "filename": name,
                        "size": f.get("Length", 0),
                    })
    return counties


async def download_county_nal(county_filename: str, save_dir: Path | None = None) -> Path:
    """Download a county NAL ZIP file."""
    save_dir = save_dir or (RAW_DIR / "property")
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / county_filename

    if save_path.exists():
        logger.info(f"Already downloaded: {save_path}")
        return save_path

    url = BASE_DL_URL + county_filename.replace(" ", "%20")
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=120,
    ) as client:
        logger.info(f"Downloading {county_filename} ({url})")
        resp = await client.get(url)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)
        logger.info(f"Saved {save_path} ({len(resp.content)} bytes)")

    return save_path


def extract_hoa_parcels(zip_path: Path) -> list[dict]:
    """
    Extract HOA/condo-relevant parcels from a county NAL ZIP.

    Filters for parcels where:
      - DOR_UC is in HOA_USE_CODES (condos, common areas, pools, etc.)
      - OR owner name contains HOA-related keywords
    """
    hoa_keywords = [
        "homeowners", "homeowner", "home owners",
        "property owners", "condominium", "condo",
        "association", "assn", "master assoc",
        "hoa", "poa", "cooperative",
    ]

    parcels = []
    with zipfile.ZipFile(str(zip_path)) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            with z.open(name) as f:
                content = f.read().decode("utf-8", errors="replace")
                reader = csv.DictReader(io.StringIO(content))

                for row in reader:
                    dor_uc = (row.get("DOR_UC") or "").strip()
                    own_name = (row.get("OWN_NAME") or "").strip()
                    own_lower = own_name.lower()

                    is_hoa_use = dor_uc in HOA_USE_CODES
                    is_hoa_owner = any(kw in own_lower for kw in hoa_keywords)

                    if is_hoa_use or is_hoa_owner:
                        # Extract only the columns we care about
                        parcel = {}
                        for col in ENRICHMENT_COLS:
                            val = (row.get(col) or "").strip()
                            parcel[col] = val
                        parcel["use_code_desc"] = HOA_USE_CODES.get(dor_uc, "")
                        parcel["is_hoa_owner"] = is_hoa_owner
                        parcel["is_hoa_use_code"] = is_hoa_use
                        parcels.append(parcel)

    return parcels


def _safe_int(val: str) -> int | None:
    try:
        return int(val.replace(",", "")) if val else None
    except ValueError:
        return None


def _safe_float(val: str) -> float | None:
    try:
        return float(val.replace(",", "")) if val else None
    except ValueError:
        return None


def aggregate_by_community(parcels: list[dict]) -> list[dict]:
    """
    Aggregate parcel data by owner name (community/HOA).
    Returns enrichment records with:
      - total_units, total_buildings, avg_value, total_value
      - avg_living_area, avg_year_built
      - has_common_areas, has_pool
    """
    from collections import defaultdict

    communities: dict[str, list[dict]] = defaultdict(list)
    for p in parcels:
        owner = p.get("OWN_NAME", "").strip()
        if owner:
            communities[owner].append(p)

    results = []
    for name, parcel_list in communities.items():
        jvs = [_safe_int(p.get("JV", "")) for p in parcel_list]
        jvs = [v for v in jvs if v is not None and v > 0]

        areas = [_safe_int(p.get("TOT_LVG_AREA", "")) for p in parcel_list]
        areas = [a for a in areas if a is not None and a > 0]

        years = [_safe_int(p.get("EFF_YR_BLT") or p.get("ACT_YR_BLT", "")) for p in parcel_list]
        years = [y for y in years if y is not None and y > 1900]

        buildings = [_safe_int(p.get("NO_BULDNG", "")) for p in parcel_list]
        buildings = [b for b in buildings if b is not None]

        units = [_safe_int(p.get("NO_RES_UNTS", "")) for p in parcel_list]
        units = [u for u in units if u is not None]

        use_codes = set(p.get("DOR_UC", "") for p in parcel_list)
        common_area_codes = {"38", "038", "094", "94", "095", "95"}
        pool_rec_codes = {"86", "086"}
        has_common_areas = bool(use_codes & common_area_codes)
        has_pool = bool(use_codes & pool_rec_codes)

        # Get physical location from first parcel with address
        phy_addr = None
        phy_city = None
        phy_zip = None
        for p in parcel_list:
            if p.get("PHY_ADDR1"):
                phy_addr = p["PHY_ADDR1"]
                phy_city = p.get("PHY_CITY", "")
                phy_zip = p.get("PHY_ZIPCD", "")
                break

        sale_prices = [_safe_int(p.get("SALE_PRC1", "")) for p in parcel_list]
        sale_prices = [s for s in sale_prices if s is not None and s > 0]

        results.append({
            "owner_name": name,
            "total_parcels": len(parcel_list),
            "total_units": sum(units) if units else None,
            "total_buildings": sum(buildings) if buildings else None,
            "total_value": sum(jvs) if jvs else None,
            "avg_value": int(sum(jvs) / len(jvs)) if jvs else None,
            "median_value": sorted(jvs)[len(jvs) // 2] if jvs else None,
            "avg_living_area_sqft": int(sum(areas) / len(areas)) if areas else None,
            "avg_year_built": int(sum(years) / len(years)) if years else None,
            "oldest_year_built": min(years) if years else None,
            "has_common_areas": has_common_areas,
            "has_pool_or_recreation": has_pool,
            "use_codes": list(use_codes),
            "physical_address": phy_addr,
            "physical_city": phy_city,
            "physical_zip": phy_zip,
            "county_code": parcel_list[0].get("CO_NO", ""),
            "avg_sale_price": int(sum(sale_prices) / len(sale_prices)) if sale_prices else None,
            "max_sale_price": max(sale_prices) if sale_prices else None,
        })

    return results


async def run_property_enrichment(counties: list[str] | None = None) -> list[dict]:
    """
    Download NAL data for specified counties (or all) and extract
    HOA-relevant property data.
    """
    logger.info("Listing available FL DOR county NAL files")
    available = await list_available_counties()
    logger.info(f"Found {len(available)} counties")

    if counties:
        available = [c for c in available if c["name"] in counties]
        logger.info(f"Filtered to {len(available)} target counties")

    all_enrichment: list[dict] = []
    for county_info in available:
        try:
            zip_path = await download_county_nal(county_info["filename"])
            parcels = extract_hoa_parcels(zip_path)
            logger.info(
                f"{county_info['name']}: {len(parcels)} HOA-related parcels"
            )
            communities = aggregate_by_community(parcels)
            for c in communities:
                c["county_name"] = county_info["name"]
            all_enrichment.extend(communities)
        except Exception as e:
            logger.error(f"Error processing {county_info['name']}: {e}")

    logger.info(
        f"Property enrichment complete: {len(all_enrichment)} communities "
        f"across {len(available)} counties"
    )
    return all_enrichment


def export_property_data(enrichment: list[dict], filename: str = "property_enrichment.csv") -> Path:
    """Export property enrichment data to CSV."""
    from config.settings import OUTPUT_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename

    if not enrichment:
        return path

    cols = list(enrichment[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in enrichment:
            # Convert lists to strings for CSV
            csv_row = {}
            for k, v in row.items():
                if isinstance(v, list):
                    csv_row[k] = "; ".join(str(x) for x in v)
                elif isinstance(v, bool):
                    csv_row[k] = "yes" if v else "no"
                else:
                    csv_row[k] = v
            writer.writerow(csv_row)

    logger.info(f"Exported {len(enrichment)} property enrichment records to {path}")
    return path
