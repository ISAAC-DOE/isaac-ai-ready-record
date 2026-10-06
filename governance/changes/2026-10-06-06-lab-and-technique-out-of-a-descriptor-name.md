# 2026-10-06-06 A lab and a technique out of a descriptor name

- **Product:** repository. **Kind:** data (our own records). **Owner:** D. Sokaras (PI).
- **Found by:** the first weekly steward review, 2026-10-06.

## Problem

Our 97 LBNL XPS records named one descriptor `lbnl.xps.elements_detected`. The lab is already in
`attribution` and the technique in `system.technique`, so the name should carry only the quantity. The
validator's prefix check does not catch it, because "lbnl" is not a technique token. That gap is noted for
a later check.

## Change and result

- **Change:** the name is now `elements_detected` in all 97 records, one versioned edit each.
- **Declared:** 97 edited; no outcome changes; no warning changes.
- **Battery** (2026-10-06T191728Z to 2026-10-06T192153Z): 97 edited, 0 outcome changes, no warning changes.
- **Gate 2:** not run separately. The rule ("the name carries only the quantity") is the validator's existing
  PREFIX_IN_DESCRIPTOR_NAME text, and the edit is mechanical.

## Not done here

16 `cu.*` and 12 `lab6.*` names on four of our Rietveld XRD records came from a minimal rename on
2026-10-04 (`xrd.lab6.unit_cell.a` to `lab6.unit_cell.a`). Fixing them needs a curation decision:
- lattice parameters to the canonical `lattice_parameter` class, with the refined phase as qualifier;
- fit parameters (background coefficients, instrument broadening, scale factor) out of the descriptors and
  into the processing step.

This is listed in [PROPOSED.md](PROPOSED.md), to go through review.
