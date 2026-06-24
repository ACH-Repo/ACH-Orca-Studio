"""Subprocess worker: read a structure file with OpenBabel and emit JSON.

Run as a short-lived child process so a hanging or crashing OpenBabel reader
(some formats loop forever on certain inputs — e.g. .bgf/.box on round-tripped
files) can be killed by the parent's timeout instead of freezing the GUI. A
worker *thread* wouldn't do: OpenBabel's SWIG binding holds the GIL during the
read, so a hung read blocks the whole interpreter, not just one thread.

Not imported by the app at runtime; invoked as
    python -m orca_workbench.core._obabel_worker <fmt> <path>
It prints exactly one JSON object on stdout:
    {"ok": true, "structures": [{"atoms": [[sym, x, y, z], ...], "name": str|null}, ...]}
    {"ok": false, "error": "..."}
"""

import json
import sys


def main(argv):
    if len(argv) < 2:
        sys.stdout.write(json.dumps({"ok": False, "error": "usage: <fmt> <path>"}))
        return 2
    fmt, path = argv[0], argv[1]
    try:
        from orca_workbench.core import coords
        coords._ensure_loggers_silenced()
        sym = coords._atomic_number_to_symbol
        try:
            from openbabel import pybel
        except ImportError:
            import pybel  # type: ignore
    except Exception as e:
        sys.stdout.write(json.dumps({"ok": False,
                                     "error": "OpenBabel not available: {}".format(e)}))
        return 1

    if fmt not in pybel.informats:
        sys.stdout.write(json.dumps({
            "ok": False,
            "error": "'{}' is not a readable OpenBabel format (it may be "
                     "write-only).".format(fmt)}))
        return 0

    try:
        structs = []
        for mol in pybel.readfile(fmt, path):
            atoms = [[sym(a.atomicnum), float(a.coords[0]), float(a.coords[1]),
                      float(a.coords[2])] for a in mol.atoms]
            title = (getattr(mol, "title", "") or "").strip()
            # Perceive a SMILES from the 3D geometry (OpenBabel already did bond
            # perception on read). This recovers the SMILES for formats that don't
            # round-trip a usable title (PDB/HIN/GZMAT substitute the filename;
            # PDBQT truncates it). It's geometry-derived, so it fills the field
            # only when the title didn't already carry one (the parent decides).
            try:
                smi = mol.write("smi").split("\t")[0].strip() or None
            except Exception:
                smi = None
            structs.append({"atoms": atoms, "name": title or None, "smiles": smi})
        sys.stdout.write(json.dumps({"ok": True, "structures": structs}))
        return 0
    except Exception as e:
        sys.stdout.write(json.dumps({"ok": False,
                                     "error": "{}: {}".format(type(e).__name__, e)}))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
