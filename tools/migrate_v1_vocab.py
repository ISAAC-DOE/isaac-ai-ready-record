#!/usr/bin/env python3
"""
One-time migration: fix records in the ISAAC database that have
non-vocabulary values for links.basis, descriptors.source, or
assets.content_role.

Run on the server where PGHOST/PGUSER/PGPASSWORD are set:
    python tools/migrate_v1_vocab.py --dry-run
    python tools/migrate_v1_vocab.py --apply
"""

import argparse
import json
import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------------------------------------------------------------------
# Mapping of old → new values
# ---------------------------------------------------------------------------
BASIS_MAP = {
    "same_electrode": "same_sample_id",
    "sequential_potential_step": "matched_operating_conditions",
}

SOURCE_MAP = {
    "catalysis_hub": "imported",
}

CONTENT_ROLE_MAP = {
    "processing_recipe": "workflow_recipe",
}

TECHNIQUE_MAP = {
    "XAS": "XAS",  # no change needed, just for completeness
}


def get_conn():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        database=os.environ.get("PGDATABASE", "app"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        cursor_factory=RealDictCursor,
    )


def fix_record(data: dict) -> tuple:
    """Apply vocabulary fixes to a record. Returns (modified_data, list_of_changes)."""
    changes = []
    modified = json.loads(json.dumps(data))  # deep copy

    # Fix links.basis
    for i, link in enumerate(modified.get("links", [])):
        old = link.get("basis")
        if old in BASIS_MAP:
            link["basis"] = BASIS_MAP[old]
            changes.append(f"links[{i}].basis: {old} → {BASIS_MAP[old]}")

    # Fix descriptors.source
    for out in modified.get("descriptors", {}).get("outputs", []):
        for j, desc in enumerate(out.get("descriptors", [])):
            old = desc.get("source")
            if old in SOURCE_MAP:
                desc["source"] = SOURCE_MAP[old]
                changes.append(f"descriptor[{j}].source: {old} → {SOURCE_MAP[old]}")

    # Fix assets.content_role
    for k, asset in enumerate(modified.get("assets", [])):
        old = asset.get("content_role")
        if old in CONTENT_ROLE_MAP:
            asset["content_role"] = CONTENT_ROLE_MAP[old]
            changes.append(f"assets[{k}].content_role: {old} → {CONTENT_ROLE_MAP[old]}")

    return modified, changes


def main():
    parser = argparse.ArgumentParser(description="Migrate ISAAC DB records to v1.0 vocabulary.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")
    parser.add_argument("--apply", action="store_true", help="Apply changes to the database.")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Specify --dry-run or --apply")
        sys.exit(1)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT record_id, data FROM records")
    rows = cur.fetchall()
    print(f"Found {len(rows)} records in database.\n")

    total_fixed = 0
    for row in rows:
        rid = row["record_id"].strip()
        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
        fixed, changes = fix_record(data)

        if changes:
            total_fixed += 1
            print(f"  {rid}:")
            for c in changes:
                print(f"    - {c}")

            if args.apply:
                cur.execute(
                    "UPDATE records SET data = %s WHERE record_id = %s",
                    (json.dumps(fixed), rid),
                )

    if args.apply and total_fixed > 0:
        conn.commit()
        print(f"\n✅ Applied fixes to {total_fixed} records.")
    elif args.dry_run:
        print(f"\n🔍 Dry run: {total_fixed} records would be updated.")
    else:
        print(f"\nNo records needed fixing.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
