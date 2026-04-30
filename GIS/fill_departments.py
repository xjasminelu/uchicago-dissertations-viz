"""
GIS Pipeline — Step 0: Fill missing departments from supplementary sources
==========================================================================

Matches extended-annotated.csv records (where Department is empty) against
three supplementary sources, applied in priority order:

  1. convocation_data.xlsx  Sheet1  — 22,993 records, 1932–2009
     Source: University of Chicago annual convocation programs, digitized.
     Columns (variable width): [0]=Title, [3]=Year, last=Department, 2nd-last=Division.
     Row width varies by era (9 cols pre-~1990, 10 cols ~1990–2009). The last column
     always contains the department (or division if no specific dept was listed).
     Strategy: exact title+year match (case-insensitive, whitespace-collapsed);
               prefix-40-char fallback.

  2. hathi_1893-1931.csv           — 2,617 records, 1893–1931
     Source: HathiTrust Digital Library digitized catalog of UChicago dissertations.
     Columns: Year, Division, Department, Name, Post, Diss_Title.
     Strategy A (title): exact title+year; prefix-40 fallback.
     Strategy B (author): last-name+year match when title strategies fail.
       - ProQuest author format: "['LASTNAME, FIRSTNAME']" → extract LASTNAME.
       - HathiTrust format: "FIRSTNAME LASTNAME" → extract last word.
       - Only applied when a single unambiguous match exists (rejects ties).
       - Reason: pre-1932 titles often differ in punctuation, capitalisation,
         or subtitle handling between ProQuest and HathiTrust catalog entries.

Raw department strings are stored as-is so DEPT_MAP in dept_housing.py can
normalise them. JSON-formatted keyword lists that accidentally occupy the
department column are filtered before writing.

Output: GIS/processed/dept_overrides.json  (GOID → raw dept string)
        GIS/processed/fill_departments_log.txt  (raw dept strings for DEPT_MAP review)

Usage:
  python3 GIS/fill_departments.py

Pipeline order:
  python3 GIS/fill_departments.py
  python3 GIS/dept_housing.py
  python3 GIS/geo_ripper.py
  python3 GIS/prepare_viz_data.py
"""

import csv
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
GIS_OUT = Path(__file__).resolve().parent / "processed"
GIS_OUT.mkdir(exist_ok=True)

DISSERTATIONS_CSV = ROOT / "extended-annotated.csv"
CONV_XLSX         = ROOT / "convocation_data.xlsx"
HATHI_CSV         = ROOT / "hathi_1893-1931.csv"

OUT_OVERRIDES_JSON = GIS_OUT / "dept_overrides.json"
OUT_LOG            = GIS_OUT / "fill_departments_log.txt"


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------

def norm(title):
    """Uppercase, collapse whitespace."""
    return re.sub(r"\s+", " ", (title or "").strip().upper()).strip()


def make_keys(title, year):
    """Return (exact_key, short_key) for matching."""
    n = norm(title)
    if not n or not year:
        return None, None
    y = int(year)
    return (n, y), (n[:40], y)


# ---------------------------------------------------------------------------
# Load convocation_data.xlsx  Sheet1
# ---------------------------------------------------------------------------

def load_convocation():
    print("[CONV] Loading convocation_data.xlsx Sheet1...")

    with zipfile.ZipFile(CONV_XLSX) as z:
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

        with z.open("xl/sharedStrings.xml") as f:
            ss = ET.parse(f)
        shared = []
        for si in ss.findall(f".//{{{ns}}}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{ns}}}t")))

        def cval(c):
            t  = c.get("t")
            ve = c.find(f"{{{ns}}}v")
            if ve is None: return ""
            return shared[int(ve.text)] if t == "s" else (ve.text or "")

        with z.open("xl/worksheets/sheet1.xml") as f:
            ws = ET.parse(f)

    exact, short = {}, {}
    total = skipped = 0

    for row_el in ws.findall(f".//{{{ns}}}row"):
        cells = row_el.findall(f"{{{ns}}}c")
        def get(i): return cval(cells[i]) if i < len(cells) else ""

        n = len(cells)
        title    = get(0).strip()
        year_raw = get(3).strip()
        # Row width varies: 9-col rows have dept at col8, 10-col rows at col9.
        # Use last column as dept, second-to-last as division fallback.
        dept     = get(n - 1).strip()
        division = get(n - 2).strip()

        try:
            year = int(year_raw)
        except ValueError:
            skipped += 1
            continue

        raw = (dept or division).strip()   # prefer dept col, fall back to division
        if not raw:
            skipped += 1
            continue

        ek, sk = make_keys(title, year)
        if ek and ek not in exact:
            exact[ek] = raw
        if sk and sk not in short:
            short[sk] = raw
        total += 1

    print(f"  {total:,} rows loaded  |  exact keys: {len(exact):,}  |  skipped: {skipped}")
    return exact, short


# ---------------------------------------------------------------------------
# Load hathi_1893-1931.csv
# ---------------------------------------------------------------------------

def hathi_last_name(name_str):
    """
    Extract surname from HathiTrust 'FIRSTNAME [MIDDLE] LASTNAME' format.
    Returns uppercase last word.
    """
    parts = (name_str or "").strip().upper().split()
    return parts[-1] if parts else ""


def pq_last_name(authors_str):
    """
    Extract surname from ProQuest "['LASTNAME, FIRSTNAME MIDDLE']" format.
    Returns the string before the first comma, uppercased.
    """
    import ast as _ast
    s = (authors_str or "").strip()
    if not s:
        return ""
    try:
        items = _ast.literal_eval(s) if s.startswith("[") else [s]
        first = str(items[0]) if items else s
    except Exception:
        first = s.strip("[]'\"")
    name = first.strip().upper()
    return name.split(",")[0].strip() if "," in name else name.split()[-1] if name.split() else ""


def load_hathi():
    """
    Load hathi_1893-1931.csv.
    Returns:
      exact  — {(norm_title, year): dept}
      short  — {(title[:40], year): dept}
      by_author_year — {(last_name, year): [dept, ...]}  for author+year fallback
    """
    print("[HATHI] Loading hathi_1893-1931.csv...")
    exact, short = {}, {}
    by_author_year = defaultdict(list)
    total = skipped = 0

    with open(HATHI_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            year_raw = (row.get("Year") or "").strip()
            dept     = (row.get("Department") or "").strip()
            division = (row.get("Division") or "").strip()
            title    = (row.get("Diss_Title") or "").strip()
            name     = (row.get("Name") or "").strip()

            try:
                year = int(year_raw)
            except ValueError:
                skipped += 1
                continue

            raw = (dept or division).strip()
            if not raw:
                skipped += 1
                continue

            if title:
                ek, sk = make_keys(title, year)
                if ek and ek not in exact:
                    exact[ek] = raw
                if sk and sk not in short:
                    short[sk] = raw

            # Author+year lookup — keyed by (last_name_upper, year)
            last = hathi_last_name(name)
            if last:
                by_author_year[(last, year)].append(raw)

            total += 1

    print(f"  {total:,} rows loaded  |  title keys: {len(exact):,}  |"
          f"  author+year keys: {len(by_author_year):,}  |  skipped: {skipped}")
    return exact, short, by_author_year


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 70)
    print("UChicago Dissertation Pipeline — Step 0: Fill Departments")
    print("=" * 70)

    conv_exact, conv_short = load_convocation()
    hath_exact, hath_short, hath_author_yr = load_hathi()

    print(f"\n[BASE] Reading {DISSERTATIONS_CSV.name}...")
    with open(DISSERTATIONS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"  {len(rows):,} total records")

    already_filled = sum(1 for r in rows if (r.get("Department") or "").strip())
    print(f"  Already have department: {already_filled:,}")
    print(f"  Empty (to fill):         {len(rows) - already_filled:,}")

    overrides = {}
    stats = defaultdict(int)
    match_detail = defaultdict(int)

    for row in rows:
        if (row.get("Department") or "").strip():
            stats["proquest"] += 1
            continue

        title = (row.get("Title") or "").strip()
        goid  = str(row.get("GOID") or "").strip()
        try:
            yr = int(str(row.get("Date") or "")[:4])
        except ValueError:
            stats["no_year"] += 1
            continue

        ek, sk = make_keys(title, yr)
        dept = source = None

        # --- convocation (1932–2009) ---
        if 1932 <= yr <= 2009:
            if ek and ek in conv_exact:
                dept, source = conv_exact[ek], "conv_exact"
            elif sk and sk in conv_short:
                dept, source = conv_short[sk], "conv_short"

        # --- hathi (1893–1931): title match first, author+year fallback ---
        elif 1893 <= yr <= 1931:
            if ek and ek in hath_exact:
                dept, source = hath_exact[ek], "hath_exact"
            elif sk and sk in hath_short:
                dept, source = hath_short[sk], "hath_short"
            else:
                # Author+year fallback: extract last name from ProQuest Authors field
                # and look up in hathi. Only accept when exactly one candidate exists
                # (ambiguous matches — two people with same surname graduating same year —
                # are silently skipped to avoid misassignment).
                last = pq_last_name((row.get("Authors") or "").strip())
                if last:
                    candidates = hath_author_yr.get((last, yr), [])
                    if len(candidates) == 1:
                        dept, source = candidates[0], "hath_author_yr"

        if dept and goid and not dept.startswith("['") and not dept.startswith('["'):
            overrides[goid] = dept
            match_detail[source] += 1
            stats["filled"] += 1
        else:
            stats["unknown"] += 1

    # -----------------------------------------------------------------------
    # Write overrides JSON
    # -----------------------------------------------------------------------
    with open(OUT_OVERRIDES_JSON, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, sort_keys=True)

    total = len(rows)
    with_dept = stats["proquest"] + stats["filled"]

    print(f"\n{'=' * 70}")
    print("RESULTS")
    print(f"{'=' * 70}")
    print(f"  Already in ProQuest:   {stats['proquest']:,}")
    print(f"  Filled — convocation:  {match_detail['conv_exact'] + match_detail['conv_short']:,}"
          f"  (exact: {match_detail['conv_exact']:,}  prefix: {match_detail['conv_short']:,})")
    hathi_total = match_detail['hath_exact'] + match_detail['hath_short'] + match_detail['hath_author_yr']
    print(f"  Filled — hathi:        {hathi_total:,}"
          f"  (title-exact: {match_detail['hath_exact']:,}"
          f"  title-prefix: {match_detail['hath_short']:,}"
          f"  author+year: {match_detail['hath_author_yr']:,})")
    print(f"  Still unknown:         {stats['unknown'] + stats['no_year']:,}")
    print(f"\n  Total with dept:  {with_dept:,} / {total:,} = {with_dept/total*100:.1f}%")
    print(f"\n  dept_overrides.json:  {len(overrides):,} entries → {OUT_OVERRIDES_JSON}")

    # -----------------------------------------------------------------------
    # Write log with unmapped raw strings (for DEPT_MAP additions)
    # -----------------------------------------------------------------------
    raw_counts = defaultdict(int)
    for raw in overrides.values():
        raw_counts[raw.strip().upper()] += 1

    with open(OUT_LOG, "w", encoding="utf-8") as f:
        f.write("fill_departments.py log\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"ProQuest existing: {stats['proquest']:,}\n")
        f.write(f"Filled convocation: {match_detail['conv_exact'] + match_detail['conv_short']:,}\n")
        f.write(f"Filled hathi:       {match_detail['hath_exact'] + match_detail['hath_short']:,}\n")
        f.write(f"Unknown remaining:  {stats['unknown'] + stats['no_year']:,}\n\n")
        f.write("Raw dept strings injected (for DEPT_MAP review):\n")
        for raw, cnt in sorted(raw_counts.items(), key=lambda x: -x[1]):
            f.write(f"  {cnt:5d}  {raw}\n")
    print(f"  Log (raw dept strings): {OUT_LOG}")
    print(f"\nNext: python3 GIS/dept_housing.py")
    print("      python3 GIS/geo_ripper.py")
    print("      python3 GIS/prepare_viz_data.py\n")


if __name__ == "__main__":
    run()
