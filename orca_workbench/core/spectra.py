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
