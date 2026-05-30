"""Generate 3D coordinates from SMILES.

Tries RDKit (ETKDGv3 + MMFF) first, falls back to OpenBabel/Pybel (UFF) if
RDKit can't embed the molecule. Both libraries are installed via pip --user
on Lido. Returns coordinates as a list of (symbol, x, y, z) tuples plus the
name of the method that succeeded.
"""

import json
import os
import re
from typing import List, Optional, Tuple


Atom = Tuple[str, float, float, float]


def _silence_chem_loggers():
    """Both RDKit and OpenBabel print loud parse errors to stderr by default.
    Our auto-detect probes routinely feed them non-SMILES strings (column
    names like 'ethanol', 'benzene') to test validity, and Generate XYZ
    failures get reported cleanly through the UI's failed-status preview —
    no need for the C-level shouting. Silence both at import time."""
    try:
        from rdkit import RDLogger
        RDLogger.DisableLog("rdApp.*")
    except ImportError:
        pass
    except Exception:
        pass
    try:
        try:
            from openbabel import openbabel as _ob
        except ImportError:
            import openbabel as _ob  # type: ignore
        # StopLogging() actually silences output; SetOutputLevel only filters severity.
        try:
            _ob.obErrorLog.StopLogging()
        except AttributeError:
            _ob.obErrorLog.SetOutputLevel(0)
    except ImportError:
        pass
    except Exception:
        pass


_silence_chem_loggers()


class CoordGenError(Exception):
    """Raised when neither RDKit nor Pybel can generate coordinates."""


def smiles_to_xyz(smiles, prefer_rdkit_only=False):
    # type: (str, bool) -> Tuple[List[Atom], str]
    """Generate 3D coords for a SMILES string.

    Returns (atoms, method) where method is "rdkit" or "pybel". Raises
    CoordGenError with a detailed message — including BOTH backends' errors —
    if generation fails.

    If prefer_rdkit_only=True, OpenBabel is not attempted (its embedded
    geometries for ringed systems are unreliable). Set this for the metal-swap
    gen_smiles workflow where we need RDKit's correctness.
    """
    rdkit_atoms, rdkit_err = _try_rdkit(smiles)
    if rdkit_atoms is not None:
        return rdkit_atoms, "rdkit"

    if prefer_rdkit_only:
        raise CoordGenError(
            "RDKit could not generate coordinates (RDKit-only mode):\n  {}".format(rdkit_err or "unknown error")
        )

    pybel_atoms, pybel_err = _try_pybel(smiles)
    if pybel_atoms is not None:
        return pybel_atoms, "pybel"

    raise CoordGenError(
        "Both backends failed for SMILES {!r}:\n"
        "  RDKit:    {}\n"
        "  OpenBabel: {}".format(
            smiles,
            rdkit_err or "unknown error",
            pybel_err or "unknown error",
        )
    )


def smiles_charge_and_mult(smiles):
    # type: (str) -> Tuple[Optional[int], Optional[int]]
    """Compute (net_charge, spin_multiplicity) from a SMILES via RDKit.

    Charge = sum of formal charges on all atoms (so [NH4+] -> +1, [O-] -> -1).
    Multiplicity = (number of unpaired electrons) + 1, i.e. 2S+1. RDKit infers
    radical electrons from explicit markers in the SMILES like [CH3] or [O][O].
    For most closed-shell organic SMILES this returns (0, 1).

    Returns (None, None) if RDKit isn't available or the SMILES can't be parsed,
    so callers can keep their existing defaults.
    """
    if not smiles or not smiles.strip():
        return None, None
    try:
        from rdkit import Chem
    except ImportError:
        return None, None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None, None
        charge = sum(a.GetFormalCharge() for a in mol.GetAtoms())
        n_radical = sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
        mult = n_radical + 1
        return int(charge), int(mult)
    except Exception:
        return None, None


def diagnose_backends():
    # type: () -> str
    """Probe RDKit and OpenBabel: report version + run a methane embed test.
    Returns a multi-line string suitable for display in a dialog or terminal.
    Use this when coord generation mysteriously fails — it tells you whether
    the libraries are even importable and whether the simplest possible SMILES
    works."""
    import sys
    lines = []
    lines.append("Python: {} ({})".format(sys.version.split()[0], sys.executable))
    lines.append("Platform: {}".format(sys.platform))
    lines.append("")
    # --- RDKit ---
    try:
        import rdkit
        ver = getattr(rdkit, "__version__", "?")
        lines.append("RDKit: installed (version {})".format(ver))
        atoms, err = _try_rdkit("C")
        if atoms is None:
            lines.append("  Methane test: FAIL")
            lines.append("    {}".format(err))
        else:
            lines.append("  Methane test: OK ({} atoms)".format(len(atoms)))
    except ImportError as e:
        lines.append("RDKit: NOT INSTALLED ({})".format(e))
        lines.append("  Fix: pip install --user rdkit")
    except Exception as e:
        lines.append("RDKit: broken ({}: {})".format(type(e).__name__, e))
    lines.append("")
    # --- OpenBabel ---
    try:
        try:
            from openbabel import openbabel as _ob
        except ImportError:
            import openbabel as _ob  # type: ignore
        ver = "?"
        for attr in ("OBReleaseVersion", "__version__"):
            f = getattr(_ob, attr, None)
            if callable(f):
                try:
                    ver = f()
                    break
                except Exception:
                    pass
            elif isinstance(f, str):
                ver = f
                break
        lines.append("OpenBabel: installed (version {})".format(ver))
        atoms, err = _try_pybel("C")
        if atoms is None:
            lines.append("  Methane test: FAIL")
            lines.append("    {}".format(err))
        else:
            lines.append("  Methane test: OK ({} atoms)".format(len(atoms)))
    except ImportError as e:
        lines.append("OpenBabel: NOT INSTALLED ({})".format(e))
        lines.append("  Fix: pip install --user openbabel-wheel")
    except Exception as e:
        lines.append("OpenBabel: broken ({}: {})".format(type(e).__name__, e))
    lines.append("")
    lines.append("If RDKit reports installed but the methane test fails, the wheel may have")
    lines.append("loaded against an incompatible libstdc++ on this host. Try a fresh pip")
    lines.append("install in a clean conda env, or ask the cluster admin which Python/conda")
    lines.append("module already ships RDKit.")
    return "\n".join(lines)


def parse_smiles_list(text):
    # type: (str) -> List[Tuple[str, Optional[str]]]
    """Parse copy-pasted SMILES content. Handles three common formats:

    1. Single-line ChemDraw paste: 'O=C1CCCC1.OCC.CC' -> three molecules.
    2. One SMILES per line.
    3. Two-column lines (whitespace, comma, tab, or semicolon delimited) with
       SMILES + name in either order. Which column is which is auto-detected
       by RDKit-parse success rate when RDKit is available; otherwise the
       first column is assumed to be SMILES (common CSV convention).

    Lines starting with '#' and empty lines are skipped. Returns a list of
    (smiles, name_or_None) pairs in the order encountered.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Single-line ChemDraw-style paste: split on '.'
    if "\n" not in text and "." in text:
        return [(s.strip(), None) for s in text.split(".") if s.strip()]

    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f for f in re.split(r"[\s,;\t]+", line) if f]
        if fields:
            rows.append(fields)
    if not rows:
        return []

    max_cols = max(len(r) for r in rows)
    if max_cols == 1:
        return [(r[0], None) for r in rows]

    # 2+ columns: auto-pick the SMILES column by validity rate
    valid_counts = [0] * max_cols
    for r in rows:
        for i, field in enumerate(r):
            if smiles_is_valid(field):
                valid_counts[i] += 1

    if max(valid_counts) > 0:
        smiles_col = valid_counts.index(max(valid_counts))
    else:
        smiles_col = 0  # RDKit unavailable or no valid SMILES — assume convention

    # Name comes from the next-most-populated non-smiles column (typically the other one)
    name_col = None
    if max_cols >= 2:
        candidates = [i for i in range(max_cols) if i != smiles_col]
        if candidates:
            name_col = candidates[0]

    result = []
    for r in rows:
        if smiles_col >= len(r):
            continue
        s = r[smiles_col]
        n = r[name_col] if (name_col is not None and name_col < len(r)) else None
        result.append((s, n))
    return result


def smiles_is_valid(smiles):
    # type: (str) -> bool
    """Return True if RDKit can parse this SMILES (returns False if RDKit absent)."""
    if not smiles or not smiles.strip():
        return False
    try:
        from rdkit import Chem
    except ImportError:
        return False
    try:
        return Chem.MolFromSmiles(smiles) is not None
    except Exception:
        return False


def _try_rdkit(smiles):
    # type: (str) -> Tuple[Optional[List[Atom]], Optional[str]]
    """Returns (atoms, None) on success, (None, error_message) on failure."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as e:
        return None, "RDKit not installed ({})".format(e)
    except Exception as e:
        return None, "RDKit import raised: {}: {}".format(type(e).__name__, e)

    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None, "RDKit could not parse SMILES (MolFromSmiles returned None)"
        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        rc = AllChem.EmbedMolecule(mol, params)
        if rc == -1:
            return None, "RDKit ETKDGv3 failed to embed a 3D conformer"
        try:
            AllChem.MMFFOptimizeMolecule(mol)
        except Exception:
            # MMFF can be missing parameters for certain atom types — accept
            # the unoptimised embedded geometry rather than failing outright.
            pass
        conf = mol.GetConformer()
        atoms = []
        for atom in mol.GetAtoms():
            pos = conf.GetAtomPosition(atom.GetIdx())
            atoms.append((atom.GetSymbol(), float(pos.x), float(pos.y), float(pos.z)))
        return atoms, None
    except Exception as e:
        return None, "RDKit raised {}: {}".format(type(e).__name__, e)


def _try_pybel(smiles):
    # type: (str) -> Tuple[Optional[List[Atom]], Optional[str]]
    """Returns (atoms, None) on success, (None, error_message) on failure."""
    try:
        try:
            from openbabel import pybel
        except ImportError:
            import pybel  # type: ignore
    except ImportError as e:
        return None, "OpenBabel/Pybel not installed ({})".format(e)
    except Exception as e:
        return None, "OpenBabel import raised: {}: {}".format(type(e).__name__, e)

    try:
        mol = pybel.readstring("smi", smiles)
        mol.addh()
        mol.make3D(steps=50)
        mol.localopt(forcefield="uff", steps=200)
        atoms = []
        for atom in mol.atoms:
            symbol = _atomic_number_to_symbol(atom.atomicnum)
            x, y, z = atom.coords
            atoms.append((symbol, float(x), float(y), float(z)))
        return atoms, None
    except Exception as e:
        return None, "OpenBabel raised {}: {}".format(type(e).__name__, e)


_PERIODIC_TABLE = [
    "X",
    "H", "He",
    "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba",
    "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn",
]


def _atomic_number_to_symbol(z):
    # type: (int) -> str
    if 0 < z < len(_PERIODIC_TABLE):
        return _PERIODIC_TABLE[z]
    return "X"


def write_xyz(path, atoms, metadata=None):
    # type: (str, List[Atom], Optional[dict]) -> None
    """Write atoms to an .xyz file. Metadata (if given) goes in the comment line as JSON."""
    comment = json.dumps(metadata) if metadata else ""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}\n".format(len(atoms)))
        f.write("{}\n".format(comment))
        for symbol, x, y, z in atoms:
            clean = "".join(ch for ch in symbol if ch.isalpha())
            f.write("{:<2} {:10.5f} {:10.5f} {:10.5f}\n".format(clean, x, y, z))


def read_xyz(path):
    # type: (str) -> Tuple[List[Atom], Optional[dict]]
    """Read an .xyz file. Returns (atoms, metadata_or_None)."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().strip().split("\n")
    n = int(lines[0].strip())
    comment = lines[1] if len(lines) > 1 else ""
    metadata = None
    if comment.strip().startswith("{"):
        try:
            metadata = json.loads(comment)
        except ValueError:
            metadata = None
    atoms = []
    for line in lines[2:2 + n]:
        parts = line.split()
        if len(parts) < 4:
            continue
        atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
    return atoms, metadata


def format_atom_block(atoms):
    # type: (List[Atom]) -> str
    """Format atoms for an ORCA `* xyz` block body (no header or trailing `*`)."""
    lines = []
    for symbol, x, y, z in atoms:
        clean = "".join(ch for ch in symbol if ch.isalpha())
        lines.append("{:<2} {:14.8f} {:14.8f} {:14.8f}".format(clean, x, y, z))
    return "\n".join(lines)
