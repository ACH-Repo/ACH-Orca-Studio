"""Benchmark tab — bulk-generate many calculations across theory levels.

Two modes:

  • Independent matrix:  for every selected molecule × every selected recipe,
    create one root calculation from the molecule's initial geometry. The
    classic "run this set of molecules at all these theory levels" sweep.

  • Optimize → property:  for every selected molecule, create one OPT with a
    chosen geometry recipe, then a derived calculation for each selected
    property recipe (FREQ/NMR/SP/...) that inherits the optimised geometry via
    the normal parent link. The "optimise once, then probe at many levels" study.

Generated calculations are ordinary PlannedCalcs dropped into a benchmark
category (default "bench"), so they flow through the same Calculations tab
(build / submit / monitor) and Report tab as everything else. This tab is just
a fast, matrix-aware way to populate them.
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import List

from orca_workbench.core.project import PlannedCalc, new_calc_id
from orca_workbench.ui.tooltip import tip


class BenchmarkTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        # Coloured header banner (scheme blue) marking this as a special,
        # set-apart bulk tool — and flagging that it may change substantially.
        banner = tk.Frame(self, background="#dce9f7")
        banner.pack(side=tk.TOP, fill=tk.X)
        tk.Label(banner, background="#dce9f7", justify=tk.LEFT, anchor=tk.W,
                 text="Benchmark — bulk job generator",
                 font=("TkDefaultFont", 11, "bold")).pack(side=tk.TOP, anchor=tk.W, padx=10, pady=(6, 0))
        tk.Label(banner, background="#dce9f7", justify=tk.LEFT, anchor=tk.W, foreground="#445",
                 text="A special tool outside the normal workflow. Experimental — its design "
                      "may change substantially in future versions.",
                 wraplength=900).pack(side=tk.TOP, anchor=tk.W, padx=10, pady=(0, 6))

        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 2))
        ttk.Label(top, text="Schedule many calculations at once across theory levels.",
                  font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT)

        # Mode selector
        mode_frame = ttk.LabelFrame(self, text="Mode")
        mode_frame.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)
        self.mode_var = tk.StringVar(value="matrix")
        rb1 = ttk.Radiobutton(mode_frame, text="Independent matrix  (each molecule × each recipe)",
                              variable=self.mode_var, value="matrix", command=self._on_mode_change)
        rb2 = ttk.Radiobutton(mode_frame, text="Optimize → property  (one OPT per molecule, then derived calcs)",
                              variable=self.mode_var, value="chain", command=self._on_mode_change)
        rb1.pack(anchor=tk.W, padx=8, pady=1)
        rb2.pack(anchor=tk.W, padx=8, pady=1)
        tip(rb1, "Full matrix: every selected molecule is run with every selected recipe, each "
                 "as an independent calculation from the molecule's initial geometry. Best for "
                 "comparing methods/basis sets on a fixed set of structures.")
        tip(rb2, "For each molecule: one geometry optimisation with the chosen OPT recipe, then "
                 "one derived calculation per selected property recipe (FREQ / NMR / SP / ...) "
                 "that reuses the optimised geometry. Best for 'optimise once, probe at many "
                 "levels' studies. Derived calcs build only after their OPT finishes.")

        # Selectors: molecules | recipes
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=4)

        mol_frame = ttk.LabelFrame(paned, text="Molecules")
        paned.add(mol_frame, weight=2)
        self.mol_tree = self._make_list(mol_frame, "Molecule")
        ttk.Button(mol_frame, text="Select all",
                   command=lambda: self._select_all(self.mol_tree)).pack(side=tk.BOTTOM, anchor=tk.W, padx=4, pady=2)

        rec_frame = ttk.LabelFrame(paned, text="Recipes  (theory levels)")
        paned.add(rec_frame, weight=3)
        self.rec_tree = self._make_list(rec_frame, "Recipe", columns=("name", "type", "method"))
        ttk.Button(rec_frame, text="Select all",
                   command=lambda: self._select_all(self.rec_tree)).pack(side=tk.BOTTOM, anchor=tk.W, padx=4, pady=2)

        # Options + actions
        opt = ttk.Frame(self)
        opt.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)

        # OPT-recipe combobox (only relevant in chain mode)
        self.optrow = ttk.Frame(opt)
        self.optrow.pack(side=tk.TOP, fill=tk.X, pady=2)
        ttk.Label(self.optrow, text="Geometry (OPT) recipe:").pack(side=tk.LEFT)
        self.opt_recipe_var = tk.StringVar()
        self.opt_combo = ttk.Combobox(self.optrow, textvariable=self.opt_recipe_var,
                                      state="readonly", width=42)
        self.opt_combo.pack(side=tk.LEFT, padx=6)
        self.opt_combo.bind("<<ComboboxSelected>>", lambda e: self._update_count())
        tip(self.opt_combo, "In 'Optimize → property' mode, each molecule is first optimised "
                            "with this recipe; the recipes selected on the right become the "
                            "derived property calculations.")

        row = ttk.Frame(opt)
        row.pack(side=tk.TOP, fill=tk.X, pady=2)
        self._catrow = row
        ttk.Label(row, text="Benchmark category folder:").pack(side=tk.LEFT)
        self.category_var = tk.StringVar(value="bench")
        cat = ttk.Entry(row, textvariable=self.category_var, width=16)
        cat.pack(side=tk.LEFT, padx=6)
        self.category_var.trace_add("write", lambda *_: self._update_count())
        tip(cat, "The <category> directory level: calcs/<mol>/<category>/<calctype>/<method>/. "
                 "Defaults to 'bench' so benchmark jobs sit apart from your manual 'gen' work.")

        self.count_var = tk.StringVar(value="")
        self.count_lbl = ttk.Label(opt, textvariable=self.count_var, foreground="#444")
        self.count_lbl.pack(side=tk.TOP, anchor=tk.W, pady=(2, 0))

        b_gen = tk.Button(opt, text="Generate calculations", command=self.on_generate,
                          font=("TkDefaultFont", 10, "bold"), bg="#cfe0f5", activebackground="#bcd6f0")
        b_gen.pack(side=tk.TOP, anchor=tk.W, pady=4)
        tip(b_gen, "Create all the planned calculations and jump to the Calculations tab to "
                   "build & submit them. Nothing is run yet — this only populates the plan.")

        # Preview
        prev = ttk.LabelFrame(self, text="Preview (what will be created)")
        prev.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=4)
        cols = ("molecule", "recipe", "type", "role", "target")
        self.preview = ttk.Treeview(prev, columns=cols, show="headings", height=6)
        for c, label, w in [("molecule", "Molecule", 90), ("recipe", "Recipe", 180),
                            ("type", "Type", 55), ("role", "Role", 90), ("target", "Target dir", 300)]:
            self.preview.heading(c, text=label)
            self.preview.column(c, width=w, anchor=tk.W)
        self.preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        psb = ttk.Scrollbar(prev, orient=tk.VERTICAL, command=self.preview.yview)
        self.preview.configure(yscrollcommand=psb.set)
        psb.pack(side=tk.RIGHT, fill=tk.Y)

        self._on_mode_change()

    def _make_list(self, parent, heading, columns=("name",)):
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended", height=8)
        if columns == ("name",):
            tree.heading("name", text=heading)
            tree.column("name", width=160, anchor=tk.W)
        else:
            for c, label, w in [("name", "Name", 200), ("type", "Type", 55), ("method", "Method", 150)]:
                tree.heading(c, text=label)
                tree.column(c, width=w, anchor=tk.W)
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        tree.bind("<<TreeviewSelect>>", lambda e: self._update_count())
        tree.bind("<Control-a>", lambda e: self._select_all(tree))
        tree.bind("<Control-A>", lambda e: self._select_all(tree))
        tree.bind("<Enter>", lambda e: tree.focus_set(), add="+")
        return tree

    # ------------------------------------------------------------- refresh

    def refresh(self):
        msel = set(self.mol_tree.selection())
        self.mol_tree.delete(*self.mol_tree.get_children())
        for m in self.app.project.molecules:
            self.mol_tree.insert("", tk.END, iid=m.filename, values=(m.filename,))
        for i in msel:
            if self.mol_tree.exists(i):
                self.mol_tree.selection_add(i)

        rsel = set(self.rec_tree.selection())
        self.rec_tree.delete(*self.rec_tree.get_children())
        for r in self.app.recipes:
            self.rec_tree.insert("", tk.END, iid=r.name,
                                 values=(r.name, r.calctype, r.method_label))
        for i in rsel:
            if self.rec_tree.exists(i):
                self.rec_tree.selection_add(i)

        names = [r.name for r in self.app.recipes]
        self.opt_combo["values"] = names
        if not self.opt_recipe_var.get() or self.opt_recipe_var.get() not in names:
            # default to the first OPT-type recipe if present
            opt_default = next((r.name for r in self.app.recipes if r.calctype.upper() == "OPT"),
                               names[0] if names else "")
            self.opt_recipe_var.set(opt_default)
        self._update_count()

    def _on_mode_change(self):
        chain = self.mode_var.get() == "chain"
        # Show the OPT-recipe row only in chain mode, kept above the category row.
        if chain:
            self.optrow.pack(side=tk.TOP, fill=tk.X, pady=2, before=self._catrow)
        else:
            self.optrow.pack_forget()
        # Relabel the recipe frame to match the mode.
        self.rec_tree.master.configure(
            text="Property recipes  (derived calcs)" if chain else "Recipes  (theory levels)")
        self._update_count()

    # ------------------------------------------------------- count/preview

    def _selected_recipes(self):
        return [self.app.get_recipe(n) for n in self.rec_tree.selection()
                if self.app.get_recipe(n) is not None]

    def _selected_mols(self):
        return [m for m in self.app.project.molecules if m.filename in set(self.mol_tree.selection())]

    def _update_count(self):
        mols = self._selected_mols()
        recipes = self._selected_recipes()
        chain = self.mode_var.get() == "chain"
        category = self.category_var.get().strip() or "bench"

        self.preview.delete(*self.preview.get_children())
        rows = []  # (mol, recipe_name, type, role, target)
        targets = []

        if chain:
            opt_recipe = self.app.get_recipe(self.opt_recipe_var.get())
            for m in mols:
                if opt_recipe is not None:
                    t = self._target(m.filename, category, opt_recipe)
                    rows.append((m.filename, opt_recipe.name, opt_recipe.calctype, "OPT (parent)", t))
                    targets.append(t)
                for r in recipes:
                    t = self._target(m.filename, category, r)
                    rows.append((m.filename, r.name, r.calctype, "derived", t))
                    targets.append(t)
            n = len(mols) * (1 + len(recipes)) if opt_recipe is not None else len(mols) * len(recipes)
        else:
            for m in mols:
                for r in recipes:
                    t = self._target(m.filename, category, r)
                    rows.append((m.filename, r.name, r.calctype, "root", t))
                    targets.append(t)
            n = len(mols) * len(recipes)

        for row in rows[:500]:  # cap preview rows
            self.preview.insert("", tk.END, values=row)

        # collision check: two calcs writing to the same target dir
        dup = len(targets) != len(set(targets))
        base = "Will create {} calculation(s)  ({} molecule(s) × {} recipe(s){}).".format(
            n, len(mols), len(recipes), " + 1 OPT each" if chain else "")
        if not mols or not recipes:
            self.count_var.set("Select at least one molecule and one recipe.")
            self.count_lbl.configure(foreground="#888")
        elif dup:
            self.count_var.set(base + "  Warning: Some targets collide (same calctype+method+variant) "
                                       "and would overwrite each other — give those recipes "
                                       "distinct method labels or variants.")
            self.count_lbl.configure(foreground="#b00000")
        else:
            self.count_var.set(base)
            self.count_lbl.configure(foreground="#1a5a1a")

    def _target(self, mol_filename, category, recipe):
        return "/".join(["calcs", mol_filename, category] + list(recipe.path_parts()))

    # --------------------------------------------------------------- generate

    def on_generate(self):
        mols = self._selected_mols()
        recipes = self._selected_recipes()
        if not mols or not recipes:
            messagebox.showinfo("Nothing to generate",
                                "Select at least one molecule and one recipe.")
            return
        chain = self.mode_var.get() == "chain"
        category = self.category_var.get().strip() or "bench"

        if chain:
            opt_recipe = self.app.get_recipe(self.opt_recipe_var.get())
            if opt_recipe is None:
                messagebox.showinfo("No OPT recipe", "Choose a geometry (OPT) recipe first.")
                return
            n = len(mols) * (1 + len(recipes))
        else:
            opt_recipe = None
            n = len(mols) * len(recipes)

        if not messagebox.askyesno("Generate benchmark",
                                   "Create {} planned calculation(s) in category '{}'?"
                                   .format(n, category)):
            return

        created = 0
        for m in mols:
            if chain:
                opt = PlannedCalc(id=new_calc_id(), molecule_filename=m.filename,
                                  recipe_name=opt_recipe.name, category=category,
                                  geometry_source="initial")
                self.app.project.planned_calcs.append(opt)
                created += 1
                for r in recipes:
                    child = PlannedCalc(id=new_calc_id(), molecule_filename=m.filename,
                                        recipe_name=r.name, category=category,
                                        geometry_source="parent:" + opt.id, parent_id=opt.id)
                    self.app.project.planned_calcs.append(child)
                    created += 1
            else:
                for r in recipes:
                    self.app.project.planned_calcs.append(
                        PlannedCalc(id=new_calc_id(), molecule_filename=m.filename,
                                    recipe_name=r.name, category=category,
                                    geometry_source="initial"))
                    created += 1

        self.app.mark_dirty()
        self.app.refresh_all_tabs()
        self.app.set_status("Benchmark: created {} calculation(s) in '{}'. Go to Calculations "
                            "to build & submit.".format(created, category))
        # Jump to the Calculations tab so the user sees them ready to run.
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass

    def _select_all(self, tree):
        items = tree.get_children("")
        if items:
            tree.selection_set(items)
        return "break"
