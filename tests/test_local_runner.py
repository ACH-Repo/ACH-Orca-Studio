"""Tests for core.local_runner — mainly that ORCA is invoked by FULL pathname.

ORCA refuses to run in parallel (%pal nprocs>1) unless argv[0] is an absolute path
(it derives its install dir from argv[0] to find the MPI launcher). The runner must
therefore resolve the configured executable to an absolute path.

Run:  python -m pytest tests/test_local_runner.py -q
"""

import os

from orca_workbench.core import local_runner as LR


def test_abs_exe_keeps_absolute():
    p = os.path.abspath(os.path.join("some", "dir", "orca.exe"))
    assert LR.resolve_orca_exe(p) == p


def test_abs_exe_resolves_bare_name_via_path(monkeypatch):
    # A bare 'orca' (relying on PATH) must be resolved to its real full path.
    fake = os.path.abspath(os.path.join(os.sep + "opt", "orca", "orca"))
    monkeypatch.setattr(LR.shutil, "which", lambda name: fake)
    assert LR.resolve_orca_exe("orca") == fake


def test_abs_exe_bare_name_not_on_path_falls_back_to_abspath(monkeypatch):
    monkeypatch.setattr(LR.shutil, "which", lambda name: None)
    out = LR.resolve_orca_exe("orca")
    assert os.path.isabs(out) and out.endswith("orca")


def test_abs_exe_relative_path_made_absolute():
    out = LR.resolve_orca_exe(os.path.join(".", "bin", "orca"))
    assert os.path.isabs(out)


def test_runner_stores_absolute_exe(monkeypatch):
    monkeypatch.setattr(LR.shutil, "which", lambda name: None)
    r = LR.LocalRunner("orca", max_concurrent=2)
    assert r.orca_exe == "orca"              # identity preserved for the compare
    assert os.path.isabs(r._orca_abs)        # but we invoke the absolute form


def test_abs_exe_empty_is_noop():
    assert LR.resolve_orca_exe("") == ""
