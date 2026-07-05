"""Apply a skin (from ``core/theme``) to the live Tk/ttk widget tree.

Theming a Tkinter app is two problems in one:

* **ttk widgets** (Frames, Buttons, Entries, Notebook, Treeview, …) are painted
  by a *theme engine*. Re-colouring them means switching to a fully re-styleable
  base theme (``clam``) and configuring its styles. Because ttk styles are global,
  this restyles every existing ttk widget at once — no tree walk needed.
* **classic tk widgets** (Text, Listbox, Canvas, Menu, and the odd tk.Label /
  tk.Button) read their colours once, at creation, from per-widget options and
  the option database. So we (a) seed the option database for widgets created
  *later* (dialogs, combobox dropdowns) and (b) walk the *existing* tree and
  recolour them in place.

The recolour is **conservative**: a classic widget's background is only changed
if its current colour is one we "manage" (a native default, or a colour some
skin set). Widgets given an intentional colour at creation — the workflow Run/
Generate/Submit buttons, the red DELETE button, LCD-style fields — are left
alone, so semantic colours survive every skin.

Call :func:`init` once (after the root exists) to capture the native theme name
and the set of managed colours, then :func:`apply_skin` to (re)paint.
"""

import tkinter as tk
from tkinter import ttk

from orca_workbench.core import theme as theme_mod

# Captured once in init(): the platform-native ttk theme (e.g. 'vista'), restored
# when the user picks the default skin.
_native_theme = None            # type: str

# Colours we're allowed to overwrite on classic widgets (normalised, lowercase):
# native widget defaults probed at init + every colour any skin paints.
_managed_bg = set()
_managed_fg = set()

# tk widget classes whose "field" should get the `surface` colour (the paper),
# vs. everything else which gets the `window` colour (the chrome).
_SURFACE_CLASSES = frozenset(("Entry", "Text", "Listbox", "Canvas", "Spinbox"))
# classic widgets that carry a foreground we may theme.
_FG_CLASSES = frozenset((
    "Label", "Button", "Entry", "Text", "Listbox", "Checkbutton",
    "Radiobutton", "Menubutton", "Spinbox", "Message", "Menu",
))


def _norm(color):
    # type: (object) -> str
    return str(color).strip().lower()


def init(root):
    # type: (tk.Misc) -> None
    """Capture the native ttk theme and probe native widget default colours.

    Safe to call more than once (idempotent); does nothing that touches the
    user's saved skin. Must run before the first apply_skin so the managed-colour
    set is populated (otherwise the conservative recolour would match nothing)."""
    global _native_theme
    style = ttk.Style(root)
    if _native_theme is None:
        try:
            _native_theme = style.theme_use()
        except tk.TclError:
            _native_theme = "default"

    # Probe real widgets for their platform default colours (e.g. SystemButtonFace
    # / SystemWindow on Windows) so we recognise "untouched" widgets on any OS.
    _managed_bg.clear()
    _managed_fg.clear()
    probes = []
    try:
        f = tk.Frame(root); probes.append(f)
        e = tk.Entry(root); probes.append(e)
        t = tk.Text(root); probes.append(t)
        lb = tk.Listbox(root); probes.append(lb)
        b = tk.Button(root); probes.append(b)
        for w, opt in ((f, "background"), (e, "background"), (t, "background"),
                       (lb, "background"), (b, "background")):
            try:
                _managed_bg.add(_norm(w.cget(opt)))
            except tk.TclError:
                pass
        for w, opt in ((e, "foreground"), (b, "foreground")):
            try:
                _managed_fg.add(_norm(w.cget(opt)))
            except tk.TclError:
                pass
    except tk.TclError:
        pass
    finally:
        for w in probes:
            try:
                w.destroy()
            except tk.TclError:
                pass

    # Plus every colour any skin paints, so re-skinning (dark -> aero) still
    # recognises the currently-painted chrome as ours to change.
    for s in theme_mod.all_skins():
        for key in ("window", "surface", "node_canvas"):
            _managed_bg.add(_norm(s[key]))
        for key in ("fg", "muted"):
            _managed_fg.add(_norm(s[key]))
    # A couple of app-specific neutral canvases that predate theming.
    _managed_bg.update({"#eef1f4", "#f4f6f8", "systemwindow", "systembuttonface", ""})


def apply_skin(root, skin_id):
    # type: (tk.Misc, str) -> None
    """(Re)paint the whole app in `skin_id`. Idempotent."""
    if _native_theme is None:
        init(root)
    skin = theme_mod.get_skin(skin_id)
    style = ttk.Style(root)

    # 1) Base ttk theme: the native one for the default skin, `clam` (fully
    #    re-styleable) for the coloured skins.
    base = skin.get("ttk_base")
    try:
        style.theme_use(base if base else _native_theme)
    except tk.TclError:
        pass

    # 2) ttk style colours — only for the coloured skins; the default skin keeps
    #    the pristine native theme untouched.
    if base:
        _style_ttk(style, skin)
    _apply_base_metrics(style, root)

    # 3) Seed the option database for widgets created later (dialogs, dropdowns).
    _apply_option_db(root, skin)

    # 4) Recolour the existing classic-tk widgets + retint Treeview tags.
    _recolor_tree(root, skin)

    # 5) Tooltip popups read module-level colours at show time.
    try:
        from orca_workbench.ui import tooltip as tooltip_mod
        tooltip_mod.set_colors(skin["tooltip_bg"], skin["tooltip_fg"])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ttk styling
# ---------------------------------------------------------------------------
def _style_ttk(style, s):
    win, surf, fg = s["window"], s["surface"], s["fg"]
    muted, accent, afg = s["muted"], s["accent"], s["accent_fg"]
    sel_bg, sel_fg, border = s["select_bg"], s["select_fg"], s["border"]

    style.configure(".", background=win, foreground=fg, fieldbackground=surf,
                    bordercolor=border, lightcolor=win, darkcolor=win,
                    troughcolor=surf, focuscolor=accent, insertcolor=fg)
    style.map(".", foreground=[("disabled", muted)])

    style.configure("TFrame", background=win)
    style.configure("TLabel", background=win, foreground=fg)
    style.configure("TLabelframe", background=win, bordercolor=border)
    style.configure("TLabelframe.Label", background=win, foreground=muted)
    style.configure("TPanedwindow", background=win)
    style.configure("TSeparator", background=border)
    style.configure("TSizegrip", background=win)

    style.configure("TButton", background=surf, foreground=fg,
                    bordercolor=border, focuscolor=accent)
    style.map("TButton",
              background=[("pressed", accent), ("active", accent), ("disabled", win)],
              foreground=[("pressed", afg), ("active", afg), ("disabled", muted)])

    for cls in ("TCheckbutton", "TRadiobutton"):
        style.configure(cls, background=win, foreground=fg, focuscolor=accent)
        style.map(cls, background=[("active", win)], foreground=[("disabled", muted)],
                  indicatorcolor=[("selected", accent), ("!selected", surf)])

    style.configure("TEntry", fieldbackground=surf, foreground=fg,
                    bordercolor=border, insertcolor=fg)
    style.map("TEntry", fieldbackground=[("readonly", win), ("disabled", win)])
    style.configure("TSpinbox", fieldbackground=surf, foreground=fg,
                    bordercolor=border, insertcolor=fg, arrowcolor=fg)
    style.configure("TCombobox", fieldbackground=surf, foreground=fg,
                    bordercolor=border, arrowcolor=fg, insertcolor=fg)
    style.map("TCombobox",
              fieldbackground=[("readonly", surf), ("disabled", win)],
              foreground=[("disabled", muted)],
              selectbackground=[("!focus", surf)], selectforeground=[("!focus", fg)])
    style.configure("TMenubutton", background=surf, foreground=fg, arrowcolor=fg)

    style.configure("TNotebook", background=win, bordercolor=border)
    style.configure("TNotebook.Tab", background=surf, foreground=muted)
    style.map("TNotebook.Tab",
              background=[("selected", win)], foreground=[("selected", fg)])

    style.configure("Treeview", background=surf, fieldbackground=surf, foreground=fg)
    style.map("Treeview",
              background=[("selected", sel_bg)], foreground=[("selected", sel_fg)])
    style.configure("Treeview.Heading", background=s["heading_bg"],
                    foreground=s["heading_fg"], relief="flat", bordercolor=border)
    style.map("Treeview.Heading",
              background=[("active", accent)], foreground=[("active", afg)])

    style.configure("TScrollbar", background=win, troughcolor=surf,
                    bordercolor=border, arrowcolor=fg)
    style.map("TScrollbar", background=[("active", accent)])
    style.configure("Vertical.TScrollbar", background=win, troughcolor=surf,
                    bordercolor=border, arrowcolor=fg)
    style.configure("Horizontal.TScrollbar", background=win, troughcolor=surf,
                    bordercolor=border, arrowcolor=fg)
    style.configure("TProgressbar", background=accent, troughcolor=surf,
                    bordercolor=border)


def _apply_base_metrics(style, root):
    """Font/size metrics that must be re-asserted after any theme switch (ttk
    stores style config per-theme, so switching to clam loses the app's startup
    tweaks). Colours are left to _style_ttk; this is size-only, so it's safe for
    the native default skin too."""
    try:
        import tkinter.font as tkfont
        base = tkfont.nametofont("TkDefaultFont").actual("size")
        style.configure("TNotebook.Tab", padding=[14, 7],
                        font=("TkDefaultFont", abs(base) + 3, "bold"))
        fnt = tkfont.nametofont("TkDefaultFont")
        style.configure("Treeview",
                        rowheight=int(fnt.metrics("linespace") * 1.35) + 2)
    except (tk.TclError, KeyError):
        pass


# ---------------------------------------------------------------------------
# option database (future widgets) + tree walk (existing widgets)
# ---------------------------------------------------------------------------
def _apply_option_db(root, s):
    """Seed defaults for classic tk widgets created after this point (dialogs,
    the combobox dropdown listbox). Explicit widget options still win, so
    intentionally-coloured widgets are unaffected."""
    win, surf, fg = s["window"], s["surface"], s["fg"]
    sel_bg, sel_fg = s["select_bg"], s["select_fg"]
    accent, afg = s["accent"], s["accent_fg"]
    add = root.option_add
    for cls in ("Frame", "Labelframe", "Toplevel"):
        add("*{}.background".format(cls), win)
    add("*Label.background", win)
    add("*Label.foreground", fg)
    for cls, bg in (("Entry", surf), ("Text", surf), ("Listbox", surf), ("Spinbox", surf)):
        add("*{}.background".format(cls), bg)
        add("*{}.foreground".format(cls), fg)
        add("*{}.selectBackground".format(cls), sel_bg)
        add("*{}.selectForeground".format(cls), sel_fg)
    add("*Text.insertBackground", fg)
    add("*Entry.insertBackground", fg)
    add("*Menu.background", win)
    add("*Menu.foreground", fg)
    add("*Menu.activeBackground", accent)
    add("*Menu.activeForeground", afg)
    # The dropdown list inside a ttk.Combobox is a classic Listbox created lazily.
    add("*TCombobox*Listbox.background", surf)
    add("*TCombobox*Listbox.foreground", fg)
    add("*TCombobox*Listbox.selectBackground", sel_bg)
    add("*TCombobox*Listbox.selectForeground", sel_fg)


def _safe(w, **opts):
    """Set each option independently, ignoring the ones this widget lacks."""
    for k, v in opts.items():
        try:
            w.configure(**{k: v})
        except tk.TclError:
            pass


def _recolor_tree(widget, s):
    """Recursively recolour classic tk widgets + retint Treeview row tags."""
    _recolor_one(widget, s)
    for child in widget.winfo_children():
        _recolor_tree(child, s)


def _recolor_one(w, s):
    # ttk widgets are painted by the style engine — the only per-widget work is
    # re-tinting a Treeview's lifecycle row tags.
    if isinstance(w, ttk.Widget):
        if isinstance(w, ttk.Treeview):
            _retint_treeview(w, s)
        return

    try:
        cls = w.winfo_class()
    except tk.TclError:
        return

    win, surf, fg = s["window"], s["surface"], s["fg"]
    sel_bg, sel_fg = s["select_bg"], s["select_fg"]

    target_bg = surf if cls in _SURFACE_CLASSES else win
    # Background: only if the widget still wears a colour we manage (i.e. it was
    # never given an intentional colour).
    try:
        bg_managed = _norm(w.cget("background")) in _managed_bg
    except tk.TclError:
        return
    if bg_managed:
        _safe(w, background=target_bg)
        _safe(w, highlightbackground=win)

    # Everything below is gated on bg_managed: a widget that keeps a custom
    # background (amber Run, red DECONSTRUCT, an LCD-style field, …) must also
    # keep its own foreground/selection/caret — repainting the fg of a widget
    # whose pale custom bg we left alone gave light-on-light text on dark skins.
    if not bg_managed:
        return

    if cls in _FG_CLASSES:
        try:
            cur_fg = _norm(w.cget("foreground"))
            if cur_fg in _managed_fg:
                _safe(w, foreground=fg)
        except tk.TclError:
            pass

    if cls in ("Text", "Listbox", "Entry", "Spinbox"):
        _safe(w, selectbackground=sel_bg, selectforeground=sel_fg, insertbackground=fg)
    if cls in ("Button", "Menubutton", "Checkbutton", "Radiobutton"):
        # Native hover/press colours would flash light grey on a dark skin.
        _safe(w, activebackground=sel_bg, activeforeground=sel_fg)
    if cls == "Menu":
        _safe(w, activebackground=s["accent"], activeforeground=s["accent_fg"])


def _retint_treeview(tv, s):
    """Re-tint the standard lifecycle row tags on a Treeview so status rows stay
    legible on this skin (leaves any font/other tag options intact — tag_configure
    only touches the options we pass)."""
    tag_fg = s.get("tag_fg", "")
    for name, bg in s["tags"].items():
        try:
            tv.tag_configure(name, background=bg, foreground=tag_fg)
        except tk.TclError:
            pass
