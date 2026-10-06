# Change ledger

One file per change to the repository of knowledge, written before it ships and completed after: the problem,
the declared flips, the battery before and after, what each reviewer said, where it shipped, and what to watch.
The process is in [`../README.md`](../README.md).

| Id | Date | Change | Kind | Gates | Shipped | Status |
|---|---|---|---|---|---|---|
| [2026-10-06-01](2026-10-06-01-links-are-stated-not-inferred.md) | 2026-10-06 | Links are stated, not inferred: NO_LINKS removed; all eight relations defined | validator, wiki | 0, 1, 2 (3 not applicable) | #263, v0.0.345 | shipped |
| [2026-10-06-02](2026-10-06-02-remove-auto-linker-links.md) | 2026-10-06 | Remove 7,418 auto-written same_sample_as links from 151 of our records | data | 0, 1, 2, 4 | data edit, 2026-10-06 | done: 143 records edited, 6,786 links removed |
| [2026-10-06-03](2026-10-06-03-remove-ten-records.md) | 2026-10-06 | Remove 10 of our records that hold no result | data | 0, 1, 4 | data edit, 2026-10-06 | done: 10 records removed, archived |
| [2026-10-06-04](2026-10-06-04-our-other-same-sample-links.md) | 2026-10-06 | Our other 313 unsupported same_sample_as links: labels into sample_id, then parts b and c | data | 0, 1, 2 (rule), 4 | data edit, 2026-10-06 | done: 37 + 164 records; our unsupported same_sample_id bases 7,731 to 0 |
| [2026-10-06-05](2026-10-06-05-same-sample-identity-without-identifier.md) | 2026-10-06 | same_sample_as for an identity the source establishes without naming the specimen | wiki | 0, 1, 2 | wiki, 2026-10-06 | shipped |
| [2026-10-06-06](2026-10-06-06-lab-and-technique-out-of-a-descriptor-name.md) | 2026-10-06 | A lab and a technique out of a descriptor name (97 of our records) | data | 0, 1, 4 | data edit, 2026-10-06 | done |
| [2026-10-06-07](2026-10-06-07-modulation-check-skips-cited-titles.md) | 2026-10-06 | The modulation check stops reading cited titles (21 false firings); the battery counts info codes | validator, steward | 0, 1, 2, 4 | #268, v0.0.350 | shipped |
| [2026-10-06-08](2026-10-06-08-examples-teach-the-rules.md) | 2026-10-06 | The examples teach what the rules say: no invented checksums, no shared-batch links, links written once and directed | wiki, examples | 0, 1, 2 (two rounds) | pending | in review |
| [2026-10-06-09](2026-10-06-09-checksum-and-shared-basis-warnings.md) | 2026-10-06 | Warnings for a checksum that is none (842 records) and same_sample_as on a shared basis (45) or without its passage | validator, wiki check | 0, 1, 2, 4 | pending | in review |

Drafts not yet started: [PROPOSED.md](PROPOSED.md).

## Before the ledger (2026-09-27 to 2026-10-04)

Recorded in their pull requests and the audit pages rather than here: #255 to #258 (hold-tier detectors and
held records), #259 (review-as-source and functional warnings; lab keys), #260 (warnings on stored records in
`/records/attention`), #261 (the contract and wiki teach what the validator enforces; contract 2026-10-02.1),
#262 (the steward battery).
