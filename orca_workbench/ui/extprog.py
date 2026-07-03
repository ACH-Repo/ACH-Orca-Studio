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


# Abstract external-program slots, shown as one "External programs" settings dialog
# (so the menu doesn't grow a line per program). Each slot resolves through a
# fallback chain so a single 3D program serves both view and edit until the user
# sets them apart, and legacy keys (avogadro_path / structure_editor_path) keep
# working. `label` is what the user sees — deliberately abstract (not a product
# name), since the path is exactly where future integrations (JMol, PyMOL, Marvin…)
# get plugged in.
PROGRAM_SLOTS = [
    {"key": "viewer_3d_path", "label": "3D viewer (view only)",
     "desc": "Opens a molecule's geometry read-only — double-click a molecule row, or "
             "right-click > View geometry. e.g. Avogadro, JMol, PyMOL."},
    {"key": "editor_3d_path", "label": "3D editor (geometry round-trip)",
     "desc": "Opens a molecule's .xyz to EDIT the geometry; on save the app reloads it "
             "(Molecules tab, right-click > Edit geometry). e.g. Avogadro."},
    {"key": "editor_2d_path", "label": "2D editor (structure / SMILES round-trip)",
     "desc": "Opens the 2D structure to edit; the app reads the SMILES back (Molecules tab: "
             "Edit structure, or double-click the depiction). e.g. ChemDraw, Marvin."},
    {"key": "text_editor_path", "label": "Text editor (recipe JSON)",
     "desc": "Opens a recipe's JSON on double-click. A GUI editor (Notepad++, Sublime, gedit)."},
]

# Effective-path fallbacks: an unset 3D view/edit path borrows the other one (and the
# legacy avogadro_path); the 2D editor borrows the legacy structure_editor_path.
_PATH_FALLBACKS = {
    "viewer_3d_path": ("viewer_3d_path", "avogadro_path", "editor_3d_path"),
    "editor_3d_path": ("editor_3d_path", "avogadro_path", "viewer_3d_path"),
    "editor_2d_path": ("editor_2d_path", "structure_editor_path"),
    "text_editor_path": ("text_editor_path",),
}


def program_path(key):
    # type: (str) -> str
    """Effective configured path for a program slot, following the fallback chain
    (so view/edit 3D default to the same program and legacy keys still resolve).
    Empty string if nothing is set anywhere in the chain."""
    for k in _PATH_FALLBACKS.get(key, (key,)):
        v = config_mod.get(k, "") or ""
        if v:
            return v
    return ""


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


class ExternalProgramsDialog(tk.Toplevel):
    """One dialog listing every abstract program slot (PROGRAM_SLOTS) with a path +
    Browse + Clear each — so Settings doesn't grow a line per program, and the labels
    stay product-neutral. Blank = use the default (the fallback chain)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("External programs")
        self.resizable(False, False)
        ttk.Label(self, text="Set the programs the app launches. Leave a box blank to use the "
                  "default — the 3D viewer and 3D editor share one program until you set them "
                  "apart. (The ORCA executable is set separately.)",
                  justify=tk.LEFT, wraplength=560).pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 8))

        self._vars = {}
        grid = ttk.Frame(self)
        grid.pack(side=tk.TOP, fill=tk.X, padx=12)
        grid.columnconfigure(1, weight=1)
        for r, slot in enumerate(PROGRAM_SLOTS):
            key = slot["key"]
            lbl = ttk.Label(grid, text=slot["label"] + ":")
            lbl.grid(row=r * 2, column=0, sticky=tk.W, pady=(8, 0))
            var = tk.StringVar(value=config_mod.get(key, "") or "")
            self._vars[key] = var
            ent = ttk.Entry(grid, textvariable=var, width=52)
            ent.grid(row=r * 2, column=1, sticky=tk.EW, padx=6, pady=(8, 0))
            ttk.Button(grid, text="Browse...", width=9,
                       command=lambda v=var: self._browse(v)).grid(row=r * 2, column=2, pady=(8, 0))
            ttk.Button(grid, text="Clear", width=6,
                       command=lambda v=var: v.set("")).grid(row=r * 2, column=3, padx=(4, 0), pady=(8, 0))
            ttk.Label(grid, text=slot["desc"], foreground="#666", justify=tk.LEFT,
                      wraplength=620).grid(row=r * 2 + 1, column=1, columnspan=3, sticky=tk.W)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.RIGHT, padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        make_modal(self, parent)
        self.wait_window()

    def _browse(self, var):
        path = filedialog.askopenfilename(title="Locate the program", parent=self)
        if path:
            var.set(path)

    def _save(self):
        for key, var in self._vars.items():
            config_mod.set_value(key, var.get().strip())
        self.destroy()


def edit_external_programs(parent):
    # type: (tk.Misc) -> None
    ExternalProgramsDialog(parent)


def open_with(parent, config_key, file_path, friendly, description, extra_args=None):
    # type: (tk.Misc, str, str, str, str, Optional[List[str]]) -> None
    """Open `file_path` with the program stored under `config_key`. Validates,
    prompts if unset/stale, and never crashes on a bad path — on failure it
    offers to re-set the path."""
    exe = program_path(config_key)   # follows the fallback chain for the abstract slots
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
