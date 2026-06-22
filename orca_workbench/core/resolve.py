"""Resolve a chemical identifier (name / SMILES / InChI / CAS / InChIKey) to a
SMILES string over public web services, for the Molecules tab's "Add by name".

Web-only and OPTIONAL by design (see the feature tiers): with no internet, only
offline SMILES/InChI parsing works and everything else returns a graceful error
— the feature degrades, it never crashes. Stdlib urllib + RDKit (already shipped);
no OPSIN jar, no extra dependencies.

Tier order:
  pasted SMILES / InChI   -> RDKit (offline)
  IUPAC name              -> OPSIN web service   (handles novel names PubChem lacks)
  trade/common name, CAS  -> PubChem PUG-REST
  miss                    -> PubChem autocomplete ("did you mean ...")

Every web result records provenance (source + retrieval date). The SMILES is
RDKit-sanitised and salt-stripped to one fragment (QM wants a single molecule,
not `drug.HCl`). The HTTP getter is injectable so the whole thing is unit-tested
with no network.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

OPSIN_URL = "https://opsin.ch.cam.ac.uk/opsin/{}.smi"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest"
TIMEOUT = 12

_INCHI = re.compile(r"^InChI=", re.I)
_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_CAS = re.compile(r"^\d{2,7}-\d{2}-\d$")
_SMILES_CHARS = re.compile(r"^[A-Za-z0-9@+\-\[\]()=#$:/\\.%*]+$")


@dataclass
class Resolution:
    query: str
    smiles: Optional[str] = None        # sanitised, salt-stripped
    raw_smiles: Optional[str] = None    # exactly as the source returned it
    source: Optional[str] = None        # provenance, e.g. "PubChem CID 3672"
    formula: Optional[str] = None
    charge: Optional[int] = None
    retrieved: Optional[str] = None     # ISO date for web sources
    candidates: List[str] = field(default_factory=list)  # did-you-mean / ambiguity
    note: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self):
        return bool(self.smiles) and not self.error

    def provenance(self):
        """One-line provenance string, suitable for Molecule.comment."""
        bits = ["resolved from {!r}".format(self.query)]
        if self.source:
            bits.append("via " + self.source)
        if self.retrieved:
            bits.append("on " + self.retrieved)
        if self.note:
            bits.append("[" + self.note + "]")
        return " ".join(bits)


# ---------------------------------------------------------------- HTTP
def _http_get(url, timeout=TIMEOUT):
    """Return (status_code, body_text). Raises urllib.error.URLError with no network.
    HTTP errors (404 etc.) are returned as (code, body), not raised, so callers
    can branch on them."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "orca-workbench-resolver/1.0",
                      "Accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""


def _reason(e):
    return getattr(e, "reason", e)


# ---------------------------------------------------------------- RDKit (optional)
def _rdkit():
    try:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        from rdkit import RDLogger
        RDLogger.DisableLog("rdApp.*")   # quiet the parse-failure spam
        return Chem, rdMolDescriptors
    except ImportError:
        return None, None


def sanitize(smiles):
    """RDKit-sanitise + salt-strip to the largest fragment. Returns
    (clean_smiles, formula, charge, n_fragments), or (None, None, None, 0) if
    invalid. Passes the SMILES through unverified if RDKit is unavailable."""
    Chem, desc = _rdkit()
    if Chem is None:
        return smiles, None, None, 1
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, None, None, 0
    frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    n = len(frags)
    if n > 1:
        mol = max(frags, key=lambda m: m.GetNumHeavyAtoms())
    return (Chem.MolToSmiles(mol), desc.CalcMolFormula(mol),
            Chem.GetFormalCharge(mol), n)


def classify(text):
    """Classify an identifier: 'inchi' | 'inchikey' | 'cas' | 'smiles' | 'name'."""
    t = (text or "").strip()
    if _INCHI.match(t):
        return "inchi"
    if _INCHIKEY.match(t):
        return "inchikey"
    if _CAS.match(t):
        return "cas"
    if t and " " not in t and _SMILES_CHARS.match(t):
        Chem, _ = _rdkit()
        if Chem is not None:
            return "smiles" if Chem.MolFromSmiles(t) is not None else "name"
        return "smiles"   # no RDKit: looks SMILES-ish with no spaces -> assume SMILES
    return "name"


# ---------------------------------------------------------------- resolve
def resolve(query, allow_network=True, get=None, cache=None):
    """Resolve `query` to a SMILES. `get(url)->(status, body)` is injectable for
    tests; `cache` is an optional dict reused across calls."""
    q = (query or "").strip()
    if not q:
        return Resolution(query=query or "", error="empty query")
    if cache is not None and q in cache:
        return cache[q]
    res = _resolve_inner(q, allow_network, get or _http_get)
    if cache is not None and res.ok:
        cache[q] = res
    return res


def _finish(res, raw):
    clean, formula, charge, n = sanitize(raw)
    if clean is None:
        res.error = "got a structure but it failed RDKit parsing: {!r}".format(raw)
        return res
    res.raw_smiles, res.smiles = raw, clean
    res.formula, res.charge = formula, charge
    if n > 1:
        res.note = "stripped {} extra fragment(s) (salt/solvent); kept the largest".format(n - 1)
    return res


def _resolve_inner(q, allow_network, get):
    kind = classify(q)

    if kind in ("smiles", "inchi"):
        Chem, _ = _rdkit()
        if kind == "inchi":
            if Chem is None:
                return Resolution(query=q, error="InChI input needs RDKit, which isn't installed")
            mol = Chem.MolFromInchi(q)
            if mol is None:
                return Resolution(query=q, error="RDKit could not parse that InChI")
            return _finish(Resolution(query=q, source="input (InChI)"), Chem.MolToSmiles(mol))
        return _finish(Resolution(query=q, source="input (SMILES)"), q)

    if not allow_network:
        return Resolution(query=q, error="offline: only SMILES/InChI resolve without internet")

    today = datetime.date.today().isoformat()

    # 1) OPSIN for systematic names (offline-resolver power, over HTTP)
    if kind == "name":
        try:
            status, body = get(OPSIN_URL.format(urllib.parse.quote(q)))
        except urllib.error.URLError as e:
            return Resolution(query=q, error="couldn't reach OPSIN ({})".format(_reason(e)))
        smi = body.strip()
        if status == 200 and smi and "\n" not in smi:
            return _finish(Resolution(query=q, source="OPSIN web service", retrieved=today), smi)

    # 2) PubChem by name / CAS / InChIKey
    namespace = "inchikey" if kind == "inchikey" else "name"
    try:
        status, body = get("{}/pug/compound/{}/{}/cids/JSON".format(
            PUBCHEM, namespace, urllib.parse.quote(q)))
    except urllib.error.URLError as e:
        return Resolution(query=q, error="couldn't reach PubChem ({})".format(_reason(e)))
    cids = []
    if status == 200:
        try:
            cids = json.loads(body)["IdentifierList"]["CID"]
        except (ValueError, KeyError):
            cids = []
    if cids:
        cid = cids[0]
        smi = _pubchem_smiles(cid, get)
        if smi:
            res = Resolution(query=q, source="PubChem CID {}".format(cid), retrieved=today)
            if len(cids) > 1:
                res.note = "{} PubChem hits; used first (CID {}). Others: {}".format(
                    len(cids), cid, ", ".join(str(c) for c in cids[1:6]))
            return _finish(res, smi)

    # 3) no match -> autocomplete suggestions
    sugg = _autocomplete(q, get)
    msg = "no match for {!r}".format(q)
    if sugg:
        msg += " - did you mean: " + ", ".join(sugg[:5]) + "?"
    return Resolution(query=q, error=msg, candidates=sugg)


def _pubchem_smiles(cid, get):
    """Fetch a stereo-bearing SMILES for a CID. PubChem renamed these properties
    in 2025 (IsomericSMILES -> SMILES; CanonicalSMILES -> ConnectivitySMILES),
    so we try the stereo names in order and take the first that returns a value."""
    for prop in ("IsomericSMILES", "SMILES", "CanonicalSMILES"):
        try:
            status, body = get("{}/pug/compound/cid/{}/property/{}/JSON".format(PUBCHEM, cid, prop))
        except urllib.error.URLError:
            return None
        if status == 200:
            try:
                props = json.loads(body)["PropertyTable"]["Properties"][0]
            except (ValueError, KeyError, IndexError):
                continue
            val = props.get(prop) or props.get("SMILES") or props.get("IsomericSMILES")
            if val:
                return val
    return None


def check_services(get=None):
    """Quick reachability probe for the resolver's web services + RDKit. Returns
    a list of (label, ok, detail) for a 'can Add-by-name work here?' diagnostic.
    Uses a short timeout so a firewalled host fails fast rather than hanging."""
    if get is None:
        def get(url):
            return _http_get(url, timeout=6)
    out = []
    for label, url in (("OPSIN web service", OPSIN_URL.format("benzene")),
                       ("PubChem PUG-REST", "{}/pug/compound/name/water/cids/JSON".format(PUBCHEM))):
        try:
            status, body = get(url)
            ok = status == 200 and bool((body or "").strip())
            out.append((label, ok, "HTTP {}".format(status)))
        except urllib.error.URLError as e:
            out.append((label, False, "unreachable ({})".format(_reason(e))))
        except Exception as e:
            out.append((label, False, "error: {}".format(e)))
    Chem, _ = _rdkit()
    out.append(("RDKit (2D depiction)", Chem is not None,
                "available" if Chem is not None else "not installed"))
    return out


def _autocomplete(q, get):
    try:
        status, body = get("{}/autocomplete/compound/{}/json?limit=8".format(
            PUBCHEM, urllib.parse.quote(q)))
    except urllib.error.URLError:
        return []
    if status != 200:
        return []
    try:
        return json.loads(body).get("dictionary_terms", {}).get("compound", []) or []
    except ValueError:
        return []
