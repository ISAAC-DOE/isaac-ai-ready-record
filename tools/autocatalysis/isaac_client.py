#!/usr/bin/env python3
"""
ISAAC API client for AutoCatalysis.

Provides methods to query and submit records to the ISAAC Portal API.
Used by evaluate.py to fetch experimental benchmarks and by the agent
to submit computed results.

Usage:
    from isaac_client import IsaacClient

    client = IsaacClient(api_token="your_token")
    records = client.query_co2rr_performance(surface="Cu")
    client.submit_record(record_dict)
"""

import json
import os
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ISAAC_API_BASE = os.environ.get(
    "ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api"
)

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class IsaacClient:
    """Client for the ISAAC Portal REST API."""

    def __init__(self, api_token: str = None, base_url: str = None):
        self.base_url = base_url or ISAAC_API_BASE
        self.api_token = api_token or os.environ.get("ISAAC_API_TOKEN", "")
        self.session = requests.Session()
        if self.api_token:
            self.session.headers["Authorization"] = f"Bearer {self.api_token}"
        self.session.headers["Content-Type"] = "application/json"

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request with retry logic."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"ISAAC API request failed after {MAX_RETRIES} attempts: "
                        f"{method} {url} — {last_error}"
                    ) from last_error

    # -------------------------------------------------------------------
    # Health & schema
    # -------------------------------------------------------------------
    def health(self) -> dict:
        """Check API health."""
        return self._request("GET", "health").json()

    def schema(self) -> dict:
        """Fetch the ISAAC record schema."""
        return self._request("GET", "schema").json()

    # -------------------------------------------------------------------
    # Record CRUD
    # -------------------------------------------------------------------
    def submit_record(self, record: dict) -> dict:
        """Submit a record to the ISAAC database.

        Returns the API response (record ID, status).
        """
        resp = self._request("POST", "records", json=record)
        return resp.json()

    def submit_records(self, records: list) -> dict:
        """Submit multiple records. Returns summary of successes/failures."""
        ok = 0
        fail = 0
        errors = []
        for record in records:
            try:
                self.submit_record(record)
                ok += 1
            except Exception as e:
                fail += 1
                errors.append({
                    "record_id": record.get("record_id", "unknown"),
                    "error": str(e),
                })
        return {"submitted": ok, "failed": fail, "errors": errors}

    def get_records(self, params: dict = None) -> list:
        """Query records from the ISAAC database.

        Args:
            params: Query parameters (domain, material, etc.).

        Returns:
            List of ISAAC records.
        """
        resp = self._request("GET", "records", params=params or {})
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("records", data.get("data", []))

    def validate_record(self, record: dict) -> dict:
        """Validate a record against the ISAAC schema without saving."""
        resp = self._request("POST", "validate", json=record)
        return resp.json()

    # -------------------------------------------------------------------
    # CO2RR-specific queries
    # -------------------------------------------------------------------
    def query_co2rr_performance(self, surface: str = None) -> list:
        """Query experimental CO2RR performance records from ISAAC.

        Args:
            surface: Optional surface filter (e.g., "Cu").

        Returns:
            List of performance records with Faradaic efficiency descriptors.
        """
        params = {"domain": "performance"}
        if surface:
            params["material"] = surface

        records = self.get_records(params)

        # Filter for CO2RR-relevant records (those with faradaic_efficiency descriptors)
        co2rr_records = []
        for record in records:
            descriptors = record.get("descriptors", {}).get("outputs", [])
            for output in descriptors:
                for desc in output.get("descriptors", []):
                    if "faradaic_efficiency" in desc.get("name", ""):
                        co2rr_records.append(record)
                        break
                else:
                    continue
                break

        return co2rr_records

    def query_simulation_records(self, surface: str = None,
                                  adsorbate: str = None) -> list:
        """Query simulation records (DFT/MLIP adsorption energies).

        Args:
            surface: Optional surface filter.
            adsorbate: Optional adsorbate filter.

        Returns:
            List of simulation records with adsorption energy descriptors.
        """
        params = {"domain": "simulation"}
        if surface:
            params["material"] = surface

        records = self.get_records(params)

        # Filter by adsorbate if specified
        if adsorbate:
            filtered = []
            for record in records:
                config = record.get("system", {}).get("configuration", {})
                if config.get("adsorbate") == adsorbate:
                    filtered.append(record)
                # Also check descriptor names
                for output in record.get("descriptors", {}).get("outputs", []):
                    for desc in output.get("descriptors", []):
                        if adsorbate in desc.get("name", ""):
                            if record not in filtered:
                                filtered.append(record)
            return filtered

        return records

    def extract_faradaic_efficiencies(self, records: list) -> list:
        """Extract Faradaic efficiency values from performance records.

        Returns:
            List of dicts: {surface, facet, product, FE, potential_V_vs_RHE}
        """
        results = []
        for record in records:
            # Get surface info
            material = record.get("sample", {}).get("material", {})
            surface = material.get("formula", material.get("name", "unknown"))

            # Get potential
            context = record.get("context", {})
            potential = context.get("applied_potential_V_vs_RHE")
            if potential is None:
                pc = context.get("potential_control", {})
                potential = pc.get("setpoint_V")

            # Get facet from configuration or material name
            config = record.get("system", {}).get("configuration", {})
            facet = config.get("facet", "poly")

            # Extract FE descriptors
            for output in record.get("descriptors", {}).get("outputs", []):
                for desc in output.get("descriptors", []):
                    name = desc.get("name", "")
                    if "faradaic_efficiency" in name:
                        product = name.replace("faradaic_efficiency.", "").replace(
                            "faradaic_efficiency_", ""
                        )
                        results.append({
                            "surface": surface,
                            "facet": facet,
                            "product": product,
                            "FE": desc.get("value"),
                            "unit": desc.get("unit", "fraction"),
                            "potential_V_vs_RHE": potential,
                            "record_id": record.get("record_id"),
                        })

        return results

    def extract_adsorption_energies(self, records: list) -> list:
        """Extract adsorption energies from simulation records.

        Returns:
            List of dicts: {surface, facet, adsorbate, E_ads_eV}
        """
        results = []
        for record in records:
            config = record.get("system", {}).get("configuration", {})
            surface = config.get("surface_composition", "unknown")
            facet = config.get("facet", "unknown")
            adsorbate = config.get("adsorbate", "unknown")

            for output in record.get("descriptors", {}).get("outputs", []):
                for desc in output.get("descriptors", []):
                    name = desc.get("name", "")
                    if "E_ads" in name or "adsorption_energy" in name.lower():
                        results.append({
                            "surface": surface,
                            "facet": facet,
                            "adsorbate": adsorbate,
                            "E_ads_eV": desc.get("value"),
                            "record_id": record.get("record_id"),
                        })

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """Quick test: check ISAAC API health and query records."""
    import argparse

    parser = argparse.ArgumentParser(description="ISAAC API client for AutoCatalysis")
    parser.add_argument("--api-token", type=str,
                        default=os.environ.get("ISAAC_API_TOKEN", ""),
                        help="ISAAC API bearer token")
    parser.add_argument("--health", action="store_true", help="Check API health")
    parser.add_argument("--query-co2rr", type=str, metavar="SURFACE",
                        help="Query CO2RR performance for a surface")
    parser.add_argument("--query-sims", type=str, metavar="SURFACE",
                        help="Query simulation records for a surface")
    args = parser.parse_args()

    client = IsaacClient(api_token=args.api_token)

    if args.health:
        print(json.dumps(client.health(), indent=2))
    elif args.query_co2rr:
        records = client.query_co2rr_performance(surface=args.query_co2rr)
        fe_data = client.extract_faradaic_efficiencies(records)
        print(f"Found {len(records)} CO2RR performance records for {args.query_co2rr}")
        for fe in fe_data:
            print(f"  {fe['surface']}({fe['facet']}) → {fe['product']}: "
                  f"FE={fe['FE']} at {fe['potential_V_vs_RHE']} V vs RHE")
    elif args.query_sims:
        records = client.query_simulation_records(surface=args.query_sims)
        energies = client.extract_adsorption_energies(records)
        print(f"Found {len(records)} simulation records for {args.query_sims}")
        for e in energies:
            print(f"  {e['surface']}({e['facet']}) + *{e['adsorbate']}: "
                  f"E_ads = {e['E_ads_eV']} eV")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
