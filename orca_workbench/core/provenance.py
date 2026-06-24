"""Provenance header for generated ORCA `.inp` files.

Every `.inp` the app builds gets a small, delimited block of `#` comment lines
at the very top recording where it came from: which molecule, name, SMILES,
charge/multiplicity, recipe, calctype/method/variant, geometry source, and the
initial-geometry path. ORCA treats `#` as a comment, so this is inert to the
calculation but lets the app re-associate calcs with molecules after a project
save file is lost (see core/discovery.py).

The block is **delimited** on purpose: recipes inject their own leading `#`
comments into the template, so an unbounded provenance header would be
indistinguishable from them. `strip_block` relies on the markers to remove
exactly the provenance and nothing else — `discovery.recipe_from_inp` calls it
so reconstructed recipe templates never swallow (and then re-emit) provenance.

Format is human-readable AND machine round-trippable:

    # >>> ORCA Workbench provenance (auto-generated) >>>
    # OWB molecule: 2-F-imidazole
    # OWB charge: 0
    # ...
    # <<< ORCA Workbench provenance <<<
"""

import datetime


BEGIN_MARKER = "# >>> ORCA Workbench provenance (auto-generated) >>>"
END_MARKER = "# <<< ORCA Workbench provenance <<<"
_PREFIX = "# OWB "

# Emission order. Keys flagged optional are omitted when empty/None; the rest are
# always written (so a reader can rely on them being present).
_FIELDS = [
    ("molecule", False),
    ("name", True),
    ("smiles", True),
    ("gen_smiles", True),
    ("charge", False),
    ("mult", False),
    ("recipe", False),
    ("calctype", False),
    ("method", True),
    ("variant", True),
    ("category", False),
    ("geometry_source", False),
    ("initial_xyz", True),
    ("origin_node", True),
]
_INT_KEYS = ("charge", "mult")


def _tool_version():
    # type: () -> str
    try:
        from orca_workbench import __version__
        return __version__
    except Exception:
        return "unknown"


def _clean(value):
    # type: (object) -> str
    """One-line string for a comment value (newlines would break the block)."""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def format_block(info, created=None):
    # type: (dict, str) -> str
    """Render the provenance block (with trailing blank line) to prepend to an
    .inp. `info` keys mirror `_FIELDS`; unknown keys are ignored. Optional keys
    with empty/None values are skipped."""
    lines = [BEGIN_MARKER]
    for key, optional in _FIELDS:
        val = info.get(key)
        if optional and (val is None or _clean(val) == ""):
            continue
        lines.append("{}{}: {}".format(_PREFIX, key, _clean("" if val is None else val)))
    stamp = created or datetime.datetime.now().isoformat(timespec="seconds")
    lines.append("{}created: {}  tool: orca-workbench {}".format(
        _PREFIX, stamp, _tool_version()))
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n\n"


def parse_block(inp_text):
    # type: (str) -> object
    """Parse a provenance block back into a dict, or None if there isn't one.
    `charge`/`mult` are coerced to int when possible; other values stay strings.
    The `created:` summary line is not parsed into fields (it's informational)."""
    if BEGIN_MARKER not in inp_text:
        return None
    out = {}
    in_block = False
    for raw in inp_text.split("\n"):
        line = raw.rstrip("\r").strip()
        if line == BEGIN_MARKER:
            in_block = True
            continue
        if line == END_MARKER:
            break
        if not in_block or not line.startswith(_PREFIX):
            continue
        body = line[len(_PREFIX):]
        if ":" not in body:
            continue
        key, _sep, val = body.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "created":
            continue
        if key in _INT_KEYS:
            try:
                val = int(val)
            except ValueError:
                pass
        out[key] = val
    return out or None


def strip_block(inp_text):
    # type: (str) -> str
    """Remove the provenance block (markers inclusive) plus one trailing blank
    line, leaving everything else (incl. the recipe's own `#` comments) intact.
    Returns the text unchanged if there's no block."""
    if BEGIN_MARKER not in inp_text:
        return inp_text
    lines = inp_text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].rstrip("\r").strip() == BEGIN_MARKER:
            i += 1
            while i < n and lines[i].rstrip("\r").strip() != END_MARKER:
                i += 1
            i += 1  # consume the end marker
            if i < n and lines[i].strip() == "":
                i += 1  # consume one trailing blank line
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)
