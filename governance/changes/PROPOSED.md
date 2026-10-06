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
