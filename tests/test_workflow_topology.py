"""Branching / merging / multiple-network behaviour of the workflow engine.

These codify the contract the GUI relies on:
  - geometry FANS OUT (one output -> many consumers) and, on the nodes that
    declare it (every calc node, Combine, Filter), also FANS IN: several geometry
    wires land on one input and their molecules merge into one stream;
  - a node WITHOUT fan_in (Condition, Transform, ZPVA) still takes one wire;
  - RESULTS fan into a Report;
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


def test_to_dict_snapshots_are_independent_of_the_live_config():
    """The editor's undo history is a list of to_dict() snapshots, so a snapshot
    must not alias the node's config — otherwise editing the config also rewrites
    its own 'before' state and the edit is invisible to undo."""
    w = wf.Workflow()
    n = w.add_node("transform", config={"ops": [{"op": "translate", "vec": [1, 0, 0]}]})
    before = w.to_dict()
    n.config["ops"].append({"op": "center", "mode": "com"})
    n.config["ops"][0]["enabled"] = False
    assert len(before["nodes"][0]["config"]["ops"]) == 1
    assert "enabled" not in before["nodes"][0]["config"]["ops"][0]
    assert before != w.to_dict()
    # and a graph rebuilt from the snapshot is decoupled from the original too
    w2 = wf.Workflow.from_dict(before)
    w2.node(n.id).config["ops"].append({"op": "mirror", "plane": "xy"})
    assert len(before["nodes"][0]["config"]["ops"]) == 1


def test_calc_geometry_input_fans_in():
    """Two Optimize branches CAN merge into one Frequencies geometry input; the
    molecules they carry become one stream and each gets its own FREQ, resolved
    against the branch it arrived on."""
    w = wf.Workflow()
    m1 = w.add_node("molecules", config={"mode": "selection", "filenames": ["A"]})
    m2 = w.add_node("molecules", config={"mode": "selection", "filenames": ["B"]})
    o1 = w.add_node("optimize", config={"recipe": "o1"})
    o2 = w.add_node("optimize", config={"recipe": "o2"})
    f = w.add_node("frequencies", config={"recipe": "f"})
    w.add_edge(m1.id, "geometry", o1.id, "geometry")
    w.add_edge(m2.id, "geometry", o2.id, "geometry")
    e1, _ = w.add_edge(o1.id, "geometry", f.id, "geometry")
    e2, why = w.add_edge(o2.id, "geometry", f.id, "geometry")
    assert e1 is not None and e2 is not None, why
    assert len(w.edges_into(f.id, "geometry")) == 2
    calcs, _warn, _nm = wf.expand_to_calcs(w, ["A", "B"], _factory)
    by = {(c.molecule_filename, c.recipe_name): c for c in calcs}
    assert set(by) == {("A", "o1"), ("B", "o2"), ("A", "f"), ("B", "f")}
    # each FREQ derives from the OPT on ITS OWN branch
    assert by[("A", "f")].parent_id == by[("A", "o1")].id
    assert by[("B", "f")].parent_id == by[("B", "o2")].id


def test_fan_in_makes_one_calc_per_distinct_molecule():
    # The same molecule arriving on two wires is still one calculation.
    w = wf.Workflow()
    m1 = w.add_node("molecules")
    m2 = w.add_node("molecules")
    p = w.add_node("property", config={"recipe": "SP"})
    w.add_edge(m1.id, "geometry", p.id, "geometry")
    w.add_edge(m2.id, "geometry", p.id, "geometry")
    calcs, _warn, node_map = wf.expand_to_calcs(w, ["A"], _factory)
    assert [c.molecule_filename for c in calcs] == ["A"]
    assert node_map[p.id] == [calcs[0].id]


def test_geometry_input_without_fan_in_still_takes_one_wire():
    # Condition gates a single calculation's result — merging makes no sense there.
    w = wf.Workflow()
    m = w.add_node("molecules")
    o1 = w.add_node("optimize", config={"recipe": "o1"})
    o2 = w.add_node("optimize", config={"recipe": "o2"})
    cond = w.add_node("condition")
    w.add_edge(m.id, "geometry", o1.id, "geometry")
    w.add_edge(m.id, "geometry", o2.id, "geometry")
    w.add_edge(o1.id, "geometry", cond.id, "in")
    edge, why = w.add_edge(o2.id, "geometry", cond.id, "in")
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


def test_annotation_nodes_are_inert():
    # Comment / Frame are annotations: no ports, ignored by validate + expand, so
    # they can never break a pipeline.
    w = wf.Workflow()
    m = w.add_node("molecules")
    o = w.add_node("optimize", config={"recipe": "opt"})
    w.add_edge(m.id, "geometry", o.id, "geometry")
    w.add_node("comment", config={"text": "a note"})
    w.add_node("frame", config={"title": "grp"})
    assert {"comment", "frame"} <= wf.ANNOTATION_NODE_TYPES
    assert not (wf.ANNOTATION_NODE_TYPES & wf.CALC_NODE_TYPES)
    assert not any("Comment" in i or "Frame" in i for i in w.validate())
    calcs, warnings, _n = wf.expand_to_calcs(w, ["A"], _factory)
    assert {(c.molecule_filename, c.recipe_name) for c in calcs} == {("A", "opt")}
    assert not warnings


def test_multiple_sources_validate_is_informational_not_false():
    w = wf.Workflow()
    w.add_node("molecules")
    w.add_node("molecules")
    msgs = w.validate()
    assert any(m.startswith("Multiple Molecules") for m in msgs)
    assert not any("only the first" in m for m in msgs)   # the old false claim is gone


def test_node_ok_flags_broken_nodes_for_the_hover_glow():
    w = wf.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": ""})      # no recipe
    assert w.node_ok(m.id)                                   # a source is always fine
    assert not w.node_ok(opt.id)                             # no recipe -> not ok
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    opt.config["recipe"] = "OPT"
    assert w.node_ok(opt.id)                                 # recipe + input -> ok
    opt2 = w.add_node("optimize", config={"recipe": "OPT"})  # recipe but no input
    assert not w.node_ok(opt2.id)


def test_node_ok_write_and_combine_and_condition():
    w = wf.Workflow()
    m1 = w.add_node("molecules")
    m2 = w.add_node("molecules")
    wr = w.add_node("write")
    assert not w.node_ok(wr.id)                              # write with no input
    w.add_edge(m1.id, "geometry", wr.id, "geometry")
    assert w.node_ok(wr.id)
    comb = w.add_node("combine")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m1.id, "geometry", comb.id, "geometry")
    w.add_edge(m2.id, "geometry", comb.id, "geometry")
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    assert not w.node_ok(comb.id)                            # feeds a calc: name/charge/mult
    comb.config["charge"] = 0
    comb.config["mult"] = 1
    assert not w.node_ok(comb.id)                            # still nameless
    comb.config["name"] = "dimer"
    assert w.node_ok(comb.id)
    cond = w.add_node("condition")                           # no calc feeder
    assert not w.node_ok(cond.id)


def test_write_node_is_a_sink_ignored_by_expand():
    w = wf.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    wr = w.add_node("write")
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    w.add_edge(m.id, "geometry", wr.id, "geometry")     # geometry fans out to Write
    assert wf.NODE_TYPES["write"]["kind"] == "writer"
    assert "write" not in wf.CALC_NODE_TYPES
    calcs, warnings, _n = wf.expand_to_calcs(w, ["A"], _factory)
    # the Write node produces no calc; the Optimize still does
    assert {(c.molecule_filename, c.recipe_name) for c in calcs} == {("A", "OPT")}
    assert not any("Write" in x for x in warnings)
