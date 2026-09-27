#!/usr/bin/env python3
"""
Generate the validation error/warning/info code table in the wiki FROM the
validator, so the wiki (the universal truth agents read) can never drift from
what the code actually enforces.

The set of codes is EXTRACTED from portal/validation.py; each must have a
registry entry below (tier + one-line meaning). If validation.py emits a code
with no registry entry, --check FAILS — forcing every new rule to be documented.

Usage:
  python3 tools/generate_validation_docs.py /path/to/wiki          # rewrite in place
  python3 tools/generate_validation_docs.py --check /path/to/wiki  # exit 1 if stale/undocumented
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VALIDATION = (REPO / "portal" / "validation.py").read_text()

# tier: error (blocks ingestion) | warning (accepted, teaches) | info (suggests)
REGISTRY = {
    # --- errors (block) ---
    "SIGN_CONVENTION": ("error", "A cathodic-reaction current is positive; IUPAC convention requires reduction currents negative (ADR-001)."),
    "WRONG_BLOCK": ("error", "A field is in the wrong block (e.g. reference_electrode/membrane in system.configuration); see the Concept Home Matrix."),
    "DUPLICATE_DESCRIPTOR_NAME": ("error", "The same descriptor name appears twice in one output block. One name, one value per block: a second value is a different condition (state it in `at`) or a different record."),
    "DESCRIPTOR_CLASS_ALIAS": ("error", "A descriptor uses a deprecated spelling listed in descriptors.class_aliases; the message names the canonical class."),
    "PREFIX_IN_DESCRIPTOR_NAME": ("error", "A descriptor name begins with a reaction, technique or method token (orr_, oer_, dft_, xanes., ...; descriptors.name_prefix_tokens). The reaction lives in context, the technique in system.technique, experimental vs computational in system.domain; the name carries only the quantity."),
    "CONDITION_IN_DESCRIPTOR_NAME": ("error", "A descriptor name contains an operating condition (a current density, potential, temperature, time or an _at_ point). Conditions go in the descriptor's `at` or in context; the name is the quantity class, e.g. overpotential with at.current_density_mA_cm2 = 10."),
    "DESCRIPTOR_CLASS_NOT_LOWERCASE": ("error", "The class part of a descriptor name (before the first dot) is not lowercase. Element, species or layer labels are qualifiers after the dot: layer_thickness.Cu, oxidation_state.Cu."),
    "CLASS_UNIT_MISMATCH": ("error", "A numeric descriptor of a canonical class is not in that class's canonical unit (descriptors.class_units). Convert the value; do not relabel the unit."),
    "MISSING_REACTION": ("error", "A performance record does not state its reaction in context.reaction {name, drive, catalysis}. The message spells out the move for records that still use the deprecated context.electrochemistry.reaction."),
    "REACTION_MISMATCH": ("error", "context.reaction.name and the deprecated context.electrochemistry.reaction disagree. A record has one reaction."),
    "REACTION_DRIVE_INCONSISTENT": ("error", "context.reaction.drive contradicts the record: an electrochemical drive without a context.electrochemistry block, or an electrochemical control_mode with a non-electrochemical drive."),
    "REACTION_FIELD_DEPRECATED": ("warning", "The record still carries context.electrochemistry.reaction. The reaction lives in context.reaction; move it there and remove the old field."),
    "AT_READOUT_WITHOUT_SWEEP": ("error", "A read-out key (at.current_density_mA_cm2, at.current_density_ECSA_mA_cm2, at.potential_V_RHE) is used on a record that is not a sweep. Read-out keys locate a value on a potentiodynamic sweep; a record held at one potential or current states it in context.electrochemistry."),
    # --- warnings (accepted, but improvable) ---
    "MISSING_PH": ("warning", "Performance record has no pH/pH_basis — needed for RHE conversion and cross-record comparison."),
    "MISSING_ELECTRODE_TYPE": ("warning", "sample.electrode_type is unset (GDE, thin_film, MEA, ...)."),
    "GALVANOSTATIC_NO_POTENTIAL": ("warning", "Galvanostatic record carries no measured voltage; add steady_state_potential / cell_voltage, or declare potential_vs_RHE rhe_basis not_reported / not_applicable."),
    "IMPLAUSIBLE_CURRENT_DENSITY": ("warning", "A current density exceeds ~10 A/cm2 — almost always a unit/area-normalization bug."),
    "NO_LINKS": ("warning", "Record has no links[] and no tags[]; group it via a typed link (same_sample_as / derived_from / intended_comparison_target) or a tag."),
    "NO_DATA_OWNER": ("warning", "Evidence record declares no attribution.contributors with role data_owner."),
    "QC_COMPROMISED_NO_EVIDENCE": ("warning", "qc.status='compromised' without a concrete evidence sentence."),
    "MODULATED_DESCRIPTORS_UNSPECIFIED": ("warning", "context.modulation is declared but descriptors_represent is unset. A cycle-averaged quantity and a steady-state quantity of the same name are DIFFERENT QUANTITIES; without this a consumer cannot tell them apart."),
    "MODULATION_DRIVEN_VARIABLE_MISSING": ("warning", "context.modulation is present but driven_variable is unset, so a consumer cannot tell which condition was being driven."),
    "MODULATION_RATE_OVERSPECIFIED": ("warning", "Both frequency_Hz and period_s are given and they can disagree. Declare one."),
    "MODULATION_EVIDENT_BUT_UNDECLARED": ("info", "Assets or notes mention a modulated, pulsed, chopped or square-wave experiment but no context.modulation block is declared, so every machine reading the record treats the measurement as static and its setpoints as the condition of the whole run."),
    "COMPONENT_SET_EXCEEDS_TOTAL": ("warning", "Leaf members of a component family (a product distribution, selectivity slate, phase or composition breakdown) sum to more than 110% of the expected total in one output block. Aggregate descriptors are excluded from the sum by design. Unlike under-closure this has no benign reading — check for percent encoding or a component counted twice."),
    "COMPONENT_SET_INCOMPLETE_UNDECLARED": ("warning", "Leaf members sum to less than 90% of the expected total and the block does not say why. Usually fine — minor and hard-to-detect species routinely go unquantified — but an undeclared gap is indistinguishable from a measurement that failed to balance. Declare descriptors.outputs[].completeness and this goes silent. Raised as info when less than 20% is missing and as a warning above that."),
    "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS": ("warning", "A descriptor declaring `aggregates` (or listed in descriptors.aggregate_descriptors) is present alongside its members, and their sum differs from its value by more than 0.02. One of the two was not read off the same data."),
    "UNCERTAINTY_BASIS_NOT_IN_VOCABULARY": ("info", "uncertainty.basis is outside the canonical set (reported, digitization_estimate, assumed, propagated, method, exact, not_reported). Free-text bases cannot be filtered or compared across records."),
    "FE_ROLE_VIOLATION": ("warning", "A faradaic_efficiency series channel claims role=measured_response; FE is a derived claim (role must be derived_signal)."),
    "FE_SERIES_DUPLICATE": ("warning", "A single-point series channel duplicates an FE descriptor of the same name."),
    "COMPUTATION_METHOD_MISSING": ("warning", "A computation record (source_type=computation / domain=simulation) has no computation.method block; declare family + functional_name (PBE/RPBE/BEEF-vdW/...) + code so the result is comparable across functionals."),
    "COMPUTATION_METHOD_INCOMPLETE": ("warning", "computation.method is missing family or functional_name — the comparability keys for a computed energy/barrier."),
    # --- info (suggestions) ---
    "SIGMA_ZERO_PLACEHOLDER": ("warning", "uncertainty.sigma=0.0 with no uncertainty.basis. To a machine this asserts the value is EXACT, and downstream scoring that divides by a noise scale will treat it as infinitely precise. If the source reported no uncertainty write sigma: null with basis: 'not_reported'; if it is genuinely exact (a set point, an integer count) say basis: 'exact'."),
    "UNIT_NOT_IN_VOCABULARY": ("info", "A unit is not in the canonical unit vocabulary and is not a known alias."),
}

BEGIN = "<!-- BEGIN GENERATED:validation-codes -->"
END = "<!-- END GENERATED:validation-codes -->"


def emitted_codes():
    return sorted(set(re.findall(r'"code":\s*"([A-Z_]+)"', VALIDATION)))


def render():
    codes = emitted_codes()
    missing = [c for c in codes if c not in REGISTRY]
    if missing:
        raise SystemExit(f"validation.py emits undocumented codes: {missing} — add them to "
                         f"tools/generate_validation_docs.py REGISTRY.")
    order = {"error": 0, "warning": 1, "info": 2}
    rows = sorted(codes, key=lambda c: (order[REGISTRY[c][0]], c))
    lines = [BEGIN,
             "## Validation codes (generated from `portal/validation.py`)",
             "",
             "> Generated — do not hand-edit between the markers. CI fails if this drifts from the "
             "validator or if a new code is emitted without a registry entry. **Errors** block "
             "ingestion (HTTP 400); **warnings** are accepted (201) and teach; **info** suggests.",
             "",
             "| Code | Tier | Meaning |",
             "|---|---|---|"]
    for c in rows:
        tier, desc = REGISTRY[c]
        lines.append(f"| `{c}` | {tier} | {desc} |")
    lines.append(END)
    return "\n".join(lines)


def apply(page: Path):
    block = render()
    text = page.read_text() if page.exists() else "# Validation Rules\n"
    if BEGIN in text and END in text:
        return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), block, text, flags=re.S)
    return text.rstrip() + "\n\n" + block + "\n"


def main():
    check = "--check" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--check"]
    wiki = Path(args[0]) if args else REPO.parent / "isaac-ai-ready-record.wiki"
    page = wiki / "Validation-Rules.md"
    desired = apply(page)
    if check:
        if not page.exists() or page.read_text() != desired:
            print("STALE: Validation-Rules.md codes table out of sync — run tools/generate_validation_docs.py")
            return 1
        print("validation codes table up to date")
        return 0
    page.write_text(desired)
    print(f"regenerated Validation-Rules.md ({len(emitted_codes())} codes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
