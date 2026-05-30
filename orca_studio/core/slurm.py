"""Generate SLURM submit scripts from a template.

The template lives in data/slurm_template.sh next to the package. It contains
placeholder strings of the form `!!##NAME##!!` that get string-substituted.

The template includes a `stdbuf -oL -eL` wrapper around the ORCA call so that
ORCA's stdout (which is what SLURM streams into the .out file on the shared
filesystem) is line-buffered. Without that, progress only appears in big
chunks — which is the most common explanation for "I can't see the file
updating during the run".
"""

import os
import re
from typing import Optional


DEFAULT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "slurm_template.sh",
)


_PLACEHOLDERS = ("CORES", "JOBID", "RUNDIR", "INPFILE", "USERMAIL")


def load_template(path=None):
    # type: (Optional[str]) -> str
    path = path or DEFAULT_TEMPLATE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def render_slurm(template, inp_filename, rundir, jobname, cores, usermail):
    # type: (str, str, str, str, int, str) -> str
    """Fill in the slurm template. `rundir` should be the path relative
    to wherever the user will run sbatch from (typically the project root).
    """
    out = template
    out = out.replace("!!##INPFILE##!!", inp_filename)
    out = out.replace("!!##RUNDIR##!!", rundir.replace("\\", "/"))
    out = out.replace("!!##JOBID##!!", _sanitize_jobname(jobname))
    out = out.replace("!!##CORES##!!", str(cores))
    out = out.replace("!!##USERMAIL##!!", usermail)
    return out


_JOBNAME_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_jobname(name):
    # type: (str) -> str
    cleaned = _JOBNAME_SAFE.sub("_", name).strip("_")
    return cleaned or "orca_job"


def remaining_placeholders(text):
    # type: (str) -> list
    """Return any !!##NAME##!! markers still in the rendered text — useful
    for catching typos before files leave the app."""
    return re.findall(r"!!##([A-Z_]+)##!!", text)
