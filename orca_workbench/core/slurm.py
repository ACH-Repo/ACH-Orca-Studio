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


_PLACEHOLDERS = ("CORES", "JOBID", "RUNDIR", "INPFILE", "USERMAIL", "PREAMBLE")


def load_template(path=None):
    # type: (Optional[str]) -> str
    path = path or DEFAULT_TEMPLATE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def render_slurm(template, inp_filename, rundir, jobname, cores, usermail, preamble=""):
    # type: (str, str, str, str, int, str, str) -> str
    """Fill in the slurm template. `rundir` should be the path relative
    to wherever the user will run sbatch from (typically the project root).
    `preamble` is an optional shell snippet injected before any work (e.g. a
    workflow condition guard for unattended dependency chains).
    """
    out = template
    out = out.replace("!!##INPFILE##!!", inp_filename)
    out = out.replace("!!##RUNDIR##!!", rundir.replace("\\", "/"))
    out = out.replace("!!##JOBID##!!", _sanitize_jobname(jobname))
    out = out.replace("!!##CORES##!!", str(cores))
    out = out.replace("!!##USERMAIL##!!", usermail)
    # Older templates may not have the PREAMBLE marker; only replace if present.
    out = out.replace("!!##PREAMBLE##!!", preamble or "")
    return out


def gate_guard(predicate, source_out_path):
    # type: (str, str) -> str
    """A pure-shell snippet that, run inside a SLURM job before any work, tests a
    workflow Condition predicate against the feeding job's output file and
    `exit 0`s (skipping this calculation) if it isn't met. Pure grep/awk so it
    needs nothing beyond a normal shell on the compute node.

    Predicates mirror core.workflow: terminated_ok, no_imaginary_freqs,
    has_imaginary_freqs. An unrecognised predicate passes (no gate)."""
    src = source_out_path.replace('"', '\\"')
    lines = [
        "# ---- workflow condition gate ----",
        'GATE_SRC="{}"'.format(src),
        'echo "Workflow gate: checking \'{}\' on $GATE_SRC"'.format(predicate),
    ]
    if predicate == "terminated_ok":
        lines.append(
            'if grep -q "ORCA TERMINATED NORMALLY" "$GATE_SRC"; then PASS=1; else PASS=0; fi')
    elif predicate in ("no_imaginary_freqs", "has_imaginary_freqs"):
        # Count negative (imaginary) modes in the VIBRATIONAL FREQUENCIES block.
        # Lines look like "   6:      -512.30 cm**-1"; the zero trans/rot modes
        # are 0.00 and don't count.
        lines.append(
            "NIMAG=$(awk '/VIBRATIONAL FREQUENCIES/{b=1;next} "
            "b && /NORMAL MODES/{b=0} "
            "b && /cm\\*\\*-1/{v=$2+0; if(v<-0.01)n++} "
            "END{print n+0}' \"$GATE_SRC\")")
        lines.append('echo "Workflow gate: $NIMAG imaginary mode(s)."')
        if predicate == "no_imaginary_freqs":
            lines.append('if [ "${NIMAG:-1}" -eq 0 ]; then PASS=1; else PASS=0; fi')
        else:
            lines.append('if [ "${NIMAG:-0}" -gt 0 ]; then PASS=1; else PASS=0; fi')
    else:
        lines.append("PASS=1")
    lines += [
        'if [ "$PASS" != "1" ]; then',
        '  echo "Workflow condition ({}) not met -> skipping this calculation."'.format(predicate),
        "  exit 0",
        "fi",
        'echo "Workflow condition ({}) satisfied; proceeding."'.format(predicate),
    ]
    return "\n".join(lines)


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
