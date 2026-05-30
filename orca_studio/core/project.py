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

    @classmethod
    def from_dict(cls, d):
        # Be tolerant of older project files that lack the newer fields.
        d = dict(d)
        d.setdefault("gen_smiles", None)
        d.setdefault("gen_error", None)
        if "gen_status" not in d:
            # Migrate: derive from the boolean `generated` flag.
            d["gen_status"] = "ok" if d.get("generated") else "pending"
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

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d.setdefault("job_id", None)
        d.setdefault("rundir", None)
        d.setdefault("parent_id", None)
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
    recipe_dir: Optional[str] = None  # override default recipe location; relative or absolute
    workflow: Optional[dict] = None   # Workflow node-graph (see core/workflow.py),
                                      # stored as a plain dict for forward-compat.

    def to_dict(self):
        # Note: usermail is intentionally NOT serialised — see field comment.
        return {
            "molecules": [asdict(m) for m in self.molecules],
            "planned_calcs": [asdict(c) for c in self.planned_calcs],
            "recipe_dir": self.recipe_dir,
            "workflow": self.workflow,
        }

    @classmethod
    def from_dict(cls, d, path=None):
        return cls(
            path=path,
            usermail=d.get("usermail", ""),
            molecules=[Molecule.from_dict(m) for m in d.get("molecules", [])],
            planned_calcs=[PlannedCalc.from_dict(c) for c in d.get("planned_calcs", [])],
            recipe_dir=d.get("recipe_dir"),
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
