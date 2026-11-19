"""Build the Chicago crimes records from the portal's Socrata metadata, the IUCR code list and the counts by year.

Usage:
    python build/build_records.py

Reads build/slice.yaml and the sources in data/raw/<release>/ (sources.yaml).
Writes metadata/releases/<release>/<table>/variables.jsonl and build/report.md.

A release is a dated snapshot of the portal; see build/slice.yaml.

Where each field comes from (STANDARD.md §3):
- label, description: the Socrata view metadata (/api/views/<id>.json), each
  column's display name and description, verbatim
- source_location: the dataset's page, with the column's field name and Socrata id
- storage_type: rule socrata_storage_type, from the column's Socrata type, and,
  for numbers, whether every value in the data is whole
- values of crimes.iucr: the IUCR code list (dataset c7ck-438e), every code,
  active or retired; labels by rule iucr_code_list_label
- availability (crimes): the portal's own non-null count of each column, by
  year (a SODA aggregate query)
- measure, variable_type: build/slice.yaml (hand)

The portal has no missing codes: a missing value is an empty cell.
"""

import csv
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "metadata"
DATASET = "chicago-crimes"
PORTAL = "https://data.cityofchicago.org"

RULE_VERSION = "0.1"


# ---------------------------------------------------------------- inputs

def load_slice():
    return yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())


def sources():
    return {s["id"]: s for s in yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]}


def socrata_columns(path):
    """The view's public columns, in order. Columns named :@computed_region_* are boundaries the
    portal computes for its own maps; they aren't in the API's or the export's output."""
    view = json.loads(Path(path).read_text())
    return [c for c in view["columns"] if not c["fieldName"].startswith(":@")]


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


# ---------------------------------------------------------------- rules

def socrata_storage_type(column, cells):
    """Rule socrata_storage_type: text -> string, calendar_date -> datetime, number -> integer when
    every value is whole, else float. checkbox (true/false) and location (a point) have no
    storage type in the standard, so the field is left empty."""
    kind = column["dataTypeName"]
    if kind == "text":
        return "string"
    if kind == "calendar_date":
        return "datetime"
    if kind == "number":
        numbers = [float(c) for c in cells if c != ""]
        return "integer" if all(x.is_integer() for x in numbers) else "float"
    return None


def iucr_code_list_label(row):
    """Rule iucr_code_list_label: the code list's PRIMARY DESCRIPTION and SECONDARY DESCRIPTION,
    each verbatim, joined by ": " (HOMICIDE: FIRST DEGREE MURDER). The crimes table carries the
    two parts in primary_type and description."""
    return f"{row['primary_description']}: {row['secondary_description']}"


# ---------------------------------------------------------------- build

def rule(name, source, note=None):
    return {"origin": "rule", "rule": name, "rule_version": RULE_VERSION, "source": source,
            **({"note": note} if note else {})}


def build_table(table, spec, release, src, hand):
    t = spec["tables"][table]
    columns = socrata_columns(ROOT / src[t["metadata"]]["path"])
    header, rows = read_rows(ROOT / src[t["data"]]["path"])
    counts = None
    if t.get("counts"):
        counts = json.loads((ROOT / src[t["counts"]]["path"]).read_text())
    codes = None
    if table == "crimes":
        _, codes = read_rows(ROOT / src[spec["tables"]["iucr"]["data"]]["path"])

    records, report = [], []
    names = [c["fieldName"] for c in columns]
    if set(names) != set(header):
        report.append(f"- `{table}`: metadata columns not in the data: {sorted(set(names) - set(header))}; "
                      f"data columns not in the metadata: {sorted(set(header) - set(names))}")
    for c in columns:
        name = c["fieldName"]
        cells = [r.get(name, "") for r in rows]
        record = {"dataset": DATASET, "release": release, "table": table, "name": name, "label": c["name"]}
        if c.get("description"):
            record["description"] = c["description"]
        if (vt := t.get("variable_type", {}).get(name)):
            record["variable_type"] = vt
        fields = {}
        storage = socrata_storage_type(c, cells)
        if storage:
            record["storage_type"] = storage
            fields["storage_type"] = rule("socrata_storage_type", t["metadata"],
                                          f"Socrata type {c['dataTypeName']}"
                                          + ("; integer when every value in the data is whole." if c["dataTypeName"] == "number" else "."))
        else:
            report.append(f"- `{table}/{name}`: Socrata type `{c['dataTypeName']}` has no storage type in the standard; left empty")
        if (m := t["measure"].get(name)):
            record["measure"] = m
            fields["measure"] = hand
        if "variable_type" in record:
            fields["variable_type"] = hand

        if table == "crimes" and name == "iucr":
            record["values"] = [{"code": r["iucr"], "label": iucr_code_list_label(r), "kind": "valid"} for r in codes]
            retired = [r["iucr"] for r in codes if r["active"] != "true"]
            fields["values"] = rule("iucr_code_list_label", spec["tables"]["iucr"]["data"],
                                    f"Every code in the IUCR code list ({len(codes)}), active or retired; "
                                    f"retired (ACTIVE false): {', '.join(retired) or 'none'}. Label: PRIMARY "
                                    "DESCRIPTION and SECONDARY DESCRIPTION, joined by ': '.")
            used = {x for x in cells if x}
            if unlisted := sorted(used - {r["iucr"] for r in codes}):
                report.append(f"- `crimes/iucr`: codes in the {spec['data_year']} data not in the code list: {unlisted}")
        if table == "crimes" and name == "fbi_code":
            report.append("- `crimes/fbi_code`: no values. The column's description points to "
                          "https://gis.chicagopolice.org/pages/crime_details for the codes, and that page now "
                          "asks for a sign-in (checked in a headless browser, 2026-11-19). "
                          f"Codes in the {spec['data_year']} data: {', '.join(sorted({x for x in cells if x}))}")

        if counts is not None:
            key = "n_id" if name == "year" else f"n_{name}"
            if key in counts[0]:
                record["availability"] = [{"wave": int(row["year"]), "n": int(row[key])}
                                          for row in counts if int(row[key]) > 0]
                fields["availability"] = {"origin": "data_file", "source": t["counts"],
                                          "note": "The portal's non-null count of this column, by year, from a SODA "
                                                  "aggregate query on the snapshot date. For year, the count of rows."}
            else:
                report.append(f"- `{table}/{name}`: no availability; the portal's count query fails for this column type")

        record["source_location"] = {"source": t["metadata"], "url": f"{PORTAL}/d/{t['socrata_id']}",
                                     "upstream_id": f"{name} (column {c['id']})"}
        record["provenance"] = {"record": {"origin": "publisher", "source": t["metadata"],
                                           "note": "Label and description: the Socrata column's name and description."},
                                "fields": fields}
        records.append(record)
    return records, report


def main():
    spec = load_slice()
    release, src = spec["release"], sources()
    hand = {"origin": "hand", "author": spec["author"], "date": spec["date"]}
    report = []
    by_table = {}
    for table in spec["tables"]:
        records, notes = build_table(table, spec, release, src, hand)
        write_jsonl(META / "releases" / release / table / "variables.jsonl", records)
        by_table[table] = records
        report += notes
    write_report(release, by_table, report)
    print(", ".join(f"{t}: {len(r)}" for t, r in by_table.items()) + " records; see build/report.md")


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_report(release, by_table, body):
    lines = [f"# Build report: {DATASET}, release {release}", "",
             "Generated by `build/build_records.py`. Do not edit.", "",
             *[f"- `{t}`: {len(r)} records" for t, r in by_table.items()], "",
             "## To look at", "", *(body or ["None."])]
    (ROOT / "build" / "report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
