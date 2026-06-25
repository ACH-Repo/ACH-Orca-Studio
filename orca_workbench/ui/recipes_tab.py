"""Recipes tab — browse and edit the recipe library.

Recipes are JSON files in the recipe directory (default: data/recipes/ inside
the package). Each recipe is a template for an ORCA .inp file with a
!!##COORDS_SEC##!! placeholder for the xyz block.

Editing model (no Save button): selecting a recipe loads it; any edit is
auto-applied to its file after a short debounce. New / Duplicate immediately
materialise a real recipe you can then edit or delete. Each recipe keeps a
STABLE file, so renaming it updates that file in place (and cascades the new
name to any planned calculations that referenced it) rather than spawning a
duplicate. Double-click a recipe to open its JSON in your configured text
editor.
"""

import datetime
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from orca_workbench.core import inputs as inputs_mod
from orca_workbench.core.inputs import COORDS_PLACEHOLDER, Recipe
from orca_workbench.ui import extprog
from orca_workbench.ui.shortcuts import install_text_shortcuts, install_tree_shift_select
from orca_workbench.ui.tooltip import tip


_HEADING_LABELS = {
    "favorite": "*",
    "name": "Name",
    "calctype": "Type",
    "method_label": "Method",
    "variant": "Variant",
    "created": "Created",
}

_EDITOR_DESC = ("Program used to open a recipe's JSON file when you double-click it. "
                "On a PC: full path to your editor (Notepad++, Sublime, VS Code, …). "
                "On the gateway over X: a GUI editor command like 'gedit' or 'kate' works; "
                "terminal editors (vim/nano) won't, as there's no attached terminal.")


class RecipesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._selected_path = None  # type: Optional[str]  (recipe.source_path = identity)
        self._editor_path = None    # type: Optional[str]  (recipe currently shown in editor)
        self._suppress = False
        self._suppress_tree_select = False
        self._flush_id = None
        self._sort_col = "name"
        self._sort_desc = False
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        b_new = ttk.Button(toolbar, text="New", command=self.on_new)
        b_dup = ttk.Button(toolbar, text="Duplicate", command=self.on_duplicate)
        # Destructive (removes files from disk) — styled red like the Calculations
        # tab's DECONSTRUCT so it reads as dangerous, not a routine button.
        b_del = tk.Button(toolbar, text="DELETE", command=self.on_delete,
                          bg="#c0392b", activebackground="#a93226",
                          fg="white", activeforeground="white")
        b_addfolder = ttk.Button(toolbar, text="Add folder...", command=self.app.on_add_recipe_dir)
        b_managefolder = ttk.Button(toolbar, text="Manage folders...",
                                    command=self.app.on_manage_recipe_dirs)
        b_reload = ttk.Button(toolbar, text="Reload library", command=self.app.reload_recipes)
        for b in (b_new, b_dup, b_addfolder, b_managefolder, b_reload):
            b.pack(side=tk.LEFT, padx=2)
        # Destructive — placed last (after Reload library) with a wide gap so it
        # isn't fat-fingered next to the routine buttons.
        b_del.pack(side=tk.LEFT, padx=(40, 2))
        tip(b_new, "Create a new recipe (a blank skeleton) and select it for editing. It's saved "
                   "immediately — no Save step. Edit it in place; delete it if you don't want it.")
        tip(b_dup, "Duplicate the selected recipe (with '(copy)' appended). The copy appears in "
                   "the list right away as its own file; edit it freely without touching the original.")
        tip(b_del, "DELETE the selected recipe(s) — single or bulk. This permanently removes the "
                   "JSON file(s) from disk; it cannot be undone. Planned calculations referencing "
                   "them will show 'recipe missing'.\n\nTo stop showing a whole folder WITHOUT "
                   "deleting any files, use Manage folders instead.\n\n"
                   "Keyboard shortcut: Delete (with the recipe list focused).")
        tip(b_addfolder, "Add another recipe folder to this project. Its recipes are appended to "
                         "the library and shown under their own divider; new recipes still save to "
                         "the primary folder. (Same as Recipes menu > Add recipe directory.)")
        tip(b_managefolder, "Reorder recipe folders, set which is primary (where New/Duplicate "
                            "save), or UNLINK a folder. Unlinking only removes it from this "
                            "project's list — it never deletes any files on disk.")
        tip(b_reload, "Re-scan the recipe folder(s) from disk. Use after editing a recipe file "
                      "externally (double-click → your text editor).")

        # Auto-save status indicator, far right.
        self.status_lbl = ttk.Label(toolbar, text="", foreground="#1a7a1a")
        self.status_lbl.pack(side=tk.RIGHT, padx=8)

        self.dir_label = ttk.Label(toolbar, text="", foreground="#666")
        self.dir_label.pack(side=tk.LEFT, padx=20)
        tip(self.dir_label, "Recipe folder(s) holding the JSON files. Add more via Recipes menu > "
                            "Add recipe directory; reorder/remove via Manage recipe directories. "
                            "When several are loaded, the list is grouped by folder.")

        # Search bar.
        search_bar = ttk.Frame(self)
        search_bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))
        ttk.Label(search_bar, text="Search:").pack(side=tk.LEFT, padx=(4, 4))
        self.search_var = tk.StringVar(value="")
        search_entry = ttk.Entry(search_bar, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.search_var.trace_add("write", lambda *_: self.refresh())
        b_clear = ttk.Button(search_bar, text="Clear", width=8,
                             command=lambda: self.search_var.set(""))
        b_clear.pack(side=tk.LEFT)
        tip(search_entry, "Filter the list by name/method/variant/type as you type. Doesn't "
                          "touch files.")
        tip(b_clear, "Clear the search filter.")

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        columns = ("favorite", "name", "calctype", "method_label", "variant", "created")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        col_specs = [
            ("favorite",     30,  25,  False, tk.CENTER, False),
            ("name",         220, 100, True,  tk.W,      True),
            ("calctype",     65,  50,  False, tk.W,      True),
            ("method_label", 180, 80,  True,  tk.W,      True),
            ("variant",      130, 60,  False, tk.W,      True),
            ("created",      120, 100, False, tk.W,      True),
        ]
        for col, width, minw, stretch, anchor, sortable in col_specs:
            label = _HEADING_LABELS[col]
            if sortable:
                self.tree.heading(col, text=label, command=lambda c=col: self._on_header_click(c))
            else:
                self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=minw, anchor=anchor, stretch=stretch)
        self._refresh_heading_arrows()
        # Folder-divider rows (shown only when several recipe folders are loaded):
        # a non-selectable header above each folder's recipes.
        self.tree.tag_configure("divider", background="#dfe3e8",
                                font=("TkDefaultFont", 9, "bold"))

        # Pack the scrollbar BEFORE the tree so the (wide) table doesn't starve it
        # of width — otherwise the bar is zero-width: wheel-scrollable but undraggable.
        sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        install_tree_shift_select(self.tree)
        self.tree.bind("<Delete>", lambda e: (self.on_delete(), "break")[1])
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Enter>", lambda e: self.tree.focus_set(), add="+")
        tip(self.tree, "Recipe library. Click a row to load it into the editor (edits auto-save). "
                       "Double-click to open its JSON in your text editor.\n\n"
                       "Click a column header to sort (again to reverse). Favorites (*) stay "
                       "pinned on top. Ctrl+click toggles, Shift+click extends, Ctrl+A all, "
                       "Delete removes.")

        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        meta = ttk.Frame(right)
        meta.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        self.name_var = tk.StringVar()
        self.calctype_var = tk.StringVar()
        self.method_var = tk.StringVar()
        self.variant_var = tk.StringVar()
        self.favorite_var = tk.BooleanVar(value=False)
        lbl, ent = self._meta_row(meta, 0, "Name:", self.name_var)
        _nt = "Display name. Editing it renames the recipe in place and updates any planned calcs that used it."
        tip(lbl, _nt); tip(ent, _nt)
        lbl, ent = self._meta_row(meta, 1, "Calc type:", self.calctype_var, width=20)
        tip(lbl, "OPT/FREQ/NMR/SP/… — a directory level under calcs/<mol>/<category>/.")
        tip(ent, "OPT/FREQ/NMR/SP/… — a directory level under calcs/<mol>/<category>/.")
        lbl, ent = self._meta_row(meta, 2, "Method label:", self.method_var)
        tip(lbl, "e.g. B3LYP_DEF2-TZVPP. A directory level.")
        tip(ent, "e.g. B3LYP_DEF2-TZVPP. A directory level.")
        lbl, ent = self._meta_row(meta, 3, "Variant (optional):", self.variant_var)
        tip(lbl, "Optional disambiguator (RIJCOSX_D4, NoFrozenCore, …) — an extra directory level.")
        tip(ent, "Optional disambiguator (RIJCOSX_D4, NoFrozenCore, …) — an extra directory level.")
        fav_check = ttk.Checkbutton(meta, text="* Favorite (pin to top of list)",
                                    variable=self.favorite_var, command=self._on_favorite_toggle)
        fav_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=4, pady=(4, 2))
        tip(fav_check, "Pin this recipe to the top of the list. Applies immediately.")
        meta.columnconfigure(1, weight=1)

        # Auto-apply edits on any field change (debounced).
        for var in (self.name_var, self.calctype_var, self.method_var, self.variant_var):
            var.trace_add("write", lambda *_: self._schedule_flush())

        text_frame = ttk.LabelFrame(right, text="Template text (use !!##COORDS_SEC##!! for the xyz block)")
        text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.text = tk.Text(text_frame, wrap="none", font=("Courier", 10), undo=True)
        ts = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        ths = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=ts.set, xscrollcommand=ths.set)
        ts.pack(side=tk.RIGHT, fill=tk.Y)
        ths.pack(side=tk.BOTTOM, fill=tk.X)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        install_text_shortcuts(self.text)
        self.text.bind("<<Modified>>", self._on_text_modified)
        tip(self.text,
            "The full ORCA .inp template. Anything here ends up in the .inp, except "
            "!!##COORDS_SEC##!! becomes the * xyz CHARGE MULT ... * block. Keep the %pal nprocs "
            "line — the requested cores are read from it.\n\n"
            "Edits auto-save. Ctrl+A select · Ctrl+Z undo · Ctrl+Y redo.")

        self.warn_lbl = ttk.Label(right, text="", foreground="#b00000")
        self.warn_lbl.pack(side=tk.TOP, anchor=tk.W, padx=4, pady=(0, 2))

    def _meta_row(self, parent, row, label, var, width=None):
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=row, column=0, sticky=tk.W, padx=4, pady=2)
        kw = {"textvariable": var}
        if width is not None:
            kw["width"] = width
        ent = ttk.Entry(parent, **kw)
        ent.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=2)
        return lbl, ent

    # -------- sort & filter --------

    def _on_header_click(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self._refresh_heading_arrows()
        self.refresh()

    def _refresh_heading_arrows(self):
        for col, label in _HEADING_LABELS.items():
            if col == "favorite":
                continue
            if col == self._sort_col:
                self.tree.heading(col, text=label + (" ▼" if self._sort_desc else " ▲"))
            else:
                self.tree.heading(col, text=label)

    def _sort_recipes(self, recipes):
        def key(r):
            v = r.created_at if self._sort_col == "created" else getattr(r, self._sort_col, "")
            return str(v or "").lower()
        favs = sorted([r for r in recipes if r.favorite], key=key, reverse=self._sort_desc)
        non = sorted([r for r in recipes if not r.favorite], key=key, reverse=self._sort_desc)
        return favs + non

    def _filter_recipes(self, recipes):
        q = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        if not q:
            return list(recipes)
        return [r for r in recipes if q in r.name.lower()
                or q in (r.method_label or "").lower()
                or q in (r.variant or "").lower()
                or q in (r.calctype or "").lower()]

    # -------- identity helpers --------

    def _recipe_by_path(self, path):
        if not path:
            return None
        for r in self.app.recipes:
            if r.source_path == path:
                return r
        return None

    def _current_recipe(self):
        return self._recipe_by_path(self._selected_path)

    # -------- refresh --------

    def refresh(self):
        self._rerender_tree(preserve_editor=False)

    def _rerender_tree(self, preserve_editor=False):
        dirs = list(getattr(self.app, "recipe_dirs", None) or [self.app.recipe_dir])
        self.dir_label.configure(text=self._dir_label_text(dirs))
        sel = self._selected_path
        self.tree.delete(*self.tree.get_children())
        # Group by source folder (in the app's folder order); within each folder
        # apply the same filter + sort. Dividers only when several folders load —
        # search & sort still operate across every recipe shown.
        show_dividers = len(dirs) > 1
        for d, recipes in self._group_recipes_by_dir(dirs):
            shown = self._sort_recipes(self._filter_recipes(recipes))
            if not shown:
                continue
            if show_dividers:
                div_iid = "__divider__" + (d or "")
                if not self.tree.exists(div_iid):
                    self.tree.insert("", tk.END, iid=div_iid, tags=("divider",),
                                     values=("", self._dir_display(d), "", "", "", ""))
            for r in shown:
                iid = r.source_path or r.name
                if self.tree.exists(iid):
                    continue
                star = "*" if r.favorite else ""
                self.tree.insert("", tk.END, iid=iid,
                                 values=(star, r.name, r.calctype, r.method_label, r.variant,
                                         _fmt_created(r.created_at)))
        if sel and self.tree.exists(sel) and not sel.startswith("__divider__"):
            self._suppress_tree_select = preserve_editor
            try:
                self.tree.selection_set(sel)
                self.tree.see(sel)
            finally:
                self._suppress_tree_select = False
        elif not preserve_editor:
            self._clear_editor()

    def _group_recipes_by_dir(self, dirs):
        """Return [(dir, [recipes])] in the app's folder order, grouping each
        recipe by the folder its source file lives in. Any recipe whose folder
        isn't in the list (shouldn't normally happen) lands in a trailing
        (dir=None) 'other' group so nothing is silently hidden."""
        def norm(p):
            return os.path.normcase(os.path.abspath(p)) if p else None
        order = [norm(d) for d in dirs]
        buckets = {k: [] for k in order}
        disp = {norm(d): d for d in dirs}
        other = []
        for r in self.app.recipes:
            key = norm(os.path.dirname(r.source_path)) if r.source_path else None
            if key in buckets:
                buckets[key].append(r)
            else:
                other.append(r)
        groups = [(disp[k], buckets[k]) for k in order]
        if other:
            groups.append((None, other))
        return groups

    def _dir_display(self, d):
        if d is None:
            return "(other recipes)"
        from orca_workbench.ui.app import DEFAULT_RECIPE_DIR
        if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(DEFAULT_RECIPE_DIR)):
            return "Built-in recipes  ({})".format(d)
        return d

    def _dir_label_text(self, dirs):
        if len(dirs) <= 1:
            return "Library: {}".format(dirs[0] if dirs else self.app.recipe_dir)
        return "Library: {} folders (primary: {})".format(len(dirs), dirs[0])

    def _on_select(self, _event):
        if getattr(self, "_suppress_tree_select", False):
            return
        sel = [s for s in self.tree.selection() if not s.startswith("__divider__")]
        if not sel:
            return
        if len(sel) > 1:
            self._selected_path = None
            self._clear_editor()
            return
        self._selected_path = sel[0]
        if sel[0] == self._editor_path:
            # Same recipe already in the editor. Don't reload — a reload does
            # text.delete+insert, which would yank the cursor to the end and
            # pollute the undo stack. This fires when _flush re-renders the tree
            # and re-selects the row mid-edit (the <<TreeviewSelect>> event lands
            # after _suppress_tree_select has already been reset).
            return
        r = self._recipe_by_path(self._selected_path)
        if r is None:
            return
        self._load_into_editor(r)

    def _load_into_editor(self, r):
        self._suppress = True
        try:
            self.name_var.set(r.name)
            self.calctype_var.set(r.calctype)
            self.method_var.set(r.method_label)
            self.variant_var.set(r.variant)
            self.favorite_var.set(r.favorite)
            self.text.delete("1.0", tk.END)
            self.text.insert("1.0", r.template)
            self.text.edit_reset()        # baseline: don't let Ctrl+Z undo the load
            self.text.edit_modified(False)
        finally:
            self._suppress = False
        self._editor_path = r.source_path or r.name
        self._update_placeholder_warning()
        self._set_status("")

    def _select_all(self, _event=None):
        items = [i for i in self.tree.get_children("") if not i.startswith("__divider__")]
        if items:
            self.tree.selection_set(items)
        return "break"

    def _clear_editor(self):
        self._selected_path = None
        self._editor_path = None
        self._suppress = True
        try:
            self.name_var.set("")
            self.calctype_var.set("")
            self.method_var.set("")
            self.variant_var.set("")
            self.favorite_var.set(False)
            self.text.delete("1.0", tk.END)
            self.text.edit_reset()
            self.text.edit_modified(False)
        finally:
            self._suppress = False
        self.warn_lbl.configure(text="")
        self._set_status("")

    # -------- live auto-apply --------

    def _on_text_modified(self, _event=None):
        if self._suppress:
            self.text.edit_modified(False)
            return
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._schedule_flush()

    def _schedule_flush(self):
        if self._suppress or self._selected_path is None:
            return
        self._set_status("● editing…", "#b07000")
        if self._flush_id is not None:
            try:
                self.after_cancel(self._flush_id)
            except Exception:
                pass
        self._flush_id = self.after(500, self._flush)

    def _flush(self):
        self._flush_id = None
        r = self._current_recipe()
        if r is None:
            return
        newname = self.name_var.get().strip()
        if not newname:
            self._set_status("! name required", "#b00000")
            return
        oldname = r.name
        r.calctype = self.calctype_var.get().strip() or "SP"
        r.method_label = self.method_var.get().strip() or "method"
        r.variant = self.variant_var.get().strip()
        r.favorite = bool(self.favorite_var.get())
        r.template = self.text.get("1.0", tk.END).rstrip() + "\n"
        r.name = newname
        try:
            inputs_mod.save_recipe(r, self.app.recipe_dir)  # stable file → in-place update
        except Exception as e:
            self._set_status("save failed: {}".format(e), "#b00000")
            return
        # Keep our identity keys in sync: a brand-new recipe gains a source_path on
        # first save, changing its tree iid — without this the re-render below would
        # reload the editor (and reset the cursor) on the next keystroke.
        new_iid = r.source_path or r.name
        self._selected_path = new_iid
        self._editor_path = new_iid
        # Renaming cascades to planned calcs that referenced the old name.
        if newname != oldname:
            n = 0
            for c in self.app.project.planned_calcs:
                if c.recipe_name == oldname:
                    c.recipe_name = newname
                    n += 1
            if n:
                self.app.mark_dirty()
                self.app.refresh_all_tabs()
        self._update_placeholder_warning()
        self._rerender_tree(preserve_editor=True)
        self._set_status("saved", "#1a7a1a")

    def _on_favorite_toggle(self):
        # Apply favourite immediately (no debounce) so the pin is snappy.
        if self._suppress or self._selected_path is None:
            return
        if self._flush_id is not None:
            try:
                self.after_cancel(self._flush_id)
            except Exception:
                pass
            self._flush_id = None
        self._flush()

    def _update_placeholder_warning(self):
        tmpl = self.text.get("1.0", tk.END)
        if COORDS_PLACEHOLDER not in tmpl:
            self.warn_lbl.configure(
                text="Warning: template has no {} — generated .inp files won't contain the molecule "
                     "coordinates.".format(COORDS_PLACEHOLDER))
        else:
            self.warn_lbl.configure(text="")

    def _set_status(self, text, color="#1a7a1a"):
        self.status_lbl.configure(text=text, foreground=color)

    # -------- new / duplicate / delete / open-external --------

    def on_new(self):
        r = Recipe(
            name=self._unique_name("New recipe"),
            calctype="SP", method_label="method",
            template="! HF def2-SVP\n\n%maxcore 5000\n\n%pal nprocs 4\nend\n\n" + COORDS_PLACEHOLDER + "\n",
        )
        self._materialise(r)

    def on_duplicate(self):
        r = self._current_recipe()
        if r is None:
            messagebox.showinfo("No selection", "Select a recipe to duplicate.")
            return
        copy = Recipe(
            name=self._unique_name(r.name + " (copy)"),
            calctype=r.calctype, method_label=r.method_label, variant=r.variant,
            favorite=False, template=r.template,
        )
        self._materialise(copy)

    def _materialise(self, recipe):
        """Save a brand-new recipe to disk immediately, reload, and select it."""
        try:
            inputs_mod.save_recipe(recipe, self.app.recipe_dir)  # source_path None → new file
        except Exception as e:
            messagebox.showerror("Could not create recipe", str(e))
            return
        new_path = recipe.source_path
        self.app.reload_recipes()
        self._selected_path = new_path
        self.refresh()
        r = self._recipe_by_path(new_path)
        if r is not None:
            self._load_into_editor(r)
        self._set_status("created", "#1a7a1a")

    def _unique_name(self, base):
        existing = {r.name for r in self.app.recipes}
        if base not in existing:
            return base
        i = 2
        while "{} {}".format(base, i) in existing:
            i += 1
        return "{} {}".format(base, i)

    def on_delete(self):
        sel = [s for s in self.tree.selection() if not s.startswith("__divider__")]
        if not sel:
            messagebox.showinfo("No selection", "Select one or more recipes to delete.")
            return
        recipes = [self._recipe_by_path(p) for p in sel]
        recipes = [r for r in recipes if r is not None and r.source_path]
        if not recipes:
            return
        tail = ("\n\nThis permanently deletes the file(s) from disk and cannot be undone. "
                "(To stop showing a whole folder without deleting files, cancel and use "
                "Manage folders.)")
        if len(recipes) == 1:
            prompt = "Permanently delete the recipe '{}' from disk?\n\nFile: {}{}".format(
                recipes[0].name, recipes[0].source_path, tail)
        else:
            preview = "\n".join("  - " + r.name for r in recipes[:8])
            extra = "" if len(recipes) <= 8 else "\n  ... ({} more)".format(len(recipes) - 8)
            prompt = "Permanently delete {} recipe file(s) from disk?\n\n{}{}{}".format(
                len(recipes), preview, extra, tail)
        if not messagebox.askyesno("Delete recipe files", prompt):
            return
        failures = []
        for r in recipes:
            try:
                os.remove(r.source_path)
            except OSError as e:
                failures.append("{}: {}".format(r.name, e))
        self._selected_path = None
        self.app.reload_recipes()
        if failures:
            messagebox.showerror("Some deletes failed", "\n".join(failures))

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        r = self._recipe_by_path(row)
        if r is None or not r.source_path:
            return
        extprog.open_with(self, "text_editor_path", r.source_path,
                          "text editor", _EDITOR_DESC)
        self._set_status("opened in editor — Reload library to pick up external edits", "#666")


def _fmt_created(iso):
    # type: (Optional[str]) -> str
    if not iso:
        return ""
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return (iso or "")[:16]
