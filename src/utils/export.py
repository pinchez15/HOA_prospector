"""Export association data to CSV, JSON, and SQLite."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from config.settings import OUTPUT_DIR
from src.models.association import Association
from src.utils.db import get_db, upsert_many

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "community_name",
    "state",
    "county",
    "physical_address",
    "mailing_address",
    "unit_count",
    "community_type",
    "date_established",
    "date_incorporated",
    "filing_status",
    "management_status",
    "registered_agent_name",
    "registered_agent_address",
    "management_company_name",
    "management_company_license",
    "officers",
    "ein",
    "principal_office_address",
    "annual_report_status",
    "last_annual_report_year",
    "revenue",
    "expenses",
    "assets",
    "total_property_value",
    "avg_unit_value",
    "avg_living_area_sqft",
    "avg_year_built",
    "amenities",
    "enrichment_sources",
    "source",
    "source_id",
]


def export_csv(associations: list[Association], filename: str = "hoa_data.csv") -> Path:
    """Export associations to CSV."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for assoc in associations:
            officers_str = "; ".join(
                f"{o.name} ({o.title})" if o.title else o.name
                for o in assoc.officers
            )
            row = {
                "community_name": assoc.community_name,
                "state": assoc.state,
                "county": assoc.county or "",
                "physical_address": assoc.physical_address or "",
                "mailing_address": assoc.mailing_address or "",
                "unit_count": assoc.unit_count or "",
                "community_type": assoc.community_type.value,
                "date_established": str(assoc.date_established) if assoc.date_established else "",
                "date_incorporated": str(assoc.date_incorporated) if assoc.date_incorporated else "",
                "filing_status": assoc.filing_status.value,
                "management_status": assoc.management_status.value,
                "registered_agent_name": assoc.registered_agent_name or "",
                "registered_agent_address": assoc.registered_agent_address or "",
                "management_company_name": assoc.management_company_name or "",
                "management_company_license": assoc.management_company_license or "",
                "officers": officers_str,
                "ein": assoc.ein or "",
                "principal_office_address": assoc.principal_office_address or "",
                "annual_report_status": assoc.annual_report_status or "",
                "last_annual_report_year": assoc.last_annual_report_year or "",
                "revenue": assoc.revenue or "",
                "expenses": assoc.expenses or "",
                "assets": assoc.assets or "",
                "total_property_value": assoc.total_property_value or "",
                "avg_unit_value": assoc.avg_unit_value or "",
                "avg_living_area_sqft": assoc.avg_living_area_sqft or "",
                "avg_year_built": assoc.avg_year_built or "",
                "amenities": "; ".join(assoc.amenities) if assoc.amenities else "",
                "enrichment_sources": "; ".join(assoc.enrichment_sources) if assoc.enrichment_sources else "",
                "source": assoc.source,
                "source_id": assoc.source_id or "",
            }
            writer.writerow(row)

    logger.info(f"Exported {len(associations)} records to {path}")
    return path


def export_json(associations: list[Association], filename: str = "hoa_data.json") -> Path:
    """Export associations to JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename

    data = []
    for assoc in associations:
        d = assoc.model_dump(mode="json")
        # Remove raw_data from export for cleanliness
        d.pop("raw_data", None)
        data.append(d)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Exported {len(associations)} records to {path}")
    return path


def export_sqlite(associations: list[Association]) -> Path:
    """Export associations to SQLite database."""
    from config.settings import DB_PATH
    db = get_db()
    count = upsert_many(db, associations)
    logger.info(f"Upserted {count} records to {DB_PATH}")
    return DB_PATH


def export_all(associations: list[Association], prefix: str = "hoa_data") -> dict[str, Path]:
    """Export to all formats."""
    return {
        "csv": export_csv(associations, f"{prefix}.csv"),
        "json": export_json(associations, f"{prefix}.json"),
        "sqlite": export_sqlite(associations),
    }
