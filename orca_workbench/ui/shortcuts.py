"""Standard text-editing shortcuts for tk.Text and ttk.Entry widgets.

Tk's default bindings for Ctrl+A / Ctrl+Z / Ctrl+Y in multiline Text widgets
are spotty across platforms — Linux X11 in particular doesn't bind them by
default. This module wraps a widget with the expected behavior so editing
recipes / pasted SMILES / etc. feels normal.
"""

import tkinter as tk
from tkinter import ttk


def install_text_shortcuts(widget):
    # type: (tk.Widget) -> None
    """Bind Ctrl+A (select all), Ctrl+Z (undo), Ctrl+Y / Ctrl+Shift+Z (redo)
    on a tk.Text widget. Idempotent."""
    if isinstance(widget, tk.Text):
        widget.bind("<Control-a>", _text_select_all, add="+")
        widget.bind("<Control-A>", _text_select_all, add="+")
        widget.bind("<Control-z>", _text_undo, add="+")
        widget.bind("<Control-Z>", _text_undo, add="+")
        widget.bind("<Control-y>", _text_redo, add="+")
        widget.bind("<Control-Y>", _text_redo, add="+")
        widget.bind("<Control-Shift-Z>", _text_redo, add="+")
        # Ensure undo is enabled — caller may have forgotten the constructor arg.
        try:
            widget.configure(undo=True)
        except tk.TclError:
            pass


def install_entry_shortcuts(widget):
    # type: (tk.Widget) -> None
    """Bind Ctrl+A (select all entry contents) on ttk.Entry / tk.Entry."""
    if isinstance(widget, (tk.Entry, ttk.Entry)):
        widget.bind("<Control-a>", _entry_select_all, add="+")
        widget.bind("<Control-A>", _entry_select_all, add="+")


def install_global_text_shortcuts(root):
    # type: (tk.Misc) -> None
    """Bind Ctrl+A app-wide for every entry and multiline text widget via Tk
    class bindings, so any field supports select-all (then type to replace) —
    no per-widget wiring needed. Covers ttk.Entry ('TEntry'), classic
    tk.Entry/Spinbox ('Entry'), and tk.Text ('Text'). Returning 'break' stops
    Tk's default Ctrl+A (move-to-line-start) from also firing."""
    for cls in ("TEntry", "Entry", "Spinbox", "TSpinbox"):
        root.bind_class(cls, "<Control-a>", _entry_select_all, add="+")
        root.bind_class(cls, "<Control-A>", _entry_select_all, add="+")
    root.bind_class("Text", "<Control-a>", _text_select_all, add="+")
    root.bind_class("Text", "<Control-A>", _text_select_all, add="+")


def install_tree_shift_select(tree):
    # type: (tk.Widget) -> None
    """Bind Shift+Up / Shift+Down on a ttk.Treeview to grow OR shrink a
    contiguous selection, the way a file list does: an anchor is set where the
    shift-selection begins, and the selection is always the range between that
    anchor and the row the arrows have walked to. Reversing direction therefore
    *deselects* rows back toward the anchor (not just keeps adding). A plain
    Up/Down or a mouse click drops the anchor, so the next shift-select starts
    fresh from wherever the focus then is.

    Rows whose iid starts with '__' (e.g. the Recipes tab's '__divider__' folder
    headers) are non-selectable and skipped. The tree should be
    selectmode='extended'."""
    tree.bind("<Shift-Up>", lambda e: _tree_shift_extend(e, -1), add="+")
    tree.bind("<Shift-Down>", lambda e: _tree_shift_extend(e, +1), add="+")
    # A plain navigation / click ends the current shift-range; clear the anchor so
    # the next Shift+arrow re-anchors at the (new) focus. add="+" + returning None
    # leaves Tk's own selection handling intact.
    for seq in ("<Up>", "<Down>", "<Button-1>"):
        tree.bind(seq, _tree_clear_anchor, add="+")


def _tree_clear_anchor(event):
    try:
        event.widget._shift_anchor = None
    except Exception:
        pass
    return None


def _tree_shift_extend(event, direction):
    tree = event.widget
    # selectable rows only (skip dividers); map iid -> index within them
    items = [i for i in tree.get_children("") if not i.startswith("__")]
    if not items:
        return "break"
    foc = tree.focus()
    sel = [s for s in tree.selection() if not s.startswith("__")]
    lead = foc if foc in items else (sel[-1] if sel else items[0])
    anchor = getattr(tree, "_shift_anchor", None)
    if anchor not in items:
        anchor = lead
        tree._shift_anchor = anchor
    li = items.index(lead)
    ni = li + direction
    if ni < 0 or ni >= len(items):
        return "break"           # already at an end
    ai = items.index(anchor)
    lo, hi = sorted((ai, ni))
    tree.selection_set(items[lo:hi + 1])     # exact range anchor..new lead
    tree.focus(items[ni])
    tree.see(items[ni])
    return "break"


def _text_select_all(event):
    w = event.widget
    w.tag_remove("sel", "1.0", "end")
    w.tag_add("sel", "1.0", "end-1c")
    w.mark_set("insert", "end-1c")
    w.see("insert")
    return "break"


def _text_undo(event):
    try:
        event.widget.edit_undo()
    except tk.TclError:
        pass
    return "break"


def _text_redo(event):
    try:
        event.widget.edit_redo()
    except tk.TclError:
        pass
    return "break"


def _entry_select_all(event):
    w = event.widget
    try:
        w.select_range(0, "end")
        w.icursor("end")
    except tk.TclError:
        pass
    return "break"
