"""ORCA geometry constraints + relaxed surface scan — a pure `%geom` block builder.

A *geometry spec* describes optional geometry manipulations for an OPT job:
  - constraints: freeze an internal coordinate (bond / angle / dihedral) or a
    Cartesian atom position, optionally pinned at a specific value.
  - scan: ONE relaxed surface scan of an internal coordinate over a range (the rest
    of the structure relaxes at each step → an energy profile).

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
      "scan": {"type": "B", "atoms": [0, 1], "start": 1.5, "end": 3.0, "steps": 10},  # or None
    }

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
    return {"constraints": [], "scan": None}


def is_empty(spec):
    # type: (Optional[dict]) -> bool
    if not spec:
        return True
    return not spec.get("constraints") and not spec.get("scan")


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
    s = spec.get("scan")
    if s:
        errs += _check_coord("Scan", s, n_atoms)
        if s.get("type") not in SCAN_TYPES:
            errs.append("Scan: only Bond/Angle/Dihedral can be scanned.")
        try:
            steps = int(s.get("steps"))
            if steps < 2:
                errs.append("Scan: needs at least 2 steps.")
        except (TypeError, ValueError):
            errs.append("Scan: steps must be a whole number (>= 2).")
        scanned = (s.get("type"), _atoms(s))
        for k in ("start", "end"):
            errs += _check_value("Scan {}".format(k), s.get(k), atoms, scanned)
    return errs


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
    if len(atoms) != need:
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
        m1 = _vcross(n1, _vunit(b2))
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
    s = spec.get("scan")
    s2 = None
    if s:
        s2 = dict(s)
        scanned = (s.get("type"), _atoms(s))
        for k in ("start", "end"):
            try:
                s2[k] = eval_value(s.get(k), atoms, scanned)
            except ValueError as e:
                raise ValueError("Scan {} {}".format(k, e))
    return {"constraints": cons, "scan": s2}


def constraint_line(c):
    # type: (dict) -> str
    """One ORCA constraint, e.g. '{ B 0 1 1.5 C }' or '{ B 0 1 C }' or '{ C 5 C }'."""
    ctype = c["type"]
    atoms = " ".join(str(a) for a in _atoms(c))
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
    s = spec.get("scan")
    if s:
        lines.append("  Scan")
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
    s = spec.get("scan")
    if s:
        atoms = ",".join(str(a) for a in _atoms(s))
        bits.append("scan {}({}) {}->{} {} x{}".format(
            s.get("type"), atoms, _fmt(s.get("start", 0)), _fmt(s.get("end", 0)),
            _UNIT.get(s.get("type"), ""), int(s.get("steps", 0) or 0)))
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
