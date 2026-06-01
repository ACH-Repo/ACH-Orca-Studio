"""Submit jobs and query their status via the SLURM CLI.

These shell out to `sbatch` and `squeue`, which only exist on the cluster.
Every function degrades gracefully (returns an error string / None) when the
commands aren't found, so the app still runs locally for composing files.
"""

import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple


_SUBMITTED_RE = re.compile(r"Submitted batch job (\d+)")


def sbatch_available():
    # type: () -> bool
    try:
        subprocess.run(["sbatch", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def submit(slurm_rel_path, project_root, dependency=None):
    # type: (str, str, Optional[str]) -> Tuple[Optional[str], Optional[str]]
    """sbatch a slurm file. `slurm_rel_path` is relative to project_root, which
    is also the cwd sbatch runs in (so the script's `cd <rundir>` resolves).
    `dependency`, if given, is a SLURM dependency spec (e.g. 'afterok:123:456')
    passed as --dependency, so the job is held until its predecessors finish —
    that's what lets a workflow run unattended after you disconnect.
    Returns (job_id, None) on success or (None, error_message)."""
    cmd = ["sbatch"]
    if dependency:
        cmd.append("--dependency=" + dependency)
        cmd.append("--kill-on-invalid-dep=yes")  # cancel if a predecessor fails
    cmd.append(slurm_rel_path)
    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return None, "sbatch not found — are you on the cluster login node?"
    except subprocess.TimeoutExpired:
        return None, "sbatch timed out"
    except Exception as e:
        return None, "sbatch error: {}".format(e)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "sbatch failed").strip()
    m = _SUBMITTED_RE.search(result.stdout or "")
    if m:
        return m.group(1), None
    return None, "Could not parse job id from sbatch output:\n" + (result.stdout or "").strip()


def query_states(user=None):
    # type: (Optional[str]) -> Optional[Dict[str, str]]
    """Return {job_id: state} for the user's queued/running jobs via squeue.
    States are SLURM's: PENDING, RUNNING, COMPLETING, etc. Jobs that have
    finished drop out of squeue, so a previously-submitted id missing from the
    result means it's done (check the .out to tell success from failure).
    Returns None if squeue isn't available."""
    user = user or os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    cmd = ["squeue", "--noheader", "-o", "%i %T"]
    if user:
        cmd += ["-u", user]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return {}
    except Exception:
        return None
    states = {}
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1]
    return states


def find_output_file(rundir_abs, jobname, job_id):
    # type: (str, str, str) -> Optional[str]
    """Locate the SLURM .out for a job. The template writes
    <rundir>/<jobname>-<jobid>.out (#SBATCH --output=RUNDIR/%x-%j.out).
    Falls back to a glob if the exact name isn't present yet."""
    exact = os.path.join(rundir_abs, "{}-{}.out".format(jobname, job_id))
    if os.path.isfile(exact):
        return exact
    # Fall back: any *-<jobid>.out, then any <jobname>-*.out
    try:
        candidates = os.listdir(rundir_abs)
    except OSError:
        return None
    for fn in candidates:
        if fn.endswith("-{}.out".format(job_id)):
            return os.path.join(rundir_abs, fn)
    for fn in candidates:
        if fn.startswith(jobname + "-") and fn.endswith(".out"):
            return os.path.join(rundir_abs, fn)
    return None
