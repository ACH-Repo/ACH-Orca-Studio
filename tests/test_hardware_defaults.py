"""Tests for the global cores / memory override helpers (inputs.set_cores /
set_maxcore), used by the Settings > Default cores / memory per job feature.

Run:  python -m pytest tests/test_hardware_defaults.py -q
"""

from orca_workbench.core import inputs


def test_set_maxcore_replaces_existing():
    inp = "! HF STO-3G\n%maxcore 1000\n%pal nprocs 4\nend\n* xyz 0 1\nH 0.0 0.0 0.0\n*\n"
    out = inputs.set_maxcore(inp, 3000)
    assert "%maxcore 3000" in out and "%maxcore 1000" not in out


def test_set_maxcore_inserts_after_keyword_when_absent():
    inp = "! HF STO-3G\n* xyz 0 1\nH 0.0 0.0 0.0\n*\n"
    lines = inputs.set_maxcore(inp, 2000).split("\n")
    assert lines[0] == "! HF STO-3G"
    assert lines[1] == "%maxcore 2000"


def test_set_maxcore_zero_is_noop():
    inp = "! HF\n%maxcore 500\n"
    assert inputs.set_maxcore(inp, 0) == inp


def test_set_cores_overrides_pal():
    # _build_one derives the SLURM core count from parse_cores(inp), so overriding
    # %pal here keeps ORCA and SLURM in sync.
    out = inputs.set_cores("! HF\n%pal nprocs 4\nend\n", 8)
    assert "%pal nprocs 8" in out
    assert inputs.parse_cores(out) == 8
