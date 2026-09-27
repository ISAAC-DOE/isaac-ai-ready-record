"""
The schema served at GET /portal/api/schema must accept what the validator accepts.

Its stated purpose is that a client (or an agent drafting a record) can fetch it and
validate locally. Until 2026-09-27 it carried vocabulary enums on OBJECT nodes
(sample.composition, sample.geometry, system.configuration) and on the processing-steps
ARRAY node, so it rejected 1,849 of the 2,212 records the validator accepted, and an
agent checking its draft against it was told that valid records were invalid.
"""

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import ontology  # noqa: E402
import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((REPO / "examples").glob("*.json"))


def _served():
    return ontology.merge_vocabulary_into_schema(validation.ISAAC_SCHEMA)


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_every_canonical_example_validates_against_the_served_schema(path):
    record = json.loads(path.read_text())
    errors = list(Draft202012Validator(_served()).iter_errors(record))
    assert not errors, f"{path.name}: {[e.message[:120] for e in errors[:3]]}"


def test_no_enum_sits_on_an_object_or_array_node():
    bad = []

    def walk(node, path="$"):
        if isinstance(node, dict):
            if "enum" in node and (node.get("type") in ("object", "array") or "properties" in node):
                bad.append(path)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(_served())
    assert not bad, f"enum injected onto non-string nodes: {bad}"


def test_string_vocabularies_are_still_enforced_by_the_served_schema():
    record = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    record = copy.deepcopy(record)
    record["system"]["technique"] = "not_a_technique"
    errors = list(Draft202012Validator(_served()).iter_errors(record))
    assert any("not_a_technique" in e.message for e in errors)


def test_processing_steps_are_checked_item_by_item():
    served = _served()
    steps = served["properties"]["measurement"]["properties"]["processing"]["properties"]["steps"]
    assert steps.get("type") == "array"
    assert "enum" not in steps
    assert steps["items"].get("enum"), "processing-step vocabulary no longer reaches the items"
