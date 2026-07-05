"""Reusable CSV-column editor dialog.

Pick which report columns to write, rename their headers, and order them
left-to-right. Shared by the Workflow Report NODE (per-node config) and the
Report TAB (whole-report output), so both offer identical CSV customisation.

Rows in the resulting CSV are always calculations; the columns are what this
dialog chooses.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from orca_workbench.core import reporting
from orca_workbench.ui.modal import fit_to_content, make_modal


def edit_csv_columns_dialog(parent, current, title, on_save):
    """Open a modal dialog to pick/rename/order CSV columns.

    `current` is None (= all default columns) or an ordered list of
    {"key","header"}. On Save, calls `on_save(columns)` where columns is None
    when the choice is exactly the default set (so future columns keep flowing)
    or the ordered [{"key","header"}, ...] otherwise.
    """
    catalogue = reporting.available_csv_columns()      # [{key,label}]
    cat_by_key = {c["key"]: c for c in catalogue}
    base = current or reporting.default_csv_columns()
    # working list: chosen columns (in order) as dicts {key, header}
    chosen = [{"key": c["key"], "header": c.get("header")
               or cat_by_key.get(c["key"], {}).get("label", c["key"])}
              for c in base if c["key"] in cat_by_key]

    top = tk.Toplevel(parent)
    top.title(title)
    top.minsize(560, 480)
    ttk.Label(top, text="Rows are calculations. Pick columns, rename headers, and order "
              "them top-to-bottom = left-to-right in the CSV.", wraplength=520,
              justify=tk.LEFT, foreground="#555").pack(anchor=tk.W, padx=10, pady=(10, 4))

    body = ttk.Frame(top)
    body.pack(fill=tk.BOTH, expand=True, padx=10)
    left = ttk.Frame(body); left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ttk.Label(left, text="Available").pack(anchor=tk.W)
    avail = tk.Listbox(left, selectmode=tk.EXTENDED, exportselection=False)
    avail.pack(fill=tk.BOTH, expand=True)
    mid = ttk.Frame(body); mid.pack(side=tk.LEFT, fill=tk.Y, padx=6)
    right = ttk.Frame(body); right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ttk.Label(right, text="In the CSV (top = leftmost)").pack(anchor=tk.W)
    chosenlb = tk.Listbox(right, selectmode=tk.EXTENDED, exportselection=False)
    chosenlb.pack(fill=tk.BOTH, expand=True)

    state = {"avail_keys": []}

    def refresh_lists(sel=None):
        chosen_keys = {c["key"] for c in chosen}
        avail.delete(0, tk.END)
        state["avail_keys"] = [c["key"] for c in catalogue if c["key"] not in chosen_keys]
        for k in state["avail_keys"]:
            avail.insert(tk.END, cat_by_key[k]["label"])
        chosenlb.delete(0, tk.END)
        for c in chosen:
            chosenlb.insert(tk.END, "{}   [{}]".format(c["header"], c["key"]))
        if sel is not None and 0 <= sel < len(chosen):
            chosenlb.selection_set(sel); chosenlb.activate(sel)

    def add_sel():
        for i in avail.curselection():
            k = state["avail_keys"][i]
            chosen.append({"key": k, "header": cat_by_key[k]["label"]})
        refresh_lists()

    def add_all():
        have = {c["key"] for c in chosen}
        for c in catalogue:
            if c["key"] not in have:
                chosen.append({"key": c["key"], "header": c["label"]})
        refresh_lists()

    def remove_sel():
        for i in sorted(chosenlb.curselection(), reverse=True):
            del chosen[i]
        refresh_lists()

    def remove_all():
        del chosen[:]
        refresh_lists()

    def move(delta):
        idx = list(chosenlb.curselection())
        if len(idx) != 1:
            return
        i = idx[0]; j = i + delta
        if 0 <= j < len(chosen):
            chosen[i], chosen[j] = chosen[j], chosen[i]
            refresh_lists(j)

    def rename():
        idx = list(chosenlb.curselection())
        if len(idx) != 1:
            return
        i = idx[0]
        new = simpledialog.askstring("Header", "Column header:",
                                     initialvalue=chosen[i]["header"], parent=top)
        if new is not None:
            chosen[i]["header"] = new.strip() or chosen[i]["key"]
            refresh_lists(i)

    ttk.Button(mid, text=">> add", command=add_sel).pack(pady=(0, 2), fill=tk.X)
    ttk.Button(mid, text="<< remove", command=remove_sel).pack(pady=2, fill=tk.X)
    ttk.Button(mid, text=">> add all", command=add_all).pack(pady=(8, 2), fill=tk.X)
    ttk.Button(mid, text="<< remove all", command=remove_all).pack(pady=2, fill=tk.X)
    ttk.Button(mid, text="Up", command=lambda: move(-1)).pack(pady=(12, 2), fill=tk.X)
    ttk.Button(mid, text="Down", command=lambda: move(1)).pack(pady=2, fill=tk.X)
    ttk.Button(mid, text="Rename", command=rename).pack(pady=(12, 2), fill=tk.X)
    chosenlb.bind("<Double-1>", lambda e: rename())

    bar = ttk.Frame(top)
    bar.pack(fill=tk.X, padx=10, pady=8)

    def save():
        if not chosen:
            messagebox.showinfo("CSV columns", "Pick at least one column.", parent=top)
            return
        default = reporting.default_csv_columns()
        same = (len(chosen) == len(default)
                and all(chosen[i]["key"] == default[i]["key"]
                        and chosen[i]["header"] == default[i]["header"]
                        for i in range(len(default))))
        # store None when it's exactly the default set (keeps future columns)
        on_save(None if same else [dict(c) for c in chosen])
        top.destroy()

    ttk.Button(bar, text="Cancel", command=top.destroy).pack(side=tk.RIGHT, padx=4)
    ttk.Button(bar, text="Save", command=save).pack(side=tk.RIGHT, padx=4)
    refresh_lists()
    fit_to_content(top, min_w=560, min_h=480)   # size to content, not a fixed px box
    make_modal(top, parent)
    return top
