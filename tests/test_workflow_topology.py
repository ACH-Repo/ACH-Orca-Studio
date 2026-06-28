"""Branching / merging / multiple-network behaviour of the workflow engine.

These codify the contract the GUI relies on:
  - geometry FANS OUT (one output -> many consumers) but never MERGES (a geometry
    input takes exactly one edge);
  - the only merge is RESULTS fanning into a Report;
  - multiple Molecules sources are independent networks, each over its own
    molecule set, and source_ids scopes a run to a subset.

Run:  python -m pytest tests/test_workflow_topology.py -q
"""

from orca_workbench.core import workflow as wf
from orca_workbench.core.project import PlannedCalc, new_calc_id


def _factory(mol, recipe, category, gsource, parent, gate, origin):
    return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe,
                       category=category, geometry_source=gsource, parent_id=parent,
                       gate=gate, origin_node=origin)


def test_geometry_input_rejects_a_second_edge():
    # Two optimize branches cannot merge into one Frequencies geometry input.
    w = wf.Workflow()
    m = w.add_node("molecules")
    o1 = w.add_node("optimize", config={"recipe": "o1"})
    o2 = w.add_node("optimize", config={"recipe": "o2"})
    f = w.add_node("frequencies", config={"recipe": "f"})
    w.add_edge(m.id, "geometry", o1.id, "geometry")
    w.add_edge(m.id, "geometry", o2.id, "geometry")
    w.add_edge(o1.id, "geometry", f.id, "geometry")
    edge, why = w.add_edge(o2.id, "geometry", f.id, "geometry")
    assert edge is None and "already connected" in why


def test_results_input_allows_fan_in():
    # The only merge: many calc results into one Report.
    w = wf.Workflow()
    f = w.add_node("frequencies", config={"recipe": "f"})
    p = w.add_node("property", config={"recipe": "p"})
    r = w.add_node("report")
    e1, _ = w.add_edge(f.id, "results", r.id, "results")
    e2, _ = w.add_edge(p.id, "results", r.id, "results")
    assert e1 is not None and e2 is not None
    assert len(w.edges_into(r.id, "results")) == 2


def test_fan_out_branch_shares_one_parent():
    w = wf.Workflow()
    m = w.add_node("molecules")
    o = w.add_node("optimize", config={"recipe": "o"})
    f = w.add_node("frequencies", config={"recipe": "f"})
    p = w.add_node("property", config={"recipe": "p"})
    w.add_edge(m.id, "geometry", o.id, "geometry")
    w.add_edge(o.id, "geometry", f.id, "geometry")
    w.add_edge(o.id, "geometry", p.id, "geometry")
    calcs, _w, _n = wf.expand_to_calcs(w, ["A"], _factory)
    by = {c.recipe_name: c for c in calcs}
    assert by["f"].parent_id == by["o"].id          # both branches derive from
    assert by["p"].parent_id == by["o"].id          # the same optimize calc


def test_two_selection_sources_are_independent_networks():
    w = wf.Workflow()
    m1 = w.add_node("molecules", config={"mode": "selection", "filenames": ["A"]})
    o1 = w.add_node("optimize", config={"recipe": "oA"})
    m2 = w.add_node("molecules", config={"mode": "selection", "filenames": ["B"]})
    o2 = w.add_node("optimize", config={"recipe": "oB"})
    w.add_edge(m1.id, "geometry", o1.id, "geometry")
    w.add_edge(m2.id, "geometry", o2.id, "geometry")
    calcs, _w, _n = wf.expand_to_calcs(w, ["A", "B"], _factory)
    assert {(c.molecule_filename, c.recipe_name) for c in calcs} == {("A", "oA"), ("B", "oB")}
    # scope to just the B network
    only_b, _w, _n = wf.expand_to_calcs(w, ["A", "B"], _factory, source_ids={m2.id})
    assert {(c.molecule_filename, c.recipe_name) for c in only_b} == {("B", "oB")}


def test_multiple_sources_validate_is_informational_not_false():
    w = wf.Workflow()
    w.add_node("molecules")
    w.add_node("molecules")
    msgs = w.validate()
    assert any(m.startswith("Multiple Molecules") for m in msgs)
    assert not any("only the first" in m for m in msgs)   # the old false claim is gone
