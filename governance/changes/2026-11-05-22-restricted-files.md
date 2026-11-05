# 2026-11-05-22 A licensed input is named and checksummed, never copied

- **Product:** repository (schema, validator, wiki, contract). **Kind:** schema (additive field), one warning,
  `INPUT_NOT_REACHABLE` narrowed. **Owner:** D. Sokaras (PI). **Follows:** [20](2026-11-05-20-inline-inputs.md).

## Problem

Change 20 asks a calculation to carry its input files, inline or at a public address. A VASP POTCAR is an input its
license forbids copying. One uploader's 22 VASP records attach their POTCAR, correctly, as a pointer with its
checksum and its name; `INPUT_NOT_REACHABLE` listed it among the files to inline, so the warning could never be
cleared. Found while checking the advice to that uploader (2026-11-05).

## Change

- `assets[].redistribution`: `restricted` (the file's license forbids copying it; the record names it and keeps
  its `sha256`, and a reader obtains it under the license) or `open` (the default when absent).
- `INPUT_NOT_REACHABLE` does not ask for a restricted input. `RESTRICTED_FILE_INLINED` (warning) flags a restricted
  asset that carries `content`.
- Documentation: Assets (the field, and "what never goes inline"), the `INPUT_NOT_REACHABLE` message, contract step
  10.

## Gates

- 0 and 1: no stored record carries `redistribution` (new field), so no stored record changes; the uploader's
  records lose the POTCAR from the warning once they mark it. Test
  `test_a_restricted_input_is_named_not_asked_for_and_never_inlined`.
- 2: reviewed with change 21's packet (codex, gemini, grok all named licensed pseudopotentials; change 20's review
  asked that they never be inlined).

## Ship and watch

Shipped in #300 with change 21 (portal v0.0.383). Live dry run: one of the uploader's VASP records, with its inputs
inlined and its POTCAR marked `restricted`, publishes with no warning.
