"""Simulate EPR spectra from computed EPR parameters (g-tensor + hyperfine).

Pure / numpy-free, so it's unit-testable. Two models, both returning a first-
derivative lineshape swept in magnetic field at a fixed microwave frequency
(X-band ~9.5 GHz by default):
  * `simulate(...)`        — ISOTROPIC (solution): g_iso + A_iso, exact multiplet
    splitting; the everyday "first look".
  * `powder_spectrum(...)` — ANISOTROPIC (powder / frozen solution): orientation-
    averages the g PRINCIPAL values + per-nucleus hyperfine PRINCIPAL tensors.

Shared assumptions:
  * general nuclear spin via `twoI` (= 2I): equivalent groups split as the
    multinomial (1+x+...+x^{2I})^n — binomial for spin-1/2 (1H/13C/19F/31P), the
    1:1:1 triplet for 14N, etc.
  * 100% isotopic abundance: every computed coupling is drawn at full weight, so a
    low-abundance coupling (e.g. 13C, ~1.1%) shows as a full satellite, not scaled.
The powder model additionally assumes the g and A principal frames are COINCIDENT
and a first-order (field-swept) resonance condition — the standard first
approximation; relative Euler angles / exact diagonalisation would be a refinement.
"""

import math
from typing import List, Optional, Tuple


# Electron Bohr magneton over Planck's constant, in MHz per millitesla.
# nu[MHz] = g * MHZ_PER_MT * B[mT]  =>  X-band (9.5 GHz), g=2.0023 -> B0 ~ 339 mT.
MHZ_PER_MT = 13.996246


def center_field_mT(g_iso, freq_GHz=9.5):
    # type: (float, float) -> float
    """Resonance field B0 (mT) for `g_iso` at microwave frequency `freq_GHz`."""
    return (freq_GHz * 1000.0) / (g_iso * MHZ_PER_MT)


def mhz_to_mT(a_mhz, g_iso):
    # type: (float, float) -> float
    """A hyperfine coupling A (MHz) expressed as a field splitting (mT) at g_iso."""
    return a_mhz / (g_iso * MHZ_PER_MT)


def equivalent_groups(hyperfine, tol_MHz=1.0):
    # type: (List[dict], float) -> List[dict]
    """Collapse parsed hyperfine nuclei into magnetically-equivalent groups
    [{element, A (MHz), count}], sorted by descending |A|. Nuclei equivalent when
    same element and |A_iso| within `tol_MHz`. Couplings smaller than `tol_MHz` are
    dropped (no resolvable splitting)."""
    groups = []  # type: List[dict]
    for h in hyperfine:
        a = h.get("A_iso")
        if a is None or abs(a) < tol_MHz:
            continue
        el = h.get("element")
        two_I = int(round(2 * h.get("I", 0.5)))
        for grp in groups:
            if (grp["element"] == el and grp.get("twoI", 1) == two_I
                    and abs(grp["A"] - a) <= tol_MHz):
                grp["count"] += 1
                break
        else:
            groups.append({"element": el, "A": a, "count": 1, "twoI": two_I})
    groups.sort(key=lambda g: abs(g["A"]), reverse=True)
    return groups


def _poly_mul(a, b):
    # type: (List[int], List[int]) -> List[int]
    out = [0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                out[i + j] += ai * bj
    return out


def _spin_multiplet(n, two_I):
    # type: (int, int) -> List[int]
    """Relative line intensities for n equivalent nuclei each of spin I (`two_I` =
    2I): coefficients of (1 + x + ... + x^{2I})^n, length n*two_I + 1. two_I=1
    (spin-1/2) reproduces Pascal's triangle row n (the binomial multiplet)."""
    base = [1] * (two_I + 1)
    poly = [1]
    for _ in range(max(0, n)):
        poly = _poly_mul(poly, base)
    return poly


def stick_lines(groups):
    # type: (List[dict]) -> List[Tuple[float, float]]
    """Stick spectrum from equivalent groups: (offset_MHz, intensity) about the
    centre, normalised to total intensity 1. Successive multiplet splitting; each
    group's nuclear spin is grp['twoI'] (= 2I, default 1 = spin-1/2)."""
    lines = [(0.0, 1.0)]
    for grp in groups:
        n, a = grp["count"], grp["A"]
        two_I = grp.get("twoI", 1)
        coeffs = _spin_multiplet(n, two_I)
        total = float(sum(coeffs)) or 1.0
        nI = n * two_I / 2.0
        new = []
        for off, inten in lines:
            for k, c in enumerate(coeffs):
                if not c:
                    continue
                shift = (k - nI) * a                 # m_total from -nI .. +nI
                new.append((off + shift, inten * c / total))
        lines = new
    return lines


def simulate(g_iso, hyperfine, freq_GHz=9.5, linewidth_mT=0.15, npoints=4000,
             tol_MHz=1.0):
    # type: (float, List[dict], float, float, int, float) -> dict
    """Isotropic EPR spectrum. Returns a dict with:
      field_mT   : the swept field axis (mT),
      absorption : the Gaussian absorption lineshape (the integral of the derivative),
      derivative : the first-derivative lineshape (what a CW spectrometer records),
      sticks     : [(field_mT, intensity)] absolute stick positions,
      center_mT  : B0, groups : the equivalent-group list.
    `linewidth_mT` is the Gaussian sigma of each line. The window can also plot the
    2nd derivative by differentiating `derivative`."""
    groups = equivalent_groups(hyperfine, tol_MHz=tol_MHz)
    sticks_off = stick_lines(groups)
    B0 = center_field_mT(g_iso, freq_GHz)
    f2mT = 1.0 / (g_iso * MHZ_PER_MT)          # MHz offset -> mT
    sticks = [(B0 + off * f2mT, inten) for off, inten in sticks_off]

    lo = min((b for b, _ in sticks), default=B0)
    hi = max((b for b, _ in sticks), default=B0)
    # Roomy margins so the signal isn't zoomed hard against the axes.
    pad = max((hi - lo) * 0.30, 14.0 * linewidth_mT)
    lo -= pad
    hi += pad
    n = max(2, int(npoints))
    sigma = float(linewidth_mT)
    field = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    deriv = []
    absorp = []
    for b in field:
        d = 0.0
        a = 0.0
        for bc, inten in sticks:
            x = (b - bc) / sigma
            if abs(x) < 12.0:                   # skip negligible tails
                g = inten * math.exp(-0.5 * x * x)
                a += g
                d += g * (-x / sigma)
        deriv.append(d)
        absorp.append(a)
    return {"field_mT": field, "absorption": absorp, "derivative": deriv,
            "sticks": sticks, "center_mT": B0, "groups": groups}


def _aniso_groups(hyperfine, tol_MHz=1.0):
    # type: (List[dict], float) -> List[dict]
    """Like equivalent_groups but keyed on the full PRINCIPAL hyperfine tensor
    [Ax,Ay,Az] (MHz), for the powder simulation. Nuclei equivalent when same element
    + spin and all three principal A within tol. Drops nuclei whose largest |A| is
    below tol. Falls back to an isotropic [A_iso]*3 if principal values are absent."""
    groups = []  # type: List[dict]
    for h in hyperfine:
        A = h.get("A")
        if not A:
            a_iso = h.get("A_iso")
            if a_iso is None:
                continue
            A = [a_iso, a_iso, a_iso]
        if max(abs(x) for x in A) < tol_MHz:
            continue
        el = h.get("element")
        two_I = int(round(2 * h.get("I", 0.5)))
        for grp in groups:
            if (grp["element"] == el and grp["twoI"] == two_I
                    and all(abs(g0 - a0) <= tol_MHz for g0, a0 in zip(grp["A"], A))):
                grp["count"] += 1
                break
        else:
            groups.append({"element": el, "A": list(A), "count": 1, "twoI": two_I})
    return groups


def powder_spectrum(g, hyperfine, freq_GHz=9.5, linewidth_mT=0.3, n_theta=50,
                    n_phi=100, npoints=3000, tol_MHz=1.0):
    # type: (List[float], List[dict], float, float, int, int, int, float) -> dict
    """Anisotropic (powder / frozen-solution) EPR: the orientation-averaged
    first-derivative spectrum from the g PRINCIPAL values and per-nucleus hyperfine
    PRINCIPAL tensors. For each orientation of the field in the (shared) principal
    frame it computes g_eff and the projected A_eff, builds the multiplet, and bins
    the lines (weighted by sin(theta)); the histogram is Gaussian-broadened and
    differentiated.

    ASSUMES the g and A principal frames are COINCIDENT (the standard first
    approximation; ORCA can report relative Euler angles for a refined model) and
    uses a first-order (field-swept) resonance condition. Returns the same dict
    shape as simulate(), plus mode='powder'."""
    gx, gy, gz = g[0], g[1], g[2]
    groups = _aniso_groups(hyperfine, tol_MHz=tol_MHz)
    nu = freq_GHz * 1000.0                    # MHz
    K = MHZ_PER_MT

    lines = []  # (field_mT, weight)
    for it in range(max(1, n_theta)):
        theta = math.pi * (it + 0.5) / n_theta
        st, ct = math.sin(theta), math.cos(theta)
        w_theta = st                          # solid-angle weight
        for ip in range(max(1, n_phi)):
            phi = 2.0 * math.pi * (ip + 0.5) / n_phi
            nx, ny, nz = st * math.cos(phi), st * math.sin(phi), ct
            geff = math.sqrt((gx * nx) ** 2 + (gy * ny) ** 2 + (gz * nz) ** 2)
            if geff <= 0:
                continue
            B0 = nu / (geff * K)
            f2mT = 1.0 / (geff * K)
            ori = []
            for grp in groups:
                ax, ay, az = grp["A"]
                aeff = math.sqrt((ax * nx) ** 2 + (ay * ny) ** 2 + (az * nz) ** 2)
                ori.append({"count": grp["count"], "A": aeff, "twoI": grp["twoI"]})
            for off, inten in stick_lines(ori):
                lines.append((B0 + off * f2mT, inten * w_theta))

    n = max(2, int(npoints))
    if not lines:
        B0 = center_field_mT(sum(g) / 3.0, freq_GHz)
        return {"field_mT": [B0], "derivative": [0.0], "absorption": [0.0],
                "center_mT": B0, "groups": groups, "mode": "powder"}

    lo = min(f for f, _ in lines)
    hi = max(f for f, _ in lines)
    pad = max((hi - lo) * 0.15, 12.0 * linewidth_mT)   # roomy margins
    lo -= pad
    hi += pad
    dB = (hi - lo) / (n - 1)
    absorp = [0.0] * n
    for f, w in lines:
        b = int(round((f - lo) / dB))
        if 0 <= b < n:
            absorp[b] += w

    # Gaussian broaden the binned absorption, then take the field derivative.
    sb = max(1e-6, linewidth_mT / dB)
    half = min(int(4.0 * sb) + 1, n)
    kernel = [math.exp(-0.5 * (k / sb) ** 2) for k in range(-half, half + 1)]
    ksum = sum(kernel) or 1.0
    kernel = [k / ksum for k in kernel]
    smooth = [0.0] * n
    for i in range(n):
        ai = absorp[i]
        if ai == 0.0:
            continue
        for kk in range(-half, half + 1):
            j = i + kk
            if 0 <= j < n:
                smooth[j] += ai * kernel[kk + half]
    deriv = [0.0] * n
    for i in range(1, n - 1):
        deriv[i] = (smooth[i + 1] - smooth[i - 1]) / (2.0 * dB)
    field = [lo + i * dB for i in range(n)]
    return {"field_mT": field, "derivative": deriv, "absorption": smooth,
            "center_mT": center_field_mT(sum(g) / 3.0, freq_GHz),
            "groups": groups, "mode": "powder"}


# Nuclear gyromagnetic ratio gamma/2pi in MHz/T for the most common NMR-active
# isotope of each element — used for ENDOR (the nuclear Larmor frequency at the
# resonance field, nu_n = gamma * B). Signs dropped (only |nu_n| matters for line
# positions). Extend as needed.
_NUCLEAR_GAMMA_MHZ_PER_T = {
    "H": 42.5774, "D": 6.53566, "C": 10.7084, "N": 3.0777, "O": 5.7742,
    "F": 40.0776, "P": 17.2515, "S": 3.2717, "B": 13.6626, "Si": 8.4655,
    "Cl": 4.1765, "Na": 11.2686, "Al": 11.1031, "Li": 16.5471, "Se": 8.157,
    "Cu": 11.319, "Mn": 10.5763, "Co": 10.077, "V": 11.2133, "Fe": 1.3758,
}


def nuclear_larmor_MHz(element, field_mT):
    # type: (str, float) -> Optional[float]
    """Nuclear Larmor frequency (MHz) for `element` at `field_mT`, or None if the
    element's gyromagnetic ratio isn't tabulated."""
    g = _NUCLEAR_GAMMA_MHZ_PER_T.get(element)
    if g is None:
        return None
    return abs(g) * field_mT / 1000.0


def endor_lines(g_iso, hyperfine, freq_GHz=9.5, tol_MHz=1.0):
    # type: (float, List[dict], float, float) -> List[dict]
    """ENDOR line positions (RF frequency, MHz) straight from the hyperfine data —
    NO new calculation. For each coupled nucleus at the resonance field B0, the two
    ENDOR lines sit at |nu_n +/- A/2| (nu_n = nuclear Larmor freq, A = isotropic
    hyperfine). Returns [{element, A_iso, nu_n, lines:[f_lo,f_hi], count}], one per
    magnetically-equivalent group, skipping elements with no tabulated gamma."""
    B0 = center_field_mT(g_iso, freq_GHz)
    out = []
    for grp in equivalent_groups(hyperfine, tol_MHz=tol_MHz):
        nu_n = nuclear_larmor_MHz(grp["element"], B0)
        if nu_n is None:
            continue
        a2 = grp["A"] / 2.0
        out.append({"element": grp["element"], "A_iso": grp["A"], "nu_n": nu_n,
                    "lines": sorted([abs(nu_n + a2), abs(nu_n - a2)]),
                    "count": grp["count"]})
    return out


def endor_spectrum(g_iso, hyperfine, freq_GHz=9.5, linewidth_MHz=0.2, npoints=4000,
                   tol_MHz=1.0):
    # type: (float, List[dict], float, float, int, float) -> dict
    """Isotropic ENDOR spectrum: intensity vs RF frequency (MHz). Gaussian-broadened
    sticks at |nu_n +/- A/2| for each coupled nucleus. Returns freq_MHz, absorption,
    derivative, sticks [(freq,intensity)], lines (from endor_lines), B0_mT."""
    lines = endor_lines(g_iso, hyperfine, freq_GHz=freq_GHz, tol_MHz=tol_MHz)
    B0 = center_field_mT(g_iso, freq_GHz)
    sticks = []
    for L in lines:
        for f in L["lines"]:
            sticks.append((f, float(L["count"])))
    n = max(2, int(npoints))
    if not sticks:
        return {"freq_MHz": [0.0, 1.0], "absorption": [0.0, 0.0],
                "derivative": [0.0, 0.0], "sticks": [], "lines": lines, "B0_mT": B0}
    lo = min(f for f, _ in sticks)
    hi = max(f for f, _ in sticks)
    pad = max((hi - lo) * 0.20, 10.0 * linewidth_MHz, 1.0)
    lo = max(0.0, lo - pad)
    hi += pad
    sigma = float(linewidth_MHz)
    freq = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    absorp = []
    deriv = []
    for x in freq:
        a = 0.0
        d = 0.0
        for fc, inten in sticks:
            t = (x - fc) / sigma
            if abs(t) < 12.0:
                gg = inten * math.exp(-0.5 * t * t)
                a += gg
                d += gg * (-t / sigma)
        absorp.append(a)
        deriv.append(d)
    return {"freq_MHz": freq, "absorption": absorp, "derivative": deriv,
            "sticks": sticks, "lines": lines, "B0_mT": B0}
