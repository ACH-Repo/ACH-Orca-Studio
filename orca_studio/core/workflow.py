"""Workflow model — a node graph describing a computational pipeline.

A workflow is a directed acyclic graph. Nodes are pipeline steps; typed ports
carry tokens between them:

  - geometry : a molecular structure flowing into a calculation
  - results  : computed properties (energies, frequencies, shieldings, …)

The graph is authored visually in the Workflow tab and persisted in project.json.
For Milestone 1 it is expanded *statically* (no conditionals) into the existing
PlannedCalc model: every calculation node, for every molecule emitted by the
Molecules source, becomes a PlannedCalc, and geometry edges become parent_id
links — so the whole thing runs through the normal build/submit/monitor path.

This module is pure (no Tkinter) so it is unit-testable and serialisable.
"""

import uuid
from typing import Dict, List, Optional, Tuple


# Node-type registry. Each: label, input ports, output ports (name, token type),
# kind (source/calc/sink), and which config keys it carries.
NODE_TYPES = {
    "molecules": {
        "label": "Molecules",
        "inputs": [],
        "outputs": [("geometry", "geometry")],
        "kind": "source",
        "config": {"mode": "all", "filenames": []},
    },
    "optimize": {
        "label": "Optimize",
        "inputs": [("geometry", "geometry")],
        "outputs": [("geometry", "geometry")],
        "kind": "calc",
        "config": {"recipe": ""},
    },
    "frequencies": {
        "label": "Frequencies",
        "inputs": [("geometry", "geometry")],
        "outputs": [("geometry", "geometry"), ("results", "results")],
        "kind": "calc",
        "config": {"recipe": ""},
    },
    "property": {
        "label": "Property (SP/NMR/…)",
        "inputs": [("geometry", "geometry")],
        "outputs": [("results", "results")],
        "kind": "calc",
        "config": {"recipe": ""},
    },
    "condition": {
        "label": "Condition",
        "inputs": [("in", "geometry")],
        "outputs": [("pass", "geometry")],
        "kind": "gate",
        "config": {"predicate": "no_imaginary_freqs"},
    },
    "report": {
        "label": "Report",
        "inputs": [("results", "results")],
        "outputs": [],
        "kind": "sink",
        "config": {"name": "report"},
    },
}

CALC_NODE_TYPES = {t for t, d in NODE_TYPES.items() if d["kind"] == "calc"}


# Condition predicates: evaluated at runtime on the .out of the calculation
# feeding the condition. label is for the picker; the eval is in eval_predicate.
PREDICATES = {
    "no_imaginary_freqs": "No imaginary frequencies (a true minimum)",
    "has_imaginary_freqs": "Has an imaginary frequency (e.g. a transition state)",
    "terminated_ok": "Terminated normally",
}


def eval_predicate(name, out_text):
    # type: (str, str) -> bool
    """Evaluate a condition predicate against a calculation's .out text."""
    from orca_studio.core import orca_parser as P
    if not out_text:
        return False
    if name == "terminated_ok":
        return bool(P._TERM_OK.search(out_text))
    if name in ("no_imaginary_freqs", "has_imaginary_freqs"):
        vibs = P.real_frequencies(P.parse_frequencies(out_text))
        n_imag = sum(1 for f in vibs if f < 0)
        if name == "no_imaginary_freqs":
            return len(vibs) > 0 and n_imag == 0
        return n_imag > 0
    return True


def gate_outcome(predicate, source_done, source_out_text):
    # type: (str, bool, Optional[str]) -> str
    """Resolve a conditional gate to 'pending' | 'open' | 'closed'.

    A gated calc runs only when its source calc has finished AND the predicate
    holds. While the source is still running we don't know yet (pending).
    """
    if not source_done:
        return "pending"
    return "open" if eval_predicate(predicate, source_out_text or "") else "closed"


def _geometry_input_port(node):
    for name, t in node.inputs():
        if t == "geometry":
            return name
    return None


def _new_id():
    return uuid.uuid4().hex[:10]


class WorkflowNode(object):
    def __init__(self, type, x=40.0, y=40.0, config=None, id=None):
        # type: (str, float, float, Optional[dict], Optional[str]) -> None
        self.id = id or _new_id()
        self.type = type
        self.x = float(x)
        self.y = float(y)
        # start from the type's default config, overlaid with any given values
        base = dict(NODE_TYPES.get(type, {}).get("config", {}))
        if config:
            base.update(config)
        self.config = base

    @property
    def label(self):
        return NODE_TYPES.get(self.type, {}).get("label", self.type)

    @property
    def kind(self):
        return NODE_TYPES.get(self.type, {}).get("kind", "calc")

    def inputs(self):
        return NODE_TYPES.get(self.type, {}).get("inputs", [])

    def outputs(self):
        return NODE_TYPES.get(self.type, {}).get("outputs", [])

    def port_type(self, port_name, is_input):
        ports = self.inputs() if is_input else self.outputs()
        for name, ptype in ports:
            if name == port_name:
                return ptype
        return None

    def to_dict(self):
        return {"id": self.id, "type": self.type, "x": self.x, "y": self.y,
                "config": self.config}

    @classmethod
    def from_dict(cls, d):
        return cls(type=d["type"], x=d.get("x", 40), y=d.get("y", 40),
                   config=d.get("config"), id=d.get("id"))


class WorkflowEdge(object):
    def __init__(self, src_node, src_port, dst_node, dst_port, id=None):
        self.id = id or _new_id()
        self.src_node = src_node
        self.src_port = src_port
        self.dst_node = dst_node
        self.dst_port = dst_port

    def to_dict(self):
        return {"id": self.id, "src_node": self.src_node, "src_port": self.src_port,
                "dst_node": self.dst_node, "dst_port": self.dst_port}

    @classmethod
    def from_dict(cls, d):
        return cls(d["src_node"], d["src_port"], d["dst_node"], d["dst_port"], id=d.get("id"))


class Workflow(object):
    def __init__(self, nodes=None, edges=None, category="wf"):
        self.nodes = nodes or []          # type: List[WorkflowNode]
        self.edges = edges or []          # type: List[WorkflowEdge]
        self.category = category

    # ---- serialisation ----

    def to_dict(self):
        return {"category": self.category,
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [e.to_dict() for e in self.edges]}

    @classmethod
    def from_dict(cls, d):
        if not d:
            return cls()
        return cls(nodes=[WorkflowNode.from_dict(n) for n in d.get("nodes", [])],
                   edges=[WorkflowEdge.from_dict(e) for e in d.get("edges", [])],
                   category=d.get("category", "wf"))

    # ---- lookups ----

    def node(self, node_id):
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def edge(self, edge_id):
        for e in self.edges:
            if e.id == edge_id:
                return e
        return None

    def edges_into(self, node_id, port=None):
        return [e for e in self.edges if e.dst_node == node_id
                and (port is None or e.dst_port == port)]

    def edges_out(self, node_id, port=None):
        return [e for e in self.edges if e.src_node == node_id
                and (port is None or e.src_port == port)]

    # ---- mutation ----

    def add_node(self, type, x=40.0, y=40.0, config=None):
        n = WorkflowNode(type, x, y, config)
        self.nodes.append(n)
        return n

    def remove_node(self, node_id):
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.edges = [e for e in self.edges if e.src_node != node_id and e.dst_node != node_id]

    def remove_edge(self, edge_id):
        self.edges = [e for e in self.edges if e.id != edge_id]

    def can_connect(self, src_node, src_port, dst_node, dst_port):
        # type: (str, str, str, str) -> Tuple[bool, str]
        if src_node == dst_node:
            return False, "can't connect a node to itself"
        sn, dn = self.node(src_node), self.node(dst_node)
        if sn is None or dn is None:
            return False, "node missing"
        st = sn.port_type(src_port, is_input=False)
        dt = dn.port_type(dst_port, is_input=True)
        if st is None or dt is None:
            return False, "port missing"
        if st != dt:
            return False, "type mismatch ({} -> {})".format(st, dt)
        # 'results' inputs fan in (a Report merges many results into one file);
        # 'geometry' inputs take a single structure.
        if dt != "results" and self.edges_into(dst_node, dst_port):
            return False, "input already connected"
        # don't allow the exact same edge twice
        for e in self.edges_into(dst_node, dst_port):
            if e.src_node == src_node and e.src_port == src_port:
                return False, "already connected"
        if self._would_cycle(src_node, dst_node):
            return False, "would create a cycle"
        return True, ""

    def add_edge(self, src_node, src_port, dst_node, dst_port):
        ok, why = self.can_connect(src_node, src_port, dst_node, dst_port)
        if not ok:
            return None, why
        e = WorkflowEdge(src_node, src_port, dst_node, dst_port)
        self.edges.append(e)
        return e, ""

    def _would_cycle(self, src_node, dst_node):
        # adding src->dst makes a cycle iff src is already reachable from dst
        seen = set()
        stack = [dst_node]
        while stack:
            cur = stack.pop()
            if cur == src_node:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            for e in self.edges_out(cur):
                stack.append(e.dst_node)
        return False

    def topo_order(self):
        # type: () -> Optional[List[str]]
        """Kahn's algorithm. Returns node ids in dependency order, or None if cyclic."""
        indeg = {n.id: 0 for n in self.nodes}
        for e in self.edges:
            if e.dst_node in indeg:
                indeg[e.dst_node] += 1
        queue = [nid for nid, d in indeg.items() if d == 0]
        order = []
        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for e in self.edges_out(nid):
                indeg[e.dst_node] -= 1
                if indeg[e.dst_node] == 0:
                    queue.append(e.dst_node)
        return order if len(order) == len(self.nodes) else None

    # ---- validation ----

    def validate(self):
        # type: () -> List[str]
        issues = []
        sources = [n for n in self.nodes if n.type == "molecules"]
        if len(sources) == 0:
            issues.append("No Molecules source node.")
        elif len(sources) > 1:
            issues.append("More than one Molecules node — only the first is used.")
        for n in self.nodes:
            if n.type in CALC_NODE_TYPES:
                if not n.config.get("recipe"):
                    issues.append("{} node has no recipe selected.".format(n.label))
                if not self.edges_into(n.id, "geometry"):
                    issues.append("{} node has no geometry input connected.".format(n.label))
            elif n.type == "condition":
                fin = self.edges_into(n.id, "in")
                if not fin:
                    issues.append("Condition node has no input connected.")
                else:
                    feeder = self.node(fin[0].src_node)
                    if feeder is None or feeder.type not in CALC_NODE_TYPES:
                        issues.append("Condition must be fed by a calculation node (so there's "
                                      "a result to test).")
        if self.topo_order() is None:
            issues.append("The graph contains a cycle.")
        return issues


def expand_to_calcs(workflow, molecule_filenames, planned_calc_factory):
    # type: (Workflow, List[str], callable) -> Tuple[list, List[str], Dict[str, list]]
    """Expand a workflow into PlannedCalcs, attaching conditional gates.

    For each molecule, walk the calc nodes in topological order. A calc node
    whose geometry comes from the Molecules source uses geometry_source
    'initial'; one whose geometry comes from another calc node uses
    'parent:<that node's calc for this molecule>'.

    `planned_calc_factory(molecule, recipe_name, category, geometry_source,
    parent_id, gate, origin_node)` builds OR reuses a PlannedCalc (passed in to
    keep this module free of UI imports). `gate` is None, or {"source": calc_id,
    "predicate": name} when the geometry path crosses a Condition node — the calc
    then runs only if that predicate holds on the source calc's output.
    `origin_node` is the graph node id, so the factory can reuse an existing calc
    for the same (node, molecule) instead of creating a duplicate.

    Disconnected sub-graphs run as independent networks: each calc node is
    grouped by the Molecules source its geometry traces back to, and each source
    contributes its own molecule set.

    Returns (calcs, warnings, node_map) where node_map maps each calc node id to
    the list of calc ids it produced (one per molecule), for live UI coloring.
    """
    warnings = []
    order = workflow.topo_order()
    if order is None:
        return [], ["Graph has a cycle — cannot expand."], {}

    sources = [n for n in workflow.nodes if n.type == "molecules"]
    if not sources:
        return [], ["No Molecules source node."], {}

    for n in workflow.nodes:
        if n.type == "report":
            warnings.append("Report results are written when the pipeline finishes (Run "
                            "pipeline). 'Generate only' just creates the calculations.")
            break

    def root_source(node):
        """Walk the geometry path all the way back (through calc / condition
        nodes too) to the Molecules source feeding this node, or None."""
        cur = node
        guard = 0
        while guard < 200:
            guard += 1
            port = _geometry_input_port(cur)
            ein = workflow.edges_into(cur.id, port) if port else []
            if not ein:
                return None
            src = workflow.node(ein[0].src_node)
            if src is None:
                return None
            if src.type == "molecules":
                return src.id
            cur = src

    def resolve(node, node_calc):
        """Walk back along the geometry path, passing through condition /
        frequencies / property nodes (none of which produce a *new* optimized
        geometry) to the nearest optimize node (parent) or the molecules source
        (initial). If the path crosses a condition node, attach a gate keyed on
        the calc feeding that condition."""
        gate = None
        cur = node
        guard = 0
        while guard < 200:
            guard += 1
            port = _geometry_input_port(cur)
            ein = workflow.edges_into(cur.id, port) if port else []
            if not ein:
                return "initial", None, gate
            src = workflow.node(ein[0].src_node)
            if src is None or src.type == "molecules":
                return "initial", None, gate
            if src.type == "optimize":
                pid = node_calc.get(src.id)
                if pid:
                    return "parent:" + pid, pid, gate
                return "initial", None, gate
            if src.type == "condition":
                if gate is None:
                    cin = workflow.edges_into(src.id, "in")
                    if cin:
                        fid = node_calc.get(cin[0].src_node)
                        if fid:
                            gate = {"source": fid,
                                    "predicate": src.config.get("predicate", "terminated_ok")}
                cur = src
                continue
            if src.type in ("frequencies", "property"):
                cur = src
                continue
            return "initial", None, gate

    # All calc nodes in topological order, grouped by the Molecules source they
    # trace back to — each group is an independent network.
    calc_nodes = [workflow.node(nid) for nid in order
                  if workflow.node(nid).type in CALC_NODE_TYPES]
    groups = {}  # source_id -> [calc node, ...] in topo order
    for cn in calc_nodes:
        if not workflow.edges_into(cn.id, "geometry"):
            continue  # validated elsewhere
        rs = root_source(cn)
        if rs is None:
            warnings.append("{} isn't connected to a Molecules source — skipped."
                            .format(cn.label))
            continue
        groups.setdefault(rs, []).append(cn)

    calcs = []
    node_map = {}  # node_id -> [calc id, ...] across molecules (for live coloring)
    for src in sources:
        group = groups.get(src.id)
        if not group:
            continue
        mols = list(molecule_filenames)
        cfg = src.config
        if cfg.get("mode") == "selection" and cfg.get("filenames"):
            sel = set(cfg["filenames"])
            mols = [m for m in mols if m in sel]
        if not mols:
            warnings.append("A Molecules node selects no molecules — its network was skipped.")
            continue
        for mol in mols:
            node_calc = {}  # node_id -> calc id for this molecule
            for node in group:
                geometry_source, parent_id, gate = resolve(node, node_calc)
                calc = planned_calc_factory(mol, node.config.get("recipe", ""),
                                            workflow.category, geometry_source, parent_id,
                                            gate, node.id)
                node_calc[node.id] = calc.id
                node_map.setdefault(node.id, []).append(calc.id)
                calcs.append(calc)
    if not calcs and not warnings:
        warnings.append("No calculation nodes are connected to a Molecules source.")
    return calcs, warnings, node_map
