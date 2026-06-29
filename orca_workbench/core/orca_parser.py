"""Parse ORCA output (.out) for live-progress monitoring.

Designed to run repeatedly on a file that's still being written (the SLURM
.out streams to the shared filesystem thanks to the stdbuf wrapper in the
slurm template). Everything is best-effort and tolerant of truncated files.

The most universally useful signal is FINAL SINGLE POINT ENERGY — there's one
per converged SCF, which for a geometry optimisation means one per optimisation
cycle. We also pull RMS/MAX gradient from the geometry-convergence tables and
the per-iteration energies of the most recent SCF block.

Verified against real ORCA 6.0.1 output for four job types: a Zn-BINAP
geometry optimisation (OPT), plus water single-point, water Freq, and PH3 NMR.
SP/FREQ/NMR have a single geometry, so they expose only the SCF block (no opt
cycles or gradients) and the plot shows SCF convergence for those. ORCA 6 SCF
iterations live under D-I-I-S / S-O-S-C-F headers, not "SCF ITERATIONS".
"""

import os
import re
from typing import Dict, List


def read_tail(path, max_bytes=262144):
    # type: (str, int) -> str
    """Read at most the last `max_bytes` of a file as text. Status checks on
    huge ORCA outputs only need the end — the termination/error markers, the
    last SCF block and the latest opt cycle all live there — so this avoids
    reading (and, on a shared filesystem, transferring) tens of MB per file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


_FINAL_E = re.compile(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)")
# Geometry-convergence-table lines only: value, tolerance, then YES/NO. This
# excludes the "Norm of the (Cartesian|Dispersion) gradient ... RMS gradient
# ... <value>" lines (which use a '...' separator), so we get exactly one
# RMS/MAX value per optimisation cycle. Verified on ORCA 6.0.1.
_RMS_GRAD = re.compile(r"RMS gradient\s+(-?\d+\.\d+)\s+-?\d+\.\d+\s+(?:YES|NO)")
_MAX_GRAD = re.compile(r"MAX gradient\s+(-?\d+\.\d+)\s+-?\d+\.\d+\s+(?:YES|NO)")
_TERM_OK = re.compile(r"\*\*\*\*\s*ORCA TERMINATED NORMALLY\s*\*\*\*\*")
_ERROR = re.compile(
    r"(ORCA finished by error|aborting the run|ORCA TERMINATED ABNORMALLY|"
    r"SCF NOT CONVERGED AFTER)",
    re.IGNORECASE,
)
_OPT_CYCLE = re.compile(r"GEOMETRY OPTIMIZATION CYCLE\s+(\d+)")
# An SCF iteration row: leading integer then the energy. Matches both the
# D-I-I-S and the S-O-S-C-F iteration tables that ORCA 6 prints.
_SCF_ITER_LINE = re.compile(r"^\s*(\d+)\s+(-?\d+\.\d+)\b")
# Markers that end the current SCF block, so we don't read past it into the
# energy summary / next section (which also has "<int> <float>"-shaped lines).
_SCF_STOP_MARKERS = (
    "SCF CONVERGED",
    "SCF NOT CONVERGED",
    "TOTAL SCF ENERGY",
    "Energy Check signals convergence",
    "GEOMETRY OPTIMIZATION CYCLE",
    "ORCA TERMINATED",
)


def parse_orca_output(text):
    # type: (str) -> Dict
    """Return a dict of progress signals. All lists are in file order."""
    final_energies = [float(x) for x in _FINAL_E.findall(text)]
    rms_grads = [float(x) for x in _RMS_GRAD.findall(text)]
    max_grads = [float(x) for x in _MAX_GRAD.findall(text)]
    opt_cycles = [int(x) for x in _OPT_CYCLE.findall(text)]
    return {
        "final_energies": final_energies,
        "rms_grads": rms_grads,
        "max_grads": max_grads,
        # Use the highest printed cycle number, not the count, so status stays
        # correct even when only the tail of a huge .out is parsed (see the
        # tail-read in the Calculations tab). Equal to the count for a full file.
        "n_opt_cycles": max(opt_cycles) if opt_cycles else 0,
        "scf_iterations": _parse_last_scf_block(text),
        "terminated_normally": bool(_TERM_OK.search(text)),
        "has_error": bool(_ERROR.search(text)),
    }


def _parse_last_scf_block(text):
    # type: (str) -> List[float]
    """Pull per-iteration energies from the most recent SCF block.

    ORCA 6 starts an SCF with a D-I-I-S table that may hand over to an
    S-O-S-C-F table (iteration numbers continue across the handover). We anchor
    on the last D-I-I-S header (falling back to S-O-S-C-F for pure-SOSCF runs)
    and collect every iteration row until a hard stop marker — NOT stopping on
    the warnings, separators, and sub-headers ORCA interleaves between rows.
    """
    anchor = text.rfind("D-I-I-S")
    if anchor == -1:
        anchor = text.rfind("S-O-S-C-F")
    if anchor == -1:
        return []
    energies = []
    for line in text[anchor:].splitlines():
        if any(marker in line for marker in _SCF_STOP_MARKERS):
            break
        m = _SCF_ITER_LINE.match(line)
        if m:
            energies.append(float(m.group(2)))
    return energies


def short_status(parsed):
    # type: (Dict) -> str
    """One-line human summary from a parsed dict."""
    if parsed.get("has_error"):
        return "error detected in output"
    if parsed.get("terminated_normally"):
        return "terminated normally"
    n = parsed.get("n_opt_cycles", 0)
    if n:
        return "running — opt cycle {}".format(n)
    fe = parsed.get("final_energies") or []
    if fe:
        return "running — {} SCF(s) done".format(len(fe))
    si = parsed.get("scf_iterations") or []
    if si:
        return "running — SCF iter {}".format(len(si))
    return "running / starting up"


# ---------------------------------------------------------------------------
# Report-property parsers. All verified against real ORCA 6.0.1 output
# (water B3LYP/def2-SVP Opt+Freq, PH3 TPSS/pcSseg-2 NMR). Each is best-effort
# and returns None / [] when the property isn't present in the file.
# ---------------------------------------------------------------------------

# "     6:    1637.61 cm**-1"  (negative value => imaginary mode)
_FREQ_LINE = re.compile(r"^\s*(\d+):\s+(-?\d+\.\d+)\s+cm\*\*-1")
# IR row: "  6:   1637.61   0.010942   55.30  0.002085 (...)"
# columns after "mode:" are  freq   eps   Int(km/mol)   T**2  ...
# so the IR intensity we want is the THIRD numeric column (group 4 here).
_IR_LINE = re.compile(r"^\s*(\d+):\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)")
# "      0       P          584.857         32.122"  (NMR shielding summary row)
_NMR_ROW = re.compile(r"^\s*(\d+)\s+([A-Z][a-z]?)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$")
# Orbital-energy row: "   4   2.0000      -0.288146        -7.8409"
_ORB_ROW = re.compile(r"^\s*\d+\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$")


def _grab_float(text, label):
    # type: (str, str) -> Optional[float]
    """Find 'Label ... <float> Eh' style ORCA summary lines (the '...'
    separator is what ORCA uses for its key=value summaries)."""
    m = re.search(re.escape(label) + r"\s*\.\.\.\s*(-?\d+\.\d+)", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def parse_frequencies(text):
    # type: (str) -> List[float]
    """All vibrational frequencies (cm^-1) from the VIBRATIONAL FREQUENCIES
    block, in order, including the leading zeros (translations/rotations) and
    any negative (imaginary) modes. Returns [] if no Freq run."""
    i = text.rfind("VIBRATIONAL FREQUENCIES")
    if i == -1:
        return []
    freqs = []
    for line in text[i:].splitlines()[1:]:
        m = _FREQ_LINE.match(line)
        if m:
            freqs.append(float(m.group(2)))
        elif freqs and line.strip() and "NORMAL MODES" in line:
            break
    return freqs


def real_frequencies(freqs):
    # type: (List[float]) -> List[float]
    """Just the genuine vibrational modes: drop the (near-)zero trans/rot
    modes that ORCA lists as 0.00."""
    return [f for f in freqs if abs(f) > 1.0]


def parse_ir(text):
    # type: (str) -> List[dict]
    """IR spectrum rows: list of {mode, freq_cm, intensity_km_mol}. [] if none."""
    i = text.rfind("IR SPECTRUM")
    if i == -1:
        return []
    rows = []
    started = False
    for line in text[i:].splitlines()[1:]:
        m = _IR_LINE.match(line)
        if m:
            started = True
            rows.append({"mode": int(m.group(1)),
                         "freq_cm": float(m.group(2)),
                         "intensity_km_mol": float(m.group(4))})
        elif started and line.strip().startswith("*"):
            break
    return rows


def parse_thermochemistry(text):
    # type: (str) -> Optional[dict]
    """Thermochemistry summary (all in Hartree) from a Freq run, or None."""
    if "THERMOCHEMISTRY AT" not in text:
        return None
    out = {
        "zero_point_energy": _grab_float(text, "Zero point energy"),
        "total_thermal_energy": _grab_float(text, "Total thermal energy"),
        "total_enthalpy": _grab_float(text, "Total Enthalpy"),
        "final_entropy_term": _grab_float(text, "Final entropy term"),
        "final_gibbs_free_energy": _grab_float(text, "Final Gibbs free energy"),
        "gibbs_minus_electronic": _grab_float(text, "G-E(el)"),
    }
    m = re.search(r"THERMOCHEMISTRY AT\s+([\d.]+)\s*K", text)
    if m:
        out["temperature_K"] = float(m.group(1))
    return out


def parse_nmr_shieldings(text):
    # type: (str) -> List[dict]
    """NMR isotropic + anisotropy shieldings (ppm) per nucleus, or []."""
    i = text.rfind("CHEMICAL SHIELDING SUMMARY")
    if i == -1:
        return []
    rows = []
    started = False
    for line in text[i:].splitlines():
        m = _NMR_ROW.match(line)
        if m:
            started = True
            rows.append({"index": int(m.group(1)),
                         "element": m.group(2),
                         "isotropic_ppm": float(m.group(3)),
                         "anisotropy_ppm": float(m.group(4))})
        elif started and line.strip() == "":
            # a blank line after rows have started ends the table
            if rows:
                break
    return rows


def parse_dipole_debye(text):
    # type: (str) -> Optional[float]
    """Total dipole moment magnitude in Debye, or None."""
    m = re.search(r"Magnitude \(Debye\)\s*:\s*(\d+\.\d+)", text)
    return float(m.group(1)) if m else None


def parse_homo_lumo(text):
    # type: (str) -> Optional[dict]
    """HOMO, LUMO and gap (eV) from the last ORBITAL ENERGIES block. Handles
    the common closed-shell case; returns None if it can't be determined."""
    i = text.rfind("ORBITAL ENERGIES")
    if i == -1:
        return None
    occ_e = []  # (occ, E_eV)
    started = False
    for line in text[i:].splitlines():
        m = _ORB_ROW.match(line)
        if m:
            started = True
            occ = float(m.group(1))
            e_ev = float(m.group(3))
            occ_e.append((occ, e_ev))
        elif started and occ_e and (line.strip() == "" or not line.strip()[0:1].isdigit()):
            # stop at the first non-data line once rows have begun, but allow
            # the spin-down header to NOT appear for closed shell
            if line.strip() and "SPIN" not in line.upper():
                break
    if not occ_e:
        return None
    occupied = [e for occ, e in occ_e if occ > 0.5]
    virtual = [e for occ, e in occ_e if occ <= 0.5]
    if not occupied or not virtual:
        return None
    homo = max(occupied)
    lumo = min(virtual)
    return {"homo_eV": homo, "lumo_eV": lumo, "gap_eV": lumo - homo}


_ABS_HEADER = "ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS"
_ABS_FLOAT = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")
_CM_PER_EV = 8065.543937


def parse_absorption_spectrum(text):
    # type: (str) -> List[dict]
    """Electronic absorption spectrum from a TD-DFT .out: a list of
    {state, energy_eV, energy_cm, wavelength_nm, fosc}, in file order.

    Robust across ORCA 5/6 column layouts (ORCA 6 adds an energy-in-eV column
    before the cm-1 one). We anchor on the row's first float > 1000 — that's the
    excitation energy in cm-1 (eV values are < ~20, state indices are integers
    with no decimal point and so aren't matched) — then take the next two floats
    as wavelength (nm) and oscillator strength. Returns [] if there's no TD-DFT
    absorption block. NOTE: verify against a real ORCA 6 TD-DFT .out on the gateway.
    """
    i = text.rfind(_ABS_HEADER)
    if i == -1:
        return []
    states = []
    started = False
    for line in text[i:].splitlines()[1:]:
        s = line.strip()
        if not s:
            if started:
                break          # blank line ends the table once rows have begun
            continue
        if set(s) <= set("-"):
            continue           # dashed separator
        vals = [float(x) for x in _ABS_FLOAT.findall(line)]
        big = next((k for k, v in enumerate(vals) if v > 1000.0), None)
        if big is None or big + 2 >= len(vals):
            if started:
                break
            continue
        cm = vals[big]
        nm = vals[big + 1]
        fosc = vals[big + 2]
        if nm <= 0:
            continue
        started = True
        states.append({"state": len(states) + 1,
                       "energy_cm": cm,
                       "energy_eV": cm / _CM_PER_EV,
                       "wavelength_nm": nm,
                       "fosc": fosc})
    return states


def count_xyz_frames(path):
    # type: (str) -> int
    """Number of frames in a multi-frame .xyz (e.g. an ORCA _trj.xyz). 0 on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            frames = 0
            while True:
                header = f.readline()
                if not header:
                    break
                header = header.strip()
                if not header:
                    continue
                try:
                    n = int(header)
                except ValueError:
                    continue
                frames += 1
                f.readline()  # comment line
                for _ in range(n):
                    f.readline()
            return frames
    except IOError:
        return 0
