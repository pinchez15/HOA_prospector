"""Central configuration for HOA Crawl scrapers."""

from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "hoa_crawl.db"

# HTTP defaults
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_WAIT = 2  # seconds between retries
# Be polite — pause between requests to avoid hammering public servers
REQUEST_DELAY = 1.0  # seconds between requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Florida DBPR
FL_DBPR_SEARCH_URL = "https://www.myfloridalicense.com/wl11.asp"
FL_DBPR_DETAIL_URL = "https://www.myfloridalicense.com/LicenseDetail.asp"

# Florida Sunbiz
FL_SUNBIZ_SEARCH_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchByName"
FL_SUNBIZ_DETAIL_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResultDetail"

# North Carolina Secretary of State
NC_SOS_SEARCH_URL = "https://www.sosnc.gov/online_services/search/by_title/_Business_Registration"

# Florida target — scrape all counties
FL_COUNTIES = [
    "Alachua", "Baker", "Bay", "Bradford", "Brevard", "Broward", "Calhoun",
    "Charlotte", "Citrus", "Clay", "Collier", "Columbia", "DeSoto", "Dixie",
    "Duval", "Escambia", "Flagler", "Franklin", "Gadsden", "Gilchrist",
    "Glades", "Gulf", "Hamilton", "Hardee", "Hendry", "Hernando", "Highlands",
    "Hillsborough", "Holmes", "Indian River", "Jackson", "Jefferson",
    "Lafayette", "Lake", "Lee", "Leon", "Levy", "Liberty", "Madison",
    "Manatee", "Marion", "Martin", "Miami-Dade", "Monroe", "Nassau",
    "Okaloosa", "Okeechobee", "Orange", "Osceola", "Palm Beach", "Pasco",
    "Pinellas", "Polk", "Putnam", "Santa Rosa", "Sarasota", "Seminole",
    "St. Johns", "St. Lucie", "Sumter", "Suwannee", "Taylor", "Union",
    "Volusia", "Wakulla", "Walton", "Washington",
]

# North Carolina target counties (priority)
NC_PRIORITY_COUNTIES = {
    "wilmington_coastal": ["New Hanover", "Brunswick", "Pender", "Onslow"],
    "charlotte": ["Mecklenburg", "Union", "Cabarrus"],
    "raleigh_durham": ["Wake", "Durham", "Orange"],
}

# ProPublica Nonprofit Explorer API (free, no auth)
PROPUBLICA_SEARCH_URL = "https://projects.propublica.org/nonprofits/api/v2/search.json"
PROPUBLICA_ORG_URL = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
PROPUBLICA_DELAY = 1.0

# HUD FHA Approved Condos
HUD_CONDO_LOOKUP_URL = "https://entp.hud.gov/idapp/html/condlook.cfm"

# NC County Property Data Sources (free bulk downloads)
NC_PROPERTY_SOURCES = {
    "Wake": {
        "url": "https://www.wake.gov/departments-government/tax-administration/data-files-statistics-and-reports/real-estate-property-data-files",
        "format": "csv",
    },
}

# Overpass API (OpenStreetMap amenities — free, no auth)
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_DELAY = 2.0

# County bounding boxes (south, west, north, east) for Overpass queries
COUNTY_BBOXES = {
    # NC priority counties
    "NC:Wake": (35.517, -78.949, 35.976, -78.276),
    "NC:Mecklenburg": (35.003, -81.062, 35.510, -80.550),
    "NC:Durham": (35.867, -79.008, 36.242, -78.696),
    "NC:Orange": (35.881, -79.542, 36.236, -79.010),
    "NC:New Hanover": (33.978, -77.956, 34.336, -77.679),
    "NC:Brunswick": (33.763, -78.649, 34.269, -77.894),
    "NC:Pender": (34.198, -78.260, 34.650, -77.669),
    "NC:Onslow": (34.417, -77.741, 34.850, -77.094),
    "NC:Union": (34.748, -80.784, 35.133, -80.275),
    "NC:Cabarrus": (35.227, -80.707, 35.504, -80.282),
    # FL major counties
    "FL:Broward": (25.957, -80.468, 26.331, -80.073),
    "FL:Dade": (25.237, -80.873, 25.979, -80.118),
    "FL:Palm Beach": (26.319, -80.885, 26.969, -80.031),
    "FL:Hillsborough": (27.573, -82.819, 28.174, -82.053),
    "FL:Orange": (28.340, -81.660, 28.791, -80.960),
    "FL:Pinellas": (27.598, -82.848, 28.175, -82.538),
    "FL:Sarasota": (27.071, -82.574, 27.381, -82.061),
}
