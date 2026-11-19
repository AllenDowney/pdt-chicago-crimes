#!/usr/bin/env python3
"""Write the snapshot's data in the common format (STANDARD.md §8), for local use.

Reads the CSVs the SODA API returned (data/raw/<release>/) and writes
data/processed/chicago-crimes-<release>-<table>.parquet: the crimes rows for the
snapshot's data year, and the IUCR code list. Each file is read back and every
cell compared with the CSV, so the conversion is lossless.

The CSVs are read as text, because codes have leading zeros (IUCR 0110, beat
0322, district 003), then each column is converted by its record's storage_type.
An empty cell is null: the portal has no missing codes.

The terms are still `unknown`, so these files stay local (data/ is gitignored).
"""

import json
import sys
from pathlib import Path

import pandas as pd

try:
    from pdt.release import data_source, write_release
except ImportError:
    sys.exit("Needs the PublicDataToolkit library: pip install -e ../PublicDataToolkit, "
             "or run through `make data`.")

ROOT = Path(__file__).resolve().parent.parent
CONVERT = {"integer": int, "float": float}


def read(release, table, names):
    source = data_source(ROOT, release, table, format="csv")
    path = ROOT / source["path"]
    if not path.exists():
        sys.exit(f"{path} is missing; run scripts/download.py first")
    records = {}
    for line in (ROOT / "metadata" / "releases" / release / table / "variables.jsonl").open():
        r = json.loads(line)
        records[r["name"]] = r
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, usecols=names)[names].astype(object)
    for name in names:
        convert = CONVERT.get(records[name].get("storage_type"), str)
        frame[name] = [None if cell == "" else convert(cell) for cell in frame[name]]
    return frame, source["id"]


if __name__ == "__main__":
    write_release(ROOT, read)
