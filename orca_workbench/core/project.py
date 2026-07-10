"""Project state — a JSON file that holds the molecules and planned calculations.

This is the persistent "memory" of the app between sessions. Open/save behaves
like a document: File > Open project.json, edit, File > Save.

Recipes are NOT stored here; they live as individual JSON files in data/recipes/
(or a user-configurable recipe directory) so they can be shared between projects.
"""

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class Molecule(object):
    name: str
    filename: str  # without .xyz extension; also used as the molname dir in calcs/
    smiles: Optional[str] = None
    charge: int = 0
    multiplicity: int = 1
    comment: str = ""
    generated: bool = False
    xyz_path: Optional[str] = None  # relative to project root
    method: Optional[str] = None    # "rdkit" / "pybel" / "imported"
    gen_smiles: Optional[str] = None  # alternate SMILES used ONLY for coord generation
                                      # (e.g. metal-swap hack: real SMILES has Cu, gen_smiles
                                      # has Mg so RDKit can embed; user manually swaps back
                                      # in the .xyz, ORCA's OPT then handles the real metal).
    gen_status: str = "pending"       # "pending" | "ok" | "failed" — drives UI row coloring
    gen_error: Optional[str] = None   # last generation error message, populated when status=failed
    coords_locked: bool = False       # True for structures imported from a coordinate file:
                                      # their .xyz is the original, so SMILES generation is
                                      # refused (it would overwrite + lose provenance). To use
                                      # SMILES instead, delete the entry and add it fresh.
    locked: bool = False              # user "Lock molecule" flag (Ctrl+L / the Lock column):
                                      # greys the row and protects it from removal. Distinct
                                      # from coords_locked (which is about SMILES regeneration).

    @classmethod
    def from_dict(cls, d):
        # Be tolerant of older project files that lack the newer fields.
        d = dict(d)
        d.setdefault("gen_smiles", None)
        d.setdefault("gen_error", None)
        d.setdefault("locked", False)
        if "gen_status" not in d:
            # Migrate: derive from the boolean `generated` flag.
            d["gen_status"] = "ok" if d.get("generated") else "pending"
        # Older projects predate the lock flag: a structure imported from a file
        # (method "imported") should lock retroactively so re-generation can't
        # clobber its original coordinates.
        d.setdefault("coords_locked", d.get("method") == "imported")
        return cls(**d)


@dataclass
class PlannedCalc(object):
    id: str
    molecule_filename: str  # foreign key into Molecule.filename
    recipe_name: str        # foreign key into Recipe.name
    category: str = "gen"
    geometry_source: str = "initial"  # "initial" | "file:<path>" | "parent:<calc_id>"
    exported: bool = False
    inp_path: Optional[str] = None
    slurm_path: Optional[str] = None
    job_id: Optional[str] = None       # SLURM job id once submitted
    rundir: Optional[str] = None       # relative dir holding the .inp/.slurm/.out
    parent_id: Optional[str] = None    # id of the calc this derives from (None = root)
    # Conditional gate from a Workflow pipeline. When set, this calc only runs
    # if the gate's predicate holds on the output of calc `source`:
    #   {"source": "<calc_id>", "predicate": "no_imaginary_freqs"}
    gate: Optional[dict] = None
    # The Workflow graph node that produced this calc (None for calcs made by
    # hand in the Calculations tab). Together with molecule_filename it gives a
    # stable identity, so re-running a pipeline reuses calcs instead of
    # duplicating them.
    origin_node: Optional[str] = None
    # Restart from another calc's converged wavefunction (MOREAD): "parent:<calc_id>"
    # makes the build inject `! ... MOREAD` + `%moinp "<that calc's .gbw>"`. The
    # recipe MUST use the same basis as that parent. None = fresh SCF guess.
    orbital_source: Optional[str] = None
    # Geometry constraints / a relaxed surface scan for an OPT job, injected into the
    # ORCA input's %geom block (see core/geomspec.py). None = plain optimization.
    # Shape: {"constraints": [{type, atoms, value?}, ...], "scan": {type, atoms, start,
    # end, steps} | None}. Atom indices are 0-based (ORCA's convention).
    geom_spec: Optional[dict] = None
    # Extra ORCA simple-input keywords appended to the `!` line at build time (e.g.
    # "UseSym" to optimise within a point group). Free-form; None/"" = nothing added.
    extra_keywords: Optional[str] = None

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d.setdefault("job_id", None)
        d.setdefault("rundir", None)
        d.setdefault("parent_id", None)
        d.setdefault("gate", None)
        d.setdefault("origin_node", None)
        d.setdefault("orbital_source", None)
        d.setdefault("geom_spec", None)
        d.setdefault("extra_keywords", None)
        return cls(**d)

    def children(self, project):
        # type: ("Project") -> list
        return [c for c in project.planned_calcs if c.parent_id == self.id]


@dataclass
class Project(object):
    path: Optional[str] = None
    usermail: str = ""   # DEPRECATED: read only for one-time migration to per-user
                         # config; never written back (see to_dict). The SLURM
                         # email is a per-user setting now, kept out of project
                         # files so they're safe to share/publish.
    molecules: List[Molecule] = field(default_factory=list)
    planned_calcs: List[PlannedCalc] = field(default_factory=list)
    recipe_dir: Optional[str] = None  # DEPRECATED single-dir field; kept in sync with
                                      # recipe_dirs[0] for back-compat with older app
                                      # versions that read it. Use recipe_dirs.
    recipe_dirs: List[str] = field(default_factory=list)  # ordered recipe folders the
                                      # project uses; [0] is the primary (where new recipes
                                      # are saved). Empty = use the built-in default only.
                                      # Entries may be relative (vs project root), absolute,
                                      # or the "<builtin>" sentinel for the packaged recipes.
    workflow: Optional[dict] = None   # Workflow node-graph (see core/workflow.py),
                                      # stored as a plain dict for forward-compat.

    def to_dict(self):
        # Note: usermail is intentionally NOT serialised — see field comment.
        return {
            "molecules": [asdict(m) for m in self.molecules],
            "planned_calcs": [asdict(c) for c in self.planned_calcs],
            # Keep the legacy singular key = the primary folder so an older app
            # version still finds a recipe dir; the plural is the source of truth.
            "recipe_dir": (self.recipe_dirs[0] if self.recipe_dirs else self.recipe_dir),
            "recipe_dirs": list(self.recipe_dirs),
            "workflow": self.workflow,
        }

    @classmethod
    def from_dict(cls, d, path=None):
        # Migrate the legacy single recipe_dir string into the ordered list.
        dirs = d.get("recipe_dirs")
        if not dirs:
            single = d.get("recipe_dir")
            dirs = [single] if single else []
        dirs = [x for x in dirs if x]
        return cls(
            path=path,
            usermail=d.get("usermail", ""),
            molecules=[Molecule.from_dict(m) for m in d.get("molecules", [])],
            planned_calcs=[PlannedCalc.from_dict(c) for c in d.get("planned_calcs", [])],
            recipe_dir=(dirs[0] if dirs else None),
            recipe_dirs=list(dirs),
            workflow=d.get("workflow"),
        )

    def root(self):
        # type: () -> str
        if self.path:
            return os.path.dirname(os.path.abspath(self.path))
        return os.getcwd()

    def molecule_by_filename(self, fname):
        # type: (str) -> Optional[Molecule]
        for m in self.molecules:
            if m.filename == fname:
                return m
        return None

    def calc_by_id(self, calc_id):
        # type: (str) -> Optional[PlannedCalc]
        for c in self.planned_calcs:
            if c.id == calc_id:
                return c
        return None


def new_calc_id():
    # type: () -> str
    return uuid.uuid4().hex[:12]


def load_project(path):
    # type: (str) -> Project
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Project.from_dict(data, path=path)


def save_project(project, path=None):
    # type: (Project, Optional[str]) -> str
    """Atomic write: writes to a temp file in the same dir then renames."""
    target = path or project.path
    if not target:
        raise ValueError("No path given and project has no path set.")
    target = os.path.abspath(target)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    dirpath = os.path.dirname(target)
    fd, tmp = tempfile.mkstemp(prefix=".project_", suffix=".json.tmp", dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, indent=2)
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    project.path = target
    return target
