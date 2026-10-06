# 2026-10-06-11 The contract stops teaching same-study comparison links

- **Product:** repository. **Kind:** record contract (version 2026-10-06.1), its worked examples, the API's
  neighbor listing, and the wiki. **Owner:** D. Sokaras (PI).
- **Found by:** reading one uploader's link notes while fixing the examples in change 08.

## Problem

The Links rule (change 01) defines `intended_comparison_target` as a control, reference or baseline that the
source explicitly assigns, and makes every relation except `same_sample_as` directed. The contract, written
before it, still said:

- step 4: a measured and a computed value from one paper are linked "with intended_comparison_target when one
  bears on the other";
- step 5: a comparison between samples is one record per sample, "linked with intended_comparison_target".

Its worked examples linked two catalysts of one paper to each other ("The other catalyst of the same study,
measured under the same conditions"), linked a calculation to a measured catalyst that the paper "explains"
with it, and gave literature samples identifiers built from a paper slug and a label
("example-ammonia-paper/catalyst-A"). The API's neighbor listing marked `intended_comparison_target` and
`replica_of` as symmetric.

What it produced (live snapshot 2026-10-06T203510Z, 2,994 records):

- 2,359 `intended_comparison_target` links; 2,274 are declared in both directions (changzhiai 2,224, ours 50)
  and 2,326 join records of one source.
- Of changzhiai's comparison links, 1,018 notes read "A different sample in the same study. Linked per
  contract step 5" and 690 echo the example's wording; the uploader's sample identifiers follow the
  paper-slug/label shape of the example.

## Change

- Step 4 (after review): link a measurement and a calculation only with a relation the source states:
  `intended_comparison_target` on the record that uses the other as a control, reference or baseline the
  source explicitly assigns; `derived_from` for a value computed from the other result; `validates` or
  `invalidates` for a calculation made to test the measurement, by the outcome the source reports ("consistent
  with" is not agreement). A calculation that only explains a measurement takes no link.
- Step 5 (after review): samples shown side by side take no link; when the source explicitly assigns one sample
  as the control, reference or baseline of another, the record that uses it carries
  `intended_comparison_target` to it, with that passage in notes.
- The "never a record" rows for a comparison stored as a value and for an activity, a characterization and a
  calculation in one record no longer prescribe links.
- Examples: the invented sample identifiers go; the calculation and catalyst B carry no link; catalyst A
  carries one `intended_comparison_target` to catalyst B with the example passage that names B as its
  baseline.
- The neighbor listing marks only `same_sample_as` symmetric (Query-API page updated).

## Flip list

- Validator: unchanged; no stored record changes outcome or codes.
- Contract tests: the literature examples carry no link and no `sample_id`; the steps state when no link is
  written; only `same_sample_as` is symmetric.

## Gate 1

Replay of 2026-10-06T203510Z before and after: 0 outcome changes, no code changes. Tests: 942 passed after the
review changes.

## Gate 2: review

Codex, Grok and Gemini, blind.

- **All three:** do not rely on a DOI to group a source's records. Many sources have none; the repository
  groups by DOI, a supporting-information DOI, a title and year, or a database collection. The DOI sentences
  were removed.
- **Codex and Grok:** the steps must say the source *explicitly* assigns the role, and must keep
  `derived_from` (a value computed from the other result) and `validates` (made to test, agreement reported);
  "explains" only; "consistent with" is not agreement. Taken.
- **Codex and Gemini:** an example with no link at all teaches no syntax and no direction; keep one link the
  source explicitly assigns, on the record that uses the reference. Grok preferred a new example (a catalyst
  and its uncoated support). Taken in the existing pair: catalyst A, the paper's subject, links to the
  conventional catalyst B that the example passage names as its baseline.
- **Gemini:** the table row "Three records, linked" (an activity, a characterization and a calculation in one
  record) still prescribed links. Fixed.
- **All three:** `replica_of` is directed: a technical replicate repeats the target; a symmetric flag would
  say the original repeats the replicate. Gemini: concurrent replicates then need an arbitrary target, and a
  query for all replicates reads both directions.

## Gate 3

A contract change is documentation. It makes the contract agree with a rule already adopted and reviewed
(change 01), so it ships on gates 0 to 2, and the next agent benchmark run measures it with the documentation
release. The baseline agents wrote 109 `intended_comparison_target` links, all between records of one paper;
that count is the measure to watch.

## Not done here

The stored links are not touched. Ours (50 reciprocal comparison links) are corrected as a data change with
the source read for each. Other uploaders learn the rule from the contract; a check for a directed link
declared in both directions needs the target record, so it would run at the API like LINK_TARGET_NOT_FOUND
(proposed).
