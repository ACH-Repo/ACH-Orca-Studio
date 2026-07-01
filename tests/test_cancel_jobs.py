"""Tests for slurm_runtime.cancel_jobs (the scancel helper behind Interrupt).

Pure/offline: subprocess.run is mocked so no real scancel is invoked.

Run:  python -m pytest tests/test_cancel_jobs.py -q
"""

from orca_workbench.core import slurm_runtime as SR


class _Result(object):
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_empty_is_noop():
    assert SR.cancel_jobs([]) == (0, [])
    assert SR.cancel_jobs([None, ""]) == (0, [])


def test_success_one_scancel_call(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _Result(returncode=0)

    monkeypatch.setattr(SR.subprocess, "run", fake_run)
    n, errs = SR.cancel_jobs([101, "102"])
    assert (n, errs) == (2, [])
    assert seen["cmd"] == ["scancel", "101", "102"]   # ids stringified, one call


def test_nonzero_returns_error(monkeypatch):
    monkeypatch.setattr(SR.subprocess, "run",
                        lambda *a, **k: _Result(returncode=1, stderr="Invalid job id 999"))
    n, errs = SR.cancel_jobs([999])
    assert n == 0 and errs and "Invalid job id 999" in errs[0]


def test_scancel_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(SR.subprocess, "run", boom)
    n, errs = SR.cancel_jobs([1])
    assert n == 0 and "scancel not found" in errs[0]
