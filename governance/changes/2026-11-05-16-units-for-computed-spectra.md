# 2026-11-05-16 Units for computed spectra, and every unit on the wiki

- **Product:** repository (vocabulary and its documentation). **Kind:** vocabulary (additive), wiki generator.
  **Owner:** D. Sokaras (PI). **Requested by:** O. Paredes Mellone, for OCEAN XAS records written by the
  `isaac-record-dft` skill.

## Problem

- OCEAN spectra and their intensity descriptors carry OCEAN's own intensity unit, and orbital occupations
  (Tr[ns]) and Bader charges carry a count of electrons. Neither unit was in the vocabulary, so every OCEAN
  record drew `UNIT_NOT_IN_VOCABULARY`, and no reader could learn what the intensity unit means.
- The skill first wrote `mu_x_Vcell_A3`. Tracing OCEAN 3.2.0.1 (`o_spect.f90`, `OCEAN_psi.f90`) showed the
  quantity is OCEAN's spectral function in atomic units times the cell volume, neither an absorption
  coefficient nor a cross-section, so `angstrom^2` would be wrong.
- The canonical units were rendered on no wiki page. Controlled-Vocabulary named a section `units.computation`
  that does not exist and sent readers to `data/vocabulary.json`.

## Change

- `units.spectroscopy` gains `ocean_intensity_au`; `units.general` gains `e`.
- New `units.definitions` (unit → meaning) for units whose name alone does not say what they are. The validator
  accepts both units without a note; the definitions are served by `GET /portal/api/ontology`.
- `tools/generate_wiki_vocab.py` renders the `Units` section (canonical lists, deprecated spellings, definitions)
  at the end of Controlled-Vocabulary; a definition is rendered as prose. Its fallback no longer replaces a
  level-1 page title: on Controlled-Vocabulary it would have deleted the title and the intro.
- Controlled-Vocabulary prose points to the generated list and drops the non-existent section name.

## Declared flips (gate 0)

- Records gaining or losing an error, hold or warning: none (units are open vocabulary).
- Info `UNIT_NOT_IN_VOCABULARY`: lost by records that use `e` (the 3 MnF OCEAN records; existing `e` users elsewhere
  likewise). `ocean_intensity_au` is new; the MnF records still carry `mu_x_Vcell_A3` until their owner updates them.

## Battery (gate 1)

- Full test suite passes; new tests `tests/test_units_vocabulary.py` (defined units are canonical and defined in
  a sentence; Units rendered on the page; the generator keeps a page title).
- All six wiki generator checks pass on the regenerated wiki.
- The live MnF2 OCEAN record with its intensity unit renamed to `ocean_intensity_au` publishes with no note.

## Review (gate 2)

Wording of the `ocean_intensity_au` definition supplied by its requester from the OCEAN source; the change is
additive and documentation-only otherwise.

## Ship and watch

- Ship: this pull request, wiki first.
- Watch: the MnF records get versioned edits (PUT, same record IDs) to the new unit; new OCEAN records use it.
