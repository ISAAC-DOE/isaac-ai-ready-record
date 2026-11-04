# 2026-11-05-18 A calibration records the convention used, and privileges no edge energy

- **Product:** repository (schema descriptions, validator, wiki, contract). **Kind:** wording, one warning
  narrowed. **Owner:** D. Sokaras (PI). **Follows:** [2026-11-05-17](2026-11-05-17-energy-axis.md).

## Problem

Change 17 shipped text that treated one edge energy as more correct than another: the `reference_source` row said
tabulated and measured Cu foil energies differ by more than 1 eV and asked for "the one used", the examples called
8979.0 eV "by convention assigned" to the foil inflection, and `CALIBRATION_INCOMPLETE` warned when no source was
given for the reference energy. The PI's rule (2026-11-05): there is no consensus on the absolute energy of an
absorption edge; teams set the Fe K or Cu K edge of a foil to different values. A record says what its axis was
aligned to (the standard and the feature, with its definition) and the value that feature was set to. The
repository is then immune to each team's preference: spectra set on the same standard and feature compare after
subtracting the difference of their set values.

## Change

- `calibration`, `reference_value_eV` (the energy the feature was set to: the record's convention, not a claim
  about the edge's true energy) and `reference_source` (optional: where the convention comes from) say so in the
  schema, on Measurement section 3.4 and in contract step 9.
- `CALIBRATION_INCOMPLETE` no longer asks for `reference_source`; it asks for the standard, the feature definition
  and the set value (and the shift of a computed spectrum).
- Measurement section 3.4 states the comparison across conventions; the examples name the value as "this group's
  convention".

## Declared flips (gate 0) and battery (gate 1)

- No stored record carries `measurement.energy_axis` (query, 2026-11-05: 0), so no stored record changes. A new
  record with no `reference_source` no longer draws `CALIBRATION_INCOMPLETE`.
- Tests: `test_the_source_of_a_convention_is_optional`; full suite and the six wiki checks pass.

## Review (gate 2)

codex, grok and gemini, blind, on the changed sentences against the PI's rule.

- **codex, grok:** "the absolute scale" (status, shift, calibration) still named one convention as the true scale.
  Now "the experimental energy scale, under the convention it states"; the shift is `E_calibrated = E_before +
  shift_applied_eV`, which places the standard's feature at `reference_value_eV`.
- **codex, grok:** the comparison had no direction and named the feature without its definition. Now `E_B = E_A +
  (reference_value_eV of B - reference_value_eV of A)` when the standard, the feature definition and the standard's
  detection mode match; equivalently `E - reference_value_eV` is the energy relative to the standard's feature,
  the same under any convention.
- **codex, grok:** every complete example cited a paper, and "the Cu K edge value of" read as a true energy. The
  measured example now has no `reference_source`; the others say "this group sets the Cu foil first inflection to
  8979.0 eV, a value taken from ...".
- **grok:** an agent could replace another team's value with 8979.0 eV, or invent a source. `reference_value_eV`
  now says to write the value the work used and never replace it; `reference_source` is written when the work
  states it and never invented; contract step 9 says both.
- **grok:** "set to" was unclear for a computed series. The feature is located on the standard computed with the
  same settings (the anchor); the core offset is added back only for a BSE series.
- **Not adopted (codex):** requiring `standard_detection_mode` in `CALIBRATION_INCOMPLETE`. Papers often say "a Cu
  foil" without the mode, and the warning would then fire on records that state everything the source states; the
  comparison rule names the mode as a condition.
- **gemini** made the same four points (comparison conditions, the computed `none` case, the example citation, agents
  "correcting" a reported value); all were already adopted from codex and grok.

## Ship and watch

Shipped in #294 (portal v0.0.377), wiki first. Live: `/schema` serves the new descriptions; the measured example
with no `reference_source` publishes with no warning; contract step 9 says ISAAC privileges no value. Watch: new XAS records state the value their standard's feature was set to,
and none draws `CALIBRATION_INCOMPLETE` for a missing source.
