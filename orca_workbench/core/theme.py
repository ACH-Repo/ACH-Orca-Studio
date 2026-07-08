"""Skins / colour themes for the app (pure data + config glue, UI-free).

A *skin* is a flat palette dict plus a base ttk theme name. The Tk-facing code
that actually paints widgets lives in ``ui/theming.py`` — this module just holds
the palettes and the get/set-active glue, so it's importable and testable with
no display (the registry, key validation and active-skin persistence are unit-
tested offline).

Design notes
------------
* The **default** skin is the app's native look. It carries a full light palette
  so that *switching back* to it from a dark skin can restore classic tk widgets,
  but ``ui/theming`` deliberately does NOT touch anything at startup while the
  default skin is active — so a fresh launch is pixel-identical to the unthemed
  app ("keep everything as is for the default skin").
* Every skin provides the SAME palette keys (``required_keys()``), so the applier
  can be totally generic.
* ``tags`` maps the Treeview lifecycle-row tag names used across the tabs
  (Molecules / Calculations / Recipes) to a background colour, so status rows
  stay legible on every skin; ``tag_fg`` is the shared row-text colour ("" =
  the widget default, used by the light skins).

Styling is intentionally NOT part of ``--simple`` mode (see the roadmap): the
caller gates on ``features.is_simple()`` before ever applying a skin.
"""

import json
import os

from orca_workbench.core import config as config_mod

# Config key holding the user's chosen skin id (per-user, never in project files).
CONFIG_KEY = "skin"

DEFAULT_SKIN_ID = "default"

# Palette keys every skin must define. Kept as a tuple so tests can assert
# completeness and the applier can trust every key is present.
_REQUIRED_KEYS = (
    "window",       # main/frame background (chrome)
    "surface",      # fields / lists / text / canvases (the "paper")
    "fg",           # primary text
    "muted",        # secondary / disabled text, dividers' labels
    "accent",       # primary action / active / selection highlight
    "accent_fg",    # text drawn on `accent`
    "select_bg",    # tree/list/text selection background
    "select_fg",    # tree/list/text selection foreground
    "border",       # borders, troughs, bevels
    "heading_bg",   # Treeview column headings
    "heading_fg",
    "node_canvas",  # workflow node-graph canvas backdrop
    "tooltip_bg",
    "tooltip_fg",
    "tags",         # dict: lifecycle tag name -> row background
    "tag_fg",       # shared foreground for tagged rows ("" = widget default)
)

# The full set of Treeview row-tag names used across the tabs. Every skin's
# `tags` dict covers these so re-skinning re-tints all status rows in one pass.
TAG_NAMES = (
    # Molecules tab
    "pending", "failed", "ok", "locked",
    # Calculations tab lifecycle
    "notbuilt", "waiting", "built", "running", "done", "error",
    "skipped", "interrupted",
    # Recipes tab
    "divider",
)


def _light_tags():
    # The app's original hard-coded row colours (Molecules/Calc/Recipes). Kept
    # here so the default + bright skins reproduce the familiar pastel scheme.
    return {
        "pending": "#fffde7", "failed": "#ffebee", "ok": "", "locked": "#dcdcdc",
        "notbuilt": "#fffde7", "waiting": "#eeeeee", "built": "", "running": "#e3f2fd",
        "done": "#e8f5e9", "error": "#ffebee", "skipped": "#ede7f6",
        "interrupted": "#ffe0b2", "divider": "#dfe3e8",
    }


# ---------------------------------------------------------------------------
# Node-graph colours. The workflow editor draws on a plain tk.Canvas (not ttk),
# so its colours are their own little palette. Each skin supplies a partial
# ``nodes`` dict; node_palette() fills the gaps from these light defaults, so a
# JSON skin only has to override what it wants. ``kinds`` maps a node kind to
# its title-bar fill (the meaningful hue: source=green, calc=blue, ...).
# ---------------------------------------------------------------------------
_NODE_DEFAULTS = {
    "body": "#fbfbfb",            # node body fill
    "fg": "#111111",             # title / label text
    "summary_fg": "#555555",     # the config-summary line
    "outline": "#7a8a99",        # idle node border
    "sel": "#1f6fb2",            # selection highlight (border + rubber band)
    "hover": "#f2a200",          # mouse-hover glow (a node the cursor is over)
    "hover_bad": "#d33a3a",      # hover glow for a node that can't work (red)
    "port_geom": "#2a8a2a",      # geometry port dot
    "port_results": "#b06000",   # results port dot
    "port_ring": "#333333",      # port outline
    "port_label": "#444444",
    "wire": "#5a6b7a",
    "wire_sel": "#1f6fb2",
    "splice": "#e08a2a",         # highlight of a wire a dropped node would splice
    "comment_bg": "#fdf6d8", "comment_fg": "#5a4a00", "comment_outline": "#d4b94a",
    "frame_bg": "#f0e6c2", "frame_fg": "#5a4a20", "frame_outline": "#b9a24a",
    "kinds": {
        "source": "#cfe8cf", "calc": "#d3e6f5", "sink": "#f0dcc0", "gate": "#ede0c8",
        "builder": "#e6d6f2", "filter": "#cfe6e0", "transform": "#f2e3c8",
        "combine": "#f2d9c8", "writer": "#e0dccf", "annotation": "#eeeeee",
    },
}

_NODE_OVERRIDES = {
    "default": {},   # the defaults above ARE the native look
    "dark": {
        "body": "#2a2b2e", "fg": "#e6e6e6", "summary_fg": "#9a9a9a",
        "outline": "#55606a", "sel": "#4aa3ff", "wire": "#6a7a88", "wire_sel": "#4aa3ff",
        "hover": "#ffb454", "hover_bad": "#ff5a5a",
        "port_geom": "#4caf50", "port_results": "#d9903b", "port_ring": "#0d0d0d",
        "port_label": "#b8b8b8", "splice": "#ffb454",
        "comment_bg": "#3a3620", "comment_fg": "#e8dca0", "comment_outline": "#6a5a20",
        "frame_bg": "#2e2b1e", "frame_fg": "#d8c98a", "frame_outline": "#5a4f2a",
        "kinds": {"source": "#24402a", "calc": "#1e3a4f", "sink": "#4a3a24",
                  "gate": "#423522", "builder": "#3a2b4a", "filter": "#24413c",
                  "transform": "#46402a", "combine": "#4a3324", "writer": "#38382e",
                  "annotation": "#333333"},
    },
    "aero": {
        "body": "#ffffff", "fg": "#0d3b5e", "summary_fg": "#3f7096",
        "outline": "#8fc7e6", "sel": "#17a5e6", "wire": "#5a90b0", "wire_sel": "#17a5e6",
        "hover": "#ff9a3a", "hover_bad": "#e04a4a",
        "port_geom": "#2a8a2a", "port_results": "#c06000", "port_label": "#3f7096",
        "splice": "#ff9a3a",
        "comment_bg": "#fff6cf", "comment_fg": "#6a5a10", "comment_outline": "#e0c85a",
        "frame_bg": "#e7f4d6", "frame_fg": "#3a5a1a", "frame_outline": "#9ec26a",
        "kinds": {"source": "#cdefd6", "calc": "#cfe6fb", "sink": "#ffe6c2",
                  "gate": "#ffedcf", "builder": "#e6ddf7", "filter": "#cdeef0",
                  "transform": "#fdeecf", "combine": "#ffe0cf", "writer": "#e8e4d6",
                  "annotation": "#e3eef5"},
    },
    "boombox": {
        "body": "#2a2d31", "fg": "#d6d6c2", "summary_fg": "#9a9a86",
        "outline": "#6b7178", "sel": "#39ff7a", "wire": "#6b7178", "wire_sel": "#39ff7a",
        "hover": "#ffd23b", "hover_bad": "#ff5555",
        "port_geom": "#39ff7a", "port_results": "#ffb13b", "port_ring": "#0a0a0a",
        "port_label": "#b0b09c", "splice": "#ffd23b",
        "comment_bg": "#2e3320", "comment_fg": "#c8d68a", "comment_outline": "#5a6a2a",
        "frame_bg": "#242820", "frame_fg": "#b8c88a", "frame_outline": "#4a5a2a",
        "kinds": {"source": "#2f4a34", "calc": "#2a3a44", "sink": "#4a3a24",
                  "gate": "#423522", "builder": "#3a2b4a", "filter": "#24413c",
                  "transform": "#46402a", "combine": "#4a3324", "writer": "#33352e",
                  "annotation": "#2e3236"},
    },
}


def node_palette(skin_id):
    # type: (str) -> dict
    """The node-graph colour dict for a skin: the light defaults, with the
    skin's overrides merged on top (kinds merge key-by-key). A JSON skin can
    carry a partial ``nodes`` dict and everything it omits falls back here."""
    out = dict(_NODE_DEFAULTS)
    out["kinds"] = dict(_NODE_DEFAULTS["kinds"])
    over = get_skin(skin_id).get("nodes") or _NODE_OVERRIDES.get(skin_id, {})
    for k, v in over.items():
        if k == "kinds" and isinstance(v, dict):
            out["kinds"].update(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# The skins, in display order. First entry is the default/native look.
# ---------------------------------------------------------------------------
_SKINS = [
    {
        "id": "default",
        "label": "Default",
        "tagline": "The classic native look — light and familiar.",
        "ttk_base": None,           # None => keep the platform-native ttk theme
        "window": "#f0f0f0",
        "surface": "#ffffff",
        "fg": "#000000",
        "muted": "#444444",
        "accent": "#0078d7",
        "accent_fg": "#ffffff",
        "select_bg": "#0078d7",
        "select_fg": "#ffffff",
        "border": "#a0a0a0",
        "heading_bg": "#e6e6e6",
        "heading_fg": "#000000",
        "node_canvas": "#eef1f4",
        "tooltip_bg": "#ffffe0",
        "tooltip_fg": "#000000",
        "tags": _light_tags(),
        "tag_fg": "",
    },
    {
        "id": "dark",
        "label": "Dark",
        "tagline": "Easy-on-the-eyes neutral dark mode.",
        "ttk_base": "clam",
        "window": "#1e1e1e",
        "surface": "#252526",
        "fg": "#e0e0e0",
        "muted": "#9a9a9a",
        "accent": "#0a84ff",
        "accent_fg": "#ffffff",
        "select_bg": "#094771",
        "select_fg": "#ffffff",
        "border": "#3c3c3c",
        "heading_bg": "#2d2d30",
        "heading_fg": "#d4d4d4",
        "node_canvas": "#1b1b1c",
        "tooltip_bg": "#333333",
        "tooltip_fg": "#f0f0f0",
        "tags": {
            "pending": "#4a4526", "failed": "#4a2323", "ok": "", "locked": "#3a3a3a",
            "notbuilt": "#4a4526", "waiting": "#333333", "built": "", "running": "#12384f",
            "done": "#1e3a24", "error": "#4a2323", "skipped": "#33294a",
            "interrupted": "#4a3a1e", "divider": "#2d2d30",
        },
        "tag_fg": "#e0e0e0",
    },
    {
        "id": "aero",
        "label": "Frutiger Aero",
        "tagline": "Bright, glassy sky-blue & fresh green — mid-2000s optimism.",
        "ttk_base": "clam",
        "window": "#dceffb",         # bright sky blue
        "surface": "#ffffff",        # crisp white "glass"
        "fg": "#0d3b5e",             # deep aqua-navy ink
        "muted": "#3f7096",
        "accent": "#17a5e6",         # aqua
        "accent_fg": "#ffffff",
        "select_bg": "#57c7f2",
        "select_fg": "#063048",
        "border": "#8fc7e6",
        "heading_bg": "#bfe3f7",
        "heading_fg": "#0d3b5e",
        "node_canvas": "#eaf7ff",
        "tooltip_bg": "#eafaff",
        "tooltip_fg": "#0d3b5e",
        "tags": {
            "pending": "#fff4c2", "failed": "#ffd9d9", "ok": "", "locked": "#cfe0ee",
            "notbuilt": "#fff4c2", "waiting": "#e3eef5", "built": "", "running": "#cfeefb",
            "done": "#d6f5d9", "error": "#ffd9d9", "skipped": "#e6ddf7",
            "interrupted": "#ffe6c2", "divider": "#bfe3f7",
        },
        "tag_fg": "#0d3b5e",
    },
    {
        "id": "boombox",
        "label": "Boombox",
        "tagline": "Winamp-era brushed metal with a glowing green LCD.",
        "ttk_base": "clam",
        "window": "#232323",
        "surface": "#2a2d31",
        "fg": "#d6d6c2",
        "muted": "#9a9a86",
        "accent": "#39ff7a",         # LCD green
        "accent_fg": "#0c130c",
        "select_bg": "#2f7d4a",
        "select_fg": "#caffd9",
        "border": "#15171a",
        "heading_bg": "#3a3f44",
        "heading_fg": "#d6d6c2",
        "node_canvas": "#1c1c1c",
        "tooltip_bg": "#0c130c",
        "tooltip_fg": "#39ff7a",
        "tags": {
            "pending": "#3a3520", "failed": "#5a2b2b", "ok": "", "locked": "#2e3236",
            "notbuilt": "#3a3520", "waiting": "#2e3236", "built": "", "running": "#12332a",
            "done": "#1f5a34", "error": "#5a2b2b", "skipped": "#2e2a3a",
            "interrupted": "#4a3a1e", "divider": "#3a3f44",
        },
        "tag_fg": "#d6d6c2",
    },
]


# --------------------------------------------------------------------------
# User skins loaded from JSON files on disk. A skin file is a flat JSON object;
# it need only carry an ``id`` plus whatever colours it wants to change — every
# other key is inherited from its ``base`` skin (default "default"), so a custom
# skin can be as small as {"id":"neon","base":"dark","accent":"#39ff14"}.
# Everything else (button hover, tab highlight, selection, ...) is DERIVED from
# the palette by ui/theming, so a colours-only JSON gives full theming.
# --------------------------------------------------------------------------
_user_skins = None   # cache: list of merged skin dicts, or None = not loaded


def user_skins_dir():
    # type: () -> str
    return os.path.join(os.path.expanduser("~"), ".orca_workbench_skins")


def _merge_skin(base, data):
    # type: (dict, dict) -> dict
    """Overlay JSON `data` onto a copy of `base`; `tags` and `nodes` merge by key."""
    out = dict(base)
    out["tags"] = dict(base.get("tags") or {})
    if base.get("nodes"):
        out["nodes"] = dict(base["nodes"])
    for k, v in data.items():
        if k in ("tags", "nodes") and isinstance(v, dict):
            merged = dict(out.get(k) or {})
            for sk, sv in v.items():
                if sk == "kinds" and isinstance(sv, dict):
                    merged["kinds"] = dict(merged.get("kinds") or {})
                    merged["kinds"].update(sv)
                else:
                    merged[sk] = sv
            out[k] = merged
        elif k != "base":
            out[k] = v
    return out


def load_user_skins():
    # type: () -> list
    """Read every *.json in user_skins_dir() into a merged skin dict (cached).
    A file that can't be parsed or has no id is skipped (best-effort)."""
    global _user_skins
    if _user_skins is not None:
        return _user_skins
    skins = []
    d = user_skins_dir()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    builtin = {s["id"]: s for s in _SKINS}
    for fn in names:
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        base_id = data.get("base", DEFAULT_SKIN_ID)
        base = dict(builtin.get(base_id, _SKINS[0]))
        # Seed the base's node-graph colours so a dark-based custom skin keeps
        # dark nodes unless it overrides them (built-in node colours live in
        # _NODE_OVERRIDES, not in the skin dicts).
        base["nodes"] = dict(_NODE_OVERRIDES.get(base_id, {}))
        merged = _merge_skin(base, data)
        merged.setdefault("label", data["id"])
        merged.setdefault("tagline", "Custom skin ({})".format(fn))
        merged["_source"] = os.path.join(d, fn)
        skins.append(merged)
    _user_skins = skins
    return skins


def reload_user_skins():
    # type: () -> None
    """Drop the cache so the next access re-reads the skins folder."""
    global _user_skins
    _user_skins = None


def write_skin_template(path, base_id=DEFAULT_SKIN_ID):
    # type: (str, str) -> None
    """Write a starter skin JSON (a full copy of `base_id`'s palette) the user
    can edit. Includes id/label/base so it loads straight away."""
    base = get_skin(base_id)
    data = {"id": "my_skin", "label": "My Skin", "base": base_id,
            "tagline": "My custom skin"}
    for k in _REQUIRED_KEYS:
        v = base.get(k)
        data[k] = dict(v) if isinstance(v, dict) else v
    data["nodes"] = node_palette(base_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def required_keys():
    # type: () -> tuple
    return _REQUIRED_KEYS


def all_skins():
    # type: () -> list
    """Every skin dict (built-in first, then user JSON skins), in display order.
    A user skin whose id matches a built-in REPLACES it (so you can re-theme
    'dark' by dropping a dark.json)."""
    user = load_user_skins()
    user_by_id = {s["id"]: s for s in user}
    out = [user_by_id.get(s["id"], dict(s)) for s in _SKINS]
    seen = {s["id"] for s in _SKINS}
    for s in user:
        if s["id"] not in seen:
            out.append(s)
            seen.add(s["id"])
    return out


def skin_ids():
    # type: () -> list
    return [s["id"] for s in all_skins()]


def get_skin(skin_id):
    # type: (str) -> dict
    """Return the skin dict for `skin_id` (user skins included), falling back to
    the default skin if the id is unknown."""
    for s in load_user_skins():
        if s["id"] == skin_id:
            return s
    for s in _SKINS:
        if s["id"] == skin_id:
            return s
    return _SKINS[0]


def swatch_colors(skin_id):
    # type: (str) -> list
    """A small representative colour list for a preview chip: window, surface,
    accent, and the ink."""
    s = get_skin(skin_id)
    return [s["window"], s["surface"], s["accent"], s["fg"]]


def active_skin_id():
    # type: () -> str
    """The user's chosen skin id from config, validated against the registry."""
    sid = config_mod.get(CONFIG_KEY, DEFAULT_SKIN_ID) or DEFAULT_SKIN_ID
    if sid not in skin_ids():
        return DEFAULT_SKIN_ID
    return sid


def set_active_skin_id(skin_id):
    # type: (str) -> None
    """Persist the chosen skin (per-user config). Unknown ids are ignored."""
    if skin_id in skin_ids():
        config_mod.set_value(CONFIG_KEY, skin_id)
