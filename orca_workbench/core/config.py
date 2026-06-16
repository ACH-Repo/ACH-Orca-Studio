"""App-level (not project-level) configuration, persisted in the user's home.

Holds machine/user settings that should survive across projects and sessions —
e.g. the path to the Avogadro executable. Stored as JSON at ~/.orca_workbench.json,
falling back to the pre-rename ~/.orca_studio.json so old settings survive.
"""

import json
import os
from typing import Any, Dict, Optional

# In-memory cache. load_config() is called many times (per tooltip pref, per
# external-program lookup, in the workflow redraw path, …); on the cluster the
# home dir is a network filesystem, so re-reading the file each time added real
# latency. Cache once, invalidate on save (single-process app, so this is safe).
_cache = None  # type: Optional[Dict[str, Any]]


def config_path():
    # type: () -> str
    return os.path.join(os.path.expanduser("~"), ".orca_workbench.json")


def _legacy_config_path():
    # type: () -> str
    # Pre-rebrand location (the app was "ORCA Studio"). Read as a fallback so a
    # user's saved settings (Avogadro path, email, …) survive the rename to ORCA
    # Workbench. The next save_config() writes the new file, after which this
    # legacy file is ignored.
    return os.path.join(os.path.expanduser("~"), ".orca_studio.json")


def load_config():
    # type: () -> Dict[str, Any]
    global _cache
    if _cache is not None:
        return dict(_cache)  # hand out a copy so callers can't mutate the cache
    path = config_path()
    if not os.path.isfile(path):
        legacy = _legacy_config_path()
        if not os.path.isfile(legacy):
            _cache = {}
            return {}
        path = legacy
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (IOError, ValueError):
        data = {}
    _cache = data
    return dict(data)


def save_config(cfg):
    # type: (Dict[str, Any]) -> None
    global _cache
    path = config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        _cache = dict(cfg)
    except IOError:
        pass


def get(key, default=None):
    # type: (str, Any) -> Any
    return load_config().get(key, default)


def set_value(key, value):
    # type: (str, Any) -> None
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
