# Proposed changes (gate 0 drafts)

Each becomes a numbered entry when it starts through the gates.

- **Rietveld results of our XRD records** (4 records). Lattice parameters take the canonical class with the
  phase as qualifier; fit parameters move to `measurement.processing`. Needs review of the naming for
  multi-phase refinements.
- **Warning: one `sample_id` on a model and on a physical sample.** A computed model and a measured specimen
  are never one object. On 2026-10-06, 13 shared sample keys joined a DFT model to a measured catalyst.
  Expected firings: one uploader's records.
- **Warning: a link basis the records do not support.** `same_sample_as` with basis `same_sample_id` where the
  two records do not state one `sample_id`. This needs the target record, so it is checked at the API like
  LINK_TARGET_NOT_FOUND. Expected firings: 17 links, all from other uploaders.
- **Warning: discovery reasoning inside an evidence record.** Hypothesis identifiers ("H1-N-RECOMBINATION"),
  benchmark case paths ("frozen_sets/case_36"), and text about "this corpus". The repository holds results;
  hypotheses belong to the discovery platform. Extends REASONING_IN_RECORD. Expected firings: about 24
  records.
- **Sample identifiers built from a DOI and a material name.** The Links page already says a material name is
  not a `sample_id`. A check is hard to make generic, so the steward measures it first; on 2026-10-06, 85
  such ids joined 227 records.
- **Local identifier scope.** Scope a local `sample_id` by organization and group, not by organization alone.
  This needs the effect on sample groups measured first.
- **counter_electrode** moves to `context.electrochemistry`, where the validator already sends its siblings
  (reference electrode, membrane, anolyte).

## From the agent benchmark baseline (2026-10-06)

Three agents from three vendors wrote records from three papers outside catalysis (battery materials,
geochemistry, enzyme kinetics), learning ISAAC only from its public documentation; a fresh scorer graded each
kit blind against an answer key written beforehand. Values were exact and on the right sample in 8 of 9 sets.
Every failure the three agents shared traces to a catalysis assumption in the core.

**Documentation release 1** (wording; gates 0 to 3, gate 3 run with the current and the new documentation,
both arms scored in one blind run):
- Contract step 8 says which vocabularies are fixed lists and which are open. A descriptor name or unit the
  vocabulary lacks is written as the source prints it; an optional fixed-list field without the right term is
  left out with a note; a missing term is never a reason to leave a result out. In the baseline one agent
  read step 8 as blocking every missing term and stored 3 of a paper's 21 core values.
- A checksum describes the bytes at the asset's URI. A text extract of a paper is a different file from the
  paper at its DOI. The agents wrote three different encodings.
- Name the machine-readable vocabulary, `GET /portal/api/ontology`; no page names it.

**A generic core, with catalysis as the first profile** (schema changes; all gates, each with a migration):
- **Liquid-phase conditions for any experiment:** pH, buffer and its concentration, ionic strength, solvent.
  Today pH exists only in `context.electrochemistry`; a numeric pH appeared in 2 of 6 aqueous runs, in two
  different fields.
- **Performance for any process.** A performance record must name a reaction from 41 catalytic tokens. No set
  holds a valid, correctly filed record of the battery paper's headline cycling results: the validator
  rejected them as performance and published them as characterization. Dissolution rates and enzyme kinetics
  went to characterization in every set. The reaction list moves to the catalysis profile.
- **Electrochemistry beyond electrocatalysis:** galvanostatic control requires an areal current; a battery
  test states a C-rate in mA/g. Two of three battery sets wrote an areal current the paper never gives.
- **An environment for in vitro and other laboratory experiments** that are neither operando nor in situ.
- **A condition range for a fitted result** (an activation energy fitted over 5 to 75 °C, a rate law over
  pH 5 to 9). Today the only null basis, `not_reported`, says the source does not state the value.
- **A value stated only as a bound** ("less than 1%"), which `uncertainty.bounds` does not express.
- **A mark for a value derived from the source's other values** (c/a from a and c; k_cat/K_M).
- **Profile vocabularies** for the headline quantities of other sciences (k_cat, K_M; dissolution rate;
  specific capacity, coulombic efficiency, cycle life) and their units.
- **The ratio naming rule** (`*_ratio.*` rejected so ratios are computed from stored parts) assumes the parts
  are reported. When a source reports only the ratio, agents renamed it to pass.

**Edison (PaperQA3) on these conventions, 2026-10-06.** Across STRENDA and SABIO-RK or BRENDA (enzyme
kinetics), mineral-dissolution compilations and EarthChem, BattINFO, and electrocatalysis reporting guidelines:
conditions belong to each observation, and pH is a condition of the observation where the medium makes it
meaningful, never one field for every experiment type; STRENDA DB requires buffer type, buffer concentration
and the final assay pH for every assay; a fitted law (rate constants, activation energy, reaction order) is a
result of its own with the pH and temperature range its inputs support; a result below a detection limit keeps
its inequality and threshold, never zero or a plain number. No surveyed resource mandates field names for fit
domains or censored values, so the core would set them. Curated literature databases lose conditions as well:
BRENDA holds pH for 50% of its K_M records and temperature for 46%. A second question (how repositories
classify performance across sciences and extend a process vocabulary) failed: the Edison account returned 402.

**Open question for step 7:** the independent key reads "room temperature" as no number; the contract stores
298.15 K with basis `room_temperature`. All three geochemistry sets followed the contract.

**Candidates from the review of change 09** (no stored record would flag them today): a warning for
`not_available_literature_source` on an asset that is no paper.
