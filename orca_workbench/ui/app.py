"""Main application window for ORCA Workbench.

Holds the current Project object and the recipe library, owns the four tabs,
and provides File menu operations (New / Open / Save / Save As) plus a status
bar at the bottom.
"""

import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from orca_workbench import __version__
from orca_workbench.core import config as config_mod
from orca_workbench.core import diagnostics as diag
from orca_workbench.core import features
from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core.inputs import Recipe
from orca_workbench.core.project import Project, load_project, save_project
from orca_workbench.ui import extprog
from orca_workbench.ui import tooltip as tooltip_mod
from orca_workbench.ui.modal import make_modal
from orca_workbench.ui.shortcuts import install_global_text_shortcuts
from orca_workbench.ui.tooltip import tip


DEFAULT_RECIPE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "recipes",
)

# Portable stand-in for the packaged default recipe folder inside a project's
# recipe_dirs list. Its absolute path is machine-specific (it lives inside the
# installed package), so we persist this sentinel instead and resolve it to
# DEFAULT_RECIPE_DIR at load time — keeping project.json shareable.
DEFAULT_RECIPE_SENTINEL = "<builtin>"

# Auto-created scratch project for a session started without a project file, so
# unsaved work (and a freshly imported orphan project) is never lost to a crash
# or an accidental close. Lives in the launch directory; superseded by Save As.
TEMPSAVE_NAME = "tempsave.json"


def _maximize(root):
    # type: (tk.Tk) -> None
    """Maximise the main window, trying the most reliable mechanism per platform
    and degrading gracefully (the geometry() fallback in __init__ stays if all fail)."""
    try:
        root.state("zoomed")           # Windows / some Linux WMs
        return
    except tk.TclError:
        pass
    try:
        root.attributes("-zoomed", True)   # many X11 window managers
        return
    except tk.TclError:
        pass
    try:
        root.update_idletasks()
        root.geometry("{}x{}+0+0".format(root.winfo_screenwidth(), root.winfo_screenheight()))
    except tk.TclError:
        pass


class App(object):
    def __init__(self, root, project_path=None):
        # type: (tk.Tk, Optional[str]) -> None
        self.root = root
        diag.attach_root(root)
        root.title("ORCA Workbench")
        root.geometry("1100x720")          # fallback size if maximising fails
        _maximize(root)

        self.project = Project()
        self.recipes = []  # type: List[Recipe]
        self.recipe_dir = DEFAULT_RECIPE_DIR          # primary dir (where new recipes save)
        self.recipe_dirs = [DEFAULT_RECIPE_DIR]       # ordered, resolved dirs the tab shows
        self._dirty = False
        # Email lives in per-user config (~/.orca_workbench.json), NOT in project
        # files — so a shared/published project never carries someone's address.
        self.usermail = config_mod.get("usermail", "") or ""
        # Autosave preference (per-user). Debounced; only fires once a project
        # has been given a path via Save/Save As.
        self.autosave_enabled = bool(config_mod.get("autosave", True))
        self._autosave_after_id = None
        # Tooltips on/off (per-user). Apply before any tab builds its tips.
        # Simple/gateway mode forces them off — hover tooltips spawn a Toplevel
        # (extra X round-trips), which we avoid on a high-latency link.
        tooltip_mod.set_enabled(bool(config_mod.get("tooltips", True))
                                and not features.is_simple())

        with diag.timed("startup:total"):
            self._build_menu()
            with diag.timed("startup:build_layout"):
                self._build_layout()
            # App-wide Ctrl+A (select all) in every entry/text widget.
            install_global_text_shortcuts(self.root)
            self.root.bind_all("<F5>", lambda e: self._on_f5())
            with diag.timed("startup:reload_recipes"):
                self.reload_recipes()
            with diag.timed("startup:refresh_all_tabs"):
                self.refresh_all_tabs()
            self._update_title()
        # Now that the UI exists, snapshot display/latency info for the log.
        diag.capture_environment()
        if diag.is_enabled():
            diag.set_status_callback(self.set_status)

        if project_path:
            self._open_project_path(project_path)
        else:
            self._init_tempsave()

        # Claim the keyboard at launch. On some window managers (and over
        # ThinLinc/X) a freshly-mapped maximized window — especially after a
        # startup messagebox like the tempsave-resume prompt — has mouse focus
        # but NOT keyboard focus, so arrow / Shift+arrow keys don't reach the
        # tables until the user alt-tabs out and back. Forcing focus once after
        # the window is mapped fixes that without the alt-tab dance.
        self.root.after(80, self._grab_initial_focus)

    def _grab_initial_focus(self):
        try:
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _init_tempsave(self):
        """No project file was opened: bind the session to an autosaving
        tempsave.json in the launch dir so unsaved work survives a crash/close.
        If one already exists (a prior unsaved session), offer to resume it."""
        path = os.path.abspath(os.path.join(os.getcwd(), TEMPSAVE_NAME))
        if os.path.isfile(path):
            if messagebox.askyesno(
                    "Resume previous session?",
                    "An unsaved session was found in this folder:\n{}\n\n"
                    "Resume it?\n\n(No = start a new project; this file will be "
                    "overwritten.)".format(path)):
                self._open_project_path(path)
                return
        self.project = Project(path=path)
        try:
            save_project(self.project)
        except Exception as e:
            self.set_status("Could not create {}: {}".format(TEMPSAVE_NAME, e))
            return
        self.refresh_all_tabs()
        self.mark_clean()
        self.set_status("Autosaving to {} - use File > Save As for a permanent "
                        "project.".format(TEMPSAVE_NAME))

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="New project", command=self.on_new, accelerator="Ctrl+N")
        filemenu.add_command(label="Open project...", command=self.on_open, accelerator="Ctrl+O")
        filemenu.add_command(label="Save", command=self.on_save, accelerator="Ctrl+S")
        filemenu.add_command(label="Save as...", command=self.on_save_as)
        filemenu.add_separator()
        self.autosave_var = tk.BooleanVar(value=self.autosave_enabled)
        filemenu.add_checkbutton(label="Autosave", variable=self.autosave_var,
                                 command=self._on_autosave_toggle)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.on_quit)
        menubar.add_cascade(label="File", menu=filemenu)

        recipemenu = tk.Menu(menubar, tearoff=0)
        recipemenu.add_command(label="Reload recipes", command=self.reload_recipes)
        recipemenu.add_command(label="Add recipe directory...", command=self.on_add_recipe_dir)
        recipemenu.add_command(label="Manage recipe directories...",
                               command=self.on_manage_recipe_dirs)
        menubar.add_cascade(label="Recipes", menu=recipemenu)

        # Settings — explicit places to set (and fix) external-program paths.
        setmenu = tk.Menu(menubar, tearoff=0)
        setmenu.add_command(label="Avogadro path...",
                            command=lambda: self._set_program("avogadro_path", "Avogadro",
                                "Path/command for Avogadro, used to open molecules on THIS machine. "
                                "On the gateway, leave blank and use molden instead."))
        setmenu.add_command(label="ORCA executable...",
                            command=lambda: self._set_program("orca_path", "the ORCA executable",
                                "Path to orca / orca.exe — used by 'Run locally' to run jobs on this machine."))
        setmenu.add_command(label="Text editor (for recipes)...",
                            command=lambda: self._set_program("text_editor_path", "your text editor",
                                "Program to open a recipe's JSON when you double-click it. A GUI "
                                "editor (Notepad++, Sublime, gedit, …); terminal editors won't work."))
        setmenu.add_command(label="molden module name...", command=self._set_molden_module)
        setmenu.add_command(label="SLURM submission delay...", command=self._set_submit_delay)
        setmenu.add_command(label="SLURM submit script (template)...",
                            command=self.on_edit_slurm_template)
        menubar.add_cascade(label="Settings", menu=setmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Node graphs...", command=self.on_help_nodegraph)
        helpmenu.add_separator()
        helpmenu.add_command(label="Check coordinate backends...", command=self.on_diagnose)
        if diag.is_enabled():
            # Only shown in --diagnose mode: dump a perf log without quitting.
            helpmenu.add_command(label="Save diagnostics log now...", command=self._on_save_diag_log)
        helpmenu.add_separator()
        helpmenu.add_command(label="About", command=self.on_about)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)
        self.root.bind_all("<Control-n>", lambda e: self.on_new())
        self.root.bind_all("<Control-o>", lambda e: self.on_open())
        self.root.bind_all("<Control-s>", lambda e: self.on_save())
        self.root.bind_all("<Control-Shift-N>", lambda e: self._add_by_name_shortcut())
        # Ctrl+Shift+O = import structure files (plain Ctrl+O is Open project).
        self.root.bind_all("<Control-Shift-O>", lambda e: self._import_files_shortcut())
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)

    def _build_layout(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        email_label = ttk.Label(toolbar, text="User email (SLURM --mail-user):")
        email_label.pack(side=tk.LEFT, padx=(8, 4), pady=4)
        self.email_var = tk.StringVar(value=self.usermail)
        email_entry = ttk.Entry(toolbar, textvariable=self.email_var, width=40)
        email_entry.pack(side=tk.LEFT, padx=4, pady=4)
        self.email_var.trace_add("write", lambda *_: self._on_email_change())
        _email_tip = ("Your email address for #SBATCH --mail-user (SLURM emails you on job "
                      "start/end/fail). Stored in your per-user config (~/.orca_workbench.json), "
                      "NOT in the project file — so sharing or publishing a project never leaks "
                      "an email address. Leave blank for no email.")
        tip(email_label, _email_tip)
        tip(email_entry, _email_tip)

        # Tooltips on/off, far right on the same row as the email box.
        self.tooltips_var = tk.BooleanVar(value=tooltip_mod.is_enabled())
        tt_cb = ttk.Checkbutton(toolbar, text="Show tooltips", variable=self.tooltips_var,
                                command=self._on_tooltips_toggle)
        tt_cb.pack(side=tk.RIGHT, padx=(4, 10), pady=4)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        # Make the main tab labels larger/bolder — they're the primary navigation
        # and otherwise look small next to the in-tab buttons.
        try:
            import tkinter.font as tkfont
            base = tkfont.nametofont("TkDefaultFont").actual("size")
            ttk.Style(self.root).configure(
                "TNotebook.Tab", padding=[14, 7],
                font=("TkDefaultFont", abs(base) + 3, "bold"))
        except Exception:
            pass

        from orca_workbench.ui.molecules_tab import MoleculesTab

        # Tab attributes default to None; each becomes a real widget when built —
        # eagerly below, or lazily on first selection (see _materialize_lazy_tab).
        self.molecules_tab = None     # type: Optional[ttk.Frame]
        self.recipes_tab = None       # type: Optional[ttk.Frame]
        self.calculations_tab = None  # type: Optional[ttk.Frame]
        self.report_tab = None        # type: Optional[ttk.Frame]
        self.workflow_tab = None      # type: Optional[ttk.Frame]
        # placeholder -> spec to materialise the real tab on first selection.
        self._lazy_tabs = {}  # type: dict

        # Molecules is the default/active tab, so it's always built eagerly.
        with diag.timed("build:molecules"):
            self.molecules_tab = MoleculesTab(self.notebook, self)
        self.notebook.add(self.molecules_tab, text="Molecules")

        if features.is_simple():
            # Gateway/--simple mode: defer the rest of the pipeline tabs (built on
            # first click) and skip the heavy Workflow tool entirely.
            # Over a high-latency X link this cuts the cold start to one tab's
            # worth of widgets. The default (full) path below is unchanged.
            self._add_lazy_tab("recipes_tab", "Recipes", None,
                               "orca_workbench.ui.recipes_tab", "RecipesTab")
            self._add_lazy_tab("calculations_tab", "Calculations", None,
                               "orca_workbench.ui.calculations_tab", "CalculationsTab")
            self._add_lazy_tab("report_tab", "Report", None,
                               "orca_workbench.ui.report_tab", "ReportTab")
        else:
            from orca_workbench.ui.recipes_tab import RecipesTab
            from orca_workbench.ui.calculations_tab import CalculationsTab
            from orca_workbench.ui.report_tab import ReportTab
            with diag.timed("build:recipes"):
                self.recipes_tab = RecipesTab(self.notebook, self)
            with diag.timed("build:calculations"):
                self.calculations_tab = CalculationsTab(self.notebook, self)
            with diag.timed("build:report"):
                self.report_tab = ReportTab(self.notebook, self)
            self.notebook.add(self.recipes_tab, text="Recipes")
            self.notebook.add(self.calculations_tab, text="Calculations")
            self.notebook.add(self.report_tab, text="Report")
            # ...then the special, colour-swatched tools at the end (outside the
            # normal chronology), built lazily on first selection. ttk tabs can't
            # take a background colour directly, so a small colour-block image is
            # the reliable cross-theme way to mark them.
            self._wf_swatch = tk.PhotoImage(width=13, height=13)
            self._wf_swatch.put("#9b7ad6", to=(0, 0, 13, 13))
            self._add_lazy_tab("workflow_tab", " Workflow", self._wf_swatch,
                               "orca_workbench.ui.workflow_tab", "WorkflowTab")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, relief=tk.SUNKEN)
        status.pack(side=tk.BOTTOM, fill=tk.X)

    def _add_lazy_tab(self, attr, text, image, module_name, class_name):
        # type: (str, str, tk.PhotoImage, str, str) -> None
        """Add a permanent placeholder Frame to the notebook; the real tab is
        built *inside* this placeholder on first selection (see
        _materialize_lazy_tab). Cuts startup widget churn — especially over
        X-forwarded Tk where every widget is a round-trip.

        The placeholder stays the notebook tab for the app's whole life; we only
        ever pack the real tab into it. We deliberately do NOT forget/insert
        notebook tabs at selection time: forgetting the currently-selected tab
        makes ttk auto-select a neighbour, which re-fires <<NotebookTabChanged>>
        re-entrantly and corrupts the tab list (the cause of the earlier
        'Slave index out of bounds' crash that made these tabs vanish)."""
        placeholder = ttk.Frame(self.notebook)
        if image is not None:
            self.notebook.add(placeholder, text=text, image=image, compound="left")
        else:
            self.notebook.add(placeholder, text=text)
        self._lazy_tabs[str(placeholder)] = {
            "attr": attr, "module": module_name, "class": class_name,
            "placeholder": placeholder, "real": None,
        }

    def _ensure_tab(self, attr):
        # type: (str) -> Optional[ttk.Frame]
        """Return the real tab for `attr`, building it now if it's still a lazy
        placeholder (simple mode). Does NOT change the selected tab — used by code
        paths that need a tab object without the user having opened it (F5,
        reconcile-after-load)."""
        existing = getattr(self, attr, None)
        if existing is not None:
            return existing
        for path, spec in self._lazy_tabs.items():
            if spec["attr"] == attr:
                return self._materialize_lazy_tab(path)
        return None

    def _add_by_name_shortcut(self):
        """Ctrl+Shift+N: jump to the Molecules tab and open 'Add by name'."""
        tab = self._ensure_tab("molecules_tab")
        if tab is None:
            return
        try:
            self.notebook.select(tab)
        except Exception:
            pass
        if hasattr(tab, "on_add_by_name"):
            tab.on_add_by_name()

    def _import_files_shortcut(self):
        """Ctrl+I: jump to the Molecules tab and open the Import files dialog."""
        tab = self._ensure_tab("molecules_tab")
        if tab is None:
            return
        try:
            self.notebook.select(tab)
        except Exception:
            pass
        if hasattr(tab, "on_import_files"):
            tab.on_import_files()

    def _select_tab(self, attr):
        # type: (str) -> Optional[ttk.Frame]
        """Switch the notebook to the tab for `attr`, materialising it if lazy.
        For a lazy tab the notebook pane is the placeholder, so we select that
        (which fires <<NotebookTabChanged>> and builds the real tab)."""
        for spec in self._lazy_tabs.values():
            if spec["attr"] == attr:
                self.notebook.select(spec["placeholder"])
                return getattr(self, attr, None)
        w = getattr(self, attr, None)
        if w is not None:
            self.notebook.select(w)
        return w

    def _materialize_lazy_tab(self, placeholder_path):
        # type: (str) -> Optional[ttk.Frame]
        """Build (once) the real tab inside its placeholder and return it.
        Idempotent: subsequent calls return the already-built tab."""
        spec = self._lazy_tabs.get(placeholder_path)
        if spec is None:
            return None
        if spec["real"] is not None:
            return spec["real"]
        import importlib
        cls = getattr(importlib.import_module(spec["module"]), spec["class"])
        with diag.timed("build:" + spec["attr"]):
            real = cls(spec["placeholder"], self)   # parented INSIDE the placeholder
            real.pack(fill=tk.BOTH, expand=True)
        spec["real"] = real
        setattr(self, spec["attr"], real)        # e.g. self.workflow_tab -> real
        return real

    def _on_tab_changed(self, _event):
        sel = self.notebook.select()
        try:
            name = self.notebook.tab(sel, "text").strip() or "?"
        except Exception:
            name = "?"
        with diag.timed("tabswitch:" + name):
            spec = self._lazy_tabs.get(sel)
            if spec is not None:
                # Selecting the placeholder: build the real tab into it on first
                # visit (no tab add/remove, so no re-entrant tab-changed event).
                tab = self._materialize_lazy_tab(sel)
            else:
                tab = self.notebook.nametowidget(sel)
            if tab is not None and hasattr(tab, "refresh"):
                tab.refresh()

    def _on_email_change(self):
        # Email is a per-user setting, persisted to ~/.orca_workbench.json — never
        # the project file. No project dirty flag, no autosave trigger.
        val = self.email_var.get()
        if val != self.usermail:
            self.usermail = val
            config_mod.set_value("usermail", val)

    def _on_f5(self):
        # F5 = refresh job status (the monitoring action). _select_tab builds the
        # Calculations tab first if it's still a lazy placeholder (simple mode).
        self._select_tab("calculations_tab")
        ct = getattr(self, "calculations_tab", None)
        if ct is not None and hasattr(ct, "on_refresh_status"):
            ct.on_refresh_status()

    def _set_program(self, config_key, friendly, description):
        path = extprog.prompt_for_program(self.root, config_key,
                                          "Set {}".format(friendly), description)
        self.set_status("Set {} to: {}".format(friendly, path) if path
                        else "{} unset.".format(friendly))

    def _set_submit_delay(self):
        from tkinter import simpledialog
        cur = config_mod.get("submit_delay_ms", 100)
        try:
            cur = int(cur)
        except (TypeError, ValueError):
            cur = 100
        val = simpledialog.askinteger(
            "SLURM submission delay",
            "Delay between sbatch submissions, in milliseconds.\n\n"
            "Submitting hundreds of jobs back-to-back can trip the scheduler's "
            "submission rate limit and silently drop some. A small delay (≈100 ms) "
            "avoids that. Set 0 to disable (only on a quiet controller).",
            initialvalue=cur, minvalue=0, maxvalue=60000, parent=self.root)
        if val is not None:
            config_mod.set_value("submit_delay_ms", int(val))
            self.set_status("SLURM submission delay set to {} ms.".format(int(val)))

    def on_edit_slurm_template(self):
        """Edit the per-machine SLURM submit-script template (partition, mem,
        walltime, …). Stored in config, applied to every job that's built."""
        from orca_workbench.core import slurm as slurm_mod
        dlg = _SlurmTemplateDialog(self.root, slurm_mod)
        if dlg.saved:
            self.set_status("SLURM submit template updated — applies to newly built jobs.")

    def _set_molden_module(self):
        from tkinter import simpledialog
        cur = config_mod.get("molden_module", "molden") or "molden"
        val = simpledialog.askstring(
            "molden module", "Module name to load for molden on the gateway "
            "(`module load <name>`):", initialvalue=cur, parent=self.root)
        if val is not None:
            config_mod.set_value("molden_module", val.strip() or "molden")
            self.set_status("molden module set to '{}'.".format(val.strip() or "molden"))

    def _on_tooltips_toggle(self):
        enabled = bool(self.tooltips_var.get())
        tooltip_mod.set_enabled(enabled)
        config_mod.set_value("tooltips", enabled)
        self.set_status("Tooltips on." if enabled else "Tooltips off.")

    def _on_autosave_toggle(self):
        self.autosave_enabled = bool(self.autosave_var.get())
        config_mod.set_value("autosave", self.autosave_enabled)
        if self.autosave_enabled:
            self.set_status("Autosave on.")
        else:
            self.set_status("Autosave off — remember to Save (Ctrl+S).")

    def mark_dirty(self):
        self._dirty = True
        self._update_title()
        self._schedule_autosave()

    def mark_clean(self):
        self._dirty = False
        self._update_title()

    def _schedule_autosave(self):
        """Debounced autosave: only for a project that already has a path, and
        only when enabled. Coalesces rapid edits into one write ~1.5s later."""
        if not self.autosave_enabled or not self.project.path:
            return
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.root.after(1500, self._do_autosave)

    def _do_autosave(self):
        self._autosave_after_id = None
        if not self.autosave_enabled or not self.project.path or not self._dirty:
            return
        try:
            save_project(self.project)
        except Exception as e:
            self.set_status("Autosave failed: {}".format(e))
            return
        self.mark_clean()
        self.set_status("Autosaved {} at {}".format(
            os.path.basename(self.project.path), time.strftime("%H:%M:%S")))

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "(unsaved project)"
        star = "*" if self._dirty else ""
        mode = "  [simple mode]" if features.is_simple() else ""
        self.root.title("ORCA Workbench {} - {}{}{}".format(__version__, name, star, mode))

    def set_status(self, msg):
        # type: (str) -> None
        self.status_var.set(msg)

    def refresh_all_tabs(self):
        for tab_name in ("molecules_tab", "recipes_tab", "workflow_tab",
                         "calculations_tab", "report_tab"):
            tab = getattr(self, tab_name, None)
            if tab is not None and hasattr(tab, "refresh"):
                tab.refresh()

    def _resolve_recipe_dir(self, d):
        # type: (str) -> str
        """Resolve one recipe_dirs entry to an absolute path: the <builtin>
        sentinel → the packaged default; a relative path → vs the project root;
        an absolute path is returned as-is."""
        if d == DEFAULT_RECIPE_SENTINEL:
            return DEFAULT_RECIPE_DIR
        if d and not os.path.isabs(d) and self.project.path:
            return os.path.join(self.project.root(), d)
        return d

    def reload_recipes(self):
        # Resolve the ordered list of recipe folders (the project's list, or just
        # the built-in default when none are set). The first folder is the
        # primary — where New/Duplicate save new recipes.
        raw = list(self.project.recipe_dirs) or [DEFAULT_RECIPE_SENTINEL]
        resolved = []
        for d in raw:
            rd = self._resolve_recipe_dir(d)
            if rd and rd not in resolved:
                resolved.append(rd)
        self.recipe_dirs = resolved
        self.recipe_dir = resolved[0] if resolved else DEFAULT_RECIPE_DIR
        self.recipes = inputs_mod.load_recipes_from_dirs(resolved)
        self.set_status("Loaded {} recipes from {} folder(s)".format(
            len(self.recipes), len(resolved)))
        if hasattr(self, "recipes_tab"):
            self.refresh_all_tabs()

    def get_recipe(self, name):
        # type: (str) -> Optional[Recipe]
        for r in self.recipes:
            if r.name == name:
                return r
        return None

    def on_new(self):
        if not self._confirm_discard():
            return
        with diag.timed("project:new"):
            # Bind the new project to a tempsave in the launch dir so autosave
            # covers it immediately (no "forgot to Save" data loss).
            self.project = Project(path=os.path.abspath(
                os.path.join(os.getcwd(), TEMPSAVE_NAME)))
            try:
                save_project(self.project)
            except Exception:
                pass
            # Email is per-user config, not per-project — leave the field as-is.
            self.reload_recipes()
            self.refresh_all_tabs()
        self.mark_clean()
        self.set_status("New project (autosaving to {}).".format(TEMPSAVE_NAME))

    def on_open(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("Project JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self._open_project_path(path)

    def _open_project_path(self, path):
        # type: (str) -> None
        """Load a project from a path (used by File>Open and the CLI arg)."""
        try:
            self.project = load_project(path)
        except Exception as e:
            messagebox.showerror("Open failed", "Could not open {}:\n{}".format(path, e))
            return
        # One-time migration: if this is an older project that still carries a
        # usermail and the user hasn't set one in config yet, adopt it (then it
        # lives in config from now on, out of project files).
        if getattr(self.project, "usermail", "") and not self.usermail:
            self.usermail = self.project.usermail
            config_mod.set_value("usermail", self.usermail)
            self.email_var.set(self.usermail)
        with diag.timed("project:open"):
            self.reload_recipes()
            self.refresh_all_tabs()
            # Re-evaluate job status so calcs left 'running' at last close are shown
            # as interrupted (local jobs and cluster jobs gone from the queue).
            # _ensure_tab builds the Calculations tab if it's still lazy (simple mode).
            try:
                self._ensure_tab("calculations_tab").reconcile_after_load()
            except Exception:
                pass
            # Refresh the Workflow view too, so node status + per-node result/plot
            # buttons appear straight away when reopening a project (no manual click).
            try:
                wt = getattr(self, "workflow_tab", None)
                if wt is not None:
                    wt.on_refresh_status()
            except Exception:
                pass
        self.mark_clean()
        self.set_status("Opened {}".format(path))

    def on_help_nodegraph(self):
        """Scrollable help for the node-graph editor (the on-canvas line is terse)."""
        win = tk.Toplevel(self.root)
        win.title("Node graphs — help")
        win.geometry("640x580")
        ttk.Label(win, text="Workflow node graph editor",
                  font=("TkDefaultFont", 12, "bold")).pack(anchor=tk.W, padx=14, pady=(12, 2))
        body = ttk.Frame(win)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=(14, 0), pady=4)
        canvas = tk.Canvas(body, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas)
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(wid, width=e.width))
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        def section(t):
            ttk.Label(inner, text=t, font=("TkDefaultFont", 10, "bold")).pack(
                anchor=tk.W, padx=4, pady=(10, 2))

        def bullet(t):
            ttk.Label(inner, text="•  " + t, wraplength=560, justify=tk.LEFT).pack(
                anchor=tk.W, padx=14, pady=1)

        ttk.Label(inner, wraplength=580, justify=tk.LEFT, foreground="#333",
                  text="Build a pipeline by adding nodes and wiring their pins. The graph runs "
                       "chronologically from left to right: each node's output geometry / results "
                       "feed the node(s) it connects to.").pack(anchor=tk.W, padx=4, pady=(4, 2))
        section("Adding & wiring")
        bullet("Add a node from the palette buttons up top, or press F3 (or drag from a pin onto "
               "empty space) for a search popup listing every node type.")
        bullet("Wire by dragging an output pin onto a compatible input pin; drop on empty space to "
               "pick a new node to connect.")
        bullet("J connects two selected nodes.")
        section("Selecting & moving")
        bullet("Click to select, Ctrl+click to multi-select, drag empty space to box-select, "
               "Ctrl+A selects all.")
        bullet("Drag a node to move it. Scroll to zoom; middle- or right-drag to pan; press 0 to "
               "reset the view.")
        section("Tidying the layout (Blueprint-style)")
        bullet("Q straightens the selection so connected nodes line up and their wires run "
               "straight across.")
        bullet("Shift+W / Shift+S align top / bottom edges; Shift+A / Shift+D align left / right "
               "edges.")
        bullet("C frames the selection in a titled box; T drops a comment note (double-click to "
               "edit it, drag its corner to resize).")
        section("Running")
        bullet("Generate only (grey): expand into calculations without launching, to review them "
               "on the Calculations tab first.")
        bullet("Run pipeline (amber): build and launch automatically as each input becomes ready "
               "— keep the app open.")
        bullet("Submit unattended (green): hand the whole pipeline to SLURM as a dependency chain, "
               "then you can close the app.")
        bullet("Select a node first to run only that network. Refresh (blue) re-queries status so "
               "finished nodes light up and expose their plot / viewer buttons.")
        section("Good to know")
        bullet("Once a node has launched calculations, its recipe locks and it can't be deleted "
               "(remove its calcs on the Calculations tab first) — this protects your run record. "
               "Report nodes are exempt: they only aggregate, so they stay editable and re-runnable.")
        section("Feel familiar?")
        ttk.Label(inner, wraplength=580, justify=tk.LEFT, foreground="#555",
                  text="This editor is largely inspired by Unreal Engine 5's Blueprint system and "
                       "by KNIME, so it deliberately shares their feel and several of their "
                       "hotkeys — if you've used either, you should feel right at home.").pack(
                           anchor=tk.W, padx=4, pady=(2, 10))
        ttk.Button(win, text="Close", command=win.destroy).pack(side=tk.BOTTOM, pady=8)

    def on_save(self):
        if not self.project.path:
            return self.on_save_as()
        try:
            save_project(self.project)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return False
        self.mark_clean()
        self.set_status("Saved {}".format(self.project.path))
        return True

    def on_save_as(self):
        prev = self.project.path
        path = filedialog.asksaveasfilename(
            title="Save project as",
            defaultextension=".json",
            filetypes=[("Project JSON", "*.json")],
        )
        if not path:
            return False
        try:
            save_project(self.project, path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return False
        # If we were autosaving to a tempsave.json, it's now superseded — remove it
        # so it doesn't linger and re-prompt on the next launch in this folder.
        if (prev and os.path.basename(prev) == TEMPSAVE_NAME
                and os.path.abspath(prev) != os.path.abspath(path)):
            try:
                os.remove(prev)
            except OSError:
                pass
        self.mark_clean()
        self.set_status("Saved {}".format(path))
        return True

    def on_add_recipe_dir(self):
        """Append a recipe directory to the project (recipes accumulate across
        folders; the tab groups them with dividers)."""
        path = filedialog.askdirectory(title="Add recipe directory")
        if not path:
            return
        dirs = list(self.project.recipe_dirs)
        if not dirs:
            # Seed with the built-in default (portable sentinel) so the new folder
            # is APPENDED to what's already shown rather than replacing it.
            dirs = [DEFAULT_RECIPE_SENTINEL]
        if any(self._resolve_recipe_dir(d) == path for d in dirs):
            self.set_status("That recipe directory is already in the list.")
            return
        dirs.append(path)
        self.project.recipe_dirs = dirs
        self.mark_dirty()
        self.reload_recipes()
        self.set_status("Added recipe directory: {}".format(path))

    def on_manage_recipe_dirs(self):
        """Open the dialog to remove / reorder recipe folders or set the primary."""
        # Ensure there's an explicit list to manage (seed from the current view).
        if not self.project.recipe_dirs:
            self.project.recipe_dirs = [DEFAULT_RECIPE_SENTINEL]
        dlg = _RecipeDirsDialog(self.root, self)
        if dlg.changed:
            self.mark_dirty()
            self.reload_recipes()

    def on_about(self):
        messagebox.showinfo(
            "About ORCA Workbench",
            "ORCA Workbench {}\n\n"
            "Composer for ORCA quantum chemistry calculations on SLURM.\n"
            "Run on the Lido login node, accessed via MobaXterm X-forwarding."
            .format(__version__),
        )

    def on_diagnose(self):
        from orca_workbench.core.coords import diagnose_backends
        text = diagnose_backends()
        top = tk.Toplevel(self.root)
        top.title("Coordinate backends diagnostic")
        top.geometry("700x420")
        top.transient(self.root)
        info = tk.Text(top, wrap="word", font=("Courier", 9))
        info.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=8)
        info.insert("1.0", text)
        info.configure(state=tk.DISABLED)
        btns = ttk.Frame(top)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btns, text="Close", command=top.destroy).pack(side=tk.RIGHT)
        from orca_workbench.ui.modal import fit_to_content
        fit_to_content(top)

    def on_quit(self):
        if not self._confirm_discard():
            return
        if diag.is_enabled():
            # Measure teardown too (the "5 s to quit" symptom is pure widget
            # destruction = X round-trips), then write the log before we exit.
            with diag.timed("quit:destroy"):
                self.root.destroy()
            path = diag.write_log()
            if path:
                sys.stdout.write("\nDiagnostics log written to: {}\n".format(path))
                sys.stdout.flush()
        else:
            self.root.destroy()

    def _on_save_diag_log(self):
        path = diag.write_log()
        self.set_status("Diagnostics log saved: {}".format(path) if path
                        else "Could not write diagnostics log.")

    def _confirm_discard(self):
        # type: () -> bool
        if not self._dirty:
            return True
        result = messagebox.askyesnocancel(
            "Unsaved changes",
            "You have unsaved changes. Save before continuing?",
        )
        if result is None:
            return False
        if result:
            return self.on_save()
        return True


def _enable_windows_dpi_awareness():
    """On Windows, declare the process DPI-aware *before* the Tk window exists.

    Otherwise, on a scaled display (125 % / 150 % …) Windows bitmap-stretches the
    whole window, while matplotlib's TkAgg backend *also* renders the figure at
    the Tk scaling factor — so embedded plots get scaled twice and appear drawn
    at two sizes, one ghosted behind the other. Making the process DPI-aware
    stops the OS double-scaling so the plot is drawn once, crisply. No-op off
    Windows (the cluster's X11 path doesn't have this problem)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # PROCESS_SYSTEM_DPI_AWARE = 1 (enough to stop the double-scaling).
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class _RecipeDirsDialog(tk.Toplevel):
    """Manage the project's ordered recipe folders: remove, reorder, set primary.

    Edits `app.project.recipe_dirs` in place and sets self.changed=True if anything
    changed (the caller then marks dirty + reloads). The <builtin> sentinel shows
    as '(built-in default)'; the top entry is the primary where new recipes save."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.changed = False
        self._dirs = list(app.project.recipe_dirs)
        self.title("Manage recipe directories")

        ttk.Label(self, text="Recipe folders, in order. The top one is the primary "
                  "(new recipes are saved there).",
                  wraplength=440, justify=tk.LEFT).pack(
                      side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=4)
        self.listbox = tk.Listbox(body, height=8, width=64, exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.LEFT, fill=tk.Y)

        side = ttk.Frame(body)
        side.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
        ttk.Button(side, text="Set primary", command=self._set_primary).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Move up", command=lambda: self._move(-1)).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Move down", command=lambda: self._move(1)).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Remove", command=self._remove).pack(fill=tk.X, pady=2)

        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        self._render()
        make_modal(self, parent)
        self.wait_window()

    def _label(self, d):
        return "(built-in default)" if d == DEFAULT_RECIPE_SENTINEL else d

    def _render(self, select=0):
        self.listbox.delete(0, tk.END)
        for i, d in enumerate(self._dirs):
            tag = "  [primary]" if i == 0 else ""
            self.listbox.insert(tk.END, self._label(d) + tag)
        if self._dirs:
            sel = max(0, min(select, len(self._dirs) - 1))
            self.listbox.selection_set(sel)
            self.listbox.activate(sel)

    def _sel(self):
        cur = self.listbox.curselection()
        return cur[0] if cur else None

    def _commit(self, new_sel):
        self.app.project.recipe_dirs = list(self._dirs)
        self.changed = True
        self._render(new_sel)

    def _set_primary(self):
        i = self._sel()
        if i is None or i == 0:
            return
        self._dirs.insert(0, self._dirs.pop(i))
        self._commit(0)

    def _move(self, delta):
        i = self._sel()
        if i is None:
            return
        j = i + delta
        if 0 <= j < len(self._dirs):
            self._dirs[i], self._dirs[j] = self._dirs[j], self._dirs[i]
            self._commit(j)

    def _remove(self):
        i = self._sel()
        if i is None:
            return
        if len(self._dirs) == 1:
            messagebox.showinfo("Keep at least one",
                                "At least one recipe folder must remain.", parent=self)
            return
        del self._dirs[i]
        self._commit(min(i, len(self._dirs) - 1))


class _SlurmTemplateDialog(tk.Toplevel):
    """Edit the SLURM submit-script template. Saved per-machine in the user config
    (never in project files), so changing the partition / memory / walltime here
    applies to every job built afterward. Empty-relative-to-default tracks the
    packaged template, so a later app update's improvements still flow through."""

    def __init__(self, parent, slurm_mod):
        super().__init__(parent)
        self.slurm = slurm_mod
        self.saved = False
        self.title("SLURM submit script template")
        self.geometry("760x560")

        ttk.Label(self, text="The SLURM submit script used for every job. Edit the #SBATCH "
                  "directives — partition, --mem, --time, account, … (e.g. change "
                  "--partition=long to your queue). The !!##CORES##!! / !!##RUNDIR##!! / "
                  "!!##INPFILE##!! / !!##JOBID##!! / !!##USERMAIL##!! / !!##PREAMBLE##!! "
                  "placeholders are filled per job — keep them. Stored per-machine in your "
                  "config, not in project files.", wraplength=720, justify=tk.LEFT,
                  foreground="#444").pack(anchor=tk.W, padx=10, pady=(10, 6))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10)
        self.text = tk.Text(frame, wrap="none", font=("Courier", 10), undo=True)
        ys = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.text.yview)
        xs = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        xs.pack(side=tk.BOTTOM, fill=tk.X)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.insert("1.0", slurm_mod.load_template())

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(bar, text="Reset to packaged default", command=self._reset).pack(side=tk.LEFT)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bar, text="Save", command=self._save).pack(side=tk.RIGHT, padx=4)

        make_modal(self, parent)
        self.wait_window()

    def _reset(self):
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", self.slurm.default_template_text())

    def _save(self):
        txt = self.text.get("1.0", tk.END).rstrip("\n") + "\n"
        missing = self.slurm.missing_required_placeholders(txt)
        if missing and not messagebox.askyesno(
                "Missing placeholders",
                "The template is missing required placeholder(s): {}.\n\nJobs built from it "
                "will be broken. Save anyway?".format(
                    ", ".join("!!##{}##!!".format(m) for m in missing)),
                parent=self):
            return
        # If it matches the packaged default, store nothing so it keeps tracking
        # the package (future updates flow through); otherwise store the override.
        if txt.strip() == self.slurm.default_template_text().strip():
            config_mod.set_value(self.slurm.CONFIG_KEY, "")
        else:
            config_mod.set_value(self.slurm.CONFIG_KEY, txt)
        self.saved = True
        self.destroy()


def _apply_ui_scaling(root):
    """Pick a Tk scaling factor that matplotlib and Tk agree on, so fonts are a
    sensible size and embedded figures render once at the right size."""
    try:
        if sys.platform == "win32":
            # Now that we're DPI-aware, scale by the real display density. An
            # 84-dpi reference sits between "too small" (dpi/96) and the
            # strictly-correct but oversized point size (dpi/72) — comfortable on
            # a 150 % display (~1.7×). Embedded matplotlib figures are decoupled
            # from this (their device pixel ratio is pinned to 1.0), so it only
            # affects widget/font size, not plot rendering.
            dpi = float(root.winfo_fpixels("1i")) or 96.0
            scaling = min(2.1, max(1.0, dpi / 84.0))
            root.tk.call("tk", "scaling", scaling)
        else:
            root.tk.call("tk", "scaling", 1.2)
        # Keep Treeview rows tall enough for the (now scaled) font.
        import tkinter.font as tkfont
        fnt = tkfont.nametofont("TkDefaultFont")
        ttk.Style(root).configure("Treeview",
                                  rowheight=int(fnt.metrics("linespace") * 1.35) + 2)
    except tk.TclError:
        pass


def main(project_path=None):
    # type: (Optional[str]) -> None
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    _apply_ui_scaling(root)
    App(root, project_path=project_path)
    root.mainloop()


if __name__ == "__main__":
    main()
