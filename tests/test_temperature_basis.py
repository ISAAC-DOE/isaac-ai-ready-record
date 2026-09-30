"""A temperature the source does not give has an honest form: null with temperature_basis 'not_reported'.

On 2026-09-30 agents following the record contract (never a default) could not store a room-
temperature HER result or an ex situ XPS spectrum whose paper stated no temperature: the schema
required a number. The September pipeline had resolved the same conflict the other way, writing
298.15 K as a default 56 times.
"""
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import validation  # noqa: E402

BASE = json.loads((REPO / "examples" / "ex_situ_xanes_cuo2_record.json").read_text())


def _with(temperature, basis=None):
    r = copy.deepcopy(BASE)
    r["context"]["temperature_K"] = temperature
    if basis is not None:
        r["context"]["temperature_basis"] = basis
    else:
        r["context"].pop("temperature_basis", None)
    return validation.validate_record_full(r)


def test_an_unstated_temperature_is_null_with_its_basis():
    assert _with(None, "not_reported")["valid"]


def test_a_null_temperature_needs_the_basis():
    assert not _with(None)["valid"]
    assert not _with(None, "stated")["valid"]


def test_not_reported_means_no_number():
    assert not _with(298.15, "not_reported")["valid"]


def test_room_temperature_and_stated_temperatures_are_numbers():
    assert _with(298.15, "room_temperature")["valid"]
    assert _with(673.15, "stated")["valid"]
    assert _with(298.15)["valid"], "a number without a basis stays valid, as in every stored record"
