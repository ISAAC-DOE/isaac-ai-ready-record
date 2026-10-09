# Manifest evolution

Each `<version>.json` here is a complete discovery manifest. To serve one, set
`ISAAC_MANIFEST_FILE=<version>` (no path, `.json` optional). With the variable unset, the
portal serves the built-in contract from `get_manifest()` in `portal/discovery.py`.

A file only changes what the agent is told. The gates the server enforces and the
per-project briefing still come from the code, so a manifest must never claim a rejection
or a flag the server does not produce.

Name each file after its `version` field, and give each version a name that says what it
changes.

## Versions

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

### 0.76-a-specimen-is-its-sample-group (built-in)
The contract in `portal/discovery.py`, served when `ISAAC_MANIFEST_FILE` is unset.
`policy_version` 66. Introduced upstream in #254.

- Independence of evidence is judged by shared specimen: records on the same physical sample
  (its whole sample group, found from links in both directions) count as one source.
- Each verdict's pin also freezes the cited records' sample and replicate groups; the
  briefing raises `evidence_regrouped` if a later deposit or deletion changes them.
