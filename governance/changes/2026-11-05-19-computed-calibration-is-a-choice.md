# 2026-11-05-19 A computed spectrum's calibration is a choice, and says nothing about measuring a standard

- **Product:** repository (schema descriptions, one enum value, wiki, contract, examples). **Kind:** wording, enum
  narrowed (no stored use). **Owner:** D. Sokaras (PI). **Follows:** [17](2026-11-05-17-energy-axis.md),
  [18](2026-11-05-18-calibration-records-the-convention.md).

## Problem

The PI (2026-11-05): calibration information about a standard's measurement belongs to experimental spectra. An
OCEAN calculation only says "I set the absorption edge of Cu metal at this energy, based on this, or on nothing: I
picked it." The shipped OCEAN example named its standard "Cu foil", measured in transmission, with
`standard_acquisition: none`: experimental baggage on a computed record.

## Change

- `calibration` is described with its two uses. Measured: the instrument's calibration, with how the standard was
  measured. Computed: a choice, which computed compound's feature was set to which value, why when the work says
  (a group convention, a table, or simply chosen), and the shift; the feature is located on the computed standard,
  usually the anchor.
- `standard_detection_mode`, `standard_acquisition` and `uncertainty.experimental_eV` are marked measured-only;
  `standard_acquisition` loses `none` (it existed only for computed series).
- `standard` and `standard_record_id` name the computed standard for a computed series (e.g. `Cu metal`, the
  anchor's record).
- The computed examples state only the choice ("set to the value this group uses for its measured Cu foil"); the
  wiki states that a computed series and a measurement that both set the Cu first inflection to 8979.0 eV are on one
  convention. Contract step 9 says both uses.
- The co-designer's skill (its PR 6) stops writing a detection mode on computed records and points
  `standard_record_id` at the computed standard.

## Gates

- 0 and 1: no stored record carries `measurement.energy_axis` (0 on 2026-11-05), so no stored record changes.
  Test `test_a_computed_calibration_states_a_choice_and_nothing_about_measuring_a_standard`; suite and the six wiki
  checks pass.
- 2: codex, grok and gemini, blind. Adopted: the "choice" sentence (wiki, contract step 9, schema) now names the
  feature definition, the set value and the shift, with a worked sentence (grok, gemini); the schema opening no
  longer frames a computed choice as placement on the experimental scale (grok, gemini); the theory-experiment
  sentence says the two are referenced to the same material, feature and value, which is how a computed spectrum is
  compared with a measured one, and that broadening, the standard's detection mode and differing definitions belong
  to the comparison's uncertainty (codex, grok, gemini, who read "one convention" as too strong). Already in place:
  `CALIBRATION_INCOMPLETE` flags a computed calibration without its definition, set value or shift (codex). Not
  adopted: requiring the standard's detection mode on measured records (codex), declined in change 18 for the same
  reason, and a check against measured-only fields on computed records, which can follow if records carry them.

## Ship and watch

Ship: this pull request, wiki first. Watch: computed records carry no measured-only calibration fields.
