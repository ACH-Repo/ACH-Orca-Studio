"""Tests for the imported-structure naming/provenance helpers in the Molecules tab.

Pure string helpers, but they live next to Tk code, so importorskip guards the
(display-free) module import in case Tkinter isn't available on a CI box.

Run from the repo root:  python -m pytest tests/ -q
"""

import pytest

mt = pytest.importorskip("orca_workbench.ui.molecules_tab")


def test_imported_stem_single_keeps_basename():
    assert mt.imported_stem("input_file", 0, False) == "input_file"


def test_imported_stem_multi_encodes_conformer():
    assert mt.imported_stem("input_file", 0, True) == "input_file_conf0"
    assert mt.imported_stem("input_file", 3, True) == "input_file_conf3"


def test_imported_comment_single_and_multi():
    assert mt.imported_comment("input_file.sdf", 0, False) == "imported from input_file.sdf"
    assert (mt.imported_comment("input_file.sdf", 2, True)
            == "imported from input_file.sdf (conformer 2)")
