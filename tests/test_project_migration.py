"""Tests for backward-compatible field migrations in core.project.

Covers the molecule coords-lock flag and the single→multi recipe-directory
migration. Pure dict round-trips, no Tk / cluster.

Run from the repo root:  python -m pytest tests/ -q
"""

from orca_workbench.core.project import Molecule, Project


# --------------------------------------------------------------- coords_locked
def test_molecule_coords_locked_defaults_false():
    m = Molecule(name="x", filename="000")
    assert m.coords_locked is False


def test_molecule_imported_locks_retroactively():
    # An old project's imported molecule (no coords_locked key) must lock.
    m = Molecule.from_dict({"name": "x", "filename": "000", "method": "imported"})
    assert m.coords_locked is True


def test_molecule_generated_not_locked_retroactively():
    m = Molecule.from_dict({"name": "x", "filename": "000", "method": "rdkit"})
    assert m.coords_locked is False


def test_molecule_coords_locked_roundtrips():
    m = Molecule(name="x", filename="000", coords_locked=True)
    again = Molecule.from_dict(
        {"name": m.name, "filename": m.filename, "coords_locked": True, "method": "rdkit"})
    assert again.coords_locked is True


# --------------------------------------------------------------- recipe_dirs
def test_legacy_single_recipe_dir_migrates_to_list():
    p = Project.from_dict({"recipe_dir": "recipes"})
    assert p.recipe_dirs == ["recipes"]
    assert p.recipe_dir == "recipes"   # legacy alias stays in sync (= primary)


def test_recipe_dirs_list_preferred_over_singular():
    p = Project.from_dict({"recipe_dir": "old", "recipe_dirs": ["a", "b"]})
    assert p.recipe_dirs == ["a", "b"]
    assert p.recipe_dir == "a"


def test_no_recipe_dir_yields_empty_list():
    p = Project.from_dict({})
    assert p.recipe_dirs == []
    assert p.recipe_dir is None


def test_recipe_dirs_serialise_roundtrip():
    p = Project.from_dict({"recipe_dirs": ["<builtin>", "/abs/extra"]})
    d = p.to_dict()
    assert d["recipe_dirs"] == ["<builtin>", "/abs/extra"]
    assert d["recipe_dir"] == "<builtin>"          # legacy key = primary
    assert Project.from_dict(d).recipe_dirs == ["<builtin>", "/abs/extra"]
