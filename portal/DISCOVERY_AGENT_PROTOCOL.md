# ISAAC Discovery — Agent Operating Protocol (v0.11, provisional)

> How an agent operates on an ISAAC **scientific-discovery project**. This is the
> *reasoning* layer — separate from, and not part of, the frozen ISAAC **records**
> standard (`schema/`, the records wiki). Hypotheses/projects are NOT records.
>
> The machine-readable version of everything below is served live at
> **`GET /portal/api/discovery/manifest`** (public, no auth) — an agent's first
> call. This document is the human-readable companion.
>
> **Status:** provisional. The state machines and compute loop reflect a first
> model; they are being reconciled with the practitioners who have run this loop
> in production. Expect v1 to adjust the lifecycles and field shapes.

## Prime directive (the kernel)

1. **Read before you act.** At the start of every turn on a project, call
   `GET /portal/api/projects/{id}/briefing`. It is the **authoritative current
   state** — a curated digest, not the full firehose. Reconcile your working
   memory to it; the dashboard wins any conflict.
2. **Write after you act.** Every hypothesis, prediction, verdict, status change,
   and compute run is an API write. **If it is not on the dashboard, it did not
   happen.** Never hold important project state only in your own context.
3. **One project = one ground truth.** Do not fork reality in your head.

These are affordances, not just etiquette: the briefing *hands* you the truth, the
API *rejects* malformed writes, and the manifest *is fetched* rather than
remembered — so doing the right thing is the easy thing.

## The method (the scientific contract)

Discovery here is **not free-form analysis**. The dashboard, briefing, ranking, and
discrimination matrix are all built around one epistemic loop — follow it in order.
This is mirrored verbatim in the manifest's `method` block (the machine-readable
copy every agent fetches on connect), and the briefing's `method_compliance` field
re-checks it every turn.

1. **Frame competing hypotheses (≥2)** that explain the goal via *different*
   mechanisms. Each carries a statement, a mechanism, and an `origin` (how you
   arrived at it). One unopposed hypothesis is an assumption, not a discovery.
2. **Enumerate falsifiers.** For *each* hypothesis register the **set** of
   predictions whose observed outcome would **kill** it — the full discriminating
   set, not one token prediction. A hypothesis with no falsifier is inadmissible.
   Every prediction needs a concrete `falsification_criterion`.
3. **Record provenance.** Every prediction **must** carry an `origin` — *how* it was
   produced (`derived_from_mechanism | discrimination_design | literature |
   prior_result | agent_reasoning`) with reasoning and sources. A prediction nobody
   can trace cannot be trusted or reproduced.
4. **Design to discriminate.** Prefer measurables where the competing hypotheses
   predict *different* outcomes; declare them in `discriminates`. The server folds
   these into the cross-hypothesis discrimination matrix.
5. **Gather method-compatible evidence** per prediction (records corpus, literature,
   compute), gating on methodological compatibility before a record counts.
6. **Render a verdict** per prediction (`supports | contradicts | neutral |
   insufficient | blocked`) with strength and explicit reasoning. You do **not**
   author a confidence number — the platform recomputes the hypothesis's confidence
   from its prediction verdicts on every `/evaluate`.
7. **Propose the single most discriminating next experiment.**

**Non-negotiables:** every hypothesis falsifiable with ≥1 falsifying prediction;
every prediction carries an `origin` *and* a `falsification_criterion`; evidence is
method-gated; every decision is dual-written (dashboard event + MLflow mirror).

## Epistemic guardrails (domain-agnostic — they bite in any field)

Two rules about the *logic of evidence*, tracked live in every briefing's
`method_compliance` (advisory now, enforced later). They are generic — nothing
here is specific to any one science.

1. **Use-novelty (no double-counting).** A model/computation *fit* to a datum
   cannot also *confirm* it — accommodation is not prediction. You may build and
   tune models freely (that's how hypotheses are *generated*; such a result is a
   *hypothesis generator* and earns no confidence by itself). But when a verdict
   leans on a model, declare `evidence_independence` (what it was fit to vs tested
   against). If those overlap, the honest verdict is **`neutral`/consistent**, not
   `supports`. Real confirmation is the model's prediction on data it did **not**
   see — the discriminating experiment.
2. **Hypothesis individuation.** A hypothesis *is* its empirical content (what it
   predicts and forbids), not its mechanism story. Only sharpening a parameter or
   wording → **refine in place** (`PUT /hypotheses/{id}/refine`, a new *version* of
   the same node; keeps its evidence + history). A claim that predicts
   *differently* on some observable (different sign, ordering, or scale — not just a
   tighter number) → a **new hypothesis** that `supersedes` the old and **must name
   the discriminating observable**. The superseded node and its refuted predictions
   stay queryable — never overwrite a falsification. *Test:* if you can't name an
   observable on which old and new diverge, it's a refinement, not a new hypothesis.

## Reading progress (convergence, not leader confidence)

Discovery does not "keep one hypothesis high." Progress is **distance to a
decision.** Two rivals that are *observationally identical* on all current data are
a **settled phenomenon with an open sub-mechanism** — not "everything is weak."

`briefing.convergence` reports contested clusters of surviving hypotheses and
whether existing evidence can still separate them:
- `blocked_on_experiment` — identical on current data, but a discriminating test is
  registered (unrun). `decision_distance ≈ 0.2`: **run it.**
- `no_discriminating_test` — identical *and* no test designed. `≈ 0.8`: **design
  one.** (Worse than "one experiment away.")

When survivors are observationally identical, **re-auditing the same data will not
separate them — it only erodes confidence.** The platform redirects
`recommended_actions` to the experiment; it **never freezes your confidences** (you
keep updating them on real evidence). A rigor pass over *unchanged* evidence should
be a no-op: don't re-deduct for a flaw already corrected. Confidence moves on new
evidence / new hypotheses / corrected assumptions — not on how many times you look.

## Independent rigor review (the backstop)

The automated checks above catch *missing* declarations. They cannot catch a
declaration the working agent simply **omitted** — e.g. a model fit to the data it
"confirms" with `evidence_independence` left blank. That needs a semantic read of
the reasoning, so it's done by an **independent critic: a separate agent/session,
not the one that did the work.**

- **When:** before trusting any high-confidence conclusion (moving a hypothesis to
  `supported`, or confidence > 0.7), and on request. Independence is the whole
  point — don't critique your own pass.
- **How:** spawn a fresh reviewer with `manifest.rigor_review.critic_prompt`. It
  reads `/context`, hunts for use-novelty / individuation / falsifiability /
  evidence-compatibility / confirmation-bias failures (inferring omitted
  declarations from the prose), and POSTs each as a **finding**
  (`POST /projects/{id}/rigor/findings` — summary, detail, category, severity,
  target).
- **Then:** the working agent reads `GET /projects/{id}/rigor/findings` and for
  each either **fixes** it (and `PUT`s it to `resolved` with how) or justifies why
  it holds; `dismissed` only for genuine non-issues. Open findings are surfaced live
  in `briefing.rigor_review`; later, open **critical** findings will block
  `supported`.

## Connect

- Base URL: `https://isaac.slac.stanford.edu/portal/api`
- Auth: `Authorization: Bearer <token>` (PI's token from the portal **API Keys**
  page; the user must be in an allowed group). Identity is server-stamped — you
  cannot spoof `owner_identity`.
- Bootstrap: `GET /portal/api/discovery/manifest` (no auth).

## Object model

`project → hypotheses → predictions`; an append-only `events` journal; one
`next_experiment` per project. `evidence_record_ids` are plain ISAAC record IDs in
the records DB — referenced read-only, never written from here.

## State machines

- **Hypothesis `status`:** `proposed → supported | eliminated | needs_more_data | superseded`
  (set via `PUT /hypotheses/{id}` — **status only**; any `confidence` sent is ignored,
  the platform computes it from the prediction verdicts).
- **Prediction `work_status`** (drives the Validation board):
  `awaiting_evidence → more_work_pending → compute_submitted → compute_running → evaluated`.
- **Prediction `verdict`** (the scientific outcome, set at `evaluated`):
  `supports | contradicts | neutral | insufficient | blocked`, with `strength` `strong|moderate|weak`.

`work_status` and `verdict` are **orthogonal**: one says where in the pipeline a
prediction is, the other says what it concluded.

## Per-turn loop

```
GET /projects/{id}/briefing            # ground yourself
… reason …
POST /projects/{id}/hypotheses         # a new idea
POST /hypotheses/{id}/predictions      # a testable consequence
PUT  /predictions/{id}/evaluate        # got data → verdict + evidence_record_ids + mlflow_run_url
PUT  /hypotheses/{id}                   # ranking changed → status only (confidence is computed)
PUT  /projects/{id}/next_experiment    # the discriminating next step
POST /projects/{id}/events             # one line per reasoning step (transcript)
```

## Compute loop (calculations as the reasoning happens)

```
submit NERSC/DFT/MLIP/microkinetics job
PUT /predictions/{id}/status {work_status: "compute_submitted", mlflow_run_url}
PUT /predictions/{id}/status {work_status: "compute_running"}      # when it starts
PUT /predictions/{id}/evaluate {verdict, strength, evidence_record_ids, mlflow_run_url}
```

The dashboard renders `compute_submitted` / `compute_running` predictions as
"what we're waiting on," and the Compute ledger aggregates the MLflow runs.

## Field shapes to standardize

- **`origin`** (how a hypothesis was formed):
  `{type: agent_reasoning|literature|prior_result|human, summary, reasoning, sources:[{record_id|doi|hypothesis}]}`.
- **`prediction_origin`** (how a *falsifying prediction* was produced — **required on
  every prediction**): `{type: derived_from_mechanism|discrimination_design|literature|prior_result|agent_reasoning, summary, reasoning, sources:[{record_id|doi}]}`.
  Paired with `falsification_criterion` (the threshold/direction that refutes the
  hypothesis) and `discriminates` ([{hypothesis_label, expected}]).
- **MLflow runs** — post as a structured `event`
  (`{event_type: compute_running, detail: "<run_name> / <what_it_computed> / <status>", mlflow_run_url}`),
  not a bare URL, so the Compute ledger has substance.
- Use the event-type, `work_status`, `status`, and `verdict` vocabularies above
  verbatim.

## The invariant

**If it is not on the dashboard, it did not happen.** The dashboard is the shared
brain for the project; your context is scratch space.

---

## v0.2 additions (hardened from the first real Cu-Au cycle)

These come from one fully-executed discrimination cycle (real VASP on Perlmutter,
UMA benchmark, CatMAP, MLflow). They are now in the contract; the briefing-5 below
is the next increment.

**Hypotheses are a graph, not a list.** Relate them with
`POST /hypotheses/{id}/relations {to_hypothesis_id, relation_type, note}`,
`relation_type ∈ {supersedes, derived_from, competes_with, co_operating}`.
`derived_from` carries an analogy transfer (Cu-Au → Cu-Ag); `co_operating` says two
mechanisms can both be partly true (not everything competes).

**Predictions discriminate.** A good prediction differs in what each hypothesis
predicts for it. Declare that with `discriminates: [{hypothesis_label, expected}]`
when you create the prediction; the server aggregates these into the
cross-hypothesis **discrimination matrix** that drives next-experiment selection.

**Compute is multi-run with a real lifecycle.** A prediction has MANY runs (a
failed job + its resubmit both belong to one prediction). Register each:
`POST /predictions/{id}/runs {backend, engine, resource, slurm_job_id,
mlflow_run_url, status, params, metrics}` and advance it with `PUT /runs/{run_id}`.
`status ∈ {queued, running, completed, failed, resubmitted}`. Backends are **data**
(`vasp`, `uma`, `catmap`, …), not a fixed enum — any engine plugs in. Design for
minutes-to-hours latency.

**Methodological compatibility is a non-negotiable gate.** Before an evidence
record can support/contradict a prediction, its method must match: same
`output_quantity` (ΔE vs ΔG), functional, and corrections (PBE vs UMA-RPBE are NOT
comparable). State the prediction's `output_quantity`; the dashboard resolves each
evidence record's method from the records DB and flags incompatible comparisons. A
verdict resting on a mismatch is surfaced as a warning, not trusted silently.

**Verdicts are atomic; the confidence rollup is the platform's job, not yours.** Write
each verdict via `/evaluate`. On every verdict the platform recomputes the
hypothesis's confidence from the full prediction set (log-odds aggregation: supports
add, contradicts subtract more heavily, neutral mild-negative, blocked excluded,
insufficient no-op; unreliable below 2 decisive verdicts) and stores it. You never
author or PUT a confidence number — you only render verdicts and set `status`.

**The briefing-5 (the next increment).** The ground-truth digest will always show,
at the top: (1) goal; (2) ranking + confidence; (3) an **evidence index keyed by
descriptor** — what already exists for this system (so you never say "no data" when
it's there); (4) a **methodological-compatibility ledger**; (5) the
**pending-experiment queue** ranked by discriminating power vs cost.

## v0.4 fixes (from the first end-to-end agent run)

- **Endpoint base path is explicit.** All endpoint paths are relative to
  `base_path` = `https://isaac.slac.stanford.edu/portal/api` — e.g.
  `base_path + "/projects"`. Do **not** prepend `/discovery/`; the manifest merely
  lives under `/discovery/`. (The manifest now carries `base_path` + a note.)
- **Vocabulary is accept-and-normalized.** Verdicts and relation types map common
  synonyms to canonical on write — `refutes → contradicts`, `inconclusive →
  neutral`, `co_operates_with → co_operating`, etc. Prefer the canonical terms,
  but natural words won't silently break the briefing's categorization anymore.
- **`POST /events` requires `summary`** (one line); `detail` is optional/long.
- **`next_experiment` is REPLACE, not merge**, and now preserves **all** keys you
  send (no silent drop). Send the complete object each PUT.
- **Evidence `system_role` is classified by the composition element-set + formula,
  not the material name** — so "Interdigitated Au–Cu …" is no longer misread (the
  free-text name was extracting Indium from "Interdigitated").
