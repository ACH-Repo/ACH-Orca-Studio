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
