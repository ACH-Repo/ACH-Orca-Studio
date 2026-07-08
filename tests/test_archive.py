"""Archive Export core (core.archive): result collection, packaging (stdlib tar/
zip + external-tool command building), and the end-to-end export.

Run:  python -m pytest tests/test_archive.py -q
"""

import os
import tarfile
import zipfile

import pytest

from orca_workbench.core import archive
from orca_workbench.core.project import Project, Molecule, PlannedCalc, save_project


class _FakeRecipe(object):
    def __init__(self, calctype, method="HF_STO-3G"):
        self.calctype = calctype
        self.method_label = method


def _finished_project(tmp_path, calctypes=("OPT", "NMR"), energies=(-76.02, -76.01)):
    """A saved project whose calcs each have a run dir + a fake .out carrying a
    FINAL SINGLE POINT ENERGY. Returns (project, recipe_by_name)."""
    (tmp_path / "XYZ_INI").mkdir()
    (tmp_path / "XYZ_INI" / "000.xyz").write_text("1\nwater\nO 0 0 0\n")
    proj = Project(path=str(tmp_path / "proj.json"))
    proj.molecules.append(Molecule(name="water", filename="000", smiles="O",
                                   xyz_path="XYZ_INI/000.xyz"))
    rbn = {}
    for i, (ct, e) in enumerate(zip(calctypes, energies)):
        rundir = "calcs/000/gen/{}/HF/t".format(ct)
        (tmp_path / rundir).mkdir(parents=True, exist_ok=True)
        (tmp_path / rundir / "000-local.out").write_text(
            "FINAL SINGLE POINT ENERGY  {:.6f}\n**** ORCA TERMINATED NORMALLY ****\n"
            .format(e))
        rname = "{} recipe".format(ct)
        proj.planned_calcs.append(PlannedCalc(
            id="c{}".format(i), molecule_filename="000", recipe_name=rname,
            category="gen", rundir=rundir, job_id="local", exported=True))
        rbn[rname] = _FakeRecipe(ct)
    save_project(proj)
    return proj, rbn


# --------------------------------------------------------------- format helpers

def test_normalize_and_external():
    assert archive.normalize_format(".TGZ") == "tar.gz"
    assert archive.normalize_format("ZIP") == "zip"
    assert archive.normalize_format(".tar.bz2") == "tar.bz2"
    assert not archive.format_needs_external("tar.gz")
    assert not archive.format_needs_external("zip")
    assert archive.format_needs_external("7z")
    assert archive.format_needs_external("rar")


# ---------------------------------------------------------------- collector

def test_collect_results_and_energy(tmp_path):
    proj, rbn = _finished_project(tmp_path)
    records = archive.collect_results(proj, rbn)
    assert len(records) == 2
    r0 = next(r for r in records if r["calctype"] == "OPT")
    assert r0["molecule"] == "000" and r0["smiles"] == "O" and r0["finished"]
    assert abs(archive.final_energy(r0) - (-76.02)) < 1e-9
    assert archive.terminated_ok(r0)


def test_collect_results_unfinished_calc(tmp_path):
    proj, rbn = _finished_project(tmp_path)
    # a calc with no rundir/job_id -> no out_path, final_energy None
    proj.planned_calcs.append(PlannedCalc(id="x", molecule_filename="000",
                                          recipe_name="OPT recipe", category="gen"))
    records = archive.collect_results(proj, rbn)
    unfinished = next(r for r in records if r["calc_id"] == "x")
    assert unfinished["out_path"] is None and not unfinished["finished"]
    assert archive.final_energy(unfinished) is None


# ---------------------------------------------------------------- packaging

def _make_tree(tmp_path):
    (tmp_path / "XYZ_INI").mkdir()
    (tmp_path / "XYZ_INI" / "000.xyz").write_text("data")
    (tmp_path / "proj.json").write_text("{}")
    (tmp_path / "calcs").mkdir()
    (tmp_path / "calcs" / "a.out").write_text("out")


def test_create_archive_targz_roundtrip(tmp_path):
    _make_tree(tmp_path)
    out = str(tmp_path / "bundle.tar.gz")
    archive.create_archive(out, str(tmp_path), ["proj.json", "XYZ_INI", "calcs"],
                           fmt="tar.gz")
    with tarfile.open(out) as tf:
        names = tf.getnames()
    # everything nested under one arc_root folder (the archive base name)
    assert any(n.endswith("bundle/proj.json") for n in names)
    assert any(n.endswith("bundle/XYZ_INI/000.xyz") for n in names)
    assert any(n.endswith("bundle/calcs/a.out") for n in names)


def test_create_archive_zip_roundtrip(tmp_path):
    _make_tree(tmp_path)
    out = str(tmp_path / "bundle.zip")
    archive.create_archive(out, str(tmp_path), ["proj.json", "calcs"], fmt="zip")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert any(n.endswith("bundle/proj.json") for n in names)
    assert any(n.endswith("bundle/calcs/a.out") for n in names)


def test_existing_members_filters_missing(tmp_path):
    _make_tree(tmp_path)
    got = archive.existing_members(str(tmp_path), ["proj.json", "NOPE", "calcs"])
    assert got == ["proj.json", "calcs"]


def test_create_archive_empty_members_raises(tmp_path):
    with pytest.raises(ValueError):
        archive.create_archive(str(tmp_path / "x.tar.gz"), str(tmp_path), ["nope"])


def test_create_archive_external_tool_command(tmp_path, monkeypatch):
    _make_tree(tmp_path)
    calls = {}

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        return type("P", (), {"returncode": 0, "stdout": b""})()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = str(tmp_path / "bundle.7z")
    archive.create_archive(out, str(tmp_path), ["proj.json", "calcs"], fmt="7z",
                           external_tool="7z")
    assert calls["cmd"][:2] == ["7z", "a"]
    assert calls["cmd"][2] == os.path.abspath(out)
    assert "proj.json" in calls["cmd"] and "calcs" in calls["cmd"]
    assert calls["cwd"] == str(tmp_path)


def test_create_archive_external_needed_without_tool_raises(tmp_path):
    _make_tree(tmp_path)
    with pytest.raises(ValueError):
        archive.create_archive(str(tmp_path / "b.7z"), str(tmp_path), ["proj.json"],
                               fmt="7z")


# ---------------------------------------------------------------- end to end

def test_export_archive_includes_figures(tmp_path):
    pytest.importorskip("matplotlib")
    proj, rbn = _finished_project(tmp_path)
    out = str(tmp_path / "export.tar.gz")
    summary = archive.export_archive(proj, rbn, out, fmt="tar.gz",
                                     include_figures=True, img_format="png")
    assert os.path.isfile(out)
    assert summary["n_figures"] >= 1                       # the SCF bar chart at least
    assert os.path.isdir(str(tmp_path / "FIGS_EXPORT"))
    with tarfile.open(out) as tf:
        names = tf.getnames()
    assert any("FIGS_EXPORT" in n for n in names)
    assert any(n.endswith("proj.json") for n in names)
    assert any("calcs" in n for n in names)


def test_export_archive_no_figures(tmp_path):
    proj, rbn = _finished_project(tmp_path)
    out = str(tmp_path / "export.zip")
    summary = archive.export_archive(proj, rbn, out, fmt="zip", include_figures=False)
    assert summary["n_figures"] == 0
    assert not (tmp_path / "FIGS_EXPORT").exists()
    assert os.path.isfile(out)
