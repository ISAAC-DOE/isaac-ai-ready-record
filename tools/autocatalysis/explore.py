"""
AutoCatalysis — Catalyst candidate definitions.

This file is the ONLY file the autonomous agent modifies.
It defines which catalyst surfaces to screen and which descriptors to compute.

The agent adds candidates to CANDIDATES and descriptors to DESCRIPTORS
based on its search strategy (see program.md).
"""

# ---------------------------------------------------------------------------
# Catalyst candidates to evaluate
# ---------------------------------------------------------------------------
# Each candidate defines a surface, crystal facet, and adsorbates to test.
# The agent adds new entries here each iteration.

CANDIDATES = [
    # Phase 1: Pure metal survey — common facets
    {"surface": "Cu", "facet": "111", "adsorbates": ["CO", "COOH", "CHO"]},
    {"surface": "Cu", "facet": "100", "adsorbates": ["CO", "COOH", "CHO"]},
    {"surface": "Cu", "facet": "110", "adsorbates": ["CO", "COOH", "CHO"]},
    {"surface": "Au", "facet": "111", "adsorbates": ["CO", "COOH"]},
    {"surface": "Au", "facet": "100", "adsorbates": ["CO", "COOH"]},
    {"surface": "Ag", "facet": "111", "adsorbates": ["CO", "COOH"]},
    {"surface": "Ag", "facet": "100", "adsorbates": ["CO", "COOH"]},
    {"surface": "Zn", "facet": "0001", "adsorbates": ["CO", "COOH"]},
    {"surface": "Sn", "facet": "100", "adsorbates": ["CO", "COOH"]},
]

# ---------------------------------------------------------------------------
# Descriptors to compute for each candidate
# ---------------------------------------------------------------------------
# The agent can add more descriptors to track which best predicts activity.

DESCRIPTORS = [
    "E_ads_CO",      # Primary volcano descriptor
    "E_ads_COOH",    # Rate-limiting step descriptor
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Volcano peak for *CO binding (eV) — optimal for C2+ products
VOLCANO_PEAK_CO = -0.67

# Maximum candidates per agent iteration
MAX_NEW_CANDIDATES_PER_ITERATION = 8
