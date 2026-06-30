"""Tests for EPR parsing + reporting (g-tensor + hyperfine).

Fixtures mirror real ORCA 6.0.1 %eprnmr output (methyl radical, verified locally).

Run:  python -m pytest tests/test_epr.py -q
"""

from orca_workbench.core import orca_parser as P
from orca_workbench.core import reporting as R


# Trimmed from a real methyl-radical UKS/def2-SVP %eprnmr run.
EPR_OUT = """\
------------------
ELECTRONIC G-MATRIX
------------------

The g-matrix:
              2.0027987    0.0000000   -0.0000000
             -0.0000000    2.0027987   -0.0000000
             -0.0000000    0.0000000    2.0022279

 gel          2.0023193    2.0023193    2.0023193
 g(tot)       2.0022279    2.0027987    2.0027987 iso=  2.0026085
 Delta-g     -0.0000914    0.0004795    0.0004795 iso=  0.0002892

-----------------------------------------
ELECTRIC AND MAGNETIC HYPERFINE STRUCTURE (4 nuclei)
-----------------------------------------

 Nucleus   0C : A  : Isotope=   13 I=  0.5 P=134.1903 MHz/au**3
 A(Tot)        132.5259             132.5264             356.2915    A(iso)=  207.1146

 Nucleus   1H : A  : Isotope=    1 I=  0.5 P=533.5514 MHz/au**3
 A(Tot)        -19.6412             -61.7432            -100.7299    A(iso)=  -60.7047

 Nucleus   2H : A  : Isotope=    1 I=  0.5 P=533.5514 MHz/au**3
 A(Tot)        -19.6232             -61.7265            -100.7149    A(iso)=  -60.6882
"""


def test_parse_g_tensor():
    epr = P.parse_epr(EPR_OUT)
    g = epr["g_tensor"]
    assert g["g_iso"] == 2.0026085
    assert g["g"] == [2.0022279, 2.0027987, 2.0027987]


def test_parse_hyperfine_pairs_nucleus_with_a_tensor():
    epr = P.parse_epr(EPR_OUT)
    hf = epr["hyperfine"]
    assert len(hf) == 3
    assert hf[0]["index"] == 0 and hf[0]["element"] == "C"
    assert hf[0]["A_iso"] == 207.1146
    assert hf[0]["A"] == [132.5259, 132.5264, 356.2915]
    # H couplings are negative (spin polarisation) — sign must survive
    assert hf[1]["element"] == "H" and hf[1]["A_iso"] == -60.7047


def test_parse_epr_none_without_block():
    assert P.parse_epr("FINAL SINGLE POINT ENERGY -39.0\n") is None


def test_g_tensor_only_no_hyperfine():
    txt = " g(tot)       2.0022279    2.0027987    2.0027987 iso=  2.0026085\n"
    epr = P.parse_epr(txt)
    assert epr["g_tensor"]["g_iso"] == 2.0026085
    assert "hyperfine" not in epr


def test_report_extractor_and_csv():
    frag = R._x_epr(EPR_OUT, None)
    assert frag["epr"]["g_tensor"]["g_iso"] == 2.0026085
    row = R._csv_row({"properties": frag})
    assert row["g_iso"] == 2.0026085
    assert row["n_hyperfine_nuclei"] == 3
    assert row["max_abs_A_iso_MHz"] == 207.1146   # the 13C coupling


def test_report_extractor_none_without_block():
    assert R._x_epr("nothing here", None) is None


# ----------------------------------------------- isotropic spectrum simulation
from orca_workbench.core import epr as EPR

_METHYL_HF = [
    {"index": 0, "element": "C", "A_iso": 207.11},
    {"index": 1, "element": "H", "A_iso": -60.70},
    {"index": 2, "element": "H", "A_iso": -60.69},
    {"index": 3, "element": "H", "A_iso": -60.69},
]


def test_center_field_xband():
    # g=2.0023 at 9.5 GHz -> ~339 mT (X-band)
    assert abs(EPR.center_field_mT(2.0023, 9.5) - 339.0) < 1.0


def test_equivalent_groups_collapses_three_H():
    groups = EPR.equivalent_groups(_METHYL_HF)
    # sorted by |A| desc: C (1 nucleus) then the 3 equivalent H
    assert groups[0]["element"] == "C" and groups[0]["count"] == 1
    assert groups[1]["element"] == "H" and groups[1]["count"] == 3


def test_small_couplings_dropped():
    hf = [{"index": 0, "element": "H", "A_iso": 0.2}]   # below tol
    assert EPR.equivalent_groups(hf, tol_MHz=1.0) == []


def test_three_equivalent_spin_half_gives_1331_quartet():
    sticks = EPR.stick_lines([{"element": "H", "A": -60.7, "count": 3}])
    assert len(sticks) == 4
    intensities = sorted(round(i, 4) for _, i in sticks)
    assert intensities == [0.125, 0.125, 0.375, 0.375]   # 1:3:3:1


def test_simulate_shapes_and_center():
    sim = EPR.simulate(2.0026, _METHYL_HF, npoints=500)
    assert len(sim["field_mT"]) == 500 and len(sim["derivative"]) == 500
    # 4 H-lines x 2 C-lines = 8 sticks
    assert len(sim["sticks"]) == 8
    assert abs(sim["center_mT"] - EPR.center_field_mT(2.0026, 9.5)) < 1e-9
    # derivative crosses zero (it's a derivative lineshape, net ~0 area)
    assert min(sim["derivative"]) < 0 < max(sim["derivative"])
