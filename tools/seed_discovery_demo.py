#!/usr/bin/env python3
"""
Seed the Cu-Au CO2RR demo project into the Discovery workbench via the LIVE REST
API. Doubles as an end-to-end contract test: it exercises POST /projects,
POST .../hypotheses, PUT /hypotheses/{id} (status only), and PUT .../next_experiment
exactly as the ISAAC agent will.

Creates the project + 5 competing hypotheses (label/statement/status) and the
proposed next experiment. Confidence is NOT seeded — under the canonical model the
platform COMPUTES it from prediction verdicts; an authored number is meaningless.
Predictions, verdicts, and evidence_record_ids are left for the ISAAC agent to
supply live (that is what actually moves each hypothesis's confidence).

Usage:
  export ISAAC_API_BASE="https://<portal-host>/portal/api"
  export ISAAC_API_TOKEN="<bearer token from the portal API Keys page>"
  python3 tools/seed_discovery_demo.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("ISAAC_API_BASE", "").rstrip("/")
TOKEN = os.environ.get("ISAAC_API_TOKEN", "")

PROJECT = {
    "title": "Cu–Au CO₂RR mechanism discrimination",
    "goal": "Determine which mechanism controls C₂H₄ selectivity in "
            "Cu–Au patterned electrodes",
    "material_system": "Cu-Au",
    "reaction": "CO2RR",
}

# (label, name, status, statement) — NO authored confidence; status is an
# independent, human/agent-set field, confidence is computed from verdicts.
HYPOTHESES = [
    ("H-001", "CO Spillover", "supported",
     "CO generated on Au spills over to adjacent Cu sites, raising local CO "
     "coverage and boosting C-C coupling to C2H4."),
    ("H-002", "Electronic Modification", "eliminated",
     "Au alloying shifts the Cu d-band center, intrinsically changing binding "
     "energies that set C2H4 selectivity."),
    ("H-003", "Interfacial Strain", "needs_more_data",
     "Lattice mismatch strain at Cu-Au boundaries modifies adsorbate binding "
     "and the C-C coupling barrier."),
    ("H-004", "Local pH Gradient", "needs_more_data",
     "Geometry-dependent local pH at the Cu-Au pattern alters the CO2/CO/OH- "
     "balance and thereby C2H4 vs C1 selectivity."),
    ("H-005", "Galvanic Coupling", "eliminated",
     "Galvanic potential differences between Cu and Au domains drive a local "
     "bias that changes the operative reaction."),
]

NEXT_EXPERIMENT = {
    "descriptor": "cation-perturbation operando AP-XPS",
    "facility": "SSRL",
    "method": "operando ambient-pressure XPS under cation perturbation",
    "rationale": "Discriminates CO-supply (H-001) from local-pH (H-004): "
                 "perturbing the cation identity moves the local-pH hypothesis "
                 "prediction but not the CO-spillover one.",
    "discriminates": [
        {"hypothesis_label": "H-001", "expected": "CO coverage on Cu tracks Au "
         "proximity, insensitive to cation identity"},
        {"hypothesis_label": "H-004", "expected": "interfacial pH proxy shifts "
         "systematically with cation identity"},
    ],
}


def _req(method, path, body=None):
    if not BASE or not TOKEN:
        sys.exit("Set ISAAC_API_BASE and ISAAC_API_TOKEN env vars first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def main():
    print(f"Seeding against {BASE}")
    status, resp = _req("POST", "/projects", PROJECT)
    if status != 201:
        sys.exit(f"create project failed [{status}]: {resp}")
    pid = resp["project_id"]
    print(f"  project_id = {pid}")

    for label, name, hstatus, statement in HYPOTHESES:
        s, r = _req("POST", f"/projects/{pid}/hypotheses",
                    {"label": label, "statement": f"{name}: {statement}",
                     "hypothesis_type": "mechanism"})
        if s != 201:
            sys.exit(f"create {label} failed [{s}]: {r}")
        hid = r["hypothesis_id"]
        # status only — confidence is computed by the platform from prediction verdicts.
        s, r = _req("PUT", f"/hypotheses/{hid}", {"status": hstatus})
        if s != 200:
            sys.exit(f"update {label} failed [{s}]: {r}")
        print(f"  {label} {name}: {hstatus}  -> {hid}")

    s, r = _req("PUT", f"/projects/{pid}/next_experiment", NEXT_EXPERIMENT)
    if s != 200:
        sys.exit(f"set next_experiment failed [{s}]: {r}")
    print("  next_experiment set")
    print(f"\nDone. Open the Discovery page and select '{PROJECT['title']}'.")
    print(f"project_id for the ISAAC agent: {pid}")


if __name__ == "__main__":
    main()
