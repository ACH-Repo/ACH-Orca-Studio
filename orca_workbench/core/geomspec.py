"""ORCA geometry constraints + relaxed surface scan — a pure `%geom` block builder.

A *geometry spec* describes optional geometry manipulations for an OPT job:
  - constraints: freeze an internal coordinate (bond / angle / dihedral) or a
    Cartesian atom position, optionally pinned at a specific value.
  - scans: relaxed surface scans of internal coordinates over a range (the rest
    of the structure relaxes at each step -> an energy profile). SEVERAL are
    allowed, because ORCA allows several: it runs the full GRID.

**MULTI-DIMENSIONAL SCANS, measured against ORCA 6.0.1 rather than assumed.**
Two `Scan` lines produced a 3 x 3 = 9-point relaxed surface scan, and the
surface table carries one column per coordinate in the order they were
declared - so the FIRST line is the outer loop and the last is the inner one,
and the order of this list is meaningful rather than cosmetic. Christian's
point is the one that settled it: "can you just make OWB allow multiple scans
because the entire point of it is being a GUI for orca?" A GUI for a program
should not be more restrictive than the program.

It's stored as a plain dict (so it serialises straight onto a PlannedCalc / workflow
node), rendered into ORCA's `%geom … end` block, and injected into the input by
`inputs.add_geom_block`. **ORCA atom indices are 0-based.** No Tkinter/ORCA here, so
it's unit-testable.

Spec shape::

    {
      "constraints": [
        {"type": "B", "atoms": [0, 1], "value": 1.5},   # value optional (None = freeze current)
        {"type": "A", "atoms": [0, 1, 2]},
        {"type": "D", "atoms": [0, 1, 2, 3], "value": 180.0},
        {"type": "C", "atoms": [5]},                     # freeze atom 5's Cartesian position
      ],
      "scans": [
        {"type": "B", "atoms": [0, 1], "start": 1.5, "end": 3.0, "steps": 10},
        {"type": "D", "atoms": [0, 1, 2, 3], "start": -180, "end": 180, "steps": 13},
      ],
    }

`steps` is the number of GEOMETRIES, not the number of intervals: ORCA ran
`= -180, -60, 4` as four points at -180, -140, -100 and -60, so the spacing is
`(end - start) / (steps - 1)`. Also measured rather than assumed.

A spec saved before multi-scan support carries `"scan": {...}` instead;
`scans_of` reads either, so old projects load unchanged. Only `scans` is
written, so a project saved here and opened in an older build would lose the
scan - a one-way version step, noted rather than worked around.

A "value"/"start"/"end" field may also be a geometry-derived EXPRESSION string —
`current`, `B(2,4)`, `A(0,1,2)`, `D(...)`, plus `+ - * /` — resolved against the input
geometry at build time (see eval_value). E.g. scan start "current", end "current + 1.5".
"""

import ast
import math
import re

from typing import List, Optional


# internal-coordinate type -> (human label, number of atoms it needs)
COORD_TYPES = {
    "B": ("Bond (distance)", 2),
    "A": ("Angle", 3),
    "D": ("Dihedral", 4),
    "C": ("Cartesian position", 1),
}
# Types that can be scanned (a Cartesian position isn't a scannable 1-D coordinate).
SCAN_TYPES = ("B", "A", "D")
_UNIT = {"B": "Å", "A": "°", "D": "°"}


def n_atoms_for(ctype):
    # type: (str) -> int
    return COORD_TYPES.get(ctype, ("", 0))[1]


def empty_spec():
    # type: () -> dict
    return {"constraints": [], "scans": []}


def scans_of(spec):
    # type: (Optional[dict]) -> List[dict]
    """The scans, in declaration order, from either shape.

    ONE place reads the key, so a spec saved before multi-scan support
    (`"scan": {...}`) and one saved after (`"scans": [...]`) cannot diverge
    anywhere else in the module.
    """
    if not spec:
        return []
    scans = spec.get("scans")
    if isinstance(scans, list):
        return [s for s in scans if isinstance(s, dict) and s]
    one = spec.get("scan")
    return [one] if isinstance(one, dict) and one else []


def with_scans(spec, scans):
    # type: (Optional[dict], List[dict]) -> dict
    """`spec` with its scans replaced, in the canonical shape."""
    out = dict(spec or {})
    out["constraints"] = list(out.get("constraints") or [])
    out["scans"] = [dict(s) for s in (scans or []) if s]
    out.pop("scan", None)
    return out


def is_empty(spec):
    # type: (Optional[dict]) -> bool
    if not spec:
        return True
    return not spec.get("constraints") and not scans_of(spec)


def _atoms(item):
    return [int(a) for a in (item.get("atoms") or [])]


def validate(spec, n_atoms=None, atoms=None):
    # type: (Optional[dict], Optional[int], Optional[list]) -> List[str]
    """Human-readable problems with a spec (empty list == OK). If `n_atoms` is given,
    also checks that every atom index is in range and distinct within one coordinate.
    If `atoms` (the geometry, list of (element, x, y, z)) is given, any value
    expression (e.g. `current + 1.5`, `B(2,4)`) is actually evaluated so a bad one is
    caught up front; without it, expressions are accepted and resolved at build time."""
    errs = []
    if is_empty(spec):
        return errs
    for i, c in enumerate(spec.get("constraints") or []):
        where = "Constraint {}".format(i + 1)
        errs += _check_coord(where, c, n_atoms)
        if c.get("type") != "C" and c.get("value") not in (None, ""):
            errs += _check_value(where, c.get("value"), atoms, (c.get("type"), _atoms(c)))
    scans = scans_of(spec)
    seen = []
    for i, s in enumerate(scans):
        where = "Scan" if len(scans) == 1 else "Scan {}".format(i + 1)
        errs += _check_coord(where, s, n_atoms)
        if s.get("type") not in SCAN_TYPES:
            errs.append("{}: only Bond/Angle/Dihedral can be scanned.".format(
                where))
        try:
            steps = int(s.get("steps"))
            if steps < 2:
                errs.append("{}: needs at least 2 points.".format(where))
        except (TypeError, ValueError):
            errs.append("{}: points must be a whole number (>= 2).".format(
                where))
        scanned = (s.get("type"), _atoms(s))
        for k in ("start", "end"):
            errs += _check_value("{} {}".format(where, k), s.get(k), atoms,
                                 scanned)
        # SCANNING ONE COORDINATE TWICE is not a 2-D surface, it is a
        # contradiction: the inner loop would be asked to hold the value the
        # outer loop just set.
        key = (s.get("type"), tuple(_atoms(s)))
        if key in seen:
            errs.append("{}: this coordinate is already being scanned.".format(
                where))
        seen.append(key)
        # ...and so is scanning something that is also frozen.
        for c in spec.get("constraints") or []:
            if (c.get("type"), tuple(_atoms(c))) == key:
                errs.append(
                    "{}: this coordinate is also constrained - it cannot be "
                    "both held and scanned.".format(where))
        # The same contradiction wearing different clothes: EVERY atom of the
        # scanned coordinate pinned by Cartesian freezes. Christian found it
        # by freezing a whole molecule and then scanning a bond inside it -
        # the types differ, so the check above says nothing, and ORCA is
        # handed a coordinate nothing can walk. Deliberately not "any atom
        # frozen": a scan with one end pinned is an ordinary thing to want.
        if _atoms(s) and set(_atoms(s)) <= cartesian_frozen(
                spec.get("constraints") or []):
            errs.append(
                "{}: every atom of this coordinate is frozen in place, so "
                "nothing can walk it.".format(where))
    return errs


def cartesian_frozen(constraints):
    # type: (list) -> set
    """Every atom held in place by a Cartesian freeze.

    Named to match MoloM's `orca.cartesian_frozen`, which had to avoid an
    existing `frozen_atoms` there meaning something else entirely.
    """
    out = set()
    for c in constraints or []:
        if c.get("type") == "C":
            out.update(_atoms(c))
    return out


def _check_value(where, v, atoms, scanned):
    """A scan/constraint value is OK if it is a number, or an expression that (when a
    geometry is supplied) evaluates cleanly."""
    if not is_expr(v):
        if not _is_number(v):
            return ["{}: value must be a number or an expression like B(2,4)+0.5.".format(where)]
        return []
    if atoms is not None:
        try:
            eval_value(v, atoms, scanned)
        except ValueError as e:
            return ["{}: {}".format(where, e)]
    return []


def _check_coord(where, c, n_atoms):
    errs = []
    ctype = c.get("type")
    if ctype not in COORD_TYPES:
        errs.append("{}: unknown coordinate type {!r}.".format(where, ctype))
        return errs
    need = n_atoms_for(ctype)
    atoms = _atoms(c)
    # `C` is the exception: it freezes POSITIONS, so any number of them is
    # meaningful and several become several lines. Every other type names an
    # internal coordinate with a fixed arity.
    if ctype == "C":
        if not atoms:
            errs.append("{}: nothing selected to freeze.".format(where))
    elif len(atoms) != need:
        errs.append("{}: {} needs {} atom(s), got {}.".format(
            where, COORD_TYPES[ctype][0], need, len(atoms)))
    if len(set(atoms)) != len(atoms):
        errs.append("{}: atom indices must be distinct.".format(where))
    if n_atoms is not None:
        for a in atoms:
            if a < 0 or a >= n_atoms:
                errs.append("{}: atom index {} is out of range (0..{}).".format(
                    where, a, n_atoms - 1))
    return errs


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _fmt(v):
    """Trim a float to a compact string (1.5 not 1.50000). Non-numeric input (an
    unresolved expression like 'B(2,4)+0.5') is returned verbatim, so describe()/
    the UI can show the expression before it is resolved at build time."""
    try:
        return "%g" % float(v)
    except (TypeError, ValueError):
        return str(v)


# --------------------------------------------------------------------------
# Geometry-derived values (variables) in scan/constraint fields
#
# A start/end/value field may be a plain number OR an expression that measures
# an internal coordinate of the INPUT geometry at build time:
#   B(i,j)      bond distance i-j            (Å)
#   A(i,j,k)    angle i-j-k                  (degrees)
#   D(i,j,k,l)  dihedral i-j-k-l             (degrees)
#   current     the coordinate being scanned/constrained (== B/A/D of its atoms)
# plus arithmetic (+ - * /, parentheses), e.g. `current + 1.5`, `B(2,4) - 0.1`.
# This lets a scan start from wherever the bond currently sits, so you never have
# to wait for (or hand-copy) a pre-optimisation to type in a number.
# --------------------------------------------------------------------------

# atoms are (element, x, y, z); coords indexable [1],[2],[3]
def _pt(atom):
    return (float(atom[1]), float(atom[2]), float(atom[3]))


def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _vnorm(a):
    return math.sqrt(_vdot(a, a))


def _vunit(a):
    n = _vnorm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n else a


def measure(ctype, idxs, atoms):
    # type: (str, List[int], list) -> float
    """Measure internal coordinate `ctype` (B/A/D) over 0-based `idxs` in `atoms`
    (a list of (element, x, y, z)). Bonds in Å, angles/dihedrals in degrees. The
    dihedral sign follows the IUPAC convention (matches core.transform)."""
    try:
        pts = [_pt(atoms[i]) for i in idxs]
    except (IndexError, TypeError):
        raise ValueError("atom index out of range for the geometry")
    if ctype == "B":
        return _vnorm(_vsub(pts[0], pts[1]))
    if ctype == "A":
        u, v = _vsub(pts[0], pts[1]), _vsub(pts[2], pts[1])
        c = _vdot(u, v) / (_vnorm(u) * _vnorm(v))
        return math.degrees(math.acos(max(-1.0, min(1.0, c))))
    if ctype == "D":
        b1, b2, b3 = _vsub(pts[1], pts[0]), _vsub(pts[2], pts[1]), _vsub(pts[3], pts[2])
        n1, n2 = _vcross(b1, b2), _vcross(b2, b3)
        # The order of THIS cross product is the sign of the dihedral, and it
        # was the wrong way round until 2026-09-06: every value came back as
        # its own negative, i.e. describing the MIRROR IMAGE of the geometry
        # in front of the user. Nothing failed - a `current` or `D(0,1,2,3)`
        # in a scan simply started from the enantiomeric conformation and the
        # input file read perfectly plausibly. Settled against a THIRD
        # implementation rather than by argument: over 400 random geometries
        # RDKit's `GetDihedralDeg` (the IUPAC convention ORCA itself uses)
        # and MoloM's `measure.dihedral` agree with each other every time,
        # and this line disagreed with both by an exact sign.
        m1 = _vcross(_vunit(b2), n1)
        return math.degrees(math.atan2(_vdot(m1, n2), _vdot(n1, n2)))
    raise ValueError("cannot measure coordinate type {!r}".format(ctype))


_COORD_RE = re.compile(r"\b([BAD])\s*\(\s*(\d+(?:\s*,\s*\d+)*)\s*\)")


def _need_atoms(atoms):
    if not atoms:
        raise ValueError("references the geometry (e.g. B(2,4)) but no geometry "
                         "was available to resolve it")
    return atoms


def _safe_arith(expr):
    """Evaluate a pure-arithmetic string (numbers, + - * /, unary +/-, parens).
    No names/calls/attributes — safe against code injection."""
    node = ast.parse(expr, mode="eval").body

    def ev(n):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            return a / b
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        # Python <3.8 compatibility (gateway is 3.9 which has Constant, but be safe)
        if isinstance(n, getattr(ast, "Num", ())):
            return float(n.n)
        raise ValueError("unsupported term in expression")

    return float(ev(node))


def is_expr(v):
    # type: (object) -> bool
    """True if v is a non-numeric value that must be resolved against a geometry."""
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return False
    try:
        float(v)
        return False
    except (TypeError, ValueError):
        return True


def eval_value(value, atoms=None, scanned=None):
    # type: (object, Optional[list], Optional[tuple]) -> float
    """Resolve a scan/constraint field to a float. A number passes straight through
    (no geometry needed); an expression measures B()/A()/D()/`current` against
    `atoms` (list of (element, x, y, z)). `scanned` = (ctype, [idxs]) supplies the
    meaning of `current`. Raises ValueError on bad syntax or missing geometry."""
    if isinstance(value, bool):
        raise ValueError("value must be a number or expression")
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s == "":
        raise ValueError("value is blank")
    try:
        return float(s)              # a plain number typed as text
    except ValueError:
        pass

    def repl_current(_m):
        if not scanned or scanned[0] not in ("B", "A", "D"):
            raise ValueError("'current' is only valid for a bond/angle/dihedral")
        return repr(measure(scanned[0], scanned[1], _need_atoms(atoms)))

    def repl_coord(m):
        idxs = [int(x) for x in m.group(2).split(",")]
        return repr(measure(m.group(1), idxs, _need_atoms(atoms)))

    try:
        expr = re.sub(r"\bcurrent\b", repl_current, s)
        expr = _COORD_RE.sub(repl_coord, expr)
        return _safe_arith(expr)
    except ValueError:
        raise
    except (SyntaxError, TypeError, ZeroDivisionError) as e:
        raise ValueError("cannot evaluate {!r}: {}".format(s, e))


def _resolve(spec, atoms):
    """Return a copy of `spec` with every scan/constraint value expression evaluated
    to a float against `atoms`. Numbers are untouched (so a geometry-free spec needs
    no atoms). Raises ValueError (naming the field) if an expression can't resolve."""
    cons = []
    for i, c in enumerate(spec.get("constraints") or []):
        c2 = dict(c)
        if c2.get("type") != "C" and c2.get("value") not in (None, ""):
            try:
                c2["value"] = eval_value(c2["value"], atoms, (c2.get("type"), _atoms(c2)))
            except ValueError as e:
                raise ValueError("Constraint {} value {}".format(i + 1, e))
        cons.append(c2)
    scans = scans_of(spec)
    out = []
    for i, s in enumerate(scans):
        s2 = dict(s)
        scanned = (s.get("type"), _atoms(s))
        where = "Scan" if len(scans) == 1 else "Scan {}".format(i + 1)
        for k in ("start", "end"):
            try:
                s2[k] = eval_value(s.get(k), atoms, scanned)
            except ValueError as e:
                raise ValueError("{} {} {}".format(where, k, e))
        out.append(s2)
    return {"constraints": cons, "scans": out}


def cartesian_runs(indices):
    # type: (list) -> list
    """Sorted indices as ORCA range tokens: [0,1,2,5] -> ['0:2', '5'].

    ORCA freezes ONE atom or a CONTIGUOUS RANGE and nothing else - measured
    on 6.0.1, where `{ C 0 2 5 C }` and `{ C 0,2,5 C }` are both a syntax
    error ("Expecting C(onstraint) in ScanConstraints") while `{ C 0:3 C }`
    holds all four exactly. So a set of atoms becomes several lines, and
    consecutive runs are collapsed so that freezing a phenyl ring reads as
    one line rather than six.

    A run of two is written out in full, because `3:4` is longer than the
    thing it abbreviates and reads as a range when it is a pair.
    """
    idxs = sorted({int(i) for i in indices})
    out, start = [], None
    for k, i in enumerate(idxs):
        if start is None:
            start = i
        nxt = idxs[k + 1] if k + 1 < len(idxs) else None
        if nxt is None or nxt != i + 1:
            if i - start >= 2:
                out.append("{}:{}".format(start, i))
            else:
                out.extend(str(v) for v in range(start, i + 1))
            start = None
    return out


def constraint_line(c):
    # type: (dict) -> str
    """One ORCA constraint, e.g. '{ B 0 1 1.5 C }' or '{ B 0 1 C }' or '{ C 5 C }'.

    A Cartesian freeze over several atoms becomes several lines - see
    `cartesian_runs`, and note MoloM's `core/orca.py` does exactly the same,
    pinned against this by a cross-check test.
    """
    ctype = c["type"]
    idxs = _atoms(c)
    if ctype == "C" and len(idxs) > 1:
        joiner = "\n    "
        return joiner.join("{{ C {} C }}".format(r)
                           for r in cartesian_runs(idxs))
    atoms = " ".join(str(a) for a in idxs)
    val = c.get("value")
    if ctype == "C" or val is None or val == "":
        return "{{ {} {} C }}".format(ctype, atoms)
    return "{{ {} {} {} C }}".format(ctype, atoms, _fmt(val))


def scan_line(s):
    # type: (dict) -> str
    """One ORCA scan line, e.g. 'B 0 1 = 1.5, 3.0, 10'."""
    atoms = " ".join(str(a) for a in _atoms(s))
    return "{} {} = {}, {}, {}".format(
        s["type"], atoms, _fmt(s["start"]), _fmt(s["end"]), int(s["steps"]))


def build_geom_inner(spec, atoms=None):
    # type: (Optional[dict], Optional[list]) -> str
    """The INNER text of the %geom block (Constraints / Scan sub-blocks), 2-space
    indented, or '' if the spec is empty. `inputs.add_geom_block` wraps/merges it.

    `atoms` (the geometry, list of (element, x, y, z)) resolves any value expression
    (`current`, `B(2,4)`, …) against the input structure; a spec that uses only plain
    numbers needs no atoms. Raises ValueError if an expression can't be resolved."""
    if is_empty(spec):
        return ""
    spec = _resolve(spec, atoms)
    lines = []
    cons = spec.get("constraints") or []
    if cons:
        lines.append("  Constraints")
        for c in cons:
            lines.append("    " + constraint_line(c))
        lines.append("  end")
    scans = scans_of(spec)
    if scans:
        # ONE Scan block holding every coordinate, which is what ORCA takes -
        # and the ORDER is the loop order, the first line being the outer
        # loop. Measured: two lines gave a 3 x 3 grid with the first
        # coordinate varying slowest.
        lines.append("  Scan")
        for s in scans:
            lines.append("    " + scan_line(s))
        lines.append("  end")
    return "\n".join(lines)


def describe(spec):
    # type: (Optional[dict]) -> str
    """Short human summary for the UI, e.g. '2 constraints; scan B(0,1) 1.5->3.0 Å x10'."""
    if is_empty(spec):
        return "(none)"
    bits = []
    cons = spec.get("constraints") or []
    if cons:
        bits.append("{} constraint{}".format(len(cons), "" if len(cons) == 1 else "s"))
    scans = scans_of(spec)
    for s in scans:
        atoms = ",".join(str(a) for a in _atoms(s))
        bits.append("scan {}({}) {}->{} {} x{}".format(
            s.get("type"), atoms, _fmt(s.get("start", 0)), _fmt(s.get("end", 0)),
            _UNIT.get(s.get("type"), ""), int(s.get("steps", 0) or 0)))
    if len(scans) > 1:
        # What the grid COSTS, said out loud: ORCA runs every combination, so
        # two 10-point scans is a hundred optimisations and not twenty.
        total = 1
        for s in scans:
            total *= max(1, int(s.get("steps", 0) or 0))
        bits.append("{} grid points".format(total))
    return "; ".join(bits)


def coord_describe(c):
    # type: (dict) -> str
    """Describe one constraint for a list row, e.g. 'Bond 0-1 = 1.5 Å' / 'Angle 0-1-2 (free)'."""
    ctype = c.get("type")
    label = COORD_TYPES.get(ctype, (ctype, 0))[0]
    atoms = "-".join(str(a) for a in _atoms(c))
    if ctype == "C":
        return "{} of atom {}".format(label, atoms)
    val = c.get("value")
    if val is None or val == "":
        return "{} {} (freeze current)".format(label, atoms)
    return "{} {} = {} {}".format(label, atoms, _fmt(val), _UNIT.get(ctype, ""))
