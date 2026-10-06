# 2026-10-06-02 Remove 7,418 auto-written same_sample_as links from 151 of our records

- **Product:** repository. **Kind:** data (our own records). **Owner:** D. Sokaras (PI), approved 2026-10-06.
- **Analysis:** "Seven Claims, 7,418 Links" (the audit page of 2026-10-04).

## Problem

In June, a script that prepared our literature records linked every pair of records from the same paper
with the same sample description. Each link is `same_sample_as`, basis `same_sample_id`, with the note
"Auto-link: records share material/electrode from the same publication". Three papers produced seven
complete cliques, 151 records and 7,418 links (k(k-1) per group).

No record states a `sample_id`. The papers do not support one physical specimen (Edison, PaperQA3, read
their methods):
- **Li et al. 2019 (80 records):** no electrode is identified, so identity is undetermined.
- **Nature Chemistry 2025 (24 records):** the paper reports "different electrodes" and "three independent
  samples".
- **Nature Catalysis 2022:** four Ba loadings were prepared, and the records do not say which they hold.
- **Two of the groups are models:** one model group, and four DFT slabs at four CO coverages, which are four
  models.

## Change

Remove exactly the links whose note begins "Auto-link", from both ends, and nothing else. Each record gets
one edit with this change note: "Removed N same_sample_as links written in June 2026 by an automated
linker from a shared paper and material name (basis same_sample_id, but no record states a sample_id, and
the source does not identify one specimen)." The previous versions are kept.

## Declared flips (gate 0)

- The 151 records gain one version each and keep their outcome (143 publish, 8 reject).
- Links written by the auto-linker: 7,418 to 0.
- Unsupported `same_sample_id` bases: 7,748 to 330.
- The seven auto-linked sample groups dissolve. After removal, the 151 records hold no links at all
  (simulated).
- Sample groups that the battery flags as conflicting or as mixing a model with a physical sample: no
  change, since those belong to another uploader's records.

## Battery (gate 1)

Simulated on the 2026-10-02 snapshot: 143 publish and 8 reject, both before and after; 0 links left on the
151. The before-and-after battery runs are recorded here after the change is applied.

## Reviews (gate 2)

Codex, Grok and Gemini, blind, 2026-10-06. All three said to remove the links, with no relabel such as
`shared_material`: the shared paper and material are already on the records. Their requirements, all taken:
- remove by provenance, from both ends, and only those edges;
- check that no other edge still binds the groups;
- say in each change note what was removed and why;
- list the remaining 330 unsupported bases and the other uploader's 569 "same study" links for disposition.

Disposition of the rest:
- **Ours (313):** change 2026-10-06-04, next.
- **Another uploader's (17 unsupported bases, 569 "same study" links):** their owner's attention page, once
  the link warning ships.

## Consumers

Five past Cu-Au CO2RR projects on the discovery platform cite 14 to 19 of these records each. Their verdicts
pin record versions and sample groups, so their scores stay reproducible. Their briefings will flag the
changed evidence at re-evaluation.
