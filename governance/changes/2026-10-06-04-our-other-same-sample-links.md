# 2026-10-06-04 Our other 313 unsupported same_sample_as links

- **Product:** repository. **Kind:** data (our own records), in three parts. **Owner:** D. Sokaras (PI),
  approved 2026-10-06.

## Problem

After change 02, 330 `same_sample_as` links still claim the basis `same_sample_id` without the two records
stating one `sample_id`. Of these, 313 are ours, and unlike the auto-linker's, they were written from what a
paper or a lab states. The specimen's label sits in the link's note instead of in `sample.sample_id`. The
313 split by what the note establishes:

- **Part a.** 94 links on 37 records name the electrode ("Same electrode E1_PureCu tested at different
  potentials"). These are 11 LBNL Cu-Au electrodes, and each label belongs to exactly one linked group.
- **Part b.** 124 links on 69 records state the same specimen in prose ("Same JK1C electrode ...", "the same
  continuously operated Cu/PTFE cathode").
- **Part c.** 95 links on 95 records carry no note; each record points at one hub record.

## Part a (done 2026-10-06)

Following the Links page: "When the cited source assigns one identifier to that object and both records can
store it, write it in `sample_id` and add no link." The 37 records gain the label as `sample_id`, and their
94 links, now redundant, are removed.

- **Declared:** unsupported bases 330 to 236; 37 more records with a `sample_id`; the 11 groups keep exactly
  their members; no outcome changes.
- **Battery** (2026-10-06T1806Z to 2026-10-06T1808Z): unsupported bases 330 to 236; `same_sample_as` links
  2,083 to 1,989; records with a `sample_id` 1,234 to 1,271; groups 304 to 304; 37 edited; 0 outcome changes.

## Parts b and c (next)

Each group is read against its note and source:
- where the source names the specimen, apply part a's rule;
- where it establishes identity without a name, keep one link with the establishing passage in `notes` (the
  rule as corrected in change 05);
- where nothing establishes identity, remove the link.

Every edit is declared and measured as above.
