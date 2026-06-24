"""Tests for the universal coordinate reader in core.coords.

The .xyz paths are dependency-free and always run. SDF / OpenBabel / RDKit cases
are gated so the suite still passes on a machine without the chem backends.

Run from the repo root:  python -m pytest tests/ -q
"""

import pytest

from orca_workbench.core import coords


def _write(path, text):
    with open(str(path), "w", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def _has_chem_backend():
    for mod in ("openbabel", "rdkit"):
        try:
            __import__(mod)
            return True
        except Exception:
            continue
    return False


SINGLE_XYZ = "2\ncomment\nH 0.0 0.0 0.0\nO 0.0 0.0 0.9\n"
MULTI_XYZ = (
    "1\nframe0\nH 0.0 0.0 0.0\n"
    "1\nframe1\nH 0.0 0.0 1.0\n"
    "1\nframe2\nH 0.0 0.0 2.0\n"
)


# --------------------------------------------------------------- xyz (native)
def test_read_single_xyz(tmp_path):
    structs = coords.read_structures(_write(tmp_path / "m.xyz", SINGLE_XYZ))
    assert len(structs) == 1
    atoms, _meta = structs[0]
    assert [a[0] for a in atoms] == ["H", "O"]


def test_read_multiframe_xyz(tmp_path):
    structs = coords.read_structures(_write(tmp_path / "traj.xyz", MULTI_XYZ))
    assert len(structs) == 3
    assert [s[0][0][3] for s in structs] == [0.0, 1.0, 2.0]   # H z per frame


def test_read_coords_file_index_and_last(tmp_path):
    p = _write(tmp_path / "traj.xyz", MULTI_XYZ)
    atoms, _m, n = coords.read_coords_file(p, conformer_index=1)
    assert n == 3 and atoms[0][3] == 1.0
    atoms_last, _m2, _n2 = coords.read_coords_file(p, conformer_index=-1)
    assert atoms_last[0][3] == 2.0


def test_read_coords_file_out_of_range(tmp_path):
    p = _write(tmp_path / "m.xyz", SINGLE_XYZ)
    with pytest.raises(coords.CoordGenError):
        coords.read_coords_file(p, conformer_index=5)


def test_xyz_metadata_roundtrip(tmp_path):
    meta = {"name": "Water", "smiles": "O", "charge": 0, "multiplicity": 1}
    p = str(tmp_path / "w.xyz")
    coords.write_xyz(p, [("O", 0.0, 0.0, 0.0), ("H", 0.0, 0.0, 0.9)], meta)
    _atoms, m, n = coords.read_coords_file(p)
    assert n == 1 and m["name"] == "Water" and m["smiles"] == "O"


def test_missing_file_raises(tmp_path):
    with pytest.raises(coords.CoordGenError):
        coords.read_structures(str(tmp_path / "nope.xyz"))


def test_empty_xyz_raises(tmp_path):
    with pytest.raises(coords.CoordGenError):
        coords.read_structures(_write(tmp_path / "empty.xyz", ""))


# --------------------------------------------------------------- format helpers
def test_supported_ext():
    assert coords.is_supported_import_file("foo.sdf")
    assert coords.is_supported_import_file("FOO.XYZ")
    assert not coords.is_supported_import_file("foo.txt")


def test_dialog_filetypes_shape():
    ft = coords.import_dialog_filetypes()
    assert ft[0][0] == "Coordinate files"
    assert "*.xyz" in ft[0][1] and "*.sdf" in ft[0][1]
    assert ft[-1] == ("All files", "*.*")


# --------------------------------------------------------------- SDF (gated)
SDF_TWO_RECORDS = """\
mol0
  test

  1  0  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$$$$
mol1
  test

  1  0  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    5.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$$$$
"""


def test_read_sdf_multirecord_converts(tmp_path):
    if not _has_chem_backend():
        pytest.skip("neither OpenBabel nor RDKit available")
    structs = coords.read_structures(_write(tmp_path / "two.sdf", SDF_TWO_RECORDS))
    assert len(structs) == 2
    assert structs[0][0][0][0] == "H"   # first atom symbol of first record


# --------------------------------------------------------------- structure selection
def test_parse_structure_selection_single_and_last():
    assert coords.parse_structure_selection("0", 5) == [0]
    assert coords.parse_structure_selection("-1", 5) == [4]
    assert coords.parse_structure_selection("3", 5) == [3]


def test_parse_structure_selection_all():
    assert coords.parse_structure_selection("all", 3) == [0, 1, 2]
    assert coords.parse_structure_selection("*", 3) == [0, 1, 2]


def test_parse_structure_selection_list_and_range():
    assert coords.parse_structure_selection("0,2,4", 5) == [0, 2, 4]
    assert coords.parse_structure_selection("0 2 4", 5) == [0, 2, 4]
    assert coords.parse_structure_selection("1-3", 5) == [1, 2, 3]


def test_parse_structure_selection_dedup_and_out_of_range():
    assert coords.parse_structure_selection("0,0,1", 5) == [0, 1]          # de-duped
    assert coords.parse_structure_selection("2,9,7", 5) == [2]             # 9,7 dropped
    assert coords.parse_structure_selection("nonsense", 5) == []           # nothing valid


# --------------------------------------------------------------- title -> metadata
def test_meta_from_title_json():
    m = coords._meta_from_title('{"name": "Methanol", "smiles": "CO", "charge": 0}')
    assert m["smiles"] == "CO" and m["name"] == "Methanol" and m["charge"] == 0


def test_meta_from_title_plain_and_empty():
    assert coords._meta_from_title("benzene") == {"name": "benzene"}
    assert coords._meta_from_title("") is None
    assert coords._meta_from_title(None) is None


def test_unreadable_format_fast_rejected(tmp_path):
    # A write-only OpenBabel format (ADF input deck) must be refused, not hang.
    if not _has_chem_backend():
        pytest.skip("OpenBabel not available to know the readable-format list")
    p = _write(tmp_path / "input_file.adf", "dummy content\n")
    with pytest.raises(coords.CoordGenError):
        coords.read_structures(p)
