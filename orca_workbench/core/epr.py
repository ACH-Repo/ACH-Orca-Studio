"""Simulate an isotropic (solution) EPR spectrum from computed EPR parameters.

Pure / numpy-free, so it's unit-testable. Given the g-tensor's isotropic value and
the per-nucleus isotropic hyperfine couplings (from `orca_parser.parse_epr`), build
the first-derivative lineshape an EPR spectrometer records, swept in magnetic field
at a fixed microwave frequency (X-band ~9.5 GHz by default).

Scope / assumptions (documented on purpose — this is the common-case "first look",
not a full powder simulation):
  * ISOTROPIC only: uses g_iso + A_iso (no g/A anisotropy, no orientation average).
  * spin-1/2 nuclei: equivalent groups split binomially (exact for 1H, 13C, 19F,
    31P; approximate for I>1/2 like 14N).
  * 100% isotopic abundance: every computed coupling is drawn at full weight, so a
    low-abundance coupling (e.g. 13C, ~1.1%) shows as a full satellite, not scaled.
A full anisotropic/powder simulation would be a separate, heavier routine.
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
        for grp in groups:
            if grp["element"] == el and abs(grp["A"] - a) <= tol_MHz:
                grp["count"] += 1
                break
        else:
            groups.append({"element": el, "A": a, "count": 1})
    groups.sort(key=lambda g: abs(g["A"]), reverse=True)
    return groups


def _binomial_row(n):
    # type: (int) -> List[int]
    """Row n of Pascal's triangle: the (n+1) relative intensities for n equivalent
    spin-1/2 nuclei."""
    row = [1]
    for _ in range(n):
        row = [a + b for a, b in zip([0] + row, row + [0])]
    return row


def stick_lines(groups):
    # type: (List[dict]) -> List[Tuple[float, float]]
    """Isotropic stick spectrum from equivalent groups: (offset_MHz, intensity)
    about the centre, normalised to total intensity 1. Successive binomial
    splitting (spin-1/2)."""
    lines = [(0.0, 1.0)]
    for grp in groups:
        n, a = grp["count"], grp["A"]
        weights = _binomial_row(n)
        total = float(sum(weights))
        new = []
        for off, inten in lines:
            for k, w in enumerate(weights):
                shift = (k - n / 2.0) * a            # m_I from -n/2 .. +n/2
                new.append((off + shift, inten * w / total))
        lines = new
    return lines


def simulate(g_iso, hyperfine, freq_GHz=9.5, linewidth_mT=0.15, npoints=4000,
             tol_MHz=1.0):
    # type: (float, List[dict], float, float, int, float) -> dict
    """Isotropic first-derivative EPR spectrum. Returns a dict with:
      field_mT   : the swept field axis (mT),
      derivative : the derivative-of-Gaussian lineshape (arb. units),
      sticks     : [(field_mT, intensity)] absolute stick positions,
      center_mT  : B0, groups : the equivalent-group list.
    `linewidth_mT` is the Gaussian sigma of each line."""
    groups = equivalent_groups(hyperfine, tol_MHz=tol_MHz)
    sticks_off = stick_lines(groups)
    B0 = center_field_mT(g_iso, freq_GHz)
    f2mT = 1.0 / (g_iso * MHZ_PER_MT)          # MHz offset -> mT
    sticks = [(B0 + off * f2mT, inten) for off, inten in sticks_off]

    lo = min((b for b, _ in sticks), default=B0)
    hi = max((b for b, _ in sticks), default=B0)
    pad = max((hi - lo) * 0.15, 6.0 * linewidth_mT)
    lo -= pad
    hi += pad
    n = max(2, int(npoints))
    sigma = float(linewidth_mT)
    field = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    deriv = []
    for b in field:
        d = 0.0
        for bc, inten in sticks:
            x = (b - bc) / sigma
            if abs(x) < 12.0:                   # skip negligible tails
                d += inten * (-x / sigma) * math.exp(-0.5 * x * x)
        deriv.append(d)
    return {"field_mT": field, "derivative": deriv, "sticks": sticks,
            "center_mT": B0, "groups": groups}
