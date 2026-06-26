# HOA Crawl

HOA data enrichment pipeline for ArborKey prospecting. Scrapes publicly available HOA/condo/cooperative association data from government registrations and property assessment rolls.

## Architecture

- `main.py` — CLI entry point with `--source` flag to select data sources
- `src/scrapers/` — One scraper per data source
  - `fl_dbpr_bulk.py` — Downloads DBPR bulk CSVs (condos, coops, CAMs, payment history)
  - `fl_dbpr_search.py` — Scrapes DBPR license search for HOA registrations
  - `fl_sunbiz.py` — Scrapes Sunbiz.org for corporate details (officers, EIN, filing status)
  - `fl_property.py` — FL DOR property assessment data (values, sqft, pools, year built)
  - `nc_sos.py` — Downloads NC SOS nonprofit-by-county reports, filters for HOAs
- `src/models/association.py` — Pydantic data model (normalized output format)
- `src/enrichment/merger.py` — Cross-references and deduplicates records across sources
- `src/utils/` — HTTP client, SQLite storage, export (CSV/JSON/SQLite)
- `config/settings.py` — URLs, county lists, HTTP settings

## Running

```bash
python main.py --source fl-dbpr      # FL DBPR bulk CSVs (fastest, ~28k records)
python main.py --source nc-sos       # NC SOS nonprofits (~22k HOA entities)
python main.py --source fl-prop      # FL property assessment (values, pools, sqft)
python main.py --source fl-prop --counties "Broward,Palm Beach"  # Specific counties
python main.py --source fl-sunbiz    # FL Sunbiz corporate search
python main.py --source fl-hoa       # FL DBPR HOA license search
python main.py --source all          # Everything
```

## Data sources

- **FL DBPR bulk CSVs** — Direct government downloads, richest data (unit counts, management companies)
- **NC SOS nonprofit reports** — Free county-by-county reports, no Cloudflare issues
- **FL DOR NAL files** — Free property assessment rolls with 165 columns per parcel
- **FL Sunbiz** — Requires scraping, may get 403 (Cloudflare)
- **NC SOS search** — Behind Cloudflare Turnstile, use nonprofit reports instead

## Output

- `data/output/*.csv` — Flat CSVs for prospecting
- `data/output/*.json` — Structured JSON
- `data/hoa_crawl.db` — SQLite database
- `data/output/*_property.csv` — Property enrichment data
