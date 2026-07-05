"""Transform / Combine workflow nodes: geometry streams, fan-in wiring, and
expand integration (the geometry backend is injected, so tests fake it).

Run:  python -m pytest tests/test_workflow_transform.py -q
"""

from orca_workbench.core import workflow as wf
from orca_workbench.core.project import PlannedCalc, new_calc_id


def _factory(mol, recipe, category, gsource, parent, gate, origin):
    return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe,
                       category=category, geometry_source=gsource, parent_id=parent,
                       gate=gate, origin_node=origin)


def _fake_backend(node, streams):
    """Fake geometry backend: Transform renames m -> m_T; Combine merges every
    incoming molecule into one 'combo' entry."""
    if node.type == "transform":
        return [m + "_T" for m in streams[0]]
    flat = [m for s in streams for m in s]
    return ["combo_" + "_".join(flat)]


def test_node_types_registered():
    assert wf.NODE_TYPES["transform"]["kind"] == "transform"
    assert wf.NODE_TYPES["combine"]["kind"] == "combine"
    assert "transform" in wf.GEOMETRY_NODE_TYPES and "combine" in wf.GEOMETRY_NODE_TYPES
    assert "transform" not in wf.CALC_NODE_TYPES


def test_combine_input_fans_in_but_optimize_does_not():
    w = wf.Workflow()
    m1, m2 = w.add_node("molecules"), w.add_node("molecules")
    comb = w.add_node("combine")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    e1, _ = w.add_edge(m1.id, "geometry", comb.id, "geometry")
    e2, _ = w.add_edge(m2.id, "geometry", comb.id, "geometry")   # 2nd wire OK
    assert e1 is not None and e2 is not None
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    e4, why = w.add_edge(m1.id, "geometry", opt.id, "geometry")  # opt stays single
    assert e4 is None and "already connected" in why


def test_transform_stream_renames_molecules():
    w = wf.Workflow()
    m = w.add_node("molecules")
    tr = w.add_node("transform", config={"ops": [{"op": "translate", "vec": [1, 0, 0]}]})
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m.id, "geometry", tr.id, "geometry")
    w.add_edge(tr.id, "geometry", opt.id, "geometry")
    calcs, warns, _nm = wf.expand_to_calcs(w, ["a", "b"], _factory,
                                           transform_apply=_fake_backend)
    assert sorted(c.molecule_filename for c in calcs) == ["a_T", "b_T"]
    assert all(c.geometry_source == "initial" for c in calcs)


def test_transform_without_backend_or_ops_is_identity():
    w = wf.Workflow()
    m = w.add_node("molecules")
    tr = w.add_node("transform")          # no ops
    p = w.add_node("property", config={"recipe": "SP"})
    w.add_edge(m.id, "geometry", tr.id, "geometry")
    w.add_edge(tr.id, "geometry", p.id, "geometry")
    calcs, _w, _nm = wf.expand_to_calcs(w, ["a"], _factory)
    assert [c.molecule_filename for c in calcs] == ["a"]


def test_combine_merges_two_sources_into_one_calc():
    w = wf.Workflow()
    m1 = w.add_node("molecules", config={"mode": "selection", "filenames": ["a"]})
    m2 = w.add_node("molecules", config={"mode": "selection", "filenames": ["b"]})
    comb = w.add_node("combine")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m1.id, "geometry", comb.id, "geometry")
    w.add_edge(m2.id, "geometry", comb.id, "geometry")
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    calcs, warns, _nm = wf.expand_to_calcs(w, ["a", "b"], _factory,
                                           transform_apply=_fake_backend)
    assert [c.molecule_filename for c in calcs] == ["combo_a_b"]
    assert calcs[0].geometry_source == "initial"


def test_chain_transform_then_optimize_then_freq_keeps_parent_links():
    w = wf.Workflow()
    m = w.add_node("molecules")
    tr = w.add_node("transform", config={"ops": [{"op": "center", "mode": "com"}]})
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    fq = w.add_node("frequencies", config={"recipe": "FREQ"})
    w.add_edge(m.id, "geometry", tr.id, "geometry")
    w.add_edge(tr.id, "geometry", opt.id, "geometry")
    w.add_edge(opt.id, "geometry", fq.id, "geometry")
    calcs, _w, _nm = wf.expand_to_calcs(w, ["a"], _factory,
                                        transform_apply=_fake_backend)
    opt_c = next(c for c in calcs if c.origin_node == opt.id)
    fq_c = next(c for c in calcs if c.origin_node == fq.id)
    assert opt_c.molecule_filename == "a_T" and opt_c.geometry_source == "initial"
    assert fq_c.geometry_source == "parent:" + opt_c.id   # optimized geom flows on


def test_combine_without_backend_warns_and_skips():
    w = wf.Workflow()
    m = w.add_node("molecules")
    comb = w.add_node("combine")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    w.add_edge(m.id, "geometry", comb.id, "geometry")
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    calcs, warns, _nm = wf.expand_to_calcs(w, ["a"], _factory)   # no backend
    assert calcs == []
    assert any("geometry backend" in x for x in warns)


def test_validate_blocks_transform_after_calc():
    w = wf.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    tr = w.add_node("transform", config={"ops": [{"op": "center", "mode": "com"}]})
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    w.add_edge(opt.id, "geometry", tr.id, "geometry")
    issues = w.validate()
    assert any("BEFORE the calculation nodes" in i for i in issues)


def test_validate_flags_bad_ops_and_missing_input():
    w = wf.Workflow()
    w.add_node("molecules")
    w.add_node("transform", config={"ops": [{"op": "warp"}]})
    issues = w.validate()
    assert any("no input connected" in i for i in issues)
    assert any("unknown type" in i for i in issues)


def test_source_ids_scoping_skips_other_networks():
    w = wf.Workflow()
    m1 = w.add_node("molecules")
    tr = w.add_node("transform", config={"ops": [{"op": "center", "mode": "com"}]})
    p1 = w.add_node("property", config={"recipe": "SP"})
    w.add_edge(m1.id, "geometry", tr.id, "geometry")
    w.add_edge(tr.id, "geometry", p1.id, "geometry")
    m2 = w.add_node("molecules")
    p2 = w.add_node("property", config={"recipe": "SP"})
    w.add_edge(m2.id, "geometry", p2.id, "geometry")
    calcs, _w, _nm = wf.expand_to_calcs(w, ["a"], _factory, source_ids={m2.id},
                                        transform_apply=_fake_backend)
    assert [c.origin_node for c in calcs] == [p2.id]   # network 1 untouched


def test_combine_warns_about_an_empty_input_wire():
    # An upstream node that emits nothing (failed ops / empty selection) must
    # produce a VISIBLE warning, not a silent partial combine.
    w = wf.Workflow()
    m1 = w.add_node("molecules", config={"mode": "selection", "filenames": ["a"]})
    m2 = w.add_node("molecules", config={"mode": "selection", "filenames": []})
    comb = w.add_node("combine")
    w.add_edge(m1.id, "geometry", comb.id, "geometry")
    w.add_edge(m2.id, "geometry", comb.id, "geometry")
    streams, warns = wf.compute_streams(
        w, {m1.id: ["a"], m2.id: []}, _fake_backend)
    assert streams[comb.id] == ["combo_a"]          # the good wire still works
    assert any("carries no molecules" in x for x in warns)


def test_compute_streams_directly():
    w = wf.Workflow()
    m = w.add_node("molecules")
    flt = w.add_node("filter", config={"mode": "include", "kind": "substring",
                                       "pattern": "keep"})
    tr = w.add_node("transform", config={"ops": [{"op": "center", "mode": "com"}]})
    w.add_edge(m.id, "geometry", flt.id, "geometry")
    w.add_edge(flt.id, "geometry", tr.id, "geometry")
    streams, warns = wf.compute_streams(w, {m.id: ["keep_a", "drop_b"]}, _fake_backend)
    assert streams[m.id] == ["keep_a", "drop_b"]
    assert streams[flt.id] == ["keep_a"]
    assert streams[tr.id] == ["keep_a_T"]
    assert warns == []


def test_combine_needs_charge_mult_only_when_feeding_a_calc():
    w = wf.Workflow()
    m1 = w.add_node("molecules")
    m2 = w.add_node("molecules")
    comb = w.add_node("combine")
    opt = w.add_node("optimize", config={"recipe": "R"})
    w.add_edge(m1.id, "geometry", comb.id, "geometry")
    w.add_edge(m2.id, "geometry", comb.id, "geometry")
    # no calc downstream yet -> not required
    assert w.combine_needs_charge_mult() == []
    w.add_edge(comb.id, "geometry", opt.id, "geometry")
    needs = w.combine_needs_charge_mult()
    assert needs and needs[0][0] == comb.id and set(needs[0][1]) == {"charge", "mult"}
    assert any("charge and multiplicity" in i for i in w.validate())
    comb.config["charge"] = 0
    assert w.combine_needs_charge_mult()[0][1] == ["mult"]
    comb.config["mult"] = 1
    assert w.combine_needs_charge_mult() == []
    assert not any("charge" in i and "Combine feeds" in i for i in w.validate())
