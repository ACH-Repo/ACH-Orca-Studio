"""Render ORCA .inp files from recipes + molecule geometry.

A "recipe" is a JSON file in data/recipes/ with these fields:
  - name:         human-readable label
  - calctype:     OPT / FREQ / NMR / SP / ... (used in the directory tree)
  - method_label: e.g. "B3LYP_DEF2-TZVPP" (used in the directory tree)
  - template:     the .inp template text, containing the placeholder
                  !!##COORDS_SEC##!! which is replaced with the full
                  `* xyz CHARGE MULT ... *` block built from the molecule.

This keeps recipes plain-text editable and free of ORCA-syntax magic on
our side — what you write in the template is what ends up in the .inp.
"""

import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional

from orca_studio.core.coords import Atom, format_atom_block


CORES_RE = re.compile(r"%pal\s+nprocs\s+(\d+)", re.IGNORECASE)
COORDS_PLACEHOLDER = "!!##COORDS_SEC##!!"


class Recipe(object):
    def __init__(self, name, calctype, method_label, template, variant="",
                 favorite=False, created_at=None, source_path=None):
        # type: (str, str, str, str, str, bool, Optional[str], Optional[str]) -> None
        self.name = name
        self.calctype = calctype
        self.method_label = method_label
        self.variant = variant or ""
        self.template = template
        self.favorite = bool(favorite)
        self.created_at = created_at  # ISO 8601 string; set on first save
        self.source_path = source_path

    def to_dict(self):
        # type: () -> dict
        return {
            "name": self.name,
            "calctype": self.calctype,
            "method_label": self.method_label,
            "variant": self.variant,
            "favorite": self.favorite,
            "created_at": self.created_at,
            "template": self.template,
        }

    @classmethod
    def from_dict(cls, d, source_path=None):
        # type: (dict, Optional[str]) -> Recipe
        return cls(
            name=d["name"],
            calctype=d["calctype"],
            method_label=d["method_label"],
            template=d["template"],
            variant=d.get("variant", ""),
            favorite=d.get("favorite", False),
            created_at=d.get("created_at"),
            source_path=source_path,
        )

    def path_parts(self):
        # type: () -> tuple
        """Directory parts under calcs/<mol>/<category>/ for this recipe.
        Excludes empty variant so existing layouts stay intact."""
        if self.variant:
            return (self.calctype, self.method_label, self.variant)
        return (self.calctype, self.method_label)


def load_recipes_from_dir(dirpath):
    # type: (str) -> List[Recipe]
    """Load all recipe JSON files from a directory. Backfills created_at from
    the file's mtime for legacy recipes that lack the field.

    A recipe's `name` is its identity throughout the app (planned calcs and the
    UI tree both key off it), so two files claiming the same name are
    fundamentally ambiguous. We keep the first one encountered (files are read
    in sorted order, so this is deterministic) and skip later duplicates with a
    warning, rather than letting the ambiguity crash the UI later.
    """
    recipes = []
    seen = {}  # name -> source path of the file we kept
    if not os.path.isdir(dirpath):
        return recipes
    for fname in sorted(os.listdir(dirpath)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(dirpath, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            r = Recipe.from_dict(data, source_path=path)
        except (IOError, ValueError, KeyError):
            continue
        if r.name in seen:
            sys.stderr.write(
                "orca_studio: skipping duplicate recipe name {!r}\n"
                "  ignored: {}\n"
                "  kept:    {}\n".format(r.name, path, seen[r.name])
            )
            continue
        seen[r.name] = path
        if not r.created_at:
            try:
                mtime = os.path.getmtime(path)
                r.created_at = datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
            except OSError:
                r.created_at = ""
        recipes.append(r)
    return recipes


def save_recipe(recipe, dirpath):
    # type: (Recipe, str) -> str
    """Write a recipe to disk and return the file path. Stamps created_at on
    first save.

    The file name is STABLE: an existing recipe (one with a source_path that
    still exists) is always rewritten in place, even if its display name
    changed — so renaming updates the file rather than spawning a duplicate.
    Only a brand-new recipe gets a fresh, unique file named after its name."""
    os.makedirs(dirpath, exist_ok=True)
    if recipe.source_path and os.path.isfile(recipe.source_path):
        path = recipe.source_path                      # update in place
    else:
        path = _unique_recipe_path(dirpath, recipe.name)  # new file
    if not recipe.created_at:
        recipe.created_at = datetime.datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recipe.to_dict(), f, indent=2)
    recipe.source_path = path
    return path


def _safe_filename(name):
    # type: (str) -> str
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "recipe"


def _unique_recipe_path(dirpath, name):
    # type: (str, str) -> str
    """A new <safe_name>.json path that doesn't collide with an existing file."""
    base = _safe_filename(name)
    path = os.path.join(dirpath, base + ".json")
    i = 2
    while os.path.exists(path):
        path = os.path.join(dirpath, "{}_{}.json".format(base, i))
        i += 1
    return path


def build_coords_section(atoms, charge, multiplicity):
    # type: (List[Atom], int, int) -> str
    """Build the `* xyz CHARGE MULT ... *` block for an ORCA input file."""
    header = "* xyz {} {}".format(charge, multiplicity)
    body = format_atom_block(atoms)
    return "{}\n{}\n*".format(header, body)


def render_inp(recipe, atoms, charge, multiplicity):
    # type: (Recipe, List[Atom], int, int) -> str
    """Render a complete ORCA .inp file text."""
    coords = build_coords_section(atoms, charge, multiplicity)
    if COORDS_PLACEHOLDER not in recipe.template:
        raise ValueError(
            "Recipe {!r} has no {} placeholder.".format(recipe.name, COORDS_PLACEHOLDER)
        )
    return recipe.template.replace(COORDS_PLACEHOLDER, coords)


def render_inp_xyzfile(recipe, xyz_path, charge, multiplicity):
    # type: (Recipe, str, int, int) -> str
    """Render an .inp whose geometry is read from an external .xyz at run time
    (`* xyzfile CHARGE MULT path`) rather than being embedded.

    Used for unattended dependency chains: a derived calc can be built and
    submitted *before* its parent runs, because ORCA reads the parent's
    optimised geometry from `xyz_path` (an absolute path on the shared
    filesystem) when the job actually starts — by which point the parent has
    finished and written it."""
    coords = "* xyzfile {} {} {}".format(charge, multiplicity, xyz_path)
    if COORDS_PLACEHOLDER not in recipe.template:
        raise ValueError(
            "Recipe {!r} has no {} placeholder.".format(recipe.name, COORDS_PLACEHOLDER)
        )
    return recipe.template.replace(COORDS_PLACEHOLDER, coords)


def parse_cores(inp_text):
    # type: (str) -> int
    """Return the nprocs declared in `%pal nprocs N`. Defaults to 1 if absent."""
    match = CORES_RE.search(inp_text)
    if match:
        return int(match.group(1))
    return 1


def detect_cores():
    # type: () -> int
    """Number of CPU cores this machine reports, for sizing local jobs. Falls
    back to 2 if the OS won't say (every modern CPU has at least two)."""
    try:
        n = os.cpu_count()
    except Exception:
        n = None
    return n if n and n >= 1 else 2


def set_cores(inp_text, n):
    # type: (str, int) -> str
    """Rewrite the `%pal nprocs N` value in an input. If the input has no %pal
    block, it's returned unchanged (ORCA then runs serial, which is fine)."""
    n = max(1, int(n))
    if not CORES_RE.search(inp_text):
        return inp_text
    return CORES_RE.sub("%pal nprocs {}".format(n), inp_text, count=1)


XYZ_BLOCK_RE = re.compile(
    r"\*\s*xyz\s*([+-]?\d+)\s+(\d+)\s+([\s\S]*?)\*",
    re.IGNORECASE,
)


def extract_coords_section(inp_text):
    # type: (str) -> str
    """Extract the full `* xyz ... *` block from an existing .inp file."""
    match = XYZ_BLOCK_RE.search(inp_text.replace("\r\n", "\n"))
    if not match:
        raise ValueError("No `* xyz` block found in input.")
    return match.group(0)


def extract_atoms_from_inp(inp_text):
    # type: (str) -> List[Atom]
    """Parse just the atom rows out of a `* xyz` block."""
    match = XYZ_BLOCK_RE.search(inp_text.replace("\r\n", "\n"))
    if not match:
        raise ValueError("No `* xyz` block found in input.")
    body = match.group(3)
    atoms = []
    atom_re = re.compile(r"^\s*([A-Z][a-z]?)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)")
    for line in body.split("\n"):
        m = atom_re.match(line)
        if m:
            atoms.append((m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))))
    return atoms
