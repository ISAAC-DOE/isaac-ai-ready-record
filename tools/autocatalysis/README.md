# AutoCatalysis — Autonomous AI-Driven Catalyst Discovery

An autonomous AI agent that screens catalyst surfaces for CO2 electroreduction (CO2RR) using FAIRChem ML interatomic potentials, validated against experimental data in ISAAC.

## Quick Start

### Prerequisites

```bash
# Install FAIRChem (GPU recommended but CPU works)
pip install fairchem-core

# ASE should already be installed; if not:
pip install ase

# HuggingFace token for UMA model access
export HF_TOKEN="your_huggingface_token"
huggingface-cli login
```

### Run Tier 1 (Theory-Only Screening)

```bash
# Evaluate all candidates in explore.py
python tools/autocatalysis/evaluate.py --theory-only

# Evaluate a single surface
python tools/autocatalysis/evaluate.py --surface Cu --facet 100 --theory-only

# Use CPU explicitly
python tools/autocatalysis/evaluate.py --theory-only --device cpu
```

### Run Tier 2 (With Experimental Validation)

```bash
# First, ingest experimental CO2RR data into ISAAC
python tools/ingest_co2rr_literature.py --save-api --api-token YOUR_TOKEN

# Then run with experimental comparison
python tools/autocatalysis/evaluate.py --with-experiments --api-token YOUR_TOKEN
```

### Run Individual Components

```bash
# Compute a single adsorption energy
python tools/autocatalysis/run_fairchem.py --surface Cu --facet 111 --adsorbate CO

# Compute all candidates from explore.py
python tools/autocatalysis/run_fairchem.py --from-explore --output results_raw.json

# Query ISAAC for experimental data
python tools/autocatalysis/isaac_client.py --query-co2rr Cu --api-token YOUR_TOKEN

# Check ISAAC API health
python tools/autocatalysis/isaac_client.py --health
```

## Architecture

```
tools/autocatalysis/
├── program.md          # Agent instructions (CO2RR domain knowledge)
├── explore.py          # Catalyst candidates (ONLY file the agent modifies)
├── evaluate.py         # Scoring: volcano distance + Spearman correlation
├── run_fairchem.py     # FAIRChem wrapper: slab → adsorption energy
├── isaac_client.py     # ISAAC API queries for experimental benchmarks
├── results.json        # Running log of all evaluation iterations
└── README.md           # This file
```

### How It Works

1. **Agent reads** `program.md` for CO2RR domain knowledge and search strategy
2. **Agent modifies** `explore.py` to add new catalyst candidates
3. **`evaluate.py` runs** `run_fairchem.py` to compute adsorption energies
4. **`evaluate.py` scores** candidates against the CO2RR volcano
5. **If Tier 2**: `isaac_client.py` fetches experimental FE data for Spearman correlation
6. **Results** are appended to `results.json`
7. **Agent decides**: keep (git commit) or revert based on score improvement

## Tiers

| Tier | Data Source | Metric | Status |
|------|-------------|--------|--------|
| 1 | FAIRChem only | Volcano distance (E_ads_CO vs -0.67 eV) | Ready |
| 2 | + ISAAC experimental FE | Spearman ρ (E_ads vs C2+ FE) | Ready |
| 3 | + Operando XAS | Multi-modal structure-activity | Planned |
| 4 | + OER extension | OER volcano (*O, *OH, *OOH) | Planned |

## Autonomous Agent Loop

The agent runs in a loop, modifying `explore.py` each iteration:

```
while True:
    1. Read explore.py → get current candidates
    2. Run evaluate.py → compute scores
    3. If score improved:
       → git commit "Iteration N: found {surface} with E_ads = {value}"
    4. If score worsened:
       → git revert
    5. Analyze results.json → decide next candidates
    6. Modify explore.py → add 3-8 new candidates
    7. Repeat
```

## Key Domain Knowledge

- **Volcano peak**: *CO binding ~ -0.67 eV is optimal for C2+ products
- **Scaling**: *CO vs *COOH linear with slope ~0.8
- **Known ranking**: Cu(100) > Cu(110) > Cu(111) for C2+ selectivity
- **Alloy strategy**: mix strong binder (Cu) with weak binder (Au, Ag) to tune *CO binding
