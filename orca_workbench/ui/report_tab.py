"""Report tab — extract results from finished calculations into JSON + CSV.

Lists the calculations that have terminated normally (only those have results
worth extracting), lets you multi-select them, pick which extractors to apply,
name the output, and Generate. Generation runs on the main thread behind a
progress dialog (parse cache makes re-runs cheap); the modal dialog also means
it can't be double-spawned.
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from orca_workbench.core import orca_parser
from orca_workbench.core import reporting
from orca_workbench.core import slurm_runtime
from orca_workbench.ui.progress import ProgressDialog
from orca_workbench.ui.shortcuts import bind_mousewheel, install_tree_shift_select
from orca_workbench.ui.tooltip import tip


class ReportTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._ext_vars = {}  # key -> BooleanVar
        # CSV output styling (mirrors the Workflow Report node's options):
        # format = "both" (json+csv) | "json" | "csv"; csv_columns None = all
        # default columns, else an ordered [{"key","header"}, ...]; csv_missing is
        # the cell value for an absent property ("" blank, or "NaN" for pandas).
        self._csv_format = "both"
        self._csv_columns = None
        self._csv_missing = ""
        self._sort_col = None      # header-click column sort (None = scan order)
        self._sort_desc = False
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        ttk.Label(top, text="Generate a results report (JSON + CSV) from finished calculations. "
                            "The list is scanned on demand — click Scan, or just hit Generate.",
                  foreground="#444").pack(side=tk.LEFT, padx=4)
        b_refresh = ttk.Button(top, text="Scan for finished calcs", command=self.rescan)
        b_refresh.pack(side=tk.RIGHT, padx=4)
        tip(b_refresh, "Scan every calc's .out file for normal termination and list them. Done on "
                       "demand (not on opening the tab) because it reads a file per calc — slow "
                       "over the cluster's shared filesystem with many calcs.")

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left: finished-calc list
        left = ttk.LabelFrame(paned, text="Finished calculations")
        paned.add(left, weight=3)
        columns = ("type", "molecule", "recipe", "state")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        self._col_labels = {"type": "Type", "molecule": "Molecule",
                            "recipe": "Recipe", "state": "State"}
        for col, width in (("type", 60), ("molecule", 90), ("recipe", 170), ("state", 150)):
            self.tree.heading(col, text=self._col_labels[col],
                              command=lambda c=col: self._on_header_click(c))
            self.tree.column(col, width=width, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        install_tree_shift_select(self.tree)
        self.tree.bind("<Enter>", lambda e: self.tree.focus_set(), add="+")
        tip(self.tree, "Calculations that terminated normally (only these have results to "
                       "extract). Multi-select with click / Ctrl+click / Shift+click / Ctrl+A. "
                       "With nothing selected, Generate uses all finished calcs.")

        # Right: extractors + output controls
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        ext_frame = ttk.LabelFrame(right, text="Extract")
        ext_frame.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        self.all_var = tk.BooleanVar(value=True)
        all_cb = ttk.Checkbutton(ext_frame, text="All of the below", variable=self.all_var,
                                 command=self._on_toggle_all)
        all_cb.pack(anchor=tk.W, padx=4, pady=(4, 2))
        tip(all_cb, "Tick/untick every extractor at once.")
        ttk.Separator(ext_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(ext_frame, text="(tip: click-and-drag down the boxes to flip several at once)",
                  foreground="#888").pack(anchor=tk.W, padx=16, pady=(0, 2))
        # The extractor list keeps growing, so the per-extractor boxes live in a
        # fixed-height scrollable area — otherwise they push the Generate button
        # (in the Output frame below) off the bottom of the tab.
        boxwrap = ttk.Frame(ext_frame)
        boxwrap.pack(side=tk.TOP, fill=tk.X, padx=0, pady=0)
        box_canvas = tk.Canvas(boxwrap, height=190, highlightthickness=0)
        box_sb = ttk.Scrollbar(boxwrap, orient=tk.VERTICAL, command=box_canvas.yview)
        box_canvas.configure(yscrollcommand=box_sb.set)
        box_sb.pack(side=tk.RIGHT, fill=tk.Y)
        box_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        box_inner = ttk.Frame(box_canvas)
        box_win = box_canvas.create_window((0, 0), window=box_inner, anchor="nw")
        box_inner.bind("<Configure>",
                       lambda e: box_canvas.configure(scrollregion=box_canvas.bbox("all")))
        box_canvas.bind("<Configure>",
                        lambda e: box_canvas.itemconfigure(box_win, width=e.width))
        # Blender-style drag-paint: press on a box and drag across others to set
        # them all to the value the first one took. _paint_vars maps each box
        # widget to its var so motion can find which box is under the cursor.
        self._paint_vars = {}
        self._painting = False
        self._paint_value = False
        for ex in reporting.EXTRACTORS:
            var = tk.BooleanVar(value=True)
            self._ext_vars[ex.key] = var
            cb = ttk.Checkbutton(box_inner, text="{}   ({})".format(ex.label, ex.applies_hint),
                                 variable=var, command=self._sync_all_var)
            cb.pack(anchor=tk.W, padx=16, pady=1)
            self._paint_vars[str(cb)] = var
            cb.bind("<Button-1>", lambda e, v=var: self._paint_press(v))
            cb.bind("<B1-Motion>", self._paint_motion)
            cb.bind("<ButtonRelease-1>", self._paint_release)
            tip(cb, "Applies to calc type: {}. Skipped automatically for calcs that don't "
                    "contain it.".format(ex.applies_hint))
        bind_mousewheel(box_canvas, box_canvas)   # wheel scrolls anywhere over the list

        out_frame = ttk.LabelFrame(right, text="Output")
        out_frame.pack(side=tk.TOP, fill=tk.X, padx=2, pady=6)
        row = ttk.Frame(out_frame)
        row.pack(fill=tk.X, padx=4, pady=4)
        ttk.Label(row, text="Report name:").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value="report")
        name_entry = ttk.Entry(row, textvariable=self.name_var)
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        tip(name_entry, "Base name for the output files: <name>.json and <name>.csv, written to "
                        "the project root. Existing files with the same name are overwritten.")

        # Format + CSV-styling controls (same options as the Workflow Report node).
        # Rebuilt on a format change so the CSV rows show only in CSV/both modes.
        self._fmt_frame = ttk.Frame(out_frame)
        self._fmt_frame.pack(fill=tk.X, padx=4, pady=(0, 2))
        self._build_csv_controls()

        self.b_generate = tk.Button(out_frame, text="Generate report", command=self.on_generate,
                                    font=("TkDefaultFont", 10, "bold"),
                                    bg="#cfe8cf", activebackground="#bfe0bf")
        self.b_generate.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 6))
        tip(self.b_generate, "Run the selected extractors over the selected (or all) finished "
                             "calcs and write the report to the project root in the chosen "
                             "format. Shows a progress bar; the JSON keeps full detail, the CSV "
                             "is a flat scalar summary (customisable columns) for spreadsheets.")

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        self.log = tk.Text(log_frame, height=7, wrap="word", state=tk.DISABLED, font=("Courier", 9))
        self.log.pack(fill=tk.BOTH, expand=True)

    # ---- extractor checkbox sync ----

    def _on_toggle_all(self):
        val = self.all_var.get()
        for var in self._ext_vars.values():
            var.set(val)

    def _sync_all_var(self):
        self.all_var.set(all(v.get() for v in self._ext_vars.values()))

    # ---- Blender-style drag-paint over the extractor checkboxes ----

    def _paint_press(self, var):
        # Set the pressed box to the opposite of its current value, and remember
        # that value as the "paint" value to apply to anything we drag over.
        # Return "break" so the Checkbutton's own toggle doesn't fire on release
        # (which would undo this), keeping us in full control.
        self._painting = True
        self._paint_value = not bool(var.get())
        var.set(self._paint_value)
        self._sync_all_var()
        return "break"

    def _paint_motion(self, event):
        if not self._painting:
            return
        # During a drag, motion events all go to the press widget, so use the
        # global pointer position to find which box is currently under it.
        w = self.winfo_containing(event.x_root, event.y_root)
        var = self._paint_vars.get(str(w)) if w is not None else None
        if var is not None and bool(var.get()) != self._paint_value:
            var.set(self._paint_value)
            self._sync_all_var()

    def _paint_release(self, _event):
        self._painting = False
        return "break"

    # ---- CSV output styling (format + custom columns + missing value) ----

    def _build_csv_controls(self):
        """(Re)build the output-format selector and, in CSV/both mode, the custom
        column editor + missing-value picker. Rebuilt on a format change."""
        f = self._fmt_frame
        for w in f.winfo_children():
            w.destroy()
        ttk.Label(f, text="Output format:", font=("TkDefaultFont", 9, "bold")).pack(
            anchor=tk.W, pady=(4, 0))
        fmt = tk.StringVar(value=self._csv_format)

        def on_fmt():
            self._csv_format = fmt.get()
            self._build_csv_controls()   # show/hide the CSV options
        for val, txt in (("both", "JSON + CSV"), ("json", "JSON only"), ("csv", "CSV only")):
            ttk.Radiobutton(f, text=txt, variable=fmt, value=val, command=on_fmt).pack(
                anchor=tk.W, padx=12)

        if fmt.get() == "json":
            return   # no CSV options when JSON-only
        b_cols = ttk.Button(f, text="Customise CSV columns...", command=self._edit_csv_columns)
        b_cols.pack(anchor=tk.W, pady=(4, 0))
        tip(b_cols, "Pick which properties become CSV columns, rename their headers, and set "
                    "their left-to-right order. Rows are always calculations.")
        summary = ("all default columns" if not self._csv_columns
                   else "{} custom column(s)".format(len(self._csv_columns)))
        ttk.Label(f, text="CSV: one row per calculation - " + summary,
                  foreground="#777", wraplength=240, justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Label(f, text="Missing value:").pack(anchor=tk.W, pady=(4, 0))
        miss = tk.StringVar(value=self._csv_missing)

        def on_miss():
            self._csv_missing = miss.get()
        for val, txt in (("", "Empty cell"), ("NaN", "NaN")):
            ttk.Radiobutton(f, text=txt, variable=miss, value=val, command=on_miss).pack(
                anchor=tk.W, padx=12)

    def _edit_csv_columns(self):
        from orca_workbench.ui.csv_columns import edit_csv_columns_dialog

        def on_save(cols):
            self._csv_columns = cols
            self._build_csv_controls()   # refresh the summary label

        edit_csv_columns_dialog(self, self._csv_columns, "CSV columns", on_save)

    # ---- finished-calc discovery ----

    def refresh(self):
        # Opening the Report tab must be instant even with hundreds of finished
        # calcs, so we do NOT scan .out files here (that reads a file per calc,
        # slow over the shared FS). The list is populated on demand by rescan()
        # ("Scan" button), and Generate computes it fresh anyway. Just clear any
        # rows left over from a previous project/scan.
        self.tree.delete(*self.tree.get_children())

    def rescan(self):
        """Scan every calc's .out for normal termination and populate the list.
        Explicit action only — never on tab open."""
        finished = self._finished_calcs()
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for calc, recipe in finished:
            calctype = recipe.calctype if recipe else "?"
            self.tree.insert("", tk.END, iid=calc.id,
                             values=(calctype, calc.molecule_filename, calc.recipe_name,
                                     "terminated normally"))
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)
        self._apply_sort()   # keep the active header sort after a re-scan
        self.app.set_status("Report: {} finished calculation(s) found.".format(len(finished)))

    # ---- header-click column sorting ----

    def _on_header_click(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self._apply_sort()

    def _apply_sort(self):
        for c, label in self._col_labels.items():
            arrow = (" ▼" if self._sort_desc else " ▲") if c == self._sort_col else ""
            self.tree.heading(c, text=label + arrow)
        if not self._sort_col:
            return
        items = list(self.tree.get_children(""))
        items.sort(key=lambda iid: str(self.tree.set(iid, self._sort_col)).lower(),
                   reverse=self._sort_desc)
        for idx, iid in enumerate(items):
            self.tree.move(iid, "", idx)

    def _finished_calcs(self):
        """List (calc, recipe) for calcs whose .out shows normal termination."""
        out = []
        for calc in self.app.project.planned_calcs:
            if not calc.job_id or not calc.rundir:
                continue
            op = self._out_path(calc)
            if not op or not os.path.isfile(op):
                continue
            # Only the tail is needed to see "TERMINATED NORMALLY" — reading the
            # whole (possibly tens-of-MB) file here made opening a big project slow.
            if orca_parser._TERM_OK.search(orca_parser.read_tail(op)):
                out.append((calc, self.app.get_recipe(calc.recipe_name)))
        return out

    def _out_path(self, calc):
        rundir_abs = os.path.join(self.app.project.root(), calc.rundir)
        return slurm_runtime.find_output_file(rundir_abs, calc.molecule_filename, calc.job_id)

    # ---- generate ----

    def on_generate(self):
        finished = self._finished_calcs()
        if not finished:
            messagebox.showinfo("Nothing to report",
                                "No finished calculations found. Submit jobs and let them "
                                "complete first (use Refresh status on the Calculations tab).")
            return
        sel = set(self.tree.selection())
        if sel:
            finished = [(c, r) for (c, r) in finished if c.id in sel]
        keys = [k for k, v in self._ext_vars.items() if v.get()]
        if not keys:
            messagebox.showinfo("No extractors", "Tick at least one thing to extract.")
            return
        name = (self.name_var.get() or "report").strip()
        import re
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "report"
        root = self.app.project.root()
        json_path = os.path.join(root, name + ".json")
        csv_path = os.path.join(root, name + ".csv")
        want_json = self._csv_format in ("both", "json")
        want_csv = self._csv_format in ("both", "csv")

        contexts = []
        for calc, recipe in finished:
            rundir_abs = os.path.join(root, calc.rundir) if calc.rundir else None
            contexts.append(reporting.CalcContext(
                calc_id=calc.id,
                label="{} {}".format(recipe.calctype if recipe else "?", calc.molecule_filename),
                molecule=calc.molecule_filename,
                calctype=recipe.calctype if recipe else "?",
                method=recipe.method_label if recipe else "?",
                out_path=self._out_path(calc),
                rundir_abs=rundir_abs,
            ))

        self._log_clear()
        pd = ProgressDialog(self, "Generating report", total=len(contexts))

        def progress(i, n, label):
            pd.step("Extracting {} ({}/{})".format(label, i + 1, n))

        try:
            report = reporting.assemble_report(contexts, keys, progress=progress)
            if want_json:
                reporting.write_json(report, json_path)
            if want_csv:
                reporting.write_csv(report, csv_path, columns=self._csv_columns,
                                    missing=self._csv_missing)
        except Exception as e:
            pd.close()
            messagebox.showerror("Report failed", str(e))
            return
        pd.close()
        if want_json:
            self._log("Wrote {}".format(json_path))
        if want_csv:
            self._log("Wrote {}".format(csv_path))
        self._log("Extracted {} extractor(s) from {} calc(s).".format(len(keys), len(contexts)))
        written = " + ".join([s for s, w in ((name + ".json", want_json),
                                             (name + ".csv", want_csv)) if w])
        self.app.set_status("Report written: {}".format(written))

    def _select_all(self, _event=None):
        items = self.tree.get_children("")
        if items:
            self.tree.selection_set(items)
        return "break"

    def _log(self, msg):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _log_clear(self):
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
