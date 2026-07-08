"""Figure rendering core (core.figures): SCF-bar grouping / y-modes (pure) and,
when matplotlib is present, the actual bar drawing + file generation.

Run:  python -m pytest tests/test_figures.py -q
"""

import os

import pytest

from orca_workbench.core import figures


def _entries():
    # Precomputed-energy entries (as the interactive window supplies them).
    return [
        {"molecule": "000", "name": "water", "smiles": "O", "calctype": "OPT",
         "energy": -76.020, "recipe": "opt"},
        {"molecule": "000", "name": "water", "smiles": "O", "calctype": "NMR",
         "energy": -76.010, "recipe": "nmr"},
        {"molecule": "001", "name": "methane", "smiles": "C", "calctype": "OPT",
         "energy": -40.190, "recipe": "opt"},
    ]


# ------------------------------------------------------------------ pure logic

def test_scf_bar_groups_orders_and_types():
    groups, calctypes = figures.scf_bar_groups(_entries())
    assert [g["molecule"] for g in groups] == ["000", "001"]     # first-seen order
    assert calctypes == ["OPT", "NMR"]                            # both types, once
    g0 = groups[0]
    assert g0["name"] == "water" and g0["smiles"] == "O"
    assert sorted(b["calctype"] for b in g0["bars"]) == ["NMR", "OPT"]


def test_scf_bar_groups_skips_missing_energy():
    entries = _entries() + [{"molecule": "002", "name": "x", "calctype": "SP"}]  # no energy
    groups, _ = figures.scf_bar_groups(entries)
    assert [g["molecule"] for g in groups] == ["000", "001"]      # 002 dropped


def test_bar_values_modes():
    groups, calctypes = figures.scf_bar_groups(_entries())
    abs_of, abs_lbl = figures._bar_values(groups, calctypes, "absolute")
    assert abs_of(groups[0], groups[0]["bars"][0]) == groups[0]["bars"][0]["energy"]
    assert "Eh" in abs_lbl

    relm_of, relm_lbl = figures._bar_values(groups, calctypes, "rel_molecule")
    # per-molecule: each group's lowest bar is 0, the other is +delta (kcal/mol)
    vals = sorted(round(relm_of(groups[0], b), 3) for b in groups[0]["bars"])
    assert vals[0] == 0.0 and vals[1] > 0.0
    assert "per molecule" in relm_lbl

    relg_of, _ = figures._bar_values(groups, calctypes, "rel_global")
    # global: the overall lowest (methane, -76.02 is not lowest; water OPT -76.02 is)
    allvals = [relg_of(g, b) for g in groups for b in g["bars"]]
    assert min(allvals) == 0.0 and all(v >= 0.0 for v in allvals)


# ------------------------------------------------------------ matplotlib-backed

def test_draw_scf_bars_returns_bar_records():
    pytest.importorskip("matplotlib")
    groups, calctypes = figures.scf_bar_groups(_entries())
    fig = figures._new_figure()
    ax = fig.add_subplot(111)
    recs = figures.draw_scf_bars(ax, groups, calctypes, mode="absolute")
    # 3 bars total (water: OPT+NMR, methane: OPT)
    assert len(recs) == 3
    assert {r["molecule"] for r in recs} == {"000", "001"}
    assert all("rect" in r and "energy" in r for r in recs)


def test_render_scf_bar_none_when_empty():
    pytest.importorskip("matplotlib")
    assert figures.render_scf_bar([]) is None


def test_generate_all_figures_writes_scf(tmp_path):
    pytest.importorskip("matplotlib")
    out_dir = str(tmp_path / "FIGS")
    written = figures.generate_all_figures(_entries(), out_dir, img_format="png")
    names = [os.path.basename(p) for p in written]
    assert "SCF_final_energies.png" in names
    # a molecule has >1 calc type -> a relative view is also emitted
    assert "SCF_final_energies_relative.png" in names
    for p in written:
        assert os.path.isfile(p)
