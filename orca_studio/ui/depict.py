"""Render a 2D skeletal-formula depiction of a SMILES as a Tk image.

Uses RDKit's Cairo 2D drawer to produce a PNG, then loads it into a
tk.PhotoImage via base64 (Tk 8.6+ understands PNG natively — no Pillow needed).
Conda-forge's RDKit ships the cairo drawer; if it's somehow missing we fall
back to an error string the caller can show instead of the image.
"""

import base64
import tkinter as tk
from typing import Optional, Tuple

# Importing coords as a side effect silences RDKit's/OpenBabel's noisy stderr
# parse-error logging (see coords._silence_chem_loggers), so typing a partial
# SMILES while the depiction updates live doesn't spam the terminal.
from orca_studio.core import coords as _coords  # noqa: F401


def render_smiles_png(smiles, size=(360, 240)):
    # type: (str, Tuple[int, int]) -> Tuple[Optional[bytes], Optional[str]]
    """Return (png_bytes, None) on success, (None, error_message) on failure."""
    if not smiles or not smiles.strip():
        return None, "(no SMILES)"
    try:
        from rdkit import Chem
        from rdkit.Chem import rdDepictor
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError as e:
        return None, "RDKit not available ({})".format(e)
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None, "(SMILES not valid)"
        rdDepictor.Compute2DCoords(mol)
        drawer = rdMolDraw2D.MolDraw2DCairo(size[0], size[1])
        opts = drawer.drawOptions()
        try:
            opts.padding = 0.08
        except Exception:
            pass
        drawer.DrawMolecule(mol)
        drawer.FinishDrawing()
        return drawer.GetDrawingText(), None
    except Exception as e:
        return None, "2D depiction unavailable ({}: {})".format(type(e).__name__, e)


def smiles_to_photoimage(smiles, size=(360, 240), master=None):
    # type: (str, Tuple[int, int], Optional[tk.Misc]) -> Tuple[Optional[tk.PhotoImage], Optional[str]]
    """Return (PhotoImage, None) or (None, error_message). Keep a reference to
    the returned image or Tk will garbage-collect it and show nothing.

    Pass `master` (any widget) so the image binds to the right Tk interpreter —
    important if more than one Tk root ever exists."""
    png, err = render_smiles_png(smiles, size)
    if png is None:
        return None, err
    try:
        data = base64.b64encode(png)
        if master is not None:
            return tk.PhotoImage(master=master, data=data), None
        return tk.PhotoImage(data=data), None
    except Exception as e:
        return None, "Tk could not load PNG ({}: {})".format(type(e).__name__, e)
