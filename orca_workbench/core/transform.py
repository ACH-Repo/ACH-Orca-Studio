"""Geometry transforms — rigid-body ops, alignments, dihedral edits, combining.

The maths behind the Workflow tab's **Transform** and **Combine** nodes (pure
numpy, no UI, no I/O — unit-testable offline like the rest of core/).

Two families of operation:

* **Rigid modifiers** — translate / rotate / center / align. They move a
  molecule as a rigid body and never alter its conformation: principal-axes
  alignment, aligning the axis defined by two atoms to x/y/z, aligning the
  plane (face) defined by three atoms so its normal points along an axis.
  Between-mol alignment is compositional: align each molecule's chosen
  axis/plane to the SAME global axis and they end up mutually aligned — so
  every transform stays a one-in-one-out operation.
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


def anchor_point(symbols, coords, mode="com", atoms=None):
    """The reference point a 'center' op moves to the origin: 'com' /
    'centroid', or — when `atoms` is given — one atom's position ([i]) or the
    midpoint of two ([i, j], e.g. the middle of a bond)."""
    c = _coords(coords)
    if atoms:
        idx = [int(a) for a in atoms]
        n = len(c)
        if len(idx) not in (1, 2) or len(set(idx)) != len(idx) \
                or not all(0 <= a < n for a in idx):
            raise ValueError("center atoms must be one index or two distinct "
                             "in-range indices")
        return c[idx].mean(axis=0)
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
OP_TYPES = ("translate", "rotate", "center", "mirror", "align_axis", "align_plane",
            "align_principal", "set_dihedral")


def apply_ops(symbols, coords, ops):
    """Apply an ordered list of op dicts to one geometry; returns new coords.
    Raises ValueError with a readable message on a bad op."""
    c = _coords(coords)
    for k, op in enumerate(ops or []):
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
        return c - anchor_point(symbols, c, op.get("mode", "com"), op.get("atoms"))
    if kind == "mirror":
        return mirror(c, op.get("plane", "xy"), center=op.get("center"),
                      symbols=symbols)
    if kind == "align_axis":
        return align_axis(c, int(op["i"]), int(op["j"]), op.get("target", "x"))
    if kind == "align_plane":
        return align_plane(c, int(op["i"]), int(op["j"]), int(op["k"]),
                           op.get("target", "z"))
    if kind == "align_principal":
        return align_principal(symbols, c, op.get("order", "xyz"))
    if kind == "set_dihedral":
        a, b, cc, d = [int(t) for t in op.get("atoms", [])]
        return set_dihedral(symbols, c, a, b, cc, d, float(op.get("angle", 0.0)))
    raise ValueError("unknown op type {!r}".format(kind))


def validate_ops(ops, n_atoms=None):
    """Static checks on an ops list; returns a list of issue strings (empty = OK).
    `n_atoms` (when known) also range-checks atom indices."""
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
        elif kind == "mirror":
            if (op.get("plane") or "xy").strip().lower() not in ("xy", "yz", "xz"):
                issues.append("op {}: mirror plane must be xy, yz or xz".format(k + 1))
        elif kind == "align_axis":
            chk_idx(op, ("i", "j"), k)
            if op.get("i") == op.get("j"):
                issues.append("op {}: the two atoms must differ".format(k + 1))
        elif kind == "align_plane":
            chk_idx(op, ("i", "j", "k"), k)
            if len({op.get("i"), op.get("j"), op.get("k")}) != 3:
                issues.append("op {}: the three atoms must differ".format(k + 1))
        elif kind == "align_principal":
            order = (op.get("order") or "xyz").strip().lower()
            if sorted(order) != ["x", "y", "z"]:
                issues.append("op {}: order must be a permutation of xyz".format(k + 1))
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
                where = ("atom {}".format(atoms[0]) if len(atoms) == 1
                         else "midpoint {}-{}".format(atoms[0], atoms[1]))
                return "center at origin ({})".format(where)
            return "center at origin ({})".format(op.get("mode", "com"))
        if kind == "mirror":
            return "mirror across the {} plane".format(op.get("plane", "xy"))
        if kind == "align_axis":
            return "align atoms {}-{} axis -> {}".format(
                op.get("i"), op.get("j"), op.get("target", "x"))
        if kind == "align_plane":
            return "align plane ({},{},{}) normal -> {}".format(
                op.get("i"), op.get("j"), op.get("k"), op.get("target", "z"))
        if kind == "align_principal":
            return "align principal axes -> {}".format(op.get("order", "xyz"))
        if kind == "set_dihedral":
            return "set dihedral D({}) = {:g} deg".format(
                ",".join(str(a) for a in op.get("atoms", [])), float(op.get("angle", 0)))
    except (TypeError, ValueError):
        pass
    return kind
