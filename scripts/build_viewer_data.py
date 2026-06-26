#!/usr/bin/env python3
"""Export enriched prospect data as static JSON for the Vercel viewer."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from viewer.server import load_csv  # noqa: E402

OUTPUT_DIR = ROOT / "viewer" / "data"


def build_counties(rows: list[dict]) -> list[dict]:
    counts = Counter()
    for row in rows:
        county = (row.get("county") or "").strip()
        if county and county != "ALL":
            counts[county] += 1
    return [{"name": name, "count": count} for name, count in counts.most_common()]


def select_demo_rows(rows: list[dict], limit: int) -> list[dict]:
    """Prefer records with unit counts and property enrichment for demos."""
    scored = []
    for row in rows:
        score = 0
        if row.get("unit_count"):
            score += 2
        if row.get("property_value"):
            score += 3
        if row.get("has_pool") or row.get("has_common_areas"):
            score += 1
        if row.get("management_company_name"):
            score += 1
        scored.append((score, row))

    scored.sort(key=lambda item: (-item[0], item[1].get("community_name") or ""))
    return [row for _, row in scored[:limit]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static JSON for the prospect viewer")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Export a curated demo subset instead of the full dataset",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max records when using --demo (default: 5000)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading and enriching prospect data...")
    rows = load_csv()

    if args.demo:
        rows = select_demo_rows(rows, args.limit)
        print(f"  Demo mode: exporting top {len(rows)} records")

    counties = build_counties(rows)

    prospects_path = OUTPUT_DIR / "prospects.json"
    counties_path = OUTPUT_DIR / "counties.json"

    with prospects_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)

    with counties_path.open("w", encoding="utf-8") as f:
        json.dump(counties, f, ensure_ascii=False)

    size_mb = prospects_path.stat().st_size / (1024 * 1024)
    print(f"  Wrote {len(rows):,} prospects -> {prospects_path} ({size_mb:.1f} MB)")
    print(f"  Wrote {len(counties)} counties -> {counties_path}")


if __name__ == "__main__":
    main()
