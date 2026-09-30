"""Policy 66: a specimen is its whole sample group, found from both ends.

The first record of a physical sample has nothing to link to; only a later record can declare
same_sample_as, pointing back. In the repository on 2026-09-30, of 3,989 record pairs joined by
same_sample_as, 121 were declared by the newer record only (up to 56 days after the older one),
57 by the older only (batch uploads with pre-generated ids) and 3,811 by both (uploaded
together). Policy 65 read a shared specimen from the cited record's own links as an unordered
pair, so it bound only when both records declared the link: of the 6,852 record pairs on one
sample it saw 3,811, and the rest, one-way pairs and records joined through a third record,
counted as independent evidence. Policy 66 reads the group from the repository's two-way index.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import discovery  # noqa: E402
import trace_provenance as tp  # noqa: E402

A, B, C, D = ("01" + ch * 24 for ch in "ABCD")   # ULIDs sort by creation time: A is the first


def _rec(rid, same=(), replica=(), sample_id=None):
    links = [{"rel": "same_sample_as", "target": t, "basis": "same_sample_id"} for t in same]
    links += [{"rel": "replica_of", "target": t, "basis": "replicate_preparation"} for t in replica]
    rec = {"record_id": rid, "links": links, "sample": {}}
    if sample_id:
        rec["sample"]["sample_id"] = sample_id
    return rec


def _groups_from(records):
    """What database.sample_groups computes, over these records: sample groups through
    same_sample_as links read from both ends and through other records, plus a shared
    sample_id; a group stating two different sample_ids is unresolved; replica partners are one
    replica_of link away, from either side."""
    def sample_group(start):
        edges = {}
        for r in records.values():
            for link in r.get("links") or []:
                if link["rel"] == "same_sample_as":
                    edges.setdefault(r["record_id"], set()).add(link["target"])
                    edges.setdefault(link["target"], set()).add(r["record_id"])
            sid = (r.get("sample") or {}).get("sample_id")
            for other in records.values():
                if sid and other is not r and (other.get("sample") or {}).get("sample_id") == sid:
                    edges.setdefault(r["record_id"], set()).add(other["record_id"])
        seen, todo = {start}, [start]
        while todo:
            for nb in edges.get(todo.pop(), ()):
                if nb not in seen:
                    seen.add(nb); todo.append(nb)
        return seen

    def sample_groups(ids):
        out = {}
        for rid in ids:
            group = sample_group(rid)
            stated = {(records.get(m, {}).get("sample") or {}).get("sample_id") for m in group} - {None}
            by_issuer = {}
            for key in stated:
                by_issuer.setdefault(discovery.database._sample_namespace("sample:" + key), set()).add(key)
            conflict = any(len(v) > 1 for v in by_issuer.values())
            partners = sorted({l["target"] for l in records.get(rid, {}).get("links") or [] if l["rel"] == "replica_of"}
                              | {r["record_id"] for r in records.values()
                                 for l in r.get("links") or [] if l["rel"] == "replica_of" and l["target"] == rid})
            out[rid] = {"sample": None if conflict else (min(group) if len(group) > 1 else None),
                        "replicas": partners,
                        "unresolved": "conflicting_sample_ids" if conflict else None}
        return out
    return sample_groups


@pytest.fixture
def repo(monkeypatch):
    """A repository of the given records, as the records store and its index answer."""
    def load(*recs):
        records = {r["record_id"]: r for r in recs}
        monkeypatch.setattr(discovery.database, "get_records_batch",
                            lambda ids: [records[i] for i in ids if i in records])
        monkeypatch.setattr(discovery.database, "sample_groups", _groups_from(records))
        monkeypatch.setattr(discovery.database, "record_version_hash",
                            lambda rid: {"version": 1, "content_hash": "v2:" + rid} if rid in records else None)
        return records
    return load


def _pins(*rids, groups=True):
    return discovery._pin_evidence(list(rids), with_groups=groups)


def _tier1(rid, pins=None):
    """A cited record's same-specimen keys: from the groups frozen in the pins (policy 66), or,
    with no pins, from its own declared links (policy 65)."""
    groups = discovery._pinned_group_keys({"evidence_pins": pins}) if pins is not None else None
    return discovery._cause_signatures([rid], groups)[0]


def test_a_link_declared_by_the_later_record_binds_both(repo):
    repo(_rec(A), _rec(B, same=[A]))        # A came first and could not name B
    pins = _pins(A, B)
    assert _tier1(A, pins) & _tier1(B, pins) == {f"sample-group:{A}"}
    assert not (_tier1(A) & _tier1(B)), "policy 65 read the declaring side only"


def test_two_later_records_pointing_back_to_the_first_are_one_specimen(repo):
    repo(_rec(A), _rec(B, same=[A]), _rec(C, same=[A]))
    pins = _pins(B, C)                       # A itself is not cited
    assert _tier1(B, pins) & _tier1(C, pins)
    assert not (_tier1(B) & _tier1(C))


def test_a_shared_sample_id_joins_records_that_never_link(repo):
    repo(_rec(A, sample_id="lab-2026-017"), _rec(B, sample_id="lab-2026-017"), _rec(C))
    pins = _pins(A, B, C)
    assert _tier1(A, pins) & _tier1(B, pins)
    assert not _tier1(C, pins), "a record alone in its group carries no same-specimen key"


def test_replication_is_pairwise_and_read_from_both_ends(repo):
    """A replicate is a separate draw of specimen error: it is tied to the record it repeats,
    and two replicates of one record stay independent of each other."""
    repo(_rec(A), _rec(B, same=[A]), _rec(C, replica=[B]), _rec(D, replica=[B]))
    pins = _pins(B, C, D)
    assert _tier1(B, pins) & _tier1(C, pins) == {f"replica:{B}|{C}"}, "declared by C, binds from B too"
    assert not (_tier1(C, pins) & _tier1(D, pins)), "siblings are not joined"
    assert not (_tier1(A, _pins(A, C)) & _tier1(C, _pins(A, C))), "a replica does not join the sample group"


def test_local_names_from_two_labs_are_one_object_when_linked(repo):
    """A facility measuring a lab's object may name it locally; linked, the two names stand for
    one object. Two ids from ONE issuer in one group would contradict each other instead."""
    repo(_rec(A, sample_id="ror:05gzmn429/s1"), _rec(B, same=[A], sample_id="ror:02jbv0t02/beam-017"))
    pins = _pins(A, B)
    assert _tier1(A, pins) & _tier1(B, pins) == {f"sample-group:{A}"}


def test_the_issuer_of_a_sample_key():
    ns = discovery.database._sample_namespace
    assert ns("sample:ror:05gzmn429/s1") == "ror:05gzmn429"
    assert ns("sample:group:some group/s1") == "group:some group"
    assert ns("sample:6f1c2a3e-8b1d-4c5e-9f00-1a2b3c4d5e6f") == "global"
    assert ns("sample:https://igsn.org/10.58052/iexyz0001") == "global"


def test_a_group_stating_two_different_sample_ids_is_unresolved(repo):
    repo(_rec(A, sample_id="lab-2026-017"), _rec(B, same=[A], sample_id="lab-2026-018"))
    pins = _pins(A, B)
    assert all(pin["groups_unresolved"] == "conflicting_sample_ids" and "sample_group" not in pin for pin in pins)
    assert discovery._ungrouped_evidence({"evidence_record_ids": [A, B], "evidence_pins": pins}) == [A, B]


def test_the_pins_freeze_the_groups_the_verdict_was_made_on(repo):
    repo(_rec(A), _rec(B, same=[A]), _rec(C, replica=[A]))
    pins = _pins(A, B)
    assert [(p["record_id"], p["sample_group"], p["replica_partners"]) for p in pins] == [
        (A, A, [C]), (B, A, [])]
    assert _pins(A, B, groups=False)[0].keys() == {"record_id", "version", "content_hash"}


def test_an_unreachable_index_is_recorded_on_the_pin(repo, monkeypatch):
    repo(_rec(A, same=[B]), _rec(B, same=[A]))
    def down(ids):
        raise RuntimeError("index unavailable")
    monkeypatch.setattr(discovery.database, "sample_groups", down)
    pins = _pins(A, B)
    assert pins and all(p["groups_unresolved"] == "index_unavailable" and "sample_group" not in p for p in pins)
    assert discovery._pinned_group_keys({"evidence_pins": pins}) == {}


# --- the score -------------------------------------------------------------------------

def _pred(pid, record_id, policy):
    return {"prediction_id": pid, "verdict": "supports", "strength": "strong", "work_status": "evaluated",
            "evidence_record_ids": [record_id], "falsification_criterion": "falls below baseline",
            "direction": "up", "reference_condition": "vs baseline", "rationale": "measured",
            "evidence_pins": _pins(record_id, groups=policy >= tp.POLICY_SAMPLE_GROUPS)}


def _hyp(policy, *record_ids):
    return {"label": "H1", "policy_version": policy,
            "predictions": [_pred(f"p{i}", rid, policy) for i, rid in enumerate(record_ids)]}


def test_policy_66_counts_one_specimen_once_where_65_counted_it_twice(repo):
    repo(_rec(A), _rec(B, same=[A]), _rec(C, same=[A]))
    assert discovery.compute_hypothesis_score(_hyp(65, B, C))["n_decisive"] == 2
    s = discovery.compute_hypothesis_score(_hyp(66, B, C))
    assert s["n_decisive"] == 1 and not s["reliable"]
    attenuated = [d for d in s["predictions"].values() if not d["counted"]]
    assert attenuated and attenuated[0]["reason"] == "shared_evidence"
    assert attenuated[0]["shared_cause"] == [f"sample-group:{A}"]


def test_policy_66_leaves_evidence_from_different_specimens_independent(repo):
    repo(_rec(A), _rec(B, same=[A]), _rec(C), _rec(D, same=[C]))
    assert discovery.compute_hypothesis_score(_hyp(66, B, D))["n_decisive"] == 2


def test_policy_65_projects_score_exactly_as_before(repo, monkeypatch):
    repo(_rec(A), _rec(B, same=[A]), _rec(C, same=[A]))
    h = _hyp(65, B, C)
    monkeypatch.setattr(discovery.database, "sample_groups",
                        lambda ids: pytest.fail("policy 65 must not read the index"))
    assert discovery.compute_hypothesis_score(h)["n_decisive"] == 2


def test_a_later_deposit_moves_no_score_until_the_verdict_is_re_evaluated(repo):
    records = repo(_rec(A), _rec(B, same=[A]), _rec(C))
    h = _hyp(66, B, C)
    assert discovery.compute_hypothesis_score(h)["n_decisive"] == 2
    records[D] = _rec(D, same=[A, C])        # a later record ties C to A's sample
    assert discovery.compute_hypothesis_score(h)["n_decisive"] == 2, "the score is reproducible"
    flagged = discovery._evidence_regrouped_for([h], 66)
    assert {d["record_id"] for d in flagged} == {C}
    assert flagged[0]["pinned"] == {"sample_group": None, "replica_partners": []}
    assert flagged[0]["current"] == {"sample_group": A, "replica_partners": []}
    h2 = _hyp(66, B, C)                      # re-evaluating re-pins
    assert discovery.compute_hypothesis_score(h2)["n_decisive"] == 1
    assert discovery._evidence_regrouped_for([h2], 66) == []


def test_the_regrouping_check_flags_unresolved_and_unpinned_evidence_and_skips_older_policies(repo):
    repo(_rec(A), _rec(B, same=[A]))
    h = _hyp(66, A, B)
    h["predictions"][0]["evidence_pins"] = [{"record_id": A, "groups_unresolved": "index_unavailable"}]
    h["predictions"][1]["evidence_pins"] = []
    flagged = {d["record_id"]: d["pinned"] for d in discovery._evidence_regrouped_for([h], 66)}
    assert flagged == {A: {"unresolved": "index_unavailable"}, B: {"unresolved": "not_pinned"}}
    assert discovery._evidence_regrouped_for([h], 65) == []


def test_unknown_independence_moves_belief_but_earns_no_standing(repo, monkeypatch):
    """Two reviewers flagged the first draft, which fell back to each record's declared links
    when the groups could not be resolved: that reads an unknown as independence."""
    repo(_rec(A), _rec(B, same=[A]), _rec(C))
    def down(ids):
        raise RuntimeError("index unavailable")
    monkeypatch.setattr(discovery.database, "sample_groups", down)
    s = discovery.compute_hypothesis_score(_hyp(66, B, C))
    assert s["n_decisive"] == 0 and s["breakdown"]["ungrouped_excluded"] == 2
    assert s["computed_confidence"] > 0.5
    assert {d["gate"] for d in s["predictions"].values()} == {"ungrouped"}
    assert discovery._evidence_regrouped_for([_hyp(66, B, C)], 66) == []   # the check degrades quietly


def test_evidence_that_is_not_a_record_id_needs_no_group(repo):
    repo(_rec(A))
    p = {"evidence_record_ids": [A, "doi:10.1000/x", "auto-ev-1"], "evidence_pins": _pins(A)}
    assert discovery._ungrouped_evidence(p) == []


class _Cur:
    """Answers the stored-confidence recompute from a scripted hypothesis."""
    def __init__(self, preds, policy):
        self.preds, self.policy, self.rows, self.sql = preds, policy, [], []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        if "FROM hyp_predictions" in sql:
            wanted = [c.strip() for c in sql.split("SELECT", 1)[1].split("FROM", 1)[0].split(",")]
            self.rows = [{c: p.get(c) for c in wanted} for p in self.preds]
        elif "FROM hyp_compute_runs" in sql:
            self.rows = []
        elif "FROM hyp_hypotheses" in sql:
            self.rows = [{"project_id": "P1", "grounding": None, "label": "H1"}]
        elif "FROM hyp_projects" in sql:
            self.rows = [{"policy_version": self.policy}]
        else:
            self.rows = []

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_the_stored_confidence_reads_the_pinned_groups(repo, monkeypatch):
    repo(_rec(A), _rec(B, same=[A]), _rec(C, same=[A]))
    h = _hyp(66, B, C)
    monkeypatch.setattr(discovery, "_snapshot_confidence", lambda *a, **k: None)
    stored = discovery._recompute_and_store_confidence(_Cur(h["predictions"], 66), "H")
    assert stored == discovery.compute_hypothesis_score(h)["computed_confidence"]
    assert stored != discovery.compute_hypothesis_score(_hyp(65, B, C))["computed_confidence"]


# --- the contract ------------------------------------------------------------------------

def test_the_gate_is_registered_and_current():
    assert tp.POLICY_SAMPLE_GROUPS == 66 == tp.CURRENT_POLICY_VERSION
    assert tp.POLICY_SHARED_CAUSE < tp.POLICY_SAMPLE_GROUPS


def test_the_manifest_advertises_the_policy_and_states_the_rule():
    man = discovery.get_manifest()
    node = man.get("contract", man)
    assert node["policy_version"] == 66
    src = open(discovery.__file__.replace(".pyc", ".py")).read()
    i = src.index("independence_is_shared_cause_not_shared_identifier")
    text = src[i:i + 4000]
    assert "policy_version >= 66" in text and "sample.sample_id" in text


def test_the_clause_names_no_domain():
    """STANDING: the manifest must be generic for any scientific discovery."""
    src = open(discovery.__file__.replace(".pyc", ".py")).read()
    i = src.index("independence_is_shared_cause_not_shared_identifier")
    clause = src[i:i + 4000].lower()
    for banned in ("cu-ag", "cu-au", "co2rr", "faradaic", "catalys", "electrode", "potentiostat",
                   "lisa", "slac", "vasp"):
        assert banned not in clause, "clause leaked a domain term: %s" % banned
