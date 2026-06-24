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

from orca_workbench.core import coords as coords_mod
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


def _molecule_dir_for(inp_abs):
    """The ancestor directory that *names* this molecule, exploiting the app's own
    layout calcs/<mol>/.../<mol>.inp: the nearest ancestor folder whose name equals
    the .inp basename. Returns its path, or None when the convention isn't present
    (a flat/external dump) — the caller then treats the file as its own molecule.
    This is what lets one molecule's OPT + many NMR inputs (all <mol>.inp) collapse
    into a single molecule instead of spawning one per .inp."""
    base = os.path.splitext(os.path.basename(inp_abs))[0]
    d = os.path.dirname(os.path.abspath(inp_abs))
    while d and os.path.basename(d):
        if os.path.basename(d) == base:
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _path_parts_under(mol_dir, rundir_abs):
    """Directory components between the molecule dir and the calc's rundir, i.e.
    [category, calctype, method_label, (variant)] for an app-built tree. Empty if
    rundir is the molecule dir itself or not a descendant."""
    if not mol_dir:
        return []
    rel = os.path.relpath(rundir_abs, mol_dir).replace("\\", "/")
    if rel.startswith("..") or rel == ".":
        return []
    return [p for p in rel.split("/") if p and p != "."]


def _recipe_parts(parts, prov):
    """(category, (calctype, method_label, variant)) from the path, falling back to
    provenance, else (None, None) so the caller reconstructs from the .inp text."""
    if len(parts) >= 3:
        variant = parts[3] if len(parts) >= 4 else ""
        return parts[0], (parts[1], parts[2], variant)
    if prov and prov.get("calctype"):
        return (prov.get("category"),
                (prov.get("calctype"), prov.get("method", ""), prov.get("variant", "")))
    return None, None


def _resolve_recipe(inp_text, recipe_parts, existing_recipes):
    """(recipe, is_new). Reuse a loaded recipe matching (calctype, method_label,
    variant) so a project's own recipes/*.json aren't duplicated; otherwise
    reconstruct from the .inp, enriched with the path-derived identity."""
    if recipe_parts and existing_recipes:
        ct, ml, var = recipe_parts
        for r in existing_recipes:
            if (r.calctype == ct and r.method_label == ml
                    and (r.variant or "") == (var or "")):
                return r, False
    recipe = recipe_from_inp(inp_text)
    if recipe_parts:
        ct, ml, var = recipe_parts
        recipe.calctype = ct or recipe.calctype
        recipe.method_label = ml or recipe.method_label
        recipe.variant = var or recipe.variant
        recipe.name = "Imported: " + "/".join(p for p in (ct, ml, var) if p)
    return recipe, True


def _find_initial_xyz(root, src_dir, mol_dir, key, prov):
    """Locate the molecule's initial geometry, provenance-first then by convention,
    so calcs imported from another location (or molecules whose .xyz never lived in
    XYZ_INI) still link. Returns an absolute path or None."""
    candidates = []
    if prov and prov.get("initial_xyz"):
        candidates.append(os.path.join(root, prov["initial_xyz"]))
    if mol_dir:
        candidates.append(os.path.join(mol_dir, key + ".xyz"))
    candidates.append(os.path.join(src_dir, "XYZ_INI", key + ".xyz"))
    candidates.append(os.path.join(root, "XYZ_INI", key + ".xyz"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _reconstruct_xyz(root, key, recs):
    """Write XYZ_INI/<key>.xyz from the embedded coords of an OPT .inp (its coords
    are the *initial* geometry), else any .inp in the group. Returns the path, or
    None if nothing parseable. Makes a recovered molecule usable for new calcs."""
    def _is_opt(rec):
        kw = _keyword_line(rec["inp_text"]).upper()
        return _infer_calctype(kw) == "OPT"
    ordered = sorted(recs, key=lambda r: 0 if _is_opt(r) else 1)
    for rec in ordered:
        try:
            atoms = inputs_mod.extract_atoms_from_inp(rec["inp_text"])
        except ValueError:
            atoms = []
        if atoms:
            target = os.path.join(root, "XYZ_INI", key + ".xyz")
            coords_mod.write_xyz(target, atoms, None)
            return target
    return None


def _first_not_none(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def import_dir(project, src_dir, save_recipe=None, category="imported",
               existing_recipes=None, reconstruct_missing_xyz=True):
    """Import standalone ORCA .inp files under src_dir as molecules + planned calcs.

    Calcs are grouped into ONE molecule each (provenance `molecule`, else the
    ancestor dir that names the .inp, else per-file) so an OPT + many NMR inputs
    don't each spawn a molecule. Each molecule is linked to its initial .xyz
    (provenance/convention, multi-location), reconstructing one from the OPT .inp
    if none is found. Recipes are reused from `existing_recipes` when their
    (calctype, method_label, variant) matches, else reconstructed.

    save_recipe(recipe) : optional callback to persist a newly-reconstructed recipe.
    existing_recipes    : recipes already loaded on the app, for reuse/no-dup.

    Mutates `project` and may write XYZ_INI/*.xyz. Returns a summary dict. Skips
    .inp already present in the project.
    """
    root = project.root()
    existing_inps = {c.inp_path for c in project.planned_calcs if c.inp_path}
    existing_mols = {m.filename for m in project.molecules}
    summary = {"scanned": 0, "imported": 0, "skipped": 0, "with_output": 0,
               "molecules": 0, "reconstructed_xyz": 0, "new_recipes": {}, "errors": []}

    inp_paths = []
    for dirpath, _dirs, files in os.walk(src_dir):
        for fn in sorted(files):
            if fn.endswith(".inp"):
                inp_paths.append(os.path.join(dirpath, fn))
    inp_paths.sort()

    # --- pass 1: read each new .inp and group it by molecule ---
    groups = {}        # group_id -> {key, kind, mol_dir, prov, recs:[...]}
    order = []         # preserve first-seen order for deterministic dedup
    for inp_abs in inp_paths:
        summary["scanned"] += 1
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
        prov = provenance_mod.parse_block(inp_text)
        base = os.path.splitext(os.path.basename(inp_abs))[0]
        mol_dir = _molecule_dir_for(inp_abs)
        if prov and prov.get("molecule"):
            key, kind, gid = prov["molecule"], "prov", "prov:" + prov["molecule"]
        elif mol_dir:
            key, kind, gid = base, "dir", "dir:" + os.path.normcase(mol_dir)
        else:
            key, kind, gid = base, "file", "file:" + os.path.normcase(inp_abs)
        rec = {"inp_abs": inp_abs, "inp_rel": inp_rel, "rundir_abs": os.path.dirname(inp_abs),
               "inp_text": inp_text, "prov": prov, "base": base}
        if gid not in groups:
            groups[gid] = {"key": key, "kind": kind, "mol_dir": mol_dir,
                           "prov": prov, "recs": []}
            order.append(gid)
        groups[gid]["recs"].append(rec)

    # --- pass 2: one molecule per group, then its calcs ---
    for gid in order:
        g = groups[gid]
        key, recs = g["key"], g["recs"]

        # reuse an existing molecule only when the identity is meaningful
        # (provenance/dir); a bare basename guess must not merge into a same-named
        # but unrelated molecule.
        mol = project.molecule_by_filename(key) if g["kind"] in ("prov", "dir") else None
        if mol is None:
            molname = key
            i = 2
            while molname in existing_mols:
                molname = "{}_{}".format(key, i)
                i += 1
            existing_mols.add(molname)

            prov = g["prov"] or {}
            xyz_abs = _find_initial_xyz(root, src_dir, g["mol_dir"], key, g["prov"])
            if xyz_abs is None and reconstruct_missing_xyz:
                xyz_abs = _reconstruct_xyz(root, molname, recs)
                if xyz_abs:
                    summary["reconstructed_xyz"] += 1
            meta = {}
            if xyz_abs and os.path.isfile(xyz_abs):
                try:
                    _atoms, m = coords_mod.read_xyz(xyz_abs)
                    meta = m or {}
                except Exception:
                    meta = {}
            xyz_rel = (os.path.relpath(xyz_abs, root).replace("\\", "/")
                       if xyz_abs and os.path.isfile(xyz_abs) else None)
            charge = _first_not_none(prov.get("charge"), meta.get("charge"))
            mult = _first_not_none(prov.get("mult"), meta.get("multiplicity"))
            if charge is None or mult is None:
                c2, m2 = _charge_mult(recs[0]["inp_text"])
                charge = c2 if charge is None else charge
                mult = m2 if mult is None else mult
            mol = Molecule(
                name=(prov.get("name") or meta.get("name") or molname),
                filename=molname,
                smiles=(prov.get("smiles") or meta.get("smiles")),
                gen_smiles=(prov.get("gen_smiles") or meta.get("gen_smiles")),
                charge=int(charge or 0),
                multiplicity=int(mult or 1),
                comment=(meta.get("comment") or "imported"),
                generated=bool(xyz_rel),
                xyz_path=xyz_rel,
                method=(meta.get("method") or "imported"),
                # A recovered initial geometry is the original that was actually
                # run — lock it so SMILES generation can't overwrite it. When no
                # xyz could be recovered (pending), leave it unlocked so the user
                # can still build coords from the SMILES.
                coords_locked=bool(xyz_rel),
                gen_status=("ok" if xyz_rel else "pending"))
            project.molecules.append(mol)
            summary["molecules"] += 1
        molname = mol.filename

        for rec in recs:
            prov = rec["prov"] or {}
            parts = _path_parts_under(g["mol_dir"], rec["rundir_abs"])
            cat, recipe_parts = _recipe_parts(parts, prov)
            cat = cat or category
            recipe, is_new = _resolve_recipe(rec["inp_text"], recipe_parts, existing_recipes)
            if is_new and recipe.name not in summary["new_recipes"]:
                summary["new_recipes"][recipe.name] = recipe
                if save_recipe is not None:
                    save_recipe(recipe)
            # geometry: each .inp embeds its own coords, so file:<inp> rebuilds it
            # exactly; honour an explicit provenance "initial".
            geosrc = "initial" if prov.get("geometry_source") == "initial" \
                else "file:" + rec["inp_rel"]
            job_id = _scan_jobid_from_files(rec["rundir_abs"], rec["base"])
            if job_id is None and os.path.isfile(os.path.join(rec["rundir_abs"], rec["base"] + ".out")):
                job_id = "imported"   # truthy sentinel; find_output_file globs the .out
            if job_id:
                summary["with_output"] += 1
            slurm_abs = os.path.join(rec["rundir_abs"], rec["base"] + ".slurm")
            project.planned_calcs.append(PlannedCalc(
                id=new_calc_id(), molecule_filename=molname, recipe_name=recipe.name,
                category=cat, geometry_source=geosrc, exported=True, inp_path=rec["inp_rel"],
                slurm_path=(os.path.relpath(slurm_abs, root).replace("\\", "/")
                            if os.path.isfile(slurm_abs) else None),
                job_id=job_id,
                rundir=os.path.relpath(rec["rundir_abs"], root).replace("\\", "/")))
            summary["imported"] += 1

    return summary


def match_parents_by_recipe(selected, all_calcs, recipe_name):
    """Map each selected calc to a parent: the calc in its OWN molecule whose
    recipe_name == recipe_name (first match). Used to bulk-wire derived calcs to
    their source (e.g. many NMR -> each molecule's OPT) in one action.

    Returns {calc_id: parent_id}. A selected calc is omitted when its molecule has
    no such parent, when the match is the calc itself (it IS that recipe), or when
    the match is one of its descendants (would create a cycle). Pure: reads only
    the passed lists. `all_calcs` is the whole project so descendants resolve.
    """
    by_mol = {}
    for p in all_calcs:
        if p.recipe_name == recipe_name:
            by_mol.setdefault(p.molecule_filename, p)

    children = {}
    for c in all_calcs:
        if c.parent_id:
            children.setdefault(c.parent_id, []).append(c.id)

    def _descendants(cid):
        out = set()
        stack = [cid]
        while stack:
            for kid in children.get(stack.pop(), []):
                if kid not in out:
                    out.add(kid)
                    stack.append(kid)
        return out

    mapping = {}
    for c in selected:
        p = by_mol.get(c.molecule_filename)
        if p is None or p.id == c.id or p.id in _descendants(c.id):
            continue
        mapping[c.id] = p.id
    return mapping
