"""
ISAAC Discovery — hypothesis-driven reasoning workbench (data-access layer).

ISOLATION CONTRACT (read before editing):
- Every function here talks ONLY to the isolated isaac_discovery database via
  database.get_discovery_db_connection(). It must NEVER call
  database.get_db_connection() (the privileged records connection). The single
  exception is read-only provenance lookups, which use
  database.get_readonly_db_connection() (records, READ ONLY) to resolve a record
  title for display — see resolve_record_summaries().
- Hypotheses/projects/predictions are NOT ISAAC records and never enter the
  records table or the frozen standard. They live only in isaac_discovery.
- discovery_user is least-privilege and physically cannot reach the records DB,
  so this contract is also enforced at the credential level; the rule above
  keeps the application code honest on top of that.

Table DDL lives in database.init_discovery_tables() (Dean's marker), created on
startup. This module is the CRUD + activity-feed + provenance surface that the
Discovery page and the /portal/api/* discovery endpoints call.
"""

import json
import logging
import math
import re
import secrets
import time

import database

logger = logging.getLogger("isaac-discovery")

# Crockford base32 (a subset of [0-9A-Z]); ULID-style 26-char ids, generated
# server-side. Discovery ids are independent of records ULIDs (separate DB).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Allowed activity-feed event types (the agent posts these via POST .../events;
# structured writes below also auto-append the matching one).
EVENT_TYPES = {
    "hypothesis_created", "prediction_added", "prediction_evaluated",
    "ranking_updated", "status_changed", "next_experiment_proposed",
    "evidence_ingested", "agent_message", "project_created",
    "compute_submitted", "compute_running", "compute_failed",
}

# Prediction workflow lifecycle (distinct from `verdict`, the scientific
# outcome). Drives the Validation board (Section B). Order = pipeline order.
# `compute_failed` is a PENDING side-state: a requested computation crashed / did
# not converge. It is NOT a verdict and does NOT move confidence — the prediction
# is simply not yet evaluated, and it surfaces as a to-do to re-run next cycle.
WORK_STATUSES = {
    "awaiting_evidence", "more_work_pending", "compute_submitted",
    "compute_running", "compute_failed", "evaluated",
}
WORK_STATUS_ORDER = ["awaiting_evidence", "more_work_pending", "compute_submitted",
                     "compute_running", "compute_failed", "evaluated"]

# Hypothesis status + prediction verdict vocabularies (documented for agents;
# not hard-enforced yet while the reasoning loop is still being learned).
HYPOTHESIS_STATUSES = ["proposed", "supported", "eliminated", "needs_more_data",
                       "superseded"]
VERDICTS = ["supports", "contradicts", "neutral", "insufficient", "blocked"]

# v1: hypotheses form a graph, not a list.
RELATION_TYPES = {"supersedes", "derived_from", "competes_with", "co_operating"}
# v1: a prediction has many compute runs; backends are data, not enum-locked.
COMPUTE_STATUSES = {"queued", "running", "completed", "failed", "resubmitted"}

# Independent rigor-critic findings. Categories name the generic epistemic failure;
# severities order the response. Accept-and-normalize like the rest.
RIGOR_CATEGORIES = {"use_novelty", "individuation", "falsifiability",
                    "evidence_compatibility", "confirmation_bias", "overreach",
                    "shared_premise", "other"}
RIGOR_SEVERITIES = {"critical", "major", "minor"}
RIGOR_FINDING_STATUSES = {"open", "resolved", "dismissed"}
# A "residual" hypothesis is the explicit NONE-OF-THE-ABOVE / the-shared-premise-is-
# -wrong alternative. Carrying one (with nonzero mass) is what lets a premise common
# to all surviving rivals actually FAIL. Marked via hypothesis_type.
RESIDUAL_HYPOTHESIS_TYPES = {"residual", "null", "none_of_the_above", "noneoftheabove",
                             "shared_premise_false", "alternative_mechanism"}
# A hypothesis must carry a SET of falsifying predictions, not one token prediction.
MIN_PREDICTIONS_PER_HYPOTHESIS = 2


def is_residual_hypothesis(h) -> bool:
    return (str(h.get("hypothesis_type") or "").strip().lower().replace("-", "_")
            in RESIDUAL_HYPOTHESIS_TYPES)

# Accept-and-normalize: agents reach for natural words. We map common synonyms to
# the canonical vocabulary on write (teach, don't block) so the briefing's
# categorization stays correct. (Lesson from the first live agent run.)
VERDICT_SYNONYMS = {
    "refutes": "contradicts", "refute": "contradicts", "refuted": "contradicts",
    "rejects": "contradicts", "contradict": "contradicts", "against": "contradicts",
    "support": "supports", "supported": "supports", "confirms": "supports",
    "inconclusive": "neutral", "ambiguous": "neutral", "mixed": "neutral",
    "no_data": "insufficient", "none": "insufficient",
    "incompatible": "blocked", "not_comparable": "blocked", "incomparable": "blocked",
    "schema_blocked": "blocked", "not_evaluable": "blocked", "ill_posed": "blocked",
}
RELATION_SYNONYMS = {
    "co_operates_with": "co_operating", "cooperates_with": "co_operating",
    "cooperating": "co_operating", "cooperates": "co_operating",
    "competes": "competes_with", "supersede": "supersedes",
    "derives_from": "derived_from", "derived": "derived_from",
}


def normalize_verdict(v):
    if not v:
        return v
    k = str(v).strip().lower()
    return VERDICT_SYNONYMS.get(k, k)


def normalize_relation(r):
    if not r:
        return r
    k = str(r).strip().lower()
    return RELATION_SYNONYMS.get(k, k)


def get_manifest() -> dict:
    """Self-describing contract: the bootstrap an agent fetches to learn how to
    operate on ISAAC discovery projects. PROVISIONAL (v0.1) — refined as the real
    reasoning loop is pinned down with the practitioners."""
    return {
        "name": "ISAAC Discovery — Agent Operating Protocol",
        "version": "0.30-provisional",
        "base_path": "https://isaac.slac.stanford.edu/portal/api",
        "isaac_ecosystem": {
            "_what": "The ISAAC tooling you should try to use. NOTHING here is assumed to "
                "be wired up for you — access depends on WHERE you run and WHO runs you. "
                "The ISAAC records + this Discovery API are MANDATORY (you're already on "
                "them, with your portal token). For everything else: on your FIRST turn, "
                "check which you can actually reach and ASK YOUR OPERATOR ('your boss') "
                "what you have access to in this environment — then USE WHAT YOU HAVE. "
                "Don't fake a capability you lack; don't skip one you have.",
            "mandatory": [
                {"name": "ISAAC records API", "for": "the standardized knowledge base — "
                    "experimental AND computational records (adsorption energies, "
                    "barriers, XANES, performance). Query it for evidence and BEFORE "
                    "recomputing anything (see integrations.isaac_data_sources).",
                 "where": "GET /portal/api/records"},
                {"name": "ISAAC Discovery API", "for": "this hypothesis workbench.",
                 "where": "/portal/api/discovery/manifest"},
            ],
            "ask_your_operator_if_available": [
                {"name": "FairChem / UMA (MLIP)", "for": "fast ML-potential adsorption "
                    "energies & trend screening across many candidates"},
                {"name": "VASP via NERSC IRI / Superfacility API", "for": "accurate DFT "
                    "for the decisive numbers a verdict rests on"},
                {"name": "mkmISAAC", "for": "microkinetic modeling — rates from energetics",
                 "where": "https://github.com/ISAAC-DOE/mkmISAAC"},
                {"name": "Catalysis Hub", "for": "published adsorption energies & reaction "
                    "energetics (analog evidence)", "where": "https://www.catalysis-hub.org"},
                {"name": "Materials Project", "for": "formation energies / stability / "
                    "structures", "where": "https://materialsproject.org"},
                {"name": "Edison literature (PaperQA3)", "for": "cited literature via the "
                    "portal proxy — no key needed", "where": "/portal/api/literature/search"},
                {"name": "MLflow", "for": "experiment + reasoning replay trace"},
            ],
            "first_turn": "Enumerate which of these you can reach, report it to your "
                "operator, and ask about anything missing you'd want — so they can wire it "
                "up. The point: every agent should reach all the ISAAC goodies it can.",
        },
        "endpoint_paths_note": "Every endpoint `path` below is relative to "
            "`base_path` (e.g. base_path + '/projects'), NOT to this manifest's own "
            "URL. Do not prepend '/discovery/' to them — the manifest just happens "
            "to live under /discovery/.",
        "prime_directive": [
            "FOLLOW THE METHOD: see `method` below. Discovery here is not free-form — "
            "it is competing FALSIFIABLE hypotheses, each carrying a traceable SET of "
            "predictions that would kill it, every prediction with recorded provenance, "
            "resolved by discriminating evidence. This is the contract, not a suggestion.",
            "READ before you act: GET /projects/{id}/briefing at the start of every "
            "turn; treat it as authoritative current state and reconcile to it. The "
            "briefing is SELF-INSTRUCTING — its `recommended_actions` is your "
            "prioritized to-do (derived from method_compliance + rigor_review), and "
            "`method_compliance` is the live audit. You do NOT need a human to tell you "
            "the next rigor step; the dashboard does. Work the actions until clear.",
            "WRITE after you act: every hypothesis, prediction, verdict, status "
            "change and compute run is an API write. If it is not on the dashboard, "
            "it did not happen — never hold project state only in your context.",
            "One project = one ground truth. Do not fork reality in your head.",
        ],
        "method": {
            "_what": "The discovery epistemics this platform enforces. The dashboard, "
                "briefing, ranking and discrimination matrix are all built around these "
                "steps — follow them in order. This is the heart of the protocol; read it "
                "before any endpoint.",
            "loop": [
                "1. FRAME competing hypotheses (>=2) that explain the goal via DIFFERENT "
                "mechanisms. Each carries a statement, a mechanism, and an `origin` (how "
                "you arrived at it — reasoning + sources). A single unopposed hypothesis "
                "is not a discovery, it is an assumption.",
                "2. ENUMERATE falsifiers: for EACH hypothesis, register the SET of "
                "predictions whose observed outcome would KILL it — not one token "
                "prediction, the full discriminating set. A hypothesis with no falsifier "
                "is inadmissible. Each prediction needs a concrete `falsification_criterion` "
                "(the threshold/direction that, if seen, refutes the hypothesis).",
                "3. RECORD PROVENANCE: every prediction MUST carry an `origin` — HOW it was "
                "produced (derived_from_mechanism | discrimination_design | literature | "
                "prior_result | agent_reasoning) with reasoning and sources. Provenance is "
                "mandatory, not decorative: a prediction nobody can trace cannot be trusted "
                "or reproduced. See field_shapes.prediction_origin.",
                "4. DESIGN TO DISCRIMINATE: prefer measurables where the competing "
                "hypotheses predict DIFFERENT outcomes; declare them in `discriminates` "
                "([{hypothesis_label, expected}]). The server aggregates these into the "
                "cross-hypothesis discrimination matrix that drives the next experiment.",
                "5. GATHER evidence per prediction (records corpus via /evidence, "
                "literature via the proxy, compute via NERSC/MLflow), GATING on "
                "methodological compatibility (output_quantity / functional / corrections) "
                "before a record is allowed to count.",
                "6. RENDER a verdict per prediction (supports | contradicts | neutral | "
                "insufficient | blocked) with a strength and EXPLICIT reasoning via "
                "/evaluate. You do NOT set confidence — the platform COMPUTES it from "
                "your verdicts (see scoring_model) and the ranking moves automatically.",
                "7. PROPOSE the single most discriminating next experiment via "
                "/next_experiment.",
            ],
            "non_negotiables": [
                "Every hypothesis is falsifiable and carries >=1 falsifying prediction.",
                "Every prediction carries an `origin` (provenance) AND a "
                "`falsification_criterion`.",
                "Evidence is methodological-compatibility-gated before it counts.",
                "USE-NOVELTY: a model/computation fit to a datum cannot also COUNT as "
                "confirming that datum — accommodation is not prediction. Declare "
                "evidence_independence on evaluate; confirmation comes only from data "
                "the fit did not already see.",
                "INDIVIDUATION: a hypothesis IS its predictions. Only sharpening a "
                "parameter or wording → refine in place (a new VERSION). A claim that "
                "predicts DIFFERENTLY on some observable → a new hypothesis that "
                "`supersedes` the old, and must name that discriminating observable.",
                "INDEPENDENT REVIEW: a high-confidence conclusion must survive an "
                "independent rigor critic (a SEPARATE agent, not yourself) — see "
                "rigor_review. Resolve open critical findings before declaring "
                "'supported'.",
                "Every decision is dual-written: dashboard event (canonical) + MLflow "
                "mirror (replay).",
            ],
        },
        "epistemic_guardrails": {
            "_what": "Two domain-agnostic rigor rules the platform tracks for you "
                "(surfaced in every briefing's `method_compliance`; advisory now, "
                "enforced later). They apply in ANY field — they are about the logic of "
                "evidence, not about any particular science.",
            "use_novelty": {
                "rule": "Evidence used to BUILD or fit a hypothesis/model cannot also "
                    "CONFIRM it. A model tuned until it reproduces an observation you "
                    "already had earns ~zero confirmatory weight from that observation — "
                    "it was used twice. This is the no-double-counting / overfitting / "
                    "Texas-sharpshooter rule.",
                "you_may": "Build and tune models freely — that is how hypotheses and "
                    "predictions are GENERATED. Label such a result a hypothesis "
                    "generator; it earns no confidence by itself.",
                "you_must": "When you render a verdict that leans on a model/computation, "
                    "declare `evidence_independence`: what the model was fit to vs what "
                    "you are testing it against. If they overlap, the honest verdict is "
                    "'neutral'/'consistent', not 'supports'. Real confirmation = the "
                    "model's prediction on data it did NOT see (the discriminating "
                    "experiment).",
            },
            "hypothesis_individuation": {
                "rule": "Distinguish refining a hypothesis from replacing it. A "
                    "hypothesis is individuated by its EMPIRICAL CONTENT (what it "
                    "predicts and forbids), not by its mechanism narrative.",
                "refine_in_place": "Same predictions, just sharper (tighter parameter, "
                    "clearer wording, updated narrative) → PUT /hypotheses/{id}/refine "
                    "(bumps `version`, keeps the node + its evidence + its history).",
                "new_hypothesis": "Predicts DIFFERENTLY on some realizable observable "
                    "(different sign, ordering, or scale — not just a tighter number) → "
                    "create a NEW hypothesis, then add_relation('supersedes', "
                    "discriminating_observable=<the observable where they diverge>, "
                    "retained_vs_abandoned=<what carried over vs was dropped>). The "
                    "superseded node and its refuted predictions stay queryable — never "
                    "overwrite a falsification.",
                "test": "If you cannot name an observable on which the new and old "
                    "predict differently, it is a refinement, not a new hypothesis.",
            },
            "shared_premise_audit": {
                "rule": "Audit the COMMON FOUNDATION of your surviving hypotheses, not "
                    "just each hypothesis. When ≥2 rivals are observationally identical, "
                    "they usually share a mechanistic premise and only contest its "
                    "sub-parameters — that shared premise is the most dangerous blind "
                    "spot, because it is unaudited and hides inside their agreement. If "
                    "it is wrong, the whole equivalence class collapses.",
                "do": "State the shared premise explicitly. Decide: TESTED or ASSUMED? If "
                    "assumed, frame it as its OWN falsifiable hypothesis with a "
                    "discriminating test. And ALWAYS carry an explicit NONE-OF-THE-ABOVE "
                    "residual hypothesis (hypothesis_type='residual') with nonzero "
                    "confidence — without it, the shared premise can never lose, which is "
                    "not falsifiable. Surfaced in briefing.shared_premise_audit.",
            },
        },
        "progress_model": {
            "_what": "How to read progress. Progress is NOT 'the leader stays high "
                "confidence' — it is DISTANCE TO A DECISION. Two rivals that are "
                "observationally identical on all current data are a SETTLED phenomenon "
                "with an open sub-mechanism, not 'everything is weak'.",
            "convergence": "briefing.convergence reports contested clusters of surviving "
                "hypotheses and whether existing evidence can still separate them. A "
                "cluster is `blocked_on_experiment` (observationally identical, but a "
                "discriminating test is registered/unrun) or `no_discriminating_test` "
                "(identical and no test designed — worse). decision_distance summarizes "
                "it (0 = decided, 0.2 = one experiment away, 0.8 = no test designed).",
            "do_not_re_audit_to_resolve": "When survivors are observationally identical, "
                "re-auditing the SAME data will not separate them — RUN the "
                "discriminating experiment instead. The platform redirects "
                "recommended_actions to the experiment. (Confidence isn't authored — it "
                "is computed from your verdicts — so re-auditing without new verdicts "
                "doesn't move it anyway.)",
            "equivalence_classes": "When survivors are observationally identical on "
                "current data they are NON-IDENTIFIABLE — report them as ONE equivalence "
                "class (convergence.equivalence_classes), decided only by the experiment. "
                "Their computed confidences may differ if their own verdicts differ; "
                "that's grounded in evidence, not authored — there is no false-precision "
                "penalty to manage.",
            "register_the_decider_as_a_prediction": "The discriminating experiment must "
                "be a FIRST-CLASS unrun prediction (descriptor + discriminates naming "
                "each survivor's expected outcome) owned by a survivor — not only a "
                "next_experiment note. Then it is a tracked falsifier and convergence "
                "reads it directly. (briefing flags clusters whose decider lives only "
                "in next_experiment.)",
            "idempotence": "A rigor pass over UNCHANGED evidence should be a no-op — do "
                "not re-deduct confidence for a flaw already corrected. Confidence "
                "moves on new evidence / new hypotheses / corrected assumptions, not on "
                "how many times you looked.",
        },
        "scoring_model": {
            "_what": "THE single, canonical way a hypothesis is evaluated. Confidence is "
                "COMPUTED by the platform from the hypothesis's prediction VERDICTS — you "
                "NEVER author or set a confidence number. You move confidence by "
                "evaluating predictions (PUT /predictions/{id}/evaluate); the platform "
                "recomputes and stores it. The stored confidence (briefing.ranking[]) IS "
                "the computed score.",
            "how": "Score evidence against each prediction → a verdict + strength, then "
                "aggregate in log-odds: SUPPORTS +strength · CONTRADICTS −strength×1.25 "
                "(falsification is more decisive than confirmation) · a STRONG "
                "contradiction of a falsifier ~ falsified (≤0.15) · NEUTRAL a small "
                "negative (a test that ran and found NO predicted effect is mild evidence "
                "against) · INSUFFICIENT 0 (tested, didn't resolve) · BLOCKED 0 and "
                "EXCLUDED (schema gate, below). strength = strong 1.0 / moderate 0.6 / "
                "weak 0.3 (an OMITTED strength is treated as weak — qualify your "
                "decisive verdicts). confidence = sigmoid(Σ). A new hypothesis starts at "
                "the 0.5 prior.",
            "schema_gate": "When evidence is NOT validly comparable to a prediction "
                "(different output_quantity, units without a declared transform, "
                "different functional / electrolyte / potential / reference state, a DFT "
                "energy vs an experimental selectivity, …), verdict=BLOCKED — refuse the "
                "comparison rather than guess. Blocked evidence does NOT move belief; it "
                "only lowers COVERAGE (fewer valid tests). Convert-then-compare first if "
                "the mismatch is reconcilable. A block must cite the specific failing "
                "dimension and be SYMMETRIC (don't block contradicting evidence on a "
                "basis you'd accept supporting evidence).",
            "why_a_single_prediction_fails": "With <2 DECISIVE (supports/contradicts) "
                "verdicts the score is UNRELIABLE — you cannot validate or falsify a "
                "hypothesis on one verdict, and a lone prediction barely moves off the "
                "0.5 prior. This is WHY each hypothesis needs a SET of distinct, "
                "structured predictions (briefing flags unreliable_scores). Build the "
                "set; the score follows the evidence.",
            "report": "The score ships with its decomposition — n_decisive, the "
                "supports/contradicts/neutral/insufficient/blocked breakdown, coverage, "
                "and a conflict measure — never a bare number.",
            "evidence_independence_enforced": "Use-novelty is now in the MATH, not just "
                "flagged. A 'supports' whose evidence_independence shows the model was FIT "
                "to the data it's TESTED on is CIRCULAR — it scores 0 and is NOT decisive "
                "(a consistency check, not confirmation; declare evidence_independence so "
                "this is visible). And CORRELATED same-direction verdicts that rest on "
                "evidence_record_ids already counted are attenuated to 0.3× and don't add "
                "to the independent-decisive count — you cannot confirm a hypothesis twice "
                "with the same data, nor manufacture 'reliability' by stacking predictions "
                "on one result. Reliability needs ≥2 INDEPENDENT decisive verdicts.",
            "failed_compute_never_penalizes": "A computation that crashes or does not "
                "converge is NOT evidence and NOT a verdict — it produced no measurement. "
                "Set the prediction's work_status='compute_failed' (a failed compute run "
                "auto-sets it); the score is UNCHANGED — confidence reflects the evidence "
                "you actually have, not what you tried and couldn't get. The failed calc "
                "becomes a re-run to-do (briefing.failed_compute + recommended_actions / "
                "pending_work) for the next cycle. Do NOT downgrade a hypothesis because a "
                "tool failed, and never log the failure as insufficient/contradicts.",
        },
        "rigor_review": {
            "_what": "An INDEPENDENT adversarial critic — a SEPARATE agent/session, not "
                "the one that did the work — re-derives whether the conclusions survive "
                "scrutiny. It is the backstop for what the automated method_compliance "
                "cannot see: a declaration the working agent simply OMITTED (e.g. a model "
                "fit to the data it 'confirms', with evidence_independence left blank — "
                "the critic infers it from the rationale/mechanism prose).",
            "when": "Trigger a review before trusting any high-confidence conclusion "
                "(e.g. moving a hypothesis to 'supported', or confidence > 0.7), and on "
                "request. Independence is the point: do NOT critique your own work in the "
                "same pass — spawn a fresh reviewer with the critic_prompt below.",
            "critic_prompt": (
                "You are an INDEPENDENT rigor critic for an ISAAC Discovery project. You "
                "did not do this work and you owe it no deference. Your job is to REFUTE, "
                "not agree. Bootstrap from the manifest "
                "(GET /portal/api/discovery/manifest), then GET /projects/{id}/context "
                "and read the whole thing — hypotheses, predictions, verdicts, "
                "evidence_independence declarations, relations, and the reasoning prose. "
                "Hunt specifically for:\n"
                "  • USE-NOVELTY: any 'supports' verdict whose model/computation was fit "
                "to the very data it is tested against — EVEN IF evidence_independence is "
                "blank; infer it from the rationale/mechanism. Accommodation is not "
                "prediction.\n"
                "  • INDIVIDUATION: a `supersedes` that is really a refinement (no genuine "
                "discriminating observable), or a 'new' hypothesis that only renames an "
                "old one.\n"
                "  • FALSIFIABILITY: hypotheses with no real falsifier; predictions whose "
                "criterion can't actually fail.\n"
                "  • EVIDENCE_COMPATIBILITY: verdicts trusting methodologically-"
                "incompatible records (wrong output_quantity / functional / conditions).\n"
                "  • CONFIRMATION_BIAS / OVERREACH: only confirming evidence sought; "
                "confidence higher than the evidence licenses.\n"
                "  • SHARED_PREMISE (the deepest one): a mechanistic assumption common to "
                "ALL surviving hypotheses that has never itself been tested. If every "
                "rival takes premise X for granted (and the contest is only over X's "
                "sub-parameters), then X is unaudited and the whole set collapses if X is "
                "wrong — this blind spot hides INSIDE agreement. Demand it be framed as "
                "its own falsifiable hypothesis with a discriminating test, and that an "
                "explicit NONE-OF-THE-ABOVE residual carries nonzero mass.\n"
                "For each problem, POST /projects/{id}/rigor/findings {summary, detail, "
                "category, severity (critical|major|minor), target_type, target_id}. "
                "Where a high-confidence claim genuinely survives your attack, say so "
                "explicitly. Do not invent issues to look busy — but do not let a "
                "convenient conclusion pass."),
            "loop": "Working agent: after a review, GET /projects/{id}/rigor/findings, "
                "then for each finding either FIX it (and PUT the finding to 'resolved' "
                "with how) or justify why it holds; 'dismissed' only for genuine "
                "non-issues. Surfaced live in briefing.rigor_review; later, open "
                "critical findings will block 'supported'.",
        },
        "resume_protocol": "To CONTINUE an existing project from a cold start (a "
            "fresh agent with no prior memory): GET /projects to find it, then GET "
            "/projects/{id}/context — a single call returning the full current state "
            "PLUS the entire step-by-step reasoning history (every hypothesis, "
            "prediction, verdict, compute run, with detail) PLUS the briefing. Read it "
            "all to reconstruct exactly how the project got here before you act. The "
            "briefing alone is a per-turn digest, not the full history — use /context "
            "to resume. FIRST thing on resume: check briefing.pending_work — async "
            "steps a prior turn started (a literature query, a submitted calc) but "
            "couldn't await. Poll & ingest each, then PUT /async/{id} done. That is "
            "usually the whole reason to resume.",
        "auth": {"scheme": "Bearer", "header": "Authorization: Bearer <token>",
                 "obtain": "portal API Keys page; user must be in an allowed group"},
        "getting_started": {
            "what": "Discovery is a hypothesis-driven scientific-discovery workbench. "
                "You drive it with an AI agent (any LLM/agent with web access) that "
                "reads this manifest and self-configures — you don't need to learn the "
                "API yourself.",
            "steps": [
                "Generate a Bearer token from the portal's API Keys page.",
                "Paste the `agent_prompt` below into your agent, with your token.",
                "Your agent fetches this manifest, learns the whole protocol, and is "
                "ready to start a new discovery project or continue a shared one.",
            ],
            "agent_prompt": (
                "You are connecting to the ISAAC Discovery platform — a "
                "hypothesis-driven scientific-discovery workbench at SLAC. Bootstrap "
                "yourself:\n\n"
                "1. Read the self-describing operating manual (it defines everything: "
                "auth, all endpoints, the reasoning protocol, how to record decisions, "
                "and the literature/compute integrations):\n"
                "   GET https://isaac.slac.stanford.edu/portal/api/discovery/manifest\n"
                "   Read ALL of it and follow it exactly.\n\n"
                "2. Authenticate every request with:\n"
                "   Authorization: Bearer <PASTE_YOUR_PORTAL_API_TOKEN_HERE>\n\n"
                "3. Verify access and list projects:\n"
                "   GET https://isaac.slac.stanford.edu/portal/api/projects\n"
                "   (the projects you own or that are shared with you).\n\n"
                "4. To CONTINUE an existing project (recommended for a fresh agent): "
                "GET /projects/{id}/context — one call returns the FULL state + the "
                "ENTIRE step-by-step reasoning history (with detail) + the briefing, so "
                "you can reconstruct exactly where the project stands. To START a new "
                "one: POST /projects.\n\n"
                "5. Follow the `method` block of the manifest — it is the scientific "
                "contract, not a suggestion. Discovery here means: frame >=2 competing "
                "FALSIFIABLE hypotheses (each with a mechanism and an origin); for each, "
                "register the SET of predictions that would KILL it, every one carrying "
                "(a) a concrete falsification_criterion and (b) an `origin` recording HOW "
                "it was produced (mechanism / discrimination design / literature / prior "
                "result / reasoning, with sources); design predictions to DISCRIMINATE "
                "between hypotheses; gather method-compatible evidence; render verdicts "
                "with reasoning; propose the discriminating next experiment.\n\n"
                "6. Prime directive: the dashboard is the single source of truth. Each "
                "turn GET the project's /briefing and reconcile to it; write every "
                "hypothesis, prediction, verdict and reasoning step back via the API — "
                "if it isn't written to the dashboard, it didn't happen.\n\n"
                "7. The briefing is SELF-INSTRUCTING: its `recommended_actions` is your "
                "prioritized to-do and `method_compliance` + `rigor_review` are the live "
                "rigor audit. You do NOT need a human to spell out the next step — work "
                "the recommended_actions (declare evidence_independence, attach "
                "discriminating observables, commission an independent rigor review for "
                "high-confidence claims, resolve findings, …) until they clear.\n\n"
                "8. Check your toolbox: read the manifest's `isaac_ecosystem` and, on "
                "this first turn, tell me which of those tools you can actually reach "
                "(FairChem/UMA, VASP via NERSC, mkmISAAC, Catalysis Hub, Materials "
                "Project, the records + literature APIs) — and ask me about any you're "
                "missing so I can wire them up. Use the ISAAC records API to look up "
                "existing computed/measured values before recomputing.\n\n"
                "Then tell me, in your own words, the current state and full history of "
                "the project (or the workflow for a new one), and what to do next."
            ),
            "minimal_human_prompt": "Reproducibility note: a human should NOT need to "
                "give task-specific scientific instructions. The whole protocol — the "
                "method, the guardrails, and the per-turn to-do — comes from this "
                "manifest + the briefing. A sufficient human prompt is just: 'Connect to "
                "ISAAC Discovery with this token, resume project <id> (or start one for "
                "<goal>), and follow the manifest and the briefing's recommended_actions.'",
        },
        "reproducibility_mode": {
            "_what": "Two ways to run, kept distinct so a RECALLED result is never "
                "mistaken for an independent reproduction.",
            "independent": "A from-scratch / reproducibility run. Treat it as a "
                "genuinely BLANK agent: derive every hypothesis, number and conclusion "
                "ONLY from the ISAAC data and your own organic queries this run. Do NOT "
                "reuse, recall, or reference hypotheses/values/lessons from any prior "
                "project or session — if you find yourself 'remembering' an answer, "
                "re-derive it from the data or don't use it. For a true independent run "
                "the human should start a NEW agent session (not continue one that has "
                "seen prior runs).",
            "continued": "A capability run that intentionally builds on a prior "
                "project's lessons. Fine — but its convergence is NOT independent "
                "evidence; report it as a continuation.",
            "default": "If the human says 'from scratch' / 'fresh' / 'new project', "
                "treat it as INDEPENDENT.",
        },
        "object_model": "project -> hypotheses -> predictions; append-only events "
                        "journal; one next_experiment per project. evidence_record_ids "
                        "are plain ISAAC record IDs (read-only cross-reference).",
        "state_machines": {
            "hypothesis_status": HYPOTHESIS_STATUSES,
            "hypothesis_relation_types": sorted(RELATION_TYPES),
            "prediction_work_status": WORK_STATUS_ORDER,
            "prediction_verdict": VERDICTS,
            "compute_run_status": sorted(COMPUTE_STATUSES),
            "note": "work_status = where in the pipeline; verdict = the scientific "
                    "outcome (set at 'evaluated'). They are orthogonal. A prediction "
                    "may have MANY compute_runs (failed + resubmit). Hypotheses form a "
                    "graph via relations (supersedes/derived_from/competes_with/"
                    "co_operating), not a flat list. A prediction's `discriminates` "
                    "([{hypothesis_label, expected}]) declares what each hypothesis "
                    "predicts for that measurable; the server aggregates these into the "
                    "cross-hypothesis discrimination matrix.",
        },
        "event_types": sorted(EVENT_TYPES),
        "endpoints": [
            {"m": "GET", "path": "/projects/{id}/briefing",
             "purpose": "Curated ground-truth digest (incl. evidence-index summary, "
                        "discrimination matrix) — READ THIS FIRST each turn."},
            {"m": "GET", "path": "/projects/{id}/context",
             "purpose": "ONE-SHOT RESUME bundle: full state + the ENTIRE step-by-step "
                        "reasoning history (every event, with detail) + the briefing. "
                        "A fresh agent with no prior context calls this FIRST to fully "
                        "reconstruct an existing project before continuing."},
            {"m": "GET", "path": "/projects/{id}/evidence",
             "purpose": "Exhaustive descriptor-keyed evidence index (element-matched "
                        "candidates, reaction annotated). ?descriptor=<name> to narrow. "
                        "Query this by a prediction's descriptor before saying 'no data'."},
            {"m": "PUT", "path": "/projects/{id}/evidence_overrides",
             "purpose": "Curate the auto candidates: {include:[record_id], exclude:[...]}."},
            {"m": "POST", "path": "/projects", "purpose": "Create a project."},
            {"m": "PUT", "path": "/projects/{id}/dataset",
             "purpose": "Declare the DATASET OF INTEREST {record_ids:[...], description} "
                        "— the curated record set the human pointed you at. Anchors "
                        "scope; coverage is checked against it (briefing flags unused "
                        "records). Use ALL of it or justify exclusions; you may still "
                        "query the wider DB for corroborating data, but don't silently "
                        "drop a declared record — a different geometry/composition/"
                        "end-member may hold a discriminating contrast a confound hides."},
            {"m": "GET", "path": "/projects", "purpose": "List your projects."},
            {"m": "GET", "path": "/projects/{id}", "purpose": "Full project view."},
            {"m": "POST", "path": "/projects/{id}/hypotheses",
             "purpose": "Add a hypothesis (statement, label, origin, mechanism)."},
            {"m": "PUT", "path": "/hypotheses/{id}",
             "purpose": "Update a hypothesis's STATUS only {status}. Confidence is NOT "
                        "set here — it is COMPUTED from the prediction verdicts (see "
                        "scoring_model). Any confidence sent is ignored."},
            {"m": "PUT", "path": "/hypotheses/{id}/refine",
             "purpose": "REFINE a hypothesis in place as a new VERSION (same empirical "
                        "content, sharpened): {statement?, mechanism?, change_note, "
                        "change_type}. Keeps the node, its evidence and history. "
                        "Confidence is computed, not refined. See "
                        "epistemic_guardrails.hypothesis_individuation."},
            {"m": "POST", "path": "/projects/{id}/hypotheses",
             "purpose": "Add a hypothesis {statement, label, hypothesis_type, mechanism, "
                        "origin}. Set hypothesis_type='residual' for an explicit "
                        "NONE-OF-THE-ABOVE / the-shared-premise-is-wrong alternative — "
                        "carry one whenever you have an equivalence class so the common "
                        "premise can fail (see epistemic_guardrails.shared_premise_audit)."},
            {"m": "POST", "path": "/hypotheses/{id}/predictions",
             "purpose": "Add a FALSIFYING prediction — STRUCTURED, not one crammed "
                        "string: {descriptor_name, direction (↑/↓/non-monotonic), "
                        "reference_condition (vs what baseline), magnitude (qualitative "
                        "ok), falsification_criterion (what observation rejects it), "
                        "output_quantity, discriminates:[{hypothesis_label,expected}], "
                        "origin}. Each hypothesis needs a SET (>=2, aim 3-4) spanning "
                        "DIFFERENT descriptors — not one token prediction on one "
                        "measurable. See field_shapes.prediction."},
            {"m": "POST", "path": "/hypotheses/{id}/relations",
             "purpose": "Link hypotheses {to_hypothesis_id, relation_type, note}. For "
                        "`supersedes` also pass {discriminating_observable, "
                        "retained_vs_abandoned, change_type} — the observable on which "
                        "the new hypothesis predicts differently is what makes it new "
                        "rather than a refinement. UPSERT on (from,to,relation_type): "
                        "re-posting UPDATES in place (e.g. to attach the observable to "
                        "an earlier bare row), never duplicates."},
            {"m": "DELETE", "path": "/hypotheses/{id}/relations",
             "purpose": "Remove a relation {to_hypothesis_id, relation_type} — e.g. a "
                        "stray duplicate or one added in error."},
            {"m": "PUT", "path": "/predictions/{id}/status",
             "purpose": "Advance the prediction work_status lane. A crashed / "
                        "non-converged computation → work_status='compute_failed': it is "
                        "NOT a verdict, leaves confidence untouched, and surfaces in "
                        "pending_work + recommended_actions as a re-run to-do. (A failed "
                        "compute run auto-sets this on its prediction too.) NEVER record a "
                        "tool failure as insufficient/contradicts — the score reflects the "
                        "evidence you actually have, not what you tried and couldn't get."},
            {"m": "POST", "path": "/predictions/{id}/runs",
             "purpose": "Register a compute run {backend, engine, resource, "
                        "slurm_job_id, mlflow_run_url, status, params, metrics}. "
                        "IDEMPOTENT on (prediction_id, slurm_job_id): re-POSTing the "
                        "same job updates it, never duplicates."},
            {"m": "PUT", "path": "/runs/{run_id}",
             "purpose": "Update a compute run {status, metrics, mlflow_run_url, ...}."},
            {"m": "DELETE", "path": "/runs/{run_id}",
             "purpose": "Delete a compute run (e.g. a stray duplicate)."},
            {"m": "PUT", "path": "/predictions/{id}/evaluate",
             "purpose": "Terminal: set verdict (supports|contradicts|neutral|insufficient"
                        "|blocked) + strength (weak|moderate|strong) + evidence + "
                        "mlflow_run_url + evidence_independence. THIS is what moves the "
                        "hypothesis's confidence — the platform recomputes & stores it "
                        "from all the verdicts (scoring_model). Use verdict='blocked' when "
                        "the evidence isn't validly comparable (schema gate). If the "
                        "supporting model was fit to the data you're testing against, the "
                        "honest verdict is 'neutral', not 'supports' (use-novelty)."},
            {"m": "POST", "path": "/projects/{id}/rigor/findings",
             "purpose": "INDEPENDENT CRITIC records a rigor problem {summary, detail, "
                        "category(use_novelty|individuation|falsifiability|"
                        "evidence_compatibility|confirmation_bias|overreach|"
                        "shared_premise|other), severity"
                        "(critical|major|minor), target_type, target_id}. See "
                        "rigor_review.critic_prompt."},
            {"m": "GET", "path": "/projects/{id}/rigor/findings",
             "purpose": "List rigor findings (?status=open). Working agent reads these "
                        "and resolves each."},
            {"m": "PUT", "path": "/rigor/findings/{finding_id}",
             "purpose": "Close a finding {status: resolved|dismissed, resolution}. "
                        "'resolved' = fixed or justified; 'dismissed' = not a real issue. "
                        "Never deletes — keeps the audit trail."},
            {"m": "POST", "path": "/projects/{id}/async",
             "purpose": "Record RESUMABLE pending work you started but can't await this "
                        "turn {kind: literature|compute|external, external_ref, summary, "
                        "poll_hint, prediction_id?}. Makes the dashboard show the project "
                        "has steps worth coming back for. (A literature search auto-"
                        "records one if you pass project_id to /literature/search.)"},
            {"m": "GET", "path": "/projects/{id}/async",
             "purpose": "List async tasks (?status=pending). Reconcile these on resume."},
            {"m": "PUT", "path": "/async/{task_id}",
             "purpose": "Resolve a task {status: ready|done|failed} once you've polled/"
                        "ingested it. 'done' = reconciled."},
            {"m": "POST", "path": "/projects/{id}/events",
             "purpose": "Append a reasoning-transcript entry (one per step)."},
            {"m": "PUT", "path": "/projects/{id}/next_experiment",
             "purpose": "Propose the discriminating next experiment."},
            {"m": "POST", "path": "/projects/{id}/share",
             "purpose": "Owner shares the project (read) with another portal identity "
                        "{identity, access}; it then appears in that user's tab."},
            {"m": "DELETE", "path": "/projects/{id}/share/{identity}",
             "purpose": "Revoke a share."},
        ],
        "per_turn_loop": [
            "GET /briefing", "reason", "write each move (hypotheses/predictions/"
            "evaluate/status)", "POST /events per step", "PUT /next_experiment"],
        "compute_loop": [
            "FIRST query isaac_data_sources (+ literature) for an EXISTING value — don't "
            "recompute what's archived",
            "submit the calc using YOUR environment's tools (MLIP e.g. FairChem UMA to "
            "screen; DFT e.g. VASP@NERSC to confirm) — see integrations.compute",
            "PUT /predictions/{id}/status {work_status:'compute_submitted', mlflow_run_url}",
            "PUT ... {work_status:'compute_running'} when it starts",
            "PUT /predictions/{id}/evaluate with verdict + evidence + final mlflow_run_url"],
        "field_shapes": {
            "prediction": {"_for": "a hypothesis carries a SET of these (>=2, aim 3-4), "
                       "spanning DIFFERENT descriptors — each fully structured, never "
                       "one crammed string.",
                       "descriptor_name": "the measurable (e.g. faradaic_efficiency.C2H4, "
                       "adsorption_energy.CO, a partial current) — VARY it across the set",
                       "direction": "↑ / ↓ / non-monotonic / flat",
                       "reference_condition": "vs WHAT baseline (e.g. 'vs pure-Cu')",
                       "magnitude": "how much (qualitative ok, e.g. 'scales with X')",
                       "falsification_criterion": "the observation that REJECTS the "
                       "hypothesis",
                       "discriminates": "[{hypothesis_label, expected}]",
                       "origin": "see prediction_origin"},
            "origin": {"type": "agent_reasoning|literature|prior_result|human",
                       "summary": "str", "reasoning": "str",
                       "sources": "[{record_id|doi|hypothesis}]"},
            "prediction_origin": {"_for": "how a FALSIFYING prediction was produced",
                       "type": "derived_from_mechanism|discrimination_design|literature|"
                                "prior_result|agent_reasoning",
                       "summary": "str (one line: where it came from)",
                       "reasoning": "str (why this measurable falsifies the hypothesis)",
                       "sources": "[{record_id|doi}]"},
            "event": {"event_type": "(required, from event_types)",
                      "summary": "(REQUIRED, one line)", "detail": "(optional, long)",
                      "hypothesis_id": "optional", "evidence_record_ids": "optional",
                      "mlflow_run_url": "optional"},
            "next_experiment": {"_semantics": "PUT REPLACES the whole object (not a "
                                "merge); send the complete payload each time. ALL keys "
                                "you send are stored — nothing is dropped.",
                                "descriptor": "str", "facility": "str", "method": "str",
                                "rationale": "str",
                                "discriminates": "[{hypothesis_label, expected}]"},
            "evidence_independence": {"_for": "USE-NOVELTY on a prediction verdict — "
                                "declare what the supporting model was fit to vs tested "
                                "against, so circular confirmation is visible.",
                                "model_was_fit": "bool",
                                "parameters_fit_to": "[evidence_id] (data the model was "
                                "tuned on)",
                                "tested_against": "[evidence_id] (data the verdict leans "
                                "on)",
                                "roles": "[{evidence: id, role: built_from|tested_against}]",
                                "_check": "if parameters_fit_to ∩ tested_against ≠ ∅, the "
                                "match is a consistency check, not confirmation → verdict "
                                "should be 'neutral'."},
            "supersedes_relation": {"_for": "individuation — why the new hypothesis is "
                                "new, not a refinement.",
                                "discriminating_observable": "str (the realizable "
                                "observable on which new vs old predict DIFFERENTLY — "
                                "sign/ordering/scale, not just a tighter number)",
                                "retained_vs_abandoned": "str (what carried over vs was "
                                "dropped)",
                                "change_type": "mechanism_change|scope_change "
                                "(parameter_refinement → refine in place instead)"},
            "refine": {"_for": "PUT /hypotheses/{id}/refine — a new VERSION of the SAME "
                                "node (same empirical content, sharpened). Confidence is "
                                "computed from verdicts, not refined.",
                                "statement": "str?", "mechanism": "obj?",
                                "change_note": "str",
                                "change_type": "refinement|reparameterization|rewording"},
        },
        "auditability": "Record EVERY decision point in BOTH places (dual-write): "
            "(1) POST an `event` to the dashboard with a `detail` carrying the full "
            "reasoning — this is canonical and drives the briefing; (2) mirror the same "
            "step to MLflow (see integrations.experiment_tracking). Put the decision "
            "logic in each prediction's `rationale` (method-compat check + direction + "
            "magnitude-vs-falsification + replication). One-line summaries are not "
            "enough — if it isn't recorded in full, it can't be audited. Any verdict "
            "that leaned on COMPUTE or a fitted MODEL MUST carry an mlflow_run_url (the "
            "replay trace); the briefing flags compute/model verdicts that lack one.",
        "integrations": {
            "isaac_data_sources": {
                "purpose": "ISAAC is itself a primary KNOWLEDGE SOURCE — query it BEFORE "
                    "computing anything from scratch. It holds EXPERIMENTAL records "
                    "(performance, characterization) AND COMPUTATIONAL records: DFT slabs "
                    "with adsorbates (e.g. Cu(100) + CO/OH/CHO), adsorption energies, "
                    "activation barriers, band gaps, XANES, ... Look up a value that's "
                    "already archived instead of recomputing it, and cite the record as "
                    "evidence.",
                "how": "GET /portal/api/records?limit=N&offset=N (+ GET /records/{id} for "
                    "full data). Filter by material/elements, "
                    "context.electrochemistry.reaction, record_domain "
                    "(performance|characterization|simulation), and descriptor names "
                    "(adsorption_energy, activation_barrier, faradaic_efficiency.*, "
                    "xanes.*, band_gap, ...). The full corpus is large — page or filter; "
                    "don't assume the first 500 are all of it.",
                "external_reference_dbs": "If your environment can reach them, also "
                    "consult external DBs for prior values — e.g. Catalysis Hub "
                    "(adsorption energies, reaction energetics), Materials Project "
                    "(formation/stability). Treat as analog evidence; check method "
                    "compatibility (functional/output_quantity) before trusting.",
            },
            "compute": {
                "purpose": "Run the calculations your hypotheses need. The platform does "
                    "NOT run them — it RECORDS and replays them (compute_run + MLflow). "
                    "Use whatever tools your ENVIRONMENT provides.",
                "tiers": "MLIP / ML-potentials (FAST screening — e.g. FairChem UMA) for "
                    "adsorption energies / trends across many candidates; DFT (ACCURATE — "
                    "e.g. VASP) via your HPC path (e.g. NERSC) for the key numbers a "
                    "verdict rests on; microkinetics / reaction-diffusion / transport "
                    "models for rates and length scales. Screen with MLIP, confirm the "
                    "decisive ones with DFT.",
                "record_it": "Register each as a compute_run (POST /predictions/{id}/runs "
                    "{backend, engine, resource, slurm_job_id, mlflow_run_url, params, "
                    "metrics}); a compute/model-backed verdict MUST carry an "
                    "mlflow_run_url. And query isaac_data_sources for an EXISTING result "
                    "before spending a calculation.",
                "persist_results_as_records": "CLOSE THE LOOP — when a calculation "
                    "produces a reusable value (an adsorption energy, a barrier, a "
                    "relaxed structure), PERSIST it into ISAAC so it never has to be "
                    "recomputed: build a schema-valid computational record, dry-run it "
                    "with POST /portal/api/validate, then POST /portal/api/records. Mark "
                    "provenance clearly AGENT-COMPUTED (method/functional or MLIP model, "
                    "params, the MLflow run, this project_id). Then cite the new "
                    "record_id as evidence. This is how the repository compounds — your "
                    "calc becomes everyone's data, and the next agent looks it up instead "
                    "of recomputing.",
                "your_specific_tools": "The EXACT binaries, HPC submission paths, API "
                    "endpoints and credentials available to you depend on WHERE you run "
                    "and WHO runs you (e.g. an S3DF session with FairChem + a NERSC/IRI "
                    "submission API) — they are NOT in this generic manifest. Use the "
                    "tools configured in your session; if your operator gave you a "
                    "capabilities profile, follow it; always record what you used so the "
                    "run is reproducible.",
            },
            "experiment_tracking_mlflow": {
                "purpose": "MLflow is the unified experiment-replay trace — it logs "
                    "the COMPUTE *and*, now, the full REASONING, so an MLflow run is a "
                    "self-contained record of the whole discovery cycle.",
                "convention": "One MLflow experiment per project, named "
                    "`ISAAC-Discovery-<project_id>`; one run per hypothesis (or per "
                    "project for the reasoning stream).",
                "log_every_thinking_step": "After you POST each dashboard event "
                    "(canonical), MIRROR it to MLflow: mlflow.log_text(json.dumps(step), "
                    "f'reasoning/{n:04d}.json') and increment a 'reasoning_step' metric, "
                    "so the run holds the complete, ordered decision sequence — every "
                    "hypothesis formed, every prediction, every verdict and why.",
                "compute": "Log params (functional, slab, …), metrics (E_ads, scores), "
                    "and the Slurm job IDs as tags; put the run URL on the dashboard "
                    "compute_run.mlflow_run_url so the two cross-link.",
                "cross_link": "Tag every MLflow run with project_id + the dashboard URL; "
                    "store mlflow_run_url back on the dashboard event/run.",
                "source_of_truth": "The DASHBOARD is canonical (the briefing reads it). "
                    "MLflow mirrors for replay — write the dashboard FIRST, then mirror, "
                    "so they never diverge.",
            },
            "literature_search": {
                "purpose": "Cited literature search over the published corpus. The "
                    "portal proxies Edison Scientific (FutureHouse PaperQA3) so you get "
                    "agentic, reference-backed answers with sources — without ever "
                    "handling the Edison API key. Use it to ground a hypothesis origin "
                    "or cross-check a prediction against prior work.",
                "provider": "Edison Scientific (FutureHouse PaperQA3)",
                "via": "portal_proxy — the portal holds the Edison key server-side; "
                    "you never see it.",
                "submit": "POST /literature/search {query, job, project_id?} -> {task_id} "
                    "(202). job ∈ literature | literature_high | precedent | analysis. "
                    "Pass project_id to auto-record it as resumable pending_work so the "
                    "dashboard shows the query is in flight.",
                "poll": "GET /literature/search/{task_id} -> {status, done, answer, "
                    "sources}. Async: PaperQA3 takes ~2-5 min; poll until done.",
                "auth": "your existing portal Bearer token (no Edison key needed).",
                "use_when": "forming a hypothesis `origin`, or cross-checking a "
                    "prediction against published work.",
                "note": "The OLD api.edisonsci.com host is DECOMMISSIONED. Direct REST "
                    "(only if the proxy is ever unavailable) is "
                    "api.platform.edisonscientific.com with an api_key->JWT exchange at "
                    "/auth/login. Prefer the proxy.",
            },
        },
        "vocabulary_is_normalized": "Verdicts and relation_types are accept-and-"
            "normalized: synonyms (e.g. 'refutes'->'contradicts', "
            "'co_operates_with'->'co_operating', 'inconclusive'->'neutral') are "
            "mapped to canonical on write. Prefer the canonical terms above.",
        "invariant": "If it is not on the dashboard, it did not happen.",
    }


def new_ulid() -> str:
    """26-char Crockford-base32 ULID (48-bit time + 80-bit randomness)."""
    val = ((int(time.time() * 1000) & ((1 << 48) - 1)) << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    return "".join(reversed(out))


def _conn():
    return database.get_discovery_db_connection()


def _append_event(cur, project_id, event_type, summary, *, detail=None,
                  hypothesis_id=None, evidence_record_ids=None,
                  mlflow_run_url=None, actor=None):
    """Insert one activity-feed row. Caller owns the transaction/commit."""
    cur.execute(
        """INSERT INTO hyp_events
             (project_id, hypothesis_id, event_type, summary, detail,
              evidence_record_ids, mlflow_run_url, actor_identity)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (project_id, hypothesis_id, event_type, summary, detail,
         evidence_record_ids, mlflow_run_url, actor))
    return cur.fetchone()["id"]


def _project_of_hypothesis(cur, hypothesis_id):
    cur.execute("SELECT project_id FROM hyp_hypotheses WHERE hypothesis_id=%s",
                (hypothesis_id,))
    row = cur.fetchone()
    return row["project_id"] if row else None


def _is_owner(cur, project_id, identity):
    cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s AND owner_identity=%s",
                (project_id, identity))
    return cur.fetchone() is not None


def _can_read(cur, project_id, identity):
    """Owner OR anyone the project is shared with."""
    if _is_owner(cur, project_id, identity):
        return True
    cur.execute("SELECT 1 FROM hyp_project_shares WHERE project_id=%s AND identity=%s",
                (project_id, identity))
    return cur.fetchone() is not None


def share_project(project_id, identity, *, access="read", owner_identity=None) -> bool:
    """Owner-only: grant another portal identity access to this project."""
    if not identity:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        if owner_identity is not None and not _is_owner(cur, project_id, owner_identity):
            return False
        cur.execute(
            """INSERT INTO hyp_project_shares (project_id, identity, access, granted_by)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (project_id, identity)
               DO UPDATE SET access=EXCLUDED.access""",
            (project_id, identity.strip(), access if access in ("read", "write") else "read",
             owner_identity))
        _append_event(cur, project_id, "status_changed",
                      f"Project shared with {identity.strip()} ({access})",
                      actor=owner_identity)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def unshare_project(project_id, identity, *, owner_identity=None) -> bool:
    conn = _conn()
    cur = conn.cursor()
    try:
        if owner_identity is not None and not _is_owner(cur, project_id, owner_identity):
            return False
        cur.execute("DELETE FROM hyp_project_shares WHERE project_id=%s AND identity=%s "
                    "RETURNING id", (project_id, identity))
        ok = cur.fetchone() is not None
        conn.commit()
        return ok
    finally:
        cur.close()
        conn.close()


# --- Projects --------------------------------------------------------------

def create_project(owner_identity, title, goal=None, material_system=None,
                   reaction=None) -> str:
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_projects
                 (project_id, owner_identity, title, goal, material_system, reaction)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (project_id, owner_identity, title, goal, material_system, reaction))
        _append_event(cur, project_id, "project_created",
                      f"Project created: {title}", actor=owner_identity)
        conn.commit()
        return project_id
    finally:
        cur.close()
        conn.close()


def list_projects(owner_identity) -> list:
    """Project cards visible to a user: ones they OWN plus ones SHARED with them.
    Each row carries `is_owner` + `owner_identity` so the UI can mark shared ones."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.project_id, p.title, p.goal, p.status, p.material_system,
                      p.reaction, p.updated_at, p.owner_identity,
                      (p.owner_identity = %s) AS is_owner,
                      COUNT(h.hypothesis_id) AS n_hypotheses
                 FROM hyp_projects p
                 LEFT JOIN hyp_hypotheses h ON h.project_id = p.project_id
                WHERE p.owner_identity = %s
                   OR p.project_id IN (SELECT project_id FROM hyp_project_shares
                                       WHERE identity = %s)
                GROUP BY p.id
                ORDER BY p.updated_at DESC""",
            (owner_identity, owner_identity, owner_identity))
        projects = cur.fetchall()
        for p in projects:
            cur.execute(
                """SELECT label, statement, confidence, status
                     FROM hyp_hypotheses
                    WHERE project_id = %s
                    ORDER BY confidence DESC NULLS LAST LIMIT 1""",
                (p["project_id"],))
            p["leading_hypothesis"] = cur.fetchone()
        return projects
    finally:
        cur.close()
        conn.close()


def get_project(project_id, owner_identity=None) -> dict | None:
    """Full project view: hypotheses (each with predictions), events, next_exp.

    `owner_identity` here is the REQUESTER. Returns None unless they can read the
    project (owner OR shared-with). API scoping is enforced by the caller too."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM hyp_projects WHERE project_id=%s", (project_id,))
        project = cur.fetchone()
        if project is None:
            return None
        if owner_identity is not None and not _can_read(cur, project_id, owner_identity):
            return None
        cur.execute("SELECT identity, access FROM hyp_project_shares WHERE project_id=%s "
                    "ORDER BY created_at", (project_id,))
        project["shared_with"] = cur.fetchall()
        cur.execute(
            """SELECT * FROM hyp_hypotheses WHERE project_id=%s
               ORDER BY confidence DESC NULLS LAST, created_at""",
            (project_id,))
        hypotheses = cur.fetchall()
        for h in hypotheses:
            cur.execute(
                """SELECT * FROM hyp_predictions WHERE hypothesis_id=%s
                   ORDER BY created_at""",
                (h["hypothesis_id"],))
            h["predictions"] = cur.fetchall()
            for p in h["predictions"]:
                cur.execute(
                    """SELECT * FROM hyp_compute_runs WHERE prediction_id=%s
                       ORDER BY created_at""", (p["prediction_id"],))
                p["compute_runs"] = cur.fetchall()
        cur.execute(
            """SELECT * FROM hyp_hypothesis_relations WHERE project_id=%s
               ORDER BY created_at""", (project_id,))
        relations = cur.fetchall()
        cur.execute(
            """SELECT * FROM hyp_events WHERE project_id=%s
               ORDER BY created_at DESC LIMIT 200""",
            (project_id,))
        events = cur.fetchall()
        return {"project": project, "hypotheses": hypotheses, "events": events,
                "relations": relations,
                "next_experiment": project.get("next_experiment")}
    finally:
        cur.close()
        conn.close()


def set_next_experiment(project_id, payload, actor=None) -> bool:
    """REPLACE the project's next_experiment with the full payload the agent
    sends — ALL keys preserved (no silent drop), plus a server proposed_at. PUT
    is replace-not-merge: send the complete object each time."""
    if not isinstance(payload, dict):
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        stored = dict(payload)
        stored["proposed_at"] = _now_iso()
        cur.execute(
            "UPDATE hyp_projects SET next_experiment=%s, updated_at=NOW() "
            "WHERE project_id=%s",
            (json.dumps(stored), project_id))
        if cur.rowcount == 0:
            return False
        desc = payload.get("descriptor") or payload.get("title") or "experiment"
        _append_event(cur, project_id, "next_experiment_proposed",
                      f"Next experiment proposed: {desc} "
                      f"({payload.get('method', '')} @ {payload.get('facility', '')})",
                      detail=payload.get("rationale"), actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Hypotheses ------------------------------------------------------------

def _snapshot_confidence(cur, project_id, hypothesis_id, confidence, *,
                         basis=None, source="updated"):
    """Append one row to the confidence time series. Called on every confidence
    change so the Belief River reads real history (not scraped event prose)."""
    if confidence is None:
        return
    cur.execute(
        """INSERT INTO hyp_confidence_snapshots
             (project_id, hypothesis_id, confidence, basis, source)
           VALUES (%s,%s,%s,%s,%s)""",
        (project_id, hypothesis_id, float(confidence), basis, source))


def create_hypothesis(project_id, statement, *, label=None, hypothesis_type=None,
                      mechanism=None, origin=None, created_by=None) -> str | None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return None
        hypothesis_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_hypotheses
                 (hypothesis_id, project_id, label, statement, hypothesis_type,
                  mechanism, origin, confidence, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (hypothesis_id, project_id, label, statement, hypothesis_type,
             json.dumps(mechanism) if mechanism is not None else None,
             json.dumps(origin) if origin is not None else None, 0.5, created_by))
        _append_event(cur, project_id, "hypothesis_created",
                      f"Hypothesis {label or ''} added: {statement[:120]}",
                      hypothesis_id=hypothesis_id, actor=created_by)
        # born at the 0.5 PRIOR (no evidence yet); evidence moves it via evaluate
        _snapshot_confidence(cur, project_id, hypothesis_id, 0.5, source="created")
        cur.execute("UPDATE hyp_projects SET updated_at=NOW() WHERE project_id=%s",
                    (project_id,))
        conn.commit()
        return hypothesis_id
    finally:
        cur.close()
        conn.close()


def update_hypothesis(hypothesis_id, *, status=None, actor=None, **_ignored) -> bool:
    """Update a hypothesis's STATUS only. Confidence is NOT settable here — it is
    COMPUTED from the prediction verdicts (see compute_hypothesis_score) and stored
    by evaluate_prediction. Any confidence/confidence_basis passed in is ignored
    (kept in the signature only so legacy callers don't error)."""
    if status is None:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, hypothesis_id)
        if project_id is None:
            return False
        cur.execute("UPDATE hyp_hypotheses SET status=%s, updated_at=NOW() "
                    "WHERE hypothesis_id=%s", (status, hypothesis_id))
        _append_event(cur, project_id, "status_changed",
                      f"Hypothesis status → {status}",
                      hypothesis_id=hypothesis_id, actor=actor)
        cur.execute("UPDATE hyp_projects SET updated_at=NOW() WHERE project_id=%s",
                    (project_id,))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Predictions -----------------------------------------------------------

def create_prediction(hypothesis_id, descriptor_name, *, label=None, direction=None,
                      reference_condition=None, magnitude=None, output_quantity=None,
                      falsification_criterion=None, discriminates=None, origin=None,
                      actor=None) -> str | None:
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, hypothesis_id)
        if project_id is None:
            return None
        prediction_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_predictions
                 (prediction_id, hypothesis_id, label, descriptor_name, direction,
                  reference_condition, magnitude, output_quantity,
                  falsification_criterion, discriminates, origin)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (prediction_id, hypothesis_id, label, descriptor_name, direction,
             reference_condition, magnitude, output_quantity, falsification_criterion,
             json.dumps(discriminates) if discriminates is not None else None,
             json.dumps(origin) if origin is not None else None))
        _append_event(cur, project_id, "prediction_added",
                      f"Prediction added: {descriptor_name} ({direction or '?'})",
                      hypothesis_id=hypothesis_id, actor=actor)
        conn.commit()
        return prediction_id
    finally:
        cur.close()
        conn.close()


def evaluate_prediction(prediction_id, verdict, *, strength=None,
                        evidence_record_ids=None, rationale=None,
                        mlflow_run_url=None, evidence_independence=None,
                        actor=None) -> bool:
    """Terminal verdict on a prediction. `evidence_independence` declares
    USE-NOVELTY: which evidence was used to BUILD/fit the supporting model vs to
    TEST it. {model_was_fit:bool, parameters_fit_to:[id], tested_against:[id],
    roles:[{evidence,role:built_from|tested_against}]}. If the same data both
    built and tested a model, a 'supports' verdict is circular — surfaced in
    method_compliance now (not yet auto-downgraded)."""
    verdict = normalize_verdict(verdict)
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.prediction_id = %s""",
            (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            """UPDATE hyp_predictions
                  SET verdict=%s, strength=%s, evidence_record_ids=%s,
                      rationale=%s, mlflow_run_url=%s, evidence_independence=%s,
                      work_status='evaluated', updated_at=NOW()
                WHERE prediction_id=%s""",
            (verdict, strength, evidence_record_ids, rationale, mlflow_run_url,
             json.dumps(evidence_independence) if evidence_independence is not None
             else None, prediction_id))
        _circ = _circularity_flag(evidence_independence)
        _detail = rationale
        if _circ:
            _detail = (f"{rationale + chr(10) if rationale else ''}"
                       f"⚠ use-novelty: {_circ}")
        _append_event(cur, row["project_id"], "prediction_evaluated",
                      f"Prediction evaluated: {row['descriptor_name']} → "
                      f"{verdict} ({strength or '?'})",
                      detail=_detail, hypothesis_id=row["hypothesis_id"],
                      evidence_record_ids=evidence_record_ids,
                      mlflow_run_url=mlflow_run_url, actor=actor)
        # CANONICAL: confidence is recomputed from the verdicts and stored here —
        # this is the ONLY thing that moves a hypothesis's confidence.
        _recompute_and_store_confidence(cur, row["hypothesis_id"], actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def _circularity_flag(ind) -> str | None:
    """Return a human-readable use-novelty warning if the declared evidence
    independence reveals double-counting (data used to BUILD a model also used
    to TEST it), else None. Domain-agnostic — purely a set-intersection check."""
    if not isinstance(ind, dict):
        return None
    fit = set(ind.get("parameters_fit_to") or [])
    tested = set(ind.get("tested_against") or [])
    overlap = fit & tested
    if overlap:
        return ("evidence used to fit the model is also being used to test it "
                f"({', '.join(sorted(overlap))}) — counts as a consistency check, "
                "not independent confirmation")
    roles = ind.get("roles") or []
    if isinstance(roles, list):
        seen = {}
        for r in roles:
            if isinstance(r, dict) and r.get("evidence"):
                seen.setdefault(r["evidence"], set()).add(r.get("role"))
        dual = [e for e, rs in seen.items()
                if {"built_from", "tested_against"} <= rs]
        if dual:
            return (f"evidence both built and tested the hypothesis "
                    f"({', '.join(sorted(dual))})")
    return None


def _backfill_confidence_snapshots(cur, project_id):
    """One-time migration for legacy projects with no snapshots: reconstruct the
    confidence time series from the event log (the same 'confidence → N' the API
    writes on every change) + a creation baseline + the current value, stamped
    with the original event timestamps. Idempotent: only runs when zero snapshots
    exist for the project."""
    import re as _re
    cur.execute("""SELECT hypothesis_id, confidence, created_at, updated_at
                     FROM hyp_hypotheses WHERE project_id=%s""", (project_id,))
    hyps = cur.fetchall()
    valid = {h["hypothesis_id"] for h in hyps}
    cur.execute("""SELECT hypothesis_id, summary, created_at FROM hyp_events
                    WHERE project_id=%s ORDER BY created_at, id""", (project_id,))
    events = cur.fetchall()
    rows = [(project_id, h["hypothesis_id"], 0.0, "created", h["created_at"])
            for h in hyps]
    pat = _re.compile(r"confidence[^0-9]*([0-9]*\.?[0-9]+)")
    for e in events:
        hid = e.get("hypothesis_id")
        if hid in valid and e.get("summary"):
            m = pat.search(e["summary"])
            if m:
                rows.append((project_id, hid, float(m.group(1)), "backfill",
                             e["created_at"]))
    for h in hyps:
        if h["confidence"] is not None:
            rows.append((project_id, h["hypothesis_id"], float(h["confidence"]),
                         "current", h["updated_at"] or h["created_at"]))
    cur.executemany(
        """INSERT INTO hyp_confidence_snapshots
             (project_id, hypothesis_id, confidence, source, created_at)
           VALUES (%s,%s,%s,%s,%s)""", rows)


def get_confidence_history(project_id, owner_identity=None) -> list:
    """The confidence time series for a project — one point per change, ordered.
    Backfills legacy projects from their event log on first read. Returns
    [{hypothesis_id, confidence, source, created_at}]. (Access is gated upstream
    by the page/briefing that calls this.)"""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return []
        cur.execute("SELECT COUNT(*) AS n FROM hyp_confidence_snapshots "
                    "WHERE project_id=%s", (project_id,))
        if (cur.fetchone()["n"] or 0) == 0:
            _backfill_confidence_snapshots(cur, project_id)
            conn.commit()
        cur.execute("""SELECT hypothesis_id, confidence, source, created_at
                         FROM hyp_confidence_snapshots WHERE project_id=%s
                        ORDER BY created_at, id""", (project_id,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def refine_hypothesis(hypothesis_id, *, statement=None, mechanism=None,
                      change_note=None, change_type="refinement",
                      actor=None, **_ignored) -> int | None:
    """Refine a hypothesis IN PLACE as a new VERSION (not a new node). Use this
    when the empirical content is the same and you are only sharpening it (tighter
    parameter, clearer wording, updated mechanism narrative). For a genuinely new
    claim that predicts differently, create a new hypothesis + add_relation(
    'supersedes', discriminating_observable=...). Snapshots the prior state into
    hyp_hypothesis_versions and bumps `version`. Returns the new version number."""
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, hypothesis_id)
        if project_id is None:
            return None
        cur.execute("""SELECT version, statement, mechanism, confidence, label
                         FROM hyp_hypotheses WHERE hypothesis_id=%s""",
                    (hypothesis_id,))
        cur_row = cur.fetchone()
        if cur_row is None:
            return None
        old_v = cur_row["version"] or 1
        # snapshot the CURRENT (about-to-be-replaced) state as the old version
        cur.execute(
            """INSERT INTO hyp_hypothesis_versions
                 (hypothesis_id, version, statement, mechanism, confidence,
                  change_note, change_type, actor_identity)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (hypothesis_id, version) DO NOTHING""",
            (hypothesis_id, old_v, cur_row["statement"],
             json.dumps(cur_row["mechanism"]) if cur_row["mechanism"] is not None else None,
             cur_row["confidence"], None, None, None))
        new_v = old_v + 1
        sets, vals = ["version=%s"], [new_v]
        if statement is not None:
            sets.append("statement=%s"); vals.append(statement)
        if mechanism is not None:
            sets.append("mechanism=%s"); vals.append(json.dumps(mechanism))
        # confidence is NOT refined here — it is computed from the prediction verdicts.
        vals.append(hypothesis_id)
        cur.execute(f"UPDATE hyp_hypotheses SET {', '.join(sets)}, updated_at=NOW() "
                    f"WHERE hypothesis_id=%s", vals)
        # record the NEW version row too (so history is complete + carries note)
        cur.execute(
            """INSERT INTO hyp_hypothesis_versions
                 (hypothesis_id, version, statement, mechanism, confidence,
                  change_note, change_type, actor_identity)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (hypothesis_id, version) DO NOTHING""",
            (hypothesis_id, new_v,
             statement if statement is not None else cur_row["statement"],
             json.dumps(mechanism) if mechanism is not None else (
                 json.dumps(cur_row["mechanism"]) if cur_row["mechanism"] is not None else None),
             cur_row["confidence"],  # carried, never refined — confidence is computed from verdicts
             change_note, change_type, actor))
        _append_event(cur, project_id, "status_changed",
                      f"Hypothesis refined → v{new_v} "
                      f"({cur_row['label'] or hypothesis_id[:6]})",
                      detail=change_note, hypothesis_id=hypothesis_id, actor=actor)
        cur.execute("UPDATE hyp_projects SET updated_at=NOW() WHERE project_id=%s",
                    (project_id,))
        conn.commit()
        return new_v
    finally:
        cur.close()
        conn.close()


def set_prediction_status(prediction_id, work_status, *, mlflow_run_url=None,
                          actor=None) -> bool:
    """Advance a prediction through its workflow lifecycle (compute_submitted /
    compute_running / compute_failed / more_work_pending / awaiting_evidence). Use
    evaluate() to reach the terminal 'evaluated' state with a verdict. 'compute_failed'
    marks a crashed/non-converged calc: it is NOT a verdict and leaves confidence
    untouched (the prediction is simply unevaluated) — it becomes a to-do to re-run."""
    if work_status not in WORK_STATUSES:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.prediction_id = %s""", (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return False
        if mlflow_run_url is not None:
            cur.execute("UPDATE hyp_predictions SET work_status=%s, "
                        "mlflow_run_url=%s, updated_at=NOW() WHERE prediction_id=%s",
                        (work_status, mlflow_run_url, prediction_id))
        else:
            cur.execute("UPDATE hyp_predictions SET work_status=%s, "
                        "updated_at=NOW() WHERE prediction_id=%s",
                        (work_status, prediction_id))
        etype = ("compute_submitted" if work_status == "compute_submitted"
                 else "compute_running" if work_status == "compute_running"
                 else "compute_failed" if work_status == "compute_failed"
                 else "status_changed")
        _append_event(cur, row["project_id"], etype,
                      f"Prediction {row['descriptor_name']} → {work_status}",
                      hypothesis_id=row["hypothesis_id"],
                      mlflow_run_url=mlflow_run_url, actor=actor)
        # moving OUT of 'evaluated' changes the score → recompute
        _recompute_and_store_confidence(cur, row["hypothesis_id"], actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Convergence / saturation (representation fix: progress != leader confidence) -

def _prediction_discriminates(p, survivor_labels) -> bool:
    """True if this prediction's declared `discriminates` separates >=2 of the given
    survivors (names them with DIFFERING expected outcomes). Domain-agnostic: it
    reads only the discriminates graph, never the science."""
    disc = p.get("discriminates") or []
    touch = [(d.get("hypothesis_label"), d.get("expected")) for d in disc
             if isinstance(d, dict) and d.get("hypothesis_label") in survivor_labels]
    labels = {lbl for lbl, _ in touch}
    expectations = {exp for _, exp in touch}
    return len(labels) >= 2 and len(expectations) >= 2


def compute_convergence(hyps, relations, next_experiment=None) -> dict:
    """Detect contested clusters of SURVIVING hypotheses and whether the existing
    evidence can still separate them — so the platform can report 'settled
    phenomenon, one experiment away' instead of letting honest low confidence read
    as regression. Computed purely from state; never freezes confidence (we freeze
    the DECISION, not the posterior — auditing won't separate observationally
    identical rivals, only an experiment will)."""
    alive = {h["hypothesis_id"]: h for h in hyps
             if h["status"] not in ("eliminated", "superseded")}
    label = {h["hypothesis_id"]: h["label"] for h in hyps}

    # contested clusters = connected components of competes_with among survivors.
    # ONLY competes_with — `co_operating` hypotheses work together, they are not
    # rivals in a discriminating contest and must not be pulled into the cluster.
    adj = {hid: set() for hid in alive}
    for r in relations or []:
        if r.get("relation_type") == "competes_with":
            a, b = r.get("from_hypothesis_id"), r.get("to_hypothesis_id")
            if a in alive and b in alive:
                adj[a].add(b)
                adj[b].add(a)
    seen, clusters = set(), []
    for hid in alive:
        if hid in seen:
            continue
        comp, stack = [], [hid]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(adj[x] - seen)
        if len(comp) >= 2:
            clusters.append(comp)

    # carry the owner label: a test only resolves a cluster if its OWNER is a
    # member of that cluster (else a third hypothesis's test that merely NAMES the
    # members — who may AGREE against it — would be mistaken for a discriminator).
    all_preds = [(h["label"], p) for h in hyps for p in h["predictions"]]
    conf_of = {h["label"]: float(h["confidence"] or 0) for h in hyps}
    out_clusters = []
    equivalence_classes = []
    worst = "decided"  # decided < resolving < blocked_on_experiment < no_test
    rank = {"decided": 0, "resolving": 1,
            "blocked_on_experiment": 2, "no_discriminating_test": 3}
    for comp in clusters:
        slabels = {label[x] for x in comp}
        # A discriminating test only counts as "run/resolving" if its verdict
        # ACTUALLY separated the survivors (supports/contradicts). A neutral or
        # insufficient verdict means the test was tried but COULDN'T discriminate
        # (e.g. confounded data) — the survivors are still observationally
        # identical, so it must NOT read as 'resolving'.
        disc_run = [p for (owner, p) in all_preds
                    if owner in slabels
                    and p.get("work_status") == "evaluated"
                    and normalize_verdict(p.get("verdict")) in ("supports", "contradicts")
                    and _prediction_discriminates(p, slabels)]
        # genuinely NOT-yet-run discriminating tests (a neutral-evaluated test is
        # neither: it was tried and couldn't separate them — re-running won't help)
        disc_unrun = [p for (owner, p) in all_preds
                      if owner in slabels
                      and p.get("work_status") != "evaluated"
                      and _prediction_discriminates(p, slabels)]
        # a pre-registered next_experiment can also be the blocking discriminator
        nx_blocks = False
        if next_experiment:
            po = next_experiment.get("predicted_outcomes") or []
            nx = [(d.get("hypothesis_label"), d.get("expected")) for d in po
                  if isinstance(d, dict) and d.get("hypothesis_label") in slabels]
            nx_blocks = (len({l for l, _ in nx}) >= 2 and len({e for _, e in nx}) >= 2)

        if disc_run:
            state = "resolving"   # a discriminating test has a verdict; survivors should separate
        elif disc_unrun or nx_blocks:
            state = "blocked_on_experiment"  # observationally identical, but a test is designed
        else:
            state = "no_discriminating_test"  # observationally identical AND no test — worse
        if rank[state] > rank[worst]:
            worst = state
        blocking = [(p.get("label") or p.get("descriptor_name")) for p in disc_unrun][:3]
        if nx_blocks and next_experiment.get("descriptor"):
            blocking.append("next_experiment: " + str(next_experiment["descriptor"]))
        # the decisive test exists only as a project next_experiment, not as a
        # first-class unrun discriminating prediction owned by a survivor — so it
        # isn't a tracked falsifier. Nudge the agent to register it.
        blocker_only_in_next_experiment = (state == "blocked_on_experiment"
                                            and nx_blocks and not disc_unrun)
        # EQUIVALENCE CLASS: when survivors are observationally identical on current
        # data they are NON-IDENTIFIABLE — report them as ONE equivalence class, not a
        # ranking. (Confidence is now COMPUTED from each one's verdicts, so any spread
        # is grounded in the evidence — there is no 'false precision' to penalise.)
        observationally_identical = state in ("blocked_on_experiment",
                                              "no_discriminating_test")
        members = sorted(({"label": l, "confidence": conf_of.get(l, 0)}
                          for l in slabels), key=lambda m: -m["confidence"])
        if observationally_identical:
            equivalence_classes.append({
                "members": [m["label"] for m in members],
                "member_confidence": {m["label"]: m["confidence"] for m in members},
                "note": ("OBSERVATIONALLY IDENTICAL on current data (non-identifiable) — "
                         "no registered test discriminates them, so report them as ONE "
                         "equivalence class. Only the experiment resolves which is right."),
            })
        out_clusters.append({
            "survivors": sorted(slabels),
            "state": state,
            "observationally_identical": observationally_identical,
            "equivalence_class": observationally_identical,
            "members": members,
            "blocking_experiments": blocking,
            "blocker_only_in_next_experiment": blocker_only_in_next_experiment,
            "_reads": {
                "blocked_on_experiment": "Survivors are observationally identical on "
                    "current data — re-auditing will NOT separate them; only the "
                    "registered experiment will. Run it. Report them as one equivalence "
                    "class, not a 0.xx-vs-0.yy ranking.",
                "no_discriminating_test": "Survivors are observationally identical and "
                    "NO registered test separates them — design a discriminating "
                    "experiment (this is worse than 'one experiment away').",
                "resolving": "A discriminating experiment has a verdict; the split "
                    "should be resolving.",
            }.get(state, ""),
        })

    distance = {"decided": 0.0, "resolving": 0.1,
                "blocked_on_experiment": 0.2, "no_discriminating_test": 0.8}[worst]
    return {
        "contested_clusters": out_clusters,
        "equivalence_classes": equivalence_classes,
        "decision_distance": distance,
        "headline": {
            "decided": "No contested survivor set — converged.",
            "resolving": "A discriminating experiment is in; the contested set is resolving.",
            "blocked_on_experiment": "Settled phenomenon — decision is ONE pre-registered "
                "experiment away. Stop auditing the same data; run the experiment.",
            "no_discriminating_test": "Contested survivors with NO test that separates "
                "them — design a discriminating experiment.",
        }[worst],
        "_note": "Progress here is distance-to-a-decision, not leader confidence. Two "
                 "rivals that are observationally identical on current data are a "
                 "SETTLED phenomenon with an open sub-mechanism — not 'everything weak'. "
                 "Confidence is never frozen; the DECISION is what's blocked.",
    }


# --- Prediction-based confidence scoring -----------------------------------

_STRENGTH_W = {"strong": 1.0, "moderate": 0.6, "weak": 0.3}
# Correlated evidence is not independent: a same-direction decisive verdict that
# rests on records ALREADY counted contributes only a fraction (you can't confirm a
# hypothesis twice with the same data, nor manufacture 'reliability' by stacking
# predictions on one result).
_CORRELATION_ATTENUATION = 0.3


def _evidence_key(p):
    """The set of evidence record IDs a verdict rests on (for independence/dedup).
    Empty → unknown provenance, treated as independent (we can't prove overlap)."""
    ids = p.get("evidence_record_ids") or []
    return frozenset(str(x) for x in ids if x)


def compute_hypothesis_score(h) -> dict:
    """THE single, canonical way a hypothesis is evaluated. Confidence is COMPUTED
    (never authored) by aggregating the verdicts of the hypothesis's evaluated
    predictions, in log-odds:
      • supports    → +strength               (confirmation)
      • contradicts → −strength × 1.25         (falsification is more decisive)
      • a STRONG contradiction of a falsifier ~ falsified (confidence capped ≤0.15)
      • neutral     → a SMALL negative (a test that ran and found NO predicted effect
                      is mild evidence against — distinct from 'no test')
      • insufficient→ 0 (not enough data — no shift, but it IS a test that didn't resolve)
      • blocked     → 0 and EXCLUDED from belief (SCHEMA GATE: the comparison is
                      methodologically incompatible / ill-posed — not a measurement, so
                      it can't move belief; it only lowers COVERAGE)
    EVIDENCE INDEPENDENCE (use-novelty) is enforced in the math, not just flagged:
      • a 'supports' whose evidence_independence shows the model was FIT to the data
        it's TESTED on is CIRCULAR — it's a consistency check, not confirmation. It
        contributes 0 and does NOT count as decisive (discounted_circular).
      • CORRELATED same-direction verdicts (sharing evidence_record_ids already
        counted) are attenuated to {_atten}× and don't add to n_decisive — you can't
        confirm twice with the same data, nor fake reliability by stacking predictions
        on one result.
    confidence = sigmoid(Σ). strength ∈ {{strong:1.0, moderate:0.6, weak:0.3}}; an
    omitted/unknown strength is treated as WEAK (the conservative tier). A score from
    <2 INDEPENDENT DECISIVE (supports/contradicts) verdicts is UNRELIABLE — you cannot
    validate/falsify a hypothesis on one verdict; that is why a hypothesis needs a SET
    of distinct, structured predictions on independent evidence.""".format(
        _atten=_CORRELATION_ATTENUATION)
    bd = {"supports": 0, "contradicts": 0, "neutral": 0, "insufficient": 0,
          "blocked": 0, "unevaluated": 0, "circular_discounted": 0,
          "correlated_attenuated": 0}
    logit = 0.0
    decisive = []   # (direction:+1/-1, strength_weight, evidence_key) — survives to pass 2
    for p in h.get("predictions", []):
        if p.get("work_status") != "evaluated":
            bd["unevaluated"] += 1
            continue
        v = normalize_verdict(p.get("verdict"))
        # omitted/unknown strength → weak (the conservative tier): an unqualified
        # verdict should move belief the LEAST, never a magic mid-value.
        sw = _STRENGTH_W.get((p.get("strength") or "").strip().lower(), _STRENGTH_W["weak"])
        if v == "supports":
            bd["supports"] += 1
            if _circularity_flag(p.get("evidence_independence")):
                bd["circular_discounted"] += 1        # use-novelty: consistency, not confirmation
                continue                              # 0 contribution, not decisive
            decisive.append((+1, sw, _evidence_key(p)))
        elif v == "contradicts":
            bd["contradicts"] += 1
            decisive.append((-1, sw, _evidence_key(p)))
        elif v == "neutral":
            logit -= 0.20; bd["neutral"] += 1          # mild evidence against
        elif v == "blocked":
            bd["blocked"] += 1                         # SCHEMA GATE — no belief shift
        else:
            bd["insufficient"] += 1                    # tested, didn't resolve
    # Pass 2 — independence/dedup, per direction. Strongest-first greedy: the first
    # verdict resting on a given record counts in full; later same-direction verdicts
    # whose evidence overlaps what's already counted are attenuated and don't add to
    # the independent-decisive count that drives reliability.
    n_decisive, strong_contra = 0, False
    for direction in (+1, -1):
        claimed = set()
        same = sorted([d for d in decisive if d[0] == direction],
                      key=lambda d: -d[1])
        for _dir, sw, ev in same:
            independent = (not ev) or not (ev & claimed)
            weight = sw if independent else sw * _CORRELATION_ATTENUATION
            if independent:
                n_decisive += 1
                if ev:
                    claimed |= ev
                if direction < 0 and sw >= 1.0:
                    strong_contra = True
            else:
                bd["correlated_attenuated"] += 1
            logit += direction * weight * (1.25 if direction < 0 else 1.0)
    computed = 1.0 / (1.0 + math.exp(-logit))
    if strong_contra:
        computed = min(computed, 0.15)
    reliable = n_decisive >= 2
    n_tested = bd["supports"] + bd["contradicts"] + bd["neutral"] + bd["insufficient"]
    n_total = len(h.get("predictions", []))
    coverage = round(n_tested / n_total, 2) if n_total else 0.0
    conflict = (min(bd["supports"], bd["contradicts"])
                / max(1, bd["supports"] + bd["contradicts"]))
    return {
        "computed_confidence": round(computed, 3),
        "n_decisive": n_decisive,
        "n_scored": n_decisive,   # back-compat alias
        "n_predictions": n_total,
        "n_blocked": bd["blocked"],
        "coverage": coverage,
        "conflict": round(conflict, 2),
        "breakdown": bd,
        "reliable": reliable,
        "note": ("Computed from the prediction verdicts (the ONLY source of "
                 "confidence). "
                 + ("UNRELIABLE — fewer than 2 INDEPENDENT DECISIVE (supports/"
                    "contradicts) verdicts; you can't validate/falsify a hypothesis on "
                    "one. Add more predictions on DISTINCT descriptors / independent "
                    "evidence."
                    if not reliable else
                    f"{n_decisive} independent decisive ({bd['supports']}+/{bd['contradicts']}−)"
                    + (f", {bd['neutral']} neutral" if bd['neutral'] else "")
                    + (f", {bd['blocked']} blocked (schema gate)" if bd['blocked'] else "")
                    + ".")
                 + (f" {bd['circular_discounted']} circular 'supports' discounted "
                    "(use-novelty)." if bd['circular_discounted'] else "")
                 + (f" {bd['correlated_attenuated']} correlated verdict(s) attenuated "
                    "(shared evidence)." if bd['correlated_attenuated'] else "")),
    }


def _recompute_and_store_confidence(cur, hypothesis_id, *, actor=None) -> float:
    """Recompute a hypothesis's confidence FROM its prediction verdicts and persist
    it (the platform owns confidence; the agent never authors it). Called on every
    prediction-evaluation change. Returns the new confidence."""
    cur.execute("""SELECT verdict, strength, work_status, evidence_independence,
                          evidence_record_ids
                     FROM hyp_predictions WHERE hypothesis_id=%s""", (hypothesis_id,))
    preds = [dict(r) for r in cur.fetchall()]
    score = compute_hypothesis_score({"predictions": preds})
    conf = score["computed_confidence"]
    cur.execute("SELECT project_id FROM hyp_hypotheses WHERE hypothesis_id=%s",
                (hypothesis_id,))
    row = cur.fetchone()
    if row is None:
        return conf
    cur.execute("UPDATE hyp_hypotheses SET confidence=%s, updated_at=NOW() "
                "WHERE hypothesis_id=%s", (conf, hypothesis_id))
    _snapshot_confidence(cur, row["project_id"], hypothesis_id, conf,
                         basis=score["note"], source="computed")
    return conf


# --- Briefing (the curated "universal truth" digest the agent reads first) --

def get_briefing(project_id, owner_identity=None) -> dict | None:
    """A compact, server-curated summary of where the project stands RIGHT NOW —
    the canonical ground truth both the human header and the agent consume.

    Deliberately NOT the full firehose: as a project grows, handing back
    everything makes an agent MORE likely to drift. This is the digest the agent
    must read at the start of each turn and reconcile its reasoning to."""
    data = get_project(project_id, owner_identity=owner_identity)
    if data is None:
        return None
    proj, hyps, events = data["project"], data["hypotheses"], data["events"]

    def _oneline(s):
        s = s or ""
        return (s.split(":", 1)[0] if ":" in s[:60] else s)[:90]

    ranking, validated, invalidated, open_q, pending_compute = [], [], [], [], []
    failed_compute = []
    supported, eliminated = [], []
    matrix = []
    hyps_without_falsifier, preds_without_origin, preds_without_criterion = [], [], []
    circular_confirmations, supports_without_independence = [], []
    high_conf_hyps, preds_missing_mlflow = [], []
    hyps_below_min_preds, preds_missing_structure, hyps_single_descriptor = [], [], []
    unreliable_scores = []
    for h in hyps:
        _score = compute_hypothesis_score(h)
        ranking.append({"label": h["label"], "status": h["status"],
                        "confidence": h["confidence"],
                        "computed_confidence": _score["computed_confidence"],
                        "n_scored": _score["n_scored"], "reliable": _score["reliable"],
                        "statement": _oneline(h["statement"])})
        if h["status"] == "supported":
            supported.append(h["label"])
        elif h["status"] == "eliminated":
            eliminated.append(h["label"])
        if h["status"] == "supported" or (h["confidence"] or 0) >= 0.7:
            high_conf_hyps.append(h["label"])
        _live = h["status"] not in ("eliminated", "superseded")
        # A score from <2 decisive predictions is unreliable; a residual is exempt
        # (it's deliberately a catch-all, not pinned by predictions yet).
        if _live and not is_residual_hypothesis(h) and not _score["reliable"]:
            unreliable_scores.append(f"{h['label']} ({_score['n_decisive']} decisive)")
        if not h["predictions"]:
            hyps_without_falsifier.append(h["label"])
        # A hypothesis needs a SET of falsifying predictions, not one token — and the
        # set should span DISTINCT measurables (a single descriptor = an impoverished
        # falsification surface). (Live hypotheses only.)
        elif _live and len(h["predictions"]) < MIN_PREDICTIONS_PER_HYPOTHESIS:
            hyps_below_min_preds.append(f"{h['label']} ({len(h['predictions'])})")
        if _live and len(h["predictions"]) >= 2 and \
                len({(p.get("descriptor_name") or "") for p in h["predictions"]}) == 1:
            hyps_single_descriptor.append(h["label"])
        for p in h["predictions"]:
            _ptag = h["label"] + "/" + (p.get("descriptor_name") or p.get("label") or "?")
            if not p.get("origin"):
                preds_without_origin.append(_ptag)
            if not p.get("falsification_criterion"):
                preds_without_criterion.append(_ptag)
            # Structured shape (per the spec): a prediction must carry descriptor +
            # direction + reference_condition + magnitude + falsification_criterion —
            # not a single crammed string. Flag any missing the core structural fields.
            _missing_struct = [f for f in ("direction", "reference_condition", "magnitude")
                               if not p.get(f)]
            if _missing_struct:
                preds_missing_structure.append(f"{_ptag} [{','.join(_missing_struct)}]")
            # Auditability: a verdict that leaned on COMPUTE or a fitted MODEL must
            # carry an mlflow_run_url (the replay trace). Pure-data verdicts are not
            # flagged — only the compute/model-backed ones that should be traceable.
            _ind = p.get("evidence_independence") or {}
            _model_backed = bool(p.get("compute_runs")) or (
                isinstance(_ind, dict) and _ind.get("model_was_fit"))
            if (p.get("work_status") == "evaluated" and _model_backed
                    and not p.get("mlflow_run_url")):
                preds_missing_mlflow.append(_ptag)
            if p.get("discriminates"):
                matrix.append({"prediction": p.get("label") or p.get("descriptor_name"),
                               "descriptor": p.get("descriptor_name"),
                               "owner_hypothesis": h["label"],
                               "expected_by_hypothesis": p["discriminates"]})
            ws = p.get("work_status")
            item = {"hypothesis_label": h["label"], "descriptor": p.get("descriptor_name"),
                    "work_status": ws, "verdict": p.get("verdict"),
                    "mlflow_run_url": p.get("mlflow_run_url")}
            if ws == "evaluated":
                nv = normalize_verdict(p.get("verdict"))
                # Use-novelty: a 'supports' verdict whose evidence was fit to the
                # very data it's tested against is circular — flag it. And a
                # 'supports' verdict with NO independence declaration at all is
                # unverified for use-novelty (the agent must declare it — esp. for
                # model-based evidence — so omitted circularity becomes visible).
                if nv == "supports":
                    _cf = _circularity_flag(p.get("evidence_independence"))
                    if _cf:
                        circular_confirmations.append({"prediction": _ptag, "issue": _cf})
                    elif not p.get("evidence_independence"):
                        supports_without_independence.append(_ptag)
                (validated if nv == "supports"
                 else invalidated if nv == "contradicts"
                 else open_q).append(item)
            elif ws in ("compute_submitted", "compute_running"):
                pending_compute.append(item)
            elif ws == "compute_failed":
                failed_compute.append(item)        # crashed calc — a re-run to-do, NOT a verdict
            else:
                open_q.append(item)

    # Individuation: a `supersedes` should declare the discriminating observable
    # on which the new hypothesis predicts differently (else it may be a mere
    # refinement that belongs in a version bump, not a new node).
    _hlabel = {h["hypothesis_id"]: h["label"] for h in hyps}
    # Tolerant check: a (from,to) supersession is OK as long as AT LEAST ONE of its
    # rows carries the observable (robust to legacy duplicate rows, and cleared by
    # the upsert re-post). Flag only pairs where NO row has it.
    _sup_pairs, _sup_has_obs = {}, set()
    for r in (data.get("relations") or []):
        if r.get("relation_type") == "supersedes":
            key = (r["from_hypothesis_id"], r["to_hypothesis_id"])
            _sup_pairs[key] = (f"{_hlabel.get(key[0], '?')} supersedes "
                               f"{_hlabel.get(key[1], '?')}")
            if r.get("discriminating_observable"):
                _sup_has_obs.add(key)
    supersedes_without_discriminator = [lbl for key, lbl in _sup_pairs.items()
                                        if key not in _sup_has_obs]

    all_findings = list_rigor_findings(project_id)
    open_findings = [f for f in all_findings if f["status"] == "open"]
    # A high-confidence claim with NO findings on record at all has never faced an
    # independent critic — flag it so the agent commissions one (it doesn't depend
    # on me telling it to; the platform does).
    high_confidence_without_review = high_conf_hyps if not all_findings else []
    rigor_review = {
        "open_findings": [
            {"finding_id": f["finding_id"], "severity": f["severity"],
             "category": f["category"], "summary": f["summary"],
             "target_type": f["target_type"], "target_id": f["target_id"]}
            for f in open_findings],
        "open_critical": sum(1 for f in open_findings if f["severity"] == "critical"),
        "ever_reviewed": bool(all_findings),
        "_note": ("Open findings from an INDEPENDENT rigor critic. Resolve (fix or "
                  "justify) or dismiss each before trusting a high-confidence claim. "
                  "If a high-confidence claim has never been reviewed, commission one "
                  "(see manifest.rigor_review) — a clean review can post one minor "
                  "'survives' finding as its record."),
    }

    convergence = compute_convergence(hyps, data.get("relations"),
                                      next_experiment=proj.get("next_experiment"))
    pending_work = get_pending_work(project_id)
    dataset_coverage = compute_dataset_coverage(project_id, hyps, proj.get("dataset"))

    # SHARED-PREMISE AUDIT: when ≥2 survivors are observationally identical
    # (an equivalence class), they are likely explaining the same phenomenon via a
    # COMMON premise. If that premise is never itself tested — and there is no
    # explicit NONE-OF-THE-ABOVE residual hypothesis that could make it fail — the
    # whole set sits on an unaudited foundation (the most dangerous blind spot,
    # because it hides inside agreement). Generic: triggered purely by the
    # equivalence-class state + absence of a residual-typed hypothesis.
    _has_equiv_class = any(len(ec.get("members", [])) >= 2
                           for ec in convergence.get("equivalence_classes", []))
    _live_residual = [h["label"] for h in hyps
                      if h["status"] not in ("eliminated", "superseded")
                      and is_residual_hypothesis(h)]
    shared_premise_audit = {
        "equivalence_class_present": _has_equiv_class,
        "has_residual_hypothesis": bool(_live_residual),
        "residual_hypotheses": _live_residual,
        "unaudited": _has_equiv_class and not _live_residual,
        "_note": ("When your surviving rivals are observationally identical they "
                  "probably share a common premise (a mechanism they all assume). "
                  "Audit it: (1) state the shared premise explicitly; (2) is it TESTED "
                  "or merely ASSUMED? — if assumed, frame it as its own falsifiable "
                  "hypothesis with a discriminating test; (3) carry an explicit "
                  "NONE-OF-THE-ABOVE residual hypothesis (hypothesis_type='residual') "
                  "with nonzero confidence, so the shared premise CAN fail. A premise "
                  "every survivor takes for granted is the most dangerous blind spot."),
    }

    elements = extract_elements(proj.get("material_system"))
    ov = proj.get("evidence_overrides") or {}
    evidence_index = build_evidence_index(elements, include_ids=ov.get("include"),
                                          exclude_ids=ov.get("exclude"))

    # Self-instructing: turn the gaps above into an explicit, prioritized to-do so
    # the agent learns what to do next FROM THE BRIEFING, not from a bespoke human
    # prompt. Every action is a generic method/rigor step — never a science answer.
    recommended_actions = []
    if not dataset_coverage.get("declared"):
        recommended_actions.append(
            "Declare the project's DATASET OF INTEREST (PUT /projects/{id}/dataset "
            "{record_ids, description}) — anchor the record set this project is about "
            "so scope is explicit and coverage can be checked.")
    elif dataset_coverage.get("n_unused"):
        _names = [r.get("material") or r.get("record_id")
                  for r in dataset_coverage.get("unused_records", [])][:6]
        recommended_actions.append(
            f"COVERAGE: {dataset_coverage['n_unused']} of "
            f"{dataset_coverage['n_dataset']} declared-dataset records are UNUSED "
            f"({', '.join(str(n) for n in _names)}). Use them or justify excluding "
            "each — a different geometry/composition/end-member may already hold the "
            "discriminating contrast a confound is hiding.")
    if pending_work["items"]:
        recommended_actions.append(
            f"RECONCILE {pending_work['count']} pending external step(s) you started "
            "but didn't await (literature query / submitted compute): poll each and "
            "ingest the result (resolve the async task / evaluate the prediction). "
            "This is the main reason to resume the project.")
    if failed_compute:
        _ftags = [f"{i['hypothesis_label']}/{i.get('descriptor') or '?'}" for i in failed_compute][:6]
        recommended_actions.append(
            f"RE-RUN {len(failed_compute)} FAILED computation(s) ({', '.join(_ftags)}): the "
            "calc crashed / did not converge, so the prediction is unevaluated. This did "
            "NOT change any score (confidence stays on the evidence you actually have) — "
            "fix and resubmit it next cycle, or replace it with method-compatible evidence. "
            "Do NOT record a failed calc as a verdict (insufficient/contradicts).")
    # Convergence redirect FIRST: when survivors are observationally identical, the
    # next move is an EXPERIMENT, not another audit (the safe version of 'freeze' —
    # we redirect the decision, never touch the confidences).
    for _c in convergence["contested_clusters"]:
        if _c["state"] == "blocked_on_experiment":
            recommended_actions.append(
                f"RUN the discriminating experiment ({', '.join(_c['blocking_experiments']) or 'registered'}) "
                f"to separate {_c['survivors']} — they are observationally identical on "
                "current data, so further auditing won't resolve them; the experiment will.")
            if _c.get("blocker_only_in_next_experiment"):
                recommended_actions.append(
                    f"Register the discriminating experiment for {_c['survivors']} as a "
                    "first-class UNRUN prediction (descriptor + discriminates naming each "
                    "survivor's expected outcome) on one of them — right now the decisive "
                    "test lives only in next_experiment, so it isn't a tracked falsifier.")
        elif _c["state"] == "no_discriminating_test":
            recommended_actions.append(
                f"DESIGN a discriminating experiment for {_c['survivors']} — they are "
                "observationally identical and no registered test separates them.")
    if shared_premise_audit["unaudited"]:
        recommended_actions.append(
            "SHARED-PREMISE AUDIT: your observationally-identical survivors likely share "
            "a common premise (a mechanism they all assume). State it, decide if it is "
            "TESTED or only ASSUMED (if assumed, frame it as its own falsifiable "
            "hypothesis with a discriminating test), and add an explicit NONE-OF-THE-"
            "ABOVE residual hypothesis (hypothesis_type='residual') so the shared premise "
            "can actually fail — otherwise the whole set rests on an unaudited foundation.")
    if preds_missing_mlflow:
        recommended_actions.append(
            f"Attach an mlflow_run_url to {len(preds_missing_mlflow)} compute/model-backed "
            "verdict(s) missing it — the MLflow run is the replay trace (dual-write: "
            "dashboard + MLflow).")
    if open_findings:
        recommended_actions.append(
            f"Resolve {len(open_findings)} open rigor finding(s) "
            f"({rigor_review['open_critical']} critical): fix or justify each, then "
            f"PUT /rigor/findings/{{id}} resolved|dismissed.")
    if high_confidence_without_review:
        recommended_actions.append(
            "Commission an INDEPENDENT rigor review for high-confidence claim(s) "
            f"{high_confidence_without_review} — none has faced a critic yet "
            "(manifest.rigor_review: spawn a separate reviewer with the critic_prompt).")
    if circular_confirmations:
        recommended_actions.append(
            f"Fix {len(circular_confirmations)} circular confirmation(s): a model fit "
            "to the data it's tested on can't 'support' — re-evaluate as neutral or "
            "confirm on held-out data (use-novelty).")
    if supports_without_independence:
        recommended_actions.append(
            f"Declare evidence_independence on {len(supports_without_independence)} "
            "'supports' verdict(s) that lack it (what the model was fit to vs tested "
            "against) so use-novelty can be checked.")
    if supersedes_without_discriminator:
        recommended_actions.append(
            f"Attach a discriminating_observable to {len(supersedes_without_discriminator)} "
            "supersession(s) — or, if it's only a refinement, make it a version via "
            "/hypotheses/{id}/refine instead of a new node.")
    if hyps_without_falsifier:
        recommended_actions.append(
            f"Add ≥1 falsifying prediction to hypotheses {hyps_without_falsifier}.")
    if unreliable_scores:
        recommended_actions.append(
            f"UNRELIABLE SCORE: {unreliable_scores} have <2 DECISIVE verdicts — their "
            "computed confidence is barely off the 0.5 prior and can't reliably "
            "validate/falsify them. STRONGLY add more predictions (on distinct "
            "descriptors) and evaluate them, so the score aggregates over a real set.")
    elif hyps_below_min_preds:
        recommended_actions.append(
            f"Build out the prediction SET for {hyps_below_min_preds} — each hypothesis "
            f"needs ≥{MIN_PREDICTIONS_PER_HYPOTHESIS} falsifying predictions (aim for "
            "3-4), spanning DIFFERENT measurables (descriptors), not one token "
            "prediction. A richer falsifier set is what separates rivals.")
    if hyps_single_descriptor:
        recommended_actions.append(
            f"Diversify the descriptors for {hyps_single_descriptor} — all their "
            "predictions use ONE measurable, an impoverished falsification surface. Add "
            "predictions on distinct descriptors (e.g. a partial-current, a binding "
            "energy, a different product's FE).")
    if preds_missing_structure:
        recommended_actions.append(
            f"Complete the STRUCTURE of {len(preds_missing_structure)} prediction(s) "
            "missing direction / reference_condition / magnitude — a prediction is "
            "{descriptor, direction, reference_condition, magnitude, "
            "falsification_criterion}, not one crammed string.")
    if preds_without_origin:
        recommended_actions.append(
            f"Add origin provenance to {len(preds_without_origin)} prediction(s).")
    if preds_without_criterion:
        recommended_actions.append(
            f"Add a falsification_criterion to {len(preds_without_criterion)} prediction(s).")
    if len(hyps) < 2:
        recommended_actions.append("Frame ≥2 competing hypotheses.")
    if not proj.get("next_experiment"):
        recommended_actions.append("Propose the discriminating next experiment "
                                   "(PUT /next_experiment).")

    return {
        "project_id": project_id,
        "title": proj["title"],
        "goal": proj.get("goal"),
        "material_system": proj.get("material_system"),
        "reaction": proj.get("reaction"),
        "elements": elements,
        "as_of": _now_iso(),
        "ranking": ranking,
        "settled": {"supported": supported, "eliminated": eliminated},
        "validated_predictions": validated,
        "invalidated_predictions": invalidated,
        "open_questions": open_q,
        "pending_compute": pending_compute,
        "failed_compute": failed_compute,   # crashed/non-converged calcs — re-run to-dos, no score effect
        "discrimination_matrix": matrix,
        "convergence": convergence,
        "pending_work": pending_work,
        "dataset_coverage": dataset_coverage,
        "recommended_actions": recommended_actions,
        "method_compliance": {
            "_what": "Live check against the manifest `method` + epistemic_guardrails. "
                     "Close these gaps — they are what make a claim auditable. The "
                     "`recommended_actions` list above turns them into your to-do.",
            "enough_competing_hypotheses": len(hyps) >= 2,
            "hypotheses_without_falsifying_prediction": hyps_without_falsifier,
            "hypotheses_below_min_predictions": hyps_below_min_preds,
            "hypotheses_with_single_descriptor": hyps_single_descriptor,
            "predictions_missing_structured_fields": preds_missing_structure,
            "unreliable_scores_too_few_predictions": unreliable_scores,
            "predictions_missing_origin_provenance": preds_without_origin,
            "predictions_missing_falsification_criterion": preds_without_criterion,
            "circular_confirmations": circular_confirmations,
            "supports_without_independence_declaration": supports_without_independence,
            "supersessions_without_discriminating_observable": supersedes_without_discriminator,
            "high_confidence_without_independent_review": high_confidence_without_review,
            "compute_verdicts_missing_mlflow_trace": preds_missing_mlflow,
            "dataset_records_unused": [r.get("material") or r.get("record_id")
                                       for r in dataset_coverage.get("unused_records", [])],
            "dataset_of_interest_undeclared": not dataset_coverage.get("declared"),
            "shared_premise_unaudited": shared_premise_audit["unaudited"],
        },
        "shared_premise_audit": shared_premise_audit,
        "rigor_review": rigor_review,
        "evidence_index": _evidence_summary(evidence_index),
        "literature": "For published-evidence cross-checks (Edison/PaperQA3): "
                      "POST /portal/api/literature/search {query, job}, then poll "
                      "GET /portal/api/literature/search/{task_id}.",
        "next_experiment": proj.get("next_experiment"),
        "recent_journal": [{"event_type": e["event_type"], "summary": e["summary"],
                            "at": (e["created_at"].isoformat()
                                   if hasattr(e["created_at"], "isoformat")
                                   else str(e["created_at"]))}
                           for e in events[:8]],
        "_note": ("Authoritative current state of this project. Reconcile your "
                  "reasoning to it before acting; write every change back here — "
                  "if it is not on this dashboard, it did not happen."),
    }


def delete_project(project_id, owner_identity=None, is_admin=False) -> bool:
    """Delete a project and all its children (events, predictions, hypotheses,
    messages). Scoped to the owner unless is_admin. Discovery DB only."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT owner_identity FROM hyp_projects WHERE project_id=%s",
                    (project_id,))
        row = cur.fetchone()
        if row is None:
            return False
        if not is_admin and owner_identity is not None and \
                row["owner_identity"] != owner_identity:
            return False
        # Order matters: clear FK children before parents.
        cur.execute("""DELETE FROM hyp_compute_runs WHERE prediction_id IN
                         (SELECT prediction_id FROM hyp_predictions WHERE hypothesis_id IN
                            (SELECT hypothesis_id FROM hyp_hypotheses WHERE project_id=%s))""",
                    (project_id,))
        cur.execute("DELETE FROM hyp_predictions WHERE hypothesis_id IN "
                    "(SELECT hypothesis_id FROM hyp_hypotheses WHERE project_id=%s)",
                    (project_id,))
        cur.execute("DELETE FROM hyp_confidence_snapshots WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_rigor_findings WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_async_tasks WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_hypothesis_versions WHERE hypothesis_id IN "
                    "(SELECT hypothesis_id FROM hyp_hypotheses WHERE project_id=%s)",
                    (project_id,))
        cur.execute("DELETE FROM hyp_hypothesis_relations WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_events WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_messages WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_project_shares WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_hypotheses WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_projects WHERE project_id=%s", (project_id,))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Events (the agent's reasoning transcript) -----------------------------

def add_event(project_id, event_type, summary, *, detail=None, hypothesis_id=None,
              evidence_record_ids=None, mlflow_run_url=None, actor=None) -> int | None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return None
        eid = _append_event(cur, project_id, event_type, summary, detail=detail,
                            hypothesis_id=hypothesis_id,
                            evidence_record_ids=evidence_record_ids,
                            mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return eid
    finally:
        cur.close()
        conn.close()


# --- Hypothesis relations (the hypothesis graph) ---------------------------

def add_relation(from_hypothesis_id, to_hypothesis_id, relation_type, *,
                 note=None, discriminating_observable=None,
                 retained_vs_abandoned=None, change_type=None, actor=None) -> bool:
    """Link two hypotheses. For `supersedes`, the caller should declare the
    DISCRIMINATING OBSERVABLE on which the new hypothesis predicts differently
    from the one it replaces — that empirical difference is what makes it a new
    hypothesis rather than a refinement (which should be a version bump instead,
    see refine_hypothesis). Surfaced in the briefing's method_compliance; not
    yet hard-gated."""
    relation_type = normalize_relation(relation_type)
    if relation_type not in RELATION_TYPES:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, from_hypothesis_id)
        proj_to = _project_of_hypothesis(cur, to_hypothesis_id)
        if project_id is None or proj_to is None:
            return False
        # UPSERT on (from, to, relation_type): re-posting a relation UPDATES it in
        # place (e.g. to attach a discriminating_observable) rather than spawning a
        # duplicate row. COALESCE keeps existing values when a field isn't re-sent.
        cur.execute(
            """SELECT id FROM hyp_hypothesis_relations
                WHERE from_hypothesis_id=%s AND to_hypothesis_id=%s
                  AND relation_type=%s""",
            (from_hypothesis_id, to_hypothesis_id, relation_type))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """UPDATE hyp_hypothesis_relations SET
                     note=COALESCE(%s, note),
                     discriminating_observable=COALESCE(%s, discriminating_observable),
                     retained_vs_abandoned=COALESCE(%s, retained_vs_abandoned),
                     change_type=COALESCE(%s, change_type)
                   WHERE id=%s""",
                (note, discriminating_observable, retained_vs_abandoned, change_type,
                 existing["id"]))
        else:
            cur.execute(
                """INSERT INTO hyp_hypothesis_relations
                     (project_id, from_hypothesis_id, to_hypothesis_id, relation_type,
                      note, discriminating_observable, retained_vs_abandoned, change_type)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (project_id, from_hypothesis_id, to_hypothesis_id, relation_type, note,
                 discriminating_observable, retained_vs_abandoned, change_type))
        _detail = note
        if relation_type == "supersedes" and discriminating_observable:
            _detail = (f"{note + chr(10) if note else ''}discriminating observable: "
                       f"{discriminating_observable}")
        _append_event(cur, project_id, "status_changed",
                      f"Relation added: {relation_type}",
                      detail=_detail, hypothesis_id=from_hypothesis_id, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def delete_relation(from_hypothesis_id, to_hypothesis_id, relation_type, *,
                    actor=None) -> int:
    """Remove relation row(s) matching (from, to, relation_type) — e.g. to clear a
    stray duplicate or a relation added in error. Returns the number deleted."""
    relation_type = normalize_relation(relation_type)
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, from_hypothesis_id)
        cur.execute(
            """DELETE FROM hyp_hypothesis_relations
                WHERE from_hypothesis_id=%s AND to_hypothesis_id=%s
                  AND relation_type=%s""",
            (from_hypothesis_id, to_hypothesis_id, relation_type))
        n = cur.rowcount
        if n and project_id:
            _append_event(cur, project_id, "status_changed",
                          f"Relation removed: {relation_type} ({n})",
                          hypothesis_id=from_hypothesis_id, actor=actor)
        conn.commit()
        return n
    finally:
        cur.close()
        conn.close()


# --- Independent rigor-critic findings -------------------------------------

def create_rigor_finding(project_id, summary, *, target_type=None, target_id=None,
                         category="other", severity="major", detail=None,
                         raised_by=None) -> str | None:
    """An independent critic records a place a claim fails rigor. Generic across
    fields — the category names the epistemic failure (use_novelty / individuation
    / falsifiability / evidence_compatibility / confirmation_bias / overreach)."""
    cat = str(category or "other").strip().lower()
    if cat not in RIGOR_CATEGORIES:
        cat = "other"
    sev = str(severity or "major").strip().lower()
    if sev not in RIGOR_SEVERITIES:
        sev = "major"
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return None
        finding_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_rigor_findings
                 (finding_id, project_id, target_type, target_id, category,
                  severity, summary, detail, raised_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (finding_id, project_id, target_type, target_id, cat, sev, summary,
             detail, raised_by))
        _append_event(cur, project_id, "status_changed",
                      f"Rigor finding [{sev}/{cat}]: {summary[:100]}",
                      detail=detail, actor=raised_by)
        conn.commit()
        return finding_id
    finally:
        cur.close()
        conn.close()


def list_rigor_findings(project_id, *, status=None) -> list:
    conn = _conn()
    cur = conn.cursor()
    try:
        if status:
            cur.execute("""SELECT * FROM hyp_rigor_findings WHERE project_id=%s
                            AND status=%s ORDER BY created_at DESC""",
                        (project_id, status))
        else:
            cur.execute("""SELECT * FROM hyp_rigor_findings WHERE project_id=%s
                            ORDER BY created_at DESC""", (project_id,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def resolve_rigor_finding(finding_id, *, status="resolved", resolution=None,
                          actor=None) -> bool:
    """Close a finding — `resolved` (the agent fixed it / explained why it holds)
    or `dismissed` (not a real issue). Keeps the audit trail; never deletes."""
    status = str(status or "resolved").strip().lower()
    if status not in RIGOR_FINDING_STATUSES:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT project_id, summary FROM hyp_rigor_findings "
                    "WHERE finding_id=%s", (finding_id,))
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            """UPDATE hyp_rigor_findings
                  SET status=%s, resolution=%s, resolved_by=%s,
                      resolved_at=CASE WHEN %s='open' THEN NULL ELSE NOW() END
                WHERE finding_id=%s""",
            (status, resolution, actor, status, finding_id))
        _append_event(cur, row["project_id"], "status_changed",
                      f"Rigor finding {status}: {row['summary'][:90]}",
                      detail=resolution, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Async tasks (resumable pending work the agent kicked off) -------------

def create_async_task(project_id, kind, *, external_ref=None, summary=None,
                      poll_hint=None, hypothesis_id=None, prediction_id=None,
                      submitted_by=None) -> str | None:
    """Record async work started but not awaited this turn (an Edison literature
    query, a submitted calculation) so the dashboard shows the project has
    RESUMABLE pending steps. Idempotent on (project_id, kind, external_ref)."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return None
        if external_ref:
            cur.execute("""SELECT task_id FROM hyp_async_tasks
                            WHERE project_id=%s AND kind=%s AND external_ref=%s""",
                        (project_id, kind, external_ref))
            row = cur.fetchone()
            if row:
                return row["task_id"]
        task_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_async_tasks
                 (task_id, project_id, kind, external_ref, summary, poll_hint,
                  hypothesis_id, prediction_id, submitted_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (task_id, project_id, kind, external_ref, summary, poll_hint,
             hypothesis_id, prediction_id, submitted_by))
        _append_event(cur, project_id, "status_changed",
                      f"Async {kind} started (resumable): {(summary or external_ref or '')[:80]}",
                      detail=poll_hint, hypothesis_id=hypothesis_id, actor=submitted_by)
        conn.commit()
        return task_id
    finally:
        cur.close()
        conn.close()


def list_async_tasks(project_id, *, status=None) -> list:
    conn = _conn()
    cur = conn.cursor()
    try:
        if status:
            cur.execute("""SELECT * FROM hyp_async_tasks WHERE project_id=%s
                            AND status=%s ORDER BY created_at DESC""",
                        (project_id, status))
        else:
            cur.execute("""SELECT * FROM hyp_async_tasks WHERE project_id=%s
                            ORDER BY created_at DESC""", (project_id,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def resolve_async_task(task_id, *, status="done", actor=None) -> bool:
    """Mark a pending task resolved (done = reconciled/ingested; ready = the
    external result is available to ingest; failed = gave up)."""
    status = str(status or "done").strip().lower()
    if status not in ("pending", "ready", "done", "failed"):
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT project_id, kind, summary FROM hyp_async_tasks "
                    "WHERE task_id=%s", (task_id,))
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            """UPDATE hyp_async_tasks SET status=%s,
                 resolved_at=CASE WHEN %s IN ('done','failed') THEN NOW() ELSE resolved_at END
               WHERE task_id=%s""", (status, status, task_id))
        if status in ("done", "failed"):
            _append_event(cur, row["project_id"], "status_changed",
                          f"Async {row['kind']} {status}: {(row['summary'] or '')[:80]}",
                          actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def get_pending_work(project_id) -> dict:
    """Aggregate RESUMABLE pending work for the dashboard: async tasks the agent
    started (literature/compute/external) plus compute runs still queued/running.
    This is what tells a user 'come back and resume — these will be ready'."""
    items = []
    for t in list_async_tasks(project_id):
        if t["status"] in ("pending", "ready"):
            items.append({"kind": t["kind"], "ref": t["external_ref"],
                          "summary": t["summary"], "status": t["status"],
                          "poll_hint": t.get("poll_hint"),
                          "started_at": (t["created_at"].isoformat()
                                         if hasattr(t["created_at"], "isoformat")
                                         else str(t["created_at"]))})
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT r.backend, r.engine, r.slurm_job_id, r.status, r.created_at,
                      r.mlflow_run_url
                 FROM hyp_compute_runs r
                 JOIN hyp_predictions p ON p.prediction_id = r.prediction_id
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE h.project_id=%s AND r.status IN ('queued','running','failed')
                ORDER BY r.created_at DESC""", (project_id,))
        _seen_pred = set()
        for r in cur.fetchall():
            _engine = f"{r.get('backend') or ''} {r.get('engine') or ''}".strip() or "compute run"
            _summary = (f"{_engine} FAILED — re-run (does not affect any score)"
                        if r["status"] == "failed" else _engine)
            items.append({"kind": "compute", "ref": r.get("slurm_job_id"),
                          "summary": _summary,
                          "status": r["status"], "poll_hint": r.get("mlflow_run_url"),
                          "started_at": (r["created_at"].isoformat()
                                         if hasattr(r["created_at"], "isoformat")
                                         else str(r["created_at"]))})
        # Also catch predictions the agent left mid-compute WITHOUT a run row — a
        # submitted calc (NERSC etc.) that never came back. This is the common
        # 'left pending when the last turn ended' case.
        cur.execute(
            """SELECT p.descriptor_name, p.work_status, p.updated_at, p.mlflow_run_url
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE h.project_id=%s
                  AND p.work_status IN ('compute_submitted','compute_running','compute_failed')
                ORDER BY p.updated_at DESC""", (project_id,))
        for r in cur.fetchall():
            _failed = r["work_status"] == "compute_failed"
            items.append({"kind": "compute", "ref": None,
                          "summary": (f"prediction '{r.get('descriptor_name') or '?'}' "
                                      + ("computation FAILED — re-run (no score effect)"
                                         if _failed else "awaiting result")),
                          "status": r["work_status"], "poll_hint": r.get("mlflow_run_url"),
                          "started_at": (r["updated_at"].isoformat()
                                         if hasattr(r["updated_at"], "isoformat")
                                         else str(r["updated_at"]))})
    finally:
        cur.close()
        conn.close()
    return {
        "items": items,
        "count": len(items),
        "resumable": bool(items),
        "_note": ("External steps started but not yet reconciled (literature query / "
                  "submitted calc). Resume the project with an agent to poll and ingest "
                  "them. Empty = fully reconciled, nothing pending."),
    }


# --- Compute runs (many per prediction; real lifecycle) --------------------

def create_compute_run(prediction_id, *, backend=None, engine=None, resource=None,
                       slurm_job_id=None, mlflow_run_url=None, status="queued",
                       params=None, metrics=None, note=None, actor=None) -> str | None:
    if status not in COMPUTE_STATUSES:
        return None
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.prediction_id = %s""", (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return None
        # Idempotent on (prediction_id, slurm_job_id): a re-POST of the same job
        # UPDATES the existing run rather than duplicating it.
        if slurm_job_id:
            cur.execute("SELECT run_id FROM hyp_compute_runs "
                        "WHERE prediction_id=%s AND slurm_job_id=%s",
                        (prediction_id, slurm_job_id))
            ex = cur.fetchone()
            if ex:
                cur.execute(
                    """UPDATE hyp_compute_runs SET status=%s,
                         mlflow_run_url=COALESCE(%s, mlflow_run_url),
                         metrics=COALESCE(%s, metrics), note=COALESCE(%s, note),
                         updated_at=NOW() WHERE run_id=%s""",
                    (status, mlflow_run_url,
                     json.dumps(metrics) if metrics is not None else None,
                     note, ex["run_id"]))
                conn.commit()
                return ex["run_id"]
        run_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_compute_runs
                 (run_id, prediction_id, backend, engine, resource, slurm_job_id,
                  mlflow_run_url, status, params, metrics, note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, prediction_id, backend, engine, resource, slurm_job_id,
             mlflow_run_url, status,
             json.dumps(params) if params is not None else None,
             json.dumps(metrics) if metrics is not None else None, note))
        _append_event(cur, row["project_id"], "compute_submitted",
                      f"Compute {backend or ''} {status} for {row['descriptor_name']}",
                      detail=note, hypothesis_id=row["hypothesis_id"],
                      mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return run_id
    finally:
        cur.close()
        conn.close()


def delete_compute_run(run_id) -> bool:
    """Delete a single compute run (e.g. a duplicate)."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM hyp_compute_runs WHERE run_id=%s RETURNING run_id",
                    (run_id,))
        ok = cur.fetchone() is not None
        conn.commit()
        return ok
    finally:
        cur.close()
        conn.close()


def update_compute_run(run_id, *, status=None, metrics=None, mlflow_run_url=None,
                       slurm_job_id=None, note=None, actor=None) -> bool:
    if status is not None and status not in COMPUTE_STATUSES:
        return False
    sets, vals = [], []
    for col, v in [("status", status), ("mlflow_run_url", mlflow_run_url),
                   ("slurm_job_id", slurm_job_id), ("note", note)]:
        if v is not None:
            sets.append(f"{col}=%s"); vals.append(v)
    if metrics is not None:
        sets.append("metrics=%s"); vals.append(json.dumps(metrics))
    if not sets:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT r.prediction_id, p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_compute_runs r
                 JOIN hyp_predictions p ON p.prediction_id = r.prediction_id
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE r.run_id = %s""", (run_id,))
        row = cur.fetchone()
        if row is None:
            return False
        vals.append(run_id)
        cur.execute(f"UPDATE hyp_compute_runs SET {', '.join(sets)}, updated_at=NOW() "
                    f"WHERE run_id=%s", vals)
        # A failed run flips its (still-in-flight, un-evaluated) prediction to the
        # compute_failed pending state — never to a verdict. This guarantees a
        # crashed/non-converged calc does NOT move confidence and becomes a re-run
        # to-do, without the agent having to remember to do it by hand.
        if status == "failed":
            cur.execute(
                "UPDATE hyp_predictions SET work_status='compute_failed', updated_at=NOW() "
                "WHERE prediction_id=%s AND work_status IN "
                "('compute_submitted','compute_running','awaiting_evidence','more_work_pending')",
                (row["prediction_id"],))
        etype = ("compute_running" if status == "running"
                 else "compute_failed" if status == "failed"
                 else "status_changed")
        _append_event(cur, row["project_id"], etype,
                      f"Compute run {status or 'updated'} for {row['descriptor_name']}",
                      detail=note, hypothesis_id=row["hypothesis_id"],
                      mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Evidence index (descriptor-keyed, element-matched; READ-ONLY records) --
# Per the practitioner spec: candidates by composition ELEMENT (not material
# string), reaction ANNOTATED not gated, indexed BY DESCRIPTOR so "is there
# FE(CO) data?" is an exhaustive lookup, not a recall. The per-record annotation
# doubles as the methodological-compatibility ledger (output_quantity/functional).

_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si",
    "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co",
    "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I",
    "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pt", "Au", "Hg", "Pb", "Bi", "W",
    "Re", "Os", "Ir", "Ta", "Hf",
}


def extract_elements(text) -> list:
    """Pull valid element symbols out of a material_system / formula / name."""
    if not text:
        return []
    out, seen = [], set()
    for m in re.findall(r"[A-Z][a-z]?", str(text)):
        if m in _ELEMENTS and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _elements_from_composition(comp) -> set:
    """Element symbols from composition KEYS by WHOLE-TOKEN match (split on
    non-letters), so `Cu_geometric_area_fraction` -> {Cu} but `CO_producing_metal`
    -> {} (CO is not an element symbol; case-sensitive, so it is not Co either)."""
    out = set()
    if isinstance(comp, dict):
        for k in comp:
            for tok in re.split(r"[^A-Za-z]+", k):
                if tok in _ELEMENTS:
                    out.add(tok)
    return out


def _role_from_elements(rec_elems, project_elements):
    rec, proj = set(rec_elems), set(project_elements)
    present, foreign = rec & proj, rec - proj
    if not present or foreign:
        return "analog"
    return "exact_system" if present == proj else "baseline"


def _system_role(record_text, project_elements):
    return _role_from_elements(extract_elements(record_text), project_elements)


def build_evidence_index(project_elements, *, include_ids=None, exclude_ids=None) -> dict:
    """descriptor_name -> [ {record_id, material, reaction, domain, value, unit,
    output_quantity, functional, system_role} ]. Read-only against records;
    degrades to {} on any error so it can never break the briefing."""
    if not project_elements:
        return {}
    include_ids = list(include_ids or [])
    exclude_ids = set(exclude_ids or [])
    try:
        conn = database.get_readonly_db_connection()
    except Exception:
        return {}
    cur = conn.cursor()
    try:
        cur.execute(
            """
            WITH cand AS (
              SELECT record_id, record_domain, data FROM records
              WHERE record_id = ANY(%(inc)s)
                 OR EXISTS (
                   SELECT 1 FROM unnest(%(elems)s::text[]) e
                   WHERE data->'sample'->'material'->>'formula' ~ ('(^|[^A-Za-z])'||e||'([^a-z]|$)')
                      OR data->'sample'->'material'->>'name'    ~ ('(^|[^A-Za-z])'||e||'([^a-z]|$)')
                      OR COALESCE((data->'sample'->'composition')::text,'') ~ ('"[^"]*'||e||'[^"]*"')
                 )
            )
            SELECT c.record_id,
                   c.data->'sample'->'material'->>'name'    AS material,
                   c.data->'sample'->'material'->>'formula' AS formula,
                   c.data->'sample'->'composition'          AS composition,
                   c.data->'context'->'electrochemistry'->>'reaction' AS reaction,
                   c.record_domain AS domain,
                   c.data->'computation'->'method'->>'functional' AS functional,
                   d->>'name' AS descriptor_name, d->>'value' AS value,
                   d->>'unit' AS unit, d->>'output_quantity' AS output_quantity
            FROM cand c,
                 jsonb_array_elements(COALESCE(c.data->'descriptors'->'outputs','[]'::jsonb)) o,
                 jsonb_array_elements(COALESCE(o->'descriptors','[]'::jsonb)) d
            LIMIT 4000
            """,
            {"elems": list(project_elements), "inc": include_ids})
        rows = cur.fetchall()
    except Exception as exc:
        logger.warning("evidence index query failed: %s", exc)
        return {}
    finally:
        cur.close()
        conn.close()

    index = {}
    for r in rows:
        rid = r["record_id"]
        if rid in exclude_ids:
            continue
        name = r["descriptor_name"]
        if not name:
            continue
        # Classify by the COMPOSITION element-set (whole-token) + formula (regex),
        # NOT the free-text name — "Interdigitated" must not read as Indium, and the
        # key "CO_producing_metal" must not read as C+O. Fall back to the name only
        # if a record has neither composition nor formula.
        rec_elems = set(extract_elements(r.get("formula") or ""))
        rec_elems |= _elements_from_composition(r.get("composition"))
        if not rec_elems:
            rec_elems = set(extract_elements(r.get("material") or ""))
        role = _role_from_elements(rec_elems, project_elements)
        index.setdefault(name, []).append({
            "record_id": rid, "material": r.get("material"),
            "reaction": r.get("reaction"), "domain": r.get("domain"),
            "value": r.get("value"), "unit": r.get("unit"),
            "output_quantity": r.get("output_quantity"),
            "functional": r.get("functional"),
            "system_role": role,
        })
    return index


def _evidence_summary(index) -> dict:
    """Compact per-descriptor rollup for the briefing (the full lists are served
    on demand by GET /projects/{id}/evidence)."""
    summary = {}
    for name, items in index.items():
        roles, reactions, methods = {}, set(), set()
        for it in items:
            roles[it["system_role"]] = roles.get(it["system_role"], 0) + 1
            if it.get("reaction"):
                reactions.add(it["reaction"])
            methods.add(it.get("output_quantity") or it.get("functional")
                        or ("experimental" if it.get("domain") != "simulation" else "?"))
        summary[name] = {"n": len(items), "by_role": roles,
                         "reactions": sorted(reactions),
                         "methods": sorted(m for m in methods if m)}
    return summary


def set_evidence_overrides(project_id, *, include=None, exclude=None,
                           owner_identity=None) -> bool:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT owner_identity FROM hyp_projects WHERE project_id=%s",
                    (project_id,))
        row = cur.fetchone()
        if row is None or (owner_identity is not None
                           and row["owner_identity"] != owner_identity):
            return False
        cur.execute("UPDATE hyp_projects SET evidence_overrides=%s, updated_at=NOW() "
                    "WHERE project_id=%s",
                    (json.dumps({"include": include or [], "exclude": exclude or []}),
                     project_id))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def set_project_dataset(project_id, record_ids, *, description=None,
                        owner_identity=None, actor=None) -> bool:
    """Declare the project's DATASET OF INTEREST — the curated record set the human
    points the agent at. Anchors scope (so the agent isn't divining it from a huge
    DB) and is what coverage is checked against. The agent should use ALL of it (or
    justify exclusions) and may still reach beyond it for corroborating data."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT owner_identity FROM hyp_projects WHERE project_id=%s",
                    (project_id,))
        row = cur.fetchone()
        if row is None or (owner_identity is not None
                           and row["owner_identity"] != owner_identity):
            return False
        ids = sorted({str(x) for x in (record_ids or []) if x})
        cur.execute("UPDATE hyp_projects SET dataset=%s, updated_at=NOW() "
                    "WHERE project_id=%s",
                    (json.dumps({"record_ids": ids, "description": description,
                                 "set_by": actor, "set_at": _now_iso()}), project_id))
        _append_event(cur, project_id, "status_changed",
                      f"Dataset of interest set: {len(ids)} record(s)"
                      + (f" — {description[:80]}" if description else ""),
                      actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def compute_dataset_coverage(project_id, hyps, dataset) -> dict:
    """Coverage of the declared dataset of interest: which of its records the agent
    has actually cited as evidence, and which remain unused (shown by material name
    so a different geometry/composition/end-member that may break a confound is
    obvious). Generic — it only compares cited record_ids to the declared set."""
    ds_ids = set((dataset or {}).get("record_ids") or [])
    if not ds_ids:
        return {"declared": False,
                "_note": "No dataset of interest declared. Point the project at its "
                         "record set (PUT /projects/{id}/dataset {record_ids, "
                         "description}) so scope is explicit and coverage is checked."}
    cited = {rid for h in hyps for p in h["predictions"]
             for rid in (p.get("evidence_record_ids") or [])}
    unused = sorted(ds_ids - cited)
    summaries = resolve_record_summaries(unused) if unused else {}
    return {
        "declared": True,
        "n_dataset": len(ds_ids),
        "n_used": len(ds_ids & cited),
        "n_unused": len(unused),
        "unused_records": [{"record_id": rid,
                            "material": (summaries.get(rid) or {}).get("material")}
                           for rid in unused],
        "_note": ("Use ALL of the declared dataset (or justify excluding a record). "
                  "Unused records — especially DIFFERENT geometries/compositions or "
                  "end-members — may carry the discriminating contrast a confound "
                  "hides, so don't silently drop them."),
    }


def get_evidence(project_id, owner_identity=None, descriptor=None) -> dict | None:
    """Full descriptor-keyed evidence index for a project (optionally filtered to
    one descriptor) — the exhaustive lookup the agent runs when evaluating."""
    data = get_project(project_id, owner_identity=owner_identity)
    if data is None:
        return None
    proj = data["project"]
    elems = extract_elements(proj.get("material_system"))
    ov = proj.get("evidence_overrides") or {}
    index = build_evidence_index(elems, include_ids=ov.get("include"),
                                 exclude_ids=ov.get("exclude"))
    if descriptor:
        index = {descriptor: index.get(descriptor, [])}
    return {"project_id": project_id, "elements": elems, "evidence_index": index}


def get_context(project_id, owner_identity=None) -> dict | None:
    """ONE-SHOT complete context for a COLD-STARTING agent resuming a project it
    has never seen: full current state + the ENTIRE step-by-step reasoning history
    (every event, with detail, chronological — not the briefing's recent slice) +
    the curated briefing (which carries the evidence-index summary and the
    discrimination matrix). Read-access enforced via get_project."""
    data = get_project(project_id, owner_identity=owner_identity)
    if data is None:
        return None
    conn = _conn()
    cur = conn.cursor()
    try:
        # FULL history (no 200 cap), oldest -> newest, with detail.
        cur.execute("SELECT * FROM hyp_events WHERE project_id=%s "
                    "ORDER BY created_at ASC LIMIT 5000", (project_id,))
        history = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return {
        "resume_note": "Complete project context for a fresh agent. Read the full "
            "state and the entire `history` below to reconstruct how the project got "
            "here, then follow the prime directive: GET /briefing each turn, write "
            "every step back. If it isn't on the dashboard, it didn't happen.",
        "project": data["project"],
        "hypotheses": data["hypotheses"],   # each with predictions + compute_runs
        "relations": data["relations"],
        "next_experiment": data["next_experiment"],
        "n_history": len(history),
        "history": history,                 # ALL steps, chronological, full detail
        "confidence_history": get_confidence_history(project_id,
                                                     owner_identity=owner_identity),
        "pending_work": get_pending_work(project_id),  # resumable loose threads
        "briefing": get_briefing(project_id, owner_identity=owner_identity),
    }


# --- Provenance (READ-ONLY against the records DB) -------------------------

def resolve_record_summaries(record_ids) -> dict:
    """Map ISAAC record_ids -> a short display summary, via the records RO
    connection. Cross-DB by design: discovery stores record_ids as plain
    strings; we look them up read-only for the provenance throughline. Never
    writes, never uses the privileged connection. Degrades to {} on any issue."""
    if not record_ids:
        return {}
    try:
        conn = database.get_readonly_db_connection()
    except Exception:
        return {}
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT record_id,
                      data->'sample'->'material'->>'name' AS material,
                      data->'context'->'electrochemistry'->>'reaction' AS reaction,
                      record_domain
                 FROM records WHERE record_id = ANY(%s)""",
            (list(record_ids),))
        out = {}
        for r in cur.fetchall():
            rid = r["record_id"] if isinstance(r, dict) else r[0]
            if isinstance(r, dict):
                out[rid] = {"material": r["material"], "reaction": r["reaction"],
                            "domain": r["record_domain"]}
            else:
                out[rid] = {"material": r[1], "reaction": r[2], "domain": r[3]}
        return out
    except Exception:
        return {}
    finally:
        cur.close()
        conn.close()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
