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
