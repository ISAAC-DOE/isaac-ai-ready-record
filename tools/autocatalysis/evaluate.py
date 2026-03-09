#!/usr/bin/env python3
"""
AutoCatalysis scoring engine.

Evaluates catalyst candidates by computing adsorption energies via FAIRChem
and scoring them against the CO2RR activity volcano. When experimental data
is available in ISAAC, computes Spearman rank correlation between predicted
binding energies and experimental Faradaic efficiencies.

Tier 1 (theory-only): Score = distance from volcano peak (*CO ~ -0.67 eV)
Tier 2 (with experiments): Score = Spearman ρ (predicted E_ads vs expt FE)

Usage:
    # Theory-only scoring (Tier 1)
    python evaluate.py --theory-only

    # With experimental validation (Tier 2)
    python evaluate.py --with-experiments --api-token YOUR_TOKEN

    # Score a single surface
    python evaluate.py --surface Cu --facet 100 --theory-only
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Theory-only scoring (Tier 1)
# ---------------------------------------------------------------------------

# Volcano peak for *CO binding energy (eV)
# From Norskov et al., optimal CO2RR to C2+ products
VOLCANO_PEAK_CO = -0.67

# Classification thresholds
EXCELLENT_THRESHOLD = 0.1   # |ΔE| < 0.1 eV → near volcano peak
PROMISING_THRESHOLD = 0.3   # |ΔE| < 0.3 eV → worth investigating
POOR_THRESHOLD = 0.5        # |ΔE| > 0.5 eV → unlikely selective


def volcano_distance(E_ads_CO: float) -> float:
    """Distance from the volcano peak in eV. Lower = better predicted catalyst."""
    return abs(E_ads_CO - VOLCANO_PEAK_CO)


def classify_candidate(distance: float) -> str:
    """Classify a candidate based on volcano distance."""
    if distance < EXCELLENT_THRESHOLD:
        return "excellent"
    elif distance < PROMISING_THRESHOLD:
        return "promising"
    elif distance < POOR_THRESHOLD:
        return "moderate"
    else:
        return "poor"


def score_theory_only(results: list) -> dict:
    """Score candidates using theory-only volcano distance metric.

    Args:
        results: List of FAIRChem results from run_fairchem.evaluate_candidates().
                 Each dict must have: surface, facet, adsorbate, E_ads_eV.

    Returns:
        Scoring summary with ranked candidates.
    """
    # Extract *CO binding energies
    co_results = [r for r in results if r.get("adsorbate") == "CO"
                  and r.get("E_ads_eV") is not None]

    if not co_results:
        return {
            "metric": "volcano_distance",
            "n_candidates": 0,
            "error": "No *CO adsorption energies found in results.",
        }

    # Score each candidate
    scored = []
    for r in co_results:
        dist = volcano_distance(r["E_ads_eV"])
        scored.append({
            "surface": r["surface"],
            "facet": r["facet"],
            "E_ads_CO_eV": r["E_ads_eV"],
            "volcano_distance": round(dist, 4),
            "classification": classify_candidate(dist),
            "binding_character": "too_strong" if r["E_ads_eV"] < VOLCANO_PEAK_CO else "too_weak",
        })

    # Sort by volcano distance (best first)
    scored.sort(key=lambda x: x["volcano_distance"])

    # Summary statistics
    excellent = [s for s in scored if s["classification"] == "excellent"]
    promising = [s for s in scored if s["classification"] == "promising"]

    return {
        "metric": "volcano_distance",
        "volcano_peak_eV": VOLCANO_PEAK_CO,
        "n_candidates": len(scored),
        "n_excellent": len(excellent),
        "n_promising": len(promising),
        "best_candidate": scored[0] if scored else None,
        "ranked_candidates": scored,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Experimental validation scoring (Tier 2)
# ---------------------------------------------------------------------------

def score_with_experiments(results: list, experimental_data: list) -> dict:
    """Score using Spearman rank correlation between predictions and experiments.

    Compares predicted *CO binding energies with experimental Faradaic
    efficiencies for C2+ products. Higher ρ = better prediction.

    Args:
        results: FAIRChem results with E_ads_CO for each surface.
        experimental_data: List of dicts from ISAAC with
                          {surface, facet, product, FE, potential_V_vs_RHE}.

    Returns:
        Scoring summary with Spearman correlation.
    """
    # Match predicted surfaces with experimental data
    matched_pairs = _match_theory_experiment(results, experimental_data)

    if len(matched_pairs) < 3:
        # Fall back to theory-only scoring if insufficient overlap
        theory_score = score_theory_only(results)
        theory_score["experimental_note"] = (
            f"Only {len(matched_pairs)} surface(s) matched between theory and "
            f"experiment (need >= 3 for correlation). Using theory-only scoring."
        )
        return theory_score

    # Extract paired values
    E_ads_values = [p["E_ads_CO_eV"] for p in matched_pairs]
    FE_c2plus_values = [p["FE_C2plus"] for p in matched_pairs]

    # Compute Spearman rank correlation
    rho, p_value = _spearman_correlation(E_ads_values, FE_c2plus_values)

    return {
        "metric": "spearman_rank_correlation",
        "spearman_rho": round(rho, 4),
        "p_value": round(p_value, 6),
        "n_matched_surfaces": len(matched_pairs),
        "matched_pairs": matched_pairs,
        "interpretation": _interpret_correlation(rho),
        "theory_scores": score_theory_only(results),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _match_theory_experiment(results: list, experimental_data: list) -> list:
    """Match theoretical predictions with experimental measurements.

    Matches on surface composition and (optionally) facet.
    For experiments without facet info, uses the polycrystalline average.
    """
    # Build lookup: (surface, facet) → E_ads_CO
    theory_lookup = {}
    for r in results:
        if r.get("adsorbate") == "CO" and r.get("E_ads_eV") is not None:
            key = (r["surface"], r["facet"])
            theory_lookup[key] = r["E_ads_eV"]

    # Aggregate experimental C2+ FE per surface/facet
    expt_c2plus = {}
    c2_products = {"C2H4", "C2H6", "EtOH", "C2H5OH", "PrOH", "nPrOH",
                   "acetaldehyde", "acetate", "allyl_alcohol"}

    for e in experimental_data:
        key = (e["surface"], e.get("facet", "poly"))
        product = e.get("product", "")

        # Sum C2+ Faradaic efficiencies
        if product in c2_products or product.startswith("C2") or product.startswith("C3"):
            fe_val = e.get("FE", 0) or 0
            if key not in expt_c2plus:
                expt_c2plus[key] = 0.0
            expt_c2plus[key] += fe_val

    # Match
    matched = []
    for (surface, facet), fe_c2plus in expt_c2plus.items():
        theory_key = (surface, facet)
        if theory_key in theory_lookup:
            matched.append({
                "surface": surface,
                "facet": facet,
                "E_ads_CO_eV": theory_lookup[theory_key],
                "FE_C2plus": fe_c2plus,
            })
        elif facet == "poly":
            # Try to match polycrystalline with any available facet
            for (ts, tf), e_ads in theory_lookup.items():
                if ts == surface:
                    matched.append({
                        "surface": surface,
                        "facet": f"{tf} (matched to poly)",
                        "E_ads_CO_eV": e_ads,
                        "FE_C2plus": fe_c2plus,
                    })
                    break

    return matched


def _spearman_correlation(x: list, y: list) -> tuple:
    """Compute Spearman rank correlation coefficient.

    Pure Python implementation (no scipy dependency).

    Returns:
        (rho, p_value) tuple.
    """
    n = len(x)
    if n < 3:
        return (0.0, 1.0)

    # Rank the values
    x_ranks = _rank_data(x)
    y_ranks = _rank_data(y)

    # Spearman ρ = 1 - 6Σd² / (n(n²-1))
    d_squared_sum = sum((xr - yr) ** 2 for xr, yr in zip(x_ranks, y_ranks))
    rho = 1 - (6 * d_squared_sum) / (n * (n ** 2 - 1))

    # Approximate p-value using t-distribution
    import math
    if abs(rho) >= 1.0:
        p_value = 0.0
    else:
        t_stat = rho * math.sqrt((n - 2) / (1 - rho ** 2))
        # Two-tailed p-value approximation
        p_value = 2 * (1 - _t_cdf(abs(t_stat), n - 2))

    return (rho, p_value)


def _rank_data(values: list) -> list:
    """Assign ranks to values (average rank for ties)."""
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n

    i = 0
    while i < n:
        j = i
        while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1

    return ranks


def _t_cdf(t: float, df: int) -> float:
    """Approximate CDF of Student's t-distribution.

    Uses the regularized incomplete beta function approximation.
    Good enough for significance testing.
    """
    import math
    x = df / (df + t * t)
    # Approximate using normal distribution for large df
    if df > 30:
        z = t * (1 - 1 / (4 * df))
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))
    # Simple approximation for small df
    return 0.5 * (1 + math.erf(t / math.sqrt(2 + 4.0 / df)))


def _interpret_correlation(rho: float) -> str:
    """Interpret Spearman correlation coefficient."""
    abs_rho = abs(rho)
    if abs_rho >= 0.9:
        strength = "very strong"
    elif abs_rho >= 0.7:
        strength = "strong"
    elif abs_rho >= 0.5:
        strength = "moderate"
    elif abs_rho >= 0.3:
        strength = "weak"
    else:
        strength = "negligible"

    direction = "positive" if rho > 0 else "negative"
    return (
        f"{strength.capitalize()} {direction} correlation (ρ = {rho:.3f}). "
        f"{'Predictions agree well with experiments.' if abs_rho >= 0.7 else 'Predictions need improvement.'}"
    )


# ---------------------------------------------------------------------------
# Full evaluation pipeline
# ---------------------------------------------------------------------------

def run_evaluation(theory_only: bool = True, device: str = "auto",
                   api_token: str = None, surface_filter: str = None,
                   facet_filter: str = None) -> dict:
    """Run the full evaluation pipeline.

    1. Import candidates from explore.py
    2. Compute adsorption energies via FAIRChem
    3. Score against volcano (Tier 1) or experiments (Tier 2)
    4. Save results to results.json

    Args:
        theory_only: If True, use volcano distance only. If False, also
                     query ISAAC for experimental validation.
        device: FAIRChem compute device.
        api_token: ISAAC API token (needed for Tier 2).
        surface_filter: Optional single-surface evaluation.
        facet_filter: Optional facet filter.

    Returns:
        Evaluation summary dict.
    """
    # Import candidates
    sys.path.insert(0, os.path.dirname(__file__))
    from explore import CANDIDATES

    # Filter if requested
    candidates = CANDIDATES
    if surface_filter:
        candidates = [c for c in candidates if c["surface"] == surface_filter]
    if facet_filter:
        candidates = [c for c in candidates if c["facet"] == facet_filter]

    if not candidates:
        return {"error": "No candidates match the filter criteria."}

    # Compute adsorption energies
    from run_fairchem import evaluate_candidates
    print(f"\nEvaluating {len(candidates)} candidates...")
    results = evaluate_candidates(candidates, device=device)

    # Score
    if theory_only:
        scores = score_theory_only(results)
    else:
        from isaac_client import IsaacClient
        client = IsaacClient(api_token=api_token)
        expt_records = client.query_co2rr_performance()
        expt_data = client.extract_faradaic_efficiencies(expt_records)
        scores = score_with_experiments(results, expt_data)

    # Combine results
    evaluation = {
        "evaluation_type": "theory_only" if theory_only else "theory_vs_experiment",
        "n_candidates_evaluated": len(candidates),
        "n_calculations_completed": sum(1 for r in results if r.get("converged")),
        "scores": scores,
        "raw_results": results,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Append to results.json
    _append_results(evaluation)

    return evaluation


def _append_results(evaluation: dict):
    """Append evaluation results to results.json log."""
    results_path = os.path.join(os.path.dirname(__file__), "results.json")

    # Load existing results
    if os.path.exists(results_path):
        with open(results_path) as f:
            try:
                log = json.load(f)
            except json.JSONDecodeError:
                log = {"iterations": []}
    else:
        log = {"iterations": []}

    # Add this iteration
    iteration = {
        "iteration": len(log["iterations"]) + 1,
        "timestamp": evaluation["timestamp"],
        "evaluation_type": evaluation["evaluation_type"],
        "n_candidates": evaluation["n_candidates_evaluated"],
        "n_completed": evaluation["n_calculations_completed"],
    }

    # Add key scores
    scores = evaluation.get("scores", {})
    if scores.get("metric") == "volcano_distance":
        best = scores.get("best_candidate")
        if best:
            iteration["best_surface"] = f"{best['surface']}({best['facet']})"
            iteration["best_volcano_distance"] = best["volcano_distance"]
            iteration["best_E_ads_CO"] = best["E_ads_CO_eV"]
        iteration["n_excellent"] = scores.get("n_excellent", 0)
        iteration["n_promising"] = scores.get("n_promising", 0)
    elif scores.get("metric") == "spearman_rank_correlation":
        iteration["spearman_rho"] = scores.get("spearman_rho")
        iteration["n_matched"] = scores.get("n_matched_surfaces")

    # Store full ranked list for the latest iteration
    iteration["ranked_candidates"] = scores.get("ranked_candidates", [])

    log["iterations"].append(iteration)

    # Update summary
    all_candidates = set()
    for it in log["iterations"]:
        for c in it.get("ranked_candidates", []):
            all_candidates.add(f"{c['surface']}({c['facet']})")
    log["total_unique_surfaces_screened"] = len(all_candidates)

    # Find overall best
    all_ranked = []
    for it in log["iterations"]:
        all_ranked.extend(it.get("ranked_candidates", []))
    if all_ranked:
        all_ranked.sort(key=lambda x: x.get("volcano_distance", float("inf")))
        # Deduplicate — keep best score per surface
        seen = set()
        best_overall = []
        for c in all_ranked:
            key = f"{c['surface']}({c['facet']})"
            if key not in seen:
                seen.add(key)
                best_overall.append(c)
        log["best_candidates_overall"] = best_overall[:10]

    with open(results_path, "w") as f:
        json.dump(log, f, indent=2)

    print(f"\nResults saved to {results_path}")
    print(f"  Iteration {iteration['iteration']}: "
          f"{iteration['n_candidates']} candidates evaluated")
    if "best_surface" in iteration:
        print(f"  Best: {iteration['best_surface']} "
              f"(volcano distance = {iteration['best_volcano_distance']:.4f} eV)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AutoCatalysis scoring engine for CO2RR catalyst screening."
    )
    parser.add_argument("--theory-only", action="store_true", default=True,
                        help="Use theory-only volcano distance scoring (default)")
    parser.add_argument("--with-experiments", action="store_true",
                        help="Also validate against ISAAC experimental data")
    parser.add_argument("--device", type=str, default="auto",
                        choices=["auto", "cuda", "cpu"], help="Compute device")
    parser.add_argument("--api-token", type=str,
                        default=os.environ.get("ISAAC_API_TOKEN", ""),
                        help="ISAAC API token for experimental queries")
    parser.add_argument("--surface", type=str, help="Evaluate a single surface")
    parser.add_argument("--facet", type=str, help="Filter by facet")
    args = parser.parse_args()

    theory_only = not args.with_experiments
    if args.with_experiments and not args.api_token:
        print("Warning: --with-experiments requires --api-token or ISAAC_API_TOKEN. "
              "Falling back to theory-only.")
        theory_only = True

    evaluation = run_evaluation(
        theory_only=theory_only,
        device=args.device,
        api_token=args.api_token,
        surface_filter=args.surface,
        facet_filter=args.facet,
    )

    # Print summary
    scores = evaluation.get("scores", {})
    print(f"\n{'=' * 60}")
    print(f"AutoCatalysis Evaluation Summary")
    print(f"{'=' * 60}")
    print(f"Metric: {scores.get('metric', 'unknown')}")
    print(f"Candidates: {evaluation.get('n_candidates_evaluated', 0)}")
    print(f"Completed: {evaluation.get('n_calculations_completed', 0)}")

    if scores.get("metric") == "volcano_distance":
        print(f"Excellent (< {EXCELLENT_THRESHOLD} eV): {scores.get('n_excellent', 0)}")
        print(f"Promising (< {PROMISING_THRESHOLD} eV): {scores.get('n_promising', 0)}")
        print(f"\nTop 5 candidates:")
        for i, c in enumerate(scores.get("ranked_candidates", [])[:5], 1):
            print(f"  {i}. {c['surface']}({c['facet']}) — "
                  f"E_ads_CO = {c['E_ads_CO_eV']:.4f} eV, "
                  f"Δ = {c['volcano_distance']:.4f} eV "
                  f"[{c['classification']}]")
    elif scores.get("metric") == "spearman_rank_correlation":
        print(f"Spearman ρ: {scores.get('spearman_rho', 'N/A')}")
        print(f"p-value: {scores.get('p_value', 'N/A')}")
        print(f"Matched surfaces: {scores.get('n_matched_surfaces', 0)}")
        print(f"Interpretation: {scores.get('interpretation', '')}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
