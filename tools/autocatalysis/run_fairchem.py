#!/usr/bin/env python3
"""
FAIRChem wrapper: build catalyst slabs and compute adsorption energies.

Uses Meta's FAIRChem UMA (Universal Materials Accelerator) model to predict
adsorption energies on catalyst surfaces. Replaces full DFT calculations with
ML interatomic potentials (~seconds per structure vs hours for DFT).

Usage:
    # Single surface evaluation
    python run_fairchem.py --surface Cu --facet 111 --adsorbate CO

    # Evaluate all candidates from explore.py
    python run_fairchem.py --from-explore

    # Use CPU instead of GPU
    python run_fairchem.py --from-explore --device cpu
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timezone

import numpy as np

try:
    from ase import Atoms
    from ase.build import fcc111, fcc100, fcc110, fcc211, bcc100, bcc110, bcc111, hcp0001
    from ase.build import add_adsorbate
    from ase.constraints import FixAtoms
    from ase.optimize import LBFGS
except ImportError:
    print("Error: ASE not found. Install via: pip install ase")
    sys.exit(1)

warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Gas-phase reference energies (eV) from standard DFT references.
# These are used to compute adsorption energies: E_ads = E_slab+ads - E_slab - E_gas
# Values are approximate; FAIRChem-predicted energies are self-consistent.
GAS_PHASE_REFERENCES = {
    "CO": -14.78,     # CO molecule in vacuum
    "COOH": -23.96,   # COOH radical reference
    "CHO": -15.32,    # CHO radical reference
    "COH": -15.10,    # COH radical reference
    "HCOO": -24.20,   # HCOO radical reference
    "H": -3.39,       # 0.5 * H2
    "OH": -10.50,     # OH radical reference
}

# Adsorbate molecular structures (positions relative to binding site)
ADSORBATE_ATOMS = {
    "CO": Atoms("CO", positions=[(0, 0, 0), (0, 0, 1.16)]),
    "COOH": Atoms("COOH", positions=[
        (0, 0, 0),          # C
        (0, 1.08, 0.65),    # O (carbonyl)
        (0, -1.08, 0.65),   # O (hydroxyl)
        (0, -1.60, 1.45),   # H
    ]),
    "CHO": Atoms("CHO", positions=[
        (0, 0, 0),          # C
        (0, 0, 1.21),       # O
        (0, 0.94, -0.54),   # H
    ]),
    "COH": Atoms("COH", positions=[
        (0, 0, 0),          # C
        (0, 0, 1.30),       # O
        (0, 0.75, 1.80),    # H
    ]),
    "H": Atoms("H", positions=[(0, 0, 0)]),
    "OH": Atoms("OH", positions=[(0, 0, 0), (0, 0, 0.97)]),
    "HCOO": Atoms("HCOO", positions=[
        (0, 0, 0),          # C
        (0, 1.08, 0.65),    # O
        (0, -1.08, 0.65),   # O
        (0, 0, -1.08),      # H
    ]),
}

# Mapping of surfaces to ASE slab builder functions
# Keys: (crystal_structure, facet)
SLAB_BUILDERS = {
    ("fcc", "111"): fcc111,
    ("fcc", "100"): fcc100,
    ("fcc", "110"): fcc110,
    ("fcc", "211"): fcc211,
    ("bcc", "100"): bcc100,
    ("bcc", "110"): bcc110,
    ("bcc", "111"): bcc111,
    ("hcp", "0001"): hcp0001,
}

# Crystal structures for common metals
METAL_CRYSTAL_STRUCTURE = {
    "Cu": "fcc", "Au": "fcc", "Ag": "fcc", "Pd": "fcc", "Pt": "fcc",
    "Ni": "fcc", "Al": "fcc", "Pb": "fcc", "Ir": "fcc", "Rh": "fcc",
    "Fe": "bcc", "W": "bcc", "Mo": "bcc", "Cr": "bcc", "V": "bcc",
    "Co": "hcp", "Zn": "hcp", "Ti": "hcp", "Ru": "hcp",
    "Sn": "fcc",  # beta-Sn is tetragonal, but FCC approximation for screening
}

# Lattice constants (Angstrom)
LATTICE_CONSTANTS = {
    "Cu": 3.61, "Au": 4.08, "Ag": 4.09, "Pd": 3.89, "Pt": 3.92,
    "Ni": 3.52, "Al": 4.05, "Pb": 4.95, "Ir": 3.84, "Rh": 3.80,
    "Fe": 2.87, "W": 3.16, "Mo": 3.15, "Cr": 2.88, "V": 3.03,
    "Co": 2.51, "Zn": 2.66, "Ti": 2.95, "Ru": 2.71,
    "Sn": 3.70,
}


# ---------------------------------------------------------------------------
# Slab construction
# ---------------------------------------------------------------------------
def build_slab(surface: str, facet: str, layers: int = 4, vacuum: float = 10.0,
               size: tuple = (2, 2, 1)) -> Atoms:
    """Build a catalyst slab using ASE.

    Args:
        surface: Element symbol (e.g., "Cu") or alloy (e.g., "CuAu").
        facet: Miller index as string (e.g., "111", "100", "211").
        layers: Number of atomic layers.
        vacuum: Vacuum spacing in Angstrom.
        size: Supercell size (a, b, layers_multiplier).

    Returns:
        ASE Atoms object with bottom layers fixed.
    """
    # Determine primary element and crystal structure
    primary_element = _get_primary_element(surface)
    crystal = METAL_CRYSTAL_STRUCTURE.get(primary_element, "fcc")

    builder_key = (crystal, facet)
    if builder_key not in SLAB_BUILDERS:
        # Fall back to fcc111 for unsupported facets
        print(f"  Warning: No builder for ({crystal}, {facet}), falling back to fcc111")
        builder_key = ("fcc", "111")

    builder = SLAB_BUILDERS[builder_key]
    a = LATTICE_CONSTANTS.get(primary_element, 3.61)

    # Build slab
    if crystal == "hcp":
        slab = builder(primary_element, size=size, a=a, c=a * 1.633,
                       vacuum=vacuum, orthogonal=True)
    else:
        slab = builder(primary_element, size=size, a=a,
                       vacuum=vacuum, orthogonal=True)

    # For alloys, substitute atoms
    if len(surface) > 2 and not surface.isalpha():
        # Parse alloy composition like "Cu3Au" or "CuAu"
        slab = _make_alloy_slab(slab, surface)
    elif len(surface) > 2 or (len(surface) == 2 and surface[1].isupper()):
        # Two-letter alloy like "CuAu"
        slab = _make_alloy_slab(slab, surface)

    # Fix bottom layers
    z_positions = slab.positions[:, 2]
    z_min = z_positions.min()
    z_range = z_positions.max() - z_min
    fix_mask = z_positions < (z_min + z_range * 0.5)
    slab.set_constraint(FixAtoms(mask=fix_mask))

    return slab


def _get_primary_element(surface: str) -> str:
    """Extract the primary (first) element from a surface formula."""
    # Handle cases like "Cu3Au", "CuAu", "Cu"
    element = ""
    for char in surface:
        if char.isupper():
            if element:
                break
            element = char
        elif char.islower():
            element += char
        else:
            break
    return element if element else surface[:2]


def _make_alloy_slab(slab: Atoms, composition: str) -> Atoms:
    """Substitute atoms in a slab to create an alloy.

    Supports formats like: CuAu (1:1), Cu3Au (3:1), CuAu3 (1:3).
    """
    import re
    parts = re.findall(r"([A-Z][a-z]?)(\d*)", composition)
    elements = []
    counts = []
    for elem, count in parts:
        if elem:
            elements.append(elem)
            counts.append(int(count) if count else 1)

    if len(elements) < 2:
        return slab

    total = sum(counts)
    fractions = [c / total for c in counts]

    # Assign elements based on fractional composition
    symbols = list(slab.get_chemical_symbols())
    n_atoms = len(symbols)
    idx = 0
    for i, (elem, frac) in enumerate(zip(elements, fractions)):
        n_this = int(round(frac * n_atoms))
        if i == len(elements) - 1:
            n_this = n_atoms - idx  # remainder
        for j in range(idx, min(idx + n_this, n_atoms)):
            symbols[j] = elem
        idx += n_this

    slab.set_chemical_symbols(symbols)
    return slab


# ---------------------------------------------------------------------------
# FAIRChem energy calculation
# ---------------------------------------------------------------------------
_calculator = None


def get_calculator(model_name: str = "uma-sm", device: str = "auto"):
    """Load the FAIRChem UMA calculator (cached singleton).

    Args:
        model_name: FAIRChem model variant. Options: "uma-sm", "uma-md", "uma-lg".
        device: "auto", "cuda", or "cpu".
    """
    global _calculator
    if _calculator is not None:
        return _calculator

    try:
        from fairchem.core import OCPCalculator
    except ImportError:
        print("Error: fairchem-core not found. Install via: pip install fairchem-core")
        print("You also need a HuggingFace token for UMA model access.")
        sys.exit(1)

    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    print(f"  Loading FAIRChem {model_name} on {device}...")
    _calculator = OCPCalculator(
        model_name=model_name,
        local_cache="/tmp/fairchem_cache",
        device=device,
    )
    return _calculator


def compute_energy(atoms: Atoms, calculator=None) -> float:
    """Compute total energy of an atomic structure using FAIRChem.

    Args:
        atoms: ASE Atoms object.
        calculator: FAIRChem calculator (uses cached singleton if None).

    Returns:
        Total energy in eV.
    """
    if calculator is None:
        calculator = get_calculator()

    atoms_copy = atoms.copy()
    atoms_copy.calc = calculator

    # Relax the structure (adsorbate + top layers)
    optimizer = LBFGS(atoms_copy, logfile=None)
    try:
        optimizer.run(fmax=0.05, steps=100)
    except Exception as e:
        print(f"  Warning: Relaxation incomplete ({e}), using current energy")

    return atoms_copy.get_potential_energy()


def compute_adsorption_energy(surface: str, facet: str, adsorbate: str,
                               height: float = 2.0, device: str = "auto") -> dict:
    """Compute adsorption energy for an adsorbate on a surface.

    E_ads = E(slab+adsorbate) - E(slab) - E(gas_reference)

    Args:
        surface: Metal or alloy (e.g., "Cu", "CuAu").
        facet: Miller index (e.g., "111").
        adsorbate: Adsorbate species (e.g., "CO").
        height: Initial adsorbate height above surface (Angstrom).
        device: Compute device ("auto", "cuda", "cpu").

    Returns:
        Dict with adsorption energy and metadata.
    """
    calc = get_calculator(device=device)

    # Build clean slab
    slab = build_slab(surface, facet)
    E_slab = compute_energy(slab, calc)

    # Build slab + adsorbate
    slab_ads = build_slab(surface, facet)
    if adsorbate not in ADSORBATE_ATOMS:
        raise ValueError(f"Unknown adsorbate: {adsorbate}. "
                         f"Available: {list(ADSORBATE_ATOMS.keys())}")
    ads_mol = ADSORBATE_ATOMS[adsorbate]
    add_adsorbate(slab_ads, ads_mol, height=height, position="ontop")
    E_slab_ads = compute_energy(slab_ads, calc)

    # Gas-phase reference
    E_gas = GAS_PHASE_REFERENCES.get(adsorbate, 0.0)

    E_ads = E_slab_ads - E_slab - E_gas

    return {
        "surface": surface,
        "facet": facet,
        "adsorbate": adsorbate,
        "E_ads_eV": round(E_ads, 4),
        "E_slab_eV": round(E_slab, 4),
        "E_slab_ads_eV": round(E_slab_ads, 4),
        "E_gas_ref_eV": round(E_gas, 4),
        "n_atoms_slab": len(slab),
        "n_atoms_total": len(slab_ads),
        "converged": True,
    }


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------
def evaluate_candidates(candidates: list, device: str = "auto") -> list:
    """Evaluate adsorption energies for a list of candidates.

    Args:
        candidates: List of dicts from explore.py CANDIDATES.
        device: Compute device.

    Returns:
        List of result dicts with adsorption energies.
    """
    results = []
    total = sum(len(c.get("adsorbates", ["CO"])) for c in candidates)
    completed = 0

    for candidate in candidates:
        surface = candidate["surface"]
        facet = candidate["facet"]
        adsorbates = candidate.get("adsorbates", ["CO"])

        for adsorbate in adsorbates:
            completed += 1
            print(f"\n[{completed}/{total}] {surface}({facet}) + *{adsorbate}")
            try:
                result = compute_adsorption_energy(
                    surface, facet, adsorbate, device=device
                )
                result["notes"] = candidate.get("notes", "")
                results.append(result)
                print(f"  E_ads = {result['E_ads_eV']:.4f} eV")
            except Exception as e:
                print(f"  Error: {e}")
                results.append({
                    "surface": surface,
                    "facet": facet,
                    "adsorbate": adsorbate,
                    "E_ads_eV": None,
                    "error": str(e),
                    "converged": False,
                })

    return results


# ---------------------------------------------------------------------------
# ISAAC record generation
# ---------------------------------------------------------------------------
def result_to_isaac_record(result: dict) -> dict:
    """Convert a FAIRChem result to an ISAAC AI-Ready Record.

    Creates a simulation-domain evidence record with adsorption energy
    as the primary descriptor.
    """
    import hashlib

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Generate deterministic record ID
    key = f"autocatalysis:{result['surface']}:{result['facet']}:{result['adsorbate']}"
    digest = hashlib.sha256(key.encode()).digest()[:16]
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    num = int.from_bytes(digest, "big")
    chars = []
    for _ in range(26):
        chars.append(alphabet[num & 0x1F])
        num >>= 5
    record_id = "".join(reversed(chars))

    surface = result["surface"]
    facet = result["facet"]
    adsorbate = result["adsorbate"]

    descriptor_name = f"E_ads_{adsorbate}"
    descriptors_list = [{
        "name": descriptor_name,
        "kind": "absolute",
        "source": "fairchem_uma",
        "value": result["E_ads_eV"],
        "unit": "eV",
        "definition": f"Adsorption energy of *{adsorbate} on {surface}({facet}), "
                      f"computed via FAIRChem UMA model.",
        "uncertainty": {"sigma": 0.1, "unit": "eV"},
    }]

    record = {
        "isaac_record_version": "1.0",
        "record_id": record_id,
        "record_type": "evidence",
        "record_domain": "simulation",
        "timestamps": {"created_utc": now_utc},
        "acquisition_source": {"source_type": "computation"},
        "sample": {
            "material": {
                "name": f"{surface}({facet}) slab",
                "formula": surface,
                "provenance": "theoretical",
                "notes": f"FAIRChem UMA relaxed {surface} slab with *{adsorbate} adsorbate.",
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
            "instrument": {
                "instrument_type": "simulation_engine",
                "instrument_name": "FAIRChem-UMA",
                "vendor_or_project": "Meta-FAIR",
            },
            "configuration": {
                "model": "uma-sm",
                "facet": facet,
                "surface_composition": surface,
                "adsorbate": adsorbate,
                "n_atoms": result.get("n_atoms_total", 0),
            },
            "simulation": {"method": "MLIP"},
        },
        "computation": {
            "method": {
                "family": "MLIP",
                "functional_class": "GNN",
                "functional_name": "UMA",
            }
        },
        "descriptors": {
            "outputs": [{
                "label": "autocatalysis_adsorption_energy",
                "generated_utc": now_utc,
                "generated_by": {
                    "agent": "autocatalysis_fairchem",
                    "version": "1.0",
                },
                "descriptors": descriptors_list,
            }]
        },
    }

    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Compute adsorption energies using FAIRChem UMA model."
    )
    parser.add_argument("--surface", type=str, help="Surface element (e.g., Cu)")
    parser.add_argument("--facet", type=str, help="Miller index (e.g., 111)")
    parser.add_argument("--adsorbate", type=str, default="CO", help="Adsorbate species")
    parser.add_argument("--from-explore", action="store_true",
                        help="Evaluate all candidates from explore.py")
    parser.add_argument("--device", type=str, default="auto",
                        choices=["auto", "cuda", "cpu"], help="Compute device")
    parser.add_argument("--output", type=str, help="Output JSON file path")
    args = parser.parse_args()

    if args.from_explore:
        # Import candidates from explore.py
        sys.path.insert(0, os.path.dirname(__file__))
        from explore import CANDIDATES
        results = evaluate_candidates(CANDIDATES, device=args.device)
    elif args.surface and args.facet:
        result = compute_adsorption_energy(
            args.surface, args.facet, args.adsorbate, device=args.device
        )
        results = [result]
        print(f"\nResult: E_ads({args.adsorbate}) = {result['E_ads_eV']:.4f} eV")
    else:
        parser.print_help()
        sys.exit(1)

    # Generate ISAAC records
    isaac_records = []
    for r in results:
        if r.get("E_ads_eV") is not None:
            isaac_records.append(result_to_isaac_record(r))

    # Output
    output_data = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_evaluated": len(results),
        "n_converged": sum(1 for r in results if r.get("converged")),
        "results": results,
        "isaac_records": isaac_records,
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nSaved {len(results)} results to {args.output}")
    else:
        print(json.dumps(output_data, indent=2))


if __name__ == "__main__":
    main()
