"""Headless (UI-free) workflow expansion.

Turns a Workflow node-graph + a Project's molecules into PlannedCalcs,
materialising the Transform/Combine nodes' derived geometries along the way.
This is the pure core the two callers share:

  * the **Workflow tab** (GUI) — its geometry backend, derived-geometry
    materialisation, calc reuse and factory all delegate here, so the app and
    the headless path expand a graph identically (single source of truth);
  * **`orca-workbench --execute_project`** — expands the graph before building
    and running/submitting the resulting calcs, so a project runs unattended
    with no GUI step (`expand_project_workflow`).

Everything here is Tkinter-free and takes a `Project` (core), not the app, so it
is unit-testable and safe to import on a headless login node.
"""

import os

from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core import transform as transform_mod
from orca_workbench.core import workflow as wf_mod
from orca_workbench.core.project import Molecule, PlannedCalc, new_calc_id


def read_geometry(project, fname, cache):
    """(symbols/coords/charge/mult dict) for a molecule — from `cache` (chained
    derived geometries computed earlier in the same expansion) or its `.xyz` on
    disk. Populates and returns from `cache` so a Transform reading a Combine's
    output sees the in-memory geometry, not a stale file. Raises ValueError with
    a human message when the molecule or its geometry is missing."""
    if fname in cache:
        return cache[fname]
    import numpy as np
    from orca_workbench.core import coords as coords_mod
    m = project.molecule_by_filename(fname)
    if m is None:
        raise ValueError("unknown molecule '{}'".format(fname))
    if not m.xyz_path:
        raise ValueError("'{}' has no geometry yet — generate its XYZ first".format(fname))
    p = os.path.join(project.root(), m.xyz_path)
    frames = coords_mod._read_xyz_frames(p)
    if not frames or not frames[0][0]:
        raise ValueError("could not read a geometry from {}".format(p))
    atoms = frames[0][0]
    cache[fname] = {"symbols": [a[0] for a in atoms],
                    "coords": np.array([[a[1], a[2], a[3]] for a in atoms], dtype=float),
                    "charge": m.charge, "mult": m.multiplicity}
    return cache[fname]


class GeometryBackend(object):
    """The geometry backend injected into compute_streams / expand_to_calcs.

    Callable as ``backend(node, streams)`` — it computes a Transform/Combine
    node's output geometries in memory, recording every derived molecule in
    ``self.pending`` (materialised into the project only later, by
    ``flush_materialisations``, so a cancelled/preview expand leaves the project
    untouched). ``self.notes`` collects NON-fatal per-molecule problems (an op
    whose atom indices don't exist in one of several molecules is skipped, the
    rest continue) so callers can show them instead of failing silently.

    Holds its own geometry ``cache``, shared with ``read_geometry`` so chained
    Transform→Combine steps see each other's output.
    """

    def __init__(self, project):
        self.project = project
        self.cache = {}
        self.pending = []
        self.notes = []

    def read_geom(self, fname):
        return read_geometry(self.project, fname, self.cache)

    def _emit(self, name, syms, xyz, q, mult, note, node_id):
        self.cache[name] = {"symbols": syms, "coords": xyz, "charge": q, "mult": mult}
        self.pending.append({"fname": name, "symbols": syms, "coords": xyz, "charge": q,
                             "mult": mult, "note": note, "node_id": node_id})
        return name

    def __call__(self, node, streams):
        if node.type == "transform":
            ops = node.config.get("ops") or []
            out = []
            # The provenance comment records what actually RAN — disabled ops are
            # noted as skipped rather than silently claimed.
            live = transform_mod.enabled_ops(ops)
            note_ops = "; ".join(transform_mod.describe_op(o) for o in live)
            if len(live) != len(ops):
                note_ops += " [{} op(s) disabled]".format(len(ops) - len(live))
            for nm in streams[0]:
                # Per-molecule: one molecule the ops don't fit (atom index out of
                # range, ring bond, ...) is SKIPPED with a note — the rest of the
                # stream continues.
                try:
                    g = self.read_geom(nm)
                    xyz = transform_mod.apply_ops(g["symbols"], g["coords"], ops)
                except ValueError as e:
                    self.notes.append("Transform skipped '{}': {}".format(nm, e))
                    continue
                out.append(self._emit("{}_tf{}".format(nm, node.id[:4]),
                                      list(g["symbols"]), xyz, g["charge"], g["mult"],
                                      "Transform of {}: {}".format(nm, note_ops), node.id))
            return out
        # combine
        mode = node.config.get("mode", "merge")
        if mode == "pairwise":
            lens = {len(s) for s in streams if len(s) != 1}
            if len(lens) > 1:
                raise ValueError("pairwise combine: the input wires carry different "
                                 "numbers of molecules ({})".format(sorted(lens)))
            n = max(len(s) for s in streams)
            rows = [[s[0] if len(s) == 1 else s[i] for s in streams] for i in range(n)]
        else:
            rows = [[nm for s in streams for nm in s]]
        base = (node.config.get("name") or "").strip() or "combined"
        out = []
        for i, row in enumerate(rows):
            geoms = [self.read_geom(nm) for nm in row]
            syms, xyz = transform_mod.combine([(g["symbols"], g["coords"]) for g in geoms])
            q = sum(g["charge"] for g in geoms)
            mult = sum(g["mult"] - 1 for g in geoms) + 1
            if node.config.get("charge") is not None:
                q = int(node.config["charge"])
            if node.config.get("mult") is not None:
                mult = int(node.config["mult"])
            name = base if len(rows) == 1 else "{}_{:02d}".format(base, i)
            out.append(self._emit("{}_cb{}".format(name, node.id[:4]), syms, xyz, q, mult,
                                  "Combined from: " + " + ".join(row), node.id))
        return out


def flush_materialisations(project, pending, needed=None):
    """Write the pending derived geometries into `project`: a TRANSFORM/<name>.xyz
    plus — for the ones a calculation actually uses — a locked Molecule row
    (updated in place on a re-expand). A molecule whose calcs already left the
    planning stage (exported / submitted) is left untouched, so a finished result
    can't silently sit on different coordinates. Returns a list of warning strings.

    `needed` is the set of molecule filenames the expansion's calcs reference
    (None = all of them, the old behaviour). Intermediate geometries — a Transform
    whose output only feeds another Transform / a Combine / a Write — get their
    .xyz written (so you can inspect them) but NO Molecule row: they aren't
    molecules of the project, they're steps on the way to one, and listing every
    one of them buried the real inputs in the Molecules tab. A row that exists
    from an older expansion but is no longer needed (and has no calculations
    pointing at it) is removed as part of the same clean-up.
    """
    from orca_workbench.core import coords as coords_mod
    root = project.root()
    warns = []
    used_by_calc = {c.molecule_filename for c in project.planned_calcs}
    for it in pending:
        fname = it["fname"]
        busy = any(c.molecule_filename == fname and (c.exported or c.job_id)
                   for c in project.planned_calcs)
        existing = project.molecule_by_filename(fname)
        if existing is not None and busy:
            warns.append("{}: calculations were already built/submitted with the "
                         "previous geometry — left unchanged (DECONSTRUCT them to "
                         "re-transform).".format(fname))
            continue
        xyz_rel = "TRANSFORM/{}.xyz".format(inputs_mod.safe_path_component(fname))
        c = it["coords"]
        atoms = [(it["symbols"][k], float(c[k][0]), float(c[k][1]), float(c[k][2]))
                 for k in range(len(it["symbols"]))]
        coords_mod.write_xyz(os.path.join(root, xyz_rel), atoms,
                             {"name": fname, "comment": it["note"]})
        wanted = needed is None or fname in needed or fname in used_by_calc
        if not wanted:
            # An intermediate: keep the file, drop any stale row for it.
            if existing is not None:
                project.molecules = [m for m in project.molecules if m.filename != fname]
            continue
        if existing is None:
            project.molecules.append(Molecule(
                name=fname, filename=fname, smiles=None, charge=it["charge"],
                multiplicity=it["mult"], comment=it["note"], generated=True,
                gen_status="ok", method="transform", coords_locked=True,
                xyz_path=xyz_rel))
        else:
            existing.charge = it["charge"]
            existing.multiplicity = it["mult"]
            existing.comment = it["note"]
            existing.xyz_path = xyz_rel
            existing.coords_locked = True
            existing.gen_status = "ok"
    return warns


def find_existing_calc(project, origin_node, mol, category, recipe_name):
    """The PlannedCalc a workflow expansion should REUSE for this (node, molecule)
    instead of creating a duplicate — matched first by exact graph-node identity
    (survives recipe edits on the node), then by target directory (molecule +
    category + recipe). None if there is none."""
    pc = project.planned_calcs
    if origin_node:
        for c in pc:
            if getattr(c, "origin_node", None) == origin_node and c.molecule_filename == mol:
                return c
    for c in pc:
        if (c.molecule_filename == mol and c.category == category
                and c.recipe_name == recipe_name):
            return c
    return None


def expand_project_workflow(project, calc_done=None, log=None):
    """Expand `project.workflow` into PlannedCalcs, materialising Transform/Combine
    geometries into the project, and append the new calcs to
    ``project.planned_calcs``.

    Idempotent: an already-generated graph reuses its calcs (matched by
    ``find_existing_calc``) rather than duplicating them, and finished calcs are
    kept verbatim while unfinished ones adopt any edits made to the graph.
    ``calc_done(calc) -> bool`` reports whether a calc has already finished
    (defaults to "no"); ``log`` is an optional progress sink.

    Returns a dict: ``expanded`` (bool — did anything get produced), ``new`` /
    ``reused`` counts, ``warnings`` (non-fatal notes) and ``blockers`` (validation
    errors that stopped expansion; empty on success). A project with no
    calculation nodes is a no-op (``expanded`` False, no blockers).
    """
    log = log or (lambda m: None)
    calc_done = calc_done or (lambda c: False)
    wf = wf_mod.Workflow.from_dict(project.workflow) if project.workflow else wf_mod.Workflow()
    if not any(n.type in wf_mod.CALC_NODE_TYPES for n in wf.nodes):
        return {"expanded": False, "new": 0, "reused": 0, "warnings": [], "blockers": []}

    # The multiple-Molecules note is informational, not a blocker (mirrors the
    # GUI). validate() already flags a Combine that feeds a calc without an
    # explicit charge/mult, so no separate check is needed here.
    blockers = [i for i in wf.validate() if not i.startswith("Multiple Molecules")]
    if blockers:
        return {"expanded": False, "new": 0, "reused": 0, "warnings": [], "blockers": blockers}

    mol_files = [m.filename for m in project.molecules]
    existing_before = {id(c) for c in project.planned_calcs}

    def factory(mol, recipe_name, category, geometry_source, parent_id, gate, origin_node):
        onode = wf.node(origin_node)
        gspec = onode.config.get("geom_spec") if onode is not None else None
        xkw = onode.config.get("extra_keywords") if onode is not None else None
        existing = find_existing_calc(project, origin_node, mol, category, recipe_name)
        if existing is not None:
            if getattr(existing, "origin_node", None) is None:
                existing.origin_node = origin_node
            # Keep finished steps verbatim; let unfinished ones adopt graph edits.
            if not calc_done(existing):
                existing.recipe_name = recipe_name
                existing.category = category
                existing.geometry_source = geometry_source
                existing.parent_id = parent_id
                existing.gate = gate
                existing.geom_spec = gspec
                existing.extra_keywords = xkw
            return existing
        return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe_name,
                           category=category, geometry_source=geometry_source,
                           parent_id=parent_id, gate=gate, origin_node=origin_node,
                           geom_spec=gspec, extra_keywords=xkw)

    backend = GeometryBackend(project)
    calcs, warnings, _node_map = wf_mod.expand_to_calcs(wf, mol_files, factory,
                                                        transform_apply=backend)
    warnings = list(warnings) + list(backend.notes)
    if not calcs:
        return {"expanded": False, "new": 0, "reused": 0, "warnings": warnings, "blockers": []}
    if backend.pending:
        # Only derived geometries a calc actually runs on become project molecules;
        # intermediate Transform steps stay files on disk (see flush_materialisations).
        warnings.extend(flush_materialisations(
            project, backend.pending, needed={c.molecule_filename for c in calcs}))
    new_calcs = [c for c in calcs if id(c) not in existing_before]
    for c in new_calcs:
        project.planned_calcs.append(c)
    reused = len(calcs) - len(new_calcs)
    return {"expanded": True, "new": len(new_calcs), "reused": reused,
            "warnings": list(dict.fromkeys(warnings)), "blockers": []}
