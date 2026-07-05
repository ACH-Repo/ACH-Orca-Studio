"""Editor dialog for ONE Transform-node operation (see core/transform).

Opened from the Transform node's config panel (Add... / Edit... / double-click).
Shows a type picker whose fields swap per op type, plus the incoming molecule's
atom list for index reference (indices are 0-based, matching core/geomspec).

`TransformOpDialog(parent, op=None, ref_geom=None)` — `op` is an existing op
dict to edit (None = new), `ref_geom` an optional (name, symbols, coords) of
the first molecule arriving at the node. After the dialog closes, `.result`
holds the new op dict, or None on cancel.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from orca_workbench.core import transform as transform_mod
from orca_workbench.ui.modal import make_modal

# (op key, label shown in the picker)
_TYPES = [
    ("translate", "Translate (shift by a vector)"),
    ("rotate", "Rotate (angle about an axis)"),
    ("center", "Center at the origin"),
    ("mirror", "Mirror across a plane"),
    ("align_axis", "Align 2-atom axis to x/y/z"),
    ("align_plane", "Align 3-atom plane (face) to x/y/z"),
    ("align_principal", "Align principal axes"),
    ("set_dihedral", "Set a dihedral (conformation edit)"),
]
_LABEL_TO_KEY = {label: key for key, label in _TYPES}
_KEY_TO_LABEL = {key: label for key, label in _TYPES}


def _parse_floats(text, n, what):
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) != n:
        raise ValueError("{}: need {} numbers".format(what, n))
    return [float(p) for p in parts]


def _parse_axis(text):
    """x/y/z, a 3-vector 'ux uy uz', or an ATOM PAIR 'i j' (axis through those
    two atoms — rides with the molecule, so it's position-invariant).
    Returns ('fixed', axis) or ('atoms', [i, j])."""
    t = (text or "").strip().lower()
    if t in ("x", "y", "z"):
        return ("fixed", t)
    parts = [p for p in t.replace(",", " ").split() if p]
    if len(parts) == 2:
        return ("atoms", [int(parts[0]), int(parts[1])])
    return ("fixed", _parse_floats(t, 3, "axis"))


def _parse_center(text):
    t = (text or "").strip().lower()
    if t in ("", "centroid"):
        return "centroid"
    if t == "com":
        return "com"
    parts = t.replace(",", " ").split()
    if len(parts) == 1:
        return int(parts[0])          # an atom index
    return _parse_floats(t, 3, "center")


class TransformOpDialog(tk.Toplevel):
    def __init__(self, parent, op=None, ref_geom=None):
        super().__init__(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel")
                         else parent)
        self.result = None
        self._op = dict(op or {})
        self.title("Transform operation")

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=8)

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(left, text="Operation:").pack(anchor=tk.W)
        cur_key = self._op.get("op", "translate")
        self._type_var = tk.StringVar(value=_KEY_TO_LABEL.get(cur_key, _TYPES[0][1]))
        cb = ttk.Combobox(left, textvariable=self._type_var, state="readonly",
                          values=[label for _k, label in _TYPES], width=34)
        cb.pack(anchor=tk.W, pady=(0, 6))
        cb.bind("<<ComboboxSelected>>", lambda e: self._render_fields())

        self._fields_frame = ttk.Frame(left)
        self._fields_frame.pack(fill=tk.BOTH, expand=True)
        self._vars = {}

        # Atom reference list (right side) — indices for the i/j/k/atoms fields.
        if ref_geom is not None:
            name, symbols, coords = ref_geom
            right = ttk.Frame(body)
            right.pack(side=tk.LEFT, fill=tk.Y, padx=(12, 0))
            ttk.Label(right, text="Atoms of '{}'\n(0-based index):".format(name),
                      justify=tk.LEFT).pack(anchor=tk.W)
            lb_frame = ttk.Frame(right)
            lb_frame.pack(fill=tk.Y, expand=True)
            sb = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL)
            lst = tk.Listbox(lb_frame, height=12, width=26, exportselection=False,
                             yscrollcommand=sb.set, font=("Courier", 9))
            sb.configure(command=lst.yview)
            for i, s in enumerate(symbols):
                lst.insert(tk.END, "{:>3}  {:<2} {:8.3f} {:8.3f} {:8.3f}".format(
                    i, s, coords[i][0], coords[i][1], coords[i][2]))
            lst.pack(side=tk.LEFT, fill=tk.Y, expand=True)
            sb.pack(side=tk.LEFT, fill=tk.Y)

        bar = ttk.Frame(self)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 8))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=3)
        ttk.Button(bar, text="OK", command=self._ok).pack(side=tk.RIGHT, padx=3)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self._render_fields()
        make_modal(self, parent)
        self.wait_window()

    # ------------------------------------------------------------------ fields
    def _field(self, label, key, default="", width=22, hint=None):
        f = self._fields_frame
        ttk.Label(f, text=label).pack(anchor=tk.W, pady=(4, 0))
        var = tk.StringVar(value=str(self._op.get(key, default)))
        ttk.Entry(f, textvariable=var, width=width).pack(anchor=tk.W)
        if hint:
            ttk.Label(f, text=hint, foreground="#777", wraplength=250,
                      justify=tk.LEFT).pack(anchor=tk.W)
        self._vars[key] = var

    def _target_combo(self, default="x"):
        f = self._fields_frame
        ttk.Label(f, text="Target lab axis:").pack(anchor=tk.W, pady=(4, 0))
        var = tk.StringVar(value=str(self._op.get("target", default)))
        ttk.Combobox(f, textvariable=var, state="readonly", values=["x", "y", "z"],
                     width=5).pack(anchor=tk.W)
        self._vars["target"] = var

    def _render_fields(self):
        for w in self._fields_frame.winfo_children():
            w.destroy()
        self._vars = {}
        kind = _LABEL_TO_KEY[self._type_var.get()]

        if kind == "translate":
            vec = self._op.get("vec", [0.0, 0.0, 0.0])
            self._vars["vec"] = tk.StringVar(value="{:g} {:g} {:g}".format(*vec)
                                             if isinstance(vec, (list, tuple)) else str(vec))
            ttk.Label(self._fields_frame, text="Shift vector (x y z, Angstrom):").pack(
                anchor=tk.W, pady=(4, 0))
            ttk.Entry(self._fields_frame, textvariable=self._vars["vec"],
                      width=22).pack(anchor=tk.W)
        elif kind == "rotate":
            axa = self._op.get("axis_atoms")
            ax = self._op.get("axis", "z")
            self._vars["axis"] = tk.StringVar(
                value=("{} {}".format(axa[0], axa[1]) if axa
                       else (ax if isinstance(ax, str) else "{:g} {:g} {:g}".format(*ax))))
            ttk.Label(self._fields_frame, text="Axis: x / y / z, a vector 'ux uy uz', "
                      "or TWO atom indices 'i j'\n(axis through those atoms — moves "
                      "with the molecule):", justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))
            ttk.Entry(self._fields_frame, textvariable=self._vars["axis"],
                      width=22).pack(anchor=tk.W)
            self._field("Angle (degrees):", "angle", default="90")
            ctr = self._op.get("center", "centroid")
            self._vars["center"] = tk.StringVar(
                value=ctr if isinstance(ctr, str)
                else (str(ctr) if isinstance(ctr, int) else "{:g} {:g} {:g}".format(*ctr)))
            ttk.Label(self._fields_frame, text="Center (ignored for an atom-pair axis): "
                      "'centroid', 'com',\nan atom index, or 'x y z':",
                      justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))
            ttk.Entry(self._fields_frame, textvariable=self._vars["center"],
                      width=22).pack(anchor=tk.W)
        elif kind == "center":
            ttk.Label(self._fields_frame, text="Move the molecule so this point sits at "
                      "the origin:").pack(anchor=tk.W, pady=(4, 0))
            self._vars["mode"] = tk.StringVar(value=self._op.get("mode", "com"))
            for val, txt in (("com", "Centre of mass"), ("centroid", "Centroid")):
                ttk.Radiobutton(self._fields_frame, text=txt, value=val,
                                variable=self._vars["mode"]).pack(anchor=tk.W, padx=8)
            atoms = self._op.get("atoms") or []
            self._vars["anchor"] = tk.StringVar(
                value=" ".join(str(a) for a in atoms))
            ttk.Label(self._fields_frame, text="...or anchor on atoms (overrides the "
                      "above):\none index 'i', or two 'i j' = their midpoint (e.g. the "
                      "middle\nof a bond):", justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))
            ttk.Entry(self._fields_frame, textvariable=self._vars["anchor"],
                      width=12).pack(anchor=tk.W)
        elif kind == "mirror":
            ttk.Label(self._fields_frame, text="Reflect across plane:").pack(
                anchor=tk.W, pady=(4, 0))
            self._vars["plane"] = tk.StringVar(value=self._op.get("plane", "xy"))
            ttk.Combobox(self._fields_frame, textvariable=self._vars["plane"],
                         state="readonly", values=["xy", "yz", "xz"],
                         width=5).pack(anchor=tk.W)
            ctr = self._op.get("center")
            self._vars["center"] = tk.StringVar(
                value="" if ctr is None
                else (ctr if isinstance(ctr, str)
                      else (str(ctr) if isinstance(ctr, int)
                            else "{:g} {:g} {:g}".format(*ctr))))
            ttk.Label(self._fields_frame, text="Through (blank = the origin; or "
                      "'centroid', 'com',\nan atom index, 'x y z'):",
                      justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))
            ttk.Entry(self._fields_frame, textvariable=self._vars["center"],
                      width=22).pack(anchor=tk.W)
            ttk.Label(self._fields_frame, text="Note: mirroring makes the enantiomer "
                      "of a chiral molecule.", foreground="#777", wraplength=250,
                      justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))
        elif kind == "align_axis":
            self._field("Atom i (stays fixed):", "i", default="0", width=8)
            self._field("Atom j:", "j", default="1", width=8)
            self._target_combo("x")
        elif kind == "align_plane":
            self._field("Atom i:", "i", default="0", width=8)
            self._field("Atom j:", "j", default="1", width=8)
            self._field("Atom k:", "k", default="2", width=8)
            self._target_combo("z")
            ttk.Label(self._fields_frame, text="The plane through the three atoms is "
                      "rotated so its NORMAL points along the target axis.",
                      foreground="#777", wraplength=250, justify=tk.LEFT).pack(
                          anchor=tk.W, pady=(2, 0))
        elif kind == "align_principal":
            self._field("Axis order (permutation of xyz):", "order", default="xyz", width=8,
                        hint="The LONG axis (smallest moment of inertia) goes to the "
                             "first letter, the middle to the second, the short to the "
                             "third. Rotation about the centre of mass.")
        elif kind == "set_dihedral":
            atoms = self._op.get("atoms", [0, 1, 2, 3])
            self._vars["atoms"] = tk.StringVar(
                value=" ".join(str(a) for a in atoms))
            ttk.Label(self._fields_frame, text="Four atoms a b c d (0-based):").pack(
                anchor=tk.W, pady=(4, 0))
            ttk.Entry(self._fields_frame, textvariable=self._vars["atoms"],
                      width=18).pack(anchor=tk.W)
            self._field("Dihedral D(a,b,c,d) target (degrees):", "angle", default="0")
            ttk.Label(self._fields_frame, text="NOT rigid: the atoms on the d-side of "
                      "the b-c bond rotate (fails on a ring bond).",
                      foreground="#777", wraplength=250, justify=tk.LEFT).pack(
                          anchor=tk.W, pady=(2, 0))

    # ---------------------------------------------------------------- OK/parse
    def _ok(self):
        kind = _LABEL_TO_KEY[self._type_var.get()]
        op = {"op": kind}
        try:
            if kind == "translate":
                op["vec"] = _parse_floats(self._vars["vec"].get(), 3, "shift vector")
            elif kind == "rotate":
                ax_kind, ax_val = _parse_axis(self._vars["axis"].get())
                op["angle"] = float(self._vars["angle"].get())
                if ax_kind == "atoms":
                    op["axis_atoms"] = ax_val
                else:
                    op["axis"] = ax_val
                    op["center"] = _parse_center(self._vars["center"].get())
            elif kind == "center":
                op["mode"] = self._vars["mode"].get()
                anchor = self._vars["anchor"].get().replace(",", " ").split()
                if anchor:
                    op["atoms"] = [int(a) for a in anchor]
            elif kind == "mirror":
                op["plane"] = self._vars["plane"].get()
                ctr = self._vars["center"].get().strip()
                if ctr:
                    op["center"] = _parse_center(ctr)
            elif kind == "align_axis":
                op["i"] = int(self._vars["i"].get())
                op["j"] = int(self._vars["j"].get())
                op["target"] = self._vars["target"].get()
            elif kind == "align_plane":
                for key in ("i", "j", "k"):
                    op[key] = int(self._vars[key].get())
                op["target"] = self._vars["target"].get()
            elif kind == "align_principal":
                op["order"] = self._vars["order"].get().strip().lower()
            elif kind == "set_dihedral":
                op["atoms"] = [int(t) for t in
                               self._vars["atoms"].get().replace(",", " ").split()]
                op["angle"] = float(self._vars["angle"].get())
        except (ValueError, KeyError) as e:
            messagebox.showerror("Transform operation", "Bad value: {}".format(e),
                                 parent=self)
            return
        issues = transform_mod.validate_ops([op])
        if issues:
            messagebox.showerror("Transform operation", "\n".join(issues), parent=self)
            return
        self.result = op
        self.destroy()
