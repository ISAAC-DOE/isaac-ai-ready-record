# 2026-10-06-05 same_sample_as for an identity the source establishes without naming the specimen

- **Product:** repository. **Kind:** wiki wording. **Owner:** D. Sokaras (PI).

## Problem

Change 01's definition said: "If it gives none, invent neither an identifier nor a link." Read literally,
this forbade recording a true identity that the source establishes without a label ("all Figure 1 data come
from the same continuously operated cathode"). It also conflicted with the case of a target record that
cannot be edited.

## Change

The definition on the Links page now reads:
- **Shared identifier:** when the cited source assigns one identifier and both records can store it, write
  it in `sample_id` and add no link.
- **Established identity, no shared identifier:** when the source establishes the identity but the records
  cannot share one identifier (none is given, they already differ, or the other record cannot be edited),
  add `same_sample_as` from this record to the other. Put in `notes` the passage or locator that
  establishes it, and invent no identifier.
- **Otherwise:** add neither.

## Gates

- **0 and 1:** no stored record changes; the change is wording only.
- **2:** Codex, Grok and Gemini, blind. All three agreed with the direction and found the conflict with
  uneditable targets; Grok's wording was taken. All three said not to mint repository identifiers: a minted
  name looks like an accession, fails for records that cannot be edited, and two agents can mint two names
  for one object.
