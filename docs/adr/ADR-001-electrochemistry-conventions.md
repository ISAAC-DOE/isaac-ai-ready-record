# ADR-001: Electrochemistry data conventions (decided by D. Sokaras, 2026-06-13)

## 1. Current sign convention: IUPAC, signed
Cathodic (reduction) currents are NEGATIVE; anodic (oxidation) currents are
POSITIVE. Applies to every current-valued field: current_setpoint_mA_cm2,
steady_state_current_density, partial_current_density.*, limiting/exchange
current densities, and current channels in measurement.series.
Rationale: "The cathodic current must be negative. This should be clear for
electrochemistry." Production was a 50/50 coin-flip correlated with which
lab uploaded — that ambiguity ends here.
Enforcement: SIGN_CONVENTION warning now; error after Migration B flips the
legacy positives.

## 2. Faradaic efficiency is a DERIVED CLAIM, never a measurement
The measurement is the GC trace, the NMR spectrum, the current. The
conversion of those signals to faradaic efficiency is interpretation —
a descriptor. Therefore:
- Single FE values: descriptors ONLY (faradaic_efficiency.{PRODUCT},
  unit fraction). A one-point measurement.series channel duplicating an FE
  descriptor is an anti-pattern.
- FE vs time/potential traces: permitted in measurement.series, but the
  channel role MUST be 'derived_signal' (never 'measured_response') and
  channel names must use canonical product tokens.

## 3. cell_type = cell BODY; wiring lives in electrode_configuration
cell_type answers "what physical cell architecture" (h_cell divided or
undivided, flow_cell, mea_cell, scanning_droplet_cell...).
"three_electrode" is wiring, not a body — its home is
system.configuration.electrode_configuration. Existing three_electrode
values: the 1,001 JCAP records become scanning_droplet_cell in their
migration; remaining records get a warning until each is mapped to its
actual body. The enum keeps 'three_electrode' as deprecated-accepted until
that burn-down completes.
