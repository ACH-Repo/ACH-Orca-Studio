"""Drive ORCA's `orca_plot` to make Gaussian-cube volumetric files from a
converged `.gbw` — post-hoc densities / molecular orbitals.

This module is PURE: it builds the keystroke string that drives the interactive
`orca_plot <file>.gbw -i` wizard, and parses the cube filename back out of its
stdout. The actual subprocess launch lives in the UI.

`orca_plot` is a lightweight post-processor (it grids an already-stored density),
NOT the SCF `orca` engine — so it runs fine *directly on the LiDO3 gateway*
(verified interactively on gw02, ORCA 6.0.1, 2026-06-29), unlike a real
calculation, which must always be sbatch'd. No cluster job needed for a cube.

Menu integers verified on ORCA 6.0.1:
  main menu : 1=type of plot, 2=orbital no, 3=operator(0=alpha/1=beta),
              4=grid intervals, 5=output format, 11=generate, 12=exit
  type submenu (after `1`): 1=molecular orbitals, 2=(scf) electron density,
              3=(scf) spin density, 4=natural orbitals, ...
The default output format is ALREADY Gaussian Cube, so option 5 is never used.
"""

import re
from typing import Optional


# "type of plot" submenu code for each kind we expose.
_TYPE_CODE = {"density": "2", "spin": "3", "mo": "1"}


def plot_stdin(plot_type="density", mo_index=None, operator=0, grid=None):
    # type: (str, Optional[int], int, Optional[int]) -> str
    """The newline-separated keystrokes to feed `orca_plot <gbw> -i` on stdin to
    generate ONE cube and exit.

    plot_type:
      "density" — SCF total electron density (the common case)
      "spin"    — SCF spin density (open-shell only)
      "mo"      — one molecular orbital; pass `mo_index` (0-based) and `operator`
                  1 for the beta / spin-down set.
    grid: number of grid intervals per axis (orca_plot default is 40); higher =
          smoother isosurfaces, bigger file.
    """
    if plot_type not in _TYPE_CODE:
        raise ValueError("plot_type must be one of {}, got {!r}".format(
            sorted(_TYPE_CODE), plot_type))
    keys = []
    if grid:
        keys += ["4", str(int(grid))]              # set grid intervals (default 40)
    keys += ["1", _TYPE_CODE[plot_type]]           # type of plot -> submenu choice
    if plot_type == "mo":
        keys += ["2", str(int(mo_index or 0))]     # which orbital
        if operator:
            keys += ["3", str(int(operator))]      # alpha(0) / beta(1)
    keys += ["11", "12"]                           # generate, then exit
    return "\n".join(keys) + "\n"


_OUTFILE_RE = re.compile(r"Output file:\s*(\S+\.cube)", re.IGNORECASE)


def parse_output_cube(stdout):
    # type: (str) -> Optional[str]
    """The cube filename orca_plot reports (`Output file: ch2o.mo0a.cube`), or
    None. Returns the LAST one if a run produced several."""
    hits = _OUTFILE_RE.findall(stdout)
    return hits[-1] if hits else None
