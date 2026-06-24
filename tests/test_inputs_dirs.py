"""Tests for multi-directory recipe loading in core.inputs.

Run from the repo root:  python -m pytest tests/ -q
"""

import json
import os

from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core.inputs import Recipe


def _write_recipe(dirpath, name, calctype="SP", method="m"):
    os.makedirs(dirpath, exist_ok=True)
    r = Recipe(name=name, calctype=calctype, method_label=method,
               template="! HF\n\n!!##COORDS_SEC##!!\n")
    inputs_mod.save_recipe(r, dirpath)
    return r


def test_load_from_dirs_merges_in_order(tmp_path):
    a = str(tmp_path / "A")
    b = str(tmp_path / "B")
    _write_recipe(a, "alpha")
    _write_recipe(b, "beta")
    recipes = inputs_mod.load_recipes_from_dirs([a, b])
    names = {r.name for r in recipes}
    assert names == {"alpha", "beta"}
    # source_path lets the UI group by folder
    by_name = {r.name: r for r in recipes}
    assert os.path.dirname(by_name["alpha"].source_path) == a
    assert os.path.dirname(by_name["beta"].source_path) == b


def test_load_from_dirs_global_dedup_first_wins(tmp_path):
    a = str(tmp_path / "A")
    b = str(tmp_path / "B")
    _write_recipe(a, "dup", method="from_A")
    _write_recipe(b, "dup", method="from_B")
    recipes = inputs_mod.load_recipes_from_dirs([a, b])
    dups = [r for r in recipes if r.name == "dup"]
    assert len(dups) == 1
    assert dups[0].method_label == "from_A"     # first folder wins


def test_load_from_dirs_skips_missing_dir(tmp_path):
    a = str(tmp_path / "A")
    _write_recipe(a, "alpha")
    recipes = inputs_mod.load_recipes_from_dirs([str(tmp_path / "nope"), a])
    assert [r.name for r in recipes] == ["alpha"]
