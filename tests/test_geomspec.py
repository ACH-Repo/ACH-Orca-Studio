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


# ---------------------------------------------------------- geometry-derived values

# A water-like geometry: O + two H. (element, x, y, z), ORCA 0-based indices.
_WATER = [("O", 0.0, 0.0, 0.117),
          ("H", 0.0, 0.757, -0.469),
          ("H", 0.0, -0.757, -0.469)]


def test_measure_bond_angle_dihedral():
    assert abs(G.measure("B", [0, 1], _WATER) - 0.9573) < 0.01     # O-H bond
    assert abs(G.measure("A", [1, 0, 2], _WATER) - 104.5) < 2.0    # H-O-H angle
    # symmetric: the two O-H bonds are equal
    assert abs(G.measure("B", [0, 1], _WATER) - G.measure("B", [0, 2], _WATER)) < 1e-9


def test_eval_value_numbers_and_expressions():
    # plain numbers need no geometry
    assert G.eval_value(1.5) == 1.5
    assert G.eval_value("1.5") == 1.5
    assert G.eval_value("-0.3") == -0.3
    # geometry references
    b = G.measure("B", [0, 1], _WATER)
    assert abs(G.eval_value("B(0,1)", _WATER) - b) < 1e-9
    assert abs(G.eval_value("B(0,1) + 0.5", _WATER) - (b + 0.5)) < 1e-9
    # `current` == the scanned coordinate
    assert abs(G.eval_value("current + 1.5", _WATER, ("B", [0, 1])) - (b + 1.5)) < 1e-9
    assert abs(G.eval_value("current", _WATER, ("B", [0, 1])) - b) < 1e-9


def test_eval_value_errors():
    for bad, kwargs in [("B(0,1)", {}),          # references geometry but none given
                        ("current", {"atoms": _WATER}),   # no scanned coord supplied
                        ("B(0,9)", {"atoms": _WATER}),    # atom index out of range
                        ("2 +", {"atoms": _WATER}),       # syntax error
                        ("__import__('os')", {"atoms": _WATER})]:  # not arithmetic
        try:
            G.eval_value(bad, **kwargs)
            assert False, "expected ValueError for {!r}".format(bad)
        except ValueError:
            pass


def test_is_expr():
    assert not G.is_expr(1.5) and not G.is_expr("1.5") and not G.is_expr("-3")
    assert G.is_expr("current") and G.is_expr("B(0,1)") and G.is_expr("current + 1")


def test_build_resolves_expressions_from_geometry():
    b = G.measure("B", [0, 1], _WATER)
    spec = {"constraints": [{"type": "A", "atoms": [1, 0, 2], "value": "A(1,0,2)"}],
            "scan": {"type": "B", "atoms": [0, 1], "start": "current",
                     "end": "current + 1.5", "steps": 15}}
    inner = G.build_geom_inner(spec, _WATER)
    scan = [l for l in inner.splitlines() if l.strip().startswith("B 0 1")][0]
    nums = [float(x) for x in scan.split("=")[1].split(",")[:2]]
    # tolerance covers the %g 6-sig-fig rounding in the emitted scan line
    assert abs(nums[0] - b) < 1e-4 and abs(nums[1] - (b + 1.5)) < 1e-4
    # the constraint value resolved to the measured angle
    ang = G.measure("A", [1, 0, 2], _WATER)
    assert "{{ A 1 0 2 {:g} C }}".format(ang) in inner


def test_build_without_geometry_raises_on_expression():
    spec = {"constraints": [], "scan": {"type": "B", "atoms": [0, 1],
            "start": "current", "end": "current + 1", "steps": 5}}
    try:
        G.build_geom_inner(spec, None)
        assert False, "expected ValueError (no geometry to resolve 'current')"
    except ValueError:
        pass
    # numeric-only specs still build with no geometry (back-compat)
    assert "B 0 1 =" in G.build_geom_inner(
        {"constraints": [], "scan": {"type": "B", "atoms": [0, 1],
         "start": 1.0, "end": 2.0, "steps": 5}}, None)


def test_validate_expressions_with_geometry():
    # a good expression validates clean when the geometry is supplied
    spec = {"constraints": [], "scan": {"type": "B", "atoms": [0, 1],
            "start": "current", "end": "current + 1.5", "steps": 10}}
    assert G.validate(spec, n_atoms=3, atoms=_WATER) == []
    # without the geometry, an expression is accepted (resolved later at build)
    assert G.validate(spec, n_atoms=3) == []
    # a broken expression is caught when the geometry is present
    bad = {"constraints": [], "scan": {"type": "B", "atoms": [0, 1],
           "start": "B(0,9)", "end": "2.0", "steps": 10}}
    assert G.validate(bad, n_atoms=3, atoms=_WATER)


# ---------------------------------------------------------------- multi-scan
def test_SEVERAL_scans_are_allowed_because_ORCA_ALLOWS_THEM():
    """Measured against ORCA 6.0.1: two `Scan` lines gave a 3 x 3 = 9-point
    relaxed surface scan, with the FIRST line the outer loop and one column
    per coordinate in the surface table. Christian: "can you just make OWB
    allow multiple scans because the entire point of it is being a GUI for
    orca?\""""
    spec = {"constraints": [{"type": "A", "atoms": [0, 1, 2]}],
            "scans": [{"type": "B", "atoms": [0, 1], "start": 1.5,
                       "end": 1.6, "steps": 3},
                      {"type": "D", "atoms": [0, 1, 2, 3], "start": -180,
                       "end": -120, "steps": 3}]}
    inner = G.build_geom_inner(spec)
    assert inner.count("  Scan") == 1, "one Scan block holding both"
    lines = [ln.strip() for ln in inner.splitlines()]
    assert lines.index("B 0 1 = 1.5, 1.6, 3") < \
        lines.index("D 0 1 2 3 = -180, -120, 3"), "declaration order is kept"
    assert G.validate(spec, n_atoms=14) == []
    assert "9 grid points" in G.describe(spec)


def test_a_spec_saved_BEFORE_multi_scan_still_reads():
    """`scans_of` is the one place that knows about the two shapes."""
    old = {"constraints": [], "scan": {"type": "B", "atoms": [0, 1],
                                       "start": 1.5, "end": 3.0, "steps": 10}}
    assert len(G.scans_of(old)) == 1
    assert not G.is_empty(old)
    assert "B 0 1 = 1.5, 3, 10" in G.build_geom_inner(old)
    assert G.validate(old, n_atoms=5) == []
    # ...and `with_scans` writes only the canonical key
    fresh = G.with_scans(old, G.scans_of(old))
    assert "scan" not in fresh and len(fresh["scans"]) == 1


def test_one_coordinate_cannot_be_scanned_twice_or_frozen_and_scanned():
    """Neither is a richer request. Scanning a coordinate twice would ask the
    inner loop to hold the value the outer loop just set, and freezing what
    is scanned is a flat contradiction."""
    twice = {"constraints": [],
             "scans": [{"type": "B", "atoms": [0, 1], "start": 1, "end": 2,
                        "steps": 3},
                       {"type": "B", "atoms": [0, 1], "start": 1, "end": 3,
                        "steps": 4}]}
    assert any("already being scanned" in e
               for e in G.validate(twice, n_atoms=5))
    clash = {"constraints": [{"type": "B", "atoms": [0, 1]}],
             "scans": [{"type": "B", "atoms": [0, 1], "start": 1, "end": 2,
                        "steps": 3}]}
    assert any("also constrained" in e for e in G.validate(clash, n_atoms=5))


def test_an_expression_resolves_per_scan():
    """`current` means THIS scan's own coordinate, so two scans resolve it
    to two different numbers."""
    atoms = [("C", 0.0, 0.0, 0.0), ("C", 1.5, 0.0, 0.0),
             ("C", 1.5, 1.2, 0.0), ("C", 3.0, 1.2, 0.0)]
    spec = {"constraints": [],
            "scans": [{"type": "B", "atoms": [0, 1], "start": "current",
                       "end": "current + 0.5", "steps": 3},
                      {"type": "B", "atoms": [1, 2], "start": "current",
                       "end": "current + 0.5", "steps": 3}]}
    inner = G.build_geom_inner(spec, atoms)
    assert "B 0 1 = 1.5, 2, 3" in inner
    assert "B 1 2 = 1.2, 1.7, 3" in inner
