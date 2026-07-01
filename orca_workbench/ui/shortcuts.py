"""Standard text-editing shortcuts for tk.Text and ttk.Entry widgets.

Tk's default bindings for Ctrl+A / Ctrl+Z / Ctrl+Y and word-wise cursor motion
are spotty across platforms — Linux X11 in particular doesn't bind them, and
ttk.Entry has no undo at all. This module wraps the whole app (via Tk *class*
bindings) so editing recipes / pasted SMILES / molecule fields feels normal:

  * Ctrl+A            select all
  * Ctrl+Z / Ctrl+Y  undo / redo (native for Text; a small stack for Entry)
  * Ctrl+Left/Right         move by word (stops at word<->delimiter boundaries)
  * Ctrl+Shift+Left/Right   extend the selection by word

`install_global_text_shortcuts(root)` wires all of it once, app-wide.
"""

import tkinter as tk
from tkinter import ttk


# A "word" is a maximal run of these; every other char (space and punctuation
# like []().,;:!?/\"'+-=*&|<>@# ...) is a delimiter you stop at — the Notepad++ /
# VS Code convention.
_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_CONTROL_MASK = 0x0004     # Control modifier bit in Tk event.state (X11 + Windows)


def is_word_char(ch):
    # type: (str) -> bool
    return ch in _WORD_CHARS


def next_word_boundary(text, pos):
    # type: (str, int) -> int
    """Index just past the current run (word OR delimiter) to the RIGHT of `pos`,
    so successive calls stop at every word<->delimiter transition. len(text) at
    the end."""
    n = len(text)
    if pos >= n:
        return n
    word = is_word_char(text[pos])
    i = pos
    while i < n and is_word_char(text[i]) == word:
        i += 1
    return i


def prev_word_boundary(text, pos):
    # type: (str, int) -> int
    """Index at the start of the current run (word OR delimiter) to the LEFT of
    `pos`. 0 at the start."""
    if pos <= 0:
        return 0
    word = is_word_char(text[pos - 1])
    i = pos
    while i > 0 and is_word_char(text[i - 1]) == word:
        i -= 1
    return i


# --------------------------------------------------------------- install hooks

def install_text_shortcuts(widget):
    # type: (tk.Widget) -> None
    """Enable native undo on a specific tk.Text. Key bindings themselves come from
    the app-wide class bindings (install_global_text_shortcuts), so this only makes
    sure the widget records an undo history. Idempotent; kept for existing callers."""
    if isinstance(widget, tk.Text):
        try:
            widget.configure(undo=True, autoseparators=True, maxundo=-1)
        except tk.TclError:
            pass


def install_entry_shortcuts(widget):
    # type: (tk.Widget) -> None
    """No-op kept for compatibility — Entry shortcuts are now class-wide."""
    return


def install_global_text_shortcuts(root):
    # type: (tk.Misc) -> None
    """Bind editing shortcuts app-wide via Tk class bindings, so every field gets
    select-all, undo/redo and word-wise motion/selection with no per-widget wiring.
    Covers ttk.Entry ('TEntry'), classic Entry/Spinbox, and tk.Text. Handlers
    return 'break' to suppress Tk's conflicting defaults (e.g. Ctrl+A = home)."""
    entry_classes = ("TEntry", "Entry", "Spinbox", "TSpinbox")
    for cls in entry_classes:
        root.bind_class(cls, "<Control-a>", _entry_select_all, add="+")
        root.bind_class(cls, "<Control-A>", _entry_select_all, add="+")
        root.bind_class(cls, "<FocusIn>", _entry_focus_baseline, add="+")
        root.bind_class(cls, "<KeyRelease>", _entry_record, add="+")
        root.bind_class(cls, "<Control-z>", _entry_undo, add="+")
        root.bind_class(cls, "<Control-Z>", _entry_undo, add="+")
        root.bind_class(cls, "<Control-y>", _entry_redo, add="+")
        root.bind_class(cls, "<Control-Y>", _entry_redo, add="+")
        root.bind_class(cls, "<Control-Shift-Z>", _entry_redo, add="+")
        root.bind_class(cls, "<Control-Left>", lambda e: _entry_word_move(e, -1), add="+")
        root.bind_class(cls, "<Control-Right>", lambda e: _entry_word_move(e, +1), add="+")
        root.bind_class(cls, "<Control-Shift-Left>", lambda e: _entry_word_select(e, -1), add="+")
        root.bind_class(cls, "<Control-Shift-Right>", lambda e: _entry_word_select(e, +1), add="+")

    root.bind_class("Text", "<Control-a>", _text_select_all, add="+")
    root.bind_class("Text", "<Control-A>", _text_select_all, add="+")
    root.bind_class("Text", "<FocusIn>", _enable_text_undo, add="+")
    root.bind_class("Text", "<Control-z>", _text_undo, add="+")
    root.bind_class("Text", "<Control-Z>", _text_undo, add="+")
    root.bind_class("Text", "<Control-y>", _text_redo, add="+")
    root.bind_class("Text", "<Control-Y>", _text_redo, add="+")
    root.bind_class("Text", "<Control-Shift-Z>", _text_redo, add="+")
    root.bind_class("Text", "<Control-Left>", lambda e: _text_word_move(e, -1), add="+")
    root.bind_class("Text", "<Control-Right>", lambda e: _text_word_move(e, +1), add="+")
    root.bind_class("Text", "<Control-Shift-Left>", lambda e: _text_word_select(e, -1), add="+")
    root.bind_class("Text", "<Control-Shift-Right>", lambda e: _text_word_select(e, +1), add="+")


# ------------------------------------------------------------- Entry undo/redo

def _entry_state(w):
    st = getattr(w, "_undo_state", None)
    if st is None:
        st = w._undo_state = {"undo": [], "redo": [], "open_word": False}
    return st


def _entry_focus_baseline(event):
    """Seed the undo history with the value present when the field gains focus, and
    break any in-progress typing run so the next keystroke starts a fresh step."""
    w = event.widget
    st = _entry_state(w)
    if not st["undo"]:
        try:
            st["undo"].append((w.get(), w.index("insert")))
        except tk.TclError:
            pass
    st["open_word"] = False


def _entry_record(event):
    """After a text-changing keystroke, snapshot the field. Runs of typed word
    characters coalesce into ONE undo step (word-level undo); anything else starts
    a new step. Skips Control-held keys (those are shortcuts, not edits)."""
    w = event.widget
    if event.state & _CONTROL_MASK:
        return
    try:
        val = w.get()
    except tk.TclError:
        return
    st = _entry_state(w)
    if not st["undo"]:
        st["undo"].append((val, 0))
    if st["undo"][-1][0] == val:
        return                                   # nothing changed
    try:
        cur = w.index("insert")
    except tk.TclError:
        cur = len(val)
    ch = event.char
    grew_by_one = len(val) == len(st["undo"][-1][0]) + 1
    if st["open_word"] and grew_by_one and len(ch) == 1 and is_word_char(ch):
        st["undo"][-1] = (val, cur)              # coalesce into the current word
    else:
        st["undo"].append((val, cur))
        if len(st["undo"]) > 300:
            st["undo"].pop(0)
    st["open_word"] = (len(ch) == 1 and is_word_char(ch))
    st["redo"] = []


def _entry_apply(w, val, pos):
    try:
        w.delete(0, "end")
        w.insert(0, val)
        w.icursor(pos)
    except tk.TclError:
        pass


def _entry_undo(event):
    w = event.widget
    st = _entry_state(w)
    if len(st["undo"]) < 2:
        return "break"
    st["redo"].append(st["undo"].pop())
    val, pos = st["undo"][-1]
    _entry_apply(w, val, pos)
    st["open_word"] = False
    return "break"


def _entry_redo(event):
    w = event.widget
    st = _entry_state(w)
    if not st["redo"]:
        return "break"
    val, pos = st["redo"].pop()
    st["undo"].append((val, pos))
    _entry_apply(w, val, pos)
    st["open_word"] = False
    return "break"


# ------------------------------------------------------- Entry word navigation

def _entry_word_move(event, direction):
    w = event.widget
    try:
        s, cur = w.get(), w.index("insert")
    except tk.TclError:
        return "break"
    new = next_word_boundary(s, cur) if direction > 0 else prev_word_boundary(s, cur)
    try:
        w.selection_clear()
    except tk.TclError:
        pass
    w._sel_anchor = None
    w.icursor(new)
    return "break"


def _entry_word_select(event, direction):
    w = event.widget
    try:
        s, cur = w.get(), w.index("insert")
    except tk.TclError:
        return "break"
    new = next_word_boundary(s, cur) if direction > 0 else prev_word_boundary(s, cur)
    if not w.selection_present():
        w._sel_anchor = cur
    anchor = getattr(w, "_sel_anchor", None)
    if anchor is None:
        anchor = cur
        w._sel_anchor = anchor
    lo, hi = sorted((anchor, new))
    try:
        if lo == hi:
            w.selection_clear()
        else:
            w.selection_range(lo, hi)
    except tk.TclError:
        pass
    w.icursor(new)
    return "break"


# -------------------------------------------------------- Text undo + word nav

def _enable_text_undo(event):
    try:
        event.widget.configure(undo=True, autoseparators=True, maxundo=-1)
    except tk.TclError:
        pass


def _text_word_index(w, direction):
    """Text index one word-boundary from 'insert' in `direction`, rolling onto the
    neighbouring line at a line edge."""
    line, col = (int(p) for p in w.index("insert").split("."))
    linetext = w.get("%d.0" % line, "%d.end" % line)
    if direction > 0:
        newcol = next_word_boundary(linetext, col)
        if newcol == col:                        # at line end -> next line start
            nxt = w.index("insert +1line linestart")
            return nxt if w.compare(nxt, "!=", "insert") else "end-1c"
        return "%d.%d" % (line, newcol)
    newcol = prev_word_boundary(linetext, col)
    if newcol == col:                            # at line start -> prev line end
        if line > 1:
            return "%d.end" % (line - 1)
        return "1.0"
    return "%d.%d" % (line, newcol)


def _idx_tuple(w, idx):
    return tuple(int(p) for p in w.index(idx).split("."))


def _text_word_move(event, direction):
    w = event.widget
    new = _text_word_index(w, direction)
    w.tag_remove("sel", "1.0", "end")
    w._sel_anchor = None
    w.mark_set("insert", new)
    w.see("insert")
    return "break"


def _text_word_select(event, direction):
    w = event.widget
    new = _text_word_index(w, direction)
    if not w.tag_ranges("sel") or getattr(w, "_sel_anchor", None) is None:
        w._sel_anchor = w.index("insert")
    anchor = w._sel_anchor
    lo, hi = sorted((anchor, new), key=lambda idx: _idx_tuple(w, idx))
    w.tag_remove("sel", "1.0", "end")
    if _idx_tuple(w, lo) != _idx_tuple(w, hi):
        w.tag_add("sel", lo, hi)
    w.mark_set("insert", new)
    w.see("insert")
    return "break"


# ------------------------------------------------------------- tree shift-select

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
        return "break"
    ai = items.index(anchor)
    lo, hi = sorted((ai, ni))
    tree.selection_set(items[lo:hi + 1])
    tree.focus(items[ni])
    tree.see(items[ni])
    return "break"


# ------------------------------------------------------------------- primitives

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
