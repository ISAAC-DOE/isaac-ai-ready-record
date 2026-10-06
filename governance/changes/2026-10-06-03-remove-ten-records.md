# 2026-10-06-03 Remove 10 of our records that hold no result

- **Product:** repository. **Kind:** data (our own records). **Owner:** D. Sokaras (PI), approved 2026-10-06.

## Problem

Ten of our records fail the current rules and cannot be repaired from what we hold.

- **8 records from Li et al., Nature Catalysis 2019** (operando XAS and XRD of oxide-derived Cu):
  - their only stored value is the CO feed percentage, which is a condition, not a result;
  - their producer would be the paper's group (Sinton and Sargent), not ours;
  - the paper is not open access, so the results cannot be re-extracted here.
- **2 neutron-reflectometry records from SNS proposal IPTS-34347,** an experiment of M. Doucet (ORNL), who
  uploaded his own records from it:
  - run 218393 duplicates his record value for value;
  - run 218386 holds only the scan's q-range and point count, with no fitted result.

## Change

Delete the ten records through the admin API, after this change and change 02 ship. Every deleted version
is archived in `record_history`, and a local copy is kept.

## Declared flips (gate 0)

- 10 fewer records.
- Our failing records go from 10 to 0.
- No other record changes, because no other record links to these after change 02.

## Battery (gate 1)

Recorded after the change is applied.
