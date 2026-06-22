"""Molecules tab — add/edit molecules, generate 3D coordinates from SMILES."""

import os
import platform
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from orca_workbench.core import config as config_mod
from orca_workbench.core import coords as coords_mod
from orca_workbench.core import resolve as resolve_mod
from orca_workbench.core.project import Molecule
from orca_workbench.ui.depict import smiles_to_photoimage
from orca_workbench.ui.modal import make_modal
from orca_workbench.ui.shortcuts import install_text_shortcuts
from orca_workbench.ui.tooltip import tip


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
        b_import = ttk.Button(toolbar, text="Import .xyz...", command=self.on_import_xyz)
        b_paste = ttk.Button(toolbar, text="Paste SMILES...", command=self.on_paste_smiles)
        b_name = ttk.Button(toolbar, text="Add by name...", command=self.on_add_by_name)
        for b in (b_add, b_remove, b_gen, b_gen_all, b_import, b_paste, b_name):
            b.pack(side=tk.LEFT, padx=2)
        tip(b_name, "Look up a molecule by chemical name (IUPAC or common), CAS number, "
                    "InChI, or SMILES via public web services (OPSIN + PubChem), preview the "
                    "2D structure, and add it. Needs internet; with none, only SMILES/InChI "
                    "work. The structure's source is recorded in the molecule's comment.")
        tip(b_paste, "Open a dialog showing what's currently in your clipboard parsed as a "
                     "list of SMILES. You can edit before committing. Same effect as Ctrl+V "
                     "while hovering the molecule list.\n\n"
                     "Accepts: dot-separated single line (ChemDraw multi-mol copy), one SMILES "
                     "per line, or two-column SMILES + name. Auto-detects which column is "
                     "which via RDKit. Charge & multiplicity auto-filled per molecule.")
        tip(b_add, "If nothing is selected: commits the current form values as a new molecule, "
                   "then starts a fresh draft for the next one. If anything IS selected (one or "
                   "many): clears the selection and starts a fresh draft (no duplicate added).")
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
        tip(b_import, "Add an existing .xyz file as a molecule. Copies the file into XYZ_INI/ "
                      "and reads name/charge/multiplicity from the JSON metadata in the comment "
                      "line if present (the format make_coords.py writes).")

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=2)

        # Status column leftmost so it's never hidden by horizontal squeeze.
        # Narrow columns are fixed-width (stretch=False); name and smiles
        # absorb whatever horizontal space is left, so resizing the window
        # widens the readable fields instead of squeezing the labels.
        columns = ("status", "name", "filename", "smiles", "charge", "mult")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        col_specs = [
            # (id, label, width, minwidth, stretch)
            ("status",   "Status",   80,  60,  False),
            ("name",     "Name",     180, 80,  True),
            ("filename", "Filename", 80,  60,  False),
            ("smiles",   "SMILES",   280, 100, True),
            ("charge",   "Q",        35,  30,  False),
            ("mult",     "M",        35,  30,  False),
        ]
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
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
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
        self.tree.bind("<Double-1>", self._on_double_click)
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
                       "  Ctrl+V — paste SMILES list from clipboard\n"
                       "  Double-click — view the molecule (opens local Avogadro if available,\n"
                       "                 else shows the .xyz path to open via MobaXterm)\n\n"
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
        self.depict_label = tk.Label(depict_frame, anchor=tk.CENTER, background="white",
                                     text="(no structure)", foreground="#888")
        self.depict_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
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
                tags=(mol.gen_status,),
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
            self._focus_filename = new_focus
            self._draft = None
            mol = self.app.project.molecule_by_filename(new_focus)
            if mol is None:
                return
            self._set_form_state("normal")
            self._populate_form_from(mol)
            self._edit_frame.configure(text="Edit selected molecule")
            self._update_preview(mol)
            return
        # Multi-select mode.
        self._focus_filename = None
        self._draft = None
        self._enter_multi_select_mode(n)

    def _enter_drafting_mode(self):
        """Discard any current draft and start a fresh blank one bound to the form."""
        self._focus_filename = None
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
                    target.filename = new_fname
                    self._focus_filename = new_fname
            # SMILES change on a real molecule invalidates the existing geometry.
            if field == "smiles" and target.gen_status == "ok":
                target.generated = False
                target.gen_status = "pending"
                target.gen_error = None
            self.app.mark_dirty()
            self._refresh_row(target)
        else:
            # Editing a draft — just store the filename literally; uniqueness check on commit.
            target.filename = new_fname

        # Auto-fill charge/mult from SMILES on draft-mode SMILES edits.
        if field == "smiles" and self._focus_filename is None:
            self._maybe_auto_charge_mult(target)

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
                tags=(mol.gen_status,),
            )
        else:
            self.refresh()

    def _row_values(self, mol):
        # type: (Molecule) -> tuple
        """Build the Treeview values tuple for one molecule, matching the
        configured column order (status, name, filename, smiles, charge, mult)."""
        if mol.gen_status == "ok":
            status = "ok ({})".format(mol.method or "?")
        elif mol.gen_status == "failed":
            status = "failed"
        else:
            status = "pending"
        return (status, mol.name, mol.filename, mol.smiles or "", mol.charge, mol.multiplicity)

    def on_add(self):
        if len(self.tree.selection()) > 0:
            # Any rows selected (single or multi) — switch to fresh draft for next entry.
            self._enter_drafting_mode()
            return
        # Drafting mode — commit the current draft as a new molecule.
        if self._draft is None:
            self._enter_drafting_mode()
            return
        draft = self._draft
        # Pre-filled with auto-numeric; user may have overridden. Defensive
        # fallback if they cleared the field entirely.
        fname = (draft.filename or "").strip()
        if not fname:
            fname = self._next_numeric_filename()
        fname = self._unique_filename(fname)
        draft.filename = fname
        if not draft.name:
            draft.name = fname
        self.app.project.molecules.append(draft)
        self.app.mark_dirty()
        self._draft = None
        # Refresh, then start a new draft for follow-up entries.
        self.refresh()
        self._enter_drafting_mode()
        self.app.set_status("Added molecule '{}'.".format(fname))

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
            # Bulk generate selected.
            mols = [self.app.project.molecule_by_filename(f) for f in selected]
            mols = [m for m in mols if m is not None and m.smiles]
            if not mols:
                messagebox.showinfo("Nothing to generate", "None of the selected molecules has a SMILES.")
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
            self.on_add()  # commits the draft and starts a new one
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
                   if m.gen_status == "pending" and m.smiles]
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

    def on_import_xyz(self):
        path = filedialog.askopenfilename(
            title="Import XYZ",
            filetypes=[("XYZ files", "*.xyz"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            atoms, metadata = coords_mod.read_xyz(path)
        except Exception as e:
            messagebox.showerror("Read failed", str(e))
            return
        fname = os.path.splitext(os.path.basename(path))[0]
        fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", fname)
        while self.app.project.molecule_by_filename(fname):
            fname = fname + "_2"
        target = os.path.join(self.app.project.root(), "XYZ_INI", fname + ".xyz")
        coords_mod.write_xyz(target, atoms, metadata)
        meta = metadata or {}
        mol = Molecule(
            name=meta.get("name") or fname,
            filename=fname,
            smiles=meta.get("smiles"),
            gen_smiles=meta.get("gen_smiles"),
            charge=int(meta.get("charge", 0)),
            multiplicity=int(meta.get("multiplicity", 1)),
            comment=meta.get("comment", "") or "",
            generated=True,
            gen_status="ok",
            method="imported",
            xyz_path=os.path.relpath(target, self.app.project.root()).replace("\\", "/"),
        )
        self.app.project.molecules.append(mol)
        self.app.mark_dirty()
        self.refresh()
        self.tree.selection_set(fname)

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
        abs_xyz = mol.xyz_path
        if not os.path.isabs(abs_xyz):
            abs_xyz = os.path.join(self.app.project.root(), abs_xyz)
        if not os.path.isfile(abs_xyz):
            messagebox.showerror("File missing", "XYZ file not found:\n{}".format(abs_xyz))
            return
        # Only launch directly if Avogadro is present on the machine the app is
        # *running* on. When the app runs on the Lido gateway (Linux) and you're
        # connected via MobaXterm, your Avogadro is on your Windows PC — there's
        # no way for a cluster process to reach it. In that case we just hand you
        # the path so you can open it from MobaXterm's file browser (which
        # downloads the file and opens it with your local Avogadro).
        avo = config_mod.get("avogadro_path", "")
        if avo and (os.path.isfile(avo) or _on_path(avo)):
            try:
                subprocess.Popen([avo, abs_xyz])
                self.app.set_status("Launched Avogadro for {}.".format(os.path.basename(abs_xyz)))
                return
            except Exception as e:
                messagebox.showerror("Launch failed", "Could not launch Avogadro:\n{}\n\n{}".format(avo, e))
                return
        OpenXyzDialog(self, abs_xyz)

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
        """Open the paste-SMILES dialog pre-filled from the clipboard."""
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            clip = ""
        PasteSmilesDialog(self, self.app, initial_text=clip, on_commit=self._add_pasted_molecules)

    def _add_pasted_molecules(self, entries):
        # type: (list) -> None
        """Add a list of (smiles, name_or_None) pairs as new molecules. Filenames
        always default to the next zero-padded numeric (so paths stay free of
        whitespace and special characters that SLURM/SUSE may choke on).
        Charge & multiplicity auto-filled from each SMILES via RDKit."""
        added = []
        for smiles, name in entries:
            smiles = (smiles or "").strip()
            if not smiles:
                continue
            fname = self._next_numeric_filename()
            charge, mult = coords_mod.smiles_charge_and_mult(smiles)
            display_name = (name or "").strip() or smiles
            mol = Molecule(
                name=display_name,
                filename=fname,
                smiles=smiles,
                charge=charge if charge is not None else 0,
                multiplicity=mult if mult is not None else 1,
            )
            self.app.project.molecules.append(mol)
            added.append(fname)
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
        path = self.var.get().strip()
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
        if not res.ok:
            self.status_var.set("No structure found.")
            self.detail_var.set(res.error or "could not resolve.")
            self._show_suggestions(res.candidates)
            return
        self.status_var.set("Resolved via {}.".format(res.source or "?"))
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
        self.add_btn.config(state=tk.NORMAL)

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
