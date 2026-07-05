"""Tests for the skin registry (core.theme).

Pure data + config glue — the config layer is swapped for an in-memory fake so
tests never touch the real ~/.orca_workbench.json and don't need a display.

Run:  python -m pytest tests/test_theme.py -q
"""

import pytest

from orca_workbench.core import theme


class _FakeConfig(object):
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set_value(self, key, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch):
    monkeypatch.setattr(theme, "config_mod", _FakeConfig())


def test_default_skin_present_and_first():
    ids = theme.skin_ids()
    assert theme.DEFAULT_SKIN_ID in ids
    assert ids[0] == theme.DEFAULT_SKIN_ID          # default leads the gallery
    # The skins the user asked for exist.
    for sid in ("default", "dark", "aero", "boombox"):
        assert sid in ids


def test_ids_are_unique():
    ids = theme.skin_ids()
    assert len(ids) == len(set(ids))


def test_every_skin_has_all_required_keys():
    for skin in theme.all_skins():
        for key in theme.required_keys():
            assert key in skin, "{} missing key {!r}".format(skin["id"], key)


def test_every_skin_tints_all_tag_names():
    # Re-skinning re-tints every lifecycle row tag used across the tabs, so each
    # skin must cover the full tag catalogue.
    for skin in theme.all_skins():
        for name in theme.TAG_NAMES:
            assert name in skin["tags"], "{} tags missing {!r}".format(skin["id"], name)


def test_default_skin_uses_native_ttk():
    # The default is the untouched native look: no ttk base override.
    assert theme.get_skin("default")["ttk_base"] is None


def test_coloured_skins_use_clam_base():
    for sid in ("dark", "aero", "boombox"):
        assert theme.get_skin(sid)["ttk_base"] == "clam"


def test_get_skin_unknown_falls_back_to_default():
    assert theme.get_skin("nope")["id"] == "default"


def test_active_skin_roundtrip():
    assert theme.active_skin_id() == theme.DEFAULT_SKIN_ID    # nothing saved yet
    theme.set_active_skin_id("aero")
    assert theme.active_skin_id() == "aero"


def test_set_active_ignores_unknown_id():
    theme.set_active_skin_id("dark")
    theme.set_active_skin_id("bogus")                # ignored, not persisted
    assert theme.active_skin_id() == "dark"


def test_active_skin_validates_stale_config():
    # A skin id left by a newer/other build is treated as the default.
    theme.config_mod.set_value(theme.CONFIG_KEY, "some_future_skin")
    assert theme.active_skin_id() == theme.DEFAULT_SKIN_ID


def test_swatch_colors_shape():
    cols = theme.swatch_colors("dark")
    assert len(cols) == 4
    assert all(isinstance(c, str) and c for c in cols)


def test_node_palette_resolves_all_kinds():
    for sid in ("default", "dark", "aero", "boombox"):
        np = theme.node_palette(sid)
        for k in ("body", "fg", "outline", "sel", "wire", "port_geom", "splice"):
            assert k in np and np[k]
        for kind in ("source", "calc", "sink", "gate", "builder", "filter",
                     "transform", "combine", "annotation"):
            assert kind in np["kinds"]
    # dark node body differs from the light default
    assert theme.node_palette("dark")["body"] != theme.node_palette("default")["body"]


def test_json_user_skins_inherit_from_base(tmp_path, monkeypatch):
    import json as _json
    d = tmp_path / "skins"
    d.mkdir()
    (d / "neon.json").write_text(_json.dumps({
        "id": "neon", "label": "Neon", "base": "dark", "accent": "#39ff14",
        "nodes": {"kinds": {"calc": "#113311"}},
    }))
    monkeypatch.setattr(theme, "user_skins_dir", lambda: str(d))
    theme.reload_user_skins()
    try:
        assert "neon" in theme.skin_ids()
        s = theme.get_skin("neon")
        assert s["accent"] == "#39ff14"          # overridden
        assert s["window"] == theme.get_skin("dark")["window"]  # inherited from dark
        np = theme.node_palette("neon")
        assert np["kinds"]["calc"] == "#113311"  # node override
        assert np["body"] == theme.node_palette("dark")["body"]  # inherited dark nodes
    finally:
        theme.reload_user_skins()   # don't leak the temp skins into other tests


def test_write_skin_template_roundtrips(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    theme.write_skin_template(str(p), base_id="aero")
    import json as _json
    data = _json.loads(p.read_text())
    assert data["base"] == "aero" and "window" in data and "nodes" in data
