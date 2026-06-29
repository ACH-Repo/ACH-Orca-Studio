"""Tests for the orca_plot wizard driver (Q3 tier 2 — density/MO cubes).

Pure: the keystroke sequence + output parsing. Integers verified on ORCA 6.0.1.

Run:  python -m pytest tests/test_orca_plot.py -q
"""

import pytest

from orca_workbench.core import orca_plot as OP


def test_density_sequence():
    # type of plot (1) -> (scf) electron density (2) -> generate (11) -> exit (12)
    assert OP.plot_stdin("density") == "1\n2\n11\n12\n"


def test_spin_density_sequence():
    assert OP.plot_stdin("spin") == "1\n3\n11\n12\n"


def test_mo_sequence_alpha():
    # molecular orbitals (1,1), orbital number (2,7), generate/exit
    assert OP.plot_stdin("mo", mo_index=7) == "1\n1\n2\n7\n11\n12\n"


def test_mo_sequence_beta_operator():
    assert OP.plot_stdin("mo", mo_index=3, operator=1) == "1\n1\n2\n3\n3\n1\n11\n12\n"


def test_grid_prefixes_the_sequence():
    assert OP.plot_stdin("density", grid=80) == "4\n80\n1\n2\n11\n12\n"


def test_mo_index_defaults_to_zero():
    assert OP.plot_stdin("mo") == "1\n1\n2\n0\n11\n12\n"


def test_invalid_plot_type_raises():
    with pytest.raises(ValueError):
        OP.plot_stdin("orbitals")


def test_parse_output_cube():
    out = ("Calling PlotGrid3d ...\n"
           "                 *** PLOTTING FINISHED ***\n"
           " Output file: ch2o.mo0a.cube\n")
    assert OP.parse_output_cube(out) == "ch2o.mo0a.cube"


def test_parse_output_cube_returns_last():
    out = "Output file: a.eldens.cube\nmore\nOutput file: a.mo7a.cube\n"
    assert OP.parse_output_cube(out) == "a.mo7a.cube"


def test_parse_output_cube_none():
    assert OP.parse_output_cube("no cube was written") is None
