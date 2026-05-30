"""Calculations tab — the unified job lifecycle: plan, derive, build, submit, monitor.

A calculation moves through: planned -> built -> submitted -> running -> done,
and a finished calc can spawn *derived* calcs (e.g. FREQ / NMR / SP from a
converged OPT). Derived calcs:
  - live in a sibling folder under the same category as their parent
    (calcs/<mol>/<category>/OPT/...  ->  .../FREQ/...),
  - inherit the parent's optimised geometry (geometry_source = "parent:<id>"),
  - can't be built until the parent has produced that geometry (natural gate).

This mirrors the user's real workflow (gen -> OPT -> confirm -> FREQ -> check
imaginary freqs -> SP/NMR), so the common path is one "Derive" click. Build and
submit guard against clobbering a job that's already running.

Merged from the former separate Calculations + Run tabs so a calc's whole state
lives in one place.
"""

import os
import platform
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

from orca_studio.core import config as config_mod
from orca_studio.core import coords as coords_mod
from orca_studio.core import inputs as inputs_mod
from orca_studio.core import local_runner as local_runner_mod
from orca_studio.core import orca_parser
from orca_studio.core import slurm as slurm_mod
from orca_studio.core import slurm_runtime
from orca_studio.core.project import Molecule, PlannedCalc, new_calc_id
from orca_studio.ui.modal import make_modal
from orca_studio.ui.plot_window import LivePlotWindow
from orca_studio.ui.progress import ProgressDialog
from orca_studio.ui.tooltip import tip


# Sentinel job id for jobs run by the local runner (vs a numeric SLURM id).
LOCAL_JOB = "local"


# Row background by lifecycle state.
_TAGS = {
    "notbuilt": "#fffde7",  # pale yellow — planned / has issue
    "waiting": "#eeeeee",   # grey — derived, waiting on parent
    "built": "",            # white — built, not submitted
    "running": "#e3f2fd",   # pale blue — queued / running
    "done": "#e8f5e9",      # pale green — terminated normally
    "error": "#ffebee",     # pale red — error in output
}


class CalculationsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._selected_id = None  # type: Optional[str]
        self._suppress = False
        self._squeue_states = None  # type: Optional[dict]
        self._clipboard_ids = []  # type: List[str]
        # Parse cache: path -> (mtime, size, parsed dict). Avoids re-reading a
        # (possibly huge) .out on every refresh when it hasn't changed.
        self._parse_cache = {}
        # Local-run support: surfaced when sbatch isn't available (i.e. a PC).
        self._local_mode = not slurm_runtime.sbatch_available()
        self._local_runner = None      # type: Optional[local_runner_mod.LocalRunner]
        self._local_poll_id = None
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        # Primary toolbar, arranged left→right by typical chronology of use:
        #   create -> build ......... monitor | launch
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))

        # Most-used entry point, most prominent, leftmost.
        b_all = tk.Button(bar, text="Add for all molecules...", command=self.on_add_for_all,
                          font=("TkDefaultFont", 10, "bold"))
        b_all.pack(side=tk.LEFT, padx=(0, 2))
        b_add = ttk.Button(bar, text="Add", command=self.on_add)
        b_derive = ttk.Button(bar, text="Derive →", command=self.on_derive)
        b_remove = ttk.Button(bar, text="Remove", command=self.on_remove)
        for b in (b_add, b_derive, b_remove):
            b.pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)
        b_build = tk.Button(bar, text="Build", command=self.on_build,
                            font=("TkDefaultFont", 10, "bold"))
        b_build.pack(side=tk.LEFT, padx=2)

        # Launch action: own place, far right, signal (amber) colour. The label
        # and behaviour adapt to the environment — sbatch present → cluster
        # Submit; absent (a PC) → run ORCA locally through a serial queue.
        if self._local_mode:
            b_submit = tk.Button(bar, text="Run locally ▶", command=self.on_run_local,
                                 font=("TkDefaultFont", 10, "bold"),
                                 bg="#e0a35a", activebackground="#e8b673", fg="#222222")
            b_submit.pack(side=tk.RIGHT, padx=(6, 0))
            b_stop = tk.Button(bar, text="■ Stop", command=self.on_stop_local)
            b_stop.pack(side=tk.RIGHT, padx=(6, 2))
            self.concurrency_var = tk.IntVar(value=1)
            sp = ttk.Spinbox(bar, from_=1, to=16, width=3, textvariable=self.concurrency_var)
            sp.pack(side=tk.RIGHT, padx=(2, 2))
            ttk.Label(bar, text="run at once:").pack(side=tk.RIGHT, padx=(8, 0))
            tip(b_submit, "Run the built calc(s) with a local ORCA install, in a serial queue "
                          "(one at a time by default). Each job's output streams to its run dir, "
                          "so the live plot and reports work just like on the cluster. Shown "
                          "because sbatch isn't available here.")
            tip(b_stop, "Cancel the local run: stop the current job and clear the queue.")
            tip(sp, "How many ORCA jobs to run simultaneously. Keep this at 1 on a few-core "
                    "laptop — each job already uses its recipe's %pal nprocs cores, and running "
                    "several multi-core jobs at once just thrashes.")
        else:
            b_submit = tk.Button(bar, text="Submit ▶", command=self.on_submit,
                                 font=("TkDefaultFont", 10, "bold"),
                                 bg="#e0a35a", activebackground="#e8b673", fg="#222222")
            b_submit.pack(side=tk.RIGHT, padx=(6, 0))
            tip(b_submit, "sbatch the selected built calcs (or all built-and-unsubmitted). "
                          "Cluster login node only. Warns before submitting a derived calc whose "
                          "parent hasn't finished. Shows a progress bar.")
        # Monitoring: distinct, bigger/bolder, light shade, just left of the launch action.
        b_status = tk.Button(bar, text="↻ Refresh status (F5)", command=self.on_refresh_status,
                             font=("TkDefaultFont", 11, "bold"),
                             bg="#d3e6f5", activebackground="#c3dcf0")
        b_status.pack(side=tk.RIGHT, padx=2)

        tip(b_all, "Bulk: pick one recipe and queue a root calculation for every molecule. The "
                   "usual starting point — most projects begin by OPT-ing the whole set.")
        tip(b_add, "Add a single root calculation; choose molecule + recipe on the right.")
        tip(b_derive, "Create follow-up calc(s) from the selected finished one(s) — the OPT → "
                      "FREQ / NMR / SP step. Children go in sibling folders and inherit the "
                      "parent's OPTIMISED geometry. Works on a multi-selection (batch). "
                      "Shortcut: select rows, Ctrl+C, Ctrl+V.")
        tip(b_remove, "Remove the selected calculation(s). Blocks removing a running job; warns "
                      "if a calc has derived children. Shortcut: Delete.")
        tip(b_build, "Write .inp + .slurm for the selected calcs (or all buildable if none "
                     "selected). Skips active jobs and derived calcs whose parent isn't ready. "
                     "Shows a progress bar for large batches.")
        tip(b_submit, "sbatch the selected built calcs (or all built-and-unsubmitted). Cluster "
                      "login node only. Warns before submitting a derived calc whose parent "
                      "hasn't finished. Shows a progress bar.")
        tip(b_status, "Query squeue for job states (finished jobs are checked against their .out "
                      "for normal termination). Keyboard: F5 anywhere in the app.")

        # Secondary row: less-frequent options.
        bar2 = ttk.Frame(self)
        bar2.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 4))
        b_open = ttk.Button(bar2, text="Open project folder", command=self.on_open_folder)
        b_open.pack(side=tk.LEFT, padx=2)
        self.write_submit_var = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(bar2, text="write submit_all.sh on Build", variable=self.write_submit_var)
        cb.pack(side=tk.LEFT, padx=12)
        tip(b_open, "Open the project root in the OS file manager (on the gateway, a Linux file "
                    "manager over X if one is installed).")
        tip(cb, "Also write submit_all.sh at the project root on Build — a bash fallback that "
                "sbatches every built .slurm. Handy for submitting from a terminal instead.")

        # Main split: tree | editor
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=3)
        columns = ("type", "molecule", "recipe", "parent", "state", "job", "path")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        for col, label, width, anchor in [
            ("type", "Type", 55, tk.W),
            ("molecule", "Molecule", 90, tk.W),
            ("recipe", "Recipe", 150, tk.W),
            ("parent", "Parent", 90, tk.W),
            ("state", "State", 180, tk.W),
            ("job", "Job", 70, tk.W),
            ("path", "Target dir", 240, tk.W),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, color in _TAGS.items():
            self.tree.tag_configure(tag, background=color)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        self.tree.bind("<Control-c>", lambda e: self._copy())
        self.tree.bind("<Control-C>", lambda e: self._copy())
        self.tree.bind("<Control-v>", lambda e: self._paste())
        self.tree.bind("<Control-V>", lambda e: self._paste())
        self.tree.bind("<Delete>", lambda e: (self.on_remove(), "break")[1])
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Enter>", lambda e: self.tree.focus_set(), add="+")
        tip(self.tree,
            "Every calculation and its lifecycle. Row colors: yellow = planned/needs attention, "
            "grey = waiting for parent, white = built, blue = queued/running, green = done, "
            "red = error.\n\n"
            "Double-click a submitted row to watch live SCF/geometry progress.\n"
            "Right-click a finished FREQ to plot its IR spectrum, or finished NMR calc(s) "
            "to plot a simulated NMR spectrum.\n"
            "Ctrl+C / Ctrl+V: copy calcs and paste them as derived children.\n"
            "Delete removes; Ctrl+A selects all.")

        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        self._build_editor(right)

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))
        self.log = tk.Text(log_frame, height=6, wrap="word", state=tk.DISABLED, font=("Courier", 9))
        self.log.pack(fill=tk.BOTH, expand=True)

    def _build_editor(self, right):
        editor = ttk.LabelFrame(right, text="Edit selected calculation")
        editor.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        editor.columnconfigure(1, weight=1)

        self.mol_var = tk.StringVar()
        self.recipe_var = tk.StringVar()
        self.category_var = tk.StringVar(value="gen")
        self.geom_mode_var = tk.StringVar(value="initial")
        self.geom_path_var = tk.StringVar(value="")

        mol_lbl = ttk.Label(editor, text="Molecule:")
        mol_lbl.grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.mol_combo = ttk.Combobox(editor, textvariable=self.mol_var, state="readonly")
        self.mol_combo.grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=2)
        self.mol_combo.bind("<<ComboboxSelected>>", lambda e: self._on_field_change())
        tip(mol_lbl, "Which molecule this calculation runs on.")

        rec_lbl = ttk.Label(editor, text="Recipe:")
        rec_lbl.grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.recipe_combo = ttk.Combobox(editor, textvariable=self.recipe_var, state="readonly")
        self.recipe_combo.grid(row=1, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=2)
        self.recipe_combo.bind("<<ComboboxSelected>>", lambda e: self._on_field_change())
        tip(rec_lbl, "Method + basis + variant. For a derived calc, change this to the follow-up "
                     "type (e.g. a FREQ or NMR recipe).")

        cat_lbl = ttk.Label(editor, text="Category dir:")
        cat_lbl.grid(row=2, column=0, sticky=tk.W, padx=4, pady=2)
        self.cat_entry = ttk.Entry(editor, textvariable=self.category_var)
        self.cat_entry.grid(row=2, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=2)
        self.category_var.trace_add("write", lambda *_: self._on_field_change())
        tip(cat_lbl, "First directory level under calcs/<mol>/. Derived calcs inherit the "
                     "parent's category so they land as siblings.")

        geom_lbl = ttk.Label(editor, text="Geometry:")
        geom_lbl.grid(row=3, column=0, sticky=tk.NW, padx=4, pady=2)
        gframe = ttk.Frame(editor)
        gframe.grid(row=3, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=2)
        self.rb_init = ttk.Radiobutton(gframe, text="Molecule's initial XYZ",
                                       variable=self.geom_mode_var, value="initial",
                                       command=self._on_field_change)
        self.rb_parent = ttk.Radiobutton(gframe, text="From parent's optimised geometry",
                                         variable=self.geom_mode_var, value="parent",
                                         command=self._on_field_change)
        self.rb_file = ttk.Radiobutton(gframe, text="From file:", variable=self.geom_mode_var,
                                       value="file", command=self._on_field_change)
        self.rb_init.pack(anchor=tk.W)
        self.rb_parent.pack(anchor=tk.W)
        self.rb_file.pack(anchor=tk.W)
        tip(self.rb_init, "Use the molecule's own generated/imported XYZ. The starting point for "
                          "root calcs like OPT.")
        tip(self.rb_parent, "Use the optimised geometry produced by the parent calc. Set "
                            "automatically when you Derive; the parent must finish before this "
                            "can be built.")
        tip(self.rb_file, "Use a specific .xyz/.inp file (path relative to the project root).")

        filerow = ttk.Frame(editor)
        filerow.grid(row=4, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=2)
        self.geom_entry = ttk.Entry(filerow, textvariable=self.geom_path_var)
        self.geom_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.geom_browse = ttk.Button(filerow, text="Browse...", command=self.on_browse_geom)
        self.geom_browse.pack(side=tk.LEFT, padx=2)
        self.geom_path_var.trace_add("write", lambda *_: self._on_field_change())

        info = ttk.LabelFrame(right, text="Resolved info")
        info.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.info_text = tk.Text(info, height=10, wrap="word", state=tk.DISABLED, font=("Courier", 9))
        self.info_text.pack(fill=tk.BOTH, expand=True)

        self._editor_widgets = [self.mol_combo, self.recipe_combo, self.cat_entry,
                                self.rb_init, self.rb_parent, self.rb_file,
                                self.geom_entry, self.geom_browse]

    # ------------------------------------------------------------- refresh

    def refresh(self):
        self.mol_combo["values"] = [m.filename for m in self.app.project.molecules]
        self.recipe_combo["values"] = [r.name for r in self.app.recipes]
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for c in self.app.project.planned_calcs:
            if self.tree.exists(c.id):
                continue  # defensive: skip a duplicate id rather than crash
            self.tree.insert("", tk.END, iid=c.id, values=self._row_values(c),
                             tags=(self._row_tag(c),))
        # restore selection
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        elif self._selected_id and self.tree.exists(self._selected_id):
            self.tree.selection_set(self._selected_id)
        else:
            self._clear_editor()

    def _row_values(self, calc):
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        recipe = self.app.get_recipe(calc.recipe_name)
        calctype = recipe.calctype if recipe else "?"
        parent = self.app.project.calc_by_id(calc.parent_id) if calc.parent_id else None
        parent_disp = self._short(parent) if parent else ""
        state, _ = self._display_state(calc)
        path = self._target_dir(calc, mol, recipe)
        return (calctype, calc.molecule_filename, calc.recipe_name, parent_disp,
                state, calc.job_id or "", path)

    def _row_tag(self, calc):
        return self._display_state(calc)[1]

    def _short(self, calc):
        if calc is None:
            return ""
        recipe = self.app.get_recipe(calc.recipe_name)
        t = recipe.calctype if recipe else "?"
        return "{} {}".format(t, calc.molecule_filename)

    # --------------------------------------------------------- lifecycle

    def _own_state(self, calc):
        # type: (PlannedCalc) -> Tuple[str, str, bool, bool]
        """(label, tag, done, active). done = finished OK; active = queued/running."""
        if not calc.exported:
            return ("planned", "notbuilt", False, False)
        if not calc.job_id:
            return ("built, not submitted", "built", False, False)
        # Local-run jobs: prefer the runner's live state while it's queued or
        # running; once finished, fall through to parse the .out for the real
        # convergence result (which also survives an app restart).
        if calc.job_id == LOCAL_JOB and self._local_runner is not None:
            st = self._local_runner.state(calc.id)
            if st == local_runner_mod.QUEUED:
                return ("local: queued", "running", False, True)
            if st == local_runner_mod.RUNNING:
                return ("local: running", "running", False, True)
            if st == local_runner_mod.CANCELLED:
                return ("local: cancelled", "error", False, False)
        sq = self._squeue_states
        if sq and calc.job_id in sq:
            return ("queue: {}".format(sq[calc.job_id]), "running", False, True)
        parsed = self._parse_out(calc)
        if parsed is None:
            return ("submitted (job {})".format(calc.job_id), "running", False, True)
        if parsed.get("has_error"):
            return ("error in output", "error", True, False)
        if parsed.get("terminated_normally"):
            return ("done — terminated normally", "done", True, False)
        return ("running — {}".format(orca_parser.short_status(parsed)), "running", False, True)

    def _display_state(self, calc):
        # type: (PlannedCalc) -> Tuple[str, str]
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        recipe = self.app.get_recipe(calc.recipe_name)
        ok, issue = self._validate(calc, mol, recipe)
        if not calc.exported:
            if calc.parent_id:
                parent = self.app.project.calc_by_id(calc.parent_id)
                if parent is not None:
                    _, _, pdone, _ = self._own_state(parent)
                    if not pdone:
                        return ("waiting for parent ({})".format(self._short(parent)), "waiting")
            if not ok:
                return (issue, "notbuilt")
            return ("ready to build", "notbuilt")
        label, tag, _done, _active = self._own_state(calc)
        return (label, tag)

    def _parse_out(self, calc):
        path = self._out_path(calc)
        if not path or not os.path.isfile(path):
            return None
        try:
            st = os.stat(path)
            sig = (st.st_mtime, st.st_size)
        except OSError:
            return None
        cached = self._parse_cache.get(path)
        if cached is not None and cached[0] == sig[0] and cached[1] == sig[1]:
            return cached[2]  # unchanged since last parse — reuse
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                parsed = orca_parser.parse_orca_output(f.read())
        except IOError:
            return None
        # Bound the cache (one entry per output file is plenty for any project).
        if len(self._parse_cache) > 500:
            self._parse_cache.clear()
        self._parse_cache[path] = (sig[0], sig[1], parsed)
        return parsed

    def _out_path(self, calc):
        if not calc.job_id or not calc.rundir:
            return None
        rundir_abs = os.path.join(self.app.project.root(), calc.rundir)
        return slurm_runtime.find_output_file(rundir_abs, calc.molecule_filename, calc.job_id)

    # ------------------------------------------------------- selection/editor

    def _on_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        if len(sel) > 1:
            self._selected_id = None
            self._clear_editor()
            self._set_info("{} calculations selected. Build / Submit / Remove act on the whole "
                           "selection; the editor edits a single row.".format(len(sel)))
            return
        self._selected_id = sel[0]
        calc = self.app.project.calc_by_id(self._selected_id)
        if calc is None:
            return
        self._suppress = True
        try:
            self.mol_var.set(calc.molecule_filename)
            self.recipe_var.set(calc.recipe_name)
            self.category_var.set(calc.category)
            if calc.geometry_source.startswith("parent:"):
                self.geom_mode_var.set("parent")
                self.geom_path_var.set("")
            elif calc.geometry_source.startswith("file:"):
                self.geom_mode_var.set("file")
                self.geom_path_var.set(calc.geometry_source[len("file:"):])
            else:
                self.geom_mode_var.set("initial")
                self.geom_path_var.set("")
        finally:
            self._suppress = False
        # The "parent" radio is only meaningful if this calc has a parent.
        self.rb_parent.configure(state=(tk.NORMAL if calc.parent_id else tk.DISABLED))
        # Lock the editor once a job has been submitted (avoid editing live jobs).
        self._set_editor_locked(bool(calc.job_id))
        self._update_info(calc)

    def _set_editor_locked(self, locked):
        for w in self._editor_widgets:
            if isinstance(w, ttk.Combobox):
                w.configure(state="disabled" if locked else "readonly")
            else:
                w.configure(state="disabled" if locked else "normal")
        if not locked:
            # re-apply parent-radio enabledness
            calc = self.app.project.calc_by_id(self._selected_id) if self._selected_id else None
            self.rb_parent.configure(state=(tk.NORMAL if (calc and calc.parent_id) else tk.DISABLED))

    def _clear_editor(self):
        self._suppress = True
        try:
            self.mol_var.set("")
            self.recipe_var.set("")
            self.category_var.set("gen")
            self.geom_mode_var.set("initial")
            self.geom_path_var.set("")
        finally:
            self._suppress = False
        self._set_info("")

    def _on_field_change(self):
        if self._suppress or not self._selected_id:
            return
        calc = self.app.project.calc_by_id(self._selected_id)
        if calc is None:
            return
        if calc.job_id:
            return  # locked; shouldn't happen (widgets disabled) but be safe
        calc.molecule_filename = self.mol_var.get()
        calc.recipe_name = self.recipe_var.get()
        calc.category = self.category_var.get().strip() or "gen"
        mode = self.geom_mode_var.get()
        if mode == "parent" and calc.parent_id:
            calc.geometry_source = "parent:" + calc.parent_id
        elif mode == "file":
            p = self.geom_path_var.get().strip()
            calc.geometry_source = "file:" + p if p else "initial"
        else:
            calc.geometry_source = "initial"
        calc.exported = False  # editing invalidates any prior build
        self.app.mark_dirty()
        self.refresh()
        if self.tree.exists(calc.id):
            self.tree.selection_set(calc.id)
        self._update_info(calc)

    # ------------------------------------------------------------ validation

    def _validate(self, calc, mol, recipe):
        # type: (PlannedCalc, Optional[Molecule], Optional[inputs_mod.Recipe]) -> Tuple[bool, str]
        if mol is None:
            return False, "molecule missing"
        if recipe is None:
            return False, "recipe missing"
        src = calc.geometry_source
        if src == "initial":
            if not mol.generated or not mol.xyz_path:
                return False, "molecule XYZ not generated"
        elif src.startswith("parent:"):
            parent = self.app.project.calc_by_id(src[len("parent:"):])
            if parent is None:
                return False, "parent calc not found"
            pgeo = self._parent_geometry_path(parent, mol)
            if not pgeo or not os.path.isfile(pgeo):
                return False, "parent geometry not ready"
        elif src.startswith("file:"):
            abs_p = os.path.join(self.app.project.root(), src[len("file:"):])
            if not os.path.isfile(abs_p):
                return False, "geometry file not found"
        else:
            return False, "unknown geometry source"
        if not self.app.usermail:
            return True, "no user email set"
        return True, ""

    def _parent_geometry_path(self, parent, mol):
        if not parent.rundir:
            return None
        # ORCA writes the optimised geometry to <basename>.xyz in the run dir.
        return os.path.join(self.app.project.root(), parent.rundir, mol.filename + ".xyz")

    def _target_dir(self, calc, mol, recipe):
        if mol is None or recipe is None:
            return "(unresolved)"
        return "/".join(["calcs", mol.filename, calc.category] + list(recipe.path_parts()))

    def _update_info(self, calc):
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        recipe = self.app.get_recipe(calc.recipe_name)
        lines = []
        state, _ = self._display_state(calc)
        lines.append("State: {}".format(state))
        if mol is None:
            lines.append("Molecule not found: " + calc.molecule_filename)
        else:
            lines.append("Molecule: {} (q={}, mult={})".format(mol.name, mol.charge, mol.multiplicity))
        if recipe is None:
            lines.append("Recipe not found: " + calc.recipe_name)
        else:
            lines.append("Recipe: {} ({} / {}{})".format(
                recipe.name, recipe.calctype, recipe.method_label,
                "/" + recipe.variant if recipe.variant else ""))
        lines.append("Target: " + self._target_dir(calc, mol, recipe))
        src = calc.geometry_source
        if src.startswith("parent:"):
            parent = self.app.project.calc_by_id(src[len("parent:"):])
            if parent is None:
                lines.append("Geometry: parent calc MISSING")
            else:
                pgeo = self._parent_geometry_path(parent, mol) if mol else None
                ready = pgeo and os.path.isfile(pgeo)
                lines.append("Geometry: from parent {} [{}]".format(
                    self._short(parent), "ready" if ready else "NOT ready yet"))
        elif src.startswith("file:"):
            lines.append("Geometry: file {}".format(src[len("file:"):]))
        else:
            lines.append("Geometry: molecule's initial XYZ")
        if calc.job_id:
            lines.append("Job id: {}".format(calc.job_id))
            op = self._out_path(calc)
            lines.append("Output: {}".format(op or "(not found yet)"))
        self._set_info("\n".join(lines))

    def _set_info(self, text):
        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert("1.0", text)
        self.info_text.configure(state=tk.DISABLED)

    # --------------------------------------------------------------- actions

    def on_add(self):
        if not self.app.project.molecules:
            messagebox.showinfo("No molecules", "Add at least one molecule first.")
            return
        if not self.app.recipes:
            messagebox.showinfo("No recipes", "No recipes loaded.")
            return
        calc = PlannedCalc(id=new_calc_id(),
                           molecule_filename=self.app.project.molecules[0].filename,
                           recipe_name=self.app.recipes[0].name)
        self.app.project.planned_calcs.append(calc)
        self.app.mark_dirty()
        self.refresh()
        self.tree.selection_set(calc.id)
        self.tree.see(calc.id)

    def on_add_for_all(self):
        if not self.app.recipes:
            messagebox.showinfo("No recipes", "No recipes loaded.")
            return
        if not self.app.project.molecules:
            messagebox.showinfo("No molecules", "Add at least one molecule first.")
            return
        recipe_name = _ask_choice(self, "Add for all molecules", "Recipe:",
                                  [r.name for r in self.app.recipes])
        if not recipe_name:
            return
        for mol in self.app.project.molecules:
            self.app.project.planned_calcs.append(
                PlannedCalc(id=new_calc_id(), molecule_filename=mol.filename, recipe_name=recipe_name))
        self.app.mark_dirty()
        self.refresh()

    def on_derive(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("No selection",
                                "Select one or more finished calculations to derive from.")
            return
        parents = [self.app.project.calc_by_id(i) for i in sel]
        parents = [p for p in parents if p is not None]
        if not parents:
            return
        # One advisory if any selected parent hasn't finished.
        not_done = [p for p in parents if not self._own_state(p)[2]]
        if not_done:
            n = len(not_done)
            if not messagebox.askyesno(
                "Parent(s) not finished",
                "{} of the {} selected calculation(s) haven't finished successfully yet. A "
                "derived calc uses the parent's optimised geometry, which won't exist until the "
                "parent completes — you can create the derived calcs now but can't build them "
                "until each parent is done.\n\nCreate them anyway?".format(n, len(parents))):
                return
        recipe_name = _ask_choice(
            self, "Derive calculation{}".format("s" if len(parents) > 1 else ""),
            "Recipe for the {} derived calculation(s):".format(len(parents)),
            [r.name for r in self.app.recipes])
        if not recipe_name:
            return
        children = [self._make_child(p, recipe_name) for p in parents]
        self.app.mark_dirty()
        self.refresh()
        keep = [c.id for c in children if self.tree.exists(c.id)]
        if keep:
            self.tree.selection_set(keep)
            self.tree.see(keep[0])
        self.app.set_status("Derived {} calculation(s).".format(len(children)))

    def _make_child(self, parent, recipe_name):
        child = PlannedCalc(
            id=new_calc_id(),
            molecule_filename=parent.molecule_filename,
            recipe_name=recipe_name,
            category=parent.category,
            geometry_source="parent:" + parent.id,
            parent_id=parent.id,
        )
        self.app.project.planned_calcs.append(child)
        return child

    def on_remove(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("No selection", "Select one or more calculations to remove.")
            return
        calcs = [self.app.project.calc_by_id(i) for i in sel]
        calcs = [c for c in calcs if c is not None]
        # Guard: don't remove a running job.
        active = [c for c in calcs if self._own_state(c)[3]]
        if active:
            messagebox.showwarning(
                "Job running",
                "{} of the selected calculations have active jobs. Cancel them in SLURM first "
                "(scancel) before removing.".format(len(active)))
            return
        # Warn about orphaning children.
        sel_ids = set(sel)
        orphans = [c for c in self.app.project.planned_calcs
                   if c.parent_id in sel_ids and c.id not in sel_ids]
        msg = "Remove {} calculation(s)?".format(len(calcs))
        if orphans:
            msg += ("\n\n{} derived calc(s) depend on these and will lose their parent geometry "
                    "link. They will also be removed.".format(len(orphans)))
        if not messagebox.askyesno("Remove", msg):
            return
        remove_ids = sel_ids | {c.id for c in orphans}
        self.app.project.planned_calcs = [c for c in self.app.project.planned_calcs
                                          if c.id not in remove_ids]
        self._selected_id = None
        self.app.mark_dirty()
        self.refresh()
        self.app.set_status("Removed {} calculation(s).".format(len(remove_ids)))

    def on_browse_geom(self):
        path = filedialog.askopenfilename(
            title="Choose geometry file (.xyz or .inp)",
            filetypes=[("XYZ / ORCA inp", "*.xyz *.inp"), ("All files", "*.*")],
            initialdir=self.app.project.root())
        if not path:
            return
        rel = os.path.relpath(path, self.app.project.root()).replace("\\", "/")
        self.geom_mode_var.set("file")
        self.geom_path_var.set(rel)

    # --------------------------------------------------------- copy / paste

    def _copy(self):
        self._clipboard_ids = list(self.tree.selection())
        if self._clipboard_ids:
            self.app.set_status("Copied {} calc(s) — Ctrl+V to derive children from them."
                                .format(len(self._clipboard_ids)))
        return "break"

    def _paste(self):
        if not self._clipboard_ids:
            return "break"
        made = 0
        for pid in self._clipboard_ids:
            parent = self.app.project.calc_by_id(pid)
            if parent is None:
                continue
            # Placeholder recipe = parent's recipe; user then edits it to FREQ/NMR/etc.
            self._make_child(parent, parent.recipe_name)
            made += 1
        if made:
            self.app.mark_dirty()
            self.refresh()
            self.app.set_status("Pasted {} derived calc(s). Edit each one's Recipe to the "
                                "follow-up type.".format(made))
        return "break"

    # ------------------------------------------------------------- build

    def on_build(self):
        self._log_clear()
        try:
            template = slurm_mod.load_template()
        except Exception as e:
            messagebox.showerror("Slurm template missing", str(e))
            return
        targets = self._selected_or_all()
        n_ok = n_skip = 0
        slurm_paths = []  # type: List[str]
        pd = ProgressDialog(self, "Building calculations", total=len(targets)) if targets else None
        for calc in targets:
            if pd is not None:
                pd.step("Building {}".format(self._short(calc)))
                if pd.cancelled:
                    self._log("Build cancelled by user.")
                    break
            mol = self.app.project.molecule_by_filename(calc.molecule_filename)
            recipe = self.app.get_recipe(calc.recipe_name)
            # Guard: never overwrite a live job's input.
            if self._own_state(calc)[3]:
                self._log("SKIP {}: job is active, not rebuilding".format(self._short(calc)))
                n_skip += 1
                continue
            ok, issue = self._validate(calc, mol, recipe)
            if not ok:
                self._log("SKIP {}: {}".format(self._short(calc), issue))
                n_skip += 1
                continue
            try:
                inp_rel, slurm_rel, rundir_rel = self._build_one(calc, mol, recipe, template)
                calc.inp_path = inp_rel
                calc.slurm_path = slurm_rel
                calc.rundir = rundir_rel
                calc.exported = True
                # A rebuild means any old job id no longer matches this input.
                calc.job_id = None
                slurm_paths.append(slurm_rel)
                self._log("BUILT {}  ->  {}".format(self._short(calc), inp_rel))
                n_ok += 1
            except Exception as e:
                self._log("FAIL {}: {}".format(self._short(calc), e))
                n_skip += 1
        if pd is not None:
            pd.close()
        if self.write_submit_var.get() and slurm_paths:
            try:
                self._log("Wrote {}".format(self._write_submit_all(slurm_paths)))
            except Exception as e:
                self._log("submit_all.sh failed: {}".format(e))
        self.app.mark_dirty()
        self.refresh()
        self.app.set_status("Build: {} ok, {} skipped.".format(n_ok, n_skip))

    def _build_one(self, calc, mol, recipe, slurm_template):
        root = self.app.project.root()
        target_dir_rel = "/".join(["calcs", mol.filename, calc.category] + list(recipe.path_parts()))
        target_dir_abs = os.path.join(root, target_dir_rel)
        os.makedirs(target_dir_abs, exist_ok=True)

        atoms = self._resolve_geometry(calc, mol)
        inp_text = inputs_mod.render_inp(recipe, atoms, mol.charge, mol.multiplicity)
        inp_filename = mol.filename + ".inp"
        with open(os.path.join(target_dir_abs, inp_filename), "w", encoding="utf-8") as f:
            f.write(inp_text)

        cores = inputs_mod.parse_cores(inp_text)
        slurm_text = slurm_mod.render_slurm(slurm_template, inp_filename=inp_filename,
                                            rundir=target_dir_rel, jobname=mol.filename,
                                            cores=cores, usermail=self.app.usermail)
        with open(os.path.join(target_dir_abs, mol.filename + ".slurm"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write(slurm_text)

        inp_rel = (target_dir_rel + "/" + inp_filename)
        slurm_rel = (target_dir_rel + "/" + mol.filename + ".slurm")
        return inp_rel, slurm_rel, target_dir_rel

    def _resolve_geometry(self, calc, mol):
        root = self.app.project.root()
        src = calc.geometry_source
        if src == "initial":
            xyz = mol.xyz_path
            if not xyz:
                raise ValueError("molecule has no XYZ")
            if not os.path.isabs(xyz):
                xyz = os.path.join(root, xyz)
            atoms, _ = coords_mod.read_xyz(xyz)
            return atoms
        if src.startswith("parent:"):
            parent = self.app.project.calc_by_id(src[len("parent:"):])
            if parent is None:
                raise ValueError("parent calc not found")
            pgeo = self._parent_geometry_path(parent, mol)
            if not pgeo or not os.path.isfile(pgeo):
                raise ValueError("parent '{}' hasn't produced an optimised geometry yet "
                                 "(it must finish first)".format(self._short(parent)))
            atoms, _ = coords_mod.read_xyz(pgeo)
            return atoms
        if src.startswith("file:"):
            abs_p = os.path.join(root, src[len("file:"):])
            if abs_p.endswith(".xyz"):
                atoms, _ = coords_mod.read_xyz(abs_p)
                return atoms
            if abs_p.endswith(".inp"):
                with open(abs_p, "r", encoding="utf-8") as f:
                    return inputs_mod.extract_atoms_from_inp(f.read())
            raise ValueError("unsupported geometry file extension")
        raise ValueError("unknown geometry source: " + src)

    def _write_submit_all(self, slurm_paths):
        path = os.path.join(self.app.project.root(), "submit_all.sh")
        lines = ["#!/bin/bash",
                 "# Generated by ORCA Studio. Run from the project root.",
                 "set -e", ""]
        lines += ['sbatch "{}"'.format(sp) for sp in slurm_paths]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
        return path

    # ------------------------------------------------------------- submit

    def on_submit(self):
        root = self.app.project.root()
        targets = self._selected_or_all()
        candidates = [c for c in targets if c.exported and c.slurm_path and not c.job_id]
        if not candidates:
            messagebox.showinfo("Nothing to submit",
                                "No built, un-submitted calculations matched. Build first; "
                                "already-submitted jobs aren't re-submitted.")
            return
        if not slurm_runtime.sbatch_available():
            messagebox.showerror("sbatch not found",
                                 "sbatch isn't available here — submit only works on the cluster "
                                 "login node. You can still run submit_all.sh manually.")
            return
        # Advisory: warn about derived calcs whose parent isn't done.
        not_ready = []
        for c in candidates:
            if c.parent_id:
                parent = self.app.project.calc_by_id(c.parent_id)
                if parent is not None and not self._own_state(parent)[2]:
                    not_ready.append(c)
        warn = ""
        if not_ready:
            warn = ("\n\nNote: {} of these derive from a parent that hasn't finished — their "
                    "geometry may be stale or missing.".format(len(not_ready)))
        if not messagebox.askyesno("Submit", "Submit {} job(s) via sbatch?{}".format(len(candidates), warn)):
            return
        n_ok = 0
        pd = ProgressDialog(self, "Submitting jobs", total=len(candidates))
        for calc in candidates:
            pd.step("Submitting {}".format(self._short(calc)))
            if pd.cancelled:
                self._log("Submit cancelled — {} already sent.".format(n_ok))
                break
            job_id, err = slurm_runtime.submit(calc.slurm_path, root)
            if job_id:
                calc.job_id = job_id
                self._log("SUBMITTED {} -> job {}".format(self._short(calc), job_id))
                n_ok += 1
            else:
                self._log("SUBMIT FAILED {}: {}".format(self._short(calc), err))
        pd.close()
        self.app.mark_dirty()
        self.on_refresh_status()
        self.app.set_status("Submitted {}/{} job(s).".format(n_ok, len(candidates)))

    def on_refresh_status(self):
        states = slurm_runtime.query_states()
        self._squeue_states = states
        if states is None and not self._local_mode:
            self._log("squeue not available — using .out files for status.")
        self.refresh()

    # ----------------------------------------------------------- run locally

    def on_run_local(self):
        root = self.app.project.root()
        targets = self._selected_or_all()
        candidates = [c for c in targets if c.exported and c.inp_path and not c.job_id]
        if not candidates:
            messagebox.showinfo(
                "Nothing to run",
                "No built, un-run calculations matched. Build first; calcs that already have a "
                "result aren't re-run (rebuild them to run again).")
            return
        orca = self._ensure_orca_path()
        if not orca:
            return
        # Advisory for derived calcs whose parent hasn't finished.
        not_ready = [c for c in candidates if c.parent_id
                     and self.app.project.calc_by_id(c.parent_id) is not None
                     and not self._own_state(self.app.project.calc_by_id(c.parent_id))[2]]
        warn = ("\n\nNote: {} derive from a parent that hasn't finished — build them after the "
                "parent completes.".format(len(not_ready))) if not_ready else ""
        conc = max(1, int(self.concurrency_var.get()))
        if not messagebox.askyesno(
                "Run locally",
                "Run {} calculation(s) with local ORCA, {} at a time?{}".format(
                    len(candidates), conc, warn)):
            return
        if self._local_runner is None or self._local_runner.orca_exe != orca:
            self._local_runner = local_runner_mod.LocalRunner(orca, max_concurrent=conc)
        else:
            self._local_runner.max_concurrent = conc
        n = 0
        for calc in candidates:
            # Only run buildable ones (skip derived whose geometry isn't ready).
            mol = self.app.project.molecule_by_filename(calc.molecule_filename)
            recipe = self.app.get_recipe(calc.recipe_name)
            ok, issue = self._validate(calc, mol, recipe)
            if not ok:
                self._log("SKIP {}: {}".format(self._short(calc), issue))
                continue
            rundir_abs = os.path.join(root, calc.rundir)
            inp_abs = os.path.join(root, calc.inp_path)
            out_abs = os.path.join(rundir_abs, calc.molecule_filename + "-" + LOCAL_JOB + ".out")
            calc.job_id = LOCAL_JOB
            self._local_runner.forget(calc.id)
            self._local_runner.submit(calc.id, inp_abs, out_abs, rundir_abs)
            self._log("QUEUED {} (local)".format(self._short(calc)))
            n += 1
        self.app.mark_dirty()
        self.refresh()
        self.app.set_status("Local run: {} job(s) queued ({} at a time).".format(n, conc))
        self._start_local_poll()

    def on_stop_local(self):
        if self._local_runner is None or not self._local_runner.busy():
            self.app.set_status("No local run in progress.")
            return
        if not messagebox.askyesno("Stop local run",
                                   "Stop the running job and clear the local queue?"):
            return
        self._local_runner.cancel_all()
        self._local_runner.poll()
        self.refresh()
        self.app.set_status("Local run stopped.")

    def _start_local_poll(self):
        if self._local_poll_id is not None:
            try:
                self.after_cancel(self._local_poll_id)
            except Exception:
                pass
        self._local_poll()

    def _local_poll(self):
        self._local_poll_id = None
        if self._local_runner is None:
            return
        changed = self._local_runner.poll()
        if changed:
            self.refresh()
        if self._local_runner.busy():
            self._local_poll_id = self.after(1500, self._local_poll)
        else:
            self.refresh()
            self.app.set_status("Local run finished.")

    def _ensure_orca_path(self):
        # type: () -> Optional[str]
        """Return a usable ORCA executable, prompting (and remembering) on first
        use. Tries config, then PATH, then asks."""
        import shutil
        path = config_mod.get("orca_path", "")
        if path and os.path.isfile(path):
            return path
        found = shutil.which("orca")
        if found:
            config_mod.set_value("orca_path", found)
            return found
        chosen = filedialog.askopenfilename(
            title="Locate the ORCA executable (orca / orca.exe)",
            parent=self)
        if not chosen:
            return None
        config_mod.set_value("orca_path", chosen)
        return chosen

    # ------------------------------------------------------- double-click plot

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        calc = self.app.project.calc_by_id(row)
        if calc is None:
            return
        if not calc.job_id:
            messagebox.showinfo("Not submitted",
                                "Submit this calculation first, then double-click to watch its "
                                "progress.")
            return
        out_path = self._out_path(calc)
        if not out_path:
            messagebox.showinfo("No output yet",
                                "The job's .out file hasn't appeared yet. Give it a moment after "
                                "submission and try again.")
            return
        LivePlotWindow(self, "{} (job {})".format(self._short(calc), calc.job_id), out_path)

    # --------------------------------------------------- right-click: spectra

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            # Right-clicking an unselected row selects just it (standard behaviour);
            # right-clicking within an existing multi-selection keeps it.
            self.tree.selection_set(row)
        sel = list(self.tree.selection())
        calcs = [self.app.project.calc_by_id(i) for i in sel]
        calcs = [c for c in calcs if c is not None]
        menu = tk.Menu(self, tearoff=0)

        finished_freq = [c for c in calcs if self._is_finished_type(c, "FREQ")]
        finished_nmr = [c for c in calcs if self._is_finished_type(c, "NMR")]

        if len(finished_freq) == 1:
            menu.add_command(label="Plot IR spectrum",
                             command=lambda c=finished_freq[0]: self._plot_ir(c))
        else:
            menu.add_command(label="Plot IR spectrum  (select one finished FREQ)",
                             state=tk.DISABLED)
        if finished_nmr:
            menu.add_command(label="Plot NMR spectrum ({} selected)".format(len(finished_nmr)),
                             command=lambda cs=list(finished_nmr): self._plot_nmr(cs))
        else:
            menu.add_command(label="Plot NMR spectrum  (select finished NMR)", state=tk.DISABLED)

        if len(calcs) == 1 and calcs[0].job_id:
            menu.add_separator()
            menu.add_command(label="Open live progress plot",
                             command=lambda c=calcs[0]: self._open_live(c))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _is_finished_type(self, calc, calctype):
        recipe = self.app.get_recipe(calc.recipe_name)
        if not recipe or recipe.calctype.upper() != calctype:
            return False
        return self._own_state(calc)[2]  # done

    def _open_live(self, calc):
        op = self._out_path(calc)
        if op:
            LivePlotWindow(self, "{} (job {})".format(self._short(calc), calc.job_id), op)

    def _plot_ir(self, calc):
        text = self._read_out(calc)
        if text is None:
            messagebox.showinfo("No output", "Could not read this calculation's .out file.")
            return
        freqs = orca_parser.parse_frequencies(text)
        ir = orca_parser.parse_ir(text)
        if not ir:
            messagebox.showinfo("No IR data",
                                "No IR spectrum found in the output — is this a Freq run?")
            return
        centers = [row["freq_cm"] for row in ir]
        intens = [row["intensity_km_mol"] for row in ir]
        from orca_studio.ui.spectra import IRSpectrumWindow
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        smiles = mol.smiles if mol else None
        # Pass the full frequency list too, so the window can report imaginary modes.
        IRSpectrumWindow(self, self._short(calc), centers, intens, freqs=freqs, smiles=smiles)

    def _plot_nmr(self, calcs):
        from orca_studio.ui.spectra import NMROptionsDialog, NMRSpectrumWindow
        # Gather shieldings per calc, and the set of available elements.
        per_calc = []
        elements = []
        for c in calcs:
            text = self._read_out(c)
            if text is None:
                continue
            sh = orca_parser.parse_nmr_shieldings(text)
            if not sh:
                continue
            mol = self.app.project.molecule_by_filename(c.molecule_filename)
            per_calc.append({"calc": c, "mol": mol, "shieldings": sh})
            for row in sh:
                if row["element"] not in elements:
                    elements.append(row["element"])
        if not per_calc:
            messagebox.showinfo("No NMR data",
                                "No chemical shieldings found in the selected calculations.")
            return
        dlg = NMROptionsDialog(self, elements)
        if not dlg.result:
            return
        element, nucleus_label, reference = dlg.result
        entries = []
        for pc in per_calc:
            sig = [row["isotropic_ppm"] for row in pc["shieldings"] if row["element"] == element]
            if not sig:
                continue
            mol = pc["mol"]
            entries.append({
                "name": "{} / {}".format(pc["calc"].molecule_filename, pc["calc"].recipe_name),
                "smiles": mol.smiles if mol else None,
                "shieldings": sig,
            })
        if not entries:
            messagebox.showinfo("No data for nucleus",
                                "None of the selected calculations has {} shieldings.".format(nucleus_label))
            return
        NMRSpectrumWindow(self, entries, nucleus_label, reference)

    def _read_out(self, calc):
        op = self._out_path(calc)
        if not op or not os.path.isfile(op):
            return None
        try:
            with open(op, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except IOError:
            return None

    # --------------------------------------------------------------- misc

    def _selected_or_all(self):
        sel = list(self.tree.selection())
        if sel:
            out = [self.app.project.calc_by_id(i) for i in sel]
            return [c for c in out if c is not None]
        return list(self.app.project.planned_calcs)

    def on_open_folder(self):
        root = self.app.project.root()
        if not os.path.isdir(root):
            messagebox.showerror("No folder", "Project root does not exist: " + root)
            return
        try:
            if platform.system() == "Windows":
                os.startfile(root)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", root])
            else:
                subprocess.Popen(["xdg-open", root])
        except Exception as e:
            messagebox.showerror("Open failed", str(e))

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


def _ask_choice(parent, title, prompt, choices):
    # type: (tk.Widget, str, str, list) -> Optional[str]
    """Small modal dialog with a single combobox + OK/Cancel."""
    top = tk.Toplevel(parent)
    top.title(title)
    ttk.Label(top, text=prompt).pack(padx=12, pady=(12, 4))
    var = tk.StringVar(value=choices[0] if choices else "")
    cb = ttk.Combobox(top, textvariable=var, values=choices, state="readonly", width=40)
    cb.pack(padx=12, pady=4)
    result = {"value": None}

    def ok():
        result["value"] = var.get()
        top.destroy()

    def cancel():
        top.destroy()

    btns = ttk.Frame(top)
    btns.pack(pady=8)
    ttk.Button(btns, text="OK", command=ok).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="Cancel", command=cancel).pack(side=tk.LEFT, padx=4)
    make_modal(top, parent)
    top.wait_window()
    return result["value"]
