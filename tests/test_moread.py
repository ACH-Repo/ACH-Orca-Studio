"""Tests for the MOREAD wavefunction-restart injection (Q3 tier 3).

`inputs.add_moread` makes a rendered ORCA input restart from a parent's converged
`.gbw`; `PlannedCalc.orbital_source` records it. Pure / offline.

Run:  python -m pytest tests/test_moread.py -q
"""

from orca_workbench.core import inputs
from orca_workbench.core.project import PlannedCalc


def test_add_moread_injects_keyword_and_moinp():
    inp = "! B3LYP def2-SVP\n\n%pal nprocs 4\nend\n\n* xyz 0 1\nO 0.0 0.0 0.0\n*\n"
    out = inputs.add_moread(inp, "/shared/run/parent.gbw")
    lines = out.split("\n")
    assert lines[0] == "! B3LYP def2-SVP MOREAD"
    assert lines[1] == '%moinp "/shared/run/parent.gbw"'
    assert "%pal nprocs 4" in out          # rest of the input untouched


def test_add_moread_idempotent_keeps_existing():
    inp = '! HF MOREAD\n%moinp "old.gbw"\n* xyz 0 1\nH 0.0 0.0 0.0\n*\n'
    out = inputs.add_moread(inp, "/shared/parent.gbw")
    assert out.count("MOREAD") == 1                 # not doubled on the ! line
    assert out.count("%moinp") == 1                 # existing %moinp kept
    assert "old.gbw" in out and "parent.gbw" not in out


def test_add_moread_noop_without_path_or_keyword():
    inp = "! HF\n* xyz 0 1\nH 0.0 0.0 0.0\n*\n"
    assert inputs.add_moread(inp, "") == inp                       # no path
    assert inputs.add_moread("no keyword line\n", "/x.gbw") == "no keyword line\n"


def test_add_moread_backslashes_normalised():
    out = inputs.add_moread("! HF\n", "C:\\run\\p.gbw")
    assert '%moinp "C:/run/p.gbw"' in out


def test_orbital_source_default_and_migration():
    c = PlannedCalc(id="1", molecule_filename="m", recipe_name="r")
    assert c.orbital_source is None
    assert PlannedCalc.from_dict({"id": "1", "molecule_filename": "m",
                                  "recipe_name": "r"}).orbital_source is None
    c3 = PlannedCalc.from_dict({"id": "1", "molecule_filename": "m", "recipe_name": "r",
                                "orbital_source": "parent:abc"})
    assert c3.orbital_source == "parent:abc"
