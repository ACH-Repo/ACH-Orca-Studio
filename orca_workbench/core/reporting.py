"""Extract results from finished ORCA calculations into a report (JSON + CSV).

An "extractor" pulls one family of properties from a calc's .out (and, for
geometry/trajectory, sibling files in its run dir). Extractors are declared in
EXTRACTORS so the UI can present them as checkboxes; each has a key, label,
and a function (out_text, ctx) -> dict-fragment (or None if not applicable).

assemble_report() runs the chosen extractors over a list of calc contexts and
returns a nested dict. write_json/write_csv serialise it — JSON keeps the full
nested structure; CSV is a flat one-row-per-calc summary of the scalar values
(energies, n_imaginary, dipole, gap, key shieldings) for quick spreadsheeting.

This module is pure (no Tkinter), so it's safe to unit-test and could run off
the main thread if ever needed.
"""

import csv
import datetime
import json
import os
from typing import Callable, Dict, List, Optional

from orca_workbench.core import orca_parser as P


class CalcContext(object):
    """Everything an extractor needs about one finished calculation, decoupled
    from the GUI's PlannedCalc so reporting stays pure/testable."""

    def __init__(self, calc_id, label, molecule, calctype, method, out_path, rundir_abs):
        # type: (str, str, str, str, str, Optional[str], Optional[str]) -> None
        self.calc_id = calc_id
        self.label = label          # human label, e.g. "OPT 000"
        self.molecule = molecule    # molecule filename / id
        self.calctype = calctype    # OPT / FREQ / NMR / ...
        self.method = method
        self.out_path = out_path    # absolute path to the .out (may be None)
        self.rundir_abs = rundir_abs


# ---- individual extractors: (out_text, ctx) -> fragment dict or None --------

def _x_final_energy(text, ctx):
    fe = P._FINAL_E.findall(text)
    if not fe:
        return None
    return {"final_single_point_energy_Eh": float(fe[-1]),
            "n_energy_points": len(fe)}


def _x_converged_geometry(text, ctx):
    # The optimised geometry ORCA writes to <base>.xyz in the run dir.
    if not ctx.rundir_abs:
        return None
    from orca_workbench.core import coords as coords_mod
    xyz = os.path.join(ctx.rundir_abs, ctx.molecule + ".xyz")
    if not os.path.isfile(xyz):
        return None
    try:
        atoms, _ = coords_mod.read_xyz(xyz)
    except Exception:
        return None
    return {"converged_geometry": {
        "n_atoms": len(atoms),
        "xyz_path": xyz,
        "atoms": [{"element": a[0], "x": a[1], "y": a[2], "z": a[3]} for a in atoms],
    }}


def _x_trajectory(text, ctx):
    # The optimisation trajectory ORCA writes to <base>_trj.xyz — a multi-frame
    # .xyz that PyMOL/VMD load as a movie. We record its path + frame count
    # rather than inlining (it can be large).
    if not ctx.rundir_abs:
        return None
    trj = os.path.join(ctx.rundir_abs, ctx.molecule + "_trj.xyz")
    if not os.path.isfile(trj):
        return None
    return {"trajectory": {"trj_path": trj, "n_frames": P.count_xyz_frames(trj)}}


def _x_frequencies(text, ctx):
    freqs = P.parse_frequencies(text)
    if not freqs:
        return None
    real = P.real_frequencies(freqs)
    ir = P.parse_ir(text)
    return {"frequencies": {
        "all_cm": freqs,
        "vibrational_cm": real,
        "n_imaginary": sum(1 for f in real if f < 0),
        "imaginary_cm": [f for f in real if f < 0],
        "ir_spectrum": ir,
    }}


def _x_thermochemistry(text, ctx):
    th = P.parse_thermochemistry(text)
    return {"thermochemistry": th} if th else None


def _x_nmr(text, ctx):
    sh = P.parse_nmr_shieldings(text)
    return {"nmr_shieldings": sh} if sh else None


def _x_dipole(text, ctx):
    d = P.parse_dipole_debye(text)
    return {"dipole_debye": d} if d is not None else None


def _x_homo_lumo(text, ctx):
    hl = P.parse_homo_lumo(text)
    return {"homo_lumo": hl} if hl else None


def _x_gradient(text, ctx):
    """Max / RMS / norm of the Cartesian gradient from the <molecule>.engrad file
    an EnGrad (or Opt) job writes next to the .out. A small but telling number: a
    geometry that 'finished' but still has a large max gradient isn't converged.
    Returns None when there's no .engrad."""
    if not ctx.rundir_abs:
        return None
    path = os.path.join(ctx.rundir_abs, ctx.molecule + ".engrad")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            rows = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        natoms = int(rows[0])
        g = [float(x) for x in rows[2:2 + 3 * natoms]]
    except (OSError, ValueError, IndexError):
        return None
    if not g or len(g) != 3 * natoms:
        return None
    import math
    norm = math.sqrt(sum(x * x for x in g))
    return {"gradient": {
        "max_au": max(abs(x) for x in g),
        "rms_au": norm / math.sqrt(len(g)),
        "norm_au": norm,
        "n_atoms": natoms,
    }}


class Extractor(object):
    def __init__(self, key, label, func, applies_hint=""):
        # type: (str, str, Callable, str) -> None
        self.key = key
        self.label = label
        self.func = func
        self.applies_hint = applies_hint  # which calc types it's typically for


EXTRACTORS = [
    Extractor("final_energy", "Final energy", _x_final_energy, "any"),
    Extractor("gradient", "Cartesian gradient (max/RMS)", _x_gradient, "ENGRAD"),
    Extractor("converged_geometry", "Converged geometry (xyz)", _x_converged_geometry, "OPT"),
    Extractor("trajectory", "Optimization trajectory (for PyMOL)", _x_trajectory, "OPT"),
    Extractor("frequencies", "Frequencies + IR", _x_frequencies, "FREQ"),
    Extractor("thermochemistry", "Thermochemistry (G, H, S, ZPE)", _x_thermochemistry, "FREQ"),
    Extractor("nmr", "NMR shieldings", _x_nmr, "NMR"),
    Extractor("dipole", "Dipole moment", _x_dipole, "any"),
    Extractor("homo_lumo", "HOMO–LUMO gap", _x_homo_lumo, "any"),
]

EXTRACTORS_BY_KEY = {e.key: e for e in EXTRACTORS}


def assemble_report(contexts, extractor_keys, progress=None):
    # type: (List[CalcContext], List[str], Optional[Callable]) -> dict
    """Run the chosen extractors over each context. `progress`, if given, is
    called as progress(i, n, label) before each calc (for a progress dialog).
    Returns a dict ready for write_json."""
    chosen = [EXTRACTORS_BY_KEY[k] for k in extractor_keys if k in EXTRACTORS_BY_KEY]
    entries = []
    n = len(contexts)
    for i, ctx in enumerate(contexts):
        if progress is not None:
            progress(i, n, ctx.label)
        entry = {
            "id": ctx.calc_id,
            "label": ctx.label,
            "molecule": ctx.molecule,
            "calctype": ctx.calctype,
            "method": ctx.method,
        }
        text = ""
        if ctx.out_path and os.path.isfile(ctx.out_path):
            try:
                with open(ctx.out_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except IOError:
                text = ""
        entry["terminated_normally"] = bool(P._TERM_OK.search(text)) if text else False
        properties = {}
        for ex in chosen:
            try:
                frag = ex.func(text, ctx)
            except Exception as e:
                frag = {"_error_" + ex.key: "{}: {}".format(type(e).__name__, e)}
            if frag:
                properties.update(frag)
        entry["properties"] = properties
        entries.append(entry)
    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "extractors": list(extractor_keys),
        "n_calculations": len(entries),
        "calculations": entries,
    }


def write_json(report, path):
    # type: (dict, str) -> None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


# Columns for the flat CSV summary (scalars only — lists/geometries stay in JSON).
def _csv_row(entry):
    p = entry.get("properties", {})
    freq = p.get("frequencies") or {}
    thermo = p.get("thermochemistry") or {}
    hl = p.get("homo_lumo") or {}
    nmr = p.get("nmr_shieldings") or []
    row = {
        "id": entry.get("id"),
        "label": entry.get("label"),
        "molecule": entry.get("molecule"),
        "calctype": entry.get("calctype"),
        "method": entry.get("method"),
        "terminated_normally": entry.get("terminated_normally"),
        "final_energy_Eh": p.get("final_single_point_energy_Eh"),
        "dipole_debye": p.get("dipole_debye"),
        "homo_lumo_gap_eV": hl.get("gap_eV"),
        "n_imaginary": freq.get("n_imaginary"),
        "lowest_freq_cm": (min(freq.get("vibrational_cm")) if freq.get("vibrational_cm") else None),
        "gibbs_free_energy_Eh": thermo.get("final_gibbs_free_energy"),
        "enthalpy_Eh": thermo.get("total_enthalpy"),
        "zpe_Eh": thermo.get("zero_point_energy"),
        "n_nmr_nuclei": (len(nmr) if nmr else None),
    }
    return row


_CSV_FIELDS = ["id", "label", "molecule", "calctype", "method", "terminated_normally",
               "final_energy_Eh", "gibbs_free_energy_Eh", "enthalpy_Eh", "zpe_Eh",
               "n_imaginary", "lowest_freq_cm", "dipole_debye", "homo_lumo_gap_eV",
               "n_nmr_nuclei"]


def write_csv(report, path):
    # type: (dict, str) -> None
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for entry in report.get("calculations", []):
            w.writerow(_csv_row(entry))
