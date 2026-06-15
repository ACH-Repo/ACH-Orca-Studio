"""App-level (not project-level) configuration, persisted in the user's home.

Holds machine/user settings that should survive across projects and sessions —
e.g. the path to the Avogadro executable. Stored as JSON at ~/.orca_workbench.json,
falling back to the pre-rename ~/.orca_studio.json so old settings survive.
"""

import json
import os
from typing import Any, Dict


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
    path = config_path()
    if not os.path.isfile(path):
        legacy = _legacy_config_path()
        if not os.path.isfile(legacy):
            return {}
        path = legacy
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (IOError, ValueError):
        return {}


def save_config(cfg):
    # type: (Dict[str, Any]) -> None
    path = config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
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
