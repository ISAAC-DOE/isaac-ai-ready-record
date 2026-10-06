# 2026-10-06-12 Contract release 1: open and closed vocabularies, and what a checksum describes

- **Product:** repository. **Kind:** record contract (version 2026-10-06.2), two validator messages, two wiki
  pages. **Owner:** D. Sokaras (PI).
- **Found by:** the agent benchmark, in the baseline and again in the first run after changes 07 to 11.

## Problem

- **Step 8** told an uploader whose vocabulary lacks a term to propose it and "store the record once the term
  is added". It did not say which vocabularies are closed. Descriptor names, units and configuration keys are
  open (the validator notes an unknown one as information); only enumerated fields are closed, and several of
  those are optional. Agents that could not propose left results out: one stored 3 of a paper's 21 core values
  in the baseline, another left out the laser-diffraction sizes, an interfacial energy and a speciation fit in
  the next run.
- **Step 2** said "Hash the bytes of every file you hold". Agents held a text extract of the paper and cited the
  paper by its DOI. They wrote the extract's hash on the DOI asset, the literature placeholder, or a second
  asset pointing at a path on their own disk. Change 09's warning defines the checksum as the SHA-256 of the
  bytes at the asset's URI, which agents read as a contradiction.

## Change (after review)

- Step 2: an asset's sha256 is the SHA-256 of the bytes its URI returns; a paper cited by its DOI takes
  `not_available_literature_source`, even when you hold a PDF or a text extract; any other resource you do not
  hold or cannot hash takes `not_available`; never zeros; a URI may need sign-in (a file server, object store or
  shared file system the record's readers can open), and a path that exists only on the writing machine is no
  asset.
- Step 8: descriptor names, units and configuration keys are open (write the source's own term; the validator
  notes it as information). Every other controlled field takes a term from a fixed list, all listed at
  `GET /portal/api/ontology`, which no page named before. Never write a nearby term. An optional field without
  the right term is left out, with the gap named in the notes; a required one makes the record wait for the
  proposed term. Each waiting result is reported with the records delivered (value, field, term); never dropped
  silently.
- The unknown-term error, the VOCABULARY_SUBSTITUTION message, the checksum warning, the Controlled-Vocabulary
  and Assets pages say the same.

## Gate 1

No check changes; four messages and two descriptions change. Tests: 942 passed.

## Gate 2: review

Codex, Grok and Gemini, blind.

- **All three:** the open/closed split stops the loss for descriptor names, units and configuration keys
  without inviting substitution. All three: a required closed term still blocks the record, so "a missing term
  never removes a result" was false, and "list each result" named no place to list it. The step now says the
  record waits for the term and that each waiting result is reported with the records delivered (value, field,
  term). Codex asked for a pending-record path for such records; that is a validator change, listed in
  PROPOSED.md.
- **All three:** "a path on your own disk is no asset" also excludes a lab file server, an object store or a
  cluster mount that the record's readers can open. Now: a URI may need sign-in; a path that exists only on the
  writing machine is no asset.
- **Grok:** change 09's checksum warning did not say that a DOI asset keeps the literature placeholder when a PDF
  or extract is held, so an agent fixing toward the warning would hash the extract onto the DOI. The warning and
  its description now say it. The Assets sentence that began "When you do not hold those bytes" contradicted the
  literature case; rewritten.
- **Grok:** the hint for an unknown term led with "leave this field out" even for a required field; it now names
  both cases.

## Gate 3

Measured by the next benchmark run against the two runs already made after changes 07 to 11.
