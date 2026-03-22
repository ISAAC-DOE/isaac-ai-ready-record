#!/usr/bin/env python3
"""
Convert facility_setup.json + run_log.csv → ISAAC AI-ready records.

Each row in run_log.csv becomes one ISAAC record of type:
  evidence / performance / laboratory

Output: one JSON file per row in ./generated_records/
"""

import csv
import json
import os
import sys
import hashlib
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETUP_FILE = os.path.join(SCRIPT_DIR, "facility_setup.json")
RUN_LOG = os.path.join(SCRIPT_DIR, "run_log.csv")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "generated_records")

# ---------------------------------------------------------------------------
# ULID generation (simplified — monotonic, no external dependency)
# ---------------------------------------------------------------------------
ULID_CHARS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32

def _encode_base32(value, length):
    result = []
    for _ in range(length):
        result.append(ULID_CHARS[value & 0x1F])
        value >>= 5
    return "".join(reversed(result))

def generate_ulid():
    """Generate a ULID: 10-char timestamp + 16-char random."""
    ts_ms = int(time.time() * 1000)
    rand_bits = int.from_bytes(os.urandom(10), "big")
    return _encode_base32(ts_ms, 10) + _encode_base32(rand_bits, 16)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(val, default=None):
    """Parse a float from CSV, returning default if empty."""
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def build_record(row, setup):
    """Build one ISAAC record from a CSV row + facility setup."""

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_id = generate_ulid()

    # --- Parse row ---
    cu_pct = safe_float(row.get("Cu_pct"), 0)
    cu_width = safe_float(row.get("Cu_width_um"), 0)
    au_width = safe_float(row.get("Au_width_um"), 0)
    potential = safe_float(row.get("potential_V_vs_RHE"))
    j_total = safe_float(row.get("current_density_mA_cm2"))
    electrode_label = row.get("electrode_label", "").strip()
    electrode_id = row.get("electrode_id", "").strip()
    run_id = row.get("run_id", "").strip()
    date_str = row.get("date", "").strip()
    experimenter = row.get("experimenter", "").strip() or setup.get("experimenter", {}).get("name", "")
    is_repeat = row.get("is_repeat", "").strip().lower() in ("yes", "true", "1")
    notes = row.get("notes", "").strip()

    # Parse all FE values
    products = {
        "H2":     safe_float(row.get("FE_H2_pct"), 0),
        "CO":     safe_float(row.get("FE_CO_pct"), 0),
        "CH4":    safe_float(row.get("FE_CH4_pct"), 0),
        "C2H4":   safe_float(row.get("FE_C2H4_pct"), 0),
        "HCOO":   safe_float(row.get("FE_HCOO_pct"), 0),
        "C2H5OH": safe_float(row.get("FE_C2H5OH_pct"), 0),
        "acetate": safe_float(row.get("FE_acetate_pct"), 0),
        "other":  safe_float(row.get("FE_other_pct"), 0),
    }

    # Material name
    if cu_pct == 0:
        mat_name = "Au film electrode"
        formula = "Au"
    elif cu_pct == 100:
        mat_name = "Cu film electrode"
        formula = "Cu"
    else:
        mat_name = f"Cu|Au striped electrode ({int(cu_pct)}% Cu)"
        formula = "Cu-Au"

    # Facility info
    fac = setup.get("facility", {})
    cell = setup.get("cell_setup", {})
    proto = setup.get("protocol", {})
    instr = setup.get("instruments", {})
    fab = setup.get("electrode_fabrication", {})

    # Timestamp
    if date_str:
        acquired_utc = f"{date_str}T00:00:00Z"
    else:
        acquired_utc = None

    # --- Build record ---
    record = {
        "isaac_record_version": "1.05",
        "record_id": record_id,
        "record_type": "evidence",
        "record_domain": "performance",
        "source_type": "laboratory",
        "timestamps": {
            "created_utc": now_utc,
        },
        "sample": {
            "material": {
                "name": mat_name,
                "formula": formula,
                "provenance": "synthesized",
            },
            "sample_form": "film",
            "electrode_type": "patterned_film",
            "composition": {},
            "geometry": {},
        },
        "context": {
            "environment": "in_situ",
            "temperature_K": (cell.get("temperature_C", 25) or 25) + 273.15,
            "electrochemistry": {
                "reaction": "CO2RR",
                "cell_type": cell.get("cell_type", "flow_cell"),
                "control_mode": "potentiostatic",
                "potential_setpoint_V": potential,
                "potential_scale": "RHE",
                "ir_compensation": {
                    "method": "automatic",
                    "percent": cell.get("iR_compensation_pct", 85),
                },
                "electrolyte": {
                    "name": cell.get("catholyte", "0.1 M KHCO3"),
                    "concentration_M": 0.1,
                },
            },
            "transport": {
                "flow_mode": "gas_diffusion",
                "feed": {
                    "phase": "gas",
                    "composition": "CO2",
                    "flow_rate": cell.get("CO2_flow_rate_sccm", 5.0),
                    "flow_rate_unit": "sccm",
                },
            },
        },
        "system": {
            "domain": "experimental",
            "technique": "chronoamperometry",
            "facility": {
                "facility_name": fac.get("facility_name", "Molecular Foundry"),
                "organization": fac.get("organization", "LBNL"),
            },
            "instrument": {
                "instrument_type": "potentiostat",
                "vendor_or_project": instr.get("potentiostat", {}).get("vendor", "BioLogic"),
            },
            "configuration": {
                "reference_electrode": cell.get("reference_electrode", "Ag/AgCl"),
                "membrane": cell.get("membrane", "Selemion AEM"),
                "counter_electrode": cell.get("counter_electrode", "CFP"),
                "anolyte": cell.get("anolyte", "0.1 M KHCO3"),
                "electrolyte_flow_rate_mL_min": cell.get("electrolyte_flow_rate_mL_min", 10.0),
                "CA_hold_time_min": proto.get("CA_hold_time_min", 80),
                "GC_injections_per_condition": proto.get("GC_injections_per_condition", 5),
            },
        },
        "measurement": {
            "processing": {
                "type": "gc_fe_analysis",
            },
            "series": [],
            "qc": {
                "status": "valid",
                "evidence": f"Average of {proto.get('GC_injections_per_condition', 5)} GC injections over {proto.get('CA_hold_time_min', 80)} min CA hold.",
            },
        },
        "descriptors": {
            "outputs": [
                {
                    "label": "co2rr_faradaic_efficiency",
                    "generated_utc": now_utc,
                    "generated_by": {
                        "agent": "isaac_co2rr_ingest",
                        "version": "1.0",
                    },
                    "descriptors": [],
                }
            ]
        },
    }

    # Add acquired timestamp if available
    if acquired_utc:
        record["timestamps"]["acquired_start_utc"] = acquired_utc

    # Electrode geometry
    if cu_width > 0 or au_width > 0:
        record["sample"]["geometry"] = {
            "Cu_stripe_width_um": cu_width,
            "Au_stripe_width_um": au_width,
            "period_um": cu_width + au_width if (cu_width > 0 and au_width > 0) else None,
            "Cu_area_fraction_pct": cu_pct,
        }

    if fab.get("Cu_film_thickness_nm"):
        record["sample"]["geometry"]["Cu_film_thickness_nm"] = fab["Cu_film_thickness_nm"]
    if fab.get("Au_film_thickness_nm"):
        record["sample"]["geometry"]["Au_film_thickness_nm"] = fab["Au_film_thickness_nm"]
    if fab.get("patterning_method"):
        record["sample"]["geometry"]["patterning_method"] = fab["patterning_method"]

    record["sample"]["composition"] = {"Cu_pct": cu_pct, "Au_pct": 100 - cu_pct}

    # Device identifiers → sample.material.identifiers
    if electrode_id:
        record["sample"]["material"].setdefault("identifiers", []).append(
            {"scheme": "internal", "value": electrode_id}
        )
    if run_id:
        record["sample"]["material"].setdefault("identifiers", []).append(
            {"scheme": "internal", "value": f"run:{run_id}"}
        )

    # Instrument model if known
    pot_model = instr.get("potentiostat", {}).get("model", "")
    if pot_model:
        record["system"]["instrument"]["instrument_name"] = pot_model
    gc_model = instr.get("gas_chromatograph", {}).get("model", "")
    if gc_model:
        record["system"]["configuration"]["gc_model"] = gc_model

    # Facility details
    if fac.get("laboratory"):
        record["system"]["facility"]["laboratory"] = fac["laboratory"]

    # Experimenter in notes
    if experimenter:
        record["system"]["configuration"]["experimenter"] = experimenter

    # --- Descriptors: one per product ---
    product_names = {
        "H2": "faradaic_efficiency.H2",
        "CO": "faradaic_efficiency.CO",
        "CH4": "faradaic_efficiency.CH4",
        "C2H4": "faradaic_efficiency.C2H4",
        "HCOO": "faradaic_efficiency.HCOO",
        "C2H5OH": "faradaic_efficiency.C2H5OH",
        "acetate": "faradaic_efficiency.CH3COO",
    }

    product_labels = {
        "H2": "Hydrogen (H2)",
        "CO": "Carbon Monoxide (CO)",
        "CH4": "Methane (CH4)",
        "C2H4": "Ethylene (C2H4)",
        "HCOO": "Formate (HCOO-)",
        "C2H5OH": "Ethanol (C2H5OH)",
        "acetate": "Acetate (CH3COO-)",
    }

    descriptors_list = record["descriptors"]["outputs"][0]["descriptors"]

    for key, fe_val in products.items():
        if key == "other" or fe_val is None:
            continue
        desc_name = product_names.get(key, f"faradaic_efficiency.{key}")
        # Uncertainty: sigma = max(0.5%, 5% * FE)
        sigma = max(0.5, 0.05 * abs(fe_val))
        descriptors_list.append({
            "name": desc_name,
            "kind": "absolute",
            "source": "auto",
            "value": round(fe_val / 100.0, 6),
            "unit": "fraction",
            "uncertainty": {
                "sigma": round(sigma / 100.0, 6),
                "unit": "fraction",
            },
            "definition": f"Faradaic efficiency for {product_labels.get(key, key)} production.",
        })

    # Current density descriptor (if available)
    if j_total is not None:
        descriptors_list.append({
            "name": "total_current_density",
            "kind": "absolute",
            "source": "auto",
            "value": j_total,
            "unit": "mA/cm2",
            "definition": "Average total current density during CA hold.",
        })

    # Repeat flag
    if is_repeat:
        if not notes:
            notes = "Repeat measurement"
        elif "repeat" not in notes.lower():
            notes = f"Repeat measurement. {notes}"

    if notes:
        record["sample"]["material"]["notes"] = notes

    return record


def main():
    # Load setup
    if not os.path.exists(SETUP_FILE):
        print(f"ERROR: {SETUP_FILE} not found")
        sys.exit(1)

    with open(SETUP_FILE) as f:
        setup = json.load(f)

    # Load run log
    if not os.path.exists(RUN_LOG):
        print(f"ERROR: {RUN_LOG} not found")
        sys.exit(1)

    with open(RUN_LOG, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} runs from {RUN_LOG}")

    # Generate records
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    records = []

    for i, row in enumerate(rows):
        record = build_record(row, setup)
        records.append(record)

        # Filename: potential_composition_index
        pot = row.get("potential_V_vs_RHE", "").strip()
        label = row.get("electrode_label", "").strip().replace(" ", "_")
        is_rep = "_repeat" if row.get("is_repeat", "").strip().lower() in ("yes", "true", "1") else ""
        fname = f"co2rr_{pot}V_{label}{is_rep}.json"

        outpath = os.path.join(OUTPUT_DIR, fname)
        with open(outpath, "w") as f:
            json.dump(record, f, indent=2)

    print(f"Generated {len(records)} ISAAC records in {OUTPUT_DIR}/")

    # Summary
    print(f"\nSummary:")
    print(f"  Record type: evidence / performance / laboratory")
    print(f"  Facility: {setup.get('facility', {}).get('facility_name', 'N/A')}")
    print(f"  Products: {', '.join(setup.get('products_detected', {}).get('gas_products_GC', []))}"
          f" + {', '.join(setup.get('products_detected', {}).get('liquid_products_NMR', []))}")

    # Validation check
    missing = []
    for row in rows:
        if not row.get("current_density_mA_cm2", "").strip():
            missing.append(f"  {row.get('potential_V_vs_RHE')}V {row.get('electrode_label', '').strip()}: current_density missing")
        if not row.get("date", "").strip():
            missing.append(f"  {row.get('potential_V_vs_RHE')}V {row.get('electrode_label', '').strip()}: date missing")
        if not row.get("run_id", "").strip():
            missing.append(f"  {row.get('potential_V_vs_RHE')}V {row.get('electrode_label', '').strip()}: run_id missing")

    if missing:
        print(f"\nWarnings ({len(missing)} missing fields — records still valid but incomplete):")
        for m in missing[:10]:
            print(m)
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    # Also save a combined file for bulk upload
    combined_path = os.path.join(OUTPUT_DIR, "_all_records.json")
    with open(combined_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nCombined file: {combined_path}")


if __name__ == "__main__":
    main()
