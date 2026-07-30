"""Run ORCA jobs locally, one (or a few) at a time — the PC counterpart to
submitting to SLURM.

A few-core laptop can't sensibly run several multi-core ORCA jobs at once, so
this is a simple serial queue: it launches up to `max_concurrent` jobs (default
1) and starts the next only as a slot frees. Each job runs `orca <input>` in its
own run directory with stdout redirected to `<mol>-local.out` — the same naming
the SLURM path uses, so the live plot, status parsing, and reports all work
unchanged.

This module is Tkinter-free: it manages subprocesses and state, and exposes
poll() to be called from the GUI's event loop (no background threads — the
caller schedules poll() via tk's after()).
"""

import os
import shutil
import subprocess
from typing import Dict, List, Optional


def resolve_orca_exe(exe):
    # type: (str) -> str
    """Absolute path to the ORCA executable. ORCA MUST be launched by full pathname for
    parallel (%pal nprocs>1) runs: it derives its own install directory from argv[0] to
    find the MPI launcher and the orca_* helper binaries. A bare name ('orca') or a
    relative path trips 'ERROR (ORCA_MAIN): For parallel runs ORCA has to be called with
    full pathname'. Resolve a bare name via PATH; make anything else absolute."""
    if not exe:
        return exe
    if os.path.isabs(exe):
        return exe
    # Bare command name with no directory -> look it up on PATH.
    if os.path.dirname(exe) == "":
        found = shutil.which(exe)
        if found:
            return os.path.abspath(found)
    return os.path.abspath(exe)


# Per-job lifecycle states this runner reports.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"          # process exited 0 (check the .out for true convergence)
FAILED = "failed"      # process exited non-zero / couldn't launch
CANCELLED = "cancelled"


class _Job(object):
    def __init__(self, calc_id, inp_abs, out_abs, rundir_abs):
        self.calc_id = calc_id
        self.inp_abs = inp_abs
        self.out_abs = out_abs
        self.rundir_abs = rundir_abs
        self.state = QUEUED
        self.proc = None        # type: Optional[subprocess.Popen]
        self._out_fh = None
        self.returncode = None


class LocalRunner(object):
    def __init__(self, orca_exe, max_concurrent=1):
        # type: (str, int) -> None
        self.orca_exe = orca_exe                 # as configured (for identity/compare)
        self._orca_abs = resolve_orca_exe(orca_exe)   # what we actually invoke (full path)
        self.max_concurrent = max(1, int(max_concurrent))
        self._jobs = {}         # type: Dict[str, _Job]
        self._order = []        # type: List[str]  (queue order)

    # ---- queue management ----

    def submit(self, calc_id, inp_abs, out_abs, rundir_abs):
        # type: (str, str, str, str) -> None
        """Add a job to the queue (or re-queue an existing calc id)."""
        job = _Job(calc_id, inp_abs, out_abs, rundir_abs)
        self._jobs[calc_id] = job
        if calc_id not in self._order:
            self._order.append(calc_id)

    def state(self, calc_id):
        # type: (str) -> Optional[str]
        job = self._jobs.get(calc_id)
        return job.state if job else None

    def busy(self):
        # type: () -> bool
        return any(j.state in (QUEUED, RUNNING) for j in self._jobs.values())

    def n_running(self):
        return sum(1 for j in self._jobs.values() if j.state == RUNNING)

    # ---- driving (called from the GUI event loop) ----

    def poll(self):
        # type: () -> List[str]
        """Reap finished processes and launch queued ones up to the concurrency
        limit. Returns the calc ids whose state changed this tick."""
        changed = []
        # 1) reap finished
        for job in self._jobs.values():
            if job.state == RUNNING and job.proc is not None:
                rc = job.proc.poll()
                if rc is not None:
                    job.returncode = rc
                    job.state = DONE if rc == 0 else FAILED
                    self._close(job)
                    changed.append(job.calc_id)
        # 2) launch queued while slots are free, in submission order
        free = self.max_concurrent - self.n_running()
        for cid in self._order:
            if free <= 0:
                break
            job = self._jobs.get(cid)
            if job is None or job.state != QUEUED:
                continue
            self._start(job)
            changed.append(cid)
            free -= 1
        return changed

    def _start(self, job):
        # type: (_Job) -> None
        try:
            env = dict(os.environ)
            # ORCA needs its own directory on PATH to find its shared libraries, and
            # must be invoked by FULL pathname for parallel runs (see _abs_exe).
            orca_dir = os.path.dirname(self._orca_abs)
            if orca_dir:
                env["PATH"] = orca_dir + os.pathsep + env.get("PATH", "")
            job._out_fh = open(job.out_abs, "w", encoding="utf-8", errors="replace")
            job.proc = subprocess.Popen(
                [self._orca_abs, os.path.basename(job.inp_abs)],
                cwd=job.rundir_abs,
                stdout=job._out_fh,
                stderr=subprocess.STDOUT,
                env=env,
            )
            job.state = RUNNING
        except Exception as e:
            # Couldn't even launch — record the reason in the .out so the user sees it.
            job.state = FAILED
            try:
                if job._out_fh is None:
                    job._out_fh = open(job.out_abs, "w", encoding="utf-8", errors="replace")
                job._out_fh.write("ORCA Workbench local runner failed to launch:\n{}\n".format(e))
            except Exception:
                pass
            self._close(job)

    def _close(self, job):
        if job._out_fh is not None:
            try:
                job._out_fh.flush()
                job._out_fh.close()
            except Exception:
                pass
            job._out_fh = None

    # ---- cancellation ----

    def cancel(self, calc_id):
        # type: (str) -> None
        job = self._jobs.get(calc_id)
        if job is None:
            return
        if job.state == RUNNING and job.proc is not None:
            try:
                job.proc.terminate()
            except Exception:
                pass
        if job.state in (QUEUED, RUNNING):
            job.state = CANCELLED
        self._close(job)

    def cancel_all(self):
        for cid in list(self._jobs.keys()):
            self.cancel(cid)

    def forget(self, calc_id):
        """Drop a job from the runner's memory (e.g. when re-running it)."""
        self._jobs.pop(calc_id, None)
        if calc_id in self._order:
            self._order.remove(calc_id)
