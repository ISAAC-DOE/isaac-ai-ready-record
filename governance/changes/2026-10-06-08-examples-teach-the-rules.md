# 2026-10-06-08 The examples teach what the rules say

- **Product:** repository. **Kind:** wiki wording and the example records. **Owner:** D. Sokaras (PI).
- **Found by:** the agent benchmark baseline. One agent learning only from the documentation wrote 21
  `same_sample_as` links on basis `shared_material_batch` between aliquots of one enzyme preparation; the
  agents wrote checksums three different ways. Both patterns are in the examples.

## Problem

The Links rules were rewritten on 2026-10-06 (change 01, change 05); the examples on other pages were not.
The tutorial tells readers to "copy the example below, replace the values, validate, submit", and its record
(also `examples/co2rr_performance_record.json`, served by the contract) taught:

- a `same_sample_as` link on basis `shared_material_batch`, "Characterization performed on identical GDE
  batch", where the rule says specimens from one batch are different specimens;
- three invented checksums, two of them not hexadecimal.

The Assets page used `"sha256": "TBD"`. The Ecosystem page drew `same_sample_as: shared_material_batch` in both
directions and called links "a reciprocal enrichment layer". The Record-Granularity worked example linked two
potentials on one electrode with `same_sample_as` (the rule puts one specimen in `sample_id`), linked
performance records `derived_from` a characterization record for "same starting material", and linked three
samples `intended_comparison_target` "of each other". The other example records carried invented checksums
(one the SHA-256 of an empty file, others 45 characters long) and the operando example linked to the
tutorial's record on a shared batch.

## Change

- Tutorial and `examples/co2rr_performance_record.json` (now identical, which the wiki check enforces, see
  change 09): `sample_id` names the electrode, the batch code moves to `sample.material.identifiers`, no
  link, every checksum `not_available`. Notes after the example say what `sample_id` is in terms that cover
  any science, show a link template without adding a link the source does not state, and say how to write a
  checksum.
- Assets page: the example checksums are `not_available`, with how to hash a file you hold.
- Ecosystem page: the two records of one electrode share its `sample_id` and need no `same_sample_as`; each
  comparison link is written once, on the record whose source assigns the reference; every relation except
  `same_sample_as` has a direction.
- Record-Granularity: one specimen in `sample_id`; `same_sample_as` only when the records cannot share one
  identifier; `derived_from` on the produced specimen's record, pointing to its immediate parent; comparison
  references only where the source assigns them; a calibration standard is `calibration_of`.
- The four other example records: no invented checksum, no shared-batch link, each comparison declared once.

## Gate 1

Wording and examples only; no validator change in this entry. Every complete record shown in the wiki
validates with no warning and equals its file in `examples/` (`generate_validation_docs.py --check`).

## Gate 2: review

Round 1, Codex, Grok and Gemini, blind. All three: contradictions remained.
- **Checksums (all three):** digests of the URI strings, which round 1 used as illustrations, are false
  checksums that look real and teach hashing the URI; worse than "TBD". Codex and Grok: `not_available` with a
  note to hash files you hold. Gemini: a real digest of a file the repository ships. Taken: `not_available`.
- **Record-Granularity (Grok, Gemini):** the second bullet still prescribed `same_sample_as` where the rule
  puts one specimen in `sample_id`. Codex: `derived_from` needs its direction and the immediate parent.
- **Ecosystem (all three):** "read from both ends" brings reciprocal links back; "computed to compare with" is
  wider than the rule's assigned reference; Codex: a standard used for calibration is `calibration_of`.
- **Tutorial (all three):** "`sample_id` names the physical electrode" invites other sciences to put a batch,
  aliquots or a cell line there. Gemini: with the link removed, the tutorial shows no link template.
  Grok: drop "easy to copy wrongly".

All points were taken.

Round 2, on the revised text. Checksums: all three found no remaining conflict. Links:
- **Codex and Grok:** "with no link" and "they are not linked" are too absolute. A shared `sample_id` removes
  only the need for `same_sample_as`; `replica_of`, `follows` or `derived_from` may still hold, and
  specimens from one batch are linked when the source states a relation. `same_sample_as` also applies when
  the identifiers differ or the other record cannot be edited, not only when the source gives none.
- **Grok:** for a calculation, `sample_id` names one model (the same structure and settings).
- **Grok and Gemini:** "write it once, on one of them" ignores direction: a directed link goes on the record
  the relation names, and a comparison on the record that uses the reference.
- **Gemini:** say what to do when the source gives no identifier, and list what is never a `sample_id`.

All taken.

## Ship and watch

- Shipped: the wiki pages on 2026-10-06 (wiki commit 3f817e9) and the example records in #271 (portal
  v0.0.353).
- Watch: `generate_validation_docs.py --check` now fails when a complete record shown in the wiki carries a
  warning or differs from its file in `examples/`, so the tutorial cannot drift from its example again.
