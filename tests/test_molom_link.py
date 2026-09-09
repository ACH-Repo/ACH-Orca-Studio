"""The other direction of the geometry round trip: MoloM DEFINES a %geom.

`open_xyz_3d` hands a geometry out; `core.molom_link` is how we ask a
question and read the answer. The channel is an environment variable naming a
file, for the reason that settled `MOLOM_ROUNDTRIP_FILE`: a program that does
not read the variable cannot be affected by it, so it can be set on every
external-editor launch and only MoloM notices.
"""

import json
import os

import pytest

from orca_workbench.core import geomspec, inputs, molom_link


def test_the_request_goes_in_a_COPY_of_the_environment(tmp_path):
    """Never the live `os.environ`: the variable is a question asked of ONE
    launch, and leaving it set here would arm every later one - including a
    launch of whatever the user repoints the 3D slot at."""
    before = dict(os.environ)
    target = molom_link.request_path(str(tmp_path))
    env = molom_link.launch_env(target)
    assert env[molom_link.GEOMSPEC_ENV] == target
    assert molom_link.GEOMSPEC_ENV not in os.environ or \
        os.environ.get(molom_link.GEOMSPEC_ENV) != target
    assert dict(os.environ) == before, "this process is unchanged"
    # ...and it inherits everything else, or the child loses its PATH
    env2 = molom_link.launch_env(target, {"PATH": "/x", "HOME": "/y"})
    assert env2["PATH"] == "/x" and env2["HOME"] == "/y"


def test_only_MOLOM_IS_OFFERED_THE_BUTTON():
    """By the executable's NAME, which is all there is without running it -
    and running it to find out is what this decides. Wrong in the harmless
    direction either way: a MoloM under another name simply does not get the
    button, and something else called `molom` is handed a variable it does
    not read."""
    for name in ("molom", "molom.exe", "MoloM.EXE", r"C:\tools\molom.bat",
                 "/usr/local/bin/molom"):
        assert molom_link.looks_like_molom(name), name
    for name in ("avogadro2.exe", "molden", "pymol", "", None,
                 "vmd.exe"):
        assert not molom_link.looks_like_molom(name), name


def test_nothing_written_yet_reads_as_NOT_YET(tmp_path):
    """None for every failure, because the caller is polling: "not there"
    and "half written" both mean keep waiting."""
    target = molom_link.request_path(str(tmp_path))
    assert molom_link.read_spec(target) is None
    with open(target, "w", encoding="utf-8") as handle:
        handle.write('{"constraints": [{"type": "B", "atoms"')   # truncated
    assert molom_link.read_spec(target) is None
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("[1, 2, 3]")                                # not a spec
    assert molom_link.read_spec(target) is None


def test_what_comes_back_is_a_spec_THIS_PACKAGE_can_use(tmp_path):
    """JSON in our own shape rather than %geom text, so nothing has to be
    parsed back - and it goes straight through validate and the injector."""
    target = molom_link.request_path(str(tmp_path))
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"constraints": [{"type": "B", "atoms": [0, 1]},
                                   {"type": "C", "atoms": [4]}],
                   "scan": {"type": "D", "atoms": [0, 1, 2, 3],
                            "start": -180.0, "end": -60.0, "steps": 4}},
                  handle)
    spec = molom_link.read_spec(target)
    assert spec is not None
    assert geomspec.validate(spec, n_atoms=10) == []
    inner = geomspec.build_geom_inner(spec)
    assert "{ B 0 1 C }" in inner and "{ C 4 C }" in inner
    assert "D 0 1 2 3 = -180, -60, 4" in inner


def test_SEVERAL_scans_come_back_and_so_does_the_old_single_shape(tmp_path):
    """ORCA runs a grid, so the reply may carry more than one - and a MoloM
    older than this build still sends `"scan"`, which must keep working."""
    target = molom_link.request_path(str(tmp_path))
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"constraints": [],
                   "scans": [{"type": "B", "atoms": [0, 1], "start": 1.5,
                              "end": 1.6, "steps": 3},
                             {"type": "D", "atoms": [0, 1, 2, 3],
                              "start": -180.0, "end": -120.0, "steps": 3}]},
                  handle)
    spec = molom_link.read_spec(target)
    assert len(geomspec.scans_of(spec)) == 2
    assert geomspec.validate(spec, n_atoms=10) == []
    inner = geomspec.build_geom_inner(spec)
    assert inner.count("Scan") == 1, "ONE Scan block holding both"
    assert "B 0 1 = 1.5, 1.6, 3" in inner
    assert "D 0 1 2 3 = -180, -120, 3" in inner
    assert "9 grid points" in geomspec.describe(spec)

    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"constraints": [],
                   "scan": {"type": "B", "atoms": [0, 1], "start": 1.5,
                            "end": 3.0, "steps": 10}}, handle)
    old = molom_link.read_spec(target)
    assert len(geomspec.scans_of(old)) == 1
    assert "B 0 1 = 1.5, 3, 10" in geomspec.build_geom_inner(old)
    text = inputs.add_geom_block("! Opt XTB\n\n* xyz 0 1\n*\n", inner)
    assert "%geom" in text and text.strip().endswith("*")


def test_a_spec_with_junk_in_it_is_cleaned_rather_than_trusted(tmp_path):
    """It arrived from another process; the shape is checked before it is
    handed to the rest of the app."""
    target = molom_link.request_path(str(tmp_path))
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"constraints": "not a list", "scan": 7}, handle)
    spec = molom_link.read_spec(target)
    assert spec == {"constraints": [], "scans": []}
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"constraints": [{"type": "B", "atoms": [0, 1]}, 5, None],
                   "scan": None}, handle)
    assert len(molom_link.read_spec(target)["constraints"]) == 1


def test_the_scratch_file_has_a_NAME(tmp_path):
    """A path a user may well see in a status line, so it says what it is -
    the same reasoning as MoloM's own temp-directory naming."""
    assert molom_link.request_path(str(tmp_path)).endswith(".json")
    assert "geomspec" in os.path.basename(
        molom_link.request_path(str(tmp_path)))
