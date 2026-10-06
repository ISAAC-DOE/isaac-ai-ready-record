# 2026-10-06-09 Two warnings: a checksum that is none, and same_sample_as on a shared basis

- **Product:** repository. **Kind:** validator (two new warnings), the example records, and the wiki check.
  **Owner:** D. Sokaras (PI).
- **Found by:** the agent benchmark baseline (three readings of what a checksum describes; an agent writing
  `same_sample_as` with basis `shared_material_batch` 21 times), then a measurement of the stored records.
  The contract already said "a checksum of zeros is never written"; no check enforced it.

## Problem

On the 2,994 stored records (live snapshot 2026-10-06T195904Z):

| Defect | Records | Uploaders |
|---|---|---|
| An asset checksum of zeros, or a word in its place ("pending", "generated_at_extraction_time") | 842 | changzhiai 674, ours 74 (71 early, 3 recent), haochen_slac 56, mahajan 38 |
| `same_sample_as` whose basis names something different specimens can share (a material batch, a replicate preparation, an analysis method, an absorber edge, operating conditions, a reference state) | 45 | ours 42, changzhiai 2, pbasera 1 |

The repository taught both. The tutorial's record (also `examples/co2rr_performance_record.json`) carried
three invented checksums, two of them not hexadecimal, and a `same_sample_as` link on "identical GDE batch".
The other example records carried invented checksums (one is the SHA-256 of an empty file, others are 45
characters long), and the operando example linked to the tutorial's record on a shared batch. The control
record of the adversarial tests carried zeros.

## Change

- **CHECKSUM_NOT_SHA256** (warning): an asset's `sha256` is neither 64 hexadecimal characters nor a
  placeholder (`not_available_literature_source`, `not_available`), or it is one character repeated, such as
  all zeros.
- **SAME_SAMPLE_ON_A_SHARED_BASIS** (warning): `same_sample_as` on a basis that names something different
  specimens or models can share: the six above, `matched_computational_method`, `same_workflow_version`,
  `analysis_pipeline_output`, and, for a measured specimen, `identical_geometry`. Not flagged:
  `same_sample_id`; `identical_geometry` on a calculation (an identical model); `unspecified`.
- **SAME_SAMPLE_WITHOUT_PASSAGE** (warning): `same_sample_as` with basis `unspecified` and empty notes. The
  first warning's remedy is basis `unspecified` with the establishing passage; without this check, changing
  the basis alone would silence it.
- **Examples:** corrected in change 08, in the same pull request, because every example must pass these
  warnings. The control record of the adversarial tests takes `not_available_literature_source` for its
  cited paper instead of zeros.
- **Wiki check:** a complete record shown in the wiki is copied as shown, so `generate_validation_docs.py
  --check` now fails when such a record carries any warning, or differs from the file in `examples/` with
  the same `record_id`. On the published wiki it reports the tutorial's two defects.
- **Docs generator:** it found the codes in `validation.py` with the pattern `[A-Z_]+`, so a code containing a
  digit (`CHECKSUM_NOT_SHA256`) was missing from Validation-Rules and from its own "undocumented code" guard.
  The pattern now admits digits.

## Flip list (declared before the battery ran)

- Stored records: CHECKSUM_NOT_SHA256 gained on the 842 records above, SAME_SAMPLE_ON_A_SHARED_BASIS on the
  45; no other code and no outcome changes (both are warnings, not holds).
- Must pass: a SHA-256 in either case, both placeholders; `same_sample_as` with `same_sample_id`, with
  `identical_geometry` on a calculation, or with `unspecified` and a passage; `intended_comparison_target`
  with a shared basis; every example.
- Must catch: 64 or 40 zeros, 64 f's, 64 zeros followed by a newline, "pending", "TBD", the tutorial's
  non-hexadecimal values, a 45-character value; `same_sample_as` with each shared basis, with
  `identical_geometry` on a measurement, with `unspecified` and empty notes; a wiki record with a warning or
  drifted from its file.
- SAME_SAMPLE_WITHOUT_PASSAGE and the added bases fire on no stored record.

## Gate 1: battery

Replay of 2026-10-06T195904Z, main's validator then this change: 2,994 records, 0 outcome changes;
CHECKSUM_NOT_SHA256 gained on 842 (changzhiai 674, ours 74, haochen_slac 56, mahajan 38), lost on 0;
SAME_SAMPLE_ON_A_SHARED_BASIS gained on 45 (ours 42, changzhiai 2, pbasera 1), lost on 0. Every example
record is clean. After review the battery was run again on the revised checks with the same result, and
SAME_SAMPLE_WITHOUT_PASSAGE gained on 0. Tests: 941 passed.

## Gate 2: review

Codex, Grok and Gemini, blind.

- **All three:** none of the six bases establishes one specimen; keep both as warnings until precision is
  sampled; add `matched_computational_method`, `same_workflow_version` and `analysis_pipeline_output`, which
  name what separate models share. Added.
- **Grok:** `identical_geometry` establishes identity only for a calculation; two measured objects can share
  a shape. Flagged on measurements only.
- **Codex and Grok:** the remedy reopens the hole: basis `unspecified` with empty notes is silent. Added
  SAME_SAMPLE_WITHOUT_PASSAGE.
- **Codex:** `^...$` in Python accepts a trailing newline, so 64 zeros plus a newline passed. Now
  `fullmatch`. Gemini: one character repeated (all f's) is a placeholder too. Folded in.
- **Empty-file digest** (the SHA-256 of zero bytes): Codex and Gemini would flag it; Grok would not, because
  an empty file has that hash and the rule allows it. Not flagged: the check reads the form of a checksum,
  and no format check can see the bytes. It fires on no stored record.
- **Messages:** zeros are 64 hexadecimal characters, so "neither a SHA-256" was wrong (Codex, Grok); "2
  asset checksum(s)" is not a sentence (Grok); the link message always mentioned a batch, never the
  calculation half of the rule, and told uploaders to keep a link that a shared `sample_id` makes redundant
  (all three). Rewritten.
- **Not taken now:** a warning for `not_available_literature_source` on an asset that is no paper (Gemini);
  it fires on no stored record.

## Ship and watch

Pending. Our own 74 and 42 records are corrected as change 10.
