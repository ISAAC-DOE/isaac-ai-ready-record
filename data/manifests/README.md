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

### 0.01-mechanics-only (AUTHORED ablation — never shipped, and not early)

`policy_version` 66, 20,197 bytes, digest `28d9b4521aa37176`. Numbered 0.01 to sit below every real
version because it is a REFERENCE POINT, not a date: written 2026-10-16 from
`0.76-a-specimen-is-its-sample-group` by removing clauses, never by adding any.

- **Removed:** every section that tells an agent how to JUDGE — `scoring_model`, `method`,
  `epistemic_guardrails`, `rigor_review`, `prime_directive`, `provenance`, `progress_model`,
  `auditability`, `resume_protocol`, `compute_loop`, `per_turn_loop`, `reproducibility_mode`,
  `integrations`, `getting_started`, `isaac_ecosystem` — plus the judgement prose inside the
  `/predictions/{id}/evaluate` and `/projects/{id}/hypotheses` endpoint descriptions, which is where
  the remaining ~2.4 KB of doctrine lived.
- **Kept unchanged:** the five-verdict vocabulary, all 32 routes, `field_shapes`, `auth`,
  `event_types`, and `enforcement` with the server's true `policy_version`. A seat can still form
  valid requests; it is simply not told which verdict to choose.

Why not `0.1-provisional` as the floor: it declares only FOUR verdicts, and `blocked` — absent there
— accounts for 56% of all wrong verdicts in the bench's earlier waves on case 2. A four-verdict rung
is scored on an easier task than any five-verdict rung, so its apparent superiority is partly that
artefact. This file holds vocabulary, routes and gates constant so a difference against a richer
contract is a difference in DOCTRINE alone.

⚠ It is an ABLATION, not a clause written to move a number — the only defensible way to author a rung
under `manifest_refine`'s standing rule that "a manifest clause chosen to move a metric has corrupted
the measurement". It claims no rejection the server does not produce, and unlike `0.28-provisional`
it does not understate the gates either.

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
