"""The repository steward's weekly digest: what changed over a week, and what the schema could learn from it.

Reads two runs of the daily battery (tools/steward/battery.py): the newest, and the newest one at least six
days older (else the oldest kept). Writes <out-dir>/weekly/<date>/digest.json and digest.md with:

  activity     records added, edited and deleted per uploader, and records whose outcome changed
  codes        every error and warning code: records it fires on then and now, by uploader
  links        link and sample-group integrity then and now
  schema       candidates for structure, against the promotion rule in governance/README.md
               (at least 3 independent uploaders and 30 records): open-namespace keys under
               system.configuration and context, descriptor classes outside the vocabulary, units outside it

Deterministic and read-only. The weekly agent review reads this digest; it never needs the API token.

Usage:  python3 tools/steward/weekly.py --out-dir /private/dir/steward
"""
import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import battery  # noqa: E402

PROMOTE_UPLOADERS, PROMOTE_RECORDS = 3, 30


def _runs(out_dir):
    return sorted(p for p in Path(out_dir).glob("*Z") if (p / "metrics.json").exists())


def _when(run):
    return dt.datetime.strptime(run.name, "%Y-%m-%dT%H%MZ").replace(tzinfo=dt.timezone.utc)


def pick_runs(out_dir, days=6):
    runs = _runs(out_dir)
    if len(runs) < 2:
        raise SystemExit("need at least two battery runs")
    now = runs[-1]
    older = [r for r in runs[:-1] if (_when(now) - _when(r)).total_seconds() >= days * 86400 and (r / "records.jsonl").exists()]
    then = older[-1] if older else next(r for r in runs if (r / "records.jsonl").exists())
    return then, now


def _load(run):
    return {json.loads(line)["record_id"]: json.loads(line) for line in (run / "records.jsonl").open()}


def _codes(metrics, kind):
    out = collections.Counter()
    for counts in (metrics.get(f"{kind}_by_uploader") or {}).values():
        out.update(counts)
    return out


def _flatten_keys(obj, prefix):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{prefix}.{k}"
            yield from _flatten_keys(v, f"{prefix}.{k}") if isinstance(v, dict) else ()


def schema_candidates(records):
    """Keys and names that recur across uploaders without a structured home."""
    keys = collections.defaultdict(lambda: [0, set()])
    classes = collections.defaultdict(lambda: [0, set()])
    units = collections.defaultdict(lambda: [0, set()])
    for r in records.values():
        owner = battery._owner(r)
        cfg = (r.get("system") or {}).get("configuration")
        for k in set(_flatten_keys(cfg, "system.configuration")) if isinstance(cfg, dict) else ():
            keys[k][0] += 1; keys[k][1].add(owner)
        for _, d in battery._descriptors(r):
            cls = str(d.get("name") or "").split(".")[0]
            if cls and cls not in battery.CANONICAL:
                classes[cls][0] += 1; classes[cls][1].add(owner)
            if d.get("unit") and d.get("unit") not in battery.UNITS:
                units[d["unit"]][0] += 1; units[d["unit"]][1].add(owner)

    def rank(table):
        rows = [{"name": k, "records": v[0], "uploaders": len(v[1])} for k, v in table.items()]
        rows.sort(key=lambda x: (-x["uploaders"], -x["records"], x["name"]))
        return {"meets_promotion_rule": [x for x in rows if x["uploaders"] >= PROMOTE_UPLOADERS and x["records"] >= PROMOTE_RECORDS],
                "top": rows[:25], "distinct": len(rows)}

    return {"configuration_keys": rank(keys), "descriptor_classes_outside_vocabulary": rank(classes),
            "units_outside_vocabulary": rank(units)}


def digest(then_run, now_run):
    a, b = _load(then_run), _load(now_run)
    ma, mb = json.loads((then_run / "metrics.json").read_text()), json.loads((now_run / "metrics.json").read_text())
    change = battery.diff(list(a.values()), list(b.values()), ma, mb)
    added = collections.Counter(battery._owner(b[r]) for r in set(b) - set(a))
    edited = collections.Counter(battery._owner(b[r]) for r in set(a) & set(b)
                                 if (a[r]["_meta"].get("version"), a[r]["_meta"].get("content_hash"))
                                 != (b[r]["_meta"].get("version"), b[r]["_meta"].get("content_hash")))
    deleted = collections.Counter(battery._owner(a[r]) for r in set(a) - set(b))
    codes = {}
    for kind in ("errors", "warnings"):
        ca, cb = _codes(ma, kind), _codes(mb, kind)
        codes[kind] = sorted(({"code": c, "then": ca.get(c, 0), "now": cb.get(c, 0)} for c in set(ca) | set(cb)),
                             key=lambda x: -abs(x["now"] - x["then"]))
    return {"from": then_run.name, "to": now_run.name, "records": {"then": len(a), "now": len(b)},
            "activity": {"added": dict(added), "edited": dict(edited), "deleted": dict(deleted),
                         "outcome_changed": change["outcome_changed"]},
            "codes": codes, "links": {"then": ma.get("links"), "now": mb.get("links")},
            "sample_groups": {"then": ma.get("sample_groups"), "now": mb.get("sample_groups")},
            "schema": schema_candidates(b)}


def markdown(d):
    lines = [f"# Weekly steward digest, {d['from']} to {d['to']}", "",
             f"Records: {d['records']['then']} to {d['records']['now']}. Added {d['activity']['added']}, "
             f"edited {d['activity']['edited']}, deleted {d['activity']['deleted']}; "
             f"{d['activity']['outcome_changed']} changed outcome.", "", "## Codes that moved most", "",
             "| code | kind | then | now |", "|---|---|---|---|"]
    for kind in ("errors", "warnings"):
        for row in d["codes"][kind][:12]:
            if row["then"] != row["now"]:
                lines.append(f"| {row['code']} | {kind[:-1]} | {row['then']} | {row['now']} |")
    lines += ["", f"Links then: {d['links']['then']}", f"Links now: {d['links']['now']}",
              f"Sample groups then: {d['sample_groups']['then']}", f"Sample groups now: {d['sample_groups']['now']}", "",
              "## Schema candidates (promotion rule: at least 3 uploaders and 30 records)", ""]
    for name, table in d["schema"].items():
        lines.append(f"**{name}**: {table['distinct']} distinct; meeting the rule: "
                     + (", ".join(f"`{x['name']}` ({x['records']} records, {x['uploaders']} uploaders)" for x in table["meets_promotion_rule"]) or "none"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    then_run, now_run = pick_runs(args.out_dir)
    d = digest(then_run, now_run)
    week = args.out_dir / "weekly" / now_run.name[:10]
    week.mkdir(parents=True, exist_ok=True)
    (week / "digest.json").write_text(json.dumps(d, indent=1))
    (week / "digest.md").write_text(markdown(d))
    print(f"{week}: {d['from']} to {d['to']}; activity {d['activity']}")


if __name__ == "__main__":
    main()
