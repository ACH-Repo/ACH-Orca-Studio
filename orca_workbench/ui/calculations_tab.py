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
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import List, Optional, Tuple

from orca_workbench.core import config as config_mod
from orca_workbench.core import coords as coords_mod
from orca_workbench.core import discovery as discovery_mod
from orca_workbench.core import geomspec as geomspec_mod
from orca_workbench.core import inputs as inputs_mod
from orca_workbench.ui.shortcuts import install_tree_shift_select
from orca_workbench.core import local_runner as local_runner_mod
from orca_workbench.core import orca_parser
from orca_workbench.core import orca_plot as orca_plot_mod
from orca_workbench.core import provenance as provenance_mod
from orca_workbench.core import slurm as slurm_mod
from orca_workbench.core import slurm_runtime
from orca_workbench.core import workflow as wf_mod
from orca_workbench.core.project import Molecule, PlannedCalc, new_calc_id
from orca_workbench.ui.modal import make_modal
from orca_workbench.ui.plot_window import LivePlotWindow
from orca_workbench.ui.progress import ProgressDialog
from orca_workbench.ui.tooltip import tip


# Sentinel job id for jobs run by the local runner (vs a numeric SLURM id).
LOCAL_JOB = "local"

# States a calc can't move on from on its own (the pipeline driver stops on them).
_TERMINAL_TAGS = ("done", "error", "skipped", "interrupted")


# Row background by lifecycle state.
_TAGS = {
    "notbuilt": "#fffde7",     # pale yellow — planned / has issue
    "waiting": "#eeeeee",      # grey — derived, waiting on parent / condition
    "built": "",               # white — built, not submitted
    "running": "#e3f2fd",      # pale blue — queued / running
    "done": "#e8f5e9",         # pale green — terminated normally
    "error": "#ffebee",        # pale red — error in output
    "skipped": "#ede7f6",      # pale purple — condition not met, won't run
    "interrupted": "#ffe0b2",  # pale orange — stopped before finishing
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
        # Live pipeline driver (Workflow tab "Run pipeline"): calc ids under
        # automatic build→run→gate-evaluate control, plus its polling handle.
        self._pipeline_ids = set()     # type: set
        self._pipeline_poll_id = None
        self._pipeline_reports = []    # type: list
        # Interrupt button (cluster mode): whether we've queried squeue since the
        # last submit/open. False => the button is "disarmed" and a press refreshes
        # status first; True => it reflects real active-job state.
        self._status_known = False
        self._btn_interrupt = None     # type: Optional[tk.Button]
        self._build()

    def reconcile_after_load(self):
        """Called after a project is opened: query job status so calcs that were
        'running' when the app last closed are re-evaluated (a local job, or a
        cluster job no longer in the queue, shows as 'interrupted' rather than
        forever 'running')."""
        self._parse_cache.clear()
        self.on_refresh_status()
        self._maybe_prompt_detect()

    def _maybe_prompt_detect(self):
        """If the freshly-opened project has calcs that were clearly submitted
        outside the app (output files on disk but no job_id), offer to link them
        — so a `submit_all.sh` run doesn't leave them invisible to monitoring."""
        try:
            pending = discovery_mod.unlinked_with_output(self.app.project)
        except Exception:
            return
        if not pending:
            return
        if messagebox.askyesno(
                "Detect submitted jobs?",
                "{} calculation(s) look submitted (output files are on disk) but "
                "aren't linked to the app, so their status and results won't "
                "show.\n\nDetect and link them now?".format(len(pending))):
            self.on_detect_jobs()

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
        b_derive = ttk.Button(bar, text="Derive ->", command=self.on_derive)
        b_remove = ttk.Button(bar, text="Remove", command=self.on_remove)
        for b in (b_add, b_derive, b_remove):
            b.pack(side=tk.LEFT, padx=2)
        b_deconstruct = tk.Button(bar, text="DECONSTRUCT", command=self.on_deconstruct,
                                  bg="#c0392b", activebackground="#a93226",
                                  fg="white", activeforeground="white")
        b_deconstruct.pack(side=tk.LEFT, padx=2)
        tip(b_deconstruct, "DANGER: remove the selected calc(s) AND erase their run directories "
                           "from disk (.inp/.slurm/.out and all results). Unlike Remove — which "
                           "only drops them from the project and leaves the files — this is "
                           "permanent and cannot be undone. Refuses calcs with a running job.")
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)
        b_build = tk.Button(bar, text="Build", command=self.on_build,
                            font=("TkDefaultFont", 10, "bold"))
        b_build.pack(side=tk.LEFT, padx=2)

        # Launch action: own place, far right, signal (amber) colour. The label
        # and behaviour adapt to the environment — sbatch present → cluster
        # Submit; absent (a PC) → run ORCA locally through a serial queue.
        if self._local_mode:
            b_submit = tk.Button(bar, text="Run locally >", command=self.on_run_local,
                                 font=("TkDefaultFont", 10, "bold"),
                                 bg="#e0a35a", activebackground="#e8b673", fg="#222222")
            b_submit.pack(side=tk.RIGHT, padx=(6, 0))
            b_stop = tk.Button(bar, text="Stop", command=self.on_stop_local)
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
            b_submit = tk.Button(bar, text="Submit >", command=self.on_submit,
                                 font=("TkDefaultFont", 10, "bold"),
                                 bg="#e0a35a", activebackground="#e8b673", fg="#222222")
            b_submit.pack(side=tk.RIGHT, padx=(6, 0))
            tip(b_submit, "sbatch the selected built calcs (or all built-and-unsubmitted). "
                          "Cluster login node only. Warns before submitting a derived calc whose "
                          "parent hasn't finished. Shows a progress bar.")
            b_unatt = tk.Button(bar, text=">> Unattended", command=self.on_submit_unattended,
                                bg="#cdebc5", activebackground="#bfe2b6")
            b_unatt.pack(side=tk.RIGHT, padx=(6, 0))
            tip(b_unatt, "Submit the selected calcs (or all unfinished) as a SLURM dependency "
                         "chain — each derived calc is held until its parent geometry job "
                         "finishes, so you can queue a whole OPT->NMR set at once and then close "
                         "the app and MobaXterm. Same engine as the Workflow tab's Submit "
                         "unattended; needs no Workflow graph.")
        # Monitoring: distinct, bigger/bolder, light shade, just left of the launch action.
        b_status = tk.Button(bar, text="Refresh (F5)", command=self.on_refresh_status,
                             font=("TkDefaultFont", 11, "bold"),
                             bg="#d3e6f5", activebackground="#c3dcf0")
        b_status.pack(side=tk.RIGHT, padx=2)
        b_detect = ttk.Button(bar, text="Detect jobs", command=self.on_detect_jobs)
        b_detect.pack(side=tk.RIGHT, padx=2)
        tip(b_detect, "Reconnect calcs that were submitted outside the app (e.g. via "
                      "submit_all.sh): recover each job's SLURM id from its output files "
                      "and from squeue, so status and result-harvest work again. Safe to "
                      "run repeatedly.")

        # Interrupt (cluster only; local mode already has its own Stop button). A
        # two-stage cancel that appears once calcs have been submitted and floats in
        # the gap between Build and the right-hand cluster. _update_interrupt_button()
        # shows/hides, colours and arms it.
        if not self._local_mode:
            self._btn_interrupt = tk.Button(bar, text="Interrupt", command=self.on_interrupt,
                                            font=("TkDefaultFont", 10, "bold"))
            tip(self._btn_interrupt,
                "Stop running/queued calculations — e.g. if you spotted a setup mistake. "
                "First press refreshes status; if jobs are still active the button turns "
                "red, and a second press cancels them (scancel) after a confirmation. "
                "Only appears once calcs have been submitted.")
            self._update_interrupt_button()

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
        b_import = ttk.Button(bar2, text="Import calcs...", command=self.on_import_calcs)
        b_import.pack(side=tk.LEFT, padx=2)
        tip(b_import, "Pick a directory of existing ORCA .inp files (with whatever .out/.engrad "
                      "sit beside them) and bring them in as molecules + calculations — for "
                      "monitoring and result extraction. A recipe is reconstructed from each "
                      ".inp so the report tab's extractors fire. Skips .inp already in the project.")
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
        self._col_labels = {
            "type": "Type", "molecule": "Molecule", "recipe": "Recipe",
            "parent": "Parent", "state": "State", "job": "Job", "path": "Target dir",
        }
        for col, width, anchor in [
            ("type", 55, tk.W), ("molecule", 90, tk.W), ("recipe", 150, tk.W),
            ("parent", 90, tk.W), ("state", 180, tk.W), ("job", 70, tk.W),
            ("path", 240, tk.W),
        ]:
            self.tree.heading(col, text=self._col_labels[col],
                              command=lambda c=col: self._on_header_click(c))
            self.tree.column(col, width=width, anchor=anchor)
        # Sort state: None = project order. Within any sort, finished calcs rank
        # to the top of each group and unbuilt/interrupted ones to the bottom.
        self._sort_col = None
        self._sort_desc = False
        # Pack the scrollbar BEFORE the tree: this table is wider than its pane, so
        # an expand=True tree packed first would consume the whole cavity and leave
        # the scrollbar zero width (scrollable by wheel, but no draggable bar).
        sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for tag, color in _TAGS.items():
            self.tree.tag_configure(tag, background=color)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        install_tree_shift_select(self.tree)
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
        _logsb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=_logsb.set)
        _logsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

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
        _infosb = ttk.Scrollbar(info, orient=tk.VERTICAL, command=self.info_text.yview)
        self.info_text.configure(yscrollcommand=_infosb.set)
        _infosb.pack(side=tk.RIGHT, fill=tk.Y)
        self.info_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._editor_widgets = [self.mol_combo, self.recipe_combo, self.cat_entry,
                                self.rb_init, self.rb_parent, self.rb_file,
                                self.geom_entry, self.geom_browse]

    # ------------------------------------------------------------- refresh

    def refresh(self):
        self.mol_combo["values"] = [m.filename for m in self.app.project.molecules]
        self.recipe_combo["values"] = [r.name for r in self.app.recipes]
        # Update rows *in place* rather than delete+reinsert: a full rebuild every
        # poll tick would wipe the current selection and the shift-click anchor,
        # so range-selecting while a pipeline runs would keep breaking.
        want_ids = [c.id for c in self.app.project.planned_calcs]
        want_set = set(want_ids)
        for iid in self.tree.get_children(""):
            if iid not in want_set:
                self.tree.delete(iid)
        for idx, c in enumerate(self.app.project.planned_calcs):
            values = self._row_values(c)
            tag = self._row_tag(c)
            if self.tree.exists(c.id):
                self.tree.item(c.id, values=values, tags=(tag,))
                if self.tree.index(c.id) != idx:
                    self.tree.move(c.id, "", idx)
            else:
                self.tree.insert("", idx, iid=c.id, values=values, tags=(tag,))
        self._apply_sort()   # reorder the display if a column sort is active
        # In-place updates preserve the selection automatically; only refresh the
        # editor when nothing is selected.
        if not self.tree.selection():
            if self._selected_id and self.tree.exists(self._selected_id):
                self.tree.selection_set(self._selected_id)
            else:
                self._clear_editor()
        self._update_interrupt_button()

    # -------------------------------------------------------------- sorting

    # Lower rank = nearer the top of its group: finished first, then in-progress,
    # then not-yet-run, with interrupted/error/skipped pushed to the bottom.
    _SORT_RANK = {"done": 0, "running": 1, "waiting": 2, "built": 2,
                  "notbuilt": 3, "skipped": 4, "interrupted": 5, "error": 6}

    def _completion_rank(self, calc):
        return self._SORT_RANK.get(self._display_state(calc)[1], 9)

    def _on_header_click(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self._refresh_heading_arrows()
        self._apply_sort()

    def _refresh_heading_arrows(self):
        for col, label in self._col_labels.items():
            arrow = ""
            if col == self._sort_col:
                arrow = " ▼" if self._sort_desc else " ▲"
            self.tree.heading(col, text=label + arrow)

    def _apply_sort(self):
        if not self._sort_col:
            return  # project order (already applied in place by refresh)
        cols = ("type", "molecule", "recipe", "parent", "state", "job", "path")
        ci = cols.index(self._sort_col)

        def primary(calc):
            if self._sort_col == "state":
                return self._completion_rank(calc)
            return str(self._row_values(calc)[ci]).lower()

        calcs = list(self.app.project.planned_calcs)
        # Stable two-pass: completion rank first (always finished-on-top within a
        # group), then the chosen column — so reversing the column doesn't flip
        # the finished-first secondary order.
        calcs.sort(key=self._completion_rank)
        calcs.sort(key=primary, reverse=self._sort_desc)
        for idx, c in enumerate(calcs):
            if self.tree.exists(c.id):
                self.tree.move(c.id, "", idx)

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
        # Local-run jobs: only THIS app's runner can have one running. If the
        # runner isn't tracking it as live, it can't still be running (local jobs
        # die with the app) — so an incomplete .out means it was interrupted.
        if calc.job_id == LOCAL_JOB:
            if self._local_runner is not None:
                st = self._local_runner.state(calc.id)
                if st == local_runner_mod.QUEUED:
                    return ("local: queued", "running", False, True)
                if st == local_runner_mod.RUNNING:
                    return ("local: running", "running", False, True)
                if st == local_runner_mod.CANCELLED:
                    return ("local: cancelled", "interrupted", False, False)
            parsed = self._parse_out(calc)
            if parsed is None:
                return ("interrupted — no output (local run was lost)", "interrupted", False, False)
            if parsed.get("has_error"):
                return ("error in output", "error", False, False)
            if parsed.get("terminated_normally"):
                return ("done — terminated normally", "done", True, False)
            return ("interrupted — local run stopped before finishing",
                    "interrupted", False, False)
        # Cluster job (numeric id). squeue is the source of truth for "running".
        sq = self._squeue_states
        if sq and calc.job_id in sq:
            return ("queue: {}".format(sq[calc.job_id]), "running", False, True)
        parsed = self._parse_out(calc)
        if parsed is not None:
            if parsed.get("has_error"):
                return ("error in output", "error", False, False)
            if parsed.get("terminated_normally"):
                return ("done — terminated normally", "done", True, False)
            # .out exists but incomplete. If we've actually queried squeue and the
            # job isn't there, it left the queue without finishing = interrupted.
            if sq is not None:
                return ("interrupted — job left the queue before finishing",
                        "interrupted", False, False)
            return ("running — {}".format(orca_parser.short_status(parsed)), "running", False, True)
        # No .out. If squeue was queried and the job is gone, it never produced
        # output = interrupted; otherwise it's freshly submitted.
        if sq is not None:
            return ("interrupted — job not in queue, no output", "interrupted", False, False)
        return ("submitted (job {})".format(calc.job_id), "running", False, True)

    def _gate_status(self, calc):
        # type: (PlannedCalc) -> str
        """Conditional gate state: 'none' (no gate) | 'pending' | 'open' | 'closed'.

        A gate ({source: calc_id, predicate: name}) comes from a Workflow
        Condition node: this calc runs only once the source calc finishes and
        the predicate holds on its output.
        """
        gate = getattr(calc, "gate", None)
        if not gate:
            return "none"
        source = self.app.project.calc_by_id(gate.get("source"))
        if source is None:
            return "open"  # source vanished — don't trap the calc forever
        _, src_tag, sdone, sactive = self._own_state(source)
        if sdone:
            return wf_mod.gate_outcome(gate.get("predicate", "terminated_ok"), True,
                                       self._read_out(source))
        # The source failed/was interrupted and isn't coming back → the condition
        # can never be satisfied, so the gate is permanently closed.
        if not sactive and src_tag in ("error", "interrupted"):
            return "closed"
        return "pending"

    def _gate_label(self, calc):
        gate = getattr(calc, "gate", None)
        if not gate:
            return ""
        source = self.app.project.calc_by_id(gate.get("source"))
        pred = wf_mod.PREDICATES.get(gate.get("predicate", ""), gate.get("predicate", "condition"))
        return "{} → {}".format(self._short(source) if source else "?", pred)

    def _display_state(self, calc):
        # type: (PlannedCalc) -> Tuple[str, str]
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        recipe = self.app.get_recipe(calc.recipe_name)
        ok, issue = self._validate(calc, mol, recipe)
        # Conditional gate takes precedence while the calc hasn't run: a closed
        # gate means it never will; a pending one means it's waiting on a sibling.
        if not calc.job_id:
            gst = self._gate_status(calc)
            if gst == "closed":
                return ("skipped — condition not met", "skipped")
            if gst == "pending":
                gate = calc.gate or {}
                src = self.app.project.calc_by_id(gate.get("source"))
                return ("waiting for condition ({})".format(self._short(src) if src else "?"),
                        "waiting")
        if not calc.exported:
            if calc.parent_id:
                parent = self.app.project.calc_by_id(calc.parent_id)
                if parent is not None:
                    _, ptag, pdone, pactive = self._own_state(parent)
                    if not pdone:
                        if not pactive and ptag in ("error", "interrupted"):
                            return ("skipped — upstream didn't finish", "skipped")
                        return ("waiting for parent ({})".format(self._short(parent)), "waiting")
            if not ok:
                return (issue, "notbuilt")
            return ("ready to build", "notbuilt")
        label, tag, _done, _active = self._own_state(calc)
        return (label, tag)

    # Status only needs the end of the file (termination/error markers, the last
    # SCF block, the latest opt cycle), so we parse just the tail of big outputs.
    # An ORCA opt of a large molecule can be tens of MB; reading the whole thing
    # for every row was the main reason opening a 40-calc project took minutes.
    _STATUS_TAIL_BYTES = 256 * 1024

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
            with open(path, "rb") as f:
                if sig[1] > self._STATUS_TAIL_BYTES:
                    f.seek(-self._STATUS_TAIL_BYTES, os.SEEK_END)
                data = f.read()
            parsed = orca_parser.parse_orca_output(data.decode("utf-8", errors="replace"))
        except (IOError, OSError):
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
        # Sanitise every component: a space (or other shell/SLURM-hostile char)
        # in the rundir breaks the SLURM script (the #SBATCH --output directive
        # and the cd/cp lines). Already-safe labels are unchanged.
        parts = [mol.filename, calc.category] + list(recipe.path_parts())
        return "/".join(["calcs"] + [inputs_mod.safe_path_component(p) for p in parts])

    def _target_key(self, calc):
        """A key for the directory a calc builds into: two calcs with the same
        key would write to (and clobber) the same folder — i.e. they are the
        same calculation."""
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        recipe = self.app.get_recipe(calc.recipe_name)
        d = self._target_dir(calc, mol, recipe)
        if d == "(unresolved)":
            return ("u", calc.molecule_filename, calc.category, calc.recipe_name)
        return d

    def _pick_keeper(self, members):
        """Of several calcs that build into the same dir, keep the most
        progressed one (a finished result beats a planned stub)."""
        def rank(c):
            _, _, done, active = self._own_state(c)
            if done:
                return 5
            if active:
                return 4
            if c.job_id:
                return 3
            if c.exported:
                return 2
            if getattr(c, "origin_node", None):
                return 1
            return 0
        return max(members, key=rank)

    def dedupe_by_target(self):
        """Collapse planned calcs that build into the same directory into one
        row (keeping the most-progressed), rewiring any parent_id / gate.source
        references onto the survivor. Returns how many rows were removed."""
        groups = {}
        for c in self.app.project.planned_calcs:
            groups.setdefault(self._target_key(c), []).append(c)
        remap = {}   # removed id -> survivor id
        for members in groups.values():
            if len(members) < 2:
                continue
            keeper = self._pick_keeper(members)
            for c in members:
                if c.id != keeper.id:
                    remap[c.id] = keeper.id
        if not remap:
            return 0
        for c in self.app.project.planned_calcs:
            if c.parent_id in remap:
                c.parent_id = remap[c.parent_id]
            if c.gate and c.gate.get("source") in remap:
                c.gate["source"] = remap[c.gate["source"]]
        self.app.project.planned_calcs = [c for c in self.app.project.planned_calcs
                                          if c.id not in remap]
        self._pipeline_ids = {remap.get(i, i) for i in self._pipeline_ids}
        self.app.mark_dirty()
        return len(remap)

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
        read_orb = messagebox.askyesno(
            "Restart from orbitals?",
            "Restart the derived calculation(s) from each parent's converged orbitals "
            "(MOREAD)?\n\nThis reuses the parent's .gbw as the SCF guess — much faster "
            "convergence — and is ideal for getting an extra property (NMR / EPR / "
            "polarisability / …) from an already-converged job at the same geometry.\n\n"
            "Requirement: the derived recipe must use the SAME basis set as the parent. "
            "Choose No for a normal fresh-SCF derived calc.")
        children = [self._make_child(p, recipe_name, read_orbitals=read_orb) for p in parents]
        self.app.mark_dirty()
        self.refresh()
        keep = [c.id for c in children if self.tree.exists(c.id)]
        if keep:
            self.tree.selection_set(keep)
            self.tree.see(keep[0])
        self.app.set_status("Derived {} calculation(s).".format(len(children)))

    def _make_child(self, parent, recipe_name, read_orbitals=False):
        child = PlannedCalc(
            id=new_calc_id(),
            molecule_filename=parent.molecule_filename,
            recipe_name=recipe_name,
            category=parent.category,
            geometry_source="parent:" + parent.id,
            parent_id=parent.id,
            orbital_source=("parent:" + parent.id) if read_orbitals else None,
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

    def on_deconstruct(self):
        """Remove the selected calc(s) from the project AND delete their run
        directories from disk. The destructive counterpart to Remove — for
        clearing out failed/abandoned calcs (and their .inp/.slurm/.out) entirely."""
        import shutil
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("No selection", "Select one or more calculations to deconstruct.")
            return
        calcs = [c for c in (self.app.project.calc_by_id(i) for i in sel) if c is not None]
        active = [c for c in calcs if self._own_state(c)[3]]
        if active:
            messagebox.showwarning(
                "Job running",
                "{} of the selected calculations have active jobs. Cancel them in SLURM "
                "(scancel) before deconstructing.".format(len(active)))
            return
        sel_ids = set(sel)
        orphans = [c for c in self.app.project.planned_calcs
                   if c.parent_id in sel_ids and c.id not in sel_ids]
        remove = calcs + orphans
        root_abs = os.path.abspath(self.app.project.root())
        # Resolve on-disk rundirs to delete — but ONLY directories strictly inside
        # the project root (never the root itself, an absolute path, or a `..`
        # escape). This is irreversible, so the path guard matters.
        to_delete = []
        for c in remove:
            if not c.rundir:
                continue
            d = os.path.abspath(os.path.join(root_abs, c.rundir))
            if d == root_abs or not d.startswith(root_abs + os.sep):
                continue
            if os.path.isdir(d):
                to_delete.append((c, d))
        msg = ("Permanently DELETE {} run director{} from disk (their .inp/.slurm/.out "
               "and all results) and remove {} calculation(s) from the project?\n\n"
               "This cannot be undone.".format(
                   len(to_delete), "y" if len(to_delete) == 1 else "ies", len(remove)))
        if orphans:
            msg += "\n\n({} derived calc(s) depend on the selection and are included.)".format(
                len(orphans))
        if not messagebox.askyesno("Deconstruct", msg, icon="warning"):
            return
        n_dirs = 0
        for c, d in to_delete:
            try:
                shutil.rmtree(d)
                n_dirs += 1
                self._log("DELETED dir {}".format(c.rundir))
                self._prune_empty_parents(d, root_abs)
            except OSError as e:
                self._log("DELETE FAILED {}: {}".format(c.rundir, e))
        remove_ids = sel_ids | {c.id for c in orphans}
        self.app.project.planned_calcs = [c for c in self.app.project.planned_calcs
                                          if c.id not in remove_ids]
        self._selected_id = None
        self.app.mark_dirty()
        self.refresh()
        self.app.set_status(
            "Deconstructed {} calc(s); deleted {} run dir(s) from disk.".format(
                len(remove_ids), n_dirs))

    def _prune_empty_parents(self, start_dir, root_abs):
        """After deleting a calc's run dir, remove any parent dirs that are now
        empty, walking up toward the project root — so deconstructing a molecule's
        only calc doesn't leave empty scaffolding (calcs/<mol>/<category>/OPT/ ->
        ... -> calcs/<mol>/) behind. os.rmdir removes a dir only when it's empty,
        so the walk stops at the first ancestor that still has siblings; the
        project root itself is never removed."""
        parent = os.path.dirname(os.path.abspath(start_dir))
        while parent != root_abs and parent.startswith(root_abs + os.sep):
            try:
                os.rmdir(parent)   # succeeds only if empty -> natural stop on siblings
            except OSError:
                break
            self._log("pruned empty dir {}".format(
                os.path.relpath(parent, root_abs).replace("\\", "/")))
            parent = os.path.dirname(parent)

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
            # Guard: conditional gate from a workflow Condition node.
            gst = self._gate_status(calc)
            if gst == "closed":
                self._log("SKIP {}: condition not met — won't run".format(self._short(calc)))
                n_skip += 1
                continue
            if gst == "pending":
                self._log("SKIP {}: waiting on condition ({})".format(
                    self._short(calc), self._gate_label(calc)))
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

    def _build_one(self, calc, mol, recipe, slurm_template, xyz_ref=None, preamble=""):
        root = self.app.project.root()
        target_dir_rel = self._target_dir(calc, mol, recipe)  # sanitised components
        target_dir_abs = os.path.join(root, target_dir_rel)
        os.makedirs(target_dir_abs, exist_ok=True)

        if xyz_ref:
            # Unattended chain: read the geometry from a file at run time (the
            # parent writes it before this job starts), rather than embedding it.
            inp_text = inputs_mod.render_inp_xyzfile(recipe, xyz_ref, mol.charge, mol.multiplicity)
        else:
            atoms = self._resolve_geometry(calc, mol)
            inp_text = inputs_mod.render_inp(recipe, atoms, mol.charge, mol.multiplicity)
        # Restart from a parent's converged orbitals (MOREAD), if requested. The .gbw
        # is referenced by absolute path on the shared FS so it's read at run time
        # (the parent finishes first via the dependency on its geometry).
        osrc = getattr(calc, "orbital_source", None)
        if osrc and osrc.startswith("parent:"):
            par = self.app.project.calc_by_id(osrc[len("parent:"):])
            if par is not None and par.rundir:
                gbw_abs = os.path.join(root, par.rundir, par.molecule_filename + ".gbw")
                inp_text = inputs_mod.add_moread(inp_text, gbw_abs)
            else:
                self._log("MOREAD skipped for {}: orbital parent not built yet (no rundir)."
                          .format(self._short(calc)))
        # Geometry constraints / a relaxed surface scan (%geom) for an OPT job, if the
        # calc carries a spec. Injected as a %geom block; the recipe supplies `! Opt`.
        gspec = getattr(calc, "geom_spec", None)
        if not geomspec_mod.is_empty(gspec):
            inp_text = inputs_mod.add_geom_block(inp_text, geomspec_mod.build_geom_inner(gspec))
        # Global hardware defaults (Settings > Default cores / memory per job): if set,
        # override the recipe's %pal nprocs / %maxcore so a user changes their PC specs
        # in ONE place instead of editing every recipe. 0 = leave the recipe's own. The
        # SLURM core count is derived from the input below, so it stays in sync.
        gcores = int(config_mod.get("default_cores", 0) or 0)
        if gcores > 0:
            inp_text = inputs_mod.set_cores(inp_text, gcores)
        gmax = int(config_mod.get("default_maxcore_mb", 0) or 0)
        if gmax > 0:
            inp_text = inputs_mod.set_maxcore(inp_text, gmax)
        if gcores > 0 or gmax > 0:
            # Log the override so the global default isn't a silent, hidden change.
            self._log("Global default applied to {}:{}{}.".format(
                self._short(calc),
                " {} cores".format(gcores) if gcores > 0 else "",
                " {} MB/core".format(gmax) if gmax > 0 else ""))
        # When running on this machine (not a cluster), don't let a recipe ask for
        # more cores than the CPU has — otherwise a first local job over-subscribes
        # and crawls. Clamp %pal nprocs to the detected core count.
        if self._local_mode:
            avail = inputs_mod.detect_cores()
            want = inputs_mod.parse_cores(inp_text)
            if want > avail:
                inp_text = inputs_mod.set_cores(inp_text, avail)
                self._log("Capped {} to {} core(s) (this machine has {}).".format(
                    self._short(calc), avail, avail))
        # Stamp a provenance header so this .inp can be re-associated with its
        # molecule/recipe if the project save file is ever lost (see core/discovery).
        inp_text = provenance_mod.format_block({
            "molecule": mol.filename,
            "name": mol.name,
            "smiles": mol.smiles,
            "gen_smiles": mol.gen_smiles,
            "charge": mol.charge,
            "mult": mol.multiplicity,
            "recipe": recipe.name,
            "calctype": recipe.calctype,
            "method": recipe.method_label,
            "variant": recipe.variant,
            "category": calc.category,
            "geometry_source": calc.geometry_source,
            "orbital_source": getattr(calc, "orbital_source", None),
            "initial_xyz": mol.xyz_path,
            "origin_node": calc.origin_node,
        }) + inp_text
        inp_filename = mol.filename + ".inp"
        with open(os.path.join(target_dir_abs, inp_filename), "w", encoding="utf-8") as f:
            f.write(inp_text)

        cores = inputs_mod.parse_cores(inp_text)
        slurm_text = slurm_mod.render_slurm(slurm_template, inp_filename=inp_filename,
                                            rundir=target_dir_rel, jobname=mol.filename,
                                            cores=cores, usermail=self.app.usermail,
                                            preamble=preamble)
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
                 "# Generated by ORCA Workbench. Run from the project root.",
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

    def _submit_delay_s(self):
        """Seconds to pause between sbatch calls. Submitting many jobs back-to-back
        can trip the controller's submission rate limit and silently drop some, so
        we throttle by default (config 'submit_delay_ms', 100 ms; 0 disables)."""
        try:
            ms = int(config_mod.get("submit_delay_ms", 100))
        except (TypeError, ValueError):
            ms = 100
        return max(0, ms) / 1000.0

    def on_submit(self):
        self._status_known = False   # new jobs go out; state is unknown until re-queried
        root = self.app.project.root()
        targets = self._selected_or_all()
        candidates = [c for c in targets if c.exported and c.slurm_path and not c.job_id
                      and self._gate_status(c) != "closed"]
        if not candidates:
            messagebox.showinfo("Nothing to submit",
                                "No built, un-submitted calculations matched. Build first; "
                                "already-submitted jobs aren't re-submitted. Calcs whose "
                                "workflow condition failed are skipped.")
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
        delay = self._submit_delay_s()
        pd = ProgressDialog(self, "Submitting jobs", total=len(candidates))
        for i, calc in enumerate(candidates):
            if i and delay:
                time.sleep(delay)        # throttle: stay under the submission rate limit
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
        self._status_known = True   # we've now queried the queue this session
        if states is None and not self._local_mode:
            self._log("squeue not available — using .out files for status.")
        self.refresh()

    # ---- Interrupt: two-stage cancel of running/queued cluster jobs -----------
    def _active_submitted_calcs(self):
        """Cluster calcs currently queued/running, per our latest status query."""
        return [c for c in self.app.project.planned_calcs
                if c.job_id and c.job_id != LOCAL_JOB and self._own_state(c)[3]]

    def _update_interrupt_button(self):
        """Show/hide, colour and arm the Interrupt button. Hidden until calcs are
        submitted; 'disarmed' (muted) until we've queried the queue this session;
        'armed' (red) when jobs are actually active. Floats centred in the gap
        between Build and the right-hand cluster."""
        btn = self._btn_interrupt
        if btn is None:
            return
        started = [c for c in self.app.project.planned_calcs
                   if c.job_id and c.job_id != LOCAL_JOB]
        if not started:
            self._interrupt_state = "hidden"
            btn.pack_forget()
            return
        if not self._status_known:
            self._interrupt_state = "disarmed"
            btn.configure(text="Interrupt (refresh first)",
                          bg="#ece7c9", activebackground="#e2dcb8", fg="#7a7a55")
        else:
            active = self._active_submitted_calcs()
            if not active:
                self._interrupt_state = "hidden"
                btn.pack_forget()
                return
            self._interrupt_state = "armed"
            btn.configure(text="INTERRUPT ({} running)".format(len(active)),
                          bg="#d9534f", activebackground="#c9302c", fg="white")
        if btn.winfo_manager() != "pack":
            btn.pack(side=tk.LEFT, expand=True)

    def on_interrupt(self):
        state = getattr(self, "_interrupt_state", "hidden")
        if state == "disarmed":
            self._log("Interrupt: refreshing status before arming...")
            self.on_refresh_status()   # sets _status_known + re-runs _update_interrupt_button
            if getattr(self, "_interrupt_state", "hidden") == "armed":
                self.app.set_status("Active jobs found — press INTERRUPT again to cancel them.")
            else:
                self.app.set_status("No active jobs found — nothing to interrupt.")
            return
        if state == "armed":
            self._do_interrupt()

    def _do_interrupt(self):
        active = self._active_submitted_calcs()
        if not active:
            self._update_interrupt_button()
            messagebox.showinfo("Interrupt", "No active jobs to interrupt.")
            return
        job_ids = sorted({c.job_id for c in active})
        listing = "\n".join("  {}  [job {}]".format(self._short(c), c.job_id)
                            for c in active[:20])
        if len(active) > 20:
            listing += "\n  ... and {} more".format(len(active) - 20)
        if not messagebox.askyesno(
                "Interrupt calculations",
                "Cancel {} running/queued calculation(s)?\n\nThis kills the SLURM job(s) "
                "with scancel — it CANNOT be undone (partial output is kept). You can "
                "rebuild and resubmit afterwards.\n\n{}".format(len(active), listing)):
            return
        self._stop_pipeline()   # stop any live pipeline driver from resubmitting them
        n, errs = slurm_runtime.cancel_jobs(job_ids)
        self.on_refresh_status()
        msg = "Interrupted {} job(s) (scancel).".format(n)
        if errs:
            msg += "\n\nscancel reported:\n" + "\n".join(errs[:6])
        self._log(msg.replace("\n", " "))
        messagebox.showinfo("Interrupt", msg)

    def on_detect_jobs(self):
        """Reconnect calcs submitted outside the app by recovering their job ids
        from the run-dir output files and (for still-pending jobs) squeue."""
        if not self.app.project.planned_calcs:
            messagebox.showinfo("Detect jobs", "No calculations in this project yet.")
            return
        namemap = slurm_runtime.query_name_map()  # None if squeue unavailable
        s = discovery_mod.relink_project(self.app.project, name_to_jobid=namemap)
        self._log("Detect jobs: {} linked from output files, {} from squeue, "
                  "{} already linked, {} still unlinked.".format(
                      s["from_files"], s["from_queue"], s["already"], len(s["unlinked"])))
        if s["unlinked"]:
            show = ", ".join(s["unlinked"][:8]) + (" ..." if len(s["unlinked"]) > 8 else "")
            self._log("  not yet linkable (likely still PENDING — re-run later): " + show)
        if s["changed"]:
            self.app.mark_dirty()
        self.on_refresh_status()  # re-query states now that ids are known
        note = "" if namemap is not None else (
            "\n\nsqueue wasn't available, so only jobs that have started writing "
            "output were linked. Re-run on the login node to pick up pending jobs.")
        messagebox.showinfo(
            "Detect jobs",
            "Linked {} calc(s) ({} from files, {} from squeue).\n"
            "{} already linked, {} still unlinked.{}".format(
                s["changed"], s["from_files"], s["from_queue"],
                s["already"], len(s["unlinked"]), note))

    def on_import_calcs(self):
        """Import a directory of standalone .inp (+ outputs) as molecules/calcs,
        then auto-detect job ids. One-shot recovery for a project whose save file
        was lost: point it at the parent dir holding calcs/ (and XYZ_INI/)."""
        src = filedialog.askdirectory(title="Import ORCA .inp files from directory")
        if not src:
            return

        def _persist(recipe):
            # Save a newly-reconstructed recipe and register it, unless that name
            # already exists (re-import) — then reuse it.
            if self.app.get_recipe(recipe.name) is not None:
                return
            try:
                inputs_mod.save_recipe(recipe, self.app.recipe_dir)
            except OSError as e:
                self._log("Import: could not save recipe {!r}: {}".format(recipe.name, e))
            self.app.recipes.append(recipe)

        # If the source ships its own recipes/ (the app writes one there), load them
        # first so reconstructed calcs reuse the real recipes instead of duplicating.
        src_recipes = os.path.join(src, "recipes")
        if os.path.isdir(src_recipes):
            have = {r.name for r in self.app.recipes}
            added = 0
            for r in inputs_mod.load_recipes_from_dir(src_recipes):
                if r.name not in have:
                    self.app.recipes.append(r)
                    have.add(r.name)
                    added += 1
            if added:
                self._log("Import: loaded {} recipe(s) from {}".format(added, src_recipes))

        s = discovery_mod.import_dir(self.app.project, src, save_recipe=_persist,
                                     existing_recipes=self.app.recipes)
        self._log("Import: scanned {}, imported {} calc(s) in {} molecule(s), "
                  "skipped {} (already present), {} had outputs, {} reconstructed "
                  "geometry, {} new recipe(s).".format(
                      s["scanned"], s["imported"], s["molecules"], s["skipped"],
                      s["with_output"], s["reconstructed_xyz"], len(s["new_recipes"])))
        for err in s["errors"]:
            self._log("  ! " + err)

        relinked = 0
        if s["imported"]:
            self.app.mark_dirty()
            # Combined import + detect: recover job ids in the same action.
            namemap = slurm_runtime.query_name_map()
            rs = discovery_mod.relink_project(self.app.project, name_to_jobid=namemap)
            relinked = rs["changed"]
            self._log("Detect jobs: linked {} ({} from files, {} from queue).".format(
                rs["changed"], rs["from_files"], rs["from_queue"]))
            self.app.refresh_all_tabs()
        self.on_refresh_status()
        messagebox.showinfo(
            "Import calcs",
            "Imported {} calculation(s) in {} molecule(s) from\n{}\n\n"
            "{} already present (skipped); {} have outputs ready to harvest; "
            "{} geometry/geometries reconstructed; {} job id(s) linked.".format(
                s["imported"], s["molecules"], src, s["skipped"], s["with_output"],
                s["reconstructed_xyz"], relinked))

    # --------------------------------------------------- unattended (dependency chain)

    def on_submit_unattended(self):
        """Calculations-tab entry to the SLURM dependency-chain submit (the same
        engine the Workflow tab uses, but driven by the calcs' own parent/gate
        links instead of a node graph). Topologically orders the selected calcs so
        parents precede children, then hands them to submit_unattended — which
        builds each derived calc with an `* xyzfile` geometry reference and an
        `afterok:` dependency, so a whole OPT -> NMR set can be queued at once and
        the app / SSH session closed."""
        targets = self._selected_or_all()
        cand = [c for c in targets
                if not self._own_state(c)[2] and self._gate_status(c) != "closed"]
        if not cand:
            messagebox.showinfo(
                "Nothing to submit",
                "No unfinished calculations selected. Unattended submission builds and "
                "queues calcs as a SLURM dependency chain (parents before children), so "
                "the cluster runs the whole set with no app open.")
            return
        self.submit_unattended([c.id for c in self._topo_order(cand)])

    def _topo_order(self, calcs):
        # type: (list) -> list
        """Order calcs dependency-first: each calc's geometry parent and gate
        source come before it. Dependencies outside `calcs` impose no ordering —
        submit_unattended reads an already-finished parent's geometry off disk."""
        byid = {c.id: c for c in calcs}
        order, perm, temp = [], set(), set()

        def deps(c):
            out = []
            if c.geometry_source.startswith("parent:"):
                pid = c.geometry_source.split(":", 1)[1]
                if pid in byid:
                    out.append(pid)
            gate = getattr(c, "gate", None)
            if gate and gate.get("source") in byid:
                out.append(gate["source"])
            return out

        def visit(c):
            if c.id in perm or c.id in temp:
                return            # already placed, or a cycle we refuse to loop on
            temp.add(c.id)
            for pid in deps(c):
                visit(byid[pid])
            temp.discard(c.id)
            perm.add(c.id)
            order.append(c)

        for c in calcs:
            visit(c)
        return order

    def submit_unattended(self, calc_ids, reports=None):
        # type: (list, Optional[list]) -> None
        """Submit a workflow's calcs as a SLURM dependency chain: each step is
        held by SLURM until its parent geometry job (afterok) and any gate-source
        job finish, and a Condition becomes a shell guard inside the job. SLURM
        then drives the whole pipeline with no GUI running — submit and
        disconnect. `calc_ids` must be in topological order (parents/sources
        first), as produced by the workflow expansion."""
        self._status_known = False   # dependency chain goes out; state now unknown
        root = self.app.project.root()
        if not slurm_runtime.sbatch_available():
            messagebox.showerror("sbatch not found",
                                 "Unattended submission needs sbatch — run this on the cluster "
                                 "login node.")
            return
        try:
            template = slurm_mod.load_template()
        except Exception as e:
            messagebox.showerror("Slurm template missing", str(e))
            return
        ids = [cid for cid in calc_ids if self.app.project.calc_by_id(cid) is not None]
        if not ids:
            return
        self._squeue_states = slurm_runtime.query_states()
        if not messagebox.askyesno(
                "Submit unattended",
                "Submit {} calculation(s) as a SLURM dependency chain?\n\n"
                "Each step is held until the job it depends on finishes, and a Condition "
                "node becomes a check inside the job. SLURM runs the whole pipeline on its "
                "own — you can close ORCA Workbench (and MobaXterm) once it's submitted.\n\n"
                "Note: merged Report files are written by the app, so reopen the project and "
                "use the Report tab once the jobs are done.".format(len(ids))):
            return
        self._log_clear()
        jobmap = {}   # calc id -> job id (submitted, or already active, in this batch)
        n_sub = n_skip = n_done = 0
        delay = self._submit_delay_s()
        pd = ProgressDialog(self, "Submitting dependency chain", total=len(ids))
        for cid in ids:
            calc = self.app.project.calc_by_id(cid)
            if calc is None:
                continue
            pd.step("Submitting {}".format(self._short(calc)))
            if pd.cancelled:
                self._log("Cancelled — {} already submitted.".format(n_sub))
                break
            mol = self.app.project.molecule_by_filename(calc.molecule_filename)
            recipe = self.app.get_recipe(calc.recipe_name)
            if mol is None or recipe is None:
                self._log("SKIP {}: molecule or recipe missing".format(self._short(calc)))
                n_skip += 1
                continue
            _, _, done, active = self._own_state(calc)
            if done:
                n_done += 1
                continue            # already finished; children read its geometry off disk
            if active:
                jobmap[cid] = calc.job_id   # already queued/running; usable as a dependency
                continue
            deps = []
            xyz_ref = None
            preamble = ""
            # geometry parent
            if calc.geometry_source.startswith("parent:"):
                parent = self.app.project.calc_by_id(calc.geometry_source[len("parent:"):])
                if parent is None:
                    self._log("SKIP {}: parent calc missing".format(self._short(calc)))
                    n_skip += 1
                    continue
                pxyz = self._unattended_parent_xyz(parent, mol, root)
                pj = jobmap.get(parent.id)
                if pj:
                    deps.append(pj)
                    xyz_ref = pxyz
                elif self._own_state(parent)[2] and os.path.isfile(pxyz):
                    xyz_ref = pxyz      # parent already done; geometry on disk, no dependency
                else:
                    self._log("SKIP {}: parent '{}' isn't finished or in this batch".format(
                        self._short(calc), self._short(parent)))
                    n_skip += 1
                    continue
            # conditional gate
            gate = getattr(calc, "gate", None)
            if gate:
                src = self.app.project.calc_by_id(gate.get("source"))
                if src is not None:
                    sj = jobmap.get(src.id)
                    if sj:
                        deps.append(sj)
                        preamble = slurm_mod.gate_guard(
                            gate.get("predicate", "terminated_ok"),
                            self._unattended_source_out(src, mol, sj, root))
                    elif self._own_state(src)[2]:
                        if self._gate_status(calc) == "closed":
                            self._log("SKIP {}: condition already not met".format(self._short(calc)))
                            n_skip += 1
                            continue
                        # condition already satisfied -> no guard needed
                    else:
                        self._log("SKIP {}: gate source '{}' isn't finished or in this batch"
                                  .format(self._short(calc), self._short(src)))
                        n_skip += 1
                        continue
            deps = list(dict.fromkeys(deps))   # dedupe (parent could equal gate source)
            try:
                inp_rel, slurm_rel, rundir_rel = self._build_one(
                    calc, mol, recipe, template, xyz_ref=xyz_ref, preamble=preamble)
                calc.inp_path = inp_rel
                calc.slurm_path = slurm_rel
                calc.rundir = rundir_rel
                calc.exported = True
                calc.job_id = None
            except Exception as e:
                self._log("BUILD FAILED {}: {}".format(self._short(calc), e))
                n_skip += 1
                continue
            dep = ("afterok:" + ":".join(deps)) if deps else None
            jobid, err = slurm_runtime.submit(calc.slurm_path, root, dependency=dep)
            if jobid:
                calc.job_id = jobid
                jobmap[cid] = jobid
                n_sub += 1
                self._log("SUBMITTED {} -> job {}{}".format(
                    self._short(calc), jobid, "   [{}]".format(dep) if dep else ""))
            else:
                self._log("SUBMIT FAILED {}: {}".format(self._short(calc), err))
            if delay:
                time.sleep(delay)        # throttle between dependency-chain submits
                n_skip += 1
        pd.close()
        self.app.mark_dirty()
        self.on_refresh_status()
        self.app.set_status(
            "Unattended: {} submitted, {} skipped, {} already done — safe to disconnect."
            .format(n_sub, n_skip, n_done))

    def _unattended_parent_xyz(self, parent, mol, root):
        """Absolute path to a parent calc's optimised geometry (ORCA writes
        <mol>.xyz in the run dir), read by a child job at run time."""
        rundir = parent.rundir
        if not rundir:
            prec = self.app.get_recipe(parent.recipe_name)
            rundir = self._target_dir(parent, mol, prec)
        return os.path.abspath(os.path.join(root, rundir, mol.filename + ".xyz"))

    def _unattended_source_out(self, src, mol, src_jobid, root):
        """Absolute path to a gate source's .out (named <jobname>-<jobid>.out by
        the slurm template), read by the gated job's condition guard."""
        rundir = src.rundir
        if not rundir:
            rundir = self._target_dir(src, mol, self.app.get_recipe(src.recipe_name))
        name = slurm_mod._sanitize_jobname(mol.filename)
        return os.path.abspath(os.path.join(root, rundir, "{}-{}.out".format(name, src_jobid)))

    # ----------------------------------------------------------- run locally

    def on_run_local(self):
        root = self.app.project.root()
        targets = self._selected_or_all()
        candidates = [c for c in targets if c.exported and c.inp_path and not c.job_id
                      and self._gate_status(c) != "closed"]
        if not candidates:
            messagebox.showinfo(
                "Nothing to run",
                "No built, un-run calculations matched. Build first; calcs that already have a "
                "result aren't re-run (rebuild them to run again). Calcs whose workflow "
                "condition failed are skipped.")
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
            if self._gate_status(calc) == "pending":
                self._log("SKIP {}: waiting on condition ({})".format(
                    self._short(calc), self._gate_label(calc)))
                continue
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
        self._stop_pipeline()
        self._local_runner.cancel_all()
        self._local_runner.poll()
        self.refresh()
        self.app.set_status("Local run stopped. Unfinished calcs are marked interrupted — "
                            "re-run the pipeline to resume them.")

    def _stop_pipeline(self):
        """Halt the live pipeline driver (leaves calc states as-is; a later
        Run pipeline resumes the unfinished ones)."""
        if self._pipeline_poll_id is not None:
            try:
                self.after_cancel(self._pipeline_poll_id)
            except Exception:
                pass
            self._pipeline_poll_id = None
        self._pipeline_ids = set()

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

    # ------------------------------------------------- live pipeline driver

    def start_pipeline(self, calc_ids, reports=None):
        # type: (list, Optional[list]) -> None
        """Take a set of calcs under automatic control: build and launch each as
        its parent geometry and conditional gate allow, evaluating Condition
        gates live as their source calcs finish. Re-running is a *resume*: calcs
        already finished are left alone, ones still genuinely running keep going,
        and interrupted/unfinished ones are re-armed so only they restart — no
        duplicate queueing. Called by the Workflow tab's "Run pipeline" button."""
        ids = [cid for cid in calc_ids if self.app.project.calc_by_id(cid) is not None]
        if not ids:
            return
        self._pipeline_ids = set(ids)
        self._pipeline_reports = list(reports or [])
        if self._local_mode:
            # Make sure we have an ORCA exe before we start the engine.
            orca = self._ensure_orca_path()
            if not orca:
                self._pipeline_ids = set()
                self._log("Pipeline cancelled — no ORCA executable selected.")
                return
            conc = 1
            if hasattr(self, "concurrency_var"):
                conc = max(1, int(self.concurrency_var.get()))
            if self._local_runner is None or self._local_runner.orca_exe != orca:
                self._local_runner = local_runner_mod.LocalRunner(orca, max_concurrent=conc)
            else:
                self._local_runner.max_concurrent = conc
            self._local_runner.poll()
        elif not slurm_runtime.sbatch_available():
            messagebox.showerror("sbatch not found",
                                 "Pipeline execution needs sbatch (cluster) or a local ORCA. "
                                 "Neither is available here.")
            self._pipeline_ids = set()
            return
        else:
            # Know which jobs are genuinely still queued before deciding what to resume.
            self._squeue_states = slurm_runtime.query_states()
        n_resume = self._rearm_pipeline()
        self._log_clear()
        self._log("Pipeline started: {} calc(s) under automatic control{}.".format(
            len(ids), " (resuming {} unfinished)".format(n_resume) if n_resume else ""))
        self._pipeline_tick()

    def _rearm_pipeline(self):
        """Reset interrupted/unfinished calcs so the driver re-runs only them.
        Leaves finished and genuinely-active calcs untouched. Returns how many
        were re-armed."""
        n = 0
        for cid in self._pipeline_ids:
            calc = self.app.project.calc_by_id(cid)
            if calc is None or not calc.exported or not calc.job_id:
                continue
            _, _, done, active = self._own_state(calc)
            if done or active:
                continue
            # interrupted / cancelled / errored → clear the job id to re-launch
            calc.job_id = None
            n += 1
        return n

    def pipeline_active(self):
        return bool(self._pipeline_ids)

    def _pipeline_tick(self):
        self._pipeline_poll_id = None
        # Refresh the status sources the driver reads to decide readiness.
        if self._local_mode:
            if self._local_runner is not None:
                self._local_runner.poll()
        else:
            self._squeue_states = slurm_runtime.query_states()

        try:
            template = slurm_mod.load_template()
        except Exception as e:
            self._log("Pipeline halted — slurm template missing: {}".format(e))
            self.refresh()
            return

        acted = False           # built or launched something this tick
        active_exists = False   # something is genuinely queued/running
        for cid in list(self._pipeline_ids):
            calc = self.app.project.calc_by_id(cid)
            if calc is None:
                self._pipeline_ids.discard(cid)
                continue
            _, tag = self._display_state(calc)
            if tag in _TERMINAL_TAGS:
                continue  # done / error / skipped / interrupted — settled
            if calc.exported and self._own_state(calc)[3]:
                active_exists = True
                continue
            gst = self._gate_status(calc)
            if gst in ("pending", "closed"):
                continue  # gate decides later (pending) or never (closed→skipped)

            mol = self.app.project.molecule_by_filename(calc.molecule_filename)
            recipe = self.app.get_recipe(calc.recipe_name)
            if not calc.exported:
                ok, _issue = self._validate(calc, mol, recipe)
                if not ok:
                    continue  # parent geometry not ready yet — wait
                if not self._pipeline_build(calc, mol, recipe, template):
                    continue
                acted = True
            if calc.exported and not calc.job_id:
                self._pipeline_launch(calc, mol)
                acted = True

        self.app.mark_dirty()
        self.refresh()
        if hasattr(self.app, "workflow_tab"):
            try:
                self.app.workflow_tab.refresh_live()
            except Exception:
                pass

        # Keep ticking while anything is running or we just kicked something off.
        # When nothing is active and a full pass launched nothing, we're settled
        # (any still-unfinished calc is blocked by a failed upstream).
        if active_exists or acted:
            self._pipeline_poll_id = self.after(2000, self._pipeline_tick)
        else:
            self._finish_pipeline()

    def _finish_pipeline(self):
        done = sum(1 for cid in self._pipeline_ids
                   if self.app.project.calc_by_id(cid) is not None
                   and self._own_state(self.app.project.calc_by_id(cid))[2])
        total = len(self._pipeline_ids)
        self._log("Pipeline settled — {}/{} finished OK.".format(done, total))
        try:
            self._generate_pipeline_reports()
        except Exception as e:
            self._log("Report generation failed: {}".format(e))
        self.app.set_status("Pipeline finished ({}/{} OK).".format(done, total))

    def _generate_pipeline_reports(self):
        """When the pipeline settles, write one merged JSON (+CSV) per Report
        node, gathering every calc wired into it plus its geometry-ancestor
        chain (so a Report fed by NMR also captures the OPT/FREQ it came from)."""
        import re
        from orca_workbench.core import reporting
        specs = getattr(self, "_pipeline_reports", None)
        if not specs:
            return
        root = self.app.project.root()
        for spec in specs:
            node_ids = spec.get("node_ids") or []
            contrib = {}  # calc id -> calc
            for c in self.app.project.planned_calcs:
                if getattr(c, "origin_node", None) in node_ids:
                    cur, guard = c, 0
                    while cur is not None and guard < 50:
                        guard += 1
                        contrib[cur.id] = cur
                        cur = (self.app.project.calc_by_id(cur.parent_id)
                               if cur.parent_id else None)
            if not contrib:
                continue
            contexts = []
            for c in contrib.values():
                recipe = self.app.get_recipe(c.recipe_name)
                rundir_abs = os.path.join(root, c.rundir) if c.rundir else None
                contexts.append(reporting.CalcContext(
                    calc_id=c.id,
                    label="{} {}".format(recipe.calctype if recipe else "?", c.molecule_filename),
                    molecule=c.molecule_filename,
                    calctype=recipe.calctype if recipe else "?",
                    method=recipe.method_label if recipe else "?",
                    out_path=self._out_path(c),
                    rundir_abs=rundir_abs,
                ))
            ekeys = spec.get("extractors")   # None => all (Report node's selection)
            keys = ekeys if ekeys is not None else [e.key for e in reporting.EXTRACTORS]
            report = reporting.assemble_report(contexts, keys)
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", (spec.get("name") or "report").strip()) or "report"
            json_path = os.path.join(root, name + ".json")
            reporting.write_json(report, json_path)
            try:
                reporting.write_csv(report, os.path.join(root, name + ".csv"))
            except Exception:
                pass
            self._log("Report written: {} ({} calc(s))".format(json_path, len(contexts)))

    def _pipeline_build(self, calc, mol, recipe, template):
        try:
            inp_rel, slurm_rel, rundir_rel = self._build_one(calc, mol, recipe, template)
            calc.inp_path = inp_rel
            calc.slurm_path = slurm_rel
            calc.rundir = rundir_rel
            calc.exported = True
            calc.job_id = None
            self._log("PIPELINE built {}".format(self._short(calc)))
            return True
        except Exception as e:
            self._log("PIPELINE build failed {}: {}".format(self._short(calc), e))
            return False

    def _pipeline_launch(self, calc, mol):
        root = self.app.project.root()
        if self._local_mode:
            if self._local_runner is None:
                return
            rundir_abs = os.path.join(root, calc.rundir)
            inp_abs = os.path.join(root, calc.inp_path)
            out_abs = os.path.join(rundir_abs, calc.molecule_filename + "-" + LOCAL_JOB + ".out")
            calc.job_id = LOCAL_JOB
            self._local_runner.forget(calc.id)
            self._local_runner.submit(calc.id, inp_abs, out_abs, rundir_abs)
            self._log("PIPELINE running {} (local)".format(self._short(calc)))
        else:
            job_id, err = slurm_runtime.submit(calc.slurm_path, root)
            if job_id:
                calc.job_id = job_id
                self._log("PIPELINE submitted {} -> job {}".format(self._short(calc), job_id))
            else:
                self._log("PIPELINE submit failed {}: {}".format(self._short(calc), err))

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

        # Geometry constraints / relaxed scan (OPT jobs): a per-calc %geom spec.
        if len(calcs) == 1:
            c0 = calcs[0]
            cur = geomspec_mod.describe(getattr(c0, "geom_spec", None))
            menu.add_command(label="Geometry constraints / scan...  [{}]".format(cur),
                             command=lambda cc=c0: self._edit_geom_spec(cc))
            menu.add_separator()

        finished_freq = [c for c in calcs if self._is_finished_type(c, "FREQ")]
        finished_nmr = [c for c in calcs if self._is_finished_type(c, "NMR")]
        finished_uvvis = [c for c in calcs if self._is_finished_type(c, "TDDFT")]
        finished_epr = [c for c in calcs if self._is_finished_type(c, "EPR")]

        if finished_freq:
            menu.add_command(label="Plot IR spectrum ({} selected)".format(len(finished_freq)),
                             command=lambda cs=list(finished_freq): self._plot_ir(cs))
        else:
            menu.add_command(label="Plot IR spectrum  (select finished FREQ)",
                             state=tk.DISABLED)
        if finished_nmr:
            menu.add_command(label="Plot NMR spectrum ({} selected)".format(len(finished_nmr)),
                             command=lambda cs=list(finished_nmr): self._plot_nmr(cs))
        else:
            menu.add_command(label="Plot NMR spectrum  (select finished NMR)", state=tk.DISABLED)
        if finished_uvvis:
            menu.add_command(label="Plot UV-Vis spectrum ({} selected)".format(len(finished_uvvis)),
                             command=lambda cs=list(finished_uvvis): self._plot_uvvis(cs))
        else:
            menu.add_command(label="Plot UV-Vis spectrum  (select finished TD-DFT)",
                             state=tk.DISABLED)
        if finished_epr:
            menu.add_command(label="Plot EPR spectrum ({} selected)".format(len(finished_epr)),
                             command=lambda cs=list(finished_epr): self._plot_epr(cs))
            menu.add_command(label="Plot ENDOR spectrum ({} selected)".format(len(finished_epr)),
                             command=lambda cs=list(finished_epr): self._plot_endor(cs))
        else:
            menu.add_command(label="Plot EPR spectrum  (select finished EPR)",
                             state=tk.DISABLED)

        if len(calcs) == 1 and calcs[0].job_id:
            menu.add_separator()
            menu.add_command(label="Open live progress plot",
                             command=lambda c=calcs[0]: self._open_live(c))

        # Finished OPT: open the optimised geometry / trajectory in 3D (molden on
        # the gateway, local Avogadro otherwise) — same path as the Molecules tab.
        finished_opt = [c for c in calcs if self._is_finished_type(c, "OPT")]
        if len(finished_opt) == 1:
            geom = self._calc_file(finished_opt[0], finished_opt[0].molecule_filename + ".xyz")
            trj = self._calc_file(finished_opt[0], finished_opt[0].molecule_filename + "_trj.xyz")
            if geom or trj:
                menu.add_separator()
            if geom:
                menu.add_command(label="Open optimized geometry (3D)",
                                 command=lambda p=geom: self._open_3d(p))
            if trj:
                menu.add_command(label="Open trajectory as movie",
                                 command=lambda p=trj: self._open_3d(p, slot="traj_viewer_path"))

        # Finished FREQ: open the .out in the 3D viewer to animate the normal modes
        # (Avogadro reads ORCA output directly; a bare .xyz has no mode data).
        if len(finished_freq) == 1:
            out = self._out_path(finished_freq[0])
            if out and os.path.isfile(out):
                if not finished_opt:
                    menu.add_separator()
                menu.add_command(label="Open normal modes (3D viewer)",
                                 command=lambda p=out: self._open_3d(p))

        # Finished calc with a converged wavefunction: generate a Gaussian-cube
        # (electron density / spin density / a molecular orbital) post-hoc with
        # orca_plot and open it in the external viewer. Only offered when the .gbw
        # is actually present in the run dir.
        if len(calcs) == 1 and self._own_state(calcs[0])[2] and \
                self._calc_file(calcs[0], calcs[0].molecule_filename + ".gbw"):
            menu.add_separator()
            menu.add_command(label="Generate density/MO cube...",
                             command=lambda c=calcs[0]: self._generate_cube(c))
            menu.add_command(label="Export Molden file (for Multiwfn)...",
                             command=lambda c=calcs[0]: self._export_molden(c))

        # Manual dependency re-linking — e.g. after importing a project whose save
        # file was lost, point a flat NMR/FREQ calc at its OPT as the parent so it
        # inherits the optimised geometry. (Affects future rebuilds, not the run
        # that already happened.)
        if calcs:
            menu.add_separator()
            if len(calcs) == 1:
                menu.add_command(label="Set parent (inherit another calc's geometry)...",
                                 command=lambda cs=list(calcs): self._set_parent_interactive(cs))
            else:
                menu.add_command(label="Set parent for {} selected...".format(len(calcs)),
                                 command=lambda cs=list(calcs): self._set_parent_interactive(cs))
            if any(c.parent_id for c in calcs):
                label = "Clear parent" if len(calcs) == 1 else \
                    "Clear parent ({} selected)".format(len(calcs))
                menu.add_command(label=label,
                                 command=lambda cs=list(calcs): self._clear_parent(cs))

        # tk_popup grabs the menu so a click anywhere dismisses it. On X11 tk_popup
        # returns immediately (unlike Windows, where it blocks), so releasing the grab
        # right after leaves the menu posted but UNgrabbed — it then only closes when
        # you click ON an item, never when you click away (exactly the reported bug).
        # Defer the release to when the menu actually unmaps instead of doing it now.
        menu.bind("<Unmap>", lambda _e, m=menu: m.grab_release(), add="+")
        menu.tk_popup(event.x_root, event.y_root)

    def _descendant_ids(self, calc):
        """Ids of calcs reachable from `calc` via parent_id links (to avoid making
        a calc its own ancestor when re-linking)."""
        out = set()
        stack = [calc.id]
        while stack:
            cur = stack.pop()
            for c in self.app.project.planned_calcs:
                if c.parent_id == cur and c.id not in out:
                    out.add(c.id)
                    stack.append(c.id)
        return out

    def _calctype_of(self, calc):
        r = self.app.get_recipe(calc.recipe_name)
        return r.calctype if r else "?"

    def _set_parent_interactive(self, calcs):
        """Set the geometry parent for one or many selected calcs. A single calc
        gets the precise exact-calc chooser; a multi-selection picks a parent
        *recipe* and each calc links to its OWN molecule's calc of that recipe (so
        N derived calcs — even across molecules — wire up in one action)."""
        calcs = [c for c in calcs if c is not None]
        if not calcs:
            return
        if len(calcs) == 1:
            self._set_parent_single(calcs[0])
        else:
            self._set_parent_bulk(calcs)

    def _set_parent_single(self, calc):
        desc = self._descendant_ids(calc)
        cands = [c for c in self.app.project.planned_calcs
                 if c.molecule_filename == calc.molecule_filename
                 and c.id != calc.id and c.id not in desc]
        if not cands:
            messagebox.showinfo(
                "Set parent",
                "No other calculation for molecule '{}' to use as a parent.".format(
                    calc.molecule_filename))
            return
        items = [("{} | {} | {}".format(self._calctype_of(c), c.recipe_name, c.category), c)
                 for c in cands]
        prompt = ("Use which calculation's geometry as the parent?\n"
                  "(Molecule '{}'.)".format(calc.molecule_filename))
        chosen = _ChooseOneDialog(self, "Choose parent calculation", prompt, items,
                                  ok_label="Set parent").result
        if chosen is None:
            return
        calc.parent_id = chosen.id
        calc.geometry_source = "parent:" + chosen.id
        if not calc.job_id:
            calc.exported = False   # a not-yet-run calc must rebuild from the new geom
        self.app.mark_dirty()
        self.refresh()
        self._log("Set parent of {} -> {}".format(self._short(calc), self._short(chosen)))

    def _set_parent_bulk(self, calcs):
        mols = sorted({c.molecule_filename for c in calcs})
        # Pool = every calc in the involved molecules (INCLUDING selected ones, so a
        # selected OPT can still parent a selected NMR). The chosen recipe is matched
        # per-molecule; self/descendant links are skipped by match_parents_by_recipe.
        pool = [c for c in self.app.project.planned_calcs if c.molecule_filename in mols]
        seen = set()
        items = []
        for c in sorted(pool, key=lambda c: (self._calctype_of(c), c.recipe_name)):
            if c.recipe_name in seen:
                continue
            seen.add(c.recipe_name)
            items.append(("{} | {}".format(self._calctype_of(c), c.recipe_name), c.recipe_name))
        if not items:
            messagebox.showinfo("Set parent",
                                "No candidate parent calculation in the selected molecule(s).")
            return
        prompt = ("Set the parent for {} selected calc(s) across {} molecule(s).\n"
                  "Pick the parent recipe — each selected calc links to its OWN "
                  "molecule's calc of that recipe (a calc that IS that recipe is "
                  "skipped):".format(len(calcs), len(mols)))
        recipe_name = _ChooseOneDialog(self, "Set parent for selection", prompt, items,
                                       ok_label="Set parents").result
        if recipe_name is None:
            return
        mapping = discovery_mod.match_parents_by_recipe(
            calcs, self.app.project.planned_calcs, recipe_name)
        linked = 0
        for c in calcs:
            pid = mapping.get(c.id)
            if not pid:
                continue
            c.parent_id = pid
            c.geometry_source = "parent:" + pid
            if not c.job_id:
                c.exported = False
            linked += 1
        skipped = len(calcs) - linked
        if linked:
            self.app.mark_dirty()
            self.refresh()
        note = "" if not skipped else "; {} skipped (no match / is that recipe)".format(skipped)
        self._log("Set parent: linked {} calc(s) to '{}'{}".format(linked, recipe_name, note))
        if not linked:
            messagebox.showinfo("Set parent",
                                "Nothing linked — no selected calc had a matching "
                                "parent of that recipe in its molecule.")

    def _clear_parent(self, calcs):
        n = 0
        for c in calcs:
            if not (c.parent_id or c.geometry_source.startswith("parent:")):
                continue
            c.parent_id = None
            if c.geometry_source.startswith("parent:"):
                c.geometry_source = "initial"
            if not c.job_id:
                c.exported = False
            n += 1
        if n:
            self.app.mark_dirty()
            self.refresh()
            self._log("Cleared parent of {} calc(s)".format(n))

    def _calc_file(self, calc, name):
        """Absolute path to <rundir>/<name> if it exists, else None."""
        if not calc.rundir:
            return None
        p = os.path.join(self.app.project.root(), calc.rundir, name)
        return p if os.path.isfile(p) else None

    def _open_3d(self, path, slot="viewer_3d_path"):
        from orca_workbench.ui.molecules_tab import open_xyz_3d
        open_xyz_3d(self, self.app, path, slot=slot)

    def _edit_geom_spec(self, calc):
        """Edit the calc's geometry constraints / relaxed-scan spec (injected into the
        ORCA input's %geom block at build time). Meaningful for OPT recipes only."""
        from orca_workbench.ui.geomspec_dialog import GeomSpecDialog
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        atoms = []
        if mol and mol.xyz_path:
            p = mol.xyz_path
            if not os.path.isabs(p):
                p = os.path.join(self.app.project.root(), p)
            try:
                atoms, _meta = coords_mod.read_xyz(p)
            except Exception:
                atoms = []
        if not atoms:
            messagebox.showinfo(
                "No geometry yet",
                "Generate this molecule's XYZ first (Molecules tab > Generate XYZ) so the "
                "atom indices are known.")
            return
        recipe = self.app.get_recipe(calc.recipe_name)
        if recipe is not None and not (recipe.calctype or "").upper().startswith("OPT"):
            if not messagebox.askyesno(
                    "Not an optimization",
                    "Constraints and relaxed scans only take effect during a geometry "
                    "optimization (`! Opt`), but '{}' is a {} recipe. Set the spec anyway?"
                    .format(calc.recipe_name, recipe.calctype)):
                return

        def _save(spec):
            calc.geom_spec = spec
            if calc.exported:
                self.app.set_status("Geometry spec saved - re-Build '{}' to apply it to the "
                                    "input.".format(self._short(calc)))
            else:
                self.app.set_status("Geometry spec for {}: {}".format(
                    self._short(calc), geomspec_mod.describe(spec)))
            self.app.mark_dirty()
            self.refresh()

        GeomSpecDialog(self, atoms, getattr(calc, "geom_spec", None), _save)

    # ---- post-hoc density / MO cubes (orca_plot on a finished .gbw) -----------
    def _generate_cube(self, calc):
        """Run orca_plot on a finished calc's .gbw to write a Gaussian cube (total
        electron density / spin density / one molecular orbital) and open it in the
        external 3D viewer. orca_plot is a light post-processor — no sbatch; it runs
        directly (on the cluster inside the same module env the SLURM jobs use)."""
        gbw = self._calc_file(calc, calc.molecule_filename + ".gbw")
        if not gbw:
            messagebox.showinfo(
                "No wavefunction file",
                "This calculation has no {}.gbw in its run dir, so there's nothing "
                "to plot. Re-run it, or pick a job type that writes a .gbw.".format(
                    calc.molecule_filename))
            return
        kind = _ask_choice(
            self, "Generate cube",
            "What to plot from {}.gbw:".format(calc.molecule_filename),
            ["Electron density (total)", "Spin density (open-shell)",
             "Molecular orbital..."])
        if not kind:
            return
        if kind.startswith("Electron"):
            stdin = orca_plot_mod.plot_stdin("density")
        elif kind.startswith("Spin"):
            stdin = orca_plot_mod.plot_stdin("spin")
        else:
            idx = simpledialog.askinteger(
                "Molecular orbital",
                "Orbital index (0-based). The HOMO is the highest occupied MO — for "
                "a closed-shell molecule with N electrons that's index N/2 - 1; the "
                "LUMO is the next one up.",
                parent=self, minvalue=0)
            if idx is None:
                return
            stdin = orca_plot_mod.plot_stdin("mo", mo_index=idx)
        rundir_abs = os.path.join(self.app.project.root(), calc.rundir)
        gbw_name = calc.molecule_filename + ".gbw"
        self._log("orca_plot: generating cube from {} ...".format(gbw_name))
        threading.Thread(target=self._run_cube_worker,
                         args=(rundir_abs, gbw_name, stdin), daemon=True).start()

    def _run_cube_worker(self, rundir_abs, gbw_name, stdin):
        try:
            cube, err = self._run_orca_plot(rundir_abs, gbw_name, stdin)
        except Exception as e:   # surface, don't crash the worker thread
            cube, err = None, "{}: {}".format(type(e).__name__, e)
        self.after(0, lambda: self._cube_done(rundir_abs, cube, err))

    def _orca_aux_command(self, tool, tool_args, rundir_abs):
        """Build (args, run_kwargs, error) to launch an ORCA aux tool (orca_plot /
        orca_2mkl) in `rundir_abs`. On the cluster, wrap it in a login shell that
        loads the SAME `module load` lines the SLURM template uses, so the shared
        ORCA libraries resolve exactly as in a real job. Locally, use the tool that
        sits beside the configured ORCA executable. On failure, args/kwargs are None
        and error is a message."""
        if self._local_mode:
            orca = config_mod.get("orca_path", "")
            if not orca:
                return None, None, ("No local ORCA executable is configured, so {} "
                                    "can't be located. Run a job locally once (it "
                                    "prompts for the ORCA path), then retry.".format(tool))
            exe = os.path.join(os.path.dirname(orca), tool)
            if os.name == "nt" and not exe.lower().endswith(".exe"):
                exe += ".exe"
            if not os.path.isfile(exe):
                return None, None, "{} not found next to the ORCA executable:\n{}".format(tool, exe)
            return [exe] + list(tool_args), {"cwd": rundir_abs}, None
        import shlex
        template = slurm_mod.load_template()
        modules = "\n".join(ln for ln in template.splitlines()
                            if ln.strip().startswith("module load"))
        inner = " ".join([tool] + [shlex.quote(a) for a in tool_args])
        script = "{}\ncd {} && {}".format(modules, shlex.quote(rundir_abs), inner)
        return ["bash", "-lc", script], {}, None

    def _run_orca_plot(self, rundir_abs, gbw_name, stdin):
        """Run orca_plot in `rundir_abs`, feeding `stdin` to its wizard. Returns
        (cube_basename, error_text)."""
        args, kw, err = self._orca_aux_command("orca_plot", [gbw_name, "-i"], rundir_abs)
        if err:
            return None, err
        # Bound the run: if the wizard ever desyncs (a future ORCA adds a prompt),
        # it loops forever on EOF printing "Invalid input" — the timeout kills it.
        try:
            proc = subprocess.run(
                args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=180, **kw)
        except subprocess.TimeoutExpired as e:
            tail = "\n".join((e.output or "").splitlines()[-10:])
            return None, ("orca_plot timed out (180 s) — likely a menu mismatch with "
                          "this ORCA version. Last output:\n{}".format(tail))
        out = proc.stdout or ""
        cube = orca_plot_mod.parse_output_cube(out)
        if cube:
            return cube, None
        tail = "\n".join(out.splitlines()[-10:]) or "(no output)"
        return None, "orca_plot exited {} and wrote no .cube. Last output:\n{}".format(
            proc.returncode, tail)

    # ---- Molden / Multiwfn hand-off (orca_2mkl on a finished .gbw) ------------
    def _export_molden(self, calc):
        """Run orca_2mkl on a finished calc's .gbw to write a Molden-format file
        (<mol>.molden.input) for hand-off to Multiwfn (ELF/LOL/NCI/Fukui/charges) or
        molden. Like the cube path, orca_2mkl is a light converter — no sbatch."""
        gbw = self._calc_file(calc, calc.molecule_filename + ".gbw")
        if not gbw:
            messagebox.showinfo(
                "No wavefunction file",
                "This calculation has no {}.gbw in its run dir, so there's nothing to "
                "convert.".format(calc.molecule_filename))
            return
        rundir_abs = os.path.join(self.app.project.root(), calc.rundir)
        base = calc.molecule_filename
        self._log("orca_2mkl: writing {}.molden.input ...".format(base))
        threading.Thread(target=self._run_molden_worker,
                         args=(rundir_abs, base), daemon=True).start()

    def _run_molden_worker(self, rundir_abs, base):
        path, err = None, None
        try:
            args, kw, err = self._orca_aux_command("orca_2mkl", [base, "-molden"], rundir_abs)
            if not err:
                proc = subprocess.run(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, timeout=120, **kw)
                molden = os.path.join(rundir_abs, base + ".molden.input")
                if os.path.isfile(molden):
                    path = molden
                else:
                    tail = "\n".join((proc.stdout or "").splitlines()[-10:])
                    err = "orca_2mkl wrote no .molden.input. Last output:\n{}".format(tail)
        except Exception as e:
            err = "{}: {}".format(type(e).__name__, e)
        self.after(0, lambda: self._molden_done(path, err))

    def _molden_done(self, path, err):
        if err:
            self._log("orca_2mkl failed: {}".format(err.splitlines()[0]))
            messagebox.showerror("orca_2mkl", err)
            return
        self._log("orca_2mkl wrote {}".format(os.path.basename(path)))
        messagebox.showinfo(
            "Molden file written",
            "Wrote:\n{}\n\nLoad it into Multiwfn (ELF / LOL / NCI / Fukui / charges / "
            "bond orders) or open it in molden.".format(path))

    def _cube_done(self, rundir_abs, cube, err):
        if err:
            self._log("orca_plot failed: {}".format(err.splitlines()[0]))
            messagebox.showerror("orca_plot", err)
            return
        path = os.path.join(rundir_abs, cube)
        self._log("orca_plot wrote {}".format(cube))
        self._open_3d(path)

    # Calc types whose richer content (vibrational modes) lives in the ORCA .out,
    # which Avogadro/molden read and animate — a bare .xyz can't show it. (Density/
    # MO visualisation needs explicit print keywords or a .gbw, so those aren't a
    # blanket rule here; FREQ is the always-correct case.)
    _OUT_VIEWER_CALCTYPES = {"FREQ"}

    def viewer_file_for_calc(self, calc):
        """Best file to open in a 3D molecular viewer for a finished calc. For a
        FREQ job that's the ORCA .out — Avogadro (and molden) animate the normal
        modes from it, which a bare .xyz can't. For everything else it's the
        geometry: the optimised <mol>.xyz in the run dir, then the .out, then the
        molecule's input .xyz. Returns an absolute path or None."""
        recipe = self.app.get_recipe(calc.recipe_name)
        ctype = (recipe.calctype if recipe else "").upper()
        if ctype in self._OUT_VIEWER_CALCTYPES:
            out = self._out_path(calc)
            if out and os.path.isfile(out):
                return out
        geom = self._calc_file(calc, calc.molecule_filename + ".xyz")
        if geom:
            return geom
        out = self._out_path(calc)
        if out and os.path.isfile(out):
            return out
        mol = self.app.project.molecule_by_filename(calc.molecule_filename)
        if mol and mol.xyz_path:
            root = self.app.project.root()
            p = mol.xyz_path if os.path.isabs(mol.xyz_path) else os.path.join(root, mol.xyz_path)
            if os.path.isfile(p):
                return p
        return None

    def _is_finished_type(self, calc, calctype):
        recipe = self.app.get_recipe(calc.recipe_name)
        if not recipe or recipe.calctype.upper() != calctype:
            return False
        return self._own_state(calc)[2]  # done

    def _open_live(self, calc):
        op = self._out_path(calc)
        if op:
            LivePlotWindow(self, "{} (job {})".format(self._short(calc), calc.job_id), op)

    def _plot_ir(self, calcs):
        # Accepts one or more finished FREQ calcs; stacks them as colour-matched
        # traces in a single IR window.
        entries = []
        for c in calcs:
            text = self._read_out(c)
            if text is None:
                continue
            ir = orca_parser.parse_ir(text)
            if not ir:
                continue
            mol = self.app.project.molecule_by_filename(c.molecule_filename)
            entries.append({
                "name": "{} / {}".format(c.molecule_filename, c.recipe_name),
                "smiles": mol.smiles if mol else None,
                "centers": [row["freq_cm"] for row in ir],
                "intensities": [row["intensity_km_mol"] for row in ir],
                "freqs": orca_parser.parse_frequencies(text),
            })
        if not entries:
            messagebox.showinfo("No IR data",
                                "No IR spectrum found in the selected calculation(s) — are they Freq runs?")
            return
        from orca_workbench.ui.spectra import IRSpectrumWindow
        title = (entries[0]["name"].split(" / ")[0] if len(entries) == 1
                 else "{} molecules".format(len(entries)))
        IRSpectrumWindow(self, title, entries)

    def _plot_uvvis(self, calcs):
        # One or more finished TD-DFT calcs; stacks them as colour-matched traces.
        entries = []
        for c in calcs:
            text = self._read_out(c)
            if text is None:
                continue
            states = orca_parser.parse_absorption_spectrum(text)
            if not states:
                continue
            mol = self.app.project.molecule_by_filename(c.molecule_filename)
            entries.append({
                "name": "{} / {}".format(c.molecule_filename, c.recipe_name),
                "smiles": mol.smiles if mol else None,
                "states": states,
            })
        if not entries:
            messagebox.showinfo("No UV-Vis data",
                                "No TD-DFT absorption spectrum found in the selected calculation(s) "
                                "— are they TD-DFT runs (a %tddft block)?")
            return
        from orca_workbench.ui.spectra import UVVisSpectrumWindow
        title = (entries[0]["name"].split(" / ")[0] if len(entries) == 1
                 else "{} molecules".format(len(entries)))
        UVVisSpectrumWindow(self, title, entries)

    def _epr_entries(self, calcs):
        """[{name, smiles, epr}] for the finished EPR calcs that parsed a g-tensor."""
        entries = []
        for c in calcs:
            text = self._read_out(c)
            epr = orca_parser.parse_epr(text) if text else None
            if not epr or not (epr.get("g_tensor") or {}).get("g_iso"):
                continue
            mol = self.app.project.molecule_by_filename(c.molecule_filename)
            entries.append({
                "name": "{} / {}".format(c.molecule_filename, c.recipe_name),
                "smiles": mol.smiles if mol else None,
                "epr": epr,
            })
        return entries

    def _plot_epr(self, calcs):
        # One or more finished EPR calcs, stacked as colour-matched traces.
        entries = self._epr_entries(calcs)
        if not entries:
            messagebox.showinfo(
                "No EPR data",
                "No EPR g-tensor found in the selected calculation(s) — are they %eprnmr "
                "runs on open-shell (radical) species?")
            return
        from orca_workbench.ui.spectra import EPRSpectrumWindow
        title = (entries[0]["name"].split(" / ")[0] if len(entries) == 1
                 else "{} molecules".format(len(entries)))
        EPRSpectrumWindow(self, title, entries)

    def _plot_endor(self, calcs):
        # ENDOR from the SAME hyperfine data (no new calc); needs resolvable couplings.
        entries = [e for e in self._epr_entries(calcs)
                   if any(abs(h.get("A_iso") or 0.0) > 1.0
                          for h in (e["epr"].get("hyperfine") or []))]
        if not entries:
            messagebox.showinfo(
                "No ENDOR data",
                "No resolvable hyperfine couplings in the selected calculation(s). ENDOR "
                "needs computed A-tensors — use the B3LYP EPR recipe (a minimal basis like "
                "STO-3G gives ~0 hyperfine).")
            return
        from orca_workbench.ui.spectra import ENDORSpectrumWindow
        title = (entries[0]["name"].split(" / ")[0] if len(entries) == 1
                 else "{} molecules".format(len(entries)))
        ENDORSpectrumWindow(self, title, entries)

    def _plot_nmr(self, calcs):
        from orca_workbench.ui.spectra import NMROptionsDialog, NMRSpectrumWindow
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


class _ChooseOneDialog(tk.Toplevel):
    """Pick one item from a labelled list. `items` is a list of (label, value);
    self.result is the chosen value, or None if cancelled."""

    def __init__(self, parent, title, prompt, items, ok_label="OK"):
        super().__init__(parent)
        self.result = None
        self._values = [v for _lbl, v in items]
        self.title(title)
        ttk.Label(self, text=prompt, justify=tk.LEFT).pack(
            side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))
        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12)
        self.lb = tk.Listbox(frame, height=min(14, max(3, len(items))), width=56)
        for lbl, _v in items:
            self.lb.insert(tk.END, lbl)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lb.selection_set(0)
        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text=ok_label, command=self._ok).pack(side=tk.RIGHT, padx=4)
        self.lb.bind("<Double-Button-1>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        make_modal(self, parent)
        self.wait_window()

    def _ok(self):
        sel = self.lb.curselection()
        if not sel:
            return
        self.result = self._values[sel[0]]
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
