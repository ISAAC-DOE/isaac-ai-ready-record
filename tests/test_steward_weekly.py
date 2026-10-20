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


def _rec(i, owner, cfg=None, name="lattice_parameter.a", day="2026-09-01"):
    return {"record_id": f"01KWEEKLY{i:017d}", "_meta": {"owner": owner, "created_at": day + "T00:00:00"},
            "system": {"configuration": cfg or {}},
            "descriptors": {"outputs": [{"descriptors": [{"name": name, "value": 1.0, "unit": "angstrom"}]}]}}


def test_a_key_needs_three_uploaders_and_thirty_records():
    recs = {}
    for i in range(30):
        r = _rec(i, ["a", "b", "c"][i % 3], {"counter_electrode": "Pt"}, day="2026-09-01" if i < 15 else "2026-10-05")
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


def test_our_unowned_records_are_not_an_independent_uploader_and_short_spans_fail():
    recs = {}
    for i in range(36):
        r = _rec(i, ["dsokaras", None, "c"][i % 3], {"edge": "Cu_K"}, day="2026-09-01" if i < 18 else "2026-10-05")
        r["_meta"]["owner"] = ["dsokaras", None, "c"][i % 3]
        recs[r["record_id"]] = r
    for i in range(36, 72):
        r = _rec(i, ["a", "b", "c"][i % 3], {"short_lived": "x"}, day="2026-10-01")
        recs[r["record_id"]] = r
    meets = {x["name"] for x in weekly.schema_candidates(recs)["configuration_keys"]["meets_promotion_rule"]}
    assert "system.configuration.edge" not in meets          # dsokaras and its unowned uploads are one uploader
    assert "system.configuration.short_lived" not in meets   # three uploaders, but all on one day


def _run(out_dir, name, owner="a"):
    run = out_dir / name
    run.mkdir(parents=True)
    rec = _rec(1, owner)
    (run / "records.jsonl").write_text(json.dumps(rec) + "\n")
    (run / "metrics.json").write_text(json.dumps({"_outcomes": {rec["record_id"]: {"outcome": "publish"}}}))
    return run


def test_a_review_is_written_under_its_own_date_and_a_stopped_battery_is_stale(tmp_path):
    import datetime as dt
    _run(tmp_path, "2026-10-03T131700Z")
    _run(tmp_path, "2026-10-10T131700Z")
    earlier = tmp_path / "weekly" / "2026-10-10"
    earlier.mkdir(parents=True)
    (earlier / "report.md").write_text("the review of 10-12")

    fresh = weekly.main(["--out-dir", str(tmp_path)], now=dt.datetime(2026, 10, 11, 7, 13, tzinfo=dt.timezone.utc))
    assert fresh == 0
    assert not (tmp_path / "weekly" / "2026-10-11" / "digest.md").read_text().startswith("**STALE")

    stale = weekly.main(["--out-dir", str(tmp_path)], now=dt.datetime(2026, 10, 19, 7, 13, tzinfo=dt.timezone.utc))
    assert stale == weekly.STALE_EXIT
    digest = (tmp_path / "weekly" / "2026-10-19" / "digest.md").read_text()
    assert digest.startswith("**STALE: the newest battery run, 2026-10-10T131700Z, is 8.7 days old")
    assert json.loads((tmp_path / "weekly" / "2026-10-19" / "digest.json").read_text())["newest_run_age_hours"] == 209.9
    assert (earlier / "report.md").read_text() == "the review of 10-12"   # an earlier review is never overwritten
