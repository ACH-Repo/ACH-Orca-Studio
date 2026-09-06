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
                 title="Geometry constraints / scan", view_xyz=None,
                 define_in_molom=None):
        # type: (tk.Misc, list, dict, callable, str, callable, callable) -> None
        super().__init__(parent)
        self.title(title)
        self._atoms = atoms or []
        self._on_save = on_save
        self._view_xyz = view_xyz   # optional: opens the reference geometry in 3D
        # Optional: `f(on_spec)` launches MoloM with a request and calls back
        # with the spec it sends. Offered only when the configured 3D editor
        # IS MoloM - see core.molom_link.looks_like_molom.
        self._define_in_molom = define_in_molom
        self._rows = []   # [{frame, type_var, atoms_var, value_var}]

        ttk.Label(self, text=(
            "Freeze coordinates and/or run relaxed surface scans for this optimisation. "
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

    def _launch_molom(self):
        """Hand the question to MoloM and load whatever it sends back.

        The reply REPLACES what is in the dialog rather than merging with it:
        MoloM was shown this same spec and the user has been editing it
        there, so its answer is the newer one - and merging two lists of
        constraints on the same coordinates would produce a contradiction
        neither program asked for.
        """
        if self._define_in_molom is None:
            return

        def _loaded(spec):
            if not self.winfo_exists():
                return
            self._load(spec)

        self._define_in_molom(_loaded)

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
        if self._define_in_molom is not None:
            bar2 = ttk.Frame(frame)
            bar2.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 0))
            ttk.Button(bar2, text="Define in MoloM...",
                       command=self._launch_molom).pack(side=tk.LEFT)
            ttk.Label(bar2, text="pick the atoms there and press 'Send to ORCA "
                      "Workbench' - this dialog fills in",
                      foreground="#888").pack(side=tk.LEFT, padx=6)
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

    # ---- scans ----------------------------------------------------------

    def _build_scan(self):
        """A LIST of scans, mirroring the constraints above.

        ORCA runs several: two `Scan` lines were measured as a 3 x 3 grid on
        ORCA 6.0.1, with the FIRST line the outer loop. A single-scan editor
        would have been a GUI more restrictive than the program it drives -
        Christian: "the entire point of it is being a GUI for orca".
        """
        outer = ttk.LabelFrame(self, text="Relaxed surface scans (optional)")
        outer.pack(side=tk.TOP, fill=tk.X, padx=12, pady=4)
        self._scan_frame = ttk.Frame(outer)
        self._scan_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        self._scan_rows = []
        self._scan_empty = ttk.Label(
            outer, text="No scan \u2014 click + Add scan to walk a coordinate "
            "while everything else relaxes.", foreground="#888")
        self._scan_empty.pack(side=tk.TOP, anchor=tk.W, padx=6, pady=(0, 2))
        ttk.Button(outer, text="+ Add scan", command=self._add_scan).pack(
            side=tk.TOP, anchor=tk.W, padx=4, pady=(2, 4))
        self._scan_note = ttk.Label(
            outer, text="", foreground="#666", wraplength=560,
            justify=tk.LEFT)
        self._scan_note.pack(side=tk.TOP, anchor=tk.W, padx=4, pady=(0, 2))
        ttk.Label(outer, text="Distances in \u00c5, angles/dihedrals in degrees. "
                  "'points' is how many GEOMETRIES ORCA runs, not how many "
                  "intervals - `-180, -60, 4` gives -180, -140, -100, -60. "
                  "from/to accept expressions, e.g. from=current, "
                  "to=current+1.5 (elongate the bond by 1.5 \u00c5).",
                  foreground="#666", wraplength=560, justify=tk.LEFT).pack(
                      side=tk.TOP, anchor=tk.W, padx=4, pady=(0, 4))

    def _add_scan(self, ctype="B", atoms="", start="", end="", steps=10):
        row = ttk.Frame(self._scan_frame)
        row.pack(side=tk.TOP, fill=tk.X, pady=1)
        type_var = tk.StringVar(value=_scan_choice_for(ctype))
        cb = ttk.Combobox(row, textvariable=type_var, values=_SCAN_CHOICES,
                          state="readonly", width=12)
        cb.pack(side=tk.LEFT)
        atoms_var = tk.StringVar(value=atoms)
        ttk.Label(row, text="atoms:").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(row, textvariable=atoms_var, width=12).pack(side=tk.LEFT)
        start_var = tk.StringVar(value=str(start))
        ttk.Label(row, text="from").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(row, textvariable=start_var, width=7).pack(side=tk.LEFT)
        end_var = tk.StringVar(value=str(end))
        ttk.Label(row, text="to").pack(side=tk.LEFT, padx=2)
        ttk.Entry(row, textvariable=end_var, width=7).pack(side=tk.LEFT)
        steps_var = tk.StringVar(value=str(steps))
        ttk.Label(row, text="points").pack(side=tk.LEFT, padx=(8, 2))
        e = ttk.Entry(row, textvariable=steps_var, width=5)
        e.pack(side=tk.LEFT)
        steps_var.trace_add("write", lambda *_a: self._sync_scan_note())
        rec = {"frame": row, "type_var": type_var, "atoms_var": atoms_var,
               "start_var": start_var, "end_var": end_var,
               "steps_var": steps_var}
        ttk.Button(row, text="X", width=3,
                   command=lambda r=rec: self._del_scan(r)).pack(side=tk.LEFT,
                                                                 padx=4)
        self._scan_rows.append(rec)
        self._sync_scan()

    def _del_scan(self, rec):
        rec["frame"].destroy()
        self._scan_rows = [r for r in self._scan_rows if r is not rec]
        self._sync_scan()

    def _sync_scan(self):
        try:
            if self._scan_rows:
                self._scan_empty.pack_forget()
            else:
                self._scan_empty.pack(side=tk.TOP, anchor=tk.W, padx=6,
                                      pady=(0, 2))
        except (AttributeError, tk.TclError):
            pass
        self._sync_scan_note()

    def _sync_scan_note(self):
        """Say what the GRID costs, because it multiplies.

        ORCA runs every combination, so two 10-point scans is a hundred
        optimisations and not twenty - which is the one thing about a
        multi-dimensional scan that surprises people.
        """
        if len(self._scan_rows) < 2:
            try:
                self._scan_note.configure(text="")
            except (AttributeError, tk.TclError):
                pass
            return
        total = 1
        for r in self._scan_rows:
            total *= max(1, _int(r["steps_var"].get()) or 1)
        try:
            self._scan_note.configure(
                text="ORCA runs the full grid: {} optimisations. The FIRST "
                     "scan is the outer loop.".format(total))
        except (AttributeError, tk.TclError):
            pass

    # ---- load / collect / save -----------------------------------------

    def _load(self, spec):
        # CLEARED FIRST. `_load` is not only the constructor's - MoloM's
        # reply comes back through it - and appending to what is on screen
        # would double every row the second time.
        self._clear_all()
        spec = spec or {}
        for c in (spec.get("constraints") or []):
            self._add_row(c.get("type", "B"),
                          " ".join(str(a) for a in (c.get("atoms") or [])),
                          "" if c.get("value") is None else c.get("value"))
        # No auto-blank row: an empty spec shows the "No constraints" hint instead
        # of an inert default bond constraint.
        self._sync_cons_empty()
        for s in G.scans_of(spec):
            self._add_scan(s.get("type", "B"),
                           " ".join(str(a) for a in (s.get("atoms") or [])),
                           s.get("start", ""), s.get("end", ""),
                           s.get("steps", 10))
        self._sync_scan()

    def _clear_all(self):
        for r in list(self._rows):
            self._del_row(r)
        for r in list(self._scan_rows):
            self._del_scan(r)
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
        scans = []
        for r in self._scan_rows:
            atoms = _parse_atoms(r["atoms_var"].get())
            if not atoms:
                continue                      # skip blank rows, as above
            scans.append({"type": _letter(r["type_var"].get()),
                          "atoms": atoms,
                          "start": _num(r["start_var"].get()),
                          "end": _num(r["end_var"].get()),
                          "steps": _int(r["steps_var"].get())})
        return {"constraints": cons, "scans": scans}

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
