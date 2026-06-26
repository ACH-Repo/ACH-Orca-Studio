"""Tests for core.zpva (property-agnostic ZPVA correction + displaced geometries).

numpy required. No ORCA / cluster — the ORCA readers are fed synthetic text/files
and the maths is checked against closed-form expressions.

Run:  python -m pytest tests/test_zpva.py -q
"""

import numpy as np
import pytest

zpva = pytest.importorskip("orca_workbench.core.zpva")
hess_mod = pytest.importorskip("orca_workbench.core.hess")


def test_anharmonic_prefactor_selftest():
    # 1-D Schrodinger on a grid confirms <q> = -(1/4w) phi (the anharmonic term).
    assert zpva.selftest() is True


def test_read_engrad(tmp_path):
    # natoms=2 -> 6 gradient components, after the comment lines + energy line.
    txt = ("#\n# Number of atoms\n#\n2\n"
           "#\n# Energy\n#\n-1.5\n"
           "#\n# Gradient\n#\n"
           "0.10\n0.20\n0.30\n-0.10\n-0.20\n-0.30\n"
           "#\n# coords\n#\n8 0 0 0\n")
    p = str(tmp_path / "m.engrad")
    with open(p, "w") as fh:
        fh.write(txt)
    g = zpva.read_engrad(p)
    assert g.shape == (6,)
    assert np.allclose(g, [0.1, 0.2, 0.3, -0.1, -0.2, -0.3])


def test_zpva_correction_matches_closed_form():
    # 1-DOF synthetic system: disp_bohr = [[1]] so the reduced-mode gradient is
    # just the (scalar) Cartesian gradient. Everything is then hand-computable.
    w = 0.01
    dq = 1.0
    modes = {"real": [0], "omega_au": np.array([w]), "disp_bohr": np.array([[1.0]])}
    p0, pp, pm = 100.0, 100.6, 99.8          # property at 0, +dq, -dq
    gp, gm = 0.004, -0.002                    # cartesian gradient at +dq, -dq
    res = zpva.zpva_correction(p0, {0: (pp, pm)}, {0: (np.array([gp]), np.array([gm]))},
                               modes, dq=dq)

    dprop = (pp - pm) / (2 * dq)
    d2prop = (pp - 2 * p0 + pm) / (dq * dq)
    phi = (gp + gm) / (dq * dq)
    avg_q = -(1.0 / (4.0 * w)) * phi
    harm = 0.25 * d2prop
    anharm = dprop * avg_q

    assert res["harmonic"] == pytest.approx(harm)
    assert res["anharmonic"] == pytest.approx(anharm)
    assert res["correction"] == pytest.approx(harm + anharm)
    assert res["property_zpva"] == pytest.approx(p0 + harm + anharm)


def _diatomic_hess():
    H = np.zeros((6, 6))
    H[2, 2] = H[5, 5] = 0.5
    H[2, 5] = H[5, 2] = -0.5
    return hess_mod.Hessian(["H", "H"], [zpva.M_H, zpva.M_H],
                            [[0, 0, 0], [0, 0, 1.4]], H, [0.0] * 6)


def test_displaced_geometries_symmetric():
    h = _diatomic_hess()
    coords0, disps, modes = zpva.displaced_geometries(h, dq=1.0)
    assert len(modes["real"]) == 1
    assert len(disps) == 2                          # one real mode x (+/-)
    assert np.allclose(coords0, h.coords_ang)
    (i, sp, cp), (j, sm, cm) = disps
    assert i == j and {sp, sm} == {1, -1}
    # +dq and -dq displacements are symmetric about the equilibrium geometry
    assert np.allclose(cp + cm, 2.0 * coords0)
    # and they actually moved
    assert not np.allclose(cp, coords0)


def test_displaced_geometries_isotopologue_smaller_step():
    # Heavier masses -> smaller Cartesian step per unit reduced coordinate.
    h = _diatomic_hess()
    _c, _d, modes_h = zpva.displaced_geometries(h, dq=1.0)
    _c2, _d2, modes_d = zpva.displaced_geometries(h, dq=1.0,
                                                  masses_amu=[zpva.M_D, zpva.M_D])
    assert zpva.largest_cartesian_step(modes_d, 1.0) < zpva.largest_cartesian_step(modes_h, 1.0)


def test_parse_property_energy_and_nmr():
    nmr_out = ("CHEMICAL SHIELDING SUMMARY (ppm)\n"
               "  Nucleus  Element    Isotropic     Anisotropy\n"
               "     0       F          316.922         85.546\n"
               "     1       H           30.123          5.000\n\n")
    assert zpva.parse_property(nmr_out, "nmr_shielding", target="F") == pytest.approx(316.922)
    assert zpva.parse_property(nmr_out, "nmr_shielding", target=1) == pytest.approx(30.123)
    eng_out = "FINAL SINGLE POINT ENERGY      -76.401234567\n"
    assert zpva.parse_property(eng_out, "energy") == pytest.approx(-76.401234567)


def test_substitute_masses():
    base = [zpva.M_H, zpva.M_H, zpva.M_H]
    out = zpva.substitute_masses(base, {0: zpva.M_D, 2: zpva.M_D})
    assert out[0] == zpva.M_D and out[1] == zpva.M_H and out[2] == zpva.M_D


# --------------------------------------------------------------- spec + plan + assemble
def test_parse_isotopologue_spec():
    isos = zpva.parse_isotopologue_spec("6:D,8:D ; 6:D")
    assert isos[0] == ("base", {})                      # base always first
    labels = [lab for lab, _ in isos]
    assert labels == ["base", "6D_8D", "6D"]
    assert isos[1][1] == {6: zpva.M_D, 8: zpva.M_D}
    assert isos[2][1] == {6: zpva.M_D}


def test_parse_isotopologue_spec_numeric_mass_and_empty():
    assert zpva.parse_isotopologue_spec("") == [("base", {})]
    isos = zpva.parse_isotopologue_spec("0:13.00335")
    assert isos[1][1] == {0: pytest.approx(13.00335)}


def _write_diatomic_hess(path):
    H = np.zeros((6, 6))
    H[2, 2] = H[5, 5] = 0.5
    H[2, 5] = H[5, 2] = -0.5
    out = ["$orca_hessian_file", "", "$hessian", "6"]
    cols = list(range(6))
    out.append("   " + "".join("{:>14d}".format(c) for c in cols))
    for r in range(6):
        out.append("{:>6d}".format(r) + "".join("{:>16.10f}".format(H[r, c]) for c in cols))
    out += ["", "$atoms", "2",
            "H  {:.7f}  0.0 0.0 0.0".format(zpva.M_H),
            "H  {:.7f}  0.0 0.0 1.4".format(zpva.M_H),
            "", "$vibrational_frequencies", "6"]
    for i in range(6):
        out.append("{:>6d}  0.0".format(i))
    out += ["", "$end", ""]
    with open(str(path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return str(path)


def test_plan_zpva_structure():
    h = _diatomic_hess()
    geoms, man = zpva.plan_zpva(h, "mol", 1.0, [("base", {})],
                                {"kind": "energy", "target": None}, "X.hess")
    # 1 shared eq + (1 real mode x +/-) = 3 geometries
    assert len(geoms) == 3
    assert geoms[0]["role"] == "eq" and geoms[0]["filename"] == "mol_zpva_eq"
    assert man["eq_filename"] == "mol_zpva_eq"
    assert set(man["isotopologues"]) == {"base"}
    assert len(man["isotopologues"]["base"]["calcs"]) == 2


def test_assemble_zpva_end_to_end(tmp_path):
    hpath = _write_diatomic_hess(tmp_path / "mol.hess")
    h = hess_mod.parse_hess(hpath)
    geoms, man = zpva.plan_zpva(h, "mol", 1.0,
                                zpva.parse_isotopologue_spec("0:D,1:D"),
                                {"kind": "energy", "target": None}, hpath)

    # Synthetic outputs: a symmetric energy well + a small constant-ish gradient.
    energies = {}
    grads = {}
    for g in geoms:
        if g["role"] == "eq":
            energies[g["filename"]] = -76.0
        else:
            energies[g["filename"]] = -76.0 + 0.01      # symmetric +/- => harmonic only
        grads[g["filename"]] = np.full(6, 1e-3)

    def read_out(fn):
        e = energies.get(fn)
        return None if e is None else "FINAL SINGLE POINT ENERGY {:.8f}\n".format(e)

    def read_engrad(fn):
        return grads.get(fn)

    res = zpva.assemble_zpva(man, read_out, read_engrad, hess_path=hpath)
    assert res["missing"] == []
    assert res["P_e"] == pytest.approx(-76.0)
    assert "base" in res["isotopologues"] and "0D_1D" in res["isotopologues"]
    assert res["isotopologues"]["base"]["shift_vs_base"] == pytest.approx(0.0)
    # each isotopologue produced a finite ZPVA-corrected value
    for r in res["isotopologues"].values():
        assert np.isfinite(r["property_zpva"])


def test_assemble_zpva_reports_missing(tmp_path):
    hpath = _write_diatomic_hess(tmp_path / "mol.hess")
    h = hess_mod.parse_hess(hpath)
    _g, man = zpva.plan_zpva(h, "mol", 1.0, [("base", {})],
                             {"kind": "energy", "target": None}, hpath)
    # No outputs available at all -> base can't be assembled.
    res = zpva.assemble_zpva(man, lambda fn: None, lambda fn: None, hess_path=hpath)
    assert "base" in res["missing"] and res["isotopologues"] == {}
