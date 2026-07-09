"""Archive Export dialog (File > Archive Export...).

Bundles the whole project — project.json, the calcs/ run tree, XYZ_INI/,
TRANSFORM/ (materialised geometries), and an optional freshly-generated
FIGS_EXPORT/ folder of plots — into one tar/zip for hand-off or backup. The heavy
lifting is pure (core/archive + core/figures); this is just the options form and a
live log. It runs on the main thread (the app deliberately avoids UI threads —
Tkinter isn't thread-safe), pumping update_idletasks so the log streams.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from orca_workbench.core import archive as archive_mod
from orca_workbench.ui import extprog
from orca_workbench.ui.modal import make_modal

_IMG_FORMATS = ["svg", "png", "pdf", "jpg", "tiff"]
_ARCHIVE_FORMATS = ["tar.gz", "zip", "tar", "tar.bz2", "tar.xz", "7z", "rar"]


class ArchiveExportDialog(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Archive Export")
        self.resizable(False, False)

        proj = app.project
        if not proj.path or not os.path.isfile(proj.path):
            self.after(10, self._need_save)
            return

        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        ttk.Label(frm, text="Bundle this project (project.json + calcs/ + XYZ_INI/ + "
                  "TRANSFORM/ + optional FIGS_EXPORT/) into one archive.",
                  wraplength=440, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8))

        self.include_figs = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Generate plots into FIGS_EXPORT/ and include them",
                        variable=self.include_figs,
                        command=self._sync_enabled).pack(anchor=tk.W)

        self.exclude_heavy = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Exclude wavefunction & scratch files (.gbw, scratch) "
                        "- much smaller & faster",
                        variable=self.exclude_heavy).pack(anchor=tk.W)

        grid = ttk.Frame(frm)
        grid.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(grid, text="Plot image format:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.img_fmt = tk.StringVar(value="svg")
        self.img_combo = ttk.Combobox(grid, textvariable=self.img_fmt, state="readonly",
                                      width=10, values=_IMG_FORMATS)
        self.img_combo.grid(row=0, column=1, sticky=tk.W, padx=6)

        ttk.Label(grid, text="Stack offset (frac of max):").grid(row=1, column=0,
                                                                 sticky=tk.W, pady=2)
        self.offset = tk.StringVar(value="0.5")
        self.offset_entry = ttk.Entry(grid, textvariable=self.offset, width=8)
        self.offset_entry.grid(row=1, column=1, sticky=tk.W, padx=6)
        ttk.Label(grid, text="(stacked versions of multi-molecule plots)",
                  foreground="#777").grid(row=1, column=2, sticky=tk.W)

        ttk.Label(grid, text="Archive format:").grid(row=2, column=0, sticky=tk.W, pady=(8, 2))
        self.arc_fmt = tk.StringVar(value="tar.gz")
        arc_combo = ttk.Combobox(grid, textvariable=self.arc_fmt, state="readonly",
                                 width=10, values=_ARCHIVE_FORMATS)
        arc_combo.grid(row=2, column=1, sticky=tk.W, padx=6, pady=(8, 2))
        arc_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_enabled())
        self.ext_note = ttk.Label(grid, text="", foreground="#a60", wraplength=250,
                                  justify=tk.LEFT)
        self.ext_note.grid(row=2, column=2, sticky=tk.W, pady=(8, 2))

        ttk.Label(grid, text="Compression:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.compression = tk.StringVar(value="Normal")
        ttk.Combobox(grid, textvariable=self.compression, state="readonly", width=10,
                     values=["Normal", "Fast", "Store"]).grid(row=3, column=1,
                                                              sticky=tk.W, padx=6, pady=2)
        ttk.Label(grid, text="(Fast/Store = quicker, larger)",
                  foreground="#777").grid(row=3, column=2, sticky=tk.W)

        self.log = tk.Text(frm, height=9, width=64, state=tk.DISABLED, wrap=tk.NONE,
                           font=("Courier", 9))
        self.log.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=12, pady=(0, 10))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=3)
        self.export_btn = ttk.Button(bar, text="Export...", command=self._run)
        self.export_btn.pack(side=tk.RIGHT, padx=3)

        self._sync_enabled()
        make_modal(self, parent)

    # ------------------------------------------------------------------ helpers
    def _need_save(self):
        messagebox.showinfo("Save first",
                            "Save the project (File > Save) before exporting — the archive "
                            "bundles the project.json and its folders on disk.", parent=self)
        self.destroy()

    def _sync_enabled(self):
        state = tk.NORMAL if self.include_figs.get() else tk.DISABLED
        for w in (self.img_combo, self.offset_entry):
            w.configure(state="readonly" if (w is self.img_combo and state == tk.NORMAL)
                        else state)
        needs = archive_mod.format_needs_external(self.arc_fmt.get())
        self.ext_note.configure(
            text=("needs an external archiver (7-Zip/WinRAR)" if needs else "built-in, no tool needed"))

    def _append(self, msg):
        try:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, msg + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
            self.update_idletasks()
        except tk.TclError:
            pass

    def _run(self):
        proj = self.app.project
        fmt = self.arc_fmt.get()
        try:
            offset = max(0.0, float(self.offset.get()))
        except ValueError:
            messagebox.showerror("Archive Export", "Stack offset must be a number.",
                                 parent=self)
            return

        external_tool = None
        if archive_mod.format_needs_external(fmt):
            external_tool = extprog.program_path("archiver_path")
            if not extprog._is_usable(external_tool):
                if messagebox.askyesno(
                        "Archiver needed",
                        "The '{}' format needs an external archiver (7-Zip/WinRAR), which "
                        "isn't set. Set it now?".format(fmt), parent=self):
                    external_tool = extprog.prompt_for_program(
                        self, "archiver_path", "Archiver (7-Zip / WinRAR)",
                        "Path to 7z.exe / Rar.exe — invoked as '<tool> a <archive> <files>'.")
                if not extprog._is_usable(external_tool or ""):
                    self._append("Cancelled: no archiver set.")
                    return

        base = os.path.splitext(os.path.basename(proj.path))[0]
        out_path = filedialog.asksaveasfilename(
            title="Save project archive", parent=self,
            initialdir=proj.root(), initialfile="{}_export.{}".format(base, fmt),
            defaultextension="." + fmt.split(".")[-1],
            filetypes=[(fmt, "*." + fmt), ("All files", "*.*")])
        if not out_path:
            return

        self.export_btn.configure(state=tk.DISABLED)
        self._append("Exporting to {} ...".format(out_path))
        recipe_by_name = {r.name: r for r in self.app.recipes}
        exclude = archive_mod.DEFAULT_EXCLUDE if self.exclude_heavy.get() else None
        compresslevel = {"Normal": 6, "Fast": 1, "Store": 0}.get(self.compression.get(), 6)
        try:
            summary = archive_mod.export_archive(
                proj, recipe_by_name, out_path, fmt=fmt,
                include_figures=self.include_figs.get(), img_format=self.img_fmt.get(),
                stacked=True, offset_frac=offset, external_tool=external_tool,
                exclude=exclude, compresslevel=compresslevel, log=self._append)
        except Exception as e:
            self._append("FAILED: {}: {}".format(type(e).__name__, e))
            messagebox.showerror("Archive Export", "Export failed:\n{}".format(e),
                                 parent=self)
            self.export_btn.configure(state=tk.NORMAL)
            return

        for w in summary.get("warnings", []):
            self._append("warning: " + w)
        self._append("Done: {}  ({} figure(s), {} member(s)).".format(
            summary["archive"], summary["n_figures"], len(summary["members"])))
        self.export_btn.configure(state=tk.NORMAL)
        if messagebox.askyesno("Archive Export",
                               "Wrote {}\n\nOpen its folder?".format(
                                   os.path.basename(out_path)), parent=self):
            self._open_folder(os.path.dirname(os.path.abspath(out_path)))

    def _open_folder(self, path):
        import subprocess
        import sys
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)      # noqa: cross-platform guarded
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._append("could not open folder: {}".format(e))
