"""Pure spectrum math (core.spectra): the adaptive grid + arbitrary-grid broaden
that keep sharp NMR lines crisp on a wide axis.

Run:  python -m pytest tests/test_spectra.py -q
"""

from orca_workbench.core import spectra as S


def test_adaptive_grid_densely_samples_sharp_peaks_on_wide_axis():
    # A 0.2 ppm peak on a ~500 ppm axis: a fixed uniform grid gives ~1 point on
    # the peak (jagged); the adaptive grid must seed many.
    centers = [9.13]
    lo, hi = -142.0, 372.0
    grid = S.adaptive_grid(centers, lo, hi, 0.2)
    near = [x for x in grid if abs(x - 9.13) <= 0.5]
    assert len(near) >= 40
    # contrast: the old uniform 1600-point grid barely samples it
    uxs, _ = S.broaden(centers, None, lo, hi, n=1600, fwhm=0.2)
    assert len([x for x in uxs if abs(x - 9.13) <= 0.5]) <= 5


def test_adaptive_grid_spans_range_and_is_sorted_unique():
    grid = S.adaptive_grid([0.0, 100.0], -10.0, 110.0, 0.5)
    assert grid == sorted(grid)
    assert len(grid) == len(set(grid))
    assert grid[0] <= -10.0 + 1e-9 and grid[-1] >= 110.0 - 1e-9


def test_adaptive_grid_ignores_centers_far_outside_range():
    # a center far outside [lo,hi] contributes no dense window
    g_with = S.adaptive_grid([9999.0], 0.0, 10.0, 0.2)
    g_none = S.adaptive_grid([], 0.0, 10.0, 0.2)
    assert g_with == g_none


def test_broaden_at_matches_broaden_on_same_grid():
    centers, intens = [1.0, 5.0], [2.0, 3.0]
    xs = [i * 0.1 for i in range(100)]
    _, ys_at = S.broaden_at(centers, intens, xs, fwhm=0.5)
    # broaden builds its own uniform grid; compare pointwise at the shared xs by
    # evaluating the same kernel sum directly.
    kernel = S.lorentzian
    expected = [sum(w * kernel(x, c, 0.5) for c, w in zip(centers, intens)) for x in xs]
    assert all(abs(a - b) < 1e-9 for a, b in zip(ys_at, expected))


def test_broaden_at_none_intensities_are_unit_sticks():
    xs = [0.0, 1.0, 2.0]
    _, ys = S.broaden_at([1.0], None, xs, fwhm=1.0)
    assert abs(ys[1] - 1.0) < 1e-9        # apex of a unit line at its center
