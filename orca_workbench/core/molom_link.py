"""Asking MoloM to DEFINE a `%geom` block, and reading its answer back.

The geometry round trip has been one-way since round 92 of the MoloM side:
OWB launches an external 3D program with a file and gets a geometry back, and
`GeomSpecDialog`'s "View geometry (3D)..." button opens the molecule so you
can read atom indices off it BY EYE and type them in here. That works and it
is tedious, and Christian's framing is the obvious one: "it only makes sense
that you click on something in OWB, molom opens and then you get a GUI with
which you can do the thing you wish to do."

**THE REQUEST GOES THROUGH THE ENVIRONMENT, NOT THE COMMAND LINE.** The 3D
program slots hold whatever the user put in them, and whether Avogadro,
molden or PyMOL ignores an unknown argument, exits non-zero, or tries to open
a file called `--geomspec` is not consistent and is not something OWB can
know. **A program that does not read an environment variable cannot be
affected by one**, so `MOLOM_GEOMSPEC_FILE` can be set on every launch and
only MoloM notices - including after the slot is repointed at something else.
That is exactly the argument that settled `MOLOM_ROUNDTRIP_FILE`, and this is
the same mechanism for the other direction.

**WHAT COMES BACK IS THIS PACKAGE'S OWN SPEC SHAPE, AS JSON** - not `%geom`
text. MoloM builds the same text we do (its `core/orca.py`, pinned by a test
that imports `geomspec` and compares byte for byte), so it could send either;
JSON means nothing has to be parsed back and the text stays what it is for,
which is humans and the clipboard.

No Tkinter here: this is the path, the environment and the reading. The
launching and the waiting are the UI's, because waiting means `after()`.
"""

import json
import os

from typing import Optional


#: The variable MoloM reads. Its own `core/orca.py` defines the same name;
#: they are pinned against each other by a test on the MoloM side.
GEOMSPEC_ENV = "MOLOM_GEOMSPEC_FILE"

#: What the file is called inside the scratch directory. A real name rather
#: than a `mkstemp` one, because it is a path a user may well see in a status
#: line - the same reasoning as MoloM's own round 84.
SPEC_FILENAME = "molom_geomspec.json"


def request_path(scratch_dir):
    # type: (str) -> str
    """Where MoloM should write the spec, given a scratch directory."""
    return os.path.join(scratch_dir, SPEC_FILENAME)


def launch_env(path, env=None):
    # type: (str, Optional[dict]) -> dict
    """A copy of the environment with the request in it.

    A COPY, never the live `os.environ`: the variable is a question asked of
    ONE launch, and leaving it set in this process would arm every later one
    - including a launch of something the user repointed the slot at.
    """
    out = dict(os.environ if env is None else env)
    out[GEOMSPEC_ENV] = str(path)
    return out


def read_spec(path):
    # type: (str) -> Optional[dict]
    """The spec MoloM wrote, or None if it has not written one yet.

    None for every failure - missing, half-written, not JSON, not a spec -
    because the caller is polling and "not yet" and "not valid" both mean
    "keep waiting" until the program exits. A malformed answer is reported
    then, once, rather than on every poll.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (IOError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    constraints = payload.get("constraints")
    if not isinstance(constraints, list):
        constraints = []
    # Either shape, because MoloM may be older than this build - the same
    # tolerance `geomspec.scans_of` gives an old project file.
    scans = payload.get("scans")
    if not isinstance(scans, list):
        one = payload.get("scan")
        scans = [one] if isinstance(one, dict) and one else []
    return {"constraints": [c for c in constraints if isinstance(c, dict)],
            "scans": [s for s in scans if isinstance(s, dict) and s]}


def looks_like_molom(program):
    # type: (Optional[str]) -> bool
    """Is the configured 3D program MoloM?

    By the executable's own NAME, which is all there is to go on without
    running it - and running it to find out is exactly what this decides
    whether to do. Wrong in the harmless direction either way: a MoloM under
    another name simply does not get the button, and something else called
    `molom` is handed an environment variable it does not read.
    """
    if not program:
        return False
    base = os.path.basename(str(program)).lower()
    for suffix in (".exe", ".bat", ".cmd", ".com"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    return base in ("molom", "molom-gui") or base.startswith("molom")
