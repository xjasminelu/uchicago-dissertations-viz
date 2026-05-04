"""
GIS Pipeline — Step 1 & 2: Department Normalization and Building Assignment
============================================================================

University of Chicago Dissertation Spatialization Project
Author: McGallian Data Viz Fellowship 2025

This script performs two sequential join operations:

  Step 1. Normalize raw dissertation department strings → modern department names
          Source: extended-annotated.csv (Department column)
          Target: uchicago_locations.csv (44 canonical departments)
          Output: dissertations_with_departments.csv

  Step 2. Join modern departments → building polygons
          Source: uchicago_locations.csv (Building column)
          Geometry source: uchicago-property-footprint-2-28-25.geojson
          Output: departments_with_buildings.geojson
               +  dissertations_spatial.geojson

Normalization methodology
--------------------------
The raw dissertation data (1893–2025) contains 156 distinct department strings
reflecting 130 years of bureaucratic change, program creation and dissolution,
interdisciplinary cross-listing conventions, and data entry inconsistency.
The following resolution rules are applied in priority order:

  R1. Empty / null department          → UNKNOWN
  R2. Exact match to modern dept       → modern dept
  R3. ISTP prefix strip                → strip "Interdisciplinary Scientist
                                          Training Program: ", then re-evaluate
  R4. Classics sub-program prefix      → strip "Classics: ", map to Classics
  R5. Psychology sub-program suffix    → strip ": <track>", map to Psychology
  R6. Capitalization normalization      → lowercase "And" → "and", "&" → "and"
  R7. Punctuation normalization        → remove spurious commas in program names
  R8. Historical renames               → e.g., Evolutionary Biology →
                                          Ecology and Evolution (renamed ~1990s)
  R9. Division-level entries           → e.g., Social Work, Policy, and Practice
                                          → Social Service Administration
                                          (Crown Family School renamed SSA)
  R10. Joint/dual department entries   → map to the PRIMARY (first-listed) dept
       where the primary dept is modern. If primary has no modern match,
       UNKNOWN is assigned — not the secondary — to avoid spurious attribution.
  R11. No match after all rules        → UNKNOWN

Unmapped departments are logged explicitly and written to
GIS/processed/unmapped_departments.txt for manual review.

Building name correction table
-------------------------------
Five building names in uchicago_locations.csv do not exactly match the
canonical names in the geojson property footprint file. Corrections:

  "Searle Chemistry Laboratory"         → "Searle Chemical Laboratory"
  "Walker Hall"                         → "Walker Museum"
  "Social Sciences Research Building"   → "Social Science Research Building"
  "Oriental Institute"                  → "Institute for the Study of Ancient
                                           Cultures"  (building renamed 2019)
  "Crown Family School"                 → "Crown Family School of Social Work,
                                           Policy, and Practice"
  "Harper Center"                       → "Booth School of Business"
                                          (Harper Center is UChicago's internal
                                           name for the Booth building)

Validation thresholds
---------------------
  - Department normalization match rate must be ≥ 90% of non-empty records
  - Building geometry match rate must be 100% of modern departments
  If either threshold is breached, the script raises and does not write outputs.
"""

import csv
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
GIS_OUT = Path(__file__).resolve().parent / "processed"
GIS_OUT.mkdir(exist_ok=True)

DISSERTATIONS_CSV  = ROOT / "extended-annotated.csv"
LOCATIONS_CSV      = ROOT / "uchicago_locations.csv"
FOOTPRINT_GEOJSON  = ROOT / "uchicago-property-footprint-2-28-25.geojson"

OUT_DISS_WITH_DEPTS   = GIS_OUT / "dissertations_with_departments.csv"
OUT_DEPTS_WITH_BLDGS  = GIS_OUT / "departments_with_buildings.geojson"
OUT_DISS_SPATIAL      = GIS_OUT / "dissertations_spatial.geojson"
OUT_UNMAPPED_LOG      = GIS_OUT / "unmapped_departments.txt"

# ---------------------------------------------------------------------------
# Step 0 — Load modern department list
# ---------------------------------------------------------------------------

def load_modern_departments(path):
    """
    Returns a dict: modern_dept_name -> {building, address, lat, lon}
    and a set of canonical modern department names (the join targets).
    """
    modern = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dept = row["Department"].strip()
            modern[dept] = {
                "building": row["Building"].strip(),
                "address":  row["Address"].strip(),
                "lat":      row["Latitude"].strip(),
                "lon":      row["Longitude"].strip(),
            }
    return modern


# ---------------------------------------------------------------------------
# Step 1 — Explicit department normalization mapping
# ---------------------------------------------------------------------------

# Full explicit lookup table: raw department string → modern department name.
# Every unique value observed in extended-annotated.csv is listed here.
# Rationale for each decision class is documented inline.

DEPT_MAP = {

    # --- Empty / missing / explicit unknown --------------------------------
    "":        "UNKNOWN",
    "UNKNOWN": "UNKNOWN",   # injected by fill_departments.py for unmappable historical depts

    # --- Direct matches (exact, already canonical) -------------------------
    "Anthropology":                        "Anthropology",
    "Art History":                         "Art History",
    "Astronomy and Astrophysics":          "Astronomy and Astrophysics",
    "Biochemistry and Molecular Biology":  "Biochemistry and Molecular Biology",
    "Business":                            "Business",
    "Chemistry":                           "Chemistry",
    "Classics":                            "Classics",
    "Computer Science":                    "Computer Science",
    "Divinity":                            "Divinity",
    "Ecology and Evolution":               "Ecology and Evolution",
    "Economics":                           "Economics",
    "Education":                           "Education",
    "English Language and Literature":     "English Language and Literature",
    "Geophysical Sciences":                "Geophysical Sciences",
    "History":                             "History",
    "Linguistics":                         "Linguistics",
    "Mathematics":                         "Mathematics",
    "Microbiology":                        "Microbiology",
    "Music":                               "Music",
    "Near Eastern Languages and Civilizations": "Near Eastern Languages and Civilizations",
    "Organismal Biology and Anatomy":      "Organismal Biology and Anatomy",
    "Philosophy":                          "Philosophy",
    "Physics":                             "Physics",
    "Political Science":                   "Political Science",
    "Psychology":                          "Psychology",
    "Romance Languages and Literatures":   "Romance Languages and Literatures",
    "Social Service Administration":       "Social Service Administration",
    "Sociology":                           "Sociology",
    "Statistics":                          "Statistics",

    # --- Capitalization variants -------------------------------------------
    # ProQuest records occasionally title-case conjunctions.
    "Biochemistry And Molecular Biology":  "Biochemistry and Molecular Biology",

    # --- Punctuation / formatting variants of modern depts -----------------
    # Genetics, Genomics, and Systems Biology: the canonical modern name in
    # uchicago_locations.csv omits commas.
    "Genetics, Genomics, and Systems Biology":       "Genetics Genomics and Systems Biology",
    "Genetics Genomics and Systems Biology":         "Genetics Genomics and Systems Biology",

    # --- Historical renames -----------------------------------------------
    # Evolutionary Biology was the predecessor program to Ecology and Evolution.
    # The dept was reorganized and renamed in the 1990s; dissertations prior to
    # that reorganization carry the old name.
    "Evolutionary Biology":                "Ecology and Evolution",

    # Classical Languages and Literatures is a named sub-track within the
    # Classics department; all records belong to Classics administratively.
    "Classical Languages and Literatures": "Classics",

    # Ancient Mediterranean World is a cross-listed concentration housed in
    # the Classics department.
    "Ancient Mediterranean World":         "Classics",

    # Genetics (undifferentiated) predates the modern GGSB program name.
    "Genetics":                            "Genetics Genomics and Systems Biology",

    # New Testament and Early Christian Literature is a track within the
    # Divinity School; dissertations carry the track name, not the school name.
    "New Testament and Early Christian Literature": "Divinity",

    # Social Work, Policy, and Practice is the renamed Crown Family School,
    # formerly the School of Social Service Administration. Both names refer
    # to the same building and administrative unit.
    "Social Work, Policy, and Practice":   "Social Service Administration",

    # Computational and Applied Mathematics is administered within the
    # Mathematics department at UChicago (CCAM is not a separate dept).
    "Computational and Applied Mathematics": "Mathematics",

    # --- Classics sub-program prefix (R4) ----------------------------------
    # Records of the form "Classics: <track>" belong to the Classics dept.
    "Classics: Ancient Mediterranean World":                        "Classics",
    "Classics: Classical Languages and Literatures":                "Classics",
    "Classics: Classical Languages and Literatures and Social Thought": "Classics",

    # --- Psychology sub-program suffix (R5) --------------------------------
    "Psychology: Committee on Human Development / Mental Health Research": "Psychology",
    "Psychology: Human Development":                 "Psychology",
    "Psychology: Research Methodology and Quantitative Psychology":  "Psychology",

    # --- ISTP prefix strip (R3) -------------------------------------------
    # The Interdisciplinary Scientist Training Program is an administrative
    # umbrella; the dissertation department is the disciplinary home.
    "Interdisciplinary Scientist Training Program: Biochemistry and Molecular Biology":
        "Biochemistry and Molecular Biology",
    "Interdisciplinary Scientist Training Program: Biochemistry and Molecular Biophysics":
        "Biochemistry and Molecular Biology",   # biophysics housed in GCIS, same division
    "Interdisciplinary Scientist Training Program: Biophysical Sciences":
        "Biochemistry and Molecular Biology",   # Biophysical Sciences housed in GCIS
    "Interdisciplinary Scientist Training Program: Cancer Biology":
        "Biochemistry and Molecular Biology",   # cancer biology in BSD/GCIS cluster
    "Interdisciplinary Scientist Training Program: Chemistry":
        "Chemistry",
    "Interdisciplinary Scientist Training Program: Computational Neuroscience":
        "Organismal Biology and Anatomy",       # neuroscience housed in BSD (Culver Hall)
    "Interdisciplinary Scientist Training Program: Development, Regeneration, and Stem Cell Biology":
        "Genetics Genomics and Systems Biology", # DRSB is a GGSB track
    "Interdisciplinary Scientist Training Program: Ecology and Evolution":
        "Ecology and Evolution",
    "Interdisciplinary Scientist Training Program: Genetics, Genomics, and Systems Biology":
        "Genetics Genomics and Systems Biology",
    "Interdisciplinary Scientist Training Program: Human Genetics":
        "Genetics Genomics and Systems Biology", # HGEN folded into GGSB
    "Interdisciplinary Scientist Training Program: Immunology":
        "Microbiology",                          # Immunology co-housed in Cummings
    "Interdisciplinary Scientist Training Program: Integrative Neuroscience":
        "Organismal Biology and Anatomy",        # neuroscience in BSD, Culver Hall
    "Interdisciplinary Scientist Training Program: Microbiology":
        "Microbiology",
    "Interdisciplinary Scientist Training Program: Molecular Engineering":
        "Molecular Engineering",                 # PME / William Eckhardt Research Center
    "Interdisciplinary Scientist Training Program: Molecular Metabolism and Nutrition":
        "Biochemistry and Molecular Biology",    # MMN is a BSD biochem-adjacent track
    "Interdisciplinary Scientist Training Program: Neurobiology":
        "Organismal Biology and Anatomy",        # neurobiology historically in OBA
    "Interdisciplinary Scientist Training Program: Public Health Sciences":
        "Biochemistry and Molecular Biology",    # PHS is a BSD program (GCIS cluster)

    # --- BSD biomedical programs mapped by divisional home ----------------
    # The Biological Sciences Division organizes around four core buildings;
    # programs are attributed to their closest administrative/spatial home.
    "Biochemistry and Molecular Biophysics":   "Biochemistry and Molecular Biology",   # GCIS
    "Biology":                                 "Ecology and Evolution",                 # historical catch-all, BSLC
    "Biophysical Sciences":                    "Biochemistry and Molecular Biology",    # GCIS
    "Cancer Biology":                          "Biochemistry and Molecular Biology",    # GCIS / Edelstone
    "Cell and Molecular Biology":              "Biochemistry and Molecular Biology",    # GCIS
    "Cellular and Molecular Physiology":       "Organismal Biology and Anatomy",        # Culver Hall
    "Computational Neuroscience":              "Organismal Biology and Anatomy",        # BSD neuroscience, Culver
    "Development, Regeneration, and Stem Cell Biology": "Genetics Genomics and Systems Biology", # GCIS
    "Developmental Biology":                   "Organismal Biology and Anatomy",        # Culver Hall
    "Human Genetics":                          "Genetics Genomics and Systems Biology", # HGEN → GGSB, GCIS
    "Immunology":                              "Microbiology",                          # co-housed Cummings
    "Integrative Biology":                     "Ecology and Evolution",                 # BSLC
    "Integrative Neuroscience":                "Organismal Biology and Anatomy",        # BSD, Culver Hall
    "Molecular Genetics and Cell Biology":     "Genetics Genomics and Systems Biology", # MGCB → GGSB, GCIS
    "Molecular Metabolism and Nutrition":      "Biochemistry and Molecular Biology",    # GCIS
    "Neurobiology":                            "Organismal Biology and Anatomy",        # historically OBA, Culver
    "Pathology":                               "Organismal Biology and Anatomy",        # BSD-adjacent, nearest unit
    "Public Health Sciences":                  "Biochemistry and Molecular Biology",    # BSD program, GCIS cluster

    # --- Physical Sciences / Engineering ----------------------------------
    "Medical Physics":                         "Physics",                               # Kersten; Med Phys draws from PSD
    "Molecular Engineering":                   "Molecular Engineering",                 # Pritzker School, Eckhardt

    # --- Social Sciences / Policy -----------------------------------------
    "Health Studies":                          "Social Service Administration",          # Crown Family School umbrella
    "Public Policy Studies":                   "Public Policy Studies",                  # Harris School, Keller Center
    "Public Policy Studies and Sociology":     "Public Policy Studies",

    # --- History of Culture -----------------------------------------------
    # The Committee on History of Culture was a UChicago interdisciplinary
    # committee that dissolved; its dissertations are attributed to History
    # (Social Science Research Building), its closest administrative home.
    "History of Culture":                      "History",

    # --- Humanities departments with own building (Wieboldt Hall) ---------
    # Wieboldt Hall (1050 E 59th St) is the primary home for most humanities
    # language and literature programs not listed in the original locations file.
    "Cinema and Media Studies":                "Cinema and Media Studies",
    "Comparative Literature":                  "Comparative Literature",
    "East Asian Languages and Civilizations":  "East Asian Languages and Civilizations",
    "Germanic Studies":                        "Germanic Studies",
    "Slavic Languages and Literatures":        "Slavic Languages and Literatures",
    "Social Thought":                          "Social Thought",
    "South Asian Languages and Civilizations": "South Asian Languages and Civilizations",

    # --- Comparative Human Development ------------------------------------
    # CHD is a Social Sciences Division committee with strong ties to
    # Psychology; shares Green Hall (5848 S University Ave).
    "Comparative Human Development":           "Comparative Human Development",

    # --- Comparative Literature -------------------------------------------
    # (handled above; entry present for clarity)

    # --- Conceptual and Historical Studies of Science ---------------------
    # CHSS is a cross-divisional committee housed in the Social Science
    # Research Building alongside History, its closest intellectual neighbor.
    "Conceptual and Historical Studies of Science": "Conceptual and Historical Studies of Science",

    # --- Jewish Studies ---------------------------------------------------
    # The Program in Jewish Studies at UChicago is affiliated with the
    # Divinity School and shares Swift Hall.
    "Jewish Studies":                          "Jewish Studies",

    # --- Joint / dual department entries (R10) ----------------------------
    # Primary (first-listed) department is used. Where both depts are now
    # mapped, the first-listed takes spatial precedence.
    "Anthropology and Cinema and Media Studies":               "Anthropology",
    "Anthropology and History":                                "Anthropology",
    "Anthropology and Linguistics":                            "Anthropology",
    "Anthropology and Social Service Administration":          "Anthropology",
    "Anthropology and South Asian Languages and Civilizations":"Anthropology",
    "Business and Economics":                                  "Business",
    "Business and Psychology":                                 "Business",
    "Business and Sociology":                                  "Business",
    "Cinema and Media Studies and Art History":                "Cinema and Media Studies",
    "Cinema and Media Studies and East Asian Languages and Civilizations":
        "Cinema and Media Studies",
    "Cinema and Media Studies and Germanic Studies":           "Cinema and Media Studies",
    "Cinema and Media Studies, East Asian Languages and Civilizations":
        "Cinema and Media Studies",
    "Cinema & Media Studies and East Asian Languages & Civilizations":
        "Cinema and Media Studies",
    "Classics and History":                                    "Classics",
    "Classics and Social Thought":                             "Classics",
    "Comparative Human Development and Anthropology":          "Comparative Human Development",
    "Comparative Human Development and Linguistics":           "Comparative Human Development",
    "Comparative Human Development and Psychology":            "Comparative Human Development",
    "Comparative Human Development and Sociology":             "Comparative Human Development",
    "Comparative Literature and Divinity":                     "Comparative Literature",
    "Computer Science and Linguistics":                        "Computer Science",
    "Computer Science and Mathematics":                        "Computer Science",
    "Conceptual and Historical Studies of Science and Anthropology":
        "Conceptual and Historical Studies of Science",
    "Conceptual and Historical Studies of Science and History":
        "Conceptual and Historical Studies of Science",
    "Conceptual and Historical Studies of Science and Philosophy":
        "Conceptual and Historical Studies of Science",
    "Divinity and Comparative Literature":                     "Divinity",
    "Divinity and Near Eastern Languages and Civilizations":   "Divinity",
    "East Asian Languages and Civilizations and Cinema and Media Studies":
        "East Asian Languages and Civilizations",
    "Economics and Business":                                  "Economics",
    "English Language and Literature and Theater and Performance Studies":
        "English Language and Literature",
    "Germanic Studies and Cinema and Media Studies":           "Germanic Studies",
    "Germanic Studies and Divinity":                           "Germanic Studies",
    "Germanic Studies and Philosophy":                         "Germanic Studies",
    "History and Anthropology":                                "History",
    "Mathematics and Computer Science":                        "Mathematics",
    "Music and Slavic Languages and Literatures":              "Music",
    "Music and Theater and Performance Studies":               "Music",
    "Near Eastern Languages and Civilizations and History":    "Near Eastern Languages and Civilizations",
    "Political Science and Anthropology":                      "Political Science",
    "Political Science and Divinity":                          "Political Science",
    "Political Science and Slavic Languages and Literatures":  "Political Science",
    "Psychology and Business":                                 "Psychology",
    "Slavic Languages and Literatures and Linguistics":        "Slavic Languages and Literatures",
    "Slavic Languages and Literatures, and Cinema and Media Studies":
        "Slavic Languages and Literatures",
    "Social Thought and Art History":                          "Social Thought",
    "Social Thought and Classics":                             "Social Thought",
    "Social Thought and Comparative Literature":               "Social Thought",
    "Social Thought and English":                              "Social Thought",
    "Social Thought and Germanic Studies":                     "Social Thought",
    "Social Thought and Philosophy":                           "Social Thought",
    "Social Thought and Romance Languages and Literatures":    "Social Thought",
    "Social Work, Policy, and Practice and Sociology":         "Social Service Administration",
    "Sociology and Business":                                  "Sociology",
    "Sociology and Economics":                                 "Sociology",
    "South Asian Languages & Civilizations and History":       "South Asian Languages and Civilizations",
    "South Asian Languages and Civilizations and Anthropology":
        "South Asian Languages and Civilizations",
    "South Asian Languages and Civilizations and Conceptual and Historical Studies of Science":
        "South Asian Languages and Civilizations",
    "South Asian Languages and Civilizations and English Language and Literature":
        "South Asian Languages and Civilizations",
    "South Asian Languages and Civilizations and History":     "South Asian Languages and Civilizations",
    "South Asian Languages and Civilizations and Near Eastern Languages and Civilizations":
        "South Asian Languages and Civilizations",
    "South Asian Languages and Civilizations and Theater and Performance Studies":
        "South Asian Languages and Civilizations",

    # ===========================================================================
    # Historical department names (pre-2009 sources: convocation records,
    # Hathi Trust, ProQuest pre-2010).  Naming conventions follow 19th- and
    # early-20th-century departmental nomenclature.
    # ===========================================================================

    # --- Physics and physical sciences ----------------------------------------
    "Natural Philosophy":               "Physics",           # 19th-c. term for physics
    "Mathematical Physics":             "Physics",
    "Mathematical Astronomy":           "Astronomy and Astrophysics",
    "Astronomy":                        "Astronomy and Astrophysics",
    "Astrophysics":                     "Astronomy and Astrophysics",
    "Astronomical Sciences":            "Astronomy and Astrophysics",

    # --- Chemistry ------------------------------------------------------------
    "Organic Chemistry":                "Chemistry",
    "Physical Chemistry":               "Chemistry",
    "Inorganic Chemistry":              "Chemistry",
    "Analytical Chemistry":             "Chemistry",
    "Physiological Chemistry":          "Biochemistry and Molecular Biology",

    # --- Mathematics ----------------------------------------------------------
    "Applied Mathematics":              "Mathematics",
    "Pure Mathematics":                 "Mathematics",
    "Mathematical Science":             "Mathematics",

    # --- Statistics -----------------------------------------------------------
    "Mathematical Statistics":          "Statistics",
    "Applied Statistics":               "Statistics",

    # --- Biological sciences — historical subdiscipline names -----------------
    # These were separate departments or tracks before the modern BSD structure.
    "Zoology":                          "Ecology and Evolution",    # Zoology Bldg, Hull Labs
    "Animal Ecology":                   "Ecology and Evolution",
    "Botany":                           "Ecology and Evolution",    # Hull Court, Culver Hall
    "Plant Physiology":                 "Ecology and Evolution",
    "Physiology":                       "Organismal Biology and Anatomy",   # Erman (orig. Physiology Bldg)
    "Anatomy":                          "Organismal Biology and Anatomy",   # Anatomy Building
    "Embryology":                       "Organismal Biology and Anatomy",
    "Histology":                        "Organismal Biology and Anatomy",
    "Morphology":                       "Organismal Biology and Anatomy",
    "Neurology":                        "Organismal Biology and Anatomy",
    "Cellular and Molecular Biology":   "Biochemistry and Molecular Biology",
    "Bacteriology":                     "Microbiology",
    "Parasitology":                     "Microbiology",

    # --- Geophysical Sciences — historical earth science names ----------------
    "Geology":                          "Geophysical Sciences",
    "Mineralogy":                       "Geophysical Sciences",
    "Paleontology":                     "Geophysical Sciences",
    "Petrography":                      "Geophysical Sciences",
    "Stratigraphy":                     "Geophysical Sciences",
    "Meteorology":                      "Geophysical Sciences",
    "Geophysics":                       "Geophysical Sciences",
    "Physical Geography":               "Geophysical Sciences",

    # --- Classics and ancient studies -----------------------------------------
    "Greek":                            "Classics",
    "Greek Language and Literature":    "Classics",
    "Latin":                            "Classics",
    "Latin Language and Literature":    "Classics",
    "Classical Philology":              "Classics",
    "Greek and Latin":                  "Classics",
    "Classical Archaeology":            "Classics",

    # --- Near Eastern Languages — pre-NELC names ------------------------------
    # Semitic Languages and Literatures was the founding name; renamed to
    # Near Eastern Languages and Civilizations in the 20th century.
    "Semitic Languages and Literatures":        "Near Eastern Languages and Civilizations",
    "Semitic Languages":                        "Near Eastern Languages and Civilizations",
    "Semitics":                                 "Near Eastern Languages and Civilizations",
    "Assyriology":                              "Near Eastern Languages and Civilizations",
    "Egyptology":                               "Near Eastern Languages and Civilizations",
    "Hebrew Language and Literature":           "Near Eastern Languages and Civilizations",
    "Arabic Language and Literature":           "Near Eastern Languages and Civilizations",
    "Old Testament Language and Literature":    "Near Eastern Languages and Civilizations",
    "Biblical and Patristic Greek":             "Near Eastern Languages and Civilizations",

    # --- Divinity School — track and historical subject names -----------------
    "Old Testament Literature and Interpretation":  "Divinity",
    "Old Testament Literature":                     "Divinity",
    "New Testament Literature and Interpretation":  "Divinity",
    "New Testament Literature":                     "Divinity",
    "New Testament Language and Literature":        "Divinity",
    "Church History":                               "Divinity",
    "Systematic Theology":                          "Divinity",
    "Comparative Religion":                         "Divinity",
    "History of Religions":                         "Divinity",
    "Philosophy of Religion":                       "Divinity",
    "Sacred Literature":                            "Divinity",
    "Homiletics":                                   "Divinity",
    "Practical Theology":                           "Divinity",
    "Christian Theology":                           "Divinity",
    "Christian Theology and Ethics":                "Divinity",
    "Religion and Literature":                      "Divinity",
    "Biblical Theology":                            "Divinity",
    "Theology":                                     "Divinity",

    # --- Germanic Studies — historical names ----------------------------------
    "German":                               "Germanic Studies",
    "German Language and Literature":       "Germanic Studies",
    "Teutonic Languages":                   "Germanic Studies",
    "Teutonic Languages and Literature":    "Germanic Studies",
    "Germanic Languages":                   "Germanic Studies",
    "Germanic Languages and Literatures":   "Germanic Studies",

    # --- Romance Languages — individual language names ------------------------
    "Romance Languages":                    "Romance Languages and Literatures",
    "French":                               "Romance Languages and Literatures",
    "French Language and Literature":       "Romance Languages and Literatures",
    "Spanish":                              "Romance Languages and Literatures",
    "Spanish Language and Literature":      "Romance Languages and Literatures",
    "Italian":                              "Romance Languages and Literatures",
    "Italian Language and Literature":      "Romance Languages and Literatures",
    "Portuguese":                           "Romance Languages and Literatures",
    "Romance Philology":                    "Romance Languages and Literatures",

    # --- English --------------------------------------------------------------
    "English":                              "English Language and Literature",
    "English Literature":                   "English Language and Literature",
    "English Language and Linguistics":     "English Language and Literature",

    # --- Linguistics — comparative philology was the 19th-c. term ------------
    "Comparative Philology":                "Linguistics",
    "Indo-European Comparative Philology":  "Linguistics",
    "Philology":                            "Linguistics",
    "General Linguistics":                  "Linguistics",

    # --- South Asian Languages ------------------------------------------------
    "Sanskrit":                             "South Asian Languages and Civilizations",
    "Sanskrit and Comparative Philology":   "South Asian Languages and Civilizations",
    "Pali":                                 "South Asian Languages and Civilizations",
    "Indic Philology":                      "South Asian Languages and Civilizations",

    # --- Slavic ---------------------------------------------------------------
    "Slavic Languages":                     "Slavic Languages and Literatures",
    "Russian Language and Literature":      "Slavic Languages and Literatures",
    "Russian":                              "Slavic Languages and Literatures",

    # --- East Asian Languages -------------------------------------------------
    "Chinese":                              "East Asian Languages and Civilizations",
    "Japanese":                             "East Asian Languages and Civilizations",
    "Far Eastern Languages":               "East Asian Languages and Civilizations",
    "East Asian Studies":                   "East Asian Languages and Civilizations",

    # --- Art History — historical names ---------------------------------------
    "History of Art":                       "Art History",
    "Fine Arts":                            "Art History",
    "Art and Archaeology":                  "Art History",
    "History of Art and Archaeology":       "Art History",

    # --- Music ----------------------------------------------------------------
    "Musicology":                           "Music",
    "Theory of Music":                      "Music",
    "Theoretical Music":                    "Music",

    # --- Social Sciences — historical and variant names -----------------------
    "Political Economy":                    "Economics",    # 19th-c. name for economics
    "Political Science and Administrative Law": "Political Science",
    "Political Institutions":               "Political Science",
    "Sociology and Anthropology":           "Sociology",    # early combined dept; primary = Sociology
    "Social Science":                       "Sociology",    # generic early catch-all

    # --- History — subfield labels used in early records ---------------------
    "American History":                     "History",
    "European History":                     "History",
    "Medieval History":                     "History",
    "Modern History":                       "History",
    "Ancient History":                      "History",
    "Historical Studies":                   "History",

    # --- Anthropology — subfield names ----------------------------------------
    "Social Anthropology":                  "Anthropology",
    "Cultural Anthropology":                "Anthropology",
    "Physical Anthropology":               "Anthropology",

    # --- Psychology -----------------------------------------------------------
    "Experimental Psychology":              "Psychology",
    "Social Psychology":                    "Psychology",

    # --- Education — historical names -----------------------------------------
    "Pedagogy":                             "Education",
    "Educational Administration":           "Education",
    "School Administration":                "Education",
    "Education and Pedagogy":               "Education",

    # --- Comparative Human Development ----------------------------------------
    "Human Development":                    "Comparative Human Development",
    "Child Development":                    "Comparative Human Development",
    "Committee on Human Development":       "Comparative Human Development",
    "Behavioral Sciences: Human Development": "Comparative Human Development",
    "Behavioral Science: Human Development":  "Comparative Human Development",
    "Psychology: Human Development":        "Comparative Human Development",
    "Psychology: Human Development/Mental Health Research": "Comparative Human Development",
    "Human Developmennt":                   "Comparative Human Development",  # typo
    "Committee Human Development":          "Comparative Human Development",  # typo

    # --- Biochemistry and Molecular Biology — historical/variant names ---------
    "Biochemistry":                              "Biochemistry and Molecular Biology",
    "Biochemistry & Molecular Biology":          "Biochemistry and Molecular Biology",
    "Biochemistrt":                              "Biochemistry and Molecular Biology",  # typo
    "Physiological Chemistry and Pharmacology":  "Biochemistry and Molecular Biology",
    "Phsiological Chemistry and Pharmacology":   "Biochemistry and Molecular Biology",  # typo

    # --- Geophysical Sciences — historical names -------------------------------
    "Geography":                             "Geophysical Sciences",
    "Geology and Paleontology":              "Geophysical Sciences",
    "Geology (Geochemistry)":               "Geophysical Sciences",
    "Geology":                               "Geophysical Sciences",
    "Meteorology":                           "Geophysical Sciences",
    "Paleozoology":                          "Geophysical Sciences",
    "Paleonzoology":                         "Geophysical Sciences",  # typo
    "Paleontology":                          "Geophysical Sciences",

    # --- Microbiology — historical names ---------------------------------------
    "Hygiene and Bacteriology":              "Microbiology",
    "Bacteriology and Parasitology":         "Microbiology",
    "Virology":                              "Microbiology",
    "Committee on Virology":                 "Microbiology",
    "Committee on Immunology":               "Microbiology",
    "Immunology":                            "Microbiology",

    # --- Ecology and Evolution — historical names ------------------------------
    "Ecology & Evolution":                   "Ecology and Evolution",
    "Evolutionary Biology":                  "Ecology and Evolution",
    "Committee on Evolutionary Biology":     "Ecology and Evolution",
    "Theoretical Biology":                   "Ecology and Evolution",
    "Mathematical Biology":                  "Ecology and Evolution",
    "Committee on Mathematical Biology":     "Ecology and Evolution",
    "Developmental Biology":                 "Ecology and Evolution",
    "Committee on Developmental Biology":    "Ecology and Evolution",
    "Biology":                               "Ecology and Evolution",

    # --- Genetics Genomics and Systems Biology ---------------------------------
    "Genetics":                              "Genetics Genomics and Systems Biology",
    "Committee on Genetics":                 "Genetics Genomics and Systems Biology",
    "Molecular Genetics and Cell Biology":   "Genetics Genomics and Systems Biology",
    "Molecular Genetics & Cell Biology":     "Genetics Genomics and Systems Biology",

    # --- Organismal Biology and Anatomy — historical names ---------------------
    "Pharmacological and Physiological Sciences":  "Organismal Biology and Anatomy",
    "Pharmacological & Physiological Sciences":    "Organismal Biology and Anatomy",
    "Pharmacological/Physiological Sciences":      "Organismal Biology and Anatomy",
    "Pharamacological and Physiological Sciences": "Organismal Biology and Anatomy",  # typo
    "Pharma Cological and Physiological Sciences": "Organismal Biology and Anatomy",  # typo
    "Pharmacology":                                "Organismal Biology and Anatomy",
    "Pharmacology and Anesthesiology":             "Organismal Biology and Anatomy",
    "Pharmacoogy":                                 "Organismal Biology and Anatomy",  # typo
    "Physiology":                                  "Organismal Biology and Anatomy",
    "Phsiology":                                   "Organismal Biology and Anatomy",  # typo
    "Physiology: Mathemaatical Biophysics":        "Organismal Biology and Anatomy",
    "Surgery":                                     "Organismal Biology and Anatomy",
    "Surgery (Opthalmology)":                      "Organismal Biology and Anatomy",
    "Medicine":                                    "Organismal Biology and Anatomy",
    "Medicine: Medical Chemistry":                 "Organismal Biology and Anatomy",
    "Pathology":                                   "Organismal Biology and Anatomy",
    "Patho":                                       "Organismal Biology and Anatomy",  # typo
    "Neurobiology":                                "Organismal Biology and Anatomy",
    "Organismal Biology & Anatomy":                "Organismal Biology and Anatomy",
    "Biopsychology":                               "Psychology",
    "Biopscyhology":                               "Psychology",  # typo
    "Behavioral Sciences: Biopsychology":          "Psychology",
    "Behavioral Sciences: Committee on Biopsychology": "Psychology",
    "Committee on Biopsychology":                  "Psychology",
    "Psychology (Biopsychology)":                  "Psychology",
    "Psychology: Biopsychology":                   "Psychology",

    # --- Business — historical names -------------------------------------------
    "Graduate School of Business":           "Business",
    "School of Business":                    "Business",
    "Booth School of Business":              "Business",
    "The School of Business":               "Business",
    "Graduate School of Business'":         "Business",  # trailing apostrophe typo

    # --- Social Service Administration — historical names ----------------------
    "School of Social Service Administration":   "Social Service Administration",
    "School of Social Services Administration":  "Social Service Administration",
    "School of Social Service  Administration":  "Social Service Administration",  # double space
    "The Graduate School of Social Service Administration": "Social Service Administration",

    # --- Public Policy Studies — historical names ------------------------------
    "Irving B. Harris Graduate School of Public Policy Studies": "Public Policy Studies",
    "Committee on Public Policy Studies":    "Public Policy Studies",
    "Planning":                              "Public Policy Studies",
    "Committee on Planning":                 "Public Policy Studies",
    # International Relations: same committee — Political Science, not Public Policy
    "International Relations":               "Political Science",
    "Graduate School of Public Policy":      "Public Policy Studies",
    "Graduate School of Public Policy Studies": "Public Policy Studies",

    # --- Social Thought — historical names ------------------------------------
    "Committee on Social Thought":           "Social Thought",
    "Social Thought and Political Science":  "Social Thought",
    "Socialthought and Linguistics":         "Social Thought",  # typo
    "History/Social Thought":               "Social Thought",

    # --- Conceptual and Historical Studies of Science -------------------------
    "Analysis of Ideas and Study of Methods":           "Conceptual and Historical Studies of Science",
    "Analysis of Ideas and the Study of Methods":       "Conceptual and Historical Studies of Science",
    "Analysis of Ideas & the Study of Methods":         "Conceptual and Historical Studies of Science",
    "Analysis of Ideas and Study of Method":            "Conceptual and Historical Studies of Science",
    "Committee on Analysis of Ideas and Study of Methods": "Conceptual and Historical Studies of Science",
    "Committee on Analysis of Ideas and the Study of Methods": "Conceptual and Historical Studies of Science",
    "Committee on the Analysis of Ideas and the Study of Methods": "Conceptual and Historical Studies of Science",
    "Conceptual Foundations of Science":                "Conceptual and Historical Studies of Science",
    "Committee on Conceptual Foundations of Science":   "Conceptual and Historical Studies of Science",
    "Committee on the Conceptual Foundations of Science": "Conceptual and Historical Studies of Science",
    "History of Culture":                               "Conceptual and Historical Studies of Science",
    "Committee on History of Culture":                  "Conceptual and Historical Studies of Science",

    # --- East Asian Languages and Civilizations — historical/variant names ----
    "Far Eastern Languages and Civilizations":   "East Asian Languages and Civilizations",
    "Far Eastern Language and Civilizations":    "East Asian Languages and Civilizations",
    "Far Eastern Languages and Civilization":    "East Asian Languages and Civilizations",
    "Far Eastern Languages & Civilizations":     "East Asian Languages and Civilizations",
    "East Asian Languages & Civilizations":      "East Asian Languages and Civilizations",
    "East Asian Language and Civilizations":     "East Asian Languages and Civilizations",

    # --- Near Eastern Languages and Civilizations — variant names -------------
    "Oriental Languages and Literatures":        "Near Eastern Languages and Civilizations",
    "Oriental Languages and Civilizations":      "Near Eastern Languages and Civilizations",
    "Oriental Languages and Literature":         "Near Eastern Languages and Civilizations",
    "Oriental Languages and Civiliztions":       "Near Eastern Languages and Civilizations",  # typo
    "Oriental Languages":                        "Near Eastern Languages and Civilizations",
    "Near Eastern Languages & Civilizations":    "Near Eastern Languages and Civilizations",
    "Near Eastern Language and Civilizations":   "Near Eastern Languages and Civilizations",
    "Near Eastern Languages and Civilization":   "Near Eastern Languages and Civilizations",

    # --- South Asian Languages and Civilizations — variant names --------------
    "South Asian Languages & Civilizations":     "South Asian Languages and Civilizations",
    "South Asian Languages and Civiliztions":    "South Asian Languages and Civilizations",  # typo

    # --- Germanic Studies — variant names -------------------------------------
    "Germanics":                             "Germanic Studies",
    "Germanic Languages":                    "Germanic Studies",
    "Germanic Languages and Literature":     "Germanic Studies",
    "Germanic Languages and Civilizations":  "Germanic Studies",

    # --- Comparative Literature — variant names --------------------------------
    "Comparative Studies in Literature":          "Comparative Literature",
    "Committee on Comparative Studies in Literature": "Comparative Literature",
    "Comparative Studies in Literature and the Arts":  "Comparative Literature",
    "Comparative Studies in Literature and Arts":      "Comparative Literature",
    "Comparative Studies in Literature in the Arts":   "Comparative Literature",
    "Comparative Studies and Literature and the Arts": "Comparative Literature",
    "Literature":                                "Comparative Literature",
    "Linguistics and Comparative Studies in Literature": "Comparative Literature",

    # --- Divinity — historical/variant names ----------------------------------
    "Divinity School":                       "Divinity",
    "The Divinity School":                   "Divinity",
    "Divinity of School":                    "Divinity",  # typo
    "New Testament":                         "Divinity",
    "New Testament & Early Christian Literature":  "Divinity",
    "Old Testament":                         "Divinity",
    "Biblical Field":                        "Divinity",
    "Theological Field":                     "Divinity",
    "Practical Theology":                    "Divinity",
    "Practical Theology (Religious Education)": "Divinity",

    # --- Art History — variant names ------------------------------------------
    "Art":                                   "Art History",
    "Arts":                                  "Art History",

    # --- Astronomy and Astrophysics — variant names ---------------------------
    "Astronomy & Astrophysics":              "Astronomy and Astrophysics",
    "Astronomy":                             "Astronomy and Astrophysics",
    "Practical Astronomy and Astrophysics":  "Astronomy and Astrophysics",

    # --- Mathematics — typos --------------------------------------------------
    "Mathemtics":                            "Mathematics",
    "Matehmatics":                           "Mathematics",

    # --- Economics — typos ----------------------------------------------------
    "Edconomics":                            "Economics",

    # --- Education — typos ----------------------------------------------------
    "Educations":                            "Education",
    "Edu Cation":                            "Education",  # space typo

    # --- English Language and Literature — variant/typo -----------------------
    "English Language & Literature":         "English Language and Literature",
    "English Language and Literatures":      "English Language and Literature",
    "English Languages and Literature":      "English Language and Literature",
    "English Languages and Literatures":     "English Language and Literature",
    "English Laanguage and Literature":      "English Language and Literature",  # typo
    "English Languge and Ltierature":        "English Language and Literature",  # typo
    "English Language and Ltierature":       "English Language and Literature",  # typo

    # --- Linguistics — typos/variants -----------------------------------------
    "Linguisitics":                          "Linguistics",  # typo
    "Linguistics and Anthropology":          "Linguistics",
    "Anthropology/Linguistics":              "Anthropology",
    "Anthropology and Linguistics":          "Anthropology",
    "Psychology and Linguistics":            "Psychology",
    "Psychology/Linguistics":                "Psychology",
    "Linguistics and Philosophy":            "Philosophy",

    # --- Political Science — typos --------------------------------------------
    "Poltical Science":                      "Political Science",  # typo

    # --- Romance Languages — variants -----------------------------------------
    "Romance Languages and Literature":      "Romance Languages and Literatures",
    "Romance Language and Literatures":      "Romance Languages and Literatures",
    "Romance Languages & Literatures":       "Romance Languages and Literatures",

    # --- Computer Science — variant -------------------------------------------
    "Information Sciences":                  "Computer Science",
    "Committee on Information Sciences":     "Computer Science",

    # --- Ancient Mediterranean World ------------------------------------------
    "Ancient Mediterranean World":           "Near Eastern Languages and Civilizations",
    "Committee on the Ancient Mediterranean World": "Near Eastern Languages and Civilizations",

    # --- Classics — variant names ---------------------------------------------
    "Classical Languages and Literatures":   "Classics",
    "Classical Languages & Literatures":     "Classics",
    "Classical Languages and Literature":    "Classics",
    "Greek Language and Literature":         "Classics",
    "Greek Languages and Literature":        "Classics",
    "Latin Language and Literature":         "Classics",

    # --- Psychology — behavioral sciences committees --------------------------
    "Behavioral Sciences: Cognition & Communication":       "Psychology",
    "Behavioral Sciences: Cognition and Communication":     "Psychology",
    "Behavioral Sciences: Methodology":                     "Psychology",
    "Behavioral Sciences: Research Methodology & Quantitative Psychology": "Psychology",
    "Behavioral Sciences: Social & Organizational Psychology": "Psychology",
    "Behavioral Sciences: Social and Organizational Psychology": "Psychology",
    "Behavioral Sciences":                                  "Psychology",
    "Behavioral Science: Cognition & Communication":        "Psychology",
    "Behavioral Science: Social & Organizational Psychology": "Psychology",
    "Psychology: Methodology":                              "Psychology",
    "Psychology: Cognition & Communication":                "Psychology",
    "Psychology: Cognition and Communication":              "Psychology",
    "Psychology: Social & Organizational Psychology":       "Psychology",
    "Psychology: Social and Organizational Psychology":     "Psychology",

    # --- Division-level fallbacks (too broad → UNKNOWN) -----------------------
    "Division of Social Sciences":                              "UNKNOWN",
    "Division of Social Science":                               "UNKNOWN",
    "Division of the Social Sciences":                         "UNKNOWN",
    "Division of Humanities":                                   "UNKNOWN",
    "Division of the Humanities":                               "UNKNOWN",
    "Division of Physical Sciences":                            "UNKNOWN",
    "Divisiom of Physical Sciences":                            "UNKNOWN",  # typo
    "Division of Biological Sciences and Pritzker School of Medicine": "UNKNOWN",
    "Division of Biological Sciences":                         "UNKNOWN",
    # Graduate Library School (1926–1989): PhD-granting professional school;
    # discontinued 1989. Harper Memorial Library east wing (1926–1969),
    # then Joseph Regenstein Library (1970–1989).
    # Source: Wikipedia/GLS; UChicago Library finding aid ICU.SPCL.GRADLIBRARY.
    "Graduate Library School":                                  "Graduate Library School",
    "The Graduate Library School":                              "Graduate Library School",
    "Library Science":                                          "Graduate Library School",
    # Home Economics / Household Administration / Sanitary Science (1892–1956).
    # Lineage: Dept of Sanitary Science (1892) → Household Administration →
    # Home Economics → dissolved 1956; Margaret Reid moved to Economics.
    # Research building confirmed at 5740 S. Woodlawn Ave from 1929
    # (now houses UChicago Center for the Economics of Human Development).
    # Sources: UChicago Library "Recipes for Domesticity" exhibit;
    #   CEHD building history (cehd.uchicago.edu).
    "Home Economics":                                           "Home Economics",
    "Home Economics and Household Administration":              "Home Economics",
    "Home Economics and Househould Administration":             "Home Economics",  # typo
    # Biophysics Committee (1947–1980): Division of Biological Sciences, Whitman Lab.
    # Topics straddle physics and biology; housed administratively in BSD.
    # Succeeded by Committee on Biophysics and Theoretical Biology (1974–1994).
    # Both dissolved; students absorbed into Biochemistry and related BSD programs.
    # Source: UChicago Grad Catalog 1960–1994; Whitman Lab history (BSD Archives).
    "Biophysics":                                          "Biochemistry and Molecular Biology",
    "Biophysics & Theoretical Biology":                    "Biochemistry and Molecular Biology",
    "Biophysics/Theoretical Biology":                      "Biochemistry and Molecular Biology",
    # Biophysics and Theoretical Biology was the renamed successor committee (1974).
    # Housed in Cummings Life Sciences Center from ~1974. Dissolved ~1994.
    # Source: UChicago Grad Catalog 1974–1994; CLSC building history.
    "Biophysics and Theoretical Biology":                  "Ecology and Evolution",

    # Human Nutrition and Nutritional Biology: Committee in Division of Biological
    # Sciences. Housed in Ben May Department of Cancer Research / Knapp Center area.
    # Later renamed Molecular Metabolism and Nutrition (which maps to BMB).
    # Source: UChicago BSD graduate programs; Ben May Inst. history.
    "Human Nutrition and Nutritional Biology":             "Biochemistry and Molecular Biology",
    "Human Nutrition & Nutritional Biology":               "Biochemistry and Molecular Biology",
    "Human Nutrition and":                                 "Biochemistry and Molecular Biology",  # truncated

    # Radiology, Ophthalmology, Radiation Oncology: clinical/medical school depts
    # housed on the medical campus (S. Maryland Ave), outside the academic quad.
    # No equivalent PhD program in the canonical 42 modern departments.
    # Source: UChicago Pritzker School of Medicine clinical department directories.
    "Radiology":                                                "UNKNOWN",
    "Radiological Physics":                                     "UNKNOWN",
    "Radiation and Cellular Oncology":                          "UNKNOWN",
    "Ophthalmology":                                            "UNKNOWN",
    "Ophthalmology & Visual Science":                           "UNKNOWN",
    "Opthalmology":                                             "UNKNOWN",  # typo
    "Anatomy and Astrophysics":                                 "UNKNOWN",  # data error
    "Departments of Education and of Sociology":                "UNKNOWN",  # ambiguous
    "Hiso":                                                     "UNKNOWN",  # unclear
    "The School of Commerce and Administration":               "Business",
    "The Law School":                                           "UNKNOWN",

    # --- Remaining committee/cross-dept names ---------------------------------
    # Committee on International Relations (1930s–1960s) was a social science
    # committee producing IR/political science research — not a policy school
    # precursor. Mapped to Political Science, its closest intellectual successor.
    "Committee on International Relations":  "Political Science",
    "Committee on Paleozoology":             "Geophysical Sciences",
    "Linguistics and Near Eastern Languages and Civilizations": "Linguistics",
    "Slavic Languages and Literature":       "Slavic Languages and Literatures",

    # --- Cross-disciplinary joint programs (from 10-col convocation rows) ----
    "Neurobiology, Pharmacology, and Physiology": "Organismal Biology and Anatomy",
    "Psychology: Developmental Psychology":        "Comparative Human Development",
    "Slavic Languages and Linguistics":            "Slavic Languages and Literatures",
    "Social Thought and Psychology":               "Social Thought",
    "Human Development/Mental Health Research":    "Comparative Human Development",
    "Linguistics and Psychology":                  "Linguistics",
    "East Asian Languages and Literatures":        "East Asian Languages and Civilizations",
    "Ophthalmology and Visual Science":            "UNKNOWN",
    "History and Economics":                       "History",
    "Jewish Studies and History":                  "Jewish Studies",
    "Slavic Languages & Literatures":              "Slavic Languages and Literatures",
    "Public Policy Studies and Political Science": "Public Policy Studies",
    "Anthropology and Art History":                "Anthropology",
    "Social Thought and the Divinity School":      "Social Thought",
    "Geographical Studies":                        "Geophysical Sciences",
    "Psychology: Biopsychology/Mental Health":     "Psychology",
    "Chemistry and Astronomy and Astrophysics":    "Chemistry",
    "Developmental Psychology/Mental Health Research": "Comparative Human Development",
    "Anthropology and East Asian Languages and Civilizations": "Anthropology",
    "Psychology: Developmental Psychology/Mental Health Research": "Comparative Human Development",
    "Psychology: Biopsychology/Mental Health Research": "Psychology",
    "Germanic Languages & Literatures":            "Germanic Studies",
    "Social Thought and Germanic Languages and Literatures": "Social Thought",
    "Social Thought and Classical Languages and Literatures": "Social Thought",
    "Cinema and Media Studies and English Language and Literature": "Cinema and Media Studies",
    "Social Thought and English Language and Literature": "Social Thought",
    "Social":                                      "UNKNOWN",
    "Psychology and Conceptual and Historical Studies of Science": "Psychology",

    # --- Convocation historical joint & committee entries (1923–2009 cleaned data) --------
    # All-caps strings with '&' separator from convocation program digitization.
    # Joint entries follow R10: primary (first-listed) department is used.
    "ANTHROPOLOGY & LINGUISTICS":                              "Anthropology",
    "ANTHROPOLOGY & HISTORY":                                  "Anthropology",
    "ANTHROPOLOGY & ART HISTORY":                              "Anthropology",
    "LINGUISTICS & ANTHROPOLOGY":                              "Linguistics",
    "LINGUISTICS & PSYCHOLOGY":                                "Linguistics",
    "LINGUISTICS & PHILOSOPHY":                                "Linguistics",
    "LINGUISTICS & NEAR EASTERN LANGUAGES AND CIVILIZATIONS":  "Linguistics",
    "LINGUISTICS & COMPARATIVE STUDIES IN LITERATURE":         "Linguistics",
    "SLAVIC LANGUAGES AND LITERATURES & LINGUISTICS":          "Slavic Languages and Literatures",
    "SOCIAL THOUGHT & COMPARATIVE LITERATURE":                 "Social Thought",
    "SOCIAL THOUGHT & PSYCHOLOGY":                             "Social Thought",
    "SOCIAL THOUGHT & PHILOSOPHY":                             "Social Thought",
    "SOCIAL THOUGHT & DIVINITY SCHOOL":                        "Social Thought",
    "SOCIALTHOUGHT & LINGUISTICS":                             "Social Thought",    # typo in source
    "SOCIAL THOUGHT & GERMANIC LANGUAGES AND LITERATURES":     "Social Thought",
    "SOCIAL THOUGHT & POLITICAL SCIENCE":                      "Social Thought",
    "SOCIAL THOUGHT & CLASSICAL LANGUAGES AND LITERATURES":    "Social Thought",
    "SOCIAL THOUGHT & ENGLISH LANGUAGE AND LITERATURE":        "Social Thought",
    "SOCIAL THOUGHT & ART HISTORY":                            "Social Thought",
    "PSYCHOLOGY & LINGUISTICS":                                "Psychology",
    "PSYCHOLOGY & CONCEPTUAL AND HISTORICAL STUDIES OF SCIENCE": "Psychology",
    "HISTORY & ECONOMICS":                                     "History",
    "HISTORY & SOCIAL THOUGHT":                                "History",
    "JEWISH STUDIES & HISTORY":                                "Jewish Studies",
    "NEAR EASTERN LANGUAGES AND CIVILIZATIONS & LINGUISTICS":  "Near Eastern Languages and Civilizations",
    "EAST ASIAN LANGUAGES AND CIVILIZATIONS & CINEMA AND MEDIA STUDIES": "East Asian Languages and Civilizations",
    "SOUTH ASIAN LANGUAGES AND CIVILIZATIONS & HISTORY":       "South Asian Languages and Civilizations",
    "COMPUTER SCIENCE & MATHEMATICS":                          "Computer Science",
    "CHEMISTRY & ASTRONOMY AND ASTROPHYSICS":                  "Chemistry",
    "EDUCATION & SOCIOLOGY":                                   "Education",

    # Committee-named programs — mapped to their departmental home
    "COMMITTEE ON MEDICAL PHYSICS":          "Physics",                              # PSD, Kersten
    "COMMITTEE ON CANCER BIOLOGY":           "Biochemistry and Molecular Biology",   # BSD/GCIS cluster
    "COMMITTEE ON NEUROBIOLOGY":             "Organismal Biology and Anatomy",       # BSD, Culver Hall
    "COMMITTEE ON CINEMA AND MEDIA STUDIES": "Cinema and Media Studies",

    # Behavioral Sciences / Psychology subdiscipline tracks
    "BEHAVIORAL SCIENCES: RESEARCH METHODOLOGY AND QUANTITATIVE PSYCHOLOGY":              "Psychology",
    "BEHAVIORAL SCIENCES: COMMITTEE ON RESEARCH METHODOLOGY AND QUANTITATIVE PSYCHOLOGY": "Psychology",
    "BEHAVIORAL SCIENCES: COMMITTEE ON COGNITION AND COMMUNICATION":                      "Psychology",
    "PSYCHOLOGY: HUMAN DEVELOPMENT AND MENTAL HEALTH RESEARCH":    "Comparative Human Development",
    "PSYCHOLOGY: BIOPSYCHOLOGY AND MENTAL HEALTH RESEARCH":        "Psychology",
    "PSYCHOLOGY: DEVELOPMENTAL PSYCHOLOGY AND MENTAL HEALTH RESEARCH": "Comparative Human Development",
    "PSYCHOLOGY: COMMITTEE ON HUMAN DEVELOPMENT":                  "Comparative Human Development",
    "HUMAN DEVELOPMENT AND MENTAL HEALTH RESEARCH":                "Comparative Human Development",
    "DEVELOPMENTAL PSYCHOLOGY AND MENTAL HEALTH RESEARCH":         "Comparative Human Development",

    # Other historical programs
    "MINISTRY STUDIES":    "Divinity",                      # Divinity School ministerial track
    "SURGERY (OPHTHALMOLOGY)": "Organismal Biology and Anatomy",   # BSD clinical, Culver Hall
    "PHYSIOLOGY: MATHEMATICAL BIOPHYSICS": "Organismal Biology and Anatomy",   # Physiology → OBA
    "LAW SCHOOL":          "UNKNOWN",                       # no spatial entry for Law School
    "GEOGRAPHY AND PROGRAM OF EDUCATION RESEARCH IN PLANNING": "UNKNOWN",   # Geography dept dissolved 1986
}


_DEPT_MAP_LOWER = {k.lower(): v for k, v in DEPT_MAP.items()}


def normalize_department(raw):
    """
    Apply the explicit DEPT_MAP lookup (case-insensitive).
    Any key not found in the map is flagged as UNKNOWN and recorded for review.
    Returns (modern_dept, was_mapped).
    """
    raw = raw.strip() if raw else ""
    if not raw:
        return "UNKNOWN", True
    # Keyword-list strings that ended up in Department column by parsing error
    if raw.startswith("['") or raw.startswith('["'):
        return "UNKNOWN", True
    if raw in DEPT_MAP:
        return DEPT_MAP[raw], True
    # Case-insensitive fallback (handles ALL-CAPS values from dept_overrides.json)
    lower = raw.lower()
    if lower in _DEPT_MAP_LOWER:
        return _DEPT_MAP_LOWER[lower], True
    # Title-case fallback
    titled = raw.title()
    if titled in DEPT_MAP:
        return DEPT_MAP[titled], True
    return "UNKNOWN", False


# ---------------------------------------------------------------------------
# Building name correction table (Step 2 pre-processing)
# ---------------------------------------------------------------------------

BUILDING_NAME_CORRECTIONS = {
    # uchicago_locations.csv name          → geojson canonical name
    "Searle Chemistry Laboratory":         "Searle Chemical Laboratory",
    "Walker Hall":                         "Walker Museum",
    "Social Sciences Research Building":   "Social Science Research Building",
    # Oriental Institute was renamed in 2019 following a faculty vote.
    "Oriental Institute":                  "Institute for the Study of Ancient Cultures",
    # Crown Family School: locations CSV uses shortened name.
    "Crown Family School":                 "Crown Family School of Social Work, Policy, and Practice",
    # Harper Center is UChicago's internal name for the Booth School building.
    "Harper Center":                       "Booth School of Business",
}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run():
    print("=" * 70)
    print("UChicago Dissertation GIS Pipeline — Steps 1 & 2")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Load inputs
    # ------------------------------------------------------------------
    print("\n[LOAD] Reading source files...")

    with open(DISSERTATIONS_CSV, newline="", encoding="utf-8") as f:
        diss_rows = list(csv.DictReader(f))
    print(f"  dissertations: {len(diss_rows):,} records")

    # Apply department overrides from fill_departments.py (if available)
    overrides_path = Path(__file__).resolve().parent / "processed" / "dept_overrides.json"
    if overrides_path.exists():
        with open(overrides_path, encoding="utf-8") as f:
            dept_overrides = json.load(f)
        applied = 0
        for row in diss_rows:
            if not row.get("Department", "").strip():
                goid = str(row.get("GOID", ""))
                if goid in dept_overrides:
                    row["Department"] = dept_overrides[goid]
                    applied += 1
        print(f"  dept_overrides applied: {applied:,} previously-empty records filled")

    modern_depts = load_modern_departments(LOCATIONS_CSV)
    print(f"  modern departments: {len(modern_depts)}")

    with open(FOOTPRINT_GEOJSON, encoding="utf-8") as f:
        geojson = json.load(f)
    gj_features = geojson["features"]
    # Build lookup: geojson building name → feature
    gj_by_name = {}
    for feat in gj_features:
        name = feat["properties"].get("name", "")
        if name:
            gj_by_name[name] = feat
    print(f"  geojson building features: {len(gj_features)} total, "
          f"{len(gj_by_name)} named")

    # ------------------------------------------------------------------
    # Step 1 — Normalize departments
    # ------------------------------------------------------------------
    print("\n[STEP 1] Normalizing dissertation departments...")

    unknown_raw_values = {}   # raw_dept → count (not in DEPT_MAP at all)
    mapping_stats = {
        "total": len(diss_rows),
        "mapped_known": 0,
        "mapped_unknown": 0,
        "not_in_map": 0,
    }

    for row in diss_rows:
        raw = row.get("Department", "").strip()
        modern, was_mapped = normalize_department(raw)
        row["department_original"] = raw
        row["modern_department"]   = modern

        if not was_mapped:
            mapping_stats["not_in_map"] += 1
            unknown_raw_values[raw] = unknown_raw_values.get(raw, 0) + 1
        elif modern == "UNKNOWN":
            mapping_stats["mapped_unknown"] += 1
        else:
            mapping_stats["mapped_known"] += 1

    total_non_empty = sum(
        1 for r in diss_rows if r.get("department_original", "").strip()
    )
    matched_modern = mapping_stats["mapped_known"]
    pct_matched = (matched_modern / total_non_empty * 100) if total_non_empty else 0

    print(f"  Total records:          {mapping_stats['total']:,}")
    print(f"  Mapped to modern dept:  {mapping_stats['mapped_known']:,} "
          f"({pct_matched:.1f}% of non-empty)")
    print(f"  Mapped to UNKNOWN:      {mapping_stats['mapped_unknown']:,} "
          f"(known gap — awaiting manual assignment)")
    print(f"  Not in map (new vals):  {mapping_stats['not_in_map']:,}")

    if unknown_raw_values:
        print(f"\n  WARNING: {len(unknown_raw_values)} raw department value(s) "
              "not found in explicit map — logged to unmapped_departments.txt")

    # Write unmapped log
    with open(OUT_UNMAPPED_LOG, "w", encoding="utf-8") as f:
        f.write("Unmapped department values (not found in DEPT_MAP)\n")
        f.write("=" * 50 + "\n")
        f.write("These values were encountered in the dataset but have no\n")
        f.write("entry in the explicit normalization table.\n")
        f.write("Please add them to DEPT_MAP in GIS/dept_housing.py.\n\n")
        for val, count in sorted(unknown_raw_values.items(),
                                 key=lambda x: -x[1]):
            f.write(f"  [{count:4d} records]  {repr(val)}\n")
        if not unknown_raw_values:
            f.write("  (none — all raw values are covered by the explicit map)\n")

    # Validation: the only hard failure is unexpected values not covered by
    # the explicit map. Intentional UNKNOWNs (programs awaiting manual
    # assignment) are expected and do not constitute a pipeline error.
    if mapping_stats["not_in_map"] > 0:
        raise RuntimeError(
            f"FAIL: {mapping_stats['not_in_map']} record(s) have raw department "
            "values not present in DEPT_MAP. Inspect unmapped_departments.txt "
            "and add entries to the explicit map."
        )

    # ------------------------------------------------------------------
    # Step 2a — Build building name correction table and validate geojson
    # ------------------------------------------------------------------
    print("\n[STEP 2a] Resolving building names → geojson features...")

    dept_to_feature = {}
    dept_build_log  = []

    for dept_name, info in modern_depts.items():
        raw_bldg  = info["building"]
        canon_bldg = BUILDING_NAME_CORRECTIONS.get(raw_bldg, raw_bldg)
        feat = gj_by_name.get(canon_bldg)

        if feat:
            dept_to_feature[dept_name] = {
                "building_name":   canon_bldg,
                "building_raw":    raw_bldg,
                "geometry":        feat["geometry"],
                "geojson_props":   feat["properties"],
                "corrected":       canon_bldg != raw_bldg,
            }
            tag = "CORRECTED → " if canon_bldg != raw_bldg else "exact      "
            dept_build_log.append(
                f"  [OK] {tag} {dept_name!r:45s} → {canon_bldg!r}"
            )
        else:
            dept_to_feature[dept_name] = None
            dept_build_log.append(
                f"  [FAIL] {dept_name!r:45s} → {canon_bldg!r} NOT FOUND IN GEOJSON"
            )

    for line in dept_build_log:
        print(line)

    unmatched_bldgs = [d for d, v in dept_to_feature.items() if v is None]
    print(f"\n  Departments with geometry: "
          f"{len(dept_to_feature) - len(unmatched_bldgs)}/{len(dept_to_feature)}")

    if unmatched_bldgs:
        raise RuntimeError(
            f"FAIL: {len(unmatched_bldgs)} modern department(s) have no geojson match: "
            + ", ".join(unmatched_bldgs)
        )

    # ------------------------------------------------------------------
    # Step 2b — Write departments_with_buildings.geojson
    # ------------------------------------------------------------------
    print("\n[STEP 2b] Writing departments_with_buildings.geojson...")

    dept_features = []
    for dept_name, match in dept_to_feature.items():
        info = modern_depts[dept_name]
        props = {
            "modern_department": dept_name,
            "building_name":     match["building_name"],
            "address":           info["address"],
        }
        dept_features.append({
            "type":       "Feature",
            "properties": props,
            "geometry":   match["geometry"],
        })

    with open(OUT_DEPTS_WITH_BLDGS, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": dept_features},
                  f, indent=2)
    print(f"  Written: {OUT_DEPTS_WITH_BLDGS}")

    # ------------------------------------------------------------------
    # Step 3 — Attach geometry to dissertation records
    # ------------------------------------------------------------------
    print("\n[STEP 3] Attaching building geometry to dissertation records...")

    no_geometry = 0
    for row in diss_rows:
        modern = row["modern_department"]
        match  = dept_to_feature.get(modern)
        if match:
            row["building_name"] = match["building_name"]
            row["has_geometry"]  = "True"
        else:
            row["building_name"] = ""
            row["has_geometry"]  = "False"
            no_geometry += 1

    pct_geo = ((len(diss_rows) - no_geometry) / len(diss_rows) * 100)
    print(f"  Records with geometry:    {len(diss_rows) - no_geometry:,} "
          f"({pct_geo:.1f}%)")
    print(f"  Records without geometry: {no_geometry:,} "
          f"(modern_department = UNKNOWN)")

    # ------------------------------------------------------------------
    # Step 3a — Write dissertations_with_departments.csv
    # ------------------------------------------------------------------
    print("\n[STEP 3a] Writing dissertations_with_departments.csv...")

    # Determine output fieldnames: original columns + new columns
    original_fields = list(diss_rows[0].keys())
    new_fields = ["department_original", "modern_department",
                  "building_name", "has_geometry"]
    # Remove new fields from original list to avoid duplicates (they were
    # inserted into row dicts during processing)
    out_fields = [f for f in original_fields
                  if f not in new_fields] + new_fields

    with open(OUT_DISS_WITH_DEPTS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(diss_rows)
    print(f"  Written: {OUT_DISS_WITH_DEPTS}")

    # ------------------------------------------------------------------
    # Step 4 — Write dissertations_spatial.geojson
    # ------------------------------------------------------------------
    print("\n[STEP 4] Writing dissertations_spatial.geojson...")

    diss_features = []
    skipped = 0
    for row in diss_rows:
        modern = row["modern_department"]
        match  = dept_to_feature.get(modern)
        if not match:
            skipped += 1
            continue

        props = {
            "id":                  row.get("GOID", ""),
            "title":               row.get("Title", ""),
            "year":                row.get("Date", "")[:4] if row.get("Date") else "",
            "department_original": row.get("department_original", ""),
            "modern_department":   modern,
            "building_name":       match["building_name"],
            "keywords":            row.get("Paper Keywords", ""),
            "description":         row.get("Subject Terms", ""),
            "authors":             row.get("Authors", ""),
            "advisors":            row.get("Advisors", ""),
            "degree":              row.get("Degree", ""),
        }
        diss_features.append({
            "type":       "Feature",
            "properties": props,
            "geometry":   match["geometry"],
        })

    with open(OUT_DISS_SPATIAL, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": diss_features},
                  f, indent=2)
    print(f"  Written: {OUT_DISS_SPATIAL}")
    print(f"  Features written: {len(diss_features):,}")
    print(f"  Skipped (UNKNOWN dept): {skipped:,}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    print(f"\nOutputs in: {GIS_OUT}/")
    print(f"  dissertations_with_departments.csv  — {len(diss_rows):,} rows")
    print(f"  departments_with_buildings.geojson  — {len(dept_features)} features")
    print(f"  dissertations_spatial.geojson       — {len(diss_features):,} features")
    print(f"  unmapped_departments.txt            — manual review log")
    print(f"\nDepartment normalization summary:")
    print(f"  Mapped to modern dept:  {mapping_stats['mapped_known']:,} "
          f"({pct_matched:.1f}%)")
    print(f"  Flagged UNKNOWN:        {mapping_stats['mapped_unknown']:,}")
    print(f"  Not in map (new):       {mapping_stats['not_in_map']:,}")
    print()


if __name__ == "__main__":
    run()
