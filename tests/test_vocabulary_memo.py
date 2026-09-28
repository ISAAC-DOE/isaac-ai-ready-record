"""
The vocabulary is read once per 30 s per process, not several times per validated record.

In production each uncached read cost a connection test plus a query, so checking one
owner's 409 records against the current rules took 41 s.
"""

import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import ontology  # noqa: E402


def test_repeated_reads_hit_the_source_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ontology, "_load_vocabulary_uncached", lambda: calls.append(1) or {"System": {}})
    ontology._forget_vocabulary()
    for _ in range(50):
        assert ontology.load_vocabulary() == {"System": {}}
    assert len(calls) == 1
    ontology._forget_vocabulary()
    ontology.load_vocabulary()
    assert len(calls) == 2
    ontology._forget_vocabulary()
