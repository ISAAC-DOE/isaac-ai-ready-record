"""The weekly digest (tools/steward/weekly.py) applies the schema promotion rule of governance/README.md:
a key or name is a candidate for structure only when at least 3 independent uploaders use it in at least
30 records."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools" / "steward"))

import weekly  # noqa: E402


def _rec(i, owner, cfg=None, name="lattice_parameter.a"):
    return {"record_id": f"01KWEEKLY{i:017d}", "_meta": {"owner": owner},
            "system": {"configuration": cfg or {}},
            "descriptors": {"outputs": [{"descriptors": [{"name": name, "value": 1.0, "unit": "angstrom"}]}]}}


def test_a_key_needs_three_uploaders_and_thirty_records():
    recs = {}
    for i in range(30):
        r = _rec(i, ["a", "b", "c"][i % 3], {"counter_electrode": "Pt"})
        recs[r["record_id"]] = r
    for i in range(30, 70):
        r = _rec(i, "a", {"only_one_lab": "x"}, name="made_up_class.q")
        recs[r["record_id"]] = r
    c = weekly.schema_candidates(recs)
    meets = {x["name"] for x in c["configuration_keys"]["meets_promotion_rule"]}
    assert "system.configuration.counter_electrode" in meets
    assert "system.configuration.only_one_lab" not in meets
    assert c["descriptor_classes_outside_vocabulary"]["meets_promotion_rule"] == []
    assert any(x["name"] == "made_up_class" for x in c["descriptor_classes_outside_vocabulary"]["top"])
