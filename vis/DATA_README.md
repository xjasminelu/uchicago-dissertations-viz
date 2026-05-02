# UChicago Dissertations — Cleaned Dataset

**Data Visualization Fellowship 2025-2026**
Jasmine Lu · Andrew McGallian · Khushi Desai
https://github.com/xjasminelu/uchicago-dissertations-viz

---

## Overview

32,013 University of Chicago dissertation records spanning 1893–2025.
Cleaned, department-normalized, building-assigned, and geographically enriched.

## Coverage

- 32,013 total records
- 31,019 records (96.9%) have a mapped canonical department and campus building
- 994 records (3.1%) are UNKNOWN — dissolved programs, division-level entries, medical campus depts
- 4,068 records (12.7%) have a detectable geographic research focus via NER

## Data Sources

- **ProQuest TDM Studio** — primary export, all 32,013 records 1893–2025
  (titles, authors, advisors, committee members, keywords, subject terms)
- **UChicago Convocation Programs 1932–2009** — used to fill missing department field
  for pre-2009 ProQuest records (22,993 records, sourced by Jasmine Lu)
- **HathiTrust Digitized Catalog 1893–1931** — used to fill missing department field
  for earliest-era records (2,617 records)

## Columns

| Column | Description |
|---|---|
| goid | ProQuest unique ID |
| title | Dissertation title |
| date | Year of completion (4-digit) |
| authors | Author(s) |
| advisors | Advisor(s) |
| committee_members | Committee members |
| degree | Degree type (e.g. PhD) |
| dept_raw | Original department string from whichever source filled it |
| dept_modern | Normalized canonical department (44 values + UNKNOWN) |
| campus_building | Building assigned via historical routing |
| keywords_author | Author-assigned ProQuest keywords |
| subject_terms | ProQuest controlled vocabulary subject terms |
| class_terms | ProQuest classification terms |
| geo_entities | Semicolon-separated countries detected via NER |
| geo_primary | Primary detected geographic focus country |

## Department Normalization

Raw department strings were normalized to 44 canonical modern departments via an
explicit 300+ entry lookup table (DEPT_MAP in GIS/dept_housing.py). Covers historical
names, program renames, abbreviations, and all-caps convocation format.

## Building Assignment

Each dissertation is routed to the campus building the department actually occupied
at the time the dissertation was written, not the department's current address.
Departments that relocated are handled via DEPT_TIMELINE in GIS/prepare_viz_data.py.

## Geographic Enrichment

Geographic entities extracted using spaCy en_core_web_lg NER on title + keywords +
subject terms. Normalized to ISO country names via a 200+ entry alias table.
Note: no abstracts are available in the ProQuest export, so coverage is limited
to what appears in titles and keywords.

## Reproducibility

The full pipeline is deterministic and reproducible given the input files.

## License

CC BY 4.0 — see LICENSE.txt

## Citation

Jasmine Lu, Andrew McGallian, Khushi Desai.
*Visualizing University of Chicago Dissertation Topics (1893–2025)*.
Data Visualization Fellowship 2025-2026.
https://github.com/xjasminelu/uchicago-dissertations-viz
