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

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from orca_studio.core import workflow as wf_mod
from orca_studio.core.project import PlannedCalc, new_calc_id
from orca_studio.ui.tooltip import tip


NODE_W = 158
TITLE_H = 22
PORT_H = 20
PORT_R = 5

_KIND_COLOR = {"source": "#cfe8cf", "calc": "#d3e6f5", "sink": "#f0dcc0", "gate": "#ede0c8"}
_BODY = "#fbfbfb"
_SEL = "#1f6fb2"

# Live execution status → accent colour (border + badge) for a node.
_STATE_COLOR = {
    "waiting": "#9e9e9e",
    "running": "#1e88e5",
    "done": "#2e7d32",
    "error": "#c62828",
    "skipped": "#7e57c2",
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
        self._add_offset = 0
        # Live execution: node_id -> [calc id, ...] from the last "Run pipeline".
        self._node_calcs = {}
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))
        ttk.Label(bar, text="Add node:").pack(side=tk.LEFT, padx=(2, 4))
        for ntype in ("molecules", "optimize", "frequencies", "property", "condition", "report"):
            label = wf_mod.NODE_TYPES[ntype]["label"]
            ttk.Button(bar, text=label, width=max(8, len(label) + 1),
                       command=lambda t=ntype: self._add_node(t)).pack(side=tk.LEFT, padx=1)

        b_run = tk.Button(bar, text="▶ Run pipeline", command=self.on_run_pipeline,
                          font=("TkDefaultFont", 10, "bold"), bg="#e0a35a",
                          activebackground="#e8b673", fg="#222222")
        b_run.pack(side=tk.RIGHT, padx=(6, 2))
        b_gen = tk.Button(bar, text="Generate only", command=self.on_generate,
                          bg="#cfe0f5", activebackground="#bcd6f0")
        b_gen.pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="Clear", command=self.on_clear).pack(side=tk.RIGHT, padx=2)
        tip(b_run, "Expand this pipeline into calculations and run them automatically: each step "
                   "builds and launches as its input geometry becomes ready, and a Condition node "
                   "decides live whether its downstream branch runs (e.g. only do NMR if the "
                   "Frequencies job found no imaginary modes). Watch progress here and on the "
                   "Calculations tab.")
        tip(b_gen, "Expand the pipeline into planned calculations (with geometry parent-links and "
                   "conditional gates) and jump to the Calculations tab — but don't launch them. "
                   "Use this if you want to review or edit before running.")

        ttk.Label(self, text="Drag a node to move · drag an output port onto an input to wire (drop "
                  "on empty space to pick a new node) · drag empty space to box-select · Ctrl+click "
                  "to multi-select · Ctrl+A all · J connects two selected nodes · F3 adds a node · "
                  "middle/right-drag to pan · Delete removes.",
                  foreground="#666", wraplength=900, justify=tk.LEFT).pack(
                      side=tk.TOP, anchor=tk.W, padx=8, pady=2)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        cframe = ttk.Frame(paned)
        paned.add(cframe, weight=4)
        self.canvas = tk.Canvas(cframe, background="#eef1f4", highlightthickness=0,
                                scrollregion=(0, 0, 4000, 3000))
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        # Pan on middle-drag (left-drag in empty space is box-select).
        self.canvas.bind("<Button-2>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B2-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        # Right button: drag pans, click (no drag) opens the context menu.
        self.canvas.bind("<Button-3>", self._on_rpress)
        self.canvas.bind("<B3-Motion>", self._on_rmotion)
        self.canvas.bind("<ButtonRelease-3>", self._on_rrelease)
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())
        self.canvas.bind("<Delete>", lambda e: self._delete_selected())
        self.canvas.bind("<Control-a>", self._on_select_all)
        self.canvas.bind("<Control-A>", self._on_select_all)
        self.canvas.bind("<j>", lambda e: self._connect_selected())
        self.canvas.bind("<J>", lambda e: self._connect_selected())
        self.canvas.bind("<F3>", lambda e: self._on_search_add())

        self.cfg_frame = ttk.LabelFrame(paned, text="Node settings")
        paned.add(self.cfg_frame, weight=1)
        self._build_config_panel()

    def _build_config_panel(self):
        for w in self.cfg_frame.winfo_children():
            w.destroy()
        if len(self._sel_nodes) > 1:
            ttk.Label(self.cfg_frame,
                      text="{} nodes selected.\n\nPress J to connect two of them, drag to move "
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
            ttk.Label(self.cfg_frame, text="Recipe:").pack(anchor=tk.W, padx=8)
            var = tk.StringVar(value=node.config.get("recipe", ""))
            cb = ttk.Combobox(self.cfg_frame, textvariable=var, state="readonly",
                              values=[r.name for r in self.app.recipes], width=26)
            cb.pack(anchor=tk.W, padx=8, pady=2)
            cb.bind("<<ComboboxSelected>>",
                    lambda e, n=node, v=var: self._set_cfg(n, "recipe", v.get()))
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

        ttk.Separator(self.cfg_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(self.cfg_frame, text="Delete node",
                   command=lambda nid=node.id: self._delete_node(nid)).pack(anchor=tk.W, padx=8)

    def _set_cfg(self, node, key, value):
        node.config[key] = value
        self._commit()
        self._redraw()

    # ----------------------------------------------------- workflow <-> project

    def refresh(self):
        self.wf = wf_mod.Workflow.from_dict(self.app.project.workflow)
        # drop selections that no longer exist
        self._sel_nodes = [nid for nid in self._sel_nodes if self.wf.node(nid) is not None]
        if self._sel_edge is not None and self.wf.edge(self._sel_edge) is None:
            self._sel_edge = None
        self._redraw()
        self._build_config_panel()

    def _commit(self):
        self.app.project.workflow = self.wf.to_dict()
        self.app.mark_dirty()

    # --------------------------------------------------------------- geometry

    def _node_height(self, node):
        n = max(len(node.inputs()), len(node.outputs()), 1)
        return TITLE_H + n * PORT_H + 8

    def _port_xy(self, node, port_name, is_input):
        ports = node.inputs() if is_input else node.outputs()
        for i, (name, _t) in enumerate(ports):
            if name == port_name:
                y = node.y + TITLE_H + i * PORT_H + PORT_H / 2
                x = node.x if is_input else node.x + NODE_W
                return x, y
        return None

    # --------------------------------------------------------------- drawing

    def _redraw(self):
        self.canvas.delete("all")
        for e in self.wf.edges:
            self._draw_edge(e)
        for n in self.wf.nodes:
            self._draw_node(n)
        # transient wire while connecting
        if self._mode == "wire" and self._drag and self._drag.get("temp"):
            x0, y0 = self._drag["from_xy"]
            x1, y1 = self._drag["cur"]
            self.canvas.create_line(x0, y0, x1, y1, fill="#888", width=2, dash=(3, 2),
                                    tags=("temp",))
        # transient rubber-band rectangle while box-selecting
        if self._mode == "box" and self._drag:
            x0, y0 = self._drag["x0"], self._drag["y0"]
            x1, y1 = self._drag["cur"]
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=_SEL, width=1,
                                         dash=(4, 3), tags=("temp",))

    def _draw_node(self, node):
        x, y = node.x, node.y
        h = self._node_height(node)
        selected = node.id in self._sel_nodes
        ntag = "N:" + node.id
        live = self._node_live_state(node)
        if selected:
            outline, width = _SEL, 3
        elif live:
            outline, width = _STATE_COLOR.get(live, "#7a8a99"), 3
        else:
            outline, width = "#7a8a99", 1
        self.canvas.create_rectangle(x, y, x + NODE_W, y + h, fill=_BODY, outline=outline,
                                     width=width, tags=(ntag, "nodebody"))
        self.canvas.create_rectangle(x, y, x + NODE_W, y + TITLE_H,
                                     fill=_KIND_COLOR.get(node.kind, "#ddd"),
                                     outline=outline, width=width, tags=(ntag,))
        self.canvas.create_text(x + 8, y + TITLE_H / 2, anchor=tk.W, text=node.label,
                                font=("TkDefaultFont", 9, "bold"), tags=(ntag,))
        if live:
            # status badge — a filled dot at the title's right edge
            bx = x + NODE_W - 11
            by = y + TITLE_H / 2
            self.canvas.create_oval(bx - 5, by - 5, bx + 5, by + 5,
                                    fill=_STATE_COLOR.get(live, "#888"), outline="#333",
                                    tags=(ntag,))
        # config summary line
        summ = self._node_summary(node)
        if summ:
            self.canvas.create_text(x + 8, y + TITLE_H + 2, anchor=tk.NW, text=summ,
                                    font=("TkDefaultFont", 8), fill="#555", width=NODE_W - 16,
                                    tags=(ntag,))
        # ports
        for i, (name, ptype) in enumerate(node.inputs()):
            py = y + TITLE_H + i * PORT_H + PORT_H / 2
            self._draw_port(node.id, name, True, x, py, ptype)
        for i, (name, ptype) in enumerate(node.outputs()):
            py = y + TITLE_H + i * PORT_H + PORT_H / 2
            self._draw_port(node.id, name, False, x + NODE_W, py, ptype)

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
        return ""

    def _draw_port(self, node_id, name, is_input, x, y, ptype):
        color = "#2a8a2a" if ptype == "geometry" else "#b06000"
        tag = "P:{}:{}:{}".format(node_id, "in" if is_input else "out", name)
        self.canvas.create_oval(x - PORT_R, y - PORT_R, x + PORT_R, y + PORT_R,
                                fill=color, outline="#333", tags=(tag, "port"))
        lx = x + PORT_R + 3 if is_input else x - PORT_R - 3
        self.canvas.create_text(lx, y, anchor=(tk.W if is_input else tk.E), text=name,
                                font=("TkDefaultFont", 7), fill="#444", tags=("P:" + node_id,))

    def _draw_edge(self, e):
        src = self.wf.node(e.src_node)
        dst = self.wf.node(e.dst_node)
        if src is None or dst is None:
            return
        a = self._port_xy(src, e.src_port, is_input=False)
        b = self._port_xy(dst, e.dst_port, is_input=True)
        if not a or not b:
            return
        selected = self._sel_edge == e.id
        col = _SEL if selected else "#5a6b7a"
        w = 3 if selected else 2
        dx = max(30, abs(b[0] - a[0]) * 0.4)
        self.canvas.create_line(a[0], a[1], a[0] + dx, a[1], b[0] - dx, b[1], b[0], b[1],
                                smooth=True, width=w, fill=col, tags=("E:" + e.id, "edge"))

    # --------------------------------------------------------------- events

    def _cxy(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

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
        cx, cy = self._cxy(event)
        ctrl = bool(event.state & 0x0004)
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
                self._toggle_node(nid)
                self._mode = None
                self._drag = None
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
        if hit[0] == "port" and hit[3]:       # input port → select its node
            if ctrl:
                self._toggle_node(hit[1])
            else:
                self._select_only(hit[1])
            self._mode = None
            self._drag = None

    def _begin_node_drag(self, cx, cy, collapse_to=None):
        orig = {}
        for nid in self._sel_nodes:
            n = self.wf.node(nid)
            if n is not None:
                orig[nid] = (n.x, n.y)
        self._mode = "drag"
        self._drag = {"orig": orig, "ox": cx, "oy": cy, "moved": False,
                      "collapse_to": collapse_to}

    def _on_motion(self, event):
        cx, cy = self._cxy(event)
        if self._mode == "drag" and self._drag:
            dx, dy = cx - self._drag["ox"], cy - self._drag["oy"]
            if abs(dx) > 2 or abs(dy) > 2:
                self._drag["moved"] = True
            for nid, (ox, oy) in self._drag["orig"].items():
                n = self.wf.node(nid)
                if n is not None:
                    n.x, n.y = ox + dx, oy + dy
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
        if mode == "drag" and drag:
            if drag.get("moved"):
                self._commit()
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
        if target is not None:
            self._try_add_edge(sn, sp, target[0], target[1])
            self._redraw()
            return
        # 3) released in empty space → Blender-style add-node search, then connect
        self._redraw()  # clear the temp wire before the popup
        ntype = self._node_search_popup(event.x_root, event.y_root,
                                        out_type=self._out_port_type(sn, sp))
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
        if edge is None and why:
            self.app.set_status("Can't connect: " + why)
            return False
        self._commit()
        return True

    # ---- hit-testing in canvas coords (robust to the temp wire on top) ----

    def _input_port_at(self, cx, cy):
        r = PORT_R + 8
        for n in self.wf.nodes:
            for i, (name, _t) in enumerate(n.inputs()):
                px = n.x
                py = n.y + TITLE_H + i * PORT_H + PORT_H / 2.0
                if abs(px - cx) <= r and abs(py - cy) <= r:
                    return (n.id, name)
        return None

    def _node_at(self, cx, cy):
        for n in reversed(self.wf.nodes):   # last drawn = on top
            h = self._node_height(n)
            if n.x <= cx <= n.x + NODE_W and n.y <= cy <= n.y + h:
                return n.id
        return None

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
            h = self._node_height(n)
            if (n.x <= hi_x and n.x + NODE_W >= lo_x
                    and n.y <= hi_y and n.y + h >= lo_y):
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

    def _clear_selection(self):
        self._sel_nodes = []
        self._sel_edge = None
        self._redraw()
        self._build_config_panel()

    # ---- right button: drag pans, click opens a context menu ----

    def _on_rpress(self, event):
        self.canvas.focus_set()
        self.canvas.scan_mark(event.x, event.y)
        self._rclick = {"x": event.x, "y": event.y, "moved": False}

    def _on_rmotion(self, event):
        if getattr(self, "_rclick", None) is not None:
            if abs(event.x - self._rclick["x"]) > 3 or abs(event.y - self._rclick["y"]) > 3:
                self._rclick["moved"] = True
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_rrelease(self, event):
        rc = getattr(self, "_rclick", None)
        self._rclick = None
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
        cx, cy = self._cxy(event)
        hit = self._hit_xy(cx, cy)
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
            menu.add_command(label="Add node here…", command=lambda: self._context_add(cx, cy))
        elif hit and hit[0] == "edge":
            self._select_edge(hit[1])
            menu.add_command(label="Delete connection", command=self._delete_selected)
        else:
            menu.add_command(label="Add node here…", command=lambda: self._context_add(cx, cy))
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
            self.app.set_status("Select exactly two nodes (Ctrl+click), then press J to connect.")
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

    def _delete_selected(self):
        if self._sel_edge is not None:
            self.wf.remove_edge(self._sel_edge)
            self._sel_edge = None
            self._commit()
            self._redraw()
            self._build_config_panel()
            return
        if self._sel_nodes:
            for nid in list(self._sel_nodes):
                self.wf.remove_node(nid)
            self._sel_nodes = []
            self._commit()
            self._redraw()
            self._build_config_panel()

    def _delete_node(self, node_id):
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
        cx, cy = self.canvas.canvasx(rx), self.canvas.canvasy(ry)
        if rx < 0 or ry < 0 or rx > self.canvas.winfo_width() or ry > self.canvas.winfo_height():
            cx, cy = self.canvas.canvasx(40), self.canvas.canvasy(40)
            px, py = self.canvas.winfo_rootx() + 60, self.canvas.winfo_rooty() + 60
        ntype = self._node_search_popup(px, py)
        if ntype:
            node = self.wf.add_node(ntype, cx, cy)
            self._commit()
            self._select_only(node.id)
        return "break"

    def _node_search_popup(self, screen_x, screen_y, out_type=None):
        """Searchable add-node menu (like Blender's Shift+A search). Returns a
        node type string or None. When out_type is given (dragging from an
        output), node types able to accept it are listed first."""
        order = ["molecules", "optimize", "frequencies", "property", "condition", "report"]

        def accepts(ntype):
            if not out_type:
                return False
            return any(pt == out_type for _n, pt in wf_mod.NODE_TYPES[ntype]["inputs"])

        all_items = [(wf_mod.NODE_TYPES[t]["label"], t) for t in order]
        if out_type:
            all_items.sort(key=lambda it: (not accepts(it[1]),))  # compatible first (stable)

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
                     if q in lbl.lower() or q in nt.lower()]
            state["items"] = items
            for lbl, nt in items:
                mark = "  (connects)" if (out_type and accepts(nt)) else ""
                lb.insert(tk.END, lbl + mark)
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
        self._add_offset = (self._add_offset + 1) % 8
        x = 60 + self._add_offset * 26
        y = 60 + self._add_offset * 26
        node = self.wf.add_node(ntype, x, y)
        self._commit()
        self._select_only(node.id)

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

    def _find_existing_calc(self, origin_node, mol):
        if not origin_node:
            return None
        for c in self.app.project.planned_calcs:
            if getattr(c, "origin_node", None) == origin_node and c.molecule_filename == mol:
                return c
        return None

    def _calc_done(self, calc):
        ct = getattr(self.app, "calculations_tab", None)
        if ct is None:
            return False
        try:
            return ct._own_state(calc)[2]
        except Exception:
            return False

    def _expand(self, verb):
        """Validate + expand the graph into PlannedCalcs, asking the user to
        confirm. Reuses existing calcs for the same (graph node, molecule) so a
        re-run continues rather than duplicating. Returns (calcs, node_map) or
        None if blocked/declined."""
        issues = self.wf.validate()
        # 'more than one Molecules' is a warning, not a blocker
        blockers = [i for i in issues if not i.startswith("More than one")]
        if blockers:
            messagebox.showwarning("Workflow not ready", "Fix these first:\n\n  • " +
                                   "\n  • ".join(blockers))
            return None
        mol_files = [m.filename for m in self.app.project.molecules]
        existing_before = {id(c) for c in self.app.project.planned_calcs}

        def factory(mol, recipe_name, category, geometry_source, parent_id, gate, origin_node):
            existing = self._find_existing_calc(origin_node, mol)
            if existing is not None:
                # Keep finished steps verbatim; let unfinished ones adopt any
                # edits made to the graph (recipe / geometry / gate).
                if not self._calc_done(existing):
                    existing.recipe_name = recipe_name
                    existing.category = category
                    existing.geometry_source = geometry_source
                    existing.parent_id = parent_id
                    existing.gate = gate
                return existing
            return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe_name,
                               category=category, geometry_source=geometry_source,
                               parent_id=parent_id, gate=gate, origin_node=origin_node)

        calcs, warnings, node_map = wf_mod.expand_to_calcs(self.wf, mol_files, factory)
        if not calcs:
            messagebox.showinfo("Nothing generated",
                                "No calculations were produced.\n\n" + "\n".join(warnings))
            return None
        new_calcs = [c for c in calcs if id(c) not in existing_before]
        reused = len(calcs) - len(new_calcs)
        n_gated = sum(1 for c in calcs if getattr(c, "gate", None))
        msg = "{} {} calculation(s) from this pipeline in category '{}'?".format(
            verb, len(calcs), self.wf.category)
        if reused:
            msg += "\n\n{} already exist and will be reused/continued; {} new.".format(
                reused, len(new_calcs))
        if n_gated:
            msg += "\n{} are conditional (gated by a Condition node).".format(n_gated)
        if warnings:
            msg += "\n\nNote:\n  • " + "\n  • ".join(dict.fromkeys(warnings))
        if not messagebox.askyesno(verb + " calculations", msg):
            return None
        # Append only the genuinely-new calcs (reused ones are already in place).
        for c in new_calcs:
            self.app.project.planned_calcs.append(c)
        self._node_calcs = node_map
        self.app.mark_dirty()
        return calcs, node_map

    def _report_specs(self):
        """For each Report node, the calc-node ids wired into it (its results
        feeders). Used to write a merged JSON when the pipeline finishes."""
        specs = []
        for n in self.wf.nodes:
            if n.type != "report":
                continue
            feeders = []
            for e in self.wf.edges_into(n.id):
                src = self.wf.node(e.src_node)
                if src is not None and src.type in wf_mod.CALC_NODE_TYPES and src.id not in feeders:
                    feeders.append(src.id)
            specs.append({"name": n.config.get("name", "report"), "node_ids": feeders})
        return specs

    def on_generate(self):
        res = self._expand("Create")
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
        res = self._expand("Run")
        if res is None:
            return
        calcs, _ = res
        self.app.refresh_all_tabs()
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass
        self.app.calculations_tab.start_pipeline([c.id for c in calcs],
                                                 reports=self._report_specs())
        self.app.set_status("Pipeline running: {} calculation(s) under automatic control."
                            .format(len(calcs)))
        self.refresh_live()

    # ----------------------------------------------------- live node coloring

    def _node_live_state(self, node):
        """Aggregate run-state across this node's expanded calcs, for coloring.
        Returns one of: '', 'waiting', 'running', 'done', 'error', 'skipped'."""
        ids = self._node_calcs.get(node.id)
        if not ids:
            return ""
        ct = getattr(self.app, "calculations_tab", None)
        if ct is None:
            return ""
        tags = []
        for cid in ids:
            calc = self.app.project.calc_by_id(cid)
            if calc is None:
                continue
            tags.append(ct._display_state(calc)[1])
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

    def refresh_live(self):
        """Recolour nodes from the live calc states (called by the pipeline
        driver each tick). Cheap: just a redraw."""
        if self._node_calcs:
            self._redraw()
