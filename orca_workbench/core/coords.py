"""Generate 3D coordinates from SMILES.

Tries RDKit (ETKDGv3 + MMFF) first, falls back to OpenBabel/Pybel (UFF) if
RDKit can't embed the molecule. Both libraries are installed via pip --user
on Lido. Returns coordinates as a list of (symbol, x, y, z) tuples plus the
name of the method that succeeded.
"""

import json
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple


Atom = Tuple[str, float, float, float]


_loggers_silenced = False


def _ensure_loggers_silenced():
    """Silence the chem-backend loggers once, lazily — on first actual use.

    Doing this at import time would drag the heavy RDKit + OpenBabel shared
    libraries into the process at startup (a slow load off the cluster's
    network-mounted conda env — the main reason the gateway launch felt slow),
    even for sessions that never touch a SMILES. So we defer it to the first
    coordinate/validity call instead."""
    global _loggers_silenced
    if _loggers_silenced:
        return
    _loggers_silenced = True
    _silence_chem_loggers()


def _silence_chem_loggers():
    """Both RDKit and OpenBabel print loud parse errors to stderr by default.
    Our auto-detect probes routinely feed them non-SMILES strings (column
    names like 'ethanol', 'benzene') to test validity, and Generate XYZ
    failures get reported cleanly through the UI's failed-status preview —
    no need for the C-level shouting."""
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
    _ensure_loggers_silenced()
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
    _ensure_loggers_silenced()
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


def smiles_mol_weight(smiles):
    # type: (str) -> Optional[float]
    """Average molecular weight (g/mol) of a SMILES via RDKit's Descriptors.MolWt.

    Uses standard atomic weights (natural isotope abundance), so it matches the
    "molar mass" a chemist reads off a bottle. Returns None if RDKit is
    unavailable or the SMILES can't be parsed, so callers can skip the annotation
    gracefully.
    """
    if not smiles or not smiles.strip():
        return None
    _ensure_loggers_silenced()
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors
    except ImportError:
        return None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return float(Descriptors.MolWt(mol))
    except Exception:
        return None


def diagnose_backends():
    # type: () -> str
    """Probe RDKit and OpenBabel: report version + run a methane embed test.
    Returns a multi-line string suitable for display in a dialog or terminal.
    Use this when coord generation mysteriously fails — it tells you whether
    the libraries are even importable and whether the simplest possible SMILES
    works."""
    import sys
    _ensure_loggers_silenced()
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
    _ensure_loggers_silenced()
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


def write_structure_file(path, atoms, name=""):
    # type: (str, List[Atom], str) -> str
    """Write atoms to `path` in the format its EXTENSION names.

    .xyz is written natively. Any other extension goes through OpenBabel
    (pybel — everything it can write: .mol, .sdf, .pdb, .mol2, .cml, ...),
    falling back to RDKit for .mol/.sdf when OpenBabel is absent. Returns the
    backend used ('native' / 'openbabel' / 'rdkit'); raises ValueError when no
    installed backend can write that format."""
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "xyz"
    if ext == "xyz":
        write_xyz(path, atoms, {"name": name} if name else None)
        return "native"

    # Build one XYZ block in memory — the lingua franca both backends read.
    xyz_text = _xyz_block(atoms, name) + "\n"

    errors = []
    try:
        try:
            from openbabel import pybel
        except ImportError:
            import pybel  # type: ignore
        if ext not in pybel.outformats:
            errors.append("OpenBabel doesn't write '{}'".format(ext))
        else:
            mol = pybel.readstring("xyz", xyz_text)
            if name:
                mol.title = name
            mol.write(ext, path, overwrite=True)
            return "openbabel"
    except ImportError as e:
        errors.append("OpenBabel/Pybel not installed ({})".format(e))
    except Exception as e:
        errors.append("OpenBabel raised {}: {}".format(type(e).__name__, e))

    if ext in ("mol", "sdf", "pdb"):
        try:
            from rdkit import Chem
            mol = Chem.MolFromXYZBlock(xyz_text)
            if mol is None:
                raise ValueError("RDKit could not read the geometry")
            try:
                # Perceive bonds so viewers show a real structure, not a point
                # cloud; harmless to skip if it fails (e.g. odd valences).
                from rdkit.Chem import rdDetermineBonds
                rdDetermineBonds.DetermineConnectivity(mol)
            except Exception:
                pass
            if ext == "pdb":
                Chem.MolToPDBFile(mol, path)
            elif ext == "sdf":
                with Chem.SDWriter(path) as w:
                    w.write(mol)
            else:
                Chem.MolToMolFile(mol, path)
            return "rdkit"
        except ImportError as e:
            errors.append("RDKit not installed ({})".format(e))
        except Exception as e:
            errors.append("RDKit raised {}: {}".format(type(e).__name__, e))

    raise ValueError("could not write '{}': {}".format(ext, "; ".join(errors)))


# File extensions that can hold MANY structures in one file (a trajectory /
# multi-record collection). xyz is a concatenated multi-frame trajectory (what
# molden/VMD/PyMOL animate); sdf is the classic multi-molecule container; pdb
# uses MODEL/ENDMDL; mol2 writes several @<TRIPOS>MOLECULE records. (.mol is
# single-structure only, so it's deliberately excluded.)
MULTI_STRUCTURE_FORMATS = ("xyz", "sdf", "pdb", "mol2")


def _xyz_block(atoms, name=""):
    lines = ["{}".format(len(atoms)), name or ""]
    for symbol, x, y, z in atoms:
        clean = "".join(ch for ch in symbol if ch.isalpha())
        lines.append("{:<2} {:14.8f} {:14.8f} {:14.8f}".format(clean, x, y, z))
    return "\n".join(lines)


def write_structures_file(path, structures):
    # type: (str, List) -> str
    """Write MANY structures into ONE file — a trajectory / multi-record
    collection. `structures` is a list of (atoms, name). The extension picks the
    format: .xyz is a native multi-frame trajectory (concatenated blocks); any
    other multi-capable extension (.sdf/.pdb/.mol2/...) goes through OpenBabel's
    multi-molecule writer, with an RDKit fallback for .sdf. Returns the backend
    used ('native' / 'openbabel' / 'rdkit'); raises ValueError if the format
    can't hold multiple structures or no backend can write it."""
    if not structures:
        raise ValueError("nothing to write")
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "xyz"
    if ext == "xyz":
        text = "\n".join(_xyz_block(atoms, nm) for atoms, nm in structures) + "\n"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return "native"

    errors = []
    try:
        try:
            from openbabel import pybel
        except ImportError:
            import pybel  # type: ignore
        if ext not in pybel.outformats:
            errors.append("OpenBabel doesn't write '{}'".format(ext))
        else:
            out = pybel.Outputfile(ext, path, overwrite=True)
            try:
                for atoms, nm in structures:
                    mol = pybel.readstring("xyz", _xyz_block(atoms, nm) + "\n")
                    if nm:
                        mol.title = nm
                    out.write(mol)
            finally:
                out.close()
            return "openbabel"
    except ImportError as e:
        errors.append("OpenBabel/Pybel not installed ({})".format(e))
    except Exception as e:
        errors.append("OpenBabel raised {}: {}".format(type(e).__name__, e))

    if ext == "sdf":
        try:
            from rdkit import Chem
            with Chem.SDWriter(path) as w:
                for atoms, nm in structures:
                    mol = Chem.MolFromXYZBlock(_xyz_block(atoms, nm) + "\n")
                    if mol is None:
                        raise ValueError("RDKit could not read a geometry")
                    try:
                        from rdkit.Chem import rdDetermineBonds
                        rdDetermineBonds.DetermineConnectivity(mol)
                    except Exception:
                        pass
                    if nm:
                        mol.SetProp("_Name", nm)
                    w.write(mol)
            return "rdkit"
        except ImportError as e:
            errors.append("RDKit not installed ({})".format(e))
        except Exception as e:
            errors.append("RDKit raised {}: {}".format(type(e).__name__, e))

    raise ValueError("could not write a multi-structure '{}': {}".format(
        ext, "; ".join(errors)))


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


# --------------------------------------------------------------- universal import
#
# Read 3D coordinates from any common chemistry file (SDF, MOL, MOL2, PDB, CIF,
# ...) and convert to our simple Atom list, so a user never hits a wall importing
# a non-.xyz structure. OpenBabel is the primary reader (148 input formats on the
# gateway, multi-record native); RDKit is a fallback for the common formats if
# OpenBabel isn't importable. .xyz stays on a dependency-free native path that
# also preserves our JSON-comment metadata and handles multi-frame trajectories.

# (extension, openbabel format code, human label) — order drives the file dialog.
SUPPORTED_IMPORT_FORMATS = [
    ("xyz", "xyz", "XYZ"),
    ("sdf", "sdf", "MDL SDF"),
    ("mol", "mol", "MDL MOL"),
    ("mol2", "mol2", "Sybyl MOL2"),
    ("pdb", "pdb", "PDB"),
    ("pdbqt", "pdbqt", "AutoDock PDBQT"),
    ("cif", "cif", "CIF"),
    ("mmcif", "mmcif", "mmCIF"),
    ("gro", "gro", "GROMACS"),
    ("hin", "hin", "HyperChem"),
    ("cml", "cml", "Chemical Markup"),
    ("gzmat", "gzmat", "Gaussian Z-matrix"),
    ("mdl", "mdl", "MDL"),
]
_EXT_TO_OBABEL = {ext: fmt for ext, fmt, _ in SUPPORTED_IMPORT_FORMATS}

# Hard cap on how long a single OpenBabel read may take before we give up. Some
# OpenBabel readers loop forever on certain inputs (.bgf/.box on round-tripped
# files), and because the read runs in a subprocess we can kill it cleanly rather
# than freezing the GUI. Generous: a real structure reads in well under a second.
IMPORT_READ_TIMEOUT_S = 15

_obabel_informats_cache = None  # None = not probed yet; set() once probed


def _openbabel_informats():
    # type: () -> Optional[set]
    """Cached set of OpenBabel-readable format codes (pybel.informats keys), or
    None if OpenBabel isn't importable. Safe to call in-process: listing formats
    can't hang (only readfile() can), so we use it to reject write-only/output
    formats instantly without paying for a reader subprocess."""
    global _obabel_informats_cache
    if _obabel_informats_cache is not None:
        return _obabel_informats_cache or None
    try:
        try:
            from openbabel import pybel
        except ImportError:
            import pybel  # type: ignore
        _obabel_informats_cache = set(pybel.informats.keys())
    except Exception:
        _obabel_informats_cache = set()   # probed, unavailable
    return _obabel_informats_cache or None


def _meta_from_title(title, source_path=None):
    # type: (Optional[str], Optional[str]) -> Optional[dict]
    """Turn a structure's title/comment into metadata. If it's a JSON object
    (our XYZ convention: {"name","smiles","charge","multiplicity",...}) parse it
    so SMILES/charge/etc. pre-fill the molecule on import; otherwise treat it as
    a plain display name. Lets embedded info ride along from any format that keeps
    a title line, for free.

    Guard: OpenBabel defaults a missing title to the input file path/name for many
    formats (.hin, .gzmat, ...). That's not a molecule name, so a path-like or
    filename-equal title is dropped — the caller then falls back to the clean
    filename instead of stamping a full path as the molecule's name."""
    if not title:
        return None
    t = title.strip()
    if t.startswith("{"):
        # Looks like our JSON metadata. If it parses to a dict, use it; if it's
        # broken/truncated (e.g. PDBQT cuts the title into a length-limited
        # record, leaving '{"name":'), it's not a display name — drop it.
        try:
            d = json.loads(t)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None
    if "/" in t or "\\" in t:
        return None
    if source_path:
        base = os.path.basename(source_path)
        if t == base or t == os.path.splitext(base)[0]:
            return None
    return {"name": t}


def is_supported_import_file(path):
    # type: (str) -> bool
    """True if the file's extension is a coordinate format we know how to read."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in _EXT_TO_OBABEL


# A SMILES-list file holds SMILES (optionally with names) rather than 3D
# coordinates: importing it creates molecules to GENERATE, not locked structures.
# (extension, human label) — drives the file dialog.
SMILES_LIST_FORMATS = [
    ("smi", "SMILES list"),
    ("smiles", "SMILES list"),
    ("csv", "CSV (SMILES + name)"),
]
_SMILES_LIST_EXTS = {ext for ext, _ in SMILES_LIST_FORMATS}


def is_smiles_list_file(path):
    # type: (str) -> bool
    """True if the file's extension is a SMILES-list format (.smi/.smiles/.csv)."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in _SMILES_LIST_EXTS


def read_smiles_file(path):
    # type: (str) -> List[Tuple[str, Optional[str]]]
    """Read a SMILES-list file (.smi/.smiles/.csv) into (smiles, name) pairs.

    The content is parsed by parse_smiles_list, which handles one-SMILES-per-line,
    a two-column SMILES+name table (delimiter and column order auto-detected), and
    a single-line dot-separated paste. When RDKit is available, entries whose
    SMILES doesn't parse are dropped — this transparently skips a header row
    (e.g. 'smiles,name') and any stray non-SMILES lines. Without RDKit we can't
    tell, so everything parse_smiles_list returns is kept.

    Raises CoordGenError if the file can't be read or yields no usable SMILES.
    """
    if not os.path.isfile(path):
        raise CoordGenError("File not found: {}".format(path))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        raise CoordGenError("Could not read {}: {}".format(path, e))
    pairs = parse_smiles_list(text)
    # When RDKit is available, keep only entries that actually parse — this drops
    # a header row ('smiles,name'), stray columns, and rejects a non-SMILES CSV
    # outright (rather than turning its rows into junk molecules). Without RDKit
    # we can't tell, so we keep whatever parse_smiles_list returned.
    if smiles_is_valid("C"):   # True only if RDKit is importable
        pairs = [(s, n) for s, n in pairs if smiles_is_valid(s)]
    if not pairs:
        raise CoordGenError(
            "No valid SMILES found in {} (expected one SMILES per line, or a "
            "SMILES,name table).".format(os.path.basename(path)))
    return pairs


def import_dialog_filetypes():
    # type: () -> list
    """Tk `filetypes` for the import dialog: an 'all coordinate files' entry first,
    then one per format, then 'All files'. Kept here (not the UI) so the format
    list has a single source of truth and stays testable."""
    all_pat = " ".join("*.{}".format(ext) for ext, _, _ in SUPPORTED_IMPORT_FORMATS)
    types = [("Coordinate files", all_pat)]
    # SMILES-list files are a separate group: importing them creates molecules to
    # generate (not locked structures), so keep the intent visibly distinct.
    smi_pat = " ".join("*.{}".format(ext) for ext, _ in SMILES_LIST_FORMATS)
    types.append(("SMILES lists ({})".format(smi_pat), smi_pat))
    for ext, _fmt, label in SUPPORTED_IMPORT_FORMATS:
        types.append(("{} (*.{})".format(label, ext), "*.{}".format(ext)))
    types.append(("All files", "*.*"))
    return types


def parse_xyz_frames_text(text):
    # type: (str) -> List[Tuple[List[Atom], Optional[dict]]]
    """Parse every frame of XYZ-format *text* (a string, not a path) into a list of
    (atoms, metadata). A plain single-geometry block yields one frame; a
    trajectory/multi-conformer block yields several. Each frame's comment line is
    decoded as JSON metadata when it looks like an object (our write_xyz convention).

    A leading integer atom-count line is required, so this returns [] for text that
    isn't XYZ — which lets callers use it to *detect* pasted coordinates. (A pasted
    SMILES list is rejected: its first line is an element/label token, not a bare
    count.)"""
    lines = (text or "").split("\n")
    frames = []
    n_lines = len(lines)
    i = 0
    while i < n_lines:
        while i < n_lines and not lines[i].strip():
            i += 1  # skip blank lines between frames
        if i >= n_lines:
            break
        try:
            n = int(lines[i].strip())
        except ValueError:
            break  # header isn't an atom count — stop (trailing junk)
        comment = lines[i + 1] if i + 1 < n_lines else ""
        atoms = []
        for j in range(i + 2, min(i + 2 + n, n_lines)):
            parts = lines[j].split()
            if len(parts) < 4:
                continue
            try:
                atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        metadata = None
        if comment.strip().startswith("{"):
            try:
                metadata = json.loads(comment)
            except ValueError:
                metadata = None
        frames.append((atoms, metadata))
        i = i + 2 + n
    return frames


def _read_xyz_frames(path):
    # type: (str) -> List[Tuple[List[Atom], Optional[dict]]]
    """Read every frame of an .xyz file (see parse_xyz_frames_text for the format
    handling — this just supplies the file's text)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_xyz_frames_text(f.read())


def _read_with_openbabel(path, fmt, timeout=IMPORT_READ_TIMEOUT_S):
    # type: (str, str, float) -> Tuple[Optional[list], Optional[str]]
    """Read all records via OpenBabel in a **killable subprocess**, so a reader
    that hangs (some formats loop forever on certain inputs) times out cleanly
    instead of freezing the caller. Returns (structures, None) on success or
    (None, error) on failure. Each structure is (atoms, metadata)."""
    cmd = [sys.executable, "-m", "orca_workbench.core._obabel_worker", fmt, path]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, ("reading timed out after {}s - the OpenBabel reader for '{}' "
                      "hung on this file (the format/file may be unreadable).".format(
                          timeout, fmt))
    except Exception as e:
        return None, "could not launch reader subprocess: {}: {}".format(type(e).__name__, e)
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if not out:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        return None, "reader produced no output{}".format(": " + err[:200] if err else "")
    try:
        data = json.loads(out.splitlines()[-1])
    except ValueError:
        return None, "reader output not understood: {}".format(out[:200])
    if not data.get("ok"):
        return None, data.get("error", "unknown error")
    structs = []
    for s in data.get("structures", []):
        atoms = [(a[0], float(a[1]), float(a[2]), float(a[3])) for a in s.get("atoms", [])]
        structs.append((atoms, _meta_from_title(s.get("name"), path)))
    return structs, None


def _read_with_rdkit(path, fmt):
    # type: (str, str) -> Tuple[Optional[list], Optional[str]]
    """Fallback reader for the common formats, used only if OpenBabel is absent.
    Reads existing 3D coordinates (does not generate them)."""
    try:
        from rdkit import Chem
    except Exception as e:
        return None, "RDKit not available ({})".format(e)
    f = fmt.lower()
    try:
        mols = []
        if f in ("sdf", "sd", "mdl"):
            mols = [m for m in Chem.SDMolSupplier(path, removeHs=False, sanitize=False)
                    if m is not None]
        elif f == "mol":
            m = Chem.MolFromMolFile(path, removeHs=False, sanitize=False)
            mols = [m] if m is not None else []
        elif f == "mol2":
            m = Chem.MolFromMol2File(path, removeHs=False, sanitize=False)
            mols = [m] if m is not None else []
        elif f in ("pdb", "ent"):
            m = Chem.MolFromPDBFile(path, removeHs=False, sanitize=False)
            mols = [m] if m is not None else []
        else:
            return None, "RDKit has no reader for '{}'".format(fmt)
        structs = []
        for m in mols:
            if m.GetNumConformers() == 0:
                continue
            conf = m.GetConformer()
            atoms = []
            for atom in m.GetAtoms():
                pos = conf.GetAtomPosition(atom.GetIdx())
                atoms.append((atom.GetSymbol(), float(pos.x), float(pos.y), float(pos.z)))
            name = m.GetProp("_Name").strip() if m.HasProp("_Name") else ""
            structs.append((atoms, _meta_from_title(name, path)))
        if not structs:
            return None, "RDKit found no 3D structures in {}".format(path)
        return structs, None
    except Exception as e:
        return None, "RDKit failed: {}: {}".format(type(e).__name__, e)


# --- heuristic Cartesian-block salvage (for write-only / input-deck formats) ---
#
# Some formats OpenBabel can only WRITE (input decks like ACES's .acesin, and
# various output files) still contain a plain "Label x y z" Cartesian block
# somewhere in the text. Rather than refuse them outright, we scan for the
# longest run of lines that have the same footprint as an .xyz body and lift the
# coordinates out — flagged as heuristic so the user knows to verify them.

# Element symbols (upper-cased) we accept as an atom label; gates out numeric
# tables (gradients, basis sets) that would otherwise look float-shaped.
_HEUR_ELEMENTS = {s.upper() for s in _PERIODIC_TABLE[1:]}
# A coordinate token: must carry a decimal point (rejects integer index columns);
# accepts Fortran 'D' exponents (1.23D-01).
_HEUR_FLOAT = r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eEdD][-+]?\d+)?"
# An atom line: element symbol (+ optional index digits) then EXACTLY three
# coordinates, end of line. Requiring exactly three rejects e.g. GAMESS's
# "C 6.0 x y z" (symbol, nuclear charge, then coords).
_HEUR_ATOM_RE = re.compile(
    r"^\s*([A-Za-z]{1,2})\d*\s+(" + _HEUR_FLOAT + r")\s+(" + _HEUR_FLOAT
    + r")\s+(" + _HEUR_FLOAT + r")\s*$")


def _heur_float(tok):
    # type: (str) -> float
    return float(tok.replace("D", "E").replace("d", "e"))


def heuristic_atoms_from_text(text):
    # type: (str) -> List[Atom]
    """Salvage a Cartesian block from arbitrary text by footprint matching.

    Returns the longest contiguous run of lines shaped like an .xyz body
    (`<element> x y z`), as Atom tuples; [] if no run of at least two such lines
    exists. The element-symbol gate means a numeric table (gradient components,
    basis-set exponents) is not mistaken for coordinates."""
    best = []      # type: List[Atom]
    run = []       # type: List[Atom]
    for line in text.split("\n"):
        m = _HEUR_ATOM_RE.match(line)
        if m and m.group(1).upper() in _HEUR_ELEMENTS:
            run.append((m.group(1), _heur_float(m.group(2)),
                        _heur_float(m.group(3)), _heur_float(m.group(4))))
        else:
            if len(run) > len(best):
                best = run
            run = []
    if len(run) > len(best):
        best = run
    return best if len(best) >= 2 else []


def _try_heuristic_file(path, fmt):
    # type: (str, str) -> Optional[list]
    """Last-resort reader: text-scan a file for an embedded xyz-footprint block.
    Returns [(atoms, meta)] with heuristic provenance, or None (no block / binary
    file / unreadable). Capped read — a coordinate block is small and near the top."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(1024 * 1024)
    except OSError:
        return None
    if b"\x00" in raw:
        return None   # looks binary — don't text-scan it
    atoms = heuristic_atoms_from_text(raw.decode("utf-8", "replace"))
    if not atoms:
        return None
    meta = {"name": os.path.splitext(os.path.basename(path))[0],
            "source": "heuristic",
            "comment": "coordinates heuristically extracted from .{} - VERIFY "
                       "before use".format(fmt)}
    return [(atoms, meta)]


def read_structures(path, fmt=None, timeout=IMPORT_READ_TIMEOUT_S):
    # type: (str, Optional[str], float) -> List[Tuple[List[Atom], Optional[dict]]]
    """Read ALL structures (records/frames) from a coordinate file.

    Returns a list of (atoms, metadata); a single-geometry file yields one entry,
    a multi-conformer SDF or multi-frame xyz yields several. Raises CoordGenError
    if nothing could be read. Non-xyz formats are read via OpenBabel in a
    timeout-guarded subprocess so a hanging reader can't freeze the caller.
    """
    _ensure_loggers_silenced()
    if not os.path.isfile(path):
        raise CoordGenError("File not found: {}".format(path))
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    fmt = (fmt or _EXT_TO_OBABEL.get(ext, ext)).lower()

    if fmt == "xyz":
        frames = _read_xyz_frames(path)
        if not frames or not any(a for a, _ in frames):
            raise CoordGenError("No atoms found in {}".format(path))
        return frames

    # Reject formats OpenBabel can't read *instantly* (no reader subprocess), so
    # importing a pile of write-only/output files skips them fast instead of
    # spawning a worker per file. (Only when we could query the format list.)
    informats = _openbabel_informats()
    if informats is not None and fmt not in informats:
        # OpenBabel can't read this format (write-only/input-deck/output). Before
        # giving up, try to salvage a Cartesian block from the text (e.g. .acesin).
        heur = _try_heuristic_file(path, fmt)
        if heur:
            return heur
        raise CoordGenError(
            "'{}' is not a readable structure format - OpenBabel can only write "
            "it, not read it (it's an input-deck/output/non-coordinate format). "
            "No xyz-style coordinate block was found to salvage either.".format(fmt))

    structs, ob_err = _read_with_openbabel(path, fmt, timeout=timeout)
    if structs:
        return structs
    rd_structs, rd_err = _read_with_rdkit(path, fmt)
    if rd_structs:
        return rd_structs
    # Both proper readers failed — last-resort heuristic salvage.
    heur = _try_heuristic_file(path, fmt)
    if heur:
        return heur
    raise CoordGenError(
        "Could not read {} (format '{}').\n  OpenBabel: {}\n  RDKit: {}".format(
            path, fmt, ob_err or "no structures returned", rd_err or "no reader"))


def read_coords_file(path, conformer_index=0, timeout=IMPORT_READ_TIMEOUT_S):
    # type: (str, int, float) -> Tuple[List[Atom], Optional[dict], int]
    """Read one structure from any coordinate file → (atoms, metadata, n_total).

    `conformer_index` is 0-based and accepts negatives (-1 = last) via normal
    Python indexing. `n_total` lets the caller decide whether to prompt the user
    to choose among multiple conformers."""
    structs = read_structures(path, timeout=timeout)
    n = len(structs)
    try:
        atoms, meta = structs[conformer_index]
    except IndexError:
        raise CoordGenError(
            "Conformer index {} out of range: {} has {} structure(s) "
            "(valid {}..{}).".format(conformer_index, os.path.basename(path), n,
                                     -n, n - 1))
    if not atoms:
        raise CoordGenError("Selected structure in {} has no atoms.".format(
            os.path.basename(path)))
    return atoms, meta, n


def parse_structure_selection(spec, n):
    # type: (str, int) -> List[int]
    """Parse a structure-selection spec for a file with n structures into an
    ordered, de-duplicated list of valid 0-based indices. Accepts:
      all / *          -> every structure
      0                -> a single index (negatives allowed: -1 = last)
      0,2,5  or  0 2 5 -> several
      0-3              -> an inclusive range (non-negative endpoints)
    Out-of-range tokens are dropped. Returns [] if nothing valid resolves."""
    spec = (spec or "").strip().lower()
    if spec in ("all", "*"):
        return list(range(n))
    out = []

    def _add(i):
        if i < 0:
            i += n                      # -1 -> last
        if 0 <= i < n and i not in out:
            out.append(i)

    for tok in re.split(r"[,\s]+", spec):
        if not tok:
            continue
        m = re.match(r"^(\d+)-(\d+)$", tok)     # non-negative inclusive range
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if a <= b else -1
            for i in range(a, b + step, step):
                _add(i)
            continue
        try:
            _add(int(tok))
        except ValueError:
            continue
    return out


def format_atom_block(atoms):
    # type: (List[Atom]) -> str
    """Format atoms for an ORCA `* xyz` block body (no header or trailing `*`)."""
    lines = []
    for symbol, x, y, z in atoms:
        clean = "".join(ch for ch in symbol if ch.isalpha())
        lines.append("{:<2} {:14.8f} {:14.8f} {:14.8f}".format(clean, x, y, z))
    return "\n".join(lines)
