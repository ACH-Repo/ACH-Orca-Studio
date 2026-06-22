"""Tests for core.headless (single-.inp run/submit). sbatch and the local ORCA
call are mocked, so these run anywhere with no cluster and no ORCA install.

Run:  python -m pytest tests/test_headless.py -q
"""

import os

from orca_workbench.core import headless, slurm_runtime


INP = ("! HF def2-SVP EnGrad\n%pal nprocs 4\nend\n"
       "* xyz 0 1\n  H 0.0 0.0 0.0\n*\n")


def write_inp(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(INP)
    return str(path)


def test_missing_file(tmp_path):
    assert headless.run_infile(str(tmp_path / "nope.inp")).startswith("error")


def test_rejects_non_inp(tmp_path):
    p = tmp_path / "thing.txt"
    p.write_text("hello")
    assert headless.run_infile(str(p)).startswith("error")


def test_slurm_submit_writes_script_and_uses_cores(tmp_path, monkeypatch):
    inp = write_inp(tmp_path / "job.inp")
    seen = {}

    def fake_submit(slurm_rel, root, dependency=None):
        seen["slurm_rel"], seen["root"] = slurm_rel, root
        return ("99999", None)

    monkeypatch.setattr(slurm_runtime, "submit", fake_submit)
    msg = headless.run_infile(inp, force="slurm", usermail="")

    assert "submitted" in msg and "99999" in msg
    assert seen["slurm_rel"] == "job.slurm" and seen["root"] == str(tmp_path)
    slurm_text = (tmp_path / "job.slurm").read_text()
    assert "ntasks-per-node=4" in slurm_text          # cores read from %pal nprocs 4
    assert "job.inp" in slurm_text


def test_slurm_submit_reports_failure(tmp_path, monkeypatch):
    inp = write_inp(tmp_path / "job.inp")
    monkeypatch.setattr(slurm_runtime, "submit",
                        lambda *a, **k: (None, "QOS limit exceeded"))
    msg = headless.run_infile(inp, force="slurm", usermail="")
    assert "failed" in msg and "QOS" in msg


def test_local_run(tmp_path, monkeypatch):
    inp = write_inp(tmp_path / "j.inp")

    class _Proc:
        returncode = 0

    monkeypatch.setattr(headless.subprocess, "run", lambda *a, **k: _Proc())
    msg = headless.run_infile(inp, force="local")
    assert "ran" in msg and "locally" in msg
    assert os.path.isfile(str(tmp_path / "j.out"))


def test_local_orca_missing(tmp_path, monkeypatch):
    inp = write_inp(tmp_path / "k.inp")

    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(headless.subprocess, "run", boom)
    msg = headless.run_infile(inp, force="local")
    assert msg.startswith("error") and "ORCA" in msg
