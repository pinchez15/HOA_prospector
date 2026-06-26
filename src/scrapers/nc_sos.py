"""
North Carolina Secretary of State — Nonprofit Reports Downloader.

NC SOS provides free nonprofit listings by county at:
  https://www.sosnc.gov/online_services/business_registration/Non_Profit_Reports

The download process:
  1. GET the reports page to get an anti-forgery token and link IDs
  2. POST to /online_services/imaging/download_ivault_file with link ID
  3. GET the file from /imaging/dime/{date}/{filename}.txt

The files are tab-delimited text with columns:
  EName, Address, Address2, City, State, Zip, CountyName

We filter for HOA-related names using keyword matching.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from config.settings import RAW_DIR, USER_AGENT
from src.models.association import (
    Association,
    CommunityType,
    FilingStatus,
    ManagementStatus,
)

logger = logging.getLogger(__name__)

REPORTS_URL = "https://www.sosnc.gov/online_services/business_registration/Non_Profit_Reports"
DOWNLOAD_AJAX_URL = "https://www.sosnc.gov/online_services/imaging/download_ivault_file"
BASE_URL = "https://www.sosnc.gov"

# Keywords indicating an HOA/community association in the entity name
HOA_KEYWORDS = [
    "homeowners", "homeowner", "home owners",
    "property owners", "property owner",
    "condominium owners", "condominium association", "condo association",
    "community association",
    "townhome association", "townhouse association",
    "cooperative association",
    "owners association",
    "master association",
    "neighborhood association",
    " hoa", "hoa ",
    " poa", "poa ",
]


def _is_hoa_name(name: str) -> bool:
    """Check if an entity name looks like an HOA/community association."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in HOA_KEYWORDS)


def _classify_nc_type(name: str) -> CommunityType:
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("condo", "condominium")):
        return CommunityType.CONDO
    if any(kw in name_lower for kw in ("townhome", "townhouse")):
        return CommunityType.TOWNHOME
    if any(kw in name_lower for kw in ("cooperative", "co-operative")):
        return CommunityType.COOPERATIVE
    return CommunityType.HOA


def _get_page_and_token(client: httpx.Client) -> tuple[BeautifulSoup, str, str]:
    """Fetch the reports page and extract the anti-forgery token and dime path."""
    resp = client.get(REPORTS_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # Anti-forgery token
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    token = token_input.get("value", "") if token_input else ""

    # Imaging_Dime path (changes daily)
    dime_match = re.search(r"Imaging_Dime\s*=\s*'([^']+)'", resp.text)
    dime_path = dime_match.group(1) if dime_match else "/imaging/dime/"

    return soup, token, dime_path


def _get_county_ids(soup: BeautifulSoup) -> dict[str, str]:
    """Extract county name -> link ID mapping from the page."""
    county_ids = {}
    links = soup.find_all("a", onclick=lambda x: x and "FileClick" in x)
    for link in links:
        text = link.get_text(strip=True)
        link_id = link.get("id", "")
        if "(Text)" in text:
            county_name = text.replace("(Text)", "").strip()
            county_ids[county_name] = link_id
    return county_ids


def _download_county_file(
    client: httpx.Client, county_id: str, token: str, dime_path: str
) -> str | None:
    """Download a single county's nonprofit file. Returns file content or None."""
    # Step 1: POST to get the filename
    resp = client.post(
        DOWNLOAD_AJAX_URL,
        data={"ID": county_id},
        headers={
            "RequestVerificationToken": token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": REPORTS_URL,
        },
    )
    if resp.status_code != 200:
        return None

    data = resp.json()
    if not data.get("ok") or not data.get("fileName"):
        return None

    filename = data["fileName"].replace("|", ".")

    # Step 2: Download the actual file
    file_url = f"{BASE_URL}{dime_path}{filename}"
    file_resp = client.get(file_url)
    if file_resp.status_code != 200:
        return None

    return file_resp.text


def _parse_nonprofit_file(content: str, county: str) -> list[Association]:
    """Parse a tab-delimited NC nonprofit file and filter for HOA-related entities."""
    associations = []
    reader = csv.DictReader(io.StringIO(content), delimiter="\t")

    for row in reader:
        name = (row.get("EName") or "").strip().strip('"')
        if not name:
            continue

        # Filter for HOA-related names only
        if not _is_hoa_name(name):
            continue

        addr1 = (row.get("Address") or "").strip()
        addr2 = (row.get("Address2") or "").strip()
        city = (row.get("City") or "").strip()
        state = (row.get("State") or "").strip()
        zipcode = (row.get("Zip") or "").strip()

        # Extract county from data (CountyName column) if available
        row_county = (row.get("CountyName") or county).strip()

        address_parts = [p for p in [addr1, addr2, city, state, zipcode] if p]
        physical_address = ", ".join(address_parts) if address_parts else None

        assoc = Association(
            community_name=name,
            source="nc_sos",
            source_id=f"nc_{row_county}_{name[:50]}",
            state="NC",
            county=row_county,
            physical_address=physical_address,
            mailing_address=physical_address,
            community_type=_classify_nc_type(name),
            filing_status=FilingStatus.ACTIVE,  # Only active nonprofits are in the listing
            management_status=ManagementStatus.UNKNOWN,
            raw_data={
                "ename": name,
                "address": addr1,
                "address2": addr2,
                "city": city,
                "state": state,
                "zip": zipcode,
                "county": row_county,
            },
        )
        associations.append(assoc)

    return associations


async def download_all_counties(counties: list[str] | None = None) -> list[Association]:
    """
    Download nonprofit reports for all (or specified) NC counties
    and filter for HOA-related entities.
    """
    client = httpx.Client(
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        logger.info("Fetching NC SOS nonprofit reports page")
        soup, token, dime_path = _get_page_and_token(client)
        county_ids = _get_county_ids(soup)
        logger.info(f"Found {len(county_ids)} counties on NC SOS")

        # If "All" is available and no specific counties requested, use it
        if counties is None and "All" in county_ids:
            logger.info("Downloading ALL counties in one file")
            content = _download_county_file(client, county_ids["All"], token, dime_path)
            if content:
                save_path = RAW_DIR / "nc_all_nonprofits.txt"
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(content, encoding="utf-8")
                logger.info(f"Saved {save_path} ({len(content)} bytes)")

                # Parse all and split by county
                all_assocs = _parse_nonprofit_file(content, "ALL")
                # Fix county from the data
                for assoc in all_assocs:
                    if assoc.raw_data and assoc.raw_data.get("county"):
                        assoc.county = assoc.raw_data["county"]

                logger.info(f"Parsed {len(all_assocs)} HOA-related entities from NC SOS (all counties)")
                return all_assocs

        # Otherwise download county by county
        target_counties = counties or list(county_ids.keys())
        all_assocs: list[Association] = []

        for county_name in target_counties:
            if county_name not in county_ids:
                logger.warning(f"County '{county_name}' not found in NC SOS")
                continue

            logger.info(f"Downloading NC SOS nonprofits for {county_name} County")
            content = _download_county_file(client, county_ids[county_name], token, dime_path)
            if content:
                save_path = RAW_DIR / f"nc_{county_name.lower().replace(' ', '_')}_nonprofits.txt"
                save_path.write_text(content, encoding="utf-8")

                assocs = _parse_nonprofit_file(content, county_name)
                logger.info(f"  {county_name}: {len(assocs)} HOA-related entities")
                all_assocs.extend(assocs)

                # Be polite
                await asyncio.sleep(1.0)

        logger.info(f"NC SOS download complete: {len(all_assocs)} HOA-related entities")
        return all_assocs

    finally:
        client.close()


async def run_nc_sos_scrape(
    fetch_details: bool = True, max_details: int | None = None
) -> list[Association]:
    """Main entry point for the NC SOS scraper."""
    return await download_all_counties()
