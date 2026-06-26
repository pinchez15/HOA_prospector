"""Lightweight localhost viewer for HOA prospect data."""

import csv
import json
import re
from collections import defaultdict
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "output" / "combined_hoa_data.csv"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"
VIEWER_DIR = Path(__file__).parent

PROPERTY_FILES = [
    "broward_test_property.csv",
    "fl_property_big_property.csv",
    "fl_property_data_property.csv",
    "fl_property_extra_property.csv",
    "nc_final_nc_property.csv",
    "nc_full_nc_property.csv",
]

# Words that suggest the owner is an HOA/condo association
HOA_KEYWORDS = {
    "CONDO", "CONDOMINIUM", "HOA", "HOMEOWNER", "HOMEOWNERS",
    "ASSOCIATION", "ASSN", "ASSOC", "CLUB", "VILLAS", "VILLAGE",
    "ESTATES", "TOWERS", "MANOR", "COMMONS", "COOPERATIVE", "CO-OP",
    "TOWNHOME", "TOWNHOUSE", "COMMUNITY", "PROPERTY OWNERS",
    "LANDING", "POINTE", "PLAZA", "TERRACE", "RESIDENCES",
}

# Noise words to strip for fuzzy name matching
NOISE_WORDS = {
    "A", "AN", "THE", "OF", "AT", "IN", "INC", "LLC", "LTD", "CORP",
    "CORPORATION", "COMPANY", "CO", "AND", "&", "NO", "PHASE",
    "UNIT", "SECTION", "SEC", "BLDG", "BUILDING",
}


def _int_or_none(v):
    if not v:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _extract_zip(addr):
    """Pull 5-digit zip from an address string."""
    if not addr:
        return None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", addr)
    return m.group(1) if m else None


def _normalize_name(name):
    """Strip noise words and punctuation, return sorted set of tokens for comparison."""
    if not name:
        return set()
    name = re.sub(r"[^A-Z0-9\s]", "", name.upper())
    tokens = set(name.split()) - NOISE_WORDS
    # Remove pure numbers (phase numbers, unit numbers)
    tokens = {t for t in tokens if not t.isdigit()}
    return tokens


def _extract_street_number(addr):
    """Extract leading street number from an address."""
    if not addr:
        return None
    m = re.match(r"(\d+)\s", addr.strip())
    return m.group(1) if m else None


def _normalize_street(addr):
    """Extract and normalize street name tokens from an address."""
    if not addr:
        return set()
    # Take just the street line (before city/state/zip)
    # Property data: "123 MAIN ST" (no city)
    # HOA data: "123 MAIN ST, CITY, ST 12345"
    street = addr.split(",")[0].strip().upper()
    street = re.sub(r"[^A-Z0-9\s]", "", street)
    tokens = set(street.split())
    # Remove pure numbers and directionals
    tokens -= {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "ST", "RD", "AVE",
               "BLVD", "DR", "CT", "LN", "PL", "WAY", "CIR", "HWY", "HIGHWAY",
               "ROAD", "DRIVE", "STREET", "AVENUE", "BOULEVARD", "COURT", "LANE",
               "PLACE", "CIRCLE", "TRAIL", "TRL", "PKWY", "PARKWAY", "FL", "US"}
    tokens = {t for t in tokens if not t.isdigit()}
    return tokens


def _name_similarity(tokens_a, tokens_b):
    """Jaccard-ish similarity between two token sets."""
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def _is_hoa_like(owner_name):
    """Check if a property owner name looks like an HOA/community."""
    words = set(owner_name.upper().split())
    return bool(words & HOA_KEYWORDS)


def load_property_records():
    """Load property records, indexed by zip and county for fuzzy matching."""
    by_zip = defaultdict(list)
    by_county = defaultdict(list)
    by_name = {}
    total = 0
    kept = 0

    for fname in PROPERTY_FILES:
        fpath = OUTPUT_DIR / fname
        if not fpath.exists():
            continue
        with open(fpath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total += 1
                owner = (row.get("owner_name") or "").strip()
                if not owner:
                    continue

                # Filter to likely HOA/community owners OR records with multiple parcels/units
                parcels = _int_or_none(row.get("total_parcels"))
                units = _int_or_none(row.get("total_units"))
                is_multi = (parcels is not None and parcels >= 3) or (units is not None and units >= 3)
                if not _is_hoa_like(owner) and not is_multi:
                    continue

                kept += 1
                rec = {
                    "owner_name": owner,
                    "has_pool": row.get("has_pool_or_recreation", "") == "yes",
                    "has_common_areas": row.get("has_common_areas", "") == "yes",
                    "total_value": _int_or_none(row.get("total_value")),
                    "avg_value": _int_or_none(row.get("avg_value")),
                    "median_value": _int_or_none(row.get("median_value")),
                    "avg_sqft": _int_or_none(row.get("avg_living_area_sqft")),
                    "avg_year_built": _int_or_none(row.get("avg_year_built")),
                    "oldest_year_built": _int_or_none(row.get("oldest_year_built")),
                    "total_parcels": _int_or_none(row.get("total_parcels")),
                    "total_units": _int_or_none(row.get("total_units")),
                    "total_buildings": _int_or_none(row.get("total_buildings")),
                    "avg_sale_price": _int_or_none(row.get("avg_sale_price")),
                    "max_sale_price": _int_or_none(row.get("max_sale_price")),
                    "prop_address": (row.get("physical_address") or "").strip(),
                    "prop_city": (row.get("physical_city") or "").strip(),
                    "prop_zip": (row.get("physical_zip") or "").strip()[:5],
                    "prop_county": (row.get("county_name") or "").strip(),
                    "_name_tokens": _normalize_name(owner),
                    "_street_tokens": _normalize_street(row.get("physical_address", "")),
                    "_street_num": _extract_street_number(row.get("physical_address", "")),
                }

                # Index by zip
                if rec["prop_zip"]:
                    by_zip[rec["prop_zip"]].append(rec)

                # Index by county
                if rec["prop_county"]:
                    by_county[rec["prop_county"].upper()].append(rec)

                # Index by exact name
                by_name[owner.upper()] = rec

    print(f"  Property: {total} total -> {kept} HOA-like records kept")
    return by_zip, by_county, by_name


def find_best_match(hoa_row, by_zip, by_county, by_name):
    """Find the best property record match for an HOA record."""
    community = (hoa_row.get("community_name") or "").strip()
    hoa_addr = hoa_row.get("physical_address") or ""
    hoa_county = (hoa_row.get("county") or "").strip()
    hoa_zip = _extract_zip(hoa_addr)

    hoa_name_tokens = _normalize_name(community)
    hoa_street_tokens = _normalize_street(hoa_addr)
    hoa_street_num = _extract_street_number(hoa_addr.split(",")[0].strip())

    # 1. Exact name match
    exact = by_name.get(community.upper())
    if exact:
        return exact, 1.0

    best_match = None
    best_score = 0.0

    # 2. Search candidates by zip code (best geographic match)
    candidates = []
    if hoa_zip:
        candidates = by_zip.get(hoa_zip, [])

    # 3. If no zip candidates, fall back to county
    if not candidates and hoa_county:
        candidates = by_county.get(hoa_county.upper(), [])

    for prop in candidates:
        score = 0.0

        # Name similarity (most important)
        name_sim = _name_similarity(hoa_name_tokens, prop["_name_tokens"])
        score += name_sim * 0.6

        # Street address similarity
        street_sim = _name_similarity(hoa_street_tokens, prop["_street_tokens"])
        score += street_sim * 0.25

        # Street number exact match bonus
        if hoa_street_num and prop["_street_num"] and hoa_street_num == prop["_street_num"]:
            score += 0.15

        if score > best_score:
            best_score = score
            best_match = prop

    # Only accept matches above threshold
    if best_score >= 0.25:
        return best_match, best_score

    return None, 0.0


def load_csv():
    print("Loading property data for enrichment...")
    by_zip, by_county, by_name = load_property_records()
    print(f"  Indexed by {len(by_zip)} zip codes, {len(by_county)} counties")

    rows = []
    matched = 0
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            uc = row.get("unit_count", "")
            row["unit_count"] = int(uc) if uc and uc.isdigit() else None
            row["_id"] = i

            # Fuzzy-match to property data
            prop, score = find_best_match(row, by_zip, by_county, by_name)
            if prop:
                matched += 1
                row["has_pool"] = prop["has_pool"]
                row["has_common_areas"] = prop["has_common_areas"]
                row["property_value"] = prop["total_value"]
                row["avg_unit_value"] = prop["avg_value"]
                row["median_value"] = prop["median_value"]
                row["avg_sqft"] = prop["avg_sqft"]
                row["avg_year_built"] = prop["avg_year_built"]
                row["oldest_year_built"] = prop["oldest_year_built"]
                row["total_parcels"] = prop["total_parcels"]
                row["total_buildings"] = prop["total_buildings"]
                row["total_units_prop"] = prop["total_units"]
                row["avg_sale_price"] = prop["avg_sale_price"]
                row["max_sale_price"] = prop["max_sale_price"]
                row["prop_match_score"] = round(score, 2)
                row["prop_match_name"] = prop["owner_name"]
            else:
                row["has_pool"] = False
                row["has_common_areas"] = False
                row["property_value"] = None
                row["avg_unit_value"] = None
                row["median_value"] = None
                row["avg_sqft"] = None
                row["avg_year_built"] = None
                row["oldest_year_built"] = None
                row["total_parcels"] = None
                row["total_buildings"] = None
                row["total_units_prop"] = None
                row["avg_sale_price"] = None
                row["max_sale_price"] = None
                row["prop_match_score"] = 0
                row["prop_match_name"] = None

            rows.append(row)

            if (i + 1) % 10000 == 0:
                print(f"  Processed {i + 1} HOA records...")

    print(f"  {matched} / {len(rows)} prospects enriched with property data")
    return rows


class ViewerHandler(SimpleHTTPRequestHandler):
    data_cache = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/api/data":
            self.send_json(self._get_data())
        elif self.path == "/api/counties":
            data = self._get_data()
            counties = {}
            for r in data:
                c = r.get("county", "") or ""
                if c and c != "ALL":
                    counties[c] = counties.get(c, 0) + 1
            result = sorted(counties.items(), key=lambda x: -x[1])
            self.send_json([{"name": k, "count": v} for k, v in result])
        elif self.path == "/":
            self.path = "/viewer.html"
            super().do_GET()
        else:
            super().do_GET()

    def send_json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _get_data(self):
        if ViewerHandler.data_cache is None:
            ViewerHandler.data_cache = load_csv()
        return ViewerHandler.data_cache


def main():
    port = 8080
    server = HTTPServer(("127.0.0.1", port), ViewerHandler)
    print(f"ArborKey Prospect Viewer running at http://localhost:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
