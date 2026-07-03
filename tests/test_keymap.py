"""Tests for the rebindable-hotkey registry (core.keymap).

Pure logic — the config layer is swapped for an in-memory fake so tests don't
touch the real ~/.orca_workbench.json.

Run:  python -m pytest tests/test_keymap.py -q
"""

import pytest

from orca_workbench.core import keymap


class _FakeConfig(object):
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set_value(self, key, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch):
    monkeypatch.setattr(keymap, "_config", _FakeConfig())


def test_defaults_registered():
    ids = keymap.actions()
    assert "app.save_project" in ids and "plot.zoom" in ids
    assert keymap.default_sequence("app.save_project") == "<Control-s>"
    assert keymap.sequence("app.save_project") == "<Control-s>"   # no override yet


def test_override_and_reset_roundtrip():
    keymap.set_override("app.save_project", "<Control-Shift-s>")
    assert keymap.sequence("app.save_project") == "<Control-Shift-s>"
    assert keymap.is_overridden("app.save_project")
    keymap.reset("app.save_project")
    assert not keymap.is_overridden("app.save_project")
    assert keymap.sequence("app.save_project") == "<Control-s>"


def test_setting_default_clears_override():
    # Setting a value equal to the default should NOT persist an override, so
    # package default changes keep flowing.
    keymap.set_override("plot.zoom", keymap.default_sequence("plot.zoom"))
    assert not keymap.is_overridden("plot.zoom")


def test_reset_all():
    keymap.set_override("app.new_project", "<Control-Shift-n>")
    keymap.set_override("plot.pan", "<KeyPress-x>")
    keymap.reset_all()
    assert not keymap.is_overridden("app.new_project")
    assert not keymap.is_overridden("plot.pan")


def test_humanize():
    assert keymap.humanize("<Control-Shift-n>") == "Ctrl+Shift+N"
    assert keymap.humanize("<F5>") == "F5"
    assert keymap.humanize("<KeyPress-f>") == "F"
    assert keymap.humanize("<Control-w>") == "Ctrl+W"
    assert keymap.humanize("") == "(unset)"


def test_event_to_sequence():
    # plain letter (state 0)
    assert keymap.event_to_sequence(0, "g") == "<g>"
    # Ctrl (0x4) + s
    assert keymap.event_to_sequence(0x4, "s") == "<Control-s>"
    # Ctrl+Shift (0x4|0x1) + n  -> letter lower-cased, Shift kept
    assert keymap.event_to_sequence(0x5, "N") == "<Control-Shift-n>"
    # function key
    assert keymap.event_to_sequence(0, "F5") == "<F5>"
    # bare modifier -> None (capture keeps waiting)
    assert keymap.event_to_sequence(0x4, "Control_L") is None


def test_sequence_variants_letter_both_cases():
    v = keymap.sequence_variants("<KeyPress-f>")
    assert "<KeyPress-f>" in v and "<KeyPress-F>" in v
    # a shorthand "<g>" normalises to KeyPress + upper variant
    v2 = keymap.sequence_variants("<g>")
    assert "<KeyPress-g>" in v2 and "<KeyPress-G>" in v2
    # a modified sequence is bound as-is (no case expansion)
    assert keymap.sequence_variants("<Control-s>") == ["<Control-s>"]


def test_conflicts_normalised_within_category():
    # binding plot.redraw to plot.zoom's key should conflict (same category)
    z = keymap.sequence("plot.zoom")
    clash = keymap.conflicts("plot.redraw", z)
    assert "plot.zoom" in clash
    # a shorthand form of the same key still conflicts (normalised compare)
    inner = z.strip("<>").replace("KeyPress-", "")
    clash2 = keymap.conflicts("plot.redraw", "<{}>".format(inner))
    assert "plot.zoom" in clash2
    # cross-category (app vs plot) is allowed even if the raw string matches
    assert keymap.conflicts("app.save_project", "<KeyPress-z>") == []
