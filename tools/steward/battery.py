"""The repository steward's deterministic battery.

Measures every record in the ISAAC repository the same way each run, so that two runs (two days, or the
repository before and after a change) can be compared number by number. It reads the records through the
portal API, validates each with the same validator code the portal runs, and computes per-uploader metrics:

  outcome        publish / hold / reject, and every error and warning code with the records it fires on
  naming         descriptor values with a canonical vocabulary name, units in the unit vocabulary
  uncertainty    numeric, zero placeholder, stated as not reported, or absent
  identity       sample_id, producer group and organization, organization resolved to a registry id
  provenance     literature records with a source DOI, zero checksums, local file paths
  conditions     temperature basis, environment of performance records, calculations naming a functional
  links          dangling targets, one-way same_sample_as, same_sample_id bases the records do not support
  sample groups  groups joining records with different sample_ids from one issuer, groups joining a model
                 with a physical sample (the same grouping the portal serves)
  duplicates     the same value for the same sample, descriptor, unit, conditions and output, twice

Usage:
  python3 tools/steward/battery.py --env-file /path/.env --out-dir /private/dir
  python3 tools/steward/battery.py --snapshot records.jsonl --out-dir DIR       (no network)

Each run writes <out-dir>/<UTC timestamp>/{metrics.json, summary.md, records.jsonl}; the newest earlier run
in out-dir is the baseline for the diff. Records stay in out-dir: never commit a run.
"""
import argparse
import collections
import datetime as dt
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "portal"))
logging.disable(logging.CRITICAL)

import record_graph as rg  # noqa: E402
import validation  # noqa: E402

VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())
ULID = re.compile(r"^[0-9A-Z]{26}$")
LOCAL_PATH = re.compile(r"^(file:|/Users/|/home/|/mnt/|\./|[A-Za-z]:\\)")
AUTO_LINK = re.compile(r"auto-link", re.I)
ZERO_SHA = "0" * 64


# --- reading the repository -------------------------------------------------------------------------

def _env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _request(base, token, method, path, body=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read() or b"{}")


def fetch_held(base, token):
    """Held uploads (private drafts) as counts only: owner, domain and hold codes, never content. None when the
    caller may not list every owner's held records."""
    rows, offset = [], 0
    while True:
        try:
            page = _request(base, token, "GET", f"/records/held?owner=all&limit=500&offset={offset}")
        except urllib.error.HTTPError:
            return None
        batch = page.get("records") or []
        rows += batch
        offset += 500
        if len(batch) < 500:
            break
    by_owner = collections.Counter(r.get("owner") or "(none)" for r in rows)
    by_code = collections.Counter(c for r in rows for c in r.get("hold") or [])
    return {"total": len(rows), "by_owner": dict(by_owner), "by_code": dict(by_code)}


def fetch_snapshot(base, token):
    """Every record with its server metadata (owner, created_at, version, content_hash)."""
    rows, offset = [], 0
    while True:
        page = _request(base, token, "POST", "/records/query", {
            "sql": ("SELECT record_id, to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS') AS created_at, "
                    "version, content_hash, data->'attribution'->>'uploaded_by' AS owner FROM records "
                    f"ORDER BY record_id LIMIT 500 OFFSET {offset}"), "max_rows": 500})["rows"]
        rows += page
        offset += 500
        if len(page) < 500:
            break
    meta = {r["record_id"].strip(): r for r in rows}
    ids = sorted(meta)
    records = []
    for i in range(0, len(ids), 200):
        for r in _request(base, token, "POST", "/records/batch", {"record_ids": ids[i:i + 200]})["records"]:
            m = meta[r["record_id"]]
            records.append({"_meta": {"owner": m["owner"], "created_at": m["created_at"], "version": m["version"],
                                      "content_hash": m["content_hash"]}, **r})
    return records


# --- vocabulary -------------------------------------------------------------------------------------

# Descriptor class lists in the vocabulary (the prefix tokens, kinds, sources and product lists are not classes).
CLASS_SECTIONS = ("electrochemical_performance", "theoretical_metric", "spectroscopy", "theoretical", "component_families",
                  "structure", "magnitude_classes", "catalytic_performance")


def _canonical_names():
    sections = VOCAB.get("Descriptors") or {}
    names = set()
    for key in CLASS_SECTIONS:
        names |= set((sections.get("descriptors." + key) or {}).get("values") or [])
    names |= set(((sections.get("descriptors.class_units") or {}).get("map")) or {})
    return names


def _units():
    units = set()
    for sec in (VOCAB.get("Units") or {}).values():
        if isinstance(sec, dict):
            units |= set(sec.get("values") or [])
    return units


CANONICAL, UNITS = _canonical_names(), _units()


# --- measuring --------------------------------------------------------------------------------------

def _owner(r):
    return r["_meta"].get("owner") or "(none)"


def _body(r):
    return {k: v for k, v in r.items() if k != "_meta"}


def _descriptors(r):
    for out in (r.get("descriptors") or {}).get("outputs") or []:
        if isinstance(out, dict):
            for d in out.get("descriptors") or []:
                if isinstance(d, dict):
                    yield out, d


def _uncertainty_state(d):
    u = d.get("uncertainty")
    if not isinstance(u, dict) or not u:
        return "absent"
    numbers = [v for k, v in u.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ("n", "n_replicates")]
    if numbers:
        return "numeric" if any(v != 0 for v in numbers) else "zero_placeholder"
    return "stated_not_reported" if u.get("basis") else "absent"


def _sample_groups(records, keys):
    """The portal's grouping: same_sample_as links read from both ends, plus a shared scoped sample key."""
    by_id = {r["record_id"]: r for r in records}
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in records:
        find(r["record_id"])
        for link in r.get("links") or []:
            if isinstance(link, dict) and link.get("rel") == "same_sample_as" and link.get("target") in by_id:
                parent[find(r["record_id"])] = find(link["target"])
    shared = collections.defaultdict(list)
    for rid, k in keys.items():
        if k:
            shared[k].append(rid)
    for members in shared.values():
        for m in members[1:]:
            parent[find(members[0])] = find(m)
    groups = collections.defaultdict(set)
    for rid in by_id:
        groups[find(rid)].add(rid)

    def issuer(key):
        body = str(key or "")[len("sample:"):]
        scope, sep, _ = body.partition("/")
        return scope if sep and scope.startswith(("ror:", "org:", "group:")) else "global"

    out = collections.Counter()
    for g in groups.values():
        if len(g) < 2:
            continue
        out["groups"] += 1
        ids_by_issuer = collections.defaultdict(set)
        for rid in g:
            if keys.get(rid):
                ids_by_issuer[issuer(keys[rid])].add(keys[rid])
        if any(len(v) > 1 for v in ids_by_issuer.values()):
            out["conflicting_groups"] += 1
            out["records_in_conflicting_groups"] += len(g)
        envs = {((by_id[rid].get("context") or {}).get("environment")) for rid in g}
        if "in_silico" in envs and envs - {"in_silico", None}:
            out["groups_mixing_model_and_physical"] += 1
    return dict(out)


def measure(records):
    owners = collections.Counter(_owner(r) for r in records)
    per = {o: collections.Counter() for o in owners}
    errors = {o: collections.Counter() for o in owners}
    warnings = {o: collections.Counter() for o in owners}
    info = {o: collections.Counter() for o in owners}
    outcome_by_record = {}
    keys = {}
    ids = {r["record_id"] for r in records}
    sample_ids = {r["record_id"]: str((r.get("sample") or {}).get("sample_id") or "").strip() for r in records}
    links = collections.Counter()
    seen_values = {}
    for r in records:
        o, body, c = _owner(r), _body(r), per[_owner(r)]
        c["records"] += 1
        res = validation.validate_record_full(body)
        outcome = res.get("outcome") or ("publish" if res.get("valid") else "reject")
        outcome_by_record[r["record_id"]] = {"outcome": outcome, "warnings": sorted({w["code"] for w in res.get("warnings") or []}),
                                             "info": sorted({i["code"] for i in res.get("info") or [] if isinstance(i, dict)}),
                                             "errors": sorted({e.get("code") or k for k in ("schema_errors", "vocabulary_errors", "semantic_errors")
                                                               for e in res.get(k) or []})}
        c["outcome_" + outcome] += 1
        errors[o].update(outcome_by_record[r["record_id"]]["errors"])
        warnings[o].update(outcome_by_record[r["record_id"]]["warnings"])
        info[o].update(outcome_by_record[r["record_id"]]["info"])
        k = rg.derive_keys(body, VOCAB)
        keys[r["record_id"]] = k.get("sample_id")
        # naming and uncertainty
        for out, d in _descriptors(r):
            c["values"] += 1
            name = str(d.get("name") or "")
            c["values_canonical_name"] += name.split(".")[0] in CANONICAL
            c["values_unit_in_vocabulary"] += d.get("unit") in UNITS
            c["uncertainty_" + _uncertainty_state(d)] += 1
            dkey = (o, sample_ids[r["record_id"]] or json.dumps((r.get("sample") or {}).get("material"), sort_keys=True),
                    name, d.get("unit"), json.dumps(d.get("value")), json.dumps(d.get("at"), sort_keys=True),
                    json.dumps(r.get("context"), sort_keys=True), json.dumps(out.get("label")))
            if dkey in seen_values and seen_values[dkey] != r["record_id"]:
                c["duplicate_values"] += 1
            seen_values.setdefault(dkey, r["record_id"])
        # identity and provenance
        pb = (r.get("attribution") or {}).get("produced_by") or {}
        c["with_sample_id"] += bool(sample_ids[r["record_id"]])
        c["with_producer_group"] += bool(str(pb.get("group") or "").strip())
        c["with_organization"] += bool(str(pb.get("organization") or "").strip())
        c["organization_resolved"] += bool(k.get("organization") and str(k["organization"]).startswith("ror:"))
        assets = r.get("assets") or []
        c["zero_checksum_records"] += any(isinstance(a, dict) and a.get("sha256") == ZERO_SHA for a in assets)
        c["local_path_records"] += any(isinstance(a, dict) and LOCAL_PATH.match(str(a.get("uri") or "")) for a in assets)
        if r.get("source_type") == "literature":
            c["literature"] += 1
            c["literature_with_source_doi"] += bool(k.get("study") and any(s.startswith("doi:") for s in k["study"]))
        # conditions and methods
        ctx = r.get("context") or {}
        c["temperature_basis_" + str(ctx.get("temperature_basis") or "absent")] += 1
        if r.get("record_domain") == "performance":
            c["performance"] += 1
            c["performance_env_" + str(ctx.get("environment") or "absent")] += 1
        method = (r.get("computation") or {}).get("method") or {}
        if method:
            c["calculations"] += 1
            named = str(method.get("functional_name") or "").strip().lower() not in ("", "not_reported", "unknown", "none")
            c["calculations_naming_functional"] += named
        # links
        for link in r.get("links") or []:
            if not isinstance(link, dict):
                continue
            links["declared"] += 1
            target = str(link.get("target") or "")
            if ULID.match(target) and target not in ids:
                links["dangling"] += 1
                c["dangling_links"] += 1
            if link.get("rel") == "same_sample_as":
                links["same_sample_as"] += 1
                if link.get("basis") == "same_sample_id" and not (sample_ids[r["record_id"]] and sample_ids[r["record_id"]] == sample_ids.get(target)):
                    links["same_sample_id_basis_unsupported"] += 1
                    c["unsupported_same_sample_id_links"] += 1
                if AUTO_LINK.search(str(link.get("notes") or link.get("note") or "")):
                    links["auto_linker"] += 1
    pairs = {(r["record_id"], link.get("target")) for r in records for link in r.get("links") or []
             if isinstance(link, dict) and link.get("rel") == "same_sample_as"}
    links["same_sample_as_one_way"] = sum(1 for a, b in pairs if (b, a) not in pairs)
    return {
        "records": len(records),
        "per_uploader": {o: dict(per[o]) for o in sorted(owners, key=lambda x: -owners[x])},
        "errors_by_uploader": {o: dict(errors[o].most_common()) for o in owners if errors[o]},
        "warnings_by_uploader": {o: dict(warnings[o].most_common()) for o in owners if warnings[o]},
        "info_by_uploader": {o: dict(info[o].most_common()) for o in owners if info[o]},
        "links": dict(links),
        "sample_groups": _sample_groups(records, keys),
        "_outcomes": outcome_by_record,
    }


# --- comparing and reporting ------------------------------------------------------------------------

def diff(previous_records, records, previous_metrics, metrics):
    before = {r["record_id"]: r["_meta"] for r in previous_records}
    now = {r["record_id"]: r["_meta"] for r in records}
    new = sorted(set(now) - set(before))
    deleted = sorted(set(before) - set(now))
    edited = sorted(rid for rid in set(now) & set(before)
                    if (now[rid].get("version"), now[rid].get("content_hash")) != (before[rid].get("version"), before[rid].get("content_hash")))
    changed_outcome = sorted(rid for rid in set(now) & set(before)
                             if previous_metrics["_outcomes"].get(rid, {}).get("outcome") != metrics["_outcomes"].get(rid, {}).get("outcome"))
    new_by_uploader = collections.Counter(_owner({"_meta": now[rid]}) for rid in new)
    # Records present in both runs whose codes changed, by code: what a flip list declares. Info codes are
    # compared only when both runs counted them (runs before 2026-10-06 did not).
    tiers = ("errors", "warnings") + (("info",) if "info_by_uploader" in previous_metrics and "info_by_uploader" in metrics else ())
    codes_changed, code_examples = {}, {}
    for rid in sorted(set(now) & set(before)):
        a, b = previous_metrics["_outcomes"].get(rid, {}), metrics["_outcomes"].get(rid, {})
        for tier in tiers:
            for code, way in [(c, "lost") for c in set(a.get(tier) or []) - set(b.get(tier) or [])] + \
                             [(c, "gained") for c in set(b.get(tier) or []) - set(a.get(tier) or [])]:
                row = codes_changed.setdefault(code, {"tier": tier, "gained": 0, "lost": 0})
                row[way] += 1
                if len(code_examples.setdefault(code, [])) < 5:
                    code_examples[code].append(rid)
    return {"new": len(new), "new_by_uploader": dict(new_by_uploader), "deleted": len(deleted), "edited": len(edited),
            "outcome_changed": len(changed_outcome), "codes_changed": codes_changed,
            "examples": {"new": new[:5], "deleted": deleted[:5], "edited": edited[:5],
                         "outcome_changed": changed_outcome[:5], "codes_changed": code_examples}}


def _share(n, d):
    return f"{100 * n / d:.0f}%" if d else "n/a"


def summary_markdown(metrics, change, stamp):
    lines = [f"# Repository steward battery, {stamp}", "",
             f"{metrics['records']} records. Links: {metrics['links']}. Sample groups: {metrics['sample_groups']}.", ""]
    if change:
        lines += [f"Since the previous run: {change['new']} new {change['new_by_uploader']}, {change['edited']} edited, "
                  f"{change['deleted']} deleted, {change['outcome_changed']} changed outcome.", ""]
        if change.get("codes_changed"):
            lines += ["Codes that changed on records present in both runs: " + json.dumps(change["codes_changed"]), ""]
    lines += ["| uploader | records | publish | hold | reject | canonical names | units in vocab | sample_id | org resolved | numeric uncertainty | zero-sigma | unsupported same_sample_id links |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for o, c in metrics["per_uploader"].items():
        v = c.get("values", 0)
        lines.append(f"| {o} | {c['records']} | {c.get('outcome_publish', 0)} | {c.get('outcome_hold', 0)} | {c.get('outcome_reject', 0)} | "
                     f"{_share(c.get('values_canonical_name', 0), v)} | {_share(c.get('values_unit_in_vocabulary', 0), v)} | "
                     f"{_share(c.get('with_sample_id', 0), c['records'])} | {_share(c.get('organization_resolved', 0), c['records'])} | "
                     f"{_share(c.get('uncertainty_numeric', 0), v)} | {c.get('uncertainty_zero_placeholder', 0)} | {c.get('unsupported_same_sample_id_links', 0)} |")
    lines += ["", "Errors by uploader: " + json.dumps(metrics["errors_by_uploader"]),
              "", "Warnings by uploader: " + json.dumps(metrics["warnings_by_uploader"]),
              "", "Info by uploader: " + json.dumps(metrics.get("info_by_uploader") or {})]
    return "\n".join(lines) + "\n"


def run_time(run):
    """When a run was made, from its folder name (by second since 2026-10-06, by minute before)."""
    for fmt in ("%Y-%m-%dT%H%M%SZ", "%Y-%m-%dT%H%MZ"):
        try:
            return dt.datetime.strptime(Path(run).name, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"not a battery run: {Path(run).name}")


def runs_in(out_dir):
    """Completed runs in out_dir, oldest first."""
    return sorted((p for p in Path(out_dir).glob("*Z") if p.is_dir() and (p / "metrics.json").exists()), key=run_time)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--env-file", type=Path, help="file with ISAAC_API_URL and ISAAC_API_TOKEN")
    ap.add_argument("--snapshot", type=Path, help="read records from this JSONL instead of the API")
    args = ap.parse_args(argv)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")  # seconds: two runs in one minute must not collide
    held = None
    if args.snapshot:
        records = [json.loads(line) for line in args.snapshot.open()]
        newest = max((r["_meta"].get("created_at") or "" for r in records), default="")
        run_info = {"kind": "replay", "made_at": stamp, "snapshot": str(args.snapshot),
                    "data_as_of": f"newest record created {newest}" if newest else "unknown"}
    else:
        env = _env(args.env_file)
        base = env.get("ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api").rstrip("/")
        records = fetch_snapshot(base, env["ISAAC_API_TOKEN"])
        held = fetch_held(base, env["ISAAC_API_TOKEN"])
        run_info = {"kind": "live", "made_at": stamp, "data_as_of": stamp}
    runs = [r for r in runs_in(args.out_dir) if (r / "records.jsonl").exists()] if args.out_dir.exists() else []
    run = args.out_dir / stamp
    run.mkdir(parents=True, exist_ok=False)
    with (run / "records.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    metrics = measure(records)
    metrics["run"] = run_info
    metrics["held"] = held if held is not None else ("not measured: replay" if args.snapshot else "not available to this token")
    change = None
    if runs:
        prev = runs[-1]
        previous_records = [json.loads(line) for line in (prev / "records.jsonl").open()]
        change = diff(previous_records, records, json.loads((prev / "metrics.json").read_text()), metrics)
        metrics["diff"] = change
    (run / "metrics.json").write_text(json.dumps(metrics, indent=1))
    (run / "summary.md").write_text(summary_markdown(metrics, change, stamp))
    print(f"{run}: {metrics['records']} records; diff {json.dumps(change) if change else 'first run'}")


if __name__ == "__main__":
    main()
