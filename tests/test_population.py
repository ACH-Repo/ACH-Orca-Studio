"""Tests for the population-charge parser + report extractor (Q3 tier 1).

Mulliken + Loewdin atomic charges (and Mulliken spin populations for open-shell).
Pure / offline; sample blocks mirror the standard ORCA output layout.

Run:  python -m pytest tests/test_population.py -q
"""

from orca_workbench.core import orca_parser as P
from orca_workbench.core import reporting


CLOSED = """\
-----------------------
MULLIKEN ATOMIC CHARGES
-----------------------
   0 O :   -0.370272
   1 H :    0.185136
   2 H :    0.185136
Sum of atomic charges:   -0.0000000

--------------------------------
LOEWDIN ATOMIC CHARGES
--------------------------------
   0 O :   -0.272410
   1 H :    0.136205
   2 H :    0.136205
"""

OPEN = """\
-----------------------------------------------------
MULLIKEN ATOMIC CHARGES AND SPIN POPULATIONS
-----------------------------------------------------
   0 O :   -0.100000    1.000000
   1 H :    0.050000    0.000000
   2 H :    0.050000    0.000000
Sum of atomic charges:    0.0000000
Sum of atomic spin populations:    1.0000000
"""


def test_parse_closed_shell_merges_mulliken_and_loewdin():
    rows = P.parse_population(CLOSED)
    assert len(rows) == 3
    o = rows[0]
    assert o["index"] == 0 and o["element"] == "O"
    assert o["mulliken"] == -0.370272 and o["loewdin"] == -0.272410
    assert o["spin"] is None
    assert rows[1]["mulliken"] == 0.185136


def test_parse_open_shell_captures_spin():
    rows = P.parse_population(OPEN)
    assert rows[0]["spin"] == 1.0 and rows[0]["mulliken"] == -0.1
    assert rows[1]["spin"] == 0.0
    assert rows[0]["loewdin"] is None          # only the Mulliken block present


def test_no_population_block():
    assert P.parse_population("FINAL SINGLE POINT ENERGY -76.0\n") == []


def test_two_letter_elements():
    txt = ("MULLIKEN ATOMIC CHARGES\n"
           "   0 Fe :   0.812345\n"
           "   1 Cl :  -0.203451\n\n")
    rows = P.parse_population(txt)
    assert [r["element"] for r in rows] == ["Fe", "Cl"]
    assert rows[0]["mulliken"] == 0.812345


def test_report_extractor_and_csv_summary():
    frag = reporting._x_population(CLOSED, ctx=None)
    assert frag["population_charges"][0]["element"] == "O"
    # the flat CSV summary surfaces the charge spread
    report = {"calculations": [{"properties": frag}]}
    row = reporting._csv_row(report["calculations"][0])
    assert row["mulliken_min"] == -0.370272
    assert row["mulliken_max"] == 0.185136


def test_report_extractor_none_without_block():
    assert reporting._x_population("nothing", ctx=None) is None


# --------------------------------------------------------------- Mayer bond orders
MAYER = """\
  Mayer bond orders larger than 0.100000
B(  0-O ,  1-H ) :   0.9971 B(  0-O ,  2-H ) :   0.9971

-------
TIMINGS
"""


def test_parse_mayer_bond_orders():
    b = P.parse_mayer_bond_orders(MAYER)
    assert len(b) == 2
    assert b[0] == {"atom1": 0, "elem1": "O", "atom2": 1, "elem2": "H", "order": 0.9971}
    assert b[1]["atom2"] == 2


def test_mayer_none_when_absent():
    assert P.parse_mayer_bond_orders("no bonds here") == []


def test_bond_orders_extractor():
    frag = reporting._x_bond_orders(MAYER, ctx=None)
    assert frag["mayer_bond_orders"][0]["order"] == 0.9971
    assert reporting._x_bond_orders("nothing", ctx=None) is None
