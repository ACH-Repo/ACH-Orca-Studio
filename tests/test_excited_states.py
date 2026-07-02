"""Tests for the TD-DFT absorption-spectrum parser + report extractor.

The parser must handle both the ORCA 5 layout (state index, cm-1, nm, fosc, …)
and the ORCA 6 layout (transition label, eV, cm-1, nm, fosc, …).

Run:  python -m pytest tests/test_excited_states.py -q
"""

from orca_workbench.core import orca_parser as P
from orca_workbench.core import reporting


ORCA6 = """\
-----------------------------------------------------------------------------------
ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS
-----------------------------------------------------------------------------------
     Transition      Energy     Energy  Wavelength fosc(D2)      D2        DX        DY        DZ
                      (eV)      (cm-1)    (nm)                 (au**2)    (au)      (au)      (au)
-----------------------------------------------------------------------------------
  0-1A  ->  1-1A    4.8254  38911.6   257.0   0.123456789   0.39528  -0.084   0.000   0.000
  0-1A  ->  2-1A    5.5000  44360.5   225.4   0.000000000   0.00000   0.000   0.000   0.000
  0-1A  ->  3-1A    6.2000  50006.4   200.0   0.045000000   0.10000   0.100   0.000   0.000

ABSORPTION SPECTRUM VIA TRANSITION VELOCITY DIPOLE MOMENTS
  (this block must NOT be parsed)
"""

ORCA5 = """\
         ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS
-----------------------------------------------------------------------------
State   Energy  Wavelength   fosc         T2        TX        TY        TZ
        (cm-1)    (nm)                  (au**2)
-----------------------------------------------------------------------------
   1   38911.6   257.0   0.123456789   0.5  0.1  0.0  0.0
   2   44360.5   225.4   0.000000000   0.0  0.0  0.0  0.0
"""


def test_parse_orca6_layout():
    st = P.parse_absorption_spectrum(ORCA6)
    assert len(st) == 3                                # velocity block excluded
    assert st[0]["wavelength_nm"] == 257.0
    assert st[0]["fosc"] == 0.123456789
    assert st[0]["energy_cm"] == 38911.6
    assert abs(st[0]["energy_eV"] - 38911.6 / 8065.543937) < 1e-3
    assert st[0]["state"] == 1 and st[2]["state"] == 3


def test_parse_orca5_layout():
    st = P.parse_absorption_spectrum(ORCA5)
    assert len(st) == 2
    assert st[0]["wavelength_nm"] == 257.0 and st[0]["fosc"] == 0.123456789
    assert st[1]["fosc"] == 0.0


def test_no_absorption_block():
    assert P.parse_absorption_spectrum("nothing here\nFINAL SINGLE POINT ENERGY -1.0\n") == []


def test_report_extractor_excited_states():
    frag = reporting._x_excited_states(ORCA6, ctx=None)
    es = frag["excited_states"]
    assert es["n_states"] == 3
    assert es["max_fosc"] == 0.123456789
    assert es["lambda_max_nm"] == 257.0           # nm of the most intense transition


def test_report_extractor_none_without_block():
    assert reporting._x_excited_states("no tddft here", ctx=None) is None


# ----------------------------------------------------- excited states = Property
from orca_workbench.core import workflow as wf


def test_excited_states_folded_into_property():
    # Vertical TD-DFT (UV-Vis) is a property computed at a fixed geometry, so there
    # is NO dedicated node — the Property node + a TD-DFT recipe covers it (the plot
    # button + report extractor key off the recipe's calctype, not a node type).
    assert "excited_states" not in wf.NODE_TYPES
    assert "property" in wf.NODE_TYPES
    # The Property node covers SP/NMR/UV-Vis/EPR/... — its label is just "Property"
    # (the recipe line under the node title says which kind), not per-type.
    assert wf.NODE_TYPES["property"]["label"] == "Property"
