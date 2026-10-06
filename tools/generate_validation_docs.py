#!/usr/bin/env python3
"""
Generate the validation error/warning/info code table in the wiki FROM the
validator, so the wiki (the universal truth agents read) can never drift from
what the code actually enforces.

The set of codes is EXTRACTED from portal/validation.py; each must have a
registry entry below (tier + one-line meaning). If validation.py emits a code
with no registry entry, --check FAILS — forcing every new rule to be documented.

It also validates every complete record shown on a wiki page: agents copy those examples,
so each one must pass the validator it teaches (--check fails otherwise).

Usage:
  python3 tools/generate_validation_docs.py /path/to/wiki          # rewrite in place
  python3 tools/generate_validation_docs.py --check /path/to/wiki  # exit 1 if stale/undocumented
"""
import json
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
    "DUPLICATE_DESCRIPTOR_NAME": ("error", "The same descriptor name appears twice in one output block with the same `at`. One name, one value per condition: a second value is a different condition (state it in `at`) or a different record."),
    "DESCRIPTOR_CLASS_ALIAS": ("error", "A descriptor uses a deprecated spelling listed in descriptors.class_aliases; the message names the canonical class."),
    "PREFIX_IN_DESCRIPTOR_NAME": ("error", "A descriptor name begins with a reaction, technique or method token (orr_, oer_, dft_, xanes., ...; descriptors.name_prefix_tokens). The reaction lives in context, the technique in system.technique, experimental vs computational in system.domain; the name carries only the quantity."),
    "CONDITION_IN_DESCRIPTOR_NAME": ("error", "A descriptor name contains an operating condition (a current density, potential, temperature, time or an _at_ point). Conditions go in the descriptor's `at` or in context; the name is the quantity class, e.g. overpotential with at.current_density_mA_cm2 = 10."),
    "DESCRIPTOR_CLASS_NOT_LOWERCASE": ("error", "The class part of a descriptor name (before the first dot) is not lowercase. Element, species or layer labels are qualifiers after the dot: layer_thickness.Cu, oxidation_state.Cu."),
    "CLASS_UNIT_MISMATCH": ("error", "A numeric descriptor of a canonical class is not in that class's canonical unit (descriptors.class_units). Convert the value; do not relabel the unit."),
    "PRODUCER_AS_AUTHOR_LIST": ("hold", "produced_by.group is a list of authors ('Huihuang Fang et al.', 'Smith and co-workers'). Name the group by its principal investigator as the byline prints it, '<PI name> group', and add produced_by.organization: the discovery engine matches this field exactly to judge whether two results are independent."),
    "PRODUCER_NAME_FORMAT": ("warning", "produced_by.group reads like author names ('Smith, J.; Lee, K.', 'Smith and Jones') and names no group, lab or institute. Write '<PI name> group' (two PIs: 'A and B groups'), the same way in every record."),
    "CONDITION_ONLY_IN_PROSE": ("warning", "A descriptor's definition states the temperature, pressure or potential (vs RHE) its value was read at ('at 400 C'), and no structured field carries that quantity. Put it in the descriptor's `at` or in context. A reference value ('relative to the value at 0 V'), a fit range, a rate ('10 K/min'), a C-rate, a preparation step ('calcined at 500 C') and the descriptor's own quantity do not match."),
    "SOURCE_LOOKS_LIKE_A_REVIEW": ("warning", "The source citation looks like a review: the journal publishes reviews (Chem. Rev., Chem. Soc. Rev., Nature Reviews, Annual Review of, Current Opinion in, Acc. Chem. Res., Rev. Mod. Phys., Phys. Rep.), or the title names one ('review', 'recent advances', 'progress and perspectives', 'meta-analysis', 'a roadmap'). A value a review quotes belongs to the paper that measured it; keep the review as the source only when it first reported the value."),
    "FUNCTIONAL_NOT_REPORTED": ("warning", "A DFT, DFT+U, hybrid-DFT or AIMD result gives no exchange-correlation functional (missing or 'not_reported'), and the message also notes a missing code. A DFT value is comparable only at the same functional, and papers state it in their methods."),
    "CHECKSUM_NOT_SHA256": ("warning", "An asset's sha256 is not 64 hexadecimal characters, or it is one character repeated, such as all zeros. The checksum is the SHA-256 of the bytes the asset's URI returns. A paper cited by its DOI takes not_available_literature_source, even when you hold a PDF or a text extract of it. Any other resource you do not hold, or cannot hash, takes not_available."),
    "SAME_SAMPLE_ON_A_SHARED_BASIS": ("warning", "A same_sample_as link gives as its basis something different specimens can share: a material batch, a replicate preparation, an analysis or computational method, a workflow version, a pipeline output, an absorber edge, operating conditions or a reference state, or, for a measured specimen, an identical geometry. same_sample_as asserts one physical specimen, or one identical model for a calculation. If the source gives the specimen an identifier both records can store, write it in sample_id and remove the link; if it establishes one specimen without an identifier, keep the link with basis unspecified and the establishing passage in notes; otherwise remove the link."),
    "SAME_SAMPLE_WITHOUT_PASSAGE": ("warning", "A same_sample_as link with basis unspecified has empty notes. Its notes carry the shortest passage or locator in the source that establishes one physical specimen (one identical model for a calculation). Add the passage, or remove the link if the source does not establish the identity."),
    "LINK_TARGET_NOT_FOUND": ("warning", "A link's target is no record the repository knows: not published, and not held by the uploader. Checked by the API at validate and upload time. Within a batch, upload the target first or link from the later record."),
    "SECOND_HAND_SOURCE": ("hold", "A curator-written field labels a value as quoted from a review ('SECOND-HAND (review table)', 'quoted in a review article', 'taken from a review table'). A value a review quotes is the result of the paper that measured it: the record is built from that paper with relation 'source', and the review is cited with relation 'reference'. 'A second-hand potentiostat' is equipment and does not match."),
    "CONDITIONS_IN_SAMPLE_NAME": ("hold", "The sample name carries a measurement condition: a number with a temperature, pressure, potential or time unit ('300 C at 50 bar', 'at 1.2 V'). Conditions of the measurement go in context or in a descriptor's `at`; a condition of how the material was made ('calcined at 500 C') is left alone."),
    "COMPETING_HYPOTHESIS_LANGUAGE": ("hold", "A curator-written field names a competing hypothesis, such as 'Supports the INTERFACE rival' or 'Metal-interface rival.': a verdict verb aimed at a named rival, or a rival named like a label (an all-capitals word of six or more letters, or a hyphenated compound that is not a material description). Which explanation the data supports belongs to the discovery platform; a conclusion the source itself draws goes in a source excerpt. 'Rival' for a competing material or technology ('activity rivaling Pt', 'a Pt-free rival to IrO2') does not match."),
    "REASONING_IN_RECORD": ("error", "A curator-written field (sample name or notes, QC notes or evidence, a descriptor definition, context notes) contains a hypothesis label, a verdict, benchmark machinery, a case or item identifier, or the purpose the record serves. A record is knowledge; reasoning about it belongs to whatever uses it, outside the repository. Quotes of a source stored in assets are not scanned."),
    "TAG_ENCODES_USE": ("error", "A tag names how the record is used (a benchmark case or item, a hypothesis) rather than what the data is. Tags group data: a dataset, a campaign, a material system, a facility, a publication."),
    "MISSING_REACTION": ("error", "A performance record does not state its reaction in context.reaction {name, drive, catalysis}. The message spells out the move for records that still use the deprecated context.electrochemistry.reaction."),
    "REACTION_MISMATCH": ("error", "context.reaction.name and the deprecated context.electrochemistry.reaction disagree. A record has one reaction."),
    "REACTION_DRIVE_INCONSISTENT": ("error", "context.reaction.drive contradicts the record: a measured electrochemical reaction without a context.electrochemistry block, or an electrochemical control_mode with a non-electrochemical drive. A calculation has no cell; its potential treatment is computation.potential_method."),
    "REACTION_FIELD_DEPRECATED": ("warning", "The record still carries context.electrochemistry.reaction. The reaction lives in context.reaction; move it there and remove the old field."),
    "POTENTIAL_FROM_OTHER_CONTEXT": ("error", "A potential on an experimental record comes from another context: its definition says it was stated for a DFT model or a simulation. Only the potential measured in this experiment, or reported for it by the source, goes on the axis. If the source reports only the current, declare potential_vs_RHE {value_V: null, rhe_basis: not_reported} and describe the cell."),
    "GALVANOSTATIC_NO_POTENTIAL": ("error", "A galvanostatic performance record says nothing about its potential. Add the measured potential (steady_state_potential in V_RHE, or the series), or state openly that the source reports only the current: rhe_basis not_reported for a half cell, not_applicable for a two-electrode device (with cell_voltage in V_cell when given)."),
    "CELL_TYPE_MISSING": ("error", "An electrochemical performance record does not name its cell body in context.electrochemistry.cell_type. Performance is a property of a catalyst in a cell."),
    "FULL_CELL_DESCRIPTION_INCOMPLETE": ("error", "An mea_cell or zero_gap_cell record lacks the membrane or the active area. A two-electrode device is described by its catalysts and loadings on each side, the membrane, the active area, the feed and the temperature."),
    "HALF_CELL_POTENTIAL_IN_FULL_CELL": ("error", "An mea_cell or zero_gap_cell record carries a half-cell potential without a reference electrode. A two-electrode device reports cell_voltage (V_cell) and potential_vs_RHE rhe_basis not_applicable, unless a reference electrode was integrated and is declared."),
    "FEED_UNDECLARED": ("error", "A gde_cell, mea_cell or zero_gap_cell record does not declare context.transport.feed {phase, composition, ...}. What the cell is fed decides the reaction environment."),
    "DEPRECATED_CELL_TYPE": ("warning", "cell_type three_electrode names the wiring, not the cell body. Name the body (beaker_cell, h_cell, flow_cell, ...) and put the electrode configuration in system.configuration."),
    "SYSTEM_DOMAIN_MISSING": ("error", "An evidence record does not say whether it is a measurement or a calculation (system.domain experimental | computational). A result reported in a paper keeps the domain of the work the paper did."),
    "DOMAIN_INCONSISTENT": ("error", 'The field values that say calculation and those that say measurement disagree. Which values say which is vocabulary data (system.domain_signals, on the System page); the message names every field on each side. A calculation reported in a paper is still a calculation.'),
    "COMPUTATION_METHOD_MISSING": ("error", 'A calculation (system.domain, system.technique or source_type says so) has no computation.method.family. Declare the fields in computation.method_requirements and those its family requires; for a calculation taken from a paper, copy the method the paper states.'),
    "COMPUTATION_METHOD_INCOMPLETE": ("error", "computation.method lacks a field that every calculation declares (computation.method_requirements: family and code) or that its family declares (computation.method_requirements_by_family). Write 'not_reported' for a value the source does not state."),
    "PRODUCED_BY_MISSING": ("error", "An evidence record does not name who produced the result in attribution.produced_by (a group or an organization). On a literature record a placeholder ('the authors', 'unknown', 'not_reported') names no one and is an error, since a paper always names its authors; elsewhere it draws PRODUCED_BY_UNNAMED. The server-stamped uploaded_by says who deposited the record, not who measured or computed it; source_type and produced_by together distinguish a published calculation, the uploader's own calculation and someone else's measurement."),
    "LITERATURE_CITATION_MISSING": ("error", "A literature record cites no source: no asset whose citation (doi, or title and year, or a doi.org uri) has relation 'source' or no relation. A paper cited only as a reference is not the record's source."),
    "CODE_OUTSIDE_METHOD": ("error", 'A calculation states its code outside computation.method.code: in system.instrument (measurement hardware only) or in a system.configuration key named code or *_code. The code has one home; the computer goes in system.facility.'),
    "LITERATURE_SOURCE_UNDECLARED": ("error", "A record that is not source_type literature or database shows that its numbers were taken from a publication: a processing step in measurement.processing.publication_extraction_steps, references to the source's figures, figure captions or pages on an asset, or a citation with relation 'source'."),
    "CITATION_RELATION_UNDECLARED": ("error", 'A record that is not source_type literature or database mentions a publication (a DOI anywhere outside sample.material.identifiers) without an asset citation {doi, relation} saying whether it is the source of the numbers, reports this same work, or is a related reference.'),
    "DATABASE_ENTRY_MISSING": ("error", "A database record names no entry: no asset with database_entry {database, entry_id}. entry_id is the database's own identifier for the entry; collection names the dataset or publication the database files it under."),
    "SAMPLE_NOT_ONE_MATERIAL": ("hold", "sample.material.name names a collection (a survey, a review, a compilation, several or varied samples, studies across conditions) instead of one material. One record holds one result on one sample or one model; many results are many records, each built from the paper that produced it."),
    "SAMPLE_NAME_CITES_A_PAPER": ("hold", "sample.material.name carries a citation (Author 2020, et al. 2020). A value the source quotes from another paper is that paper's result; its record is built from that paper."),
    "MULTIPLE_SOURCES": ("hold", "More than one work is cited as the source (citation.relation 'source', or no relation). One result comes from one work; the same work published elsewhere is 'reports_this_work', a paper cited for context is 'reference'."),
    "QUALIFIER_NOT_A_PRODUCT": ("hold", "A catalytic-performance descriptor (descriptors.catalytic_performance) has a qualifier after the dot that is neither a chemical formula nor a vocabulary product token, e.g. a catalyst or a condition written into the name. A second catalyst is a second record; a condition goes in `at`."),
    "NUMBER_AS_TEXT": ("warning", "An absolute or differential descriptor holds text ('9.24', '4.06, 8.03', 'sixfold', 'n.d.'). A value is one number with its unit; several values are a series or separate records; a label is kind categorical. A value below a detection limit has no number form yet."),
    "SENTENCE_AS_VALUE": ("hold", "A descriptor value is a sentence (six or more lowercase words). A value is a number with a unit or a short category label; a statement about results interprets them and is not stored as a value."),
    "PERFORMANCE_EX_SITU": ("hold", "A performance record says context.environment 'ex_situ'. Performance is measured while the reaction runs (in_situ or operando); a measurement on a catalyst outside the reaction is a characterization record."),
    "VOCABULARY_SUBSTITUTION": ("hold", "A curator-written field says a vocabulary term was substituted ('vocabulary substitution', 'not in the controlled vocabulary', 'closest available term'). A substituted term is a wrong term; use the exact term or propose it, and a value with no measurement or calculation behind it is not a record."),
    "AT_READOUT_WITHOUT_SWEEP": ("error", "A read-out key (at.current_density_mA_cm2, at.current_density_ECSA_mA_cm2, at.potential_V_RHE) is used on a record that is not a sweep. Read-out keys locate a value on a potentiodynamic sweep; a record held at one potential or current states it in context.electrochemistry."),
    # --- warnings (accepted, but improvable) ---
    "COMPUTATION_ON_MEASUREMENT": ("warning", "A record whose fields say measurement declares computation.method. A method that analysed the measurement (an EXAFS fit, a Rietveld refinement) belongs in measurement.processing; a method that computed a result of its own makes that result a separate calculation record."),
    "PRODUCED_BY_UNNAMED": ("warning", "A record that is not from the literature names a placeholder producer group ('not_reported', 'unknown'). Accepted: some producers cannot be named (anonymized or industrial data), and the discovery engine reads the placeholder as absent. Name the group or organization when it is known."),
    "NEGATIVE_MAGNITUDE": ("warning", "A descriptor of a class stored as a non-negative magnitude (descriptors.magnitude_classes: overpotential, Tafel slope, ECSA, roughness factor, turnover frequency, exchange current density) has a negative value. The direction is carried by the reaction and by the sign of the current density at which the value is read."),
    "MISSING_PH": ("warning", "Performance record has no pH/pH_basis — needed for RHE conversion and cross-record comparison."),
    "MISSING_ELECTRODE_TYPE": ("warning", "sample.electrode_type is unset (GDE, thin_film, MEA, ...)."),
    "IMPLAUSIBLE_CURRENT_DENSITY": ("warning", "A current density exceeds ~10 A/cm2 — almost always a unit/area-normalization bug."),
    "NO_DATA_OWNER": ("warning", "Evidence record declares no attribution.contributors with role data_owner."),
    "QC_COMPROMISED_NO_EVIDENCE": ("warning", "qc.status='compromised' without a concrete evidence sentence."),
    "MODULATED_DESCRIPTORS_UNSPECIFIED": ("warning", "context.modulation is declared but descriptors_represent is unset. A cycle-averaged quantity and a steady-state quantity of the same name are DIFFERENT QUANTITIES; without this a consumer cannot tell them apart."),
    "MODULATION_DRIVEN_VARIABLE_MISSING": ("warning", "context.modulation is present but driven_variable is unset, so a consumer cannot tell which condition was being driven."),
    "MODULATION_RATE_OVERSPECIFIED": ("warning", "Both frequency_Hz and period_s are given and they can disagree. Declare one."),
    "MODULATION_EVIDENT_BUT_UNDECLARED": ("info", "No context.modulation block is declared, and a word for periodic driving (modulat, pulsed, chopped, duty cycle, square wave) appears in an asset's URI, notes or figure text, in system.configuration, in sample.notes or in context.electrochemistry.notes. Titles of cited works are not searched. Declare context.modulation when this record's measurement was driven periodically: without it, every machine reading the record treats each setpoint as the condition of the whole run. If the word describes a preparation step (pulsed laser deposition), an instrument part (an optical chopper) or another work, keep the record as it is."),
    "COMPONENT_SET_EXCEEDS_TOTAL": ("warning", "Leaf members of a component family (a product distribution, selectivity slate, phase or composition breakdown) sum to more than 110% of the expected total in one output block. Aggregate descriptors are excluded from the sum by design. Unlike under-closure this has no benign reading — check for percent encoding or a component counted twice."),
    "COMPONENT_SET_INCOMPLETE_UNDECLARED": ("warning", "Leaf members sum to less than 90% of the expected total and the block does not say why. Usually fine — minor and hard-to-detect species routinely go unquantified — but an undeclared gap is indistinguishable from a measurement that failed to balance. Declare descriptors.outputs[].completeness and this goes silent. Raised as info when less than 20% is missing and as a warning above that."),
    "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS": ("warning", "A descriptor declaring `aggregates` (or listed in descriptors.aggregate_descriptors) is present alongside its members, and their sum differs from its value by more than 0.02. One of the two was not read off the same data."),
    "UNCERTAINTY_BASIS_NOT_IN_VOCABULARY": ("info", "uncertainty.basis is outside the canonical set (reported, digitization_estimate, assumed, propagated, method, exact, not_reported). Free-text bases cannot be filtered or compared across records."),
    "FE_ROLE_VIOLATION": ("warning", "A faradaic_efficiency series channel claims role=measured_response; FE is a derived claim (role must be derived_signal)."),
    "FE_SERIES_DUPLICATE": ("warning", "A single-point series channel duplicates an FE descriptor of the same name."),
    # --- info (suggestions) ---
    "SIGMA_ZERO_PLACEHOLDER": ("warning", "uncertainty.sigma=0.0 with no uncertainty.basis. To a machine this asserts the value is EXACT, and downstream scoring that divides by a noise scale will treat it as infinitely precise. If the source reported no uncertainty write sigma: null with basis: 'not_reported'; if it is genuinely exact (a set point, an integer count) say basis: 'exact'."),
    "UNIT_NOT_IN_VOCABULARY": ("info", "A unit is not in the canonical unit vocabulary and is not a known alias."),
}

BEGIN = "<!-- BEGIN GENERATED:validation-codes -->"
END = "<!-- END GENERATED:validation-codes -->"


def emitted_codes():
    return sorted(set(re.findall(r'"code":\s*"([A-Z][A-Z0-9_]*)"', VALIDATION)))


def render():
    codes = emitted_codes()
    missing = [c for c in codes if c not in REGISTRY]
    if missing:
        raise SystemExit(f"validation.py emits undocumented codes: {missing} — add them to "
                         f"tools/generate_validation_docs.py REGISTRY.")
    order = {"error": 0, "hold": 1, "warning": 2, "info": 3}
    rows = sorted(codes, key=lambda c: (order[REGISTRY[c][0]], c))
    lines = [BEGIN,
             "## Validation codes (generated from `portal/validation.py`)",
             "",
             "> Generated: do not hand-edit between the markers. CI fails if this drifts from the "
             "validator or if a new code is emitted without a registry entry. **Errors** block "
             "ingestion (HTTP 400). **Hold** stores the record privately (HTTP 409, reason `held`): "
             "it is not public, not searchable, and not read by the discovery engine. A corrected "
             "version without hold warnings is published when sent with POST or PUT. **Warnings** are "
             "accepted (201) and name what to fix; **info** suggests.",
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


def embedded_record_failures(wiki: Path) -> list:
    """Complete records shown in wiki pages that a reader would copy wrongly: rejected, carrying a warning, or
    different from the file in examples/ with the same record_id."""
    sys.path.insert(0, str(REPO / "portal"))
    import validation
    examples = {}
    for path in sorted((REPO / "examples").glob("*.json")):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and data.get("record_id"):
            examples[data["record_id"]] = (path.name, data)
    bad = []
    for page in sorted(wiki.glob("*.md")):
        for block in re.findall(r"```json\n(.*?)```", page.read_text(), re.S):
            if '"isaac_record_version"' not in block or '"record_type"' not in block:
                continue
            try:
                record = json.loads(block)
            except ValueError:
                continue  # an elided fragment illustrates a block; it is not a record
            result = validation.validate_record_full(record)
            rid = record.get("record_id")
            if not result["valid"]:
                codes = sorted({e.get("code") or "schema" for e in result["errors"]})
                bad.append(f"{page.name}: record {rid} is rejected {codes}")
            elif result.get("warnings"):
                codes = sorted({w.get("code") for w in result["warnings"]})
                bad.append(f"{page.name}: record {rid} carries warnings {codes}; an example is copied as shown")
            if rid in examples and examples[rid][1] != record:
                bad.append(f"{page.name}: record {rid} differs from examples/{examples[rid][0]}")
    return bad


def main():
    check = "--check" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--check"]
    wiki = Path(args[0]) if args else REPO.parent / "isaac-ai-ready-record.wiki"
    page = wiki / "Validation-Rules.md"
    desired = apply(page)
    rejected = embedded_record_failures(wiki)
    for line in rejected:
        print("REJECTED EXAMPLE: " + line)
    if check:
        if not page.exists() or page.read_text() != desired:
            print("STALE: Validation-Rules.md codes table out of sync — run tools/generate_validation_docs.py")
            return 1
        if rejected:
            return 1
        print("validation codes table up to date; every record shown in the wiki validates")
        return 0
    page.write_text(desired)
    print(f"regenerated Validation-Rules.md ({len(emitted_codes())} codes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
