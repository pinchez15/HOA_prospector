"""
Florida DBPR License Search Scraper.

Unlike condos (which have bulk CSVs), HOA registrations must be searched
through the DBPR license search at myfloridalicense.com.

The search is a classic ASP form (wl11.asp) that requires session management.
We search by license type "Homeowners' Association" across all counties.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from config.settings import FL_COUNTIES, RAW_DIR
from src.models.association import (
    Association,
    CommunityType,
    FilingStatus,
    ManagementStatus,
)
from src.utils.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.myfloridalicense.com"

# The DBPR search is session-based ASP. We need to:
# 1. Hit the initial page to get a session ID
# 2. Submit search criteria
# 3. Paginate through results
# 4. Fetch detail pages

# License type codes for HOA-related entities
LICENSE_TYPES = {
    "HOA": "8100",  # Homeowners' Association
}


class DBPRSearchScraper:
    """Scrapes the DBPR license search for HOA registrations."""

    def __init__(self, client: RateLimitedClient | None = None):
        self.client = client or RateLimitedClient(delay=2.0)
        self._session_id: str | None = None

    async def _init_session(self) -> str:
        """Hit the search page to get a session ID."""
        resp = await self.client.get(f"{BASE_URL}/wl11.asp?mode=0&SID=")
        # Extract SID from the response URL or page content
        match = re.search(r"SID=([A-Za-z0-9]+)", str(resp.url))
        if match:
            self._session_id = match.group(1)
        else:
            # Try to find it in the page content
            match = re.search(r"SID=([A-Za-z0-9]+)", resp.text)
            if match:
                self._session_id = match.group(1)
            else:
                self._session_id = ""
        logger.info(f"DBPR session initialized: SID={self._session_id[:8]}...")
        return self._session_id

    async def search_hoas(self, county: str | None = None) -> list[dict]:
        """
        Search DBPR for HOA registrations, optionally filtered by county.
        Returns raw result dicts from the search results page.
        """
        if not self._session_id:
            await self._init_session()

        # Submit search form
        search_data = {
            "hSearchType": "SearchByName",
            "hBoardType": "",
            "hSID": self._session_id,
            "hDDChange": "",
            "hPageAction": "",
            "OrgName": "",
            "Board": "8100",  # HOA board type
            "LicenseType": "",
            "County": county or "",
            "City": "",
        }

        results = []
        page = 1
        while True:
            try:
                if page == 1:
                    resp = await self.client.post(
                        f"{BASE_URL}/wl11.asp",
                        data=search_data,
                    )
                else:
                    # Pagination
                    page_data = {
                        "hSID": self._session_id,
                        "hPageAction": f"Page{page}",
                    }
                    resp = await self.client.post(
                        f"{BASE_URL}/wl11.asp",
                        data=page_data,
                    )

                soup = BeautifulSoup(resp.text, "lxml")
                rows = self._parse_search_results(soup)

                if not rows:
                    break

                results.extend(rows)
                logger.info(
                    f"DBPR HOA search page {page}: {len(rows)} results "
                    f"({len(results)} total) county={county}"
                )

                # Check for next page
                if not self._has_next_page(soup):
                    break
                page += 1

            except Exception as e:
                logger.warning(f"DBPR search error page {page} county={county}: {e}")
                break

        return results

    def _parse_search_results(self, soup: BeautifulSoup) -> list[dict]:
        """Parse DBPR search result rows."""
        results = []
        # The results are typically in a table
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                # Look for result rows with license links
                link = row.find("a", href=re.compile(r"LicenseDetail|wl12"))
                if not link:
                    continue

                text_cells = [c.get_text(strip=True) for c in cells]
                detail_url = urljoin(BASE_URL, link.get("href", ""))
                results.append({
                    "name": text_cells[0] if text_cells else "",
                    "license_number": text_cells[1] if len(text_cells) > 1 else "",
                    "status": text_cells[2] if len(text_cells) > 2 else "",
                    "county": text_cells[3] if len(text_cells) > 3 else "",
                    "detail_url": detail_url,
                })
        return results

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """Check if there's a next page link."""
        next_link = soup.find("a", string=re.compile(r"Next|>>|›", re.I))
        return next_link is not None

    async def get_detail(self, detail_url: str) -> dict | None:
        """Fetch and parse an HOA detail page from DBPR."""
        try:
            resp = await self.client.get(detail_url)
            soup = BeautifulSoup(resp.text, "lxml")
            return self._parse_detail_page(soup)
        except Exception as e:
            logger.warning(f"Error fetching DBPR detail {detail_url}: {e}")
            return None

    def _parse_detail_page(self, soup: BeautifulSoup) -> dict:
        """Extract all fields from a DBPR license detail page."""
        data: dict = {}
        text = soup.get_text()

        # Parse label: value patterns
        patterns = {
            "license_number": r"License\s*(?:Number|#|No\.?)\s*[:\-]?\s*(\S+)",
            "name": r"(?:Business|Licensee|Organization)\s*Name\s*[:\-]?\s*(.+?)(?:\n|$)",
            "status": r"License\s*Status\s*[:\-]?\s*(\w[\w\s]*\w)",
            "county": r"County\s*[:\-]?\s*(\w[\w\s]*\w)",
            "address": r"Address\s*[:\-]?\s*(.+?)(?:\n|$)",
            "units": r"(?:Units?|Lots?)\s*(?:Count)?\s*[:\-]?\s*(\d+)",
            "managing_entity": r"Managing\s*Entity\s*[:\-]?\s*(.+?)(?:\n|$)",
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data[key] = match.group(1).strip()

        return data

    def search_result_to_association(self, result: dict, detail: dict | None = None) -> Association:
        """Convert a DBPR search result + optional detail to an Association."""
        merged = {**result}
        if detail:
            merged.update(detail)

        status = (merged.get("status") or "").lower()
        if "active" in status or "current" in status:
            filing_status = FilingStatus.ACTIVE
        elif "delinquent" in status:
            filing_status = FilingStatus.DELINQUENT
        elif "inactive" in status:
            filing_status = FilingStatus.INACTIVE
        else:
            filing_status = FilingStatus.UNKNOWN

        unit_count = None
        if merged.get("units"):
            try:
                unit_count = int(merged["units"])
            except ValueError:
                pass

        mgmt_name = merged.get("managing_entity")
        if mgmt_name:
            mgmt_status = ManagementStatus.PROFESSIONALLY_MANAGED
        else:
            mgmt_status = ManagementStatus.UNKNOWN

        return Association(
            community_name=merged.get("name", ""),
            source="fl_dbpr_hoa",
            source_id=merged.get("license_number"),
            state="FL",
            county=merged.get("county"),
            physical_address=merged.get("address"),
            community_type=CommunityType.HOA,
            unit_count=unit_count,
            filing_status=filing_status,
            management_status=mgmt_status,
            management_company_name=mgmt_name,
            raw_data=merged,
        )

    async def scrape_all_counties(self, fetch_details: bool = True) -> list[Association]:
        """Search all FL counties for HOA registrations."""
        await self._init_session()

        all_associations = []
        for county in FL_COUNTIES:
            logger.info(f"Searching DBPR HOAs in {county} County")
            results = await self.search_hoas(county=county)

            for r in results:
                detail = None
                if fetch_details and r.get("detail_url"):
                    detail = await self.get_detail(r["detail_url"])
                assoc = self.search_result_to_association(r, detail)
                all_associations.append(assoc)

        logger.info(f"DBPR HOA search complete: {len(all_associations)} associations")
        return all_associations


# Need this import for urljoin
from urllib.parse import urljoin


async def run_dbpr_hoa_search(fetch_details: bool = True) -> list[Association]:
    """Main entry point for the DBPR HOA search scraper."""
    scraper = DBPRSearchScraper()
    try:
        return await scraper.scrape_all_counties(fetch_details=fetch_details)
    finally:
        await scraper.client.close()
