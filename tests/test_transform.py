"""Tests for core.transform — rigid ops, alignments, dihedral edits, combine.

Pure numpy, no display, no I/O.  Run:  python -m pytest tests/test_transform.py -q
"""

import math

import numpy as np
import pytest

from orca_workbench.core import transform as T


def _dists(c):
    c = np.asarray(c)
    return np.linalg.norm(c[:, None, :] - c[None, :, :], axis=2)


# A 4-carbon bonded chain (0-1-2-3) with no other contacts — dihedral testbed.
CHAIN_SYMS = ["C", "C", "C", "C"]
CHAIN = np.array([
    [-0.5, 1.4, 0.0],
    [0.0, 0.0, 0.0],
    [1.54, 0.0, 0.0],
    [2.0, 1.2, 0.8],
])


def test_translate_and_centroid():
    c = T.translate(CHAIN, [1.0, -2.0, 0.5])
    assert np.allclose(c[1], [1.0, -2.0, 0.5])
    assert np.allclose(T.centroid(c), T.centroid(CHAIN) + [1.0, -2.0, 0.5])


def test_rotate_90_about_z():
    c = T.rotate(np.array([[1.0, 0.0, 0.0]]), "z", 90.0, center=[0.0, 0.0, 0.0])
    assert np.allclose(c[0], [0.0, 1.0, 0.0], atol=1e-12)


def test_rotate_is_rigid():
    c = T.rotate(CHAIN, [1.0, 2.0, 3.0], 37.5)
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-10)


def test_center_of_mass_weighting():
    # O at origin, H far out: COM must sit close to O.
    syms, c = ["O", "H"], np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    com = T.center_of_mass(syms, c)
    assert com[0] == pytest.approx(1.008 / (15.999 + 1.008), abs=1e-6)


def test_align_axis_points_along_x_and_anchors_i():
    c = T.align_axis(CHAIN, 1, 2, "x")
    assert np.allclose(c[1], CHAIN[1], atol=1e-10)          # atom i fixed
    v = c[2] - c[1]
    assert v[0] > 0 and abs(v[1]) < 1e-10 and abs(v[2]) < 1e-10
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-10)  # rigid


def test_align_plane_normal_to_z():
    c = T.align_plane(CHAIN, 0, 1, 2, "z")
    n = T.plane_normal(c, 0, 1, 2)
    assert abs(abs(float(n[2])) - 1.0) < 1e-10
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-10)


def test_align_principal_linear_molecule_to_x():
    # CO2 rotated arbitrarily; after align, the long axis lies on x.
    syms = ["O", "C", "O"]
    co2 = np.array([[-1.16, 0.0, 0.0], [0.0, 0.0, 0.0], [1.16, 0.0, 0.0]])
    rot = T.rotate(co2, [1.0, 2.0, 3.0], 53.0, center=[0.3, -0.2, 0.7])
    c = T.align_principal(syms, rot, "xyz")
    c = c - T.center_of_mass(syms, c)
    assert np.allclose(c[:, 1:], 0.0, atol=1e-8)            # all on the x axis
    assert np.allclose(_dists(c), _dists(co2), atol=1e-8)


def test_bonds_chain():
    assert T.bonds(CHAIN_SYMS, CHAIN) == [(0, 1), (1, 2), (2, 3)]


def test_set_dihedral_roundtrip():
    for target in (0.0, 60.0, -90.0, 179.0):
        c = T.set_dihedral(CHAIN_SYMS, CHAIN, 0, 1, 2, 3, target)
        assert T.measure_dihedral(c, 0, 1, 2, 3) == pytest.approx(target, abs=1e-8)
        # only the d-side moved; bond lengths survive
        assert np.allclose(c[:3], CHAIN[:3], atol=1e-12)
        d = _dists(c)
        assert d[2][3] == pytest.approx(_dists(CHAIN)[2][3], abs=1e-10)


def test_set_dihedral_in_ring_raises():
    # A 4-ring: rotating about a ring bond can't partition the molecule.
    syms = ["C", "C", "C", "C"]
    ring = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0],
                     [1.5, 1.5, 0.0], [0.0, 1.5, 0.0]])
    with pytest.raises(ValueError):
        T.set_dihedral(syms, ring, 0, 1, 2, 3, 30.0)


def test_combine_concatenates():
    syms, coords = T.combine([
        (["O", "H"], [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        (["N"], [[5.0, 5.0, 5.0]]),
    ])
    assert syms == ["O", "H", "N"]
    assert coords.shape == (3, 3) and np.allclose(coords[2], [5.0, 5.0, 5.0])


def test_min_distance():
    d = T.min_distance([[0.0, 0.0, 0.0]], [[3.0, 4.0, 0.0], [10.0, 0.0, 0.0]])
    assert d == pytest.approx(5.0)


def test_apply_ops_pipeline():
    ops = [
        {"op": "center", "mode": "centroid"},
        {"op": "rotate", "axis": "z", "angle": 90.0, "center": [0.0, 0.0, 0.0]},
        {"op": "translate", "vec": [0.0, 0.0, 4.0]},
    ]
    c = T.apply_ops(CHAIN_SYMS, CHAIN, ops)
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-10)
    assert T.centroid(c)[2] == pytest.approx(4.0)


def test_apply_ops_reports_bad_op_position():
    with pytest.raises(ValueError) as e:
        T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "translate", "vec": [0, 0, 0]},
                                        {"op": "warp"}])
    assert "op 2" in str(e.value)


def test_validate_ops():
    assert T.validate_ops([]) == []
    assert T.validate_ops([{"op": "translate", "vec": [1, 2, 3]}], 4) == []
    issues = T.validate_ops([
        {"op": "warp"},
        {"op": "translate", "vec": [1, 2]},
        {"op": "align_axis", "i": 0, "j": 9},
        {"op": "set_dihedral", "atoms": [0, 1, 2], "angle": 0},
        {"op": "align_principal", "order": "xxy"},
    ], n_atoms=4)
    assert len(issues) == 5


def test_mirror_xy_plane():
    c = T.mirror([[1.0, 2.0, 3.0]], "xy")
    assert np.allclose(c[0], [1.0, 2.0, -3.0])
    # through a custom center: z' = 2*zc - z
    c = T.mirror([[1.0, 2.0, 3.0]], "xy", center=[0.0, 0.0, 1.0])
    assert np.allclose(c[0], [1.0, 2.0, -1.0])
    with pytest.raises(ValueError):
        T.mirror([[0.0, 0.0, 0.0]], "ab")


def test_mirror_is_isometric_but_improper():
    c = T.mirror(CHAIN, "yz")
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-12)   # distances survive
    # the dihedral flips sign — the enantiomer
    assert T.measure_dihedral(c, 0, 1, 2, 3) == pytest.approx(
        -T.measure_dihedral(CHAIN, 0, 1, 2, 3), abs=1e-9)


def test_rotate_about_atom_pair_axis_is_position_invariant():
    ang = 137.0
    a = T.rotate_about_atoms(CHAIN, 1, 2, ang)
    shifted = T.translate(CHAIN, [10.0, -5.0, 2.0])
    b = T.rotate_about_atoms(shifted, 1, 2, ang)
    # same result up to the shift — the axis rides with the molecule
    assert np.allclose(b - [10.0, -5.0, 2.0], a, atol=1e-9)
    # the two axis atoms don't move at all
    assert np.allclose(a[1], CHAIN[1]) and np.allclose(a[2], CHAIN[2])


def test_center_anchored_on_atom_and_midpoint():
    ops = [{"op": "center", "atoms": [1, 2]}]
    c = T.apply_ops(CHAIN_SYMS, CHAIN, ops)
    assert np.allclose((c[1] + c[2]) / 2.0, [0.0, 0.0, 0.0], atol=1e-12)
    c = T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "center", "atoms": [3]}])
    assert np.allclose(c[3], [0.0, 0.0, 0.0], atol=1e-12)


def test_new_ops_through_interpreter_and_validate():
    ops = [
        {"op": "rotate", "axis_atoms": [1, 2], "angle": 90.0},
        {"op": "mirror", "plane": "xz"},
    ]
    assert T.validate_ops(ops, n_atoms=4) == []
    c = T.apply_ops(CHAIN_SYMS, CHAIN, ops)
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-9)
    issues = T.validate_ops([
        {"op": "rotate", "axis_atoms": [1, 9]},
        {"op": "center", "atoms": [0, 0]},
        {"op": "mirror", "plane": "qq"},
    ], n_atoms=4)
    assert len(issues) == 3


def test_dimer_recipe_end_to_end():
    # The carboxylic-dimer style flow: align bond 1-2 to x, set dihedral 0,
    # origin at the 1-2 midpoint, shift up — then the mirrored partner.
    base_ops = [
        {"op": "align_axis", "i": 1, "j": 2, "target": "x"},
        {"op": "set_dihedral", "atoms": [0, 1, 2, 3], "angle": 0.0},
        {"op": "center", "atoms": [1, 2]},
        {"op": "translate", "vec": [0.0, 0.0, 1.0]},
    ]
    top = T.apply_ops(CHAIN_SYMS, CHAIN, base_ops)
    bottom = T.apply_ops(CHAIN_SYMS, CHAIN,
                         base_ops + [{"op": "rotate", "axis": "x",
                                      "angle": 180.0, "center": [0, 0, 0]}])
    syms, dimer = T.combine([(CHAIN_SYMS, top), (CHAIN_SYMS, bottom)])
    assert len(syms) == 8
    # partners sit symmetrically about z=0, 1 A above/below
    assert (top[1][2] + top[2][2]) / 2 == pytest.approx(1.0)
    assert (bottom[1][2] + bottom[2][2]) / 2 == pytest.approx(-1.0)
    assert T.min_distance(top, bottom) > 0.5     # no atom collision


def test_center_fraction_along_bond():
    # frac 0 = atom i, 1 = atom j, 0.25 = a quarter of the way from i to j.
    c = T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "center", "atoms": [1, 2], "frac": 0.0}])
    assert np.allclose(c[1], [0.0, 0.0, 0.0], atol=1e-12)          # atom i at origin
    c = T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "center", "atoms": [1, 2], "frac": 1.0}])
    assert np.allclose(c[2], [0.0, 0.0, 0.0], atol=1e-12)          # atom j at origin
    c = T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "center", "atoms": [1, 2], "frac": 0.25}])
    # the quarter-point from atom 1 toward atom 2 now sits at the origin
    assert np.allclose(0.75 * c[1] + 0.25 * c[2], [0.0, 0.0, 0.0], atol=1e-12)
    # default (no frac) is still the midpoint, and rigid
    c = T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "center", "atoms": [1, 2]}])
    assert np.allclose((c[1] + c[2]) / 2.0, [0.0, 0.0, 0.0], atol=1e-12)
    assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-12)


def test_center_fraction_out_of_range_is_flagged():
    assert T.validate_ops([{"op": "center", "atoms": [1, 2], "frac": 1.5}], 4)
    with pytest.raises(ValueError):
        T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "center", "atoms": [1, 2], "frac": 2.0}])


def test_set_plane_angle_hits_target_and_is_rigid():
    # A non-planar chain; set the (0,1,2) plane's angle to each coordinate plane.
    for ref in ("xy", "yz", "xz"):
        for target in (0.0, 30.0, 45.0, 90.0):
            c = T.apply_ops(CHAIN_SYMS, CHAIN,
                            [{"op": "set_plane_angle", "i": 0, "j": 1, "k": 2,
                              "plane": ref, "angle": target}])
            assert T.plane_angle(c, 0, 1, 2, ref) == pytest.approx(target, abs=1e-7)
            assert np.allclose(_dists(c), _dists(CHAIN), atol=1e-9)   # rigid


def test_set_plane_angle_from_already_parallel():
    # 3 atoms already in the xy-plane (normal ∥ z): tilting to 90 must still work
    # (the intersection line is undefined in the parallel start state).
    syms = ["C", "C", "C"]
    flat = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    assert T.plane_angle(flat, 0, 1, 2, "xy") == pytest.approx(0.0, abs=1e-9)
    c = T.apply_ops(syms, flat, [{"op": "set_plane_angle", "i": 0, "j": 1, "k": 2,
                                  "plane": "xy", "angle": 90.0}])
    assert T.plane_angle(c, 0, 1, 2, "xy") == pytest.approx(90.0, abs=1e-7)


def test_set_plane_angle_validation():
    ok = T.validate_ops([{"op": "set_plane_angle", "i": 0, "j": 1, "k": 2,
                          "plane": "xy", "angle": 45}], n_atoms=4)
    assert ok == []
    issues = T.validate_ops([
        {"op": "set_plane_angle", "i": 0, "j": 0, "k": 2, "plane": "xy", "angle": 45},
        {"op": "set_plane_angle", "i": 0, "j": 1, "k": 2, "plane": "qq", "angle": 45},
        {"op": "set_plane_angle", "i": 0, "j": 1, "k": 2, "plane": "xy", "angle": 120},
        {"op": "set_plane_angle", "i": 0, "j": 1, "k": 9, "plane": "xy", "angle": 45},
    ], n_atoms=4)
    assert len(issues) == 4


def test_describe_op_strings():
    assert "translate" in T.describe_op({"op": "translate", "vec": [1, 0, 0]})
    assert "D(0,1,2,3)" in T.describe_op({"op": "set_dihedral",
                                          "atoms": [0, 1, 2, 3], "angle": 60})
    assert "0-1" in T.describe_op({"op": "align_axis", "i": 0, "j": 1})
    assert "0.25 along 1-2" in T.describe_op({"op": "center", "atoms": [1, 2], "frac": 0.25})
    assert "midpoint 1-2" in T.describe_op({"op": "center", "atoms": [1, 2]})
    assert "angle to xy" in T.describe_op({"op": "set_plane_angle", "i": 0, "j": 1,
                                           "k": 2, "plane": "xy", "angle": 30})
