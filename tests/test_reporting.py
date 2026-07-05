"""Test the gradient report extractor (reads a calc's .engrad from its rundir)."""

import math

from orca_workbench.core import reporting as RP


def _engrad(path, grads):
    lines = ["#", "# Number of atoms", "#", " {}".format(len(grads) // 3),
             "#", "# Energy", "#", "  -1.0", "#", "# Gradient", "#"]
    lines += ["  {:.8f}".format(x) for x in grads]
    lines += ["#", "# coords", "#"] + ["  1  0.0 0.0 0.0"] * (len(grads) // 3)
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def test_gradient_extractor(tmp_path):
    g = [0.01, 0.0, 0.0, -0.03, 0.0, 0.04]   # 2 atoms
    _engrad(str(tmp_path / "m.engrad"), g)
    ctx = RP.CalcContext("id", "lbl", "m", "ENGRAD", "HF", None, str(tmp_path))
    out = RP._x_gradient("", ctx)["gradient"]
    assert out["n_atoms"] == 2
    assert abs(out["max_au"] - 0.04) < 1e-9
    assert abs(out["norm_au"] - math.sqrt(0.0026)) < 1e-9
    assert abs(out["rms_au"] - math.sqrt(0.0026 / 6)) < 1e-9


def test_gradient_extractor_absent(tmp_path):
    ctx = RP.CalcContext("id", "lbl", "m", "NMR", "HF", None, str(tmp_path))
    assert RP._x_gradient("", ctx) is None        # no .engrad -> None, no crash


def test_gradient_registered():
    assert "gradient" in RP.EXTRACTORS_BY_KEY


def _fake_report():
    return {"calculations": [
        {"id": "c1", "label": "OPT a", "molecule": "a", "calctype": "OPT",
         "method": "HF", "terminated_normally": True,
         "properties": {"final_single_point_energy_Eh": -1.23, "dipole_debye": 0.5}},
        {"id": "c2", "label": "NMR b", "molecule": "b", "calctype": "NMR",
         "method": "HF", "terminated_normally": True,
         "properties": {"final_single_point_energy_Eh": -4.56}},  # no dipole
    ]}


def test_csv_catalogue_and_defaults():
    from orca_workbench.core import reporting
    cols = reporting.available_csv_columns()
    keys = [c["key"] for c in cols]
    assert "final_energy_Eh" in keys and "molecule" in keys
    defaults = reporting.default_csv_columns()
    assert all("key" in c and "header" in c for c in defaults)


def test_write_csv_custom_columns_and_missing(tmp_path):
    from orca_workbench.core import reporting
    p = str(tmp_path / "r.csv")
    cols = [{"key": "molecule", "header": "Mol"},
            {"key": "dipole_debye", "header": "mu (D)"},
            {"key": "final_energy_Eh", "header": "E"}]
    reporting.write_csv(_fake_report(), p, columns=cols, missing="NaN")
    lines = open(p, encoding="utf-8").read().splitlines()
    assert lines[0] == "Mol,mu (D),E"                 # custom headers, custom order
    assert lines[1].split(",") == ["a", "0.5", "-1.23"]
    assert lines[2].split(",") == ["b", "NaN", "-4.56"]  # missing dipole -> NaN


def test_write_csv_default_when_no_columns(tmp_path):
    from orca_workbench.core import reporting
    p = str(tmp_path / "r2.csv")
    reporting.write_csv(_fake_report(), p)            # defaults, empty missing
    header = open(p, encoding="utf-8").read().splitlines()[0]
    assert "Molecule" in header and "Final energy (Eh)" in header
    # a missing dipole is a blank cell by default
    row_b = open(p, encoding="utf-8").read().splitlines()[2]
    assert ",," in row_b
