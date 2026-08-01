# Replication rounds — hardening the discovery contract against real runs

## What this exercise is for

**We are measuring the manifest, not the catalysis.**

N independent agents, different models, one identical question, one identical evidence
base, total isolation at the project layer. Where they converge and where they diverge is
evidence about **how tightly the application layer constrains a large language model**.

Every divergence is one of two things:

| | What it means | What to do |
|---|---|---|
| **(a) Genuine scientific ambiguity** | The evidence really does admit more than one reading | Nothing. This is what open science looks like, and a contract that suppressed it would be worse than useless |
| **(b) A hole in the contract** | The manifest let an agent wander, skip a step, assert without citing, or invent structure | Edit the manifest. This is the deliverable |

Sorting divergences into (a) and (b) is the whole job. It is a judgement, so it must be
**evidenced**: quote the trace, name the clause that failed, and state the edit.

The failure mode to avoid is over-steering. A contract that forces every model to the same
answer has not produced reproducible science, it has produced a very expensive way of
writing down what we already believed. **We want the reasoning constrained and the
conclusion free.** Structure is enforced; the answer is not.

## The three families of measure

**STEERING** (per run, from `benchmark_replicates.py`) — did the agent do what the contract
told it to? Every field maps to a specific manifest clause, so a low value points at the
exact instruction that failed to land:

- competing rivals present, and a residual/null competitor (method.loop step 1)
- `grounding` declared per hypothesis
- a SET of falsifiers per hypothesis, each with a `falsification_criterion` (step 2)
- `origin` on every prediction (step 3)
- `discriminates` populated (step 4)
- decisive verdicts CITED and PINNED (step 6) — the most gameable instruction we have
- unattributed writes and incomplete decisions (trace contract, policy 60)
- the decider registered as a first-class unrun prediction (step 7)

**CONVERGENCE** (across runs) — four axes, kept separate because they can disagree:

1. top-ranked mechanism
2. full ranked order
3. hypothesis-set overlap (Jaccard, pairwise)
4. the next experiment chosen

Two runs agreeing on the winner while having reasoned about entirely different candidate
sets is a *different* result from two runs that considered the same five and ranked them
alike. The first says the data are decisive; the second says the schema is.

**COST** (per run) — events, reasoning steps, compute runs, wall time. A contract that
achieves compliance by making every run enormously expensive has traded one problem for
another.

## Reading the result

- **High steering + high convergence** → the contract is working. Tighten nothing.
- **High steering + low convergence** → likely case (a). The method was followed and the
  answers still differ, so the evidence is genuinely ambiguous. Look for a discriminating
  experiment, not a manifest edit.
- **Low steering + high convergence** → dangerous. Agents agreed *without* following the
  method, which means they agreed for reasons the trace does not capture. Prior knowledge
  leaking in is the first suspect. Harden the clause they skipped.
- **Low steering + low convergence** → case (b). The contract did not bind. Fix the
  specific clause, and prefer a machine-checkable gate over more prose.

## Prose versus gates

The v0.60 lesson: `actor_model` and `decision` were *described* in the manifest and agents
still omitted them. Description is not compliance. When a requirement is decidable by
machine, make it a gate and let the 400 teach the agent. When it is a judgement
(is this decision *complete*? is this hypothesis genuinely *new*?), surface it in
`method_compliance` and let the briefing ask. **Never gate a judgement** — an agent that
cannot pass will write nothing rather than something partial.

## Independence

The RECORD repository is shared by design; all agents read the same corpus. Discovery
projects are private, enforced server-side (verified: `404` on read, `403` on write,
across accounts). The residual risk is that one run persists a calculation mid-window and
a sibling cites it. Records carry `attribution.uploaded_by`, so this is **detectable**;
the benchmark reports shared citations so contamination is measured rather than assumed.
Launch prompts instruct runs not to persist records during a round.

## The loop

1. Run N blinded replicates on one question.
2. `benchmark_replicates.py` → report.
3. Classify every divergence as (a) or (b), with the trace quoted.
4. For each (b): name the clause, decide gate versus flag, edit the manifest, bump
   `policy_version` if a new gate is added.
5. Deploy, verify, and **record the round below** so the next round can be compared.
6. Repeat on a fresh question. A contract that only holds on one question has not been
   tested.

Never retro-enforce a new gate on old projects: stamp each project with the contract it
was born under. The past is preserved without taxing the future.

---

# Round log

## Round 1 — Cu|Au CO₂RR, the rise and fall of ethylene selectivity

- **Started:** 2026-08-01
- **Contract under test:** manifest `v0.60-provenance`, `policy_version 60`
- **Question:** explain BOTH limbs of the FE(C₂H₄) trend, the rise with copper fraction
  and the subsequent fall, with competing falsifiable mechanisms
- **Evidence base (identical for all runs):** 37 performance records — 23 patterned Cu|Au
  stripes (Cu 20/50/67/80/89 %, Cu width 20/40/80/160 µm, Au fixed 20 µm), 5 pure Cu,
  3 pure Au, 6 Cu-on-Au dotted controls. CO₂ feed, 0.1 M KHCO₃, gas-diffusion cell,
  −0.9 to −1.3 V vs RHE
- **Runs:** A Claude Opus 5 · B Grok 4.5 · C GPT-5 Codex · D Gemini 3 Pro ·
  E Claude Opus 5 (**within-model control**, so model variance can be separated from run
  variance — without it a disagreement is uninterpretable)
- **Isolation verified before launch:** each account lists 0 projects; `404` on a sibling's
  project by direct ID; `403` on cross-write
- **Baseline state at launch:** every project 0 unattributed events, dataset attached,
  policy 60 enforcing

### Findings

_(to be completed after the runs; classify each divergence as (a) or (b), quote the trace,
name the clause, state the edit)_

### Manifest changes arising

_(to be completed)_
