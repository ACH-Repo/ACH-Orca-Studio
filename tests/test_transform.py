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


def test_flatten_zeros_perpendicular_axis():
    # a non-planar chain -> flatten onto xy sets every z to 0 (planar), x/y untouched
    out = T.flatten(CHAIN, "xy")
    assert np.allclose(out[:, 2], 0.0)
    assert np.allclose(out[:, :2], CHAIN[:, :2])
    # yz -> zero x; xz -> zero y
    assert np.allclose(T.flatten(CHAIN, "yz")[:, 0], 0.0)
    assert np.allclose(T.flatten(CHAIN, "xz")[:, 1], 0.0)
    # subset only: flatten atoms 0 and 1, leave 2 and 3 alone
    sub = T.flatten(CHAIN, "xy", atoms=[0, 1])
    assert sub[0, 2] == 0.0 and sub[1, 2] == 0.0
    assert sub[2, 2] == CHAIN[2, 2] and sub[3, 2] == CHAIN[3, 2]
    with pytest.raises(ValueError):
        T.flatten(CHAIN, "bad")


def test_flatten_via_ops_and_validate():
    out = T.apply_ops(CHAIN_SYMS, CHAIN, [{"op": "flatten", "plane": "xy"}])
    assert np.allclose(np.asarray(out)[:, 2], 0.0)
    assert T.validate_ops([{"op": "flatten", "plane": "xy"}]) == []
    assert T.validate_ops([{"op": "flatten", "plane": "nope"}])
    assert T.validate_ops([{"op": "flatten", "plane": "xy", "atoms": [9]}], n_atoms=4)
    assert "flatten" in T.describe_op({"op": "flatten", "plane": "xy"})


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


# --- inter-molecular placement (the Combine node) --------------------------

# A water: O, then two H. Atom 1 (an H) is the donor hydrogen.
WATER_SYMS = ["O", "H", "H"]
WATER = np.array([[0.0, 0.0, 0.0], [0.9572, 0.0, 0.0], [-0.2400, 0.9267, 0.0]])


def test_signed_axis_vectors():
    assert np.allclose(T._axis_vec("-x"), [-1, 0, 0])
    assert np.allclose(T._axis_vec("+z"), [0, 0, 1])
    assert np.allclose(T._axis_vec("x"), [1, 0, 0])
    with pytest.raises(ValueError):
        T._axis_vec("-q")


def test_snap_fragment_sets_the_exact_distance_and_direction():
    """The H-bond build: donor H of A at 1.9 A from acceptor O of B, along +x."""
    a = WATER.copy()                       # fixed: atom 1 = donor H
    b = WATER.copy() + np.array([50.0, 3.0, -7.0])   # mobile, far away
    moved = T.snap_fragment(a, 1, b, 0, 1.9, "x")
    d = moved[0] - a[1]
    assert np.linalg.norm(d) == pytest.approx(1.9)
    assert np.allclose(d, [1.9, 0.0, 0.0])
    # a translation: the mobile fragment's internal geometry is untouched
    assert np.allclose(_dists(moved), _dists(b))


def test_snap_auto_direction_keeps_the_current_orientation():
    a = WATER.copy()
    b = WATER.copy() + np.array([0.0, 6.0, 0.0])     # sitting +y from A
    moved = T.snap_fragment(a, 0, b, 0, 2.5, None)   # auto: keep +y, fix distance
    d = moved[0] - a[0]
    assert np.linalg.norm(d) == pytest.approx(2.5)
    assert np.allclose(T._unit(d), [0.0, 1.0, 0.0])


def test_snap_negative_axis_and_bad_indices():
    a, b = WATER.copy(), WATER.copy() + 10.0
    moved = T.snap_fragment(a, 0, b, 0, 2.0, "-z")
    assert np.allclose(moved[0] - a[0], [0.0, 0.0, -2.0])
    with pytest.raises(ValueError):
        T.snap_fragment(a, 9, b, 0, 2.0, "x")
    with pytest.raises(ValueError):
        T.snap_fragment(a, 0, b, 9, 2.0, "x")


def test_apply_placements_builds_a_dimer_and_chains():
    frags = [(WATER_SYMS, WATER.copy()),
             (WATER_SYMS, WATER.copy() + 20.0),
             (WATER_SYMS, WATER.copy() - 30.0)]
    placements = [{"op": "snap", "fixed": 0, "mobile": 1, "i": 1, "j": 0,
                   "distance": 1.9, "direction": "x"},
                  # chained: fragment 2 hangs off fragment 1
                  {"op": "snap", "fixed": 1, "mobile": 2, "i": 1, "j": 0,
                   "distance": 2.1, "direction": "y"}]
    out = T.apply_placements(frags, placements)
    assert np.linalg.norm(out[1][1][0] - out[0][1][1]) == pytest.approx(1.9)
    assert np.linalg.norm(out[2][1][0] - out[1][1][1]) == pytest.approx(2.1)
    assert np.allclose(out[0][1], WATER)          # the fixed fragment never moves
    syms, xyz = T.combine(out)
    assert len(syms) == 9 and xyz.shape == (9, 3)


def test_disabled_placement_is_skipped():
    frags = [(WATER_SYMS, WATER.copy()), (WATER_SYMS, WATER.copy() + 20.0)]
    p = {"op": "snap", "fixed": 0, "mobile": 1, "i": 1, "j": 0, "distance": 1.9,
         "direction": "x", "enabled": False}
    out = T.apply_placements(frags, [p])
    assert np.allclose(out[1][1], WATER + 20.0)   # untouched


def test_apply_placements_reports_the_offending_position():
    frags = [(WATER_SYMS, WATER.copy()), (WATER_SYMS, WATER.copy() + 5.0)]
    with pytest.raises(ValueError) as e:
        T.apply_placements(frags, [
            {"op": "snap", "fixed": 0, "mobile": 1, "i": 1, "j": 0, "distance": 2.0},
            {"op": "snap", "fixed": 0, "mobile": 5, "i": 0, "j": 0, "distance": 2.0}])
    assert "placement 2" in str(e.value)


def test_validate_placements():
    ok = [{"op": "snap", "fixed": 0, "mobile": 1, "i": 1, "j": 0, "distance": 1.9,
           "direction": "-x"}]
    assert T.validate_placements(ok, n_fragments=2, frag_sizes=[3, 3]) == []
    issues = T.validate_placements([
        {"op": "warp", "fixed": 0, "mobile": 1},
        {"op": "snap", "fixed": 0, "mobile": 0, "i": 0, "j": 0, "distance": 1.0},
        {"op": "snap", "fixed": 0, "mobile": 3, "i": 0, "j": 0, "distance": 1.0},
        {"op": "snap", "fixed": 0, "mobile": 1, "i": 9, "j": 0, "distance": 1.0},
        {"op": "snap", "fixed": 0, "mobile": 1, "i": 0, "j": 0, "distance": -1.0},
        {"op": "snap", "fixed": 0, "mobile": 1, "i": 0, "j": 0, "distance": 1.0,
         "direction": [1, 0]},
    ], n_fragments=2, frag_sizes=[3, 3])
    assert len(issues) == 6
    # a disabled placement is never an issue
    assert T.validate_placements([{"op": "warp", "enabled": False}], n_fragments=2) == []


def test_describe_placement():
    text = T.describe_placement({"op": "snap", "fixed": 0, "mobile": 1, "i": 1,
                                 "j": 0, "distance": 1.9, "direction": "x"})
    assert "1.9" in text and "frag 1[0]" in text and "frag 0[1]" in text


def test_align_bisector_points_the_lone_pairs():
    """The H-O-H bisector goes along the target axis, so the lone-pair side faces
    the other way — and atom j (the centre) doesn't move."""
    out = T.align_bisector(WATER, 1, 0, 2, "x")
    b = T.bisector(out, 1, 0, 2)
    assert np.allclose(b, [1, 0, 0], atol=1e-9)
    assert np.allclose(out[0], WATER[0])                 # the O stayed put
    assert np.allclose(_dists(out), _dists(WATER))       # rigid
    # both hydrogens end up on the +x side, lone pairs towards -x
    assert out[1][0] > 0 and out[2][0] > 0
    flipped = T.align_bisector(WATER, 1, 0, 2, "-x")
    assert np.allclose(T.bisector(flipped, 1, 0, 2), [-1, 0, 0], atol=1e-9)


def test_align_bisector_errors():
    with pytest.raises(ValueError):
        T.bisector(WATER, 1, 0, 9)                       # out of range
    linear = np.array([[-1.0, 0, 0], [0.0, 0, 0], [1.0, 0, 0]])
    with pytest.raises(ValueError):
        T.bisector(linear, 0, 1, 2)                      # opposite bonds: undefined


def test_align_bisector_through_the_ops_interpreter():
    op = {"op": "align_bisector", "i": 1, "j": 0, "k": 2, "target": "x"}
    assert T.validate_ops([op], n_atoms=3) == []
    assert "bisector" in T.describe_op(op)
    out = T.apply_ops(WATER_SYMS, WATER, [op])
    assert np.allclose(T.bisector(out, 1, 0, 2), [1, 0, 0], atol=1e-9)
    assert T.validate_ops([{"op": "align_bisector", "i": 1, "j": 1, "k": 2}], n_atoms=3)


def test_hbond_dimer_recipe_gives_a_real_hydrogen_bond():
    """The recipe that ORCA confirms: donor O-H along +x, ACCEPTOR BISECTOR along
    +x (so its lone pairs face -x, back at the donor), snapped at 1.95 A.

    Verified against real ORCA 6.0.1 (HF/def2-SVP): -5.1 kcal/mol, i.e. a proper
    water dimer. Aligning the acceptor's O-H instead of its bisector gives a
    near-unbound -0.5 kcal/mol arrangement, which is why align_bisector exists."""
    donor = T.apply_ops(WATER_SYMS, WATER, [{"op": "align_axis", "i": 0, "j": 1,
                                             "target": "x"}])
    acceptor = T.apply_ops(WATER_SYMS, WATER, [{"op": "align_bisector", "i": 1, "j": 0,
                                                "k": 2, "target": "x"}])
    frags = T.apply_placements([(WATER_SYMS, donor), (WATER_SYMS, acceptor)],
                               [{"op": "snap", "fixed": 0, "mobile": 1, "i": 1, "j": 0,
                                 "distance": 1.95, "direction": "x"}])
    donor_h, acc_o = frags[0][1][1], frags[1][1][0]
    assert np.linalg.norm(acc_o - donor_h) == pytest.approx(1.95)
    # near-linear O-H...O (the hallmark of a hydrogen bond)
    v1 = donor_h - frags[0][1][0]
    v2 = acc_o - donor_h
    cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
    assert cos > 0.999
    # the acceptor's hydrogens point AWAY from the donor, not at it
    assert frags[1][1][1][0] > acc_o[0] and frags[1][1][2][0] > acc_o[0]
    assert T.min_distance(frags[0][1], frags[1][1]) == pytest.approx(1.95, abs=1e-6)


def test_full_hbond_workflow_align_then_snap():
    """The end-to-end recipe: orient each monomer with ordinary Transform ops,
    then snap them together into a water dimer.

    Both monomers get their O->H along +x: the donor's H then points AT the
    partner, and the acceptor's O leads with its lone-pair side (its own H
    points away, downstream). Aligning the acceptor's O-H to -x instead would
    aim its hydrogen straight back at the donor H — an H...H contact, not an
    H bond — which is exactly the mistake the clash check warns about."""
    a = T.apply_ops(WATER_SYMS, WATER, [{"op": "align_axis", "i": 0, "j": 1,
                                         "target": "x"}])
    b = T.apply_ops(WATER_SYMS, WATER, [{"op": "align_axis", "i": 0, "j": 1,
                                         "target": "x"}])
    frags = T.apply_placements([(WATER_SYMS, a), (WATER_SYMS, b)],
                               [{"op": "snap", "fixed": 0, "mobile": 1, "i": 1,
                                 "j": 0, "distance": 1.9, "direction": "x"}])
    donor_h, acceptor_o = frags[0][1][1], frags[1][1][0]
    assert np.linalg.norm(acceptor_o - donor_h) == pytest.approx(1.9)
    # nothing else got closer than the H bond itself: a sane, non-clashing dimer
    assert T.min_distance(frags[0][1], frags[1][1]) == pytest.approx(1.9, abs=1e-6)
    # ... and both monomers kept their internal geometry
    assert np.allclose(_dists(frags[0][1]), _dists(WATER), atol=1e-9)
    assert np.allclose(_dists(frags[1][1]), _dists(WATER), atol=1e-9)


def test_disabled_op_is_skipped_but_keeps_its_place():
    """Unticking one op of a chain must change only that step's contribution —
    the rest still run, in order, and the op stays in the list."""
    base = [{"op": "translate", "vec": [1.0, 0.0, 0.0]},
            {"op": "translate", "vec": [0.0, 2.0, 0.0]},
            {"op": "translate", "vec": [0.0, 0.0, 3.0]}]
    all_on = T.apply_ops(CHAIN_SYMS, CHAIN, base)
    assert np.allclose(all_on - CHAIN, [1.0, 2.0, 3.0])
    middle_off = [dict(base[0]), dict(base[1], enabled=False), dict(base[2])]
    got = T.apply_ops(CHAIN_SYMS, CHAIN, middle_off)
    assert np.allclose(got - CHAIN, [1.0, 0.0, 3.0])
    assert len(middle_off) == 3                       # nothing was deleted
    # explicit enabled=True behaves like a missing key (the default)
    assert np.allclose(T.apply_ops(CHAIN_SYMS, CHAIN,
                                   [dict(o, enabled=True) for o in base]), all_on)


def test_op_enabled_defaults_to_true_for_legacy_ops():
    assert T.op_enabled({"op": "translate"}) is True   # written before the flag existed
    assert T.op_enabled({"op": "translate", "enabled": False}) is False
    ops = [{"op": "a"}, {"op": "b", "enabled": False}, {"op": "c", "enabled": True}]
    assert [o["op"] for o in T.enabled_ops(ops)] == ["a", "c"]


def test_disabled_ops_are_not_validated():
    # a broken op parked (unticked) in the list must not block the graph
    bad = {"op": "align_axis", "i": 0, "j": 99}
    assert T.validate_ops([bad], n_atoms=4)
    assert T.validate_ops([dict(bad, enabled=False)], n_atoms=4) == []


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


def _hexagon(z=0.0):
    return np.array([[math.cos(math.radians(60 * i)), math.sin(math.radians(60 * i)), z]
                     for i in range(6)])


def test_kabsch_recovers_a_rigid_motion():
    P = CHAIN
    R = T.rotation_matrix("z", 40.0)
    Q = P.dot(R.T) + np.array([1.0, 2.0, 3.0])
    R2, Pc, Qc = T.kabsch(P, Q)
    assert np.allclose((P - Pc).dot(R2.T) + Qc, Q, atol=1e-9)


def test_align_moiety_superimposes_the_matched_atoms():
    ring = _hexagon()
    tmpl = np.vstack([ring, [[2.0, 0.0, 0.0]]])         # ring + a substituent atom
    R = T.rotation_matrix([1.0, 1.0, 0.2], 55.0)
    mobile = tmpl.dot(R.T) + np.array([4.0, -2.0, 1.0])  # a rigid copy, moved
    out = T.align_moiety(mobile, tmpl, list(range(6)), list(range(6)))
    assert T._rmsd(out[:6], ring) < 1e-6                 # ring lands on the template ring
    assert np.allclose(_dists(out), _dists(mobile), atol=1e-9)   # rigid


def test_align_moiety_resolves_ring_symmetry_with_anchors():
    # A symmetric ring fits many ways; the user's identity correspondence is
    # deliberately rotated by 2, so ONLY the anchor (a substituent) can pick the
    # right cyclic ordering. With the anchor supplied, the transformed anchor must
    # land on the template's.
    ring = _hexagon()
    A_t = np.array([[2.0, 0.0, 0.0]])                    # template anchor at vertex-0 dir
    tmpl = np.vstack([ring, A_t])                        # 7 atoms (anchor at idx 6)
    R = T.rotation_matrix([0.3, 0.5, 1.0], 47.0)
    t = np.array([2.0, -1.0, 0.5])
    moved_ring = ring.dot(R.T) + t
    A_m = A_t.dot(R.T) + t
    perm = [(i + 2) % 6 for i in range(6)]              # relabel: wrong-by-2 ordering
    mobile = np.vstack([moved_ring[perm], A_m])          # ring idx 0..5, anchor idx 6
    out = T.align_moiety(mobile, tmpl, list(range(6)), list(range(6)),
                         anchor_mobile=[6], anchor_ref=[6])
    assert T._rmsd(out[6:7], tmpl[6:7]) < 1e-6           # anchor aligned -> symmetry resolved


def test_align_moiety_validation():
    tpl = [["C", 0, 0, 0], ["C", 1, 0, 0], ["C", 0, 1, 0], ["C", 1, 1, 0]]
    ok = T.validate_ops([{"op": "align_moiety", "template": tpl,
                          "mobile": [0, 1, 2], "ref": [0, 1, 2]}], n_atoms=4)
    assert ok == []
    issues = T.validate_ops([
        {"op": "align_moiety", "template": [], "mobile": [0, 1, 2], "ref": [0, 1, 2]},
        {"op": "align_moiety", "template": tpl, "mobile": [0, 1], "ref": [0, 1]},   # <3
        {"op": "align_moiety", "template": tpl, "mobile": [0, 1, 9], "ref": [0, 1, 2]},
        {"op": "align_moiety", "template": tpl, "mobile": [0, 1, 2], "ref": [0, 1, 9]},
    ], n_atoms=4)
    assert len(issues) >= 4


def test_align_moiety_through_the_interpreter():
    ring = _hexagon()
    tmpl_rows = [["C", float(x), float(y), float(z)] for x, y, z in ring]
    R = T.rotation_matrix("y", 30.0)
    mobile = ring.dot(R.T) + np.array([0.0, 0.0, 5.0])
    op = {"op": "align_moiety", "template": tmpl_rows,
          "mobile": [0, 1, 2, 3, 4, 5], "ref": [0, 1, 2, 3, 4, 5]}
    assert T.validate_ops([op], n_atoms=6) == []
    out = T.apply_ops(["C"] * 6, mobile, [op])
    assert T._rmsd(out, ring) < 1e-6
    assert "moiety" in T.describe_op(op)


def test_moiety_orderings_count_and_shape():
    # A symmetric 6-ring correspondence has 2*k symmetry-equivalent mappings
    # (k cyclic rotations x the reflection), each a permutation of the same atoms.
    orders = T.moiety_orderings([0, 1, 2, 3, 4, 5])
    assert len(orders) == 12
    for o in orders:
        assert sorted(o) == [0, 1, 2, 3, 4, 5]
    assert len(set(tuple(o) for o in orders)) == 12       # all distinct


def test_align_moiety_explicit_ordering_forces_a_fit():
    # Force each candidate orientation and confirm the ring atoms still land on the
    # template (every symmetry op maps the ring onto itself), while at least two
    # orderings put a labelled vertex in DIFFERENT places (they are genuine choices).
    ring = _hexagon()
    tmpl_rows = [["C", float(x), float(y), float(z)] for x, y, z in ring]
    R = T.rotation_matrix([0.2, 1.0, 0.4], 63.0)
    mobile = ring.dot(R.T) + np.array([3.0, 0.0, -2.0])
    n = len(T.moiety_orderings([0, 1, 2, 3, 4, 5]))
    vertex0_positions = []
    for k in range(n):
        op = {"op": "align_moiety", "template": tmpl_rows,
              "mobile": [0, 1, 2, 3, 4, 5], "ref": [0, 1, 2, 3, 4, 5], "ordering": k}
        assert T.validate_ops([op], n_atoms=6) == []
        out = T.apply_ops(["C"] * 6, mobile, [op])
        # every symmetry op maps the ring onto itself: the output point SET equals
        # the template ring (compare column-sorted, which is set-invariant).
        assert np.allclose(np.sort(out, axis=0), np.sort(ring, axis=0), atol=1e-6)
        vertex0_positions.append(tuple(np.round(out[0], 4)))
    assert len(set(vertex0_positions)) > 1                # different orientations differ


def test_align_moiety_ordering_out_of_range_rejected():
    tpl = [["C", 0, 0, 0], ["C", 1, 0, 0], ["C", 0, 1, 0],
           ["C", 1, 1, 0], ["C", 2, 0, 0], ["C", 0, 2, 0]]
    op = {"op": "align_moiety", "template": tpl,
          "mobile": [0, 1, 2, 3, 4, 5], "ref": [0, 1, 2, 3, 4, 5], "ordering": 99}
    issues = T.validate_ops([op], n_atoms=6)
    assert any("orientation" in s for s in issues)
    # describe_op surfaces the chosen orientation as "orient k/N"
    good = dict(op, ordering=2)
    assert "orient 3/12" in T.describe_op(good)


def test_describe_op_strings():
    assert "translate" in T.describe_op({"op": "translate", "vec": [1, 0, 0]})
    assert "D(0,1,2,3)" in T.describe_op({"op": "set_dihedral",
                                          "atoms": [0, 1, 2, 3], "angle": 60})
    assert "0-1" in T.describe_op({"op": "align_axis", "i": 0, "j": 1})
    assert "0.25 along 1-2" in T.describe_op({"op": "center", "atoms": [1, 2], "frac": 0.25})
    assert "midpoint 1-2" in T.describe_op({"op": "center", "atoms": [1, 2]})
    assert "angle to xy" in T.describe_op({"op": "set_plane_angle", "i": 0, "j": 1,
                                           "k": 2, "plane": "xy", "angle": 30})
