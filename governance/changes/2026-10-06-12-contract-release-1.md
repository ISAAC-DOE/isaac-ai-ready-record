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

Run 3 on the documentation with release 1, scored blind beside the baseline in one pass (three papers, three
agents):

| Agent | Outright errors | Links the paper does not support | Core values stored (of 107) |
|---|---|---|---|
| A | 5 to 2 | 0 to 0 | 80 to 42 |
| B | 4 to 0 | 29 to 0 | 105 to 29 |
| C | 13 to 25 | 18 to 5 | 86 to 75 |

- Two agents did what step 8 says: each result whose required term is missing (a reaction for enzyme kinetics,
  battery cycling or mineral dissolution) is reported as waiting for that term, about 36 and 51 results by the
  notes, instead of
  being filed under the wrong kind. Their kind errors are gone, and those results are not stored until the
  vocabulary has the term.
- The third agent barely reported waiting results. Its errors rose, from temperatures and an atmosphere
  written where the paper states none, a crystal temperature, and two rows quoted from an earlier paper. Release 1
  did not touch those rules; on the previous documentation the same agent's runs ranged from 2 to 13 errors.
- By the gate-3 rule the worst agent got worse, so its baseline, run 3 and a fourth run were scored together by
  one scorer per paper. Battery: 11, 17 and 12 error instances; geochemistry: 5, 5 and 0. On those two papers its
  errors on release 1 sit within its own spread. Enzyme kinetics: 1, 3 and 4. Both release-1 runs filed the two
  wild-type rows that the paper quotes from the group's earlier paper as this paper's results, which none of its
  three earlier runs did. Its notes say why: the mutant records needed a wild-type baseline to link to (step 5,
  change 11). Change 14 closes that case in step 5.
- The agent's first fourth run on enzyme kinetics replayed run 3 byte for byte (same record identifiers); the
  benchmark runner now gives every run a unique prompt, and the run was repeated independently. No other pair of
  runs was identical.
