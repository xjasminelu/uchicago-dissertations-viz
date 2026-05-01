# GIS Pipeline — UChicago Dissertation Spatialization (1893–2025)

Interactive GIS visualization of 32,013 University of Chicago dissertations — campus choropleth, global research focus, keyword similarity, and department histories.

McGallian Data Visualization Fellowship 2025

---

## Live visualization

Open `vis/index.html` in a browser. No server required — all data is pre-embedded in `vis/vis_data.js`, `vis/world_data.js`, and `vis/diss_index.js`.

---

## Full pipeline — step by step

All scripts live in `GIS/`. Run them in order from the project root:

```
python3 GIS/fill_departments.py
python3 GIS/dept_housing.py
python3 GIS/geo_ripper.py
python3 GIS/prepare_viz_data.py
```

### Step 0 — `GIS/fill_departments.py`

**What it does:** The ProQuest export (`extended-annotated.csv`) has department metadata only for 2009–2025 records. This script fills the gap for all earlier records using two supplementary sources:

1. **UChicago Convocation Programs 1932–2009** (`convocation_data.xlsx`) — 22,993 records from digitized annual convocation programs. Matched to ProQuest by title + year (case-insensitive, whitespace-collapsed). Variable column width handled: pre-~1990 rows store department in the last of 9 columns; post-1990 in the last of 10 columns.

2. **HathiTrust Digitized Catalog 1893–1931** (`hathi_1893-1931.csv`) — 2,617 records. Matched by title + year (exact, then 40-character prefix fallback). When title matching fails, falls back to author surname + year — extracts last name from ProQuest's `['LASTNAME, FIRSTNAME']` format and matches against HathiTrust's `FIRSTNAME LASTNAME` format. Only unambiguous single matches accepted; ties silently discarded.

**Output:** `GIS/processed/dept_overrides.json` — GOID → raw department string for all filled records.

---

### Step 1+2 — `GIS/dept_housing.py`

**What it does:**
1. Applies `dept_overrides.json` to fill empty Department fields in `extended-annotated.csv`.
2. Normalizes all raw department strings to 44 canonical modern departments via `DEPT_MAP` — an explicit lookup table with 300+ entries covering historical names, program renames, abbreviations, typos, cross-disciplinary committee names, and ISTP prefixes. Lookup is case-insensitive.
3. Joins canonical departments to building polygons from the UChicago Property Footprint GeoJSON.

**Key normalization decisions documented in `DEPT_MAP`:**
- Zoology / Botany / Animal Ecology → Ecology and Evolution
- Semitic Languages / Semitics / Assyriology → Near Eastern Languages and Civilizations
- Biophysics (1947–1980) → Biochemistry and Molecular Biology (Committee on Biophysics, Whitman Lab, BSD)
- Biophysics and Theoretical Biology (1974–1994) → Ecology and Evolution (successor committee, CLSC, BSD)
- International Relations → Political Science (Committee on IR was a social science committee, not a policy school ancestor)
- Home Economics → Home Economics (canonical; dissolved 1956, building at 5740 S Woodlawn)
- Graduate Library School → Graduate Library School (canonical; discontinued 1989)
- Division-level entries → UNKNOWN (too broad to assign)
- Medical campus clinical depts (Radiology, Ophthalmology) → UNKNOWN (outside academic quad)

**Outputs:**
- `GIS/processed/dissertations_with_departments.csv` — 32,013 rows with `department_original` and `modern_department`
- `GIS/processed/departments_with_buildings.geojson` — 44 dept features with building polygons
- `GIS/processed/dissertations_spatial.geojson` — one feature per mapped dissertation
- `GIS/processed/unmapped_departments.txt` — any new raw strings not covered by DEPT_MAP (should be empty after a clean run)

---

### Step 3 — `GIS/geo_ripper.py`

**What it does:** Extracts geographic research focus from dissertation metadata using spaCy NER.

- Runs `en_core_web_lg` on concatenated `Title` + `Paper Keywords` + `Subject Terms` for each record.
- Collects GPE and LOC entity types.
- Normalizes to ISO country names via a 200+ entry alias table (`COUNTRY_NORMALIZE` + `CITY_TO_COUNTRY`): historical states (USSR → Russia), city → country (London → United Kingdom), ancient entities (Babylonia → Iraq), Chinese dynasties, pre-Columbian civilizations, etc.
- Filters out US states, US cities, macro-regions (Asia, Latin America), and NLP garbage (run-on academic phrases, terms with digits or math notation).
- Stores all detected countries as semicolon-separated `geo_entities`; first country as `primary_geo`.

**Output:** `GIS/processed/dissertations_geo_enriched.csv` — adds `geo_entities`, `primary_geo`, `geo_lat`, `geo_lon` columns.

---

### Step 4 — `GIS/prepare_viz_data.py`

**What it does:** Reads all pipeline outputs and builds the JavaScript data files for the visualization.

1. **Campus choropleth data** — aggregates dissertation counts by department × year × building, using `DEPT_TIMELINE` to route each dissertation to the building the department actually occupied at that time (not current address). All 44 departments covered across all eras. Building polygons from the Property Footprint GeoJSON.

2. **Keyword similarity** — for each department, concatenates all `Paper Keywords` and `Subject Terms` into a keyword document. Vectorizes with `TfidfVectorizer` (scikit-learn; up to 800 features, English stop words removed, min_df=2, unigrams). Computes pairwise cosine similarity → 44×44 matrix. Also computes per-decade matrices (500 features, min_df=1). Computes campus tour order via nearest-neighbor TSP + 2-opt improvement starting from the northernmost building.

3. **Global research data** — aggregates geo_entities by country, year, and decade.

4. **Dissertation index** — inverted index by building and by country, for the click-to-explore modal.

5. **World polygons** — downloads Natural Earth GeoJSON via `datasets/geo-countries` (cached as `vis/world.geojson`).

**Outputs:**
- `vis/vis_data.js` — all campus, similarity, and global data as JS constants
- `vis/world_data.js` — world country polygons
- `vis/diss_index.js` — dissertation explorer index (byBuilding, byCountry)

---

### Shareable dataset

`dissertations_clean_share.csv` (repo root) — 32,013 rows, 15 columns, ready for collaborators:

| Column | Description |
|---|---|
| `goid` | ProQuest unique ID |
| `title` | Dissertation title |
| `date` | Year (4-digit) |
| `authors` | Author(s) |
| `advisors` | Advisor(s) |
| `committee_members` | Committee members |
| `degree` | Degree type |
| `dept_raw` | Original department string from source |
| `dept_modern` | Normalized canonical department (44 values + UNKNOWN) |
| `campus_building` | Building assigned by historical routing |
| `keywords_author` | Author-assigned ProQuest keywords |
| `subject_terms` | ProQuest controlled vocabulary |
| `class_terms` | ProQuest classification terms |
| `geo_entities` | Semicolon-separated detected countries |
| `geo_primary` | Primary geographic focus |

**Coverage:** 30,662 / 32,013 records (95.8%) have a mapped department and building. 1,351 (4.2%) are UNKNOWN — division-level records, discontinued programs with no confirmed building (Home Economics, Radiology, medical campus clinical depts).

---

## Input files

| File | Source | Notes |
|---|---|---|
| `extended-annotated.csv` | ProQuest TDM Studio export | 32,013 UChicago dissertations 1893–2025. **Requires ProQuest license — not redistributable.** |
| `convocation_data.xlsx` | UChicago Library / convocation office archives | 22,993 records, 1932–2009 |
| `hathi_1893-1931.csv` | HathiTrust Digital Library digitized catalog | 2,617 records, 1893–1931 |
| `uchicago_locations.csv` | Compiled manually | 44 canonical departments with current buildings and corrected coordinates |
| `uchicago-property-footprint-2-28-25.geojson` | UChicago Facilities Services, 2025 | 344 campus buildings with polygons and year_built |

---

## Dependencies

```
Python 3.10+
spacy >= 3.8
scikit-learn >= 1.4
numpy >= 1.26
```

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

All other dependencies (`csv`, `json`, `zipfile`, `xml.etree.ElementTree`, `pathlib`, `collections`, `math`, `re`) are Python standard library.

The `.xlsx` reader in `fill_departments.py` uses `zipfile` + stdlib XML parsing — **no openpyxl required**.

---

## Reproducibility

**Fully reproducible** given the input files. The pipeline is deterministic:
- No random seeds needed (TF-IDF + cosine similarity are deterministic; TSP uses a fixed starting point)
- All normalization decisions are in explicit lookup tables (`DEPT_MAP` in `dept_housing.py`, `COUNTRY_NORMALIZE` in `prepare_viz_data.py`)
- All judgment calls are documented inline in the source files and in the Dept Histories tab of the visualization

**The one non-redistributable input** is `extended-annotated.csv` (ProQuest license). A collaborator with institutional ProQuest TDM Studio access can re-export the same query (University of Chicago, all years, all fields). The other four input files are in this repository.

**To re-run from scratch:**
```bash
pip install -r requirements.txt
python -m spacy download en_core_web_lg
python3 GIS/fill_departments.py
python3 GIS/dept_housing.py
python3 GIS/geo_ripper.py
python3 GIS/prepare_viz_data.py
# Open vis/index.html in a browser
```

---

## Repository structure

```
├── extended-annotated.csv          # ProQuest export (not redistributable)
├── convocation_data.xlsx           # UChicago convocation programs 1932–2009
├── hathi_1893-1931.csv             # HathiTrust catalog 1893–1931
├── uchicago_locations.csv          # 44 canonical departments + buildings
├── uchicago-property-footprint-2-28-25.geojson  # Campus building polygons
├── requirements.txt
├── dissertations_clean_share.csv   # Shareable dataset (32,013 rows, 15 columns)
├── GIS/
│   ├── fill_departments.py         # Step 0: fill dept gaps from supplementary sources
│   ├── dept_housing.py             # Step 1+2: normalize depts + join to buildings
│   ├── geo_ripper.py               # Step 3: NER geographic extraction
│   ├── prepare_viz_data.py         # Step 4: build vis JS data files
│   └── processed/
│       ├── dept_overrides.json                 # GOID → raw dept (from fill_departments)
│       ├── dissertations_with_departments.csv  # After normalization
│       ├── departments_with_buildings.geojson  # 44 dept polygons
│       ├── dissertations_geo_enriched.csv      # After geo NER
│       └── unmapped_departments.txt            # Audit log (should be empty)
└── vis/
    ├── index.html                  # Interactive visualization (4 tabs)
    ├── vis_data.js                 # Campus + similarity + global data (generated)
    ├── world_data.js               # World GeoJSON (generated)
    └── diss_index.js               # Dissertation explorer index (generated)
```
