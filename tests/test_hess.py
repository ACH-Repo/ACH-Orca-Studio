"""Tests for core.hess (ORCA .hess parser + normal-mode machinery).

Uses a synthetic two-atom .hess with a single bond-stretch force constant, so the
one real vibrational frequency is analytic and the parser/diagonaliser/projector
can be checked against it (and against an independent numpy diagonalisation).
numpy required (core.hess imports it).

Run:  python -m pytest tests/test_hess.py -q
"""

import numpy as np
import pytest

hess_mod = pytest.importorskip("orca_workbench.core.hess")


def _write_hess(path, symbols, masses, coords_bohr, H, orca_freqs):
    """Emit a minimal but valid ORCA .hess (block-column $hessian, $atoms,
    $vibrational_frequencies)."""
    n = len(symbols)
    ndof = 3 * n
    out = ["$orca_hessian_file", "", "$hessian", str(ndof)]
    c = 0
    while c < ndof:
        cols = list(range(c, min(c + 5, ndof)))
        out.append("      " + "".join("{:>14d}".format(cc) for cc in cols))
        for r in range(ndof):
            out.append("{:>6d}".format(r) + "".join("{:>16.10f}".format(H[r, cc]) for cc in cols))
        c += 5
    out += ["", "$atoms", str(n)]
    for s, m, (x, y, z) in zip(symbols, masses, coords_bohr):
        out.append("{:<3s} {:12.7f}  {:16.10f} {:16.10f} {:16.10f}".format(s, m, x, y, z))
    out += ["", "$vibrational_frequencies", str(ndof)]
    for i, f in enumerate(orca_freqs):
        out.append("{:>6d} {:16.6f}".format(i, f))
    out += ["", "$end", ""]
    with open(str(path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return str(path)


# A diatomic with a single spring along z between the two atoms.
K = 0.5                      # Hartree / Bohr²
D_BOHR = 1.4                 # bond length
M_H = 1.007825
M_D = 2.014102


def _diatomic_H():
    H = np.zeros((6, 6))
    # z-components are indices 2 (atom0) and 5 (atom1)
    H[2, 2] = K
    H[5, 5] = K
    H[2, 5] = -K
    H[5, 2] = -K
    return H


def _analytic_freq_cm(m0, m1, k=K):
    mu = (m0 * m1) / (m0 + m1)                       # amu
    omega_au = np.sqrt(k / (mu * hess_mod.AMU_TO_ME))
    return omega_au * hess_mod.HARTREE_TO_CM


def _make(tmp_path, masses):
    coords = [(0.0, 0.0, 0.0), (0.0, 0.0, D_BOHR)]
    p = _write_hess(tmp_path / "m.hess", ["H", "H"], masses, coords,
                    _diatomic_H(), [0.0] * 6)
    return hess_mod.parse_hess(p)


def test_parse_roundtrip(tmp_path):
    h = _make(tmp_path, [M_H, M_H])
    assert h.symbols == ["H", "H"] and h.natoms == 2 and h.ndof == 6
    assert np.allclose(h.masses, [M_H, M_H])
    assert np.allclose(h.coords_bohr[1], [0.0, 0.0, D_BOHR])
    assert np.allclose(h.hessian, _diatomic_H())
    assert np.allclose(h.coords_ang[1][2], D_BOHR * hess_mod.BOHR_TO_ANG)


def test_unprojected_evals_match_numpy(tmp_path):
    h = _make(tmp_path, [M_H, M_H])
    nm = hess_mod.normal_modes(h, project=False)
    m = h.masses * hess_mod.AMU_TO_ME
    msqrt = np.repeat(np.sqrt(m), 3)
    Hmw = h.hessian / np.outer(msqrt, msqrt)
    assert np.allclose(np.sort(nm["evals"]), np.sort(np.linalg.eigvalsh(Hmw)))


def test_projection_leaves_one_real_mode_at_analytic_freq(tmp_path):
    h = _make(tmp_path, [M_H, M_H])
    nm = hess_mod.normal_modes(h, project=True)
    real = hess_mod.real_mode_indices(nm["freqs_cm"], tol=1.0)
    assert len(real) == 1                              # linear diatomic: 3N-5 = 1
    got = abs(nm["freqs_cm"][real[0]])
    assert abs(got - _analytic_freq_cm(M_H, M_H)) < 1e-2


def test_isotopologue_lowers_stretch(tmp_path):
    """Same Cartesian Hessian, heavier masses -> lower frequency (~1/sqrt(2) for
    H2 -> D2). Exercises the masses_amu override used for isotopologues."""
    h = _make(tmp_path, [M_H, M_H])
    nm_h = hess_mod.normal_modes(h, project=True)
    nm_d = hess_mod.normal_modes(h, masses_amu=[M_D, M_D], project=True)
    f_h = max(abs(f) for f in nm_h["freqs_cm"])
    f_d = max(abs(f) for f in nm_d["freqs_cm"])
    assert f_d < f_h
    assert abs(f_d - _analytic_freq_cm(M_D, M_D)) < 1e-2
    assert abs((f_h / f_d) - np.sqrt(M_D / M_H)) < 1e-3   # ratio = sqrt(mass ratio)


def test_sqrt_factor_constant():
    # Sanity: the cm^-1 conversion prefactor is ~5140.5.
    assert abs(hess_mod.SQRT_FACTOR - 5140.5) < 2.0
