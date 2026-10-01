"""Keys that connect records, derived from what each record already says (portal/record_graph.py).

A paper cited as '10.1038/x' by one record and 'https://doi.org/10.1038/X' by another was two
different strings, so an agent asking for "every record from this paper" found some of them; a
same_sample_as link declared by one record only was invisible from the other. The keys (study,
sample, lab, organization, setup, method) and the two-way link index close both gaps without
asking anything new of uploaders. These tests run offline: derivation is pure, and the database
and HTTP layers run against fakes. The SQL itself was checked on a Postgres loaded with the full
corpus (see the pull request).
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import record_graph as rg  # noqa: E402

VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())
EXAMPLES = REPO / "examples"


def _record(**parts):
    base = {"record_id": "01TESTRECORD00000000000000", "record_type": "evidence"}
    base.update(parts)
    return base


def _cite(doi=None, relation="source", uri=None, title=None, year=None):
    citation = {k: v for k, v in (("doi", doi), ("relation", relation), ("title", title), ("year", year)) if v}
    asset = {"asset_id": "a", "content_role": "documentation", "uri": uri or "https://example.org/x", "sha256": "x",
             "citation": citation}
    return asset


# --- study -------------------------------------------------------------------

@pytest.mark.parametrize("spelling", [
    "10.1038/s41929-023-01008-0", "10.1038/S41929-023-01008-0", "doi:10.1038/s41929-023-01008-0",
    "DOI: 10.1038/s41929-023-01008-0", "https://doi.org/10.1038/s41929-023-01008-0",
    "http://dx.doi.org/10.1038/s41929-023-01008-0.", " https://doi.org/10.1038/s41929-023-01008-0 "])
def test_every_spelling_of_a_doi_is_one_study(spelling):
    assert rg.normalize_doi(spelling) == "10.1038/s41929-023-01008-0"
    assert rg.study_keys(_record(assets=[_cite(spelling)])) == ["doi:10.1038/s41929-023-01008-0"]
    assert rg.key_from_param("study", spelling) == "doi:10.1038/s41929-023-01008-0"


@pytest.mark.parametrize("junk", ["", None, "not a doi", "https://example.org/paper", "11.1038/x", "10.12/x"])
def test_a_value_that_is_no_doi_names_no_study(junk):
    assert rg.normalize_doi(junk) is None
    assert rg.key_from_param("study", junk) is None


def test_the_doi_may_sit_in_the_citation_or_in_a_doi_link():
    uri_only = _record(assets=[{"asset_id": "a", "content_role": "auxiliary_reference", "sha256": "x",
                                "uri": "https://doi.org/10.1021/jacs.9b09932"}])
    citation_without_doi = _record(assets=[_cite(uri="https://doi.org/10.1021/jacs.9b09932")])
    assert rg.study_keys(uri_only) == rg.study_keys(citation_without_doi) == ["doi:10.1021/jacs.9b09932"]


def test_a_reference_is_not_the_study_and_reports_this_work_is():
    record = _record(assets=[_cite("10.1000/original", "source"), _cite("10.1000/review", "reference"),
                             _cite("10.1000/dataset", "reports_this_work")])
    assert rg.study_keys(record) == ["doi:10.1000/dataset", "doi:10.1000/original"]


def test_a_paper_and_its_supporting_information_are_one_study():
    record = _record(assets=[_cite("10.1021/jacs.9b09932"), _cite("10.1021/jacs.9b09932.s001")])
    assert rg.study_keys(record) == ["doi:10.1021/jacs.9b09932"]


def test_a_work_without_a_doi_is_keyed_by_title_and_year():
    record = _record(assets=[_cite(title="Understanding  Reactivity_of Oxides", year=2024)])
    assert rg.study_keys(record) == ["work:understanding reactivity of oxides|2024"]
    assert rg.key_from_param("study", "work:Understanding Reactivity of Oxides|2024") == rg.study_keys(record)[0]


def test_a_database_collection_is_a_study():
    record = _record(assets=[{"asset_id": "a", "content_role": "raw_data_pointer", "uri": "https://x", "sha256": "x",
                              "database_entry": {"database": "Catalysis-Hub", "entry_id": "42",
                                                 "collection": "PasumarthiDoubleLayer2023"}}])
    assert rg.study_keys(record) == ["db:catalysis-hub:pasumarthidoublelayer2023"]
    assert rg.key_from_param("study", "db:CATALYSIS-HUB:PasumarthiDoubleLayer2023") == rg.study_keys(record)[0]


def test_a_record_citing_two_sources_carries_both_keys():
    record = _record(assets=[_cite("10.1000/a"), _cite("10.2000/b")])
    assert rg.study_keys(record) == ["doi:10.1000/a", "doi:10.2000/b"]


def test_the_contract_paper_examples_are_one_study_and_the_review_is_not_theirs():
    a, b, calc, rate = (json.loads((EXAMPLES / n).read_text()) for n in (
        "literature_paper_catalyst_a_record.json", "literature_paper_catalyst_b_record.json",
        "literature_paper_calculation_record.json", "literature_original_paper_rate_record.json"))
    assert rg.study_keys(a) == rg.study_keys(b) == rg.study_keys(calc) and len(rg.study_keys(a)) == 1
    review = [x["citation"]["doi"] for x in rate["assets"] if x["citation"]["relation"] == "reference"]
    assert len(rg.study_keys(rate)) == 1 and "doi:" + rg.normalize_doi(review[0]) not in rg.study_keys(rate)


# --- sample, lab, organization ------------------------------------------------------

def test_a_globally_unique_sample_id_is_keyed_as_written():
    for sid in ("6f1c2a3e-8b1d-4c5e-9f00-1a2b3c4d5e6f", "IGSN:IEXYZ0001", "https://igsn.org/10.58052/IEXYZ0001",
                "10.58052/IEXYZ0001", "urn:uuid:6f1c2a3e-8b1d-4c5e-9f00-1a2b3c4d5e6f"):
        record = _record(sample={"sample_id": sid}, attribution={"produced_by": {"organization": "SLAC"}})
        assert rg.derive_keys(record, VOCAB)["sample_id"] == "sample:" + rg.key_text(sid), sid
        assert rg.key_from_param("sample", sid) == "sample:" + rg.key_text(sid)


def test_a_local_sample_id_is_scoped_by_organization_then_lab():
    """Two labs writing 'S1' mean two objects; a local name keys nothing without a scope."""
    ours = _record(sample={"sample_id": " Cu-Foil_07 "}, attribution={"produced_by": {"organization": "SLAC"}})
    theirs = _record(sample={"sample_id": "Cu-Foil_07"}, attribution={"produced_by": {"organization": "LBNL"}})
    lab_only = _record(sample={"sample_id": "Cu-Foil_07"}, attribution={"produced_by": {"group": "Some group"}})
    alone = _record(sample={"sample_id": "Cu-Foil_07"})
    k_ours, k_theirs = rg.derive_keys(ours, VOCAB)["sample_id"], rg.derive_keys(theirs, VOCAB)["sample_id"]
    assert k_ours.startswith("sample:ror:") and k_ours.endswith("/cu-foil 07") and k_ours != k_theirs
    assert rg.derive_keys(lab_only, VOCAB)["sample_id"] == "sample:group:some group/cu-foil 07"
    assert rg.derive_keys(alone, VOCAB)["sample_id"] is None
    assert rg.sample_key(_record(sample={})) is None
    # asked for by name, a local id matches that name in every lab; a returned key matches itself
    assert rg.key_from_param("sample", "Cu-Foil_07") == "sample:*/cu-foil 07"
    assert rg.key_from_param("sample", k_ours.upper()) == k_ours


@pytest.mark.parametrize("group", ["the authors", "Authors", "original authors", "authors of the paper",
                                   "not_specified_in_source", "unknown", "N/A", "", None])
def test_a_placeholder_group_is_no_lab(group):
    record = _record(attribution={"produced_by": {"group": group}})
    assert rg.derive_keys(record, VOCAB)["lab"] is None


def test_a_named_group_is_a_lab_in_any_case_or_spacing():
    one = rg.derive_keys(_record(attribution={"produced_by": {"group": "Joel W. Ager group"}}), VOCAB)["lab"]
    two = rg.derive_keys(_record(attribution={"produced_by": {"group": "joel w.  ager GROUP"}}), VOCAB)["lab"]
    assert one == two == "group:joel w. ager group" == rg.key_from_param("lab", "Joel W. Ager group")
    # A surname that reads like a placeholder abbreviation is still a name.
    assert rg.derive_keys(_record(attribution={"produced_by": {"group": "Kyungsu Na group"}}), VOCAB)["lab"]


def test_an_organization_alias_resolves_to_the_registry_ror_id():
    registry = VOCAB["System"]["system.organizations"]["values"]
    slac_ror = registry["SLAC National Accelerator Laboratory"].rsplit("/", 1)[-1]
    for name in ("SLAC", "SLAC National Accelerator Laboratory", "slac national accelerator laboratory"):
        record = _record(attribution={"produced_by": {"organization": name}})
        assert rg.derive_keys(record, VOCAB)["organization"] == f"ror:{slac_ror}"
    assert rg.key_from_param("organization", "SLAC", VOCAB) == f"ror:{slac_ror}"
    assert rg.key_from_param("organization", registry["SLAC National Accelerator Laboratory"]) == f"ror:{slac_ror}"
    unknown = _record(attribution={"produced_by": {"organization": "Some  Institute"}})
    assert rg.derive_keys(unknown, VOCAB)["organization"] == "org:some institute"
    placeholder = _record(attribution={"produced_by": {"organization": "not_specified_in_source"}})
    assert rg.derive_keys(placeholder, VOCAB)["organization"] is None


def test_the_facility_organization_stands_in_when_no_producer_organization_is_given():
    record = _record(system={"facility": {"organization": "LBNL"}})
    assert rg.derive_keys(record, VOCAB)["organization"].startswith("ror:")


# --- setup and method ------------------------------------------------------------------

def _setup(domain="experimental", **facility):
    instrument = facility.pop("instrument_name", None)
    system = {"domain": domain, "facility": facility}
    if instrument:
        system["instrument"] = {"instrument_name": instrument}
    return _record(system=system)


def test_a_setup_is_where_and_on_what_a_measurement_was_made():
    one = rg.setup_key(_setup(facility_name="SSRL", beamline="15-2", instrument_name="BL15-2 HERFD station"))
    two = rg.setup_key(_setup(facility_name="ssrl", beamline="15-2", instrument_name="bl15-2_herfd_station"))
    assert one == two == "setup:ssrl||15-2|bl15-2 herfd station"
    assert rg.key_from_param("setup", one.upper()) == one


def test_an_instrument_model_alone_names_no_setup():
    assert rg.setup_key(_setup(instrument_name="BioLogic SP-300")) is None
    assert rg.setup_key(_setup(facility_name="not_specified_in_source", instrument_name="BioLogic SP-300")) is None


def test_a_placeholder_part_is_blank_and_a_calculation_has_no_setup():
    assert rg.setup_key(_setup(facility_name="LiSA", instrument_name="potentiostat_not_specified_in_source")) == "setup:lisa|||"
    assert rg.setup_key(_setup(domain="computational", facility_name="NERSC", cluster="perlmutter")) is None


def test_a_density_functional_method_is_known_only_with_its_functional():
    def method(**m):
        return rg.method_key(_record(computation={"method": m}))
    assert method(family="DFT", functional_name="RPBE", code="VASP") == "method:dft|rpbe|vasp"
    assert method(family="DFT", code="VASP") is None
    assert method(family="DFT", functional_name="not_reported", code="VASP") is None
    assert method(family="microkinetic", code="CatMAP") == "method:microkinetic||catmap"
    assert method(functional_name="PBE") is None


# --- links -----------------------------------------------------------------------------

def test_link_edges_are_one_per_target_and_relation_and_skip_self_and_junk():
    record = _record(links=[
        {"rel": "same_sample_as", "target": "01AAAAAAAAAAAAAAAAAAAAAAAA", "basis": "same_sample_id"},
        {"rel": "same_sample_as", "target": "01AAAAAAAAAAAAAAAAAAAAAAAA", "basis": "unspecified"},
        {"rel": "derived_from", "target": "01AAAAAAAAAAAAAAAAAAAAAAAA", "basis": "analysis_pipeline_output"},
        {"rel": "same_sample_as", "target": "01TESTRECORD00000000000000", "basis": "same_sample_id"},
        {"rel": "", "target": "01BBBBBBBBBBBBBBBBBBBBBBBB"}, "not a link"])
    assert rg.link_edges(record["record_id"], record) == [
        ("01AAAAAAAAAAAAAAAAAAAAAAAA", "derived_from", "analysis_pipeline_output"),
        ("01AAAAAAAAAAAAAAAAAAAAAAAA", "same_sample_as", "same_sample_id")]
    assert rg.link_edges("x", _record(links={"not": "a list"})) == []


def test_symmetric_relations_are_real_relations():
    assert rg.SYMMETRIC_RELATIONS <= set(VOCAB["Links"]["links.rel"]["values"])


# --- keys round-trip and re-derive when the rules change --------------------------------

def test_every_key_an_example_carries_round_trips_through_the_filter():
    for path in sorted(EXAMPLES.glob("*.json")):
        keys = rg.derive_keys(json.loads(path.read_text()), VOCAB)
        for dimension, column in rg.DIMENSIONS.items():
            for key in keys[column] if dimension == "study" else [keys[column]] if keys[column] else []:
                assert rg.key_from_param(dimension, key, VOCAB) == key, (path.name, dimension, key)


def test_the_derivation_id_moves_with_the_rules_and_the_vocabulary():
    base = rg.derivation_id(VOCAB)
    assert base.startswith(f"{rg.KEYS_VERSION}:") and base == rg.derivation_id(VOCAB)
    changed = json.loads(json.dumps(VOCAB))
    changed["System"]["system.organization_aliases"]["map"]["Stanford"] = "Stanford University"
    assert rg.derivation_id(changed) != base


def test_derivation_never_raises_on_a_malformed_record():
    for junk in ({}, {"assets": "x", "links": 3, "sample": [], "system": "x", "computation": {"method": "DFT"}},
                 {"assets": [None, 1, {"citation": "x"}], "attribution": {"produced_by": "x"}}, "not a record"):
        keys = rg.derive_keys(junk, VOCAB)
        assert keys["study"] == [] and all(keys[c] is None for c in rg.DIMENSIONS.values() if c != "study")


# --- the database layer, offline -----------------------------------------------------

class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def execute(self, sql, params=None):
        self.conn.log.append((" ".join(sql.split()), params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("index table unavailable")
        self.rows = self.conn.answer(sql)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, answer, fail_on=None):
        self.log, self.commits, self.rollbacks = [], 0, 0
        self.answer, self.fail_on = answer, fail_on

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_an_upload_succeeds_when_indexing_fails(monkeypatch):
    import database
    conn = _Conn(lambda sql: [] if "records_held" in sql else [{"record_id": "01TESTRECORD00000000000000"}],
                 fail_on="FOR SHARE")
    monkeypatch.setattr(database, "get_db_connection", lambda: conn)
    record = json.loads((EXAMPLES / "literature_paper_catalyst_a_record.json").read_text())
    saved = database.save_record(record, skip_validation=True, mode="insert")
    assert saved == "01TESTRECORD00000000000000"
    assert conn.commits == 1 and conn.rollbacks == 1  # the record committed; only the index rolled back


def test_indexing_writes_the_keys_and_replaces_the_declared_links(monkeypatch):
    import database
    record = json.loads((EXAMPLES / "literature_paper_catalyst_a_record.json").read_text())
    rid = record["record_id"]

    def answer(sql):
        return [{"data": record, "version": 3, "content_hash": "v2:abc"}] if "FOR SHARE" in sql else []
    conn = _Conn(answer)
    database._index_after_commit(conn, rid)
    sql = [s for s, _ in conn.log]
    keys_params = next(p for s, p in conn.log if s.startswith("INSERT INTO record_keys"))
    assert keys_params[0] == rid and keys_params[2] == 3 and keys_params[4] == rg.study_keys(record)
    assert keys_params[1] == rg.derivation_id()
    delete_at = next(i for i, s in enumerate(sql) if s.startswith("DELETE FROM record_links"))
    insert_at = next(i for i, s in enumerate(sql) if s.startswith("INSERT INTO record_links"))
    assert delete_at < insert_at and conn.commits == 1
    links_params = conn.log[insert_at][1]
    assert links_params[:3] == [rid, record["links"][0]["target"], record["links"][0]["rel"]]


def test_indexing_a_deleted_record_drops_its_keys_and_links(monkeypatch):
    import database
    conn = _Conn(lambda sql: [])
    database._index_after_commit(conn, "01GONE0000000000000000000A")
    sql = [s for s, _ in conn.log]
    assert any(s.startswith("DELETE FROM record_keys") for s in sql)
    assert any(s.startswith("DELETE FROM record_links WHERE source_id") for s in sql)


def test_key_filters_read_the_index(monkeypatch):
    import database
    conn = _Conn(lambda sql: [{"count": 0}] if "COUNT(*)" in sql else [])
    monkeypatch.setattr(database, "get_db_connection", lambda: conn)
    database.list_records(filters={"study": "doi:10.1/x", "lab": "group:y", "record_domain": "performance"})
    count_sql, params = conn.log[0]
    assert "study @> ARRAY[%s]::text[]" in count_sql and "WHERE lab = %s" in count_sql
    assert params == ["performance", "doi:10.1/x", "group:y"]


def test_the_index_refreshes_itself_at_most_every_ten_minutes(monkeypatch):
    import database
    runs = []
    monkeypatch.setattr(database, "backfill_record_graph", lambda: runs.append(1) or 0)
    monkeypatch.setattr(database, "_graph_refreshed_at", {"t": None})
    database.refresh_record_graph_if_due()   # first use in this process
    database.refresh_record_graph_if_due()   # within ten minutes: skipped
    database._graph_refreshed_at["t"] -= database._GRAPH_REFRESH_SECONDS + 1
    database.refresh_record_graph_if_due()   # ten minutes later
    assert len(runs) == 2


def test_a_local_sample_id_filter_matches_that_name_in_every_lab(monkeypatch):
    import database
    conn = _Conn(lambda sql: [{"count": 0}] if "COUNT(*)" in sql else [])
    monkeypatch.setattr(database, "get_db_connection", lambda: conn)
    database.list_records(filters={"sample_id": "sample:*/cu-foil 07%"})
    count_sql, params = conn.log[0]
    assert "sample_id LIKE %s" in count_sql and params == ["sample:%/cu-foil 07\\%"]
    database.list_records(filters={"sample_id": "sample:ror:05gzmn429/cu-foil 07"})
    assert conn.log[2][1] == ["sample:ror:05gzmn429/cu-foil 07"]


def test_the_new_tables_are_public_like_records():
    import database
    assert {"record_keys", "record_links"} <= set(database._AGENT_PUBLIC_TABLES)
    assert not {"record_keys", "record_links"} & set(database._AGENT_FORBIDDEN_TABLES)


# --- the HTTP layer, offline ----------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    import api
    monkeypatch.setattr(api, "_get_auth_info", lambda: {"method": "bearer_token", "user": "t", "groups": ["researcher"]})
    monkeypatch.setattr(api.database, "refresh_record_graph_if_due", lambda: None)
    api.app.config["TESTING"] = True
    return api, api.app.test_client()


def test_list_filters_normalize_before_querying(client, monkeypatch):
    api, c = client
    seen = {}
    monkeypatch.setattr(api.database, "list_records",
                        lambda **kw: (seen.update(kw) or ([], 0)))
    res = c.get("/portal/api/records?study=https://doi.org/10.1038/S41929-023-01008-0&lab=Joel%20W.%20Ager%20group"
                "&organization=SLAC&sample_id=Cu-Foil_07")
    assert res.status_code == 200
    f = seen["filters"]
    assert f["study"] == "doi:10.1038/s41929-023-01008-0" and f["lab"] == "group:joel w. ager group"
    assert f["organization"].startswith("ror:") and f["sample_id"] == "sample:*/cu-foil 07"


@pytest.mark.parametrize("query", ["study=not-a-doi", "setup=ESRF", "method=PBE", "lab=%20%20", "lab=group:",
                                   "organization=not_specified_in_source", "sample_id=sample:"])
def test_a_key_filter_that_names_nothing_is_a_400_saying_what_to_send(client, monkeypatch, query):
    api, c = client
    monkeypatch.setattr(api.database, "list_records", lambda **kw: pytest.fail("must not query"))
    res = c.get(f"/portal/api/records?{query}")
    assert res.status_code == 400 and "takes" in res.get_json()["error"]


def test_cluster_and_neighbors_validate_and_pass_through(client, monkeypatch):
    api, c = client
    calls = []
    monkeypatch.setattr(api.database, "record_clusters", lambda rid, **kw: calls.append((rid, kw)) or {"ok": 1})
    monkeypatch.setattr(api.database, "record_neighbors", lambda rid, **kw: calls.append((rid, kw)) or {"ok": 1})
    rid = "01TESTRECORD00000000000000"
    assert c.get(f"/portal/api/records/{rid}/cluster").status_code == 200
    assert c.get(f"/portal/api/records/{rid}/cluster?by=study&limit=5000&offset=10").status_code == 200
    assert calls[-1] == (rid, {"by": "study", "limit": 1000, "offset": 10})
    assert c.get(f"/portal/api/records/{rid}/neighbors?direction=in&rel=same_sample_as").status_code == 200
    assert calls[-1] == (rid, {"direction": "in", "rel": "same_sample_as", "limit": 200, "offset": 0})
    for bad in ("cluster?by=colour", "cluster?foo=1", "cluster?limit=0", "cluster?offset=x",
                "neighbors?direction=sideways", "neighbors?rel=cousin_of", "neighbors?foo=1"):
        assert c.get(f"/portal/api/records/{rid}/{bad}").status_code == 400, bad


def test_cluster_and_neighbors_of_a_missing_record_are_404(client, monkeypatch):
    api, c = client
    monkeypatch.setattr(api.database, "record_clusters", lambda rid, **kw: None)
    monkeypatch.setattr(api.database, "record_neighbors", lambda rid, **kw: None)
    assert c.get("/portal/api/records/01MISSING00000000000000000/cluster").status_code == 404
    assert c.get("/portal/api/records/01MISSING00000000000000000/neighbors").status_code == 404
