"""Turn the research workbook into seed data for the dashboard.

Run once (or again after you edit the Excel):
    python radar/import_excel.py path/to/Fiama_Competitor_Research.xlsx
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "seed.json"

SKIP = {"README", "Engagement Summary", "Range Summary"}   # notes and formula-only tabs


def header_row(ws):
    """First row (in the top 8) that looks like column headings."""
    for i, r in enumerate(ws.iter_rows(min_row=1, max_row=8, values_only=True), start=1):
        cells = [c for c in r if c not in (None, "")]
        if r and str(r[0] or "").strip() in ("Brand", "Marketing lever"):
            return i
        if len(cells) >= 4 and all(isinstance(c, str) and len(c) < 60 for c in cells):
            return i
    return 1


# Agency names are kept out of the app (team rule, 19 Nov 2026). Their case-study links are hidden too.
AGENCIES = r"anymind|digitas"


def scrub(v):
    if not isinstance(v, str) or not re.search(AGENCIES, v, re.I):
        return v
    if re.match(r"https?://", v.strip()):
        return ""
    v = re.sub(rf"\b(?:{AGENCIES})\b(?:\s*[–-]\s*|\s+)(?=case\b|work\b)?", "", v, flags=re.I)
    v = re.sub(r"\bcase\)", "case study)", v)
    return ("Case study, " + v) if re.match(r"[A-Z][a-z]{2} \d{4}$", v.strip()) else ("Case study: " + v if "case" not in v.lower() else v)


def clean(v):
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d %b %Y")
    if isinstance(v, float):
        return round(v, 4)
    return v


def main(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    seed = {"imported": date.today().isoformat(), "sheets": {}}
    for name in wb.sheetnames:
        if name in SKIP:
            continue
        ws = wb[name]
        rows = list(ws.iter_rows(min_row=header_row(ws), values_only=True))
        if not rows:
            continue
        headers = [str(h).strip() if h else f"col{i}" for i, h in enumerate(rows[0])]
        keep = [i for i, h in enumerate(headers) if "agenc" not in h.lower()]   # agency names are left out on purpose
        headers = [headers[i] for i in keep]
        records = []
        for r in rows[1:]:
            if not any(x not in (None, "") for x in r):
                continue
            rec = {h: scrub(clean(r[i])) for h, i in zip(headers, keep)}
            first = str(r[0] or "")
            if first.startswith("[Add") or "creator]" in str(r[1] or ""):
                continue
            records.append(rec)
        seed["sheets"][name] = {"columns": headers, "rows": records}
    cfg = json.loads((ROOT / "config" / "brands.json").read_text())
    seed["brands"] = [{k: b[k] for k in ("name", "role", "instagram", "youtube_handle", "website", "official_sites") if k in b}
                      for b in cfg["brands"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(seed, ensure_ascii=False, indent=1))
    print(f"Wrote {OUT} ({sum(len(s['rows']) for s in seed['sheets'].values())} rows)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "seed" / "Fiama_Competitor_Research.xlsx")
