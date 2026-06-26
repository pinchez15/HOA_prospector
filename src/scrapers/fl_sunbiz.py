"""
Florida Sunbiz.org Scraper.

Scrapes the Division of Corporations search at search.sunbiz.org for
non-profit corporation details: officers, registered agents, filing history,
EIN, principal addresses, and annual report status.

URL patterns:
  Search: /Inquiry/CorporationSearch/SearchResults/EntityName/{name}/Page{n}
  Detail: /Inquiry/CorporationSearch/SearchResultDetail?inquirytype=EntityName&...

Sunbiz blocks simple fetches (403), so we use full browser-like headers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from config.settings import RAW_DIR
from src.models.association import (
    Association,
    CommunityType,
    FilingStatus,
    ManagementStatus,
    Officer,
)
from src.utils.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

BASE_URL = "https://search.sunbiz.org"
SEARCH_PATH = "/Inquiry/CorporationSearch/SearchResults/EntityName/{term}/Page{page}"

# Keywords that indicate HOA/condo/community associations in entity names
HOA_KEYWORDS = [
    "homeowners",
    "homeowner",
    "home owners",
    "property owners",
    "community association",
    "condominium association",
    "condo association",
    "condo assn",
    "condominium assn",
    "townhome association",
    "townhouse association",
    "cooperative association",
    "owners association",
    "master association",
    "poa",
    "hoa",
]

# Search terms to cast a wide net for HOA-related nonprofits
SEARCH_TERMS = [
    "homeowners association",
    "homeowner association",
    "home owners association",
    "property owners association",
    "condominium association",
    "community association",
    "townhome association",
    "cooperative association",
    "master association",
    "owners association inc",
    "condo association",
]


def _build_search_url(term: str, page: int = 1) -> str:
    encoded_term = quote(term, safe="")
    path = SEARCH_PATH.format(term=encoded_term, page=page)
    return BASE_URL + path


def _classify_type(name: str) -> CommunityType:
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("condo", "condominium")):
        return CommunityType.CONDO
    if any(kw in name_lower for kw in ("cooperative", "co-operative", "co-op")):
        return CommunityType.COOPERATIVE
    if any(kw in name_lower for kw in ("townhome", "townhouse")):
        return CommunityType.TOWNHOME
    if any(kw in name_lower for kw in ("homeowner", "home owner", "property owner", "hoa", "poa")):
        return CommunityType.HOA
    return CommunityType.UNKNOWN


def _parse_filing_status(status_text: str) -> FilingStatus:
    s = status_text.lower().strip()
    if s == "active":
        return FilingStatus.ACTIVE
    if s == "inactive":
        return FilingStatus.INACTIVE
    if "dissolved" in s and "admin" in s:
        return FilingStatus.ADMIN_DISSOLVED
    if "dissolved" in s:
        return FilingStatus.DISSOLVED
    return FilingStatus.UNKNOWN


class SunbizScraper:
    """Scrapes Florida Sunbiz for HOA-related non-profit corporations."""

    def __init__(self, client: RateLimitedClient | None = None):
        self.client = client or RateLimitedClient(delay=1.5)
        # Extra headers to look like a real browser — Sunbiz returns 403 otherwise
        self._extra_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

    async def search_entities(self, term: str, max_pages: int = 50) -> list[dict]:
        """Search Sunbiz for entities matching a term. Returns list of {name, url, doc_number, status}."""
        results = []
        for page in range(1, max_pages + 1):
            url = _build_search_url(term, page)
            try:
                resp = await self.client.get(url, headers=self._extra_headers)
                soup = BeautifulSoup(resp.text, "lxml")

                # Results are in a table with class 'searchResultsTable' or similar
                rows = soup.select("table.searchResultsTable tr") or soup.select(
                    "#search-results tr"
                )
                if not rows:
                    # Try alternate selector
                    rows = soup.select("div.searchResultDetail") or soup.select(
                        "div.search-results a"
                    )

                if not rows and page == 1:
                    # Might be a different page structure — try finding all links
                    links = soup.find_all("a", href=re.compile(r"SearchResultDetail"))
                    for link in links:
                        href = link.get("href", "")
                        name = link.get_text(strip=True)
                        if name:
                            results.append({
                                "name": name,
                                "url": urljoin(BASE_URL, href),
                                "doc_number": None,
                                "status": None,
                            })
                    if not links:
                        break
                    continue

                page_has_results = False
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                    link = cells[0].find("a")
                    if not link:
                        continue
                    name = link.get_text(strip=True)
                    href = link.get("href", "")
                    doc_number = cells[1].get_text(strip=True) if len(cells) > 1 else None
                    status = cells[2].get_text(strip=True) if len(cells) > 2 else None
                    results.append({
                        "name": name,
                        "url": urljoin(BASE_URL, href),
                        "doc_number": doc_number,
                        "status": status,
                    })
                    page_has_results = True

                if not page_has_results:
                    break

                logger.info(f"Sunbiz search '{term}' page {page}: {len(results)} total results")

            except Exception as e:
                logger.warning(f"Sunbiz search error on page {page} for '{term}': {e}")
                break

        return results

    async def get_entity_detail(self, detail_url: str) -> dict | None:
        """Fetch and parse a Sunbiz entity detail page."""
        try:
            resp = await self.client.get(detail_url, headers=self._extra_headers)
            soup = BeautifulSoup(resp.text, "lxml")
            return self._parse_detail_page(soup, detail_url)
        except Exception as e:
            logger.warning(f"Error fetching Sunbiz detail {detail_url}: {e}")
            return None

    def _parse_detail_page(self, soup: BeautifulSoup, url: str) -> dict:
        """Extract all fields from a Sunbiz entity detail page."""
        data: dict = {"source_url": url}

        # Entity name
        name_el = soup.find("div", class_="detailSection") or soup.find("p", class_="corporationName")
        if name_el:
            data["name"] = name_el.get_text(strip=True)

        # Look for label-value pairs in the detail sections
        text = soup.get_text()

        # Document Number
        match = re.search(r"Document Number\s*[:\-]?\s*(\S+)", text)
        if match:
            data["doc_number"] = match.group(1)

        # FEI/EIN Number
        match = re.search(r"FEI/EIN Number\s*[:\-]?\s*(\S+)", text)
        if match:
            data["ein"] = match.group(1)

        # Date Filed
        match = re.search(r"Date Filed\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", text)
        if match:
            data["date_filed"] = match.group(1)

        # State
        match = re.search(r"State\s*[:\-]?\s*(FL|FLORIDA)", text, re.IGNORECASE)
        if match:
            data["state"] = "FL"

        # Status
        match = re.search(r"Status\s*[:\-]?\s*(\w[\w\s/]*\w)", text)
        if match:
            data["status"] = match.group(1).strip()

        # Last Event
        match = re.search(r"Last Event\s*[:\-]?\s*(.+?)(?:\n|Last Event Date)", text)
        if match:
            data["last_event"] = match.group(1).strip()

        # Principal Address
        section = soup.find(string=re.compile(r"Principal Address", re.I))
        if section:
            parent = section.find_parent()
            if parent:
                addr_text = parent.find_next_sibling()
                if addr_text:
                    data["principal_address"] = addr_text.get_text(separator=", ", strip=True)

        # Mailing Address
        section = soup.find(string=re.compile(r"Mailing Address", re.I))
        if section:
            parent = section.find_parent()
            if parent:
                addr_text = parent.find_next_sibling()
                if addr_text:
                    data["mailing_address"] = addr_text.get_text(separator=", ", strip=True)

        # Registered Agent
        section = soup.find(string=re.compile(r"Registered Agent", re.I))
        if section:
            parent = section.find_parent()
            if parent:
                agent_block = parent.find_next_sibling()
                if agent_block:
                    lines = [l.strip() for l in agent_block.get_text().split("\n") if l.strip()]
                    if lines:
                        data["registered_agent_name"] = lines[0]
                    if len(lines) > 1:
                        data["registered_agent_address"] = ", ".join(lines[1:])

        # Officers/Directors
        officers = []
        officer_sections = soup.find_all(string=re.compile(r"(Title|Officer/Director Detail)", re.I))
        # Alternative: look for the officer table/section
        officer_section = soup.find(string=re.compile(r"Officer/Director Detail", re.I))
        if officer_section:
            parent = officer_section.find_parent()
            if parent:
                # Walk through subsequent elements to find officer entries
                for sibling in parent.find_next_siblings():
                    text_content = sibling.get_text()
                    title_match = re.search(r"Title\s*(\w[\w\s]*)", text_content)
                    name_lines = [l.strip() for l in text_content.split("\n") if l.strip()]
                    if title_match and len(name_lines) >= 2:
                        officers.append({
                            "title": title_match.group(1).strip(),
                            "name": name_lines[1] if len(name_lines) > 1 else "",
                            "address": ", ".join(name_lines[2:]) if len(name_lines) > 2 else "",
                        })
        data["officers"] = officers

        # Annual Reports
        annual_reports = []
        report_section = soup.find(string=re.compile(r"Annual Report", re.I))
        if report_section:
            parent = report_section.find_parent()
            if parent:
                table = parent.find_next("table")
                if table:
                    for row in table.find_all("tr"):
                        cells = row.find_all("td")
                        if cells:
                            year = cells[0].get_text(strip=True)
                            date = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                            annual_reports.append({"year": year, "filed_date": date})
        data["annual_reports"] = annual_reports

        return data

    def detail_to_association(self, detail: dict) -> Association:
        """Convert a parsed Sunbiz detail dict to an Association."""
        name = detail.get("name", "")
        officers = [
            Officer(
                name=o.get("name", ""),
                title=o.get("title"),
                address=o.get("address"),
            )
            for o in detail.get("officers", [])
            if o.get("name")
        ]

        annual_reports = detail.get("annual_reports", [])
        last_report_year = None
        if annual_reports:
            years = [int(r["year"]) for r in annual_reports if r.get("year", "").isdigit()]
            if years:
                last_report_year = max(years)

        status_text = detail.get("status", "")
        filing_status = _parse_filing_status(status_text)

        date_filed = None
        if detail.get("date_filed"):
            try:
                date_filed = datetime.strptime(detail["date_filed"], "%m/%d/%Y").date()
            except ValueError:
                pass

        return Association(
            community_name=name,
            source="fl_sunbiz",
            source_id=detail.get("doc_number"),
            state="FL",
            physical_address=detail.get("principal_address"),
            mailing_address=detail.get("mailing_address"),
            community_type=_classify_type(name),
            date_incorporated=date_filed,
            filing_status=filing_status,
            management_status=ManagementStatus.UNKNOWN,
            registered_agent_name=detail.get("registered_agent_name"),
            registered_agent_address=detail.get("registered_agent_address"),
            officers=officers,
            ein=detail.get("ein"),
            principal_office_address=detail.get("principal_address"),
            last_annual_report_year=last_report_year,
            raw_data=detail,
        )

    async def scrape_all_hoa_entities(
        self, max_pages_per_term: int = 50, max_details: int | None = None
    ) -> list[Association]:
        """Search for all HOA-related terms and scrape their detail pages."""
        # Phase 1: Collect all search results
        all_results: dict[str, dict] = {}  # keyed by URL to deduplicate
        for term in SEARCH_TERMS:
            logger.info(f"Searching Sunbiz for: {term}")
            results = await self.search_entities(term, max_pages=max_pages_per_term)
            for r in results:
                if r.get("url"):
                    all_results[r["url"]] = r
            logger.info(f"  Found {len(results)} results, {len(all_results)} total unique")

        logger.info(f"Total unique Sunbiz search results: {len(all_results)}")

        # Phase 2: Fetch detail pages
        detail_urls = list(all_results.keys())
        if max_details:
            detail_urls = detail_urls[:max_details]

        associations = []
        for i, url in enumerate(detail_urls):
            if i % 100 == 0:
                logger.info(f"Fetching Sunbiz detail {i+1}/{len(detail_urls)}")
            detail = await self.get_entity_detail(url)
            if detail:
                assoc = self.detail_to_association(detail)
                associations.append(assoc)

        logger.info(f"Sunbiz scrape complete: {len(associations)} associations")
        return associations


async def run_sunbiz_scrape(max_pages_per_term: int = 50, max_details: int | None = None) -> list[Association]:
    """Main entry point for the Sunbiz scraper."""
    scraper = SunbizScraper()
    try:
        return await scraper.scrape_all_hoa_entities(
            max_pages_per_term=max_pages_per_term,
            max_details=max_details,
        )
    finally:
        await scraper.client.close()
