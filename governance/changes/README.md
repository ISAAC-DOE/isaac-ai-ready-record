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
| [2026-10-06-08](2026-10-06-08-examples-teach-the-rules.md) | 2026-10-06 | The examples teach what the rules say: no invented checksums, no shared-batch links, links written once and directed | wiki, examples | 0, 1, 2 (two rounds) | #271 and the wiki, 2026-10-06 | shipped |
| [2026-10-06-09](2026-10-06-09-checksum-and-shared-basis-warnings.md) | 2026-10-06 | Warnings for a checksum that is none (842 records) and same_sample_as on a shared basis (45) or without its passage | validator, wiki check | 0, 1, 2, 4 | #271, v0.0.353 | shipped |
| [2026-10-06-10](2026-10-06-10-our-checksums-and-batch-links.md) | 2026-10-06 | Our checksums (74 records), our shared-batch links (42) and our reciprocal comparison links (50) | data | 0, 1, 4 | data edits, 2026-10-06 | done: 74 checksums, 42 links removed, 118 comparison links decided (25 kept, 12 retyped) |
| [2026-10-06-11](2026-10-06-11-contract-comparison-links.md) | 2026-10-06 | The contract stops teaching same-study comparison links (contract 2026-10-06.1); only same_sample_as is symmetric | contract, examples, API | 0, 1, 2 | #272, v0.0.354 | shipped |
| [2026-10-06-12](2026-10-06-12-contract-release-1.md) | 2026-10-06 | Contract release 1: open and closed vocabularies; a checksum describes the bytes at the asset's URI (contract 2026-10-06.2) | contract, messages, wiki | 0, 1, 2, 3 | #273, v0.0.355 | shipped; gate 3: two agents better; the third within its spread except a quoted-baseline case, closed by change 14 |
| [2026-10-06-13](2026-10-06-13-same-sample-link-on-sample-id.md) | 2026-10-06 | Warning: same_sample_as on basis same_sample_id (952 records) | validator | 0, 1, 2, 4 | #275, v0.0.357 | shipped |
| [2026-10-06-14](2026-10-06-14-quoted-baseline.md) | 2026-10-06 | Step 5: a quoted baseline is not this work's result (contract 2026-10-06.3) | contract | 0, 1, 2, 3 | #276, v0.0.358 | shipped; gate 3: quoted rows 2 of 2 runs to 0 of 2 |
| [2026-10-09-15](2026-10-09-15-record-form-for-catalysis-labs.md) | 2026-10-09 | The portal's record form can enter a catalysis lab's result | portal | 0, 1, 2 | #278, v0.0.360 | shipped |

Drafts not yet started: [PROPOSED.md](PROPOSED.md).

## Before the ledger (2026-09-27 to 2026-10-04)

Recorded in their pull requests and the audit pages rather than here: #255 to #258 (hold-tier detectors and
held records), #259 (review-as-source and functional warnings; lab keys), #260 (warnings on stored records in
`/records/attention`), #261 (the contract and wiki teach what the validator enforces; contract 2026-10-02.1),
#262 (the steward battery).
