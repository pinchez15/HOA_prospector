"""Parse DBPR managing-entity fields into association vs CAM company."""

from __future__ import annotations

import re


def extract_cam_from_co(text: str | None) -> str | None:
    """Extract CAM company name from a C/O line or address string."""
    if not text or not text.strip():
        return None

    match = re.search(r"C/O\s+([^,]+)", text.strip(), re.I)
    if not match:
        return None

    return match.group(1).strip().rstrip(",.")


def format_mgmt_address(
    route: str | None,
    street: str | None,
    city: str | None,
    state: str | None,
    zipcode: str | None,
) -> str | None:
    parts = [p.strip() for p in (route, street, city, state, zipcode) if p and p.strip()]
    return ", ".join(parts) if parts else None


def resolve_managing_entity(
    mgmt_name: str | None,
    mgmt_route: str | None,
    mgmt_street: str | None,
    mgmt_city: str | None,
    mgmt_state: str | None,
    mgmt_zip: str | None,
) -> tuple[str | None, str | None, str | None]:
    """
    Return (management_company_name, registered_agent_name, registered_agent_address).

    DBPR often lists the HOA association as Managing Entity Name and the CAM firm
    on the next line as "C/O KEYS-CALDWELL, INC." in Managing Entity Route.
    """
    name = (mgmt_name or "").strip() or None
    route = (mgmt_route or "").strip()
    cam = extract_cam_from_co(route)
    address = format_mgmt_address(mgmt_route, mgmt_street, mgmt_city, mgmt_state, mgmt_zip)

    if cam:
        return cam, name, address

    return name, name, address


def normalize_row_cam_fields(row: dict) -> dict:
    """Backfill management_company_name from C/O address on exported rows."""
    current = (row.get("management_company_name") or "").strip()
    agent = (row.get("registered_agent_name") or "").strip()
    cam = extract_cam_from_co(row.get("registered_agent_address")) or extract_cam_from_co(current)
    if cam:
        row["management_company_name"] = cam
        if not agent and current:
            row["registered_agent_name"] = current
    return row
