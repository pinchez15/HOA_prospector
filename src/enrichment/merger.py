"""
Record merger and enrichment.

Cross-references associations from multiple sources:
  - FL DBPR bulk CSVs (condos/coops) have unit counts and management company
  - FL Sunbiz has officers, EIN, filing history
  - FL DBPR search has HOA-specific registrations
  - NC SOS has officers and filing status

Merge logic:
  1. DBPR records are the base (richest for FL)
  2. Sunbiz records add corporate details (officers, EIN, annual reports)
  3. Management status is derived from multiple signals
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from src.models.association import Association, ManagementStatus

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize an association name for fuzzy matching."""
    s = name.upper().strip()
    # Remove common suffixes
    for suffix in [", INC.", " INC.", ", INC", " INC", ", A CONDO", " A CONDO",
                   " CONDOMINIUM", " ASSOCIATION", " ASSN", " ASSOC",
                   " OF ", " THE ", ",", "."]:
        s = s.replace(suffix, " ")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def merge_associations(
    *source_lists: list[Association],
) -> list[Association]:
    """
    Merge associations from multiple sources into a deduplicated list.
    When the same community appears in multiple sources, combine fields.
    """
    # Index by normalized name + state for fuzzy matching
    by_key: dict[str, list[Association]] = defaultdict(list)

    for source_list in source_lists:
        for assoc in source_list:
            key = f"{assoc.state}:{normalize_name(assoc.community_name)}"
            by_key[key].append(assoc)

    merged: list[Association] = []
    for key, group in by_key.items():
        if len(group) == 1:
            merged.append(group[0])
        else:
            merged.append(_merge_group(group))

    logger.info(
        f"Merged {sum(len(sl) for sl in source_lists)} records into {len(merged)} unique associations"
    )
    return merged


def _merge_group(group: list[Association]) -> Association:
    """Merge multiple records for the same association, preferring the richest data."""
    # Sort by source priority: DBPR bulk > DBPR search > Sunbiz > NC SOS
    priority = {"fl_dbpr_condo": 0, "fl_dbpr_coop": 0, "fl_dbpr_hoa": 1, "fl_sunbiz": 2, "nc_sos": 3}
    group.sort(key=lambda a: priority.get(a.source, 99))

    base = group[0].model_copy()

    for other in group[1:]:
        # Fill in missing fields from other sources
        if not base.unit_count and other.unit_count:
            base.unit_count = other.unit_count
        if not base.ein and other.ein:
            base.ein = other.ein
        if not base.officers and other.officers:
            base.officers = other.officers
        if not base.date_incorporated and other.date_incorporated:
            base.date_incorporated = other.date_incorporated
        if not base.principal_office_address and other.principal_office_address:
            base.principal_office_address = other.principal_office_address
        if not base.registered_agent_name and other.registered_agent_name:
            base.registered_agent_name = other.registered_agent_name
        if not base.registered_agent_address and other.registered_agent_address:
            base.registered_agent_address = other.registered_agent_address
        if not base.management_company_name and other.management_company_name:
            base.management_company_name = other.management_company_name
        if not base.last_annual_report_year and other.last_annual_report_year:
            base.last_annual_report_year = other.last_annual_report_year
        if not base.mailing_address and other.mailing_address:
            base.mailing_address = other.mailing_address
        if not base.physical_address and other.physical_address:
            base.physical_address = other.physical_address
        if not base.revenue and other.revenue:
            base.revenue = other.revenue
        if not base.expenses and other.expenses:
            base.expenses = other.expenses
        if not base.assets and other.assets:
            base.assets = other.assets
        if not base.total_property_value and other.total_property_value:
            base.total_property_value = other.total_property_value
        if not base.avg_unit_value and other.avg_unit_value:
            base.avg_unit_value = other.avg_unit_value
        if not base.avg_living_area_sqft and other.avg_living_area_sqft:
            base.avg_living_area_sqft = other.avg_living_area_sqft
        if not base.avg_year_built and other.avg_year_built:
            base.avg_year_built = other.avg_year_built
        if not base.amenities and other.amenities:
            base.amenities = other.amenities
        # Merge enrichment sources
        for src in other.enrichment_sources:
            if src not in base.enrichment_sources:
                base.enrichment_sources = [*base.enrichment_sources, src]

    # Derive management status from merged signals
    base.management_status = _derive_management_status(base)

    return base


def enrich_from_dicts(
    associations: list[Association],
    enrichment_records: list[dict],
    source_name: str,
) -> list[Association]:
    """
    Apply enrichment data from dicts to matching associations.
    Matches by normalized owner_name to community_name.
    Fills missing fields without overwriting existing data.
    """
    # Build lookup from enrichment records
    enrich_lookup: dict[str, dict] = {}
    for rec in enrichment_records:
        key = normalize_name(rec.get("owner_name", ""))
        if key:
            enrich_lookup[key] = rec

    matched = 0
    result = []
    for assoc in associations:
        key = normalize_name(assoc.community_name)
        rec = enrich_lookup.get(key)
        if not rec:
            result.append(assoc)
            continue

        matched += 1
        updated = assoc.model_copy()

        # Fill property fields
        if not updated.unit_count and rec.get("total_units"):
            updated.unit_count = int(rec["total_units"])
        if not updated.total_property_value and rec.get("total_value"):
            updated.total_property_value = int(rec["total_value"])
        if not updated.avg_unit_value and rec.get("avg_value"):
            updated.avg_unit_value = int(rec["avg_value"])
        if not updated.avg_living_area_sqft and rec.get("avg_living_area_sqft"):
            updated.avg_living_area_sqft = int(rec["avg_living_area_sqft"])
        if not updated.avg_year_built and rec.get("avg_year_built"):
            updated.avg_year_built = int(rec["avg_year_built"])
        if not updated.physical_address and rec.get("physical_address"):
            city = rec.get("physical_city", "")
            zipcode = rec.get("physical_zip", "")
            parts = [rec["physical_address"], city, zipcode]
            updated.physical_address = ", ".join(p for p in parts if p)

        if source_name not in updated.enrichment_sources:
            updated.enrichment_sources = [*updated.enrichment_sources, source_name]

        result.append(updated)

    logger.info(f"Enrichment from {source_name}: {matched} associations matched out of {len(associations)}")
    return result


def apply_amenities(
    associations: list[Association],
    amenity_map: dict[str, list[str]],
) -> list[Association]:
    """Apply amenity tags to associations by source_id."""
    result = []
    tagged = 0
    for assoc in associations:
        if assoc.source_id in amenity_map:
            updated = assoc.model_copy()
            updated.amenities = amenity_map[assoc.source_id]
            if "overpass_amenities" not in updated.enrichment_sources:
                updated.enrichment_sources = [*updated.enrichment_sources, "overpass_amenities"]
            result.append(updated)
            tagged += 1
        else:
            result.append(assoc)
    logger.info(f"Amenities: {tagged} associations tagged")
    return result


def _derive_management_status(assoc: Association) -> ManagementStatus:
    """
    Derive whether a community is self-managed or professionally managed.

    Signals for PROFESSIONALLY MANAGED:
      - Management company name is present
      - Registered agent address is a commercial address (suite, office, etc.)

    Signals for SELF MANAGED:
      - No management company listed
      - Registered agent is an individual at a residential address
      - Registered agent name matches an officer name
    """
    if assoc.management_company_name:
        return ManagementStatus.PROFESSIONALLY_MANAGED

    # Check if registered agent looks like a management company
    if assoc.registered_agent_name:
        ra_name_lower = assoc.registered_agent_name.lower()
        mgmt_indicators = [
            "management", "mgmt", "property", "realty", "real estate",
            "associates", "consulting", "services", "group", "partners",
        ]
        if any(indicator in ra_name_lower for indicator in mgmt_indicators):
            return ManagementStatus.PROFESSIONALLY_MANAGED

    # Check if registered agent address looks residential
    if assoc.registered_agent_address:
        addr_lower = assoc.registered_agent_address.lower()
        commercial_indicators = ["suite", "ste ", "floor", "fl ", "#", "office"]
        if any(indicator in addr_lower for indicator in commercial_indicators):
            return ManagementStatus.PROFESSIONALLY_MANAGED

    # If registered agent name matches an officer, likely self-managed
    if assoc.registered_agent_name and assoc.officers:
        ra_normalized = normalize_name(assoc.registered_agent_name)
        for officer in assoc.officers:
            if normalize_name(officer.name) == ra_normalized:
                return ManagementStatus.SELF_MANAGED

    # If no management company and no commercial indicators, lean toward self-managed
    if not assoc.management_company_name:
        return ManagementStatus.SELF_MANAGED

    return ManagementStatus.UNKNOWN
