"""Run or submit a single ORCA .inp without opening the GUI.

When you just have one input file and a terminal (no X / ThinLinc),
`orca-workbench --run my.inp` does the right thing for the environment:

  * On a SLURM login node it wraps the .inp in the app's standard submit
    template (node-local /scratch staging, copy-back, walltime trap) and sbatches
    it — so the job runs on a compute node, NOT on the gateway. This is the value
    over a bare `orca my.inp`: on a cluster you must not run ORCA on the login node.
  * On a plain machine (no sbatch) it runs ORCA directly: `orca my.inp > my.out`.

Reuses the same template + settings the GUI uses; no project file needed.
"""

import os
import subprocess

from orca_workbench.core import config as config_mod
from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core import slurm as slurm_mod
from orca_workbench.core import slurm_runtime


def run_infile(inp_path, cores=None, usermail=None, force=None):
    """Run/submit one .inp. `force` is None (auto-detect), 'slurm', or 'local'.
    Returns a one-line human-readable status string (starts with 'error' / says
    'failed' on failure)."""
    inp_path = os.path.abspath(inp_path)
    if not os.path.isfile(inp_path):
        return "error: no such file: {}".format(inp_path)
    if not inp_path.lower().endswith(".inp"):
        return "error: not a .inp file: {}".format(inp_path)

    rundir = os.path.dirname(inp_path)
    inp_name = os.path.basename(inp_path)
    base = os.path.splitext(inp_name)[0]
    try:
        with open(inp_path, "r", encoding="utf-8", errors="replace") as fh:
            inp_text = fh.read()
    except OSError as e:
        return "error: could not read {}: {}".format(inp_name, e)
    cores = cores or inputs_mod.parse_cores(inp_text)

    use_slurm = slurm_runtime.sbatch_available() if force is None else (force == "slurm")
    if use_slurm:
        return _submit(rundir, inp_name, base, cores, usermail)
    return _run_local(rundir, inp_name, base)


def _submit(rundir, inp_name, base, cores, usermail):
    try:
        template = slurm_mod.load_template()
    except Exception as e:
        return "error: slurm template missing: {}".format(e)
    mail = usermail if usermail is not None else (config_mod.get("usermail", "") or "")
    slurm_text = slurm_mod.render_slurm(
        template, inp_filename=inp_name, rundir=".", jobname=base, cores=cores, usermail=mail)
    slurm_path = os.path.join(rundir, base + ".slurm")
    try:
        with open(slurm_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(slurm_text)
    except OSError as e:
        return "error: could not write {}: {}".format(slurm_path, e)
    # sbatch runs in `rundir`, so the script's `cd .` and #SBATCH --output=./...
    # resolve there; results land next to the .inp.
    job_id, err = slurm_runtime.submit(base + ".slurm", rundir)
    if job_id:
        return ("submitted {} as SLURM job {} ({} cores); output -> "
                "{}".format(inp_name, job_id, cores,
                            os.path.join(rundir, "{}-{}.out".format(base, job_id))))
    return "submit failed: {}".format(err)


def _run_local(rundir, inp_name, base):
    orca = config_mod.get("orca_path") or "orca"
    out_path = os.path.join(rundir, base + ".out")
    try:
        with open(out_path, "w", encoding="utf-8") as out:
            proc = subprocess.run([orca, inp_name], cwd=rundir,
                                  stdout=out, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        return ("error: ORCA executable not found ('{}'); set it in the GUI "
                "(Settings > ORCA executable) or put orca on PATH".format(orca))
    except OSError as e:
        return "error: failed to run ORCA: {}".format(e)
    status = "ok" if proc.returncode == 0 else "exit {}".format(proc.returncode)
    return "ran {} locally -> {} ({})".format(inp_name, out_path, status)
