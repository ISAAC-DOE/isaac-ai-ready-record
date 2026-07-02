"""Prediction-creation idempotency (live-reproducibility feedback).

POST /hypotheses/{id}/predictions must be idempotent on
(hypothesis_id, descriptor_name, output_quantity) — the same pattern that
already protects compute runs (slurm_job_id). Before this, a resumed or
interrupted agent that re-POSTed a prediction silently created duplicates.

Runs offline: the DB connection is faked, exercising create_prediction's
real control flow (dedupe SELECT -> early return vs INSERT path).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import discovery  # noqa: E402


class _Cur:
    def __init__(self, existing_row):
        self.existing = existing_row
        self.executed = []
        self._last = None

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))
        if "FROM hyp_hypotheses" in sql or "project_of" in sql:
            self._last = {"project_id": "P1"}
        elif "SELECT prediction_id FROM hyp_predictions" in sql:
            self._last = self.existing
        else:
            self._last = None

    def fetchone(self):
        return self._last

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _patch(monkeypatch, existing_row):
    cur = _Cur(existing_row)
    conn = _Conn(cur)
    monkeypatch.setattr(discovery, "_conn", lambda: conn)
    monkeypatch.setattr(discovery, "_project_of_hypothesis",
                        lambda c, hid: "P1")
    monkeypatch.setattr(discovery, "_append_event",
                        lambda *a, **k: None)
    return cur, conn


def test_repost_returns_existing_ulid_no_insert(monkeypatch):
    cur, conn = _patch(monkeypatch, {"prediction_id": "01EXISTINGULID0000000000AA"})
    out = discovery.create_prediction(
        "01HYP0000000000000000000AA", "faradaic_efficiency.C2H4",
        output_quantity="fraction", actor="agent")
    assert out == "01EXISTINGULID0000000000AA"
    assert not any(s.startswith("INSERT INTO hyp_predictions") for s in cur.executed), \
        "re-POST must NOT insert a duplicate prediction"


def test_new_prediction_inserts(monkeypatch):
    cur, conn = _patch(monkeypatch, None)  # no existing row
    out = discovery.create_prediction(
        "01HYP0000000000000000000AA", "faradaic_efficiency.C2H4",
        output_quantity="fraction", actor="agent")
    assert out and out != "01EXISTINGULID0000000000AA"
    assert any(s.startswith("INSERT INTO hyp_predictions") for s in cur.executed)
    assert conn.committed


def test_output_quantity_distinguishes(monkeypatch):
    """Same descriptor with a DIFFERENT output_quantity is a different prediction:
    the dedupe key must be the pair, not descriptor_name alone."""
    cur, _ = _patch(monkeypatch, None)
    discovery.create_prediction(
        "01HYP0000000000000000000AA", "faradaic_efficiency.C2H4",
        output_quantity="partial_current_density", actor="agent")
    dedupe = [s for s in cur.executed if "SELECT prediction_id FROM hyp_predictions" in s]
    assert dedupe and "output_quantity IS NOT DISTINCT FROM" in dedupe[0], \
        "dedupe must key on (descriptor_name, output_quantity), NULL-safely"
