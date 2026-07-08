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


# --------------------------------------- parse xyz from text (clipboard paste)
def test_parse_xyz_frames_text_single():
    frames = coords.parse_xyz_frames_text(SINGLE_XYZ)
    assert len(frames) == 1
    atoms, _meta = frames[0]
    assert [a[0] for a in atoms] == ["H", "O"]


def test_parse_xyz_frames_text_multiframe():
    frames = coords.parse_xyz_frames_text(MULTI_XYZ)
    assert len(frames) == 3
    assert [f[0][0][3] for f in frames] == [0.0, 1.0, 2.0]


def test_parse_xyz_frames_text_metadata():
    text = '1\n{"name": "H atom", "charge": 0}\nH 0.0 0.0 0.0\n'
    frames = coords.parse_xyz_frames_text(text)
    assert len(frames) == 1
    _atoms, meta = frames[0]
    assert meta is not None and meta["name"] == "H atom"


def test_parse_xyz_frames_text_rejects_smiles_list():
    # A pasted SMILES list must NOT be mistaken for XYZ: its first line isn't a
    # bare atom count, so parsing yields no usable (atom-bearing) frames.
    for smiles_blob in ("CCO\nc1ccccc1\nCC(=O)O",
                        "O ethanol\nCCO water",
                        "single-name-line"):
        frames = coords.parse_xyz_frames_text(smiles_blob)
        assert not any(atoms for atoms, _m in frames), smiles_blob


def test_parse_xyz_frames_text_numeric_first_line_but_no_coords():
    # First line is an int, but the following lines aren't coordinates (a name
    # column that happens to be a number) -> a frame with zero atoms, rejected.
    frames = coords.parse_xyz_frames_text("123\nethanol foo\nbenzene bar")
    assert not any(atoms for atoms, _m in frames)


def test_parse_xyz_frames_text_empty():
    assert coords.parse_xyz_frames_text("") == []
    assert coords.parse_xyz_frames_text("   \n  \n") == []


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


def test_meta_from_title_drops_paths_and_filenames():
    # OpenBabel often stuffs the input file path/name into the title — not a name.
    assert coords._meta_from_title("C:/Users/x/input_file.hin") is None
    assert coords._meta_from_title("/home/x/mol.gzmat") is None
    assert coords._meta_from_title("input_file.hin", "/some/dir/input_file.hin") is None
    assert coords._meta_from_title("input_file", "/d/input_file.hin") is None
    # a genuine name (no separators, not the filename) is still kept
    assert coords._meta_from_title("Aspirin", "/d/input_file.hin") == {"name": "Aspirin"}


def test_meta_from_title_truncated_json_dropped():
    # A length-limited record (PDBQT REMARK) truncates JSON metadata — not a name.
    assert coords._meta_from_title('{"name":') is None
    assert coords._meta_from_title('{"name": "x"') is None        # unterminated


def test_no_fabricated_smiles_for_titleless_format(tmp_path):
    # A geometry file with no SMILES in its title must NOT get a perceived/guessed
    # SMILES (OpenBabel's 3D bond-order perception is unreliable) — better blank.
    if coords._openbabel_informats() is None:
        pytest.skip("OpenBabel not available")
    structs = coords.read_structures(_write(tmp_path / "two.sdf", SDF_TWO_RECORDS))
    for _atoms, meta in structs:
        assert not (meta or {}).get("smiles")


def test_unreadable_format_fast_rejected(tmp_path):
    # A write-only OpenBabel format (ADF input deck) must be refused, not hang.
    if not _has_chem_backend():
        pytest.skip("OpenBabel not available to know the readable-format list")
    p = _write(tmp_path / "input_file.adf", "dummy content\n")
    with pytest.raises(coords.CoordGenError):
        coords.read_structures(p)


def _has_rdkit():
    try:
        import rdkit  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------- SMILES lists
def test_is_smiles_list_file():
    assert coords.is_smiles_list_file("a.smi")
    assert coords.is_smiles_list_file("A.SMILES")
    assert coords.is_smiles_list_file("set.csv")
    assert not coords.is_smiles_list_file("a.xyz")


def test_dialog_filetypes_includes_smiles_lists():
    ft = coords.import_dialog_filetypes()
    assert ft[0][0] == "Coordinate files"           # still first
    assert ft[-1] == ("All files", "*.*")           # still last
    assert any("*.smi" in pat and "*.csv" in pat for _label, pat in ft)


def test_read_smiles_file_one_per_line(tmp_path):
    p = _write(tmp_path / "list.smi", "CCO\nc1ccccc1\n# a comment\n")
    pairs = coords.read_smiles_file(p)
    assert ("CCO", None) in pairs and ("c1ccccc1", None) in pairs


def test_read_smiles_file_csv_with_header_and_name(tmp_path):
    # Header-row skipping + column auto-detection need RDKit to judge validity.
    if not _has_rdkit():
        pytest.skip("RDKit needed to detect the SMILES column / skip header")
    p = _write(tmp_path / "set.csv", "smiles,name\nCCO,ethanol\nCCC,propane\n")
    pairs = coords.read_smiles_file(p)
    assert pairs == [("CCO", "ethanol"), ("CCC", "propane")]


def test_read_smiles_file_reversed_columns(tmp_path):
    if not _has_rdkit():
        pytest.skip("RDKit needed to detect which column is the SMILES")
    p = _write(tmp_path / "set.csv", "name,smiles\nethanol,CCO\npropane,CCC\n")
    pairs = coords.read_smiles_file(p)
    assert pairs == [("CCO", "ethanol"), ("CCC", "propane")]


def test_read_smiles_file_empty_raises(tmp_path):
    with pytest.raises(coords.CoordGenError):
        coords.read_smiles_file(_write(tmp_path / "empty.smi", "\n\n"))


# --------------------------------------------------------------- heuristic salvage
ACESIN_LIKE = """\
*ACES2(BASIS=PVDZ)
some header line that is not coordinates
O   0.000000   0.000000   0.117300
H   0.000000   0.757200  -0.469200
H   0.000000  -0.757200  -0.469200

*END
"""

# A numeric table (gradient components) must NOT be mistaken for coordinates.
GRADIENT_TABLE = """\
gradient
1   0.001234   -0.004567   0.008910
2  -0.002345    0.005678  -0.009012
"""


def test_heuristic_extracts_xyz_block():
    atoms = coords.heuristic_atoms_from_text(ACESIN_LIKE)
    assert [a[0] for a in atoms] == ["O", "H", "H"]
    assert atoms[1][2] == 0.7572


def test_heuristic_rejects_numeric_table():
    assert coords.heuristic_atoms_from_text(GRADIENT_TABLE) == []


def test_heuristic_handles_fortran_d_exponent():
    text = "C  1.0D-01  2.0d+00  -3.0D-01\nH  0.5  0.5  0.5\n"
    atoms = coords.heuristic_atoms_from_text(text)
    assert atoms[0][1] == pytest.approx(0.1)


def test_read_structures_heuristic_fallback(tmp_path):
    # A write-only/input-deck extension with an embedded xyz block is salvaged.
    p = _write(tmp_path / "mol.acesin", ACESIN_LIKE)
    structs = coords.read_structures(p)
    assert len(structs) == 1
    atoms, meta = structs[0]
    assert [a[0] for a in atoms] == ["O", "H", "H"]
    assert meta.get("source") == "heuristic"


def test_write_structure_file_native_xyz(tmp_path):
    from orca_workbench.core.coords import write_structure_file, _read_xyz_frames
    atoms = [("O", 0.0, 0.0, 0.0), ("H", 0.96, 0.0, 0.0)]
    p = str(tmp_path / "w.xyz")
    assert write_structure_file(p, atoms, "water-ish") == "native"
    frames = _read_xyz_frames(p)
    assert len(frames) == 1 and len(frames[0][0]) == 2


def test_write_structure_file_converted_or_clear_error(tmp_path):
    # .mol goes through OpenBabel/RDKit when installed; otherwise the error
    # must NAME the missing backends (never fail silently).
    from orca_workbench.core.coords import write_structure_file
    import pytest as _pytest
    atoms = [("O", 0.0, 0.0, 0.0), ("H", 0.96, 0.0, 0.0)]
    p = str(tmp_path / "w.mol")
    try:
        backend = write_structure_file(p, atoms, "w")
    except ValueError as e:
        assert "OpenBabel" in str(e) or "RDKit" in str(e)
        return
    assert backend in ("openbabel", "rdkit")
    import os as _os
    assert _os.path.getsize(p) > 0


# ----------------------------------------------- write_structures_file (multi)
_STRUCTS = [
    ([("O", 0.0, 0.0, 0.0), ("H", 0.9, 0.0, 0.0), ("H", -0.2, 0.9, 0.0)], "f0"),
    ([("O", 0.0, 0.0, 1.0), ("H", 0.9, 0.0, 1.0), ("H", -0.2, 0.9, 1.0)], "f1"),
]


def test_write_structures_xyz_trajectory_roundtrips(tmp_path):
    p = str(tmp_path / "traj.xyz")
    assert coords.write_structures_file(p, _STRUCTS) == "native"
    frames = coords.parse_xyz_frames_text(open(p).read())
    assert len(frames) == 2
    # second frame's O sits at z=1.0 (frames written in order, geometry preserved)
    assert frames[1][0][0][3] == pytest.approx(1.0, abs=1e-6)
    assert [a[0] for a in frames[0][0]] == ["O", "H", "H"]


def test_write_structures_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        coords.write_structures_file(str(tmp_path / "x.xyz"), [])


def test_multi_structure_formats_list():
    assert coords.MULTI_STRUCTURE_FORMATS[0] == "xyz"
    assert "sdf" in coords.MULTI_STRUCTURE_FORMATS
    assert "mol" not in coords.MULTI_STRUCTURE_FORMATS      # single-structure only


@pytest.mark.skipif(not _has_chem_backend(), reason="no OpenBabel/RDKit")
def test_write_structures_sdf_has_two_records(tmp_path):
    p = str(tmp_path / "coll.sdf")
    backend = coords.write_structures_file(p, _STRUCTS)
    assert backend in ("openbabel", "rdkit")
    assert open(p).read().count("$$$$") == 2               # two SDF records
