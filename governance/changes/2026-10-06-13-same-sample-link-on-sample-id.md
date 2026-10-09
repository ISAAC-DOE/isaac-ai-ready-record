# 2026-10-06-13 A warning for a same-specimen link that rests on the shared identifier

- **Product:** repository. **Kind:** validator (one new warning). **Owner:** D. Sokaras (PI).
- **Found by:** reading one uploader's afternoon batch (574 records, after contract 2026-10-06.1 went live), which
  still wrote the patterns the old contract taught; no warning flagged them, so the upload responses carried no
  signal.

## Problem

The Links rule says: "`same_sample_id` means the two records state the same `sample.sample_id`, which already
makes them one specimen, so such a link adds nothing." On the 3,568 stored records (live snapshot
2026-10-06T220616Z), 952 records carry 2,105 `same_sample_as` links on that basis (one uploader 941 records, two
labs 6 and 5). In 2,086 links both records state the same identifier; in 19 they do not, so the basis is false.
The one uploader's identifiers are built from a paper slug and a material name, so removing its links alone
would leave records of different specimens merged by one identifier.

## Change

SAME_SAMPLE_LINK_ON_SAMPLE_ID (warning), one per such link, with its path. It reads only
the record: the basis is on this record, and the rule rejects it without looking at the target. The message
orders the outcomes: identical identifiers, remove the link; different or missing ones, write the identifier the
source assigns on both and remove the link, or keep the link with basis `unspecified` and the establishing
passage when the records cannot share one, or else remove it. It ends with what is never a `sample_id`: a
material name, formula, cell line, strain, catalog number, batch code or paper slug.

## Flip list

- Stored records: the warning gained on 952 (941, 6, 5); no outcome changes; no other code changes.
- Must pass: `same_sample_as` on any other basis; `intended_comparison_target` with basis `same_sample_id` is not
  this warning's case.
- Must catch: one link; two links (two warnings, each with its path).

## Gate 1

Replay of 2026-10-06T220616Z: 0 outcome changes; SAME_SAMPLE_LINK_ON_SAMPLE_ID gained on exactly 952 records
(941, 6, 5), lost on 0. Tests: 943 passed.

## Gate 2: review

- **Codex and Grok:** a record-local warning is right: the basis sits on this record and the rule rejects it with no
  lookup. Codex would also resolve each target at the API to say which case applies per link; listed in
  PROPOSED.md with the check for links declared both ways.
- **Both:** no case makes such a link useful; a reader that ignores identifiers is a reader defect.
- **Grok:** for the uploader whose identifiers are a paper slug plus a material name, "already one specimen" is
  false; the message must say what is never a sample_id and order the outcomes. **Codex:** say
  `sample.sample_id`, allow for a target the uploader cannot edit, and include removing the link when no passage
  establishes identity. All taken.
- **Gemini** (after its quota reset): record-local is right; one warning per link, each with its path, instead of
  one per record (Codex also found the per-record message ambiguous for several links). Changed. Its wording
  points (the calculation case, a target that cannot be edited, removing the link without evidence) were
  already in the revised message.

## Ship and watch

- Shipped in #275 (portal v0.0.357, 2026-10-06), after the benchmark run then in progress had finished.
- Live: `/records/attention` shows the warning on 944 records of one uploader (it uploaded four more records
  after the snapshot) and 5 of another; a third lab's 6 records are rejected for other errors, so the warning
  appears in their quality reports.
- Watch: whether that uploader's next batches stop writing these links.
