# Manifest evolution

Each `<version>.json` here is a complete discovery manifest. With nothing set, the portal
serves the built-in contract from `get_manifest()` in `portal/discovery.py` (currently
`0.76-a-specimen-is-its-sample-group`). To try another one:

| How | Example | Scope |
|---|---|---|
| Query parameter | `GET /portal/api/discovery/manifest?version=0.75-the-contract-names-no-ones-case` | one request; names in this folder or `builtin` only, no paths |
| Environment | `ISAAC_MANIFEST_FILE=0.77-a-computable-criterion-is-answered` | the deployment's default |
| Your own draft | `ISAAC_MANIFEST_FILE=/Users/me/drafts/my-manifest.json` | the deployment's default, from a local file (path must end in `.json`) |
| Python | `discovery.get_manifest("0.28-provisional")`, `get_manifest("/path/draft.json")`, `get_manifest("builtin")` | that call |

`builtin` always means the built-in contract, even when `ISAAC_MANIFEST_FILE` names another.
Nothing is stored per project: which manifest an agent read is up to how it fetched it.

A file only changes what the agent is told. The gates the server enforces and the
per-project briefing still come from the code, so a manifest must never claim a rejection
or a flag the server does not produce.

Name each file after its `version` field, and give each version a name that says what it
changes.

## Versions

### 0.85-decidability-is-declared (AUTHORED — never shipped, NOT enforced)

94,214 bytes (this folder's copy, built from the online 0.77 so the host strings are the served ones),
digest `0ed193ec213954f9` (sorted-key canonical JSON), `policy_version` **66** (unchanged; nothing new
is enforced). Base: `0.77-a-computable-criterion-is-answered`, **not** 0.83 or 0.84 — it does not
contain `derived_is_a_complete_basis` or `declare_the_observation_fully`. Ten leaves change, verified
by leaf diff: `version`, one new `scoring_model` entry, one sentence appended to
`PUT /predictions/{id}/evaluate` pointing to it, and a seven-leaf `_authored_rung` provenance block.

- **Added `scoring_model.decidability_is_declared`.** The three non-decisive verdicts are presented as
  the outcomes of two questions asked in order rather than three labels to choose between. FIRST, is
  the quantity the criterion names available from the cited records — reported, or derivable because
  every component is reported? Read it out of `falsification_criterion`, not `descriptor_name`. If not
  available: `insufficient`, and nothing further is asked. SECOND, only if available, can the
  criterion's own arithmetic be carried out on those records as written? If not, because the available
  quantity is the wrong KIND of thing: `blocked`, naming the failing dimension, applied symmetrically.
  If it can: answer. The clause also places **`neutral` with `supports` and `contradicts`, not with
  `insufficient` and `blocked`** — it is the outcome of a test that ran and found no predicted effect,
  not a reason a test could not run — and notes that where the criterion's own falsification branch
  treats the absence of the effect as refuting the claim, the answer is `contradicts`, a line set by
  the item and not by this contract. Restates 0.77's `why_a_criterion_cannot_be_decided`,
  `when_a_validity_objection_disqualifies` and `insufficient_means_the_quantity_is_unavailable` as one
  ordered procedure, and supplies the only definition of `neutral` outside the parenthetical in
  `scoring_model.how`.

**Read from nothing — this rung is prose only.** No engine code reads `decidability_is_declared`.
`enforcement.rejects_400` stays at 2 entries, no `method_compliance` entry is added and no wire field
is introduced, so the file claims no rejection and no flag the server does not produce. That is
deliberate: it is the CONTROL arm for a possible later version with identical wording that IS enforced
(a `_check_decidability` alongside `_check_verdict_basis`, gated on `policy_version >= 67`). Isolating
enforcement needs the words held constant; `0.76-the-diagnosis-names-the-verdict` and
`0.77-derivability-is-enforced` in the bench ladder differ in wording and enforcement at once, so
neither can attribute a difference to either.

Why: in the bench's 50-case five-verdict re-seat, per-label recall was 97% and 95% on `supports` and
`contradicts` but 63%, 58% and 43% on `insufficient`, `neutral` and `blocked`, with 53 of 612 items
wrong under two independent seats. Reading all 53 rationales showed the seat executing the
coverage-versus-validity test explicitly and still splitting from the key, and showed two patterns this
clause addresses: holding the numbers and returning `insufficient` where they fall short of the bar,
and returning `blocked` on a comparison whose arithmetic it had already carried out, which
`when_a_validity_objection_disqualifies` forbids in as many words. The clause names no case, no item
and no verdict to prefer.

**Status: not yet registered, and not yet run.** A prior manipulation that put 0.77's existing clauses
verbatim into the seat briefing was a null (2 of 612 items moved, exact McNemar p = 0.50, pooled
accuracy down ~1 point), so prose alone is not expected to close this gap; the bench's own gate-versus
-prose contrast stands at n=1 per arm and is uncallable under its registered decision rule. Register in
`experiments/manifest_refine/PREREG.md` before any wave is run against this file.

### 0.84-derived-values-and-full-observations (AUTHORED — never shipped)

92,876 bytes (bench archive copy), digest `73b387d3ee561c01` (bench archive copy, sorted-key canonical
JSON), `policy_version` **66** (unchanged; nothing new is enforced). Base:
`0.77-a-computable-criterion-is-answered`, **not** 0.83. Four leaves change, verified by leaf diff:
`version`, two new `scoring_model` entries, and one sentence appended to `PUT /predictions/{id}/evaluate`
pointing to them.

- **Added `scoring_model.derived_is_a_complete_basis`.** A value computed from the cited records is a
  complete basis for a decisive verdict (`basis: derived`, arithmetic in the rationale); no compute run
  or MLflow trace is needed for arithmetic on cited values. The briefing's
  `decisive_verdict_without_descriptor_in_evidence` entry is advisory and neither rejects the verdict
  nor lowers its score. Read from `_check_verdict_basis`, which places no requirement on `derived`.
  Extends 0.77's `insufficient_means_the_quantity_is_unavailable` with the two facts it left out.
- **Added `scoring_model.declare_the_observation_fully`.** Send `observed {value, unit, scale}` in the
  threshold's unit on every decisive verdict: the server then computes the margin and keeps the
  authored one only as a claim; with no scale or a unit mismatch it falls back to the authored margin.
  Read from `_derived_margin` and its caller in `compute_hypothesis_score`.

Why: in the bench's waves, one seat repeatedly returned `insufficient` for a value computable from the
cited records, citing the need for a compute run, and one excursion came from an authored margin of 0
on a falsifier sent without a scale. Neither clause names a case, an item or a verdict to prefer.
Registered before deployment in the bench's `experiments/manifest_refine/PREREG.md` appendix A12.

### 0.83-the-enforcement-block-is-complete (AUTHORED — never shipped)

92,723 bytes, digest `564a93c8b8db4758`, `policy_version` **66** (unchanged; nothing new is
enforced). `0.77-a-computable-criterion-is-answered` with **2 of 306 contract leaves changed** —
`enforcement.rejects_400` and `version` — verified by leaf diff.

**`rejects_400` goes from 2 entries to 7.** The manifest claimed the server rejects two things; it
also rejects five more. Each was read from the engine source, not inferred:

- a decisive verdict (`supports`/`contradicts`) with no `basis` (policy 64,
  `discovery._check_verdict_basis`). `insufficient` and `blocked` are **exempt** — they rest on the
  absence of evidence and have nothing to cite, and the server skips the check for them.
- a `basis` outside the five-value vocabulary (cited_record, derived, computed_run, literature,
  prior_knowledge)
- `basis: cited_record` with no `evidence_record_ids`, or whose `descriptor_name` appears in NONE of
  the cited records
- `basis: computed_run` with no compute run linked, or `basis: literature` with no citation attached
- an `observed.scale` that is the prediction's own THRESHOLD, or that disagrees with the cited
  records' declared uncertainty by more than 2x in either direction (`_check_observed_scale`)

Nothing about enforcement changed — these gates already existed at policy 66. Only the document's
account of them did, which is why `policy_version` is untouched. This is the inverse of the warning
on `0.28-provisional`: that file understates the gates by omitting `enforcement` entirely, and 0.77
understates them by listing two of seven.

⚠ It adds no clause telling an agent which verdict to choose, names no error class and no case. It is
therefore not a clause that could have been chosen to move a metric, under `manifest_refine`'s
standing rule. Registered in that experiment's `PREREG.md` appendix A10, which also retracts an
earlier and incorrect `0.83` (digest `9c6b476e3ffb14c4`, never served or run, file deleted) that had
removed the working `reliability{basis}` feature and duplicated a sentence already present in
`scoring_model`.

### 0.77-a-computable-criterion-is-answered
Base: `0.76-a-specimen-is-its-sample-group`. `policy_version` 66 (unchanged; nothing new is
enforced). Taken from the discovery bench's `0.77-derivability-is-enforced`, without that
manifest's claim of a server-side rejection.

- **Added `scoring_model.when_a_validity_objection_disqualifies`.** A defect in the evidence
  weakens a verdict; it does not block one. If the criterion's arithmetic can be carried out
  on the cited records, answer it with `strength: weak` and name the defect. `blocked` is for
  objections that make the comparison itself impossible.
- **Added `scoring_model.insufficient_means_the_quantity_is_unavailable`.** Before returning
  `insufficient`, check whether the descriptor is reported or derivable from reported
  components. If it is, compute it and rule, or use `blocked` if it is the wrong kind of
  quantity. Advice only: the server does not reject or flag such a verdict.

Why: in the bench's waves, agents that agreed on what was wrong with the evidence still
returned different verdicts (`blocked` vs. a decision), and some returned `insufficient`
for a quantity derivable from reported parts, against `method.loop` step 6.

### 0.76-a-specimen-is-its-sample-group (built-in; also captured here)
The contract in `portal/discovery.py`, served when `ISAAC_MANIFEST_FILE` is unset.
`policy_version` 66. Introduced upstream in #254. The file here is a verbatim capture of
`GET /discovery/manifest` from isaac.slac.stanford.edu, confirmed byte-identical to what
production serves on 2026-10-15 (digest `b78bc1c52682`).

- Independence of evidence is judged by shared specimen: records on the same physical sample
  (its whole sample group, found from links in both directions) count as one source.
- Each verdict's pin also freezes the cited records' sample and replicate groups; the
  briefing raises `evidence_regrouped` if a later deposit or deletion changes them.

⚠ A NUMBER DOES NOT IDENTIFY A MANIFEST. The discovery bench's manifest_refine ladder carries its
own `0.76-the-diagnosis-names-the-verdict` and `0.77-derivability-is-enforced`, AUTHORED for that
experiment and sharing nothing with the versions here but a number (the two 0.76s differ in 24% of
their leaves). Compare by slug or digest. The bench's authored rungs are deliberately NOT kept in
this folder; they live in `experiments/manifest_refine/manifests/` in the bench repo.


### 0.75-the-contract-names-no-ones-case
The contract production served before 0.76. `policy_version` **65** — an agent served this file is
not told the specimen-based independence rule that 66 introduced. Digest `3414a167deed`, captured
from the live endpoint and matching the bench's wave of the same name.

### 0.28-provisional
Production's contract on 2026-06-25, recovered from `portal/discovery.py` at commit `b2688d0c`:
26 keys, and the version where `scoring_model` first appears and `blocked` joins the verdict
vocabulary. Digest `4b32b4278451`.

⚠ It declares **no `policy_version` and no `enforcement` key at all**, while the server still
gates. That understates the server in the mirror image of the warning above: an agent served this
file is told nothing about rejections it will still receive. Serve it to reproduce a historical
wave, not to describe the server as it stands.

### 0.01-minimal-five-verdicts (AUTHORED ablation — never shipped, and not early)

1,905 bytes, digest `a7df1ca6bc99f9aa`. Numbered 0.01 to sit below every real version because it is a
REFERENCE POINT, not a date: written 2026-10-16 from `0.1-provisional` (engine commit `ea414821`, the
first manifest that existed) by removing whole sections, never by adding or rewriting any. Every
surviving value is byte-identical to 0.1 — endpoints, `auth` and the verdict list verified equal,
`event_types` a strict subset.

