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

_KIND_COLOR = {"source": "#cfe8cf", "calc": "#d3e6f5", "sink": "#f0dcc0"}
_BODY = "#fbfbfb"
_SEL = "#1f6fb2"


class WorkflowTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.wf = wf_mod.Workflow()
        self._selected = None      # ("node"|"edge", id)
        self._mode = None          # "drag" | "wire" | "pan" | None
        self._drag = None          # transient drag state
        self._add_offset = 0
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))
        ttk.Label(bar, text="Add node:").pack(side=tk.LEFT, padx=(2, 4))
        for ntype in ("molecules", "optimize", "frequencies", "property", "report"):
            label = wf_mod.NODE_TYPES[ntype]["label"]
            ttk.Button(bar, text=label, width=max(8, len(label) + 1),
                       command=lambda t=ntype: self._add_node(t)).pack(side=tk.LEFT, padx=1)

        b_gen = tk.Button(bar, text="Generate calculations", command=self.on_generate,
                          font=("TkDefaultFont", 10, "bold"), bg="#cfe0f5",
                          activebackground="#bcd6f0")
        b_gen.pack(side=tk.RIGHT, padx=(6, 2))
        ttk.Button(bar, text="Clear", command=self.on_clear).pack(side=tk.RIGHT, padx=2)
        tip(b_gen, "Expand this pipeline across the Molecules source into planned calculations "
                   "(with geometry parent-links from the wires) and jump to the Calculations tab. "
                   "Condition-free graphs only for now.")

        ttk.Label(self, text="Drag a node to move it · drag from an output port (right) to an input "
                  "port (left) to connect · drag empty space to pan · Delete removes the selection.",
                  foreground="#666").pack(side=tk.TOP, anchor=tk.W, padx=8, pady=2)

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
        self.canvas.bind("<Enter>", lambda e: self.canvas.focus_set())
        self.canvas.bind("<Delete>", lambda e: self._delete_selected())

        self.cfg_frame = ttk.LabelFrame(paned, text="Node settings")
        paned.add(self.cfg_frame, weight=1)
        self._build_config_panel()

    def _build_config_panel(self):
        for w in self.cfg_frame.winfo_children():
            w.destroy()
        sel = self._selected
        if not sel or sel[0] != "node":
            ttk.Label(self.cfg_frame, text="Select a node to configure it.",
                      foreground="#888", wraplength=200).pack(padx=8, pady=8)
            return
        node = self.wf.node(sel[1])
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
        # drop a selection that no longer exists
        if self._selected and self._selected[0] == "node" and self.wf.node(self._selected[1]) is None:
            self._selected = None
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

    def _draw_node(self, node):
        x, y = node.x, node.y
        h = self._node_height(node)
        selected = self._selected == ("node", node.id)
        ntag = "N:" + node.id
        outline = _SEL if selected else "#7a8a99"
        width = 3 if selected else 1
        self.canvas.create_rectangle(x, y, x + NODE_W, y + h, fill=_BODY, outline=outline,
                                     width=width, tags=(ntag, "nodebody"))
        self.canvas.create_rectangle(x, y, x + NODE_W, y + TITLE_H,
                                     fill=_KIND_COLOR.get(node.kind, "#ddd"),
                                     outline=outline, width=width, tags=(ntag,))
        self.canvas.create_text(x + 8, y + TITLE_H / 2, anchor=tk.W, text=node.label,
                                font=("TkDefaultFont", 9, "bold"), tags=(ntag,))
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
        selected = self._selected == ("edge", e.id)
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
        cx, cy = self._cxy(event)
        hit = self._hit(event)
        if hit is None:
            # pan + deselect
            self._mode = "pan"
            self.canvas.scan_mark(event.x, event.y)
            self._set_selected(None)
            return
        if hit[0] == "port" and not hit[3]:   # output port → start wire
            node = self.wf.node(hit[1])
            self._mode = "wire"
            self._drag = {"src": (hit[1], hit[2]),
                          "from_xy": self._port_xy(node, hit[2], False),
                          "cur": (cx, cy), "temp": True}
            return
        if hit[0] == "node":
            self._set_selected(("node", hit[1]))
            node = self.wf.node(hit[1])
            self._mode = "drag"
            self._drag = {"id": hit[1], "ox": cx, "oy": cy, "nx": node.x, "ny": node.y, "moved": False}
            return
        if hit[0] == "edge":
            self._set_selected(("edge", hit[1]))
            self._mode = None
            return
        if hit[0] == "port" and hit[3]:       # input port → just select its node
            self._set_selected(("node", hit[1]))
            self._mode = None

    def _on_motion(self, event):
        cx, cy = self._cxy(event)
        if self._mode == "pan":
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            return
        if self._mode == "drag" and self._drag:
            node = self.wf.node(self._drag["id"])
            if node is None:
                return
            node.x = self._drag["nx"] + (cx - self._drag["ox"])
            node.y = self._drag["ny"] + (cy - self._drag["oy"])
            self._drag["moved"] = True
            self._redraw()
            return
        if self._mode == "wire" and self._drag:
            self._drag["cur"] = (cx, cy)
            self._redraw()

    def _on_release(self, event):
        if self._mode == "drag" and self._drag and self._drag.get("moved"):
            self._commit()
        elif self._mode == "wire" and self._drag:
            hit = self._hit(event)
            if hit and hit[0] == "port" and hit[3]:   # released on an input port
                sn, sp = self._drag["src"]
                edge, why = self.wf.add_edge(sn, sp, hit[1], hit[2])
                if edge is None and why:
                    self.app.set_status("Can't connect: " + why)
                else:
                    self._commit()
            self._drag = None
            self._redraw()
        self._mode = None
        self._drag = None

    def _set_selected(self, sel):
        if sel != self._selected:
            self._selected = sel
            self._redraw()
            self._build_config_panel()

    def _delete_selected(self):
        if not self._selected:
            return
        kind, ident = self._selected
        if kind == "node":
            self._delete_node(ident)
        elif kind == "edge":
            self.wf.remove_edge(ident)
            self._selected = None
            self._commit()
            self._redraw()

    def _delete_node(self, node_id):
        self.wf.remove_node(node_id)
        self._selected = None
        self._commit()
        self._redraw()
        self._build_config_panel()

    # --------------------------------------------------------------- actions

    def _add_node(self, ntype):
        self._add_offset = (self._add_offset + 1) % 8
        x = 60 + self._add_offset * 26
        y = 60 + self._add_offset * 26
        node = self.wf.add_node(ntype, x, y)
        self._commit()
        self._set_selected(("node", node.id))
        self._redraw()

    def on_clear(self):
        if not self.wf.nodes:
            return
        if not messagebox.askyesno("Clear workflow", "Remove all nodes and connections?"):
            return
        self.wf = wf_mod.Workflow(category=self.wf.category)
        self._selected = None
        self._commit()
        self._redraw()
        self._build_config_panel()

    def on_generate(self):
        issues = self.wf.validate()
        # 'more than one Molecules' is a warning, not a blocker
        blockers = [i for i in issues if not i.startswith("More than one")]
        if blockers:
            messagebox.showwarning("Workflow not ready", "Fix these first:\n\n  • " +
                                   "\n  • ".join(blockers))
            return
        mol_files = [m.filename for m in self.app.project.molecules]

        def factory(mol, recipe_name, category, geometry_source, parent_id):
            return PlannedCalc(id=new_calc_id(), molecule_filename=mol, recipe_name=recipe_name,
                               category=category, geometry_source=geometry_source,
                               parent_id=parent_id)

        calcs, warnings = wf_mod.expand_to_calcs(self.wf, mol_files, factory)
        if not calcs:
            messagebox.showinfo("Nothing generated",
                                "No calculations were produced.\n\n" + "\n".join(warnings))
            return
        msg = "Create {} calculation(s) from this pipeline in category '{}'?".format(
            len(calcs), self.wf.category)
        if warnings:
            msg += "\n\nNote:\n  • " + "\n  • ".join(dict.fromkeys(warnings))
        if not messagebox.askyesno("Generate calculations", msg):
            return
        self.app.project.planned_calcs.extend(calcs)
        self.app.mark_dirty()
        self.app.refresh_all_tabs()
        self.app.set_status("Workflow: created {} calculation(s). Go to Calculations to run."
                            .format(len(calcs)))
        try:
            self.app.notebook.select(self.app.calculations_tab)
        except Exception:
            pass
