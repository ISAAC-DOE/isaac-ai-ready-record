# 2026-11-05-21 The transition operator of a computed spectrum, and X-ray Raman scattering

- **Product:** repository (schema, vocabulary, validator, wiki, contract). **Kind:** schema (additive), four
  warnings, contract 2026-11-05.3. **Owner:** D. Sokaras (PI). **Proposed by:** O. Paredes Mellone (OCEAN),
  2026-11-05; PI go the same day.

## Problem

A computed spectrum is defined by the transition operator the code read: XAS in the dipole or dipole-plus-quadrupole
approximation, or X-ray Raman scattering (XRS) with the full momentum-transfer operator exp(iq.r). ISAAC had no
field for it, and `XRS` was not in `system.technique` (a closed list), so a computed or measured XRS record could not
be stored at all. The co-designer's skill takes the technique from the photon files the BSE read, because OCEAN's
run label says XAS even for qRaman. XRS depends on |q|, and codes read q in their own unit: OCEAN reads `length 1.85
inversebohr` as 3.50 inverse angstrom; a value stored in only one unit hides a slip.

## Change

- `system.technique` gains `XRS`.
- `computation.spectroscopy.transition`: `operator` (`dipole`, `dipole_quadrupole`, `momentum_transfer`; the
  code's keyword in notes, so the vocabulary stays code-agnostic), `q_inverse_angstrom`, `q_as_written` {`value`,
  `unit`}, `vectors` ({`e`, `k`} for XAS, {`q`} for XRS, optional `weight`), `average`, `energy_eV`, `notes`.
  `site_average.polarization` is superseded by `transition.average` (one home); records written before keep it.
- `measurement.energy_axis.detection` gains `q_inverse_angstrom`, `q_as_written`, `scattering_angle_deg` and
  `analyzer_energy_eV`, so a computed and a measured XRS spectrum can be matched on q.
- Warnings: `XRS_TECHNIQUE_MISMATCH` (drafted as `TECHNIQUE_OPERATOR_MISMATCH`), `XRS_WITHOUT_Q` (computed: the
  transition block; measured: the detection block), `QUADRUPOLE_WITHOUT_WAVE_VECTOR`, `Q_CONVERSION_MISMATCH` (the
  value as written, converted, differs from the inverse-angstrom value by more than 1%; drafted at 0.5%). XRS joins the X-ray spectra the energy-axis checks
  cover.
- Deferred to the comparison endpoint (phase 2 of change 17): intensities compared across operators or across q,
  which needs two records.
- Documentation: Computation 7.7 (the block, the rule that the operator decides the technique), Measurement 3.4
  (measured q), System (XRS), contract step 9 and a new contract example, generated pages, README. Examples: the
  OCEAN Cu K example states its operator; new `examples/ocean_xrs_record.json` (O K XRS of CuO at 1.85 inverse bohr =
  3.496 inverse angstrom, with its OCEAN input and photon files inline).

## Declared flips (gate 0) and battery (gate 1, 2026-11-05, 4,796 records)

- No stored record carries the new fields or the XRS technique: no change expected.
- Measured: 0 outcome changes, no code gained or lost.
- Tests: `tests/test_transition_operator.py` (14): the examples clean; XRS and the operator list served; each
  mismatch caught; XRS without q caught for a calculation and for a measurement; a quadrupole without k caught, a
  dipole needs none; q slips in both directions caught, correct conversions (inverse bohr, inverse nm) pass; a
  computed XRS spectrum without an energy axis is flagged like any computed X-ray spectrum. Suite 1028 passed; six
  wiki checks pass.

## Review (gate 2)

codex, gemini and grok, blind. All confirmed the operators and the conversion (1.85 inverse bohr = 3.496 inverse
angstrom). Adopted:
- The operator constrains the technique and does not rename absorption: `dipole` and `dipole_quadrupole` keep the
  absorption technique the work names (XAS, XANES, EXAFS, HERFD-XAS); `momentum_transfer` under any technique but
  XRS is a mismatch; a measured absorption record with a momentum transfer or scattering angle is flagged (sources
  call XRS "XAS by inelastic scattering"). XRS is also called NIXS or NRIXS. The code was renamed
  `XRS_TECHNIQUE_MISMATCH` (codex, gemini, grok).
- Physics wording: at low q the XRS shape is dipole-like and the intensity grows as q^2; at higher q the monopole
  (s to s at a K edge) and higher multipoles contribute; `dipole_quadrupole` is E1+E2; for XRS the operator depends
  on q and the incident energy sets the kinematics; energy loss is E_in - E_out; |q| = |k_in - k_out|, computed from
  2theta and the energies when only those are given, and never the crystallographic s = |q|/2pi (codex, gemini, grok).
- `XRS_WITHOUT_Q` fires on the momentum-transfer operator whatever the label (codex); `q_as_written` only when the
  source writes one (codex, gemini, grok); the tolerance is 1% so a value rounded to its reported precision passes
  (codex).
- `QUADRUPOLE_WITHOUT_WAVE_VECTOR` asks for e and k on every vector, e perpendicular to k, and `energy_eV` (gemini,
  grok); never invent a direction.
- `vectors_frame` (cartesian | crystal) and, for a measured single crystal, `detection.q_direction_hkl` (grok).
- The example's name said "2x2x2 supercell" for an 8-atom conventional cell (grok).

Not adopted: that the (i/2)(e.r)(k.r) term contains the magnetic dipole (grok): r_i r_j is symmetric, so only the
symmetric part of e(x)k contributes and the term is the electric quadrupole, as codex stated. A required
`q_as_written` (codex): it is written only when the source writes one. Hard validation of every vector
(normalization, crystal-frame conversion): beyond what the sources state.

## Agents (gate 3)

Fresh agents from three vendors, given only the new Computation, Measurement, System and Record-Granularity pages,
wrote the technique and the transition or detection block from three short sources: a computed XRS run (OCEAN
qRaman, "length 1.85 inversebohr"), a computed Cu K XAS with quadrupole terms (three (e, k) pairs), and a measured
XRS report (2theta 120 degrees, analyzer 9.69 keV, q 8.5 inverse angstrom). Key written before any run.

| Item in the key | codex | gemini | grok |
|---|---|---|---|
| A: technique XRS, operator momentum_transfer, q 3.496 (3.5) inverse angstrom, as written 1.85 inverse_bohr, three q vectors, 9690 eV, keyword only in notes | yes | yes | yes |
| B: technique XAS, operator dipole_quadrupole, three (e, k) pairs as stated, 8979 eV | yes | yes | yes |
| C: technique XRS, detection q 8.5, angle 120, analyzer 9690 eV, no invented vectors | yes | yes | yes |

Changed after the run: the transition table lists `notes`, and the vectors row shows the JSON shape (grok, gemini).

## Ship and watch

SHIP_PENDING (with change 22). Then the co-designer's skill writes the transition block (his work), and the measured XRS records
state q.
