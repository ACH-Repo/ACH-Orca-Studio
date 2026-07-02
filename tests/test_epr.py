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


# ---- general nuclear spin (I != 1/2) ----------------------------------------
def test_spin_multiplet_binomial_and_triplet():
    assert EPR._spin_multiplet(3, 1) == [1, 3, 3, 1]    # three spin-1/2 -> 1:3:3:1
    assert EPR._spin_multiplet(1, 2) == [1, 1, 1]        # one spin-1 (14N) -> 1:1:1
    assert EPR._spin_multiplet(2, 2) == [1, 2, 3, 2, 1]  # two spin-1 -> 1:2:3:2:1


def test_one_spin1_nucleus_gives_equal_triplet():
    sticks = EPR.stick_lines([{"element": "N", "A": 40.0, "count": 1, "twoI": 2}])
    assert len(sticks) == 3
    assert all(abs(i - 1 / 3.0) < 1e-9 for _, i in sticks)


def test_parser_captures_nuclear_spin():
    epr = P.parse_epr(EPR_OUT)
    assert epr["hyperfine"][0]["I"] == 0.5


# ---- anisotropic powder simulation ------------------------------------------
def test_powder_shapes_and_mode():
    sim = EPR.powder_spectrum([2.0090, 2.0061, 2.0022], _METHYL_HF,
                              n_theta=20, n_phi=40, npoints=600)
    assert sim["mode"] == "powder"
    assert len(sim["field_mT"]) == 600 and len(sim["derivative"]) == 600
    assert min(sim["derivative"]) < 0 < max(sim["derivative"])


def test_powder_g_anisotropy_sets_field_span():
    # No hyperfine: a rhombic g-tensor must span B(g_max)..B(g_min) at X-band.
    g = [2.0090, 2.0061, 2.0022]
    sim = EPR.powder_spectrum(g, [], n_theta=30, n_phi=60, npoints=800)
    b_gmax = EPR.center_field_mT(max(g), 9.5)   # larger g -> smaller field
    b_gmin = EPR.center_field_mT(min(g), 9.5)
    field = sim["field_mT"]
    assert field[0] <= b_gmax + 0.5 and field[-1] >= b_gmin - 0.5
    assert field[0] < field[-1]


def test_powder_no_data_is_safe():
    sim = EPR.powder_spectrum([2.0, 2.0, 2.0], [], n_theta=4, n_phi=4, npoints=10)
    assert sim["mode"] == "powder" and len(sim["field_mT"]) == len(sim["derivative"])


# ----------------------------------------------- ENDOR (same hyperfine, no new calc)
def test_nuclear_larmor():
    assert abs(EPR.nuclear_larmor_MHz("H", 340.0) - 14.48) < 0.05   # ~14.5 MHz at X-band
    assert EPR.nuclear_larmor_MHz("Xx", 340.0) is None              # untabulated element


def test_endor_lines_weak_and_strong_coupling():
    # H, weak coupling (A/2 < nu_n): the two lines straddle the proton Larmor freq
    lines = EPR.endor_lines(2.0026, [{"element": "H", "A_iso": 10.0, "I": 0.5}])
    L = lines[0]
    lo, hi = L["lines"]
    assert L["element"] == "H" and lo < L["nu_n"] < hi
    assert abs((hi - lo) - 10.0) < 1e-6              # split by A
    # C, strong coupling (A/2 > nu_n): lines centred on A/2
    lines = EPR.endor_lines(2.0026, [{"element": "C", "A_iso": 120.0, "I": 0.5}])
    lo, hi = lines[0]["lines"]
    assert abs((lo + hi) / 2.0 - 60.0) < 1e-6


def test_endor_spectrum_shape():
    hf = [{"element": "H", "A_iso": 40.0, "I": 0.5}] * 3 + \
         [{"element": "C", "A_iso": 120.0, "I": 0.5}]
    sp = EPR.endor_spectrum(2.0026, hf, npoints=400)
    assert len(sp["freq_MHz"]) == 400 and len(sp["absorption"]) == 400
    assert len(sp["sticks"]) == 4                    # 2 groups x 2 lines each
    assert max(sp["absorption"]) > 0


def test_endor_empty_when_no_coupling():
    sp = EPR.endor_spectrum(2.0026, [{"element": "H", "A_iso": 0.0, "I": 0.5}])
    assert sp["sticks"] == []                        # A below tol -> no lines
