#!/usr/bin/env python3
"""
Ingest published CO2RR experimental data into ISAAC as AI-Ready Records.

Creates ISAAC records from landmark CO2RR literature (Hori 1994, Kuhl 2012,
Zhu 2013, Kim 2014, Li 2012, Kas 2015) for use as experimental benchmarks
in the AutoCatalysis agent loop.

Each record is:
- record_type: "evidence"
- record_domain: "performance"
- Descriptors contain Faradaic efficiency values for CO2RR products

Usage:
    # Dry run — generate records, validate, print summary
    python tools/ingest_co2rr_literature.py --dry-run

    # Save records to JSON files
    python tools/ingest_co2rr_literature.py --output-dir output/co2rr_literature

    # Push to ISAAC database
    python tools/ingest_co2rr_literature.py --save-api --api-token YOUR_TOKEN
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

try:
    from jsonschema import validate, ValidationError
except ImportError:
    print("Error: jsonschema not found. Install via: pip install jsonschema")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Published CO2RR experimental data
# ---------------------------------------------------------------------------
# Sources: landmark papers in CO2 electroreduction literature

LITERATURE_DATA = [
    # ---------------------------------------------------------------
    # Must-have: Hori 1994 — foundational single-crystal Cu data
    # ---------------------------------------------------------------
    {
        "surface": "Cu(100)",
        "formula": "Cu",
        "facet": "100",
        "sample_form": "single_crystal",
        "products": {
            "C2H4": 0.67,
            "CH4": 0.05,
            "CO": 0.02,
            "HCOO": 0.03,
            "H2": 0.20,
        },
        "potential_V_vs_RHE": -1.0,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1007/BF00898316",
            "title": "Electrochemical CO2 reduction on metal electrodes",
            "authors": "Hori, Y.; Murata, A.; Takahashi, R.",
            "year": 1994,
        },
        "priority": "must_have",
    },
    {
        "surface": "Cu(111)",
        "formula": "Cu",
        "facet": "111",
        "sample_form": "single_crystal",
        "products": {
            "CH4": 0.40,
            "C2H4": 0.10,
            "CO": 0.02,
            "HCOO": 0.05,
            "H2": 0.35,
        },
        "potential_V_vs_RHE": -1.0,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1007/BF00898316",
            "title": "Electrochemical CO2 reduction on metal electrodes",
            "authors": "Hori, Y.; Murata, A.; Takahashi, R.",
            "year": 1994,
        },
        "priority": "must_have",
    },
    {
        "surface": "Cu(110)",
        "formula": "Cu",
        "facet": "110",
        "sample_form": "single_crystal",
        "products": {
            "C2H4": 0.39,
            "CH4": 0.15,
            "CO": 0.03,
            "HCOO": 0.04,
            "H2": 0.30,
        },
        "potential_V_vs_RHE": -1.0,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1007/BF00898316",
            "title": "Electrochemical CO2 reduction on metal electrodes",
            "authors": "Hori, Y.; Murata, A.; Takahashi, R.",
            "year": 1994,
        },
        "priority": "must_have",
    },
    {
        "surface": "Au(poly)",
        "formula": "Au",
        "facet": "poly",
        "sample_form": "polycrystalline",
        "products": {
            "CO": 0.87,
            "H2": 0.10,
            "HCOO": 0.02,
        },
        "potential_V_vs_RHE": -0.5,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1007/BF00898316",
            "title": "Electrochemical CO2 reduction on metal electrodes",
            "authors": "Hori, Y.; Murata, A.; Takahashi, R.",
            "year": 1994,
        },
        "priority": "must_have",
    },
    # ---------------------------------------------------------------
    # Must-have: Kuhl 2012 — polycrystalline Cu product distribution
    # ---------------------------------------------------------------
    {
        "surface": "Cu(poly)",
        "formula": "Cu",
        "facet": "poly",
        "sample_form": "polycrystalline",
        "products": {
            "C2H4": 0.25,
            "CH4": 0.30,
            "CO": 0.02,
            "HCOO": 0.10,
            "EtOH": 0.09,
            "PrOH": 0.03,
            "H2": 0.18,
        },
        "potential_V_vs_RHE": -1.1,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1039/C2EE21234J",
            "title": "New insights into the electrochemical reduction of carbon dioxide "
                     "on metallic copper surfaces",
            "authors": "Kuhl, K. P.; Cave, E. R.; Abram, D. N.; Jaramillo, T. F.",
            "year": 2012,
        },
        "priority": "must_have",
    },
    # ---------------------------------------------------------------
    # Nice-to-have: Zhu 2013 — Au nanoparticles, size effect
    # ---------------------------------------------------------------
    {
        "surface": "Au NP 8nm",
        "formula": "Au",
        "facet": "poly",
        "sample_form": "nanoparticle",
        "products": {
            "CO": 0.90,
            "H2": 0.08,
        },
        "potential_V_vs_RHE": -0.67,
        "electrolyte": "0.5M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1021/ja3112313",
            "title": "Monodisperse Au nanoparticles for selective electrocatalytic "
                     "reduction of CO2 to CO",
            "authors": "Zhu, W.; Michalsky, R.; Metin, O.; Lv, H.; Guo, S.; "
                       "Wright, C. J.; Sun, X.; Peterson, A. A.; Sun, S.",
            "year": 2013,
        },
        "priority": "nice_to_have",
    },
    # ---------------------------------------------------------------
    # Nice-to-have: Kim 2014 — Cu-Au alloys
    # ---------------------------------------------------------------
    {
        "surface": "Cu3Au",
        "formula": "Cu3Au",
        "facet": "poly",
        "sample_form": "thin_film",
        "products": {
            "CO": 0.80,
            "H2": 0.15,
            "HCOO": 0.03,
        },
        "potential_V_vs_RHE": -0.7,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1038/ncomms5948",
            "title": "Synergistic geometric and electronic effects for electrochemical "
                     "reduction of carbon dioxide using gold-copper bimetallic nanoparticles",
            "authors": "Kim, D.; Resasco, J.; Yu, Y.; Asiri, A. M.; Yang, P.",
            "year": 2014,
        },
        "priority": "nice_to_have",
    },
    {
        "surface": "CuAu",
        "formula": "CuAu",
        "facet": "poly",
        "sample_form": "thin_film",
        "products": {
            "CO": 0.85,
            "H2": 0.10,
            "HCOO": 0.03,
        },
        "potential_V_vs_RHE": -0.7,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1038/ncomms5948",
            "title": "Synergistic geometric and electronic effects for electrochemical "
                     "reduction of carbon dioxide using gold-copper bimetallic nanoparticles",
            "authors": "Kim, D.; Resasco, J.; Yu, Y.; Asiri, A. M.; Yang, P.",
            "year": 2014,
        },
        "priority": "nice_to_have",
    },
    # ---------------------------------------------------------------
    # Nice-to-have: Li 2012 — oxide-derived Cu
    # ---------------------------------------------------------------
    {
        "surface": "OD-Cu",
        "formula": "Cu",
        "facet": "poly",
        "sample_form": "film",
        "products": {
            "C2H4": 0.60,
            "CO": 0.05,
            "HCOO": 0.05,
            "H2": 0.25,
        },
        "potential_V_vs_RHE": -0.9,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1021/ja2108799",
            "title": "CO2 reduction at low overpotential on Cu electrodes resulting "
                     "from the reduction of thick Cu2O films",
            "authors": "Li, C. W.; Kanan, M. W.",
            "year": 2012,
        },
        "priority": "nice_to_have",
        "notes": "Oxide-derived Cu with enhanced grain boundary density.",
    },
    # ---------------------------------------------------------------
    # Nice-to-have: Kas 2015 — Cu2O-derived Cu
    # ---------------------------------------------------------------
    {
        "surface": "Cu2O-derived",
        "formula": "Cu",
        "facet": "poly",
        "sample_form": "film",
        "products": {
            "C2H4": 0.55,
            "EtOH": 0.10,
            "CO": 0.03,
            "H2": 0.28,
        },
        "potential_V_vs_RHE": -0.85,
        "electrolyte": "0.1M KHCO3",
        "temperature_K": 298,
        "literature": {
            "doi": "10.1039/C4CP05620A",
            "title": "Electrochemical CO2 reduction on Cu2O-derived copper "
                     "nanoparticles: controlling the catalytic selectivity of "
                     "hydrocarbons",
            "authors": "Kas, R.; Kortlever, R.; Milbrat, A.; Koper, M. T. M.; "
                       "Mul, G.; Baltrusaitis, J.",
            "year": 2015,
        },
        "priority": "nice_to_have",
        "notes": "Cu2O-derived nanostructured Cu film.",
    },
]


# ---------------------------------------------------------------------------
# Record ID generation
# ---------------------------------------------------------------------------
def literature_id_to_ulid(surface: str, doi: str) -> str:
    """Generate a deterministic 26-char ULID-like ID from surface + DOI.

    Ensures re-ingestion yields the same record_id for deduplication.
    """
    key = f"co2rr_lit:{surface}:{doi}"
    digest = hashlib.sha256(key.encode()).digest()[:16]
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    num = int.from_bytes(digest, "big")
    chars = []
    for _ in range(26):
        chars.append(alphabet[num & 0x1F])
        num >>= 5
    return "".join(reversed(chars))


# ---------------------------------------------------------------------------
# Convert literature entry → ISAAC record
# ---------------------------------------------------------------------------
def convert_to_isaac_record(entry: dict, now_utc: str) -> dict:
    """Convert a literature data entry to an ISAAC AI-Ready Record.

    Creates a performance-domain evidence record with Faradaic efficiency
    descriptors for each CO2RR product.
    """
    record_id = literature_id_to_ulid(
        entry["surface"], entry["literature"]["doi"]
    )

    # Build Faradaic efficiency descriptors
    descriptors_list = []
    for product, fe_value in entry["products"].items():
        descriptors_list.append({
            "name": f"faradaic_efficiency.{product}",
            "kind": "absolute",
            "source": "experiment",
            "value": fe_value,
            "unit": "fraction",
            "uncertainty": {"sigma": 0.05, "unit": "fraction"},
            "definition": f"Faradaic efficiency for {product} production "
                          f"at {entry['potential_V_vs_RHE']} V vs RHE.",
        })

    # Build ISAAC record
    record = {
        "isaac_record_version": "1.0",
        "record_id": record_id,
        "record_type": "evidence",
        "record_domain": "performance",
        "timestamps": {"created_utc": now_utc},
        "acquisition_source": {"source_type": "literature"},
        "sample": {
            "material": {
                "name": f"{entry['surface']} electrode",
                "formula": entry["formula"],
                "provenance": "experimental",
                "notes": entry.get("notes", f"CO2RR data from {entry['literature']['authors'].split(';')[0].strip()} et al. ({entry['literature']['year']})."),
            },
            "sample_form": entry.get("sample_form", "bulk"),
        },
        "context": {
            "environment": "electrochemical",
            "temperature_K": entry.get("temperature_K", 298),
            "electrolyte": {
                "name": entry.get("electrolyte", "0.1M KHCO3"),
                "concentration_M": _parse_concentration(entry.get("electrolyte", "")),
            },
            "potential_control": {
                "setpoint_V": entry["potential_V_vs_RHE"],
                "scale": "RHE",
            },
        },
        "system": {
            "domain": "experimental",
            "instrument": {
                "instrument_type": "potentiostat",
                "instrument_name": "literature_reported",
            },
            "configuration": {
                "electrolyte": entry.get("electrolyte", "0.1M KHCO3"),
                "reference_electrode": "RHE",
                "potential_V_vs_RHE": entry["potential_V_vs_RHE"],
                "facet": entry.get("facet", "poly"),
                "surface_composition": entry["formula"],
            },
        },
        "descriptors": {
            "outputs": [{
                "label": "co2rr_selectivity",
                "generated_utc": now_utc,
                "generated_by": {
                    "agent": "literature_ingest",
                    "version": "1.0",
                },
                "descriptors": descriptors_list,
            }]
        },
        "literature": entry["literature"],
    }

    return record


def _parse_concentration(electrolyte_str: str) -> float:
    """Parse molarity from electrolyte string like '0.1M KHCO3'."""
    import re
    match = re.search(r"([\d.]+)\s*M", electrolyte_str)
    if match:
        return float(match.group(1))
    return 0.1  # default


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def load_schema():
    """Load the ISAAC JSON schema."""
    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "schema", "isaac_record_v1.json"
    )
    if not os.path.exists(schema_path):
        # Try alternate path
        schema_path = os.path.join(
            os.path.dirname(__file__), "schema", "isaac_record_v1.json"
        )
    with open(schema_path) as f:
        return json.load(f)


def validate_record(record: dict, schema: dict) -> list:
    """Validate a record against the ISAAC schema. Returns list of errors."""
    errors = []
    try:
        validate(instance=record, schema=schema)
    except ValidationError as e:
        errors.append(str(e.message))
    return errors


# ---------------------------------------------------------------------------
# Output modes
# ---------------------------------------------------------------------------
def save_to_files(records: list, output_dir: str):
    """Save records as individual JSON files."""
    os.makedirs(output_dir, exist_ok=True)
    for record in records:
        path = os.path.join(output_dir, f"{record['record_id']}.json")
        with open(path, "w") as f:
            json.dump(record, f, indent=2)
    print(f"Saved {len(records)} records to {output_dir}/")


def save_to_api(records: list, api_token: str):
    """Push records to the ISAAC database via the portal API."""
    import requests

    api_base = os.environ.get(
        "ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api"
    )
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    ok = 0
    fail = 0
    for record in records:
        try:
            resp = requests.post(
                f"{api_base}/records",
                headers=headers,
                json=record,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                ok += 1
                print(f"  {record['record_id']}: uploaded "
                      f"({record['sample']['material']['name']})")
            else:
                print(f"  {record['record_id']}: HTTP {resp.status_code} "
                      f"- {resp.text[:200]}")
                fail += 1
        except Exception as exc:
            print(f"  {record['record_id']}: {exc}")
            fail += 1

    print(f"\nAPI upload: {ok} succeeded, {fail} failed (out of {len(records)})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Ingest published CO2RR experimental data into ISAAC."
    )
    parser.add_argument(
        "--priority",
        choices=["must_have", "nice_to_have", "all"],
        default="all",
        help="Filter by data priority (default: all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate records without saving.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save ISAAC JSON records.",
    )
    parser.add_argument(
        "--save-api",
        action="store_true",
        help="Push records to the ISAAC database via the portal API.",
    )
    parser.add_argument(
        "--api-token",
        type=str,
        default=os.environ.get("ISAAC_API_TOKEN", ""),
        help="Bearer token for the ISAAC API.",
    )
    args = parser.parse_args()

    if args.save_api and not args.api_token:
        print("--save-api requires --api-token or ISAAC_API_TOKEN env var.")
        sys.exit(1)

    if not args.dry_run and not args.output_dir and not args.save_api:
        print("No output mode selected. Add --dry-run, --output-dir, or --save-api.")
        sys.exit(1)

    # Filter data by priority
    data = LITERATURE_DATA
    if args.priority != "all":
        data = [d for d in data if d.get("priority") == args.priority]

    schema = load_schema()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_records = []
    validation_errors = 0

    print(f"\nConverting {len(data)} literature entries to ISAAC records...\n")

    for entry in data:
        record = convert_to_isaac_record(entry, now_utc)
        errors = validate_record(record, schema)
        if errors:
            print(f"  Validation error for {entry['surface']}: {errors[0]}")
            validation_errors += 1
        else:
            all_records.append(record)
            print(f"  {entry['surface']}: validated "
                  f"({len(entry['products'])} products, "
                  f"{entry['potential_V_vs_RHE']} V vs RHE) "
                  f"[{entry['priority']}]")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Summary: {len(all_records)} valid records, {validation_errors} errors")
    print(f"{'=' * 60}")

    if not all_records:
        print("No valid records to save.")
        sys.exit(0)

    # Group by source
    by_source = {}
    for r in all_records:
        doi = r.get("literature", {}).get("doi", "unknown")
        by_source.setdefault(doi, []).append(r)
    for doi, recs in by_source.items():
        surfaces = [r["sample"]["material"]["name"] for r in recs]
        print(f"  {doi}: {', '.join(surfaces)}")

    # Output
    if args.output_dir:
        save_to_files(all_records, args.output_dir)

    if args.save_api:
        save_to_api(all_records, args.api_token)

    if args.dry_run:
        print(f"\nDry run complete. No records saved.")
        print(f"\nSample record ({all_records[0]['sample']['material']['name']}):")
        print(json.dumps(all_records[0], indent=2))


if __name__ == "__main__":
    main()
