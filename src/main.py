"""
Golden Customer Record builder.

Reads CRM and transaction CSVs, cleans and standardises each source,
matches customers across the two, and merges each match group into a
single golden record. Output is written to golden_customers.csv.

Run:
    python main.py
"""

import csv
import re
from collections import defaultdict

CRM_FILE = "crm_customers.csv"
TXN_FILE = "transaction_customers.csv"
OUTPUT_FILE = "golden_customers.csv"


# ---------------------------------------------------------------------------
# Field-level cleaning
# ---------------------------------------------------------------------------

def clean_text(value):
    """Trim whitespace. Return empty string for missing values."""
    if value is None:
        return ""
    return value.strip()


def clean_email(value):
    """Lower-case and trim an email. Empty string if missing."""
    return clean_text(value).lower()


def clean_name(value):
    """Trim and title-case a name so 'jOHN' and ' john ' agree."""
    return clean_text(value).title()


def clean_phone(value):
    """
    Normalise a phone number to digits only (dropping a leading +).
    Different sources store the same number as '+4477...' or '4477...';
    stripping non-digits lets them compare equal. Empty string if missing.
    """
    value = clean_text(value)
    if not value:
        return ""
    return re.sub(r"\D", "", value)


def clean_date(value):
    """
    Standardise a date to ISO format YYYY-MM-DD.

    Handles the two formats present in the data:
      - already ISO:  2024-01-06
      - compact:      20240106
    Returns empty string if it cannot be parsed.
    """
    value = clean_text(value)
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    if re.fullmatch(r"\d{8}", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return ""


# ---------------------------------------------------------------------------
# Loading and standardising each source into a common shape
# ---------------------------------------------------------------------------

# Every record, from either source, is reduced to these fields.
COMMON_FIELDS = [
    "first_name",
    "last_name",
    "email",
    "phone",
    "address",
    "city",
    "country",
    "source",
    "source_id",
    "event_date",  # registration_date for CRM, purchase_date for TXN
]


def load_crm(path):
    """Read the CRM file and return a list of standardised records."""
    records = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            records.append({
                "first_name": clean_name(row.get("first_name")),
                "last_name": clean_name(row.get("last_name")),
                "email": clean_email(row.get("email")),
                "phone": clean_phone(row.get("phone")),
                "address": clean_text(row.get("address")),
                "city": clean_text(row.get("city")),
                "country": clean_text(row.get("country")),
                "source": "CRM",
                "source_id": clean_text(row.get("customer_id")),
                "event_date": clean_date(row.get("registration_date")),
            })
    return records


def load_txn(path):
    """Read the transaction file and return a list of standardised records."""
    records = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            records.append({
                "first_name": clean_name(row.get("first_name")),
                "last_name": clean_name(row.get("last_name")),
                "email": clean_email(row.get("customer_email")),
                "phone": clean_phone(row.get("phone")),
                "address": clean_text(row.get("shipping_address")),
                "city": clean_text(row.get("city")),
                "country": clean_text(row.get("country")),
                "source": "TXN",
                "source_id": clean_text(row.get("transaction_id")),
                "event_date": clean_date(row.get("purchase_date")),
            })
    return records


# ---------------------------------------------------------------------------
# Matching: decide which records describe the same customer
# ---------------------------------------------------------------------------

def match_key(record):
    """
    Return a stable identity key for a record.

    Preference order:
      1. email (most reliable identifier when present)
      2. phone
      3. first+last name as a last resort

    Records that share a key are treated as the same customer.
    """
    if record["email"]:
        return ("email", record["email"])
    if record["phone"]:
        return ("phone", record["phone"])
    if record["first_name"] or record["last_name"]:
        return ("name", record["first_name"], record["last_name"])
    return ("row", id(record))  # unmatchable: keep it on its own


def group_records(records):
    """Group all records by their match key. Returns a list of groups."""
    groups = defaultdict(list)
    for record in records:
        groups[match_key(record)].append(record)
    return list(groups.values())


# ---------------------------------------------------------------------------
# Merging: collapse a group into one golden record
# ---------------------------------------------------------------------------

def pick_best(group, field):
    """
    Choose the best value for a field across a match group.

    Rule: prefer the most recent non-empty value by event_date, so newer
    data wins. Falls back to any non-empty value if no dates are present.
    """
    dated = [r for r in group if r[field] and r["event_date"]]
    if dated:
        return max(dated, key=lambda r: r["event_date"])[field]
    for r in group:
        if r[field]:
            return r[field]
    return ""


def merge_group(group):
    """Merge one group of matched records into a single golden record."""
    return {
        "first_name": pick_best(group, "first_name"),
        "last_name": pick_best(group, "last_name"),
        "email": pick_best(group, "email"),
        "phone": pick_best(group, "phone"),
        "address": pick_best(group, "address"),
        "city": pick_best(group, "city"),
        "country": pick_best(group, "country"),
        "last_event_date": max((r["event_date"] for r in group if r["event_date"]),
                               default=""),
        "sources": ";".join(sorted({r["source"] for r in group})),
        "source_ids": ";".join(sorted(r["source_id"] for r in group if r["source_id"])),
        "record_count": len(group),
    }


GOLDEN_FIELDS = [
    "golden_id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "address",
    "city",
    "country",
    "last_event_date",
    "sources",
    "source_ids",
    "record_count",
]


def build_golden_records(crm_records, txn_records):
    """Full pipeline: combine sources, group, merge, assign golden IDs."""
    all_records = crm_records + txn_records
    groups = group_records(all_records)

    golden = []
    for i, group in enumerate(sorted(groups, key=lambda g: pick_best(g, "email")), start=1):
        record = merge_group(group)
        record["golden_id"] = f"GOLD{i:05d}"
        golden.append(record)
    return golden


def write_output(records, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=GOLDEN_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def main():
    crm_records = load_crm(CRM_FILE)
    txn_records = load_txn(TXN_FILE)
    golden = build_golden_records(crm_records, txn_records)
    write_output(golden, OUTPUT_FILE)

    print(f"CRM records read:        {len(crm_records)}")
    print(f"Transaction records read:{len(txn_records)}")
    print(f"Golden records written:  {len(golden)}  -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
