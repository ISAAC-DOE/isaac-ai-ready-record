#!/usr/bin/env python3
"""
Ingest CO2RR reactions from Catalysis Hub into ISAAC AI-Ready Records.

Queries the Catalysis Hub GraphQL API for CO2 reduction reactions,
converts each to an ISAAC-schema record, validates, and optionally
saves to the ISAAC database via the portal API.

Usage:
    # Dry run — fetch, convert, validate, print summary
    python tools/ingest_catalysis_hub.py --dry-run

    # Save JSON files to a directory
    python tools/ingest_catalysis_hub.py --output-dir output/cathub_co2rr

    # Push to ISAAC database via API
    python tools/ingest_catalysis_hub.py --save-api --api-token YOUR_TOKEN

    # Filter by surface composition
    python tools/ingest_catalysis_hub.py --surfaces Cu Au CuAu --dry-run
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

try:
    from jsonschema import validate, ValidationError
except ImportError:
    print("Error: jsonschema not found. Install via: pip install jsonschema")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CATHUB_GRAPHQL_URL = "https://api.catalysis-hub.org/graphql"
ISAAC_API_BASE = "https://isaac.slac.stanford.edu/portal/api"

GRAPHQL_REACTION_FIELDS = """
    id
    Equation
    chemicalComposition
    surfaceComposition
    facet
    sites
    reactants
    products
    reactionEnergy
    activationEnergy
    dftCode
    dftFunctional
    pubId
"""

GRAPHQL_PUBLICATION_FIELDS = """
    title
    authors
    year
    doi
    pubId
"""

# How many reactions per GraphQL page
PAGE_SIZE = 25

# Retry settings for flaky Heroku API
MAX_RETRIES = 5
RETRY_BACKOFF = 3  # seconds, doubles each retry


# ---------------------------------------------------------------------------
# Deterministic record ID from CatHub reaction ID
# ---------------------------------------------------------------------------
def cathub_id_to_ulid(cathub_id: str) -> str:
    """Generate a deterministic 26-char ULID-like ID from a CatHub reaction ID.

    Uses SHA-256 of 'cathub:<id>' and encodes the first 16 bytes as
    Crockford Base32 (26 chars). Deterministic so re-ingestion yields
    the same record_id, enabling dedup via ON CONFLICT DO UPDATE.
    """
    digest = hashlib.sha256(f"cathub:{cathub_id}".encode()).digest()[:16]
    # Crockford Base32 alphabet (excludes I, L, O, U)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    # Convert 16 bytes (128 bits) → 26 base-32 digits
    num = int.from_bytes(digest, "big")
    chars = []
    for _ in range(26):
        chars.append(alphabet[num & 0x1F])
        num >>= 5
    return "".join(reversed(chars))


# ---------------------------------------------------------------------------
# GraphQL helpers with retry
# ---------------------------------------------------------------------------
def _graphql_query(query: str, retries: int = MAX_RETRIES) -> dict:
    """Execute a GraphQL query against CatHub with exponential backoff."""
    backoff = RETRY_BACKOFF
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                CATHUB_GRAPHQL_URL,
                json={"query": query},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                wait = backoff * (2 ** (attempt - 1))
                print(f"  ⏳ Attempt {attempt}/{retries} failed ({exc}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"CatHub API failed after {retries} attempts: {last_error}"
                ) from last_error


def fetch_reactions(surface: str, after: str = None) -> dict:
    """Fetch one page of CO2RR reactions for a surface composition."""
    after_arg = f', after: "{after}"' if after else ""
    query = f"""{{
        reactions(
            first: {PAGE_SIZE},
            reactants: "CO2gas",
            surfaceComposition: "{surface}"
            {after_arg}
        ) {{
            totalCount
            pageInfo {{ hasNextPage endCursor }}
            edges {{
                node {{ {GRAPHQL_REACTION_FIELDS} }}
            }}
        }}
    }}"""
    return _graphql_query(query)


def fetch_publication(pub_id: str) -> dict:
    """Fetch publication metadata for a pubId."""
    query = f"""{{
        publications(pubId: "{pub_id}", first: 1) {{
            edges {{
                node {{ {GRAPHQL_PUBLICATION_FIELDS} }}
            }}
        }}
    }}"""
    data = _graphql_query(query)
    edges = data["data"]["publications"]["edges"]
    if edges:
        return edges[0]["node"]
    return {}


def fetch_all_reactions(surface: str) -> list:
    """Fetch all CO2RR reactions for a surface, handling pagination."""
    all_reactions = []
    after = None
    total = None

    while True:
        data = fetch_reactions(surface, after=after)
        rxn_data = data["data"]["reactions"]
        if total is None:
            total = rxn_data["totalCount"]
            print(f"  📊 {surface}: {total} CO2RR reactions found")
            if total == 0:
                return []

        for edge in rxn_data["edges"]:
            all_reactions.append(edge["node"])

        page_info = rxn_data["pageInfo"]
        if page_info["hasNextPage"]:
            after = page_info["endCursor"]
            print(f"    ... fetched {len(all_reactions)}/{total}")
        else:
            break

    print(f"  ✅ Fetched {len(all_reactions)} reactions for {surface}")
    return all_reactions


# ---------------------------------------------------------------------------
# Convert CatHub reaction → ISAAC record
# ---------------------------------------------------------------------------
def parse_sites(sites_str: str) -> str:
    """Extract adsorption site from CatHub sites JSON string."""
    try:
        sites = json.loads(sites_str) if sites_str else {}
        # e.g. {"COOHstar": "ontop"} → "ontop"
        if sites:
            return list(sites.values())[0]
    except (json.JSONDecodeError, IndexError):
        pass
    return "unknown"


def parse_functional_name(dft_functional: str) -> str:
    """Extract functional name from CatHub dftFunctional string.

    E.g. 'RPBE_-0.413VSHE' → 'RPBE', 'BEEF-vdW' → 'BEEF-vdW'
    """
    if not dft_functional:
        return "unknown"
    # Strip potential correction suffix (e.g. '_-0.413VSHE')
    parts = dft_functional.split("_")
    return parts[0]


def functional_class(name: str) -> str:
    """Guess DFT functional class from name."""
    name_upper = name.upper()
    if name_upper in ("RPBE", "PBE", "PW91", "BLYP"):
        return "GGA"
    if "VDW" in name_upper or "OPTPBE" in name_upper:
        return "vdW-DF"
    if "BEEF" in name_upper:
        return "BEEF"
    if "HSE" in name_upper or "B3LYP" in name_upper:
        return "hybrid"
    return "GGA"  # default assumption for CatHub


def convert_reaction(reaction: dict, publication: dict, now_utc: str) -> dict:
    """Convert a single CatHub reaction to an ISAAC AI-Ready Record."""
    cathub_id = reaction["id"]
    record_id = cathub_id_to_ulid(cathub_id)

    surface = reaction.get("surfaceComposition", "unknown")
    facet = reaction.get("facet", "unknown")
    formula = reaction.get("chemicalComposition", surface)
    equation = reaction.get("Equation", "")
    site = parse_sites(reaction.get("sites", "{}"))
    func_name = parse_functional_name(reaction.get("dftFunctional", ""))

    # Build material name
    material_name = f"{surface}({facet}) slab"
    # Extract adsorbate from equation for notes
    products_str = equation.split("->")[-1].strip() if "->" in equation else ""
    notes = f"DFT-optimized {surface} slab from Catalysis Hub."
    if products_str:
        notes += f" {products_str} adsorbed at {site} site."

    # Descriptors: reaction energy, and optionally activation energy
    descriptors_list = []
    if reaction.get("reactionEnergy") is not None:
        descriptors_list.append({
            "name": "reaction_energy",
            "kind": "absolute",
            "source": "catalysis_hub",
            "value": reaction["reactionEnergy"],
            "unit": "eV",
            "definition": f"DFT reaction energy for: {equation}",
            "uncertainty": {"sigma": 0, "unit": "eV"},
        })
    if reaction.get("activationEnergy") is not None:
        descriptors_list.append({
            "name": "activation_energy",
            "kind": "absolute",
            "source": "catalysis_hub",
            "value": reaction["activationEnergy"],
            "unit": "eV",
            "definition": f"DFT activation energy for: {equation}",
            "uncertainty": {"sigma": 0, "unit": "eV"},
        })

    record = {
        "isaac_record_version": "1.0",
        "record_id": record_id,
        "record_type": "evidence",
        "record_domain": "simulation",
        "source_type": "database",
        "timestamps": {"created_utc": now_utc},
        "sample": {
            "material": {
                "name": material_name,
                "formula": formula,
                "provenance": "theoretical",
                "notes": notes,
            },
            "sample_form": "slab_model",
        },
        "context": {
            "environment": "in_silico",
            "temperature_K": 0,
            "simulation_assumptions": {"solvation_model": "none"},
        },
        "system": {
            "domain": "computational",
            "technique": "DFT",
            "instrument": {
                "instrument_type": "simulation_engine",
                "instrument_name": reaction.get("dftCode", "unknown"),
                "vendor_or_project": reaction.get("dftCode", "unknown"),
            },
            "configuration": {
                "dft_code": reaction.get("dftCode", "unknown"),
                "xc_functional": reaction.get("dftFunctional", "unknown"),
                "facet": facet,
                "surface_composition": surface,
                "adsorption_site": site,
            },
        },
        "computation": {
            "method": {
                "family": "DFT",
                "functional_class": functional_class(func_name),
                "functional_name": func_name,
            }
        },
        "descriptors": {
            "outputs": [
                {
                    "label": "catalysis_hub_co2rr_energetics",
                    "generated_utc": now_utc,
                    "generated_by": {
                        "agent": "isaac_cathub_ingest",
                        "version": "1.0",
                    },
                    "descriptors": descriptors_list,
                }
            ]
        },
    }

    # Add literature if publication data exists
    if publication:
        authors = publication.get("authors", [])
        if isinstance(authors, list):
            authors = "; ".join(authors) if authors else ""
        record["literature"] = {
            "doi": publication.get("doi", ""),
            "title": publication.get("title", ""),
            "authors": authors,
            "year": publication.get("year", 0),
            "catalysis_hub_pub_id": publication.get("pubId", ""),
        }

    return record


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def load_schema():
    """Load the ISAAC JSON schema."""
    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "schema", "isaac_record_v1.json"
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
    print(f"💾 Saved {len(records)} records to {output_dir}/")


def save_to_api(records: list, api_token: str):
    """Push records to the ISAAC database via the portal API."""
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    ok = 0
    fail = 0
    for record in records:
        try:
            resp = requests.post(
                f"{ISAAC_API_BASE}/records",
                headers=headers,
                json=record,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                ok += 1
            else:
                print(f"  ❌ {record['record_id']}: HTTP {resp.status_code} — {resp.text[:200]}")
                fail += 1
        except Exception as exc:
            print(f"  ❌ {record['record_id']}: {exc}")
            fail += 1

    print(f"\n📤 API upload: {ok} succeeded, {fail} failed (out of {len(records)})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Ingest CO2RR reactions from Catalysis Hub into ISAAC."
    )
    parser.add_argument(
        "--surfaces",
        nargs="+",
        default=["Cu", "Au"],
        help="Surface compositions to query (default: Cu Au)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch, convert, validate — but do not save anything.",
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
        help="Bearer token for the ISAAC API (or set ISAAC_API_TOKEN env var).",
    )
    args = parser.parse_args()

    if args.save_api and not args.api_token:
        print("❌ --save-api requires --api-token or ISAAC_API_TOKEN env var.")
        sys.exit(1)

    if not args.dry_run and not args.output_dir and not args.save_api:
        print("No output mode selected. Add --dry-run, --output-dir, or --save-api.")
        sys.exit(1)

    schema = load_schema()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Cache publications by pubId to avoid repeated fetches
    pub_cache: dict = {}
    all_records = []
    validation_errors = 0

    for surface in args.surfaces:
        print(f"\n🔍 Querying CatHub for CO2RR on {surface}...")
        try:
            reactions = fetch_all_reactions(surface)
        except RuntimeError as exc:
            print(f"  ❌ {exc}")
            continue

        for rxn in reactions:
            pub_id = rxn.get("pubId", "")
            if pub_id and pub_id not in pub_cache:
                try:
                    pub_cache[pub_id] = fetch_publication(pub_id)
                except Exception:
                    pub_cache[pub_id] = {}
            publication = pub_cache.get(pub_id, {})

            record = convert_reaction(rxn, publication, now_utc)
            errors = validate_record(record, schema)
            if errors:
                print(f"  ⚠️  Validation error for {record['record_id']}: {errors[0]}")
                validation_errors += 1
            else:
                all_records.append(record)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📋 Summary: {len(all_records)} valid records, {validation_errors} errors")
    for surface in args.surfaces:
        count = sum(
            1 for r in all_records
            if r["system"]["configuration"]["surface_composition"] == surface
        )
        print(f"   {surface}: {count} records")
    print(f"{'=' * 60}")

    if not all_records:
        print("No valid records to save.")
        sys.exit(0)

    # Output
    if args.output_dir:
        save_to_files(all_records, args.output_dir)

    if args.save_api:
        save_to_api(all_records, args.api_token)

    if args.dry_run:
        print("\n🏁 Dry run complete. No records saved.")
        # Print first record as sample
        print("\nSample record:")
        print(json.dumps(all_records[0], indent=2))


if __name__ == "__main__":
    main()
