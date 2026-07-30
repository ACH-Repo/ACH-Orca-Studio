"""Editor dialog for ONE Combine-node placement (see core/transform placements).

Where a Transform op reshapes a single molecule, a *placement* positions whole
fragments against each other — the inter-molecular half of building a complex.
The one operation so far is **snap**: put atom `j` of the mobile fragment a
chosen distance from atom `i` of the fixed fragment, along a chosen direction.

That is the H-bond recipe: orient each monomer with its own Transform node
(donor X-H pointing at the partner, acceptor leading with its lone pair), then
snap the donor H onto the acceptor at ~1.9 A. The fragments keep the
orientation their Transform gave them — a placement only translates.

`PlacementDialog(parent, placement=None, fragments=None)` — `fragments` is
[(label, symbols, coords), ...], one per wire into the Combine node (in
connection order), used for the fragment pickers and the atom reference lists.
After the dialog closes, `.result` holds the placement dict, or None on cancel.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from orca_workbench.ui.modal import make_modal

_DIRECTIONS = [
    ("auto", "auto - keep the current direction, just fix the distance"),
    ("x", "+x"), ("-x", "-x"), ("y", "+y"), ("-y", "-y"), ("z", "+z"), ("-z", "-z"),
]
_DIR_LABELS = [label for _k, label in _DIRECTIONS]
_LABEL_TO_DIR = {label: key for key, label in _DIRECTIONS}
_DIR_TO_LABEL = {key: label for key, label in _DIRECTIONS}


class PlacementDialog(tk.Toplevel):
    def __init__(self, parent, placement=None, fragments=None):
        super().__init__(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel")
                         else parent)
        self.result = None
        self._p = dict(placement or {})
        self._frags = list(fragments or [])
        self.title("Combine placement (snap fragments together)")

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=8)
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(left, text="Snap one fragment onto another: the mobile fragment is "
                  "translated (never rotated) so its anchor atom sits at the chosen "
                  "distance from the fixed fragment's anchor atom.",
                  wraplength=330, justify=tk.LEFT, foreground="#555").pack(
                      anchor=tk.W, pady=(0, 6))

        names = [self._frag_label(k) for k in range(max(2, len(self._frags)))]
        self._fixed = self._frag_combo(left, "Fixed fragment (stays put):", names,
                                       int(self._p.get("fixed", 0) or 0))
        self._i = self._int_field(left, "... its anchor atom index (0-based):", "i", 0)
        self._mobile = self._frag_combo(left, "Mobile fragment (gets moved):", names,
                                        int(self._p.get("mobile", 1) or 1))
        self._j = self._int_field(left, "... its anchor atom index (0-based):", "j", 0)

        ttk.Label(left, text="Distance between the two anchor atoms (Angstrom):").pack(
            anchor=tk.W, pady=(6, 0))
        self._dist = tk.StringVar(value=str(self._p.get("distance", 1.9)))
        ttk.Entry(left, textvariable=self._dist, width=10).pack(anchor=tk.W)
        ttk.Label(left, text="Typical hydrogen bond: 1.6-2.2 A (H...acceptor). "
                  "A pi-stack sits near 3.4 A.", foreground="#777", wraplength=330,
                  justify=tk.LEFT).pack(anchor=tk.W)

        ttk.Label(left, text="Direction (from the fixed atom towards the mobile one):").pack(
            anchor=tk.W, pady=(6, 0))
        cur_dir = self._p.get("direction")
        if cur_dir is None:
            cur_dir = "auto"
        self._dir = tk.StringVar(value=_DIR_TO_LABEL.get(
            cur_dir if isinstance(cur_dir, str) else "auto", _DIR_LABELS[0]))
        ttk.Combobox(left, textvariable=self._dir, state="readonly",
                     values=_DIR_LABELS, width=44).pack(anchor=tk.W)
        ttk.Label(left, text="Align each fragment's key bond to an axis with its own "
                  "Transform node first; then a signed axis here puts them face to face. "
                  "'auto' only corrects the distance along the direction they already "
                  "have.", foreground="#777", wraplength=330, justify=tk.LEFT).pack(
                      anchor=tk.W, pady=(2, 0))

        # Atom reference lists — one per fragment, so both anchor indices can be
        # read off without leaving the dialog.
        if self._frags:
            right = ttk.Frame(body)
            right.pack(side=tk.LEFT, fill=tk.BOTH, padx=(12, 0))
            nb = ttk.Notebook(right)
            nb.pack(fill=tk.BOTH, expand=True)
            for k, (label, symbols, coords) in enumerate(self._frags):
                page = ttk.Frame(nb)
                nb.add(page, text="{}: {}".format(k, label[:14]))
                sb = ttk.Scrollbar(page, orient=tk.VERTICAL)
                lst = tk.Listbox(page, height=13, width=28, exportselection=False,
                                 yscrollcommand=sb.set, font=("Courier", 9))
                sb.configure(command=lst.yview)
                for i, s in enumerate(symbols):
                    lst.insert(tk.END, "{:>3}  {:<2} {:8.3f} {:8.3f} {:8.3f}".format(
                        i, s, coords[i][0], coords[i][1], coords[i][2]))
                lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                sb.pack(side=tk.LEFT, fill=tk.Y)

        bar = ttk.Frame(self)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 8))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=3)
        ttk.Button(bar, text="OK", command=self._ok).pack(side=tk.RIGHT, padx=3)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        make_modal(self, parent)
        self.wait_window()

    # ------------------------------------------------------------------ widgets
    def _frag_label(self, k):
        if k < len(self._frags):
            return "{}: {}".format(k, self._frags[k][0])
        return "{}: (wire {})".format(k, k + 1)

    def _frag_combo(self, parent, label, names, current):
        ttk.Label(parent, text=label).pack(anchor=tk.W, pady=(6, 0))
        var = tk.StringVar(value=names[current] if 0 <= current < len(names) else names[0])
        ttk.Combobox(parent, textvariable=var, state="readonly", values=names,
                     width=34).pack(anchor=tk.W)
        return var

    def _int_field(self, parent, label, key, default):
        ttk.Label(parent, text=label).pack(anchor=tk.W, pady=(2, 0))
        var = tk.StringVar(value=str(self._p.get(key, default)))
        ttk.Entry(parent, textvariable=var, width=10).pack(anchor=tk.W)
        return var

    @staticmethod
    def _frag_index(text):
        try:
            return int(str(text).split(":", 1)[0])
        except (TypeError, ValueError):
            return 0

    def _ok(self):
        from orca_workbench.core import transform as transform_mod
        try:
            p = {"op": "snap",
                 "fixed": self._frag_index(self._fixed.get()),
                 "mobile": self._frag_index(self._mobile.get()),
                 "i": int(self._i.get().strip()),
                 "j": int(self._j.get().strip()),
                 "distance": float(self._dist.get().strip())}
        except (TypeError, ValueError):
            messagebox.showwarning("Placement", "Atom indices must be whole numbers and "
                                                "the distance a number.", parent=self)
            return
        direction = _LABEL_TO_DIR.get(self._dir.get(), "auto")
        if direction != "auto":
            p["direction"] = direction
        if not transform_mod.op_enabled(self._p):
            p["enabled"] = False        # keep an existing on/off state through an edit
        sizes = [len(f[1]) for f in self._frags] or None
        issues = transform_mod.validate_placements(
            [p], n_fragments=len(self._frags) or None, frag_sizes=sizes)
        if issues:
            messagebox.showwarning("Placement", "\n".join(issues), parent=self)
            return
        self.result = p
        self.destroy()
