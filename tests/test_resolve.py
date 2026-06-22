"""Tests for core.resolve. The HTTP getter is mocked, so these run with no
network. RDKit is required (it's a shipped dependency) for sanitisation/classify.

Run:  python -m pytest tests/test_resolve.py -q
"""

import pytest

from orca_workbench.core import resolve as R

pytest.importorskip("rdkit")   # resolve degrades without it, but the tests assert sanitised output


def make_get(routes, counter=None):
    """routes: list of (url-substring, (status, body)); first match wins."""
    def get(url):
        if counter is not None:
            counter.append(url)
        for sub, resp in routes:
            if sub in url:
                return resp
        return (404, "")
    return get


def cids(*ids):
    return (200, '{"IdentifierList": {"CID": [%s]}}' % ", ".join(str(i) for i in ids))


def prop(smiles, name="IsomericSMILES"):
    return (200, '{"PropertyTable": {"Properties": [{"CID": 1, "%s": "%s"}]}}' % (name, smiles))


def auto(*names):
    terms = ", ".join('"%s"' % n for n in names)
    return (200, '{"dictionary_terms": {"compound": [%s]}}' % terms)


# --------------------------------------------------------------- classify
@pytest.mark.parametrize("text,kind", [
    ("CCO", "smiles"),
    ("c1ccccc1", "smiles"),
    ("benzene", "name"),
    ("acetylsalicylic acid", "name"),
    ("InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3", "inchi"),
    ("BSYNRYMUTXBXSQ-WXRBYKJCSA-N", "inchikey"),
    ("50-78-2", "cas"),
])
def test_classify(text, kind):
    assert R.classify(text) == kind


# --------------------------------------------------------------- offline / input
def test_smiles_input_resolves_offline():
    res = R.resolve("CCO", allow_network=False)
    assert res.ok and res.source == "input (SMILES)"
    assert res.formula == "C2H6O"


def test_inchi_input_resolves():
    res = R.resolve("InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3")
    assert res.ok and res.source == "input (InChI)"
    assert res.formula == "C2H6O"


def test_name_offline_is_graceful_error():
    res = R.resolve("benzene", allow_network=False)
    assert not res.ok and "offline" in res.error.lower()


# --------------------------------------------------------------- OPSIN
def test_name_via_opsin():
    get = make_get([("opsin/", (200, "C1=CC=CC=C1"))])
    res = R.resolve("benzene", get=get)
    assert res.ok and res.source == "OPSIN web service"
    assert res.formula == "C6H6" and res.retrieved


# --------------------------------------------------------------- PubChem fallback
def test_name_falls_back_to_pubchem_when_opsin_misses():
    get = make_get([
        ("opsin/", (404, "")),
        ("/cids/", cids(2244)),
        ("/property/", prop("CC(=O)Oc1ccccc1C(=O)O")),
    ])
    res = R.resolve("aspirin", get=get)
    assert res.ok and res.source == "PubChem CID 2244"
    assert res.formula == "C9H8O4"


def test_cas_goes_straight_to_pubchem():
    routes = [("opsin/", (200, "SHOULD-NOT-BE-USED")),
              ("/cids/", cids(2244)), ("/property/", prop("CC(=O)Oc1ccccc1C(=O)O"))]
    seen = []
    get = make_get(routes, counter=seen)
    res = R.resolve("50-78-2", get=get)
    assert res.ok and "PubChem" in res.source
    assert not any("opsin" in u for u in seen)   # CAS never hits OPSIN


def test_inchikey_uses_inchikey_namespace():
    seen = []
    get = make_get([("/cids/", cids(702)), ("/property/", prop("CCO"))], counter=seen)
    res = R.resolve("LFQSCWFLJHTTHZ-UHFFFAOYSA-N", get=get)
    assert res.ok
    assert any("/compound/inchikey/" in u for u in seen)


def test_salt_is_stripped_to_largest_fragment():
    # ethylamine hydrochloride -> neutral ethylamine, counter-ion dropped
    get = make_get([("opsin/", (404, "")), ("/cids/", cids(5234)),
                    ("/property/", prop("CCN.Cl"))])
    res = R.resolve("ethylamine hydrochloride", get=get)
    assert res.ok and res.formula == "C2H7N" and res.charge == 0
    assert res.note and "fragment" in res.note


def test_stripping_keeps_a_charged_fragment_as_is():
    # honest behaviour: stripping a salt can leave a charged ion; we report it,
    # the user confirms/adjusts charge before adding (no silent neutralisation).
    get = make_get([("opsin/", (404, "")), ("/cids/", cids(517045)),
                    ("/property/", prop("CC(=O)[O-].[Na+]"))])
    res = R.resolve("sodium acetate", get=get)
    assert res.ok and res.charge == -1   # acetate anion kept, charge surfaced


def test_multiple_cids_recorded_in_note():
    get = make_get([("opsin/", (404, "")), ("/cids/", cids(1, 2, 3)),
                    ("/property/", prop("CCO"))])
    res = R.resolve("glucose", get=get)
    assert res.ok and "PubChem CID 1" == res.source
    assert "3 PubChem hits" in res.note


def test_pubchem_property_rename_fallback():
    # old IsomericSMILES request 404s; the new `SMILES` property answers.
    get = make_get([
        ("opsin/", (404, "")),
        ("/cids/", cids(962)),
        ("/property/IsomericSMILES/", (404, "")),
        ("/property/SMILES/", prop("O", name="SMILES")),
    ])
    res = R.resolve("water", get=get)
    assert res.ok and res.formula == "H2O"


# --------------------------------------------------------------- no match
def test_no_match_offers_autocomplete():
    get = make_get([("opsin/", (404, "")), ("/cids/", (404, "")),
                    ("/autocomplete/", auto("aspirin", "asparagine"))])
    res = R.resolve("asprin", get=get)            # typo
    assert not res.ok
    assert "did you mean" in res.error and "aspirin" in res.candidates


# --------------------------------------------------------------- network down
def test_network_error_is_graceful():
    import urllib.error

    def boom(url):
        raise urllib.error.URLError("no route to host")
    res = R.resolve("benzene", get=boom)
    assert not res.ok and "couldn't reach" in res.error


# --------------------------------------------------------------- cache
def test_cache_avoids_second_lookup():
    seen = []
    get = make_get([("opsin/", (200, "C1=CC=CC=C1"))], counter=seen)
    cache = {}
    r1 = R.resolve("benzene", get=get, cache=cache)
    n_after_first = len(seen)
    r2 = R.resolve("benzene", get=get, cache=cache)
    assert r1 is r2 and len(seen) == n_after_first   # no new HTTP calls


# --------------------------------------------------------------- provenance
def test_check_services_reports_each():
    get = make_get([("opsin/", (200, "C1=CC=CC=C1")),
                    ("/cids/", (200, '{"IdentifierList": {"CID": [962]}}'))])
    rows = R.check_services(get=get)
    labels = {label: ok for label, ok, _ in rows}
    assert labels["OPSIN web service"] is True
    assert labels["PubChem PUG-REST"] is True
    assert "RDKit (2D depiction)" in labels   # ok depends on whether rdkit is installed


def test_check_services_flags_unreachable():
    import urllib.error

    def down(url):
        raise urllib.error.URLError("network is unreachable")
    rows = R.check_services(get=down)
    by = {label: ok for label, ok, _ in rows}
    assert by["OPSIN web service"] is False and by["PubChem PUG-REST"] is False


def test_provenance_string():
    res = R.Resolution(query="aspirin", smiles="CC(=O)Oc1ccccc1C(=O)O",
                       source="PubChem CID 2244", retrieved="2026-06-22",
                       note="kept largest fragment")
    p = res.provenance()
    assert "aspirin" in p and "PubChem CID 2244" in p and "2026-06-22" in p
