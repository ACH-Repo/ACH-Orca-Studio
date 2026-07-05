"""Headless workflow expansion (core.workflow_expand) — the pure engine shared by
the Workflow tab's Generate and `orca-workbench --execute_project`. Uses REAL
geometries + the real transform backend (no fakes), so it exercises the same code
the GUI runs.

Run:  python -m pytest tests/test_workflow_expand.py -q
"""

import os

from orca_workbench.core import coords as coords_mod
from orca_workbench.core import workflow as wf_mod
from orca_workbench.core import workflow_expand as wexp
from orca_workbench.core.project import Project, Molecule


def _project(tmp_path, n_mols=2):
    (tmp_path / "XYZ_INI").mkdir()
    proj = Project(path=str(tmp_path / "proj.json"))
    for i in range(n_mols):
        rel = "XYZ_INI/{:03d}.xyz".format(i)
        coords_mod.write_xyz(str(tmp_path / rel),
                             [("O", 0.0, 0.0, 0.1 + i), ("H", 0.95, 0.0, -0.2),
                              ("H", -0.3, 0.93, -0.25)], {"name": "w%d" % i})
        proj.molecules.append(Molecule(name="w%d" % i, filename="{:03d}".format(i),
                                        charge=0, multiplicity=1, generated=True,
                                        gen_status="ok", xyz_path=rel))
    return proj


def test_no_workflow_is_a_noop(tmp_path):
    proj = _project(tmp_path)
    res = wexp.expand_project_workflow(proj)
    assert res["expanded"] is False and res["new"] == 0 and not res["blockers"]
    assert proj.planned_calcs == []


def test_linear_pipeline_expands_with_parent_links(tmp_path):
    proj = _project(tmp_path, n_mols=2)
    w = wf_mod.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    prop = w.add_node("property", config={"recipe": "SP"})
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    w.add_edge(opt.id, "geometry", prop.id, "geometry")
    proj.workflow = w.to_dict()

    res = wexp.expand_project_workflow(proj)
    assert res["expanded"] and res["new"] == 4 and res["reused"] == 0   # 2 mols x 2 calcs
    opts = [c for c in proj.planned_calcs if c.origin_node == opt.id]
    props = [c for c in proj.planned_calcs if c.origin_node == prop.id]
    assert len(opts) == 2 and len(props) == 2
    # the property calc reads the optimize calc's geometry (parent link)
    for p in props:
        parent = next(o for o in opts if o.molecule_filename == p.molecule_filename)
        assert p.geometry_source == "parent:" + parent.id
        assert p.parent_id == parent.id


def test_expansion_is_idempotent(tmp_path):
    proj = _project(tmp_path, n_mols=2)
    w = wf_mod.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    proj.workflow = w.to_dict()

    r1 = wexp.expand_project_workflow(proj)
    assert r1["new"] == 2
    n_after_first = len(proj.planned_calcs)
    r2 = wexp.expand_project_workflow(proj)
    assert r2["new"] == 0 and r2["reused"] == 2       # reused, not duplicated
    assert len(proj.planned_calcs) == n_after_first


def test_transform_materialises_locked_molecule_and_xyz(tmp_path):
    proj = _project(tmp_path, n_mols=1)
    w = wf_mod.Workflow()
    m = w.add_node("molecules")
    tr = w.add_node("transform", config={"ops": [{"op": "translate", "vec": [10.0, 0.0, 0.0]}]})
    prop = w.add_node("property", config={"recipe": "SP"})
    w.add_edge(m.id, "geometry", tr.id, "geometry")
    w.add_edge(tr.id, "geometry", prop.id, "geometry")
    proj.workflow = w.to_dict()

    res = wexp.expand_project_workflow(proj)
    assert res["expanded"] and res["new"] == 1
    # a derived molecule "000_tf<node>" was materialised: locked, on disk, shifted
    derived = [mol for mol in proj.molecules if mol.filename.startswith("000_tf")]
    assert len(derived) == 1
    d = derived[0]
    assert d.coords_locked and d.method == "transform"
    p = os.path.join(proj.root(), d.xyz_path)
    assert os.path.isfile(p)
    atoms, _ = coords_mod.read_xyz(p)
    assert abs(atoms[0][1] - 10.0) < 1e-6           # O shifted +10 in x
    # the calc runs on the derived molecule with an initial (own-xyz) geometry
    calc = next(c for c in proj.planned_calcs if c.origin_node == prop.id)
    assert calc.molecule_filename == d.filename and calc.geometry_source == "initial"


def test_missing_recipe_is_a_blocker(tmp_path):
    proj = _project(tmp_path, n_mols=1)
    w = wf_mod.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": ""})   # no recipe selected
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    proj.workflow = w.to_dict()

    res = wexp.expand_project_workflow(proj)
    assert res["expanded"] is False and res["blockers"]
    assert any("recipe" in b for b in res["blockers"])
    assert proj.planned_calcs == []


def test_combine_without_charge_mult_is_a_blocker(tmp_path):
    proj = _project(tmp_path, n_mols=2)
    w = wf_mod.Workflow()
    m1 = w.add_node("molecules", config={"mode": "selection", "filenames": ["000"]})
    m2 = w.add_node("molecules", config={"mode": "selection", "filenames": ["001"]})
    comb = w.add_node("combine", config={"name": "dimer"})
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m1.id, "geometry", comb.id, "geometry")
    w.add_edge(m2.id, "geometry", comb.id, "geometry")
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    proj.workflow = w.to_dict()

    res = wexp.expand_project_workflow(proj)
    assert res["expanded"] is False
    assert any("charge" in b for b in res["blockers"])


def test_combine_with_charge_mult_materialises_dimer(tmp_path):
    proj = _project(tmp_path, n_mols=2)
    w = wf_mod.Workflow()
    m1 = w.add_node("molecules", config={"mode": "selection", "filenames": ["000"]})
    m2 = w.add_node("molecules", config={"mode": "selection", "filenames": ["001"]})
    comb = w.add_node("combine", config={"name": "dimer", "charge": 0, "mult": 1})
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m1.id, "geometry", comb.id, "geometry")
    w.add_edge(m2.id, "geometry", comb.id, "geometry")
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    proj.workflow = w.to_dict()

    res = wexp.expand_project_workflow(proj)
    assert res["expanded"] and res["new"] == 1
    dimer = next(mol for mol in proj.molecules if mol.filename.startswith("dimer"))
    p = os.path.join(proj.root(), dimer.xyz_path)
    atoms, _ = coords_mod.read_xyz(p)
    assert len(atoms) == 6            # two waters merged
    assert dimer.charge == 0 and dimer.multiplicity == 1
