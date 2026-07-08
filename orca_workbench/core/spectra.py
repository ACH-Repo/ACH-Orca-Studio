"""Pure spectrum math: line broadening and NMR shift conversion.

No numpy, no matplotlib, no Tkinter — just lists of floats, so it's trivially
unit-testable and importable even where numpy is broken. The UI layer
(ui/spectra.py) turns these into matplotlib plots.

Broadening: each stick line (center, intensity) is convolved with a Lorentzian
(default) or Gaussian of the given FWHM and summed onto an evenly spaced grid.
For the handful of peaks and few hundred grid points in a spectrum this is
instant in pure Python.
"""

import math
from typing import List, Optional, Tuple


def lorentzian(x, x0, fwhm):
    # type: (float, float, float) -> float
    half = fwhm / 2.0
    return (half * half) / ((x - x0) ** 2 + half * half)


def gaussian(x, x0, fwhm):
    # type: (float, float, float) -> float
    sigma = fwhm / 2.3548200450309493  # FWHM -> sigma
    return math.exp(-((x - x0) ** 2) / (2.0 * sigma * sigma))


def broaden(centers, intensities, x_min, x_max, n=1200, fwhm=10.0, shape="lorentzian"):
    # type: (List[float], List[float], float, float, int, float, str) -> Tuple[List[float], List[float]]
    """Return (xs, ys): the summed broadened spectrum on an evenly spaced grid.
    `intensities` may be None -> all ones (e.g. for NMR sticks)."""
    n = max(2, int(n))
    if intensities is None:
        intensities = [1.0] * len(centers)
    kernel = gaussian if shape == "gaussian" else lorentzian
    step = (x_max - x_min) / (n - 1)
    xs = [x_min + step * i for i in range(n)]
    ys = [0.0] * n
    for c, inten in zip(centers, intensities):
        if inten == 0:
            continue
        for i in range(n):
            ys[i] += inten * kernel(xs[i], c, fwhm)
    return xs, ys


def adaptive_grid(centers, x_min, x_max, fwhm, coarse=400, points_per_fwhm=12,
                  window=14.0):
    # type: (List[float], float, float, float, int, int, float) -> List[float]
    """A non-uniform evaluation grid: a coarse uniform baseline across
    [x_min, x_max] PLUS a dense cluster (points_per_fwhm points per FWHM) within
    ±window·FWHM of every center inside the range.

    A uniform grid sized to the whole range under-samples sharp lines — an NMR
    peak of FWHM 0.2 ppm on a 500 ppm axis lands on ~1 grid point and renders as a
    jagged triangle when you zoom in. Seeding dense points around each center keeps
    peaks crisp at any zoom without a huge uniform grid. Returns sorted unique xs."""
    n = max(2, int(coarse))
    step = (x_max - x_min) / (n - 1)
    xs = [x_min + step * i for i in range(n)]
    half = max(window * fwhm, fwhm)
    dense_step = fwhm / max(1, int(points_per_fwhm))
    for c in centers:
        if c < x_min - half or c > x_max + half:
            continue
        lo = max(x_min, c - half)
        hi = min(x_max, c + half)
        m = max(2, int((hi - lo) / dense_step) + 1)
        s = (hi - lo) / (m - 1)
        xs.extend(lo + s * j for j in range(m))
    return sorted(set(xs))


def broaden_at(centers, intensities, xs, fwhm=10.0, shape="lorentzian"):
    # type: (List[float], Optional[List[float]], List[float], float, str) -> Tuple[List[float], List[float]]
    """Broaden sticks onto an ARBITRARY (possibly non-uniform) grid `xs` — the
    companion to `broaden` for use with `adaptive_grid`. Returns (xs, ys)."""
    if intensities is None:
        intensities = [1.0] * len(centers)
    kernel = gaussian if shape == "gaussian" else lorentzian
    ys = []
    for x in xs:
        y = 0.0
        for c, inten in zip(centers, intensities):
            if inten:
                y += inten * kernel(x, c, fwhm)
        ys.append(y)
    return xs, ys


def auto_range(centers, pad_frac=0.08, min_pad=20.0):
    # type: (List[float], float, float) -> Tuple[float, float]
    """A sensible x-range covering all centers with a little padding."""
    if not centers:
        return (0.0, 1.0)
    lo, hi = min(centers), max(centers)
    span = hi - lo
    pad = max(min_pad, span * pad_frac)
    return (lo - pad, hi + pad)


def nmr_shift(sigma, reference):
    # type: (float, Optional[float]) -> float
    """Chemical shift delta = reference - sigma (ppm). If reference is None,
    returns the raw shielding sigma unchanged (caller labels the axis)."""
    if reference is None:
        return sigma
    return reference - sigma
