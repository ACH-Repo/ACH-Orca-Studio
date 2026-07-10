"""User-rebindable keyboard shortcuts — a tiny registry, UI-free.

Maps stable *action ids* (e.g. "app.save_project") to Tk key-sequence strings
(e.g. "<Control-s>"), with per-user overrides persisted in config
(~/.orca_workbench.json, under the "keymap" key). The ui layer reads
`sequence(id)` when it binds a shortcut, and the Settings > Keyboard shortcuts
dialog edits the overrides; nothing here touches Tk, so it's unit-testable.

Sequences are ordinary Tk event patterns: "<Control-n>", "<F5>", "<KeyPress-f>",
"<Control-Shift-N>". The default catalogue is registered at import time so the
dialog can list everything even before a plot window has been opened.
"""

from orca_workbench.core import config as _config

# action_id -> {"category", "label", "default"}
_ACTIONS = {}
_ORDER = []


def register(action_id, category, label, default):
    # type: (str, str, str, str) -> None
    if action_id not in _ACTIONS:
        _ORDER.append(action_id)
    _ACTIONS[action_id] = {"category": category, "label": label, "default": default}


def is_registered(action_id):
    return action_id in _ACTIONS


def label(action_id):
    return _ACTIONS.get(action_id, {}).get("label", action_id)


def category(action_id):
    return _ACTIONS.get(action_id, {}).get("category", "")


def default_sequence(action_id):
    return _ACTIONS.get(action_id, {}).get("default")


def actions():
    """All registered action ids, in registration order."""
    return list(_ORDER)


def by_category():
    """Ordered {category: [action_id, ...]} for building the settings dialog."""
    out = {}
    for aid in _ORDER:
        out.setdefault(_ACTIONS[aid]["category"], []).append(aid)
    return out


# ------------------------------------------------------------------ overrides

def _overrides():
    ov = _config.get("keymap", {})
    return dict(ov) if isinstance(ov, dict) else {}


def sequence(action_id):
    """The active sequence for an action: the user override if set, else the default."""
    ov = _overrides().get(action_id)
    if ov:
        return ov
    return default_sequence(action_id)


def is_overridden(action_id):
    return action_id in _overrides()


def set_override(action_id, seq):
    """Record a user override. Setting it back to the default (or empty) clears it, so
    project-independent defaults keep flowing after a package update."""
    ov = _overrides()
    if seq and seq != default_sequence(action_id):
        ov[action_id] = seq
    else:
        ov.pop(action_id, None)
    _config.set_value("keymap", ov)


def reset(action_id):
    ov = _overrides()
    if ov.pop(action_id, None) is not None:
        _config.set_value("keymap", ov)


def reset_all():
    _config.set_value("keymap", {})


def conflicts(action_id, seq):
    """Other actions in the SAME category already bound to `seq`. (Cross-category
    overlaps — e.g. a global shortcut vs a plot-window key — are allowed since they
    live in different windows.)"""
    cat = category(action_id)
    h = humanize(seq)   # compare normalised forms so "<g>" == "<KeyPress-g>"
    return [a for a in _ORDER
            if a != action_id and category(a) == cat and humanize(sequence(a)) == h]


# --------------------------------------------------- sequence <-> human helpers

_MOD_LABELS = [("Control", "Ctrl"), ("Shift", "Shift"), ("Alt", "Alt"), ("Mod1", "Alt")]
_MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Caps_Lock", "Num_Lock", "ISO_Level3_Shift",
}


def humanize(seq):
    # type: (str) -> str
    """'<Control-Shift-n>' -> 'Ctrl+Shift+N'; '<F5>' -> 'F5'; '<KeyPress-f>' -> 'F'."""
    if not seq:
        return "(unset)"
    s = seq.strip("<>").replace("KeyPress-", "").replace("Key-", "")
    parts = [p for p in s.split("-") if p]
    out = []
    for p in parts:
        lab = None
        for mod, ml in _MOD_LABELS:
            if p == mod:
                lab = ml
                break
        if lab is None:
            lab = p.upper() if len(p) == 1 else p   # single letter -> upper; keysyms as-is
        out.append(lab)
    return "+".join(out)


def event_to_sequence(state, keysym):
    # type: (int, str) -> "Optional[str]"
    """Build a Tk sequence from a KeyPress event's modifier bitmask + keysym. Returns
    None for a bare modifier press so a capture UI keeps waiting for a real key.

    Modifier bits vary by platform; we read Control (0x4) and Shift (0x1) which are
    stable, and Alt as Mod1 (0x8, X11) OR 0x20000 (Windows)."""
    if keysym in _MODIFIER_KEYSYMS:
        return None
    mods = []
    if state & 0x4:
        mods.append("Control")
    if state & 0x8 or state & 0x20000:
        mods.append("Alt")
    if state & 0x1:
        mods.append("Shift")
    key = keysym
    if len(key) == 1 and key.isalpha():
        key = key.lower()   # Shift is captured separately; store the base letter
    return "<" + "-".join(mods + [key]) + ">"


def sequence_variants(seq):
    # type: (str) -> "list"
    """Sequences to actually bind for `seq`.

    Tk treats '<...-f>' and '<...-F>' as different events, and with **Shift** held the
    event's keysym is the UPPER-case letter — so a Ctrl+Shift+M shortcut, stored as
    '<Control-Shift-m>', only fires if we ALSO bind '<Control-Shift-M>'. We therefore
    add the upper-case variant *only* when the sequence contains Shift.

    Crucially we do NOT add the upper-case variant to a non-Shift modified key: binding
    '<Control-S>' alongside '<Control-s>' would ALSO capture Ctrl+Shift+S, stealing it
    from a distinct Shift shortcut (Save vs. Save-As, New vs. Add-by-name). A bare
    letter (no modifiers, e.g. a plot 'f' key) still binds both cases so it survives
    Caps Lock, where there's no separate Shift shortcut to collide with."""
    if not seq:
        return []
    inner = seq.strip("<>").replace("KeyPress-", "").replace("Key-", "")
    parts = inner.split("-")
    key = parts[-1] if parts else ""
    if len(key) == 1 and key.isalpha():
        mods = parts[:-1]
        if mods:
            lo = "<" + "-".join(mods + [key.lower()]) + ">"
            if any(m.lower() == "shift" for m in mods):
                return [lo, "<" + "-".join(mods + [key.upper()]) + ">"]
            return [lo]
        return ["<KeyPress-{}>".format(key.lower()), "<KeyPress-{}>".format(key.upper())]
    return [seq]


# ---------------------------------------------------------- default catalogue

def _register_defaults():
    APP = "Application"
    PLOT = "Spectrum plots"
    CALC = "Calculations tab"
    defs = [
        ("app.new_project",  APP,  "New project",              "<Control-n>"),
        ("app.open_project", APP,  "Open project",             "<Control-o>"),
        ("app.save_project", APP,  "Save project",             "<Control-s>"),
        ("app.save_as",      APP,  "Save project as",          "<Control-Shift-S>"),
        ("app.add_by_name",  APP,  "Add molecule by name",     "<Control-Shift-N>"),
        ("app.import_files", APP,  "Import structure files",   "<Control-Shift-O>"),
        ("app.refresh",      APP,  "Refresh / status (F5)",    "<F5>"),
        # Fires the tab's launch action: Run locally on a PC, Submit on the
        # cluster. Bound on the calc table (not app-wide), so typing Ctrl+Enter
        # in some text field elsewhere can never launch jobs.
        ("calc.launch",      CALC, "Launch calculations (Run/Submit)", "<Control-Return>"),
        ("plot.reset",       PLOT, "Reset view (full)",        "<KeyPress-f>"),
        ("plot.edit_limits", PLOT, "Edit axis limits",         "<KeyPress-m>"),
        ("plot.zoom",        PLOT, "Zoom mode (cycle H/V/box)", "<KeyPress-z>"),
        ("plot.pan",         PLOT, "Pan mode (cycle H/V/free)", "<KeyPress-p>"),
        ("plot.redraw",      PLOT, "Redraw",                   "<KeyPress-r>"),
        ("plot.save_image",  PLOT, "Save image",               "<Control-s>"),
        ("plot.close",       PLOT, "Close plot window",        "<Control-w>"),
    ]
    for aid, cat, lab, default in defs:
        register(aid, cat, lab, default)


_register_defaults()
