"""Zero-point vibrational averaging (ZPVA) of a molecular property.

Pure/UI-free (numpy only). Ported from the Fim_NMR study's `assemble_zpva.py`
(the maths) and `build_zpva_project.py` (the displaced-geometry generation), made
**property-agnostic**: the ZPVA correction works on any scalar property sampled at
mode-displaced geometries (an NMR shielding, an energy, a dipole component, …),
not just the ¹⁹F shielding of the original project.

Theory (second-order, independent-mode) in dimensionless reduced normal
coordinates q (⟨q_i²⟩ = ½):

    ⟨P⟩ − P_e  =  ¼ Σ_i  d²P/dq_i²                     [harmonic curvature]
                +  Σ_i (dP/dq_i) · ⟨q_i⟩               [anharmonic shift]

    ⟨q_i⟩ = −(1/(4 ω_i)) Σ_j  φ_ijj                    [average displacement]
    φ_ijj = d³V/dq_i dq_j²  (semidiagonal cubic, a.u.)

Property derivatives come from P at ±dq along each mode (central differences).
The cubic constants φ_ijj come from projecting the Cartesian gradient at those
same displaced geometries onto the reduced modes:
    g_i(Q) = grad_cart · (dx/dq_i),   φ_ijj = d²g_i / dq_j².

Isotope shifts: average each isotopologue in ITS OWN modes (re-diagonalised from
the shared, mass-independent Cartesian Hessian via core.hess.normal_modes), then
take the difference of the averaged properties.
"""

from __future__ import annotations

import numpy as np

from orca_workbench.core import hess as hess_mod


# masses (amu) handy for H -> D isotope substitution
M_H = 1.007825
M_D = 2.014102


# --------------------------------------------------------------------------
# displaced-geometry generation
# --------------------------------------------------------------------------
def displaced_geometries(hessian, dq=1.0, masses_amu=None, tol=1.0):
    """Build the ±dq displaced geometries for every real vibrational mode.

    Returns (eq_coords_ang, displacements, modes) where
      eq_coords_ang : (N,3) the equilibrium geometry, Ångström
      displacements : list of (mode_index, sign, coords_ang) — coords_ang is (N,3)
      modes         : dict carrying the normal-mode data the assembler needs for
                      THIS mass set: omega_au, disp_bohr, real, freqs_cm.

    Displacements are along the *dimensionless reduced normal coordinate* q, so a
    step dq is mode-independent in amplitude. `masses_amu` (length N) selects an
    isotopologue; None uses the .hess masses.
    """
    nm = hess_mod.normal_modes(hessian, masses_amu=masses_amu, project=True)
    real = hess_mod.real_mode_indices(nm["freqs_cm"], tol)
    coords0 = hessian.coords_ang
    disp = nm["disp_bohr"]                       # (ndof, ndof) Bohr per dq=1
    out = []
    for i in real:
        dx_ang = disp[:, i].reshape(-1, 3) * hess_mod.BOHR_TO_ANG
        for sign in (+1, -1):
            out.append((i, sign, coords0 + sign * dq * dx_ang))
    modes = {"omega_au": nm["omega_au"], "disp_bohr": disp,
             "real": real, "freqs_cm": nm["freqs_cm"]}
    return coords0, out, modes


def largest_cartesian_step(modes, dq=1.0):
    """The biggest single-atom Cartesian displacement (Å) at this dq, so a caller
    can sanity-check the step size before generating hundreds of inputs."""
    disp = modes["disp_bohr"]
    real = modes["real"]
    if not real:
        return 0.0
    return max(np.abs(disp[:, i]).max() for i in real) * hess_mod.BOHR_TO_ANG * dq


# --------------------------------------------------------------------------
# the ZPVA correction (property-agnostic)
# --------------------------------------------------------------------------
def _cubic_phi(g, real, dq):
    """φ[i][j] = d²g_i/dq_j² = (g_i(+j) + g_i(−j)) / dq²   (g_i(0)=0)."""
    phi = {i: {} for i in real}
    for j in real:
        gp, gm = g[j]                 # reduced-mode gradient vectors at ± along j
        for i in real:
            phi[i][j] = (gp[i] + gm[i]) / (dq * dq)
    return phi


def zpva_correction(prop_e, prop_pm, grad_pm, modes, dq=1.0):
    """Compute the ZPVA correction to a scalar property.

    prop_e      : property at the equilibrium geometry (float)
    prop_pm[i]  : (P(+dq mode i), P(−dq mode i))
    grad_pm[i]  : (grad_cart(+dq mode i), grad_cart(−dq mode i)), Hartree/Bohr,
                  each a length-ndof array
    modes       : the dict returned by displaced_geometries (omega_au, disp_bohr,
                  real)

    Returns a dict: harmonic, anharmonic, correction, property_zpva, avg_q.
    """
    real = modes["real"]
    omega = modes["omega_au"]
    disp = modes["disp_bohr"]
    dprop, d2prop, g = {}, {}, {}
    for i in real:
        pp, pm = prop_pm[i]
        dprop[i] = (pp - pm) / (2.0 * dq)
        d2prop[i] = (pp - 2.0 * prop_e + pm) / (dq * dq)
        gp, gm = grad_pm[i]
        # reduced-mode gradient vectors:  g_k = grad_cart · dx/dq_k
        g[i] = (disp.T @ np.asarray(gp), disp.T @ np.asarray(gm))
    phi = _cubic_phi(g, real, dq)
    harm = 0.25 * sum(d2prop[i] for i in real)
    avg_q = {i: -(1.0 / (4.0 * omega[i])) * sum(phi[i][j] for j in real) for i in real}
    anharm = sum(dprop[i] * avg_q[i] for i in real)
    return {"harmonic": harm, "anharmonic": anharm, "correction": harm + anharm,
            "property_zpva": prop_e + harm + anharm, "avg_q": avg_q}


# --------------------------------------------------------------------------
# ORCA output readers (property extraction)
# --------------------------------------------------------------------------
def read_engrad(path):
    """Cartesian gradient (ndof,) in Hartree/Bohr from an ORCA `.engrad`.

    The file is positional once comment (#) lines are dropped:
      [0]      natoms
      [1]      total energy
      [2:2+3N] gradient, one component per line
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        rows = [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    natoms = int(rows[0])
    grad = np.array([float(x) for x in rows[2:2 + 3 * natoms]])
    if grad.size != 3 * natoms:
        raise ValueError("{}: expected {} gradient values, got {}".format(
            path, 3 * natoms, grad.size))
    return grad


def parse_shielding(out_text, element=None, index=None):
    """Isotropic NMR shielding (ppm) of a chosen nucleus from an ORCA .out, or
    None. Select by 0-based `index` (the nucleus number in the shielding table),
    by `element` symbol (first match), or neither (first nucleus listed)."""
    from orca_workbench.core import orca_parser as P
    rows = P.parse_nmr_shieldings(out_text)
    if not rows:
        return None
    if index is not None:
        for r in rows:
            if r["index"] == int(index):
                return r["isotropic_ppm"]
        return None
    if element:
        for r in rows:
            if r["element"].upper() == str(element).upper():
                return r["isotropic_ppm"]
        return None
    return rows[0]["isotropic_ppm"]


# Property registry: a node picks a `kind` and an optional `target` (nucleus
# index/element for NMR). Each returns a scalar or None when absent.
def parse_property(out_text, kind, target=None):
    """Extract a ZPVA-able scalar property from an ORCA .out by kind:
    'nmr_shielding' (target = nucleus index or element), 'energy' (final SP
    energy, Eh), or 'dipole' (total |μ|, Debye)."""
    from orca_workbench.core import orca_parser as P
    if kind == "nmr_shielding":
        idx = None
        elem = None
        if target is not None and str(target).strip() != "":
            if str(target).strip().lstrip("-").isdigit():
                idx = int(target)
            else:
                elem = str(target).strip()
        return parse_shielding(out_text, element=elem, index=idx)
    if kind == "energy":
        m = P._FINAL_E.findall(out_text)
        return float(m[-1]) if m else None
    if kind == "dipole":
        return P.parse_dipole_debye(out_text)
    return None


PROPERTY_KINDS = ("nmr_shielding", "energy", "dipole")


# --------------------------------------------------------------------------
# isotope substitution helper
# --------------------------------------------------------------------------
def substitute_masses(base_masses, substitutions):
    """Return a copy of `base_masses` (amu) with `substitutions` applied.
    `substitutions` maps a 0-based atom index to a new mass in amu (e.g.
    {6: M_D, 8: M_D} to deuterate atoms 6 and 8)."""
    masses = np.asarray(base_masses, dtype=float).copy()
    for idx, mass in (substitutions or {}).items():
        masses[int(idx)] = float(mass)
    return masses


# --------------------------------------------------------------------------
# isotopologue spec + high-level plan / assemble (the two-step builder core)
# --------------------------------------------------------------------------
ISOTOPE_MASSES = {"H": M_H, "D": M_D, "T": 3.0160492777}


def parse_isotopologue_spec(text):
    """Parse a text isotopologue spec into [(label, {atom_index: mass_amu}), ...].

    Format: isotopologues separated by ';'; each is a comma/space list of
    'index:isotope' where isotope is H/D/T or a numeric amu mass. 0-based atom
    indices (matching the .hess / xyz order). The base (no substitution)
    isotopologue is ALWAYS returned first, as the reference for isotope shifts.

      "6:D,8:D ; 6:D"  ->  [("base", {}), ("6D_8D", {6:2.014, 8:2.014}),
                            ("6D", {6:2.014})]
    """
    out = [("base", {})]
    for chunk in (text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        subs, toks = {}, []
        for tok in chunk.replace(",", " ").split():
            if ":" not in tok:
                continue
            idx_s, sym = tok.split(":", 1)
            try:
                idx = int(idx_s)
            except ValueError:
                continue
            sym = sym.strip()
            mass = ISOTOPE_MASSES.get(sym.upper())
            if mass is None:
                try:
                    mass = float(sym)
                except ValueError:
                    continue
            subs[idx] = mass
            toks.append("{}{}".format(idx, sym.upper() if sym.upper() in ISOTOPE_MASSES else sym))
        if subs:
            out.append(("_".join(toks), subs))
    return out


def plan_zpva(hessian, base, dq, isotopologues, property_cfg, hess_path, tol=1.0):
    """Plan the displaced single-points for a ZPVA run (the 'two-step builder'
    first step). Pure: returns (geometries, manifest); the caller writes the xyz
    files / creates the calcs / saves the manifest.

    geometries : list of dicts {filename, isotopologue, mode, sign, coords_ang,
                 role} — one shared equilibrium ('eq') plus, per isotopologue, the
                 ±dq displaced points ('disp'). coords_ang is an (N,3) array.
    manifest   : everything assemble_zpva needs (per-isotopologue masses, real
                 modes, the filename→(mode,sign) map, the property to extract, and
                 the path to the shared .hess).
    """
    eq_name = "{}_zpva_eq".format(base)
    geometries = [{"filename": eq_name, "isotopologue": None, "mode": None,
                   "sign": 0, "coords_ang": hessian.coords_ang, "role": "eq"}]
    manifest = {"base": base, "hess": hess_path, "dq": float(dq),
                "symbols": list(hessian.symbols), "property": dict(property_cfg),
                "eq_filename": eq_name, "isotopologues": {}}
    for label, subs in isotopologues:
        masses = substitute_masses(hessian.masses, subs)
        _coords0, disps, modes = displaced_geometries(hessian, dq=dq, masses_amu=masses, tol=tol)
        calcs = []
        for (mode, sign, coords) in disps:
            tag = "p" if sign > 0 else "m"
            fname = "{}_zpva_{}_m{:02d}_{}".format(base, label, mode, tag)
            geometries.append({"filename": fname, "isotopologue": label,
                               "mode": int(mode), "sign": int(sign),
                               "coords_ang": coords, "role": "disp"})
            calcs.append({"filename": fname, "mode": int(mode), "sign": int(sign)})
        manifest["isotopologues"][label] = {
            "substitutions": {str(k): float(v) for k, v in subs.items()},
            "masses_amu": [float(m) for m in masses],
            "real_modes": [int(i) for i in modes["real"]],
            "freqs_cm": {str(i): float(modes["freqs_cm"][i]) for i in modes["real"]},
            "calcs": calcs}
    return geometries, manifest


def assemble_zpva(manifest, read_out, read_engrad_fn, hess_path=None):
    """Assemble a ZPVA run from finished outputs (the second step). I/O is
    injected so this stays pure/testable:
      read_out(filename)        -> the calc's .out text, or None if missing
      read_engrad_fn(filename)  -> the calc's gradient array, or None if missing

    Returns {P_e, property, isotopologues: {label: {harmonic, anharmonic,
    correction, property_zpva, shift_vs_base}}, missing: [labels]}.
    """
    prop = manifest["property"]
    kind, target = prop.get("kind"), prop.get("target")
    dq = manifest["dq"]
    h = hess_mod.parse_hess(hess_path or manifest["hess"])

    eq_text = read_out(manifest["eq_filename"])
    P_e = parse_property(eq_text, kind, target) if eq_text else None

    res = {"P_e": P_e, "property": prop, "isotopologues": {}, "missing": []}
    for label, iso in manifest["isotopologues"].items():
        real = iso["real_modes"]
        nm = hess_mod.normal_modes(h, masses_amu=iso["masses_amu"], project=True)
        modes = {"omega_au": nm["omega_au"], "disp_bohr": nm["disp_bohr"], "real": real}
        by = {(c["mode"], c["sign"]): c for c in iso["calcs"]}
        prop_pm, grad_pm, ok = {}, {}, (P_e is not None)
        for i in real:
            cp, cm = by.get((i, 1)), by.get((i, -1))
            if not cp or not cm:
                ok = False
                break
            op, om = read_out(cp["filename"]), read_out(cm["filename"])
            pp = parse_property(op, kind, target) if op else None
            pm = parse_property(om, kind, target) if om else None
            gp, gm = read_engrad_fn(cp["filename"]), read_engrad_fn(cm["filename"])
            if pp is None or pm is None or gp is None or gm is None:
                ok = False
                break
            prop_pm[i], grad_pm[i] = (pp, pm), (gp, gm)
        if not ok:
            res["missing"].append(label)
            continue
        res["isotopologues"][label] = zpva_correction(P_e, prop_pm, grad_pm, modes, dq)

    base = res["isotopologues"].get("base")
    if base is not None:
        for r in res["isotopologues"].values():
            r["shift_vs_base"] = r["property_zpva"] - base["property_zpva"]
    return res


# --------------------------------------------------------------------------
# 1-D numerical validation of the anharmonic prefactor (used by the tests)
# --------------------------------------------------------------------------
def selftest(verbose=False):
    """Solve H = (ω/2)(−d²/dq² + q²) + (1/6) φ q³ on a grid, get the exact ⟨q⟩,
    and confirm the formula ⟨q⟩ = −(1/(4ω)) φ. Returns True if it holds."""
    w = 0.01                      # a.u. (~2200 cm⁻¹)
    N = 2001
    q = np.linspace(-12, 12, N)
    h = q[1] - q[0]
    main = np.full(N, -2.0)
    off = np.ones(N - 1)
    lap = (np.diag(main) + np.diag(off, 1) + np.diag(off, -1)) / h ** 2
    T = -(w / 2.0) * lap
    ok = True
    for phi in (-1e-4, -3e-4, 3e-4, 1e-4):
        V = 0.5 * w * q ** 2 + (1.0 / 6.0) * phi * q ** 3
        ev, evec = np.linalg.eigh(T + np.diag(V))
        psi = evec[:, 0]
        psi = psi / np.sqrt(np.sum(psi ** 2) * h)
        q_exact = np.sum(psi ** 2 * q) * h
        q_formula = -(1.0 / (4.0 * w)) * phi
        rel = abs(q_exact - q_formula) / abs(q_formula)
        if verbose:
            print("phi={:+.1e}  <q>_exact={:+.6e}  -(1/4w)phi={:+.6e}  rel={:.2%}".format(
                phi, q_exact, q_formula, rel))
        ok = ok and rel < 0.05
    return bool(ok)
