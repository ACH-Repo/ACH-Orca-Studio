"""Geometry transforms — rigid-body ops, alignments, dihedral edits, combining.

The maths behind the Workflow tab's **Transform** and **Combine** nodes (pure
numpy, no UI, no I/O — unit-testable offline like the rest of core/).

Two families of operation:

* **Rigid modifiers** — translate / rotate / center / align. They move a
  molecule as a rigid body and never alter its conformation: principal-axes
  alignment, aligning the axis defined by two atoms to x/y/z, aligning the
  plane (face) defined by three atoms so its normal points along an axis, or
  tilting that plane to a chosen angle from a coordinate plane
  (``set_plane_angle``). ``center`` moves a chosen reference point to the
  origin — the centre of mass/centroid, an atom, or a point a fraction of the
  way along a bond. Between-mol alignment is compositional: align each
  molecule's chosen axis/plane to the SAME global axis and they end up mutually
  aligned — so every transform stays a one-in-one-out operation.
* **Internal-coordinate edits** — currently ``set_dihedral`` (D a b c d ->
  angle): rotates the atoms on the d-side of the b–c bond, which DOES change
  the conformation (deliberately — "set this dihedral to 0°").

Plus ``combine``: concatenate n fragments into one geometry (the Combine node).

A Transform node stores an ordered **ops list** (plain dicts, JSON-able, atom
indices 0-based like core/geomspec); ``apply_ops`` interprets it, ``validate_ops``
checks it, ``describe_op`` renders one op for the UI.

Coordinates are (N, 3) float arrays in Å; ``symbols`` is a length-N list of
element symbols. Angles are degrees everywhere (UI-facing).
"""

import math

import numpy as np

# ---------------------------------------------------------------------------
# Element data (enough for bond detection + mass weighting; sane fallbacks)
# ---------------------------------------------------------------------------
# Covalent radii in Å (Cordero et al. 2008, single-bond values, rounded).
COVALENT_RADII = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84, "C": 0.76,
    "N": 0.71, "O": 0.66, "F": 0.57, "Ne": 0.58, "Na": 1.66, "Mg": 1.41,
    "Al": 1.21, "Si": 1.11, "P": 1.07, "S": 1.05, "Cl": 1.02, "Ar": 1.06,
    "K": 2.03, "Ca": 1.76, "Sc": 1.70, "Ti": 1.60, "V": 1.53, "Cr": 1.39,
    "Mn": 1.39, "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22,
    "Ga": 1.22, "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20, "Kr": 1.16,
    "Rb": 2.20, "Sr": 1.95, "Y": 1.90, "Zr": 1.75, "Nb": 1.64, "Mo": 1.54,
    "Ru": 1.46, "Rh": 1.42, "Pd": 1.39, "Ag": 1.45, "Cd": 1.44, "In": 1.42,
    "Sn": 1.39, "Sb": 1.39, "Te": 1.38, "I": 1.39, "Xe": 1.40, "Cs": 2.44,
    "Ba": 2.15, "W": 1.62, "Re": 1.51, "Os": 1.44, "Ir": 1.41, "Pt": 1.36,
    "Au": 1.36, "Hg": 1.32, "Tl": 1.45, "Pb": 1.46, "Bi": 1.48,
}
_DEFAULT_RADIUS = 1.5

# Standard atomic weights (amu), for center-of-mass / inertia weighting.
ATOMIC_MASSES = {
    "H": 1.008, "He": 4.003, "Li": 6.94, "Be": 9.012, "B": 10.81, "C": 12.011,
    "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180, "Na": 22.990,
    "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974, "S": 32.06,
    "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078, "Sc": 44.956,
    "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845,
    "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38, "Ga": 69.723,
    "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904, "Kr": 83.798,
    "Rb": 85.468, "Sr": 87.62, "Y": 88.906, "Zr": 91.224, "Nb": 92.906,
    "Mo": 95.95, "Ru": 101.07, "Rh": 102.906, "Pd": 106.42, "Ag": 107.868,
    "Cd": 112.414, "In": 114.818, "Sn": 118.710, "Sb": 121.760, "Te": 127.60,
    "I": 126.904, "Xe": 131.293, "Cs": 132.905, "Ba": 137.327, "W": 183.84,
    "Re": 186.207, "Os": 190.23, "Ir": 192.217, "Pt": 195.084, "Au": 196.967,
    "Hg": 200.592, "Tl": 204.38, "Pb": 207.2, "Bi": 208.980,
}
_DEFAULT_MASS = 12.0

_AXES = {"x": np.array([1.0, 0.0, 0.0]),
         "y": np.array([0.0, 1.0, 0.0]),
         "z": np.array([0.0, 0.0, 1.0])}


def _sym(s):
    # "C1" / "c" -> "C"; tolerate labels with digits.
    letters = "".join(ch for ch in str(s) if ch.isalpha())
    return letters[:2].capitalize() if len(letters) >= 2 and letters[:2].capitalize() \
        in COVALENT_RADII else letters[:1].upper()


def _radius(sym):
    return COVALENT_RADII.get(_sym(sym), _DEFAULT_RADIUS)


def _mass(sym):
    return ATOMIC_MASSES.get(_sym(sym), _DEFAULT_MASS)


def _coords(c):
    a = np.asarray(c, dtype=float)
    if a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("coords must be (N, 3)")
    return a


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return v / n


def _axis_vec(axis):
    """'x'/'y'/'z' or a 3-vector -> unit vector."""
    if isinstance(axis, str):
        key = axis.strip().lower()
        if key not in _AXES:
            raise ValueError("unknown axis {!r} (use x/y/z or a 3-vector)".format(axis))
        return _AXES[key].copy()
    return _unit(axis)


# ---------------------------------------------------------------------------
# Rigid-body primitives
# ---------------------------------------------------------------------------
def centroid(coords):
    return _coords(coords).mean(axis=0)


def center_of_mass(symbols, coords):
    c = _coords(coords)
    m = np.array([_mass(s) for s in symbols], dtype=float)
    return (c * m[:, None]).sum(axis=0) / m.sum()


def translate(coords, vec):
    return _coords(coords) + np.asarray(vec, dtype=float)


def rotation_matrix(axis, angle_deg):
    """Rodrigues rotation matrix about `axis` (through the origin)."""
    u = _axis_vec(axis)
    t = math.radians(float(angle_deg))
    c, s = math.cos(t), math.sin(t)
    ux, uy, uz = u
    K = np.array([[0.0, -uz, uy], [uz, 0.0, -ux], [-uy, ux, 0.0]])
    return c * np.eye(3) + s * K + (1.0 - c) * np.outer(u, u)


def _resolve_center(center, symbols, coords):
    """None/'centroid' -> centroid; 'com' -> centre of mass; int -> that atom;
    else a literal [x, y, z]."""
    if center is None or (isinstance(center, str) and center == "centroid"):
        return centroid(coords)
    if isinstance(center, str) and center == "com":
        return center_of_mass(symbols, coords)
    if isinstance(center, (int, np.integer)) and not isinstance(center, bool):
        c = _coords(coords)
        if not (0 <= int(center) < len(c)):
            raise ValueError("center atom index {} out of range".format(center))
        return c[int(center)]
    return np.asarray(center, dtype=float)


def rotate(coords, axis, angle_deg, center=None, symbols=None):
    """Rotate all atoms by `angle_deg` about `axis` through `center`."""
    c = _coords(coords)
    o = _resolve_center(center, symbols or [], c)
    R = rotation_matrix(axis, angle_deg)
    return (c - o).dot(R.T) + o


def mirror(coords, plane="xy", center=None, symbols=None):
    """Reflect across the given coordinate plane ('xy' / 'yz' / 'xz') through
    `center` (default: the true origin, so a pre-centered molecule mirrors about
    the coordinate plane itself). NOTE: reflection is an improper operation —
    a chiral molecule becomes its enantiomer."""
    normal_axis = {"xy": 2, "yz": 0, "xz": 1}.get((plane or "xy").strip().lower())
    if normal_axis is None:
        raise ValueError("mirror plane must be xy, yz or xz")
    c = _coords(coords)
    o = (np.zeros(3) if center is None
         else _resolve_center(center, symbols or [], c))
    out = c - o
    out[:, normal_axis] = -out[:, normal_axis]
    return out + o


def flatten(coords, plane="xy", atoms=None):
    """Make the molecule planar by projecting onto a coordinate plane: zero the
    perpendicular axis ('xy' -> z=0, 'yz' -> x=0, 'xz' -> y=0). `atoms` (indices)
    limits it to a subset; default flattens everything. Handy for seeding a planar /
    Cs-symmetric starting geometry — align the molecular plane to a coordinate plane
    (align_plane / align_principal) first, flatten, then optimise with ORCA `UseSym`."""
    axis = {"xy": 2, "yz": 0, "xz": 1}.get((plane or "xy").strip().lower())
    if axis is None:
        raise ValueError("flatten plane must be xy, yz or xz")
    c = _coords(coords).copy()
    if atoms:
        for a in atoms:
            c[int(a), axis] = 0.0
    else:
        c[:, axis] = 0.0
    return c


def rotate_about_atoms(coords, i, j, angle_deg):
    """Rotate the whole molecule by `angle_deg` about the axis THROUGH atoms
    i and j. Position-invariant: the axis rides with the molecule, so the
    result doesn't depend on where the molecule sits relative to the origin
    (atoms i and j stay exactly in place)."""
    c = _coords(coords)
    n = len(c)
    if not (0 <= i < n and 0 <= j < n) or i == j:
        raise ValueError("rotate_about_atoms needs two distinct in-range atoms")
    R = rotation_matrix(c[j] - c[i], angle_deg)
    return (c - c[i]).dot(R.T) + c[i]


def anchor_point(symbols, coords, mode="com", atoms=None, frac=0.5):
    """The reference point a 'center' op moves to the origin: 'com' /
    'centroid', or — when `atoms` is given — one atom's position ([i]) or a
    point along the segment between two ([i, j]). `frac` is the position along
    that segment: 0 = atom i, 1 = atom j, 0.5 = the midpoint (the default, e.g.
    the middle of a bond); any value in [0, 1] is allowed for finer control."""
    c = _coords(coords)
    if atoms:
        idx = [int(a) for a in atoms]
        n = len(c)
        if len(idx) not in (1, 2) or len(set(idx)) != len(idx) \
                or not all(0 <= a < n for a in idx):
            raise ValueError("center atoms must be one index or two distinct "
                             "in-range indices")
        if len(idx) == 1:
            return c[idx[0]]
        t = float(frac)
        if not (0.0 <= t <= 1.0):
            raise ValueError("center fraction must be between 0 and 1")
        return (1.0 - t) * c[idx[0]] + t * c[idx[1]]
    if mode == "centroid":
        return centroid(c)
    return center_of_mass(symbols, c)


def rotation_between(v_from, v_to):
    """The minimal rotation matrix taking v_from onto v_to."""
    a, b = _unit(v_from), _unit(v_to)
    d = float(np.dot(a, b))
    if d > 1.0 - 1e-12:
        return np.eye(3)
    if d < -1.0 + 1e-12:
        # 180°: rotate about any axis perpendicular to a.
        perp = np.cross(a, _AXES["x"] if abs(a[0]) < 0.9 else _AXES["y"])
        return rotation_matrix(perp, 180.0)
    axis = np.cross(a, b)
    angle = math.degrees(math.acos(max(-1.0, min(1.0, d))))
    return rotation_matrix(axis, angle)


# ---------------------------------------------------------------------------
# Alignment (all rigid)
# ---------------------------------------------------------------------------
def align_axis(coords, i, j, target="x"):
    """Rotate so the atom-i -> atom-j axis points along `target`. Atom i stays
    fixed (the rotation is about atom i) — predictable anchoring."""
    c = _coords(coords)
    n = len(c)
    if not (0 <= i < n and 0 <= j < n) or i == j:
        raise ValueError("align_axis needs two distinct in-range atoms")
    R = rotation_between(c[j] - c[i], _axis_vec(target))
    return (c - c[i]).dot(R.T) + c[i]


def plane_normal(coords, i, j, k):
    c = _coords(coords)
    return _unit(np.cross(c[j] - c[i], c[k] - c[i]))


def align_plane(coords, i, j, k, target="z"):
    """Rotate so the normal of the atom (i, j, k) plane (the "face") points
    along `target`. The rotation is about the three atoms' centroid."""
    c = _coords(coords)
    n = len(c)
    if len({i, j, k}) != 3 or not all(0 <= a < n for a in (i, j, k)):
        raise ValueError("align_plane needs three distinct in-range atoms")
    o = (c[i] + c[j] + c[k]) / 3.0
    R = rotation_between(plane_normal(c, i, j, k), _axis_vec(target))
    return (c - o).dot(R.T) + o


_PLANE_NORMAL_AXIS = {"xy": "z", "yz": "x", "xz": "y"}


def plane_angle(coords, i, j, k, ref_plane="xy"):
    """Acute angle in degrees ([0, 90]) between the plane through atoms i, j, k
    and a coordinate plane ('xy' / 'yz' / 'xz'). 0 = the molecular plane is
    parallel to the coordinate plane; 90 = perpendicular to it."""
    ax = _PLANE_NORMAL_AXIS.get((ref_plane or "xy").strip().lower())
    if ax is None:
        raise ValueError("reference plane must be xy, yz or xz")
    n_mol = plane_normal(_coords(coords), i, j, k)
    d = abs(float(np.dot(n_mol, _axis_vec(ax))))
    return math.degrees(math.acos(max(0.0, min(1.0, d))))


def set_plane_angle(coords, i, j, k, ref_plane="xy", angle_deg=0.0):
    """Rigidly rotate (about the i, j, k centroid) so the angle between the
    plane through atoms i, j, k and the coordinate plane `ref_plane`
    ('xy'/'yz'/'xz') equals `angle_deg` (0 = parallel / lying flat in it, 90 =
    perpendicular / standing on edge). This is the planar analogue of
    ``set_dihedral`` but RIGID — it rotates about the two planes' line of
    intersection, so it's the minimal tilt that achieves the requested angle
    and never changes the conformation."""
    c = _coords(coords)
    n = len(c)
    if len({i, j, k}) != 3 or not all(0 <= a < n for a in (i, j, k)):
        raise ValueError("set_plane_angle needs three distinct in-range atoms")
    ax = _PLANE_NORMAL_AXIS.get((ref_plane or "xy").strip().lower())
    if ax is None:
        raise ValueError("reference plane must be xy, yz or xz")
    n_ref = _axis_vec(ax)
    n_mol = plane_normal(c, i, j, k)
    dot = max(-1.0, min(1.0, float(np.dot(n_mol, n_ref))))
    alpha = math.degrees(math.acos(dot))          # normal-to-normal angle [0,180]
    target = float(angle_deg)
    # The plane-plane angle is the ACUTE one ([0,90]); aim the normal-to-normal
    # angle at whichever equivalent (target or 180-target) is nearer the current
    # alpha, so a small requested change is a small rotation.
    tgt_normal = target if alpha <= 90.0 else 180.0 - target
    cross = np.cross(n_mol, n_ref)
    ncross = float(np.linalg.norm(cross))
    if ncross < 1e-9:
        # already parallel: the intersection line is undefined, so tilt about any
        # axis lying in the reference plane (the tilt direction is arbitrary here).
        w = _unit(np.cross(n_ref, _AXES["x"] if abs(n_ref[0]) < 0.9 else _AXES["y"]))
    else:
        w = cross / ncross
    o = (c[i] + c[j] + c[k]) / 3.0
    R = rotation_matrix(w, alpha - tgt_normal)     # +alpha about w takes n_mol onto n_ref
    return (c - o).dot(R.T) + o


def kabsch(P, Q):
    """Optimal rigid superposition of matched point sets P -> Q (both (n,3)).
    Returns (R, Pc, Qc): the rotation R, and the two centroids, such that the
    best-fit image of a point p is ``(p - Pc) @ R.T + Qc``. Proper rotation only
    (the reflection is corrected), so chirality is preserved."""
    P = _coords(P)
    Q = _coords(Q)
    if P.shape != Q.shape or len(P) < 1:
        raise ValueError("kabsch needs two equal-length point sets")
    Pc, Qc = P.mean(axis=0), Q.mean(axis=0)
    H = (P - Pc).T.dot(Q - Qc)
    U, _s, Vt = np.linalg.svd(H)
    d = 1.0 if np.linalg.det(Vt.T.dot(U.T)) >= 0 else -1.0
    R = Vt.T.dot(np.diag([1.0, 1.0, d])).dot(U.T)
    return R, Pc, Qc


def _rmsd(A, B):
    A, B = _coords(A), _coords(B)
    d = A - B
    return float(np.sqrt((d * d).sum(axis=1).mean()))


def _ring_orderings(seq):
    """The dihedral-group set of a ring correspondence: every cyclic rotation of
    `seq` and its reflection (reversed), de-duplicated. These are the mappings a
    symmetric ring can take against a FIXED partner — what we enumerate to resolve
    a phenyl-type ambiguity."""
    k = len(seq)
    out, seen = [], set()
    for shift in range(k):
        rot = list(seq[shift:]) + list(seq[:shift])
        for cand in (rot, list(reversed(rot))):
            t = tuple(cand)
            if t not in seen:
                seen.add(t)
                out.append(cand)
    return out


def moiety_orderings(mobile):
    """The finite, enumerable set of symmetry-equivalent correspondences a ring
    moiety `mobile` can take against a fixed template — every cyclic rotation and
    its reflection (see `_ring_orderings`). Public so the UI can size + step the
    'try all ring orientations' control (there are len(...) of them)."""
    return _ring_orderings([int(i) for i in mobile])


def align_moiety(coords, template_coords, mobile, ref,
                 anchor_mobile=None, anchor_ref=None, ordering=None):
    """Rigidly move the whole molecule so its `mobile` atoms superpose on the
    template's `ref` atoms (Kabsch best fit). `mobile`/`ref` are equal-length,
    0-based index lists (mobile into `coords`, ref into `template_coords`).

    Ring/symmetry resolution: a symmetric substructure (a phenyl ring fits ~7
    ways) has a finite set of equally-valid mobile->ref mappings — one per ring
    symmetry op (`moiety_orderings`). Resolve the ambiguity either way:
      * `anchor_mobile`/`anchor_ref`: pass a few extra matched atoms (e.g. the
        substituent-bearing carbons); every ordering is tried and the one whose
        fit best matches the anchors is kept ("overlay the ring, then minimise
        error to the rest").
      * `ordering`: an explicit index into `moiety_orderings(mobile)` — force that
        exact fit, no search. This is what the 'cycle through the N candidate ring
        alignments in the preview' control drives (step the index, eyeball each).
    With neither it's a single deterministic Kabsch on the order you gave.
    Returns new coords."""
    c = _coords(coords)
    T = _coords(template_coords)
    mobile = [int(i) for i in mobile]
    ref = [int(j) for j in ref]
    am = [int(i) for i in (anchor_mobile or [])]
    ar = [int(j) for j in (anchor_ref or [])]
    if len(mobile) != len(ref):
        raise ValueError("the moiety atom lists must be the same length")
    if len(mobile) < 3:
        raise ValueError("moiety alignment needs at least 3 matched atom pairs")
    if len(set(mobile)) != len(mobile) or len(set(ref)) != len(ref):
        raise ValueError("moiety atom indices must be distinct")
    if len(am) != len(ar):
        raise ValueError("the anchor atom lists must be the same length")
    n, nt = len(c), len(T)
    if not all(0 <= i < n for i in mobile + am):
        raise ValueError("a molecule atom index is out of range")
    if not all(0 <= j < nt for j in ref + ar):
        raise ValueError("a template atom index is out of range")

    Q_moi = T[ref]
    Q_anc = T[ar] if ar else None
    if ordering is not None:
        all_ord = _ring_orderings(mobile)
        orderings = [all_ord[int(ordering) % len(all_ord)]]   # one forced fit
    elif am:
        orderings = _ring_orderings(mobile)
    else:
        orderings = [mobile]
    best = None
    for order in orderings:
        P = c[order + am] if am else c[order]
        Q = np.vstack([Q_moi, Q_anc]) if am else Q_moi
        R, Pc, Qc = kabsch(P, Q)
        if am:                                   # tie-break on the anchors
            score = _rmsd((c[am] - Pc).dot(R.T) + Qc, Q_anc)
        else:
            score = _rmsd((c[order] - Pc).dot(R.T) + Qc, Q_moi)
        if best is None or score < best[0]:
            best = (score, R, Pc, Qc)
    _, R, Pc, Qc = best
    return (c - Pc).dot(R.T) + Qc


def inertia_axes(symbols, coords):
    """(com, axes) — principal axes of the mass-weighted inertia tensor, rows
    sorted by moment ASCENDING (axes[0] = smallest moment = the long axis)."""
    c = _coords(coords)
    m = np.array([_mass(s) for s in symbols], dtype=float)
    com = (c * m[:, None]).sum(axis=0) / m.sum()
    d = c - com
    x, y, z = d[:, 0], d[:, 1], d[:, 2]
    I = np.array([
        [(m * (y * y + z * z)).sum(), -(m * x * y).sum(), -(m * x * z).sum()],
        [-(m * x * y).sum(), (m * (x * x + z * z)).sum(), -(m * y * z).sum()],
        [-(m * x * z).sum(), -(m * y * z).sum(), (m * (x * x + y * y)).sum()],
    ])
    w, v = np.linalg.eigh(I)          # ascending eigenvalues, orthonormal columns
    axes = v.T
    if np.linalg.det(axes) < 0:       # keep it a proper rotation (no reflection)
        axes[2] = -axes[2]
    return com, axes


def align_principal(symbols, coords, order="xyz"):
    """Rigidly rotate (about the COM) so the principal axes line up with the
    lab axes: the LONG axis (smallest moment) goes to order[0], the middle to
    order[1], the short to order[2]. order is a permutation of 'xyz'."""
    letters = list((order or "xyz").strip().lower())
    if sorted(letters) != ["x", "y", "z"]:
        raise ValueError("order must be a permutation of 'xyz'")
    c = _coords(coords)
    com, axes = inertia_axes(symbols, c)
    # Build the target frame: row r of `axes` should map onto lab axis letters[r].
    target = np.array([_AXES[l] for l in letters])   # rows
    R = target.T.dot(axes)                           # R @ axes[r] = target[r]
    return (c - com).dot(R.T) + com


# ---------------------------------------------------------------------------
# Internal coordinates (non-rigid, deliberate conformation edits)
# ---------------------------------------------------------------------------
def bonds(symbols, coords, scale=1.15):
    """Bonded atom pairs by the covalent-radii criterion:
    d(i,j) <= scale * (r_i + r_j)."""
    c = _coords(coords)
    n = len(c)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            cutoff = scale * (_radius(symbols[i]) + _radius(symbols[j]))
            if np.linalg.norm(c[i] - c[j]) <= cutoff:
                out.append((i, j))
    return out


def measure_dihedral(coords, a, b, c, d):
    """Signed dihedral D(a,b,c,d) in degrees (IUPAC sign convention)."""
    p = _coords(coords)
    b1, b2, b3 = p[b] - p[a], p[c] - p[b], p[d] - p[c]
    n1, n2 = np.cross(b1, b2), np.cross(b2, b3)
    m1 = np.cross(n1, _unit(b2))
    x, y = float(np.dot(n1, n2)), float(np.dot(m1, n2))
    return math.degrees(math.atan2(y, x))


def _side_of_bond(symbols, coords, b, c):
    """Atom indices reachable from c without crossing the b–c bond (the set the
    dihedral rotation moves). Raises if b–c sits in a ring (both ends reachable)."""
    adj = {}
    for i, j in bonds(symbols, coords):
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    seen = {c}
    stack = [c]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if cur == c and nxt == b:
                continue                       # don't cross the b-c bond itself
            if nxt == b:
                raise ValueError(
                    "bond {}-{} is inside a ring — the dihedral side can't be "
                    "separated (rigid rotation would tear the ring)".format(b, c))
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def set_dihedral(symbols, coords, a, b, c, d, target_deg):
    """Rotate the atoms on the d-side of the b–c bond so D(a,b,c,d) equals
    `target_deg`. NON-rigid by design (a conformation edit)."""
    p = _coords(coords).copy()
    n = len(p)
    ids = (a, b, c, d)
    if len(set(ids)) != 4 or not all(0 <= t < n for t in ids):
        raise ValueError("set_dihedral needs four distinct in-range atoms")
    moving = _side_of_bond(symbols, p, b, c)
    if a in moving or b in moving:
        raise ValueError("atoms {}/{} ended up on the rotating side — is "
                         "{}-{}-{}-{} really a bonded chain?".format(a, b, a, b, c, d))
    delta = float(target_deg) - measure_dihedral(p, a, b, c, d)
    # Rotating the d-side about the b->c axis by +delta DECREASES the measured
    # dihedral under our sign convention, so rotate about c->b instead.
    R = rotation_matrix(p[b] - p[c], delta)
    o = p[c]
    idx = sorted(moving)
    p[idx] = (p[idx] - o).dot(R.T) + o
    return p


# ---------------------------------------------------------------------------
# Combining fragments
# ---------------------------------------------------------------------------
def combine(fragments):
    """Concatenate [(symbols, coords), ...] into one (symbols, coords) — the
    Combine node. Purely an append: place/rotate fragments with transforms
    FIRST, then combine."""
    if not fragments:
        raise ValueError("nothing to combine")
    symbols, blocks = [], []
    for syms, c in fragments:
        symbols.extend(list(syms))
        blocks.append(_coords(c))
    return symbols, np.vstack(blocks)


def min_distance(coords_a, coords_b):
    """Smallest interatomic distance between two fragments (clash check)."""
    a, b = _coords(coords_a), _coords(coords_b)
    diff = a[:, None, :] - b[None, :, :]
    return float(np.sqrt((diff * diff).sum(axis=2)).min())


# ---------------------------------------------------------------------------
# The ops-list interpreter (what a Transform node's config stores)
# ---------------------------------------------------------------------------
OP_TYPES = ("translate", "rotate", "center", "mirror", "flatten", "align_axis",
            "align_plane", "set_plane_angle", "align_principal", "align_moiety",
            "set_dihedral")


def op_enabled(op):
    # type: (dict) -> bool
    """Whether an op takes part. An op dict without an `enabled` key is ENABLED —
    so every op list written before this existed keeps working, and only a
    deliberately unticked op carries `enabled: False`.

    Disabling instead of deleting is how you see what one step of a long chain
    actually contributes: untick it, preview, tick it back."""
    return bool((op or {}).get("enabled", True))


def enabled_ops(ops):
    # type: (list) -> list
    """Just the ops that are switched on, in order."""
    return [o for o in (ops or []) if op_enabled(o)]


def apply_ops(symbols, coords, ops):
    """Apply an ordered list of op dicts to one geometry; returns new coords.
    Ops marked `enabled: False` are SKIPPED (see op_enabled). Raises ValueError
    with a readable message on a bad op — numbered by its position in the full
    list, so the message matches what the editor shows."""
    c = _coords(coords)
    for k, op in enumerate(ops or []):
        if not op_enabled(op):
            continue
        try:
            c = _apply_one(symbols, c, op or {})
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError("op {} ({}): {}".format(k + 1, (op or {}).get("op", "?"), e))
    return c


def _apply_one(symbols, c, op):
    kind = op.get("op")
    if kind == "translate":
        return translate(c, op.get("vec", [0.0, 0.0, 0.0]))
    if kind == "rotate":
        atoms = op.get("axis_atoms")
        if atoms:
            i, j = [int(t) for t in atoms]
            return rotate_about_atoms(c, i, j, float(op.get("angle", 0.0)))
        return rotate(c, op.get("axis", "z"), float(op.get("angle", 0.0)),
                      center=op.get("center", "centroid"), symbols=symbols)
    if kind == "center":
        return c - anchor_point(symbols, c, op.get("mode", "com"), op.get("atoms"),
                                op.get("frac", 0.5))
    if kind == "mirror":
        return mirror(c, op.get("plane", "xy"), center=op.get("center"),
                      symbols=symbols)
    if kind == "flatten":
        return flatten(c, op.get("plane", "xy"), op.get("atoms"))
    if kind == "align_axis":
        return align_axis(c, int(op["i"]), int(op["j"]), op.get("target", "x"))
    if kind == "align_plane":
        return align_plane(c, int(op["i"]), int(op["j"]), int(op["k"]),
                           op.get("target", "z"))
    if kind == "set_plane_angle":
        return set_plane_angle(c, int(op["i"]), int(op["j"]), int(op["k"]),
                               op.get("plane", "xy"), float(op.get("angle", 0.0)))
    if kind == "align_principal":
        return align_principal(symbols, c, op.get("order", "xyz"))
    if kind == "align_moiety":
        tpl = op.get("template") or []
        tcoords = [[row[-3], row[-2], row[-1]] for row in tpl]
        return align_moiety(c, tcoords, op.get("mobile", []), op.get("ref", []),
                            op.get("anchor_mobile"), op.get("anchor_ref"),
                            ordering=op.get("ordering"))
    if kind == "set_dihedral":
        a, b, cc, d = [int(t) for t in op.get("atoms", [])]
        return set_dihedral(symbols, c, a, b, cc, d, float(op.get("angle", 0.0)))
    raise ValueError("unknown op type {!r}".format(kind))


def validate_ops(ops, n_atoms=None):
    """Static checks on an ops list; returns a list of issue strings (empty = OK).
    `n_atoms` (when known) also range-checks atom indices.

    A DISABLED op is not checked: an unticked op doesn't run, so a half-finished
    one parked in the list must not block the graph."""
    issues = []

    def chk_idx(op, keys, k):
        for key in keys:
            try:
                v = int(op.get(key))
            except (TypeError, ValueError):
                issues.append("op {}: '{}' needs an atom index".format(k + 1, key))
                continue
            if n_atoms is not None and not (0 <= v < n_atoms):
                issues.append("op {}: atom {} out of range (molecule has {} atoms)"
                              .format(k + 1, v, n_atoms))

    for k, op in enumerate(ops or []):
        if not op_enabled(op):
            continue
        kind = (op or {}).get("op")
        if kind not in OP_TYPES:
            issues.append("op {}: unknown type {!r}".format(k + 1, kind))
            continue
        if kind == "translate":
            vec = op.get("vec")
            if not (isinstance(vec, (list, tuple)) and len(vec) == 3):
                issues.append("op {}: translate needs a 3-vector".format(k + 1))
        elif kind == "rotate":
            atoms = op.get("axis_atoms")
            if atoms is not None:
                if not (isinstance(atoms, (list, tuple)) and len(atoms) == 2
                        and atoms[0] != atoms[1]):
                    issues.append("op {}: axis_atoms needs two distinct atom "
                                  "indices".format(k + 1))
                elif n_atoms is not None and not all(
                        0 <= int(a) < n_atoms for a in atoms):
                    issues.append("op {}: axis atom out of range".format(k + 1))
            else:
                ax = op.get("axis", "z")
                if isinstance(ax, str):
                    if ax.strip().lower() not in _AXES:
                        issues.append("op {}: axis must be x/y/z, a 3-vector, or "
                                      "two atoms".format(k + 1))
                elif not (isinstance(ax, (list, tuple)) and len(ax) == 3):
                    issues.append("op {}: axis must be x/y/z, a 3-vector, or "
                                  "two atoms".format(k + 1))
        elif kind == "center":
            atoms = op.get("atoms")
            if atoms is not None:
                if not (isinstance(atoms, (list, tuple)) and len(atoms) in (1, 2)
                        and len(set(atoms)) == len(atoms)):
                    issues.append("op {}: center atoms = one index or two distinct "
                                  "indices".format(k + 1))
                elif n_atoms is not None and not all(
                        0 <= int(a) < n_atoms for a in atoms):
                    issues.append("op {}: center atom out of range".format(k + 1))
            if "frac" in op:
                try:
                    fr = float(op.get("frac"))
                    if not (0.0 <= fr <= 1.0):
                        issues.append("op {}: center fraction must be between 0 and 1"
                                      .format(k + 1))
                except (TypeError, ValueError):
                    issues.append("op {}: center fraction must be a number".format(k + 1))
        elif kind == "mirror":
            if (op.get("plane") or "xy").strip().lower() not in ("xy", "yz", "xz"):
                issues.append("op {}: mirror plane must be xy, yz or xz".format(k + 1))
        elif kind == "flatten":
            if (op.get("plane") or "xy").strip().lower() not in ("xy", "yz", "xz"):
                issues.append("op {}: flatten plane must be xy, yz or xz".format(k + 1))
            atoms = op.get("atoms")
            if atoms and n_atoms is not None:
                for v in atoms:
                    try:
                        if not (0 <= int(v) < n_atoms):
                            issues.append("op {}: atom {} out of range".format(k + 1, v))
                    except (TypeError, ValueError):
                        issues.append("op {}: bad atom index".format(k + 1))
        elif kind == "align_axis":
            chk_idx(op, ("i", "j"), k)
            if op.get("i") == op.get("j"):
                issues.append("op {}: the two atoms must differ".format(k + 1))
        elif kind == "align_plane":
            chk_idx(op, ("i", "j", "k"), k)
            if len({op.get("i"), op.get("j"), op.get("k")}) != 3:
                issues.append("op {}: the three atoms must differ".format(k + 1))
        elif kind == "set_plane_angle":
            chk_idx(op, ("i", "j", "k"), k)
            if len({op.get("i"), op.get("j"), op.get("k")}) != 3:
                issues.append("op {}: the three atoms must differ".format(k + 1))
            if (op.get("plane") or "xy").strip().lower() not in ("xy", "yz", "xz"):
                issues.append("op {}: reference plane must be xy, yz or xz".format(k + 1))
            try:
                ang = float(op.get("angle", 0.0))
                if not (0.0 <= ang <= 90.0):
                    issues.append("op {}: plane angle must be between 0 and 90 degrees"
                                  .format(k + 1))
            except (TypeError, ValueError):
                issues.append("op {}: angle must be a number".format(k + 1))
        elif kind == "align_principal":
            order = (op.get("order") or "xyz").strip().lower()
            if sorted(order) != ["x", "y", "z"]:
                issues.append("op {}: order must be a permutation of xyz".format(k + 1))
        elif kind == "align_moiety":
            tpl = op.get("template") or []
            mob = list(op.get("mobile") or [])
            ref = list(op.get("ref") or [])
            am = list(op.get("anchor_mobile") or [])
            ar = list(op.get("anchor_ref") or [])
            if not tpl:
                issues.append("op {}: align_moiety needs a template geometry".format(k + 1))
            if len(mob) != len(ref):
                issues.append("op {}: the moiety atom lists must match in length".format(k + 1))
            elif len(mob) < 3:
                issues.append("op {}: moiety alignment needs at least 3 matched pairs"
                              .format(k + 1))
            if len(am) != len(ar):
                issues.append("op {}: the anchor atom lists must match in length".format(k + 1))
            if n_atoms is not None:
                for v in mob + am:
                    try:
                        if not (0 <= int(v) < n_atoms):
                            issues.append("op {}: molecule atom {} out of range".format(k + 1, v))
                    except (TypeError, ValueError):
                        issues.append("op {}: bad molecule atom index".format(k + 1))
            for v in ref + ar:
                try:
                    if not (0 <= int(v) < len(tpl)):
                        issues.append("op {}: template atom {} out of range".format(k + 1, v))
                except (TypeError, ValueError):
                    issues.append("op {}: bad template atom index".format(k + 1))
            ordv = op.get("ordering")
            if ordv is not None:
                try:
                    oi = int(ordv)
                    if len(mob) >= 3 and not (0 <= oi < len(moiety_orderings(mob))):
                        issues.append("op {}: ring orientation index out of range".format(k + 1))
                except (TypeError, ValueError):
                    issues.append("op {}: bad ring orientation index".format(k + 1))
        elif kind == "set_dihedral":
            atoms = op.get("atoms")
            if not (isinstance(atoms, (list, tuple)) and len(atoms) == 4):
                issues.append("op {}: set_dihedral needs 4 atom indices".format(k + 1))
            elif len(set(atoms)) != 4:
                issues.append("op {}: the four atoms must differ".format(k + 1))
            elif n_atoms is not None:
                for v in atoms:
                    if not (0 <= int(v) < n_atoms):
                        issues.append("op {}: atom {} out of range".format(k + 1, v))
    return issues


def describe_op(op):
    """One short human line per op (for the node's ops list / summary)."""
    kind = (op or {}).get("op", "?")
    try:
        if kind == "translate":
            v = op.get("vec", [0, 0, 0])
            return "translate by ({:g}, {:g}, {:g})".format(*[float(x) for x in v])
        if kind == "rotate":
            atoms = op.get("axis_atoms")
            if atoms:
                return "rotate {:g} deg about atoms {}-{}".format(
                    float(op.get("angle", 0)), atoms[0], atoms[1])
            ax = op.get("axis", "z")
            ax_s = ax if isinstance(ax, str) else "({:g},{:g},{:g})".format(
                *[float(x) for x in ax])
            return "rotate {:g} deg about {} (center {})".format(
                float(op.get("angle", 0)), ax_s, op.get("center", "centroid"))
        if kind == "center":
            atoms = op.get("atoms")
            if atoms:
                if len(atoms) == 1:
                    where = "atom {}".format(atoms[0])
                else:
                    fr = float(op.get("frac", 0.5))
                    where = ("midpoint {}-{}".format(atoms[0], atoms[1])
                             if abs(fr - 0.5) < 1e-9
                             else "{:g} along {}-{}".format(fr, atoms[0], atoms[1]))
                return "center at origin ({})".format(where)
            return "center at origin ({})".format(op.get("mode", "com"))
        if kind == "mirror":
            return "mirror across the {} plane".format(op.get("plane", "xy"))
        if kind == "flatten":
            atoms = op.get("atoms")
            where = " (atoms {})".format(",".join(str(a) for a in atoms)) if atoms else ""
            return "flatten onto the {} plane{}".format(op.get("plane", "xy"), where)
        if kind == "align_axis":
            return "align atoms {}-{} axis -> {}".format(
                op.get("i"), op.get("j"), op.get("target", "x"))
        if kind == "align_plane":
            return "align plane ({},{},{}) normal -> {}".format(
                op.get("i"), op.get("j"), op.get("k"), op.get("target", "z"))
        if kind == "set_plane_angle":
            return "set plane ({},{},{}) angle to {} = {:g} deg".format(
                op.get("i"), op.get("j"), op.get("k"), op.get("plane", "xy"),
                float(op.get("angle", 0)))
        if kind == "align_principal":
            return "align principal axes -> {}".format(op.get("order", "xyz"))
        if kind == "align_moiety":
            na = len(op.get("anchor_mobile") or [])
            mob = op.get("mobile") or []
            ordv = op.get("ordering")
            if ordv is not None and len(mob) >= 3:
                extra = ", orient {}/{}".format(int(ordv) + 1, len(moiety_orderings(mob)))
            elif na:
                extra = " + {} anchors".format(na)
            else:
                extra = ""
            return "align moiety ({} atoms{}) onto a template".format(len(mob), extra)
        if kind == "set_dihedral":
            return "set dihedral D({}) = {:g} deg".format(
                ",".join(str(a) for a in op.get("atoms", [])), float(op.get("angle", 0)))
    except (TypeError, ValueError):
        pass
    return kind
