"""The ZPVA builder node is a meta-node: it must be registered but NOT picked up
by the static expand_to_calcs (it runs its own action from the UI). These guard
that contract so a future change to the node registry can't silently start
double-expanding it.

Run:  python -m pytest tests/test_workflow_zpva.py -q
"""

from orca_workbench.core import workflow as wf
from orca_workbench.core.project import PlannedCalc, new_calc_id


def _factory(mol, recipe, category, gsource, parent, gate, origin):
    return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe,
                       category=category, geometry_source=gsource, parent_id=parent,
                       gate=gate, origin_node=origin)


def test_zpva_node_registered_as_builder():
    assert "zpva" in wf.NODE_TYPES
    assert wf.NODE_TYPES["zpva"]["kind"] == "builder"
    assert "zpva" in wf.BUILDER_NODE_TYPES
    assert "zpva" not in wf.CALC_NODE_TYPES        # never statically expanded


def test_frequencies_geometry_connects_into_zpva():
    w = wf.Workflow()
    f = w.add_node("frequencies", config={"recipe": "FreqRecipe"})
    z = w.add_node("zpva")
    ok, _why = w.can_connect(f.id, "geometry", z.id, "geometry")
    assert ok


def test_optimize_exposes_results_output():
    outs = dict(wf.NODE_TYPES["optimize"]["outputs"])
    assert outs.get("geometry") == "geometry"
    assert outs.get("results") == "results"      # OPT results can feed a Report


def test_optimize_results_connects_to_report():
    w = wf.Workflow()
    o = w.add_node("optimize", config={"recipe": "OptRecipe"})
    r = w.add_node("report")
    ok, _why = w.can_connect(o.id, "results", r.id, "results")
    assert ok


def test_traces_to_type_gates_zpva_suggestion():
    w = wf.Workflow()
    m = w.add_node("molecules")
    o = w.add_node("optimize", config={"recipe": "O"})
    f = w.add_node("frequencies", config={"recipe": "F"})
    w.add_edge(m.id, "geometry", o.id, "geometry")
    w.add_edge(o.id, "geometry", f.id, "geometry")
    # ZPVA may be offered downstream of Frequencies, but not of Optimize/Molecules.
    assert w.traces_to_type(f.id, "frequencies") is True
    assert w.traces_to_type(o.id, "frequencies") is False
    assert w.traces_to_type(m.id, "frequencies") is False


def test_traces_to_type_through_filter():
    # Frequencies -> Filter still traces back to Frequencies, so ZPVA stays valid
    # off the filter's geometry pin (ZPVA a chosen subset).
    w = wf.Workflow()
    f = w.add_node("frequencies", config={"recipe": "F"})
    fl = w.add_node("filter")
    w.add_edge(f.id, "geometry", fl.id, "geometry")
    assert w.traces_to_type(fl.id, "frequencies") is True


def test_expand_ignores_zpva_builder():
    w = wf.Workflow()
    m = w.add_node("molecules")
    f = w.add_node("frequencies", config={"recipe": "FreqRecipe"})
    z = w.add_node("zpva", config={"recipe": "SPRecipe"})
    w.add_edge(m.id, "geometry", f.id, "geometry")
    w.add_edge(f.id, "geometry", z.id, "geometry")

    calcs, warnings, node_map = wf.expand_to_calcs(w, ["mol1"], _factory)
    # only the Frequencies node expands; the ZPVA builder produces nothing here.
    assert len(calcs) == 1
    assert calcs[0].origin_node == f.id
    assert z.id not in node_map
