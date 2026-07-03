"""Tests for the editor round-trip core (SMILES <-> 2D structure file).

The RDKit path is gated so the suite still passes without RDKit; the pure
newest_structure_file / .smi helpers always run.

Run:  python -m pytest tests/test_roundtrip.py -q
"""

import os
import time

import pytest

from orca_workbench.core import roundtrip as R


def _has_rdkit():
    try:
        import rdkit  # noqa: F401
        return True
    except Exception:
        return False


rdkit_only = pytest.mark.skipif(not _has_rdkit(), reason="RDKit not installed")


@rdkit_only
@pytest.mark.parametrize("smiles", ["CCO", "c1ccccc1", "CC(=O)[O-]", "O=C(O)c1ccccc1"])
def test_smiles_molfile_roundtrip(tmp_path, smiles):
    from rdkit import Chem
    p = str(tmp_path / "m.mol")
    R.write_smiles_molfile(smiles, p)
    assert os.path.isfile(p)
    back = R.read_structure_smiles(p)
    assert back and Chem.CanonSmiles(back) == Chem.CanonSmiles(smiles)


@rdkit_only
def test_blank_smiles_writes_empty_molfile(tmp_path):
    p = str(tmp_path / "blank.mol")
    R.write_smiles_molfile("", p)          # draw-from-scratch canvas
    assert os.path.isfile(p)
    assert R.read_structure_smiles(p) is None   # no atoms -> no SMILES, no crash


@rdkit_only
def test_bad_smiles_raises(tmp_path):
    with pytest.raises(ValueError):
        R.write_smiles_molfile("this-is-not-smiles((", str(tmp_path / "x.mol"))


def test_read_smi_file(tmp_path):
    p = tmp_path / "s.smi"
    p.write_text("CCO ethanol\n", encoding="utf-8")
    assert R.read_structure_smiles(str(p)) == "CCO"


def test_newest_structure_file_picks_latest(tmp_path):
    t0 = time.time()
    (tmp_path / "a.cdxml").write_text("x", encoding="utf-8")
    time.sleep(0.02)
    (tmp_path / "b.mol").write_text("y", encoding="utf-8")
    newest = R.newest_structure_file(str(tmp_path), after=t0)
    assert os.path.basename(newest) == "b.mol"
    # a non-structure file is ignored
    (tmp_path / "c.txt").write_text("z", encoding="utf-8")
    assert os.path.basename(R.newest_structure_file(str(tmp_path), after=t0)) == "b.mol"


def test_newest_structure_file_none_when_empty(tmp_path):
    assert R.newest_structure_file(str(tmp_path), after=0.0) is None
    assert R.read_structure_smiles(str(tmp_path / "missing.mol")) is None
