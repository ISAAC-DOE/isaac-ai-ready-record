"""Units are documented where agents read them: every unit with a definition is canonical, the Units section is
rendered on the Controlled-Vocabulary wiki page, and the generator never replaces a page title."""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())


def _gen():
    spec = importlib.util.spec_from_file_location("gwv", REPO / "tools" / "generate_wiki_vocab.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_every_defined_unit_is_canonical_and_accepted():
    import validation
    defined = VOCAB["Units"]["units.definitions"]["map"]
    assert {"e", "ocean_intensity_au"} <= set(defined)
    for unit, meaning in defined.items():
        assert unit in validation.CANONICAL_UNIT_SET, unit
        assert len(meaning.split()) >= 8, unit          # a definition, not a label


def test_units_are_rendered_on_the_controlled_vocabulary_page():
    g = _gen()
    assert g.PAGE_SECTIONS.get("Controlled-Vocabulary.md") == ["Units"]
    block = g.render_section(["Units"])
    assert "units.definitions" in block and "ocean_intensity_au" in block
    assert "| `ocean_intensity_au` | OCEAN intensity" in block      # a meaning is prose, not a backticked token


def test_generator_keeps_a_page_title_that_says_controlled_vocabulary(tmp_path):
    g = _gen()
    page = tmp_path / "Controlled-Vocabulary.md"
    page.write_text("# Controlled Vocabulary (ISAAC v1.05+)\n\nIntro that must survive.\n\n## Unit grammar\n\ntext\n")
    out = g.apply(page, ["Units"])
    assert out.startswith("# Controlled Vocabulary (ISAAC v1.05+)\n\nIntro that must survive.")
    assert g.BEGIN in out and out.index(g.BEGIN) > out.index("## Unit grammar")
