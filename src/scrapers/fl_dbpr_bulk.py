"""
Florida DBPR Bulk CSV Downloader & Parser.

Downloads official CSV extracts from DBPR's public records page:
  - Condo associations (5 regional files)
  - Cooperatives
  - CAM licensees (management companies)
  - Developer summary
  - County summary

These are the richest, most reliable data — direct government bulk exports.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime
from pathlib import Path

import httpx

from config.settings import RAW_DIR, USER_AGENT
from src.models.association import (
    Association,
    CommunityType,
    FilingStatus,
    ManagementStatus,
)

logger = logging.getLogger(__name__)

# All known DBPR bulk CSV download URLs
CONDO_CSVS = {
    "north_florida": "https://www2.myfloridalicense.com/sto/file_download/extracts/Condo_NF.csv",
    "central_east": "https://www2.myfloridalicense.com/sto/file_download/extracts/condo_CE.csv",
    "central_west": "https://www2.myfloridalicense.com/sto/file_download/extracts/Condo_CW.csv",
    "dade_monroe": "https://www2.myfloridalicense.com/sto/file_download/extracts/Condo_MD.csv",
    "broward_palm_beach": "https://www2.myfloridalicense.com/sto/file_download/extracts/condo_PB.csv",
}

COOP_CSV = "https://www2.myfloridalicense.com/sto/file_download/extracts/coopmailing.csv"

CAM_LICENSEES_CSV = "https://www2.myfloridalicense.com/sto/file_download/extracts/lic38cam.csv"

DEVELOPER_SUMMARY_CSV = (
    "https://www2.myfloridalicense.com/sto/file_download/extracts/developersummary.csv"
)

COUNTY_SUMMARY_CSV = (
    "https://www2.myfloridalicense.com/sto/file_download/extracts/countysummary.csv"
)

PAYMENT_HISTORY_CSVS = {
    "condo_A-C": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8002A.csv",
    "condo_D-I": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8002D.csv",
    "condo_J-O": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8002J.csv",
    "condo_P-R": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8002P.csv",
    "condo_S-U": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8002S.csv",
    "condo_V-Z": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8002V.csv",
    "coop": "https://www2.myfloridalicense.com/sto/file_download/extracts/paymenthist_8004.csv",
}


async def download_csv(url: str, save_path: Path) -> str:
    """Download a CSV file and return its content."""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=60,
        follow_redirects=True,
    ) as client:
        logger.info(f"Downloading {url}")
        resp = await client.get(url)
        resp.raise_for_status()
        content = resp.text
        save_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved {save_path} ({len(content)} bytes)")
        return content


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str or not date_str.strip():
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_int(val: str | None) -> int | None:
    if not val or not val.strip():
        return None
    try:
        return int(val.strip().replace(",", ""))
    except ValueError:
        return None


def _filing_status(primary: str, secondary: str) -> FilingStatus:
    secondary_lower = (secondary or "").lower().strip()
    if secondary_lower == "delinquent":
        return FilingStatus.DELINQUENT
    if secondary_lower in ("terminated", "dissolved"):
        return FilingStatus.DISSOLVED
    primary_lower = (primary or "").lower().strip()
    if primary_lower == "approved":
        return FilingStatus.ACTIVE
    if primary_lower in ("withdrawn", "rejected"):
        return FilingStatus.INACTIVE
    return FilingStatus.UNKNOWN


def _management_status(managing_entity_name: str | None) -> ManagementStatus:
    if not managing_entity_name or not managing_entity_name.strip():
        return ManagementStatus.SELF_MANAGED
    return ManagementStatus.PROFESSIONALLY_MANAGED


def parse_condo_csv(content: str, region: str) -> list[Association]:
    """Parse a DBPR condo CSV into Association records."""
    reader = csv.DictReader(io.StringIO(content))
    associations = []
    for row in reader:
        try:
            mgmt_name = (row.get("Managing Entity Name") or "").strip()
            mgmt_route = (row.get("Managing Entity Route") or "").strip()
            mgmt_street = (row.get("Managing Entity Street") or "").strip()
            mgmt_city = (row.get("Managing Entity City") or "").strip()
            mgmt_state = (row.get("Managing Entity State") or "").strip()
            mgmt_zip = (row.get("Managing Entity Zip") or "").strip()
            mgmt_address_parts = [p for p in [mgmt_route, mgmt_street, mgmt_city, mgmt_state, mgmt_zip] if p]
            mgmt_address = ", ".join(mgmt_address_parts) if mgmt_address_parts else None

            assoc = Association(
                community_name=(row.get("Condo Name") or "").strip(),
                source="fl_dbpr_condo",
                source_id=(row.get("Project Number") or "").strip(),
                state="FL",
                county=(row.get("County") or "").strip(),
                physical_address=(row.get("Street City State Zip") or "").strip(),
                community_type=CommunityType.CONDO,
                unit_count=_parse_int(row.get("Units")),
                date_established=_parse_date(row.get("Recorded Date")),
                filing_status=_filing_status(
                    row.get("Primary Status", ""),
                    row.get("Secondary Status", ""),
                ),
                management_status=_management_status(mgmt_name),
                management_company_name=mgmt_name or None,
                management_company_license=(row.get("Managing Entity Number") or "").strip() or None,
                registered_agent_name=mgmt_name or None,
                registered_agent_address=mgmt_address,
                raw_data={
                    "region": region,
                    "file_number": (row.get("File Number") or "").strip(),
                    "primary_status": (row.get("Primary Status") or "").strip(),
                    "secondary_status": (row.get("Secondary Status") or "").strip(),
                },
            )
            associations.append(assoc)
        except Exception as e:
            logger.warning(f"Error parsing condo row: {e} — row: {row}")
    return associations


def parse_coop_csv(content: str) -> list[Association]:
    """Parse the DBPR cooperative CSV into Association records."""
    reader = csv.reader(io.StringIO(content))
    associations = []
    for row in reader:
        if len(row) < 18:
            continue
        try:
            project_num = row[0].strip().strip('"')
            file_num = str(row[1]).strip().strip('"')
            name = row[2].strip().strip('"')
            county = row[3].strip().strip('"')
            street = row[4].strip().strip('"')
            city = row[5].strip().strip('"')
            state = row[6].strip().strip('"')
            zipcode = row[7].strip().strip('"')
            units = row[8].strip().strip('"')
            recorded_date = row[9].strip().strip('"')
            primary_status = row[10].strip().strip('"')
            secondary_status = row[11].strip().strip('"')
            mgmt_entity_num = row[12].strip().strip('"')
            mgmt_entity_name = row[13].strip().strip('"')
            mgmt_route = row[14].strip().strip('"')
            mgmt_street = row[15].strip().strip('"')
            mgmt_city = row[16].strip().strip('"')
            mgmt_state = row[17].strip().strip('"')
            mgmt_zip = row[18].strip().strip('"') if len(row) > 18 else ""

            physical = ", ".join(p for p in [street, city, state, zipcode] if p)
            mgmt_addr_parts = [p for p in [mgmt_route, mgmt_street, mgmt_city, mgmt_state, mgmt_zip] if p]
            mgmt_address = ", ".join(mgmt_addr_parts) if mgmt_addr_parts else None

            assoc = Association(
                community_name=name,
                source="fl_dbpr_coop",
                source_id=project_num,
                state="FL",
                county=county,
                physical_address=physical or None,
                community_type=CommunityType.COOPERATIVE,
                unit_count=_parse_int(units),
                date_established=_parse_date(recorded_date),
                filing_status=_filing_status(primary_status, secondary_status),
                management_status=_management_status(mgmt_entity_name),
                management_company_name=mgmt_entity_name or None,
                management_company_license=mgmt_entity_num or None,
                registered_agent_name=mgmt_entity_name or None,
                registered_agent_address=mgmt_address,
                raw_data={
                    "file_number": file_num,
                    "primary_status": primary_status,
                    "secondary_status": secondary_status,
                },
            )
            associations.append(assoc)
        except Exception as e:
            logger.warning(f"Error parsing coop row: {e}")
    return associations


async def download_and_parse_all_condos() -> list[Association]:
    """Download all 5 regional condo CSVs and parse into associations."""
    all_assocs: list[Association] = []
    for region, url in CONDO_CSVS.items():
        save_path = RAW_DIR / f"condo_{region}.csv"
        try:
            content = await download_csv(url, save_path)
            assocs = parse_condo_csv(content, region)
            logger.info(f"Parsed {len(assocs)} condos from {region}")
            all_assocs.extend(assocs)
        except Exception as e:
            logger.error(f"Failed to download/parse {region}: {e}")
    return all_assocs


async def download_and_parse_coops() -> list[Association]:
    """Download and parse the cooperative CSV."""
    save_path = RAW_DIR / "cooperatives.csv"
    try:
        content = await download_csv(COOP_CSV, save_path)
        assocs = parse_coop_csv(content)
        logger.info(f"Parsed {len(assocs)} cooperatives")
        return assocs
    except Exception as e:
        logger.error(f"Failed to download/parse cooperatives: {e}")
        return []


async def download_cam_licensees() -> Path:
    """Download CAM licensee CSV (used for competitor intelligence cross-referencing)."""
    save_path = RAW_DIR / "cam_licensees.csv"
    await download_csv(CAM_LICENSEES_CSV, save_path)
    return save_path


async def download_developer_summary() -> Path:
    """Download developer summary CSV."""
    save_path = RAW_DIR / "developer_summary.csv"
    await download_csv(DEVELOPER_SUMMARY_CSV, save_path)
    return save_path


async def download_county_summary() -> Path:
    """Download county summary CSV."""
    save_path = RAW_DIR / "county_summary.csv"
    await download_csv(COUNTY_SUMMARY_CSV, save_path)
    return save_path


async def download_payment_history() -> list[Path]:
    """Download all payment history CSVs."""
    paths = []
    for key, url in PAYMENT_HISTORY_CSVS.items():
        save_path = RAW_DIR / f"payment_history_{key}.csv"
        try:
            await download_csv(url, save_path)
            paths.append(save_path)
        except Exception as e:
            logger.error(f"Failed to download payment history {key}: {e}")
    return paths


async def run_full_dbpr_download() -> list[Association]:
    """Run the complete DBPR bulk download pipeline."""
    logger.info("Starting FL DBPR bulk download pipeline")

    # Download condos and coops in parallel
    condo_task = download_and_parse_all_condos()
    coop_task = download_and_parse_coops()

    # Also download supplementary data
    cam_task = download_cam_licensees()
    dev_task = download_developer_summary()
    county_task = download_county_summary()
    payment_task = download_payment_history()

    condos, coops, *_ = await asyncio.gather(
        condo_task, coop_task, cam_task, dev_task, county_task, payment_task
    )

    all_associations = condos + coops
    logger.info(
        f"FL DBPR pipeline complete: {len(condos)} condos + {len(coops)} coops = "
        f"{len(all_associations)} total associations"
    )
    return all_associations
