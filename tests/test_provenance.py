"""Tests for core.provenance and its interaction with discovery.recipe_from_inp.

All pure/offline. Verifies the provenance block round-trips, that it is stripped
back out cleanly (without eating the recipe's own `#` comments), and that its
presence doesn't perturb the existing .inp parsers.

Run from the repo root:  python -m pytest tests/ -q
"""

from orca_workbench.core import provenance, discovery, inputs


RECIPE_BODY = (
    "# Benchmark 19F GIAO shielding. wB97M-V/pcSseg-2.\n"
    "# Property restricted to fluorine.\n"
    "\n"
    "! wB97M-V pcSseg-2 NMR VeryTightSCF DEFGRID3\n"
    "%pal nprocs 8\nend\n"
    "\n"
    "* xyz 0 1\n"
    "F 0.0 0.0 0.0\n"
    "H 0.0 0.0 0.9\n"
    "*\n"
    "%eprnmr\n NUCLEI = ALL F {SHIFT}\nEND\n"
)

INFO = {
    "molecule": "2-F-imidazole",
    "name": "2-Fluoroimidazole",
    "smiles": "Fc1ncc[nH]1",
    "charge": 0,
    "mult": 1,
    "recipe": "Bench 19F NMR",
    "calctype": "NMR",
    "method": "wB97M-V_pcSseg-2",
    "variant": "19F",
    "category": "bench",
    "geometry_source": "initial",
    "initial_xyz": "XYZ_INI/2-F-imidazole.xyz",
}


def test_format_parse_roundtrip():
    block = provenance.format_block(INFO, created="2026-06-23T00:00:00")
    got = provenance.parse_block(block)
    for k, v in INFO.items():
        assert got[k] == v
    assert isinstance(got["charge"], int) and isinstance(got["mult"], int)


def test_optional_fields_omitted_when_empty():
    block = provenance.format_block({
        "molecule": "m", "charge": 0, "mult": 1, "recipe": "r",
        "calctype": "SP", "category": "gen", "geometry_source": "initial"})
    got = provenance.parse_block(block)
    assert "smiles" not in got and "name" not in got and "initial_xyz" not in got


def test_parse_none_when_absent():
    assert provenance.parse_block(RECIPE_BODY) is None


def test_strip_removes_only_the_block():
    stamped = provenance.format_block(INFO) + RECIPE_BODY
    stripped = provenance.strip_block(stamped)
    assert provenance.BEGIN_MARKER not in stripped
    assert "# OWB" not in stripped
    assert "# Benchmark 19F GIAO shielding" in stripped   # recipe comments survive
    assert stripped.strip() == RECIPE_BODY.strip()


def test_strip_idempotent_without_block():
    assert provenance.strip_block(RECIPE_BODY) == RECIPE_BODY


def test_header_does_not_perturb_parsers():
    stamped = provenance.format_block(INFO) + RECIPE_BODY
    assert discovery._keyword_line(stamped).startswith("wB97M-V")
    assert discovery._charge_mult(stamped) == (0, 1)        # from * xyz, not # OWB charge
    block = inputs.extract_coords_section(stamped)
    assert block.startswith("* xyz") and block.rstrip().endswith("*")


def test_recipe_from_inp_excludes_provenance():
    stamped = provenance.format_block(INFO) + RECIPE_BODY
    r = discovery.recipe_from_inp(stamped)
    assert inputs.COORDS_PLACEHOLDER in r.template
    assert "# OWB" not in r.template
    assert provenance.BEGIN_MARKER not in r.template
    assert "# Benchmark 19F GIAO shielding" in r.template   # recipe comment retained
