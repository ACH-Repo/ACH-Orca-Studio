"""Tests for the %geom constraint / relaxed-scan builder (pure, no ORCA).

The ORCA syntax these produce was verified against a real local ORCA 6.0.1 run
(constrained OPT held an O-H bond at 1.10 Å; a relaxed scan produced the expected
coordinate->energy surface).

Run:  python -m pytest tests/test_geomspec.py -q
"""

from orca_workbench.core import geomspec as G
from orca_workbench.core import inputs as I
from orca_workbench.core import orca_parser as P


_SCAN_OUT = """
Some header

The Calculated Surface using the SCF energy
   0.90000000 -74.95492770
   1.00000000 -74.96577405
   1.10000000 -74.95429355
   1.20000000 -74.93028122

SUGGESTED CITATIONS
"""


def test_parse_relaxed_scan_from_out():
    pts = P.parse_relaxed_scan(_SCAN_OUT)
    assert [p["coordinate"] for p in pts] == [0.9, 1.0, 1.1, 1.2]
    assert abs(pts[1]["energy"] - (-74.96577405)) < 1e-9
    assert P.parse_relaxed_scan("no scan here") == []


def test_parse_relaxed_scan_dat():
    dat = "   0.90000000 -74.95492770 \n   1.00000000 -74.96577405 \n"
    pts = P.parse_relaxed_scan_dat(dat)
    assert len(pts) == 2 and pts[0]["coordinate"] == 0.9


def test_constraint_line_variants():
    assert G.constraint_line({"type": "B", "atoms": [0, 1], "value": 1.5}) == "{ B 0 1 1.5 C }"
    assert G.constraint_line({"type": "B", "atoms": [0, 1]}) == "{ B 0 1 C }"
    assert G.constraint_line({"type": "B", "atoms": [0, 1], "value": None}) == "{ B 0 1 C }"
    assert G.constraint_line({"type": "A", "atoms": [0, 1, 2], "value": 109.5}) == "{ A 0 1 2 109.5 C }"
    assert G.constraint_line({"type": "D", "atoms": [0, 1, 2, 3]}) == "{ D 0 1 2 3 C }"
    # a Cartesian freeze never takes a value
    assert G.constraint_line({"type": "C", "atoms": [5], "value": 9}) == "{ C 5 C }"


def test_scan_line():
    assert G.scan_line({"type": "B", "atoms": [0, 1], "start": 1.5, "end": 3.0, "steps": 10}) \
        == "B 0 1 = 1.5, 3, 10"


def test_build_geom_inner_full_and_partial():
    spec = {"constraints": [{"type": "B", "atoms": [0, 1], "value": 1.5},
                            {"type": "D", "atoms": [0, 1, 2, 3]}],
            "scan": {"type": "B", "atoms": [0, 2], "start": 1.0, "end": 2.0, "steps": 5}}
    inner = G.build_geom_inner(spec)
    assert "Constraints" in inner and "{ B 0 1 1.5 C }" in inner and "{ D 0 1 2 3 C }" in inner
    assert "Scan" in inner and "B 0 2 = 1, 2, 5" in inner
    # constraints-only / scan-only
    assert "Scan" not in G.build_geom_inner({"constraints": [{"type": "B", "atoms": [0, 1]}], "scan": None})
    assert "Constraints" not in G.build_geom_inner({"constraints": [], "scan": spec["scan"]})
    assert G.build_geom_inner(None) == "" and G.build_geom_inner(G.empty_spec()) == ""


def test_validate_catches_bad_specs():
    assert G.validate(None) == []
    assert G.validate({"constraints": [{"type": "B", "atoms": [0]}], "scan": None})  # needs 2 atoms
    assert G.validate({"constraints": [{"type": "B", "atoms": [0, 1]}]}, n_atoms=1)  # index out of range
    assert G.validate({"constraints": [{"type": "B", "atoms": [2, 2]}]}, n_atoms=5)  # duplicate atoms
    # scannable / steps
    assert G.validate({"constraints": [], "scan": {"type": "C", "atoms": [0], "start": 1, "end": 2, "steps": 3}})
    assert G.validate({"constraints": [],
                       "scan": {"type": "B", "atoms": [0, 1], "start": 1, "end": 2, "steps": 1}})  # <2 steps
    # a valid spec has no errors
    assert G.validate({"constraints": [{"type": "B", "atoms": [0, 1], "value": 1.5}],
                       "scan": {"type": "B", "atoms": [0, 2], "start": 1, "end": 2, "steps": 5}},
                      n_atoms=3) == []


def test_add_geom_block_new_and_existing():
    inner = G.build_geom_inner({"constraints": [{"type": "B", "atoms": [0, 1], "value": 1.1}],
                                "scan": None})
    # no existing %geom -> add one after the '!' line, before the coords
    base = "! HF STO-3G Opt\n%pal nprocs 1 end\n* xyz 0 1\nO 0 0 0\nH 0 0 1\n*\n"
    out = I.add_geom_block(base, inner)
    assert "%geom" in out and "{ B 0 1 1.1 C }" in out
    assert out.index("%geom") < out.index("* xyz")      # block precedes the coordinates
    assert out.index("! HF") < out.index("%geom")       # and follows the keyword line
    # existing %geom -> sub-blocks spliced in after its opening line
    base2 = "! HF STO-3G Opt\n%geom\n  MaxIter 100\nend\n* xyz 0 1\nO 0 0 0\n*\n"
    out2 = I.add_geom_block(base2, inner)
    assert out2.count("%geom") == 1 and "{ B 0 1 1.1 C }" in out2 and "MaxIter 100" in out2
    # empty inner -> unchanged
    assert I.add_geom_block(base, "") == base


def test_describe():
    assert G.describe(None) == "(none)"
    spec = {"constraints": [{"type": "B", "atoms": [0, 1]}],
            "scan": {"type": "B", "atoms": [0, 2], "start": 1.5, "end": 3.0, "steps": 10}}
    d = G.describe(spec)
    assert "1 constraint" in d and "scan B(0,2)" in d and "x10" in d
