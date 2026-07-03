"""Editor round-trips (Molecules-tab prepping): convert between a SMILES string and
a 2D structure file a chemical editor (ChemDraw / Marvin / …) can open + save.

Pure/UI-free and I/O-only here — the ui layer launches the editor and drives the
'edit then re-import' handshake. RDKit handles MDL molfiles/SDF directly; other
formats (ChemDraw .cdx/.cdxml, Marvin .mrv) fall back to OpenBabel if present.

The geometry round-trip (Avogadro) needs nothing here — Avogadro saves the .xyz
in place, so the ui just re-reads it via core.coords.
"""

import os

# Structure files a 2D editor might save. Order = read-preference for a tie.
STRUCTURE_EXTS = (".mol", ".sdf", ".mol2", ".cdxml", ".cdx", ".mrv", ".smi")


def write_smiles_molfile(smiles, path):
    # type: (str, str) -> str
    """Write an MDL molfile (.mol) with 2D coordinates for `smiles`, so a 2D editor
    can open it. An empty/None SMILES writes a blank molfile (draw from scratch).
    Raises ValueError if the SMILES can't be parsed, ImportError if RDKit is absent."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    smiles = (smiles or "").strip()
    if smiles:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError("RDKit could not parse SMILES: {!r}".format(smiles))
    else:
        mol = Chem.RWMol()   # empty canvas
    try:
        AllChem.Compute2DCoords(mol)
    except Exception:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    Chem.MolToMolFile(mol, path)
    return path


def _rdkit_smiles(path):
    # type: (str) -> "Optional[str]"
    try:
        from rdkit import Chem
    except Exception:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".sdf",):
            mols = [m for m in Chem.SDMolSupplier(path) if m is not None]
            mol = mols[0] if mols else None
        elif ext == ".mol2":
            mol = Chem.MolFromMol2File(path)
        else:                       # .mol / .rxn / default
            mol = Chem.MolFromMolFile(path)
        if mol is None:
            return None
        smi = Chem.MolToSmiles(mol)
        return smi or None
    except Exception:
        return None


def _obabel_smiles(path):
    # type: (str) -> "Optional[str]"
    """Read a structure via OpenBabel/pybel (handles cdx/cdxml/mrv/…). None if
    OpenBabel is absent or can't read the file."""
    try:
        try:
            from openbabel import pybel
        except ImportError:
            import pybel  # type: ignore
    except Exception:
        return None
    fmt = os.path.splitext(path)[1].lower().lstrip(".")
    try:
        mols = list(pybel.readfile(fmt, path))
    except Exception:
        return None
    if not mols:
        return None
    try:
        # pybel writes "SMILES\tTITLE\n"; keep the first whitespace-delimited token.
        out = mols[0].write("smi").strip()
        return out.split()[0] if out else None
    except Exception:
        return None


def read_structure_smiles(path):
    # type: (str) -> "Optional[str]"
    """Canonical SMILES for a structure file saved by an editor. Tries RDKit for
    molfile/SDF, OpenBabel for everything else (and as a fallback). None if nothing
    could read it."""
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mol", ".sdf", ".mol2", ".rxn"):
        smi = _rdkit_smiles(path)
        if smi:
            return smi
        return _obabel_smiles(path)
    if ext == ".smi":
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    tok = line.strip().split()
                    if tok:
                        return tok[0]
        except IOError:
            return None
        return None
    # cdx / cdxml / mrv / other: OpenBabel first, then a last RDKit try.
    return _obabel_smiles(path) or _rdkit_smiles(path)


def newest_structure_file(dirpath, after=0.0, exts=STRUCTURE_EXTS):
    # type: (str, float, tuple) -> "Optional[str]"
    """Most-recently-modified structure file in `dirpath` with mtime >= `after`.
    Lets the ui pick up whatever the editor saved (it may have chosen .cdxml over the
    .mol we handed it) without guessing the exact filename."""
    best = None
    best_mt = after
    try:
        names = os.listdir(dirpath)
    except OSError:
        return None
    for n in names:
        if os.path.splitext(n)[1].lower() not in exts:
            continue
        p = os.path.join(dirpath, n)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if os.path.isfile(p) and mt >= best_mt:
            best_mt, best = mt, p
    return best
