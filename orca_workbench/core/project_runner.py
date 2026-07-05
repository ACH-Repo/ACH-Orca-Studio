"""Headless execution of a whole project — expand its Workflow, then build +
run/submit every calc, with no GUI.

The workflow: design everything locally in the GUI (add molecules, build recipes,
wire a Workflow — or add calcs by hand), then copy the `project.json` + the
`XYZ_INI/` folder to the cluster and run:

    orca-workbench --execute_project project.json

First the project's Workflow graph is **expanded** (like the GUI's Generate):
Transform/Combine nodes materialise their derived geometries into `TRANSFORM/`
and the graph turns into PlannedCalcs (`core.workflow_expand`). Expansion is
idempotent — an already-generated project reuses its calcs. Then every planned
calc's ORCA input is built into its run directory and, per environment:

  * **SLURM login node** — submits them as a dependency chain: a child that reads
    a parent's optimised geometry is held (`afterok`) until the parent finishes,
    and reads the parent's `<mol>.xyz` at run time (exactly like the GUI's
    "Submit unattended"). Submit and disconnect.
  * **plain machine** — runs them locally, one at a time, in dependency order
    (`orca <inp> > <out>`), reading each finished parent's optimised geometry off
    disk before building the child.

It reuses the same expand/input/slurm/geomspec builders the GUI uses, so a job
built here is byte-identical to one built in the app. This module is UI-free; the
CLI (`__main__`) is the only caller.
"""

import os

from orca_workbench.core import config as config_mod
from orca_workbench.core import geomspec as geomspec_mod
from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core import orca_parser
from orca_workbench.core import provenance as provenance_mod
from orca_workbench.core import slurm as slurm_mod
from orca_workbench.core import slurm_runtime
from orca_workbench.core import workflow_expand
from orca_workbench.core.coords import read_xyz
from orca_workbench.core.project import load_project

# The packaged default recipe folder + the portable sentinel a project stores
# for it (mirrors ui/app.py so a project.json is resolved identically headlessly).
_DEFAULT_RECIPE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "recipes")
_BUILTIN_SENTINEL = "<builtin>"


def _resolve_recipe_dirs(project):
    raw = list(project.recipe_dirs) or [_BUILTIN_SENTINEL]
    out = []
    for d in raw:
        if d == _BUILTIN_SENTINEL:
            rd = _DEFAULT_RECIPE_DIR
        elif d and not os.path.isabs(d) and project.path:
            rd = os.path.join(project.root(), d)
        else:
            rd = d
        if rd and rd not in out:
            out.append(rd)
    return out


def target_dir_rel(calc, mol, recipe):
    """The run directory (relative to the project root) a calc builds into —
    calcs/<mol>/<category>/<calctype>/<method>[/<variant>], each component
    sanitised (matches the GUI's _target_dir)."""
    parts = [mol.filename, calc.category] + list(recipe.path_parts())
    return "/".join(["calcs"] + [inputs_mod.safe_path_component(p) for p in parts])


def order_dependency_first(calcs):
    """Topological order: each calc's geometry parent (and gate source) come
    before it. Dependencies outside `calcs` impose no ordering (a finished
    parent's geometry is read off disk). Pure — ported from the GUI."""
    byid = {c.id: c for c in calcs}
    order, perm, temp = [], set(), set()

    def deps(c):
        out = []
        if c.geometry_source.startswith("parent:"):
            pid = c.geometry_source.split(":", 1)[1]
            if pid in byid:
                out.append(pid)
        gate = getattr(c, "gate", None)
        if gate and gate.get("source") in byid:
            out.append(gate["source"])
        return out

    def visit(c):
        if c.id in perm or c.id in temp:
            return
        temp.add(c.id)
        for pid in deps(c):
            visit(byid[pid])
        temp.discard(c.id)
        perm.add(c.id)
        order.append(c)

    for c in calcs:
        visit(c)
    return order


class ProjectRunner(object):
    """Builds and runs/submits a project's calcs headlessly. `log` is a callable
    for progress lines (defaults to print)."""

    def __init__(self, project, log=None):
        self.project = project
        self.root = project.root()
        self.log = log or (lambda m: print(m))
        self.recipes = inputs_mod.load_recipes_from_dirs(_resolve_recipe_dirs(project))
        self._recipe_by_name = {r.name: r for r in self.recipes}

    # -- lookups ------------------------------------------------------------
    def recipe(self, name):
        return self._recipe_by_name.get(name)

    def _abs(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.root, rel)

    def _parent_xyz(self, parent, mol):
        """Absolute path to a parent OPT's optimised geometry (ORCA writes
        <mol>.xyz in the run dir)."""
        rundir = parent.rundir
        if not rundir:
            prec = self.recipe(parent.recipe_name)
            if prec is None:
                return None
            pmol = self.project.molecule_by_filename(parent.molecule_filename) or mol
            rundir = target_dir_rel(parent, pmol, prec)
        return self._abs(os.path.join(rundir, mol.filename + ".xyz"))

    # -- geometry -----------------------------------------------------------
    def _embed_atoms(self, calc, mol):
        """Atoms to embed in the .inp (local mode reads finished parents off disk)."""
        src = calc.geometry_source
        if src == "initial":
            if not mol.xyz_path:
                raise ValueError("molecule '{}' has no XYZ".format(mol.filename))
            atoms, _ = read_xyz(self._abs(mol.xyz_path))
            return atoms
        if src.startswith("parent:"):
            parent = self.project.calc_by_id(src[len("parent:"):])
            if parent is None:
                raise ValueError("parent calc not found")
            p = self._parent_xyz(parent, mol)
            if not p or not os.path.isfile(p):
                raise ValueError("parent '{}' hasn't produced an optimised geometry yet"
                                 .format(parent.molecule_filename))
            atoms, _ = read_xyz(p)
            return atoms
        if src.startswith("file:"):
            p = self._abs(src[len("file:"):])
            if p.endswith(".xyz"):
                atoms, _ = read_xyz(p)
                return atoms
            if p.endswith(".inp"):
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    return inputs_mod.extract_atoms_from_inp(fh.read())
        raise ValueError("unsupported geometry source: " + src)

    # -- building -----------------------------------------------------------
    def build_input_text(self, calc, mol, recipe, xyz_ref=None, local_cap=False):
        """Render one calc's complete .inp text (geometry, MOREAD, %geom, hardware
        defaults, provenance) — reuses the exact GUI builders. `xyz_ref`, if given,
        makes the geometry an external `* xyzfile` reference (for a submit chain);
        otherwise the geometry is embedded."""
        if xyz_ref:
            inp = inputs_mod.render_inp_xyzfile(recipe, xyz_ref, mol.charge, mol.multiplicity)
        else:
            inp = inputs_mod.render_inp(recipe, self._embed_atoms(calc, mol),
                                        mol.charge, mol.multiplicity)
        osrc = getattr(calc, "orbital_source", None)
        if osrc and osrc.startswith("parent:"):
            par = self.project.calc_by_id(osrc[len("parent:"):])
            if par is not None and par.rundir:
                gbw = self._abs(os.path.join(par.rundir, par.molecule_filename + ".gbw"))
                inp = inputs_mod.add_moread(inp, gbw)
        gspec = getattr(calc, "geom_spec", None)
        if not geomspec_mod.is_empty(gspec):
            inp = inputs_mod.add_geom_block(inp, geomspec_mod.build_geom_inner(gspec))
        gcores = int(config_mod.get("default_cores", 0) or 0)
        if gcores > 0:
            inp = inputs_mod.set_cores(inp, gcores)
        gmax = int(config_mod.get("default_maxcore_mb", 0) or 0)
        if gmax > 0:
            inp = inputs_mod.set_maxcore(inp, gmax)
        if local_cap:
            avail = inputs_mod.detect_cores()
            if inputs_mod.parse_cores(inp) > avail:
                inp = inputs_mod.set_cores(inp, avail)
        inp = provenance_mod.format_block({
            "molecule": mol.filename, "name": mol.name, "smiles": mol.smiles,
            "gen_smiles": mol.gen_smiles, "charge": mol.charge, "mult": mol.multiplicity,
            "recipe": recipe.name, "calctype": recipe.calctype, "method": recipe.method_label,
            "variant": recipe.variant, "category": calc.category,
            "geometry_source": calc.geometry_source,
            "orbital_source": getattr(calc, "orbital_source", None),
            "initial_xyz": mol.xyz_path, "origin_node": calc.origin_node,
        }) + inp
        return inp

    def _write_inp(self, calc, mol, recipe, xyz_ref=None, local_cap=False):
        rundir_rel = target_dir_rel(calc, mol, recipe)
        rundir_abs = self._abs(rundir_rel)
        os.makedirs(rundir_abs, exist_ok=True)
        inp_text = self.build_input_text(calc, mol, recipe, xyz_ref=xyz_ref, local_cap=local_cap)
        inp_rel = os.path.join(rundir_rel, mol.filename + ".inp")
        with open(self._abs(inp_rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(inp_text)
        calc.inp_path = inp_rel
        calc.rundir = rundir_rel
        calc.exported = True
        return rundir_rel, inp_text

    # -- workflow expansion -------------------------------------------------
    def _calc_done(self, calc):
        """Whether a calc has already finished (its .out terminated normally) —
        so re-expanding a project keeps finished steps verbatim."""
        if not calc.rundir:
            return False
        op = slurm_runtime.find_output_file(self._abs(calc.rundir), calc.molecule_filename,
                                            calc.job_id)
        if not op or not os.path.isfile(op):
            return False
        try:
            return bool(orca_parser._TERM_OK.search(orca_parser.read_tail(op)))
        except OSError:
            return False

    def expand(self):
        """Expand the project's Workflow graph into PlannedCalcs (materialising
        Transform/Combine geometries), mirroring the GUI's Generate. Idempotent.
        Returns the workflow_expand summary dict (see that module)."""
        exp = workflow_expand.expand_project_workflow(
            self.project, calc_done=self._calc_done, log=self.log)
        if exp["blockers"]:
            self.log("workflow NOT expanded — fix these in the GUI first:")
            for b in exp["blockers"]:
                self.log("  - " + b)
        elif exp["expanded"]:
            self.log("workflow expanded: {} new calc(s) created, {} reused.".format(
                exp["new"], exp["reused"]))
            for w in exp["warnings"]:
                self.log("  note: " + w)
        return exp

    # -- execution ----------------------------------------------------------
    def _runnable(self):
        """(calcs, skipped) — planned calcs with a resolvable molecule+recipe,
        excluding closed gates. Ordered dependency-first."""
        good, skipped = [], []
        for c in self.project.planned_calcs:
            mol = self.project.molecule_by_filename(c.molecule_filename)
            rec = self.recipe(c.recipe_name)
            if mol is None or rec is None:
                skipped.append((c, "molecule or recipe missing"))
            else:
                good.append(c)
        return order_dependency_first(good), skipped

    def execute(self, force=None, expand=True):
        """Expand the Workflow (unless `expand` is False), then build + run/submit
        the project. `force` is None (auto-detect), 'slurm', or 'local'. Returns a
        summary dict."""
        exp = self.expand() if expand else {"blockers": []}
        calcs, skipped = self._runnable()
        if not calcs and exp.get("blockers"):
            return {"built": 0, "launched": 0, "skipped": len(skipped), "mode": None,
                    "message": "workflow has errors: " + "; ".join(exp["blockers"])}
        for c, why in skipped:
            self.log("SKIP {}: {}".format(c.molecule_filename, why))
        if not calcs:
            return {"built": 0, "launched": 0, "skipped": len(skipped),
                    "mode": None, "message": "no runnable calculations"}
        use_slurm = (slurm_runtime.sbatch_available() if force is None
                     else force == "slurm")
        if use_slurm:
            return self._execute_slurm(calcs, len(skipped))
        return self._execute_local(calcs, len(skipped))

    def _execute_local(self, calcs, n_skipped):
        """Run each calc with a local ORCA, in dependency order, embedding a
        finished parent's optimised geometry."""
        orca = config_mod.get("orca_path") or "orca"
        import subprocess
        built = launched = failed = 0
        for c in calcs:
            mol = self.project.molecule_by_filename(c.molecule_filename)
            rec = self.recipe(c.recipe_name)
            try:
                rundir_rel, _ = self._write_inp(c, mol, rec, local_cap=True)
            except Exception as e:
                self.log("BUILD FAILED {}: {}".format(mol.filename, e))
                failed += 1
                continue
            built += 1
            rundir_abs = self._abs(rundir_rel)
            out_abs = os.path.join(rundir_abs, mol.filename + "-local.out")
            self.log("running {} ...".format(target_dir_rel(c, mol, rec)))
            try:
                with open(out_abs, "w", encoding="utf-8") as out:
                    proc = subprocess.run([orca, mol.filename + ".inp"], cwd=rundir_abs,
                                          stdout=out, stderr=subprocess.STDOUT)
            except FileNotFoundError:
                self.log("error: ORCA not found ('{}') — set orca_path in the GUI or PATH"
                         .format(orca))
                return {"built": built, "launched": launched, "failed": failed,
                        "skipped": n_skipped, "mode": "local",
                        "message": "ORCA executable not found"}
            except OSError as e:
                self.log("error running {}: {}".format(mol.filename, e))
                failed += 1
                continue
            c.job_id = "local"
            launched += 1
            self.log("  -> {} (exit {})".format(os.path.basename(out_abs), proc.returncode))
        return {"built": built, "launched": launched, "failed": failed,
                "skipped": n_skipped, "mode": "local"}

    def _execute_slurm(self, calcs, n_skipped):
        """Build all + submit as a SLURM dependency chain (parent geometry via
        xyzfile + afterok). Mirrors the GUI's submit_unattended, headless."""
        try:
            template = slurm_mod.load_template()
        except Exception as e:
            return {"built": 0, "launched": 0, "skipped": n_skipped, "mode": "slurm",
                    "message": "slurm template missing: {}".format(e)}
        mail = config_mod.get("usermail", "") or ""
        jobmap = {}   # calc id -> job id
        built = submitted = failed = 0
        for c in calcs:
            mol = self.project.molecule_by_filename(c.molecule_filename)
            rec = self.recipe(c.recipe_name)
            deps, xyz_ref = [], None
            if c.geometry_source.startswith("parent:"):
                parent = self.project.calc_by_id(c.geometry_source[len("parent:"):])
                if parent is None:
                    self.log("SKIP {}: parent missing".format(mol.filename))
                    continue
                xyz_ref = self._parent_xyz(parent, mol)
                pj = jobmap.get(parent.id)
                if pj:
                    deps.append(pj)
                elif not (xyz_ref and os.path.isfile(xyz_ref)):
                    self.log("SKIP {}: parent not in this batch and not finished"
                             .format(mol.filename))
                    continue
            gate = getattr(c, "gate", None)
            if gate and gate.get("source") in jobmap:
                deps.append(jobmap[gate["source"]])
            try:
                rundir_rel, inp_text = self._write_inp(c, mol, rec, xyz_ref=xyz_ref)
            except Exception as e:
                self.log("BUILD FAILED {}: {}".format(mol.filename, e))
                failed += 1
                continue
            built += 1
            slurm_text = slurm_mod.render_slurm(
                template, inp_filename=mol.filename + ".inp", rundir=".",
                jobname=mol.filename, cores=inputs_mod.parse_cores(inp_text), usermail=mail)
            slurm_rel = os.path.join(rundir_rel, mol.filename + ".slurm")
            with open(self._abs(slurm_rel), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(slurm_text)
            c.slurm_path = slurm_rel
            dep = ("afterok:" + ":".join(deps)) if deps else None
            job_id, err = slurm_runtime.submit(slurm_rel, self.root, dependency=dep)
            if job_id:
                jobmap[c.id] = job_id
                c.job_id = job_id
                submitted += 1
                self.log("submitted {} -> job {}{}".format(
                    mol.filename, job_id, " (after {})".format(",".join(deps)) if deps else ""))
            else:
                self.log("submit failed {}: {}".format(mol.filename, err))
                failed += 1
        return {"built": built, "launched": submitted, "failed": failed,
                "skipped": n_skipped, "mode": "slurm"}


def execute_project_file(path, force=None, log=None, save=True, expand=True):
    """Top-level entry: load a project.json, expand its Workflow, and
    build+run/submit it. `expand` False runs only the already-generated planned
    calcs. Returns a one-line status string (prefixed 'error' on failure) for the
    CLI exit code."""
    log = log or (lambda m: print(m))
    if not os.path.isfile(path):
        return "error: no such project file: {}".format(path)
    try:
        project = load_project(path)
    except Exception as e:
        return "error: could not open {}: {}".format(path, e)
    runner = ProjectRunner(project, log=log)
    if not runner.recipes:
        return "error: no recipes loaded (check the project's recipe folders)"
    summary = runner.execute(force=force, expand=expand)
    if save:
        # Persist the run dirs / job ids we just assigned, so reopening the
        # project in the GUI can monitor + harvest the jobs.
        try:
            from orca_workbench.core.project import save_project
            save_project(project)
        except Exception as e:
            log("warning: could not save project after execution: {}".format(e))
    if summary.get("message") and not summary.get("built"):
        return "error: " + summary["message"]
    return ("{} calc(s) built, {} {} ({} skipped, {} failed)".format(
        summary.get("built", 0), summary.get("launched", 0),
        "submitted" if summary.get("mode") == "slurm" else "run locally",
        summary.get("skipped", 0), summary.get("failed", 0)))
