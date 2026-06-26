#!/usr/bin/env python3
"""
HOA Crawl — Data enrichment pipeline for HOA/condo association prospecting.

Usage:
    python main.py                    # Run full pipeline (FL DBPR bulk first)
    python main.py --source fl-dbpr   # Only FL DBPR bulk CSVs (condos + coops)
    python main.py --source fl-sunbiz # Only FL Sunbiz corporate search
    python main.py --source fl-hoa    # Only FL DBPR HOA license search
    python main.py --source nc-sos    # Only NC Secretary of State
    python main.py --source all       # All sources
    python main.py --source fl-prop   # FL property assessment data (values, sqft, pools)

Options:
    --max-details N   Limit detail page fetches (for testing)
    --no-details      Skip detail page fetches (search results only)
    --output PREFIX   Output file prefix (default: hoa_data)
    --counties A,B    Comma-separated county names (for fl-prop)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from src.enrichment.merger import apply_amenities, enrich_from_dicts, merge_associations
from src.models.association import Association
from src.scrapers.fl_dbpr_bulk import run_full_dbpr_download
from src.scrapers.fl_dbpr_search import run_dbpr_hoa_search
from src.scrapers.fl_property import run_property_enrichment, export_property_data
from src.scrapers.fl_sunbiz import run_sunbiz_scrape
from src.scrapers.nc_sos import run_nc_sos_scrape
from src.scrapers.propublica import run_propublica_enrichment
from src.scrapers.nc_property import run_nc_property_enrichment, export_nc_property_data
from src.scrapers.amenities import run_amenities_enrichment
from src.utils.export import export_all

console = Console()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


async def run_pipeline(
    sources: list[str],
    max_details: int | None = None,
    fetch_details: bool = True,
    output_prefix: str = "hoa_data",
    counties: list[str] | None = None,
) -> None:
    all_associations: list[list[Association]] = []

    # Phase 1: FL DBPR Bulk CSVs (condos + coops) — fastest, richest data
    if "fl-dbpr" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 1: FL DBPR Bulk CSV Downloads ═══[/]")
        try:
            dbpr_assocs = await run_full_dbpr_download()
            all_associations.append(dbpr_assocs)
            console.print(f"  [green]✓ {len(dbpr_assocs)} condo/coop associations from DBPR bulk CSVs[/]")
        except Exception as e:
            console.print(f"  [red]✗ DBPR bulk download failed: {e}[/]")

    # Phase 2: FL DBPR HOA Search — for HOAs specifically
    if "fl-hoa" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 2: FL DBPR HOA License Search ═══[/]")
        try:
            hoa_assocs = await run_dbpr_hoa_search(fetch_details=fetch_details)
            all_associations.append(hoa_assocs)
            console.print(f"  [green]✓ {len(hoa_assocs)} HOA associations from DBPR search[/]")
        except Exception as e:
            console.print(f"  [red]✗ DBPR HOA search failed: {e}[/]")

    # Phase 3: FL Sunbiz — corporate details (officers, EIN, filing history)
    if "fl-sunbiz" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 3: FL Sunbiz Corporate Search ═══[/]")
        try:
            sunbiz_assocs = await run_sunbiz_scrape(
                max_details=max_details,
            )
            all_associations.append(sunbiz_assocs)
            console.print(f"  [green]✓ {len(sunbiz_assocs)} associations from Sunbiz[/]")
        except Exception as e:
            console.print(f"  [red]✗ Sunbiz scrape failed: {e}[/]")

    # Phase 4: NC Secretary of State (also auto-load for nc-enrich/nc-propublica)
    nc_enrichment_sources = {"nc-enrich", "nc-propublica", "nc-prop", "amenities"}
    if "nc-sos" in sources or "all" in sources or (nc_enrichment_sources & set(sources)):
        console.print("\n[bold blue]═══ Phase 4: NC Secretary of State ═══[/]")
        try:
            nc_assocs = await run_nc_sos_scrape(
                fetch_details=fetch_details,
                max_details=max_details,
            )
            all_associations.append(nc_assocs)
            console.print(f"  [green]✓ {len(nc_assocs)} associations from NC SOS[/]")
        except Exception as e:
            console.print(f"  [red]✗ NC SOS scrape failed: {e}[/]")

    # Phase 5: FL Property Assessment Data (values, sqft, pools, year built)
    if "fl-prop" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 5: FL Property Assessment Data (DOR NAL) ═══[/]")
        try:
            prop_data = await run_property_enrichment(counties=counties)
            prop_path = export_property_data(prop_data, f"{output_prefix}_property.csv")
            console.print(
                f"  [green]✓ {len(prop_data)} community property profiles from FL DOR[/]"
            )
            console.print(f"  [green]✓ Exported to {prop_path}[/]")

            # Stats
            with_pools = sum(1 for d in prop_data if d.get("has_pool_or_recreation"))
            with_common = sum(1 for d in prop_data if d.get("has_common_areas"))
            with_value = sum(1 for d in prop_data if d.get("total_value"))
            console.print(f"    Communities with pool/recreation: [yellow]{with_pools}[/]")
            console.print(f"    Communities with common areas:    [yellow]{with_common}[/]")
            console.print(f"    Communities with value data:      [yellow]{with_value}[/]")
        except Exception as e:
            console.print(f"  [red]✗ FL property enrichment failed: {e}[/]")

    if not all_associations:
        console.print("[red]No association data collected from any source.[/]")
        return

    # Merge and deduplicate
    console.print("\n[bold blue]═══ Merging & Enriching ═══[/]")
    merged = merge_associations(*all_associations)
    console.print(f"  [green]✓ {len(merged)} unique associations after merge[/]")

    # Phase 6: ProPublica Nonprofit Enrichment (EIN, officers, financials)
    if "nc-propublica" in sources or "nc-enrich" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 6: ProPublica Nonprofit Enrichment ═══[/]")
        try:
            before_ein = sum(1 for a in merged if a.ein)
            merged = await run_propublica_enrichment(
                merged, max_lookups=max_details, state="NC"
            )
            after_ein = sum(1 for a in merged if a.ein)
            console.print(f"  [green]✓ ProPublica: {after_ein - before_ein} new EINs found[/]")
            with_revenue = sum(1 for a in merged if a.revenue)
            with_officers = sum(1 for a in merged if a.officers)
            console.print(f"    With revenue data: [yellow]{with_revenue}[/]")
            console.print(f"    With officers:     [yellow]{with_officers}[/]")
        except Exception as e:
            console.print(f"  [red]✗ ProPublica enrichment failed: {e}[/]")

    # Phase 7: NC Property Data Enrichment (values, sqft, year built)
    if "nc-prop" in sources or "nc-enrich" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 7: NC Property Data Enrichment ═══[/]")
        try:
            nc_prop = await run_nc_property_enrichment(counties=counties)
            if nc_prop:
                export_nc_property_data(nc_prop, f"{output_prefix}_nc_property.csv")
                merged = enrich_from_dicts(merged, nc_prop, "nc_property")
                console.print(f"  [green]✓ {len(nc_prop)} NC property community profiles[/]")
        except Exception as e:
            console.print(f"  [red]✗ NC property enrichment failed: {e}[/]")

    # Phase 8: Amenities Enrichment (pools, clubhouses from OSM)
    if "amenities" in sources or "nc-enrich" in sources or "all" in sources:
        console.print("\n[bold blue]═══ Phase 8: OSM Amenities Enrichment ═══[/]")
        try:
            amenity_map = await run_amenities_enrichment(merged, counties=counties)
            merged = apply_amenities(merged, amenity_map)
            with_amenities = sum(1 for a in merged if a.amenities)
            console.print(f"  [green]✓ {with_amenities} associations tagged with amenities[/]")
        except Exception as e:
            console.print(f"  [red]✗ Amenities enrichment failed: {e}[/]")

    # Export
    console.print("\n[bold blue]═══ Exporting ═══[/]")
    paths = export_all(merged, prefix=output_prefix)
    for fmt, path in paths.items():
        console.print(f"  [green]✓ {fmt}: {path}[/]")

    # Summary table
    _print_summary(merged)


def _print_summary(associations: list[Association]) -> None:
    """Print a summary table of the scraped data."""
    console.print("\n[bold]═══ Summary ═══[/]")

    table = Table(title="Associations by Source")
    table.add_column("Source", style="cyan")
    table.add_column("Count", justify="right", style="green")

    from collections import Counter
    source_counts = Counter(a.source for a in associations)
    for source, count in source_counts.most_common():
        table.add_row(source, str(count))
    table.add_row("[bold]Total[/]", f"[bold]{len(associations)}[/]")
    console.print(table)

    table2 = Table(title="Associations by State & Type")
    table2.add_column("State", style="cyan")
    table2.add_column("Type", style="yellow")
    table2.add_column("Count", justify="right", style="green")

    state_type_counts = Counter((a.state, a.community_type.value) for a in associations)
    for (state, ctype), count in sorted(state_type_counts.items()):
        table2.add_row(state, ctype, str(count))
    console.print(table2)

    table3 = Table(title="Management Status")
    table3.add_column("Status", style="cyan")
    table3.add_column("Count", justify="right", style="green")

    mgmt_counts = Counter(a.management_status.value for a in associations)
    for status, count in mgmt_counts.most_common():
        table3.add_row(status, str(count))
    console.print(table3)

    table4 = Table(title="Filing Status")
    table4.add_column("Status", style="cyan")
    table4.add_column("Count", justify="right", style="green")

    filing_counts = Counter(a.filing_status.value for a in associations)
    for status, count in filing_counts.most_common():
        table4.add_row(status, str(count))
    console.print(table4)

    # Key prospecting stats
    self_managed = sum(1 for a in associations if a.management_status.value == "self_managed")
    delinquent = sum(1 for a in associations if a.filing_status.value == "delinquent")
    with_units = sum(1 for a in associations if a.unit_count and a.unit_count > 0)
    with_officers = sum(1 for a in associations if a.officers)

    with_ein = sum(1 for a in associations if a.ein)
    with_revenue = sum(1 for a in associations if a.revenue)
    with_value = sum(1 for a in associations if a.total_property_value)
    with_amenities = sum(1 for a in associations if a.amenities)
    with_enrichment = sum(1 for a in associations if a.enrichment_sources)

    console.print(f"\n[bold]Key Prospecting Signals:[/]")
    console.print(f"  Self-managed communities: [yellow]{self_managed}[/]")
    console.print(f"  Delinquent filings:       [yellow]{delinquent}[/]")
    console.print(f"  With unit counts:         [yellow]{with_units}[/]")
    console.print(f"  With board contacts:      [yellow]{with_officers}[/]")
    console.print(f"\n[bold]Enrichment Coverage:[/]")
    console.print(f"  With EIN:                 [yellow]{with_ein}[/]")
    console.print(f"  With revenue data:        [yellow]{with_revenue}[/]")
    console.print(f"  With property value:      [yellow]{with_value}[/]")
    console.print(f"  With amenities tagged:    [yellow]{with_amenities}[/]")
    console.print(f"  Enriched (any source):    [yellow]{with_enrichment}[/]")


def main():
    parser = argparse.ArgumentParser(description="HOA Crawl — Data enrichment pipeline")
    parser.add_argument(
        "--source",
        choices=[
            "fl-dbpr", "fl-sunbiz", "fl-hoa", "nc-sos", "fl-prop",
            "nc-propublica", "nc-prop", "amenities", "nc-enrich",
            "all",
        ],
        default="fl-dbpr",
        help="Which data source to scrape (default: fl-dbpr)",
    )
    parser.add_argument(
        "--counties",
        type=str,
        default=None,
        help="Comma-separated county names for fl-prop (e.g. 'Broward,Palm Beach')",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=None,
        help="Max detail pages to fetch (for testing)",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip fetching detail pages",
    )
    parser.add_argument(
        "--output",
        default="hoa_data",
        help="Output file prefix",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    setup_logging(args.verbose)

    sources = [args.source] if args.source != "all" else ["all"]
    counties = [c.strip() for c in args.counties.split(",")] if args.counties else None

    console.print(f"[bold]HOA Crawl Pipeline[/] — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    console.print(f"Sources: {sources}")

    asyncio.run(
        run_pipeline(
            sources=sources,
            max_details=args.max_details,
            fetch_details=not args.no_details,
            output_prefix=args.output,
            counties=counties,
        )
    )


if __name__ == "__main__":
    main()
