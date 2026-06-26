# HOA Crawl

Public-data pipeline and prospect viewer for HOA / condo / cooperative association research. Built for ArborKey client evaluation and sales demos.

## What's included

- **Data pipeline** — CLI scrapers for FL DBPR, NC SOS, property assessment rolls, and enrichment sources
- **Prospect viewer** — Static web UI for filtering, sorting, and drilling into HOA leads
- **Demo deployment** — Pre-built JSON dataset suitable for Vercel hosting without a backend

## Quick start (local pipeline)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Fastest source (~28k FL condo/coop records)
python main.py --source fl-dbpr

# NC nonprofits (~22k HOA entities)
python main.py --source nc-sos
```

Outputs land in `data/output/` (gitignored). See `CLAUDE.md` for full source list.

## Prospect viewer

### Local (with live enrichment)

Requires pipeline output in `data/output/`:

```bash
python viewer/server.py
# open http://localhost:8080
```

The local server enriches prospects on the fly from property assessment CSVs.

### Static / Vercel

Build a static JSON bundle for deployment:

```bash
python scripts/build_viewer_data.py --demo   # 5k curated records for demos
python scripts/build_viewer_data.py          # full enriched dataset (large)
```

This writes `viewer/data/prospects.json` and `viewer/data/counties.json`. The viewer loads these files directly — no Python server required.

## Deploy to Vercel

1. Push this repo to GitHub
2. Import the project in [Vercel](https://vercel.com/new)
3. Use default settings — `vercel.json` sets `outputDirectory` to `viewer`
4. Deploy

The committed demo JSON in `viewer/data/` is enough for the hosted demo. Re-run the build script locally and push when you want refreshed data.

## Project layout

```
main.py                 CLI entry point
config/settings.py      URLs, county lists, HTTP settings
src/scrapers/           One module per data source
src/enrichment/         Cross-source merge and enrichment
src/models/             Normalized Association schema
viewer/                 Static prospect UI + optional local server
scripts/                Viewer data export
```

## Data sources

| Source | Records | Notes |
|--------|---------|-------|
| FL DBPR bulk CSVs | ~28k | Condos, coops, CAMs — richest registration data |
| NC SOS nonprofits | ~22k | County reports, no Cloudflare |
| FL DOR NAL files | varies | Property values, sqft, pools, year built |
| FL Sunbiz | varies | Corporate details; may hit 403 |

## License

Private — ArborKey internal / client demo use.
