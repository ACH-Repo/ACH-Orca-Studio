"""Filter node: static, identity-based molecule subsetting in expand_to_calcs
(distinct from the runtime, result-based Condition gate).

Run:  python -m pytest tests/test_workflow_filter.py -q
"""

from orca_workbench.core import workflow as wf
from orca_workbench.core.project import PlannedCalc, new_calc_id


def _factory(mol, recipe, category, gsource, parent, gate, origin):
    return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe,
                       category=category, geometry_source=gsource, parent_id=parent,
                       gate=gate, origin_node=origin)


def test_filter_node_registered():
    assert wf.NODE_TYPES["filter"]["kind"] == "filter"
    assert "filter" not in wf.CALC_NODE_TYPES


def test_filter_matches_substring():
    inc = {"mode": "include", "kind": "substring", "pattern": "fluoro"}
    assert wf.filter_matches(inc, "4fluoro_opt", 0, 3) is True
    assert wf.filter_matches(inc, "benzene", 1, 3) is False
    exc = {"mode": "exclude", "kind": "substring", "pattern": "benz"}
    assert wf.filter_matches(exc, "benzene", 0, 3) is False
    assert wf.filter_matches(exc, "phenol", 1, 3) is True


def test_filter_matches_index_and_empty():
    idx = {"mode": "include", "kind": "index", "pattern": "0-1,3"}
    assert [wf.filter_matches(idx, "m", i, 5) for i in range(5)] == [True, True, False, True, False]
    assert wf.filter_matches({"pattern": ""}, "anything", 0, 1) is True


def test_expand_filter_restricts_downstream():
    w = wf.Workflow()
    m = w.add_node("molecules")
    flt = w.add_node("filter", config={"mode": "include", "kind": "substring", "pattern": "keep"})
    p = w.add_node("property", config={"recipe": "NMR"})
    w.add_edge(m.id, "geometry", flt.id, "geometry")
    w.add_edge(flt.id, "geometry", p.id, "geometry")
    calcs, _w, _nm = wf.expand_to_calcs(w, ["keep_a", "drop_b", "keep_c"], _factory)
    assert sorted(c.molecule_filename for c in calcs) == ["keep_a", "keep_c"]


def test_expand_filter_after_optimize_keeps_parent_link():
    w = wf.Workflow()
    m = w.add_node("molecules")
    opt = w.add_node("optimize", config={"recipe": "OPT"})
    flt = w.add_node("filter", config={"mode": "include", "kind": "substring", "pattern": "keep"})
    p = w.add_node("property", config={"recipe": "NMR"})
    w.add_edge(m.id, "geometry", opt.id, "geometry")
    w.add_edge(opt.id, "geometry", flt.id, "geometry")
    w.add_edge(flt.id, "geometry", p.id, "geometry")
    calcs, _w, _nm = wf.expand_to_calcs(w, ["keep_a", "drop_b"], _factory)

    opt_calcs = [c for c in calcs if c.origin_node == opt.id]
    prop_calcs = [c for c in calcs if c.origin_node == p.id]
    assert sorted(c.molecule_filename for c in opt_calcs) == ["drop_b", "keep_a"]  # all optimised
    assert [c.molecule_filename for c in prop_calcs] == ["keep_a"]                 # only kept
    keep_opt = next(c for c in opt_calcs if c.molecule_filename == "keep_a")
    assert prop_calcs[0].geometry_source == "parent:" + keep_opt.id               # link survives filter
