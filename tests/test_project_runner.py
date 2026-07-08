"""Headless project execution (core.project_runner) — pure/offline parts:
dependency ordering, target dirs, input building, and a fake-ORCA local run.

Run:  python -m pytest tests/test_project_runner.py -q
"""

import os

import pytest

from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core import coords as coords_mod
from orca_workbench.core.project import (Project, Molecule, PlannedCalc, new_calc_id,
                                         save_project)
from orca_workbench.core import project_runner as pr


_RECIPE = {
    "name": "TEST OPT", "calctype": "OPT", "method_label": "HF_STO-3G",
    "variant": "t", "template": "! HF STO-3G Opt\n%pal nprocs 2\nend\n\n!!##COORDS_SEC##!!\n",
}
_RECIPE_SP = {
    "name": "TEST SP", "calctype": "SP", "method_label": "HF_STO-3G",
    "variant": "t", "template": "! HF STO-3G\n%pal nprocs 2\nend\n\n!!##COORDS_SEC##!!\n",
}


def _project(tmp_path, recipes):
    rdir = tmp_path / "recipes"
    rdir.mkdir()
    import json
    for r in recipes:
        (rdir / (r["name"].replace(" ", "_") + ".json")).write_text(json.dumps(r))
    (tmp_path / "XYZ_INI").mkdir()
    coords_mod.write_xyz(str(tmp_path / "XYZ_INI" / "000.xyz"),
                         [("O", 0.0, 0.0, 0.1), ("H", 0.95, 0.0, -0.2),
                          ("H", -0.3, 0.93, -0.25)], {"name": "water"})
    proj = Project(path=str(tmp_path / "proj.json"))
    proj.recipe_dirs = [str(rdir)]
    proj.molecules.append(Molecule(name="water", filename="000", charge=0, multiplicity=1,
                                   generated=True, gen_status="ok", xyz_path="XYZ_INI/000.xyz"))
    return proj


def test_order_dependency_first():
    a = PlannedCalc(id="a", molecule_filename="m", recipe_name="R", geometry_source="initial")
    b = PlannedCalc(id="b", molecule_filename="m", recipe_name="R",
                    geometry_source="parent:a", parent_id="a")
    c = PlannedCalc(id="c", molecule_filename="m", recipe_name="R",
                    geometry_source="parent:b", parent_id="b")
    order = [x.id for x in pr.order_dependency_first([c, b, a])]  # shuffled in
    assert order.index("a") < order.index("b") < order.index("c")


def test_target_dir_rel():
    mol = Molecule(name="w", filename="000", charge=0, multiplicity=1)
    rec = inputs_mod.Recipe.from_dict(_RECIPE)
    calc = PlannedCalc(id="1", molecule_filename="000", recipe_name="TEST OPT", category="gen")
    assert pr.target_dir_rel(calc, mol, rec) == "calcs/000/gen/OPT/HF_STO-3G/t"


def test_build_input_embeds_geometry(tmp_path):
    proj = _project(tmp_path, [_RECIPE])
    runner = pr.ProjectRunner(proj, log=lambda m: None)
    assert runner.recipe("TEST OPT") is not None
    calc = PlannedCalc(id=new_calc_id(), molecule_filename="000",
                       recipe_name="TEST OPT", category="gen", geometry_source="initial")
    txt = runner.build_input_text(calc, proj.molecules[0], runner.recipe("TEST OPT"))
    assert "* xyz 0 1" in txt and "O " in txt          # geometry embedded
    assert "OWB molecule: 000" in txt                  # provenance header


def test_execute_local_with_fake_orca(tmp_path, monkeypatch):
    proj = _project(tmp_path, [_RECIPE, _RECIPE_SP])
    save_project(proj)
    opt = PlannedCalc(id=new_calc_id(), molecule_filename="000", recipe_name="TEST OPT",
                      category="gen", geometry_source="initial")
    sp = PlannedCalc(id=new_calc_id(), molecule_filename="000", recipe_name="TEST SP",
                     category="sp", geometry_source="parent:" + opt.id, parent_id=opt.id)
    proj.planned_calcs += [sp, opt]   # out of order on purpose

    # Fake ORCA: 'runs' the .inp, and (for the OPT) writes the optimised 000.xyz
    # its child will read, so the parent-chain build path is exercised offline.
    import subprocess

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        if stdout is not None:
            stdout.write("**** ORCA TERMINATED NORMALLY ****\n")
        if "OPT" in cwd:   # the optimised geometry the child needs
            coords_mod.write_xyz(os.path.join(cwd, "000.xyz"),
                                 [("O", 0.0, 0.0, 0.0), ("H", 0.96, 0.0, 0.0),
                                  ("H", -0.24, 0.93, 0.0)], None)
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = pr.ProjectRunner(proj, log=lambda m: None)
    summary = runner.execute(force="local")
    assert summary["built"] == 2 and summary["launched"] == 2 and summary["failed"] == 0
    # child built into its own dir, reading the parent's optimised geometry
    sp_inp = tmp_path / "calcs" / "000" / "sp" / "SP" / "HF_STO-3G" / "t" / "000.inp"
    assert sp_inp.is_file()
    assert all(c.job_id == "local" and c.rundir for c in proj.planned_calcs)


def test_execute_expands_workflow_then_runs(tmp_path, monkeypatch):
    """--execute_project with a Workflow graph (and NO pre-generated calcs) should
    expand the graph itself, then build + run the resulting chain."""
    from orca_workbench.core import workflow as wf_mod
    proj = _project(tmp_path, [_RECIPE, _RECIPE_SP])
    w = wf_mod.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": "TEST OPT"})
    prop = w.add_node("property", config={"recipe": "TEST SP"})
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    w.add_edge(opt.id, "geometry", prop.id, "geometry")
    proj.workflow = w.to_dict()
    save_project(proj)
    assert proj.planned_calcs == []          # nothing generated yet

    import subprocess

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        if stdout is not None:
            stdout.write("**** ORCA TERMINATED NORMALLY ****\n")
        if "OPT" in cwd:
            coords_mod.write_xyz(os.path.join(cwd, "000.xyz"),
                                 [("O", 0.0, 0.0, 0.0), ("H", 0.96, 0.0, 0.0),
                                  ("H", -0.24, 0.93, 0.0)], None)
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = pr.ProjectRunner(proj, log=lambda m: None)
    summary = runner.execute(force="local")
    # the graph became 2 calcs (OPT + SP), both built and run
    assert summary["built"] == 2 and summary["launched"] == 2 and summary["failed"] == 0
    assert len(proj.planned_calcs) == 2
    sp = next(c for c in proj.planned_calcs if c.recipe_name == "TEST SP")
    assert sp.geometry_source.startswith("parent:")   # SP reads the OPT's geometry


def test_slurm_render_uses_calc_rundir_not_dot(tmp_path, monkeypatch):
    """Regression: the headless SLURM path must render RUNDIR as the calc dir
    (relative to the project root, where sbatch runs), NOT '.', or the job cd's
    to the root and can't find <mol>.inp ('no inp file 000.inp' on the gateway)."""
    proj = _project(tmp_path, [_RECIPE])
    save_project(proj)
    calc = PlannedCalc(id=new_calc_id(), molecule_filename="000", recipe_name="TEST OPT",
                       category="gen", geometry_source="initial")
    proj.planned_calcs.append(calc)

    submitted = {}

    def fake_submit(slurm_rel, root, dependency=None):
        submitted["slurm_rel"] = slurm_rel
        return "999", None

    monkeypatch.setattr(pr.slurm_runtime, "submit", fake_submit)
    runner = pr.ProjectRunner(proj, log=lambda m: None)
    summary = runner.execute(force="slurm")
    assert summary["launched"] == 1
    expected_rundir = "calcs/000/gen/OPT/HF_STO-3G/t"
    slurm_text = (tmp_path / expected_rundir / "000.slurm").read_text()
    assert "cd {}".format(expected_rundir) in slurm_text        # cd into the calc dir
    assert "\ncd .\n" not in slurm_text                          # not the buggy '.'
    assert submitted["slurm_rel"] == os.path.join(expected_rundir, "000.slurm")


def test_generate_pending_geometry_from_smiles(tmp_path, monkeypatch):
    """A molecule with a SMILES but no .xyz should get one generated at run time
    (so --execute_project is self-contained). Fake smiles_to_xyz to stay offline."""
    proj = _project(tmp_path, [_RECIPE])
    proj.molecules.append(Molecule(name="meth", filename="001", smiles="C", charge=0,
                                   multiplicity=1, gen_status="pending", xyz_path=None))

    def fake_smiles(smiles, prefer_rdkit_only=False):
        return [("C", 0.0, 0.0, 0.0), ("H", 0.6, 0.6, 0.6)], "fake"

    monkeypatch.setattr(coords_mod, "smiles_to_xyz", fake_smiles)
    runner = pr.ProjectRunner(proj, log=lambda m: None)
    gen, failed = runner.generate_pending_geometries()
    assert gen == 1 and failed == 0
    m = proj.molecule_by_filename("001")
    assert m.xyz_path and (tmp_path / m.xyz_path).is_file() and m.gen_status == "ok"
    # a per-molecule generation failure is non-fatal (the rest still proceed)
    proj.molecules.append(Molecule(name="bad", filename="002", smiles="???",
                                   gen_status="pending"))
    monkeypatch.setattr(coords_mod, "smiles_to_xyz",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    gen2, failed2 = runner.generate_pending_geometries()
    assert failed2 == 1 and proj.molecule_by_filename("002").gen_status == "failed"


def test_confirm_gate_blocks_execution(tmp_path):
    proj = _project(tmp_path, [_RECIPE])
    save_project(proj)
    proj.planned_calcs.append(PlannedCalc(id=new_calc_id(), molecule_filename="000",
                                          recipe_name="TEST OPT", category="gen",
                                          geometry_source="initial"))
    runner = pr.ProjectRunner(proj, log=lambda m: None)
    seen = {}

    def deny(calcs, mode):
        seen["n"], seen["mode"] = len(calcs), mode
        return False

    summary = runner.execute(force="local", confirm=deny)
    assert seen["n"] == 1 and summary["message"] == "cancelled by user"
    assert summary["built"] == 0 and summary["launched"] == 0


def test_execute_project_file_missing(tmp_path):
    msg = pr.execute_project_file(str(tmp_path / "nope.json"))
    assert msg.startswith("error")
