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
"""

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


def validate(spec, n_atoms=None):
    # type: (Optional[dict], Optional[int]) -> List[str]
    """Human-readable problems with a spec (empty list == OK). If `n_atoms` is given,
    also checks that every atom index is in range and distinct within one coordinate."""
    errs = []
    if is_empty(spec):
        return errs
    for i, c in enumerate(spec.get("constraints") or []):
        errs += _check_coord("Constraint {}".format(i + 1), c, n_atoms)
    s = spec.get("scan")
    if s:
        errs += _check_coord("Scan", s, n_atoms, scan=True)
        if s.get("type") not in SCAN_TYPES:
            errs.append("Scan: only Bond/Angle/Dihedral can be scanned.")
        try:
            steps = int(s.get("steps"))
            if steps < 2:
                errs.append("Scan: needs at least 2 steps.")
        except (TypeError, ValueError):
            errs.append("Scan: steps must be a whole number (>= 2).")
        for k in ("start", "end"):
            if not _is_number(s.get(k)):
                errs.append("Scan: {} must be a number.".format(k))
    return errs


def _check_coord(where, c, n_atoms, scan=False):
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
    if not scan and ctype != "C" and c.get("value") is not None and not _is_number(c.get("value")):
        errs.append("{}: value must be a number or left blank.".format(where))
    return errs


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _fmt(v):
    """Trim a float to a compact string (1.5 not 1.50000)."""
    f = float(v)
    return ("%g" % f)


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


def build_geom_inner(spec):
    # type: (Optional[dict]) -> str
    """The INNER text of the %geom block (Constraints / Scan sub-blocks), 2-space
    indented, or '' if the spec is empty. `inputs.add_geom_block` wraps/merges it."""
    if is_empty(spec):
        return ""
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
