"""
Visualization Data Preparation
================================
Reads all processed pipeline outputs and writes vis/vis_data.js —
a single JavaScript file embedding all data needed by vis/index.html.

Run after dept_housing.py and geo_ripper.py:
    python3 GIS/prepare_viz_data.py

Outputs:
    vis/vis_data.js  — embedded JS constants for the visualization
"""

import ast
import csv
import json
import math
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT    = Path(__file__).resolve().parent.parent
GIS_OUT = Path(__file__).resolve().parent / "processed"
VIS_DIR = ROOT / "vis"
VIS_DIR.mkdir(exist_ok=True)

IN_ENRICHED    = GIS_OUT / "dissertations_geo_enriched.csv"
IN_DEPT_GEO    = GIS_OUT / "departments_with_buildings.geojson"
IN_LOCATIONS   = ROOT / "uchicago_locations.csv"
IN_FOOTPRINT   = ROOT / "uchicago-property-footprint-2-28-25.geojson"
OUT_JS         = VIS_DIR / "vis_data.js"
OUT_WORLD_JS   = VIS_DIR / "world_data.js"
OUT_DISS_INDEX = VIS_DIR / "diss_index.js"
WORLD_CACHE    = VIS_DIR / "world.geojson"

CAMPUS_CENTER  = (41.7890, -87.5993)   # center of main quad


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def parse_list_field(raw):
    if not raw or not raw.strip():
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            return [str(i).strip() for i in ast.literal_eval(raw) if i]
        except Exception:
            pass
    return [raw.strip()]


def polygon_centroid(geometry):
    """Return [lat, lon] centroid of a GeoJSON Polygon or MultiPolygon."""
    if geometry["type"] == "Polygon":
        ring = geometry["coordinates"][0]
    elif geometry["type"] == "MultiPolygon":
        ring = max(geometry["coordinates"], key=lambda p: len(p[0]))[0]
    else:
        return None
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if not pts:
        return None
    lons = [c[0] for c in pts]
    lats = [c[1] for c in pts]
    return [sum(lats) / len(lats), sum(lons) / len(lons)]


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def campus_angle(lat, lon):
    """Clockwise degrees from north, campus-center origin."""
    dlat = lat - CAMPUS_CENTER[0]
    dlon = (lon - CAMPUS_CENTER[1]) * math.cos(math.radians(CAMPUS_CENTER[0]))
    deg  = math.degrees(math.atan2(dlon, dlat))
    return deg % 360


def _collapse(s):
    """Normalize spacing/commas for loose string comparison."""
    return re.sub(r"[,\s]+", " ", s or "").strip().lower()


# Populated by build_interdisciplinary_data after locations are loaded
_CANONICAL_DEPT_NORMS: set = set()   # collapsed strings
_CANONICAL_DEPT_WORDSETS: set = set() # frozensets of words (catches reversed names)


def is_joint_program(raw, modern):
    """True if the dissertation was filed under a joint/dual program."""
    if not raw:
        return False
    raw_collapsed = _collapse(raw)
    # Normalize away comma/spacing differences before comparing to modern
    if raw_collapsed == _collapse(modern):
        return False
    # If raw matches any canonical dept name (direct or word-order variant)
    # it's a single dept filed under an alternate form, not a joint program.
    if _CANONICAL_DEPT_NORMS:
        if raw_collapsed in _CANONICAL_DEPT_NORMS:
            return False
        raw_words = frozenset(w for w in raw_collapsed.split() if w != "and")
        if raw_words in _CANONICAL_DEPT_WORDSETS:
            return False
    if raw.startswith("Interdisciplinary Scientist"):
        return False
    parts = raw.split(" and ")
    if len(parts) >= 2:
        first = parts[0].strip()
        if first and first[0].isupper() and len(first) > 3:
            return True
    return "&" in raw


# ---------------------------------------------------------------------------
# Country normalization
# ---------------------------------------------------------------------------

COUNTRY_NORMALIZE = {
    # United States variants
    "the united states": "United States",
    "united states":     "United States",
    "u.s":               "United States",
    "u.s.":              "United States",
    "u.s.a":             "United States",
    "u.s.a.":            "United States",
    "us":                "United States",   # two-letter abbrev title-cased to "Us"
    "usa":               "United States",
    "america":           "United States",
    # United Kingdom
    "england":           "United Kingdom",
    "great britain":     "United Kingdom",
    "britain":           "United Kingdom",
    "scotland":          "United Kingdom",
    "wales":             "United Kingdom",
    "northern ireland":  "United Kingdom",
    "uk":                "United Kingdom",
    # Russia / Soviet
    "ussr":              "Russia",
    "soviet union":      "Russia",
    "the soviet union":  "Russia",
    "soviet russia":     "Russia",
    # Germany
    "west germany":      "Germany",
    "east germany":      "Germany",
    "prussia":           "Germany",
    "weimar republic":   "Germany",
    # Netherlands
    "the netherlands":   "Netherlands",
    "holland":           "Netherlands",
    # Germany language/historical variants
    "deutschland":       "Germany",
    "german empire":     "Germany",
    "federal republic of germany": "Germany",
    # Other name variants
    "iran":              "Iran",
    "persia":            "Iran",
    "czechia":           "Czech Republic",
    "czechoslovakia":    "Czech Republic",
    "korea":             "South Korea",
    "south korea":       "South Korea",
    "north korea":       "North Korea",
    "the philippines":   "Philippines",
    "burma":             "Myanmar",
    "rhodesia":          "Zimbabwe",
    "formosa":           "Taiwan",
    "nationalist china": "Taiwan",
    "ceylon":            "Sri Lanka",
    "siam":              "Thailand",
    "abyssinia":         "Ethiopia",
    "the sudan":         "Sudan",
    "the congo":         "Dem. Rep. Congo",
    "ivory coast":       "Côte d'Ivoire",
    "zaire":             "Dem. Rep. Congo",
    "tibet":             "China",
    "the dominican republic": "Dominican Republic",
    "upper egypt":       "Egypt",
    "babylonia":         "Iraq",
    "sparta":            "Greece",       # ancient city-state, now Greece
    "palestine":         "Palestinian Territories",
    "hong kong":         "China",
    "macau":             "China",
    "yugoslavia":        "Serbia",
    "austro-hungarian empire": "Austria",
    "habsburg empire":   "Austria",
    "ottoman empire":    "Turkey",
    "the ottoman empire": "Turkey",
    "anatolia":          "Turkey",
    "byzantine empire":  "Turkey",
    "mesopotamia":       "Iraq",
    "arabia":            "Saudi Arabia",
    "manchuria":         "China",
    "bengal":            "India",
    "punjab":            "India",
    "catalonia":         "Spain",
    "andalusia":         "Spain",
    "sicily":            "Italy",
    "bohemia":           "Czech Republic",
    "transylvania":      "Romania",
    "crimea":            "Ukraine",
    "siberia":           "Russia",
    "quebec":            "Canada",
    "ontario":           "Canada",
    "british columbia":  "Canada",
}

# Cities → country (applied after COUNTRY_NORMALIZE lookup fails)
CITY_TO_COUNTRY = {
    # Europe
    "athens":        "Greece",
    "vienna":        "Austria",
    "amsterdam":     "Netherlands",
    "brussels":      "Belgium",
    "stockholm":     "Sweden",
    "copenhagen":    "Denmark",
    "oslo":          "Norway",
    "helsinki":      "Finland",
    "lisbon":        "Portugal",
    "dublin":        "Ireland",
    "prague":        "Czech Republic",
    "budapest":      "Hungary",
    "warsaw":        "Poland",
    "krakow":        "Poland",
    "bucharest":     "Romania",
    "sofia":         "Bulgaria",
    "belgrade":      "Serbia",
    "zagreb":        "Croatia",
    "madrid":        "Spain",
    "barcelona":     "Spain",
    "milan":         "Italy",
    "naples":        "Italy",
    "florence":      "Italy",
    "venice":        "Italy",
    "zurich":        "Switzerland",
    "geneva":        "Switzerland",
    "bern":          "Switzerland",
    "munich":        "Germany",
    "hamburg":       "Germany",
    "frankfurt":     "Germany",
    "heidelberg":    "Germany",
    "cologne":       "Germany",
    "kyiv":          "Ukraine",
    "kiev":          "Ukraine",
    "lviv":          "Ukraine",
    "riga":          "Latvia",
    "vilnius":       "Lithuania",
    "tallinn":       "Estonia",
    "sarajevo":      "Bosnia and Herzegovina",
    "thessaloniki":  "Greece",
    "thessaly":      "Greece",
    # Middle East
    "jerusalem":     "Israel",
    "tel aviv":      "Israel",
    "haifa":         "Israel",
    "damascus":      "Syria",
    "aleppo":        "Syria",
    "baghdad":       "Iraq",
    "tehran":        "Iran",
    "beirut":        "Lebanon",
    "amman":         "Jordan",
    "riyadh":        "Saudi Arabia",
    "mecca":         "Saudi Arabia",
    "medina":        "Saudi Arabia",
    "ankara":        "Turkey",
    "istanbul":      "Turkey",
    "dubai":         "United Arab Emirates",
    "abu dhabi":     "United Arab Emirates",
    "muscat":        "Oman",
    "kuwait city":   "Kuwait",
    "cairo":         "Egypt",
    "alexandria":    "Egypt",
    "tunis":         "Tunisia",
    "algiers":       "Algeria",
    "casablanca":    "Morocco",
    "rabat":         "Morocco",
    "tripoli":       "Libya",
    "khartoum":      "Sudan",
    # Asia
    "beijing":       "China",
    "shanghai":      "China",
    "nanjing":       "China",
    "guangzhou":     "China",
    "wuhan":         "China",
    "chengdu":       "China",
    "xi'an":         "China",
    "taipei":        "Taiwan",
    "tokyo":         "Japan",
    "osaka":         "Japan",
    "kyoto":         "Japan",
    "nagoya":        "Japan",
    "hiroshima":     "Japan",
    "nara":          "Japan",
    "seoul":         "South Korea",
    "busan":         "South Korea",
    "mumbai":        "India",
    "bombay":        "India",
    "delhi":         "India",
    "calcutta":      "India",
    "kolkata":       "India",
    "bangalore":     "India",
    "hyderabad":     "India",
    "madras":        "India",
    "chennai":       "India",
    # Indian states and regions
    "gujarat":       "India",
    "tamil nadu":    "India",
    "rajasthan":     "India",
    "kerala":        "India",
    "maharashtra":   "India",
    "uttar pradesh": "India",
    "west bengal":   "India",
    "kashmir":       "India",
    "lahore":        "Pakistan",
    "karachi":       "Pakistan",
    "islamabad":     "Pakistan",
    "dhaka":         "Bangladesh",
    "colombo":       "Sri Lanka",
    "kathmandu":     "Nepal",
    "rangoon":       "Myanmar",
    "yangon":        "Myanmar",
    "bangkok":       "Thailand",
    "hanoi":         "Vietnam",
    "ho chi minh city": "Vietnam",
    "saigon":        "Vietnam",
    "phnom penh":    "Cambodia",
    "jakarta":       "Indonesia",
    "surabaya":      "Indonesia",
    "manila":        "Philippines",
    "singapore":     "Singapore",
    "kuala lumpur":  "Malaysia",
    "ulaanbaatar":   "Mongolia",
    # Africa
    "nairobi":       "Kenya",
    "mombasa":       "Kenya",
    "lagos":         "Nigeria",
    "abuja":         "Nigeria",
    "accra":         "Ghana",
    "dar es salaam": "Tanzania",
    "addis ababa":   "Ethiopia",
    "johannesburg":  "South Africa",
    "cape town":     "South Africa",
    "durban":        "South Africa",
    "kinshasa":      "Democratic Republic of the Congo",
    "dakar":         "Senegal",
    "abidjan":       "Ivory Coast",
    "kampala":       "Uganda",
    "lusaka":        "Zambia",
    "harare":        "Zimbabwe",
    "maputo":        "Mozambique",
    "luanda":        "Angola",
    # Americas
    "mexico city":   "Mexico",
    "guadalajara":   "Mexico",
    "monterrey":     "Mexico",
    "havana":        "Cuba",
    "kingston":      "Jamaica",
    "santo domingo": "Dominican Republic",
    "san juan":      "Puerto Rico",
    "bogota":        "Colombia",
    "medellin":      "Colombia",
    "caracas":       "Venezuela",
    "lima":          "Peru",
    "quito":         "Ecuador",
    "santiago":      "Chile",
    "buenos aires":  "Argentina",
    "montevideo":    "Uruguay",
    "asuncion":      "Paraguay",
    "la paz":        "Bolivia",
    "rio de janeiro": "Brazil",
    "sao paulo":     "Brazil",
    "brasilia":      "Brazil",
    "montreal":      "Canada",
    "toronto":       "Canada",
    "vancouver":     "Canada",
    "ottawa":        "Canada",
    # Additional cities surfaced during normalization review
    "weimar":        "Germany",
    "dresden":       "Germany",
    "oaxaca":        "Mexico",
    "veracruz":      "Mexico",
    "guadalupe":     "Mexico",
    "antioch":       "Turkey",       # ancient Antioch (modern Antakya)
    "clairvaux":     "France",
    "tiwanaku":      "Bolivia",
    "toledo":        "Spain",
    "cordoba":       "Spain",
    "seville":       "Spain",
    "grenada":       "Spain",
    "florence":      "Italy",
    "palermo":       "Italy",
    "bologna":       "Italy",
    "edinburgh":     "United Kingdom",
    "oxford":        "United Kingdom",
    "cambridge":     "United Kingdom",
    "glasgow":       "United Kingdom",
    "liverpool":     "United Kingdom",
    "manchester":    "United Kingdom",
    # Major European capitals — moved here FROM EXCLUDE_GPE so they map correctly
    "london":        "United Kingdom",
    "paris":         "France",
    "berlin":        "Germany",
    "rome":          "Italy",
    "moscow":        "Russia",
    "vienna":        "Austria",
    "madrid":        "Spain",
    "athens":        "Greece",
    # Ancient / historical place names — map to modern successor state
    "assyria":       "Iraq",          # Neo-Assyrian empire, core in Tigris valley
    "babylonia":     "Iraq",
    "sumer":         "Iraq",
    "akkad":         "Iraq",
    "nineveh":       "Iraq",
    "ur":            "Iraq",
    "carthage":      "Tunisia",
    "nubia":         "Sudan",
    "lower nubia":   "Sudan",
    "upper nubia":   "Sudan",
    "phoenicia":     "Lebanon",
    "canaan":        "Palestinian Territories",
    "judea":         "Israel",
    "judaea":        "Israel",
    "ancient egypt": "Egypt",
    "ancient rome":  "Italy",
    "ancient greece": "Greece",
    "ancient iran":  "Iran",
    "ancient india": "India",
    "ancient china": "China",
    # Chinese dynasties
    "qing":          "China",
    "ming":          "China",
    "han":           "China",
    "tang":          "China",
    "song":          "China",
    "qing dynasty":  "China",
    "ming dynasty":  "China",
    "han dynasty":   "China",
    "the qing empire": "China",
    "qing empire":   "China",
    # Pre-Columbian civilizations
    "aztec":         "Mexico",
    "maya":          "Mexico",
    "inca":          "Peru",
    "inca empire":   "Peru",
    "aztec empire":  "Mexico",
    "mongolia":      "Mongolia",
    "mongol empire": "Mongolia",
    "the mongol empire": "Mongolia",
}

# GPE strings to exclude (cities, US states, macro-regions, academic junk)
EXCLUDE_GPE = {
    # US cities and states (all 50 states)
    "womens", "chicago", "new york", "new york city", "nyc",
    "los angeles", "san francisco", "boston", "washington",
    "manhattan", "brooklyn", "philadelphia", "atlanta",
    "detroit", "cleveland", "cincinnati", "pittsburgh",
    "st. louis", "baltimore", "minneapolis", "houston",
    "new orleans", "denver", "phoenix", "seattle",
    "gary", "providence", "buffalo", "hartford",
    "cook county", "hollywood", "harlem",
    "alabama", "alaska", "arizona", "arkansas", "california",
    "colorado", "connecticut", "delaware", "florida", "hawaii",
    "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia",
    "washington state", "west virginia", "wisconsin", "wyoming",
    # Additional US cities not already in the city list above
    "columbus", "indianapolis", "jacksonville", "fort worth", "austin",
    "san jose", "san antonio", "dallas", "san diego", "nashville",
    "memphis", "louisville", "portland", "oklahoma city", "tucson",
    "albuquerque", "fresno", "sacramento", "mesa", "omaha",
    # Macro-regions too broad to map
    "europe", "africa", "asia", "the west", "the east",
    "the middle east", "middle east", "latin america", "sub-saharan africa",
    "southeast asia", "east asia", "south asia", "central asia",
    "north america", "south america", "western europe", "eastern europe",
    "new england", "the south", "midwest", "the balkans", "balkans",
    "scandinavia", "the pacific", "the caribbean", "caribbean",
    # Historical too vague
    "empire", "the empire", "the republic",
    # Clearly not places — proper nouns / academic terms misidentified by NER
    "womens", "nonparametric", "lagrange", "qur'an", "la",
    "developmental", "geography", "theology", "sociology",
    "philosophy", "economics", "science", "history",
    "schleiermacher", "jefferson", "saint", "antioch",
    "levant", "the levant", "west africa", "east africa",
    "forelimb", "allosteric", "nonstationary", "spectroscopy",
    "liouville", "christ", "lutheran", "tudor", "bronze",
    "new york state", "kansas city", "memphis", "charlotte",
    "augustine", "the kingdom of god", "b.c.",
    "mass", "upper midwest", "far east", "near east",
}

# Patterns that indicate a string is NOT a place name
_GARBAGE_RE = re.compile(
    r'\d'                   # contains digits (N4, Copper-64, T4, etc.)
    r'|[+=()\[\]{}<>]'     # math / chem notation
    r'|^[a-z]{1,2}$'       # 1-2 char lowercase (cp, ns, sc, mt, etc.)
)


def _is_garbage(key: str) -> bool:
    """Return True if key looks like a scientific term or NER noise, not a place."""
    if _GARBAGE_RE.search(key):
        return True
    if len(key) <= 2:
        return True
    # Reject strings with more than 5 words — country names are short; longer
    # strings are almost always NER run-ons from the text context
    if len(key.split()) > 5:
        return True
    # Common NER run-on patterns (article + stopword start)
    if key.startswith(("the city of", "the state of", "the province of",
                        "the region of", "the republic of the")):
        return True
    # Multi-word strings containing academic/non-geographic terms are NER
    # run-ons where the model grabbed surrounding context text (e.g.
    # "The United States Economics Economics" from a bibliography entry)
    _NOISE_TERMS = frozenset({
        "economics", "developmental", "geography", "theological",
        "biblical", "sociological", "psychological", "historical",
        "philosophical", "bibliography", "dissertation",
    })
    words = set(key.split())
    if len(key.split()) > 2 and words & _NOISE_TERMS:
        return True
    return False


# Modifier words that NER appends to place names as context (e.g. "Egypt Ancient",
# "Babylonia Historical", "Rome Ancient").  Strip these before lookup so the
# underlying place name can be normalized correctly.
_PLACE_MODIFIERS = frozenset({
    "ancient", "modern", "historical", "medieval", "early", "late",
    "classical", "imperial", "colonial", "pre-colonial", "post-colonial",
    "upper", "lower", "northern", "southern", "eastern", "western",
    "central", "outer", "inner",
})


def _strip_modifiers(key: str) -> str:
    """Remove leading/trailing modifier words from a lowercased place key."""
    words = key.split()
    while words and words[0] in _PLACE_MODIFIERS:
        words = words[1:]
    while words and words[-1] in _PLACE_MODIFIERS:
        words = words[:-1]
    return " ".join(words)


def normalize_country(name):
    """Return canonical country name or None to exclude."""
    if not name:
        return None
    key = name.strip().lower().rstrip(".")
    if _is_garbage(key):
        return None
    if key in EXCLUDE_GPE:
        return None
    if key in COUNTRY_NORMALIZE:
        return COUNTRY_NORMALIZE[key]
    if key in CITY_TO_COUNTRY:
        return CITY_TO_COUNTRY[key]

    # Try stripping temporal/directional modifiers ("Egypt Ancient" → "Egypt")
    stripped = _strip_modifiers(key)
    if stripped and stripped != key:
        if stripped in EXCLUDE_GPE:
            return None
        if stripped in COUNTRY_NORMALIZE:
            return COUNTRY_NORMALIZE[stripped]
        if stripped in CITY_TO_COUNTRY:
            return CITY_TO_COUNTRY[stripped]

    titled = (stripped or key).strip().title()
    if len(titled) <= 2:
        return None
    return titled


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_data():
    print("[LOAD] Reading source files...")
    with open(IN_ENRICHED, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"  enriched dissertations: {len(rows):,}")

    with open(IN_DEPT_GEO, encoding="utf-8") as f:
        dept_geo = json.load(f)
    print(f"  department features: {len(dept_geo['features'])}")

    locations = {}
    with open(IN_LOCATIONS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            locations[row["Department"].strip()] = {
                "lat": float(row["Latitude"]),
                "lon": float(row["Longitude"]),
                "building": row["Building"].strip(),
            }
    print(f"  department locations: {len(locations)}")

    # Load footprint geometries for historical building lookup
    footprint_geoms: dict = {}
    if IN_FOOTPRINT.exists():
        with open(IN_FOOTPRINT, encoding="utf-8") as f:
            footprint = json.load(f)
        for feat in footprint["features"]:
            bname = feat["properties"].get("name", "")
            if bname and feat.get("geometry"):
                footprint_geoms[bname] = feat["geometry"]
        print(f"  footprint buildings loaded: {len(footprint_geoms)}")
        # Verify all DEPT_TIMELINE buildings are present
        missing = [
            b for periods in DEPT_TIMELINE.values()
            for b in [p["building"] for p in periods]
            if b not in footprint_geoms
        ]
        if missing:
            print(f"  WARNING: footprint missing buildings: {missing}")
    else:
        print("  WARNING: footprint GeoJSON not found — historical building mapping disabled")

    return rows, dept_geo, locations, footprint_geoms


# ---------------------------------------------------------------------------
# Building occupancy years (when each dept moved into its CURRENT building)
# Source: uchicago-property-footprint-2-28-25.geojson year_built, with
# overrides where the dept moved in later than the building was constructed.
# Dissertations before this year are excluded from the campus choropleth —
# the building simply didn't exist (or wasn't the dept's home) yet.
# ---------------------------------------------------------------------------

DEPT_OCCUPANCY_YEAR = {
    "Anthropology":                               1896,  # Haskell Hall 1896
    "Art History":                                1974,  # Cochrane-Woods 1974
    "Astronomy and Astrophysics":                 1985,  # Kersten Physics 1985
    "Biochemistry and Molecular Biology":         2005,  # Gordon Center 2005
    "Business":                                   2004,  # Booth/Harper Center 2004
    "Chemistry":                                  1968,  # Searle Chemical Lab 1968
    "Cinema and Media Studies":                   1928,  # Wieboldt Hall 1928
    "Classics":                                   1915,  # Classics Building 1915
    "Comparative Human Development":              1898,  # Green Hall 1898
    "Comparative Literature":                     1928,  # Wieboldt Hall 1928
    "Computer Science":                           1984,  # John Crerar Library 1984
    "Conceptual and Historical Studies of Science": 1929, # SSRB 1929
    "Divinity":                                   1926,  # Swift Hall 1926
    "East Asian Languages and Civilizations":     1928,  # Wieboldt Hall 1928
    "Ecology and Evolution":                      1994,  # Donnelley Biological Sciences 1994
    "Economics":                                  2009,  # Saieh Hall → Economics dept ~2009
    "Education":                                  1931,  # Judd Hall 1931
    "English Language and Literature":            1912,  # Harper Memorial Library 1912
    "Genetics Genomics and Systems Biology":      2005,  # Gordon Center 2005
    "Geophysical Sciences":                       1969,  # Hinds Laboratory 1969
    "Germanic Studies":                           1928,  # Wieboldt Hall 1928
    "History":                                    1929,  # SSRB 1929
    "Jewish Studies":                             1926,  # Swift Hall 1926
    "Linguistics":                                1915,  # Rosenwald Hall 1915
    "Mathematics":                                1930,  # Eckhart Hall 1930
    "Microbiology":                               1973,  # Cummings Life Sciences 1973
    "Molecular Engineering":                      2015,  # William Eckhardt Research Center 2015
    "Music":                                      1892,  # Goodspeed Hall 1892
    "Near Eastern Languages and Civilizations":   1932,  # Oriental Institute 1932
    "Organismal Biology and Anatomy":             1897,  # Culver Hall 1897
    "Philosophy":                                 1940,  # Stuart Hall 1940
    "Physics":                                    1985,  # Kersten Physics 1985
    "Political Science":                          1971,  # Pick Hall 1971
    "Psychology":                                 1898,  # Green Hall 1898
    "Public Policy Studies":                      1988,  # Harris School founded 1988
    "Romance Languages and Literatures":          1893,  # Foster Hall 1893
    "Slavic Languages and Literatures":           1928,  # Wieboldt Hall 1928
    "Social Service Administration":              1963,  # Crown Family School 1963
    "Social Thought":                             1928,  # Wieboldt Hall 1928
    "Sociology":                                  1929,  # SSRB 1929
    "South Asian Languages and Civilizations":    1928,  # Wieboldt Hall 1928
    "Statistics":                                 1929,  # Jones Laboratory 1929
    "Graduate Library School":                    1926,  # Harper Memorial Library east wing, 1926
    "Home Economics":                             1929,  # 5740 S. Woodlawn Ave confirmed from 1929
}


# ---------------------------------------------------------------------------
# Department building timeline — complete coverage for all 43 canonical depts.
# Building names match exactly to uchicago-property-footprint-2-28-25.geojson
# so geometries can be looked up.  "from": 0 means "from the university's
# founding" (no lower-bound anachronism warning).
#
# Sources: UChicago Library "Bold Experiment" exhibit, department histories,
# UChicago Facilities, and footprint GeoJSON year_built fields.
# ---------------------------------------------------------------------------

DEPT_TIMELINE = {

    # ── PHYSICAL SCIENCES ──────────────────────────────────────────────────
    "Chemistry": [
        # Kent Chemical Lab opened 1894 for chemistry (gift of Sidney Kent).
        # Searle Chemical Laboratory completed 1968; chemistry relocated then.
        {"from": 0,    "to": 1967, "building": "Kent Chemical Laboratory"},
        {"from": 1968, "to": 9999, "building": "Searle Chemical Laboratory"},
    ],
    "Physics": [
        # Ryerson Physical Laboratory (1894) housed physics until Kersten (1985).
        {"from": 0,    "to": 1984, "building": "Ryerson Laboratory"},
        {"from": 1985, "to": 9999, "building": "Kersten Physics Teaching Center"},
    ],
    "Astronomy and Astrophysics": [
        # Ryerson also housed Mathematical Astronomy (predecessor dept).
        {"from": 0,    "to": 1984, "building": "Ryerson Laboratory"},
        {"from": 1985, "to": 9999, "building": "Kersten Physics Teaching Center"},
    ],
    "Mathematics": [
        # Ryerson housed "physics and mathematics" from 1894 per UChicago Library.
        # Eckhart Hall built 1930 specifically for mathematics.
        {"from": 0,    "to": 1929, "building": "Ryerson Laboratory"},
        {"from": 1930, "to": 9999, "building": "Eckhart Hall"},
    ],
    "Statistics": [
        # Statistics grew from mathematics; shared Ryerson until Jones Lab (1929).
        {"from": 0,    "to": 1928, "building": "Ryerson Laboratory"},
        {"from": 1929, "to": 9999, "building": "Jones Laboratory"},
    ],
    "Computer Science": [
        # CS didn't exist as a separate dept until ~1984; Ryerson is the
        # nearest pre-history home (math/physics building).
        {"from": 0,    "to": 1983, "building": "Ryerson Laboratory"},
        {"from": 1984, "to": 9999, "building": "John Crerar Library"},
    ],
    "Geophysical Sciences": [
        # Walker Museum (1893) was built for geological sciences.
        # Hinds Laboratory opened 1969 for geophysical sciences.
        {"from": 0,    "to": 1968, "building": "Walker Museum"},
        {"from": 1969, "to": 9999, "building": "Hinds Laboratory"},
    ],
    "Molecular Engineering": [
        # PME established 2011; William Eckhardt Research Center opened 2015.
        {"from": 0,    "to": 9999, "building": "William Eckhardt Research Center"},
    ],

    # ── BIOLOGICAL SCIENCES ────────────────────────────────────────────────
    "Ecology and Evolution": [
        # Zoology Building (1897, Hull Biological Labs, Helen Culver gift).
        # Committee on Biophysics and Theoretical Biology (1974–1994) was housed in
        # Cummings Life Sciences Center; merged into Ecology and related BSD programs.
        # Donnelley Biological Sciences Learning Center opened 1994.
        # Sources: UChicago BSD history; CLSC history; Donnelley BSLC building record.
        {"from": 0,    "to": 1972, "building": "Zoology Building"},
        {"from": 1973, "to": 1993, "building": "Cummings Life Sciences Center"},
        {"from": 1994, "to": 9999, "building": "Donnelley Biological Sciences Learning Center"},
    ],
    "Microbiology": [
        # Bacteriology/Microbiology in Zoology Building until Cummings (1973).
        {"from": 0,    "to": 1972, "building": "Zoology Building"},
        {"from": 1973, "to": 9999, "building": "Cummings Life Sciences Center"},
    ],
    "Biochemistry and Molecular Biology": [
        # Pre-1926: Zoology Building (Hull Biological Laboratories, 1897).
        # 1926–1972: Whitman Laboratory opened 1926 for biological chemistry/biophysics
        #   research (Frederick Whitman gift). The Committee on Biophysics (est. 1947)
        #   operated here; biophysics dissertations routed to Whitman for this era.
        # 1973–2004: Cummings Life Sciences Center (opened 1973) became the BSD anchor.
        # 2005+: Gordon Center for Integrative Science (GCIS).
        # Sources: UChicago BSD history; Whitman Lab building record (footprint 1926);
        #   Cummings CLSC history; GCIS dedication 2005.
        {"from": 0,    "to": 1925, "building": "Zoology Building"},
        {"from": 1926, "to": 1972, "building": "Whitman Laboratory"},
        {"from": 1973, "to": 2004, "building": "Cummings Life Sciences Center"},
        {"from": 2005, "to": 9999, "building": "Gordon Center for Integrative Science"},
    ],
    "Genetics Genomics and Systems Biology": [
        {"from": 0,    "to": 2004, "building": "Zoology Building"},
        {"from": 2005, "to": 9999, "building": "Gordon Center for Integrative Science"},
    ],
    "Organismal Biology and Anatomy": [
        # Anatomy Building (1897, Hull Biological Labs) — current building.
        {"from": 0,    "to": 9999, "building": "Anatomy Building"},
    ],

    # ── HUMANITIES ─────────────────────────────────────────────────────────
    "Near Eastern Languages and Civilizations": [
        # Haskell Hall (1896) was built as "Haskell Oriental Museum" for
        # Semitic/Oriental studies. NELC moved to the new Oriental Institute
        # (now ISAC) in 1932 (funded by John D. Rockefeller Jr.).
        {"from": 0,    "to": 1931, "building": "Haskell Hall"},
        {"from": 1932, "to": 9999, "building": "Institute for the Study of Ancient Cultures"},
    ],
    "Art History": [
        # Walker Museum (1893) housed humanities overflow including art history.
        # Department moved to Cochrane-Woods Art Center in 1974 (per dept website).
        {"from": 0,    "to": 1973, "building": "Walker Museum"},
        {"from": 1974, "to": 9999, "building": "Cochrane-Woods Art Center"},
    ],
    "English Language and Literature": [
        # Cobb Hall (1892) was the first UChicago building; all humanities began there.
        # Harper Memorial Library (1912) became the English dept home.
        {"from": 0,    "to": 1911, "building": "Cobb Hall"},
        {"from": 1912, "to": 9999, "building": "Harper Memorial Library"},
    ],
    "Classics": [
        {"from": 0,    "to": 1914, "building": "Cobb Hall"},
        {"from": 1915, "to": 9999, "building": "Classics Building"},
    ],
    "Linguistics": [
        {"from": 0,    "to": 1914, "building": "Cobb Hall"},
        {"from": 1915, "to": 9999, "building": "Rosenwald Hall"},
    ],
    "Philosophy": [
        {"from": 0,    "to": 1939, "building": "Cobb Hall"},
        {"from": 1940, "to": 9999, "building": "Stuart Hall"},
    ],
    "Music": [
        # Goodspeed Hall (1892) has been the music home since essentially
        # the university's founding.
        {"from": 0,    "to": 9999, "building": "Goodspeed Hall"},
    ],
    "Romance Languages and Literatures": [
        # Foster Hall (1893) — essentially co-founded with the university.
        {"from": 0,    "to": 9999, "building": "Foster Hall"},
    ],
    "Germanic Studies": [
        # Germanic/Teutonic Languages existed from 1892 founding; Cobb Hall
        # first, then Wieboldt Hall (1928) which became the languages building.
        {"from": 0,    "to": 1927, "building": "Cobb Hall"},
        {"from": 1928, "to": 9999, "building": "Wieboldt Hall"},
    ],
    "Comparative Literature": [
        {"from": 0,    "to": 9999, "building": "Wieboldt Hall"},
    ],
    "Cinema and Media Studies": [
        {"from": 0,    "to": 9999, "building": "Wieboldt Hall"},
    ],
    "East Asian Languages and Civilizations": [
        {"from": 0,    "to": 9999, "building": "Wieboldt Hall"},
    ],
    "Slavic Languages and Literatures": [
        {"from": 0,    "to": 9999, "building": "Wieboldt Hall"},
    ],
    "South Asian Languages and Civilizations": [
        {"from": 0,    "to": 9999, "building": "Wieboldt Hall"},
    ],
    "Social Thought": [
        # Committee on Social Thought founded 1941; Wieboldt Hall from start.
        {"from": 0,    "to": 9999, "building": "Wieboldt Hall"},
    ],

    # ── DIVINITY ───────────────────────────────────────────────────────────
    "Divinity": [
        # Divinity School is a founding unit (1892). Cobb Hall was the initial
        # all-purpose building. Swift Hall opened 1926.
        {"from": 0,    "to": 1925, "building": "Cobb Hall"},
        {"from": 1926, "to": 9999, "building": "Swift Hall"},
    ],
    "Jewish Studies": [
        # Jewish Studies affiliated with Divinity School; shares Swift Hall.
        {"from": 0,    "to": 1925, "building": "Cobb Hall"},
        {"from": 1926, "to": 9999, "building": "Swift Hall"},
    ],

    # ── SOCIAL SCIENCES ────────────────────────────────────────────────────
    "History": [
        # History is a founding dept (1892). Cobb Hall until SSRB (1929,
        # funded by Laura Spelman Rockefeller Memorial, $1.1M).
        {"from": 0,    "to": 1928, "building": "Cobb Hall"},
        {"from": 1929, "to": 9999, "building": "Social Science Research Building"},
    ],
    "Sociology": [
        {"from": 0,    "to": 1928, "building": "Cobb Hall"},
        {"from": 1929, "to": 9999, "building": "Social Science Research Building"},
    ],
    "Economics": [
        # Political Economy (early name for economics) from 1892 in Cobb Hall.
        # SSRB (1929) housed economics; Saieh Hall for Economics opened ~2009.
        {"from": 0,    "to": 1928, "building": "Cobb Hall"},
        {"from": 1929, "to": 2008, "building": "Social Science Research Building"},
        {"from": 2009, "to": 9999, "building": "Saieh Hall for Economics"},
    ],
    "Political Science": [
        # Social/political science in Cobb Hall, then SSRB (1929), then
        # Pick Hall (1971).
        {"from": 0,    "to": 1928, "building": "Cobb Hall"},
        {"from": 1929, "to": 1970, "building": "Social Science Research Building"},
        {"from": 1971, "to": 9999, "building": "Pick Hall"},
    ],
    "Conceptual and Historical Studies of Science": [
        # CHSS committee housed in SSRB alongside History; Cobb Hall before that.
        {"from": 0,    "to": 1928, "building": "Cobb Hall"},
        {"from": 1929, "to": 9999, "building": "Social Science Research Building"},
    ],
    "Anthropology": [
        # Haskell Hall (1896) is Anthropology's current home. The building was
        # originally Haskell Oriental Museum (NELC); Anthropology moved in
        # after NELC vacated for ISAC (1932). Pre-1932: Cobb Hall.
        {"from": 0,    "to": 1931, "building": "Cobb Hall"},
        {"from": 1932, "to": 9999, "building": "Haskell Hall"},
    ],
    "Psychology": [
        # Green Hall (1898) — Psychology's long-standing home.
        {"from": 0,    "to": 9999, "building": "Green Hall"},
    ],
    "Comparative Human Development": [
        # CHD committee shares Green Hall with Psychology.
        {"from": 0,    "to": 9999, "building": "Green Hall"},
    ],
    "Education": [
        {"from": 0,    "to": 1930, "building": "Cobb Hall"},
        {"from": 1931, "to": 9999, "building": "Judd Hall"},
    ],
    "Social Service Administration": [
        # SSA founded 1908; Crown Family School (formerly the SSA building)
        # opened 1963.
        {"from": 0,    "to": 1962, "building": "Cobb Hall"},
        {"from": 1963, "to": 9999, "building": "Crown Family School of Social Work, Policy, and Practice"},
    ],
    "Public Policy Studies": [
        # Harris School founded 1988; pre-Harris Planning records (1955–1976) → Cobb Hall.
        {"from": 0,    "to": 1987, "building": "Cobb Hall"},
        {"from": 1988, "to": 9999, "building": "Keller Center"},
    ],
    "Business": [
        # GSB founded 1898 in Rosenwald Hall era; Booth building opened 2004.
        {"from": 0,    "to": 2003, "building": "Rosenwald Hall"},
        {"from": 2004, "to": 9999, "building": "Booth School of Business"},
    ],

    # ── HOME ECONOMICS / HOUSEHOLD ADMINISTRATION (dissolved 1956) ─────────
    "Home Economics": [
        # Dept of Sanitary Science (1892) → Household Administration →
        # Home Economics under Margaret Reid (~1951) → dissolved 1956.
        # Pre-1929 building unconfirmed; Cobb Hall used as default.
        # 5740 S. Woodlawn Ave confirmed from ~1929 by CEHD building history.
        # (Building year_built 1928 in footprint; used for HE research from 1929.)
        # Sources: UChicago Library "Recipes for Domesticity" exhibit;
        #   cehd.uchicago.edu building history.
        {"from": 0,    "to": 1928, "building": "Cobb Hall"},
        {"from": 1929, "to": 1956, "building": "5740 South Woodlawn Avenue"},
    ],

    # ── GRADUATE LIBRARY SCHOOL (discontinued 1989) ────────────────────────
    "Graduate Library School": [
        # GLS founded 1926 in the east wing of Harper Memorial Library.
        # Moved to Joseph Regenstein Library upon its opening in 1970.
        # School discontinued 1989 under President Hanna Gray.
        # Sources: Wikipedia — University of Chicago Graduate Library School;
        #   UChicago Library finding aid ICU.SPCL.GRADLIBRARY (1928–1979).
        # Only years 1926–1989 produce dissertations; "to": 1989 caps the range.
        {"from": 1926, "to": 1969, "building": "Harper Memorial Library"},
        {"from": 1970, "to": 1989, "building": "Joseph Regenstein Library"},
    ],
}


def get_historical_building(dept: str, year: int):
    """Return the footprint building name for dept in year, or None."""
    for period in DEPT_TIMELINE.get(dept, []):
        if period["from"] <= year <= period["to"]:
            return period["building"]
    return None


# ---------------------------------------------------------------------------
# Campus choropleth data
# ---------------------------------------------------------------------------

def build_campus_data(rows, dept_geo, locations, footprint_geoms):
    print("\n[CAMPUS] Aggregating dissertations by building and year...")

    # (dept, building) → {year → count}
    # Each dept gets its own feature; historical dept-building pairs produce
    # separate features from current-building ones.
    dept_bldg_counts: dict[tuple, Counter] = defaultdict(Counter)

    historical_mapped = 0
    for row in rows:
        dept = row.get("modern_department", "").strip()
        if not dept or dept == "UNKNOWN":
            continue
        date = row.get("Date", "") or ""
        try:
            year = int(date[:4])
        except (ValueError, TypeError):
            continue
        if not year:
            continue

        # Historical override: check DEPT_TIMELINE first
        actual_bldg = get_historical_building(dept, year)
        if actual_bldg:
            historical_mapped += 1
        elif row.get("has_geometry") == "True":
            actual_bldg = row.get("building_name", "").strip()
        else:
            continue

        if not actual_bldg:
            continue

        dept_bldg_counts[(dept, actual_bldg)][year] += 1

    print(f"  Rows mapped via DEPT_TIMELINE: {historical_mapped:,}")

    # Geometry lookup: current-building GeoJSON first, then footprint
    bldg_geom: dict[str, dict] = {}
    for feat in dept_geo["features"]:
        bname = feat["properties"].get("building_name", "")
        if bname:
            bldg_geom[bname] = feat["geometry"]
    for bname, geom in footprint_geoms.items():
        if bname not in bldg_geom:
            bldg_geom[bname] = geom

    all_years: set = set()
    features = []
    missing_geom: list = []

    for (dept, bldg), year_counts in dept_bldg_counts.items():
        geom = bldg_geom.get(bldg)
        if not geom:
            missing_geom.append(bldg)
            continue

        loc = locations.get(dept, {})
        centroid = polygon_centroid(geom)
        if not centroid:
            centroid = [loc.get("lat", 0), loc.get("lon", 0)]

        # buildingOpened: year below which an anachronism warning fires.
        # For DEPT_TIMELINE depts, use the period's "from" year (0 = predates records).
        # For all others, use DEPT_OCCUPANCY_YEAR (current building's occupancy start).
        building_opened = None
        for period in DEPT_TIMELINE.get(dept, []):
            if period["building"] == bldg:
                building_opened = period["from"] if period["from"] > 0 else 0
                break
        if building_opened is None:
            building_opened = DEPT_OCCUPANCY_YEAR.get(dept, 0)

        all_years.update(year_counts.keys())
        features.append({
            "department":     dept,
            "building":       bldg,
            "buildingOpened": building_opened,
            "geometry":       geom,
            "centroid":       centroid,
            "byYear":         {str(y): c for y, c in sorted(year_counts.items())},
            "total":          sum(year_counts.values()),
        })

    if missing_geom:
        unique_missing = sorted(set(missing_geom))
        print(f"  WARNING: No geometry for buildings: {unique_missing}")

    features.sort(key=lambda x: -x["total"])
    years_sorted = sorted(all_years)
    print(f"  Dept-building features: {len(features)}, Years: {min(years_sorted)}–{max(years_sorted)}")
    return {"features": features, "years": years_sorted}


# ---------------------------------------------------------------------------
# Global choropleth data
# ---------------------------------------------------------------------------

def build_global_data(rows):
    print("\n[GLOBAL] Building country-level dissertation counts...")

    country_total     = Counter()
    country_by_decade = defaultdict(Counter)
    country_by_year   = defaultdict(Counter)
    dissertations_with_geo = 0

    for row in rows:
        date = row.get("Date", "") or ""
        year = int(date[:4]) if date and len(date) >= 4 else None

        # Use full geo_entities list (semicolon-separated raw NER output)
        # so each dissertation contributes all countries it mentions, not
        # just the single primary_geo.  Deduplicate per dissertation.
        ge_raw = (row.get("geo_entities", "") or "").strip()
        raw_entities = [e.strip() for e in ge_raw.split(";") if e.strip()] if ge_raw else []
        if not raw_entities:
            pg = (row.get("primary_geo", "") or "").strip()
            if pg:
                raw_entities = [pg]
        if not raw_entities:
            continue

        seen = set()
        countries_this_row = []
        for ent in raw_entities:
            c = normalize_country(ent.strip())
            if c and c not in seen:
                seen.add(c)
                countries_this_row.append(c)

        if not countries_this_row:
            continue

        dissertations_with_geo += 1
        for country in countries_this_row:
            country_total[country] += 1
            if year:
                decade = f"{(year // 10) * 10}s"
                country_by_decade[decade][country] += 1
                country_by_year[year][country] += 1

    top50 = dict(country_total.most_common(50))
    print(f"  Dissertations with geo focus: {dissertations_with_geo:,}")
    print(f"  Unique country entities: {len(country_total)}")
    print(f"  Top 10: {list(country_total.most_common(10))}")

    geo_years = sorted(country_by_year.keys())
    print(f"  Years with geo data: {geo_years[0] if geo_years else 'none'}–{geo_years[-1] if geo_years else 'none'}")

    return {
        "counts":      top50,
        "allCounts":   dict(country_total),
        "byDecade":    {d: dict(c) for d, c in sorted(country_by_decade.items())},
        "byYear":      {str(y): dict(c) for y, c in sorted(country_by_year.items())},
        "years":       [str(y) for y in geo_years],
        "total":       dissertations_with_geo,
    }


# ---------------------------------------------------------------------------
# Interdisciplinary data
# ---------------------------------------------------------------------------

def build_interdisciplinary_data(rows, locations):
    print("\n[INTERDISCIPLINARY] Computing joint program metrics...")

    # Seed canonical-dept guards so single depts with "and" aren't split,
    # including reversed word-order variants (e.g. "Civilizations and South Asian…")
    global _CANONICAL_DEPT_NORMS, _CANONICAL_DEPT_WORDSETS
    _CANONICAL_DEPT_NORMS = {_collapse(d) for d in locations}
    _CANONICAL_DEPT_WORDSETS = {
        frozenset(w for w in _collapse(d).split() if w != "and")
        for d in locations
    }
    # Sorted longest-first so "South Asian Languages and Civilizations" is tried
    # before "South Asian Languages" when parsing joint program strings.
    _canonical_sorted = sorted(locations.keys(), key=len, reverse=True)
    _canonical_norms_sorted = [(_collapse(d), d) for d in _canonical_sorted]

    def extract_joint_depts(raw):
        """
        Parse a joint-program raw string into (d1, d2) by trying canonical dept
        names as prefixes/suffixes before falling back to naive " and " split.
        Returns (d1_str, d2_str) or None.
        """
        raw_norm = _collapse(raw.replace("&", "and"))
        for norm, orig in _canonical_norms_sorted:
            # Pattern: "<canonical> and <other>"
            prefix = norm + " and "
            if raw_norm.startswith(prefix):
                d2 = raw_norm[len(prefix):].strip()
                if d2:
                    return (orig[:55], d2[:55].title())
            # Pattern: "<other> and <canonical>"
            suffix = " and " + norm
            if raw_norm.endswith(suffix):
                d1 = raw_norm[:-len(suffix)].strip()
                if d1:
                    return (d1[:55].title(), orig[:55])
        # Fallback: naive first-split — only accept if BOTH parts look like
        # plausible department names (i.e. at least one is a canonical dept).
        # This prevents biomedical program names like "Cell and Molecular Biology"
        # from being falsely split into ("Cell", "Molecular Biology").
        parts = raw.replace("&", "and").split(" and ")
        if len(parts) >= 2:
            d1, d2 = parts[0].strip(), parts[1].strip()
            if d1 and d2:
                d1n, d2n = _collapse(d1), _collapse(d2)
                if d1n in _CANONICAL_DEPT_NORMS or d2n in _CANONICAL_DEPT_NORMS:
                    return (d1[:55], d2[:55])
        return None

    year_total = Counter()
    year_joint = Counter()
    pair_counts = Counter()

    for row in rows:
        raw    = row.get("department_original", "").strip()
        modern = row.get("modern_department", "").strip()
        date   = row.get("Date", "") or ""
        year   = int(date[:4]) if date and len(date) >= 4 else None

        if not year or modern == "UNKNOWN":
            continue

        year_total[year] += 1
        if is_joint_program(raw, modern):
            year_joint[year] += 1
            pair = extract_joint_depts(raw)
            if pair:
                d1, d2 = pair
                key = tuple(sorted([d1, d2]))
                pair_counts[key] += 1

    by_year = {}
    for year in sorted(year_total):
        total = year_total[year]
        joint = year_joint[year]
        by_year[str(year)] = {
            "total": total,
            "joint": joint,
            "pct":   round(joint / total * 100, 1) if total else 0,
        }

    top_pairs = [
        {"pair": f"{p[0]} + {p[1]}", "count": c, "depts": list(p)}
        for p, c in pair_counts.most_common(20)
    ]

    dept_locs = {
        dept: {"lat": info["lat"], "lon": info["lon"], "building": info["building"]}
        for dept, info in locations.items()
    }

    joint_total = sum(year_joint.values())
    all_total   = sum(year_total.values())
    print(f"  Joint dissertations: {joint_total:,} / {all_total:,} "
          f"({joint_total/all_total*100:.1f}%)")
    print(f"  Unique joint pairs: {len(pair_counts)}")

    return {
        "byYear":       by_year,
        "topPairs":     top_pairs,
        "deptLocations": dept_locs,
    }


# ---------------------------------------------------------------------------
# Ring species data
# ---------------------------------------------------------------------------

def build_ring_species_data(rows, locations):
    print("\n[RING SPECIES] Computing keyword similarity and spatial distances...")

    # Keyword document per department
    dept_docs = defaultdict(list)
    for row in rows:
        dept = row.get("modern_department", "").strip()
        if dept == "UNKNOWN" or dept not in locations:
            continue
        kws = parse_list_field(row.get("Paper Keywords", "") or "")
        sub = parse_list_field(row.get("Subject Terms", "") or "")
        title = (row.get("Title", "") or "").split()
        dept_docs[dept].extend(kws + sub)

    # Only keep departments with enough keyword data
    MIN_TERMS = 30
    dept_names = [d for d, terms in dept_docs.items() if len(terms) >= MIN_TERMS]
    dept_names.sort()
    print(f"  Departments with ≥{MIN_TERMS} keyword terms: {len(dept_names)}")

    # TF-IDF vectorization
    dept_texts = [" ".join(dept_docs[d]) for d in dept_names]
    vectorizer = TfidfVectorizer(
        max_features=800,
        stop_words="english",
        ngram_range=(1, 1),
        min_df=2,
    )
    tfidf_matrix = vectorizer.fit_transform(dept_texts)
    sim_matrix   = cosine_similarity(tfidf_matrix)
    print(f"  TF-IDF vocabulary: {len(vectorizer.vocabulary_)} terms")

    # Spatial distances (meters)
    n = len(dept_names)
    dist_matrix = np.zeros((n, n))
    for i, d1 in enumerate(dept_names):
        l1 = locations[d1]
        for j, d2 in enumerate(dept_names):
            if i != j:
                l2 = locations[d2]
                dist_matrix[i][j] = haversine_m(
                    l1["lat"], l1["lon"], l2["lat"], l2["lon"]
                )

    # Campus angles (kept for reference / scatter X-axis context)
    angles = {
        d: campus_angle(locations[d]["lat"], locations[d]["lon"])
        for d in dept_names
    }

    # Ring order: angular sort around campus center → 2-opt improvement.
    # Angular sort guarantees a consistent clockwise sweep (the ring looks like
    # a geographic loop); 2-opt fixes crossings from buildings at similar angles.
    def _dist(a, b):
        la, loa = locations[a]["lat"], locations[a]["lon"]
        lb, lob = locations[b]["lat"], locations[b]["lon"]
        return haversine_m(la, loa, lb, lob)

    ring_order = sorted(dept_names, key=lambda d: angles[d])

    improved = True
    while improved:
        improved = False
        n_t = len(ring_order)
        for i in range(n_t - 1):
            for j in range(i + 2, n_t):
                d_old = _dist(ring_order[i], ring_order[i+1]) + \
                        _dist(ring_order[j], ring_order[(j+1) % n_t])
                d_new = _dist(ring_order[i], ring_order[j]) + \
                        _dist(ring_order[i+1], ring_order[(j+1) % n_t])
                if d_new < d_old - 0.5:
                    ring_order[i+1:j+1] = ring_order[i+1:j+1][::-1]
                    improved = True

    tour_len = sum(_dist(ring_order[k], ring_order[(k+1) % len(ring_order)])
                   for k in range(len(ring_order)))
    print(f"  Ring tour length: {tour_len:.0f} m (angular sort + 2-opt)")

    # Top keywords per department
    feature_names = vectorizer.get_feature_names_out()
    top_keywords  = {}
    for i, dept in enumerate(dept_names):
        row_arr  = tfidf_matrix[i].toarray().flatten()
        top_idx  = row_arr.argsort()[-15:][::-1]
        top_keywords[dept] = [
            [feature_names[j], round(float(row_arr[j]), 4)]
            for j in top_idx if row_arr[j] > 0
        ]

    # Compute scatter data: all unique pairs (distance, similarity)
    scatter = []
    for i in range(n):
        for j in range(i+1, n):
            scatter.append({
                "d1":   dept_names[i],
                "d2":   dept_names[j],
                "dist": round(dist_matrix[i][j]),
                "sim":  round(float(sim_matrix[i][j]), 4),
            })

    # Ring-consecutive similarities
    ring_sims = []
    for k, dept in enumerate(ring_order):
        next_dept = ring_order[(k + 1) % len(ring_order)]
        i = dept_names.index(dept)
        j = dept_names.index(next_dept)
        ring_sims.append({
            "from": dept,
            "to":   next_dept,
            "sim":  round(float(sim_matrix[i][j]), 4),
            "dist": round(dist_matrix[i][j]),
        })

    print(f"  Scatter pairs: {len(scatter)}")
    avg_ring_sim = sum(r["sim"] for r in ring_sims) / len(ring_sims)
    print(f"  Avg ring-consecutive similarity: {avg_ring_sim:.3f}")

    # Active departments per year (depts with ≥1 dissertation in that year)
    dept_set = set(dept_names)
    year_dept_count = defaultdict(Counter)
    for row in rows:
        dept = row.get("modern_department", "").strip()
        if dept not in dept_set:
            continue
        date = row.get("Date", "") or ""
        try:
            year = int(date[:4])
        except (ValueError, TypeError):
            continue
        year_dept_count[year][dept] += 1

    active_by_year = {}
    for year in sorted(year_dept_count.keys()):
        active = sorted(d for d in dept_names if year_dept_count[year].get(d, 0) > 0)
        if len(active) >= 2:
            active_by_year[str(year)] = active

    ring_years = sorted(active_by_year.keys())
    print(f"  Years with ring data: {ring_years[0] if ring_years else 'none'}–{ring_years[-1] if ring_years else 'none'}")
    print(f"  Avg active depts/year: {sum(len(v) for v in active_by_year.values()) / len(active_by_year):.1f}" if active_by_year else "  No ring years")

    # Per-decade keyword similarity matrices
    # Each decade gets its own TF-IDF model over dissertations from that decade.
    # Only departments with >= 15 terms in that decade are included.
    # Stored as simByDecade[label] = {departments, simMatrix, topKeywords}
    MIN_TERMS_DECADE = 15
    sim_by_decade: dict = {}

    decade_rows: dict[str, list] = defaultdict(list)
    for row in rows:
        dept = row.get("modern_department", "").strip()
        if dept not in dept_set:
            continue
        date = row.get("Date", "") or ""
        try:
            yr = int(date[:4])
        except (ValueError, TypeError):
            continue
        decade_rows[f"{(yr // 10) * 10}s"].append((dept, row))

    for dlabel, drows in sorted(decade_rows.items()):
        decade_docs: dict[str, list] = defaultdict(list)
        for dept, row in drows:
            kws = parse_list_field(row.get("Paper Keywords", "") or "")
            sub = parse_list_field(row.get("Subject Terms", "") or "")
            decade_docs[dept].extend(kws + sub)
        valid = [d for d in dept_names if len(decade_docs.get(d, [])) >= MIN_TERMS_DECADE]
        if len(valid) < 5:
            continue
        texts = [" ".join(decade_docs[d]) for d in valid]
        try:
            vec = TfidfVectorizer(max_features=500, stop_words="english",
                                  ngram_range=(1, 1), min_df=1)
            mat = vec.fit_transform(texts)
            sim = cosine_similarity(mat)
            feat_names = vec.get_feature_names_out()
            d_kws: dict[str, list] = {}
            for i, dept in enumerate(valid):
                row_arr = mat[i].toarray().flatten()
                top_idx = row_arr.argsort()[-12:][::-1]
                d_kws[dept] = [[feat_names[j], round(float(row_arr[j]), 4)]
                               for j in top_idx if row_arr[j] > 0]
            sim_by_decade[dlabel] = {
                "departments": valid,
                "simMatrix":   [[round(float(v), 4) for v in r] for r in sim],
                "topKeywords": d_kws,
            }
        except Exception as e:
            print(f"  WARNING: decade {dlabel} similarity failed: {e}")

    print(f"  Decade similarity matrices: {sorted(sim_by_decade.keys())}")

    # Per-year keyword similarity matrices
    # Used for the annual time-series view. A lower term threshold keeps the
    # series available in sparser years while still excluding nearly empty docs.
    MIN_TERMS_YEAR = 8
    sim_by_year: dict = {}

    year_rows: dict[str, list] = defaultdict(list)
    for row in rows:
        dept = row.get("modern_department", "").strip()
        if dept not in dept_set:
            continue
        date = row.get("Date", "") or ""
        try:
            yr = int(date[:4])
        except (ValueError, TypeError):
            continue
        year_rows[str(yr)].append((dept, row))

    for ylabel, yrows in sorted(year_rows.items(), key=lambda kv: int(kv[0])):
        year_docs: dict[str, list] = defaultdict(list)
        for dept, row in yrows:
            kws = parse_list_field(row.get("Paper Keywords", "") or "")
            sub = parse_list_field(row.get("Subject Terms", "") or "")
            year_docs[dept].extend(kws + sub)
        valid = [d for d in dept_names if len(year_docs.get(d, [])) >= MIN_TERMS_YEAR]
        if len(valid) < 3:
            continue
        texts = [" ".join(year_docs[d]) for d in valid]
        try:
            vec = TfidfVectorizer(max_features=300, stop_words="english",
                                  ngram_range=(1, 1), min_df=1)
            mat = vec.fit_transform(texts)
            sim = cosine_similarity(mat)
            sim_by_year[ylabel] = {
                "departments": valid,
                "simMatrix":   [[round(float(v), 4) for v in r] for r in sim],
            }
        except Exception as e:
            print(f"  WARNING: year {ylabel} similarity failed: {e}")

    if sim_by_year:
        years_with_sim = sorted(sim_by_year.keys(), key=int)
        print(f"  Year similarity matrices: {years_with_sim[0]}–{years_with_sim[-1]} ({len(years_with_sim)} years)")
    else:
        print("  Year similarity matrices: none")

    return {
        "departments":   dept_names,
        "ringOrder":     ring_order,
        "angles":        {d: round(angles[d], 1) for d in dept_names},
        "locations":     {d: [locations[d]["lat"], locations[d]["lon"]] for d in dept_names},
        "buildings":     {d: locations[d]["building"] for d in dept_names},
        "simMatrix":     [[round(float(v), 4) for v in row] for row in sim_matrix],
        "distMatrix":    [[round(float(v)) for v in row] for row in dist_matrix],
        "topKeywords":   top_keywords,
        "scatter":       scatter,
        "ringConsecutive": ring_sims,
        "activeByYear":  active_by_year,
        "ringYears":     ring_years,
        "simByYear":     sim_by_year,
        "simByDecade":   sim_by_decade,
    }


# ---------------------------------------------------------------------------
# Dissertation explorer index — per-building and per-country record lists
# ---------------------------------------------------------------------------

def _first_item(raw: str) -> str:
    """Return first element from a ProQuest list-string like ['Smith, J.']."""
    if not raw or not raw.strip():
        return ""
    raw = raw.strip()
    if raw.startswith("["):
        try:
            items = ast.literal_eval(raw)
            return str(items[0]).strip() if items else ""
        except Exception:
            pass
    return raw.strip()


def build_diss_index(rows):
    print("\n[DISS INDEX] Building dissertation explorer index...")

    by_building: dict = defaultdict(list)
    by_country:  dict = defaultdict(list)

    for row in rows:
        dept = row.get("modern_department", "").strip()
        date = row.get("Date", "") or ""
        try:
            year = int(date[:4])
        except (ValueError, TypeError):
            year = None

        title   = (row.get("Title", "") or "").strip()[:140]
        author  = _first_item(row.get("Authors", "") or "")
        advisor = _first_item(row.get("Advisors", "") or "")
        goid    = row.get("GOID", "")

        entry = {
            "t": title,
            "y": year or "",
            "d": dept if dept not in ("UNKNOWN", "") else "",
            "a": author,
            "v": advisor,
            "g": goid,
        }

        # ── Building index ──────────────────────────────────────────────────
        if dept and dept != "UNKNOWN" and year:
            bldg = get_historical_building(dept, year)
            if not bldg and row.get("has_geometry") == "True":
                bldg = row.get("building_name", "").strip()
            if bldg:
                by_building[bldg].append(entry)

        # ── Country/geo index ───────────────────────────────────────────────
        ge_raw = (row.get("geo_entities", "") or "").strip()
        raw_ents = [e.strip() for e in ge_raw.split(";") if e.strip()] if ge_raw else []
        if not raw_ents:
            pg = (row.get("primary_geo", "") or "").strip()
            if pg:
                raw_ents = [pg]

        seen: set = set()
        for ent in raw_ents:
            country = normalize_country(ent)
            if country and country not in seen:
                seen.add(country)
                by_country[country].append(entry)

    # Sort each list: most recent first
    for lst in list(by_building.values()) + list(by_country.values()):
        lst.sort(key=lambda x: -(x["y"] or 0))

    total_bldg = sum(len(v) for v in by_building.values())
    total_geo  = sum(len(v) for v in by_country.values())
    print(f"  Buildings indexed: {len(by_building)}, entries: {total_bldg:,}")
    print(f"  Countries indexed: {len(by_country)}, entries: {total_geo:,}")

    return {"byBuilding": dict(by_building), "byCountry": dict(by_country)}


def write_diss_index(diss_index):
    js_str = json.dumps(diss_index, separators=(",", ":"))
    with open(OUT_DISS_INDEX, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by GIS/prepare_viz_data.py — do not edit manually\n")
        f.write(f"window.DISS_INDEX = {js_str};\n")
    size_kb = OUT_DISS_INDEX.stat().st_size / 1024
    print(f"  Written: {OUT_DISS_INDEX} ({size_kb:.0f} KB)")


# ---------------------------------------------------------------------------
# World GeoJSON — download once, cache locally, write vis/world_data.js
# ---------------------------------------------------------------------------

WORLD_URL = ("https://raw.githubusercontent.com/datasets/geo-countries"
             "/master/data/countries.geojson")

def build_world_js():
    """
    Loads world country polygons and writes vis/world_data.js.
    Uses cached vis/world.geojson if present; otherwise downloads from CDN.
    Returns True on success, False if no network and no cache.
    """
    if WORLD_CACHE.exists():
        print(f"\n[WORLD] Using cached {WORLD_CACHE.name}")
        with open(WORLD_CACHE, encoding="utf-8") as f:
            world_gj = json.load(f)
    else:
        print(f"\n[WORLD] Downloading world GeoJSON from CDN...")
        try:
            with urllib.request.urlopen(WORLD_URL, timeout=30) as resp:
                raw = resp.read()
            world_gj = json.loads(raw)
            with open(WORLD_CACHE, "wb") as f:
                f.write(raw)
            print(f"  Cached to {WORLD_CACHE}")
        except Exception as e:
            print(f"  WARNING: Could not download world GeoJSON: {e}")
            print(f"  Global tab will not render country polygons.")
            print(f"  Re-run after connecting to the internet.")
            return False

    js_str = json.dumps(world_gj, separators=(",", ":"))
    with open(OUT_WORLD_JS, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by GIS/prepare_viz_data.py — do not edit manually\n")
        f.write(f"const WORLD_GEOJSON = {js_str};\n")
    size_kb = OUT_WORLD_JS.stat().st_size / 1024
    print(f"  Written: {OUT_WORLD_JS} ({size_kb:.0f} KB)")
    return True


# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------

def write_js(campus, global_data, interdisciplinary, ring_species):
    print(f"\n[WRITE] Writing {OUT_JS} ...")
    payload = {
        "campus":            campus,
        "global":            global_data,
        "interdisciplinary": interdisciplinary,
        "ringSpecies":       ring_species,
    }
    js_str = json.dumps(payload, separators=(",", ":"))
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by GIS/prepare_viz_data.py — do not edit manually\n")
        f.write(f"const VIS_DATA = {js_str};\n")
    size_kb = OUT_JS.stat().st_size / 1024
    print(f"  Written: {OUT_JS} ({size_kb:.0f} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 70)
    print("Visualization Data Preparation")
    print("=" * 70)

    rows, dept_geo, locations, footprint_geoms = load_data()
    campus            = build_campus_data(rows, dept_geo, locations, footprint_geoms)
    global_data       = build_global_data(rows)
    interdisciplinary = build_interdisciplinary_data(rows, locations)
    ring_species      = build_ring_species_data(rows, locations)

    write_js(campus, global_data, interdisciplinary, ring_species)
    diss_index = build_diss_index(rows)
    write_diss_index(diss_index)
    build_world_js()

    print("\n" + "=" * 70)
    print("DONE — open vis/index.html in a browser to explore")
    print("=" * 70)


if __name__ == "__main__":
    run()
