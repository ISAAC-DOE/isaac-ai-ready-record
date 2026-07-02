# ISAAC AI-Ready Scientific Record

**Website:** [isaac.slac.stanford.edu](https://isaac.slac.stanford.edu) — ISAAC (Integrated Scientific Agentic AI for Catalysis), an AI Pathfinder project of the DOE Office of Basic Energy Sciences within the Genesis Mission.

**Version:** 1.0 (Frozen)

## Overview
This repository defines the authoritative standard for the **ISAAC AI-Ready Record**. 
It provides the schema, documentation, and examples required to represent scientific data in a format that is semantically rigorous, machine-readable, and optimized for autonomous agent reasoning.

## Documentation Authority
The **[Wiki](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki)** is the normative single source of truth for the v1.0 standard. All definitions, constraints, and vocabularies are rigorously defined there.

*   **[Record Overview](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/Record-Overview)**: The 8-block anatomy of a record.
*   **[Measurement](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/Measurement)**: The observable schema and data contract.
*   **[System](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/System)**: Infrastructure and configuration definitions.
*   **[Sample](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/Sample)**: Material identity and realization.
*   **[Links](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/Links)**: The knowledge graph ontology.
*   **[Descriptors](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/Descriptors)**: Scientific claims and features.
*   **[Assets](https://github.com/ISAAC-DOE/isaac-ai-ready-record/wiki/Assets)**: External immutable objects.

## Repository Structure
*   `schema/`: Strict JSON Schema definitions (`isaac_record_v1.json`) for validation.
*   `examples/`: **Golden Records** demonstrating 100% compliant usage across domains:
    *   `operando_xanes_co2rr_record.json`: Operando characterization.
    *   `simulation_xas_record.json`: Computational simulation.
    *   `ex_situ_xanes_cuo2_record.json`: Basic experimental characterization.
    *   `co2rr_performance_record.json`: Flow cell performance.
    *   `echem_performance_record.json`: RDE electrochemistry.
*   `wiki/`: Detailed normative documentation.

## Core Design Philosophy (v1.0)
1.  **Strict Separation of Concerns**: Sample identity != Measurement data != System config.
2.  **Machine-First Semantics**: Closed vocabularies for all structural types to enable reliable agent queries.
3.  **Refrence-Based**: Heavy data lives in immutable Assets; the Record is the metadata graph.
4.  **Shared Abstraction**: Experiment and Simulation share the same `measurement.series` structure for direct comparability.
