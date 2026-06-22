"""Robust launching of, and configuration for, external programs.

Paths to external tools (Avogadro, a text editor, ORCA, …) are stored per-user
in ~/.orca_workbench.json. They can go stale — the user uninstalls or moves the
program — so every launch here:

  1. validates the stored path first (file exists, or resolves on PATH),
  2. prompts for a new one if it's missing/invalid,
  3. wraps the actual launch in try/except, and
  4. on failure, offers to re-set the path instead of crashing.

The same prompt is reused by the Settings menu so paths can be set explicitly.
"""

import os
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from orca_workbench.core import config as config_mod
from orca_workbench.ui.modal import make_modal


def _is_usable(path):
    # type: (str) -> bool
    if not path:
        return False
    return os.path.isfile(path) or (shutil.which(path) is not None)


class ProgramPathDialog(tk.Toplevel):
    """Prompt for a program path/command and store it in config. self.result is
    the chosen path, or None if cancelled."""

    def __init__(self, parent, config_key, title, description):
        super().__init__(parent)
        self.result = None
        self._config_key = config_key
        self.title(title)
        self.resizable(False, False)

        ttk.Label(self, text=description, justify=tk.LEFT, wraplength=460).pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))
        row = ttk.Frame(self)
        row.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        self.var = tk.StringVar(value=config_mod.get(config_key, "") or "")
        entry = ttk.Entry(row, textvariable=self.var, width=46)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row, text="Browse...", command=self._browse).pack(side=tk.LEFT)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save", command=self._ok).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=4)

        entry.focus_set()
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        make_modal(self, parent)
        self.wait_window()

    def _browse(self):
        path = filedialog.askopenfilename(title="Locate the program", parent=self)
        if path:
            self.var.set(path)

    def _ok(self):
        path = self.var.get().strip()
        if not path:
            messagebox.showinfo("Empty", "Enter a path/command, Clear, or Cancel.", parent=self)
            return
        if not _is_usable(path):
            if not messagebox.askyesno(
                "Not found",
                "'{}' isn't a file and isn't on your PATH. Save it anyway?".format(path),
                parent=self):
                return
        config_mod.set_value(self._config_key, path)
        self.result = path
        self.destroy()

    def _clear(self):
        config_mod.set_value(self._config_key, "")
        self.result = None
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


def prompt_for_program(parent, config_key, title, description):
    # type: (tk.Misc, str, str, str) -> Optional[str]
    """Show the path dialog; returns the chosen path or None. Stores in config."""
    return ProgramPathDialog(parent, config_key, title, description).result


def open_with(parent, config_key, file_path, friendly, description, extra_args=None):
    # type: (tk.Misc, str, str, str, str, Optional[List[str]]) -> None
    """Open `file_path` with the program stored under `config_key`. Validates,
    prompts if unset/stale, and never crashes on a bad path — on failure it
    offers to re-set the path."""
    exe = config_mod.get(config_key, "")
    if not _is_usable(exe):
        exe = prompt_for_program(parent, config_key,
                                 "Set {}".format(friendly), description)
        if not exe:
            return
    args = [exe] + list(extra_args or []) + [file_path]
    try:
        subprocess.Popen(args)
        return
    except Exception as e:
        if messagebox.askyesno(
                "Couldn't launch {}".format(friendly),
                "Failed to start {}:\n  {}\n\n{}\n\nPick a different program?".format(
                    friendly, exe, e),
                parent=parent):
            exe = prompt_for_program(parent, config_key,
                                     "Set {}".format(friendly), description)
            if not exe:
                return
            try:
                subprocess.Popen([exe] + list(extra_args or []) + [file_path])
            except Exception as e2:
                messagebox.showerror("Launch failed", str(e2), parent=parent)
