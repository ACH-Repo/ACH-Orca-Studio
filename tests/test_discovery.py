"""Tests for core.discovery (relink / import) and the slurm_runtime helpers
they rely on. No cluster or display needed: projects are built in memory and
job output is simulated with temp files.

Run from the repo root:  python -m pytest tests/ -q
"""

import os

import pytest

from orca_workbench.core import discovery, slurm_runtime
from orca_workbench.core.project import Project, Molecule, PlannedCalc


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _project(tmp_path):
    """An empty project rooted at tmp_path (root() -> tmp_path)."""
    return Project(path=str(tmp_path / "project.json"))


def _calc(mol, rundir, job_id=None):
    return PlannedCalc(id=mol, molecule_filename=mol, recipe_name="r",
                       rundir=rundir, exported=True, job_id=job_id)


def _touch(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


NMR_INP = (
    "! M062X pcSseg-2 RIJCOSX CPCM(DMSO) NMR EnGrad VeryTightSCF DEFGRID3\n"
    "%pal nprocs 8\nend\n"
    "* xyz 0 1\n  F 0.0 0.0 0.0\n  H 0.0 0.0 0.9\n*\n"
    "%eprnmr\n NUCLEI = ALL F {SHIFT}\nEND\n"
)


# --------------------------------------------------------------------------
# relink_project
# --------------------------------------------------------------------------
def test_relink_from_output_file(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("x", "calcs/x"))
    _touch(str(tmp_path / "calcs" / "x" / "x-12345.out"))

    s = discovery.relink_project(proj)

    assert proj.planned_calcs[0].job_id == "12345"
    assert s["from_files"] == 1 and s["changed"] == 1 and s["from_queue"] == 0
    assert proj.planned_calcs[0].exported is True


def test_relink_picks_newest_file(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("x", "calcs/x"))
    old = _touch(str(tmp_path / "calcs" / "x" / "x-111.out"))
    new = _touch(str(tmp_path / "calcs" / "x" / "x-222.out"))
    os.utime(old, (1_000_000, 1_000_000))   # force old < new
    os.utime(new, (2_000_000, 2_000_000))

    discovery.relink_project(proj)
    assert proj.planned_calcs[0].job_id == "222"


def test_relink_uses_err_when_no_out(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("x", "calcs/x"))
    _touch(str(tmp_path / "calcs" / "x" / "x-777.err"))

    discovery.relink_project(proj)
    assert proj.planned_calcs[0].job_id == "777"


def test_relink_from_squeue_map(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("pending", "calcs/p"))  # no files on disk
    os.makedirs(str(tmp_path / "calcs" / "p"))

    s = discovery.relink_project(proj, name_to_jobid={"pending": ("9001", "PENDING")})

    assert proj.planned_calcs[0].job_id == "9001"
    assert s["from_queue"] == 1 and s["from_files"] == 0


def test_relink_files_take_priority_over_queue(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("x", "calcs/x"))
    _touch(str(tmp_path / "calcs" / "x" / "x-555.out"))

    discovery.relink_project(proj, name_to_jobid={"x": "999"})
    assert proj.planned_calcs[0].job_id == "555"   # file wins


def test_relink_skips_already_linked(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("x", "calcs/x", job_id="42"))
    _touch(str(tmp_path / "calcs" / "x" / "x-999.out"))

    s = discovery.relink_project(proj)
    assert proj.planned_calcs[0].job_id == "42"     # untouched
    assert s["already"] == 1 and s["changed"] == 0


def test_relink_all_overrides_existing(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("x", "calcs/x", job_id="42"))
    _touch(str(tmp_path / "calcs" / "x" / "x-999.out"))

    discovery.relink_project(proj, relink_all=True)
    assert proj.planned_calcs[0].job_id == "999"


def test_relink_reports_unlinked(tmp_path):
    proj = _project(tmp_path)
    proj.planned_calcs.append(_calc("ghost", "calcs/ghost"))  # nothing on disk
    os.makedirs(str(tmp_path / "calcs" / "ghost"))

    s = discovery.relink_project(proj)
    assert s["changed"] == 0 and "ghost" in s["unlinked"]


# --------------------------------------------------------------------------
# unlinked_with_output (the open-time prompt signal)
# --------------------------------------------------------------------------
def test_unlinked_with_output_distinguishes_built_from_submitted(tmp_path):
    proj = _project(tmp_path)
    # (a) merely built: only .inp/.slurm, no output -> should NOT flag
    proj.planned_calcs.append(_calc("built", "calcs/built"))
    _touch(str(tmp_path / "calcs" / "built" / "built.inp"))
    # (b) submitted out-of-band: output present, no job_id -> SHOULD flag
    proj.planned_calcs.append(_calc("ran", "calcs/ran"))
    _touch(str(tmp_path / "calcs" / "ran" / "ran-1.out"))
    # (c) already linked -> should NOT flag
    proj.planned_calcs.append(_calc("done", "calcs/done", job_id="7"))
    _touch(str(tmp_path / "calcs" / "done" / "done-7.out"))

    flagged = [c.molecule_filename for c in discovery.unlinked_with_output(proj)]
    assert flagged == ["ran"]


# --------------------------------------------------------------------------
# recipe reconstruction from an .inp
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kw,expected", [
    ("! M062X pcSseg-2 NMR EnGrad", "NMR"),
    ("! BP86 def2-TZVP D3BJ TightOpt", "OPT"),
    ("! PBE0 def2-SVP Freq", "FREQ"),
    ("! r2SCAN-3c EnGrad", "ENGRAD"),
    ("! B3LYP def2-TZVP", "SP"),
])
def test_infer_calctype(kw, expected):
    r = discovery.recipe_from_inp(kw + "\n* xyz 0 1\n  H 0 0 0\n*\n")
    assert r.calctype == expected


def test_recipe_template_has_placeholder(tmp_path):
    r = discovery.recipe_from_inp(NMR_INP)
    assert discovery.COORDS_PLACEHOLDER in r.template
    assert "* xyz" not in r.template            # coords were abstracted out
    assert r.method_label == "M062X_pcSseg-2"
    assert r.variant == "imported"


# --------------------------------------------------------------------------
# import_dir
# --------------------------------------------------------------------------
def test_import_dir_creates_molecules_and_calcs(tmp_path):
    proj = _project(tmp_path)
    run = tmp_path / "ext" / "run1"
    _touch(str(run / "mol_a.inp"), NMR_INP)
    _touch(str(run / "mol_a-777.out"), "ORCA TERMINATED NORMALLY")
    _touch(str(run / "mol_b.inp"), NMR_INP)               # no output beside it

    s = discovery.import_dir(proj, str(tmp_path / "ext"))

    assert s["scanned"] == 2 and s["imported"] == 2 and s["with_output"] == 1
    by = {c.molecule_filename: c for c in proj.planned_calcs}
    assert by["mol_a"].job_id == "777"
    assert by["mol_b"].job_id is None
    assert by["mol_a"].geometry_source.startswith("file:")
    assert by["mol_a"].rundir.replace("\\", "/").endswith("ext/run1")
    assert len(proj.molecules) == 2
    assert len(s["new_recipes"]) == 1                     # both share one recipe


def test_import_dir_plain_out_gets_sentinel(tmp_path):
    proj = _project(tmp_path)
    run = tmp_path / "ext"
    _touch(str(run / "c.inp"), NMR_INP)
    _touch(str(run / "c.out"), "data")                   # plainly named output

    discovery.import_dir(proj, str(run))
    calc = proj.planned_calcs[0]
    assert calc.job_id == "imported"                     # truthy -> harvestable
    # and find_output_file resolves the plain .out via the new fallback
    out = slurm_runtime.find_output_file(
        os.path.join(proj.root(), calc.rundir), "c", calc.job_id)
    assert out and os.path.basename(out) == "c.out"


def test_import_dir_skips_already_present(tmp_path):
    proj = _project(tmp_path)
    _touch(str(tmp_path / "ext" / "m.inp"), NMR_INP)

    discovery.import_dir(proj, str(tmp_path / "ext"))
    s2 = discovery.import_dir(proj, str(tmp_path / "ext"))   # re-import

    assert s2["imported"] == 0 and s2["skipped"] == 1
    assert len(proj.planned_calcs) == 1


def test_import_dir_parses_charge_and_multiplicity(tmp_path):
    proj = _project(tmp_path)
    inp = "! B3LYP def2-SVP\n* xyz -2 3\n  O 0 0 0\n*\n"
    _touch(str(tmp_path / "ext" / "anion.inp"), inp)

    discovery.import_dir(proj, str(tmp_path / "ext"))
    mol = proj.molecules[0]
    assert mol.charge == -2 and mol.multiplicity == 3


def test_import_dir_disambiguates_duplicate_names(tmp_path):
    proj = _project(tmp_path)
    _touch(str(tmp_path / "ext" / "a" / "dup.inp"), NMR_INP)
    _touch(str(tmp_path / "ext" / "b" / "dup.inp"), NMR_INP)

    discovery.import_dir(proj, str(tmp_path / "ext"))
    names = sorted(m.filename for m in proj.molecules)
    assert names == ["dup", "dup_2"]


def test_import_dir_invokes_save_recipe_once_per_recipe(tmp_path):
    proj = _project(tmp_path)
    _touch(str(tmp_path / "ext" / "x.inp"), NMR_INP)
    _touch(str(tmp_path / "ext" / "y.inp"), NMR_INP)      # same level -> same recipe
    saved = []

    discovery.import_dir(proj, str(tmp_path / "ext"), save_recipe=lambda r: saved.append(r.name))
    assert len(saved) == 1                                # de-duplicated


# --------------------------------------------------------------------------
# slurm_runtime helpers
# --------------------------------------------------------------------------
def test_find_output_file_prefers_exact_over_plain(tmp_path):
    d = tmp_path
    _touch(str(d / "j-5.out"))
    _touch(str(d / "j.out"))
    assert slurm_runtime.find_output_file(str(d), "j", "5").endswith("j-5.out")


def test_find_output_file_plain_fallback(tmp_path):
    d = tmp_path
    _touch(str(d / "j.out"))
    got = slurm_runtime.find_output_file(str(d), "j", "no-such-id")
    assert got and os.path.basename(got) == "j.out"


def test_query_name_map_parses_squeue(monkeypatch):
    class _R:
        stdout = "111 jobA RUNNING\n222 jobB PENDING\n"
    monkeypatch.setattr(slurm_runtime.subprocess, "run", lambda *a, **k: _R())
    m = slurm_runtime.query_name_map(user="me")
    assert m == {"jobA": ("111", "RUNNING"), "jobB": ("222", "PENDING")}


def test_query_name_map_none_without_squeue(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(slurm_runtime.subprocess, "run", _boom)
    assert slurm_runtime.query_name_map() is None
