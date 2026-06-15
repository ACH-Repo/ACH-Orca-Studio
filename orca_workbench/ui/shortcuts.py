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
