"""The record form offers a dropdown only for a text field of the record.

On 2026-09-30 the form crashed on every load ("can only concatenate list (not 'dict') to list",
reported with a first fix by changzhiai in #246): system.organizations became a registry of
names and ROR ids (a dict) on 2026-08-09, and the form turned every vocabulary category into a
dropdown. Most such categories are data the validator reads (placeholders, class lists, units),
and the form writes a dropdown's value into the record at the category's path, so each of those
dropdowns wrote a field the schema rejects.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import form_fields  # noqa: E402

VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())


def test_every_offered_dropdown_is_a_text_field_with_text_options():
    for section in VOCAB:
        for key, opts, _ in form_fields.extra_categories(section, [], VOCAB):
            assert form_fields.is_text_field(key), key
            assert opts and all(isinstance(o, str) for o in opts), key


def test_the_organization_registry_does_not_crash_the_form_and_is_not_a_dropdown():
    handled = ["system.domain", "system.technique", "system.instrument.instrument_type"]
    offered = [k for k, _, _ in form_fields.extra_categories("System", handled, VOCAB)]
    assert "system.organizations" not in offered
    names = form_fields.options(VOCAB["System"]["system.organizations"]["values"])
    assert "SLAC National Accelerator Laboratory" in names


def test_data_the_validator_reads_is_never_offered():
    offered = {k for s in VOCAB for k, _, _ in form_fields.extra_categories(s, [], VOCAB)}
    for key in ("system.organization_placeholders", "measurement.processing.publication_extraction_steps",
                "descriptors.catalytic_performance", "descriptors.faradaic_efficiency_products", "units.general"):
        assert key not in offered, key


def test_paths_through_arrays_or_into_objects_are_not_text_fields():
    assert form_fields.is_text_field("context.environment")
    assert form_fields.is_text_field("context.electrochemistry.potential_vs_RHE.rhe_basis")
    for key in ("links.basis", "assets.citation.relation", "descriptors.outputs.descriptors.source",
                "sample.geometry", "system.configuration", "measurement.processing.steps", "system.organizations"):
        assert not form_fields.is_text_field(key), key


def test_a_registry_on_a_text_field_offers_its_keys():
    vocab = {"Context": {"context.environment": {"values": {"ex_situ": {"note": "a"}, "operando": {"note": "b"}}}}}
    assert form_fields.extra_categories("Context", [], vocab) == [("context.environment", ["ex_situ", "operando"], "")]
