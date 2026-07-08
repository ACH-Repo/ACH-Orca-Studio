"""Tests for the word-boundary logic behind Ctrl(+Shift)+Left/Right navigation.

Pure string logic (no Tk). A 'word' is [A-Za-z0-9_]; every other char is a
delimiter you stop at (Notepad++ / VS Code convention).

Run:  python -m pytest tests/test_shortcuts.py -q
"""

from orca_workbench.ui.shortcuts import (
    is_word_char, next_word_boundary, prev_word_boundary)


def test_is_word_char():
    assert is_word_char("a") and is_word_char("Z") and is_word_char("7") and is_word_char("_")
    for ch in "[](){}.,;:!?/\\\"'+-=*&|<>@# ":
        assert not is_word_char(ch)


def test_next_stops_at_each_word_delimiter_transition():
    s = "foo.bar"
    assert next_word_boundary(s, 0) == 3     # end of "foo"
    assert next_word_boundary(s, 3) == 4     # end of "."
    assert next_word_boundary(s, 4) == 7     # end of "bar"
    assert next_word_boundary(s, 7) == 7     # already at end


def test_prev_mirrors_next():
    s = "foo.bar"
    assert prev_word_boundary(s, 7) == 4     # start of "bar"
    assert prev_word_boundary(s, 4) == 3     # start of "."
    assert prev_word_boundary(s, 3) == 0     # start of "foo"
    assert prev_word_boundary(s, 0) == 0


def test_brackets_stop_but_adjacent_delimiters_group():
    # A delimiter isolated by words is its own stop; a run of adjacent delimiters
    # (here "](") groups into one — the two-class (word vs delimiter) convention.
    s = "a[b](c)"   # a | [ | b | ]( | c | )
    stops = []
    i = 0
    while True:
        j = next_word_boundary(s, i)
        if j == i:
            break
        stops.append(j)
        i = j
    assert stops == [1, 2, 3, 5, 6, 7]


def test_whitespace_runs_group():
    s = "foo   bar"
    assert next_word_boundary(s, 0) == 3     # "foo"
    assert next_word_boundary(s, 3) == 6     # the 3 spaces as one run
    assert next_word_boundary(s, 6) == 9     # "bar"


def test_edges_and_empty():
    assert next_word_boundary("", 0) == 0
    assert prev_word_boundary("", 0) == 0
    assert next_word_boundary("ab", 5) == 2   # pos past end clamps to len(text)


# --- Regression: entry class-bindings must survive a string event.widget -------
# On the LiDO3 gateway's conda Tk, event.widget can arrive as the widget PATH
# STRING (Tkinter's nametowidget lookup KeyError'd for an untracked ttk-internal
# widget). The handlers used to do w.get()/w._undo_state and crash with
# "'str' object has no attribute ...". They must now bail instead.

class _StrEvent:
    def __init__(self, widget=".!frame.!ghost", state=0, char="a"):
        self.widget = widget
        self.state = state
        self.char = char


def test_entry_handlers_survive_string_widget():
    from orca_workbench.ui import shortcuts as sc
    # _ROOT unset (or unresolvable path) -> every handler returns without raising.
    ev = _StrEvent()
    sc._entry_focus_baseline(ev)
    sc._entry_record(ev)
    sc._entry_undo(ev)
    sc._entry_redo(ev)
    sc._entry_select_all(ev)
    sc._entry_word_move(ev, 1)
    sc._entry_word_select(ev, -1)
    sc._entry_see_after(ev)
    assert sc._entry_widget(_StrEvent("no.such.path")) is None


def test_entry_widget_passes_through_real_widget():
    from orca_workbench.ui import shortcuts as sc
    obj = object()
    assert sc._entry_widget(_StrEvent(widget=obj)) is obj   # non-str -> as-is
