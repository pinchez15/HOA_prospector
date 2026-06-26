"""
ProPublica Nonprofit Explorer enrichment scraper.

Searches for HOA nonprofits and extracts:
  - EIN (Employer Identification Number)
  - Officers/board members (from 990 filings)
  - Revenue, expenses, assets (from 990 filings)
  - Filing history

API docs: https://projects.propublica.org/nonprofits/api
Free, no auth required.

HOAs typically file as 501(c)(4) (civic leagues) or 501(c)(7) (social clubs).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from config.settings import (
    PROPUBLICA_DELAY,
    PROPUBLICA_ORG_URL,
    PROPUBLICA_SEARCH_URL,
    RAW_DIR,
)
from src.models.association import Association, Officer
from src.utils.http_client import RateLimitedClient
from src.utils.matching import find_best_match, fuzzy_match_score

logger = logging.getLogger(__name__)

CACHE_DIR = RAW_DIR / "propublica"

# HOA-relevant subsection codes: 4 = 501(c)(4), 7 = 501(c)(7)
HOA_SUBSECTION_CODES = {4, 7}


def _cache_path(key: str) -> Path:
    h = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{h}.json"


def _read_cache(key: str) -> dict | None:
    path = _cache_path(key)
    if path.exists():
        return json.loads(path.read_text())
    return None


def _write_cache(key: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(json.dumps(data))


class PropublicaScraper:
    def __init__(self, client: RateLimitedClient | None = None):
        self.client = client or RateLimitedClient(delay=PROPUBLICA_DELAY)

    async def search(self, query: str, state: str, page: int = 0) -> dict:
        """Search ProPublica for nonprofits."""
        # Clean query — remove punctuation that breaks the API
        import re
        clean_query = re.sub(r'[,."\'!@#$%^&*()]+', ' ', query)
        clean_query = re.sub(r'\s+', ' ', clean_query).strip()

        cache_key = f"search:{clean_query}:{state}:{page}"
        cached = _read_cache(cache_key)
        if cached is not None:
            return cached

        params = {"q": clean_query, "state[id]": state, "page": str(page)}
        try:
            resp = await self.client.get(PROPUBLICA_SEARCH_URL, params=params)
            data = resp.json()
            _write_cache(cache_key, data)
            return data
        except Exception as e:
            logger.debug(f"ProPublica search failed for '{clean_query}': {e}")
            return {"organizations": [], "total_results": 0}

    async def get_org(self, ein: int | str) -> dict | None:
        """Fetch org detail by EIN."""
        cache_key = f"org:{ein}"
        cached = _read_cache(cache_key)
        if cached is not None:
            return cached

        url = PROPUBLICA_ORG_URL.format(ein=ein)
        try:
            resp = await self.client.get(url)
            data = resp.json()
            _write_cache(cache_key, data)
            return data
        except Exception as e:
            logger.warning(f"Error fetching EIN {ein}: {e}")
            return None

    async def search_all_pages(self, query: str, state: str, max_pages: int = 10) -> list[dict]:
        """Search and collect all result pages."""
        all_orgs = []
        for page in range(max_pages):
            data = await self.search(query, state, page)
            orgs = data.get("organizations", [])
            if not orgs:
                break
            all_orgs.extend(orgs)
            if page + 1 >= data.get("num_pages", 1):
                break
        return all_orgs

    async def find_match_for_association(
        self, assoc: Association
    ) -> dict | None:
        """Search ProPublica for an association and return the best matching org."""
        import re

        name = assoc.community_name
        # Strip common suffixes to build a search-friendly query
        clean = re.sub(
            r'\b(inc|llc|corp|association|assn|assoc|condominium|condo|homeowners?|'
            r'property owners|owners|community|of|the|a)\b',
            '', name, flags=re.IGNORECASE
        )
        clean = re.sub(r'[,."\'!@#$%^&*()]+', ' ', clean)
        words = clean.split()
        # Use first 2-3 meaningful words (API chokes on long queries)
        query = " ".join(words[:3]).strip()
        if len(query) < 3:
            query = " ".join(name.split()[:2])

        results = await self.search(query, assoc.state)
        orgs = results.get("organizations", [])

        if not orgs and len(words) > 1:
            # Try just the first 2 words
            query2 = " ".join(words[:2])
            results = await self.search(query2, assoc.state)
            orgs = results.get("organizations", [])

        if not orgs:
            return None

        # Filter to HOA-relevant subsection codes
        hoa_orgs = [o for o in orgs if o.get("subseccd") in HOA_SUBSECTION_CODES]
        # Also keep orgs without subsection code (might still match)
        other_orgs = [o for o in orgs if o.get("subseccd") not in HOA_SUBSECTION_CODES]

        # Try matching HOA orgs first, then all orgs
        for candidate_list in [hoa_orgs, other_orgs]:
            if not candidate_list:
                continue
            match, score = find_best_match(
                assoc.community_name, candidate_list, name_key="name", threshold=0.70
            )
            if match:
                logger.debug(
                    f"Matched '{assoc.community_name}' -> '{match['name']}' "
                    f"(score={score:.2f}, EIN={match.get('ein')})"
                )
                return match

        return None

    def apply_search_match(self, assoc: Association, match: dict) -> Association:
        """Apply ProPublica search result data to an association."""
        updated = assoc.model_copy()
        ein = match.get("ein")
        if ein and not updated.ein:
            updated.ein = str(ein)
        if "propublica" not in updated.enrichment_sources:
            updated.enrichment_sources = [*updated.enrichment_sources, "propublica"]
        return updated

    def apply_org_detail(self, assoc: Association, detail: dict) -> Association:
        """Apply ProPublica org detail (990 filing data) to an association."""
        updated = assoc.model_copy()
        org = detail.get("organization", {})

        # EIN
        if org.get("ein") and not updated.ein:
            updated.ein = str(org["ein"])

        # Financial data from org summary
        if org.get("revenue_amount") and not updated.revenue:
            updated.revenue = int(org["revenue_amount"])
        if org.get("asset_amount") and not updated.assets:
            updated.assets = int(org["asset_amount"])
        if org.get("income_amount") and not updated.revenue:
            updated.revenue = int(org["income_amount"])

        # Address enrichment
        if org.get("address") and not updated.mailing_address:
            parts = [org.get("address", ""), org.get("city", ""), org.get("state", ""), org.get("zipcode", "")]
            updated.mailing_address = ", ".join(p for p in parts if p)

        # Filings with data (990 forms)
        filings = detail.get("filings_with_data", [])
        if filings:
            latest = filings[0]
            # Revenue and expenses from most recent filing
            if latest.get("totrevenue") and not updated.revenue:
                updated.revenue = int(latest["totrevenue"])
            if latest.get("totfuncexpns") and not updated.expenses:
                updated.expenses = int(latest["totfuncexpns"])
            if latest.get("totassetsend") and not updated.assets:
                updated.assets = int(latest["totassetsend"])

        # Filings without data (still shows years filed)
        filings_no_data = detail.get("filings_without_data", [])
        all_filings = filings + filings_no_data
        if all_filings and not updated.last_annual_report_year:
            years = []
            for f in all_filings:
                tp = str(f.get("tax_prd", ""))
                if len(tp) >= 4 and tp[:4].isdigit():
                    years.append(int(tp[:4]))
            if years:
                updated.last_annual_report_year = max(years)

        if "propublica" not in updated.enrichment_sources:
            updated.enrichment_sources = [*updated.enrichment_sources, "propublica"]

        return updated


async def run_propublica_enrichment(
    associations: list[Association],
    max_lookups: int | None = None,
    state: str | None = None,
) -> list[Association]:
    """
    Enrich associations with ProPublica nonprofit data.

    For each association, searches ProPublica, fuzzy-matches, and
    fetches org details if a match is found.
    """
    scraper = PropublicaScraper()

    # Filter to target state if specified
    targets = associations
    if state:
        targets = [a for a in associations if a.state == state]
    if max_lookups:
        targets = targets[:max_lookups]

    logger.info(f"ProPublica enrichment: {len(targets)} associations to look up")
    enriched_map: dict[str, Association] = {}  # source_id -> enriched assoc

    matched = 0
    with_detail = 0
    for i, assoc in enumerate(targets):
        if i > 0 and i % 100 == 0:
            logger.info(f"ProPublica: {i}/{len(targets)} processed, {matched} matched")

        match = await scraper.find_match_for_association(assoc)
        if match:
            matched += 1
            updated = scraper.apply_search_match(assoc, match)

            # Fetch full org detail for EIN
            ein = match.get("ein")
            if ein:
                detail = await scraper.get_org(ein)
                if detail:
                    updated = scraper.apply_org_detail(updated, detail)
                    with_detail += 1

            enriched_map[assoc.source_id] = updated

    # Build final list — enriched where matched, original otherwise
    result = []
    for assoc in associations:
        if assoc.source_id in enriched_map:
            result.append(enriched_map[assoc.source_id])
        else:
            result.append(assoc)

    logger.info(
        f"ProPublica enrichment complete: {matched} matched, {with_detail} with filing details, "
        f"out of {len(targets)} looked up"
    )

    await scraper.client.close()
    return result
