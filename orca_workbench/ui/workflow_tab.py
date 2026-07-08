"""Workflow tab — a node-graph pipeline editor (Milestone 1).

Author a computational pipeline visually: drop nodes (Molecules → Optimize →
Frequencies → Property → Report), wire compatible ports, configure each node,
then Generate to expand it across your molecules into planned calculations that
run through the normal Calculations tab.

The editor is built on tk.Canvas: nodes are drawn as boxes with input ports on
the left and output ports on the right; drag a node to move it, drag from an
output port to an input port to connect, drag empty space to pan. Live
conditional execution comes in a later milestone — for now Generate does a
static expansion of a condition-free graph.
"""

import csv
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from orca_workbench.core import diagnostics as diag
from orca_workbench.core import workflow as wf_mod
from orca_workbench.core import workflow_expand
from orca_workbench.core.inputs import safe_path_component
from orca_workbench.core.project import Molecule, PlannedCalc, new_calc_id
from orca_workbench.ui.modal import make_modal
from orca_workbench.ui.shortcuts import install_text_shortcuts
from orca_workbench.ui.tooltip import tip


NODE_W = 200
TITLE_H = 22
SUMMARY_H = 20   # band under the title for the config summary (recipe / mode / …)
PORT_H = 20
PORT_R = 5

# Node-graph colours are now data-driven from the active skin — see
# theme.node_palette() and WorkflowTab._np / _load_node_colors / apply_theme.

# Live execution status → accent colour (border + badge) for a node.
_STATE_COLOR = {
    "waiting": "#9e9e9e",
    "running": "#1e88e5",
    "done": "#2e7d32",
    "error": "#c62828",
    "skipped": "#7e57c2",
    "interrupted": "#ef6c00",
}


class WorkflowTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.wf = wf_mod.Workflow()
        # Selection: an ordered list of node ids (order matters for J-connect),
        # plus at most one selected edge. Nodes and an edge aren't selected at
        # the same time.
        self._sel_nodes = []       # type: list
        self._sel_edge = None      # type: Optional[str]
        self._mode = None          # "drag" | "wire" | "pan" | "box" | None
        self._drag = None          # transient drag state
        self._hover_node = None    # node id the cursor is over (for the hover glow)
        # Once the user has confirmed editing an already-executed graph, don't nag
        # again this session (the escape hatch stays an informed, explicit choice
        # the first time). Reset when a fresh project/graph is loaded.
        self._edit_run_ack = False
        self._add_offset = 0
        # View transform: world (node.x/y) -> screen = world*zoom + (ox, oy).
        self._zoom = 1.0
        self._ox = 0.0
        self._oy = 0.0
        self._pan = None           # transient middle/right pan state
        # Live execution: node_id -> [calc id, ...] from the last "Run pipeline".
        self._node_calcs = {}
        # Undo/redo of graph edits (add/delete/move/wire): whole-graph snapshots.
        # Every mutation goes through _commit(), so one snapshot per commit — and a
        # drag commits once (at release), so a move is a single undo step.
        self._undo_stack = []      # type: list
        self._redo_stack = []      # type: list
        self._undo_baseline = None
        # Node-graph colours from the active skin (see theme.node_palette); refreshed
        # by apply_theme() when the user switches skins. Seeded before _build so the
        # first draw is themed.
        self._load_node_colors()
        self._build()

    def _load_node_colors(self):
        """(Re)read the node-graph colour palette from the active skin."""
        from orca_workbench.core import theme as theme_mod
        sid = theme_mod.active_skin_id()
        try:
            self._np = theme_mod.node_palette(sid)
        except Exception:
            sid = theme_mod.DEFAULT_SKIN_ID
            self._np = theme_mod.node_palette(sid)
        self._np["canvas"] = theme_mod.get_skin(sid).get("node_canvas", "#eef1f4")

    def apply_theme(self):
        """Called by App.apply_skin: re-read the node colours + recolour the
        canvas backdrop, then redraw the graph in the new skin."""
        self._load_node_colors()
        try:
            self.canvas.configure(background=self._np["canvas"])
        except tk.TclError:
            pass
        self._redraw()

    # ---- world <-> screen transform (for zoom + pan) ----

    def _w2s(self, wx, wy):
        return wx * self._zoom + self._ox, wy * self._zoom + self._oy

    def _s2w(self, sx, sy):
        return (sx - self._ox) / self._zoom, (sy - self._oy) / self._zoom

    def _set_initial_sash(self, _e=None):
        """Once the tab is mapped and has a real width, put the sash at ~72% so the
        Node settings panel is a narrow ~28% side panel (not ~half the tab)."""
        try:
            total = self._wf_paned.winfo_width()
            if total > 100:
                self._wf_paned.sashpos(0, int(total * 0.72))
                self._wf_paned.unbind("<Map>")
        except Exception:
            pass

    def _fs(self, pt):
        """Scale a font point size by the current zoom. Node widths are computed for
        the unscaled font, so the text must scale in proportion at every zoom or it
        overflows the (shrinking) box — hence min 1pt, not a larger floor. When zoomed
        far out the text is tiny (you're reading layout, not labels); it's crisp again
        as you zoom in."""
        return max(1, int(round(pt * self._zoom)))

    # ------------------------------------------------------------------ UI

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))
        ttk.Label(bar, text="Add node:").pack(side=tk.LEFT, padx=(2, 4))
        # Palette = the common pipeline nodes only. Niche/utility nodes (Filter,
        # ZPVA) stay one keystroke away via the F3 / drag-on-empty search popup,
        # which is registry-driven so it lists every node type. Transform/Combine
        # (geometry building: shift/rotate/align fragments, then merge) earned a
        # palette spot — they're the front of the pipeline when constructing
        # complexes/dimers by hand.
        for ntype in ("molecules", "transform", "combine", "optimize", "frequencies",
                      "property", "condition", "report"):
            label = wf_mod.NODE_TYPES[ntype]["label"]
            ttk.Button(bar, text=label, width=max(8, len(label) + 1),
                       command=lambda t=ntype: self._add_node(t)).pack(side=tk.LEFT, padx=1)

        b_run = tk.Button(bar, text="> Run pipeline", command=self.on_run_pipeline,
                          font=("TkDefaultFont", 10, "bold"), bg="#e0a35a",
                          activebackground="#e8b673", fg="#222222")
        b_run.pack(side=tk.RIGHT, padx=(6, 2))
        b_unatt = tk.Button(bar, text=">> Submit unattended", command=self.on_submit_unattended,
                            bg="#cdebc5", activebackground="#bfe2b6")
        b_unatt.pack(side=tk.RIGHT, padx=2)
        b_gen = tk.Button(bar, text="Generate only", command=self.on_generate,
                          bg="#e2e2e2", activebackground="#d5d5d5")
        b_gen.pack(side=tk.RIGHT, padx=2)
        b_refresh = tk.Button(bar, text="Refresh (F5)", command=self.on_refresh_status,
                              bg="#d3e6f5", activebackground="#c3dcf0")
        b_refresh.pack(side=tk.RIGHT, padx=(12, 2))
        tip(b_refresh, "Re-query job status and rebuild each node's results, so finished nodes "
                       "light up and their plot/viewer buttons appear. Also works after reopening "
                       "a project (rebuilds the node->calc map from the calcs themselves).")
        tip(b_run, "Expand this pipeline into calculations and run them automatically: each step "
                   "builds and launches as its input geometry becomes ready, and a Condition node "
                   "decides live whether its downstream branch runs (e.g. only do NMR if the "
                   "Frequencies job found no imaginary modes). Watch progress here and on the "
                   "Calculations tab. The app must stay open while it advances.\n\nSelect a node "
                   "(or several) first to run ONLY that pipeline — handy when the canvas holds "
                   "several independent networks. With nothing selected, every network runs.")
        tip(b_unatt, "Cluster only. Submit the whole pipeline as a SLURM dependency chain: each "
                     "step is held until the job it needs finishes, and a Condition becomes a "
                     "check inside the job. SLURM then runs everything on its own — you can close "
                     "ORCA Workbench and MobaXterm. Use this for long pipelines you don't want to "
                     "babysit. (Selection scoping works the same as Run pipeline.)")
        tip(b_gen, "Expand the pipeline into planned calculations (with geometry parent-links and "
                   "conditional gates) and jump to the Calculations tab — but don't launch them. "
                   "Use this if you want to review or edit before running.")

        ttk.Label(self, text="Add nodes to the canvas and connect their pins to build a workflow, "
                  "run chronologically from left to right.  For hotkeys and the full guide: "
                  "Help > Node graphs.",
                  foreground="#666", wraplength=1100, justify=tk.LEFT).pack(
                      side=tk.TOP, anchor=tk.W, padx=8, pady=2)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._wf_paned = paned
        # Give the canvas ~72% and the settings panel ~28% (it was too wide).
        paned.bind("<Map>", self._set_initial_sash, add="+")

        cframe = ttk.Frame(paned)
        paned.add(cframe, weight=4)
        self.canvas = tk.Canvas(cframe, background=self._np["canvas"], highlightthickness=0,
                                scrollregion=(0, 0, 4000, 3000))
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)   # node glow follows the cursor
        # Pan on middle-drag (left-drag in empty space is box-select).
        self.canvas.bind("<Button-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        # Right button: drag pans, click (no drag) opens the context menu.
        self.canvas.bind("<Button-3>", self._on_rpress)
        self.canvas.bind("<B3-Motion>", self._on_rmotion)
        self.canvas.bind("<ButtonRelease-3>", self._on_rrelease)
        # Trackpad/wheel navigation:
        #   two-finger swipe (plain wheel)  -> pan vertically
        #   Shift + wheel                   -> pan horizontally
        #   Ctrl + wheel / pinch            -> zoom (centred on the cursor)
        self.canvas.bind("<MouseWheel>", self._wheel_pan_v)            # Windows / macOS
        self.canvas.bind("<Shift-MouseWheel>", self._wheel_pan_h)
        self.canvas.bind("<Control-MouseWheel>", self._wheel_zoom)
        # X11 sends wheel as buttons 4/5 (with modifier prefixes).
        self.canvas.bind("<Button-4>", lambda e: self._pan_by(0, 60))
        self.canvas.bind("<Button-5>", lambda e: self._pan_by(0, -60))
        self.canvas.bind("<Shift-Button-4>", lambda e: self._pan_by(60, 0))
        self.canvas.bind("<Shift-Button-5>", lambda e: self._pan_by(-60, 0))
        self.canvas.bind("<Control-Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.1))
        self.canvas.bind("<Control-Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.1))
        # Arrow keys pan — except right after an align/straighten (Q / Shift+WASD),
        # where they tune the selection's spacing instead (Left/Right = horizontal
        # gap, Up/Down = vertical gap) until you click or change the selection.
        self.canvas.bind("<Up>", lambda e: self._arrow_key("up"))
        self.canvas.bind("<Down>", lambda e: self._arrow_key("down"))
        self.canvas.bind("<Left>", lambda e: self._arrow_key("left"))
        self.canvas.bind("<Right>", lambda e: self._arrow_key("right"))
        for k in ("<plus>", "<KP_Add>", "<equal>"):
            self.canvas.bind(k, lambda e: self._zoom_center(1.1))
        for k in ("<minus>", "<KP_Subtract>"):
            self.canvas.bind(k, lambda e: self._zoom_center(1 / 1.1))
        self.canvas.bind("<Key-0>", lambda e: self._reset_view())
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())
        self.canvas.bind("<Delete>", lambda e: self._delete_selected())
        self.canvas.bind("<Control-a>", self._on_select_all)
        self.canvas.bind("<Control-A>", self._on_select_all)
        # V connects the two selected nodes (was J — J/K/L are now the vim-style
        # cut keys: J cuts a node's INPUT wires, L its OUTPUT wires, K all).
        self.canvas.bind("<v>", lambda e: self._connect_selected())
        self.canvas.bind("<V>", lambda e: self._connect_selected())
        self.canvas.bind("<j>", lambda e: self._cut_selected("in"))
        self.canvas.bind("<J>", lambda e: self._cut_selected("in"))
        self.canvas.bind("<l>", lambda e: self._cut_selected("out"))
        self.canvas.bind("<L>", lambda e: self._cut_selected("out"))
        self.canvas.bind("<k>", lambda e: self._cut_selected("both"))
        self.canvas.bind("<K>", lambda e: self._cut_selected("both"))
        # Blender-style deselect-all; Ctrl+C/V copy-paste the selected nodes
        # (Ctrl bindings are more specific than the plain <c>/<v> ones, so Tk
        # routes them here, not to frame/connect).
        self.canvas.bind("<Alt-a>", lambda e: (self._clear_selection(), "break")[1])
        self.canvas.bind("<Alt-A>", lambda e: (self._clear_selection(), "break")[1])
        self.canvas.bind("<Control-c>", self._copy_selection)
        self.canvas.bind("<Control-C>", self._copy_selection)
        self.canvas.bind("<Control-v>", self._paste_clipboard)
        self.canvas.bind("<Control-V>", self._paste_clipboard)
        self.canvas.bind("<F3>", lambda e: self._on_search_add())
        # Annotations (canvas-scoped, so they only fire in the node editor):
        # C frames the selected nodes (Unreal-style), T drops a comment note.
        self.canvas.bind("<c>", lambda e: self._frame_selection())
        self.canvas.bind("<C>", lambda e: self._frame_selection())
        self.canvas.bind("<t>", lambda e: self._add_comment())
        self.canvas.bind("<T>", lambda e: self._add_comment())
        # Blueprint-style tidy-up of the selection (canvas-scoped, like c/t/j):
        # Q straightens a connected chain onto one line; Shift+WASD aligns edges.
        self.canvas.bind("<q>", lambda e: self._straighten_selected())
        self.canvas.bind("<Q>", lambda e: self._straighten_selected())
        self.canvas.bind("<W>", lambda e: self._align_selected("top"))
        self.canvas.bind("<S>", lambda e: self._align_selected("bottom"))
        self.canvas.bind("<A>", lambda e: self._align_selected("left"))
        self.canvas.bind("<D>", lambda e: self._align_selected("right"))
        # Undo/redo of graph edits (canvas-scoped; text fields keep their own Ctrl+Z).
        self.canvas.bind("<Control-z>", self._undo_graph)
        self.canvas.bind("<Control-Z>", self._undo_graph)
        self.canvas.bind("<Control-y>", self._redo_graph)
        self.canvas.bind("<Control-Y>", self._redo_graph)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)

        cfg_outer = ttk.LabelFrame(paned, text="Node settings")
        paned.add(cfg_outer, weight=1)
        self._cfg_canvas = tk.Canvas(cfg_outer, highlightthickness=0)
        cfg_sb = ttk.Scrollbar(cfg_outer, orient=tk.VERTICAL, command=self._cfg_canvas.yview)
        self._cfg_canvas.configure(yscrollcommand=cfg_sb.set)
        cfg_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._cfg_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.cfg_frame = ttk.Frame(self._cfg_canvas)
        self._cfg_win = self._cfg_canvas.create_window((0, 0), window=self.cfg_frame, anchor="nw")
        self.cfg_frame.bind("<Configure>", lambda e: self._cfg_canvas.configure(
            scrollregion=self._cfg_canvas.bbox("all")))
        self._cfg_canvas.bind("<Configure>", lambda e: self._cfg_canvas.itemconfigure(
            self._cfg_win, width=e.width))
        self._build_config_panel()

    def _cfg_wheel(self, e):
        # Windows/macOS deliver <MouseWheel> with e.delta; X11 (ThinLinc gateway)
        # delivers <Button-4>/<Button-5> instead — handle both.
        num = getattr(e, "num", 0)
        if num == 4:
            step = -1
        elif num == 5:
            step = 1
        else:
            step = -1 if getattr(e, "delta", 0) > 0 else 1
        self._cfg_canvas.yview_scroll(step, "units")
        return "break"

    def _bind_cfg_wheel(self, widget):
        """Bind the mouse wheel on the panel AND every child, so scrolling works
        anywhere over the settings — not only when the pointer is on the scrollbar."""
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, self._cfg_wheel)
        for ch in widget.winfo_children():
            self._bind_cfg_wheel(ch)

    def _build_config_panel(self):
        self._populate_config_panel()
        self._bind_cfg_wheel(self.cfg_frame)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._cfg_canvas.bind(seq, self._cfg_wheel)
        try:
            self._cfg_canvas.yview_moveto(0.0)   # show the top of the new node's settings
        except Exception:
            pass

    def _populate_config_panel(self):
        for w in self.cfg_frame.winfo_children():
            w.destroy()
        if len(self._sel_nodes) > 1:
            ttk.Label(self.cfg_frame,
                      text="{} nodes selected.\n\nPress V to connect two of them, drag to move "
                           "them together, or Delete to remove.".format(len(self._sel_nodes)),
                      foreground="#666", wraplength=200, justify=tk.LEFT).pack(padx=8, pady=8)
            return
        if len(self._sel_nodes) != 1:
            ttk.Label(self.cfg_frame, text="Select a node to configure it.",
                      foreground="#888", wraplength=200).pack(padx=8, pady=8)
            return
        node = self.wf.node(self._sel_nodes[0])
        if node is None:
            return
        ttk.Label(self.cfg_frame, text=node.label, font=("TkDefaultFont", 10, "bold")).pack(
            anchor=tk.W, padx=8, pady=(8, 4))

        if node.type in wf_mod.CALC_NODE_TYPES:
            ttk.Label(self.cfg_frame, text="Recipe (type to filter):").pack(anchor=tk.W, padx=8)
            self._recipe_search_combo(node, "recipe").pack(anchor=tk.W, padx=8, pady=2)
            if node.type == "optimize":
                self._geom_spec_widget(node)
        elif node.type == "molecules":
            mode = tk.StringVar(value=node.config.get("mode", "all"))
            ttk.Radiobutton(self.cfg_frame, text="All molecules", variable=mode, value="all",
                            command=lambda n=node, m=mode: self._set_cfg(n, "mode", m.get())
                            ).pack(anchor=tk.W, padx=8)
            ttk.Radiobutton(self.cfg_frame, text="Selected only:", variable=mode, value="selection",
                            command=lambda n=node, m=mode: self._set_cfg(n, "mode", m.get())
                            ).pack(anchor=tk.W, padx=8)
            lb = tk.Listbox(self.cfg_frame, selectmode=tk.EXTENDED, height=8, exportselection=False)
            for m in self.app.project.molecules:
                lb.insert(tk.END, m.filename)
            chosen = set(node.config.get("filenames", []))
            for i, m in enumerate(self.app.project.molecules):
                if m.filename in chosen:
                    lb.selection_set(i)
            lb.pack(anchor=tk.W, fill=tk.X, padx=8, pady=2)
            lb.bind("<<ListboxSelect>>", lambda e, n=node, w=lb: self._set_cfg(
                n, "filenames", [w.get(i) for i in w.curselection()]))
        elif node.type == "condition":
            ttk.Label(self.cfg_frame, text="Run the downstream branch only if\nthe feeding "
                      "calculation's result:", justify=tk.LEFT).pack(anchor=tk.W, padx=8)
            labels = list(wf_mod.PREDICATES.values())
            keys = list(wf_mod.PREDICATES.keys())
            cur_key = node.config.get("predicate", keys[0])
            var = tk.StringVar(value=wf_mod.PREDICATES.get(cur_key, labels[0]))
            cb = ttk.Combobox(self.cfg_frame, textvariable=var, state="readonly",
                              values=labels, width=30)
            cb.pack(anchor=tk.W, padx=8, pady=4)
            cb.bind("<<ComboboxSelected>>",
                    lambda e, n=node, v=var, ks=keys, ls=labels:
                    self._set_cfg(n, "predicate", ks[ls.index(v.get())]))
            ttk.Label(self.cfg_frame, text="Wire a calculation's geometry output into 'in', and "
                      "this node's 'pass' output into the next step. The check runs on that "
                      "feeding calc's output once it finishes.",
                      foreground="#777", wraplength=210, justify=tk.LEFT).pack(
                          anchor=tk.W, padx=8, pady=(2, 4))
        elif node.type == "report":
            ttk.Label(self.cfg_frame, text="Report name:").pack(anchor=tk.W, padx=8)
            var = tk.StringVar(value=node.config.get("name", "report"))
            ent = ttk.Entry(self.cfg_frame, textvariable=var, width=26)
            ent.pack(anchor=tk.W, padx=8, pady=2)
            var.trace_add("write", lambda *_a, n=node, v=var: self._set_cfg(n, "name", v.get()))
            self._build_report_format(node)
            self._build_report_extractors(node)
        elif node.type == "zpva":
            self._build_zpva_panel(node)
        elif node.type == "filter":
            self._build_filter_panel(node)
        elif node.type == "transform":
            self._build_transform_panel(node)
        elif node.type == "combine":
            self._build_combine_panel(node)
        elif node.type == "write":
            self._build_write_panel(node)
        elif self._is_annotation(node):
            what = "title" if node.type == "frame" else "text"
            ttk.Label(self.cfg_frame, text="A {} annotation (not part of the run).".format(
                node.type), foreground="#777", wraplength=210, justify=tk.LEFT).pack(
                    anchor=tk.W, padx=8, pady=(0, 4))
            ttk.Button(self.cfg_frame, text="Edit {}…".format(what),
                       command=lambda n=node: self._edit_annotation_text(n)).pack(anchor=tk.W, padx=8)
            ttk.Label(self.cfg_frame, text="Double-click it on the canvas to edit; drag its "
                      "bottom-right corner to resize.", foreground="#999", wraplength=210,
                      justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=(2, 0))

        if node.type in wf_mod.CALC_NODE_TYPES:
            self._build_results_section(node)

        ttk.Separator(self.cfg_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(self.cfg_frame, text="Delete node",
                   command=lambda nid=node.id: self._delete_node(nid)).pack(anchor=tk.W, padx=8)

    def _recipe_search_combo(self, node, key="recipe"):
        """An editable recipe combobox that filters its list as you TYPE — the
        recipe library can be long. Only a real recipe name is committed (on pick /
        Return / focus-out); partial text just narrows the dropdown. Returns the
        widget so the caller packs it."""
        # Once a node has launched calcs its recipe is locked (changing it would
        # silently desync the node from the calcs it spawned). Show the recipe name
        # read-only so it's still clear which one ran.
        if self._node_is_locked(node.id):
            current = node.config.get(key, "") or "(none set)"
            return ttk.Label(self.cfg_frame, text="{}  (locked - node has run)".format(current),
                             foreground="#555", wraplength=210, justify=tk.LEFT)
        names = [r.name for r in self.app.recipes]
        var = tk.StringVar(value=node.config.get(key, ""))
        cb = ttk.Combobox(self.cfg_frame, textvariable=var, values=names, width=28)

        def commit(*_a):
            v = var.get().strip()
            if v in names:
                self._set_cfg(node, key, v)

        def on_key(e):
            if e.keysym in ("Up", "Down", "Return", "Escape", "Tab", "Left", "Right"):
                return
            # Narrow the dropdown list to what's been typed. We deliberately do NOT
            # re-post the dropdown here: posting it on every keystroke grabs keyboard
            # focus to the listbox (the async grab beats a synchronous focus_set),
            # which is what made typing lose focus letter-by-letter. The filtered
            # matches show when the user opens the list (Down / the arrow).
            typed = var.get().strip().lower()
            cb["values"] = [n for n in names if typed in n.lower()] if typed else names

        cb.bind("<KeyRelease>", on_key, add="+")
        cb.bind("<<ComboboxSelected>>", commit, add="+")
        cb.bind("<Return>", commit, add="+")
        cb.bind("<FocusOut>", commit, add="+")
        return cb

    def _geom_spec_widget(self, node):
        """Optimize-node geometry constraints / relaxed scan. Applies to every molecule
        the node optimizes, so atom indices must be valid across them (the dialog shows
        the FIRST molecule's atoms for reference)."""
        from orca_workbench.core import geomspec as G
        ttk.Label(self.cfg_frame, text="Geometry: {}".format(G.describe(node.config.get("geom_spec"))),
                  foreground="#444", wraplength=210, justify=tk.LEFT).pack(
            anchor=tk.W, padx=8, pady=(6, 0))
        if self._node_is_locked(node.id):
            return   # node has run — don't let its spec desync from the calcs it spawned
        ttk.Button(self.cfg_frame, text="Constraints / scan...",
                   command=lambda n=node: self._edit_node_geom_spec(n)).pack(
            anchor=tk.W, padx=8, pady=2)

    def _edit_node_geom_spec(self, node):
        import os as _os
        from orca_workbench.core import coords as coords_mod
        from orca_workbench.ui.geomspec_dialog import GeomSpecDialog
        mols = self.app.project.molecules
        atoms = []
        if mols and mols[0].xyz_path:
            p = mols[0].xyz_path
            if not _os.path.isabs(p):
                p = _os.path.join(self.app.project.root(), p)
            try:
                atoms, _m = coords_mod.read_xyz(p)
            except Exception:
                atoms = []
        if not atoms:
            messagebox.showinfo(
                "No geometry yet",
                "Generate a molecule's XYZ first so atom indices are known. Constraints "
                "apply to every molecule this node optimizes - the first molecule's atoms "
                "are shown for reference.")
            return

        def _save(spec):
            node.config["geom_spec"] = spec
            self.app.mark_dirty()
            self._build_config_panel()   # refresh the summary

        vref = mols[0].xyz_path if (mols and mols[0].xyz_path) else None
        if vref and not _os.path.isabs(vref):
            vref = _os.path.join(self.app.project.root(), vref)

        def _view():
            if vref:
                from orca_workbench.ui.molecules_tab import open_xyz_3d
                open_xyz_3d(self, self.app, vref)

        GeomSpecDialog(self, atoms, node.config.get("geom_spec"), _save,
                       title="Optimize node: geometry constraints / scan (first molecule shown)",
                       view_xyz=(_view if vref else None))

    def _build_results_section(self, node):
        """List this calc node's expanded calculations with one-click launchers
        for the relevant viewers (IR / NMR spectrum, live progress plot, output),
        so you can inspect results straight from the graph."""
        ids = self._node_calcs.get(node.id)
        ct = getattr(self.app, "calculations_tab", None)
        if not ids or ct is None:
            return
        calcs = [self.app.project.calc_by_id(c) for c in ids]
        calcs = [c for c in calcs if c is not None]
        if not calcs:
            return
        ttk.Separator(self.cfg_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(self.cfg_frame, text="Results", font=("TkDefaultFont", 9, "bold")).pack(
            anchor=tk.W, padx=8)
        ttk.Label(self.cfg_frame, text="Open a viewer for a finished calculation:",
                  foreground="#777", wraplength=210).pack(anchor=tk.W, padx=8)
        shown = calcs[:12]
        for calc in shown:
            recipe = self.app.get_recipe(calc.recipe_name)
            ctype = (recipe.calctype if recipe else "").upper()
            label, tag, done, _active = ct._own_state(calc)
            colour = _STATE_COLOR.get(
                {"done": "done", "error": "error", "interrupted": "interrupted",
                 "running": "running"}.get(tag, "waiting"), "#444")
            ttk.Label(self.cfg_frame, text="• {} — {}".format(calc.molecule_filename, label),
                      foreground=colour, wraplength=210, justify=tk.LEFT).pack(
                          anchor=tk.W, padx=10)
            btns = ttk.Frame(self.cfg_frame)
            btns.pack(anchor=tk.W, padx=18, pady=(0, 3))
            if ctype == "FREQ" and done:
                b = ttk.Button(btns, text="IR", width=4, command=lambda c=calc: ct._plot_ir([c]))
                b.pack(side=tk.LEFT, padx=1); tip(b, "Plot the simulated IR spectrum.")
            if ctype == "NMR" and done:
                b = ttk.Button(btns, text="NMR", width=5, command=lambda c=calc: ct._plot_nmr([c]))
                b.pack(side=tk.LEFT, padx=1); tip(b, "Plot the simulated NMR spectrum.")
            if ctype == "TDDFT" and done:
                b = ttk.Button(btns, text="UV-Vis", width=7, command=lambda c=calc: ct._plot_uvvis([c]))
                b.pack(side=tk.LEFT, padx=1); tip(b, "Plot the simulated UV-Vis absorption spectrum.")
            if ctype == "EPR" and done:
                b = ttk.Button(btns, text="EPR", width=5, command=lambda c=calc: ct._plot_epr([c]))
                b.pack(side=tk.LEFT, padx=1); tip(b, "Plot the simulated EPR spectrum.")
                b = ttk.Button(btns, text="ENDOR", width=6, command=lambda c=calc: ct._plot_endor([c]))
                b.pack(side=tk.LEFT, padx=1); tip(b, "Plot the simulated ENDOR spectrum "
                                                     "(RF transitions of the coupled nuclei).")
            if ctype == "OPT" and done:
                trj = ct._calc_file(calc, calc.molecule_filename + "_trj.xyz")
                if trj:
                    b = ttk.Button(btns, text="Traj", width=5,
                                   command=lambda p=trj: ct._open_3d(p, slot="traj_viewer_path"))
                    b.pack(side=tk.LEFT, padx=1)
                    tip(b, "Open the optimisation trajectory (<mol>_trj.xyz), a multi-frame .xyz. "
                           "molden and Avogadro animate it as a movie; you can also load it into "
                           "PyMOL or VMD as a trajectory.")
            if calc.job_id:
                b = ttk.Button(btns, text="Live", width=5, command=lambda c=calc: ct._open_live(c))
                b.pack(side=tk.LEFT, padx=1)
                tip(b, "Open the live SCF / geometry-convergence plot.")
            if done:
                lbl3d = "Modes" if ctype == "FREQ" else "Struct"
                b = ttk.Button(btns, text=lbl3d, width=7,
                               command=lambda c=calc: self._open_structure(c))
                b.pack(side=tk.LEFT, padx=1)
                tip(b, "Open in your external 3D viewer (Avogadro/molden). For a FREQ job this "
                       "opens the .out so you can animate the normal modes; otherwise the "
                       "optimised geometry.")
                b2 = ttk.Button(btns, text="Out", width=4,
                                command=lambda c=calc: self._open_output(c))
                b2.pack(side=tk.LEFT, padx=1); tip(b2, "Open the ORCA .out file.")
        if len(calcs) > len(shown):
            ttk.Label(self.cfg_frame, text="… and {} more (see the Calculations tab)."
                      .format(len(calcs) - len(shown)), foreground="#888").pack(anchor=tk.W, padx=10)

    def _open_output(self, calc):
        ct = self.app.calculations_tab
        path = ct._out_path(calc)
        if not path or not os.path.isfile(path):
            self.app.set_status("No output file for this calculation yet.")
            return
        self._os_open(path)

    def _open_structure(self, calc):
        """Open the calc in the external 3D viewer (Avogadro / molden). Uses the
        Calculations tab's smart picker, so a FREQ job opens its .out — letting you
        animate the normal modes — while geometry jobs open the optimised .xyz."""
        ct = getattr(self.app, "calculations_tab", None)
        path = ct.viewer_file_for_calc(calc) if ct is not None else None
        if not path:
            self.app.set_status("No geometry/output file for this calculation yet.")
            return
        from orca_workbench.ui.molecules_tab import open_xyz_3d
        open_xyz_3d(self, self.app, path)

    def _os_open(self, path):
        import platform
        import subprocess
        try:
            if platform.system() == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.app.set_status("Could not open {}: {}".format(os.path.basename(path), e))

    def _set_cfg(self, node, key, value):
        node.config[key] = value
        self._commit()
        self._redraw()

    def _build_report_format(self, node):
        """Output-format selector for a Report node + (in CSV mode) a custom
        column editor. Rows are always calculations; columns are user-chosen
        properties with custom headers and left-to-right order."""
        f = self.cfg_frame
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(f, text="Output format:", font=("TkDefaultFont", 9, "bold")).pack(
            anchor=tk.W, padx=8)
        fmt = tk.StringVar(value=node.config.get("format", "both"))

        def on_fmt():
            self._set_cfg(node, "format", fmt.get())
            self._build_config_panel()   # show/hide the CSV editor
        for val, txt in (("both", "JSON + CSV"), ("json", "JSON only"), ("csv", "CSV only")):
            ttk.Radiobutton(f, text=txt, variable=fmt, value=val, command=on_fmt).pack(
                anchor=tk.W, padx=16)

        if fmt.get() == "json":
            return   # no CSV options when JSON-only
        ttk.Button(f, text="Customise CSV columns...",
                   command=lambda: self._edit_csv_columns(node)).pack(anchor=tk.W, padx=8, pady=(4, 0))
        cols = node.config.get("csv_columns")
        summary = ("all default columns" if not cols
                   else "{} custom column(s)".format(len(cols)))
        ttk.Label(f, text="CSV: one row per calculation · " + summary,
                  foreground="#777", wraplength=220, justify=tk.LEFT).pack(anchor=tk.W, padx=8)
        ttk.Label(f, text="Missing value:").pack(anchor=tk.W, padx=8, pady=(4, 0))
        miss = tk.StringVar(value=node.config.get("csv_missing", ""))
        for val, txt in (("", "Empty cell"), ("NaN", "NaN")):
            ttk.Radiobutton(f, text=txt, variable=miss, value=val,
                            command=lambda v=miss: self._set_cfg(node, "csv_missing", v.get())
                            ).pack(anchor=tk.W, padx=16)

    def _edit_csv_columns(self, node):
        """Dialog to pick CSV columns, rename their headers, and order them
        left-to-right. Stored as node.config['csv_columns'] (None = all). Uses the
        shared editor so the Report tab and Report node behave identically."""
        from orca_workbench.ui.csv_columns import edit_csv_columns_dialog

        def on_save(cols):
            self._set_cfg(node, "csv_columns", cols)
            self._build_config_panel()

        edit_csv_columns_dialog(self, node.config.get("csv_columns"),
                                "CSV columns — " + node.config.get("name", "report"),
                                on_save)

    def _build_report_extractors(self, node):
        """Property checkboxes for a Report node — the same selection the Report tab
        offers, so you choose what goes into this node's JSON/CSV. Stored as
        node.config['extractors'] (list of keys; absent/None = everything, so newly
        added extractors are included automatically)."""
        from orca_workbench.core import reporting
        ttk.Separator(self.cfg_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=(8, 2))
        ttk.Label(self.cfg_frame, text="Properties to extract:",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W, padx=8)
        chosen = node.config.get("extractors")   # None => all
        allkeys = [ex.key for ex in reporting.EXTRACTORS]
        cvars = {}

        def on_toggle(*_a):
            keys = [k for k in allkeys if cvars[k].get()]
            # store None when everything is ticked, so future extractors are included
            self._set_cfg(node, "extractors", None if keys == allkeys else keys)

        # Checkboxes go straight into the (scrollable) settings panel — no inner
        # scroll of their own, so one wheel scrolls the whole panel. Press-and-drag
        # across them to flip several at once (like the Report tab).
        self._rep_paint_vars = {}
        self._rep_on_toggle = on_toggle
        for ex in reporting.EXTRACTORS:
            v = tk.BooleanVar(value=(chosen is None or ex.key in chosen))
            cvars[ex.key] = v
            cb = ttk.Checkbutton(self.cfg_frame, text="{}  ({})".format(ex.label, ex.applies_hint),
                                 variable=v, command=on_toggle)
            cb.pack(anchor=tk.W, padx=12, pady=1)
            self._rep_paint_vars[str(cb)] = v
            cb.bind("<Button-1>", lambda e, var=v: self._rep_paint_press(var))
            cb.bind("<B1-Motion>", self._rep_paint_motion)
            cb.bind("<ButtonRelease-1>", self._rep_paint_release)

    def _rep_paint_press(self, var):
        self._rep_painting = True
        self._rep_paint_value = not bool(var.get())
        var.set(self._rep_paint_value)
        self._rep_on_toggle()
        return "break"   # suppress the Checkbutton's own toggle (we set it directly)

    def _rep_paint_motion(self, event):
        if not getattr(self, "_rep_painting", False):
            return
        w = self.winfo_containing(event.x_root, event.y_root)
        var = self._rep_paint_vars.get(str(w)) if w is not None else None
        if var is not None and bool(var.get()) != self._rep_paint_value:
            var.set(self._rep_paint_value)
            self._rep_on_toggle()

    def _rep_paint_release(self, _event):
        self._rep_painting = False
        return "break"

    # ----------------------------------------------------- workflow <-> project

    def refresh(self):
        self.wf = wf_mod.Workflow.from_dict(self.app.project.workflow)
        # Only wipe the undo history when the graph actually changed underneath us
        # (project opened / new / cleared) — not on incidental refreshes, so undo
        # survives a normal editing session.
        if self._undo_baseline is None or self.app.project.workflow != self._undo_baseline:
            self._reset_undo()
            self._edit_run_ack = False   # a fresh graph: re-arm the executed-edit gate
        # drop selections that no longer exist
        self._sel_nodes = [nid for nid in self._sel_nodes if self.wf.node(nid) is not None]
        if self._sel_edge is not None and self.wf.edge(self._sel_edge) is None:
            self._sel_edge = None
        self._redraw()
        self._build_config_panel()

    def _commit(self):
        cur = self.wf.to_dict()
        self.app.project.workflow = cur
        self.app.mark_dirty()
        base = self._undo_baseline
        if base is not None and base != cur:
            self._undo_stack.append(base)
            if len(self._undo_stack) > 100:
                self._undo_stack.pop(0)
            self._redo_stack = []
        self._undo_baseline = cur

    def _reset_undo(self):
        """Start a fresh undo history from the current graph (used when the graph is
        (re)loaded from the project)."""
        self._undo_stack = []
        self._redo_stack = []
        self._undo_baseline = self.wf.to_dict()

    def _restore_graph(self, d):
        """Replace the graph with snapshot dict `d` WITHOUT going through _commit
        (so undo/redo don't feed themselves)."""
        self.wf = wf_mod.Workflow.from_dict(d)
        self.app.project.workflow = self.wf.to_dict()
        self.app.mark_dirty()
        self._undo_baseline = self.app.project.workflow
        self._sel_nodes = [nid for nid in self._sel_nodes if self.wf.node(nid) is not None]
        self._sel_edge = None
        self._redraw()
        self._build_config_panel()

    def _undo_graph(self, _event=None):
        if not self._undo_stack:
            return "break"
        self._redo_stack.append(self.wf.to_dict())
        self._restore_graph(self._undo_stack.pop())
        self.app.set_status("Undo (node graph).")
        return "break"

    def _redo_graph(self, _event=None):
        if not self._redo_stack:
            return "break"
        self._undo_stack.append(self.wf.to_dict())
        self._restore_graph(self._redo_stack.pop())
        self.app.set_status("Redo (node graph).")
        return "break"

    # --------------------------------------------------------------- geometry

    def _node_height(self, node):
        n = max(len(node.inputs()), len(node.outputs()), 1)
        return TITLE_H + SUMMARY_H + n * PORT_H + 8

    def _title_font_obj(self):
        f = getattr(self, "_title_font", None)
        if f is None:
            try:
                import tkinter.font as tkfont
                f = tkfont.Font(font=("TkDefaultFont", 9, "bold"))
            except Exception:
                f = False
            self._title_font = f
        return f or None

    def _node_width(self, node):
        """World-unit node width: the base width widened to fit the (bold) title,
        so long labels (e.g. 'Property (SP/NMR/UV-Vis/…)') don't overflow the box.
        Used everywhere the node's right edge / port-x is computed, so draw and
        hit-testing stay consistent."""
        f = self._title_font_obj()
        try:
            measured = f.measure(node.label) if f else int(len(node.label) * 7.2)
        except Exception:
            measured = int(len(node.label) * 7.2)
        return float(max(NODE_W, measured + 36))   # +margin so titles never touch the edge

    def _is_annotation(self, node):
        return node is not None and node.kind == "annotation"

    def _node_rect(self, node):
        """(x, y, w, h) in world units for any node — regular (width fits the
        title, height fits the ports) or annotation (its own config w/h)."""
        if self._is_annotation(node):
            return (node.x, node.y, float(node.config.get("w", 200.0)),
                    float(node.config.get("h", 90.0)))
        return (node.x, node.y, self._node_width(node), self._node_height(node))

    def _nodes_in_frame(self, frame):
        """Ids of non-frame nodes whose centre lies inside the frame's rect — the
        nodes a frame drags along with it."""
        fx, fy, fw, fh = self._node_rect(frame)
        out = []
        for n in self.wf.nodes:
            if n.id == frame.id or n.type == "frame":
                continue
            x, y, w, h = self._node_rect(n)
            if fx <= x + w / 2.0 <= fx + fw and fy <= y + h / 2.0 <= fy + fh:
                out.append(n.id)
        return out

    def _resize_handle_at(self, cx, cy):
        """Id of an annotation whose bottom-right resize handle is under (cx, cy)."""
        tol = 9.0 / max(self._zoom, 0.05)
        for n in reversed(self.wf.nodes):
            if not self._is_annotation(n):
                continue
            x, y, w, h = self._node_rect(n)
            if abs(cx - (x + w)) <= tol and abs(cy - (y + h)) <= tol:
                return n.id
        return None

    def _port_xy(self, node, port_name, is_input):
        ports = node.inputs() if is_input else node.outputs()
        for i, (name, _t) in enumerate(ports):
            if name == port_name:
                y = node.y + TITLE_H + SUMMARY_H + i * PORT_H + PORT_H / 2
                x = node.x if is_input else node.x + self._node_width(node)
                return x, y
        return None

    # --------------------------------------------------------------- drawing

    def _redraw(self):
        self.canvas.delete("all")
        self._hover_node = None   # the glow ring was cleared with "all"; next Motion re-adds
        # Frames sit behind everything; comments behind the real nodes; then edges;
        # then the real (computational) nodes on top.
        for n in self.wf.nodes:
            if n.type == "frame":
                self._draw_frame(n)
        for n in self.wf.nodes:
            if n.type == "comment":
                self._draw_comment(n)
        detached = (self._drag or {}).get("detach") if self._mode == "wire" else None
        # Wire under a dragged isolated node (would splice) — drawn highlighted.
        splice_eid = (self._drag or {}).get("splice_edge") if self._mode == "drag" else None
        for e in self.wf.edges:
            if e.id == detached:
                continue   # being re-dragged; shown as the temp wire instead
            self._draw_edge(e, highlight=(e.id == splice_eid))
        for n in self.wf.nodes:
            if not self._is_annotation(n):
                self._draw_node(n)
        # transient wire while connecting
        if self._mode == "wire" and self._drag and self._drag.get("temp"):
            x0, y0 = self._w2s(*self._drag["from_xy"])
            x1, y1 = self._w2s(*self._drag["cur"])
            self.canvas.create_line(x0, y0, x1, y1, fill=self._np["wire"], width=2, dash=(3, 2),
                                    tags=("temp",))
        # transient rubber-band rectangle while box-selecting
        if self._mode == "box" and self._drag:
            x0, y0 = self._w2s(self._drag["x0"], self._drag["y0"])
            x1, y1 = self._w2s(*self._drag["cur"])
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=self._np["sel"], width=1,
                                         dash=(4, 3), tags=("temp",))

    def _draw_node(self, node):
        z = self._zoom
        x, y = self._w2s(node.x, node.y)             # screen top-left
        w = self._node_width(node) * z
        h = self._node_height(node) * z
        th = TITLE_H * z
        sh = SUMMARY_H * z
        selected = node.id in self._sel_nodes
        ntag = "N:" + node.id
        np = self._np
        live = self._node_live_state(node)
        if selected:
            outline, width = np["sel"], 3
        elif live:
            outline, width = _STATE_COLOR.get(live, np["outline"]), 3
        else:
            outline, width = np["outline"], 1
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=np["body"], outline=outline,
                                     width=width, tags=(ntag, "nodebody"))
        self.canvas.create_rectangle(x, y, x + w, y + th,
                                     fill=np["kinds"].get(node.kind, np["body"]),
                                     outline=outline, width=width, tags=(ntag,))
        self.canvas.create_text(x + 8 * z, y + th / 2, anchor=tk.W, text=node.label,
                                fill=np["fg"],
                                font=("TkDefaultFont", self._fs(9), "bold"), tags=(ntag,))
        if live:
            # status badge — a filled dot at the title's right edge
            bx = x + w - 11 * z
            by = y + th / 2
            r = 5 * z
            self.canvas.create_oval(bx - r, by - r, bx + r, by + r,
                                    fill=_STATE_COLOR.get(live, "#888"), outline=np["port_ring"],
                                    tags=(ntag,))
        # config summary — in its own band under the title (above the ports, so
        # it never overlaps the port labels), centred and single-line-clipped.
        summ = self._node_summary(node)
        if summ:
            self.canvas.create_text(x + w / 2, y + th + sh / 2,
                                    anchor=tk.CENTER, text=self._fit_summary(summ),
                                    font=("TkDefaultFont", self._fs(8)), fill=np["summary_fg"],
                                    tags=(ntag,))
        # ports (below the summary band)
        py0 = y + th + sh
        step = PORT_H * z
        for i, (name, ptype) in enumerate(node.inputs()):
            self._draw_port(node.id, name, True, x, py0 + i * step + step / 2, ptype)
        for i, (name, ptype) in enumerate(node.outputs()):
            self._draw_port(node.id, name, False, x + w, py0 + i * step + step / 2, ptype)
        # live progress caption under the node (KNIME-style), once it has run data
        prog = self._node_progress(node)
        if prog:
            text, col = prog
            self.canvas.create_text(x + w / 2, y + h + 9 * z, anchor=tk.CENTER, text=text,
                                    font=("TkDefaultFont", self._fs(7), "bold"), fill=col,
                                    tags=(ntag,))

    def _fit_summary(self, text):
        """Trim a summary string so it fits one line inside the node."""
        if len(text) <= 24:
            return text
        return text[:23] + "…"

    def _node_summary(self, node):
        if node.type in wf_mod.CALC_NODE_TYPES:
            return node.config.get("recipe", "") or "(pick a recipe)"
        if node.type == "molecules":
            if node.config.get("mode") == "selection":
                return "{} selected".format(len(node.config.get("filenames", [])))
            return "all molecules"
        if node.type == "condition":
            pred = node.config.get("predicate", "")
            return {"no_imaginary_freqs": "if no imag. freq.",
                    "has_imaginary_freqs": "if imag. freq.",
                    "terminated_ok": "if terminated OK"}.get(pred, pred)
        if node.type == "report":
            return node.config.get("name", "report") + ".json"
        if node.type == "transform":
            n = len(node.config.get("ops") or [])
            return "{} op{}".format(n, "" if n == 1 else "s") if n else "(no ops yet)"
        if node.type == "combine":
            mode = node.config.get("mode", "merge")
            return "pairwise" if mode == "pairwise" else "merge all inputs"
        return ""

    def _draw_port(self, node_id, name, is_input, x, y, ptype):
        z = self._zoom
        r = PORT_R * z
        np = self._np
        color = np["port_geom"] if ptype == "geometry" else np["port_results"]
        tag = "P:{}:{}:{}".format(node_id, "in" if is_input else "out", name)
        self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                fill=color, outline=np["port_ring"], tags=(tag, "port"))
        lx = x + (r + 3 * z) if is_input else x - (r + 3 * z)
        self.canvas.create_text(lx, y, anchor=(tk.W if is_input else tk.E), text=name,
                                font=("TkDefaultFont", self._fs(7)), fill=np["port_label"],
                                tags=("P:" + node_id,))

    def _draw_resize_handle(self, x, y, w, h, ntag, color):
        z = self._zoom
        s = 6 * z
        self.canvas.create_line(x + w - s, y + h, x + w, y + h - s, fill=color, tags=(ntag,))
        self.canvas.create_line(x + w - s * 1.9, y + h, x + w, y + h - s * 1.9, fill=color, tags=(ntag,))

    def _draw_comment(self, node):
        z = self._zoom
        x, y = self._w2s(node.x, node.y)
        _x, _y, ww, hh = self._node_rect(node)
        w, h = ww * z, hh * z
        sel = node.id in self._sel_nodes
        ntag = "N:" + node.id
        np = self._np
        outline = np["sel"] if sel else np["comment_outline"]
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=np["comment_bg"], outline=outline,
                                     width=2 if sel else 1, tags=(ntag, "nodebody"))
        self.canvas.create_text(x + 7 * z, y + 6 * z, anchor=tk.NW, width=max(10.0, w - 14 * z),
                                text=node.config.get("text", ""),
                                font=("TkDefaultFont", self._fs(9)), fill=np["comment_fg"],
                                tags=(ntag,))
        self._draw_resize_handle(x, y, w, h, ntag, outline)

    def _draw_frame(self, node):
        z = self._zoom
        x, y = self._w2s(node.x, node.y)
        _x, _y, ww, hh = self._node_rect(node)
        w, h = ww * z, hh * z
        sel = node.id in self._sel_nodes
        ntag = "N:" + node.id
        th = 20 * z
        # transparent body so contained nodes show through; coloured title bar
        np = self._np
        outline = np["sel"] if sel else np["frame_outline"]
        self.canvas.create_rectangle(x, y, x + w, y + h, fill="", outline=outline,
                                     width=2 if sel else 1, tags=(ntag, "nodebody"))
        self.canvas.create_rectangle(x, y, x + w, y + th, fill=np["frame_bg"], outline=outline,
                                     width=1, tags=(ntag,))
        self.canvas.create_text(x + 7 * z, y + th / 2, anchor=tk.W,
                                text=node.config.get("title", "Group"),
                                font=("TkDefaultFont", self._fs(9), "bold"), fill=np["frame_fg"],
                                tags=(ntag,))
        self._draw_resize_handle(x, y, w, h, ntag, outline)

    def _draw_edge(self, e, highlight=False):
        src = self.wf.node(e.src_node)
        dst = self.wf.node(e.dst_node)
        if src is None or dst is None:
            return
        a = self._port_xy(src, e.src_port, is_input=False)
        b = self._port_xy(dst, e.dst_port, is_input=True)
        if not a or not b:
            return
        a = self._w2s(*a)
        b = self._w2s(*b)
        selected = self._sel_edge == e.id
        np = self._np
        if highlight:                       # a dropped node would splice HERE
            col, w = np["splice"], 4
        elif selected:
            col, w = np["wire_sel"], 3
        else:
            col, w = np["wire"], 2
        dx = max(30 * self._zoom, abs(b[0] - a[0]) * 0.4)
        self.canvas.create_line(a[0], a[1], a[0] + dx, a[1], b[0] - dx, b[1], b[0], b[1],
                                smooth=True, width=w, fill=col, tags=("E:" + e.id, "edge"))

    # --------------------------------------------------------------- events

    def _cxy(self, event):
        # screen (widget) pixels -> world coordinates
        return self._s2w(event.x, event.y)

    def _hit(self, event):
        """Return ('port', node, port, is_input) | ('node', id) | ('edge', id) | None."""
        item = self.canvas.find_withtag("current")
        if not item:
            return None
        for t in self.canvas.gettags(item[0]):
            if t.startswith("P:") and t.count(":") == 3:
                _, nid, io, name = t.split(":")
                return ("port", nid, name, io == "in")
            if t.startswith("N:"):
                return ("node", t[2:])
            if t.startswith("E:"):
                return ("edge", t[2:])
        return None

    def _on_press(self, event):
        self.canvas.focus_set()
        self._align_ctx = None      # any click ends the arrows-tune-spacing mode
        cx, cy = self._cxy(event)
        ctrl = bool(event.state & 0x0004)
        # resize handle of a comment / frame takes priority over everything
        rid = self._resize_handle_at(cx, cy)
        if rid is not None:
            n = self.wf.node(rid)
            self._select_only(rid)
            self._mode = "resize"
            self._drag = {"nid": rid, "ox": cx, "oy": cy,
                          "w0": float(n.config.get("w", 200.0)),
                          "h0": float(n.config.get("h", 90.0))}
            return
        hit = self._hit(event)
        if hit is None:
            # empty space → box select (Ctrl extends the current selection)
            self._mode = "box"
            self._drag = {"x0": cx, "y0": cy, "cur": (cx, cy), "add": ctrl,
                          "base": list(self._sel_nodes)}
            return
        if hit[0] == "port" and not hit[3]:   # output port → start a wire
            node = self.wf.node(hit[1])
            self._mode = "wire"
            self._drag = {"src": (hit[1], hit[2]),
                          "from_xy": self._port_xy(node, hit[2], False),
                          "cur": (cx, cy), "temp": True}
            return
        if hit[0] == "node":
            nid = hit[1]
            if ctrl:
                # Ctrl+CLICK toggles selection; Ctrl+DRAG duplicates the selection
                # (or just this node) and moves the copy — PowerPoint-style. Start a
                # copy-pending drag; if it never moves, _on_release toggles instead.
                self._mode = "drag"
                self._drag = {"ox": cx, "oy": cy, "moved": False,
                              "copy_pending": nid, "sel_snapshot": list(self._sel_nodes)}
                return
            if nid not in self._sel_nodes:
                self._select_only(nid)
            # drag the whole current selection; if it's a group, a click-without-
            # drag collapses to just this node (handled on release).
            self._begin_node_drag(cx, cy,
                                  collapse_to=nid if len(self._sel_nodes) > 1 else None)
            return
        if hit[0] == "edge":
            self._select_edge(hit[1])
            self._mode = None
            self._drag = None
            return
        if hit[0] == "port" and hit[3]:       # input port
            ein = self.wf.edges_into(hit[1], hit[2])
            if ein and not ctrl:
                # A CONNECTED input: pick the wire up — drag it to another
                # input to rewire, or drop it on empty space to delete it.
                e = ein[-1]
                src = self.wf.node(e.src_node)
                self._mode = "wire"
                self._drag = {"src": (e.src_node, e.src_port),
                              "from_xy": self._port_xy(src, e.src_port, False),
                              "cur": (cx, cy), "temp": True, "detach": e.id}
                self._redraw()
                return
            if ctrl:
                self._toggle_node(hit[1])
            else:
                self._select_only(hit[1])
            self._mode = None
            self._drag = None

    def _begin_node_drag(self, cx, cy, collapse_to=None):
        ids = set(self._sel_nodes)
        # a frame drags the nodes it contains along with it
        for nid in list(ids):
            n = self.wf.node(nid)
            if n is not None and n.type == "frame":
                ids.update(self._nodes_in_frame(n))
        orig = {}
        for nid in ids:
            n = self.wf.node(nid)
            if n is not None:
                orig[nid] = (n.x, n.y)
        self._mode = "drag"
        self._drag = {"orig": orig, "ox": cx, "oy": cy, "moved": False,
                      "collapse_to": collapse_to}

    def _duplicate_node_ids(self, node_ids, dx=0.0, dy=0.0):
        """Duplicate `node_ids` (fresh ids; wires INTERNAL to the set preserved;
        positions offset by dx/dy). Returns the new node ids. Does NOT commit — the
        caller commits once (so a Ctrl+drag copy is a single undo step)."""
        import copy
        ids = set(node_ids)
        internal = [e for e in self.wf.edges if e.src_node in ids and e.dst_node in ids]
        idmap, new_ids = {}, []
        for nid in node_ids:
            n = self.wf.node(nid)
            if n is None:
                continue
            nn = self.wf.add_node(n.type, n.x + dx, n.y + dy, copy.deepcopy(n.config))
            idmap[nid] = nn.id
            new_ids.append(nn.id)
        for e in internal:
            if e.src_node in idmap and e.dst_node in idmap:
                self.wf.add_edge(idmap[e.src_node], e.src_port, idmap[e.dst_node], e.dst_port)
        return new_ids

    def _materialize_copy_drag(self):
        """Turn a Ctrl+drag (copy-pending) into real duplicated nodes the drag then
        moves — the copies sit on the originals, then follow the cursor. Copies the
        whole selection if the pressed node was part of it, else just that node."""
        d = self._drag
        nid = d.get("copy_pending")
        snap = d.get("sel_snapshot", [])
        source = list(snap) if nid in snap else [nid]
        new_ids = self._duplicate_node_ids(source)
        d.pop("copy_pending", None)
        if not new_ids:
            return
        self._sel_nodes = new_ids
        self._sel_edge = None
        d["orig"] = {i: (self.wf.node(i).x, self.wf.node(i).y)
                     for i in new_ids if self.wf.node(i) is not None}
        d["copied"] = True

    def _on_motion(self, event):
        cx, cy = self._cxy(event)
        if self._mode == "resize" and self._drag:
            n = self.wf.node(self._drag["nid"])
            if n is not None:
                n.config["w"] = max(80.0, self._drag["w0"] + (cx - self._drag["ox"]))
                n.config["h"] = max(46.0, self._drag["h0"] + (cy - self._drag["oy"]))
                self._redraw()
            return
        if self._mode == "drag" and self._drag:
            dx, dy = cx - self._drag["ox"], cy - self._drag["oy"]
            if abs(dx) > 2 or abs(dy) > 2:
                self._drag["moved"] = True
            # Ctrl+drag: on the first real movement, duplicate the source nodes and
            # drag the COPIES (the originals stay put) — PowerPoint-style.
            if (self._drag.get("copy_pending") is not None and self._drag["moved"]
                    and "orig" not in self._drag):
                self._materialize_copy_drag()
            if "orig" not in self._drag:
                return   # copy-pending but hasn't moved far enough to copy yet
            # Shift locks movement to the dominant axis (horizontal or vertical,
            # whichever the cursor has moved more in) — PowerPoint-style.
            if bool(event.state & 0x0001):
                if abs(dx) >= abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            for nid, (ox, oy) in self._drag["orig"].items():
                n = self.wf.node(nid)
                if n is not None:
                    n.x, n.y = ox + dx, oy + dy
            # Live splice affordance: a single isolated node dragged over a wire
            # highlights that wire, so the user sees where a drop would insert it.
            self._drag["splice_edge"] = self._splice_candidate(self._drag)
            self._redraw()
            return
        if self._mode == "wire" and self._drag:
            self._drag["cur"] = (cx, cy)
            self._redraw()
            return
        if self._mode == "box" and self._drag:
            self._drag["cur"] = (cx, cy)
            self._apply_box_selection()
            self._redraw()

    def _on_release(self, event):
        cx, cy = self._cxy(event)
        mode, drag = self._mode, self._drag
        self._mode = None
        self._drag = None
        if mode == "resize" and drag:
            self._commit()
            return
        if mode == "drag" and drag:
            if drag.get("copy_pending") is not None and not drag.get("moved"):
                self._toggle_node(drag["copy_pending"])   # Ctrl+click, no drag = toggle
                return
            if drag.get("moved"):
                # Dropping a lone, unconnected node onto a wire splices it in.
                self._maybe_splice_at_drop(cx, cy, drag)
                self._commit()
                self._redraw()
                if drag.get("copied"):
                    self._build_config_panel()
                    self.app.set_status("Duplicated {} node(s).".format(len(drag["orig"])))
            elif drag.get("collapse_to"):
                self._select_only(drag["collapse_to"])
            return
        if mode == "wire" and drag:
            self._finish_wire(cx, cy, event, drag)
            return
        if mode == "box" and drag:
            moved = abs(cx - drag["x0"]) > 3 or abs(cy - drag["y0"]) > 3
            if not moved and not drag["add"]:
                self._sel_nodes = []
                self._sel_edge = None
            self._redraw()
            self._build_config_panel()

    # ---- wiring on drop ----

    def _finish_wire(self, cx, cy, event, drag):
        sn, sp = drag["src"]
        # 1) released on (or near) an input port?
        target = self._input_port_at(cx, cy)
        # 2) released on a node body → its first compatible input
        if target is None:
            nid = self._node_at(cx, cy)
            if nid is not None and nid != sn:
                dp = self._compatible_input_port(sn, sp, nid)
                if dp is not None:
                    target = (nid, dp)
        # A wire PICKED UP from a connected input (detach-drag): re-plug it on a
        # target, or delete the connection on an empty drop — never the add-node
        # popup (that's for drawing NEW wires from an output).
        detach = drag.get("detach")
        if detach is not None:
            old = self.wf.edge(detach)
            if old is not None and target == (old.dst_node, old.dst_port):
                self._redraw()                      # dropped back where it was
                return
            if old is not None and not self._confirm_destructive_edit("Rewiring a connection"):
                self._redraw()                      # keep the wire as it was
                return
            if old is not None:
                self.wf.remove_edge(detach)
            if target is None:
                self._commit()
                self._redraw()
                self.app.set_status("Connection removed.")
                return
            if not self._try_add_edge(sn, sp, target[0], target[1]) and old is not None:
                self.wf.edges.append(old)           # invalid target: keep the old wire
            self._redraw()
            return
        if target is not None:
            self._try_add_edge(sn, sp, target[0], target[1])
            self._redraw()
            return
        # 3) released in empty space → Blender-style add-node search, then connect
        self._redraw()  # clear the temp wire before the popup
        ntype = self._node_search_popup(event.x_root, event.y_root,
                                        out_type=self._out_port_type(sn, sp), src_node=sn)
        if ntype:
            node = self.wf.add_node(ntype, cx, cy - TITLE_H / 2.0)
            self._commit()
            dp = self._compatible_input_port(sn, sp, node.id)
            if dp is not None:
                self._try_add_edge(sn, sp, node.id, dp)
            else:
                self.app.set_status("Added {} (no matching input to connect).".format(node.label))
            self._select_only(node.id)
        self._redraw()

    def _try_add_edge(self, sn, sp, dn, dp):
        edge, why = self.wf.add_edge(sn, sp, dn, dp)
        if edge is None and why == "input already connected":
            # Standard node-editor behaviour: dropping a wire on an occupied
            # single input REPLACES the old connection (put back if the new
            # one turns out invalid, e.g. it would make a cycle).
            if not self._confirm_destructive_edit("Replacing a connection"):
                return False
            old = list(self.wf.edges_into(dn, dp))
            for oe in old:
                self.wf.remove_edge(oe.id)
            edge, why = self.wf.add_edge(sn, sp, dn, dp)
            if edge is None:
                self.wf.edges.extend(old)
        if edge is None and why:
            self.app.set_status("Can't connect: " + why)
            return False
        self._commit()
        return True

    # ---- splice a dropped node into a wire ----

    def _splice_candidate(self, drag):
        """The edge id a currently-dragged single isolated node would splice
        into (its centre is over the wire and the ports are type-compatible), or
        None. Shared by the live hover-highlight and the drop handler."""
        if len(drag.get("orig", {})) != 1:
            return None
        nid = next(iter(drag["orig"]))
        node = self.wf.node(nid)
        if node is None or self._is_annotation(node):
            return None
        if self.wf.edges_into(nid) or self.wf.edges_out(nid):
            return None                             # only an isolated node splices
        cx = node.x + self._node_width(node) / 2.0
        cy = node.y + self._node_height(node) / 2.0
        eid = self._edge_near(cx, cy)
        if eid is None:
            return None
        e = self.wf.edge(eid)
        if e is None or e.src_node == nid or e.dst_node == nid:
            return None
        etype = self._out_port_type(e.src_node, e.src_port)
        in_port = self._compatible_input_port(e.src_node, e.src_port, nid)
        out_port = next((name for name, t in node.outputs() if t == etype), None)
        if in_port is None or out_port is None:
            return None
        return eid

    def _maybe_splice_at_drop(self, cx, cy, drag):
        """Standard node-editor move: dropping an ISOLATED node onto a connection
        splices it in — old src feeds the node, the node feeds the old
        destination — and pushes the downstream nodes right to make room."""
        eid = drag.get("splice_edge") or self._splice_candidate(drag)
        if eid is None:
            return
        if not self._confirm_destructive_edit("Splicing a node into a wire"):
            return
        nid = next(iter(drag["orig"]))
        node = self.wf.node(nid)
        e = self.wf.edge(eid)
        if e is None:
            return
        etype = self._out_port_type(e.src_node, e.src_port)
        in_port = self._compatible_input_port(e.src_node, e.src_port, nid)
        out_port = next((name for name, t in node.outputs() if t == etype), None)
        if in_port is None or out_port is None:
            return
        src_node, dst_node, dst_port = e.src_node, e.dst_node, e.dst_port
        self.wf.remove_edge(eid)
        e1, _w1 = self.wf.add_edge(src_node, e.src_port, nid, in_port)
        e2, _w2 = self.wf.add_edge(nid, out_port, dst_node, dst_port)
        if e1 is None or e2 is None:                # shouldn't happen — restore
            if e1 is not None:
                self.wf.remove_edge(e1.id)
            if e2 is not None:
                self.wf.remove_edge(e2.id)
            self.wf.edges.append(e)
            return
        # Sit the new node on the wire (between src and dst) and push the dst
        # side right so it doesn't overlap the inserted node.
        s, d = self.wf.node(src_node), self.wf.node(dst_node)
        if s is not None and d is not None:
            node.x = s.x + self._node_width(s) + 40.0
            node.y = (s.y + d.y) / 2.0
            need = node.x + self._node_width(node) + 40.0 - d.x
            if need > 0:
                self._shift_subtree_right(dst_node, need)
        self.app.set_status("Spliced {} into the connection.".format(node.label))

    def _shift_subtree_right(self, start_id, dx):
        """Move `start_id` and everything reachable downstream of it right by dx
        (to make room for a spliced-in node). Skips the node being inserted."""
        seen = set()
        stack = [start_id]
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            n = self.wf.node(nid)
            if n is not None and not self._is_annotation(n):
                n.x += dx
            stack.extend(e.dst_node for e in self.wf.edges_out(nid))

    def _edge_near(self, cx, cy, tol=16.0):
        """The edge whose wire runs within `tol` world units of (cx, cy), or
        None. Distance is measured to the polyline through the same control
        points the smooth bezier is drawn with — close enough for hit-testing."""
        def seg_dist(px, py, a, b):
            ax, ay = a
            bx, by = b
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            t = 0.0 if L2 <= 1e-12 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
            qx, qy = ax + t * vx, ay + t * vy
            return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5

        best = None
        for e in self.wf.edges:
            src, dst = self.wf.node(e.src_node), self.wf.node(e.dst_node)
            if src is None or dst is None:
                continue
            a = self._port_xy(src, e.src_port, is_input=False)
            b = self._port_xy(dst, e.dst_port, is_input=True)
            if not a or not b:
                continue
            dx = max(30.0, abs(b[0] - a[0]) * 0.4)
            pts = [a, (a[0] + dx, a[1]), (b[0] - dx, b[1]), b]
            d = min(seg_dist(cx, cy, pts[k], pts[k + 1]) for k in range(3))
            if d <= tol and (best is None or d < best[0]):
                best = (d, e.id)
        return best[1] if best else None

    # ---- hit-testing in canvas coords (robust to the temp wire on top) ----

    def _input_port_at(self, cx, cy):
        r = PORT_R + 8
        for n in self.wf.nodes:
            for i, (name, _t) in enumerate(n.inputs()):
                px = n.x
                py = n.y + TITLE_H + SUMMARY_H + i * PORT_H + PORT_H / 2.0
                if abs(px - cx) <= r and abs(py - cy) <= r:
                    return (n.id, name)
        return None

    def _node_at(self, cx, cy):
        # Real (computational) nodes are on top, then comments, then frames behind.
        for keep in (lambda m: not self._is_annotation(m),
                     lambda m: m.type == "comment",
                     lambda m: m.type == "frame"):
            for n in reversed([m for m in self.wf.nodes if keep(m)]):
                x, y, w, h = self._node_rect(n)
                if x <= cx <= x + w and y <= cy <= y + h:
                    return n.id
        return None

    def _on_hover(self, event):
        """Glow the node under the cursor. Cheap: it only redraws the glow ring
        when the hovered node CHANGES (plain motion over the same node does
        nothing), so it's safe over ThinLinc (no per-event canvas redraw)."""
        if self._mode or self._drag or self._pan:
            return   # an active drag/pan owns the canvas
        cx, cy = self._s2w(event.x, event.y)
        nid = self._node_at(cx, cy)
        if nid is not None:
            n = self.wf.node(nid)
            if n is not None and self._is_annotation(n):
                nid = None   # annotations don't glow (they have their own affordances)
        if nid == self._hover_node:
            return
        self._hover_node = nid
        self._draw_hover_glow()

    def _draw_hover_glow(self):
        """(Re)draw just the hover ring for self._hover_node — a themeable glow
        (np['hover']), or red (np['hover_bad']) when the node can't currently work
        (missing recipe / input, misplaced Transform, Combine without charge/mult).
        Tagged 'hoverglow' so it's the only thing this touches."""
        self.canvas.delete("hoverglow")
        nid = self._hover_node
        node = self.wf.node(nid) if nid else None
        if node is None:
            return
        z = self._zoom
        x, y = self._w2s(node.x, node.y)
        w = self._node_width(node) * z
        h = self._node_height(node) * z
        col = self._np["hover"] if self.wf.node_ok(nid) else self._np["hover_bad"]
        pad = 3 * z + 1
        self.canvas.create_rectangle(x - pad, y - pad, x + w + pad, y + h + pad,
                                     outline=col, width=2, tags=("hoverglow",))

    def _out_port_type(self, node_id, port):
        n = self.wf.node(node_id)
        return n.port_type(port, is_input=False) if n is not None else None

    def _compatible_input_port(self, src_node, src_port, dst_node_id):
        dst = self.wf.node(dst_node_id)
        if dst is None:
            return None
        for name, _t in dst.inputs():
            ok, _why = self.wf.can_connect(src_node, src_port, dst_node_id, name)
            if ok:
                return name
        return None

    def _apply_box_selection(self):
        d = self._drag
        lo_x, hi_x = sorted((d["x0"], d["cur"][0]))
        lo_y, hi_y = sorted((d["y0"], d["cur"][1]))
        inside = []
        for n in self.wf.nodes:
            x, y, w, h = self._node_rect(n)
            if x <= hi_x and x + w >= lo_x and y <= hi_y and y + h >= lo_y:
                inside.append(n.id)
        if d["add"]:
            self._sel_nodes = d["base"] + [nid for nid in inside if nid not in d["base"]]
        else:
            self._sel_nodes = inside
        self._sel_edge = None

    # ---- selection helpers ----

    def _select_only(self, nid):
        self._sel_nodes = [nid]
        self._sel_edge = None
        self._redraw()
        self._build_config_panel()

    def _toggle_node(self, nid):
        if nid in self._sel_nodes:
            self._sel_nodes.remove(nid)
        else:
            self._sel_nodes.append(nid)
        self._sel_edge = None
        self._redraw()
        self._build_config_panel()

    def _select_edge(self, eid):
        self._sel_edge = eid
        self._sel_nodes = []
        self._redraw()
        self._build_config_panel()

    def _on_select_all(self, _event=None):
        self._sel_nodes = [n.id for n in self.wf.nodes]
        self._sel_edge = None
        self._redraw()
        self._build_config_panel()
        return "break"

    def _selected_node_objs(self):
        objs = [self.wf.node(nid) for nid in self._sel_nodes]
        return [n for n in objs if n is not None]

    def _align_selected(self, edge):
        """Blueprint-style Shift+WASD: align the selected nodes' edges. top/bottom
        (W/S) share a horizontal edge; left/right (A/D) a vertical edge."""
        nodes = self._selected_node_objs()
        if len(nodes) < 2:
            return "break"
        w = self._node_width
        h = self._node_height
        if edge == "left":
            x = min(n.x for n in nodes)
            for n in nodes:
                n.x = x
        elif edge == "right":
            r = max(n.x + w(n) for n in nodes)
            for n in nodes:
                n.x = r - w(n)
        elif edge == "top":
            y = min(n.y for n in nodes)
            for n in nodes:
                n.y = y
        elif edge == "bottom":
            b = max(n.y + h(n) for n in nodes)
            for n in nodes:
                n.y = b - h(n)
        # A/D leave a vertical stack, W/S a horizontal row — arrows now tune
        # the gaps (see _arrow_key).
        self._set_align_ctx()
        self.app.mark_dirty()
        self._redraw()
        return "break"

    def _straighten_selected(self):
        """Blueprint-style Q: align connected nodes so the wires between them run
        straight — for each edge among the selection, move the downstream node so
        its input pin sits at the same height as the upstream output pin. Processed
        left-to-right so a chain propagates. With no connections in the selection,
        falls back to aligning vertical centres onto one line."""
        nodes = self._selected_node_objs()
        if len(nodes) < 2:
            return "break"
        sel = {n.id for n in nodes}
        edges = [e for e in self.wf.edges if e.src_node in sel and e.dst_node in sel]
        if not edges:
            cy = sum(n.y + self._node_height(n) / 2.0 for n in nodes) / len(nodes)
            for n in nodes:
                n.y = cy - self._node_height(n) / 2.0
        else:
            edges.sort(key=lambda e: self.wf.node(e.src_node).x)   # upstream first
            for e in edges:
                src, dst = self.wf.node(e.src_node), self.wf.node(e.dst_node)
                sp = self._port_xy(src, e.src_port, is_input=False)
                dp = self._port_xy(dst, e.dst_port, is_input=True)
                if sp is None or dp is None:
                    continue
                dst.y = sp[1] - (dp[1] - dst.y)   # dst input pin := src output pin height
        # ...and distribute horizontally with even gaps so nothing overlaps;
        # arrow keys then fine-tune the spacing (see _arrow_key).
        self._set_align_ctx()
        self._distribute("x")
        self.app.mark_dirty()
        self._redraw()
        return "break"

    # ---- spacing / distribution (arrow keys after an align) ----

    def _set_align_ctx(self):
        """Arm the arrow keys as spacing tuners for the just-aligned selection
        (cleared by the next canvas click or selection change)."""
        self._align_ctx = {"nodes": list(self._sel_nodes)}

    def _distribute(self, axis):
        """Evenly space the align-context nodes along `axis` with the current
        gap, preserving their order (first node stays put)."""
        ctx = getattr(self, "_align_ctx", None)
        nodes = [self.wf.node(nid) for nid in (ctx or {}).get("nodes", [])]
        nodes = [n for n in nodes if n is not None]
        if len(nodes) < 2:
            return
        if axis == "x":
            gap = getattr(self, "_gap_x", 60.0)
            nodes.sort(key=lambda n: n.x)
            x = nodes[0].x
            for n in nodes:
                n.x = x
                x += self._node_width(n) + gap
        else:
            gap = getattr(self, "_gap_y", 30.0)
            nodes.sort(key=lambda n: n.y)
            y = nodes[0].y
            for n in nodes:
                n.y = y
                y += self._node_height(n) + gap
        self.app.mark_dirty()
        self._redraw()

    def _arrow_key(self, direction):
        ctx = getattr(self, "_align_ctx", None)
        if ctx and ctx.get("nodes") == self._sel_nodes and len(self._sel_nodes) > 1:
            if direction in ("left", "right"):
                self._gap_x = max(4.0, getattr(self, "_gap_x", 60.0)
                                  + (10.0 if direction == "right" else -10.0))
                self._distribute("x")
            else:
                self._gap_y = max(4.0, getattr(self, "_gap_y", 30.0)
                                  + (10.0 if direction == "down" else -10.0))
                self._distribute("y")
            return "break"
        pan = {"left": (60, 0), "right": (-60, 0),
               "up": (0, 60), "down": (0, -60)}[direction]
        self._pan_by(*pan)
        return "break"

    # ---- cut connections (vim-style J/K/L) ----

    def _cut_selected(self, which):
        """J: cut every wire INTO the selected node(s); L: every wire OUT;
        K: all of them."""
        sel = set(self._sel_nodes)
        if not sel:
            self.app.set_status("Select node(s) first (J = cut inputs, L = outputs, "
                                "K = all).")
            return "break"
        drop = set()
        for nid in sel:
            if which in ("in", "both"):
                drop.update(e.id for e in self.wf.edges_into(nid))
            if which in ("out", "both"):
                drop.update(e.id for e in self.wf.edges_out(nid))
        if not drop:
            self.app.set_status("No connections to cut on the selection.")
            return "break"
        if not self._confirm_destructive_edit("Cutting {} wire(s)".format(len(drop))):
            return "break"
        self.wf.edges = [e for e in self.wf.edges if e.id not in drop]
        self._commit()
        self._redraw()
        self.app.set_status("Cut {} connection(s).".format(len(drop)))
        return "break"

    # ---- copy / paste nodes ----

    def _copy_selection(self, _event=None):
        import copy
        nodes = [self.wf.node(nid) for nid in self._sel_nodes]
        nodes = [n for n in nodes if n is not None]
        if not nodes:
            return "break"
        ids = {n.id for n in nodes}
        self._node_clipboard = {
            "nodes": [copy.deepcopy(n.to_dict()) for n in nodes],
            # only the wires INTERNAL to the copied set travel along
            "edges": [e.to_dict() for e in self.wf.edges
                      if e.src_node in ids and e.dst_node in ids],
        }
        self._paste_count = 0
        self.app.set_status("Copied {} node(s).".format(len(nodes)))
        return "break"

    def _paste_clipboard(self, _event=None):
        import copy
        clip = getattr(self, "_node_clipboard", None)
        if not clip:
            self.app.set_status("Nothing copied yet (Ctrl+C on selected nodes first).")
            return "break"
        self._paste_count = getattr(self, "_paste_count", 0) + 1
        off = 40.0 * self._paste_count
        idmap = {}
        new_ids = []
        for nd in clip["nodes"]:
            node = self.wf.add_node(nd["type"], nd["x"] + off, nd["y"] + off,
                                    copy.deepcopy(nd.get("config")))
            idmap[nd["id"]] = node.id
            new_ids.append(node.id)
        for ed in clip["edges"]:
            self.wf.add_edge(idmap[ed["src_node"]], ed["src_port"],
                             idmap[ed["dst_node"]], ed["dst_port"])
        self._commit()
        self._sel_nodes = new_ids
        self._sel_edge = None
        self._redraw()
        self._build_config_panel()
        self.app.set_status("Pasted {} node(s).".format(len(new_ids)))
        return "break"

    def _clear_selection(self):
        self._sel_nodes = []
        self._sel_edge = None
        self._align_ctx = None
        self._redraw()
        self._build_config_panel()

    # ---- pan + zoom ----

    def _pan_start(self, event):
        self.canvas.focus_set()
        self._pan = {"x": event.x, "y": event.y, "ox": self._ox, "oy": self._oy}

    def _pan_move(self, event):
        if not self._pan:
            return
        self._ox = self._pan["ox"] + (event.x - self._pan["x"])
        self._oy = self._pan["oy"] + (event.y - self._pan["y"])
        self._redraw()

    def _pan_by(self, dx, dy):
        self._ox += dx
        self._oy += dy
        self._redraw()
        return "break"

    def _wheel_pan_v(self, event):
        return self._pan_by(0, int(event.delta / 120 * 60) or (60 if event.delta > 0 else -60))

    def _wheel_pan_h(self, event):
        return self._pan_by(int(event.delta / 120 * 60) or (60 if event.delta > 0 else -60), 0)

    def _wheel_zoom(self, event):
        return self._zoom_at(event.x, event.y, 1.1 if event.delta > 0 else 1 / 1.1)

    def _zoom_center(self, factor):
        self._zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, factor)
        return "break"

    def _reset_view(self):
        self._zoom, self._ox, self._oy = 1.0, 0.0, 0.0
        self._redraw()
        return "break"

    def _zoom_at(self, sx, sy, factor):
        z0 = self._zoom
        z1 = max(0.3, min(3.0, z0 * factor))
        if abs(z1 - z0) < 1e-6:
            return
        # keep the world point under the cursor fixed on screen
        wx, wy = self._s2w(sx, sy)
        self._zoom = z1
        self._ox = sx - wx * z1
        self._oy = sy - wy * z1
        self._redraw()
        return "break"

    # ---- right button: drag pans, click opens a context menu ----

    def _on_rpress(self, event):
        self.canvas.focus_set()
        self._pan = {"x": event.x, "y": event.y, "ox": self._ox, "oy": self._oy}
        self._rclick = {"x": event.x, "y": event.y, "moved": False}

    def _on_rmotion(self, event):
        if getattr(self, "_rclick", None) is not None:
            if abs(event.x - self._rclick["x"]) > 3 or abs(event.y - self._rclick["y"]) > 3:
                self._rclick["moved"] = True
        self._pan_move(event)

    def _on_rrelease(self, event):
        rc = getattr(self, "_rclick", None)
        self._rclick = None
        self._pan = None
        if rc is not None and not rc["moved"]:
            self._show_context_menu(event)

    def _hit_xy(self, cx, cy):
        node_id = edge_id = None
        for it in self.canvas.find_overlapping(cx - 2, cy - 2, cx + 2, cy + 2):
            for t in self.canvas.gettags(it):
                if t.startswith("N:") and node_id is None:
                    node_id = t[2:]
                elif t.startswith("E:") and edge_id is None:
                    edge_id = t[2:]
        if node_id:
            return ("node", node_id)
        if edge_id:
            return ("edge", edge_id)
        return None

    def _show_context_menu(self, event):
        wx, wy = self._cxy(event)            # world coords (for placing a node)
        hit = self._hit_xy(event.x, event.y)  # screen coords (for canvas hit-test)
        menu = tk.Menu(self, tearoff=0)
        if hit and hit[0] == "node":
            nid = hit[1]
            if nid not in self._sel_nodes:
                self._select_only(nid)
            n = len(self._sel_nodes)
            if n == 2:
                menu.add_command(label="Connect the 2 selected  (J)",
                                 command=self._connect_selected)
            menu.add_command(label="Disconnect this node",
                             command=lambda: self._disconnect_node(nid))
            menu.add_command(label="Delete node" + ("s ({})".format(n) if n > 1 else ""),
                             command=self._delete_selected)
            menu.add_separator()
            menu.add_command(label="Add node here…", command=lambda: self._context_add(wx, wy))
        elif hit and hit[0] == "edge":
            self._select_edge(hit[1])
            menu.add_command(label="Delete connection", command=self._delete_selected)
        else:
            menu.add_command(label="Add node here…", command=lambda: self._context_add(wx, wy))
            menu.add_separator()
            menu.add_command(label="Select all  (Ctrl+A)", command=self._on_select_all)
            menu.add_command(label="Clear selection", command=self._clear_selection)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _context_add(self, cx, cy):
        ntype = self._node_search_popup(self.canvas.winfo_pointerx(),
                                        self.canvas.winfo_pointery())
        if ntype:
            node = self.wf.add_node(ntype, cx, cy)
            self._commit()
            self._select_only(node.id)

    def _disconnect_node(self, node_id):
        before = len(self.wf.edges)
        self.wf.edges = [e for e in self.wf.edges
                         if e.src_node != node_id and e.dst_node != node_id]
        if len(self.wf.edges) != before:
            self._commit()
            self._redraw()
            self.app.set_status("Disconnected {} edge(s).".format(before - len(self.wf.edges)))

    def _connect_selected(self):
        if len(self._sel_nodes) != 2:
            self.app.set_status("Select exactly two nodes (Ctrl+click), then press V to connect.")
            return "break"
        a, b = self._sel_nodes[0], self._sel_nodes[1]
        pair = self._find_connectable(a, b) or self._find_connectable(b, a)
        if pair is None:
            self.app.set_status("Those two nodes have no compatible free ports to connect.")
            return "break"
        if self._try_add_edge(*pair):
            self.app.set_status("Connected.")
        self._redraw()
        return "break"

    def _find_connectable(self, src_id, dst_id):
        src = self.wf.node(src_id)
        if src is None:
            return None
        for sp, _t in src.outputs():
            dp = self._compatible_input_port(src_id, sp, dst_id)
            if dp is not None:
                return (src_id, sp, dst_id, dp)
        return None

    def _node_launched_calcs(self, node_id):
        """Calcs this node spawned that have been submitted/run (have a job id).
        Sourced from the project (by origin_node), so it's correct after a reopen."""
        return [c for c in self.app.project.planned_calcs
                if getattr(c, "origin_node", None) == node_id and c.job_id]

    def _node_is_locked(self, node_id):
        """A node is locked — recipe uneditable, protected from deletion — once it has
        launched calc(s). Report nodes are exempt: they only aggregate results, so
        they stay editable and re-runnable."""
        node = self.wf.node(node_id)
        if node is not None and node.type == "report":
            return False
        return bool(self._node_launched_calcs(node_id))

    def _graph_has_run(self):
        """True if any node has launched calculations — i.e. the graph now
        DOCUMENTS work that has actually executed."""
        return any(self._node_launched_calcs(n.id) for n in self.wf.nodes)

    def _confirm_destructive_edit(self, what):
        """Gate a structural edit of an already-EXECUTED graph behind one
        explicit, informed confirmation — the escape hatch. Returns True to
        proceed. A no-op (returns True) when the graph hasn't run yet, or once the
        user has acknowledged it this session (so it doesn't nag every edit)."""
        if self._edit_run_ack or not self._graph_has_run():
            return True
        ok = messagebox.askyesno(
            "Edit an executed workflow?",
            "This workflow documents calculations that have already run.\n\n"
            "{} can detach finished results from the graph that produced them, so the "
            "record no longer reflects what was actually executed.\n\n"
            "Proceed anyway? (You won't be asked again this session.)".format(what))
        if ok:
            self._edit_run_ack = True
        return ok

    def _delete_selected(self):
        if self._sel_edge is not None:
            if not self._confirm_destructive_edit("Deleting a wire"):
                return
            self.wf.remove_edge(self._sel_edge)
            self._sel_edge = None
            self._commit()
            self._redraw()
            self._build_config_panel()
            return
        if not self._sel_nodes:
            return
        locked = [nid for nid in self._sel_nodes if self._node_is_locked(nid)]
        deletable = [nid for nid in self._sel_nodes if nid not in locked]
        if locked:
            messagebox.showinfo(
                "Protected node(s)",
                "{} of the selected node(s) have already launched calculation(s), so "
                "they're kept to protect your run record. To remove one, first delete its "
                "calculations on the Calculations tab (Deconstruct).{}".format(
                    len(locked),
                    "" if not deletable else
                    "\n\nThe other {} node(s) will be deleted.".format(len(deletable))))
        for nid in deletable:
            self.wf.remove_node(nid)
        self._sel_nodes = list(locked)   # keep the protected ones selected
        self._commit()
        self._redraw()
        self._build_config_panel()

    def _delete_node(self, node_id):
        if self._node_is_locked(node_id):
            messagebox.showinfo(
                "Protected node",
                "This node has already launched calculation(s), so it's kept to protect "
                "your run record. Delete its calculations on the Calculations tab first.")
            return
        self.wf.remove_node(node_id)
        if node_id in self._sel_nodes:
            self._sel_nodes.remove(node_id)
        self._commit()
        self._redraw()
        self._build_config_panel()

    # ---- Blender-style add-node search popup ----

    def _on_search_add(self, _event=None):
        # Open the node search at the pointer and add the chosen node there,
        # unconnected (bound to F3).
        px, py = self.canvas.winfo_pointerx(), self.canvas.winfo_pointery()
        rx, ry = px - self.canvas.winfo_rootx(), py - self.canvas.winfo_rooty()
        if rx < 0 or ry < 0 or rx > self.canvas.winfo_width() or ry > self.canvas.winfo_height():
            rx, ry = 40, 40
            px, py = self.canvas.winfo_rootx() + 60, self.canvas.winfo_rooty() + 60
        cx, cy = self._s2w(rx, ry)   # place at the pointer, in world coords
        ntype = self._node_search_popup(px, py)
        if ntype:
            node = self.wf.add_node(ntype, cx, cy)
            self._commit()
            self._select_only(node.id)
        return "break"

    def _node_search_popup(self, screen_x, screen_y, out_type=None, src_node=None):
        """Searchable add-node menu (like Blender's Shift+A search). Returns a
        node type string or None. When out_type is given (dragging from an
        output), only node types able to accept it are listed; `src_node` (the
        port's owner) lets us refine further by chemical prerequisite."""
        # Canonical ordering, but derived from the registry so every node type
        # (including new ones like ZPVA / Filter) shows up automatically.
        canonical = ["molecules", "transform", "combine", "optimize", "frequencies",
                     "property", "condition", "filter", "zpva", "report"]
        order = ([t for t in canonical if t in wf_mod.NODE_TYPES]
                 + [t for t in wf_mod.NODE_TYPES if t not in canonical])
        # Annotations (Comment/Frame) are spawned by their own keys (T / C), not the
        # add-node search — keep them out of it.
        order = [t for t in order if wf_mod.NODE_TYPES[t]["kind"] != "annotation"]

        def accepts(ntype):
            return any(pt == out_type for _n, pt in wf_mod.NODE_TYPES[ntype]["inputs"])

        def offer(ntype):
            # When dragging from an output port, only offer nodes that ACCEPT that
            # port's type (a geometry pin won't suggest Report; a results pin won't
            # suggest Optimize).
            if not accepts(ntype):
                return False
            # ZPVA needs the .hess of an upstream Frequencies job, so only suggest
            # it downstream of a node that is (or traces back to) a Frequencies —
            # not a bare Molecules / Optimize / Filter geometry pin.
            if ntype == "zpva" and src_node is not None and not self.wf.traces_to_type(
                    src_node, "frequencies"):
                return False
            return True

        # Unprompted (F3 / right-click, no out_type) shows everything.
        types = [t for t in order if offer(t)] if out_type else order
        all_items = [(wf_mod.NODE_TYPES[t]["label"], t) for t in types]
        # Search aliases: typing what you want to DO finds the node that does it
        # (e.g. "align" -> Transform, which owns the alignment ops).
        aliases = {
            "transform": ("align", "rotate", "translate", "mirror", "move", "shift",
                          "center", "dihedral", "flip"),
            "combine": ("merge", "append", "join", "dimer", "assemble"),
            "filter": ("subset", "select"),
            "molecules": ("source", "input"),
            "write": ("export", "save", "trajectory", "traj", "dump", "xyz", "sdf"),
        }

        top = tk.Toplevel(self)
        top.title("Add node")
        top.transient(self.winfo_toplevel())
        top.resizable(False, False)
        try:
            top.geometry("+{}+{}".format(int(screen_x), int(screen_y)))
        except Exception:
            pass
        ttk.Label(top, text="Search nodes:").pack(anchor=tk.W, padx=6, pady=(6, 0))
        ent = ttk.Entry(top, width=32)
        ent.pack(fill=tk.X, padx=6, pady=(0, 2))
        lb = tk.Listbox(top, height=6, activestyle="dotbox", exportselection=False)
        lb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        state = {"items": [], "result": None}

        def repopulate():
            q = ent.get().strip().lower()
            lb.delete(0, tk.END)
            items = [(lbl, nt) for (lbl, nt) in all_items
                     if q in lbl.lower() or q in nt.lower()
                     or any(q in a for a in aliases.get(nt, ()))]
            state["items"] = items
            for lbl, _nt in items:
                lb.insert(tk.END, lbl)
            if items:
                lb.selection_set(0)
                lb.activate(0)

        def confirm(_e=None):
            if not top.winfo_exists():
                return
            cur = lb.curselection()
            i = cur[0] if cur else (0 if state["items"] else None)
            if i is not None and state["items"]:
                state["result"] = state["items"][i][1]
            top.destroy()

        def cancel(_e=None):
            top.destroy()

        def move(delta):
            if not state["items"]:
                return "break"
            cur = lb.curselection()
            i = max(0, min(len(state["items"]) - 1, (cur[0] if cur else 0) + delta))
            lb.selection_clear(0, tk.END)
            lb.selection_set(i)
            lb.activate(i)
            lb.see(i)
            return "break"

        def on_key(e):
            if e.keysym in ("Up", "Down", "Return", "Escape"):
                return
            repopulate()

        ent.bind("<KeyRelease>", on_key)
        ent.bind("<Down>", lambda e: move(1))
        ent.bind("<Up>", lambda e: move(-1))
        ent.bind("<Return>", confirm)
        ent.bind("<Escape>", cancel)
        # Single left-click selects (class binding) then confirms (after idle so
        # the selection is up to date).
        lb.bind("<Button-1>", lambda e: top.after(1, confirm))
        lb.bind("<Return>", confirm)
        lb.bind("<Escape>", cancel)
        top.bind("<Escape>", cancel)
        repopulate()
        ent.focus_set()
        top.after(20, lambda: (top.lift(), ent.focus_force()))
        try:
            top.grab_set()
        except Exception:
            pass
        top.wait_window()
        return state["result"]

    # --------------------------------------------------------------- actions

    def _add_node(self, ntype):
        with diag.timed("workflow:add_node"):
            self._add_offset = (self._add_offset + 1) % 8
            x = 60 + self._add_offset * 26
            y = 60 + self._add_offset * 26
            node = self.wf.add_node(ntype, x, y)
            self._commit()
            self._select_only(node.id)

    def _frame_selection(self):
        """C: draw a titled Frame around the selected real nodes. The frame then
        drags those nodes together; double-click its title to rename it."""
        sel = [self.wf.node(nid) for nid in self._sel_nodes]
        sel = [n for n in sel if n is not None and not self._is_annotation(n)]
        if not sel:
            self.app.set_status("Select node(s) first, then press C to frame them.")
            return
        rects = [self._node_rect(n) for n in sel]
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        pad, title = 18.0, 24.0
        node = self.wf.add_node("frame", x0 - pad, y0 - pad - title,
                                {"title": "Group", "w": (x1 - x0) + 2 * pad,
                                 "h": (y1 - y0) + 2 * pad + title})
        self._commit()
        self._select_only(node.id)
        self.app.set_status("Framed {} node(s) — drag the frame to move them together; "
                            "double-click the title to rename.".format(len(sel)))

    def _add_comment(self):
        """T: drop a resizable comment note at the pointer and open it for editing."""
        rx = self.canvas.winfo_pointerx() - self.canvas.winfo_rootx()
        ry = self.canvas.winfo_pointery() - self.canvas.winfo_rooty()
        if not (0 <= rx <= self.canvas.winfo_width() and 0 <= ry <= self.canvas.winfo_height()):
            rx, ry = 60, 60
        cx, cy = self._s2w(rx, ry)
        node = self.wf.add_node("comment", cx, cy, {"text": "Comment", "w": 200.0, "h": 90.0})
        self._commit()
        self._select_only(node.id)
        self._edit_annotation_text(node)

    def _on_double_click(self, event):
        n = self.wf.node(self._node_at(*self._cxy(event)))
        if self._is_annotation(n):
            self._edit_annotation_text(n)
            return "break"

    def _edit_annotation_text(self, node):
        key = "title" if node.type == "frame" else "text"
        cur = str(node.config.get(key, ""))
        top = tk.Toplevel(self)
        top.title("Edit " + node.label)
        top.geometry("420x230" if node.type == "comment" else "420x130")
        ttk.Label(top, text=("Comment text:" if node.type == "comment" else "Frame title:")).pack(
            anchor=tk.W, padx=10, pady=(10, 2))

        def ok():
            node.config[key] = getval()
            self._commit()
            self._redraw()      # repaint immediately so the new title/text shows
            top.destroy()

        if node.type == "comment":
            txt = tk.Text(top, wrap="word", height=7, undo=True)
            txt.pack(fill=tk.BOTH, expand=True, padx=10)
            txt.insert("1.0", cur)
            txt.focus_set()
            install_text_shortcuts(txt)
            getval = lambda: txt.get("1.0", tk.END).rstrip("\n")
        else:
            var = tk.StringVar(value=cur)
            ent = ttk.Entry(top, textvariable=var)
            ent.pack(fill=tk.X, padx=10)
            ent.focus_set()
            ent.select_range(0, tk.END)
            ent.bind("<Return>", lambda e: ok())
            getval = lambda: var.get()

        bar = ttk.Frame(top)
        bar.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(bar, text="OK", command=ok).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=top.destroy).pack(side=tk.RIGHT, padx=4)
        make_modal(top, self)

    def on_clear(self):
        if not self.wf.nodes:
            return
        if not messagebox.askyesno("Clear workflow", "Remove all nodes and connections?"):
            return
        self.wf = wf_mod.Workflow(category=self.wf.category)
        self._sel_nodes = []
        self._sel_edge = None
        self._commit()
        self._redraw()
        self._build_config_panel()

    # ------------------------------------------------------------- Filter node
    def _build_filter_panel(self, node):
        f = self.cfg_frame
        ttk.Label(f, text="Keep only the molecules that match — a static subset by name or "
                  "index (unlike Condition, which gates on a calculation's result).",
                  foreground="#555", wraplength=220, justify=tk.LEFT).pack(anchor=tk.W, padx=8)

        mode = tk.StringVar(value=node.config.get("mode", "include"))
        ttk.Label(f, text="Mode:").pack(anchor=tk.W, padx=8, pady=(6, 0))
        for val, txt in (("include", "Include matches (keep)"),
                         ("exclude", "Exclude matches (drop)")):
            ttk.Radiobutton(f, text=txt, variable=mode, value=val,
                            command=lambda v=mode: self._set_cfg(node, "mode", v.get())
                            ).pack(anchor=tk.W, padx=16)

        kind = tk.StringVar(value=node.config.get("kind", "substring"))
        ttk.Label(f, text="Match by:").pack(anchor=tk.W, padx=8, pady=(6, 0))
        for val, txt in (("substring", "Filename substring(s)"),
                         ("index", "Index range")):
            ttk.Radiobutton(f, text=txt, variable=kind, value=val,
                            command=lambda v=kind: self._set_cfg(node, "kind", v.get())
                            ).pack(anchor=tk.W, padx=16)

        ttk.Label(f, text="Pattern:").pack(anchor=tk.W, padx=8, pady=(6, 0))
        pat = tk.StringVar(value=node.config.get("pattern", ""))
        ent = ttk.Entry(f, textvariable=pat, width=26)
        ent.pack(anchor=tk.W, padx=8, pady=2)
        pat.trace_add("write", lambda *_a, v=pat: self._set_cfg(node, "pattern", v.get()))
        ttk.Label(f, text="Substrings: comma-separated, match if the filename contains any "
                  "(e.g. 'fluoro, _opt'). Index range: like 0-3,5 over the molecules feeding "
                  "this network. Empty = keep all.", foreground="#777", wraplength=220,
                  justify=tk.LEFT).pack(anchor=tk.W, padx=8)

    # ---------------------------------------- Transform / Combine (geometry prep)
    def _build_transform_panel(self, node):
        from orca_workbench.core import transform as transform_mod
        f = self.cfg_frame
        ttk.Label(f, text="Rigid moves / alignments / dihedral edits, applied IN ORDER "
                  "to every molecule flowing through. Atom indices are 0-based. Outputs "
                  "become new locked molecules when the pipeline is generated.",
                  foreground="#555", wraplength=220, justify=tk.LEFT).pack(anchor=tk.W, padx=8)

        # EXTENDED select: multi-select a block and move it as one; reorder by
        # drag OR the Up/Down buttons. The listbox is refreshed in place (NOT via
        # _build_config_panel), so reordering keeps the current selection.
        lb = tk.Listbox(f, height=6, exportselection=False, selectmode=tk.EXTENDED,
                        activestyle="dotbox")
        lb.pack(fill=tk.X, padx=8, pady=4)
        self._op_listbox = lb
        drag = {"from": None, "moved": False}

        def ops():
            return list(node.config.get("ops") or [])

        def refill(select_indices):
            lb.delete(0, tk.END)
            for op in ops():
                lb.insert(tk.END, transform_mod.describe_op(op))
            for i in select_indices:
                if 0 <= i < lb.size():
                    lb.selection_set(i)
            if select_indices:
                lb.activate(select_indices[0])
                lb.see(select_indices[0])

        def sel_indices():
            return list(lb.curselection())

        def has_moiety(op_list):
            return any((o.get("op") == "align_moiety") for o in op_list)

        def commit(new_ops, select_indices):
            had = has_moiety(ops())
            self._set_cfg(node, "ops", new_ops)   # marks dirty + redraws canvas
            if had != has_moiety(new_ops):
                self._build_config_panel()        # show/hide the Cycle button
            else:
                refill(select_indices)

        def on_add():
            op = self._edit_op_dialog(node, None)
            if op is not None:
                new = ops() + [op]
                commit(new, [len(new) - 1])

        def on_edit(_e=None):
            idx = sel_indices()
            if len(idx) != 1:
                return
            i = idx[0]
            op = self._edit_op_dialog(node, ops()[i])
            if op is not None:
                lst = ops()
                lst[i] = op
                commit(lst, [i])

        def on_remove():
            idx = set(sel_indices())
            if not idx:
                return
            lst = [op for k, op in enumerate(ops()) if k not in idx]
            commit(lst, [min(idx)] if lst else [])

        def move_block(delta):
            """Move the selected (possibly non-contiguous) rows by delta, as a
            block, preserving their internal order and the selection."""
            idx = sorted(sel_indices())
            if not idx:
                return "break"
            lst = ops()
            n = len(lst)
            lo, hi = idx[0], idx[-1]
            if delta < 0 and lo == 0:
                return "break"
            if delta > 0 and hi == n - 1:
                return "break"
            block = [lst[i] for i in idx]
            rest = [op for k, op in enumerate(lst) if k not in set(idx)]
            insert_at = lo + delta
            # position among `rest`: count rest-items before the insert point
            new = rest[:insert_at] + block + rest[insert_at:]
            commit(new, list(range(insert_at, insert_at + len(block))))
            return "break"

        # A thin insertion line (a 2px Frame placed over the listbox) shows WHERE a
        # dragged block will land — between two rows — instead of highlighting every
        # row the cursor passes over.
        line = tk.Frame(lb, height=2, bg=self._np.get("sel", "#1f6fb2"))

        def insert_pos(y):
            """0..size — the gap the drop would insert before."""
            idx = lb.nearest(y)
            bbox = lb.bbox(idx)
            if bbox is None:
                return lb.size()
            _bx, by, _bw, bh = bbox
            return idx if y < by + bh / 2 else idx + 1

        def show_line(p):
            if p >= lb.size():
                bb = lb.bbox(max(0, lb.size() - 1))
                ly = (bb[1] + bb[3]) if bb else 0
            else:
                bb = lb.bbox(p)
                ly = bb[1] if bb else 0
            line.place(in_=lb, x=0, relwidth=1.0, y=max(0, ly - 1))

        def on_press(e):
            idx = lb.nearest(e.y)
            drag["from"] = idx
            drag["moved"] = False
            sel = list(lb.curselection())
            if idx in sel and len(sel) > 1:
                # Grabbing an existing multi-selection: keep it (suppress the
                # listbox's own press-select that would collapse it to one row).
                drag["block"] = sel
                return "break"
            drag["block"] = None      # a fresh single-row drag; normal select runs

        def on_drag_motion(e):
            if drag["from"] is None:
                return
            drag["moved"] = True
            show_line(insert_pos(e.y))
            return "break"            # suppress the listbox's drag band-select

        def on_drop(e):
            frm = drag["from"]
            block = drag.get("block")
            moved = drag.get("moved")
            drag["from"] = None
            drag["block"] = None
            line.place_forget()
            if frm is None or not moved:
                return
            p = insert_pos(e.y)
            lst = ops()
            bidx = sorted(block if block else [frm])
            bidx = [i for i in bidx if 0 <= i < len(lst)]
            if not bidx:
                return
            blk = [lst[i] for i in bidx]
            rest = [op for k, op in enumerate(lst) if k not in set(bidx)]
            before = sum(1 for i in bidx if i < p)
            p2 = max(0, p - before)
            new = rest[:p2] + blk + rest[p2:]
            if new != lst:
                commit(new, list(range(p2, p2 + len(blk))))

        refill([])
        lb.bind("<Double-1>", on_edit)
        lb.bind("<ButtonPress-1>", on_press, add="+")
        lb.bind("<B1-Motion>", on_drag_motion, add="+")
        lb.bind("<ButtonRelease-1>", on_drop, add="+")

        row1 = ttk.Frame(f)
        row1.pack(fill=tk.X, padx=8)
        ttk.Button(row1, text="Add...", width=7, command=on_add).pack(side=tk.LEFT, padx=1)
        ttk.Button(row1, text="Edit...", width=7, command=on_edit).pack(side=tk.LEFT, padx=1)
        ttk.Button(row1, text="Remove", width=8, command=on_remove).pack(side=tk.LEFT, padx=1)
        row2 = ttk.Frame(f)
        row2.pack(fill=tk.X, padx=8, pady=(2, 0))
        ttk.Button(row2, text="Up", width=5, command=lambda: move_block(-1)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row2, text="Down", width=6, command=lambda: move_block(1)).pack(side=tk.LEFT, padx=1)

        def cycle_moiety():
            """Step the ring-orientation of a moiety op through its N symmetry-
            equivalent fits and re-open the 3D preview — the 'cycle through the
            candidate ring alignments and eyeball the right one' control. Cycles
            the selected align_moiety op, else the last one in the list."""
            lst = ops()
            sel = sel_indices()
            target = None
            if (len(sel) == 1 and 0 <= sel[0] < len(lst)
                    and lst[sel[0]].get("op") == "align_moiety"):
                target = sel[0]
            else:
                for kk in range(len(lst) - 1, -1, -1):
                    if lst[kk].get("op") == "align_moiety":
                        target = kk
                        break
            if target is None:
                return
            op = dict(lst[target])
            mob = op.get("mobile") or []
            try:
                n = len(transform_mod.moiety_orderings(mob)) if len(mob) >= 3 else 0
            except Exception:
                n = 0
            if n <= 0:
                messagebox.showinfo("Cycle moiety orientation",
                                    "This moiety op needs at least 3 matched atoms first.")
                return
            cur = op.get("ordering")
            op["ordering"] = 0 if cur is None else (int(cur) + 1) % n
            lst[target] = op
            commit(lst, [target])
            self.app.set_status("Moiety orientation {} / {} - opening preview...".format(
                op["ordering"] + 1, n))
            self._preview_node_geometry(node)

        if has_moiety(node.config.get("ops") or []):
            cbtn = ttk.Button(f, text="Cycle moiety orientation >", command=cycle_moiety)
            cbtn.pack(anchor=tk.W, padx=8, pady=(0, 2))
            tip(cbtn, "Force the moiety op to the NEXT of its N symmetry-equivalent ring "
                      "fits and open the 3D preview. Click repeatedly to step through them "
                      "and keep the orientation that looks right (it's saved on the op). "
                      "Cycles the selected moiety op, or the last one.")
        ttk.Label(f, text="Tip: multi-select ops (Shift/Ctrl-click) and move them as a block, "
                  "or drag a row to reorder. To align two molecules to EACH OTHER, give each "
                  "its own Transform aligning the chosen axis/plane to the same lab axis — then "
                  "Combine.", foreground="#777", wraplength=220, justify=tk.LEFT).pack(
                      anchor=tk.W, padx=8, pady=(4, 0))

    def _edit_op_dialog(self, node, op):
        """Open the op editor, with the first input molecule's atoms as reference
        and every other molecule (with a geometry) offered as an alignment template
        (for the moiety op)."""
        from orca_workbench.ui.transform_dialog import TransformOpDialog
        ref = None
        try:
            geoms = self._node_input_geoms(node, limit=1)
            if geoms:
                ref = geoms[0]
        except Exception:
            ref = None
        templates = []
        cache = {}
        for m in self.app.project.molecules:
            if not m.xyz_path:
                continue
            try:
                g = self._read_geom(m.filename, cache)
                templates.append((m.filename, g["symbols"], g["coords"]))
            except Exception:
                continue
        dlg = TransformOpDialog(self, op=op, ref_geom=ref, templates=templates)
        return dlg.result

    def _build_combine_panel(self, node):
        f = self.cfg_frame
        ttk.Label(f, text="Appends every geometry arriving on its wires (in connection "
                  "order) into ONE structure. Position the fragments with Transform "
                  "nodes FIRST — Combine is a pure append.", foreground="#555",
                  wraplength=220, justify=tk.LEFT).pack(anchor=tk.W, padx=8)

        ttk.Label(f, text="Output molecule name:").pack(anchor=tk.W, padx=8, pady=(6, 0))
        nm = tk.StringVar(value=node.config.get("name", ""))
        ent = ttk.Entry(f, textvariable=nm, width=24)
        ent.pack(anchor=tk.W, padx=8, pady=2)
        nm.trace_add("write", lambda *_a: self._set_cfg(node, "name", nm.get().strip()))

        mode = tk.StringVar(value=node.config.get("mode", "merge"))
        ttk.Label(f, text="Mode:").pack(anchor=tk.W, padx=8, pady=(6, 0))
        ttk.Radiobutton(f, text="Merge ALL incoming molecules into one", variable=mode,
                        value="merge", command=lambda: self._set_cfg(node, "mode", mode.get())
                        ).pack(anchor=tk.W, padx=16)
        ttk.Radiobutton(f, text="Pairwise: combine row-by-row across inputs", variable=mode,
                        value="pairwise", command=lambda: self._set_cfg(node, "mode", mode.get())
                        ).pack(anchor=tk.W, padx=16)
        ttk.Label(f, text="Pairwise: input i of wire 1 merges with input i of wire 2, … "
                  "(a single-molecule wire repeats for every row — e.g. add the same water "
                  "to each of n solutes).", foreground="#777", wraplength=220,
                  justify=tk.LEFT).pack(anchor=tk.W, padx=8)

        # Charge / multiplicity — classic tk.Entry so we can flash their bg red
        # (missing but required) / green (a valid integer just entered). References
        # are stashed in self._combine_fields so the guided-fix flow can highlight
        # + focus them (see _guide_combine_fix).
        self._combine_fields = {}
        _RED, _GREEN = "#e57373", "#66bb6a"

        def int_or_none_entry(label, key, hint):
            ttk.Label(f, text=label).pack(anchor=tk.W, padx=8, pady=(6, 0))
            cur = node.config.get(key)
            var = tk.StringVar(value="" if cur is None else str(cur))
            e = tk.Entry(f, textvariable=var, width=10)
            e.pack(anchor=tk.W, padx=8, pady=1)
            self._combine_fields[key] = (e, var)
            normal_bg, normal_fg = e.cget("bg"), e.cget("fg")

            def commit(*_a):
                txt = var.get().strip()
                required = key in getattr(self, "_combine_required", set())
                if not txt:
                    self._set_cfg(node, key, None)
                    e.configure(bg=(_RED if required else normal_bg),
                                fg=("#000000" if required else normal_fg))
                    return
                try:
                    val = int(txt)
                except ValueError:
                    e.configure(bg=_RED, fg="#000000")     # not an integer yet
                    return
                self._set_cfg(node, key, val)
                getattr(self, "_combine_required", set()).discard(key)
                # brief green confirmation, then back to the themed default
                e.configure(bg=_GREEN, fg="#000000")
                e.after(800, lambda: e.winfo_exists() and e.configure(
                    bg=normal_bg, fg=normal_fg))
            var.trace_add("write", commit)
            ttk.Label(f, text=hint, foreground="#777", wraplength=220,
                      justify=tk.LEFT).pack(anchor=tk.W, padx=8)

        int_or_none_entry("Charge (blank = sum of fragments):", "charge", "")
        int_or_none_entry("Multiplicity (blank = couple all unpaired spins "
                          "ferromagnetically):", "mult",
                          "Antiferromagnetic / low-spin cases: set it here. Required when "
                          "this Combine feeds a calculation.")
        # Preview / Write the merged geometry — same 'run until here' debug view the
        # Transform panel offers (computed in memory; nothing is written to the project).
        self._add_preview_button(node)

    def _guide_combine_fix(self, node_id, missing):
        """Select the offending Combine, red-highlight the empty charge/mult
        box(es), and focus the FIRST one — then hand control back to the user
        (we don't chase focus to the second box). Each box greens + clears when a
        valid integer is typed (see the commit closure)."""
        node = self.wf.node(node_id)
        if node is None:
            return
        self._select_only(node_id)          # rebuilds the panel -> _combine_fields
        self._build_config_panel()
        self._combine_required = set(missing)
        for key in missing:
            ent, _var = self._combine_fields.get(key, (None, None))
            if ent is not None:
                try:
                    ent.configure(bg="#e57373", fg="#000000")
                except tk.TclError:
                    pass
        first = self._combine_fields.get(missing[0], (None, None))[0]
        if first is not None:
            try:
                first.focus_set()
            except tk.TclError:
                pass
        # (the Preview/Write buttons are already part of the rebuilt combine panel)

    def _add_preview_button(self, node):
        """The 'run until here' debug view: computes this node's output geometry
        on demand (nothing is written into the project) and opens it in the 3D
        viewer. Plus a Write button to export the same output to a file."""
        try:
            import tkinter.font as tkfont
            base = abs(tkfont.nametofont("TkDefaultFont").actual("size"))
            ttk.Style(self).configure("Preview.TButton",
                                      font=("TkDefaultFont", base, "bold"))
            b = ttk.Button(self.cfg_frame, text="Preview output (3D)...",
                           style="Preview.TButton",
                           command=lambda: self._preview_node_geometry(node))
        except Exception:
            b = ttk.Button(self.cfg_frame, text="Preview output (3D)...",
                           command=lambda: self._preview_node_geometry(node))
        b.pack(anchor=tk.W, padx=8, pady=(8, 2))
        tip(b, "Execute the geometry pipeline up to THIS node only and open the "
               "result in the external 3D viewer. Nothing is added to the project — "
               "a pure debug view, so you can iterate on rotations/shifts before "
               "running anything.")
        wbtn = ttk.Button(self.cfg_frame, text="Write output to file...",
                          command=lambda: self._write_node_geometry(node))
        wbtn.pack(anchor=tk.W, padx=8, pady=(0, 2))
        tip(wbtn, "Export this node's output geometry to a coordinate file — .xyz "
                  "natively, or any format OpenBabel / RDKit on this machine can "
                  "write (.mol, .sdf, .pdb, .mol2, ...), chosen by the extension "
                  "you save with.")

    def _write_node_geometry(self, node):
        """Export the node's output stream to coordinate file(s). Format follows
        the chosen extension: .xyz is written natively; anything else goes
        through OpenBabel (pybel) or RDKit, whichever is available and knows
        the format. Multiple output molecules get numbered suffixes."""
        from tkinter import filedialog
        from orca_workbench.core import coords as coords_mod
        backend, cache, _pending, notes = self._make_geom_backend()
        streams, warns = wf_mod.compute_streams(self.wf, self._source_molsets(), backend)
        out = streams.get(node.id, [])
        problems = list(dict.fromkeys(warns + notes))
        if not out:
            messagebox.showwarning("Write output", "\n".join(problems) if problems
                                   else "Nothing arrives at this node yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Write node output",
            defaultextension=".xyz",
            initialfile=safe_path_component(out[0]),
            filetypes=[("XYZ", "*.xyz"), ("MDL Molfile", "*.mol"), ("SDF", "*.sdf"),
                       ("PDB", "*.pdb"), ("Mol2", "*.mol2"), ("All files", "*.*")])
        if not path:
            return
        root_p, ext = os.path.splitext(path)
        written, errors = [], []
        for i, nm in enumerate(out):
            try:
                g = self._read_geom(nm, cache)
            except ValueError as e:
                errors.append(str(e))
                continue
            c = g["coords"]
            atoms = [(g["symbols"][k], float(c[k][0]), float(c[k][1]), float(c[k][2]))
                     for k in range(len(g["symbols"]))]
            target = path if len(out) == 1 else "{}_{:02d}{}".format(root_p, i, ext)
            try:
                coords_mod.write_structure_file(target, atoms, name=nm)
                written.append(target)
            except Exception as e:
                errors.append("{}: {}".format(nm, e))
        msg = "Wrote {} file(s).".format(len(written))
        if written:
            msg += "\n" + "\n".join(written[:6])
        if errors or problems:
            msg += "\n\nProblems:\n" + "\n".join(errors + problems)
        (messagebox.showwarning if (errors or problems) else messagebox.showinfo)(
            "Write output", msg)

    def _source_molsets(self):
        """Molecules-node id -> its (selection-applied) molecule filename list."""
        mols_all = [m.filename for m in self.app.project.molecules]
        out = {}
        for n in self.wf.nodes:
            if n.type != "molecules":
                continue
            mols = list(mols_all)
            if n.config.get("mode") == "selection" and n.config.get("filenames"):
                sel = set(n.config["filenames"])
                mols = [m for m in mols if m in sel]
            out[n.id] = mols
        return out

    def _read_geom(self, fname, cache):
        """(symbols, coords, charge, mult) for a molecule — from the backend cache
        (chained derived geometries) or its .xyz on disk. Delegates to the shared
        core reader (see core.workflow_expand)."""
        return workflow_expand.read_geometry(self.app.project, fname, cache)

    def _make_geom_backend(self):
        """The geometry backend injected into compute_streams/expand_to_calcs.

        Returns (backend, cache, pending, notes): backend(node, streams) computes
        a Transform/Combine node's output geometries in memory; every derived
        molecule is recorded in `pending` and only written into the project by
        _flush_geom_materialisations — so a cancelled expand (or a pure preview)
        leaves the project untouched. `notes` collects NON-fatal per-molecule
        problems (an op whose atom indices don't exist in one of several molecules
        — that molecule is skipped, the rest continue) so callers can SHOW them
        instead of failing silently. The heavy lifting lives in the shared core
        module so the GUI and `--execute_project` expand identically."""
        gb = workflow_expand.GeometryBackend(self.app.project)
        return gb, gb.cache, gb.pending, gb.notes

    def _flush_geom_materialisations(self, pending):
        """Write the pending derived geometries into the project (TRANSFORM/*.xyz
        + locked Molecule rows). Delegates to the shared core materialiser; see
        core.workflow_expand.flush_materialisations. Returns warnings."""
        return workflow_expand.flush_materialisations(self.app.project, pending)

    def _node_input_geoms(self, node, limit=1):
        """The first geometries ARRIVING at a node — for the op editor's atom
        reference list. Chained Transform/Combine upstream are computed in
        memory (nothing materialised)."""
        backend, cache, _pending, _notes = self._make_geom_backend()
        streams, _warns = wf_mod.compute_streams(self.wf, self._source_molsets(), backend)
        names = []
        for e in self.wf.edges_into(node.id, "geometry"):
            names.extend(streams.get(e.src_node, []))
        out = []
        for nm in names[:limit]:
            try:
                g = self._read_geom(nm, cache)
                out.append((nm, g["symbols"], g["coords"]))
            except Exception:
                continue
        return out

    def _preview_node_geometry(self, node):
        """'Run until here' for the geometry pipeline: compute this node's output
        stream in memory and open it in the external 3D viewer. Never touches the
        project."""
        import tempfile
        from orca_workbench.core import coords as coords_mod
        from orca_workbench.ui.molecules_tab import open_xyz_3d
        backend, cache, _pending, notes = self._make_geom_backend()
        streams, warns = wf_mod.compute_streams(self.wf, self._source_molsets(), backend)
        out = streams.get(node.id, [])
        problems = list(dict.fromkeys(warns + notes))
        if problems:
            # ALWAYS surface problems in a dialog — a combine that silently
            # drops an input (or a transform that skips molecules) looks like
            # "nothing happened" if the only trace is the status bar.
            messagebox.showwarning(
                "Preview", "\n".join(problems)
                + ("" if out else "\n\n(No geometry reaches this node.)"))
            if not out:
                return
        if not out:
            messagebox.showinfo("Preview", "Nothing arrives at this node yet — connect its "
                                "input(s) and make sure the molecules have geometries "
                                "(generate XYZ first).")
            return
        cap = 5
        tdir = tempfile.mkdtemp(prefix="orca_wb_preview_")
        paths = []
        for nm in out[:cap]:
            try:
                g = self._read_geom(nm, cache)
            except ValueError as e:
                messagebox.showwarning("Preview", str(e))
                return
            c = g["coords"]
            atoms = [(g["symbols"][k], float(c[k][0]), float(c[k][1]), float(c[k][2]))
                     for k in range(len(g["symbols"]))]
            p = os.path.join(tdir, safe_path_component(nm) + ".xyz")
            coords_mod.write_xyz(p, atoms, {"name": nm,
                                            "comment": "preview - not saved to project"})
            paths.append(p)
        if len(out) > cap:
            messagebox.showinfo("Preview", "{} molecules reach this node — opening the "
                                "first {}.".format(len(out), cap))
        for p in paths:
            open_xyz_3d(self, self.app, p)

    # ------------------------------------------------------------- Write node
    def _set_cfg_quiet(self, node, key, value):
        """Set a node config value WITHOUT a redraw / undo snapshot — for text
        fields that update per keystroke (a Write node's path/folder). The graph
        structure is unchanged, so we just record it and mark the project dirty."""
        node.config[key] = value
        self.app.mark_dirty()

    def _build_write_panel(self, node):
        """Config for a Write node: export the geometries arriving here as one
        multi-structure file (trajectory / collection) or a batch of
        one-file-per-molecule into a folder."""
        from tkinter import filedialog
        from orca_workbench.core import coords as coords_mod
        f = self.cfg_frame
        ttk.Label(f, text="Export the geometries arriving here to disk.",
                  foreground="#555", wraplength=220, justify=tk.LEFT).pack(
                      anchor=tk.W, padx=8, pady=(4, 2))

        mode = tk.StringVar(value=node.config.get("mode", "trajectory"))

        def on_mode():
            self._set_cfg(node, "mode", mode.get())
            self._build_config_panel()   # swap the format list + destination widget
        ttk.Radiobutton(f, text="One file (trajectory / collection)", variable=mode,
                        value="trajectory", command=on_mode).pack(anchor=tk.W, padx=8)
        ttk.Radiobutton(f, text="Batch: one file per molecule (folder)", variable=mode,
                        value="batch", command=on_mode).pack(anchor=tk.W, padx=8)

        is_traj = mode.get() == "trajectory"
        fmts = (list(coords_mod.MULTI_STRUCTURE_FORMATS) if is_traj
                else ["xyz", "mol", "sdf", "pdb", "mol2"])
        ttk.Label(f, text="Format:").pack(anchor=tk.W, padx=8, pady=(6, 0))
        cur_fmt = node.config.get("format", fmts[0])
        fmt = tk.StringVar(value=cur_fmt if cur_fmt in fmts else fmts[0])
        cb = ttk.Combobox(f, textvariable=fmt, state="readonly", values=fmts, width=8)
        cb.pack(anchor=tk.W, padx=8, pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._set_cfg(node, "format", fmt.get()))

        if is_traj:
            ttk.Label(f, text="Destination file:").pack(anchor=tk.W, padx=8, pady=(6, 0))
            pv = tk.StringVar(value=node.config.get("path", ""))
            row = ttk.Frame(f); row.pack(fill=tk.X, padx=8)
            ttk.Entry(row, textvariable=pv, width=20).pack(side=tk.LEFT, fill=tk.X, expand=True)
            pv.trace_add("write", lambda *_a: self._set_cfg_quiet(node, "path", pv.get()))

            def browse_file():
                ext = fmt.get()
                p = filedialog.asksaveasfilename(
                    defaultextension="." + ext, initialfile="trajectory." + ext,
                    filetypes=[(ext.upper(), "*." + ext), ("All files", "*.*")])
                if p:
                    pv.set(p)
            ttk.Button(row, text="...", width=3, command=browse_file).pack(side=tk.LEFT, padx=(2, 0))
        else:
            ttk.Label(f, text="Destination folder:").pack(anchor=tk.W, padx=8, pady=(6, 0))
            fv = tk.StringVar(value=node.config.get("folder", ""))
            row = ttk.Frame(f); row.pack(fill=tk.X, padx=8)
            ttk.Entry(row, textvariable=fv, width=20).pack(side=tk.LEFT, fill=tk.X, expand=True)
            fv.trace_add("write", lambda *_a: self._set_cfg_quiet(node, "folder", fv.get()))

            def browse_dir():
                p = filedialog.askdirectory()
                if p:
                    fv.set(p)
            ttk.Button(row, text="...", width=3, command=browse_dir).pack(side=tk.LEFT, padx=(2, 0))

        b = ttk.Button(f, text="Write now", command=lambda: self._write_node_now(node))
        b.pack(anchor=tk.W, padx=8, pady=(8, 2))
        tip(b, "Write the geometries currently arriving at this node. Reads each molecule's "
               "CURRENT .xyz now (like Transform/Combine), so place Write before the "
               "calculation nodes — it exports the input geometries, not optimised ones.")
        ttk.Label(f, text="Several wires can feed one Write (its geometry input fans in), so you "
                  "can pile molecules from different branches into one trajectory.",
                  foreground="#999", wraplength=220, justify=tk.LEFT).pack(
                      anchor=tk.W, padx=8, pady=(2, 0))

    def _write_node_now(self, node):
        """Export the Write node's incoming geometry stream to disk, per its mode
        (single multi-structure file, or one file per molecule into a folder)."""
        from orca_workbench.core import coords as coords_mod
        backend, cache, _pending, notes = self._make_geom_backend()
        streams, warns = wf_mod.compute_streams(self.wf, self._source_molsets(), backend)
        # gather from EVERY inbound wire (the geometry input fans in)
        names = []
        for e in self.wf.edges_into(node.id, "geometry"):
            for nm in streams.get(e.src_node, []):
                if nm not in names:
                    names.append(nm)
        problems = list(dict.fromkeys(warns + notes))
        if not names:
            messagebox.showwarning("Write", "\n".join(problems) if problems else
                                   "No geometry reaches this Write node yet — connect its "
                                   "input and make sure the molecules have geometries.")
            return
        structs = []
        for nm in names:
            try:
                g = self._read_geom(nm, cache)
            except ValueError as e:
                problems.append(str(e))
                continue
            c = g["coords"]
            atoms = [(g["symbols"][k], float(c[k][0]), float(c[k][1]), float(c[k][2]))
                     for k in range(len(g["symbols"]))]
            structs.append((atoms, nm))
        if not structs:
            messagebox.showwarning("Write", "\n".join(problems) or "No readable geometries.")
            return
        mode = node.config.get("mode", "trajectory")
        fmt = node.config.get("format", "xyz")
        try:
            if mode == "trajectory":
                path = (node.config.get("path") or "").strip()
                if not path:
                    messagebox.showwarning("Write", "Choose a destination file first.")
                    return
                used = coords_mod.write_structures_file(path, structs)
                msg = "Wrote {} structure(s) to\n{}\n(via {}).".format(len(structs), path, used)
            else:
                folder = (node.config.get("folder") or "").strip()
                if not folder:
                    messagebox.showwarning("Write", "Choose a destination folder first.")
                    return
                os.makedirs(folder, exist_ok=True)
                for atoms, nm in structs:
                    target = os.path.join(folder, safe_path_component(nm) + "." + fmt)
                    coords_mod.write_structure_file(target, atoms, name=nm)
                msg = "Wrote {} file(s) into\n{}".format(len(structs), folder)
        except Exception as e:
            messagebox.showerror("Write failed", str(e))
            return
        if problems:
            msg += "\n\nProblems:\n" + "\n".join(problems)
        (messagebox.showwarning if problems else messagebox.showinfo)("Write", msg)
        self.app.set_status("Write node: exported {} structure(s).".format(len(structs)))

    # ------------------------------------------------------------- ZPVA builder
    def _build_zpva_panel(self, node):
        f = self.cfg_frame

        def labeled_combo(label, key, values, default):
            ttk.Label(f, text=label).pack(anchor=tk.W, padx=8, pady=(6, 0))
            var = tk.StringVar(value=node.config.get(key, default))
            cb = ttk.Combobox(f, textvariable=var, state="readonly", values=list(values), width=26)
            cb.pack(anchor=tk.W, padx=8, pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e, k=key, v=var: self._set_cfg(node, k, v.get()))

        def labeled_entry(label, key, default, width=26):
            ttk.Label(f, text=label).pack(anchor=tk.W, padx=8, pady=(6, 0))
            var = tk.StringVar(value=str(node.config.get(key, default)))
            ent = ttk.Entry(f, textvariable=var, width=width)
            ent.pack(anchor=tk.W, padx=8, pady=2)
            var.trace_add("write", lambda *_a, k=key, v=var: self._set_cfg(node, k, v.get()))

        ttk.Label(f, text="Displaced single-point recipe (property + EnGrad, type to filter):").pack(
            anchor=tk.W, padx=8, pady=(6, 0))
        self._recipe_search_combo(node, "recipe").pack(anchor=tk.W, padx=8, pady=2)
        labeled_combo("Property to ZPVA-correct:", "property",
                      ("nmr_shielding", "energy", "dipole"), "nmr_shielding")
        labeled_entry("Target nucleus (NMR: index or element, e.g. 0 or F):", "target", "", 12)
        labeled_entry("dq (reduced-coordinate step):", "dq", 1.0, 8)
        labeled_entry("Isotopologues (optional):", "isotopologues", "")
        ttk.Label(f, text="atom-index:isotope (H/D/T or amu), comma-separated; ';' between "
                  "isotopologues — e.g. 6:D,8:D ; 6:D. The base (no substitution) is always "
                  "included as the reference.", foreground="#777", wraplength=220,
                  justify=tk.LEFT).pack(anchor=tk.W, padx=8)

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)
        b1 = ttk.Button(f, text="Expand ZPVA (needs finished FREQ)",
                        command=lambda: self._zpva_expand(node))
        b1.pack(anchor=tk.W, padx=8, pady=2)
        tip(b1, "Read the .hess of the finished upstream Frequencies job, generate the +/-dq "
                "mode-displaced single-points (one shared equilibrium + 2 per real mode per "
                "isotopologue), and add them as 'zpva' calculations. Build + submit them in the "
                "Calculations tab as usual.")
        b2 = ttk.Button(f, text="Assemble ZPVA", command=lambda: self._zpva_assemble(node))
        b2.pack(anchor=tk.W, padx=8, pady=2)
        tip(b2, "Once the displaced jobs have finished, average the property over the zero-point "
                "motion and (if isotopologues were given) report the isotope shifts. Writes a "
                "report and opens a results window.")
        ttk.Label(f, text="Wire Frequencies -> ZPVA, run the FREQ job, then Expand. After the "
                  "displaced jobs finish, Assemble.", foreground="#777", wraplength=220,
                  justify=tk.LEFT).pack(anchor=tk.W, padx=8, pady=(4, 0))

    def _zpva_freq_node(self, node):
        """Walk the ZPVA node's geometry input back to the nearest Frequencies
        node (the one that produces the .hess), or None."""
        cur = node
        for _ in range(50):
            ein = self.wf.edges_into(cur.id, "geometry")
            if not ein:
                return None
            src = self.wf.node(ein[0].src_node)
            if src is None:
                return None
            if src.type == "frequencies":
                return src
            cur = src
        return None

    def _zpva_molecules(self, node):
        """Molecule filenames feeding this node's network (honouring a Molecules
        source set to 'selection')."""
        srcs = self.wf.network_sources([node.id])
        allm = [m.filename for m in self.app.project.molecules]
        out = []
        for sid in srcs:
            s = self.wf.node(sid)
            if s is None:
                continue
            if s.config.get("mode") == "selection" and s.config.get("filenames"):
                sel = set(s.config["filenames"])
                out += [m for m in allm if m in sel]
            else:
                out += allm
        seen, res = set(), []
        for m in out:
            if m not in seen:
                seen.add(m)
                res.append(m)
        return res

    def _find_calc_for_node(self, node_id, mol):
        for c in self.app.project.planned_calcs:
            if getattr(c, "origin_node", None) == node_id and c.molecule_filename == mol:
                return c
        return None

    def _zpva_expand(self, node):
        from orca_workbench.core import hess as hess_mod
        from orca_workbench.core import zpva as zpva_mod
        freq_node = self._zpva_freq_node(node)
        if freq_node is None:
            messagebox.showwarning("ZPVA", "Wire a Frequencies node's geometry output into this "
                                   "ZPVA node first (Molecules -> ... -> Frequencies -> ZPVA).")
            return
        recipe = (node.config.get("recipe") or "").strip()
        if not recipe or self.app.get_recipe(recipe) is None:
            messagebox.showwarning("ZPVA", "Choose the displaced single-point recipe (it must "
                                   "request the property AND EnGrad).")
            return
        mols = self._zpva_molecules(node)
        if not mols:
            messagebox.showwarning("ZPVA", "No molecules feed this pipeline.")
            return
        try:
            dq = float(node.config.get("dq", 1.0) or 1.0)
        except (TypeError, ValueError):
            dq = 1.0
        prop_cfg = {"kind": node.config.get("property", "nmr_shielding"),
                    "target": (node.config.get("target") or "").strip() or None}
        isos = zpva_mod.parse_isotopologue_spec(node.config.get("isotopologues", ""))
        root = self.app.project.root()

        plans, skipped = [], []
        for mol in mols:
            fc = self._find_calc_for_node(freq_node.id, mol)
            if fc is None or not self._calc_done(fc):
                skipped.append("{}: FREQ not finished".format(mol))
                continue
            hess_path = os.path.join(root, fc.rundir or "", mol + ".hess")
            if not os.path.isfile(hess_path):
                skipped.append("{}: no .hess in {}".format(mol, fc.rundir or "?"))
                continue
            try:
                hessian = hess_mod.parse_hess(hess_path)
            except Exception as e:
                skipped.append("{}: .hess unreadable ({})".format(mol, e))
                continue
            molecule = self.app.project.molecule_by_filename(mol)
            charge = molecule.charge if molecule else 0
            mult = molecule.multiplicity if molecule else 1
            hess_rel = os.path.relpath(hess_path, root).replace("\\", "/")
            geoms, manifest = zpva_mod.plan_zpva(hessian, mol, dq, isos, prop_cfg, hess_rel)
            manifest["charge"], manifest["multiplicity"], manifest["recipe"] = charge, mult, recipe
            plans.append((mol, geoms, manifest, charge, mult))

        if not plans:
            messagebox.showwarning("ZPVA", "Nothing to expand.\n\n" + "\n".join(skipped))
            return
        total = sum(len(g) for _m, g, _man, _c, _mu in plans)
        msg = ("Generate {} displaced single-point(s) across {} molecule(s)?\n\n"
               "{} isotopologue(s) x real modes x (+/-), plus a shared equilibrium each. "
               "They become 'zpva' calculations — build + submit them in the Calculations "
               "tab.".format(total, len(plans), len(isos)))
        if skipped:
            msg += "\n\nSkipped:\n  " + "\n  ".join(skipped[:8])
        if not messagebox.askyesno("Expand ZPVA", msg):
            return

        manifests_rel, created = [], 0
        for mol, geoms, manifest, charge, mult in plans:
            created += self._materialize_zpva(geoms, manifest, recipe, charge, mult, node.id, root)
            mrel = "ZPVA/{}/zpva_manifest.json".format(safe_path_component(mol))
            mpath = os.path.join(root, mrel)
            os.makedirs(os.path.dirname(mpath), exist_ok=True)
            with open(mpath, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
            manifests_rel.append(mrel)
        node.config["manifests"] = manifests_rel
        self._commit()
        self.app.mark_dirty()
        self.app.refresh_all_tabs()
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass
        self.app.set_status("ZPVA: created {} displaced single-point(s). Build + submit them in "
                            "Calculations, then come back and Assemble.".format(created))

    def _materialize_zpva(self, geoms, manifest, recipe, charge, mult, node_id, root):
        from orca_workbench.core import coords as coords_mod
        symbols = manifest["symbols"]
        base = manifest["base"]
        sub = safe_path_component(base)
        n = 0
        for g in geoms:
            fname = g["filename"]
            coords = g["coords_ang"]
            atoms = [(symbols[k], float(coords[k][0]), float(coords[k][1]), float(coords[k][2]))
                     for k in range(len(symbols))]
            xyz_rel = "ZPVA/{}/{}.xyz".format(sub, fname)
            if self.app.project.molecule_by_filename(fname) is None:
                coords_mod.write_xyz(os.path.join(root, xyz_rel), atoms,
                                     {"name": fname, "comment": "ZPVA {} of {}".format(
                                         g["role"], base)})
                self.app.project.molecules.append(Molecule(
                    name=fname, filename=fname, smiles=None, charge=charge, multiplicity=mult,
                    comment="ZPVA {} geometry of {} (mode {}, sign {})".format(
                        g["role"], base, g["mode"], g["sign"]),
                    generated=True, gen_status="ok", method="zpva", coords_locked=True,
                    xyz_path=xyz_rel))
            if not any(c.molecule_filename == fname and c.category == "zpva"
                       for c in self.app.project.planned_calcs):
                self.app.project.planned_calcs.append(PlannedCalc(
                    id=new_calc_id(), molecule_filename=fname, recipe_name=recipe,
                    category="zpva", geometry_source="initial", origin_node=node_id))
                n += 1
        return n

    def _zpva_assemble(self, node):
        from orca_workbench.core import zpva as zpva_mod
        manifests = node.config.get("manifests") or []
        if not manifests:
            messagebox.showinfo("ZPVA", "No ZPVA run for this node yet. Expand ZPVA and run the "
                                "displaced jobs first.")
            return
        root = self.app.project.root()
        ct = getattr(self.app, "calculations_tab", None)
        calc_by_fname = {c.molecule_filename: c for c in self.app.project.planned_calcs
                         if c.category == "zpva"}

        def read_out(fn):
            c = calc_by_fname.get(fn)
            p = ct._out_path(c) if (c is not None and ct is not None) else None
            if not p or not os.path.isfile(p):
                return None
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()

        def read_engrad(fn):
            c = calc_by_fname.get(fn)
            if c is None or not c.rundir:
                return None
            p = os.path.join(root, c.rundir, fn + ".engrad")
            if not os.path.isfile(p):
                return None
            try:
                return zpva_mod.read_engrad(p)
            except Exception:
                return None

        results = []
        for rel in manifests:
            mpath = os.path.join(root, rel)
            if not os.path.isfile(mpath):
                continue
            with open(mpath, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            hp = manifest.get("hess", "")
            hess_abs = hp if os.path.isabs(hp) else os.path.join(root, hp)
            try:
                res = zpva_mod.assemble_zpva(manifest, read_out, read_engrad, hess_path=hess_abs)
            except Exception as e:
                messagebox.showerror("ZPVA", "Assembly failed for {}: {}".format(
                    manifest.get("base"), e))
                continue
            results.append((manifest.get("base"), manifest, res))
        if not results:
            messagebox.showinfo("ZPVA", "No manifests found to assemble.")
            return
        _ZpvaResultsWindow(self, results, root)

    def _find_existing_calc(self, origin_node, mol, category, recipe_name):
        # Shared with the headless expander (matches by graph-node identity, then
        # by target directory) — see core.workflow_expand.find_existing_calc.
        return workflow_expand.find_existing_calc(self.app.project, origin_node, mol,
                                                  category, recipe_name)

    def _calc_done(self, calc):
        ct = getattr(self.app, "calculations_tab", None)
        if ct is None:
            return False
        try:
            return ct._own_state(calc)[2]
        except Exception:
            return False

    def _expand(self, verb, source_ids=None):
        """Validate + expand the graph into PlannedCalcs, asking the user to
        confirm. Reuses existing calcs for the same (graph node, molecule) so a
        re-run continues rather than duplicating. If `source_ids` is given, only
        those networks are expanded. Returns (calcs, node_map) or None."""
        # A Combine that feeds a calc but lacks explicit charge/mult gets a
        # dedicated guided fix (popup -> select node -> highlight + focus the
        # missing box), which takes priority over the generic blocker list.
        needs = self.wf.combine_needs_charge_mult()
        if needs:
            nid, missing = needs[0]
            labels = {"charge": "charge", "mult": "multiplicity"}
            messagebox.showwarning(
                "Combine: set charge & multiplicity",
                "A Combine node feeds a calculation, so its total {} must be set "
                "explicitly — auto-summing charges and coupling every unpaired spin is "
                "rarely right for a merged molecule.\n\nClick OK and I'll take you to the "
                "node; fill the highlighted box(es).".format(
                    " and ".join(labels[k] for k in missing)))
            self._guide_combine_fix(nid, missing)
            return None
        issues = self.wf.validate()
        # the multiple-Molecules note is informational, not a blocker
        blockers = [i for i in issues if not i.startswith("Multiple Molecules")]
        if blockers:
            messagebox.showwarning("Workflow not ready", "Fix these first:\n\n  • " +
                                   "\n  • ".join(blockers))
            return None
        mol_files = [m.filename for m in self.app.project.molecules]
        # Clean up any pre-existing duplicate rows (same target dir) first, so a
        # re-run reuses one canonical calc per directory instead of stacking up.
        ct = getattr(self.app, "calculations_tab", None)
        if ct is not None:
            try:
                ct.dedupe_by_target()
            except Exception:
                pass
        existing_before = {id(c) for c in self.app.project.planned_calcs}

        def factory(mol, recipe_name, category, geometry_source, parent_id, gate, origin_node):
            # An Optimize node may carry geometry constraints / a relaxed scan; the node
            # knows its own config (this closure has self.wf), so no signature change.
            onode = self.wf.node(origin_node)
            gspec = onode.config.get("geom_spec") if onode is not None else None
            existing = self._find_existing_calc(origin_node, mol, category, recipe_name)
            if existing is not None:
                # Adopt this graph node so future runs match by node identity too.
                if getattr(existing, "origin_node", None) is None:
                    existing.origin_node = origin_node
                # Keep finished steps verbatim; let unfinished ones adopt any
                # edits made to the graph (recipe / geometry / gate / geom_spec).
                if not self._calc_done(existing):
                    existing.recipe_name = recipe_name
                    existing.category = category
                    existing.geometry_source = geometry_source
                    existing.parent_id = parent_id
                    existing.gate = gate
                    existing.geom_spec = gspec
                return existing
            return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe_name,
                               category=category, geometry_source=geometry_source,
                               parent_id=parent_id, gate=gate, origin_node=origin_node,
                               geom_spec=gspec)

        # Geometry backend: Transform/Combine nodes compute their derived
        # molecules in memory during expansion; they're only written into the
        # project (below) after the user confirms.
        geom_backend, _geom_cache, geom_pending, geom_notes = self._make_geom_backend()
        calcs, warnings, node_map = wf_mod.expand_to_calcs(self.wf, mol_files, factory,
                                                           source_ids=source_ids,
                                                           transform_apply=geom_backend)
        warnings.extend(geom_notes)   # per-molecule transform skips, shown in the confirm
        if not calcs:
            messagebox.showinfo("Nothing generated",
                                "No calculations were produced.\n\n" + "\n".join(warnings))
            return None
        new_calcs = [c for c in calcs if id(c) not in existing_before]
        reused = len(calcs) - len(new_calcs)
        n_gated = sum(1 for c in calcs if getattr(c, "gate", None))
        scope = "selected pipeline" if source_ids is not None else "pipeline"
        msg = "{} {} calculation(s) from this {} in category '{}'?".format(
            verb, len(calcs), scope, self.wf.category)
        if reused:
            msg += "\n\n{} already exist and will be reused/continued; {} new.".format(
                reused, len(new_calcs))
        if n_gated:
            msg += "\n{} are conditional (gated by a Condition node).".format(n_gated)
        if warnings:
            msg += "\n\nNote:\n  • " + "\n  • ".join(dict.fromkeys(warnings))
        if not messagebox.askyesno(verb + " calculations", msg):
            return None
        # NOW write any Transform/Combine-derived molecules into the project
        # (before the calcs that reference them are built).
        if geom_pending:
            gwarns = self._flush_geom_materialisations(geom_pending)
            if gwarns:
                messagebox.showwarning("Derived geometries", "\n".join(gwarns))
        # Append only the genuinely-new calcs (reused ones are already in place).
        for c in new_calcs:
            self.app.project.planned_calcs.append(c)
        self._node_calcs = node_map
        self.app.mark_dirty()
        return calcs, node_map

    def _report_specs(self, source_ids=None):
        """For each Report node, the calc-node ids wired into it (its results
        feeders). Used to write a merged JSON when the pipeline finishes. If
        `source_ids` is given, only reports in those networks are included."""
        specs = []
        for n in self.wf.nodes:
            if n.type != "report":
                continue
            if source_ids is not None and not (self.wf.network_sources([n.id]) & source_ids):
                continue
            feeders = []
            for e in self.wf.edges_into(n.id):
                src = self.wf.node(e.src_node)
                if src is not None and src.type in wf_mod.CALC_NODE_TYPES and src.id not in feeders:
                    feeders.append(src.id)
            specs.append({"name": n.config.get("name", "report"), "node_ids": feeders,
                          "extractors": n.config.get("extractors"),
                          "format": n.config.get("format", "both"),
                          "csv_columns": n.config.get("csv_columns"),
                          "csv_missing": n.config.get("csv_missing", "")})
        return specs

    def _selected_sources(self):
        """If nodes are selected, the Molecules source(s) of the network(s) they
        belong to (so we run just those pipelines); else None = run everything.
        Returns None if the selection spans every source (i.e. all networks)."""
        if not self._sel_nodes:
            return None
        srcs = self.wf.network_sources(self._sel_nodes)
        all_srcs = {n.id for n in self.wf.nodes if n.type == "molecules"}
        if not srcs or srcs == all_srcs:
            return None
        return srcs

    def _remap_node_calcs(self):
        """Rebuild the node id -> [calc ids] map from the project by each calc's
        origin_node. This makes node status / result buttons work even after
        reopening a project (when no _expand ran this session). Keeps the existing
        map if the project has no workflow-tagged calcs."""
        m = {}
        for c in self.app.project.planned_calcs:
            nid = getattr(c, "origin_node", None)
            if nid:
                m.setdefault(nid, []).append(c.id)
        if m:
            self._node_calcs = m

    def on_refresh_status(self):
        """Re-query job status and rebuild the graph view so finished nodes light up
        and their per-node plot/viewer buttons appear. Queries SLURM directly and
        updates the Calc tab's status cache WITHOUT refreshing/switching to it (the
        node status reads that cache via ct._own_state)."""
        from orca_workbench.core import slurm_runtime
        ct = getattr(self.app, "calculations_tab", None)
        if ct is not None:
            ct._squeue_states = slurm_runtime.query_states()
            ct._status_known = True
        self._remap_node_calcs()
        self._redraw()
        self._build_config_panel()
        self.app.set_status("Workflow status refreshed.")

    def on_generate(self):
        res = self._expand("Create", source_ids=self._selected_sources())
        if res is None:
            return
        calcs, _ = res
        self.app.refresh_all_tabs()
        self.app.set_status("Workflow: created {} calculation(s). Go to Calculations to run."
                            .format(len(calcs)))
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass

    def on_run_pipeline(self):
        source_ids = self._selected_sources()
        res = self._expand("Run", source_ids=source_ids)
        if res is None:
            return
        calcs, _ = res
        self.app.refresh_all_tabs()
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass
        self.app.calculations_tab.start_pipeline(
            [c.id for c in calcs], reports=self._report_specs(source_ids=source_ids))
        scope = "selected pipeline" if source_ids is not None else "pipeline"
        self.app.set_status("{} running: {} calculation(s) under automatic control."
                            .format(scope.capitalize(), len(calcs)))
        self.refresh_live()

    def on_submit_unattended(self):
        source_ids = self._selected_sources()
        res = self._expand("Submit", source_ids=source_ids)
        if res is None:
            return
        calcs, _ = res
        self.app.refresh_all_tabs()
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass
        self.app.calculations_tab.submit_unattended(
            [c.id for c in calcs], reports=self._report_specs(source_ids=source_ids))
        self.refresh_live()

    # ----------------------------------------------------- live node coloring

    def _node_tags(self, node):
        """The per-calc display-state tags for a node's expanded calcs."""
        ids = self._node_calcs.get(node.id)
        ct = getattr(self.app, "calculations_tab", None)
        if not ids or ct is None:
            return []
        tags = []
        for cid in ids:
            calc = self.app.project.calc_by_id(cid)
            if calc is not None:
                tags.append(ct._display_state(calc)[1])
        return tags

    def _node_live_state(self, node):
        """Aggregate run-state across this node's expanded calcs, for coloring.
        Returns one of: '', 'waiting', 'running', 'done', 'error', 'skipped'."""
        tags = self._node_tags(node)
        if not tags:
            return ""
        # Worst-but-informative aggregation: error > running/waiting > skipped > done.
        if any(t == "error" for t in tags):
            return "error"
        if any(t in ("running",) for t in tags):
            return "running"
        if any(t in ("waiting", "notbuilt", "built") for t in tags):
            return "waiting"
        if all(t == "skipped" for t in tags):
            return "skipped"
        if any(t == "done" for t in tags):
            return "done"
        return ""

    def _node_progress(self, node):
        """A short live progress caption for a node, e.g. 'running 2/3', plus its
        colour. Returns (text, colour) or None when the node hasn't been run."""
        tags = self._node_tags(node)
        if not tags:
            return None
        total = len(tags)
        done = sum(1 for t in tags if t == "done")
        state = self._node_live_state(node)
        frac = "{}/{}".format(done, total)
        if state == "running":
            return ("running {}".format(frac), _STATE_COLOR["running"])
        if state == "error":
            return ("error {}".format(frac), _STATE_COLOR["error"])
        if state == "skipped":
            return ("skipped", _STATE_COLOR["skipped"])
        if any(t == "interrupted" for t in tags):
            return ("interrupted {}".format(frac), _STATE_COLOR["interrupted"])
        if state == "done":
            return ("done {}".format(frac), _STATE_COLOR["done"])
        return (frac, _STATE_COLOR["waiting"])

    def refresh_live(self):
        """Recolour nodes from the live calc states (called by the pipeline
        driver each tick). Cheap: just a redraw."""
        if self._node_calcs:
            self._redraw()


_ZPVA_UNITS = {"nmr_shielding": "ppm", "energy": "Eh", "dipole": "Debye"}


class _ZpvaResultsWindow(tk.Toplevel):
    """Show assembled ZPVA results (per molecule × isotopologue): the static
    property, the harmonic/anharmonic correction, the ZPVA-averaged value and —
    when isotopologues were requested — the isotope shift vs the base. Writes a
    JSON report per molecule plus a combined CSV, and draws a shift/correction
    bar chart."""

    def __init__(self, parent, results, root):
        super().__init__(parent)
        self.title("ZPVA results")
        self.geometry("820x520")
        self._results = results            # [(base, manifest, res), ...]
        self._root = root
        kind = results[0][2].get("property", {}).get("kind", "")
        self._unit = _ZPVA_UNITS.get(kind, "")

        ttk.Label(self, text="ZPVA-averaged {} ({}).  P_e = static value; correction = "
                  "harmonic + anharmonic; shift = vs the base isotopologue.".format(
                      kind or "property", self._unit or "a.u."),
                  wraplength=780, foreground="#444").pack(anchor=tk.W, padx=10, pady=(10, 4))

        cols = ("molecule", "isotopologue", "p_e", "harmonic", "anharmonic", "zpva", "shift")
        heads = ("Molecule", "Isotopologue", "P_e", "Harmonic", "Anharmonic", "<P> (ZPVA)",
                 "Shift vs base")
        tree = ttk.Treeview(self, columns=cols, show="headings", height=10)
        for c, h in zip(cols, heads):
            tree.heading(c, text=h)
            tree.column(c, width=110 if c in ("molecule", "isotopologue") else 95,
                        anchor=tk.W if c in ("molecule", "isotopologue") else tk.E)
        tree.pack(fill=tk.X, padx=10, pady=4)
        self._fill_table(tree)

        missing = [lab for _b, _m, r in results for lab in r.get("missing", [])]
        if missing:
            ttk.Label(self, text="Incomplete (jobs not finished): {}".format(
                ", ".join(sorted(set(missing))[:12])), foreground="#b00000",
                wraplength=780).pack(anchor=tk.W, padx=10)

        self._chart_frame = ttk.Frame(self)
        self._chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self._draw_chart()

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.status = ttk.Label(bar, text="", foreground="#1a7a1a")
        self.status.pack(side=tk.LEFT)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        self._write_reports()

    def _rows(self):
        for base, _man, res in self._results:
            pe = res.get("P_e")
            for label, r in res.get("isotopologues", {}).items():
                yield (base, label, pe, r.get("harmonic"), r.get("anharmonic"),
                       r.get("property_zpva"), r.get("shift_vs_base"))

    @staticmethod
    def _fmt(v):
        return "" if v is None else "{:.4f}".format(v)

    def _fill_table(self, tree):
        for base, label, pe, harm, anh, zp, shift in self._rows():
            tree.insert("", tk.END, values=(base, label, self._fmt(pe), self._fmt(harm),
                                            self._fmt(anh), self._fmt(zp), self._fmt(shift)))

    def _draw_chart(self):
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as e:
            ttk.Label(self._chart_frame, text="(chart unavailable: {})".format(e),
                      foreground="#888").pack()
            return
        # Prefer isotope shifts; if there are none (base only), show the corrections.
        shifts = [("{}:{}".format(b, lab), sh) for b, lab, _pe, _h, _a, _z, sh in self._rows()
                  if lab != "base" and sh is not None]
        if shifts:
            labels, vals = zip(*shifts)
            title, ylab = "ZPVA isotope shifts", "shift ({})".format(self._unit)
        else:
            data = [("{}:{}".format(b, lab), c) for b, lab, _pe, _h, _a, _z, _s in self._rows()
                    for c in [(_h or 0) + (_a or 0)]]
            if not data:
                return
            labels, vals = zip(*data)
            title, ylab = "ZPVA corrections", "correction ({})".format(self._unit)
        fig = Figure(figsize=(7.4, 2.8), dpi=100)
        ax = fig.add_subplot(111)
        ax.bar(range(len(vals)), vals, color="#7b5ea7")
        ax.axhline(0, color="#888", linewidth=0.6)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=7)
        ax.set_ylabel(ylab)
        ax.set_title(title, fontsize=10)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self._chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _write_reports(self):
        wrote = []
        csv_rows = []
        for base, _man, res in self._results:
            jpath = os.path.join(self._root, "ZPVA", "{}_zpva_report.json".format(
                safe_path_component(base)))
            try:
                os.makedirs(os.path.dirname(jpath), exist_ok=True)
                with open(jpath, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, indent=2)
                wrote.append(jpath)
            except OSError:
                pass
        for base, label, pe, harm, anh, zp, shift in self._rows():
            csv_rows.append({"molecule": base, "isotopologue": label, "P_e": pe,
                             "harmonic": harm, "anharmonic": anh, "property_zpva": zp,
                             "shift_vs_base": shift})
        if csv_rows:
            cpath = os.path.join(self._root, "ZPVA", "zpva_report.csv")
            try:
                with open(cpath, "w", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=["molecule", "isotopologue", "P_e",
                                       "harmonic", "anharmonic", "property_zpva", "shift_vs_base"])
                    w.writeheader()
                    for r in csv_rows:
                        w.writerow(r)
                wrote.append(cpath)
            except OSError:
                pass
        if wrote:
            self.status.configure(text="Wrote {} report file(s) to ZPVA/.".format(len(wrote)))
