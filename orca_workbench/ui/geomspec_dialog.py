"""Dialog to edit geometry constraints + a relaxed surface scan for an OPT job.

Edits a core.geomspec spec dict: freeze bonds/angles/dihedrals/Cartesian positions
(optionally at a value) and/or one relaxed scan of a coordinate over a range. Shows
the molecule's atom list for reference (ORCA indices are 0-based). Reusable by the
Calculations tab (per-calc) and the Workflow Optimize node.
"""

import re
import tkinter as tk
from tkinter import messagebox, ttk

from orca_workbench.core import geomspec as G
from orca_workbench.ui.modal import fit_to_content, make_modal

# combobox display -> coordinate-type letter
_TYPE_CHOICES = ["B: bond (2 atoms)", "A: angle (3)", "D: dihedral (4)", "C: cartesian (1)"]
_SCAN_CHOICES = ["B: bond", "A: angle", "D: dihedral"]


def _letter(choice):
    return (choice or "B")[0]


def _choice_for(letter):
    for c in _TYPE_CHOICES:
        if c[0] == letter:
            return c
    return _TYPE_CHOICES[0]


def _scan_choice_for(letter):
    for c in _SCAN_CHOICES:
        if c[0] == letter:
            return c
    return _SCAN_CHOICES[0]


def _parse_atoms(text):
    return [int(x) for x in re.split(r"[,\s]+", (text or "").strip()) if x != ""]


class GeomSpecDialog(tk.Toplevel):
    def __init__(self, parent, atoms, spec, on_save,
                 title="Geometry constraints / scan", view_xyz=None):
        # type: (tk.Misc, list, dict, callable, str, callable) -> None
        super().__init__(parent)
        self.title(title)
        self._atoms = atoms or []
        self._on_save = on_save
        self._view_xyz = view_xyz   # optional: opens the reference geometry in 3D
        self._rows = []   # [{frame, type_var, atoms_var, value_var}]

        ttk.Label(self, text=(
            "Freeze coordinates and/or run ONE relaxed surface scan for this optimisation. "
            "Atom indices are 0-based (see the list). A blank constraint value freezes the "
            "current value. Needs an OPT recipe (`! Opt`).\n"
            "Values may be a number OR an expression measured from the input geometry: "
            "current (the scanned/constrained coordinate), B(i,j), A(i,j,k), D(i,j,k,l), "
            "plus + - * /.  e.g. scan from 'current' to 'current + 1.5'."),
            justify=tk.LEFT, wraplength=600, foreground="#444").pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))

        self._build_atom_reference()
        self._build_constraints()
        self._build_scan()

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Clear all", command=self._clear_all).pack(side=tk.LEFT)

        self._load(spec)
        self.bind("<Escape>", lambda e: self.destroy())
        fit_to_content(self)
        make_modal(self, parent)

    # ---- atom reference -------------------------------------------------

    def _build_atom_reference(self):
        frame = ttk.LabelFrame(self, text="Atoms (index : element  x  y  z)")
        frame.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        if self._view_xyz is not None:
            bar = ttk.Frame(frame)
            bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 0))
            ttk.Button(bar, text="View geometry (3D)...",
                       command=self._view_xyz).pack(side=tk.LEFT)
            ttk.Label(bar, text="opens the reference molecule so you can read off atom "
                      "indices", foreground="#888").pack(side=tk.LEFT, padx=6)
        cols = ("idx", "el", "x", "y", "z")
        tv = ttk.Treeview(frame, columns=cols, show="headings", height=min(7, max(3, len(self._atoms))))
        for c, w in (("idx", 50), ("el", 50), ("x", 90), ("y", 90), ("z", 90)):
            tv.heading(c, text=c)
            tv.column(c, width=w, anchor=tk.W)
        for i, a in enumerate(self._atoms):
            el, x, y, z = a[0], a[1], a[2], a[3]
            tv.insert("", tk.END, values=(i, el, "%.4f" % x, "%.4f" % y, "%.4f" % z))
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tv.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)

    # ---- constraints ----------------------------------------------------

    def _build_constraints(self):
        outer = ttk.LabelFrame(self, text="Constraints (freeze coordinates)")
        outer.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        hdr = ttk.Frame(outer)
        hdr.pack(side=tk.TOP, fill=tk.X, padx=4)
        ttk.Label(hdr, text="type", width=18).pack(side=tk.LEFT)
        ttk.Label(hdr, text="atoms (e.g. 0 1)", width=18).pack(side=tk.LEFT, padx=4)
        ttk.Label(hdr, text="value (opt.)", width=14).pack(side=tk.LEFT)
        self._cons_frame = ttk.Frame(outer)
        self._cons_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        # Shown only while there are no constraint rows, so an empty spec doesn't
        # look like a stray (and inert) default bond constraint.
        self._cons_empty = ttk.Label(outer, text="No constraints — click + Add constraint "
                                     "to freeze a bond/angle/dihedral/atom.",
                                     foreground="#888")
        self._cons_empty.pack(side=tk.TOP, anchor=tk.W, padx=6, pady=(0, 2))
        ttk.Button(outer, text="+ Add constraint", command=self._add_row).pack(
            side=tk.TOP, anchor=tk.W, padx=4, pady=(2, 4))

    def _add_row(self, ctype="B", atoms="", value=""):
        row = ttk.Frame(self._cons_frame)
        row.pack(side=tk.TOP, fill=tk.X, pady=1)
        type_var = tk.StringVar(value=_choice_for(ctype))
        cb = ttk.Combobox(row, textvariable=type_var, values=_TYPE_CHOICES, state="readonly", width=16)
        cb.pack(side=tk.LEFT)
        atoms_var = tk.StringVar(value=atoms)
        ttk.Entry(row, textvariable=atoms_var, width=18).pack(side=tk.LEFT, padx=4)
        value_var = tk.StringVar(value=("" if value is None else str(value)))
        ttk.Entry(row, textvariable=value_var, width=14).pack(side=tk.LEFT)
        rec = {"frame": row, "type_var": type_var, "atoms_var": atoms_var, "value_var": value_var}
        ttk.Button(row, text="X", width=3, command=lambda r=rec: self._del_row(r)).pack(
            side=tk.LEFT, padx=4)
        self._rows.append(rec)
        self._sync_cons_empty()

    def _del_row(self, rec):
        rec["frame"].destroy()
        self._rows = [r for r in self._rows if r is not rec]
        self._sync_cons_empty()

    def _sync_cons_empty(self):
        try:
            if self._rows:
                self._cons_empty.pack_forget()
            else:
                self._cons_empty.pack(side=tk.TOP, anchor=tk.W, padx=6, pady=(0, 2))
        except (AttributeError, tk.TclError):
            pass

    # ---- scan -----------------------------------------------------------

    def _build_scan(self):
        outer = ttk.LabelFrame(self, text="Relaxed surface scan (optional)")
        outer.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        self._scan_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="Scan one coordinate", variable=self._scan_on,
                        command=self._sync_scan).pack(side=tk.TOP, anchor=tk.W, padx=4, pady=(2, 0))
        row = ttk.Frame(outer)
        row.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        self._scan_type = tk.StringVar(value=_SCAN_CHOICES[0])
        self._scan_type_cb = ttk.Combobox(row, textvariable=self._scan_type, values=_SCAN_CHOICES,
                                          state="readonly", width=12)
        self._scan_type_cb.pack(side=tk.LEFT)
        self._scan_atoms = tk.StringVar()
        ttk.Label(row, text="atoms:").pack(side=tk.LEFT, padx=(8, 2))
        self._scan_atoms_e = ttk.Entry(row, textvariable=self._scan_atoms, width=12)
        self._scan_atoms_e.pack(side=tk.LEFT)
        ttk.Label(row, text="from").pack(side=tk.LEFT, padx=(8, 2))
        self._scan_start = tk.StringVar()
        self._scan_start_e = ttk.Entry(row, textvariable=self._scan_start, width=7)
        self._scan_start_e.pack(side=tk.LEFT)
        ttk.Label(row, text="to").pack(side=tk.LEFT, padx=2)
        self._scan_end = tk.StringVar()
        self._scan_end_e = ttk.Entry(row, textvariable=self._scan_end, width=7)
        self._scan_end_e.pack(side=tk.LEFT)
        ttk.Label(row, text="steps").pack(side=tk.LEFT, padx=(8, 2))
        self._scan_steps = tk.StringVar(value="10")
        self._scan_steps_e = ttk.Entry(row, textvariable=self._scan_steps, width=5)
        self._scan_steps_e.pack(side=tk.LEFT)
        ttk.Label(outer, text="Distances in Å, angles/dihedrals in degrees. from/to accept "
                  "expressions, e.g. from=current, to=current+1.5 (elongate the bond by 1.5 Å).",
                  foreground="#666", wraplength=560, justify=tk.LEFT).pack(
                      side=tk.TOP, anchor=tk.W, padx=4, pady=(0, 4))
        self._scan_widgets = [self._scan_type_cb, self._scan_atoms_e, self._scan_start_e,
                              self._scan_end_e, self._scan_steps_e]

    def _sync_scan(self):
        state = tk.NORMAL if self._scan_on.get() else tk.DISABLED
        for w in self._scan_widgets:
            try:
                w.configure(state="readonly" if (state == tk.NORMAL and w is self._scan_type_cb)
                            else state)
            except tk.TclError:
                pass

    # ---- load / collect / save -----------------------------------------

    def _load(self, spec):
        spec = spec or {}
        for c in (spec.get("constraints") or []):
            self._add_row(c.get("type", "B"),
                          " ".join(str(a) for a in (c.get("atoms") or [])),
                          "" if c.get("value") is None else c.get("value"))
        # No auto-blank row: an empty spec shows the "No constraints" hint instead
        # of an inert default bond constraint.
        self._sync_cons_empty()
        s = spec.get("scan")
        if s:
            self._scan_on.set(True)
            self._scan_type.set(_scan_choice_for(s.get("type", "B")))
            self._scan_atoms.set(" ".join(str(a) for a in (s.get("atoms") or [])))
            self._scan_start.set(str(s.get("start", "")))
            self._scan_end.set(str(s.get("end", "")))
            self._scan_steps.set(str(s.get("steps", 10)))
        self._sync_scan()

    def _clear_all(self):
        for r in list(self._rows):
            self._del_row(r)
        self._scan_on.set(False)
        for v in (self._scan_atoms, self._scan_start, self._scan_end):
            v.set("")
        self._scan_steps.set("10")
        self._sync_scan()

    def _collect(self):
        cons = []
        for r in self._rows:
            atoms = _parse_atoms(r["atoms_var"].get())
            if not atoms:
                continue   # skip blank rows
            ctype = _letter(r["type_var"].get())
            c = {"type": ctype, "atoms": atoms}
            v = r["value_var"].get().strip()
            if ctype != "C" and v != "":
                c["value"] = float(v) if _isnum(v) else v
            cons.append(c)
        scan = None
        if self._scan_on.get():
            scan = {"type": _letter(self._scan_type.get()),
                    "atoms": _parse_atoms(self._scan_atoms.get()),
                    "start": _num(self._scan_start.get()),
                    "end": _num(self._scan_end.get()),
                    "steps": _int(self._scan_steps.get())}
        return {"constraints": cons, "scan": scan}

    def _save(self):
        spec = self._collect()
        errs = G.validate(spec, n_atoms=len(self._atoms), atoms=self._atoms)
        if errs:
            messagebox.showerror("Check the spec", "\n".join(errs), parent=self)
            return
        self._on_save(None if G.is_empty(spec) else spec)
        self.destroy()


def _isnum(v):
    try:
        float(v); return True
    except (TypeError, ValueError):
        return False


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v   # let validate report it


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return v
