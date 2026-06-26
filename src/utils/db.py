"""SQLite storage for scraped association data."""

from __future__ import annotations

import json
from datetime import date, datetime

from config.settings import DB_PATH
from src.models.association import Association

import sqlite_utils


def get_db() -> sqlite_utils.Database:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite_utils.Database(str(DB_PATH))
    _ensure_tables(db)
    return db


def _ensure_tables(db: sqlite_utils.Database) -> None:
    if "associations" not in db.table_names():
        db["associations"].create(
            {
                "source": str,
                "source_id": str,
                "community_name": str,
                "state": str,
                "county": str,
                "physical_address": str,
                "mailing_address": str,
                "community_type": str,
                "unit_count": int,
                "date_established": str,
                "date_incorporated": str,
                "filing_status": str,
                "management_status": str,
                "registered_agent_name": str,
                "registered_agent_address": str,
                "management_company_name": str,
                "management_company_license": str,
                "officers_json": str,
                "ein": str,
                "principal_office_address": str,
                "annual_report_status": str,
                "last_annual_report_year": int,
                "scraped_at": str,
                "raw_data_json": str,
            },
            pk=("source", "source_id"),
        )
        db["associations"].create_index(["state", "county"], if_not_exists=True)
        db["associations"].create_index(["filing_status"], if_not_exists=True)
        db["associations"].create_index(["management_status"], if_not_exists=True)


def _serialize(val):
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def upsert_association(db: sqlite_utils.Database, assoc: Association) -> None:
    """Insert or update an association record, keyed on (source, source_id)."""
    row = {
        "community_name": assoc.community_name,
        "source": assoc.source,
        "source_id": assoc.source_id,
        "state": assoc.state,
        "county": assoc.county,
        "physical_address": assoc.physical_address,
        "mailing_address": assoc.mailing_address,
        "community_type": assoc.community_type.value,
        "unit_count": assoc.unit_count,
        "date_established": _serialize(assoc.date_established),
        "date_incorporated": _serialize(assoc.date_incorporated),
        "filing_status": assoc.filing_status.value,
        "management_status": assoc.management_status.value,
        "registered_agent_name": assoc.registered_agent_name,
        "registered_agent_address": assoc.registered_agent_address,
        "management_company_name": assoc.management_company_name,
        "management_company_license": assoc.management_company_license,
        "officers_json": json.dumps([o.model_dump() for o in assoc.officers]),
        "ein": assoc.ein,
        "principal_office_address": assoc.principal_office_address,
        "annual_report_status": assoc.annual_report_status,
        "last_annual_report_year": assoc.last_annual_report_year,
        "revenue": assoc.revenue,
        "expenses": assoc.expenses,
        "assets": assoc.assets,
        "total_property_value": assoc.total_property_value,
        "avg_unit_value": assoc.avg_unit_value,
        "avg_living_area_sqft": assoc.avg_living_area_sqft,
        "avg_year_built": assoc.avg_year_built,
        "amenities_json": json.dumps(assoc.amenities) if assoc.amenities else None,
        "enrichment_sources_json": json.dumps(assoc.enrichment_sources) if assoc.enrichment_sources else None,
        "scraped_at": _serialize(assoc.scraped_at),
        "raw_data_json": json.dumps(assoc.raw_data) if assoc.raw_data else None,
    }
    db["associations"].insert(row, pk=("source", "source_id"), alter=True, replace=True)


def upsert_many(db: sqlite_utils.Database, associations: list[Association]) -> int:
    """Upsert a batch of associations. Returns count inserted/updated."""
    count = 0
    for assoc in associations:
        upsert_association(db, assoc)
        count += 1
    return count
