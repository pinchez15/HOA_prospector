"""Normalized HOA/Condo association data model."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CommunityType(str, Enum):
    HOA = "hoa"
    CONDO = "condo"
    COOPERATIVE = "cooperative"
    TOWNHOME = "townhome"
    UNKNOWN = "unknown"


class FilingStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DISSOLVED = "dissolved"
    DELINQUENT = "delinquent"
    SUSPENDED = "suspended"
    ADMIN_DISSOLVED = "administratively_dissolved"
    UNKNOWN = "unknown"


class ManagementStatus(str, Enum):
    PROFESSIONALLY_MANAGED = "professionally_managed"
    SELF_MANAGED = "self_managed"
    UNKNOWN = "unknown"


class Officer(BaseModel):
    name: str
    title: Optional[str] = None
    address: Optional[str] = None


class Association(BaseModel):
    """Normalized association record — the canonical output format."""

    # Identity
    community_name: str
    source: str = Field(description="e.g. 'fl_dbpr', 'fl_sunbiz', 'nc_sos'")
    source_id: Optional[str] = Field(default=None, description="ID from the source system")

    # Location
    state: str
    county: Optional[str] = None
    physical_address: Optional[str] = None
    mailing_address: Optional[str] = None

    # Classification
    community_type: CommunityType = CommunityType.UNKNOWN
    unit_count: Optional[int] = Field(default=None, description="Number of units/lots")

    # Dates
    date_established: Optional[date] = None
    date_incorporated: Optional[date] = None

    # Status
    filing_status: FilingStatus = FilingStatus.UNKNOWN
    management_status: ManagementStatus = ManagementStatus.UNKNOWN

    # Registered agent
    registered_agent_name: Optional[str] = None
    registered_agent_address: Optional[str] = None

    # Management
    management_company_name: Optional[str] = None
    management_company_license: Optional[str] = None

    # People
    officers: list[Officer] = Field(default_factory=list)

    # Corporate details (from Sunbiz / NC SOS)
    ein: Optional[str] = None
    principal_office_address: Optional[str] = None

    # Filing history signals
    annual_report_status: Optional[str] = None
    last_annual_report_year: Optional[int] = None

    # Financial (from IRS 990 / ProPublica)
    revenue: Optional[int] = None
    expenses: Optional[int] = None
    assets: Optional[int] = None

    # Property enrichment (from county property data)
    total_property_value: Optional[int] = None
    avg_unit_value: Optional[int] = None
    avg_living_area_sqft: Optional[int] = None
    avg_year_built: Optional[int] = None

    # Amenities (from Overpass / property data)
    amenities: list[str] = Field(default_factory=list, description="e.g. ['pool', 'clubhouse']")

    # Enrichment tracking
    enrichment_sources: list[str] = Field(default_factory=list, description="Sources that contributed data")

    # Metadata
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw_data: Optional[dict] = Field(default=None, description="Original scraped fields")
