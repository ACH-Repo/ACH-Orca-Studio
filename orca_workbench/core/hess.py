"""ORCA `.hess` parser + normal-mode machinery (for ZPVA / displaced-geometry work).

Pure and UI-free (the golden rule): numpy only, no Tkinter, no I/O beyond reading
the `.hess` text file. Ported from the standalone `hess_tools.py` of the Fim_NMR
¹⁹F isotope-shift study so the app can now build and average ZPVA pipelines in-app.

Targets Python 3.9. numpy is imported at module load, so callers that don't need
ZPVA never import this module (the Workflow node imports it lazily).

Units inside an ORCA `.hess`:
  $hessian                  -> Hartree / Bohr²
  $atoms                    -> mass in amu, coordinates in Bohr
  $vibrational_frequencies  -> cm⁻¹ (already mass-weighted + trans/rot projected
                               by ORCA; used only as a cross-check)
"""

from __future__ import annotations

import numpy as np

# --- physical constants (CODATA-ish) ---------------------------------------
# ω[cm⁻¹] = SQRT_FACTOR * sqrt(λ),  λ = eigenvalue of the amu-mass-weighted
# Hessian expressed in Hartree/Bohr².
#   Hartree = 4.3597447222071e-18 J
#   Bohr    = 5.29177210903e-11 m
#   amu     = 1.66053906660e-27 kg
#   c       = 2.99792458e10 cm/s
_HARTREE = 4.3597447222071e-18
_BOHR = 5.29177210903e-11
_AMU = 1.66053906660e-27
_C_CM = 2.99792458e10
SQRT_FACTOR = np.sqrt(_HARTREE / (_BOHR ** 2 * _AMU)) / (2.0 * np.pi * _C_CM)  # ~5140.5

BOHR_TO_ANG = 0.529177210903
AMU_TO_ME = _AMU / 9.1093837015e-31           # 1822.888...
HARTREE_TO_CM = 219474.6313702                # 1 Hartree in cm⁻¹


class Hessian(object):
    """Parsed contents of an ORCA `.hess` file."""

    def __init__(self, symbols, masses, coords_bohr, hessian, orca_freqs):
        self.symbols = list(symbols)                  # length N
        self.masses = np.asarray(masses, dtype=float)  # amu, length N
        self.coords_bohr = np.asarray(coords_bohr, dtype=float)  # (N,3) Bohr
        self.hessian = np.asarray(hessian, dtype=float)          # (3N,3N) Hartree/Bohr²
        self.orca_freqs = np.asarray(orca_freqs, dtype=float)    # cm⁻¹, length 3N
        self.natoms = len(self.symbols)
        self.ndof = 3 * self.natoms

    @property
    def coords_ang(self):
        return self.coords_bohr * BOHR_TO_ANG


def _read_section(lines, name):
    """Return the lines belonging to section `$name` (header excluded)."""
    out = []
    grab = False
    for ln in lines:
        if ln.strip().startswith("$"):
            grab = ln.strip() == "${}".format(name)
            continue
        if grab:
            out.append(ln)
    return out


def parse_hess(path):
    """Parse an ORCA `.hess` into a Hessian object."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    # --- $atoms : N, then "Sym mass x y z" (coords in Bohr) ---
    atoms_lines = [l for l in _read_section(lines, "atoms") if l.strip()]
    n = int(atoms_lines[0].split()[0])
    symbols, masses, coords = [], [], []
    for l in atoms_lines[1:1 + n]:
        tok = l.split()
        symbols.append(tok[0])
        masses.append(float(tok[1]))
        coords.append([float(tok[2]), float(tok[3]), float(tok[4])])
    ndof = 3 * n

    # --- $hessian : block-column format, up to 5 columns per block ---
    hess_lines = [l for l in _read_section(lines, "hessian") if l.strip()]
    dim = int(hess_lines[0].split()[0])
    if dim != ndof:
        raise ValueError("hessian dim {} != 3N {}".format(dim, ndof))
    H = np.zeros((ndof, ndof))
    i = 1
    while i < len(hess_lines):
        col_idx = [int(c) for c in hess_lines[i].split()]
        i += 1
        for _ in range(ndof):
            tok = hess_lines[i].split()
            row = int(tok[0])
            for k, c in enumerate(col_idx):
                H[row, c] = float(tok[1 + k])
            i += 1

    # --- $vibrational_frequencies (cross-check only) ---
    freq_lines = [l for l in _read_section(lines, "vibrational_frequencies") if l.strip()]
    nf = int(freq_lines[0].split()[0])
    freqs = [float(freq_lines[1 + j].split()[1]) for j in range(nf)]

    return Hessian(symbols, masses, coords, H, freqs)


def normal_modes(hess, masses_amu=None, project=True):
    """Diagonalise the mass-weighted Hessian (everything internal in atomic units).

    masses_amu : optional length-N override to build an isotopologue's modes from
                 the same (mass-independent) Cartesian Hessian.
    project    : project out translations + rotations before diagonalising
                 (reproduces ORCA's reported frequencies).

    Returns a dict with:
      freqs_cm   (ndof,)      signed cm⁻¹ (negative = imaginary)
      omega_au   (ndof,)      angular frequency, atomic units
      mw_evecs   (ndof,ndof)  orthonormal mass-weighted eigenvectors (columns)
      disp_bohr  (ndof,ndof)  column i = Cartesian displacement (Bohr) produced by
                              a unit step (dq=1) in the dimensionless reduced normal
                              coordinate of mode i:  x = M^-1/2 · l · q/√ω
      evals      (ndof,)      eigenvalues (= ω_au²)
    """
    m = (hess.masses if masses_amu is None else np.asarray(masses_amu, dtype=float)) * AMU_TO_ME
    msqrt = np.repeat(np.sqrt(m), 3)            # √m_e, (ndof,)
    Hmw = hess.hessian / np.outer(msqrt, msqrt)  # Hartree/(Bohr²·m_e) = a.u. freq²

    if project:
        P = _trans_rot_projector(hess.coords_bohr, m)
        Hmw = P.T @ Hmw @ P

    evals, evecs = np.linalg.eigh(Hmw)          # ascending; evals = ω_au²
    omega_au = np.sign(evals) * np.sqrt(np.abs(evals))
    freqs = omega_au * HARTREE_TO_CM

    # Cartesian (Bohr) displacement per unit reduced coordinate dq:
    #   Q = q/√ω  (a.u., ħ=1);   x = M^-1/2 · evec · Q
    safe_w = np.where(np.abs(omega_au) > 1e-9, np.abs(omega_au), np.inf)
    disp_bohr = (evecs / msqrt[:, None]) / np.sqrt(safe_w)[None, :]

    return {
        "freqs_cm": freqs,
        "omega_au": omega_au,
        "mw_evecs": evecs,
        "disp_bohr": disp_bohr,
        "evals": evals,
    }


def _trans_rot_projector(coords_bohr, masses):
    """Projector (ndof×ndof) onto the internal (vibrational) subspace."""
    coords_bohr = np.asarray(coords_bohr, dtype=float)
    masses = np.asarray(masses, dtype=float)
    n = len(masses)
    ndof = 3 * n
    msqrt = np.repeat(np.sqrt(masses), 3)
    com = (masses[:, None] * coords_bohr).sum(0) / masses.sum()
    r = coords_bohr - com

    D = np.zeros((ndof, 6))
    # translations (mass-weighted)
    for a in range(3):
        v = np.zeros(ndof)
        v[a::3] = 1.0
        D[:, a] = v * msqrt
    # infinitesimal rotations
    for a in range(n):
        x, y, z = r[a]
        sm = np.sqrt(masses[a])
        D[3 * a:3 * a + 3, 3] += sm * np.array([0.0, -z, y])
        D[3 * a:3 * a + 3, 4] += sm * np.array([z, 0.0, -x])
        D[3 * a:3 * a + 3, 5] += sm * np.array([-y, x, 0.0])

    q, _ = np.linalg.qr(D)
    keep = [i for i in range(6) if np.linalg.norm(D[:, i]) > 1e-8]
    q = q[:, :len(keep)]
    return np.eye(ndof) - q @ q.T


def real_mode_indices(freqs_cm, tol=1.0):
    """Indices of genuine vibrational modes (|frequency| above `tol` cm⁻¹), i.e.
    dropping the (near-)zero translations/rotations. Works on a freqs_cm array."""
    freqs_cm = np.asarray(freqs_cm, dtype=float)
    return [i for i in range(len(freqs_cm)) if abs(freqs_cm[i]) > tol]
