"""Discover and reconnect calculations that the app didn't create itself.

Two related jobs:

* relink_project() -- a project's calcs were submitted outside the app (e.g. by a
  generated submit_all.sh on the cluster), so their job_id is null and the app
  can't monitor or harvest them. We recover the id from the SLURM output files
  (<rundir>/<jobname>-<jobid>.out, written once a job starts) and, for jobs still
  PENDING, from a squeue name->id map. The job NAME is the molecule filename, so
  it keys straight back to the planned calc.

* import_dir() -- pull standalone ORCA .inp files (with whatever .out/.engrad sit
  beside them) into the project as molecules + planned calcs, so they show up for
  monitoring and result extraction. A recipe is reconstructed from each .inp so
  the report extractors (which key off recipe.calctype) fire correctly.

Both are pure with respect to the UI: they mutate the Project object and the
filesystem only, and return a small summary dict the caller can surface.
"""

import os
import re

from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core import provenance as provenance_mod
from orca_workbench.core.inputs import Recipe, COORDS_PLACEHOLDER, safe_path_component
from orca_workbench.core.project import Molecule, PlannedCalc, new_calc_id


# <jobname>-<digits>.out / .err  -> the SLURM job id is in the filename.
def _scan_jobid_from_files(rundir_abs, jobname):
    """Newest <jobname>-<jobid>.(out|err) in rundir -> jobid, or None."""
    pat = re.compile(r"^" + re.escape(jobname) + r"-(\d+)\.(?:out|err)$")
    best = None  # (mtime, jobid)
    try:
        entries = os.listdir(rundir_abs)
    except OSError:
        return None
    for fn in entries:
        m = pat.match(fn)
        if not m:
            continue
        try:
            mt = os.path.getmtime(os.path.join(rundir_abs, fn))
        except OSError:
            mt = 0
        if best is None or mt > best[0]:
            best = (mt, m.group(1))
    return best[1] if best else None


def relink_project(project, name_to_jobid=None, relink_all=False):
    """Backfill planned_calc.job_id from on-disk output files and a squeue map.

    name_to_jobid : optional {job_name: job_id} (job_name == molecule filename),
                    e.g. from slurm_runtime.query_name_map(); covers PENDING jobs.
    relink_all    : also re-derive ids for calcs that already have one.

    Mutates `project`. Returns a summary dict. Caller marks the project dirty.
    """
    name_to_jobid = name_to_jobid or {}
    root = project.root()
    summary = {"total": len(project.planned_calcs), "from_files": 0,
               "from_queue": 0, "already": 0, "unlinked": [], "changed": 0}
    for c in project.planned_calcs:
        if c.job_id and not relink_all:
            summary["already"] += 1
            continue
        if not c.rundir or not c.molecule_filename:
            summary["unlinked"].append(c.molecule_filename or c.id)
            continue
        rundir_abs = os.path.join(root, c.rundir)
        jid = _scan_jobid_from_files(rundir_abs, c.molecule_filename)
        src = "file"
        if jid is None:
            entry = name_to_jobid.get(c.molecule_filename)
            jid = entry[0] if isinstance(entry, (list, tuple)) else entry
            src = "queue"
        if jid is None:
            summary["unlinked"].append(c.molecule_filename)
            continue
        if jid != c.job_id:
            c.job_id = jid
            c.exported = True
            summary["changed"] += 1
        summary["from_files" if src == "file" else "from_queue"] += 1
    return summary


def unlinked_with_output(project):
    """Calcs that have SLURM output files on disk (<jobname>-<jobid>.out/.err)
    but no job_id -- i.e. submitted outside the app. Distinguishes a real
    out-of-band submission from a calc that's merely been built (which has only
    .inp/.slurm and no output yet). Returns the list of such calcs."""
    root = project.root()
    out = []
    for c in project.planned_calcs:
        if c.job_id or not c.rundir or not c.molecule_filename:
            continue
        if _scan_jobid_from_files(os.path.join(root, c.rundir), c.molecule_filename):
            out.append(c)
    return out


# ----------------------------------------------------------------- import

_RUNTYPE_HINTS = [  # order matters: first hit wins
    ("FREQ", ("FREQ", "NUMFREQ", "ANFREQ")),
    ("OPT", ("OPT", "TIGHTOPT", "COPT", "OPTTS")),
    ("NMR", ("NMR",)),
    ("ENGRAD", ("ENGRAD",)),
]


def _keyword_line(inp_text):
    for ln in inp_text.splitlines():
        s = ln.strip()
        if s.startswith("!"):
            return s[1:].strip()
    return ""


def _infer_calctype(kw_upper):
    toks = set(kw_upper.split())
    for label, hints in _RUNTYPE_HINTS:
        if toks & set(hints):
            return label
    return "SP"


def _infer_method_label(kw_line):
    """A short functional_basis tag from the `!` line, for grouping/paths."""
    skip = {"RIJCOSX", "RIJK", "RIJDX", "AUTOAUX", "TIGHTSCF", "VERYTIGHTSCF",
            "DEFGRID1", "DEFGRID2", "DEFGRID3", "OPT", "TIGHTOPT", "FREQ",
            "NUMFREQ", "ANFREQ", "NMR", "ENGRAD", "SP", "D3BJ", "D3", "D4",
            "NOAUTOSTART", "MINIPRINT", "NORI", "RI"}
    picked = []
    for t in kw_line.split():
        tu = t.upper()
        if tu in skip or tu.startswith("CPCM") or tu.startswith("SMD") \
                or tu.startswith("%") or "(" in t:
            continue
        picked.append(t)
        if len(picked) == 2:
            break
    return safe_path_component("_".join(picked) or "imported")


def recipe_from_inp(inp_text):
    """Reconstruct a Recipe from an existing .inp (coords -> placeholder)."""
    # Drop any ORCA Workbench provenance header first so it never becomes part of
    # the reconstructed template (which would re-stamp/accumulate on rebuild).
    inp_text = provenance_mod.strip_block(inp_text)
    try:
        block = inputs_mod.extract_coords_section(inp_text)
        template = inp_text.replace(block, COORDS_PLACEHOLDER)
    except ValueError:
        template = inp_text  # no xyz block (e.g. xyzfile); keep as-is
    kw = _keyword_line(inp_text)
    calctype = _infer_calctype(kw.upper())
    method = _infer_method_label(kw)
    name = "Imported: {} {}".format(calctype, method)
    return Recipe(name=name, calctype=calctype, method_label=method,
                  template=template, variant="imported")


def _charge_mult(inp_text):
    m = re.search(r"\*\s*xyz(?:file)?\s+([+-]?\d+)\s+(\d+)", inp_text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 1


def import_dir(project, src_dir, save_recipe=None, category="imported"):
    """Import every *.inp under src_dir as a molecule + planned calc.

    save_recipe(recipe) : optional callback to persist a newly-created recipe
                          (e.g. app.save + register). Called once per distinct
                          recipe name. If None, recipes are only attached to the
                          project's in-memory list via the returned summary.

    Mutates `project`. Returns a summary dict including any new recipes so the
    caller can register them on the app. Skips .inp already present in the project.
    """
    root = project.root()
    existing_inps = {c.inp_path for c in project.planned_calcs if c.inp_path}
    existing_mols = {m.filename for m in project.molecules}
    summary = {"scanned": 0, "imported": 0, "skipped": 0, "with_output": 0,
               "new_recipes": {}, "errors": []}

    inp_paths = []
    for dirpath, _dirs, files in os.walk(src_dir):
        for fn in sorted(files):
            if fn.endswith(".inp"):
                inp_paths.append(os.path.join(dirpath, fn))

    for inp_abs in sorted(inp_paths):
        summary["scanned"] += 1
        rundir_abs = os.path.dirname(inp_abs)
        base = os.path.splitext(os.path.basename(inp_abs))[0]
        # Normalize to forward slashes up front so the skip-check matches the
        # stored inp_path on every OS (Windows relpath yields backslashes).
        inp_rel = os.path.relpath(inp_abs, root).replace("\\", "/")
        if inp_rel in existing_inps:
            summary["skipped"] += 1
            continue
        try:
            with open(inp_abs, "r", encoding="utf-8", errors="replace") as fh:
                inp_text = fh.read()
        except OSError as e:
            summary["errors"].append("{}: {}".format(inp_rel, e))
            continue

        recipe = recipe_from_inp(inp_text)
        if recipe.name not in summary["new_recipes"]:
            summary["new_recipes"][recipe.name] = recipe
            if save_recipe is not None:
                save_recipe(recipe)
        charge, mult = _charge_mult(inp_text)

        molname = base
        suffix = 2
        while molname in existing_mols:
            molname = "{}_{}".format(base, suffix)
            suffix += 1
        existing_mols.add(molname)

        project.molecules.append(Molecule(
            name=molname, filename=molname, charge=charge, multiplicity=mult,
            comment="imported from {}".format(inp_rel), generated=False,
            xyz_path=None, method="imported", gen_status="ok"))

        # locate an output sitting beside the .inp and recover its job id
        job_id = _scan_jobid_from_files(rundir_abs, base)
        if job_id is None and os.path.isfile(os.path.join(rundir_abs, base + ".out")):
            job_id = "imported"   # truthy sentinel; find_output_file globs the .out
        if job_id:
            summary["with_output"] += 1

        slurm_abs = os.path.join(rundir_abs, base + ".slurm")
        project.planned_calcs.append(PlannedCalc(
            id=new_calc_id(), molecule_filename=molname, recipe_name=recipe.name,
            category=category, geometry_source="file:" + inp_rel,
            exported=True, inp_path=inp_rel,
            slurm_path=(os.path.relpath(slurm_abs, root).replace("\\", "/")
                        if os.path.isfile(slurm_abs) else None),
            job_id=job_id, rundir=os.path.relpath(rundir_abs, root).replace("\\", "/")))
        summary["imported"] += 1

    return summary
