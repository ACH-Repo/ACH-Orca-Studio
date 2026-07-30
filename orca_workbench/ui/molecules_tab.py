"""Molecules tab — add/edit molecules, generate 3D coordinates from SMILES."""

import os
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from orca_workbench.core import config as config_mod
from orca_workbench.core import coords as coords_mod
from orca_workbench.core import resolve as resolve_mod
from orca_workbench.core import roundtrip as roundtrip_mod
from orca_workbench.ui import extprog as extprog_mod
from orca_workbench.core.project import Molecule
from orca_workbench.ui.depict import smiles_to_photoimage
from orca_workbench.ui.modal import fit_to_content, make_modal
from orca_workbench.ui.shortcuts import install_text_shortcuts, install_tree_shift_select
from orca_workbench.ui.tooltip import tip


def imported_stem(base, idx, multi):
    # type: (str, int, bool) -> str
    """Filename stem for an imported structure. A multi-record source encodes the
    conformer index (genuine identity); a single-structure source keeps the clean
    basename. (Any residual collision is then handled by the _2/_3 deduper.)"""
    return "{}_conf{}".format(base, idx) if multi else base


def imported_comment(base_name, idx, multi):
    # type: (str, int, bool) -> str
    """Provenance note stamped into an imported molecule's Comment (and .xyz
    metadata) so it's traceable back to its source file even when the filename is
    a deduped basename."""
    suffix = " (conformer {})".format(idx) if multi else ""
    return "imported from {}{}".format(base_name, suffix)


class MoleculesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._focus_filename = None  # type: Optional[str]
        self._draft = None  # type: Optional[Molecule]
        self._suppress_field_writes = False
        self._suppress_select_events = False
        # When the user has manually touched these in the draft, we stop
        # auto-overwriting them from the SMILES on subsequent SMILES edits.
        self._user_touched_charge = False
        self._user_touched_mult = False
        # Filename of a freshly-added row that still auto-fills charge/mult from its
        # SMILES (like a draft does), until the user navigates away. Lets "Add" make
        # a real, selected row while keeping the draft-mode SMILES auto-fill.
        self._autofill_row = None  # type: Optional[str]
        # Drag-to-reorder state (see _drag_start/_drag_motion/_drag_release).
        self._drag_item = None  # type: Optional[str]
        self._drag_moved = False
        self._drag_y0 = 0
        # Molecules already warned (this session) that a geometry edit may have staled
        # their SMILES — so iterative reloads don't nag each time.
        self._geom_edit_warned = set()
        # Depiction cache {smiles: PhotoImage} so switching rows is instant.
        self._depict_cache = {}
        self._depict_image = None  # current displayed image (kept from GC)
        self._prerender_queue = []
        self._prerendering = False
        self._build()
        self._enter_drafting_mode()

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        b_add = ttk.Button(toolbar, text="Add", command=self.on_add)
        b_remove = ttk.Button(toolbar, text="Remove", command=self.on_remove)
        b_gen = ttk.Button(toolbar, text="Generate XYZ", command=self.on_generate)
        b_gen_all = ttk.Button(toolbar, text="Generate Pending", command=self.on_generate_all)
        b_import = ttk.Button(toolbar, text="Import files...", command=self.on_import_files)
        b_import_dir = ttk.Button(toolbar, text="Import folder...", command=self.on_import_folder)
        b_paste = ttk.Button(toolbar, text="Paste SMILES...", command=self.on_paste_smiles)
        b_name = ttk.Button(toolbar, text="Add by name...", command=self.on_add_by_name)
        for b in (b_add, b_remove, b_gen, b_gen_all, b_import, b_import_dir, b_paste, b_name):
            b.pack(side=tk.LEFT, padx=2)
        tip(b_name, "Look up a molecule by chemical name (IUPAC or common), CAS number, "
                    "InChI, or SMILES via public web services (OPSIN + PubChem), preview the "
                    "2D structure, and add it. Needs internet; with none, only SMILES/InChI "
                    "work. The structure's source is recorded in the molecule's comment.\n\n"
                    "Shortcut: Ctrl+Shift+N (from anywhere in the app).")
        tip(b_paste, "Open a dialog showing what's currently in your clipboard parsed as a "
                     "list of SMILES. You can edit before committing. Same effect as Ctrl+V "
                     "while hovering the molecule list.\n\n"
                     "Accepts: dot-separated single line (ChemDraw multi-mol copy), one SMILES "
                     "per line, or two-column SMILES + name. Auto-detects which column is "
                     "which via RDKit. Charge & multiplicity auto-filled per molecule.")
        tip(b_add, "Create a new molecule row immediately and select it for editing (the Name "
                   "field is focused so you can type straight away). Each press adds one row. If "
                   "you'd already typed into the form without a row selected, that becomes the "
                   "new row; otherwise a blank one is added. Edit its fields on the right, then "
                   "Generate XYZ (or press Add again for the next molecule).")
        tip(b_remove, "Remove the selected molecule(s) from the project — works on a single row "
                      "or a bulk selection. Also removes any planned calculations that referenced "
                      "them. Doesn't delete the .xyz files from disk.\n\n"
                      "Keyboard shortcut: Delete (with the molecule list focused).")
        tip(b_gen, "Generate 3D coordinates from SMILES via RDKit (ETKDGv3 + MMFF), falling back "
                   "to OpenBabel/UFF. Writes XYZ_INI/<filename>.xyz with metadata in the comment "
                   "line. Works on either the selected molecule(s) — bulk-generates if multiple "
                   "are selected — or the current draft (auto-commits it first).\n\n"
                   "Keyboard shortcut: Ctrl+Enter (with the molecule list focused).")
        tip(b_gen_all, "Generate XYZ for every molecule with status 'pending' (i.e. has a SMILES "
                       "but no XYZ yet, or was just invalidated by a SMILES edit). Independent "
                       "of selection — operates across the whole project. Failed rows are NOT "
                       "retried by this button; use Ctrl+Enter on a failed selection to retry.")
        tip(b_import, "Import one or many existing structure files as molecules. Multi-select in "
                      "the dialog (Ctrl+A = all, Shift-click = range, Ctrl-click = add one). "
                      "Reads .xyz natively (incl. JSON metadata) and converts SDF/MOL/MOL2/PDB/"
                      "CIF/etc. to .xyz via OpenBabel/RDKit. Each is copied into XYZ_INI/ and "
                      "locked to its original coordinates (no SMILES regeneration). "
                      "Multi-structure files (e.g. multi-conformer SDF) let you pick one, "
                      "several (0,2,5 / 0-3), or all.\n\n"
                      "SMILES-list files (.smi/.smiles/.csv with SMILES, optionally a name "
                      "column) are added instead as pending molecules to Generate.\n\n"
                      "Shortcut: Ctrl+Shift+O.")
        tip(b_import_dir, "Import every supported structure file in a chosen folder (e.g. a folder "
                          "full of .xyz or .sdf), in one go. Same conversion + conformer handling "
                          "as Import files.")

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=2)

        # Status column leftmost so it's never hidden by horizontal squeeze.
        # Narrow columns are fixed-width (stretch=False); name and smiles
        # absorb whatever horizontal space is left, so resizing the window
        # widens the readable fields instead of squeezing the labels.
        columns = ("status", "name", "filename", "smiles", "charge", "mult", "lock")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        col_specs = [
            # (id, label, width, minwidth, stretch)
            ("status",   "Status",   80,  60,  False),
            ("name",     "Name",     180, 80,  True),
            ("filename", "Filename", 80,  60,  False),
            ("smiles",   "SMILES",   280, 100, True),
            ("charge",   "Q",        35,  30,  False),
            ("mult",     "M",        35,  30,  False),
            ("lock",     "Lock",     45,  40,  False),
        ]
        self._lock_col_id = "#{}".format(len(columns))   # display id of the Lock column
        self._lock_painting = False
        self._lock_paint = False
        self._col_labels = {c: lbl for c, lbl, _w, _mw, _s in col_specs}
        for col, label, width, minw, stretch in col_specs:
            self.tree.heading(col, text=label,
                              command=lambda c=col: self._on_header_click(c))
            self.tree.column(col, width=width, minwidth=minw, anchor=tk.W, stretch=stretch)
        # Sort state: None = insertion order. Generated molecules group on top.
        self._sort_col = None
        self._sort_desc = False
        # Row coloring by gen_status (set via per-row tags below in refresh()).
        # ok = no background; pending = pale yellow; failed = pale red.
        self.tree.tag_configure("pending", background="#fffde7")  # pale yellow
        self.tree.tag_configure("failed", background="#ffebee")   # pale red
        self.tree.tag_configure("ok", background="")
        self.tree.tag_configure("locked", background="#dcdcdc")   # grey = user-locked
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        # Scrollbar before the tree: a wide table packed first would leave the bar
        # zero-width (wheel-scrollable but not draggable).
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # Keyboard shortcuts active while the tree has focus (auto-focused on
        # mouse-enter via the <Enter> binding below):
        #   Ctrl+V       — paste SMILES list from clipboard
        #   Ctrl+A       — select all rows
        #   Delete       — remove selected (single or bulk)
        #   Ctrl+Enter   — generate XYZ for selected (single or bulk)
        self.tree.bind("<Control-v>", lambda e: self.on_paste_smiles())
        self.tree.bind("<Control-V>", lambda e: self.on_paste_smiles())
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        self.tree.bind("<Delete>", lambda e: (self.on_remove(), "break")[1])
        self.tree.bind("<Control-Return>", lambda e: (self.on_generate(), "break")[1])
        self.tree.bind("<Control-KP_Enter>", lambda e: (self.on_generate(), "break")[1])
        install_tree_shift_select(self.tree)
        # Lock column: Ctrl+L toggles lock on the selection; click or drag the Lock
        # cell to flip it (drag-paint, like the Report tab). Locked rows grey out and
        # are protected from removal.
        self.tree.bind("<Control-l>", self._toggle_lock_selected, add="+")
        self.tree.bind("<Control-L>", self._toggle_lock_selected, add="+")
        self.tree.bind("<Button-1>", self._lock_press, add="+")
        self.tree.bind("<B1-Motion>", self._lock_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._lock_release, add="+")
        # Drag a row up/down to reorder; on drop the filenames renumber 000.. top to
        # bottom (unless calcs are already built against them — see _renumber_molecules).
        # Alt+Up/Down do the same by keyboard (reliable everywhere, incl. remote/ThinLinc).
        self.tree.bind("<Button-1>", self._drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._drag_release, add="+")
        self.tree.bind("<Alt-Up>", lambda e: self._move_focused(-1), add="+")
        self.tree.bind("<Alt-Down>", lambda e: self._move_focused(+1), add="+")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_row_right_click)
        self.tree.bind("<Enter>", lambda e: self.tree.focus_set(), add="+")
        tip(self.tree, "Molecules in this project.\n\n"
                       "Row colors (status column shows the same info):\n"
                       "  white = XYZ generated and ready\n"
                       "  pale yellow = pending — needs Generate XYZ before this can be exported\n"
                       "  pale red = generation failed — click the row to see the error in the preview\n\n"
                       "Selection:\n"
                       "  click — select one row (form on the right shows it)\n"
                       "  Ctrl+click — toggle individual rows in/out of selection\n"
                       "  Shift+click — extend selection to a range\n"
                       "  Ctrl+A — select all\n\n"
                       "Keyboard (hover to focus the list, then):\n"
                       "  Delete — remove selected rows\n"
                       "  Ctrl+Enter — generate XYZ for selected rows\n"
                       "  Ctrl+V — paste SMILES list (or an XYZ file string) from clipboard\n"
                       "  Double-click — view the molecule (opens local Avogadro if available,\n"
                       "                 else shows the .xyz path to open via MobaXterm)\n\n"
                       "Reorder rows (renumbers filenames 000, 001, ... top to bottom, the\n"
                       "fixed naming convention):\n"
                       "  drag a row up or down, or\n"
                       "  Alt+Up / Alt+Down to move the selected row (reliable over ThinLinc).\n"
                       "Clear any column sort first — reordering only applies to insertion order.\n\n"
                       "With multiple rows selected, the edit form locks and Remove / "
                       "Generate XYZ act on the whole group. Click a single row again to "
                       "return to editing.")

        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        edit = ttk.LabelFrame(right, text="New molecule (click Add to create)")
        edit.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        self._edit_frame = edit

        self.name_var = tk.StringVar()
        self.filename_var = tk.StringVar()
        self.smiles_var = tk.StringVar()
        self.gen_smiles_var = tk.StringVar()
        self.charge_var = tk.StringVar(value="0")
        self.mult_var = tk.StringVar(value="1")
        self.comment_var = tk.StringVar()

        lab, ent = self._field_row(edit, 0, "Name:", self.name_var)
        self._name_entry = ent
        _nt = "Human-readable name. Shown in the table and stored in the .xyz metadata."
        tip(lab, _nt); tip(ent, _nt)
        lab, ent = self._field_row(edit, 1, "Filename (no .xyz):", self.filename_var)
        _ft = ("Short identifier used as the .xyz filename AND as the <mol> directory name in "
               "calcs/. Defaults to the next zero-padded numeric (000, 001, ...) to keep paths "
               "safe — SLURM and SUSE-side tools choke on whitespace and special characters in "
               "directory names, which silently breaks jobs.\n\n"
               "Override with anything you like, but stick to letters/digits/underscore/dash. "
               "Renaming later moves the molecule in the tree on next export.")
        tip(lab, _ft); tip(ent, _ft)
        lab, ent = self._field_row(edit, 2, "SMILES:", self.smiles_var)
        _st = ("SMILES (Simplified Molecular Input Line Entry System) string describing the "
               "real chemical structure. When typed in draft mode, Charge and Multiplicity "
               "below are auto-filled via RDKit (you can still override either by typing into "
               "those fields). Leave blank and use Import .xyz if you'd rather start from a "
               "pre-built geometry.")
        tip(lab, _st); tip(ent, _st)
        lab, ent = self._field_row(edit, 3, "Coord-gen SMILES (optional):", self.gen_smiles_var)
        _gst = ("Optional alternate SMILES used ONLY by Generate XYZ. The main SMILES stays "
                "as the true chemical identity (it's what charge/mult are read from and what "
                "ORCA sees indirectly through the geometry).\n\n"
                "Typical use: RDKit lacks parameters for some niche metal complexes, but works "
                "if you swap the metal for one it knows (Mg often substitutes well). Put the "
                "swapped SMILES here, generate, then edit the .xyz to put the real metal back. "
                "ORCA's geometry optimisation will fix any bond-length offset from the swap.")
        tip(lab, _gst); tip(ent, _gst)
        lab, ent = self._field_row(edit, 4, "Charge:", self.charge_var, width=8)
        _ct = ("Net molecular charge = sum of formal charges on all atoms.\n"
               "Examples: neutral = 0, [NH4]+ = +1, [O]- = -1, [Fe]3+ complex = +3.\n\n"
               "Auto-filled from SMILES on draft entry (RDKit sums formal charges). Editing "
               "this field locks it against further auto-updates for the current draft.")
        tip(lab, _ct); tip(ent, _ct)
        lab, ent = self._field_row(edit, 5, "Multiplicity:", self.mult_var, width=8)
        _mt = ("Spin multiplicity 2S+1, where S is the total spin quantum number.\n"
               "  1 = singlet — all electrons paired (most stable organic molecules at GS)\n"
               "  2 = doublet — one unpaired electron (radicals: •CH3, NO, NO2)\n"
               "  3 = triplet — two unpaired electrons, parallel spins (O2 ground state)\n"
               "  4 = quartet, 5 = quintet, ...\n\n"
               "Auto-filled from SMILES: RDKit infers unpaired electrons from explicit radical "
               "markers like [CH3] or [O][O]. For typical closed-shell organics this gives 1. "
               "Editing this field locks it against further auto-updates for the current draft.")
        tip(lab, _mt); tip(ent, _mt)
        lab, ent = self._field_row(edit, 6, "Comment:", self.comment_var)
        _ctt = ("Free-form note. Stored in the .xyz metadata comment line, never read by ORCA. "
                "Good for tracking provenance — e.g. 'generated with Cu → Mg swap, manually "
                "swapped back in xyz' or 'minimised in Avogadro first, then re-embedded'.")
        tip(lab, _ctt); tip(ent, _ctt)

        edit.columnconfigure(1, weight=1)

        # Per-field traces so we know which field fired (lets us auto-fill
        # charge/mult on SMILES change, and detect manual touches).
        self.name_var.trace_add("write", lambda *_: self._on_field_change("name"))
        self.filename_var.trace_add("write", lambda *_: self._on_field_change("filename"))
        self.smiles_var.trace_add("write", lambda *_: self._on_field_change("smiles"))
        self.gen_smiles_var.trace_add("write", lambda *_: self._on_field_change("gen_smiles"))
        self.charge_var.trace_add("write", lambda *_: self._on_field_change("charge"))
        self.mult_var.trace_add("write", lambda *_: self._on_field_change("mult"))
        self.comment_var.trace_add("write", lambda *_: self._on_field_change("comment"))

        # Vertical split: 2D structure on top (gets ~2/3), plaintext XYZ
        # preview below (~1/3; it's scrollable so it doesn't need much).
        vpaned = ttk.PanedWindow(right, orient=tk.VERTICAL)
        vpaned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._vpaned = vpaned

        depict_frame = ttk.LabelFrame(vpaned, text="Structure (from SMILES)")
        vpaned.add(depict_frame, weight=2)
        # Round-trip: open the current 2D structure in an external editor (ChemDraw /
        # Marvin / …), edit, and read the new SMILES back. Molecules-tab only (prepping).
        edit_bar = ttk.Frame(depict_frame)
        edit_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self._edit_smiles_btn = ttk.Button(edit_bar, text="Edit 2D structure...",
                                           command=self.on_edit_smiles_external)
        self._edit_smiles_btn.pack(side=tk.RIGHT, padx=4, pady=(0, 2))
        tip(self._edit_smiles_btn,
            "Open this molecule's 2D structure in your external 2D editor (set under "
            "Settings > External programs), draw/modify it, save, then import the edited "
            "SMILES back here. Updates the SMILES only, never the 3D geometry. With no SMILES "
            "yet you can draw one from scratch.\n\nShortcut: double-click the structure image.")
        self.depict_label = tk.Label(depict_frame, anchor=tk.CENTER, background="white",
                                     text="(no structure)", foreground="#888")
        self.depict_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Double-click the depiction to edit the structure in the external 2D editor.
        self.depict_label.bind("<Double-1>", lambda e: self.on_edit_smiles_external())
        # Re-render the current structure to fit when the panel is resized, so
        # it scales uniformly into the box instead of being clipped.
        self._current_depict_smiles = ""
        self._depict_resize_after = None
        self.depict_label.bind("<Configure>", self._on_depict_resize)
        tip(self.depict_label, "2D skeletal depiction of this molecule's real SMILES (not the "
                               "coord-gen SMILES). Scales to fit the panel; updates as you type "
                               "a SMILES or select a molecule. Shows '(SMILES not valid)' for "
                               "incomplete input.")

        preview_frame = ttk.LabelFrame(vpaned, text="XYZ preview")
        vpaned.add(preview_frame, weight=1)
        self.preview = tk.Text(preview_frame, wrap="none", font=("Courier", 9))
        self.preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pscroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.preview.yview)
        pscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview.configure(yscrollcommand=pscroll.set, state=tk.DISABLED)

        # ttk weights only govern how extra space is shared on resize, not the
        # initial sash position — pin it to ~2/3 once the pane has a real height.
        self.after(200, self._init_vpane_sash)

    def _field_row(self, parent, row, label, var, width=None):
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=row, column=0, sticky=tk.W, padx=4, pady=2)
        kw = {"textvariable": var}
        if width is not None:
            kw["width"] = width
        entry = ttk.Entry(parent, **kw)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=2)
        return lbl, entry

    # -------------------------------------------------------------- sorting

    _MOL_STATUS_RANK = {"ok": 0, "pending": 1, "failed": 2}

    def _on_header_click(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        for c, label in self._col_labels.items():
            arrow = (" ▼" if self._sort_desc else " ▲") if c == self._sort_col else ""
            self.tree.heading(c, text=label + arrow)
        self.refresh()

    def _sorted_molecules(self):
        mols = list(self.app.project.molecules)
        if not self._sort_col:
            return mols
        cols = ("status", "name", "filename", "smiles", "charge", "mult")
        ci = cols.index(self._sort_col)

        def primary(mol):
            if self._sort_col == "status":
                return self._MOL_STATUS_RANK.get(mol.gen_status, 9)
            return str(self._row_values(mol)[ci]).lower()

        # Generated molecules group to the top within each key (stable two-pass).
        mols.sort(key=lambda m: self._MOL_STATUS_RANK.get(m.gen_status, 9))
        mols.sort(key=primary, reverse=self._sort_desc)
        return mols

    def refresh(self):
        sel = self._focus_filename
        self.tree.delete(*self.tree.get_children())
        for mol in self._sorted_molecules():
            if self.tree.exists(mol.filename):
                continue  # defensive: skip a duplicate filename rather than crash
            self.tree.insert(
                "", tk.END, iid=mol.filename,
                values=self._row_values(mol),
                tags=self._row_tags(mol),
            )
        # Warm the depiction cache in the background so row clicks are instant.
        self._prerender_depictions()
        if sel and self.app.project.molecule_by_filename(sel):
            mol = self.app.project.molecule_by_filename(sel)
            self.tree.selection_set(sel)
            self.tree.see(sel)
            self._set_form_state("normal")
            self._populate_form_from(mol)
            self._edit_frame.configure(text="Edit selected molecule")
            # Refresh the preview too — selection_set won't re-fire
            # <<TreeviewSelect>> if the selection is unchanged (e.g. right
            # after Generate XYZ), so update it explicitly here.
            self._update_preview(mol)
        else:
            self._focus_filename = None
            if self._draft is None:
                self._enter_drafting_mode()
            else:
                # If the draft's auto-numeric filename now collides with a
                # real molecule (e.g. after opening a project), bump it.
                if self._draft.filename and self.app.project.molecule_by_filename(self._draft.filename):
                    self._draft.filename = self._next_numeric_filename()
                self._set_form_state("normal")
                self._populate_form_from(self._draft)
                self._edit_frame.configure(text="New molecule (click Add to create)")
                self._set_preview("(not generated yet — draft)")

    def _on_select(self, _event):
        if self._suppress_select_events:
            return
        sel = self.tree.selection()
        n = len(sel)
        if n == 0:
            # Selection cleared — return to drafting mode.
            if self._draft is not None and self._focus_filename is None:
                return  # already drafting
            self._focus_filename = None
            self._enter_drafting_mode()
            return
        if n == 1:
            # Single-row edit mode.
            new_focus = sel[0]
            if self._focus_filename == new_focus and self._draft is None:
                return  # no change
            if new_focus != self._autofill_row:
                self._autofill_row = None   # left the freshly-added row
            self._focus_filename = new_focus
            self._draft = None
            mol = self.app.project.molecule_by_filename(new_focus)
            if mol is None:
                return
            self._set_form_state("normal")
            self._populate_form_from(mol)
            self._edit_frame.configure(text="Edit selected molecule")
            self._update_preview(mol)
            # A locked molecule's fields are read-only until it's unlocked.
            self._refresh_lock_form_state()
            return
        # Multi-select mode.
        self._focus_filename = None
        self._draft = None
        self._enter_multi_select_mode(n)

    def _enter_drafting_mode(self):
        """Discard any current draft and start a fresh blank one bound to the form."""
        self._focus_filename = None
        self._autofill_row = None
        self._draft = Molecule(name="", filename=self._next_numeric_filename())
        self._user_touched_charge = False
        self._user_touched_mult = False
        self._suppress_select_events = True
        try:
            self.tree.selection_remove(self.tree.selection())
        finally:
            self._suppress_select_events = False
        self._set_form_state("normal")
        self._populate_form_from(self._draft)
        self._edit_frame.configure(text="New molecule (click Add to create)")
        self._set_preview("(not generated yet — draft)")
        self._update_depiction("")

    def _enter_multi_select_mode(self, n):
        # type: (int) -> None
        """Multiple rows selected — lock the form, indicate bulk-action mode."""
        self._suppress_field_writes = True
        try:
            for v in (self.name_var, self.filename_var, self.smiles_var,
                      self.gen_smiles_var, self.charge_var, self.mult_var, self.comment_var):
                v.set("")
        finally:
            self._suppress_field_writes = False
        self._set_form_state("disabled")
        self._edit_frame.configure(
            text="{} molecules selected — click a single row to edit, "
                 "or use Remove / Generate XYZ for bulk actions".format(n)
        )
        self._set_preview("(multi-selection — select a single row to preview its XYZ)")
        self._update_depiction("")

    def _set_form_state(self, state):
        # type: (str) -> None
        """Enable or disable all the Entry widgets in the edit frame."""
        for child in self._edit_frame.winfo_children():
            if isinstance(child, ttk.Entry):
                child.configure(state=state)

    def _refresh_lock_form_state(self):
        """When a single (locked) molecule is selected, grey out its edit fields so
        they can't be changed until it's unlocked (Ctrl+L). Called on selection and
        whenever a lock is toggled."""
        if len(self.tree.selection()) != 1 or self._draft is not None:
            return
        mol = self.app.project.molecule_by_filename(self._focus_filename)
        if mol is None:
            return
        locked = getattr(mol, "locked", False)
        self._set_form_state("disabled" if locked else "normal")
        self._edit_frame.configure(text="Edit selected molecule"
                                   + (" (locked - Ctrl+L to unlock)" if locked else ""))

    def _select_all(self, _event=None):
        all_items = self.tree.get_children("")
        if all_items:
            self.tree.selection_set(all_items)
        return "break"

    def _populate_form_from(self, mol):
        # type: (Molecule) -> None
        self._suppress_field_writes = True
        try:
            self.name_var.set(mol.name)
            self.filename_var.set(mol.filename)
            self.smiles_var.set(mol.smiles or "")
            self.gen_smiles_var.set(mol.gen_smiles or "")
            self.charge_var.set(str(mol.charge))
            self.mult_var.set(str(mol.multiplicity))
            self.comment_var.set(mol.comment)
        finally:
            self._suppress_field_writes = False

    def _on_field_change(self, field=None):
        if self._suppress_field_writes:
            return
        target = self._current_target()
        if target is None:
            return
        # Apply form -> target
        target.name = self.name_var.get()
        new_fname = self.filename_var.get().strip()
        target.smiles = self.smiles_var.get().strip() or None
        target.gen_smiles = self.gen_smiles_var.get().strip() or None
        try:
            target.charge = int(self.charge_var.get())
        except ValueError:
            pass
        try:
            target.multiplicity = int(self.mult_var.get())
        except ValueError:
            pass
        target.comment = self.comment_var.get()

        # Track manual touches (only meaningful for drafts; mark on real edits too,
        # so we don't auto-clobber if the user types SMILES into a selected molecule).
        if field == "charge":
            self._user_touched_charge = True
        elif field == "mult":
            self._user_touched_mult = True

        if self._focus_filename is not None:
            # Editing a real molecule — handle filename rename and refresh the row.
            if new_fname and new_fname != target.filename:
                if self.app.project.molecule_by_filename(new_fname) is None:
                    if self._autofill_row == target.filename:
                        self._autofill_row = new_fname   # keep SMILES auto-fill alive
                    target.filename = new_fname
                    self._focus_filename = new_fname
            # SMILES change on a real molecule invalidates the existing geometry —
            # but NOT for imported (locked) coords: their geometry is the original
            # file, independent of any SMILES the user records for reference.
            if (field == "smiles" and target.gen_status == "ok"
                    and not target.coords_locked):
                target.generated = False
                target.gen_status = "pending"
                target.gen_error = None
            self.app.mark_dirty()
            self._refresh_row(target)
        else:
            # Editing a draft — just store the filename literally; uniqueness check on commit.
            target.filename = new_fname

        # Auto-fill charge/mult from SMILES in draft mode, or on a freshly-added
        # row that hasn't been navigated away from yet (so "Add" then typing a
        # SMILES still auto-fills, even though the row is already committed).
        if field == "smiles" and (self._focus_filename is None
                                  or self._focus_filename == self._autofill_row):
            self._maybe_auto_charge_mult(target)
            self._maybe_annotate_mass(target)
            if self._focus_filename is not None:
                self._refresh_row(target)   # reflect the new charge/mult in the table

        # Live-update the 2D depiction when the SMILES changes.
        if field == "smiles":
            self._update_depiction(target.smiles or "")

    def _maybe_auto_charge_mult(self, target):
        # type: (Molecule) -> None
        """In draft mode, compute charge/mult from SMILES and update the form
        UNLESS the user has already touched those fields."""
        if not target.smiles:
            return
        if self._user_touched_charge and self._user_touched_mult:
            return
        charge, mult = coords_mod.smiles_charge_and_mult(target.smiles)
        if charge is None:
            return  # RDKit absent or SMILES unparseable; leave user's values
        self._suppress_field_writes = True
        try:
            if not self._user_touched_charge:
                self.charge_var.set(str(charge))
                target.charge = charge
            if not self._user_touched_mult:
                self.mult_var.set(str(mult))
                target.multiplicity = mult
        finally:
            self._suppress_field_writes = False

    # A managed "MW=<x> g/mol" token appended to the comment. Matched (with an
    # optional leading separator) so a SMILES edit REPLACES it rather than stacking
    # duplicates; the user's free-text comment is preserved around it.
    _MW_TOKEN_RE = re.compile(r"\s*;?\s*MW\s*=\s*[0-9.]+\s*g/mol", re.IGNORECASE)

    def _maybe_annotate_mass(self, target):
        # type: (Molecule) -> None
        """Record the SMILES molecular weight in the comment as a managed
        'MW=<x> g/mol' token. No-op if RDKit is absent or the SMILES won't parse,
        so a half-typed SMILES leaves the comment untouched."""
        if not target.smiles:
            return
        mw = coords_mod.smiles_mol_weight(target.smiles)
        if mw is None:
            return
        base = self._MW_TOKEN_RE.sub("", target.comment or "").strip().rstrip(";").strip()
        token = "MW={:.2f} g/mol".format(mw)
        new_comment = "{}; {}".format(base, token) if base else token
        if new_comment == (target.comment or ""):
            return
        self._suppress_field_writes = True
        try:
            self.comment_var.set(new_comment)
            target.comment = new_comment
        finally:
            self._suppress_field_writes = False

    def _current_target(self):
        # type: () -> Optional[Molecule]
        if self._focus_filename is not None:
            return self.app.project.molecule_by_filename(self._focus_filename)
        return self._draft

    def _refresh_row(self, mol):
        if self.tree.exists(mol.filename):
            self.tree.item(
                mol.filename,
                values=self._row_values(mol),
                tags=self._row_tags(mol),
            )
        else:
            self.refresh()

    def _row_values(self, mol):
        # type: (Molecule) -> tuple
        """Build the Treeview values tuple for one molecule, matching the configured
        column order (status, name, filename, smiles, charge, mult, lock)."""
        if mol.gen_status == "ok":
            status = "ok ({})".format(mol.method or "?")
        elif mol.gen_status == "failed":
            status = "failed"
        else:
            status = "pending"
        lock = "[x]" if getattr(mol, "locked", False) else "[ ]"
        return (status, mol.name, mol.filename, mol.smiles or "", mol.charge,
                mol.multiplicity, lock)

    def _row_tags(self, mol):
        # A locked row greys out (overriding the gen_status colour) so it's obvious.
        return ("locked",) if getattr(mol, "locked", False) else (mol.gen_status,)

    def _update_row(self, mol):
        if self.tree.exists(mol.filename):
            self.tree.item(mol.filename, values=self._row_values(mol), tags=self._row_tags(mol))

    def _toggle_lock_selected(self, _event=None):
        """Ctrl+L: lock the selected molecules, or unlock them if they're all locked."""
        mols = [self.app.project.molecule_by_filename(i) for i in self.tree.selection()]
        mols = [m for m in mols if m is not None]
        if not mols:
            return "break"
        target = not all(getattr(m, "locked", False) for m in mols)
        for m in mols:
            m.locked = target
            self._update_row(m)
        self.app.mark_dirty()
        self._refresh_lock_form_state()
        self.app.set_status("{} {} molecule(s).".format(
            "Locked" if target else "Unlocked", len(mols)))
        return "break"

    def _lock_press(self, event):
        # Click on a Lock cell: flip it and start a drag-paint at that value. Return
        # "break" so the click doesn't also change the row selection.
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != self._lock_col_id:
            return None
        mol = self.app.project.molecule_by_filename(self.tree.identify_row(event.y))
        if mol is None:
            return None
        self._lock_paint = not getattr(mol, "locked", False)
        self._lock_painting = True
        mol.locked = self._lock_paint
        self._update_row(mol)
        self.app.mark_dirty()
        self._refresh_lock_form_state()
        return "break"

    def _lock_motion(self, event):
        if not self._lock_painting:
            return None
        mol = self.app.project.molecule_by_filename(self.tree.identify_row(event.y))
        if mol is not None and getattr(mol, "locked", False) != self._lock_paint:
            mol.locked = self._lock_paint
            self._update_row(mol)
            self.app.mark_dirty()
        return "break"

    def _lock_release(self, _event):
        if self._lock_painting:
            self._lock_painting = False
            return "break"
        return None

    # --------------------------------------------------------- drag-to-reorder

    def _drag_start(self, event):
        """Arm a potential row drag. Returns None so the normal click-selection still
        happens; the drag only 'takes' once the pointer moves past a small threshold."""
        self._drag_item = None
        self._drag_moved = False
        self._drag_y0 = event.y
        # Reordering only makes sense in insertion order — a column sort imposes its
        # own order, so don't reorder then (the tooltip tells the user to clear it).
        if self._sort_col is not None:
            return None
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        row = self.tree.identify_row(event.y)
        if not row or row.startswith("__"):
            return None
        self._drag_item = row
        return None

    def _drag_motion(self, event):
        """Note that a drag is in progress. We deliberately do NOT reposition rows
        per motion event — that redraw-per-event storm lags out over a remote X11
        framebuffer (ThinLinc). The actual reorder happens once, on release, from the
        drop position. Returns 'break' while armed to suppress the Treeview's native
        band-selection (which would otherwise multi-select as the pointer moves)."""
        if not self._drag_item or self._lock_painting:
            return None
        if abs(event.y - getattr(self, "_drag_y0", event.y)) > 4:
            if not self._drag_moved:
                self._drag_moved = True
                try:
                    self.tree.configure(cursor="hand2")   # visual "dragging" cue
                except tk.TclError:
                    pass
        if self._drag_moved:
            # A thin insertion line at the drop gap (like the Transform op editor) —
            # ONE place() per motion, no tree mutation, so it's ThinLinc-safe.
            self._show_reorder_line(event.y)
        return "break"

    def _show_reorder_line(self, y):
        """Place a 2px insertion line at the row gap nearest y (before the upper
        half of a row, after the lower half; past the last row = the end)."""
        line = getattr(self, "_reorder_line", None)
        if line is None or not line.winfo_exists():
            line = tk.Frame(self.tree, height=2, bg="#1f6fb2")
            self._reorder_line = line
        kids = [i for i in self.tree.get_children("") if not i.startswith("__")]
        if not kids:
            return
        row = self.tree.identify_row(y)
        if not row or row.startswith("__"):
            bb = self.tree.bbox(kids[-1])
            ly = (bb[1] + bb[3]) if bb else 0
        else:
            bb = self.tree.bbox(row)
            if not bb:
                return
            ly = bb[1] if y < bb[1] + bb[3] / 2 else bb[1] + bb[3]
        line.place(in_=self.tree, x=0, relwidth=1.0, y=max(0, ly - 1))

    def _hide_reorder_line(self):
        line = getattr(self, "_reorder_line", None)
        if line is not None and line.winfo_exists():
            line.place_forget()

    def _drag_release(self, event):
        """On drop, move the grabbed row to the drop position and renumber. Uses the
        release Y (not accumulated motion) so it survives coalesced/dropped motion
        events over the remote link; a move past the press point counts as a drag even
        if no intermediate <B1-Motion> arrived."""
        item = self._drag_item
        moved = self._drag_moved or abs(event.y - getattr(self, "_drag_y0", event.y)) > 4
        self._drag_item = None
        self._drag_moved = False
        self._hide_reorder_line()
        try:
            self.tree.configure(cursor="")
        except tk.TclError:
            pass
        if not (item and moved):
            return None
        order = [i for i in self.tree.get_children("") if not i.startswith("__")]
        if item not in order:
            return None
        order.remove(item)
        target = self.tree.identify_row(event.y)
        if target and target in order:
            insert_at = order.index(target)
            try:                       # dropped on the lower half of a row → after it
                bx = self.tree.bbox(target)
                if bx and event.y > bx[1] + bx[3] / 2:
                    insert_at += 1
            except (tk.TclError, TypeError):
                pass
            order.insert(insert_at, item)
        else:
            order.append(item)         # dropped past the last row → end
        self._apply_new_order(order, keep_focus=item)
        return "break"

    def _move_focused(self, delta):
        """Alt+Up / Alt+Down: move the focused (or selected) row one step and renumber.
        A keyboard alternative to drag that's fully reliable over remote/ThinLinc, where
        drag events can be finicky."""
        if self._sort_col is not None:
            self.app.set_status("Clear the column sort before reordering molecules.")
            return "break"
        order = [i for i in self.tree.get_children("") if not i.startswith("__")]
        cur = self._focus_filename if self._focus_filename in order else None
        if cur is None:
            sel = [i for i in self.tree.selection() if i in order]
            cur = sel[0] if sel else None
        if cur is None:
            return "break"
        i = order.index(cur)
        j = i + delta
        if j < 0 or j >= len(order):
            return "break"
        order.insert(j, order.pop(i))
        self._apply_new_order(order, keep_focus=cur)
        return "break"

    def _apply_new_order(self, ordered_filenames, keep_focus=None):
        """Reorder project.molecules to the given filename order, then renumber the
        filenames 000.. top to bottom (when safe). Refreshes this + dependent tabs."""
        by_fn = {m.filename: m for m in self.app.project.molecules}
        new_list = [by_fn[f] for f in ordered_filenames if f in by_fn]
        for m in self.app.project.molecules:      # keep any stragglers (defensive)
            if m not in new_list:
                new_list.append(m)
        self.app.project.molecules = new_list
        remap = self._renumber_molecules()
        self.app.mark_dirty()
        if keep_focus is not None:
            self._focus_filename = remap.get(keep_focus, keep_focus)
        self.refresh()
        self.app.refresh_all_tabs()   # calcs reference molecules by filename
        n = len(new_list)
        if remap:
            self.app.set_status("Reordered and renumbered {} molecule(s) (000..).".format(n))
        else:
            self.app.set_status("Reordered {} molecule(s).".format(n))

    def _renumber_molecules(self):
        # type: () -> dict
        """Reassign molecule filenames to sequential zero-padded numerics (000, 001,
        ...) top to bottom — the fixed %03d convention — renaming the XYZ_INI .xyz
        files on disk and updating planned-calc references. Returns {old: new} for the
        names that changed (empty dict when nothing changed or it was skipped).

        Skipped entirely if any planned calculation is already exported or submitted:
        those are keyed to the current filename on disk (rundir, .inp/.slurm/.gbw), so
        renaming would orphan them. In that case only the row order changes."""
        mols = self.app.project.molecules
        for c in self.app.project.planned_calcs:
            if c.exported or c.job_id or c.rundir:
                return {}
        desired = ["{:03d}".format(i) for i in range(len(mols))]
        remap = {m.filename: new for m, new in zip(mols, desired) if m.filename != new}
        if not remap:
            return {}
        root = self.app.project.root()
        tag = ".reorder-{}.tmp".format(uuid.uuid4().hex[:8])
        # Phase 1: stage each changing molecule's .xyz to a unique temp path, so a
        # permutation (e.g. swapping 000<->001) can't clobber a name in flight.
        plan = []  # (mol, new_name, tmp_abs_or_None, final_abs, final_rel)
        for m, new in zip(mols, desired):
            if m.filename == new:
                continue
            final_abs = os.path.join(root, "XYZ_INI", new + ".xyz")
            final_rel = os.path.relpath(final_abs, root).replace("\\", "/")
            tmp_abs = None
            if m.xyz_path:
                old_abs = (m.xyz_path if os.path.isabs(m.xyz_path)
                           else os.path.join(root, m.xyz_path))
                if os.path.isfile(old_abs):
                    tmp_abs = old_abs + tag
                    try:
                        os.replace(old_abs, tmp_abs)
                    except OSError:
                        tmp_abs = None
            plan.append((m, new, tmp_abs, final_abs, final_rel))
        # Phase 2: assign the new names and move the staged files into place.
        for m, new, tmp_abs, final_abs, final_rel in plan:
            m.filename = new
            if tmp_abs is not None:
                try:
                    os.makedirs(os.path.dirname(final_abs) or ".", exist_ok=True)
                    os.replace(tmp_abs, final_abs)
                    m.xyz_path = final_rel
                except OSError:
                    # Keep the reference valid even if the final move failed.
                    m.xyz_path = os.path.relpath(tmp_abs, root).replace("\\", "/")
        # Repoint planned-calc references (safe: none are exported — guarded above).
        for c in self.app.project.planned_calcs:
            if c.molecule_filename in remap:
                c.molecule_filename = remap[c.molecule_filename]
            gs = c.geometry_source or ""
            if gs.startswith("file:"):
                for old, new in remap.items():
                    token = "XYZ_INI/{}.xyz".format(old)
                    if token in gs:
                        c.geometry_source = gs.replace(
                            token, "XYZ_INI/{}.xyz".format(new))
                        break
        return remap

    def on_add(self):
        """Create a new molecule row immediately, select it, and focus the Name
        field so it can be edited right away — one press yields one real, editable
        row. If the user had typed into the form without a row selected (an
        uncommitted draft), that draft becomes the new row; otherwise a fresh blank
        row is created, with the filename doubling as the initial name."""
        draft = self._draft
        has_content = draft is not None and any([
            (draft.name or "").strip(),
            (draft.smiles or "").strip(),
            (draft.gen_smiles or "").strip(),
            (draft.comment or "").strip(),
        ])
        if has_content:
            mol = draft
            fname = (mol.filename or "").strip() or self._next_numeric_filename()
        else:
            fname = self._next_numeric_filename()
            # Name defaults to the filename so the row isn't blank; the focused Name
            # field is select-all'd, so the first keystroke replaces it.
            mol = Molecule(name=fname, filename=fname)
        mol.filename = self._unique_filename(fname)
        self.app.project.molecules.append(mol)
        self.app.mark_dirty()
        self._draft = None
        self._focus_filename = mol.filename
        self._autofill_row = mol.filename
        self._user_touched_charge = False
        self._user_touched_mult = False
        self.refresh()   # inserts the row and selects it (focus_filename is set)
        self._focus_name_field()
        self.app.set_status("Added molecule '{}'. Edit its details on the right.".format(mol.filename))

    def _focus_name_field(self):
        """Put keyboard focus in the Name entry with its text selected, so the user
        can type the new molecule's name immediately (first keystroke replaces the
        default filename-name)."""
        try:
            self._name_entry.focus_set()
            self._name_entry.select_range(0, tk.END)
            self._name_entry.icursor(tk.END)
        except (tk.TclError, AttributeError):
            pass

    def _unique_filename(self, base):
        # type: (str) -> str
        if not self.app.project.molecule_by_filename(base):
            return base
        i = 2
        while True:
            candidate = "{}_{:03d}".format(base, i)
            if not self.app.project.molecule_by_filename(candidate):
                return candidate
            i += 1

    def _next_numeric_filename(self, width=3):
        # type: (int) -> str
        """Next zero-padded numeric filename not already in the project.
        Picks max(existing numerics) + 1 so deletions don't backfill — keeps
        numbering monotonic. Non-numeric filenames are ignored."""
        max_n = -1
        for m in self.app.project.molecules:
            try:
                n = int(m.filename)
                if n > max_n:
                    max_n = n
            except (ValueError, TypeError):
                pass
        fmt = "{{:0{}d}}".format(width)
        return fmt.format(max_n + 1)

    def on_remove(self):
        selected = list(self.tree.selection())
        if not selected:
            messagebox.showinfo("No selection", "Select one or more molecules to remove.")
            return
        # Locked molecules are protected — drop them from the removal set with a note.
        locked = [i for i in selected
                  if getattr(self.app.project.molecule_by_filename(i), "locked", False)]
        if locked:
            selected = [i for i in selected if i not in locked]
            if not selected:
                messagebox.showinfo(
                    "Locked",
                    "{} selected molecule(s) are locked. Unlock them (Ctrl+L or the Lock "
                    "column) before removing.".format(len(locked)))
                return
            messagebox.showinfo(
                "Locked",
                "{} locked molecule(s) will be kept; removing the other {}.".format(
                    len(locked), len(selected)))
        n = len(selected)
        if n == 1:
            mol = self.app.project.molecule_by_filename(selected[0])
            prompt = "Remove '{}'?".format(mol.name if mol else selected[0])
        else:
            prompt = "Remove {} selected molecules?\n\nAny planned calculations referencing them will also be removed.".format(n)
        if not messagebox.askyesno("Remove molecules", prompt):
            return
        sel_set = set(selected)
        self.app.project.molecules = [m for m in self.app.project.molecules if m.filename not in sel_set]
        self.app.project.planned_calcs = [
            c for c in self.app.project.planned_calcs if c.molecule_filename not in sel_set
        ]
        self._focus_filename = None
        self.app.mark_dirty()
        self.app.refresh_all_tabs()
        self.app.set_status("Removed {} molecule(s).".format(n))

    def on_generate(self):
        selected = list(self.tree.selection())
        if len(selected) > 1:
            # Bulk generate selected. Skip imported (locked) coords — they're the
            # original geometry and must not be overwritten by SMILES generation.
            mols = [self.app.project.molecule_by_filename(f) for f in selected]
            mols = [m for m in mols if m is not None and m.smiles and not m.coords_locked]
            if not mols:
                messagebox.showinfo("Nothing to generate",
                                    "None of the selected molecules has a SMILES to generate "
                                    "from (imported structures are locked to their original "
                                    "coordinates).")
                return
            failures = []
            for mol in mols:
                if not self._generate_one(mol, interactive=False):
                    failures.append(mol.name)
            self.refresh()
            if failures:
                messagebox.showwarning("Some generations failed", "Failed for:\n" + "\n".join(failures))
            else:
                self.app.set_status("Generated XYZ for {} molecules.".format(len(mols)))
            return
        if self._focus_filename is None:
            # Drafting mode — commit the draft first if it has any SMILES content.
            if self._draft is None or not (self._draft.smiles or "").strip():
                messagebox.showinfo("No molecule", "Type a SMILES into the form first, or select an existing molecule.")
                return
            self.on_add()  # commits the draft as a real row and selects it
            if not self.app.project.molecules:
                return
            mol = self.app.project.molecules[-1]
        else:
            mol = self.app.project.molecule_by_filename(self._focus_filename)
            if mol is None:
                return
        self._generate_one(mol, interactive=True)

    def on_generate_all(self):
        # Only true 'pending' rows — leave 'failed' alone (they need user
        # intervention: edit SMILES, set a gen_smiles swap, or import .xyz).
        # To retry a failed row, the user can select it and press Ctrl+Enter,
        # or edit its SMILES (which resets it to pending and then this picks
        # it up on the next run).
        pending = [m for m in self.app.project.molecules
                   if m.gen_status == "pending" and m.smiles and not m.coords_locked]
        if not pending:
            messagebox.showinfo("Nothing to do",
                                "No molecules with status 'pending' have a SMILES to generate from.")
            return
        failures = []
        for mol in pending:
            ok = self._generate_one(mol, interactive=False)
            if not ok:
                failures.append(mol.name)
        self.refresh()
        if failures:
            messagebox.showwarning(
                "Some generations failed",
                "Failed for:\n" + "\n".join(failures),
            )
        else:
            self.app.set_status("Generated XYZ for {} molecules.".format(len(pending)))

    def _generate_one(self, mol, interactive):
        # type: (Molecule, bool) -> bool
        # Coordinates imported from a file are the original geometry — never
        # overwrite them by re-generating from SMILES. (To use SMILES instead,
        # delete this entry and add a fresh one.) Leave status untouched.
        if mol.coords_locked:
            if interactive:
                src = mol.comment or "an imported file"
                messagebox.showinfo(
                    "Generation blocked",
                    "'{}' has coordinates imported from {}.\n\n"
                    "Generation is blocked so the original geometry (and its "
                    "provenance) is never overwritten. To build coordinates from "
                    "SMILES instead, delete this entry and add it again from a "
                    "SMILES.".format(mol.name or mol.filename, src))
            return False
        # Use the alternate gen_smiles if set (metal-swap hack), else the real SMILES.
        gen_was_used = bool((mol.gen_smiles or "").strip())
        smiles_for_gen = (mol.gen_smiles or "").strip() or mol.smiles
        if not smiles_for_gen:
            mol.gen_status = "failed"
            mol.gen_error = "No SMILES to generate from."
            self.app.mark_dirty()
            if interactive:
                messagebox.showerror("No SMILES", mol.gen_error)
            return False
        # When the user supplied a gen_smiles, they're doing a deliberate
        # workaround (typically a metal-swap). OpenBabel's UFF embed butchers
        # ringed systems — falling back to it would silently produce a worse
        # geometry that masks the original RDKit failure. Force RDKit-only in
        # that case so the user sees the real error.
        out_path = os.path.join(self.app.project.root(), "XYZ_INI", mol.filename + ".xyz")
        try:
            atoms, method = coords_mod.smiles_to_xyz(
                smiles_for_gen, prefer_rdkit_only=gen_was_used,
            )
        except coords_mod.CoordGenError as e:
            mol.generated = False
            mol.gen_status = "failed"
            mol.gen_error = str(e)
            self.app.mark_dirty()
            if interactive:
                messagebox.showerror("Generation failed", str(e))
            return False
        except ImportError as e:
            mol.generated = False
            mol.gen_status = "failed"
            mol.gen_error = "Missing dependency: " + str(e)
            self.app.mark_dirty()
            if interactive:
                messagebox.showerror(
                    "Missing dependency",
                    "Install rdkit and/or openbabel:\n  pip install --user rdkit openbabel-wheel\n\n{}".format(e),
                )
            return False
        metadata = {
            "name": mol.name,
            "smiles": mol.smiles,
            "charge": mol.charge,
            "multiplicity": mol.multiplicity,
            "method": method,
            "comment": mol.comment,
        }
        if mol.gen_smiles:
            metadata["gen_smiles"] = mol.gen_smiles
            metadata["gen_note"] = "Coords generated from gen_smiles (e.g. metal-swap hack); atoms may need manual edit before ORCA."
        coords_mod.write_xyz(out_path, atoms, metadata)
        mol.generated = True
        mol.gen_status = "ok"
        mol.gen_error = None
        mol.method = method
        mol.xyz_path = os.path.relpath(out_path, self.app.project.root()).replace("\\", "/")
        self.app.mark_dirty()
        if interactive:
            self.refresh()
            note = " [via gen_smiles swap]" if mol.gen_smiles else ""
            self.app.set_status("Generated {} ({}){}.".format(mol.xyz_path, method, note))
        return True

    def on_import_files(self):
        """Import one or many structure files (multi-select). Any format OpenBabel/
        RDKit can read is converted to .xyz; .xyz is read natively."""
        paths = filedialog.askopenfilenames(
            title="Import structure files",
            filetypes=coords_mod.import_dialog_filetypes(),
        )
        if not paths:
            return
        self._import_paths(list(paths))

    def on_import_folder(self):
        """Import every supported structure file in a chosen folder."""
        d = filedialog.askdirectory(title="Import all structure files from folder")
        if not d:
            return
        try:
            names = sorted(os.listdir(d))
        except OSError as e:
            messagebox.showerror("Import folder", str(e))
            return
        paths = [os.path.join(d, n) for n in names
                 if os.path.isfile(os.path.join(d, n))
                 and (coords_mod.is_supported_import_file(n)
                      or coords_mod.is_smiles_list_file(n))]
        if not paths:
            messagebox.showinfo(
                "Import folder",
                "No supported coordinate or SMILES-list files found in\n{}\n\n"
                "Supported: {}".format(
                    d, ", ".join([ext for ext, _f, _l in coords_mod.SUPPORTED_IMPORT_FORMATS]
                                 + [ext for ext, _l in coords_mod.SMILES_LIST_FORMATS])))
            return
        self._import_paths(paths)

    def _import_paths(self, paths):
        """Shared batch importer: read each file (converting non-xyz formats), let
        the user pick one / several / all structures from a multi-record file
        (once, or remembered for the rest of the batch), write XYZ_INI/<name>.xyz
        per chosen structure, and add a Molecule. One refresh + status at the end."""
        root = self.app.project.root()
        imported = 0
        errors = []
        remembered_spec = None    # raw index spec ("all", "0,2", "-1", ...) reused for the batch
        first_fname = None
        for path in paths:
            base_name = os.path.basename(path)
            # SMILES-list files (.smi/.smiles/.csv) hold SMILES, not coordinates:
            # add them as pending molecules to GENERATE, not locked structures.
            if coords_mod.is_smiles_list_file(path):
                try:
                    pairs = coords_mod.read_smiles_file(path)
                except Exception as e:
                    errors.append("{}: {}".format(base_name, e))
                    continue
                new = self._add_smiles_entries(pairs)
                imported += len(new)
                if first_fname is None and new:
                    first_fname = new[0]
                continue
            try:
                structs = coords_mod.read_structures(path)
            except Exception as e:
                errors.append("{}: {}".format(base_name, e))
                continue
            n = len(structs)
            if n == 1:
                indices = [0]
            else:
                spec = remembered_spec
                if spec is None:
                    res = ConformerSelectDialog(self, base_name, n).result
                    if res is None:
                        continue          # user skipped this file
                    spec, remember = res
                    if remember:
                        remembered_spec = spec
                indices = coords_mod.parse_structure_selection(spec, n)
                if not indices:
                    errors.append("{}: no valid structure index in '{}' (file has {})".format(
                        base_name, spec, n))
                    continue
            base = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                          os.path.splitext(base_name)[0]) or "mol"
            multi = n > 1
            for idx in indices:
                atoms, meta = structs[idx]
                if not atoms:
                    errors.append("{} [#{}]: structure has no atoms".format(base_name, idx))
                    continue
                meta = dict(meta or {})   # copy so enriching doesn't touch shared structs
                # Provenance: record the source file (and conformer, for multi-record
                # files) so an imported molecule is always traceable from its Comment,
                # even when the filename is just a deduped basename.
                if not meta.get("comment"):
                    meta["comment"] = imported_comment(base_name, idx, multi)
                fname = self._unique_filename(imported_stem(base, idx, multi))
                target = os.path.join(root, "XYZ_INI", fname + ".xyz")
                # If the file being imported already IS the destination (launched
                # from a folder whose XYZ_INI is the source), don't rewrite it —
                # that would clobber the original and re-encode its comment.
                in_place = (os.path.normcase(os.path.abspath(target))
                            == os.path.normcase(os.path.abspath(path)))
                try:
                    if not in_place:
                        coords_mod.write_xyz(target, atoms, meta or None)
                except Exception as e:
                    errors.append("{} [#{}]: write failed: {}".format(base_name, idx, e))
                    continue
                # If the stored name is just the SMILES (common for structures the
                # app generated from SMILES), don't echo it in the Name column —
                # use the filename; the SMILES still shows in its own column.
                mname = meta.get("name")
                if mname and mname == meta.get("smiles"):
                    mname = None
                self.app.project.molecules.append(Molecule(
                    name=mname or fname,
                    filename=fname,
                    smiles=meta.get("smiles"),
                    gen_smiles=meta.get("gen_smiles"),
                    charge=int(meta.get("charge", 0) or 0),
                    multiplicity=int(meta.get("multiplicity", 1) or 1),
                    comment=meta.get("comment", "") or "",
                    generated=True,
                    gen_status="ok",
                    method="imported",
                    coords_locked=True,   # the imported .xyz is the original — don't let
                                          # SMILES generation overwrite it (delete + re-add
                                          # from SMILES if that's what you want instead).
                    xyz_path=os.path.relpath(target, root).replace("\\", "/"),
                ))
                imported += 1
                if first_fname is None:
                    first_fname = fname
        if imported:
            self.app.mark_dirty()
            self.refresh()
            if first_fname:
                try:
                    self.tree.selection_set(first_fname)
                except tk.TclError:
                    pass
        msg = "Imported {} molecule(s).".format(imported)
        if errors:
            msg += " {} failed.".format(len(errors))
        self.app.set_status(msg)
        if errors:
            shown = "\n".join(errors[:20]) + ("\n..." if len(errors) > 20 else "")
            messagebox.showwarning(
                "Import finished with errors",
                "Imported {} file(s). These could not be imported:\n\n{}".format(imported, shown))

    def _unique_filename(self, base):
        """A molecule filename not already used in the project (base, base_2, ...)."""
        fname = base
        i = 2
        while self.app.project.molecule_by_filename(fname):
            fname = "{}_{}".format(base, i)
            i += 1
        return fname

    def _update_preview(self, mol):
        # Keep the structure depiction in sync with the previewed molecule.
        self._update_depiction(mol.smiles or "")
        if mol.gen_status == "failed":
            err = mol.gen_error or "(no error message recorded)"
            self._set_preview(
                "Coordinate generation FAILED for this molecule.\n\n"
                "{}\n\n"
                "What to try next:\n"
                "  - Verify the SMILES parses (paste into ChemDraw / Avogadro).\n"
                "  - For exotic metal complexes, set a 'Coord-gen SMILES' that swaps the\n"
                "    metal for one RDKit knows (Mg often works); manually swap back in the\n"
                "    resulting .xyz, ORCA's OPT will relax the bond lengths.\n"
                "  - Or import a pre-built .xyz from Avogadro/ChemDraw via 'Import .xyz...'.".format(err)
            )
            return
        if not mol.generated or not mol.xyz_path:
            self._set_preview("(not generated yet — click Generate XYZ)")
            return
        abs_path = mol.xyz_path
        if not os.path.isabs(abs_path):
            abs_path = os.path.join(self.app.project.root(), abs_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                text = f.read()
        except IOError as e:
            text = "(failed to read XYZ: {})".format(e)
        if mol.coords_locked:
            note = ("# locked coordinates — this .xyz is authoritative (SMILES generation "
                    "is disabled for this entry)")
            if (mol.smiles or "").strip():
                note += ("\n# NOTE: the recorded SMILES is reference only and may not match "
                         "these coordinates")
            text = note + "\n" + text
        self._set_preview(text)

    def _set_preview(self, text):
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        self.preview.insert("1.0", text)
        self.preview.configure(state=tk.DISABLED)

    def _depict_size(self):
        # type: () -> tuple
        """Current draw size from the panel, bucketed to the nearest 40px so
        minor resizes reuse cached renders. RDKit fits the molecule into this
        box preserving aspect, so a wide box gets a wide-fitted structure and a
        tall box a tall-fitted one — no clipping either way."""
        try:
            w = self.depict_label.winfo_width()
            h = self.depict_label.winfo_height()
        except tk.TclError:
            w = h = 0
        if w < 40 or h < 40:
            return (360, 240)  # not laid out yet — sensible default
        bucket = lambda v: max(120, int(round(v / 40.0)) * 40)
        return (bucket(w - 8), bucket(h - 8))

    def _update_depiction(self, smiles):
        # type: (str) -> None
        """Show the 2D structure for the given SMILES, scaled to fit the panel.
        Cached by (smiles, width, height) so revisiting a molecule at the same
        panel size is an instant dict lookup."""
        smi = (smiles or "").strip()
        self._current_depict_smiles = smi
        if not smi:
            self._depict_image = None
            self.depict_label.configure(image="", text="(no structure)")
            return
        w, h = self._depict_size()
        key = (smi, w, h)
        cached = self._depict_cache.get(key)
        if cached is not None:
            self._depict_image = cached
            self.depict_label.configure(image=cached, text="")
            return
        img, err = smiles_to_photoimage(smi, size=(w, h), master=self.depict_label)
        if img is not None:
            self._cache_put(key, img)
            self._depict_image = img
            self.depict_label.configure(image=img, text="")
        else:
            self._depict_image = None
            self.depict_label.configure(image="", text=err or "(no structure)")

    def _on_depict_resize(self, event):
        # Debounce: re-render the current structure at the new size after the
        # user stops dragging the sash / resizing the window.
        if self._depict_resize_after is not None:
            try:
                self.after_cancel(self._depict_resize_after)
            except Exception:
                pass
        self._depict_resize_after = self.after(150, self._rerender_current_depiction)

    def _rerender_current_depiction(self):
        self._depict_resize_after = None
        if not self.winfo_exists():
            return
        if self._current_depict_smiles:
            self._update_depiction(self._current_depict_smiles)

    def _init_vpane_sash(self):
        if not self.winfo_exists():
            return
        try:
            self._vpaned.update_idletasks()
            h = self._vpaned.winfo_height()
            if h > 50:
                self._vpaned.sashpos(0, int(h * 2 / 3))
            else:
                # Pane not sized yet; try again shortly.
                self.after(200, self._init_vpane_sash)
        except tk.TclError:
            pass

    def _cache_put(self, key, img):
        # Soft cap to bound memory; depictions are cheap to regenerate if evicted.
        if len(self._depict_cache) > 400:
            self._depict_cache.clear()
        self._depict_cache[key] = img

    def _prerender_depictions(self):
        """Warm the depiction cache for all generated molecules in the
        background at the current panel size, so even the first click on a row
        is instant. Renders one per idle tick to keep the UI responsive."""
        w, h = self._depict_size()
        pending = []
        seen = set()
        for m in self.app.project.molecules:
            smi = (m.smiles or "").strip()
            key = (smi, w, h)
            if smi and key not in self._depict_cache and smi not in seen:
                seen.add(smi)
                pending.append(smi)
        self._prerender_queue = pending
        if pending and not self._prerendering:
            self._prerendering = True
            self.after(100, self._prerender_step)

    def _prerender_step(self):
        if not self.winfo_exists():
            self._prerendering = False
            return
        if not self._prerender_queue:
            self._prerendering = False
            return
        smi = self._prerender_queue.pop(0)
        w, h = self._depict_size()
        key = (smi, w, h)
        if key not in self._depict_cache:
            img, _ = smiles_to_photoimage(smi, size=(w, h), master=self.depict_label)
            if img is not None:
                self._cache_put(key, img)
        if self._prerender_queue:
            try:
                self.after(60, self._prerender_step)
            except tk.TclError:
                self._prerendering = False
        else:
            self._prerendering = False

    # -------- open in Avogadro --------

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        mol = self.app.project.molecule_by_filename(row)
        if mol is None:
            return
        if not mol.generated or not mol.xyz_path:
            messagebox.showinfo(
                "No coordinates",
                "'{}' has no generated XYZ yet. Generate coordinates first "
                "(select it and press Ctrl+Enter, or click Generate XYZ).".format(mol.name),
            )
            return
        # Double-click = VIEW only (read-only). The geometry EDIT round-trip is on the
        # right-click menu so a plain double-click never pops the reload dialog.
        self._view_geometry(mol)

    def _on_row_right_click(self, event):
        """Row context menu: view / edit geometry, and edit the 2D structure. The edit
        round-trips live here (not on double-click) so browsing rows stays friction-free."""
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        mol = self.app.project.molecule_by_filename(row) if row else None
        menu = tk.Menu(self, tearoff=0)
        has_xyz = bool(mol and mol.generated and mol.xyz_path)
        menu.add_command(label="View geometry (3D)",
                         state=tk.NORMAL if has_xyz else tk.DISABLED,
                         command=lambda: mol and self._view_geometry(mol))
        menu.add_command(label="Edit geometry (round-trip)...",
                         state=tk.NORMAL if has_xyz else tk.DISABLED,
                         command=lambda: mol and self._edit_geometry(mol))
        menu.add_separator()
        menu.add_command(label="Edit 2D structure (round-trip)...",
                         state=tk.NORMAL if mol is not None else tk.DISABLED,
                         command=self.on_edit_smiles_external)
        # Defer grab_release to <Unmap> so the menu dismisses on click-away (on X11
        # releasing it right after tk_popup leaves it posted-but-ungrabbed).
        menu.bind("<Unmap>", lambda _e, m=menu: m.grab_release(), add="+")
        menu.tk_popup(event.x_root, event.y_root)

    def _view_geometry(self, mol):
        # type: (Molecule) -> None
        """Open the molecule's .xyz read-only in the configured 3D viewer (or the
        gateway path dialog / molden fallback)."""
        open_xyz_3d(self, self.app, mol.xyz_path)

    # -------------------------------------------------- SMILES round-trip (2D editor)

    def on_edit_smiles_external(self):
        """Launch an external 2D editor on the current structure, then read the edited
        SMILES back. Works on the selected molecule or the draft; SMILES-only (never
        touches the 3D geometry). With no SMILES yet, opens a blank canvas to draw one."""
        target = self._current_target()
        if target is None:
            messagebox.showinfo("No molecule", "Select a molecule (or start a draft) first.")
            return
        editor = self._resolve_structure_editor()
        if not editor:
            return
        smiles = (self.smiles_var.get() or "").strip()
        tmpdir = tempfile.mkdtemp(prefix="owb_smiles_")
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", target.filename or "structure") or "structure"
        molpath = os.path.join(tmpdir, stem + ".mol")
        try:
            roundtrip_mod.write_smiles_molfile(smiles, molpath)
        except ImportError:
            messagebox.showerror("RDKit needed",
                                 "Editing SMILES externally needs RDKit to build the 2D file.\n"
                                 "Install it:  pip install --user rdkit")
            return
        except ValueError as e:
            messagebox.showerror("Bad SMILES", str(e))
            return
        try:
            subprocess.Popen([editor, molpath])
        except Exception as e:
            messagebox.showerror("Launch failed",
                                 "Could not launch the 2D editor:\n{}\n\n{}".format(editor, e))
            return
        launched_at = time.time()
        EditRoundtripDialog(
            self,
            title="Edit structure in the 2D editor",
            message=("Editing the structure of '{}'.\n\n"
                     "In the editor: draw/modify the structure, then Save (an MDL molfile / .mol "
                     "is ideal; ChemDraw's .cdxml is also read). Then click Import to read the "
                     "SMILES back.\n\nThis updates the SMILES only, not the 3D geometry.\n\n"
                     "Working file:\n{}".format(target.filename or "(draft)", molpath)),
            action_label="Import edited structure",
            on_action=lambda: self._import_edited_smiles(molpath, tmpdir, launched_at))

    def _resolve_structure_editor(self):
        # type: () -> Optional[str]
        """Path to the external 2D editor (the `editor_2d_path` slot): the configured one,
        else auto-detect ChemDraw, else ask (and remember). None if the user cancels."""
        p = extprog_mod.program_path("editor_2d_path")
        if p and (os.path.isfile(p) or _on_path(p)):
            return p
        for cand in _CHEMDRAW_CANDIDATES:
            if os.path.isfile(cand):
                config_mod.set_value("editor_2d_path", cand)
                self.app.set_status("Using 2D editor: {}".format(cand))
                return cand
        messagebox.showinfo(
            "Pick a 2D editor",
            "No 2D structure editor is set yet. Choose the executable of ChemDraw, Marvin, "
            "or another editor that can open an .mol file. It'll be remembered (Settings > "
            "External programs).")
        path = filedialog.askopenfilename(
            title="Locate the 2D structure editor executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if not path:
            return None
        config_mod.set_value("editor_2d_path", path)
        return path

    def _import_edited_smiles(self, molpath, tmpdir, launched_at):
        # type: (str, str, float) -> bool
        """Read whatever the editor saved (the .mol we handed it, or a .cdxml/etc. it chose)
        back into a SMILES and, if changed, update the field after a confirm. Returns True
        to close the dialog."""
        src = roundtrip_mod.newest_structure_file(tmpdir, after=launched_at)
        if src is None:
            src = molpath if os.path.isfile(molpath) else None
        if src is None:
            messagebox.showwarning(
                "Nothing to import",
                "No saved structure file was found in\n{}\n\nMake sure you saved in the editor "
                "(an .mol molfile is safest).".format(tmpdir), parent=self)
            return False
        new_smiles = roundtrip_mod.read_structure_smiles(src)
        if not new_smiles:
            messagebox.showwarning(
                "Unreadable",
                "Couldn't read a structure from:\n{}\n\nIf it's a ChemDraw .cdxml, OpenBabel is "
                "needed to read it — try saving as an MDL molfile (.mol) instead.".format(
                    os.path.basename(src)), parent=self)
            return False
        old = (self.smiles_var.get() or "").strip()
        if new_smiles == old:
            self.app.set_status("Structure unchanged - SMILES kept.")
            return True
        if not messagebox.askyesno(
                "Update SMILES",
                "Replace the SMILES from the edited structure?\n\n"
                "Old:  {}\nNew:  {}".format(old or "(none)", new_smiles), parent=self):
            return False
        # Setting the var runs _on_field_change: updates the molecule/draft, redraws the
        # depiction, invalidates a generated geometry (-> pending), and (draft/fresh row)
        # re-fills charge/mult.
        self.smiles_var.set(new_smiles)
        self.app.set_status("Imported edited SMILES: {}".format(new_smiles))
        return True

    # ------------------------------------------------- geometry round-trip (3D editor)

    def _edit_geometry(self, mol):
        # type: (Molecule) -> None
        """Molecules-tab geometry round-trip: open the .xyz in the LOCAL 3D editor, then
        Reload re-reads the file it saved. Falls back to the read-only viewer (OpenXyzDialog
        / molden) when no local 3D editor is set (e.g. the gateway)."""
        abs_xyz = mol.xyz_path
        if not os.path.isabs(abs_xyz):
            abs_xyz = os.path.join(self.app.project.root(), abs_xyz)
        if not os.path.isfile(abs_xyz):
            messagebox.showerror("File missing", "File not found:\n{}".format(abs_xyz))
            return
        avo = extprog_mod.program_path("editor_3d_path")
        if not (avo and (os.path.isfile(avo) or _on_path(avo))):
            # No local 3D editor to round-trip with — just view it (gateway dialog / molden).
            open_xyz_3d(self, self.app, mol.xyz_path)
            return
        try:
            subprocess.Popen([avo, abs_xyz])
        except Exception as e:
            messagebox.showerror("Launch failed",
                                 "Could not launch the 3D editor:\n{}\n\n{}".format(avo, e))
            return
        EditRoundtripDialog(
            self,
            title="Edit geometry (round-trip)",
            message=("Editing the geometry of '{}' in your 3D editor.\n\n"
                     "In the editor: adjust the geometry, then Save so it overwrites the .xyz "
                     "(in Avogadro, if it asks, choose Save/Export to the same file). Come back "
                     "and click Reload to pull the changes into the app - you can reload as many "
                     "times as you like.\n\nFile:\n{}".format(mol.filename, abs_xyz)),
            action_label="Reload geometry",
            on_action=lambda: self._reload_geometry(mol, abs_xyz),
            keep_open=True)

    def _reload_geometry(self, mol, abs_xyz):
        # type: (Molecule, str) -> bool
        """Re-read the (Avogadro-saved) .xyz. The geometry is already on disk, so this
        refreshes the preview and locks the coords (a hand-edited geometry shouldn't be
        clobbered by SMILES regeneration). Returns True but keeps the dialog open."""
        try:
            atoms, _meta = coords_mod.read_xyz(abs_xyz)
        except Exception as e:
            messagebox.showerror("Reload failed", "Could not read the .xyz:\n{}".format(e),
                                 parent=self)
            return False
        if not atoms:
            messagebox.showwarning("Empty", "The .xyz has no atoms - not reloaded.", parent=self)
            return False
        mol.generated = True
        mol.gen_status = "ok"
        mol.gen_error = None
        first_edit = not mol.coords_locked
        if first_edit:
            mol.coords_locked = True   # hand-edited geometry is now authoritative
            note = "geometry hand-edited in the 3D editor"
            mol.comment = (mol.comment + "; " + note) if mol.comment else note
        # A hand-edited geometry can drift from the recorded SMILES; we can't reliably
        # re-derive SMILES from coordinates, so flag it (and the locked-coords preview
        # note repeats the caveat). Warn once per molecule, only if a SMILES is set.
        stale = bool((mol.smiles or "").strip())
        if stale and mol.filename not in self._geom_edit_warned:
            self._geom_edit_warned.add(mol.filename)
            messagebox.showinfo(
                "Geometry updated",
                "The 3D geometry of '{}' is now the hand-edited one (locked; ORCA uses it "
                "directly).\n\nHeads-up: its recorded SMILES\n  {}\nmay no longer match the "
                "edited structure. SMILES can't be reliably re-derived from coordinates, so "
                "it's kept for reference only - update it via 'Edit 2D structure' if needed."
                .format(mol.filename, mol.smiles), parent=self)
        self.app.mark_dirty()
        self._update_row(mol)
        if self._focus_filename == mol.filename:
            self._update_preview(mol)
        self.app.set_status(
            "Reloaded geometry for '{}' ({} atoms).{}".format(
                mol.filename, len(atoms), " SMILES may no longer match." if stale else ""))
        return True

    def on_add_by_name(self):
        """Resolve a chemical name/identifier to a structure over the web and add it."""
        ResolveNameDialog(self, self.app, on_commit=self._add_resolved)

    def _add_resolved(self, res):
        # type: (resolve_mod.Resolution) -> None
        """Add a molecule from a successful Resolution, recording its provenance
        (source + date) in the comment so the structure's origin is auditable."""
        smiles = (res.smiles or "").strip()
        if not smiles:
            return
        fname = self._next_numeric_filename()
        charge, mult = coords_mod.smiles_charge_and_mult(smiles)
        mol = Molecule(
            name=(res.query or smiles).strip(), filename=fname, smiles=smiles,
            charge=charge if charge is not None else (res.charge or 0),
            multiplicity=mult if mult is not None else 1,
            comment=res.provenance())
        self.app.project.molecules.append(mol)
        self.app.mark_dirty()
        self.refresh()
        self.app.set_status("Added {} (resolved from '{}').".format(fname, res.query))

    def on_paste_smiles(self):
        """Add molecules from the clipboard. If it holds an XYZ file string (a bare
        atom-count line then coordinates), add it directly as an imported, coords-
        locked structure — SMILES autodetection would otherwise mistake the atom
        lines for one-atom SMILES. Otherwise open the paste-SMILES dialog."""
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            clip = ""
        frames = coords_mod.parse_xyz_frames_text(clip)
        usable = [(atoms, meta) for atoms, meta in frames if atoms]
        if usable:
            self._add_xyz_structures(usable)
            return
        PasteSmilesDialog(self, self.app, initial_text=clip, on_commit=self._add_pasted_molecules)

    def _add_xyz_structures(self, frames):
        # type: (list) -> None
        """Add XYZ frames (from a pasted file string) as imported, coords-locked
        molecules — same treatment as Import files, but the source is the clipboard.
        A single geometry adds one molecule; a multi-frame paste adds one per frame."""
        root = self.app.project.root()
        multi = len(frames) > 1
        added = []
        for idx, (atoms, meta) in enumerate(frames):
            if not atoms:
                continue
            meta = dict(meta or {})
            if not meta.get("comment"):
                meta["comment"] = "pasted from clipboard" + (
                    " (frame {})".format(idx) if multi else "")
            fname = self._unique_filename(self._next_numeric_filename())
            target = os.path.join(root, "XYZ_INI", fname + ".xyz")
            try:
                coords_mod.write_xyz(target, atoms, meta or None)
            except Exception as e:
                messagebox.showerror("Paste XYZ", "Could not write '{}': {}".format(fname, e))
                continue
            mname = meta.get("name")
            if mname and mname == meta.get("smiles"):
                mname = None
            self.app.project.molecules.append(Molecule(
                name=mname or fname,
                filename=fname,
                smiles=meta.get("smiles"),
                gen_smiles=meta.get("gen_smiles"),
                charge=int(meta.get("charge", 0) or 0),
                multiplicity=int(meta.get("multiplicity", 1) or 1),
                comment=meta.get("comment", "") or "",
                generated=True,
                gen_status="ok",
                method="imported",
                coords_locked=True,
                xyz_path=os.path.relpath(target, root).replace("\\", "/"),
            ))
            added.append(fname)
        if added:
            self.app.mark_dirty()
            self.refresh()
            try:
                self.tree.selection_set(added[0])
                self.tree.see(added[0])
            except tk.TclError:
                pass
            self.app.set_status(
                "Added {} structure(s) from clipboard XYZ ({} atoms).".format(
                    len(added), len(frames[0][0])))
        else:
            self.app.set_status("Clipboard XYZ had no usable atoms.")

    def _add_smiles_entries(self, entries):
        # type: (list) -> list
        """Append (smiles, name_or_None) pairs as new PENDING molecules (to be
        generated from SMILES). Filenames are the next zero-padded numeric (so
        paths stay free of whitespace/special characters SLURM/SUSE may choke on);
        the name column becomes the display name. Charge & multiplicity are
        auto-filled from each SMILES via RDKit. Returns the new filenames.
        Shared by the paste-SMILES dialog and SMILES-list file import."""
        added = []
        for smiles, name in entries:
            smiles = (smiles or "").strip()
            if not smiles:
                continue
            fname = self._next_numeric_filename()
            charge, mult = coords_mod.smiles_charge_and_mult(smiles)
            display_name = (name or "").strip() or smiles
            self.app.project.molecules.append(Molecule(
                name=display_name,
                filename=fname,
                smiles=smiles,
                charge=charge if charge is not None else 0,
                multiplicity=mult if mult is not None else 1,
            ))
            added.append(fname)
        return added

    def _add_pasted_molecules(self, entries):
        # type: (list) -> None
        """Add a list of (smiles, name_or_None) pairs as new molecules."""
        added = self._add_smiles_entries(entries)
        if added:
            self.app.mark_dirty()
            self.refresh()
            preview = ", ".join(added[:5]) + (", ..." if len(added) > 5 else "")
            self.app.set_status("Pasted {} molecule(s): {}".format(len(added), preview))
        else:
            self.app.set_status("Paste produced no usable SMILES.")

    @staticmethod
    def _slug_from_name(name, max_len=24):
        # type: (Optional[str], int) -> str
        s = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("_")
        return s[:max_len]


def _on_path(name):
    # type: (str) -> bool
    """True if `name` resolves to an executable on PATH (like `which`)."""
    import shutil
    try:
        return shutil.which(name) is not None
    except Exception:
        return False


# Common install locations of ChemDraw, tried when no 2D editor is configured yet.
# (Revvity/PerkinElmer/CambridgeSoft across recent versions.) The user can override
# via Settings > 2D structure editor.
_CHEMDRAW_CANDIDATES = [
    r"C:\Program Files\RevvitySignalsSoftware\ChemDrawApplications\ChemDraw\ChemDraw.exe",
    r"C:\Program Files (x86)\RevvitySignalsSoftware\ChemDrawApplications\ChemDraw\ChemDraw.exe",
    r"C:\Program Files\PerkinElmerInformatics\ChemOffice\ChemDraw\ChemDraw.exe",
    r"C:\Program Files (x86)\PerkinElmerInformatics\ChemOffice\ChemDraw\ChemDraw.exe",
    r"C:\Program Files\CambridgeSoft\ChemOffice\ChemDraw\ChemDraw.exe",
    r"C:\Program Files (x86)\CambridgeSoft\ChemOffice\ChemDraw\ChemDraw.exe",
]


class EditRoundtripDialog(tk.Toplevel):
    """Non-modal 'edit then re-import' handshake for the external-editor round-trips.

    Shows instructions while the external editor (ChemDraw / Avogadro) is open, with an
    action button (Import / Reload) that runs `on_action` — which returns True on success.
    Non-modal so the app and the editor coexist; `keep_open` leaves it up after a
    successful action so a geometry edit can be reloaded repeatedly."""

    def __init__(self, parent, title, message, action_label, on_action, keep_open=False):
        super().__init__(parent)
        self.title(title)
        self._on_action = on_action
        self._keep_open = keep_open
        ttk.Label(self, text=message, justify=tk.LEFT, wraplength=460).pack(
            side=tk.TOP, fill=tk.X, padx=16, pady=(14, 8))
        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 12))
        ttk.Button(btns, text="Cancel" if not keep_open else "Done",
                   command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text=action_label, command=self._do).pack(side=tk.RIGHT, padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        fit_to_content(self)
        try:
            self.transient(parent.winfo_toplevel())
        except tk.TclError:
            pass
        self.lift()

    def _do(self):
        try:
            ok = self._on_action()
        except Exception as e:
            messagebox.showerror("Failed", str(e), parent=self)
            return
        if ok and not self._keep_open:
            self.destroy()


def open_in_molden(parent, xyz_path):
    # type: (tk.Misc, str) -> None
    """First-attempt launch of molden on the Lido gateway, displayed over the
    X-forwarded session. molden is a cluster *module*, so it isn't on PATH until
    loaded — we run it through a login shell that does `module load molden`.
    The molden module name can be overridden via the 'molden_module' config key.
    Linux/gateway only; on Windows there's no molden to launch."""
    if platform.system() == "Windows":
        messagebox.showinfo(
            "Gateway only",
            "molden runs on the Lido gateway (over X-forwarding), not on Windows. "
            "On your PC, open the .xyz with your local Avogadro instead.")
        return
    import shlex
    module = config_mod.get("molden_module", "molden") or "molden"
    # `bash -lc` sources the login profile so `module` is defined; load molden,
    # then exec it on the file. 2>/dev/null hides the module chatter.
    cmd = "module load {} 2>/dev/null; exec molden {}".format(
        shlex.quote(module), shlex.quote(xyz_path))
    try:
        subprocess.Popen(["bash", "-lc", cmd])
    except Exception as e:
        messagebox.showerror(
            "molden launch failed",
            "Could not start molden:\n{}\n\n"
            "Check it's available with `module avail molden`, and that you're in an "
            "X-forwarded session.".format(e))


def open_xyz_3d(parent, app, xyz_path, slot="viewer_3d_path"):
    """Open an .xyz read-only in an external program if it's present on THIS machine,
    otherwise the OpenXyzDialog (which offers molden on the gateway + hands you the
    path). `slot` selects which configured program: `viewer_3d_path` (default) for a
    single geometry, `traj_viewer_path` for a multi-frame optimisation trajectory
    (PyMOL by preference) — each falls back to the 3D viewer if unset. Shared by the
    Molecules / Calculations / Workflow tabs."""
    abs_xyz = xyz_path
    if not os.path.isabs(abs_xyz):
        abs_xyz = os.path.join(app.project.root(), abs_xyz)
    if not os.path.isfile(abs_xyz):
        messagebox.showerror("File missing", "File not found:\n{}".format(abs_xyz))
        return
    # Only launch directly if the program is on the machine the app *runs* on (on the
    # gateway it isn't — the local viewer is on the Windows PC, unreachable from a
    # cluster process), in which case the dialog hands you the path / offers molden.
    viewer = extprog_mod.program_path(slot)
    if viewer and (os.path.isfile(viewer) or _on_path(viewer)):
        try:
            subprocess.Popen([viewer, abs_xyz])
            app.set_status("Opened {} in the external viewer.".format(os.path.basename(abs_xyz)))
            return
        except Exception as e:
            messagebox.showerror("Launch failed",
                                 "Could not launch the viewer:\n{}\n\n{}".format(viewer, e))
            return
    OpenXyzDialog(parent, abs_xyz)


class OpenXyzDialog(tk.Toplevel):
    """Shown when no local Avogadro is available (the usual cluster case).

    The app runs on the gateway, so it can't launch the Avogadro on your PC.
    Instead we hand you the file's path: open it from MobaXterm's file browser
    (double-click there) and MobaXterm downloads it and opens it with your local
    Avogadro. A 'Copy path' button makes navigating MobaXterm's SFTP pane easy.
    """

    def __init__(self, parent, xyz_path):
        super().__init__(parent)
        self.title("Open structure")
        self.geometry("620x300")
        self._parent = parent
        self._xyz_path = xyz_path

        msg = ("Avogadro isn't available on the machine running ORCA Workbench, so the app "
               "can't open it for you (this is normal when running on the Lido gateway — "
               "your Avogadro is on your Windows PC).\n\n"
               "Options: open the path below from MobaXterm's file browser (it downloads the "
               "file and opens your local Avogadro), or try molden directly on the gateway "
               "(it displays over the X-forwarded session — needs `module load molden`).")
        ttk.Label(self, text=msg, justify=tk.LEFT, wraplength=580).pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))

        path_frame = ttk.LabelFrame(self, text="File path on the cluster")
        path_frame.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        entry = ttk.Entry(path_frame)
        entry.insert(0, xyz_path)
        entry.configure(state="readonly")
        entry.pack(side=tk.TOP, fill=tk.X, padx=6, pady=6)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Copy path", command=self._copy).pack(side=tk.RIGHT, padx=4)
        b_molden = ttk.Button(btns, text="Open in molden (gateway)", command=self._open_molden)
        b_molden.pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Avogadro is on THIS machine...",
                   command=self._set_local).pack(side=tk.LEFT, padx=4)
        tip(b_molden, "First attempt: launch molden on the gateway via "
                      "`module load molden && molden <file>`, displayed over X-forwarding. "
                      "Needs the molden module (module avail molden) and a working X session "
                      "(MobaXterm provides one). Linux/gateway only.")

        self.bind("<Escape>", lambda e: self.destroy())
        make_modal(self, parent)

    def _open_molden(self):
        open_in_molden(self, self._xyz_path)

    def _copy(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self._xyz_path)
        except tk.TclError:
            pass

    def _set_local(self):
        # For users who actually do have Avogadro where the app runs.
        dlg = AvogadroPathDialog(self)
        if dlg.result:
            self.destroy()
            try:
                subprocess.Popen([dlg.result, self._xyz_path])
            except Exception as e:
                messagebox.showerror("Launch failed", str(e))


class AvogadroPathDialog(tk.Toplevel):
    """Prompt for the Avogadro executable path and remember it in app config.
    self.result holds the chosen path (or None if cancelled)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.result = None  # type: Optional[str]
        self.title("Set Avogadro path")
        self.resizable(False, False)

        msg = ("Set this ONLY if Avogadro is installed on the same machine the app is "
               "running on. If you run ORCA Workbench on the Lido gateway via MobaXterm, your "
               "Avogadro is on your Windows PC and the app can't reach it from the cluster — "
               "use MobaXterm's file browser to open .xyz files instead (Cancel this).\n\n"
               "If you DO have Avogadro here (a local Windows run, or an Avogadro module on "
               "the cluster), enter its path/command. Remembered in ~/.orca_workbench.json. "
               "On Windows: full path to Avogadro2.exe. On Linux: 'avogadro'/'avogadro2' if "
               "it's on PATH.")
        ttk.Label(self, text=msg, wraplength=460, justify=tk.LEFT).pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))

        row = ttk.Frame(self)
        row.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        ttk.Label(row, text="Avogadro path / command:").pack(side=tk.LEFT)
        self.var = tk.StringVar(value=config_mod.get("avogadro_path", "") or "")
        entry = ttk.Entry(row, textvariable=self.var, width=44)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))
        ttk.Button(row, text="Browse...", command=self._browse).pack(side=tk.LEFT)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save & open", command=self._ok).pack(side=tk.RIGHT, padx=4)

        entry.focus_set()
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        make_modal(self, parent)
        self.wait_window()

    def _browse(self):
        path = filedialog.askopenfilename(title="Locate the Avogadro executable")
        if path:
            self.var.set(path)

    def _ok(self):
        path = extprog_mod.strip_path_quotes(self.var.get())
        if not path:
            messagebox.showinfo("Empty", "Enter a path or command, or Cancel.", parent=self)
            return
        if not (os.path.isfile(path) or _on_path(path)):
            if not messagebox.askyesno(
                "Not found",
                "'{}' isn't a file and isn't on your PATH. Save it anyway?".format(path),
                parent=self,
            ):
                return
        config_mod.set_value("avogadro_path", path)
        self.result = path
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ConformerSelectDialog(tk.Toplevel):
    """Ask which structure(s) to import from a multi-record file (multi-conformer
    SDF, multi-molecule file, multi-frame xyz, ...).

    self.result is (spec, remember) or None if skipped. `spec` is the raw text
    the user entered (see _parse_index_spec for the accepted forms); `remember`
    re-applies it to the rest of the current import batch without prompting."""

    def __init__(self, parent, filename, n_structures):
        super().__init__(parent)
        self.result = None  # type: Optional[tuple]
        self._n = int(n_structures)
        self.title("Select structures")
        self.resizable(False, False)

        msg = ("'{}' contains {} structures (indices 0..{}).\n\n"
               "Which to import? You can pick one, several, or all:").format(
                   filename, self._n, self._n - 1)
        ttk.Label(self, text=msg, wraplength=440, justify=tk.LEFT).pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(self, text="Examples:   0      0,2,5      0-3      all      -1 (last)",
                  justify=tk.LEFT, foreground="#555555").pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(0, 6))

        row = ttk.Frame(self)
        row.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        ttk.Label(row, text="Structures:").pack(side=tk.LEFT)
        self.var = tk.StringVar(value="0")
        self.entry = ttk.Entry(row, textvariable=self.var, width=24)
        self.entry.pack(side=tk.LEFT, padx=(6, 0))

        self.remember_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Remember this selection for the rest of this import",
                        variable=self.remember_var).pack(
            side=tk.TOP, anchor=tk.W, padx=12, pady=(4, 0))

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Skip file", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Import", command=self._ok).pack(side=tk.RIGHT, padx=4)

        self.entry.focus_set()
        self.entry.select_range(0, tk.END)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        make_modal(self, parent)
        self.wait_window()

    def _ok(self):
        spec = self.var.get().strip()
        chosen = coords_mod.parse_structure_selection(spec, self._n)
        if not chosen:
            messagebox.showinfo(
                "Nothing selected",
                "'{}' didn't resolve to any structure in 0..{}.\n"
                "Try e.g. 0, or 0,2,5, or 0-3, or all, or -1.".format(spec, self._n - 1),
                parent=self)
            return
        self.result = (spec, bool(self.remember_var.get()))
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ResolveNameDialog(tk.Toplevel):
    """Resolve a chemical name / SMILES / InChI / CAS to a structure over the web
    (OPSIN + PubChem), preview the 2D depiction, and add it on confirm.

    The network call runs on a worker thread so the UI never freezes; the worker
    only stashes the result and the main thread picks it up via after() polling
    (no cross-thread Tk access). Degrades gracefully with no internet."""

    def __init__(self, parent, app, on_commit=None):
        tk.Toplevel.__init__(self, parent)
        self.app = app
        self._on_commit = on_commit
        self._cache = {}            # reused across lookups in this dialog
        self._result = None
        self._svc = None
        self._pending = False
        self._img = None            # keep a ref so the PhotoImage isn't GC'd
        self.title("Add molecule by name")

        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(top, text="Name / SMILES / InChI / CAS:").pack(side=tk.LEFT)
        self.query_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.query_var, width=40)
        ent.pack(side=tk.LEFT, padx=6)
        ent.bind("<Return>", lambda e: self._start_resolve())
        ttk.Button(top, text="Resolve", command=self._start_resolve).pack(side=tk.LEFT)
        ttk.Button(top, text="Test connection", command=self._test_services).pack(side=tk.LEFT, padx=(6, 0))

        self.status_var = tk.StringVar(value="Type an identifier and press Resolve.")
        ttk.Label(self, textvariable=self.status_var, foreground="#555").pack(anchor=tk.W, padx=10)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self.depict_label = ttk.Label(body)
        self.depict_label.pack(side=tk.LEFT, padx=(0, 10))
        det = ttk.Frame(body)
        det.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.detail_var = tk.StringVar(value="")
        ttk.Label(det, textvariable=self.detail_var, justify=tk.LEFT, wraplength=300).pack(anchor=tk.NW)
        self.sugg_frame = ttk.Frame(det)
        self.sugg_frame.pack(anchor=tk.NW, pady=4)
        # Fragment chooser, shown only for multi-component results (salts, complexes).
        self.frag_var = tk.StringVar(value="")
        self.frag_frame = ttk.Frame(det)
        self.frag_frame.pack(anchor=tk.NW, fill=tk.X, pady=2)

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=10, pady=(4, 10))
        self.add_btn = ttk.Button(btns, text="Add", command=self._commit, state=tk.DISABLED)
        self.add_btn.pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=6)

        self.geometry("560x340")
        ent.focus_set()
        make_modal(self, parent)

    def _start_resolve(self):
        q = self.query_var.get().strip()
        if not q or self._pending:
            return
        self._result = None
        self.add_btn.config(state=tk.DISABLED)
        self._clear_suggestions()
        self._clear_fragments()
        self.detail_var.set("")
        self.depict_label.config(image="")
        self._img = None
        self.status_var.set("Resolving '{}' …".format(q))
        self._pending = True
        threading.Thread(target=self._worker, args=(q,), daemon=True).start()
        self.after(100, self._poll)

    def _worker(self, q):
        # No Tk calls here — only stash the result for the main thread to read.
        try:
            self._result = resolve_mod.resolve(q, cache=self._cache)
        except Exception as e:
            self._result = resolve_mod.Resolution(query=q, error="resolver error: {}".format(e))
        self._pending = False

    def _poll(self):
        if self._pending:
            self.after(100, self._poll)
            return
        if self.winfo_exists():
            self._show(self._result)

    def _show(self, res):
        if res is None:
            return
        self._clear_fragments()
        if not res.ok:
            self.status_var.set("No structure found.")
            self.detail_var.set(res.error or "could not resolve.")
            self._show_suggestions(res.candidates)
            fit_to_content(self)
            return
        self.status_var.set("Resolved via {}.".format(res.source or "?"))
        self._refresh_detail(res)
        self._render_fragments(res)
        self.add_btn.config(state=tk.NORMAL)
        # The chooser/suggestions are added after the async resolve, so grow the
        # dialog to fit them — otherwise they sit below the initial fixed size and
        # are hidden until the window is dragged larger.
        fit_to_content(self)

    def _refresh_detail(self, res):
        """(Re)draw the detail text + 2D depiction for the result's current
        SMILES — called on first show and whenever the fragment choice changes."""
        lines = ["SMILES:  {}".format(res.smiles),
                 "Formula: {}    charge {}".format(
                     res.formula or "?", res.charge if res.charge is not None else "?")]
        if res.note:
            lines.append("Note: " + res.note)
        lines.append(res.provenance())
        self.detail_var.set("\n".join(lines))
        img, _err = smiles_to_photoimage(res.smiles, size=(220, 200), master=self.depict_label)
        self._img = img
        self.depict_label.config(image=img if img else "")

    def _render_fragments(self, res):
        """When the structure had several components, offer a radio choice of
        which fragment to keep (default = the largest, matching res.smiles), plus
        'keep all'. Fragments containing a metal are flagged so a coordination
        complex isn't mistaken for a counter-ion to discard."""
        self._clear_fragments()
        frags = res.fragments or []
        if len(frags) <= 1:
            return
        ttk.Label(self.frag_frame, text="Multiple fragments — choose which to keep:",
                  foreground="#555").pack(anchor=tk.W)
        self.frag_var.set(res.smiles)   # default = largest (already in res.smiles)
        for f in frags:
            metal = " [metal]" if f.get("has_metal") else ""
            label = "{}  charge {}{}".format(f.get("formula") or f["smiles"],
                                             f.get("charge"), metal)
            ttk.Radiobutton(self.frag_frame, text=label, value=f["smiles"],
                            variable=self.frag_var,
                            command=lambda r=res: self._select_fragment(r)).pack(anchor=tk.W)
        ttk.Radiobutton(self.frag_frame, text="Keep ALL fragments (multi-component)",
                        value="__all__", variable=self.frag_var,
                        command=lambda r=res: self._select_fragment(r)).pack(anchor=tk.W)

    def _select_fragment(self, res):
        choice = self.frag_var.get()
        frags = res.fragments or []
        if choice == "__all__":
            res.smiles = ".".join(f["smiles"] for f in frags)
            res.charge = sum(int(f.get("charge") or 0) for f in frags)
            res.formula = None
            res.note = "kept ALL {} fragments (multi-component)".format(len(frags))
        else:
            f = next((f for f in frags if f["smiles"] == choice), None)
            if f is None:
                return
            res.smiles, res.formula, res.charge = f["smiles"], f.get("formula"), f.get("charge")
            if f is frags[0]:
                res.note = ("stripped {} extra fragment(s) (salt/solvent); kept the "
                            "largest".format(len(frags) - 1))
            else:
                res.note = ("kept the selected fragment; dropped the other {}"
                            .format(len(frags) - 1))
        self._refresh_detail(res)

    def _clear_fragments(self):
        for w in self.frag_frame.winfo_children():
            w.destroy()

    def _show_suggestions(self, names):
        self._clear_suggestions()
        if not names:
            return
        ttk.Label(self.sugg_frame, text="Did you mean:").pack(anchor=tk.W)
        row = ttk.Frame(self.sugg_frame)
        row.pack(anchor=tk.W)
        for n in names[:5]:
            ttk.Button(row, text=n, command=lambda nn=n: self._retry(nn)).pack(side=tk.LEFT, padx=2)

    def _test_services(self):
        """Probe OPSIN / PubChem / RDKit so the user can tell whether Add-by-name
        will work here (e.g. firewalled gateway). Threaded, like the resolve path."""
        if self._pending:
            return
        self._svc = None
        self._clear_suggestions()
        self._clear_fragments()
        self.add_btn.config(state=tk.DISABLED)
        self.status_var.set("Testing OPSIN / PubChem reachability …")
        self._pending = True
        threading.Thread(target=self._svc_worker, daemon=True).start()
        self.after(100, self._svc_poll)

    def _svc_worker(self):
        try:
            self._svc = resolve_mod.check_services()
        except Exception as e:
            self._svc = [("connectivity", False, str(e))]
        self._pending = False

    def _svc_poll(self):
        if self._pending:
            self.after(100, self._svc_poll)
            return
        if not self.winfo_exists():
            return
        rows = self._svc or []
        allok = all(ok for _, ok, _ in rows)
        self.status_var.set("Connectivity OK - Add by name will work here." if allok
                            else "Some services unreachable - Add by name may not work here.")
        self.detail_var.set("\n".join(
            "{} {}  ({})".format("OK  " if ok else "FAIL", label, detail)
            for label, ok, detail in rows))
        fit_to_content(self)

    def _retry(self, name):
        self.query_var.set(name)
        self._start_resolve()

    def _clear_suggestions(self):
        for w in self.sugg_frame.winfo_children():
            w.destroy()

    def _commit(self):
        if self._result and self._result.ok and self._on_commit:
            self._on_commit(self._result)
        self.destroy()


class PasteSmilesDialog(tk.Toplevel):
    """Modal dialog: edit a paste of SMILES, preview parsed rows, commit on OK.

    The text area is pre-populated from the clipboard. Re-parse happens whenever
    you click the Preview button or change the text and tab out. Charge & mult
    are computed from each SMILES via RDKit when available.
    """

    def __init__(self, parent, app, initial_text="", on_commit=None):
        super().__init__(parent)
        self.title("Paste SMILES")
        self.geometry("780x540")
        self.app = app
        self._on_commit = on_commit

        intro = ("Paste SMILES below. Accepts: dot-separated single line (ChemDraw multi-mol "
                 "copy), one SMILES per line, or two columns of SMILES + name in either order "
                 "(whitespace/comma/tab/semicolon delimited). Lines starting with '#' are skipped.")
        ttk.Label(self, text=intro, wraplength=740, justify=tk.LEFT, foreground="#444").pack(
            side=tk.TOP, fill=tk.X, padx=8, pady=(8, 4))

        text_frame = ttk.LabelFrame(self, text="Clipboard contents (editable)")
        text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.text = tk.Text(text_frame, wrap="word", height=8, font=("Courier", 10), undo=True)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ts = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        ts.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.configure(yscrollcommand=ts.set)
        install_text_shortcuts(self.text)
        self.text.insert("1.0", initial_text or "")

        mid = ttk.Frame(self)
        mid.pack(side=tk.TOP, fill=tk.X, padx=8, pady=4)
        ttk.Button(mid, text="Preview / re-parse", command=self.on_preview).pack(side=tk.LEFT)
        self.summary_var = tk.StringVar(value="")
        ttk.Label(mid, textvariable=self.summary_var, foreground="#444").pack(side=tk.LEFT, padx=10)

        prev_frame = ttk.LabelFrame(self, text="Parsed rows (will be added on OK)")
        prev_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=4)
        columns = ("smiles", "name", "valid", "charge", "mult")
        self.preview_tree = ttk.Treeview(prev_frame, columns=columns, show="headings", height=8)
        for col, label, width in [
            ("smiles", "SMILES", 320),
            ("name", "Name", 150),
            ("valid", "Valid?", 60),
            ("charge", "Q", 40),
            ("mult", "M", 40),
        ]:
            self.preview_tree.heading(col, text=label)
            self.preview_tree.column(col, width=width, anchor=tk.W)
        self.preview_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        psb = ttk.Scrollbar(prev_frame, orient=tk.VERTICAL, command=self.preview_tree.yview)
        psb.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_tree.configure(yscrollcommand=psb.set)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        self.ok_btn = ttk.Button(btns, text="Add to project", command=self.on_ok)
        self.ok_btn.pack(side=tk.RIGHT, padx=4)

        self._entries = []  # type: list
        self.on_preview()
        make_modal(self, parent)

    def on_preview(self):
        text = self.text.get("1.0", tk.END)
        entries = coords_mod.parse_smiles_list(text)
        self._entries = entries
        self.preview_tree.delete(*self.preview_tree.get_children())
        n_valid = 0
        for i, (s, n) in enumerate(entries):
            valid = coords_mod.smiles_is_valid(s)
            if valid:
                n_valid += 1
                ch, mu = coords_mod.smiles_charge_and_mult(s)
            else:
                ch, mu = (None, None)
            self.preview_tree.insert("", tk.END, iid=str(i), values=(
                s, n or "", "yes" if valid else "?",
                "" if ch is None else ch, "" if mu is None else mu,
            ))
        if entries:
            self.summary_var.set(
                "{} row(s), {} parse as valid SMILES via RDKit.".format(len(entries), n_valid)
            )
        else:
            self.summary_var.set("No rows parsed.")

    def on_ok(self):
        if not self._entries:
            self.destroy()
            return
        if self._on_commit is not None:
            self._on_commit(self._entries)
        self.destroy()
