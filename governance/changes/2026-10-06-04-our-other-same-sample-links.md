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

## Parts b and c (done 2026-10-06)

An agent read every group against its notes, records and open-access sources, and wrote one decision per
group with evidence (`followup_2026-10-04/change04bc/decisions.jsonl`). The decisions were reviewed before
any edit:
- **b (the source establishes identity without a usable identifier): 77 links kept**, with basis
  `unspecified` and the establishing passage in `notes`. Kistler et al. 2026, Fig. 1 is one continuous 3 h
  run of one cell. Our SSRL IrOx series is one JK1C electrode at successive potentials. Two Crumlin-group
  XPS sessions list their scans under logbook sheets 'Sample_1' and 'Sample_2'.
- **c (nothing establishes identity): 142 links removed.** These cover scans the logbook does not list, and
  literature groups whose notes name only a material and a condition. One paper states that each
  experiment was run at least three times. Two papers are not open access, so for those groups the source
  was not checked; a link can be restored if the source establishes identity.
- **Moved from a to b:** the agent proposed writing 'Sample_1' and 'Sample_2' into `sample_id`. We kept
  links instead, because the record graph scopes a local identifier by organization, and a generic label
  would merge with any other LBNL 'Sample_1'.

Declared: 164 records edited; unsupported bases 236 to 17 (all from other uploaders); `same_sample_as`
links 1,989 to 1,847; no outcome changes.

Battery (2026-10-06T1832Z to 2026-10-06T1833Z):
- unsupported bases: 236 to 17, of which changzhiai has 3 and haochen_slac 14;
- `same_sample_as` links: 1,989 to 1,847;
- one-way links: 179 to 125;
- sample groups: 304 to 296;
- outcome changes: 0;
- 164 edited.

Our records now hold no unsupported `same_sample_id` bases. On 2026-10-02 they held 7,731.

## Rule gaps found, for later changes

- **Local identifier scope.** The Links page says a local identifier is read within its producer, but the
  record graph scopes it by organization. A generic label ('Sample_1') would merge across groups of one
  institution. Proposal: scope local identifiers by organization and group, after measuring the effect on
  sample groups.
- **A batch code that names the batch's only electrode** (JK1C) is not covered by the rule.
- **Our curation of another lab's logbook** is not clearly the source's statement.
- **How many links to keep in a group of more than two:** a star to one record, or a chain. The page
  should say.
- **A value averaged over several runs** may not belong to one physical object.
- **Decision c cannot tell "not established" from "not checked"** when the source cannot be read.
