"""Main application window for ORCA Studio.

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

from orca_studio import __version__
from orca_studio.core import config as config_mod
from orca_studio.core import inputs as inputs_mod
from orca_studio.core.inputs import Recipe
from orca_studio.core.project import Project, load_project, save_project
from orca_studio.ui import extprog
from orca_studio.ui import tooltip as tooltip_mod
from orca_studio.ui.shortcuts import install_global_text_shortcuts
from orca_studio.ui.tooltip import tip


DEFAULT_RECIPE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "recipes",
)


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
        root.title("ORCA Studio")
        root.geometry("1100x720")          # fallback size if maximising fails
        _maximize(root)

        self.project = Project()
        self.recipes = []  # type: List[Recipe]
        self.recipe_dir = DEFAULT_RECIPE_DIR
        self._dirty = False
        # Email lives in per-user config (~/.orca_studio.json), NOT in project
        # files — so a shared/published project never carries someone's address.
        self.usermail = config_mod.get("usermail", "") or ""
        # Autosave preference (per-user). Debounced; only fires once a project
        # has been given a path via Save/Save As.
        self.autosave_enabled = bool(config_mod.get("autosave", True))
        self._autosave_after_id = None
        # Tooltips on/off (per-user). Apply before any tab builds its tips.
        tooltip_mod.set_enabled(bool(config_mod.get("tooltips", True)))

        self._build_menu()
        self._build_layout()
        # App-wide Ctrl+A (select all) in every entry/text widget.
        install_global_text_shortcuts(self.root)
        self.root.bind_all("<F5>", lambda e: self._on_f5())
        self.reload_recipes()
        self.refresh_all_tabs()
        self._update_title()

        if project_path:
            self._open_project_path(project_path)

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
        recipemenu.add_command(label="Set recipe directory...", command=self.on_set_recipe_dir)
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
        menubar.add_cascade(label="Settings", menu=setmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Check coordinate backends...", command=self.on_diagnose)
        helpmenu.add_separator()
        helpmenu.add_command(label="About", command=self.on_about)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)
        self.root.bind_all("<Control-n>", lambda e: self.on_new())
        self.root.bind_all("<Control-o>", lambda e: self.on_open())
        self.root.bind_all("<Control-s>", lambda e: self.on_save())
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
                      "start/end/fail). Stored in your per-user config (~/.orca_studio.json), "
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

        from orca_studio.ui.molecules_tab import MoleculesTab
        from orca_studio.ui.recipes_tab import RecipesTab
        from orca_studio.ui.benchmark_tab import BenchmarkTab
        from orca_studio.ui.workflow_tab import WorkflowTab
        from orca_studio.ui.calculations_tab import CalculationsTab
        from orca_studio.ui.report_tab import ReportTab

        self.molecules_tab = MoleculesTab(self.notebook, self)
        self.recipes_tab = RecipesTab(self.notebook, self)
        self.calculations_tab = CalculationsTab(self.notebook, self)
        self.report_tab = ReportTab(self.notebook, self)
        self.benchmark_tab = BenchmarkTab(self.notebook, self)
        self.workflow_tab = WorkflowTab(self.notebook, self)

        # The four pipeline tabs in chronological order...
        self.notebook.add(self.molecules_tab, text="Molecules")
        self.notebook.add(self.recipes_tab, text="Recipes")
        self.notebook.add(self.calculations_tab, text="Calculations")
        self.notebook.add(self.report_tab, text="Report")
        # ...then the special, colour-swatched tools at the end (outside the
        # normal chronology). ttk tabs can't take a background colour directly,
        # so a small colour-block image is the reliable cross-theme way to mark
        # them.
        self._bench_swatch = tk.PhotoImage(width=13, height=13)
        self._bench_swatch.put("#7aa8d6", to=(0, 0, 13, 13))
        self.notebook.add(self.benchmark_tab, text=" Benchmark",
                          image=self._bench_swatch, compound="left")
        self._wf_swatch = tk.PhotoImage(width=13, height=13)
        self._wf_swatch.put("#9b7ad6", to=(0, 0, 13, 13))
        self.notebook.add(self.workflow_tab, text=" Workflow",
                          image=self._wf_swatch, compound="left")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, relief=tk.SUNKEN)
        status.pack(side=tk.BOTTOM, fill=tk.X)

    def _on_tab_changed(self, _event):
        idx = self.notebook.index(self.notebook.select())
        tab = self.notebook.nametowidget(self.notebook.select())
        if hasattr(tab, "refresh"):
            tab.refresh()
        _ = idx

    def _on_email_change(self):
        # Email is a per-user setting, persisted to ~/.orca_studio.json — never
        # the project file. No project dirty flag, no autosave trigger.
        val = self.email_var.get()
        if val != self.usermail:
            self.usermail = val
            config_mod.set_value("usermail", val)

    def _on_f5(self):
        # F5 = refresh job status (the monitoring action).
        ct = getattr(self, "calculations_tab", None)
        if ct is not None and hasattr(ct, "on_refresh_status"):
            self.notebook.select(self.calculations_tab)
            ct.on_refresh_status()

    def _set_program(self, config_key, friendly, description):
        path = extprog.prompt_for_program(self.root, config_key,
                                          "Set {}".format(friendly), description)
        self.set_status("Set {} to: {}".format(friendly, path) if path
                        else "{} unset.".format(friendly))

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
        self.root.title("ORCA Studio {} - {}{}".format(__version__, name, star))

    def set_status(self, msg):
        # type: (str) -> None
        self.status_var.set(msg)

    def refresh_all_tabs(self):
        for tab_name in ("molecules_tab", "recipes_tab", "benchmark_tab", "workflow_tab",
                         "calculations_tab", "report_tab"):
            tab = getattr(self, tab_name, None)
            if tab is not None and hasattr(tab, "refresh"):
                tab.refresh()

    def reload_recipes(self):
        rdir = self.project.recipe_dir or self.recipe_dir
        if not os.path.isabs(rdir) and self.project.path:
            rdir = os.path.join(self.project.root(), rdir)
        self.recipe_dir = rdir
        self.recipes = inputs_mod.load_recipes_from_dir(rdir)
        self.set_status("Loaded {} recipes from {}".format(len(self.recipes), rdir))
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
        self.project = Project()
        # Email is per-user config, not per-project — leave the field as-is.
        self.reload_recipes()
        self.refresh_all_tabs()
        self.mark_clean()
        self.set_status("New empty project.")

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
        self.reload_recipes()
        self.refresh_all_tabs()
        # Re-evaluate job status so calcs left 'running' at last close are shown
        # as interrupted (local jobs and cluster jobs gone from the queue).
        try:
            self.calculations_tab.reconcile_after_load()
        except Exception:
            pass
        self.mark_clean()
        self.set_status("Opened {}".format(path))

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
        self.mark_clean()
        self.set_status("Saved {}".format(path))
        return True

    def on_set_recipe_dir(self):
        path = filedialog.askdirectory(title="Choose recipe directory")
        if not path:
            return
        self.project.recipe_dir = path
        self.mark_dirty()
        self.reload_recipes()

    def on_about(self):
        messagebox.showinfo(
            "About ORCA Studio",
            "ORCA Studio {}\n\n"
            "Composer for ORCA quantum chemistry calculations on SLURM.\n"
            "Run on the Lido login node, accessed via MobaXterm X-forwarding."
            .format(__version__),
        )

    def on_diagnose(self):
        from orca_studio.core.coords import diagnose_backends
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

    def on_quit(self):
        if self._confirm_discard():
            self.root.destroy()

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
